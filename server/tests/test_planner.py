import asyncio
import json
import random

import pytest
from websockets.asyncio.server import serve

from gothic_ai.brain import DeciderClient, LogApplier, NpcPlanner, PlannerConfig, Tier
from gothic_ai.brain.tiers import Placement, TierConfig, assign_tiers, raw_tier
from gothic_ai.protocol import RosterEntry, parse_game_message
from tests.fakes import Clock, FakeDecider


def entry(
    npc: str, metres: float, in_view: bool = False, activity: str = "routine", guild: int = 3
) -> RosterEntry:
    return RosterEntry(
        id=npc, name=npc.title(), guild=guild, distance=metres * 100, in_view=in_view, activity=activity
    )


async def settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


def make(decider: FakeDecider | None = None, **cfg) -> tuple[NpcPlanner, FakeDecider, LogApplier, Clock]:
    decider = decider or FakeDecider()
    applier, clock = LogApplier(), Clock()
    planner = NpcPlanner(decider, applier, config=PlannerConfig(**cfg), clock=clock, rng=random.Random(7))
    return planner, decider, applier, clock


async def step(planner: NpcPlanner, clock: Clock, seconds: float = 0.0) -> None:
    clock.t += seconds
    planner.tick(clock.t)
    await settle()


# --- tiers ---------------------------------------------------------------------------------


def test_tier_borders_with_hysteresis() -> None:
    cfg = TierConfig()

    def tier(m: float, prev=None, in_view=False):
        return raw_tier(Placement("x", m, in_view, True, 0), prev, cfg)

    assert tier(15) is Tier.NEAR and tier(25) is Tier.MID and tier(35) is Tier.FAR
    assert tier(25, in_view=True) is Tier.NEAR
    assert tier(22, prev=Tier.NEAR) is Tier.NEAR  # 20 m border + 15%
    assert tier(24, prev=Tier.NEAR) is Tier.MID
    assert tier(33, prev=Tier.MID) is Tier.MID and tier(33) is Tier.FAR
    assert raw_tier(Placement("x", 1, True, False, 0), None, cfg) is Tier.OFF


def test_tier_caps_keep_the_most_important() -> None:
    cfg = TierConfig(near_cap=2, mid_cap=1)
    ps = [Placement(f"n{i}", 5 + i, False, True, importance=10 - i) for i in range(4)]
    tiers = assign_tiers(ps, {}, cfg)
    assert [tiers[f"n{i}"] for i in range(4)] == [Tier.NEAR, Tier.NEAR, Tier.MID, Tier.FAR]


# --- planner -------------------------------------------------------------------------------


async def test_new_npcs_get_spread_first_checks() -> None:
    planner, decider, _, clock = make()
    planner.on_roster([entry(f"far{i}", 50) for i in range(100)], clock.t)
    checks = sorted(r.next_check - clock.t for r in planner.npcs.values())
    assert all(r.tier is Tier.FAR for r in planner.npcs.values())
    assert checks[0] >= 0 and checks[-1] <= 280 and checks[50] > 60  # spread over the period, no spike
    await step(planner, clock, 1)
    assert len(decider.calls) <= 3


async def test_urgent_event_goes_to_urgent_lane_and_is_applied() -> None:
    planner, decider, applier, clock = make()
    planner.on_roster([entry("SMITH", 5)], clock.t)
    planner.on_event("SMITH", "player_speech", clock.t, detail="Привет, кузнец!")
    await step(planner, clock)
    call = decider.calls[0]
    assert (call["lane"], call["deadline_ms"]) == ("urgent", 300)
    assert '"Привет, кузнец!"' in call["state"] and call["state"].startswith("You are Smith, a citizen")
    assert set(call["questions"]) == {"react"}
    await settle()
    react = [d for d in applier.recent if d.question == "react"]
    assert react and react[0].choice == "greet" and react[0].stimuli == ("player_speech",)
    # how the attitude changed is asked afterwards, in the background
    assert decider.calls[1]["lane"] == "background" and set(decider.calls[1]["questions"]) == {"attitude"}
    assert any(d.question == "attitude" and d.attitude == 3.0 for d in applier.recent)


