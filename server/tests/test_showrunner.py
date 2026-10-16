"""A showrunner spends the save branch's budget before asking its model or builders."""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gothic_ai.cast import Cast, Person
from gothic_ai.characters import Card
from gothic_ai.llm import ToolCall
from gothic_ai.llm.fake import FakeBackend, FakeStep
from gothic_ai.memory import MemoryStore
from gothic_ai.occasions import Occasions
from gothic_ai.places import PlaceBook
from gothic_ai.protocol import Loading, NpcSpec, RoutineEntry, Session
from gothic_ai.questbook import Goal, Quest, Stage, Talk
from gothic_ai.showrunner import (
    CACHE_KEY,
    MAX_LINES_PER_CHAPTER,
    MAX_OPEN_LINES,
    MAX_PEOPLE_PER_CHAPTER,
    MIN_GAME_MINUTES,
    MIN_REAL_SECONDS,
    PROPOSE,
    PROPOSE_TOOL,
    Proposal,
    Showrunner,
)
from gothic_ai.story import QUEST as QUEST_RECORD
from tests import test_authoring
from tests.fake_game import FakeGame
from tests.fakes import Clock
from tests.test_authoring import BOOK, _author
from tests.test_director import HILDA, LOBART, NAMES, VINO, XARDAS
from tests.test_director import QUEST as SCRIPT_QUEST
from tests.test_occasions import load

HOME, YARD, FIELD = "FARM1_INHOUSE", "FARM1_ENTRANCE", "FARM1_FIELD"
PREMISE = "Ночью пропала хозяйская пила. Сегодня работа встала, а до дождя надо починить забор."
OWN = "DIR_NPC_1"
DEAD = "BAU_999_DEAD"
graph = test_authoring.graph


def proposal(**change):
    return {
        "kind": "quest",
        "giver": HILDA,
        "place": "",
        "new_people": False,
        "premise": PREMISE,
        "reason": "Герой пришёл как раз к началу работы.",
        **change,
    }


def occasion(**change):
    return {
        "fact": "occasion",
        "kind": "arrived",
        "place": HOME,
        "waypoint": "NW_FARM1_INHOUSE_01",
        "people": [HILDA, LOBART],
        "day": 10,
        "hour": 8,
        "minute": 0,
        **change,
    }


def line(**change):
    return {
        "fact": "line",
        "occasion": occasion(day=1),
        "kind": "quest",
        "giver": HILDA,
        "premise": "Старое поручение хозяйки.",
        "quests": ["DIR_Q1"],
        "characters": [],
        "chapter": 1,
        "day": 1,
        "hour": 8,
        "minute": 0,
        "wall_time": 1000.0,
        **change,
    }


def quest(id="DIR_Q1", giver=HILDA, title="Пропавшая пила"):
    talk = Talk("Нужна помощь?", [{"speaker": "self", "text": "Найди мою пилу."}])
    return Quest(
        id=id,
        giver=giver,
        title=title,
        give=talk,
        stages=[Stage("Надо помочь хозяйке.", [Goal("talk", npc=giver, talk=talk)])],
        done_journal="Я помог хозяйке.",
        names=dict(NAMES),
    )


def spec(npc=OWN, name="Рутгер", point="NW_FARM1_OUT_01"):
    return NpcSpec(
        npc=npc,
        name=name,
        like="VLK_4000_BUERGER",
        at=point,
        routine=[RoutineEntry(start="00:00", end="24:00", state="ZS_STAND_WP", waypoint=point)],
    )


@dataclass
class World:
    fake: FakeGame
    clock: Clock
    showrunner: Showrunner
    author: object
    chains: object
    casting: object
    llm: FakeBackend

    @property
    def chronicle(self):
        return self.showrunner.chronicle

    async def keep(self, *quests):
        await self.author.patches.make([{"op": QUEST_RECORD, "quest": q.to_dict()} for q in quests], [])

    def finish(self, *ids, state="done"):
        suffix = {"done": "done", "failed": "failed", "obsolete": "obsolete"}[state]
        for id in ids:
            self.chronicle.record({"fact": f"story_quest_{suffix}", "id": id})


