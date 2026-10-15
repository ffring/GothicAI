"""What an npc knows of where people and things are (docs/npc-tools.md М4, «Знаю»).

knowledge.py gives an npc its dialogs as topics: Cavalorn knows «Вниз по этой дороге располагается одна из
тех грязных дыр, где прячутся бандиты», but not which place that is. Here those words are tied to the map:
«Бандиты, которые тебя ограбили, засели в пещере бандитов под лестницей к башне Ксардаса» — XARDAS_BANDITS,
and Brago and his men by their ids.

The book of facts is made once, offline (`gothic-ai lore <world dump>`): for each npc of the world the
conversational model reads its dialogs, the places around its home and those its words name (the book of
places, places.py), and the people they name or that live near it (the dump's routines), and writes down
who and what is where. Each fact keeps the dialog it comes from. At run time a fact is the npc's while that
dialog is: told, or open by the story (Knowledge.views): the bandits' cave is forgotten once Brago is dead
and the dialog that tells of it is out of the story. The situation has the facts of the places and people
around the npc; `recall` and `where` (whereabouts.py) find the rest.

An npc not in the book (the director's own, another world's) knows nothing here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .knowledge import roots
from .llm import LLMBackend, Message, TextDelta, Tool
from .places import Place, PlaceBook

if TYPE_CHECKING:
    from .dialogue import Conversation, DialogueManager
    from .game import GameLink
    from .gamedata.storygraph import Node, StoryGraph
    from .knowledge import Knowledge
    from .llm import ToolCall
    from .perception import PerceptionDesk
    from .protocol import Snapshot
    from .turn import ToolOutcome

log = logging.getLogger(__name__)

VERSION = 1
LORE = Path(__file__).parent / "gamedata" / "lore.json"

HOME_CM = 15000.0  # the places this near its home are told to the model (150 m)
NEAR_PLACES = 25
NAMED_PLACES = 12
NAME_SPREAD = 5  # a word that names more places than this names none («дорога»)
PEOPLE_MAX = 30
FACTS_MAX = 6
CONCURRENCY = 4
SHOWN_CM = 10000.0  # the facts of places this near the npc go into its situation (100 m)
SHOWN_MAX = 5
RECALLED = 5

INSTRUCTIONS = """Ты размечаешь, что знает житель острова Хоринис (Gothic 2: Ночь Ворона) о том, где кто и что
находится. Тебе даны его диалоги с героем (id диалога; реплики: «Ты» — он сам, «Герой» — герой) и справочник:
места рядом с его домом и места, которые называют его слова (id — название), и люди (id — имя — где живёт).

Выпиши факты, которые он знает сам: кто или что где находится, кто чем занят и где его найти, где опасно.
Каждый факт привяжи к месту из справочника (place) и к людям из справочника (who), если они есть в факте.
Пиши от лица этого жителя, обращаясь к нему на «ты», коротко и по сути: «Бандиты, которые тебя ограбили,
засели в пещере под лестницей к башне Ксардаса — вниз по дороге от твоего костра.» Не пересказывай разговор,
не пиши о чувствах, о герое и о его поручениях — только мир: где люди, логова, вещи, опасности.

Только то, что прямо сказано в его репликах или однозначно из них следует вместе со справочником: если он
говорит «вниз по этой дороге дыра, где прячутся бандиты», а рядом с его домом есть пещера, где живут
бандиты, — это она. Не выдумывай. Место из диалога, которого нет в справочнике, — place: null; такой факт
оставь, только если в нём есть люди из справочника. Не больше 6 фактов; знать нечего — пустой массив.