async def test_events_debounce_into_one_request() -> None:
    planner, decider, _, clock = make()
    planner.on_roster([entry("GUARD", 8)], clock.t)
    planner.on_event("GUARD", "player_near", clock.t, distance_cm=300)
    await step(planner, clock, 0.1)
    planner.on_event("GUARD", "item_dropped", clock.t, detail="an apple")
    await step(planner, clock, 0.1)
    assert decider.calls == []  # still debouncing
    await step(planner, clock, 0.2)
    assert len(decider.calls) == 1
    call = decider.calls[0]
    assert call["lane"] == "background" and call["deadline_ms"] == 1000
    assert "came close to you (3 m away)" in call["state"] and "dropped an apple" in call["state"]
    assert "pick_up" in call["questions"]["react"]["criteria"]


async def test_scripted_events_are_context_only() -> None:
    planner, decider, _, clock = make()
    planner.on_roster([entry("GUARD", 8)], clock.t)
    planner.npcs["GUARD"].next_check = clock.t + 5
    planner.on_event("GUARD", "weapon_drawn", clock.t)
    await step(planner, clock, 1)
    assert decider.calls == []
    await step(planner, clock, 5)  # the timer fires: the drawn weapon is in its state
    assert "drew a weapon" in decider.calls[0]["state"]
    assert planner.stats.scripted_events == 1


async def test_mid_tier_reacts_only_to_salient_events() -> None:
    planner, decider, _, clock = make()
    planner.on_roster([entry("FARMER", 25)], clock.t)
    planner.npcs["FARMER"].next_check = clock.t + 100
    planner.on_event("FARMER", "player_near", clock.t)
    await step(planner, clock, 1)
    assert decider.calls == []
    planner.on_event("FARMER", "enter_room", clock.t)
    await step(planner, clock, 1)
    assert len(decider.calls) == 1 and decider.calls[0]["lane"] == "background"  # urgent only when NEAR


async def test_far_npc_gets_a_plan() -> None:
    planner, decider, applier, clock = make(FakeDecider(choice="socialize"))
    planner.on_roster([entry("HERMIT", 50)], clock.t)
    await step(planner, clock, 281)
    call = decider.calls[0]
    assert set(call["questions"]) == {"plan"} and call["deadline_ms"] == 30000
    assert applier.recent[0].question == "plan" and applier.recent[0].choice == "socialize"


async def test_new_event_outdates_the_request_in_flight() -> None:
    decider = FakeDecider()
    decider.gate = asyncio.Event()
    planner, _, applier, clock = make(decider)
    planner.on_roster([entry("BOSPER", 6)], clock.t)
    planner.on_event("BOSPER", "player_near", clock.t)
    await step(planner, clock, 0.3)
    first = decider.calls[0]["rid"]
    planner.on_event("BOSPER", "item_given", clock.t, detail="a bow")  # urgent: cancels the running one
    await settle()
    assert first in decider.cancelled
    await step(planner, clock, 0.05)
    assert len(decider.calls) == 1  # one in flight per npc
    decider.gate.set()
    await settle()
    assert planner.stats.discarded == 1 and not applier.recent  # the old answer is not applied
    await step(planner, clock, 0.05)
    assert decider.calls[1]["lane"] == "urgent" and "gave you a bow" in decider.calls[1]["state"]
    await settle()
    assert applier.recent[0].stimuli == ("item_given", "player_near")