@asynccontextmanager
async def world(story_graph, *proposals):
    fake, clock = FakeGame(branch="A"), Clock()
    clock.t = 100_000.0
    llm = FakeBackend(
        script=[FakeStep("", [ToolCall(PROPOSE, p, f"line{i}")]) for i, p in enumerate(proposals)],
        delay=0,
    )
    link_cm, author, chronicle = await _author(fake, llm, story_graph, MemoryStore(":memory:"))
    try:
        data = deepcopy(BOOK)
        for place, kind in zip(data["places"], ["house", "yard", "field", "street"], strict=True):
            place["kind"] = kind
        author.places = PlaceBook(data)
        author.book.places = author.places
        fake.day, fake.hour, fake.minute = 10, 8, 0
        fake.nearest_waypoint = "NW_FARM1_INHOUSE_01"
        fake.people = [
            {
                "npc": npc,
                "name": NAMES.get(npc, "Умерший крестьянин"),
                "routine": [
                    {
                        "start": 0,
                        "end": 1440,
                        "state": "ZS_STAND_WP",
                        "waypoint": "NW_FARM1_INHOUSE_01",
                    }
                ],
            }
            for npc in (HILDA, LOBART, VINO, XARDAS, DEAD)
        ]
        for npc in NAMES:
            fake.npc_states[npc]["distance"] = 200 if npc != VINO else 6000
        fake.npc_states[DEAD] = {"state": "dead", "name": "Умерший крестьянин", "distance": 100}
        author.cards[HILDA] = Card(
            HILDA,
            "Хильда",
            "Ты — хозяйка фермы. Не терпишь бездельников.",
            "A practical farmer.",
            3,
            3,
            3,
            3,
            3,
            3,
        )
        cast = Cast(author.link, author.patches, chronicle)
        casting = SimpleNamespace(cast=cast, character=AsyncMock())
        chains = SimpleNamespace(chain=AsyncMock())
        occasions = Occasions(author.link, chronicle, author.places, clock=clock, busy_poll_s=0.01)
        runner = Showrunner(occasions, author, chains, casting, llm, clock=clock)
        state = World(fake, clock, runner, author, chains, casting, llm)

        async def make_quest(npc, premise="", *, requires=None, follows=""):
            q = quest(id=author.book.next_id(), giver=npc)
            await state.keep(q)
            return q

        async def make_chain(
            npc, *, new_people=False, premise="", max_people=None, requires=None, follows=""
        ):
            first = quest(id=author.book.next_id(), giver=npc)
            second = quest(id=author.book.next_id({first.id}), giver=npc, title="След пилы")
            await state.keep(first, second)
            return [first, second]

        async def make_character(place, *, about="", premise="", **kwargs):
            person = spec(npc=cast.next_id())
            await author.patches.introduce(person)
            fake.npc_states[person.npc] = {"state": "alive", "name": person.name, "distance": 300}
            return person

        author.quest = AsyncMock(side_effect=make_quest)
        chains.chain.side_effect = make_chain
        casting.character.side_effect = make_character
        yield state
    finally:
        await link_cm.__aexit__(None, None, None)


@pytest.mark.parametrize("rule", ["open", "chapter", "game_time", "wall_time"])
async def test_budget_denies_before_the_model_and_records_why(graph, rule):
    async with world(graph, proposal()) as w:
        if rule == "open":
            for i in range(MAX_OPEN_LINES):
                w.chronicle.record(line(giver=f"GIVER_{i}", quests=[f"DIR_Q{i + 1}"]))
        elif rule == "chapter":
            for i in range(MAX_LINES_PER_CHAPTER):
                w.chronicle.record(line(quests=[f"DIR_Q{i + 1}"]))
                w.finish(f"DIR_Q{i + 1}")
        else:
            previous = line(
                day=9 if rule == "game_time" else 8,
                hour=8,
                wall_time=w.clock.t - (MIN_REAL_SECONDS if rule == "game_time" else MIN_REAL_SECONDS - 1),
            )
            w.chronicle.record(previous)
            w.finish("DIR_Q1")
            if rule == "game_time":
                w.fake.hour, w.fake.minute = 7, 59
        result = await w.showrunner.decide(occasion())
        assert result["fact"] == "line_skipped" and result["why"]
        assert w.chronicle.facts("line_skipped") == [result]
        assert result["occasion"] == occasion()
        assert w.llm.calls == []
        w.author.quest.assert_not_called()
        w.chains.chain.assert_not_called()
        w.casting.character.assert_not_called()


async def test_giver_and_people_budget_can_skip_when_no_builder_kind_fits(graph):
    async with world(graph, proposal()) as w:
        # Hilda has an unfinished line; all eight newcomers were already introduced by it.
        w.chronicle.record(line(characters=[f"DIR_NPC_{i}" for i in range(8)]))
        for npc in (LOBART, VINO):
            w.fake.npc_states[npc]["state"] = "dead"
        budget = w.showrunner.budget(1, occasion())
        assert budget.people_left == 0 and budget.busy_givers == {HILDA}
        result = await w.showrunner.decide(occasion())
        assert result["fact"] == "line_skipped" and result["why"]
        assert w.llm.calls == []


async def test_both_cooldown_boundaries_allow_a_model_call(graph):
    async with world(graph, proposal(kind="none")) as w:
        previous = line(day=9, wall_time=w.clock.t - MIN_REAL_SECONDS)
        w.chronicle.record(previous)
        w.finish("DIR_Q1")
        assert MIN_GAME_MINUTES == 24 * 60
        assert w.showrunner.budget(1, occasion()).reasons == []
        result = await w.showrunner.decide(occasion())
        assert result["fact"] == "line_none" and len(w.llm.calls) == 1


