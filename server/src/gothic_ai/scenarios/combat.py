from __future__ import annotations

import json
import time
from typing import Any

from .. import triggers
from ..combat_barks import CombatBarks
from ..contracts import GOLD
from ..dialogue import DialogueManager
from ..game import GameError, GameLink
from ..llm import ToolCall
from ..llm.fake import FakeBackend, FakeStep
from ..mercy import PARDON, PEE_DISTANCE_CM, Mercy
from ..protocol import CombatHit, GameMessage, LineStarted, Snapshot
from ..talk import TalkDesk, fights
from ..trace import read as read_trace
from .common import LOBART, ClaimScript, _logged, _m5b_player_weapon, _until, _until_async, attach_when_free


async def mercy(
    link: GameLink, dialogue: DialogueManager, talk: TalkDesk, mercy: Mercy,
) -> dict[str, str]:
    """A farmer yields after a real low-health hit and can be spoken to after being spared."""
    if dialogue.llm.name != "fake":
        raise GameError("scenario mercy requires LLM=fake")
    await link.wait_world()
    await link.debug("skip_important", near=LOBART)
    await attach_when_free(link, LOBART)
    await link.debug("teleport", near=LOBART, distance=200)
    await link.act(LOBART, "stop")
    await link.debug("player_hp", hp=2000)
    await link.debug("player_equip", item="ITMW_1H_BAU_MACE")
    await _m5b_player_weapon(link, "melee")
    original = dialogue.llm
    dialogue.llm = ClaimScript(
        said=[("Сейчас бой:", FakeStep(tool_calls=[ToolCall("surrender", {"words": "Хватит! Сдаюсь!"})])),
              ("Лобарт, живи.", FakeStep("Спасибо. Я запомню."))], told=[], default="",
    )
    results: dict[str, str] = {}
    hits: list[CombatHit] = []
    voices: list[LineStarted] = []

    def record(msg: GameMessage) -> None:
        if isinstance(msg, CombatHit) and msg.npc == LOBART:
            hits.append(msg)
        elif isinstance(msg, LineStarted) and msg.npc == LOBART:
            voices.append(msg)

    async def health(hp: int, maximum: int) -> None:
        attributes = await link.story_eval(LOBART, [["var", "ATR_HITPOINTS"], ["var", "ATR_HITPOINTS_MAX"]])
        if (len(attributes) != 2 or any(type(a) is not int for a in attributes)
                or attributes[0] == attributes[1]):
            raise GameError(f"mercy preparation: invalid HP indices {attributes!r}")
        fields = [["field", ["inst", LOBART], "ATTRIBUTE", a] for a in attributes]
        key = f"DIR_MERCY_HP_{hp}"
        await link.patch_graph([triggers.trigger(key, triggers.STATE, [
            {"do": "set", "target": target, "value": ["int", value]}
            for target, value in zip(fields, [hp, maximum], strict=True)
        ])])
        try:
            if not await _until_async(lambda: link.story_eval(LOBART, fields),
                                      lambda values: values == [hp, maximum], 4):
                values = await link.story_eval(LOBART, fields)
                raise GameError(
                    f"mercy preparation: NPC health did not apply: {values!r}, wanted {[hp, maximum]}",
                )
        finally:
            await link.patch_graph([triggers.removal(key)])

    link.listeners.append(record)
    try:
        await health(1000, 1000)
        await link.detach(LOBART)
        await link.debug("player_hit", npc=LOBART)
        await link.story_effect(LOBART, [{"do": "attack", "reason": "AR_NONE"}])
        combat = [
            ["call", "NPC_ISINSTATE", ["inst", "SELF"], ["sym", "ZS_ATTACK"]],
            ["&&", ["call", "NPC_GETTARGET", ["inst", "SELF"]],
             ["==", ["call", "HLP_GETINSTANCEID", ["inst", "OTHER"]],
              ["call", "HLP_GETINSTANCEID", ["inst", "HERO"]]]],
        ]
        started = await _until_async(lambda: link.story_eval(LOBART, combat),
                                     lambda values: values == [1, 1], 8)
        state = await link.story_eval(LOBART, combat)
        results["fight_started"] = (
            f"{'done' if started else 'failed'} (hits={len(hits)}, fighting/hero_target={state})"
        )
        if not started:
            return _logged(results)
        await health(250, 1000)
        await link.debug("player_hit", npc=LOBART)
        surrendered = await _until(lambda: mercy._latest(LOBART) is not None, 8)
        low = bool(hits and 0 < hits[-1].hp < hits[-1].hp_max * 0.3)
        results["low_hp_surrender"] = f"{'done' if surrendered and low else 'failed'} (hits={len(hits)})"
        if not surrendered:
            return _logged(results)
        at = time.monotonic()
        stopped = await _until_async(
            lambda: link.npc_view([LOBART]),
            lambda views: bool(views) and views[0].hp > 0 and views[0].weapon == "none"
            and not fights(talk.people.get(LOBART)), 3,
        )
        [view] = await link.npc_view([LOBART])
        results["fight_stopped"] = (
            f"{'done' if stopped and view.weapon == 'none' else 'failed'} "
            f"(seconds={time.monotonic() - at:.2f}, weapon={view.weapon})"
        )
        names = ["AIV_LASTFIGHTAGAINSTPLAYER", "AIV_LASTFIGHTCOMMENT", "AIV_DEFEATEDBYPLAYER"]
        indices = await link.story_eval(LOBART, [["var", n] for n in names])
        values = await link.story_eval(LOBART, [
            *(["field", ["inst", "SELF"], "AIVAR", i] for i in indices), ["var", "FIGHT_LOST"],
        ])
        native = all(type(i) is int for i in indices) and values[:3] == [values[3], 0, 1]
        results["native_outcome"] = f"{'done' if native else 'failed'} (indices={indices}, values={values})"
        await _until(lambda: any(v.voiced for v in voices), 8)
        results["surrender_voice"] = f"{'done' if any(v.voiced for v in voices) else 'failed'}"
        await _m5b_player_weapon(link, "none")
        spared = await _until(lambda: mercy._latest(LOBART)["status"] == "spared", 4)
        remembered = any(PARDON in m.content for m in dialogue.conversations[LOBART].history)
        results["spared"] = f"{'done' if spared and remembered else 'failed'}"
        before = len(dialogue.llm.calls)
        await link.debug("type", text="Лобарт, живи.")
        answered = await _until(lambda: len(dialogue.llm.calls) > before, 8)
        normal = answered and talk.partner and talk.partner.npc == LOBART
        results["normal_talk"] = f"{'done' if normal else 'failed'}"
        return _logged(results)
    finally:
        link.listeners.remove(record)
        dialogue.llm = original