async def test_npc_in_dialog_is_left_alone() -> None:
    planner, decider, _, clock = make()
    planner.on_roster([entry("XARDAS", 3)], clock.t)
    planner.on_message(
        parse_game_message(json.dumps({"type": "chat_open", "npc": "XARDAS", "name": "Ксардас"}))
    )
    assert planner.npcs["XARDAS"].tier is Tier.OFF
    planner.on_event("XARDAS", "player_speech", clock.t, detail="Здравствуй")
    planner.on_roster([entry("XARDAS", 3)], clock.t)  # a stale roster does not end the dialog
    await step(planner, clock, 30)
    assert decider.calls == []
    planner.on_message(parse_game_message(json.dumps({"type": "chat_close", "npc": "XARDAS"})))
    assert planner.npcs["XARDAS"].tier is Tier.NEAR
    planner.set_agenda("XARDAS", "make the hero go to Lord Hagen")
    await step(planner, clock, 1.6)  # catch-up decision after the dialog
    assert len(decider.calls) == 1 and "What you want: make the hero go" in decider.calls[0]["state"]


async def test_budget_and_stale_requests() -> None:
    planner, decider, _, clock = make(bg_rate=2.0)
    planner.on_roster([entry(f"n{i}", 8) for i in range(10)], clock.t)
    for rec in planner.npcs.values():
        rec.next_check = clock.t
        rec.agenda = "sell the hero some bread"
    await step(planner, clock, 0.0)
    assert len(decider.calls) == 2  # the rate budget allows a burst of 2
    for _ in range(6):
        await step(planner, clock, 0.5)
    assert planner.stats.stale > 0  # NEAR requests waiting > 2 s are dropped, not served late
    assert len(decider.calls) < 10


async def test_unsure_or_no_action_changes_nothing() -> None:
    planner, _, applier, clock = make(FakeDecider(choice="comment", confidence=0.3))
    planner.on_roster([entry("A", 5)], clock.t)
    planner.on_event("A", "player_speech", clock.t, detail="эй")
    await step(planner, clock)
    assert planner.stats.unsure == 1 and not applier.recent

    planner, _, applier, clock = make(FakeDecider(choice="continue"))
    planner.on_roster([entry("A", 5)], clock.t)
    planner.on_event("A", "player_speech", clock.t, detail="эй")
    await step(planner, clock)
    assert planner.stats.no_action == 1 and not applier.recent


async def test_decider_trouble_stretches_timers() -> None:
    planner, _, _, clock = make(FakeDecider(kind="expired"))
    planner.on_roster([entry(f"m{i}", 25) for i in range(5)], clock.t)
    for rec in planner.npcs.values():
        rec.next_check = clock.t
        rec.agenda = "ask the hero for news"
    await step(planner, clock)
    assert planner.slowdown > 1.5
    assert all(r.next_check - clock.t >= 0 for r in planner.npcs.values())
    assert planner._period(Tier.MID)[0] > 30 and planner._period(Tier.NEAR) == (6.0, 10.0)


async def test_idle_respects_the_minimum_gap() -> None:
    planner, decider, _, clock = make()
    planner.on_roster([entry("A", 5)], clock.t)
    planner.on_event("A", "player_speech", clock.t, detail="эй")
    await step(planner, clock)
    n = len(decider.calls)
    planner.on_idle("A", clock.t)
    await step(planner, clock, 0.5)
    assert len(decider.calls) == n  # decided 0.5 s ago: wait
    await step(planner, clock, 1.6)
    assert len(decider.calls) == n + 1


async def test_full_roster_drops_missing_npcs_and_promotion_catches_up() -> None:
    planner, decider, _, clock = make()
    planner.on_roster([entry("A", 50), entry("B", 50)], clock.t)
    planner.npcs["A"].next_check = clock.t + 200
    planner.set_agenda("A", "warn the hero about wolves")
    planner.on_roster([entry("A", 10)], clock.t)  # B gone, A came close
    assert set(planner.npcs) == {"A"} and planner.npcs["A"].tier is Tier.NEAR
    assert planner.npcs["A"].next_check <= clock.t + 1.5
    await step(planner, clock, 1.6)
    assert set(decider.calls[0]["questions"]) == {"react"}