@pytest.mark.parametrize("state", ["open", "given", "done", "failed", "obsolete"])
async def test_a_line_stays_open_until_every_quest_is_finished(graph, state):
    async with world(graph) as w:
        await w.keep(quest(), quest(id="DIR_Q2"))
        w.finish("DIR_Q1")
        if state != "open":
            suffix = {"given": "given", "done": "done", "failed": "failed", "obsolete": "obsolete"}[state]
            w.chronicle.record({"fact": f"story_quest_{suffix}", "id": "DIR_Q2"})
        assert w.showrunner.is_open(line(quests=["DIR_Q1", "DIR_Q2"])) is (state in {"open", "given"})
        assert not w.showrunner.is_open(line(quests=[]))


async def test_budget_is_chapter_local_but_open_lines_and_cooldown_cross_chapters(graph):
    async with world(graph) as w:
        w.chronicle.record(line(chapter=1, characters=[f"DIR_NPC_{i}" for i in range(8)]))
        budget = w.showrunner.budget(2, occasion())
        assert budget.open_left == MAX_OPEN_LINES - 1
        assert budget.lines_left == MAX_LINES_PER_CHAPTER
        assert budget.people_left == MAX_PEOPLE_PER_CHAPTER
        assert budget.busy_givers == {HILDA}
        w.chronicle.record(line(chapter=2, quests=[], characters=[OWN, "DIR_NPC_2", "DIR_NPC_3"]))
        w.chronicle.record(line(chapter=2, quests=[], characters=[OWN]))
        assert w.showrunner.budget(2, occasion()).people_left == MAX_PEOPLE_PER_CHAPTER - 3


async def test_brief_has_local_cards_time_quests_deeds_history_and_only_free_living_candidates(graph):
    async with world(graph) as w:
        await w.keep(quest(title="Уже было"))
        await w.keep(quest(id="DIR_Q2", title="Старое поручение строителя"))
        w.finish("DIR_Q2")
        w.chronicle.record(line(characters=[OWN]))
        w.chronicle.record({"fact": "story_quest_given", "id": "DIR_Q1"})
        w.chronicle.record({"fact": "story_quest_stage", "id": "DIR_Q1", "stage": 2})
        w.fake.vars[SCRIPT_QUEST] = 1
        newcomer = spec().model_copy(update={"routine": []})
        person = Person(
            ru="Ты — пришлый торговец, осторожный и терпеливый.",
            en="A patient travelling merchant.",
            greed=3,
            honesty=3,
            courage=3,
            talk=3,
            pushy=3,
            strangers=3,
            voice="A calm middle aged man with a low voice.",
        )
        await w.author.patches.introduce(newcomer, person.model_dump())
        w.fake.npc_states[OWN] = {"state": "alive", "name": newcomer.name, "distance": 300}
        deed = {"fact": "found_out", "quest": "MIS_LIE", "npc": LOBART, "reason": "Герой солгал."}
        w.chronicle.record(deed)
        current = occasion(kind="deed", deed=deed)
        brief = await w.showrunner.brief(current)
        assert brief.occasion == current
        assert (brief.chapter, brief.day, brief.hour, brief.minute) == (1, 10, 8, 0)
        assert set(brief.candidates) == {LOBART, OWN}
        assert not {HILDA, XARDAS, VINO, DEAD} & brief.candidates.keys()
        assert {HOME, YARD, FIELD} <= brief.places.keys() and "CITY" not in brief.places
        text = brief.text()
        for expected in (
            "deed",
            HOME,
            "дом",
            "Хильда",
            "Не терпишь бездельников",
            "Убрать поле репы",
            "Уже было",
            "MIS_LIE",
            "Герой солгал",
            "Рутгер",
            "пришлый торговец",
        ):
            assert expected in text
        assert deed in brief.deeds
        assert any("Уже было" in q and "2" in q for q in brief.quests)
        assert any("Уже было" in item for item in brief.history)
        assert any("Старое поручение строителя" in item and HILDA in item for item in brief.history)
        assert any(OWN in item or "Рутгер" in item for item in brief.history)
        assert brief.budget.open_left == 1 and brief.budget.lines_left == 3
        assert brief.budget.people_left == 7


@pytest.mark.parametrize("value", [1, 2])
async def test_brief_includes_vanilla_event_giver_known_journal_and_no_future_ending(graph, value):
    async with world(graph) as w:
        w.fake.vars[SCRIPT_QUEST] = value
        graph.nodes["DIA_LOBART_RUEBENRUNNING"].effects.append(
            {"do": "journal_entry", "topic": "TOPIC_RUEBEN", "text": "Будущая тайная развязка."}
        )
        event = {
            "fact": "vanilla_quest",
            "quest": SCRIPT_QUEST,
            "title": "Убрать поле репы",
            "from": "open" if value == 1 else "running",
            "to": "running" if value == 1 else "success",
            "giver": LOBART,
        }
        for i in range(12):
            w.chronicle.record({**event, "minute": i})
        brief = await w.showrunner.brief(occasion(kind="vanilla", vanilla=event))
        text = brief.text()
        assert len(brief.vanilla) == 10
        assert brief.vanilla == w.chronicle.facts("vanilla_quest")[-10:]
        for known in (SCRIPT_QUEST, "Убрать поле репы", "Лобарт", LOBART, event["to"]):
            assert known in text
        assert "Лобарт хочет, чтобы я собрал репу." in text
        assert "Будущая тайная развязка" not in text
        assert brief.occasion["vanilla"] == event


