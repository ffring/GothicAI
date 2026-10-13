from gothic_ai.chronicle import Chronicle
from gothic_ai.concerns import Concerns
from gothic_ai.deals import Deals
from gothic_ai.dialogue import DialogueManager
from gothic_ai.executor import AGENDA_MAX
from gothic_ai.llm.fake import FakeBackend
from gothic_ai.memory import MemoryStore
from gothic_ai.persona import PersonaProvider
from tests.fake_game import FakeGame, connected
from tests.test_dialogue import XARDAS, _until

LOBART, GOLD, POTION = "BAU_950_LOBART", "ITMI_GOLD", "ITPO_HEALTH_01"
WOLF = f"found_out:{LOBART}:MIS_WOLF"
BOOK = f"caught:{XARDAS}:MIS_BOOK"


async def _setup(fake: FakeGame):
    store = MemoryStore(":memory:")
    link_cm = connected(fake)
    link = await link_cm.__aenter__()
    dialogue = DialogueManager(link, FakeBackend("…", delay=0), PersonaProvider(None), memory=store)
    chronicle = Chronicle(link, store)
    deals = Deals(link, dialogue, chronicle)
    concerns = Concerns(link, chronicle, deals)
    return link_cm, chronicle, deals, concerns


def _debt(chronicle: Chronicle, deals: Deals, debt: str, *, npc: str = LOBART, due: int = 2, **give) -> None:
    """A debt written past the deals, as the e2e seeds one."""
    give = give or {"item": GOLD, "count": 25, "name": "Золото"}
    chronicle.record({"fact": "debt", "id": debt, "npc": npc, "npc_name": npc, **give, "due": due, "day": 0})
    deals.reload()


def _lie(kind: str, npc: str, quest: str, title: str) -> dict:
    return {"fact": kind, "quest": quest, "npc": npc, "name": npc, "title": title, "how": "сам", "day": 1}


async def test_a_broken_debt_is_the_lenders_concern():
    fake = FakeGame(branch="A")
    link_cm, chronicle, deals, concerns = await _setup(fake)
    try:
        _debt(chronicle, deals, "d1")
        assert concerns.of(LOBART) == []  # not due yet, nor today
        deals.overdue(3)
        c = concerns.top(LOBART)
        assert c is not None and (c.key, c.npc, c.rank) == ("debt_broken:d1", LOBART, 0)
        assert c.en == "make the hero pay back the 25 gold he owes you; his day has passed"
        assert len(c.en.split()) <= 15 and len(c.en) <= AGENDA_MAX
        assert c.ru == "герой не вернул тебе долг в срок: 25 золотых до конца дня 3"
        assert concerns.agenda(LOBART) == c.en
        # the hero owes nothing to the others
        assert concerns.of(XARDAS) == [] and concerns.agenda(XARDAS) is None

        # something else borrowed
        _debt(chronicle, deals, "d2", npc=XARDAS, item=POTION, count=2, name="Эликсир лечения")
        deals.overdue(3)
        c = concerns.top(XARDAS)
        assert c is not None and "Эликсир лечения ×2" in c.ru
        assert c.en == "make the hero pay back what he borrowed from you; his day has passed"
    finally:
        await link_cm.__aexit__(None, None, None)


async def test_a_debt_due_today_reminds_and_not_before():
    fake = FakeGame(branch="A")
    link_cm, chronicle, deals, concerns = await _setup(fake)
    try:
        seen: list[tuple[str, str | None]] = []  # as the planner reads the agenda when told
        concerns.on_change.append(lambda npc: seen.append((npc, concerns.agenda(npc))))
        _debt(chronicle, deals, "d1", due=2)
        seen.clear()
        deals._set_day(1)
        assert seen == [(LOBART, None)]
        deals._set_day(1)  # the same day: nothing new
        assert len(seen) == 1
        fake.day = 2
        deals._set_day(2)
        reminder = "remind the hero to pay back 25 gold to you by tonight"
        assert seen[-1] == (LOBART, reminder)
        (c,) = concerns.of(LOBART)
        assert (c.key, c.rank) == ("debt_due:d1", 2)
        assert c.ru == "сегодня последний день: герой должен вернуть тебе 25 золотых"

        # he pays it back in a deal: the deals know before the facts are told
        o = await deals.propose(LOBART, "Лобарт", {"repay": ["d1"]}, None)
        assert o.status == "accepted"
        await _until(lambda: any(m["type"] == "deal_show" for m in fake.deals))
        fake.hero_items = {GOLD: 25}
        await deals.accept(deals.open[LOBART].id, 1)
        assert deals.debts["d1"].paid and seen[-1] == (LOBART, None)
        assert concerns.of(LOBART) == []
    finally:
        await link_cm.__aexit__(None, None, None)


