import asyncio

from gothic_ai.brain.incident import IncidentEvent, IncidentTracker, escalation_reason
from gothic_ai.brain.reflex import Reflexes, react_menu
from gothic_ai.brain.situation import NpcContext
from gothic_ai.budget import Budget
from gothic_ai.dialogue import DialogueManager
from gothic_ai.executor import Intent
from gothic_ai.incidents import IncidentDesk
from gothic_ai.llm import FakeBackend, FakeStep, ToolCall
from gothic_ai.persona import PersonaProvider
from gothic_ai.trace import TraceWriter, read
from tests.fake_game import FakeGame, connected
from tests.fakes import Clock, FakeDecider
from tests.test_dialogue import XARDAS, _until

WOLF = {"id": "WOLF", "name": "Волк", "distance": 400.0, "guild": 0, "dead": False}


def test_events_close_together_make_one_incident():
    tracker = IncidentTracker(gap_s=20)
    a = tracker.add(XARDAS, IncidentEvent("attacked", 100.0, "WOLF", "Волк"))
    b = tracker.add(XARDAS, IncidentEvent("attacked", 105.0, "WOLF", "Волк"))
    assert a is b and b.version == 2 and b.hits_from("WOLF", 95.0) == 2
    assert tracker.is_current(XARDAS, a.id, 110.0)
    c = tracker.add(XARDAS, IncidentEvent("attacked", 130.0, "WOLF", "Волк"))  # after a quiet gap
    assert c is not a and not tracker.is_current(XARDAS, a.id, 130.0)


def test_escalation_needs_salience_a_listener_and_happens_once_per_incident():
    tracker = IncidentTracker()
    inc = tracker.add(XARDAS, IncidentEvent("down", 100.0))
    assert escalation_reason(inc, 100.0, 300.0) is None  # not worth words
    inc = tracker.add("A", IncidentEvent("attacked", 100.0, "WOLF", "Волк"))
    assert escalation_reason(inc, 100.0, 5000.0) is None  # nobody hears it
    assert escalation_reason(inc, 100.0, 300.0) == "new incident"
    inc.escalated_version, inc.escalated_at = inc.version, 100.0
    assert escalation_reason(inc, 101.0, 300.0) is None  # already called
    tracker.add("A", IncidentEvent("hero_attacked", 105.0, "WOLF", "Волк", "PLAYER"))
    assert escalation_reason(inc, 110.0, 300.0) is None  # changed, but too soon
    assert escalation_reason(inc, 131.0, 300.0) == "incident changed"


def test_budget_refuses_calls_nobody_asked_for_but_counts_the_players():
    clock = Clock()
    budget = Budget(per_npc_per_min=2, total_per_min=3, clock=clock)
    assert budget.allow("A") and budget.allow("A") and not budget.allow("A")  # per npc
    assert budget.allow("B") and not budget.allow("B")  # the server's total
    clock.t += 30  # refills: one more per npc and 1.5 in total
    assert budget.allow("A")
    budget.note("A")  # the player spoke: never refused, but it uses the quota
    assert not budget.allow("A")


def test_the_intent_narrows_the_bodys_options():
    assert react_menu(Intent("flee"), "WOLF") == ["flee", "back_away", "run_to_hero"]
    assert react_menu(Intent("seek_help", target="PC_THIEF"), "WOLF") == ["run_to_helper", "flee", "stay"]
    assert "fight_back" in react_menu(Intent("guard_hero"), "WOLF")
    assert "run_to_hero" not in react_menu(None, "PLAYER")  # the hero is the danger


async def test_threats_are_answered_in_shadow_and_unsure_answers_are_not_applied():
    tracker = IncidentTracker()
    inc = tracker.add(XARDAS, IncidentEvent("attacked", 1.0, "WOLF", "Волк"))
    ctx = NpcContext(XARDAS, "Ксардас", -1, 300.0, True, traits="A necromancer.")

    shadow = await Reflexes(FakeDecider("fight_back", 0.95)).react(ctx, inc, None)
    assert (
        shadow is not None and shadow.choice == "fight_back" and not shadow.applied and shadow.why == "shadow"
    )
    assert "You are Ксардас. A necromancer." in shadow.state and "Волк attacks you." in shadow.state

    unsure = await Reflexes(FakeDecider("run_to_hero", 0.3)).react(ctx, inc, None)
    assert unsure is not None and not unsure.applied and unsure.why.startswith("unsure")

    sure = await Reflexes(FakeDecider("run_to_hero", 0.8)).react(ctx, inc, None)
    assert sure is not None and sure.applied and sure.actions == [("go_to_npc", {"npc": "PLAYER"})]