def test_proposal_parses_follows_and_all_three_requirements_and_keeps_old_defaults():
    requires = [
        {"kind": "quest", "quest": SCRIPT_QUEST, "state": "success"},
        {"kind": "knows", "node": "DIA_LOBART_WORKNOW"},
        {"kind": "chapter", "chapter": 2},
    ]
    p = Proposal.of(proposal(follows=SCRIPT_QUEST, requires=requires))
    assert (p.follows, p.requires) == (SCRIPT_QUEST, requires)
    assert Proposal.of(proposal()).follows == "" and Proposal.of(proposal()).requires == []
    assert {"follows", "requires"} <= set(PROPOSE_TOOL.parameters["required"])


@pytest.mark.parametrize(
    ("bad", "reason"),
    [
        ({"follows": "MIS_INVENTED"}, "не квест ванильного графа"),
        ({"giver": LOBART, "follows": SCRIPT_QUEST}, "выдаёт или закрывает"),
        ({"requires": [{"kind": "quest", "quest": SCRIPT_QUEST, "state": "success"}]}, "недостижимо"),
        ({"requires": [{"kind": "quest", "quest": "MIS_INVENTED", "state": "running"}]}, "не переменная"),
        ({"requires": [{"kind": "knows", "node": "DIA_INVENTED"}]}, "нет ванильного диалога"),
        ({"requires": [{"kind": "chapter", "chapter": 7}]}, "от 1 до 6"),
        ({"requires": {}}, "requires"),
        ({"follows": 12}, "follows"),
    ],
)
async def test_vanilla_canon_rejection_returns_the_reason_before_building(graph, bad, reason):
    async with world(graph, proposal(**bad), proposal()) as w:
        w.fake.vars[SCRIPT_QUEST] = 3
        result = await w.showrunner.decide(occasion(kind="vanilla"))
        assert result["fact"] == "line"
        [rejected] = w.chronicle.facts("line_rejected")
        assert reason in str(rejected["why"])
        correction = "\n".join(getattr(item, "content", "") for item in w.llm.calls[1][1])
        assert all(e in correction for e in rejected["why"])
        w.author.quest.assert_awaited_once()


async def test_canon_also_excludes_a_different_npc_who_only_closes_the_followed_quest(graph):
    graph.nodes["DIA_LOBART_RUEBENRUNNING"].npc = HILDA
    async with world(graph, proposal(follows=SCRIPT_QUEST), proposal()) as w:
        assert (await w.showrunner.decide(occasion()))["fact"] == "line"
        assert "выдаёт или закрывает" in str(w.chronicle.facts("line_rejected"))


@pytest.mark.parametrize("kind", ["quest", "chain", "stranger"])
async def test_vanilla_fields_reach_every_builder_and_survive_chronicle_reload(graph, kind):
    requires = [{"kind": "quest", "quest": SCRIPT_QUEST, "state": "success"}]
    async with world(graph, proposal(kind=kind, place=HOME, follows=SCRIPT_QUEST, requires=requires)) as w:
        w.fake.vars[SCRIPT_QUEST] = 2
        result = await w.showrunner.decide(occasion(kind="vanilla"))
        assert result["fact"] == "line"
        assert result["follows"] == SCRIPT_QUEST and result["requires"] == requires
        call = w.chains.chain.await_args if kind == "chain" else w.author.quest.await_args
        assert call.kwargs["follows"] == SCRIPT_QUEST and call.kwargs["requires"] == requires
        await load(w.fake, w.chronicle, branch="restored")
        assert w.showrunner.lines == [result]


@pytest.mark.parametrize(
    ("bad", "reason"),
    [
        ({"giver": XARDAS}, "сюжет"),
        ({"giver": "UNKNOWN"}, "кандидат"),
        ({"giver": DEAD}, "кандидат"),
        ({"kind": "stranger", "giver": "", "place": "CITY"}, "сводк"),
        ({"new_people": 1}, "new_people"),
        ({"kind": "invented"}, "вид"),
        ({"premise": ""}, "замысел"),
    ],
)
async def test_canon_refusal_returns_reasons_to_one_second_attempt(graph, bad, reason):
    async with world(graph, proposal(**bad), proposal()) as w:
        result = await w.showrunner.decide(occasion())
        assert result["fact"] == "line" and result["giver"] == HILDA
        [rejected] = w.chronicle.facts("line_rejected")
        assert rejected["why"]
        correction = "\n".join(getattr(item, "content", "") for item in w.llm.calls[1][1])
        assert reason.lower() in correction.lower()
        assert len(w.llm.calls) == 2
        assert w.llm.cache_keys == [CACHE_KEY, CACHE_KEY]
        assert [[tool.name for tool in call[2]] for call in w.llm.calls] == [[PROPOSE], [PROPOSE]]
        assert await w.showrunner.decide(occasion()) == result
        assert len(w.llm.calls) == 2
        w.author.quest.assert_awaited_once()