async def test_a_lie_found_out_or_caught_until_forgiven():
    fake = FakeGame(branch="A")
    link_cm, chronicle, deals, concerns = await _setup(fake)
    try:
        chronicle.record(_lie("found_out", LOBART, "MIS_WOLF", "Убить волка"))
        c = concerns.top(LOBART)
        assert c is not None and (c.key, c.rank) == (f"lied:{WOLF}", 1)
        assert c.en == "make the hero answer for lying to you and taking your reward"
        assert c.ru == "герой обманул тебя: взял награду за «Убить волка», а дела не сделал"
        chronicle.record(_lie("caught", XARDAS, "MIS_BOOK", "Принести книгу"))
        c = concerns.top(XARDAS)
        assert c is not None and c.key == f"lied:{BOOK}"
        assert c.en == "make the hero prove what he claims; never take his word again"
        assert c.ru == "герой обманул тебя: уверял, что сделал «Принести книгу», а это неправда"
        assert all(len(e.split()) <= 15 for e in (concerns.agenda(LOBART), c.en) if e)

        # forgiven in a deal
        chronicle.record({"fact": "deal", "npc": LOBART, "name": "Лобарт", "forgive": [WOLF], "silence": []})
        assert concerns.of(LOBART) == []
        # amends made for it
        chronicle.record({"fact": "amends", "id": "A1", "npc": XARDAS, "to": XARDAS, "gold": 10, "lie": BOOK})
        assert concerns.of(XARDAS) == []
    finally:
        await link_cm.__aexit__(None, None, None)


async def test_rumors_about_others_are_not_concerns():
    fake = FakeGame(branch="A")
    link_cm, chronicle, deals, concerns = await _setup(fake)
    try:
        chronicle.record(_lie("caught", XARDAS, "MIS_BOOK", "Принести книгу"))
        rumor = {
            "fact": "rumor",
            "kind": "caught",
            "to": LOBART,
            "from": XARDAS,
            "about": XARDAS,
            "about_name": "Ксардас",
            "quest": "MIS_BOOK",
            "title": "Принести книгу",
        }
        chronicle.record(rumor)
        assert BOOK in deals.lies_of(LOBART)[0]  # he knows of it
        assert concerns.of(LOBART) == [] and concerns.top(LOBART) is None
        assert [c.key for c in concerns.of(XARDAS)] == [f"lied:{BOOK}"]
    finally:
        await link_cm.__aexit__(None, None, None)


async def test_rank_and_on_change_from_facts():
    fake = FakeGame(branch="A")
    link_cm, chronicle, deals, concerns = await _setup(fake)
    try:
        changed: list[str] = []
        concerns.on_change.append(changed.append)
        _debt(chronicle, deals, "due", due=2)
        chronicle.record(_lie("caught", LOBART, "MIS_FIELD", "Вспахать поле"))
        _debt(chronicle, deals, "late", due=1)
        deals._set_day(2)
        deals.overdue(2)
        mind = concerns.of(LOBART)
        assert [(c.key, c.rank) for c in mind] == [
            ("debt_broken:late", 0),
            (f"lied:caught:{LOBART}:MIS_FIELD", 1),
            ("debt_due:due", 2),
        ]
        assert concerns.top(LOBART) == mind[0] and concerns.of(LOBART) is mind  # kept

        # facts of other kinds change nothing
        changed.clear()
        chronicle.record({"fact": "belief", "npc": LOBART, "quest": "MIS_FIELD", "believed": False})
        chronicle.record({"fact": "died", "npc": "WOLF", "killer": "PLAYER"})
        assert changed == [] and concerns.of(LOBART) is mind
        # both sides of the amends are told
        chronicle.record({"fact": "amends", "id": "A1", "npc": XARDAS, "to": LOBART, "lie": "caught:X:Q"})
        assert changed == [XARDAS, LOBART] and concerns.of(LOBART) is not mind

        # a load: the branch loaded has none of it
        await fake.send(type="session", branch="B", parent=None, parent_seq=0, reason="load")
        await _until(lambda: deals.debts == {})
        assert concerns.of(LOBART) == []
    finally:
        await link_cm.__aexit__(None, None, None)


async def test_a_debt_read_again_is_told_at_once():
    """A debt written past the deals and then read again: whoever cached the lender's agenda in between (the
    planner, told by the fact) hears of it from the reload, not on the next day."""
    fake = FakeGame(branch="A")
    link_cm, chronicle, deals, concerns = await _setup(fake)
    try:
        seen: list[tuple[str, str | None]] = []
        concerns.on_change.append(lambda npc: seen.append((npc, concerns.agenda(npc))))
        deals._set_day(2)
        _debt(chronicle, deals, "d1", due=2)  # due today
        assert seen[0] == (LOBART, None)  # the fact came before the deals read it
        assert seen[-1] == (LOBART, "remind the hero to pay back 25 gold to you by tonight")
    finally:
        await link_cm.__aexit__(None, None, None)


async def test_the_agenda_set_for_a_lie_ends_with_it():
    """quests.py keeps the lie's agenda in the npc's intent; once the lie is forgiven it is no agenda of the
    npc's any more. An agenda of the conversation's own stays."""
    fake = FakeGame(branch="A")
    link_cm, chronicle, deals, concerns = await _setup(fake)
    try:
        caught = "make the hero prove what he claims; never take his word again"
        chronicle.record(_lie("caught", XARDAS, "MIS_BOOK", "Книга"))
        assert concerns.current(XARDAS, caught) == caught  # the lie stands
        chronicle.record({"fact": "deal", "npc": XARDAS, "forgive": [BOOK], "day": 1})
        assert concerns.top(XARDAS) is None
        assert concerns.current(XARDAS, caught) is None
        assert (
            concerns.current(XARDAS, "get the hero to bring me a book") == "get the hero to bring me a book"
        )
        assert concerns.current(XARDAS, None) is None
    finally:
        await link_cm.__aexit__(None, None, None)
