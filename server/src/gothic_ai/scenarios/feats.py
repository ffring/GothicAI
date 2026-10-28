"""A real hero kill becomes a feat, a toast and a journal entry, then rewinds with the save."""

from __future__ import annotations

import asyncio
from typing import Any

from ..chronicle import Fact
from ..feats import CATEGORIES, WRITE, Feats
from ..game import GameError, GameLink
from ..journal import Journal
from ..llm import ToolCall
from ..llm.fake import FakeBackend
from ..protocol import FeatToastCmd, Session
from .common import _logged, _save_or_fail, _until, wait_message


async def feats(link: GameLink, chronicler: Feats, journal: Journal, slot: int = 8) -> dict[str, str]:
    """Use death_load's native human kill on a fresh game, with one scripted chronicler answer."""
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: object = "") -> bool:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"
        return ok

    if not check("fake_model", chronicler.model.llm.name == "fake", "requires LLM=fake"):
        return _logged(results)

    chronicle = chronicler.chronicle
    victim = "BDT_1014_BANDIT_L"
    trigger: int | None = None
    answer = {
        "verdict": "feat", "title": "Первый приговор", "text": "Отправил бандита на тот свет.",
        "category": "blood", "evidence": [], "emblem": "A broken sword beside a boot",
        "reason": "Герой впервые убил человека.",
    }
    backend = FakeBackend("", tool_calls=[ToolCall(WRITE, answer)], delay=0)
    original_llm, original_sink, send = chronicler.model.llm, journal.sink, link.send
    toasts: list[FeatToastCmd] = []
    entries: list[dict[str, Any]] = []
    loading: asyncio.Task[Session] | None = None

    def record(seq: int, fact: Fact) -> None:
        nonlocal trigger
        # The native kill records its murder before npc_died; use the first of its two occasions.
        killed = fact.get("fact") == "died" and fact.get("npc") == victim and fact.get("killer") == "PLAYER"
        murder = fact.get("fact") == "crime" and fact.get("victim") == victim and fact.get("kind") == "murder"
        if trigger is None and (murder or killed):
            trigger = seq
            # on_entry runs synchronously, before the chronicler's scheduled model call.
            answer["evidence"] = [seq]

    async def sending(cmd: Any) -> None:
        await send(cmd)
        if isinstance(cmd, FeatToastCmd):
            toasts.append(cmd)

    def keep(entry: dict[str, Any]) -> None:
        entries.append(entry)
        if original_sink is not None:
            original_sink(entry)

    chronicler.model.llm = backend
    chronicle.on_entry.append(record)
    link.send = sending  # type: ignore[method-assign]
    journal.sink = keep
    try:
        await link.wait_world()
        if link.session is None:
            await wait_message(link, Session, timeout=10)
        # A fresh snapshot proves world ticks started and primes the native death scan.
        await link.snapshot("NONE_100_XARDAS", fresh=True)
        if not check("clean_branch", not chronicler.feats, f"feats={len(chronicler.feats)}"):
            return _logged(results)
        saved = await _save_or_fail(link, slot)
        check("save_before_deed", True, f"slot={slot}, branch={saved.branch}, seq={saved.seq}")

        await link.debug("kill", instance=victim, by="PLAYER")
        if not check("hero_deed", await _until(lambda: trigger is not None, 10), f"npc={victim}"):
            return _logged(results)
        if not check("feat_recorded", await _until(lambda: bool(chronicler.feats), 60), "limit_s=60"):
            return _logged(results)
        award = chronicler.feats[0]
        check(
            "evidence", trigger in award.get("evidence", []) and award.get("trigger") == trigger
            and award.get("category") in CATEGORIES,
            f"trigger={trigger}, evidence={award.get('evidence')}, category={award.get('category')}",
        )
        await _until(lambda: any(toast.feat == award["id"] for toast in toasts), 5)
        check(
            "toast", any(toast.feat == award["id"] and toast.icon for toast in toasts),
            f"feat={award['id']}, icons={[toast.icon for toast in toasts]}",
        )
        check(
            "journal", any(entry.get("kind") == "feat" and entry.get("id") == award["id"]
                           and entry.get("category") == award["category"] for entry in entries),
            f"feat={award['id']}",
        )
        check("model_call", len(backend.calls) == 1, f"calls={len(backend.calls)}")

        loading = asyncio.create_task(wait_message(link, Session, lambda msg: msg.reason == "load", 120))
        await asyncio.sleep(0)
        await link.debug("load", slot)
        session = await loading
        await link.wait_world(timeout=120)
        check(
            "rollback", session.parent == saved.branch and session.parent_seq == saved.seq
            and not chronicler.feats,
            f"branch={session.branch}, parent={session.parent}, parent_seq={session.parent_seq}, "
            f"feats={len(chronicler.feats)}",
        )
        return _logged(results)
    except (GameError, TimeoutError, RuntimeError) as error:
        check("error", False, str(error) or type(error).__name__)
        return _logged(results)
    finally:
        if loading is not None and not loading.done():
            loading.cancel()
            await asyncio.gather(loading, return_exceptions=True)
        chronicler.model.llm, journal.sink = original_llm, original_sink
        link.send = send  # type: ignore[method-assign]
        chronicle.on_entry.remove(record)