async def test_occupied_giver_is_rejected_even_with_another_free_candidate(graph):
    async with world(graph, proposal(), proposal(giver=LOBART)) as w:
        w.chronicle.record(line())
        result = await w.showrunner.decide(occasion())
        assert result["fact"] == "line" and result["giver"] == LOBART
        assert len(w.llm.calls) == 2 and len(w.chronicle.facts("line_rejected")) == 1
        assert w.author.quest.await_args.args[0] == LOBART


@pytest.mark.parametrize("kind", ["chain", "stranger"])
async def test_new_people_canon_respects_remaining_slots_but_existing_quest_can_pass(graph, kind):
    async with world(
        graph,
        proposal(kind=kind, new_people=kind == "chain", giver=HILDA if kind == "chain" else "", place=HOME),
        proposal(),
    ) as w:
        w.chronicle.record(line(characters=[f"DIR_NPC_{i}" for i in range(8)]))
        w.finish("DIR_Q1")
        result = await w.showrunner.decide(occasion())
        assert result["fact"] == "line" and len(w.llm.calls) == 2
        assert w.chronicle.facts("line_rejected")[0]["why"]
        w.casting.character.assert_not_called()
        w.chains.chain.assert_not_called()
        assert w.showrunner.budget(1, occasion()).people_left == 0


@pytest.mark.parametrize(
    ("attitude", "guild_attitude", "included"),
    [(0, 3, False), (2, 0, False), (2, 2, True), (3, 2, True)],
)
async def test_brief_checks_personal_and_directional_guild_hostility(
    graph, attitude, guild_attitude, included
):
    async with world(graph) as w:
        w.fake.attitudes[HILDA] = attitude
        w.fake.fields[HILDA, "GUILD"] = 8
        w.fake.fields["HERO", "GUILD"] = 7
        w.fake.guild_attitudes[8, 7] = guild_attitude
        w.fake.guild_attitudes[7, 8] = 3  # the reverse direction must not mask hostility towards HERO
        brief = await w.showrunner.brief(occasion())
        assert (HILDA in brief.candidates) is included
        assert LOBART in brief.candidates


async def test_brief_batches_all_candidates_and_keeps_friendly_people_but_not_own_bandits(graph):
    async with world(graph) as w:
        await w.author.patches.introduce(spec().model_copy(update={"guild": "GIL_BDT"}))
        w.fake.npc_states[OWN] = {"state": "alive", "name": "Бандит", "distance": 300}
        w.fake.fields[OWN, "GUILD"] = 9
        w.fake.attitudes.update({HILDA: 2, LOBART: 3, OWN: 3})
        brief = await w.showrunner.brief(occasion())
        assert set(brief.candidates) == {HILDA, LOBART}
        batches = [r for r in w.fake.story if r.get("type") == "story_eval" and "NPC_GETATTITUDE" in str(r)]
        assert len(batches) == 1 and len(batches[0]["exprs"]) == 3
        assert all("WLD_GETGUILDATTITUDE" in str(e) for e in batches[0]["exprs"])


async def test_hostile_giver_is_rejected_before_the_builder_and_neutral_retry_is_accepted(graph):
    async with world(graph, proposal(giver=HILDA), proposal(giver=LOBART)) as w:
        w.fake.attitudes[HILDA] = 0
        result = await w.showrunner.decide(occasion())
        assert result["fact"] == "line" and result["giver"] == LOBART
        w.author.quest.assert_awaited_once_with(LOBART, premise=PREMISE, follows="", requires=[])
        assert HILDA in str(w.chronicle.facts("line_rejected"))


async def test_two_canon_failures_stop_without_any_builder(graph):
    async with world(graph, proposal(giver=XARDAS)) as w:
        result = await w.showrunner.decide(occasion())
        assert result["fact"] == "line_rejected"
        assert len(w.llm.calls) == 2 and len(w.chronicle.facts("line_rejected")) == 2
        assert w.chronicle.facts("line") == []
        w.author.quest.assert_not_called()
        assert (await w.showrunner.decide(occasion()))["fact"] == "line_rejected"
        assert len(w.llm.calls) == 2


def passed_premise(call):
    return call.kwargs.get("premise", call.args[1] if len(call.args) > 1 else None)


