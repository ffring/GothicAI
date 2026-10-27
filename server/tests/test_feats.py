"""A chronicler sees the hero's facts and spends only the current save branch's budget."""

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from gothic_ai import feats as feats_module
from gothic_ai.chronicle import Chronicle
from gothic_ai.feats import Feats, Proposal, canon, hero_view, notable
from gothic_ai.journal import Journal
from gothic_ai.llm import LLMError, ToolCall
from gothic_ai.llm.fake import FakeBackend, FakeStep
from gothic_ai.memory import MemoryStore
from gothic_ai.protocol import Loading, Session
from tests.fake_game import FakeGame, connected
from tests.test_dialogue import _until


def proposal(**changes):
    return {
        "verdict": "feat",
        "title": "Чужой кошель",
        "text": "Украл кошель у Лобарта.",
        "category": "theft",
        "evidence": [1],
        "emblem": "An open purse beside a rusty lock",
        "reason": "Первая кража героя.",
        **changes,
    }


def crime(**changes):
    return {
        "fact": "crime", "id": "C1", "kind": "theft", "victim": "LOBART",
        "victim_name": "Лобарт", **changes,
    }


def feat(**changes):
    return {
        "fact": "feat", "id": "old", "title": "Старое дело", "text": "Вернул чужое.",
        "chapter": 1, "day": 1, "hour": 8, "minute": 0, "trigger": 99,
        "evidence": [99], **changes,
    }


@asynccontextmanager
async def world(*answers, gather_s=0, history=(), backend=None):
    fake = FakeGame(branch="A")
    fake.day = 10
    store = MemoryStore(":memory:")
    store.ensure_branch("A")
    for fact in history:
        store.add_fact("A", json.dumps(fact, ensure_ascii=False))
    llm = backend or FakeBackend(
        script=[FakeStep(tool_calls=[ToolCall("write_feat", answer)]) for answer in answers], delay=0,
    )
    journal = []
    async with connected(fake) as link:
        chronicle = Chronicle(link, store)
        chronicler = Feats(link, chronicle, llm, journal=Journal(journal.append), gather_s=gather_s)
        try:
            yield fake, chronicle, chronicler, llm, journal
        finally:
            await chronicler.close()


async def settled(chronicler):
    await _until(lambda: chronicler._work is None or chronicler._work.done())


@pytest.mark.parametrize("fact", [
    {"fact": "blamed", "truth": False},
    {"fact": "found_out", "npc": "SECRET"},
    {"fact": "npc_intent", "plan": "SECRET"},
    {"fact": "died", "killer": "OTHER", "name": "SECRET"},
    {"fact": "surrender", "status": "offered"},
    {"fact": "feat", "title": "SECRET"},
])
def test_hidden_facts_have_no_hero_view(fact):
    assert hero_view(1, fact) is None


@pytest.mark.parametrize(("fact", "removed"), [
    (crime(witnesses=["SECRET"]), {"witnesses"}),
    ({"fact": "hero_yield", "npc": "N", "t": 1, "deadline": 2}, {"t", "deadline"}),
    ({"fact": "yield_broken", "npc": "N", "t": 1}, {"t"}),
    ({"fact": "humiliated", "npc": "N", "t": 1, "witnesses": []}, {"t", "witnesses"}),
    ({"fact": "surrender", "status": "spared", "deadline": 1}, {"deadline"}),
    ({"fact": "initiative", "npc": "N", "seen_by": ["SECRET"]}, {"seen_by"}),
    ({"fact": "bounty_betrayal", "witnesses": [], "witness_names": []},
     {"witnesses", "witness_names"}),
    ({"fact": "quest", "tag": "SECRET", "instance": "SECRET"}, {"tag", "instance"}),
    ({"fact": "claim", "believed": True, "blamed": "N", "blame_crime": "C", "checked": True},
     {"believed", "blamed", "blame_crime", "checked"}),
    ({"fact": "died", "killer": "PLAYER", "npc": "N", "name": "Имя", "monster": False,
      "tag": "SECRET"}, {"killer", "tag"}),
])
def test_hero_view_removes_private_fields_without_mutating_the_fact(fact, removed):
    assert hero_view(1, fact) == {key: value for key, value in fact.items() if key not in removed}
    assert removed <= fact.keys()