async def humiliate(
    link: GameLink, dialogue: DialogueManager, talk: TalkDesk, mercy: Mercy,
) -> dict[str, str]:
    return await hero_yield(link, dialogue, talk, mercy, humiliating=True)


async def hero_yield(
    link: GameLink, dialogue: DialogueManager, talk: TalkDesk, mercy: Mercy, *, humiliating: bool = False,
) -> dict[str, str]:
    """The hero's typed surrender stops a native fight and pays real gold to its winner."""
    if dialogue.llm.name != "fake":
        raise GameError("scenario hero_yield requires LLM=fake")
    await link.wait_world()
    await link.debug("skip_important", near=LOBART)
    await attach_when_free(link, LOBART)
    await link.debug("teleport", near=LOBART, distance=200)
    await link.act(LOBART, "stop")
    await link.debug("player_hp", hp=2000)
    sword = "ITMW_SHORTSWORD1"
    await link.debug("player_equip", item=sword)
    equipped = await _until_async(
        lambda: link.story_eval(LOBART, [["call", "NPC_GETEQUIPPEDMELEEWEAPON", ["inst", "HERO"]]]),
        lambda values: values == [sword], 4,
    )
    if not equipped:
        raise GameError("hero_yield preparation: the hero did not equip the sword")
    await _m5b_player_weapon(link, "melee")
    gold = [["call", "NPC_HASITEMS", ["inst", who], ["sym", GOLD]] for who in ("HERO", "SELF")]
    [hero_gold, _] = await link.story_eval(LOBART, gold)
    if hero_gold < 100:
        await link.story_effect(LOBART, [{"do": "create", "item": GOLD, "count": 100 - hero_gold},
                                       {"do": "give", "item": GOLD, "count": 100 - hero_gold}])
    before = await link.story_eval(LOBART, gold)
    original, original_quick = dialogue.llm, dialogue.quick
    original_hits = mercy.barks.hits if mercy.barks is not None else False
    if humiliating and mercy.barks is not None:
        mercy.barks.hits = False  # the setup hit must not consume the scene's fake phrase
    if humiliating and dialogue.quick is None:
        dialogue.quick = FakeBackend("[hostile] Вот тебе и пощада!", delay=0)
    samples = []
    typed_at = time.monotonic()

    def record(msg: GameMessage) -> None:
        if humiliating and isinstance(msg, Snapshot) and msg.npc.id == LOBART and msg.player:
            samples.append({
                "game_ms": msg.t, "elapsed_ms": round((time.monotonic() - typed_at) * 1000),
                "npc_pos": msg.npc.pos, "hero_pos": msg.player.pos,
                "distance_cm": round(msg.player.distance, 1),
                "npc_animations": msg.npc.animations, "hero_animations": msg.player.animations,
                "kneeling": msg.player.kneeling,
            })

    link.listeners.append(record)
    phrase = "Сдаюсь, забирай золото, только не бей"
    script = ClaimScript(said=[(phrase, FakeStep(tool_calls=[ToolCall(
        "accept_surrender", {"take": ["gold"], "words": "Ладно, живи. Золото моё.",
                             **({"humiliate": True} if humiliating else {})},
    )]))], told=[], default="")
    dialogue.llm = script
    results: dict[str, str] = {}
    try:
        await link.detach(LOBART)
        await link.debug("player_hit", npc=LOBART)
        await link.story_effect(LOBART, [{"do": "attack", "reason": "AR_NONE"}])
        combat = [
            ["call", "NPC_ISINSTATE", ["inst", "SELF"], ["sym", "ZS_ATTACK"]],
            ["&&", ["call", "NPC_GETTARGET", ["inst", "SELF"]],
             ["==", ["call", "HLP_GETINSTANCEID", ["inst", "OTHER"]],
              ["call", "HLP_GETINSTANCEID", ["inst", "HERO"]]]],
        ]
        started = await _until_async(lambda: link.story_eval(LOBART, combat), lambda v: v == [1, 1], 8)
        roster_ready = await _until(lambda: fights(talk.people.get(LOBART)), 4)
        state = await link.story_eval(LOBART, combat)
        results["fight_started"] = (
            f"{'done' if started and roster_ready else 'failed'} (fighting/hero_target={state})"
        )
        if not started or not roster_ready:
            return _logged(results)
        typed_at = time.monotonic()
        await link.debug("type", text=phrase)
        accepted = await _until(lambda: bool(mercy.chronicle.facts("hero_yield")), 8)
        results["accepted"] = f"{'done' if accepted else 'failed'} (outcomes={script.outputs})"
        if not accepted:
            return _logged(results)
        at = time.monotonic()
        stopped = await _until_async(
            lambda: link.npc_view([LOBART]),
            lambda views: bool(views) and views[0].hp > 0 and views[0].weapon == "none"
            and not fights(talk.people.get(LOBART)), 3,
        )
        results["fight_stopped"] = f"{'done' if stopped else 'failed'} (seconds={time.monotonic() - at:.2f})"
        after = await link.story_eval(LOBART, gold)
        transferred = before[0] > 0 and after == [0, before[1] + before[0]]
        results["real_gold"] = (
            f"{'done' if transferred else 'failed'} (hero/npc before={before}, after={after})"
        )
        indices = await link.story_eval(LOBART, [
            ["var", n] for n in ("AIV_LASTFIGHTAGAINSTPLAYER", "AIV_LASTFIGHTCOMMENT")
        ])
        values = await link.story_eval(LOBART, [
            *(["field", ["inst", "SELF"], "AIVAR", i] for i in indices), ["var", "FIGHT_WON"],
        ])
        native = all(type(i) is int for i in indices) and values[:2] == [values[2], 0]
        results["native_outcome"] = f"{'done' if native else 'failed'} (indices={indices}, values={values})"
        if humiliating:
            pee = await _until_async(
                lambda: link.snapshot(LOBART, fresh=True),
                lambda snap: snap.player is not None and snap.player.kneeling
                and "S_PRAY" in snap.player.animations and "S_PEE" in snap.npc.animations
                and snap.player.distance <= PEE_DISTANCE_CM + 30, 20,
            )
            results["pee_state"] = f"{'done' if pee else 'failed'}"
            finished = await _until_async(
                lambda: link.snapshot(LOBART, fresh=True),
                lambda snap: mercy._scene is None and "S_PEE" not in snap.npc.animations, 15,
            )
            conv = dialogue.conversations.get(LOBART)
            if finished and conv is not None and conv.reply is not None:
                await conv.reply
            snap = await link.snapshot(LOBART, fresh=True)
            held = finished and snap.player and snap.player.kneeling and "S_PRAY" in snap.player.animations
            results["kneeling_held"] = f"{'done' if held else 'failed'}"
            traces = ([r for r in read_trace(dialogue.trace.path()) if r.get("type") == "combat_bark"
                       and r.get("reason") == "humiliate"] if dialogue.trace else [])
            said = any(r.get("outcome") == "said" for r in traces)
            results["humiliate_said"] = f"{'done' if said else 'failed'} ({traces})"
            if snap.player:
                x, y, z = snap.player.pos
                await link.debug("player_goto", pos=(x + 200, y, z))
                released = await _until_async(
                    lambda: link.snapshot(LOBART, fresh=True),
                    lambda state: state.player is not None and not state.player.kneeling
                    and "S_PRAY" not in state.player.animations
                    and "T_PRAY_2_STAND" not in state.player.animations, 8,
                )
                results["pose_released"] = f"{'done' if released else 'failed'}"
            else:
                results["pose_released"] = "failed (no player snapshot)"
            results["native_pose_exit"] = (
                "done" if any("T_PRAY_2_STAND" in row["hero_animations"] for row in samples)
                else "failed"
            )
            results["scene_samples"] = "done " + json.dumps(samples, ensure_ascii=False)
        return _logged(results)
    finally:
        link.listeners.remove(record)
        dialogue.llm, dialogue.quick = original, original_quick
        if mercy.barks is not None:
            mercy.barks.hits = original_hits