Ответ — только JSON-массив: {"dialog": id диалога, откуда факт, "place": id места или null, "who": [id
людей], "ru": факт, "en": the same in English, said to him ("you")}"""


# --- the book of facts, offline ----------------------------------------------------------------------


@dataclass
class Person:
    npc: str
    name: str
    homes: list[str]  # the places of its routine, the most hours first


def people_of(dump: Iterable[dict[str, Any]], book: PlaceBook) -> dict[str, Person]:
    """The world's people and where they live, from the dump's routines."""
    out = {}
    for p in dump:
        hours: Counter[str] = Counter()
        for e in p.get("routine", []):
            place = book.of(str(e.get("waypoint", "")))
            if place is not None:
                hours[place.id] += (int(e.get("end", 0)) - int(e.get("start", 0))) % 1440 or 1440
        out[str(p["npc"]).upper()] = Person(
            str(p["npc"]).upper(), str(p.get("name", "")), [h for h, _ in hours.most_common()]
        )
    return out


def dialogs(graph: StoryGraph, npc: str) -> list[tuple[str, list[str]]]:
    """The npc's dialogs as the model reads them: id, then its lines and those of the hero's answers in it,
    «Ты: …», «Герой: …» (the answers are under the dialog they are in)."""
    out = []
    for node in graph.dialogs_of(npc):
        lines: list[str] = []
        seen: set[str] = set()
        todo: list[Node] = [node]
        while todo:
            n = todo.pop(0)
            if n.id in seen:
                continue
            seen.add(n.id)
            if n.is_choice and n.description:
                lines.append(f"[ответ героя: {n.description}]")
            for e in n.effects:
                if e["do"] == "line" and e.get("text"):
                    lines.append(f"{_speaker(e.get('speaker'))}: {e['text']}")
            todo += [graph.nodes[c] for c in n.choices() if c in graph.nodes]
        if any(not x.startswith("[") for x in lines):
            out.append((node.id, lines))
    return out


def _speaker(e: Any) -> str:
    match e:
        case ["inst", "OTHER" | "HERO"]:
            return "Герой"
        case ["inst", "SELF"]:
            return "Ты"
    return "Кто-то"


def candidates(
    npc: str, text: str, book: PlaceBook, people: dict[str, Person]
) -> tuple[list[Place], list[Person]]:
    """The places and people the model may tie the npc's facts to: around its home, and those its words
    name; the people who live there, and those named by name."""
    me = people.get(npc)
    homes = [p for p in (book.get(h) for h in (me.homes if me else [])) if p is not None and p.center]
    near: dict[str, tuple[float, Place]] = {}
    for h in homes:
        assert h.center is not None
        for p, d in book.near(h.center, HOME_CM, len(book.places)):
            if p.id not in near or d < near[p.id][0]:
                near[p.id] = (d, p)
    places = [p for _, p in sorted(near.values(), key=lambda dp: dp[0])][:NEAR_PLACES]
    words = {w for w in roots(text) if len(w) >= 5}
    named: Counter[str] = Counter()
    hits: dict[str, set[str]] = defaultdict(set)
    for p in book.places:
        for w in words & roots(p.ru):
            hits[w].add(p.id)
    for ids in hits.values():
        if len(ids) <= NAME_SPREAD:
            named.update(ids)
    for pid, _ in named.most_common(NAMED_PLACES):
        if (p := book.get(pid)) is not None and p not in places:
            places.append(p)
    ids = {p.id for p in places}
    folk = [o for o in people.values() if o.npc != npc and o.homes and o.homes[0] in ids]
    by_name = [o for o in people.values() if o.npc != npc and _named(o.name, words) and o not in folk]
    folk = (sorted(by_name, key=lambda o: o.name) + folk)[:PEOPLE_MAX]
    for o in folk:  # where those named live
        if o.homes and o.homes[0] not in ids and (p := book.get(o.homes[0])) is not None:
            places.append(p)
            ids.add(p.id)
    return places, folk


def _named(name: str, words: set[str]) -> bool:
    """Whether the words name him by his own name; a common one («Бандит», «Крестьянин») names nobody."""
    if not name or name.lower() in COMMON_NAMES:
        return False
    return any(w in words for w in roots(name) if len(w) >= 4)


COMMON_NAMES = {
    "бандит", "крестьянин", "крестьянка", "наемник", "наёмник", "ополченец", "горожанин", "горожанка",
    "паладин", "послушник", "маг огня", "стражник", "рыбак", "охотник", "пират", "моряк", "рудокоп",
    "бродяга", "незнакомец", "вор", "рабочий", "женщина", "мужчина", "торговец", "лесоруб",
}  # fmt: skip


def prompt(
    npc: str,
    name: str,
    talks: list[tuple[str, list[str]]],
    places: list[Place],
    folk: list[Person],
    book: PlaceBook,
) -> str:
    me = f"Житель: {name} ({npc})"
    blocks = [me, "Места:"]
    blocks += [f"- {p.id} — {p.ru} ({p.kind_ru})" for p in places]
    blocks.append("Люди:")
    for o in folk:
        home = book.get(o.homes[0]) if o.homes else None
        blocks.append(f"- {o.npc} — {o.name}" + (f" — {home.where_ru} ({home.id})" if home else ""))
    blocks.append("Его диалоги:")
    for did, lines in talks:
        blocks.append(f"[{did}]\n" + "\n".join(lines))
    return "\n".join(blocks)


async def extract(
    npc: str,
    graph: StoryGraph,
    book: PlaceBook,
    people: dict[str, Person],
    llm: LLMBackend,
) -> list[dict[str, Any]]:
    """The facts of one npc, checked: its own dialogs, places and people of the lists given, no more than
    FACTS_MAX. A broken answer is asked once more."""
    talks = dialogs(graph, npc)
    if not talks:
        return []
    me = people.get(npc)
    text = " ".join(line for _, lines in talks for line in lines)
    places, folk = candidates(npc, text, book, people)
    ask = prompt(npc, me.name if me else npc, talks, places, folk, book)
    got: list[Any] | None = None
    for attempt in (1, 2):
        answer = ""
        async for ev in llm.stream(instructions=INSTRUCTIONS, messages=[Message("user", ask)]):
            if isinstance(ev, TextDelta):
                answer += ev.text
        got = _parse(answer)
        if got is not None:
            break
        log.warning("lore: %s came back broken (attempt %d)", npc, attempt)
    return check(got or [], {d for d, _ in talks}, {p.id for p in places}, {o.npc for o in folk})


def check(
    facts: Iterable[Any], dialog_ids: set[str], place_ids: set[str], people_ids: set[str]
) -> list[dict[str, Any]]:
    """The facts that hold: from the npc's own dialog, of a place or people it was given, with words."""
    out = []
    for f in facts:
        if not isinstance(f, dict) or f.get("dialog") not in dialog_ids or not str(f.get("ru", "")).strip():
            continue
        place = f.get("place") if f.get("place") in place_ids else None
        who = [str(w).upper() for w in f.get("who") or [] if str(w).upper() in people_ids]
        if place is None and not who:
            continue
        out.append(
            {
                "dialog": f["dialog"],
                "place": place,
                "who": who,
                "ru": str(f["ru"]).strip(),
                "en": str(f.get("en", "")).strip(),
            }
        )
    return out[:FACTS_MAX]