@pytest.mark.parametrize(
    ("kind", "new_people"), [("quest", False), ("chain", False), ("chain", True), ("stranger", False)]
)
async def test_each_kind_passes_the_premise_and_records_the_made_objects(graph, kind, new_people):
    async with world(graph, proposal(kind=kind, new_people=new_people, place=HOME)) as w:
        result = await w.showrunner.decide(occasion())
        assert result["fact"] == "line" and result["kind"] == kind
        assert result["occasion"] == occasion() and result["premise"] == PREMISE
        assert (result["chapter"], result["day"], result["hour"], result["minute"]) == (1, 10, 8, 0)
        assert result["wall_time"] == w.clock.t
        assert set(result["quests"]) == set(w.author.book.all())
        if kind == "chain":
            assert len(result["quests"]) == 2
            call = w.chains.chain.await_args
            assert call.args[0] == HILDA and call.kwargs["new_people"] is new_people
            assert call.kwargs["max_people"] == (MAX_PEOPLE_PER_CHAPTER if new_people else 0)
            assert passed_premise(call) == PREMISE
            w.author.quest.assert_not_called()
        else:
            call = w.author.quest.await_args
            assert call.args[0] == (OWN if kind == "stranger" else HILDA)
            assert passed_premise(call) == PREMISE
            assert result["giver"] == call.args[0]
        if kind == "stranger":
            call = w.casting.character.await_args
            assert call.args[0] == HOME and call.kwargs["about"] == PREMISE
            assert passed_premise(call) == PREMISE
            assert result["characters"] == [OWN]
        else:
            assert result["characters"] == []
            w.casting.character.assert_not_called()


async def test_a_chain_keeps_the_builders_quest_order_across_two_digit_ids(graph):
    async with world(graph, proposal(kind="chain")) as w:

        async def chain(*args, **kwargs):
            quests = [quest(id="DIR_Q9"), quest(id="DIR_Q10")]
            await w.keep(*quests)
            return quests

        w.chains.chain.side_effect = chain
        result = await w.showrunner.decide(occasion())
        assert result["quests"] == ["DIR_Q9", "DIR_Q10"]


async def test_none_records_the_decision_without_spending_a_line_or_calling_builders(graph):
    async with world(graph, proposal(kind="none", reason="Пусть сначала закончит свои дела.")) as w:
        before = w.showrunner.budget(1, occasion())
        result = await w.showrunner.decide(occasion())
        assert result["fact"] == "line_none" and result["reason"] == "Пусть сначала закончит свои дела."
        assert result["proposal"] == proposal(kind="none", reason="Пусть сначала закончит свои дела.")
        assert w.showrunner.lines == []
        assert w.showrunner.budget(1, occasion()) == before
        assert len(w.llm.calls) == 1
        w.author.quest.assert_not_called()
        w.chains.chain.assert_not_called()
        w.casting.character.assert_not_called()


@pytest.mark.parametrize("kind", ["quest", "chain", "stranger"])
async def test_a_failed_builder_records_a_used_occasion(graph, kind):
    async with world(graph, proposal(kind=kind, place=HOME)) as w:
        if kind == "chain":
            w.chains.chain.side_effect = None
            w.chains.chain.return_value = None
        elif kind == "stranger":
            w.casting.character.side_effect = None
            w.casting.character.return_value = None
        else:
            w.author.quest.side_effect = None
            w.author.quest.return_value = None
        result = await w.showrunner.decide(occasion())
        assert result["fact"] == "line_failed" and result["occasion"] == occasion()
        assert w.chronicle.facts("line_failed") == [result]
        assert w.showrunner.lines == []
        assert await w.showrunner.decide(occasion()) == result
        assert len(w.llm.calls) == 1


async def test_a_stranger_left_by_a_failed_quest_still_spends_a_character_slot(graph):
    async with world(graph, proposal(kind="stranger", giver="", place=HOME)) as w:
        w.author.quest.side_effect = None
        w.author.quest.return_value = None
        result = await w.showrunner.decide(occasion())
        assert result["fact"] == "line_failed" and result["characters"] == [OWN]
        budget = w.showrunner.budget(1, occasion())
        assert budget.people_left == MAX_PEOPLE_PER_CHAPTER - 1
        assert budget.lines_left == MAX_LINES_PER_CHAPTER and budget.open_left == MAX_OPEN_LINES


async def test_partial_bandit_group_counts_every_character_after_a_failed_chain(graph):
    async with world(graph, proposal(kind="chain", new_people=True)) as w:

        async def partial(*args, **kwargs):
            for i in range(3):
                await w.author.patches.introduce(spec(npc=f"DIR_NPC_{i + 1}", name="Бандит"))
            return None

        w.chains.chain.side_effect = partial
        result = await w.showrunner.decide(occasion())
        assert result["fact"] == "line_failed"
        assert set(result["characters"]) == {"DIR_NPC_1", "DIR_NPC_2", "DIR_NPC_3"}
        assert w.showrunner.budget(1, occasion()).people_left == MAX_PEOPLE_PER_CHAPTER - 3


async def test_loading_restores_lines_progress_and_the_budget_from_the_saved_branch(graph):
    async with world(graph, proposal(kind="stranger", giver="", place=HOME)) as w:
        result = await w.showrunner.decide(occasion())
        saved = w.chronicle.memory.head(w.chronicle.branch)
        before = w.showrunner.budget(1, occasion())
        assert w.showrunner.is_open(result)
        w.finish(*result["quests"])
        w.chronicle.record(line(giver=LOBART, quests=[], characters=["DIR_NPC_999"]))
        assert w.showrunner.budget(1, occasion()) != before
        await load(w.fake, w.chronicle, head=saved)
        assert w.showrunner.lines == [result]
        assert w.showrunner.is_open(result)
        assert w.showrunner.budget(1, occasion()) == before
        assert set(w.author.book.all()) == set(result["quests"])
        assert [person.npc for person in w.casting.cast.characters()] == [OWN]
        assert await w.showrunner.decide(occasion()) == result
        assert len(w.llm.calls) == 1