def test_notable_first_repeated_and_unusual_outcomes():
    entries = [(1, crime())]
    assert notable(1, entries[-1][1], entries)
    entries.append((2, crime(id="C2")))
    assert not notable(2, entries[-1][1], entries)
    entries.append((3, crime(id="C3")))
    assert notable(3, entries[-1][1], entries)
    entries.append((4, crime(id="C4", victim="HILDA")))
    assert not notable(4, entries[-1][1], entries)
    ordinary = {"fact": "quest_outcome", "quest": "Q", "how": "done", "pay": "full"}
    assert not notable(5, ordinary, [(5, ordinary)])
    for changes in ({"how": "amended"}, {"how": "dropped"}, {"pay": "none"}):
        unusual = {**ordinary, **changes}
        assert notable(6, unusual, [(5, ordinary), (6, unusual)])
    monster = {"fact": "died", "npc": "MONSTER", "killer": "PLAYER", "monster": True}
    for flag in (False, None):
        human = {"fact": "died", "npc": "HUMAN", "killer": "PLAYER", "monster": flag}
        assert notable(2, human, [(1, monster), (2, human)])


def test_turns_require_the_related_earlier_deed():
    entries = [(1, {"fact": "lie", "quest": "Q", "npc": "LOBART"})]
    made_good = {"fact": "lie_made_good", "quest": "Q", "npc": "LOBART"}
    # The second made-good is noteworthy because it follows a lie about this quest.
    entries.append((2, {**made_good, "quest": "OTHER"}))
    entries.append((3, made_good))
    assert notable(3, made_good, entries)
    unknown = {**made_good, "quest": "UNKNOWN", "npc": "HILDA"}
    assert not notable(4, unknown, [*entries, (4, unknown)])
    for kind, earlier, reference in [("yield_broken", "hero_yield", "yield"),
                                     ("truce_broken", "truce", "truce")]:
        old = {"fact": earlier, "id": "T", "npc": "LEADER", "members": ["MEMBER"]}
        broken = {"fact": kind, reference: "T", "npc": "MEMBER"}
        history = [(1, old), (2, {**broken, reference: "OTHER"})]
        assert notable(3, broken, [*history, (3, broken)])


@pytest.mark.parametrize(("changes", "previous"), [
    ({"category": "unknown"}, []),
    ({"title": ""}, []),
    ({"title": "Один два три четыре пять"}, []),
    ({"title": "Я" * 41}, []),
    ({"text": ""}, []),
    ({"text": "Я" * 141}, []),
    ({"text": "Первая строка\nВторая строка"}, []),
    ({"title": "Gold"}, []),
    ({"text": "Взял gold."}, []),
    ({"title": "Кошель!"}, []),
    ({"text": "Взял кошель!"}, []),
    ({"title": "Кошель 🗡"}, []),
    ({"text": "Взял кошель 🗡"}, []),
    ({"title": "Поздравляем"}, []),
    ({"text": "Достижение героя."}, []),
    ({"title": "Ачивка"}, []),
    ({"title": "ЧУЖОЙ КОШЕЛЬ"}, [feat(title="Чужой кошель")]),
    ({"evidence": []}, []),
    ({"evidence": [1, 2, 3, 4, 5, 6, 7]}, []),
    ({"evidence": [1, 99]}, []),
    ({"evidence": [2]}, []),
    ({"evidence": [1, 2]}, [feat(trigger=2)]),
    ({"title": "Кошель Хильда"}, []),
    ({"title": "Кошель Хильды"}, []),
    ({"text": "Обокрал Хильду."}, []),
    ({"emblem": ""}, []),
    ({"emblem": "purse " * 16}, []),
    ({"emblem": "Чужой кошель"}, []),
    *[({"emblem": f"An old {word}"}, []) for word in
      ("blood", "gore", "face", "text", "letter", "rune", "FACES", "letters", "runes")],
    ({"text": "Метка ©️."}, []),
])
def test_canon_rejects_each_invalid_field(changes, previous):
    window = [(1, crime()), (2, {"fact": "deal", "npc": "HILDA", "name": "Хильда"})]
    errors = canon(Proposal.of(proposal(**changes)), 1, window, previous)
    assert errors and all(isinstance(error, str) and any("а" <= c.lower() <= "я" for c in error)
                          for error in errors)


