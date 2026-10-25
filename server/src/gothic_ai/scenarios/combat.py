from __future__ import annotations

from typing import Any

from ..combat_barks import CombatBarks
from ..dialogue import DialogueManager
from ..game import GameLink
from ..llm.fake import FakeBackend
from ..protocol import CombatHit, GameMessage, LineStarted
from ..trace import read as read_trace
from .common import LOBART, _logged, _until, attach_when_free


async def combat_barks(
    link: GameLink, dialogue: DialogueManager, barks: CombatBarks,
) -> dict[str, str]:
    """One real hero hit: actual health loss and a voiced cry on the move."""
    await link.wait_world()
    await link.debug("skip_important", near=LOBART)
    await attach_when_free(link, LOBART)
    await link.debug("teleport", near=LOBART, distance=200)
    await link.act(LOBART, "stop")
    await link.act(LOBART, "wait", ms=100)
    # Without QUICK_URL only the scenario supplies a fake quick model.
    quick = dialogue.quick
    if quick is None:
        dialogue.quick = FakeBackend("[pain] Ай, больно!", delay=0)
    hits: list[CombatHit] = []
    voices: dict[str, bool] = {}

    def record(msg: GameMessage) -> None:
        if isinstance(msg, CombatHit) and msg.npc == LOBART:
            hits.append(msg)
        elif isinstance(msg, LineStarted) and msg.npc == LOBART:
            voices[msg.line] = msg.voiced

    def traces() -> list[dict[str, Any]]:
        if dialogue.trace is None or not dialogue.trace.path().exists():
            return []
        return [r for r in read_trace(dialogue.trace.path())
                if r.get("type") == "combat_bark" and r.get("npc") == LOBART]

    results: dict[str, str] = {}
    link.listeners.insert(0, record)
    try:
        ready = await _until(lambda: barks._eligible(LOBART), 8)
        if not ready:
            return _logged({"ready": "failed (no living human in the event radius)"})
        before = await link.snapshot(LOBART, fresh=True)
        await link.debug("player_hit", npc=LOBART)
        await _until(lambda: bool(hits), 3)
        hit = hits[0] if hits else None
        ok = (hit is not None and hit.by == "PLAYER" and hit.damage > 0
              and hit.hp == before.npc.hp - hit.damage and hit.kind == "melee")
        results["combat_hit"] = f"{'done' if ok else 'failed'} ({hit.model_dump() if hit else 'absent'})"
        await _until(lambda: any(r.get("outcome") == "said" for r in traces()), 8)
        said = next((r for r in traces() if r.get("outcome") == "said"), None)
        lines = [line for line, (text, _) in dialogue._lines.items() if said and text == said["text"]]
        await _until(lambda: any(voices.get(line) for line in lines), 8)
        ok = (said is not None and said.get("reason") == "hurt" and said.get("overlay") is True
              and any(voices.get(line) for line in lines))
        results["hurt_overlay"] = (
            f"{'done' if ok else 'failed'} (text={said.get('text') if said else None!r}, "
            f"first_phrase_ms={said.get('first_phrase_ms') if said else None}, "
            f"voice_ms={said.get('voice_ms') if said else None}, voiced={any(voices.values())})"
        )
        return _logged(results)
    finally:
        link.listeners.remove(record)
        dialogue.quick = quick
        await link.detach(LOBART)
