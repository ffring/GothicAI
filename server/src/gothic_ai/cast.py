"""The director's own characters in the world (docs/director.md, stage 2, layer 2).

A character is an npc of ours (DIR_NPC_<n>, a script symbol of its own) that the game makes like a nameless
npc of the scripts: a farmer, a townsman, a mercenary. The template gives its class, body, fight and senses;
the description gives its name, guild, look, things and day, and where it comes. The code checks the
description, the game makes it once (story_npc), and the branch's log keeps it (StoryPatches): a load brings
back what the save had, and every session the game gets the branch's characters again, before the graph's
changes. Who it is as a person (its card, voice, what it knows, whom it knows) is layer 3.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Collection, Mapping
from typing import Any

from .chronicle import Chronicle
from .game import GameError, GameLink
from .protocol import NpcSpec
from .story import StoryPatches

log = logging.getLogger(__name__)

ID = re.compile(r"DIR_NPC_[A-Z0-9_]+")
# nameless npc's of the scripts, of the kinds a stranger may be; what the scripts tie to them stays theirs
TEMPLATES = re.compile(r"BAU_\d+_BAUER|VLK_\d+_BUERGER(IN)?|SLD_\d+_SOELDNER")
GUILDS = frozenset({"GIL_BAU", "GIL_VLK", "GIL_OUT", "GIL_SLD"})
# the scripts' heads and faces that are nobody's: no face of a person the hero knows
MALE_HEADS = ("BALD", "FATBALD", "FIGHTER", "THIEF", "PONY", "PSIONIC")
HEADS = {
    "MALE": frozenset(f"HUM_HEAD_{h}" for h in MALE_HEADS),
    "FEMALE": frozenset({"HUM_HEAD_BABE", "HUM_HEAD_BABEHAIR", *(f"HUM_HEAD_BABE{i}" for i in range(1, 9))}),
}
MALE_FACES = re.compile(r"FACE_[NLBP]_NORMAL(BART)?\d+")
FEMALE_FACES = frozenset(
    f"FACEBABE_N_{f}"
    for f in (
        "BAUBLONDE",
        "BLACKHAIR",
        "BLONDIE",
        "BROWN",
        "GREYCLOTH",
        "HAIRANDCLOTH",
        "MIDBAUBLONDE",
        "OLDBLONDE",
        "OLDBROWN",
        "VLKBLONDE",
        "WHITECLOTH",
        "YOUNGBLONDE",
    )
)
SKINS = frozenset({"BODYTEX_N", "BODYTEX_L", "BODYTEX_B", "BODYTEX_P"})
ARMORS = {
    "MALE": frozenset(f"ITAR_{a}" for a in ("BAU_L", "BAU_M", "VLK_L", "VLK_M", "VLK_H")),
    "FEMALE": frozenset(
        f"ITAR_{a}BABE_{s}" for a, s in (("BAU", "L"), ("BAU", "M"), ("VLK", "L"), ("VLK", "M"), ("VLK", "H"))
    ),
}
MAX_COUNT = 100  # of one thing
MAX_GOLD = 1000
DAY = 24 * 60
NAME_MAX = 40


def _minutes(clock: str) -> int | None:
    m = re.fullmatch(r"(\d\d):(\d\d)", clock)
    if m is None or int(m[1]) > 23 or int(m[2]) > 59:
        return None
    return int(m[1]) * 60 + int(m[2])


def _day_problems(spec: NpcSpec) -> list[str]:
    """The routine fills the day: one after another, no gaps, no overlaps."""
    spans: list[tuple[int, int]] = []
    for e in spec.routine:
        start, end = _minutes(e.start), _minutes(e.end)
        if start is None or end is None:
            return [f"распорядок: время пишется ЧЧ:ММ, а не {e.start}–{e.end}"]
        if start == end:
            return [f"распорядок: {e.start}–{e.end} — пустой отрезок"]
        spans.append((start, (end - start) % DAY))
    if sum(length for _, length in spans) != DAY:
        return ["распорядок: отрезки должны занимать ровно сутки"]
    spans.sort()
    for (start, length), (following, _) in zip(spans, [*spans[1:], spans[0]], strict=True):
        if (start + length) % DAY != following:
            return ["распорядок: каждый отрезок начинается там, где кончился прошлый"]
    return []


def problems(spec: NpcSpec, symbols: Mapping[str, str], taken: Collection[str]) -> list[str]:
    """What is wrong with a description, in words for the model. `symbols`: what the game says the names are
    (story_state, "npc", "item", "const", "func"...); `taken`: the ids of the branch's characters."""
    out: list[str] = []

    def kind(name: str, want: str, what: str) -> None:
        if symbols.get(name) != want:
            out.append(f"{what} {name} нет в игре")

    if not ID.fullmatch(spec.npc):
        out.append(f"персонаж режиссёра называется DIR_NPC_…: {spec.npc}")
    if spec.npc in taken:
        out.append(f"персонаж {spec.npc} уже есть")
    if not TEMPLATES.fullmatch(spec.like):
        out.append(f"шаблон — безымянный крестьянин, горожанин или наёмник скриптов, а не {spec.like}")
    else:
        kind(spec.like, "npc", "NPC")
    if not spec.name.strip() or len(spec.name) > NAME_MAX:
        out.append(f"имя — от 1 до {NAME_MAX} знаков")
    if spec.guild is not None:
        if spec.guild not in GUILDS:
            out.append(f"гильдия — одна из {', '.join(sorted(GUILDS))}, а не {spec.guild}")
        else:
            kind(spec.guild, "const", "гильдии")
    if (look := spec.look) is not None:
        if look.gender not in HEADS:
            out.append(f"пол — MALE или FEMALE, а не {look.gender}")
        else:
            if look.head.upper() not in HEADS[look.gender]:
                out.append(f"голова {look.head} не годится: {', '.join(sorted(HEADS[look.gender]))}")
            faces_ok = MALE_FACES.fullmatch(look.face) if look.gender == "MALE" else look.face in FEMALE_FACES
            if not faces_ok:
                out.append(f"лицо {look.face} не годится: только лица безымянных")
            else:
                kind(look.face, "const", "лица")
            if look.armor is not None:
                if look.armor not in ARMORS[look.gender]:
                    out.append(f"одежда {look.armor} не годится: {', '.join(sorted(ARMORS[look.gender]))}")
                else:
                    kind(look.armor, "item", "одежды")
        if look.skin not in SKINS:
            out.append(f"кожа — одна из {', '.join(sorted(SKINS))}, а не {look.skin}")
    for thing in spec.items:
        kind(thing.item, "item", "предмета")
        top = MAX_GOLD if thing.item == "ITMI_GOLD" else MAX_COUNT
        if not 1 <= thing.count <= top:
            out.append(f"{thing.item}: от 1 до {top}")
    if spec.routine:
        out += _day_problems(spec)
        for state in sorted({e.state for e in spec.routine}):
            if not state.startswith("ZS_"):
                out.append(f"распорядок: состояние скриптов называется ZS_…: {state}")
            else:
                kind(state, "func", "состояния")
    return out


