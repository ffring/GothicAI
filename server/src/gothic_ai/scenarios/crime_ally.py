"""A companion witnesses the hero finishing the bandit who attacked him."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from .. import triggers
from ..executor import Intent
from ..game import GameError, GameLink
from ..llm import ToolCall
from ..llm.fake import FakeBackend, FakeStep
from ..protocol import GameMessage, HeroDeed, NpcEvent
from .common import _ByNpc, _logged, _OnWords, _until, _until_async

if TYPE_CHECKING:
    from ..crimes import Crimes
    from ..dialogue import DialogueManager
    from ..perception import PerceptionDesk
    from ..talk import TalkDesk

CAVALORN, BRAGO = "BAU_4300_ADDON_CAVALORN", "BDT_1014_BANDIT_L"
OTHERS = ("BDT_1013_BANDIT_L", "BDT_1015_BANDIT_L")
QUESTION = "Кавалорн, что скажешь о том, что случилось с Браго?"
ANSWER = "Ты помог мне отбиться от Браго. Я с тобой."


async def crime_ally(
    link: GameLink,
    dialogue: DialogueManager,
    talk: TalkDesk,
    service: Crimes,
    perception: PerceptionDesk | None = None,
) -> dict[str, str]:
    """New game, fake models; answers deliberately fails while the current murder rule refuses."""
    if dialogue.llm.name != "fake":
        raise GameError("scenario crime_ally requires LLM=fake and a new game without SAVE")
    await link.wait_world()
    results: dict[str, str] = {}
    entries: list[dict[str, Any]] = []
    received: list[GameMessage] = []
    follows: list[str] = []
    original_llm, original_sink, original_act = dialogue.llm, dialogue.journal.sink, link.act

    def check(name: str, ok: bool, detail: object = "") -> bool:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"
        return ok

    def keep(entry: dict[str, Any]) -> None:
        entries.append(entry)
        if original_sink is not None:
            original_sink(entry)

    async def recorded(npc: str, action: Any, /, **kw: Any) -> Any:
        if npc.upper() == CAVALORN and action == "follow":
            follows.append(action)
        return await original_act(npc, action, **kw)

    def distance(npc: str) -> float:
        return talk.people[npc].distance if npc in talk.people else -1

    def record(message: GameMessage) -> None:
        received.append(message)

    challenge = _OnWords(
        [("не трогай", FakeStep("Сам напросился!", [ToolCall("set_intent", {
            "goal": "fight", "target": CAVALORN, "attitude": "hostile", "agenda": None,
        })]))],
        FakeStep("Убирайся."),
    )
    dialogue.llm = _ByNpc({
        "Кавалорн": _OnWords([(QUESTION, FakeStep(ANSWER))], FakeStep("Я рядом.")),
        "Браго": challenge,
        "Бандит": challenge,
    }, FakeBackend("Хм.", delay=0))
    dialogue.journal.sink = keep
    link.act = recorded  # type: ignore[method-assign]
    link.listeners.append(record)

    try:
        if not check("new_game", not service.facts(), f"{len(service.facts())} crimes"):
            return _logged(results)
        await link.debug("player_equip", item="ITAR_PAL_H")
        for npc in (CAVALORN, BRAGO, *OTHERS):
            await link.debug("skip_important", near=npc)
        for npc in OTHERS:
            # This path has no killer and produces no hero deed.
            await link.debug("kill", npc=npc)
        for _ in range(2):
            await link.debug("kill", instance="YGOBBO_GREEN", near=CAVALORN, by=CAVALORN)

        # One native hero hit must knock Brago out before Cavalorn's counterattack. Set only the
        # hero's initial strength through the existing fixture path; remove the trigger before combat.
        strength = ["field", ["inst", "HERO"], "ATTRIBUTE", 4]
        trigger = "DIR_CRIME_ALLY_STRENGTH"
        await link.patch_graph([triggers.trigger(trigger, triggers.STATE, [
            {"do": "set", "target": strength, "value": ["int", 10000]},
        ])])
        try:
            prepared = await _until_async(lambda: link.evaluate(strength), lambda value: value == 10000, 5)
        finally:
            await link.patch_graph([triggers.removal(trigger)])
        if not check("hero_ready", prepared, "armor, strength=10000; native melee damage"):
            return _logged(results)

        await link.debug("player_goto", to=CAVALORN, distance=300)
        if not check("cavalorn_near", await _until(lambda: 0 <= distance(CAVALORN) <= 1500, 20)):
            return _logged(results)
        dialogue.open(CAVALORN, "Кавалорн").intent = Intent("follow_hero", "PLAYER", "friendly")
        talk.hear("Кавалорн, за мной!", None, {"via": "scenario"})
        if not check("follow", await _until(lambda: bool(follows) and 0 <= distance(CAVALORN) <= 500, 20)):
            return _logged(results)

        # The hero goes to XARDAS_BANDITS, as in companion; Cavalorn follows along the real waynet.
        await link.debug("player_goto", to=BRAGO, distance=300)
        if not check("bandits", await _until(
            lambda: 0 <= distance(BRAGO) <= 500 and 0 <= distance(CAVALORN) <= 600, 60,
        ), "XARDAS_BANDITS"):
            return _logged(results)
        if perception is not None:
            await perception.fresh(BRAGO)
        await link.snapshot(BRAGO, fresh=True)
        talk.hear("Эй, Браго! Не трогай Кавалорна.", BRAGO, {"via": "scenario"})

        def attacked() -> bool:
            conv = dialogue.conversations.get(BRAGO)
            return (
                conv is not None
                and (conv.intent.goal, conv.intent.target) == ("fight", CAVALORN)
                and any(isinstance(message, NpcEvent)
                        and message.npc == CAVALORN and message.kind == "npc_attack"
                        and message.other == BRAGO for message in received)
            )

        if not check(
            "brago_attacked", await _until(attacked, 30), f"set_intent fight -> {CAVALORN}; npc_attack",
        ):
            return _logged(results)

        await link.debug("teleport", near=BRAGO, distance=250)

        async def hit() -> bool:
            try:
                await link.debug("player_hit", npc=BRAGO)
                return True
            except GameError as error:
                if str(error) != "the hit caused no damage":
                    raise
                return False  # A native parry or backstep may briefly block the real damage path.

        hit_landed = await _until_async(hit, bool, 5)
        down = await _until(lambda: BRAGO in talk.people and talk.people[BRAGO].activity == "down", 10)
        snapshot = await link.snapshot(BRAGO, fresh=True)
        if not check(
            "brago_down", hit_landed and down and snapshot.npc.hp == 1,
            f"hero_hit={hit_landed}, activity={'down' if down else 'not down'}, hp={snapshot.npc.hp}",
        ):
            return _logged(results)

        witness: dict[str, Any] = {}

        async def witness_ready() -> bool:
            views = await link.npc_view([CAVALORN])
            view = views[0] if views else None
            witness["view"] = view.model_dump() if view is not None else None
            seen = await link.evaluate(["call", "NPC_CANSEENPC", ["inst", CAVALORN], ["inst", "HERO"]])
            witness["sees_hero"] = seen
            return (
                view is not None and view.hp > 0 and view.body == "stand"
                and view.state != "ZS_ATTACK" and view.others_damage_ready is True
                and view.hero is not None and view.hero.distance <= 1000 and bool(seen)
                and (talk.orders is None or CAVALORN not in talk.orders.fights)
            )

        if not check("witness_ready", await _until_async(witness_ready, bool, 20), witness):
            return _logged(results)
        await link.debug("kill", instance=BRAGO, by="PLAYER")

        def murder() -> dict[str, Any] | None:
            return next((fact for fact in service.facts()
                         if fact.get("kind") == "murder" and fact.get("victim") == BRAGO
                         and CAVALORN in fact.get("witnesses", [])), None)

        seen = await _until(lambda: murder() is not None, 10)
        deed = next((message for message in received if isinstance(message, HeroDeed)
                     and message.kind == "murder" and message.victim == BRAGO), None)
        if not check("murder_seen", seen and deed is not None, murder() or "no witnessed murder"):
            return _logged(results)

        await link.debug("teleport", near=CAVALORN, distance=250)
        await _until(lambda: 0 <= distance(CAVALORN) <= 500, 5)
        mark, started = len(entries), time.monotonic()
        talk.hear(QUESTION, CAVALORN, {"via": "scenario"})
        answers = await _until(lambda: any(
            entry.get("kind") == "npc" and entry.get("npc") == CAVALORN and ANSWER in entry.get("text", "")
            for entry in entries[mark:]
        ), 15)
        addressed = any(entry.get("kind") == "player" and entry.get("npc") == CAVALORN
                        and entry.get("text") == QUESTION for entry in entries[mark:])
        check(
            "answers", addressed and answers,
            f"focused hero question; reply in {time.monotonic() - started:.1f} s",
        )
        return _logged(results)
    finally:
        dialogue.llm, dialogue.journal.sink = original_llm, original_sink
        link.act = original_act  # type: ignore[method-assign]
        link.listeners.remove(record)