@pytest.mark.parametrize(("name", "text"), [
    ("Хильда", "Обокрал Хильду."),
    ("Хильда", "Взял кошель у Хильды."),
    ("Лобарт", "Взял кошель у Лобарта."),
    ("Онар", "Договорился с Онаром."),
    ("Лорд Хаген", "Обокрал лорда Хагена."),
])
def test_canon_accepts_names_from_evidence_and_unchanged_proposal(name, text):
    raw = proposal()
    assert canon(Proposal.of(raw), 1, [(1, crime())], []) == []
    raw["text"] = text
    window = [(1, {"fact": "deal"}), (2, {"fact": "deal", "name": name})]
    assert canon(Proposal.of(raw), 1, window, []) == [
        f"Имя «{name.casefold()}» отсутствует в доказательствах",
    ]
    raw["evidence"] = [1, 2]
    assert canon(Proposal.of(raw), 1, window, []) == []


@pytest.mark.parametrize("changes", [
    {"emblem": "A purse on a wooden surface"},
    {"emblem": "A purse made of textile"},
    {"emblem": "A prune beside a purse"},
    {"text": "Поручение №2."},
    {"text": "Повернул на 90°."},
    {"text": "Метка ©."},
])
def test_canon_accepts_ordinary_words_and_symbols(changes):
    assert canon(Proposal.of(proposal(**changes)), 1, [(1, crime())], []) == []


async def test_complete_path_retries_records_toast_and_journal_then_rolls_back():
    async with world(proposal(title="Кошель!"), proposal()) as (fake, chronicle, chronicler, llm, journal):
        seq = chronicle.record(crime(witnesses=["SECRET"]))
        await settled(chronicler)
        await _until(lambda: any(m["type"] == "feat_toast" for m in fake.received))
        [award] = chronicle.facts("feat")
        assert seq == 1 and award["evidence"] == [seq] and award["trigger"] == seq
        assert len(award["id"]) == 12 and int(award["id"], 16) >= 0
        assert (award["chapter"], award["day"], award["hour"], award["minute"]) == (1, 10, 8, 0)
        assert award["emblem"] == proposal()["emblem"] and award["reason"] == proposal()["reason"]
        [toast] = [m for m in fake.received if m["type"] == "feat_toast"]
        icon = Path(feats_module.__file__).parent / "assets/feats/categories/theft/icon.png"
        assert icon.is_file() and toast["icon"] == str(icon.resolve())
        assert toast["feat"] == award["id"] and toast["title"] == award["title"]
        assert toast["text"] == award["text"]
        assert [{k: v for k, v in entry.items() if k != "t"} for entry in journal] == [{
            "kind": "feat", "id": award["id"], "title": award["title"],
            "text": award["text"], "category": "theft",
        }]
        assert len(llm.calls) == 2 and llm.cache_keys == ["chronicler", "chronicler"]
        assert "Отклонено" in llm.calls[1][1][-1].content
        assert [tool.name for tool in llm.calls[0][2]] == ["write_feat"]
        assert "SECRET" not in llm.calls[0][1][0].content
        assert chronicle.entries("feat")[0][0] > seq
        chronicle.memory.ensure_branch("B", "A", seq)
        link = chronicle.link
        link._dispatch(Session(type="session", branch="B", parent="A", parent_seq=seq, reason="load"))
        assert chronicler.feats == [] and chronicle.facts("feat") == []
        assert len(chronicle.entries()) == 1


async def test_hidden_truth_never_reaches_the_chronicler():
    async with world(proposal(verdict="none")) as (_, chronicle, chronicler, llm, _):
        for fact in [
            {"fact": "blamed", "name": "СКРЫТОЕ_ОБВИНЕНИЕ"},
            {"fact": "found_out", "name": "СКРЫТОЕ_РАСКРЫТИЕ"},
            {"fact": "npc_intent", "name": "СКРЫТЫЙ_ЗАМЫСЕЛ"},
            {"fact": "died", "killer": "OTHER", "name": "ЧУЖАЯ_ЖЕРТВА"},
        ]:
            chronicle.record(fact)
        assert llm.calls == []
        chronicle.record(crime(witnesses=["ТАЙНЫЙ_СВИДЕТЕЛЬ"]))
        await settled(chronicler)
        assert len(llm.calls) == 1
        text = llm.calls[0][1][0].content
        for hidden in ("СКРЫТОЕ_ОБВИНЕНИЕ", "СКРЫТОЕ_РАСКРЫТИЕ", "СКРЫТЫЙ_ЗАМЫСЕЛ",
                       "ЧУЖАЯ_ЖЕРТВА", "ТАЙНЫЙ_СВИДЕТЕЛЬ"):
            assert hidden not in text
        assert not chronicle.facts("feat")


