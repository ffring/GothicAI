from types import SimpleNamespace
from typing import Any

from gothic_ai.chronicle import Chronicle
from gothic_ai.dialogue import DialogueManager
from gothic_ai.llm import ToolCall, ToolResult
from gothic_ai.llm.fake import FakeBackend, FakeStep
from gothic_ai.memory import MemoryStore
from gothic_ai.perception import PerceptionDesk
from gothic_ai.persona import PersonaProvider
from gothic_ai.places import PlaceBook
from gothic_ai.protocol import NpcView, NpcViews
from gothic_ai.sightings import Sightings, when
from gothic_ai.trust import Trust
from tests.fake_game import FakeGame, connected
from tests.fakes import Clock
from tests.test_dialogue import _until
from tests.test_perception import HILDA, LOBART, Social, armor, look

DAY = 1440
AT_8 = DAY + 8 * 60 + 10  # the second day, 08:10

BOOK = PlaceBook(
    {
        "world": "NEWWORLD.ZEN",
        "places": [
            {"id": "FARM1_PASTURE", "ru": "пастбище", "where_ru": "на пастбище у входа на ферму Лобарта",
             "en": "the pasture", "where_en": "on the pasture"},
            {"id": "FARM1_OUT", "ru": "двор", "where_ru": "во дворе фермы Лобарта",
             "en": "the yard", "where_en": "in Lobart's farmyard"},
        ],
        "points": {"NW_FARM1_PATH_01": 0, "NW_FARM1_OUT_01": 1, "NW_FARM1_OUT_02": 1},
    }
)  # fmt: skip


def view(t: int, hero: dict | None = None, *sees: dict) -> NpcView:
    """Lobart's view at game minute `t`: the hero on the pasture, Hilda in the yard, a stranger."""
    hero = hero or look("PLAYER", "Я", 8, seen=True, waypoint="NW_FARM1_PATH_01")
    return NpcView.model_validate(
        {
            "npc": LOBART,
            "name": "Лобарт",
            "waypoint": "NW_FARM1_OUT_01",
            "time": t,
            "hero": hero,
            "sees": list(sees)
            or [
                look(HILDA, "Хильда", 6, state="ZS_COOK_CAULDRON", waypoint="NW_FARM1_OUT_02"),
                look("SLD_800_X", "Наёмник", 12, guild=7, armor=armor("ITAR_SLD_L", "Доспех")),
            ],
        }
    )


class Link:
    def __init__(self) -> None:
        self.listeners: list[Any] = []
        self.session = SimpleNamespace(branch="A", parent=None, parent_seq=0)
        self.sent: list[Any] = []

    async def send(self, msg: Any) -> None:
        self.sent.append(msg)


def push(seen: Sightings, *views: NpcView) -> None:
    """The game's once-a-second push of the views (npc_views), as the link hands it out."""
    for listener in seen.link.listeners:
        listener(NpcViews(type="npc_views", t=0, views=list(views)))


def sightings(store: MemoryStore, link: Link | None = None, clock: Clock | None = None) -> Sightings:
    link = link or Link()
    desk = PerceptionDesk(link, social=Social, places=BOOK, clock=clock or Clock())  # type: ignore[arg-type]
    return Sightings(link, store, desk, Chronicle(link, store))  # type: ignore[arg-type]


async def test_what_it_saw_stays_where_and_how_and_follows_the_save():
    store = MemoryStore(":memory:")
    store.ensure_branch("A")
    seen = sightings(store)
    for t in (AT_8, AT_8 + 2, AT_8 + 4):  # the hero on the pasture for four minutes
        push(seen, view(t))
    seen._sweep(AT_8 + 64)  # an hour later: he has long gone
    militia = armor("ITAR_MIL_L", "Лёгкий доспех ополчения")
    hero = look("PLAYER", "Я", 3, seen=True, waypoint="NW_FARM1_OUT_01", armor=militia, weapon="melee")
    push(seen, view(AT_8 + 64, hero, look(HILDA, "Хильда", 30, waypoint="NW_FARM1_OUT_02")))

    assert seen.situation(LOBART) == (
        "Что ты видел сам, своими глазами (не с чужих слов):\n"
        "Героя (имени его ты не знаешь, но в лицо узнаёшь):\n"
        "- В1: сейчас — во дворе фермы Лобарта; на нём: Лёгкий доспех ополчения (по виду ополченец); "
        "с оружием наголо.\n"
        "- В2: сегодня 08:10–08:14, около часа назад — на пастбище у входа на ферму Лобарта; без доспеха."
    )  # Hilda is before its eyes now: not a memory; the stranger is not kept
    assert [t.name for t in seen.tools(LOBART)] == ["catch_lie"]

    # in the memory of the save branch: written when it starts and when it ends, not every second
    # (the hero and Hilda: the first sightings, started and ended; the second ones, started)
    assert len(store.seen("A", LOBART)) == 2 + 2 + 1 + 1
    again = sightings(store)
    trail = again.hero_trail(LOBART)
    assert [(s.start, s.end, s.places) for s in trail] == [
        (AT_8 + 64, AT_8 + 64, ["во дворе фермы Лобарта"]),
        (AT_8, AT_8 + 4, ["на пастбище у входа на ферму Лобарта"]),
    ]
    # a load to before it: it never saw him
    store.ensure_branch("B", "A", 0)
    link = Link()
    link.session.branch = "B"
    assert sightings(store, link).situation(LOBART) is None