async def test_loading_while_the_model_thinks_discards_its_old_branch_decision(graph):
    async with world(graph) as w:
        saved = w.chronicle.memory.head(w.chronicle.branch)
        started, resume = asyncio.Event(), asyncio.Event()

        async def delayed(*args, **kwargs):
            started.set()
            await resume.wait()
            return proposal()

        w.showrunner.model.propose = delayed
        deciding = asyncio.create_task(w.showrunner.decide(occasion()))
        try:
            await asyncio.wait_for(started.wait(), 1)
            await load(w.fake, w.chronicle, head=saved)
            resume.set()
            assert await asyncio.wait_for(deciding, 1) is None
            assert not any(f["fact"].startswith("line") for f in w.chronicle.facts())
            w.author.quest.assert_not_called()
        finally:
            resume.set()
            deciding.cancel()
            await asyncio.gather(deciding, return_exceptions=True)


@pytest.mark.parametrize("built", ["people", "quests", "both"])
async def test_loading_records_partial_build_synchronously_on_the_old_branch_and_spends_its_budget(
    graph, built
):
    async with world(graph, proposal(kind="chain", new_people=True)) as w:
        # Pre-existing objects are not receipts of the interrupted attempt.
        await w.author.patches.introduce(spec(npc="DIR_NPC_99"))
        await w.keep(quest(id="DIR_OLD"))
        old_branch = w.chronicle.branch
        saved = w.chronicle.memory.head(old_branch)
        started, resume = asyncio.Event(), asyncio.Event()

        async def partial(*args, **kwargs):
            if built in {"people", "both"}:
                await w.author.patches.introduce(spec())
            if built in {"quests", "both"}:
                await w.keep(quest())
            started.set()
            await resume.wait()

        w.chains.chain.side_effect = partial
        deciding = asyncio.create_task(w.showrunner.decide(occasion()))
        try:
            await asyncio.wait_for(started.wait(), 1)
            link = w.showrunner.link
            link._dispatch(Loading(type="loading"))
            # No await: the cancelled builder has not resumed, and Session may immediately follow.
            [failed] = w.chronicle.facts("line_failed")
            assert failed["occasion"] == occasion() and "загрузк" in failed["why"][0]
            assert failed["characters"] == ([OWN] if built in {"people", "both"} else [])
            assert failed["quests"] == (["DIR_Q1"] if built in {"quests", "both"} else [])
            budget = w.showrunner.budget(1, occasion())
            assert budget.people_left == MAX_PEOPLE_PER_CHAPTER - len(failed["characters"])
            assert budget.lines_left == MAX_LINES_PER_CHAPTER - bool(failed["quests"])
            assert budget.open_left == MAX_OPEN_LINES - bool(failed["quests"])
            assert budget.game_wait == (MIN_GAME_MINUTES if failed["quests"] else 0)
            assert budget.real_wait == (MIN_REAL_SECONDS if failed["quests"] else 0)
            link._dispatch(
                Session(type="session", branch="loaded", parent=old_branch, parent_seq=saved, reason="load")
            )
            assert w.chronicle.branch == "loaded" and w.chronicle.facts("line_failed") == []
            assert await asyncio.wait_for(deciding, 1) is None
            assert w.chronicle.facts("line_failed") == []
            assert w.showrunner.budget(1, occasion()).people_left == MAX_PEOPLE_PER_CHAPTER
            await load(w.fake, w.chronicle, branch=old_branch)
            assert w.chronicle.facts("line_failed") == [failed]
            assert w.showrunner.budget(1, occasion()) == budget
        finally:
            resume.set()
            deciding.cancel()
            await asyncio.gather(deciding, return_exceptions=True)


@pytest.mark.parametrize("stage", ["state", "model", "builder"])
async def test_run_logs_unexpected_failure_records_the_occasion_and_continues(
    graph, monkeypatch, caplog, stage
):
    async with world(graph, proposal(kind="chain", new_people=True), proposal(kind="none")) as w:
        first, second = occasion(), occasion(kind="chapter", chapter=2)
        monkeypatch.setattr(
            w.showrunner.occasions,
            "next",
            AsyncMock(side_effect=[first, second, asyncio.CancelledError()]),
        )
        if stage == "state":
            state = await w.showrunner._state()
            monkeypatch.setattr(
                w.showrunner, "_state", AsyncMock(side_effect=[RuntimeError("broken"), state])
            )
        elif stage == "model":
            monkeypatch.setattr(
                w.showrunner.model,
                "propose",
                AsyncMock(side_effect=[RuntimeError("broken"), proposal(kind="none")]),
            )
        else:

            async def broken_builder(*args, **kwargs):
                await w.author.patches.introduce(spec())
                await w.keep(quest())
                raise RuntimeError("broken")

            w.chains.chain.side_effect = broken_builder
        with pytest.raises(asyncio.CancelledError):
            await w.showrunner.run()
        [failed] = w.chronicle.facts("line_failed")
        assert failed["occasion"] == first and "RuntimeError: broken" in failed["why"][0]
        assert any(record.exc_info and "showrunner" in record.message for record in caplog.records)
        assert any(f.get("occasion") == second for f in w.chronicle.facts())
        if stage == "builder":
            assert failed["characters"] == [OWN] and failed["quests"] == ["DIR_Q1"]
            assert w.showrunner.budget(1, occasion()).people_left == MAX_PEOPLE_PER_CHAPTER - 1


