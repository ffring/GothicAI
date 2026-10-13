"""Places by name (docs/npc-tools.md, wave 1 step 5): «во дворе фермы Лобарта» instead of NW_FARM1_OUT_01.

The game only has waypoints. The book of places is made once, offline:
1. `dump`: the world's waypoints and freepoints and its people's routines, from the game (-devmode,
   SCENARIO=places);
2. `group`: points into places. Their names say what part of the world they are in (NW_CITY_HABOUR_03,
   FP_ROAM_NW_FARM1_SHEEP_02: the city's harbour, Lobart's farm); points named alike that lie apart are
   different places; a point or two on their own join the nearest place of their part of the world;
3. `label`: the conversational model names each place from its points' names, who lives or works there
   and the places around: «двор фермы Лобарта», where — «во дворе фермы Лобарта», and in English;
4. the book is read through and kept as a file (gamedata/places.json).

At run time `PlaceBook` tells the place of a waypoint, and the places around a spot (a companion is sent
to one, orders.py).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .game import GameLink
from .llm import LLMBackend, Message, TextDelta

log = logging.getLogger(__name__)

VERSION = 1
BOOK = Path(__file__).parent / "gamedata" / "places.json"

LINK_CM = 2500  # points named alike closer than this are one place
SPLIT_POINTS = 40  # a part of the world named alike with more points goes by its third word too (floors)
MIN_POINTS = 3  # fewer, and the points join the nearest place of their part of the world
BATCH = 25  # places named in one call
SAMPLE_NAMES = 10

# what freepoints are for, between FP_ and the place: FP_ROAM_NW_FARM1_SHEEP_02, FP_STAND_NW_CITY_...
ACTIVITIES = {
    "ROAM", "STAND", "SIT", "SMALLTALK", "CAMPFIRE", "SLEEP", "PICK", "ITEM", "GUARD", "SWEEP", "PRAY",
    "EAT", "DRINK", "PEE", "SMITH", "COOK", "READ", "PRACTICE", "PICKHERB", "REPAIR", "WASH", "LUTE",
    "SAW", "RAKE", "STOMP", "ROASTSCAVENGER", "DEAD", "SPAWN", "ARMSCROSSED", "TALK",
}  # fmt: skip


async def dump(link: GameLink, path: Path) -> dict[str, Any]:
    """The world's points and people, as the game has them now, into `path`."""
    points = await link.waypoints()
    people = await link.story_people()
    data = {"world": link.world, "points": points, "people": people}
    await asyncio.to_thread(path.write_text, json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def tokens(name: str) -> list[str]:
    """What a point's name says of where it is: FP_ROAM_NW_FARM1_SHEEP_02 -> [FARM1, SHEEP]."""
    t = name.upper().split("_")
    if t and t[0] == "FP":
        t = t[1:]
        while t and t[0] in ACTIVITIES:
            t = t[1:]
    return [x for x in t if x != "NW" and not re.fullmatch(r"\d+[A-Z]?", x)]


def _key(name: str, words: int = 2) -> str:
    t = tokens(name)
    return "_".join(t[:words]) if t else "?"


@dataclass
class Group:
    """Points that make one place."""

    id: str
    points: list[dict[str, Any]]
    residents: list[str] = field(default_factory=list)  # who lives or works there, and when

    @property
    def area(self) -> str:
        return self.id.split("_")[0].split("#")[0]

    @property
    def center(self) -> tuple[float, float, float]:
        n = len(self.points)
        return (
            sum(p["x"] for p in self.points) / n,
            sum(p["y"] for p in self.points) / n,
            sum(p["z"] for p in self.points) / n,
        )


def _near(a: dict[str, Any], b: dict[str, Any], link: float = LINK_CM) -> bool:
    # height counts double: floors of a tower are different places
    return (a["x"] - b["x"]) ** 2 + (a["z"] - b["z"]) ** 2 + (2 * (a["y"] - b["y"])) ** 2 <= link**2


def _components(points: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    parent = list(range(len(points)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            if _near(points[i], points[j]):
                parent[root(i)] = root(j)
    out: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for i, p in enumerate(points):
        out[root(i)].append(p)
    return list(out.values())


def _dist(a: tuple[float, float, float], p: dict[str, Any]) -> float:
    return math.hypot(a[0] - p["x"], a[1] - p["y"], a[2] - p["z"])


def group(points: Iterable[dict[str, Any]], people: Iterable[dict[str, Any]] = ()) -> list[Group]:
    """Points into places: named alike and close together; strays join the nearest place, of their part of
    the world if it has one. Who lives or works there comes from the people's routines."""
    by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in points:
        by_key[_key(p["name"])].append(p)
    for key in [k for k, v in by_key.items() if len(v) > SPLIT_POINTS]:  # a tower's floors, a city's huts
        for p in by_key.pop(key):
            by_key[_key(p["name"], 3)].append(p)
    groups: list[Group] = []
    strays: list[dict[str, Any]] = []
    for key in sorted(by_key):
        parts = sorted(_components(by_key[key]), key=lambda c: (-len(c), min(p["name"] for p in c)))
        for i, part in enumerate(parts):
            if len(part) >= MIN_POINTS:
                groups.append(Group(key if i == 0 else f"{key}#{i + 1}", part))
            else:
                strays += part
    for p in strays:
        area = tokens(p["name"])[:1]
        same = [g for g in groups if area and g.area == area[0]] or groups
        best = min(same, key=lambda g: min(_dist((q["x"], q["y"], q["z"]), p) for q in g.points))
        best.points.append(p)
    where = {p["name"].upper(): g for g in groups for p in g.points}
    for person in people:
        for e in person.get("routine", []):
            g = where.get(str(e.get("waypoint", "")).upper())
            if g is None:
                continue
            line = f"{person['name']}: {e['state']} {e['start'] // 60:02d}–{e['end'] // 60:02d} ч"
            if line not in g.residents:
                g.residents.append(line)
    return groups


# --- naming ----------------------------------------------------------------------------------------

LABEL_INSTRUCTIONS = """Ты составляешь справочник мест острова Хоринис из игры Gothic 2: Ночь Ворона.
Каждое место — группа точек из файлов игры. О месте дано: ключ, имена его точек (так их назвали
разработчики, по-английски и по-немецки), сколько в нём точек, кто там живёт или работает (имя: состояние
скрипта ZS_…, часы) и соседние места с расстоянием.

Назови каждое место так, как сказал бы житель острова: коротко, по-русски, без слова «точка» и без
английских имён — «двор фермы Лобарта», «кузница Гарада», «рыночная площадь Хориниса», «второй этаж башни
Ксардаса», «дорога от фермы Лобарта к городу», «лес к востоку от таверны». Имена людей бери из списка
жителей, и только для их дома, работы или постоянного места («кузница Гарада»); кто там просто стоит,
место не называет. Звери в именах точек (WOLF, BLOODFLY, GOBBO, SHEEP, MONSTER, SPAWN) — это где они
появляются, а не название места: зверей могли перебить, поэтому называй по местности и соседям («опушка
у городской стены»), кроме пастбищ и загонов. Не выдумывай того, чего нет в данных: если неясно, назови по
виду и по соседям. Одинаковых названий у разных мест быть не должно: различай их («северная часть…»,
«у ворот…»).

Ответ — только JSON-массив, по объекту на место, в том же порядке:
{"id": ключ, "ru": название, "where_ru": где? («во дворе фермы Лобарта», «на рыночной площади
Хориниса»), "en": English name, "where_en": where? ("in Lobart's farmyard")}"""


def describe(g: Group, groups: list[Group]) -> str:
    """What the model is told of a place."""
    center = g.center
    names = sorted({p["name"] for p in g.points})
    sample = names[:SAMPLE_NAMES] + (["…"] if len(names) > SAMPLE_NAMES else [])
    others = sorted(
        ((math.dist(center, o.center) / 100, o.id) for o in groups if o is not g), key=lambda x: x[0]
    )[:4]
    lines = [
        f"id: {g.id}",
        f"точки ({len(g.points)}): {', '.join(sample)}",
        "жители: " + ("; ".join(g.residents[:8]) if g.residents else "нет"),
        "соседи: " + ", ".join(f"{oid} ({d:.0f} м)" for d, oid in others),
    ]
    return "\n".join(lines)


async def label(groups: list[Group], llm: LLMBackend, batch: int = BATCH) -> dict[str, dict[str, str]]:
    """The model's names of the places, by id; batches go by part of the world, so neighbours are named
    together. A batch that comes back broken is asked once more."""
    names: dict[str, dict[str, str]] = {}
    ordered = sorted(groups, key=lambda g: (g.area, g.id))
    batches = [ordered[i : i + batch] for i in range(0, len(ordered), batch)]
    for n, part in enumerate(batches, 1):
        prompt = "\n\n".join(describe(g, groups) for g in part)
        for attempt in (1, 2):
            text = ""
            async for ev in llm.stream(instructions=LABEL_INSTRUCTIONS, messages=[Message("user", prompt)]):
                if isinstance(ev, TextDelta):
                    text += ev.text
            got = _parse(text)
            if got is not None:
                break
            log.warning("places: batch %d came back broken (attempt %d)", n, attempt)
        for item in got or []:
            if isinstance(item, dict) and item.get("id") in {g.id for g in part}:
                names[item["id"]] = {
                    k: str(item.get(k, "")).strip() for k in ("ru", "where_ru", "en", "where_en")
                }
        log.info("places: batch %d/%d, %d named", n, len(batches), len(names))
    return names


def _parse(text: str) -> list[Any] | None:
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if m is None:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, list) else None


def book(groups: list[Group], names: dict[str, dict[str, str]], world: str | None) -> dict[str, Any]:
    """The file: places and which place each point belongs to."""
    places = []
    points: dict[str, int] = {}
    for g in sorted(groups, key=lambda g: g.id):
        if g.id not in names:
            continue
        cx, cy, cz = g.center
        places.append({"id": g.id, **names[g.id], "center": [round(cx), round(cy), round(cz)]})
        for p in g.points:
            points[p["name"].upper()] = len(places) - 1
    return {"version": VERSION, "world": (world or "").upper(), "places": places, "points": points}


# --- at run time -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Place:
    id: str
    ru: str
    where_ru: str
    en: str
    where_en: str
    center: tuple[float, float, float] | None = None  # the middle of its points
    points: tuple[str, ...] = ()  # its waypoints and freepoints


class PlaceBook:
    """The place of a waypoint, from the book (gamedata/places.json)."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.world = str(data.get("world", "")).upper()
        self.points: dict[str, int] = dict(data.get("points", {}))
        names: dict[int, list[str]] = defaultdict(list)
        for name, i in sorted(self.points.items()):
            names[i].append(name)
        self.places = [
            Place(
                p["id"],
                p["ru"],
                p["where_ru"],
                p["en"],
                p["where_en"],
                tuple(float(c) for c in p["center"]) if len(p.get("center") or ()) == 3 else None,  # type: ignore[arg-type]
                tuple(names[i]),
            )
            for i, p in enumerate(data.get("places", []))
        ]
        self._by_id = {p.id: p for p in self.places}

    @classmethod
    def load(cls, path: Path = BOOK) -> PlaceBook | None:
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != VERSION:
            log.warning("places: %s is version %s, expected %s", path, data.get("version"), VERSION)
            return None
        return cls(data)

    def of(self, *waypoints: str | None) -> Place | None:
        """The place of the first of these waypoints the book knows."""
        for wp in waypoints:
            if wp and (i := self.points.get(wp.upper())) is not None:
                return self.places[i]
        return None

    def get(self, place_id: str) -> Place | None:
        return self._by_id.get(place_id.strip().upper())

    def near(self, pos: Sequence[float], radius_cm: float, limit: int) -> list[tuple[Place, float]]:
        """The places whose middle is within `radius_cm` of `pos` (x, y, z), nearest first, with how far."""
        out = []
        for p in self.places:
            if p.center is None or not p.points:
                continue
            d = math.dist(pos, p.center)
            if d <= radius_cm:
                out.append((p, d))
        return sorted(out, key=lambda pd: pd[1])[:limit]