async def combat_barks(
    link: GameLink, dialogue: DialogueManager, barks: CombatBarks,
) -> dict[str, str]:
    """Start a fight, then measure a real hero hit with the participant's model slot ready."""
    await link.wait_world()
    await link.debug("skip_important", near=LOBART)
    await attach_when_free(link, LOBART)
    attached = True
    await link.debug("teleport", near=LOBART, distance=200)
    await link.act(LOBART, "stop")
    await link.act(LOBART, "wait", ms=100)
    quick, rng = dialogue.quick, barks.rng
    if quick is None:
        dialogue.quick = FakeBackend(script=[
            FakeStep("[pain] Не лезь!"), FakeStep("[pain] Ай, больно!"),
        ], delay=0)
    barks.rng = lambda: 0  # measure latency, with the probability gate covered by unit tests
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

    def ready() -> bool:
        warm = dialogue._prewarms.get(LOBART)
        slot = dialogue._quick_slots.get(LOBART)
        return (slot is not None and not dialogue._quick_busy[slot] and barks._eligible(LOBART)
                and not barks._busy(LOBART) and not barks._cooldown(LOBART)
                and not barks._world_busy() and (warm is None or warm.done()))

    results: dict[str, str] = {}
    link.listeners.insert(0, record)
    try:
        if not await _until(lambda: barks._eligible(LOBART), 8):
            return _logged({"ready": "failed (no living human in the event radius)"})
        # Free native fighters do not enter a controlled NPC's conversational incident turn.
        await link.detach(LOBART)
        attached = False
        await link.debug("player_hit", npc=LOBART)
        started = await _until(lambda: bool(hits), 3)
        await attach_when_free(link, LOBART)
        attached = True
        await link.act(LOBART, "stop")
        warmed = started and await _until(ready, 12)
        results["fight_started"] = (
            f"{'done' if warmed else 'failed'} (hits={len(hits)}, model_ready={warmed})"
        )
        if not warmed:
            return _logged(results)

        trace_start, hit_start = len(traces()), len(hits)
        before = await link.snapshot(LOBART, fresh=True)
        await link.detach(LOBART)
        attached = False
        await link.debug("player_hit", npc=LOBART)
        await _until(lambda: len(hits) > hit_start, 3)
        hit = hits[hit_start] if len(hits) > hit_start else None
        ok = (hit is not None and hit.by == "PLAYER" and hit.damage > 0
              and hit.hp == before.npc.hp - hit.damage and hit.kind == "melee")
        results["combat_hit"] = f"{'done' if ok else 'failed'} ({hit.model_dump() if hit else 'absent'})"
        await _until(lambda: any(r.get("outcome") == "said" for r in traces()[trace_start:]), 8)
        said = next((r for r in traces()[trace_start:] if r.get("outcome") == "said"), None)
        lines = [line for line, (text, _) in dialogue._lines.items() if said and text == said["text"]]
        await _until(lambda: any(voices.get(line) for line in lines), 8)
        ok = (said is not None and said.get("reason") == "hurt" and said.get("overlay") is True
              and any(voices.get(line) for line in lines))
        results["hurt_overlay"] = (
            f"{'done' if ok else 'failed'} (text={said.get('text') if said else None!r}, "
            f"first_token_ms={said.get('first_token_ms') if said else None}, "
            f"first_phrase_ms={said.get('first_phrase_ms') if said else None}, "
            f"voice_ms={said.get('voice_ms') if said else None}, voiced={any(voices.values())})"
        )
        return _logged(results)
    finally:
        link.listeners.remove(record)
        dialogue.quick, barks.rng = quick, rng
        if attached:
            await link.detach(LOBART)