async def test_run_marks_an_unexpected_handoff_failure_and_takes_the_next_occasion(
    graph, monkeypatch, caplog
):
    async with world(graph, proposal(kind="none")) as w:
        first, second = occasion(), occasion(kind="chapter", chapter=2)
        w.showrunner.occasions.pending[:] = [first, second]
        original = w.showrunner.link.story_state

        async def broken_once(**kwargs):
            monkeypatch.setattr(w.showrunner.link, "story_state", original)
            raise RuntimeError("handoff broken")

        monkeypatch.setattr(w.showrunner.link, "story_state", broken_once)
        finished = asyncio.Event()
        w.chronicle.on_fact.append(lambda fact: finished.set() if fact.get("fact") == "line_none" else None)
        running = asyncio.create_task(w.showrunner.run())
        try:
            await asyncio.wait_for(finished.wait(), 1)
            [failed] = w.chronicle.facts("line_failed")
            assert failed["occasion"] == first and "RuntimeError: handoff broken" in failed["why"][0]
            assert w.chronicle.facts("line_none")[0]["occasion"] == second
            assert w.showrunner.occasions.pending == []
            assert any(record.exc_info for record in caplog.records)
        finally:
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await running


@pytest.mark.parametrize("loading", [False, True])
async def test_server_shutdown_cancellation_propagates_even_if_loading_cancelled_the_builder(graph, loading):
    async with world(graph, proposal()) as w:
        started = asyncio.Event()

        async def building(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()

        w.author.quest.side_effect = building
        w.showrunner.occasions.next = AsyncMock(return_value=occasion())
        running = asyncio.create_task(w.showrunner.run())
        try:
            await asyncio.wait_for(started.wait(), 1)
            if loading:
                w.showrunner.link._dispatch(Loading(type="loading"))
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(running, 1)
            assert len(w.chronicle.facts("line_failed")) == int(loading)
        finally:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)


async def test_run_does_not_write_a_late_old_builder_failure_into_the_loaded_branch(
    graph, monkeypatch, caplog
):
    async with world(graph, proposal()) as w:
        started, resume = asyncio.Event(), asyncio.Event()
        old_branch, saved = w.chronicle.branch, w.chronicle.memory.head(w.chronicle.branch)
        original_next = w.showrunner.occasions.next
        first = True

        async def next_occasion():
            nonlocal first
            if first:
                first = False
                return occasion()
            return await original_next()

        async def late_failure(*args, **kwargs):
            await w.keep(quest())
            started.set()
            try:
                await resume.wait()
            except asyncio.CancelledError:
                await resume.wait()
            raise RuntimeError("late old failure")

        monkeypatch.setattr(w.showrunner.occasions, "next", next_occasion)
        w.author.quest.side_effect = late_failure
        running = asyncio.create_task(w.showrunner.run())
        try:
            await asyncio.wait_for(started.wait(), 1)
            w.showrunner.link._dispatch(Loading(type="loading"))
            [failed] = w.chronicle.facts("line_failed")
            assert failed["quests"] == ["DIR_Q1"]
            w.showrunner.link._dispatch(
                Session(type="session", branch="loaded", parent=old_branch, parent_seq=saved, reason="load")
            )
            resume.set()
            # This event follows the cancelled child and the loop's exception handler.
            for _ in range(20):
                if any("late old failure" in str(record.exc_info) for record in caplog.records):
                    break
                await asyncio.sleep(0.01)
            assert any(record.exc_info for record in caplog.records)
            assert w.chronicle.facts("line_failed") == []
            await load(w.fake, w.chronicle, branch=old_branch)
            assert w.chronicle.facts("line_failed") == [failed]
        finally:
            resume.set()
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)


async def test_pitch_uses_the_heros_place_and_budget_without_consuming_the_queue(graph):
    async with world(graph, proposal(kind="none")) as w:
        queued = occasion(kind="chapter")
        w.showrunner.occasions.pending.append(queued)
        result = await w.showrunner.pitch()
        assert result["fact"] == "line_none"
        assert result["occasion"]["place"] == HOME
        assert w.showrunner.occasions.pending == [queued]
        w.chronicle.record(line(day=10, wall_time=w.clock.t))
        w.clock.t += 1
        result = await w.showrunner.pitch()
        assert result["fact"] == "line_skipped"
        assert len(w.llm.calls) == 1