def _names(spec: NpcSpec) -> list[str]:
    """The names the game must know."""
    names = [spec.like, *(t.item for t in spec.items), *(e.state for e in spec.routine)]
    if spec.guild:
        names.append(spec.guild)
    if spec.look is not None:
        names += [spec.look.face, *([spec.look.armor] if spec.look.armor else [])]
    return sorted(set(names))


class Cast:
    """The director's characters on the current branch, and how a new one comes."""

    def __init__(self, link: GameLink, patches: StoryPatches, chronicle: Chronicle | None = None) -> None:
        self.link = link
        self.patches = patches
        self.chronicle = chronicle

    def characters(self) -> list[NpcSpec]:
        return self.patches.characters()

    def get(self, npc: str) -> NpcSpec | None:
        return next((c for c in self.characters() if c.npc == npc), None)

    def next_id(self) -> str:
        """DIR_NPC_<n>, the next one free on the branch."""
        taken = {c.npc for c in self.characters()}
        n = 1
        while f"DIR_NPC_{n}" in taken:
            n += 1
        return f"DIR_NPC_{n}"

    async def introduce(self, spec: NpcSpec) -> tuple[dict[str, Any] | None, list[str]]:
        """A new character: the code checks it, the game makes it, the branch keeps it. The game's answer
        ({npc, state, index}), or None and what is wrong."""
        symbols = (await self.link.story_state(symbols=_names(spec))).get("symbols", {})
        issues = problems(spec, symbols, {c.npc for c in self.characters()})
        if not issues:
            try:
                made = await self.patches.introduce(spec)
            except GameError as err:
                issues = [f"игра не сделала персонажа: {err}"]
            else:
                log.info("cast: %s «%s» like %s at %s", spec.npc, spec.name, spec.like, spec.at)
                if self.chronicle is not None:
                    self.chronicle.record(
                        {"fact": "cast", "npc": spec.npc, "name": spec.name, "like": spec.like, "at": spec.at}
                    )
                return made, []
        log.info("cast: %s rejected: %s", spec.npc, "; ".join(issues))
        return None, issues