async def _scene(decider: FakeDecider | None, llm: FakeBackend, fake: FakeGame, tmp_path, budget=None):
    """Xardas in a chat, a desk watching him; yields inside the connection."""
    link_cm = connected(fake)
    link = await link_cm.__aenter__()
    trace = TraceWriter(tmp_path)
    dialogue = DialogueManager(link, llm, PersonaProvider(None), greet=False, trace=trace)
    reflexes = Reflexes(decider) if decider is not None else None
    desk = IncidentDesk(link, dialogue, reflexes, budget or Budget(), trace, settle_s=0.05)
    await link.attach(XARDAS)
    dialogue.open(XARDAS, "Ксардас")
    await _until(lambda: XARDAS in desk.attached)
    return link_cm, link, dialogue, desk, trace


async def test_the_body_reacts_at_once_and_the_words_come_after(tmp_path):
    fake = FakeGame()
    fake.nearby.append(WOLF)
    llm = FakeBackend("Проклятая тварь! Ко мне, герой!", delay=0.05)
    cm, link, dialogue, desk, trace = await _scene(FakeDecider("run_to_hero", 0.9), llm, fake, tmp_path)
    try:
        await link.snapshot(XARDAS, fresh=True)
        await fake.perception(XARDAS, "ASSESSDAMAGE", "WOLF")
        await _until(lambda: fake.said, timeout=5)
    finally:
        await cm.__aexit__(None, None, None)

    kinds = [(m["type"], m.get("action")) for m in fake.received if m["type"] in ("action", "say")]
    assert kinds.index(("action", "go_to_npc")) < kinds.index(("say", None))  # the body first
    cue = llm.calls[0][1][-1]
    assert (
        cue.role == "developer"
        and "Волк нападает на тебя" in cue.content
        and "ты бежишь к герою" in cue.content
    )
    (reflex,) = [r for r in read(trace.path()) if r["type"] == "reflex"]
    assert reflex["choice"] == "run_to_hero" and reflex["applied"] and reflex["incident"] == 1


async def test_a_second_hit_hands_the_npc_to_the_games_scripts(tmp_path):
    fake = FakeGame()
    fake.nearby.append(WOLF)
    cm, link, dialogue, desk, trace = await _scene(None, FakeBackend("Ах ты!", delay=0.2), fake, tmp_path)
    try:
        await link.snapshot(XARDAS, fresh=True)
        await fake.perception(XARDAS, "ASSESSDAMAGE", "WOLF")
        await asyncio.sleep(0.05)
        await fake.perception(XARDAS, "ASSESSDAMAGE", "WOLF")
        await _until(lambda: XARDAS not in fake.attached, timeout=5)
    finally:
        await cm.__aexit__(None, None, None)

    types = [m["type"] for m in fake.received]
    assert "chat_end" in types
    assert any(m["type"] == "action" and m["action"] == "return_to_routine" for m in fake.received)
    notes = [m.content for m in dialogue.conversations[XARDAS].history if m.role == "developer"]
    assert any("защищаешься" in n for n in notes)


async def test_words_about_an_incident_that_is_over_are_dropped(tmp_path):
    fake = FakeGame()
    fake.nearby.append(WOLF)
    llm = FakeBackend("Я тебе покажу!", delay=0.3)
    cm, link, dialogue, desk, trace = await _scene(None, llm, fake, tmp_path)
    try:
        await link.snapshot(XARDAS, fresh=True)
        await fake.perception(XARDAS, "ASSESSDAMAGE", "WOLF")
        await _until(lambda: llm.calls, timeout=5)
        await fake.send(type="detached", npc=XARDAS, reason="down")  # knocked out while the model thinks
        await _until(lambda: dialogue.conversations[XARDAS].reply.done(), timeout=5)
    finally:
        await cm.__aexit__(None, None, None)

    assert fake.said == []
    notes = [m.content for m in dialogue.conversations[XARDAS].history if m.role == "developer"]
    assert "[Тебя сбили с ног]" in notes
    turns = [r for r in read(trace.path()) if r["type"] == "turn"]
    assert turns[-1]["status"] == "stale"