@pytest.mark.parametrize("history", [
    [feat(id=str(i), day=i) for i in range(1, 4)],
    [feat(day=10, hour=7, minute=59)],
    [feat(evidence=[2])],
])
async def test_budget_discards_without_a_model_call(history):
    async with world(proposal(), history=history) as (_, chronicle, chronicler, llm, _):
        chronicle.record(crime())
        await settled(chronicler)
        assert llm.calls == []
        assert len(chronicle.facts("feat")) == len(history)


@pytest.mark.parametrize("answer", [proposal(title="Кошель!"), proposal(verdict="none")])
async def test_rejected_or_declined_occasion_does_not_record_a_feat(answer):
    async with world(answer) as (_, chronicle, chronicler, llm, _):
        chronicle.record(crime())
        await settled(chronicler)
        assert len(llm.calls) == (1 if answer["verdict"] == "none" else 2)
        assert chronicle.facts("feat") == []


async def test_model_error_loses_the_occasion(caplog):
    class FailedBackend(FakeBackend):
        async def stream(self, **kwargs):
            raise LLMError("test unavailable")
            yield  # pragma: no cover

    async with world(backend=FailedBackend()) as (_, chronicle, chronicler, _, _):
        chronicle.record(crime())
        await settled(chronicler)
        assert chronicle.facts("feat") == []
        assert "test unavailable" in caplog.text


async def test_loading_cancels_the_wait_and_an_inflight_model():
    for gather_s, model_delay in [(60, 0), (0, 60)]:
        backend = FakeBackend(
            script=[FakeStep(tool_calls=[ToolCall("write_feat", proposal())])],
            first=model_delay, delay=0,
        )
        async with world(backend=backend, gather_s=gather_s) as (_, chronicle, chronicler, llm, _):
            chronicle.record(crime())
            if model_delay:
                await _until(lambda: bool(llm.calls))
            chronicle.link._dispatch(Loading(type="loading"))
            chronicle.link._dispatch(Session(type="session", branch="B", reason="load"))
            await settled(chronicler)
            assert chronicle.facts("feat") == []
            assert len(llm.calls) == (1 if model_delay else 0)


async def test_while_busy_only_the_latest_occasion_waits():
    backend = FakeBackend(
        script=[FakeStep(tool_calls=[ToolCall("write_feat", proposal(verdict="none"))])],
        first=0.05, delay=0,
    )
    async with world(backend=backend) as (_, chronicle, chronicler, llm, _):
        chronicle.record(crime())
        await _until(lambda: len(llm.calls) == 1)
        chronicle.record({"fact": "hero_yield", "npc": "N", "name": "Первый"})
        latest = chronicle.record({"fact": "truce", "npc": "N", "name": "Последний"})
        await _until(lambda: len(llm.calls) == 2)
        await settled(chronicler)
        assert len(llm.calls) == 2
        text = llm.calls[1][1][0].content
        assert f"#{latest}" in text and "truce" in text
        assert not chronicle.facts("feat")


async def test_story_state_timeout_drops_only_the_active_occasion(monkeypatch, caplog):
    async with world(proposal(verdict="none")) as (_, chronicle, chronicler, llm, _):
        started, release = asyncio.Event(), asyncio.Event()
        story_state = chronicle.link.story_state

        async def timeout_once(**kwargs):
            if not started.is_set():
                started.set()
                await release.wait()
                raise TimeoutError
            return await story_state(**kwargs)

        monkeypatch.setattr(chronicle.link, "story_state", timeout_once)
        first = chronicle.record(crime())
        work = chronicler._work
        await _until(started.is_set)
        latest = chronicle.record({"fact": "truce", "npc": "N", "name": "Последний"})
        release.set()
        await settled(chronicler)
        assert work.done() and work.exception() is None
        assert chronicler._pending is None
        assert len(llm.calls) == 1
        assert llm.calls[0][1][0].content.startswith(f"Повод: #{latest} truce ")
        assert f"#{first}" in caplog.text and "TimeoutError" in caplog.text