def _parse(text: str) -> list[Any] | None:
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if m is None:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, list) else None


async def build(
    npcs: Sequence[str],
    graph: StoryGraph,
    book: PlaceBook,
    people: dict[str, Person],
    llm: LLMBackend,
    *,
    done: dict[str, list[dict[str, Any]]] | None = None,
    save: Callable[[dict[str, list[dict[str, Any]]]], None] | None = None,
    concurrency: int = CONCURRENCY,
) -> dict[str, list[dict[str, Any]]]:
    """The facts of these npc's, CONCURRENCY at a time; those `done` are kept, and each one written as it
    comes (`save`): a long run picks up where it stopped."""
    facts = dict(done or {})
    gate = asyncio.Semaphore(concurrency)
    todo = [n for n in npcs if n not in facts]

    async def one(npc: str) -> None:
        async with gate:
            got = await extract(npc, graph, book, people, llm)
        facts[npc] = got
        log.info("lore: %s, %d fact(s) (%d of %d)", npc, len(got), len(facts), len(npcs))
        if save is not None:
            save(facts)

    await asyncio.gather(*(one(n) for n in todo))
    return facts


def file(
    world: str, facts: dict[str, list[dict[str, Any]]], people: dict[str, Person] | None = None
) -> dict[str, Any]:
    """The book of facts, with the names of the people they tell of («где Ватрас?»)."""
    told = {w for items in facts.values() for f in items for w in f.get("who", ())}
    names = {w: people[w].name for w in sorted(told) if people is not None and w in people}
    return {
        "version": VERSION,
        "world": world.upper(),
        "npcs": {k: facts[k] for k in sorted(facts)},
        "names": names,
    }


# --- at run time -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Fact:
    npc: str
    dialog: str  # the dialog it comes from: the npc knows it while it knows the dialog
    place: str | None
    who: tuple[str, ...]
    ru: str
    en: str


def npc_pos(
    npc: str, link: GameLink, book: PlaceBook | None, desk: PerceptionDesk | None
) -> tuple[float, float, float] | None:
    """Where the npc is: its snapshot (under control), else the place of the waypoint it is at, else where
    the hero is (it talks with him)."""
    snap = link.snapshots.get(npc.upper())
    if snap is not None:
        return snap.npc.pos
    view = desk.view(npc) if desk is not None else None
    if view is not None and book is not None:
        place = book.of(view.waypoint, view.nearest_waypoint)
        if place is not None and place.center is not None:
            return place.center
    player = link.player
    return player.pos if player is not None else None