async def test_no_words_without_budget(tmp_path):
    fake = FakeGame()
    fake.nearby.append(WOLF)
    llm = FakeBackend("Прочь!", delay=0)
    budget = Budget(per_npc_per_min=0, total_per_min=0)
    cm, link, dialogue, desk, trace = await _scene(None, llm, fake, tmp_path, budget)
    try:
        await link.snapshot(XARDAS, fresh=True)
        await fake.perception(XARDAS, "ASSESSDAMAGE", "WOLF")
        await asyncio.sleep(0.3)
    finally:
        await cm.__aexit__(None, None, None)
    assert llm.calls == []


async def test_the_body_gestures_while_the_npc_thinks_of_an_answer(tmp_path):
    fake = FakeGame()
    llm = FakeBackend("Да, так и есть.", delay=0.1)
    cm, link, dialogue, desk, trace = await _scene(FakeDecider("nod", 0.9), llm, fake, tmp_path)
    try:
        await dialogue.say_as_player(XARDAS, "Ты ведь Ксардас?")
        await _until(lambda: fake.said, timeout=5)
    finally:
        await cm.__aexit__(None, None, None)

    kinds = [(m["type"], m.get("action")) for m in fake.received if m["type"] in ("action", "say")]
    assert kinds[0] == ("action", "play_ani")
    nod = next(m for m in fake.received if m["type"] == "action")
    assert nod["args"] == {"name": "T_YES"}


async def test_a_fight_ends_with_the_incident_and_the_old_intent_comes_back(tmp_path):
    fake = FakeGame()
    fake.nearby.append(WOLF)
    follow = {
        "goal": "follow_hero",
        "target": None,
        "attitude": "friendly",
        "agenda": "wants the hero to see Hagen",
    }
    fight = {"goal": "fight", "target": "WOLF", "attitude": "friendly", "agenda": None}  # null: agenda kept
    llm = FakeBackend(
        script=[
            FakeStep("Хорошо, пойдём.", [ToolCall("set_intent", follow)]),
            FakeStep("Прочь, тварь!", [ToolCall("set_intent", fight)]),
        ],
        delay=0,
    )
    link_cm = connected(fake)
    link = await link_cm.__aenter__()
    try:
        dialogue = DialogueManager(link, llm, PersonaProvider(None), greet=False)
        desk = IncidentDesk(link, dialogue, None, Budget(), None, settle_s=0.05, gap_s=0.5)
        await link.attach(XARDAS)
        conv = dialogue.open(XARDAS, "Ксардас")
        await _until(lambda: XARDAS in desk.attached)
        await link.snapshot(XARDAS, fresh=True)
        await dialogue.say_as_player(XARDAS, "Пойдём со мной")
        await fake.perception(XARDAS, "ASSESSDAMAGE", "WOLF")
        await _until(lambda: conv.intent.goal == "fight", timeout=5)
        assert conv.intent.agenda == "wants the hero to see Hagen"
        await _until(lambda: conv.intent.goal == "follow_hero", timeout=5)  # the wolf is gone
    finally:
        await link_cm.__aexit__(None, None, None)
    assert conv.intent.attitude == "friendly" and conv.intent.agenda == "wants the hero to see Hagen"


async def test_fights_are_carried_out_only_when_threats_may_be_applied():
    tracker = IncidentTracker()
    inc = tracker.add(XARDAS, IncidentEvent("attacked", 1.0, "WOLF", "Волк"))
    ctx = NpcContext(XARDAS, "Ксардас", -1, 300.0, True)
    allowed = await Reflexes(FakeDecider("fight_back", 0.9), apply_threats=True).react(ctx, inc, None)
    assert allowed is not None and allowed.applied and allowed.actions == [("fight", {"npc": "WOLF"})]
    shadow = await Reflexes(FakeDecider("fight_back", 0.9)).react(ctx, inc, None)
    assert shadow is not None and not shadow.applied and shadow.why == "shadow"


async def test_lines_said_on_the_move_wait_for_each_other(tmp_path):
    fake = FakeGame()
    llm = FakeBackend("Волки! Все сюда, на помощь! Бегите к ферме, быстрее!", delay=0)
    async with connected(fake) as link:
        dialogue = DialogueManager(link, llm, PersonaProvider(None), greet=False)
        dialogue.open(XARDAS, "Ксардас")
        t0 = asyncio.get_running_loop().time()
        task = dialogue.event_turn(XARDAS, "[Событие: волки]", overlay=True)
        assert task is not None
        await asyncio.wait_for(task, 10)
        await _until(lambda: len(fake.said) >= 2)
        took = asyncio.get_running_loop().time() - t0
    says = [m for m in fake.received if m["type"] == "say"]
    assert all(m["overlay"] for m in says)
    assert took >= 1.5  # the second line waited for the first one to be said