async def test_messages_from_the_game() -> None:
    planner, decider, _, clock = make()
    msgs = [
        {
            "type": "npc_roster",
            "t": 1,
            "hour": 21,
            "minute": 5,
            "npcs": [
                {"id": "VLK_400_GUARD", "name": "Стражник", "guild": 2, "distance": 400, "in_view": True}
            ],
        },
        {"type": "npc_event", "npc": "VLK_400_GUARD", "kind": "enter_room", "distance": 300},
    ]
    for m in msgs:
        planner.on_message(parse_game_message(json.dumps(m)))
    await step(planner, clock)
    state = decider.calls[0]["state"]
    assert "a militiaman of Khorinis" in state and "21:05, evening" in state and "looking at you" in state
    planner.on_message(parse_game_message(json.dumps({"type": "loading"})))
    assert planner.npcs == {}


# --- decider client ------------------------------------------------------------------------


async def test_decider_client_round_trip_and_disconnect() -> None:
    hold = asyncio.Event()

    async def service(ws) -> None:
        await ws.send(json.dumps({"type": "hello", "model": "fake"}))
        async for raw in ws:
            msg = json.loads(raw)
            if msg["type"] == "decide" and msg["id"] == "slow":
                await hold.wait()
                await ws.close()
                return
            if msg["type"] == "decide":
                await ws.send(
                    json.dumps(
                        {
                            "type": "answers",
                            "id": msg["id"],
                            "answers": {"react": {}},
                            "queue_ms": 1.0,
                            "infer_ms": 2.0,
                        }
                    )
                )

    async with serve(service, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        client = DeciderClient(f"ws://127.0.0.1:{port}")
        out = await client.decide("a", "urgent", 1.0, 300, "state", {"react": {}})
        assert out.kind == "answers" and out.infer_ms == 2.0 and client.model == "fake"
        slow = asyncio.create_task(client.decide("slow", "background", 0, 5000, "s", {"react": {}}))
        await asyncio.sleep(0.05)
        hold.set()
        out = await slow
        assert (out.kind, out.detail) == ("error", "decider disconnected")
        await client.aclose()

    down = DeciderClient("ws://127.0.0.1:1", retry_s=60, connect_timeout=0.5)
    out = await down.decide("x", "urgent", 0, 300, "s", {"react": {}})
    assert out.kind == "error"
    assert (await down.decide("y", "urgent", 0, 300, "s", {"react": {}})).detail == "decider unreachable"


@pytest.mark.parametrize(
    "hour,part", [(3, "night"), (9, "morning"), (14, "afternoon"), (20, "evening"), (23, "night")]
)
def test_clock_text(hour: int, part: str) -> None:
    from gothic_ai.brain.situation import clock_text

    assert clock_text(hour, 0).endswith(part)


async def test_quiet_npcs_are_not_asked() -> None:
    planner, decider, _, clock = make()
    planner.on_roster([entry("PEPE", 8)], clock.t)
    planner.npcs["PEPE"].next_check = clock.t
    await step(planner, clock, 0.1)
    planner.on_idle("PEPE", clock.t)
    await step(planner, clock, 3)
    assert decider.calls == [] and planner.stats.quiet == 2  # nothing happened, nothing wanted
    planner.set_agenda("PEPE", "get help against the wolves that take your sheep")
    planner.on_idle("PEPE", clock.t)
    await step(planner, clock, 0.1)
    assert "What you want: get help against the wolves" in decider.calls[0]["state"]


@pytest.mark.parametrize(
    "choice,confidence,applied",
    [
        ("approach_talk", 0.5, False),
        ("approach_talk", 0.7, True),
        ("look_at", 0.3, True),
        ("comment", 0.45, False),
    ],
)
async def test_per_option_thresholds(choice: str, confidence: float, applied: bool) -> None:
    planner, _, applier, clock = make(FakeDecider(choice=choice, confidence=confidence), ask_attitude=False)
    planner.on_roster([entry("A", 5)], clock.t)
    planner.on_event("A", "player_speech", clock.t, detail="эй")
    await step(planner, clock)
    assert bool(applier.recent) is applied


async def test_an_action_is_not_repeated_within_its_cooldown() -> None:
    planner, _, applier, clock = make(FakeDecider(choice="approach_talk", confidence=0.9), ask_attitude=False)
    planner.on_roster([entry("PABLO", 6)], clock.t)
    planner.set_agenda("PABLO", "find the thief")
    for _ in range(5):
        planner.on_event("PABLO", "player_speech", clock.t, detail="эй")
        await step(planner, clock)
        clock.t += 10
    assert len(applier.recent) == 1 and planner.stats.cooldown == 4
    clock.t += 80
    planner.on_event("PABLO", "player_speech", clock.t, detail="эй")
    await step(planner, clock)
    assert len(applier.recent) == 2  # 120 s later it may come again


async def test_npcs_nearby_overhear_the_player() -> None:
    planner, decider, _, clock = make()
    planner.on_roster([entry("XARDAS", 2), entry("LESTER", 6), entry("FAR", 30)], clock.t)
    planner.on_message(
        parse_game_message(json.dumps({"type": "player_text", "npc": "XARDAS", "text": "Где Ли?"}))
    )
    await step(planner, clock)
    reacts = [c for c in decider.calls if "react" in c["questions"]]
    assert [c["rid"].split("#")[0] for c in reacts] == ["LESTER"]  # not the partner, not the far one
    assert '"Где Ли?"' in reacts[0]["state"]


def test_decisions_become_soft_actions() -> None:
    from gothic_ai.brain.apply import Decision, actions_for

    def d(choice: str, stimuli: tuple[str, ...] = (), question: str = "react") -> Decision:
        return Decision("A", question, choice, 0.9, {}, stimuli)

    assert actions_for(d("approach_talk")) == [("go_to_npc", {})]
    assert actions_for(d("comment", ("player_near", "enter_room"))) == [("say_svm", {"svm": "GETOUTOFHERE"})]
    assert actions_for(d("comment", ("player_near",))) == [("look_at", {"ms": 3000})]
    assert actions_for(d("keep_routine", question="plan")) == [] and actions_for(d("pick_up")) == []


async def test_game_applier_sends_npc_act() -> None:
    from gothic_ai.brain import Decision, GameApplier

    class Link:
        def __init__(self) -> None:
            self.sent: list[tuple] = []

        async def npc_act(self, npc, action, **args):
            self.sent.append((npc, action, args))
            return "n1"

    link = Link()
    applier = GameApplier(link)  # type: ignore[arg-type]
    applier.apply(Decision("VLK_400", "react", "tell_to_leave", 0.8, {}, ("enter_room",)))
    await settle()
    assert link.sent == [("VLK_400", "say_svm", {"svm": "GETOUTOFHERE"})]
    assert applier.recent[0].choice == "tell_to_leave"


def test_protocol_v5_messages() -> None:
    from gothic_ai.protocol import NpcActCmd, NpcActResult, NpcIdle

    idle = parse_game_message('{"type":"npc_idle","npc":"A","act":"n3","reason":"done"}')
    assert isinstance(idle, NpcIdle) and (idle.act, idle.reason) == ("n3", "done")
    res = parse_game_message(
        '{"type":"npc_act_result","id":"n3","npc":"A","status":"rejected","detail":"busy"}'
    )
    assert isinstance(res, NpcActResult) and res.status == "rejected"
    cmd = NpcActCmd(id="n4", npc="A", action="say_svm", args={"svm": "NOTNOW"})
    assert json.loads(cmd.to_json()) == {
        "type": "npc_act",
        "id": "n4",
        "npc": "A",
        "action": "say_svm",
        "args": {"svm": "NOTNOW"},
    }