class Lore:
    """What an npc knows of where people and things are: a TurnExtension (situation, recall)."""

    def __init__(
        self,
        data: dict[str, Any],
        book: PlaceBook | None,
        knowledge: Knowledge | None,
        *,
        link: GameLink | None = None,
        desk: PerceptionDesk | None = None,
    ) -> None:
        self.book = book
        self.knowledge = knowledge
        self.link = link
        self.desk = desk
        self.facts: dict[str, list[Fact]] = {
            npc.upper(): [
                Fact(
                    npc.upper(),
                    f["dialog"],
                    f.get("place"),
                    tuple(f.get("who", ())),
                    f["ru"],
                    f.get("en", ""),
                )
                for f in items
            ]
            for npc, items in data.get("npcs", {}).items()
        }
        self.names: dict[str, str] = {k.upper(): str(v) for k, v in data.get("names", {}).items()}

    @classmethod
    def load(
        cls,
        book: PlaceBook | None,
        knowledge: Knowledge | None,
        *,
        path: Path = LORE,
        link: GameLink | None = None,
        desk: PerceptionDesk | None = None,
    ) -> Lore | None:
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != VERSION:
            log.warning("lore: %s is version %s, expected %s", path, data.get("version"), VERSION)
            return None
        return cls(data, book, knowledge, link=link, desk=desk)

    def known(self, npc: str) -> list[Fact]:
        """Its facts it knows now (Knowledge's latest view of it): their dialogs told, or open for sure by
        the story. Not one that waits for the hero to bring something: Cavalorn does not know the ornament
        is with the paladins until the hero has got it from Hagen."""
        npc = npc.upper()
        facts = self.facts.get(npc, [])
        view = self.knowledge.views.get(npc) if self.knowledge is not None else None
        if not facts or view is None:
            return []
        dialogs = {t.node.id for t in view.told} | {t.node.id for t in view.open if t.sure}
        return [f for f in facts if f.dialog in dialogs]

    def of_place(self, npc: str, place: str) -> list[Fact]:
        return [f for f in self.known(npc) if f.place == place]

    def of_person(self, npc: str, who: str) -> list[Fact]:
        return [f for f in self.known(npc) if who.upper() in f.who]

    # --- TurnExtension ----------------------------------------------------------------------------

    all_tools: tuple[Tool, ...] = ()

    async def prepare(self, npc: str) -> None:
        return None  # Knowledge's own prepare reads the story; this only reads its view

    def tools(self, npc: str) -> list[Tool]:
        return []

    async def handle(
        self, dialogue: DialogueManager, conv: Conversation, call: ToolCall, snap: Snapshot | None
    ) -> ToolOutcome | None:
        return None

    def situation(self, npc: str) -> str | None:
        """The facts of the places around the npc and of the people it sees."""
        known = self.known(npc)
        if not known:
            return None
        here = npc_pos(npc, self.link, self.book, self.desk) if self.link is not None else None
        view = self.desk.view(npc) if self.desk is not None else None
        seen = {o.id.upper() for o in view.sees} if view is not None else set()
        shown: list[tuple[float, Fact]] = []
        for f in known:
            d = float("inf")
            place = self.book.get(f.place) if self.book is not None and f.place else None
            if place is not None and here is not None:
                d = self.book.distance(place, here) if self.book is not None else d
            if any(w in seen for w in f.who):
                d = min(d, 0.0)
            if d <= SHOWN_CM:
                shown.append((d, f))
        if not shown:
            return None
        lines = [f"- {f.ru}" for _, f in sorted(shown, key=lambda df: df[0])[:SHOWN_MAX]]
        return "Что ты знаешь о местах и людях вокруг:\n" + "\n".join(lines)

    def recall(self, npc: str, query: str) -> str | None:
        """Its facts the query's words meet, of their places' names too."""
        words = {w for w in roots(query) if len(w) >= 4}
        if not words:
            return None
        scored = []
        for i, f in enumerate(self.known(npc)):
            text = f.ru
            if self.book is not None and f.place and (p := self.book.get(f.place)) is not None:
                text += " " + p.ru
            score = len(words & roots(text))
            if score:
                scored.append((-score, i, f))
        if not scored:
            return None
        return "Ты знаешь о местах и людях:\n" + "\n".join(
            f"- {f.ru}" for _, _, f in sorted(scored)[:RECALLED]
        )