async def test_the_people_it_knows_last_seen_and_a_body():
    store = MemoryStore(":memory:")
    store.ensure_branch("A")
    clock = Clock()
    seen = sightings(store, clock=clock)
    hero = look("PLAYER", "Я", 9, seen=False)
    push(seen, view(AT_8, hero, look(HILDA, "Хильда", 6, waypoint="NW_FARM1_OUT_02")))
    push(
        seen,
        view(AT_8 + 30, hero, look(HILDA, "Хильда", 6, kind="body", body="dead", waypoint="NW_FARM1_OUT_02")),
    )
    seen._sweep(AT_8 + DAY)
    clock.t += 60  # its view of then is long gone
    assert seen.situation(LOBART) == (
        "Что ты видел сам, своими глазами (не с чужих слов):\n"
        "Знакомых, последний раз:\n"
        "- тело: Хильда — вчера 08:40, утром — во дворе фермы Лобарта."
    )
    assert "Хильда — вчера 08:10, утром" in (seen.recall(LOBART, "где Хильда?") or "")
    assert seen.recall(LOBART, "волк") is None
    assert seen.tools(LOBART) == []  # never saw the hero: nothing to catch him with


def test_time_in_words():
    assert when(AT_8, AT_8 + 4, AT_8 + 6) == "сегодня 08:10–08:14, только что"
    assert when(AT_8, AT_8, AT_8 + 25) == "сегодня 08:10, 25 минут назад"
    assert when(AT_8, AT_8 + 20, AT_8 + 200) == "сегодня 08:10–08:30, 3 часа назад"
    assert when(AT_8, AT_8 + 3, AT_8 + 3) == "сейчас, с 08:10"
    assert when(AT_8 + 11 * 60, AT_8 + 11 * 60, AT_8 + DAY) == "вчера 19:10, вечером"
    assert when(AT_8, AT_8, AT_8 + 5 * DAY) == "5 дней назад 08:10, утром"


async def test_a_lie_about_where_he_was_is_caught_by_what_it_saw():
    fake = FakeGame(branch="A")
    store = MemoryStore(":memory:")
    wrong = FakeStep("Хм.", [ToolCall("catch_lie", {"said": "убил волка", "seen": "В2"}, "c1")])
    catch = FakeStep(
        "Врёшь! Я тебя час назад на пастбище видел.",
        [ToolCall("catch_lie", {"said": "всё утро был в городе", "seen": "B2"}, "c2")],
    )
    llm = FakeBackend(script=[wrong, catch, FakeStep("Пошёл вон.")], delay=0)
    async with connected(fake) as link:
        dialogue = DialogueManager(link, llm, PersonaProvider(None), memory=store)
        chronicle = Chronicle(link, store)
        desk = PerceptionDesk(link, social=Social, places=BOOK)
        seen = Sightings(link, store, desk, chronicle)
        dialogue.extensions += [desk, seen, Trust(chronicle)]
        dialogue.sight = desk.sight
        fake.people_near = [{"npc": HILDA, "name": "Хильда", "distance": 400, "guild": 0}]
        for t in (AT_8, AT_8 + 4):  # this morning, on the pasture
            seen.saw(view(t))
        # an hour later he comes back and talks to Lobart (the view asked before the turn)
        now = view(AT_8 + 64, look("PLAYER", "Я", 3, seen=True, waypoint="NW_FARM1_OUT_01"))
        fake.views = {LOBART: now.model_dump()}
        dialogue.open(LOBART, "Лобарт")
        await dialogue.say_as_player(LOBART, "Я всё утро был в городе, только что оттуда.")
        await _until(lambda: chronicle.facts("rumor") != [], 5)

    situation = str(llm.calls[0][1][-2].content)
    assert "В2: сегодня 08:10–08:14, около часа назад — на пастбище у входа на ферму Лобарта" in situation
    assert "catch_lie" in [t.name for t in llm.calls[0][2]]
    outputs = [i.output for c in llm.calls for i in c[1] if isinstance(i, ToolResult)]
    assert outputs[0].startswith("Отклонено: он этого не говорил")  # words he did not say
    c = chronicle.facts("caught")[0]
    assert (c["npc"], c["title"], c["said"]) == (LOBART, "всё утро был в городе", True)
    assert c["how"].startswith("сам видел его сегодня 08:10–08:14, около часа назад — на пастбище")
    assert dialogue.conversations[LOBART].intent.attitude == "wary"
    trust = Trust(chronicle)
    assert "он уже лгал тебе: «всё утро был в городе» — сам видел его" in (trust.situation(LOBART) or "")
    heard = [e.content for e in dialogue.conversations[HILDA].memory.entries]
    assert any("тот сказал «всё утро был в городе», а это неправда" in t for t in heard)


def test_a_lie_in_words_is_retold_as_words():
    from gothic_ai.amends import Lie as AmendsLie
    from gothic_ai.social import Rumor

    rumor = Rumor("SAID_X_1", "caught", LOBART, "Лобарт", "всё утро был в городе", said=True)
    assert rumor.told("Хильда") == (
        "Слух (со слов: Хильда): герой обманул человека по имени Лобарт — "
        "сказал ему «всё утро был в городе», а это неправда"
    )
    lie = AmendsLie("SAID_X_1", "caught", LOBART, "Лобарт", "всё утро был в городе", said=True)
    assert "сказал ему «всё утро был в городе», а тот своими глазами видел" in lie.words()
