"""The hero talks to the world (M5e): no chat window, nothing is a mode.

His phrase — spoken (voice.py: the T key held anywhere) or typed (the input line, `player_text`) — is an
event of the world:

- **whom it is said to** is the code's rule (`addressee`): a name in the phrase, the npc he looks at, the
  current partner if still close, one of two npc's who were just talking near him (gossip.py), the nearest
  npc in front of him; with none, he said it into the air;
- **the conversation** is a soft state here — who the partner is, when something was last said — and the
  npc is not captured: its scripts go on. It ends with the npc's goodbye, or once the hero is farther than
  `END_CM` and nothing has been said for `END_SILENCE_S`;
- **the body** of a free npc is the behavioral model's (`Reflexes.posture`): stop and listen, keep working,
  walk along with the hero, turn away. When it is unsure the code decides by what the npc is doing. The
  reply is said on the move unless the npc stands listening;
- the npc is held (attached) only while its body does something for the conversation: listening, walking
  along, handing over an item, following the hero (dialogue.py asks for it through `take_body`);
- the hero speaking while the npc answers **cuts it off** (`hush`), and it remembers that; so does his voice
  to two npc's talking near him (`on_hero_voice`);
- **witnesses** within `OVERHEAR_CM` remember what they heard, without a model call;
- what the hero **does** meanwhile (draws a weapon, strikes, walks off mid-reply) goes into the partner's
  memory, and so into its next turn;
- an **order** to his companions ("Стой!", "За мной!") is no conversation: they obey at once, without
  the conversational model (orders.py);
- words to a **wolf** at him (he looks at it, or it growls at him and he names nobody) are no conversation
  either: it hears their tone and lets him pass, runs or bites (beasts.py).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable, Collection, Coroutine, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from . import commands
from .brain.day import describe
from .brain.planner import NpcPlanner
from .brain.reflex import POSTURE_OPTIONS, Reflexes
from .brain.situation import NpcContext, clock_text
from .dialogue import DialogueManager
from .game import GameError, GameLink
from .journal import Journal
from .protocol import (
    Attached,
    ChatStatusCmd,
    Detached,
    GameMessage,
    LineStarted,
    Loading,
    NpcEvent,
    NpcRoster,
    Perception,
    PlayerText,
    PttStart,
    RosterEntry,
    TranscriptCmd,
)
from .trace import TraceWriter

if TYPE_CHECKING:
    from .beasts import BeastDesk
    from .orders import Orders

log = logging.getLogger(__name__)

# how far the hero is heard (user, 2026-10-13: 20 m for a name and 8 m for the partner lost words said on
# the run, to a companion a few steps behind or to one he called from afar)
NAME_CM = 4000.0  # a name is heard this far: it is called out
FOCUS_CM = 2500.0  # the npc the hero looks at, if not farther than this
PARTNER_CM = 1500.0  # the current partner stays the addressee this close
FRONT_CM = 1200.0  # the nearest npc in front of the hero
SCENE_CM = 2000.0  # two npc's talking near him (gossip.py), if not farther than this
OVERHEAR_CM = 1000.0  # witnesses
END_CM = 1500.0  # farther than this and silent for END_SILENCE_S: the conversation is over
END_SILENCE_S = 20.0
SNAPSHOT_WAIT_S = 1.0
LOOK_MS = 4000
NOTE_EVERY_S = 15.0  # one note per kind of the hero's doings this often

# states an npc is not busy in: when the behavioral model is unsure, such an npc stops and listens
IDLE_STATES = ("ZS_STAND", "ZS_SIT", "ZS_SMALLTALK", "ZS_GUARD")
# body jobs that take the partner away from the hero for the conversation and bring it back (check_claim,
# ask_witness): it has not walked away
ERRANDS = frozenset({"check", "witness"})

# what the hero does mid-conversation, for the partner's memory (npc_event kinds; perceptions of an
# attached npc map to them below)
HERO_ACTS = {
    "weapon_drawn": "Герой посреди разговора достал оружие",
    "threatened": "Герой посреди разговора наставил на тебя оружие",
    "attack": "Герой посреди разговора напал на {detail}",
    "magic": "Герой посреди разговора колдует",
    "theft": "Герой посреди разговора обокрал тебя",
    "murder": "Герой посреди разговора убил {detail}",
}
PERC_ACTS = {"DRAWWEAPON": "weapon_drawn", "ASSESSTHREAT": "threatened"}

_WORD = re.compile(r"[a-zа-я]+")


def words(text: str) -> list[str]:
    return _WORD.findall(text.lower().replace("ё", "е"))


# case endings of a name ("скажи Лобарту", "с Ксардасом", "у Кассии"): cut before the letters are compared
_ENDINGS = ("ами", "ом", "ой", "ою", "ем", "ей", "ам", "ах", "у", "а", "е", "ы", "и", "ю", "я")


# letters speech recognition easily takes for each other: an unstressed vowel, a voiced consonant and its
# voiceless pair ("Ксардоз" for "Ксардас"); such a change counts half
_ALIKE = frozenset(
    frozenset(p) for p in ("ао", "еи", "еэ", "иы", "уо", "ая", "ую", "бп", "вф", "гк", "дт", "жш", "зс")
)


def _change(x: str, y: str) -> float:
    return 0.0 if x == y else 0.5 if frozenset((x, y)) in _ALIKE else 1.0


def edits(a: str, b: str, limit: float) -> float:
    """Letters to put in, take out, change or swap to make `a` of `b` (alike letters change for half);
    anything over `limit` is `limit` + 1."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2: list[float] = []
    prev = [float(j) for j in range(len(b) + 1)]
    for i in range(1, len(a) + 1):
        row = [float(i)] + [0.0] * len(b)
        for j in range(1, len(b) + 1):
            row[j] = min(prev[j] + 1, row[j - 1] + 1, prev[j - 1] + _change(a[i - 1], b[j - 1]))
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                row[j] = min(row[j], prev2[j - 2] + 1)
        if min(row) > limit:
            return limit + 1
        prev2, prev = prev, row
    return min(prev[-1], limit + 1)


def _forms(word: str) -> list[tuple[str, bool]]:
    """The word, and the word without a case ending (True)."""
    return [(word, False)] + [
        (word[: -len(e)], True) for e in _ENDINGS if word.endswith(e) and len(word) - len(e) >= 3
    ]


def name_edits(word: str, part: str, misheard: bool = False) -> float | None:
    """How far `word` is from the name `part`, if it may be it (None: it is not). In some case form
    ("Лобарту"; a word without an ending is not a name that has one: "лук" is not "Лука", "Луке" is), a
    name of five letters or more also with an alike letter ("Андрэ" for "Андре"). `misheard`: as speech
    recognition may have heard it ("Сардас" for "Ксардас", "Ксардоз", "Лабарту"): one letter off in a name
    of 5-7 letters, two in a longer one, only an alike one in a name of four ("Анар" for "Онар", but "она"
    is not), none in a shorter one."""
    n = len(part)
    if misheard:
        slack = 0.0 if n <= 3 else 0.5 if n == 4 else 1.0 if n <= 7 else 2.0
    else:
        slack = 0.5 if n >= 5 else 0.0
    best = min(
        (edits(w, p, slack) for w, w_cut in _forms(word) for p, p_cut in _forms(part) if w_cut or not p_cut),
        default=slack + 1,
    )
    return best if best <= slack else None


_STRETCH = re.compile(r"[^,.!?;:…—–]+")
# what the hero calls someone by instead of a name: never a misheard name ("Да, мастер." is not "Бастер")
_ADDRESS = frozenset(
    "мастер господин госпожа брат сестра сэр лорд друг дружище приятель старик старина парень командир "
    "капитан генерал отец сынок учитель хозяин торговец кузнец страж стражник маг".split()
)


def calls(text: str) -> list[str]:
    """Words the hero may be calling someone by: a word alone between commas or at an end of the phrase
    ("Сардас, как..." "..., Ксардас?"), not an address of its own ("мастер")."""
    out: list[str] = []
    for stretch in _STRETCH.findall(text):
        w = words(stretch)
        if len(w) == 1 and w[0] not in _ADDRESS:
            out += w
    return out


def named(phrase: list[str], name: str, called: list[str] | None = None) -> float | None:
    """How far from `name` the nearest word of the phrase is, if the name is there (None: it is not): in any
    case form ("Лобарт, ...", "скажи Лобарту"), and among the words the hero calls someone by (`calls`)
    also as speech recognition may have heard it ("Сардас, ..." for "Ксардас": elsewhere a misheard word
    is more likely an ordinary one, "в городе" is not "Гарад")."""
    parts = [p for p in words(name) if len(p) >= 3]
    found = [name_edits(w, p) for p in parts for w in phrase]
    found += [name_edits(w, p, misheard=True) for p in parts for w in called or ()]
    return min((d for d in found if d is not None), default=None)


@dataclass(frozen=True)
class Addressee:
    id: str
    name: str
    why: str  # name | focus | partner | scene | front | order (companions who obey it) | beast (a wolf)


def addressee(
    text: str,
    focus: str | None,
    people: dict[str, RosterEntry],
    partner: str | None,
    scene: Sequence[str] = (),
) -> Addressee | None:
    """Whom the hero speaks to: a name in the phrase, his focus, the current partner, one of the npc's of
    a `scene` (two who were just talking near him, the one who spoke last first), the nearest in front."""
    alive = {k: e for k, e in people.items() if e.activity != "down"}
    phrase, by = words(text), calls(text)
    near = [
        (d, e)
        for e in alive.values()
        if e.distance <= NAME_CM and (d := named(phrase, e.name, by)) is not None
    ]
    if near:
        # the name heard best ("Лестер!" with Фестер near too), then the nearer one
        _, e = min(near, key=lambda de: (de[0], de[1].distance))
        return Addressee(e.id.upper(), e.name, "name")
    if focus and (e := alive.get(focus.upper())) is not None and e.distance <= FOCUS_CM:
        return Addressee(e.id.upper(), e.name, "focus")
    if partner and (e := alive.get(partner)) is not None and e.distance <= PARTNER_CM:
        return Addressee(e.id.upper(), e.name, "partner")
    # a scene starts only while he has no partner, so this never takes words meant for one
    near = [e for n in scene if (e := alive.get(n.upper())) is not None and e.distance <= SCENE_CM]
    if near:
        seen = [e for e in near if e.in_view]
        e = min(seen, key=lambda e: e.distance) if seen else near[0]
        return Addressee(e.id.upper(), e.name, "scene")
    front = [e for e in alive.values() if e.in_view and e.distance <= FRONT_CM]
    if front:
        e = min(front, key=lambda e: e.distance)
        return Addressee(e.id.upper(), e.name, "front")
    return None


@dataclass
class Talk:
    npc: str
    name: str
    started: float
    last_at: float  # something was said, by either side
    posture: str | None = None
    walked_off: bool = False  # the hero went away while the npc was answering (noted once)


class TalkDesk:
    def __init__(
        self,
        link: GameLink,
        dialogue: DialogueManager,
        reflexes: Reflexes | None = None,
        *,
        journal: Journal | None = None,
        trace: TraceWriter | None = None,
        clock: Callable[[], float] = time.monotonic,
        planner: NpcPlanner | None = None,
        end_silence_s: float = END_SILENCE_S,
    ) -> None:
        self.link = link
        self.dialogue = dialogue
        self.reflexes = reflexes
        self.journal = journal or dialogue.journal
        self.trace = trace
        self.clock = clock
        self.planner = planner
        # what an npc perceives now, for the behavioral model (perception.py)
        self.view_of: Callable[[str], tuple[str, ...]] = lambda npc: ()
        self.end_silence_s = end_silence_s
        self.people: dict[str, RosterEntry] = {}  # the roster: people around the hero
        self.game_time: tuple[int, int] | None = None
        self.partner: Talk | None = None
        self.last: tuple[Addressee | None, str] | None = None  # the latest phrase and whom it went to
        self.attached: set[str] = set()  # by anyone
        self.held: dict[str, set[str]] = {}  # npc's we attached, and what for
        self._noted: dict[tuple[str, str], float] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        # a conversation is over (npc, why: npc, gone, walked_away, switched): e.g. an offer not taken goes
        self.on_talk_end: list[Callable[[str, str], None]] = []
        # the hero starts to speak: others talking near him stop (gossip.py); True if someone did
        self.on_hero_voice: list[Callable[[], bool]] = []
        # npc's of a scene near the hero his next words may go to, the one who spoke last first (gossip.py)
        self.scene_hint: Callable[[], list[str]] = lambda: []
        # orders to the hero's companions ("Стой!"), carried out at once, without an answer (orders.py)
        self.orders: Orders | None = None
        # wolves at the hero hear his words (beasts.py)
        self.beasts: BeastDesk | None = None
        link.listeners.append(self.on_message)
        dialogue.take_body = self.hold
        dialogue.free_body = self.free
        dialogue.on_end = self.on_npc_end

    # --- events ---------------------------------------------------------------------------

    def on_message(self, msg: GameMessage) -> None:
        match msg:
            case NpcRoster():
                if msg.hour is not None and msg.minute is not None:
                    self.game_time = (msg.hour, msg.minute)
                entries = {e.id.upper(): e for e in msg.npcs}
                self.people = entries if msg.full else {**self.people, **entries}
                self._check_end()
            case PttStart():
                self.interrupt()  # the hero starts to speak: whoever answers him stops
            case PlayerText():
                self.interrupt()
                self.hear(msg.text, msg.focus, {"via": "text"})
            case LineStarted():
                if self.partner is not None and msg.npc.upper() == self.partner.npc:
                    self.partner.last_at = self.clock()
            case NpcEvent():
                if msg.other == "PLAYER":
                    self._hero_act(msg.npc.upper(), msg.kind, msg.detail)
            case Perception():
                if msg.other == "PLAYER" and msg.perc in PERC_ACTS:
                    self._hero_act(msg.npc.upper(), PERC_ACTS[msg.perc], None)
            case Attached():
                self.attached.add(msg.npc.upper())
            case Detached():
                npc = msg.npc.upper()
                self.attached.discard(npc)
                self.held.pop(npc, None)  # free again (following ended, handed to its scripts...)
            case Loading():
                self.partner = None
                self.people.clear()
                self.held.clear()
                self.attached.clear()

    def hear(self, text: str, focus: str | None = None, details: dict[str, Any] | None = None) -> None:
        """The hero said `text` (voice or the input line): to whom, witnesses, the answer."""
        text = text.strip()
        if not text:
            return
        now = self.clock()
        to = addressee(
            text, focus, self.people, self.partner.npc if self.partner else None, self.scene_hint()
        )
        order = commands.parse(text, self._is_name)
        squad: list[tuple[str, str]] = []
        beast = self.beasts.hears(text, focus, to, order) if self.beasts is not None else None
        if beast is not None:  # said to a wolf: it is judging its tone already
            to = beast
        elif squad := self._order(order, focus, to):  # said to the companions who obey it
            to = Addressee(squad[0][0], squad[0][1], "order")
        self.last = (to, text)
        echo = TranscriptCmd(text=text, to=to.id if to else None, to_name=to.name if to else None)
        self._spawn(self.link.send(echo))
        self.journal.add(
            "player",
            npc=to.id if to else "",
            name=to.name if to else "",
            text=text,
            by=to.why if to else "nobody",
            **(details or {}),
        )
        log.info("hero -> %s (%s): %s", to.id if to else "nobody", to.why if to else "-", text)
        self._witnesses(text, to, {npc for npc, _ in squad})
        if self.planner is not None and beast is None:
            self.planner.on_player_text(to.id if to else "", text, now)
        if squad or beast is not None:
            return  # carried out already (an order is in their memories), no answer is asked
        if to is None:
            self._spawn(self.link.send(ChatStatusCmd(status="notice", detail="Рядом некому ответить")))
            return
        talk = self.partner
        if talk is None or talk.npc != to.id:
            if talk is not None:
                self._end("switched", to.name)
            talk = self._open(to.id, to.name, now)
        talk.last_at = now
        self._spawn(self._answer(talk, text))

    def _order(
        self, order: commands.Order | None, focus: str | None, to: Addressee | None
    ) -> list[tuple[str, str]]:
        """If the phrase is an order and companions obey it, carry it out now; who obeys."""
        if self.orders is None or order is None:
            return []
        return self.orders.take(order, focus, to)

    def _is_name(self, word: str) -> bool:
        """The word is the name of someone around ("Горн, стой!")."""
        for e in self.people.values():
            for part in words(e.name):
                if part == word or (len(part) >= 3 and name_edits(word, part) is not None):
                    return True
        return False

    def begin(self, npc: str, name: str, posture: str | None = None) -> bool:
        """The npc opens the conversation itself (it came up to the hero): it is his partner now, and his next
        words go to it. Not while he talks to another; True if it is the partner already."""
        npc = npc.upper()
        if self.partner is not None:
            return self.partner.npc == npc
        self._open(npc, name, self.clock()).posture = posture
        return True

    def _open(self, npc: str, name: str, now: float) -> Talk:
        self.dialogue.open(npc, name).new_meeting = True
        self.partner = Talk(npc, name, now, now)
        if self.planner is not None:
            self.planner.talking(npc, True)
        return self.partner

    def interrupt(self) -> bool:
        """The hero speaks: the partner stops answering (what it said so far stays in its memory), and so
        do two npc's talking near him. Synchronous: what they said is in their memories before his words
        are answered. True if anyone stopped."""
        cut = any([f() for f in self.on_hero_voice])  # every one of them, not just up to the first
        if self.partner is None or not self.dialogue.interrupt(self.partner.npc):
            return cut
        self.journal.add(
            "interrupt", npc=self.partner.npc, name=self.partner.name, **({"scene": True} if cut else {})
        )
        return True

    def end(self, why: str = "walked_away") -> None:
        """The conversation is over (scenarios, the console)."""
        self._end(why)

    def on_npc_end(self, npc: str) -> None:
        """The npc said goodbye."""
        if self.partner is not None and self.partner.npc == npc.upper():
            self._end("npc")

    # --- the answer -----------------------------------------------------------------------

    async def _answer(self, talk: Talk, text: str) -> None:
        npc = talk.npc
        snap = asyncio.create_task(self._snapshot(npc)) if npc not in self.attached else None
        if npc in self.held:  # it listens, walks along or follows already
            if "scene" in self.held[npc]:  # it stood talking to another (gossip.py): now it listens to him
                self.held[npc].add("listen")
                self._spawn(self._act(npc, "turn_to", npc="PLAYER"))
            choice: str | None = "listen" if "listen" in self.held[npc] else "walk_with"
        elif npc in self.attached:
            choice = None  # held by someone else (a scenario, the console): it stands as before
        else:
            choice = await self._apply(npc, await self._posture(talk, text))
        talk.posture = choice
        if snap is not None:
            await snap
        if self.partner is not talk:
            return  # the hero turned to someone else meanwhile
        body = POSTURE_OPTIONS[choice].ru if choice else None
        self.dialogue.say_as_player(npc, text, overlay=choice not in (None, "listen"), body=body)

    async def _snapshot(self, npc: str) -> None:
        """A free npc's inventory and surroundings for its turn (attached ones report theirs)."""
        try:
            await self.link.snapshot(npc, fresh=True, timeout=SNAPSHOT_WAIT_S)
        except (GameError, TimeoutError) as err:
            log.info("no snapshot of %s: %s", npc, err)

    async def _posture(self, talk: Talk, text: str) -> str:
        entry = self.people.get(talk.npc)
        default = default_posture(entry)
        if self.reflexes is None:
            return default
        conv = self.dialogue.conversations.get(talk.npc)
        ctx = NpcContext(
            talk.npc,
            talk.name,
            entry.guild if entry else 0,
            entry.distance if entry else 0.0,
            entry.in_view if entry else True,
            traits=conv.persona.traits if conv else None,
            view=self.view_of(talk.npc),
        )
        clock = clock_text(*self.game_time) if self.game_time else None
        reaction = await self.reflexes.posture(ctx, text, doing(entry), conv.intent if conv else None, clock)
        if reaction is None:
            return default
        if self.trace is not None:
            self.trace.write({**reaction.record(), "default": default})
        log.info(
            "%s posture %s (%.2f, %.0f ms)%s",
            talk.npc,
            reaction.choice,
            reaction.confidence,
            reaction.latency_ms,
            "" if reaction.applied else f" not applied: {reaction.why}, {default} instead",
        )
        return reaction.choice if reaction.applied else default

    async def _apply(self, npc: str, choice: str) -> str:
        """Carry the posture out; what the body really does (holding it may fail)."""
        match choice:
            case "listen":
                if await self.listen(npc):
                    return "listen"
            case "walk_with":
                if await self.hold(npc, "walk"):
                    self._spawn(self._act(npc, "follow", npc="PLAYER"))
                    return "walk_with"
            case "leave":
                self._spawn(self._soft(npc, "turn_away"))
                return "leave"
        self._spawn(self._soft(npc, "look_at", ms=LOOK_MS))
        return "keep_working"

    async def listen(self, npc: str) -> bool:
        """The npc stops and turns to the hero, held while it listens; False if its body cannot be held."""
        npc = npc.upper()
        if not await self.hold(npc, "listen"):
            return False
        self._spawn(self._act(npc, "turn_to", npc="PLAYER"))
        return True

    async def _act(self, npc: str, action: str, /, **args: Any) -> None:
        try:
            await self.link.act(npc, action, timeout=10, **args)  # type: ignore[arg-type]
        except (GameError, TimeoutError) as err:
            log.info("%s %s failed: %s", npc, action, err)

    async def _soft(self, npc: str, action: str, /, **args: Any) -> None:
        try:
            await self.link.npc_act(npc, action, **args)  # type: ignore[arg-type]
        except (GameError, TimeoutError) as err:
            log.info("%s %s failed: %s", npc, action, err)

    # --- the body ---------------------------------------------------------------------------

    async def hold(self, npc: str, why: str) -> bool:
        """Hold a free npc's body for `why` (attach it). An npc someone else holds is lent as it is."""
        npc = npc.upper()
        if npc in self.held:
            self.held[npc].add(why)
            return True
        if npc in self.attached:
            return True
        try:
            await self.link.attach(npc)
        except (GameError, TimeoutError) as err:
            log.info("%s cannot be held for %s: %s", npc, why, err)
            return False
        self.held.setdefault(npc, set()).add(why)
        log.info("%s held for %s", npc, why)
        return True

    def free(self, npc: str, why: str) -> None:
        """`why` is done; with nothing else to do the npc goes back to its routine."""
        npc = npc.upper()
        reasons = self.held.get(npc)
        if reasons is None or why not in reasons:
            return
        reasons.discard(why)
        if reasons:
            return
        del self.held[npc]
        log.info("%s free again (%s done)", npc, why)
        self._spawn(self._detach(npc))

    async def _detach(self, npc: str) -> None:
        try:
            await self.link.detach(npc)
        except (GameError, TimeoutError) as err:
            log.info("%s detach failed: %s", npc, err)

    # --- the end, witnesses, the hero's doings ----------------------------------------------

    def _check_end(self) -> None:
        talk = self.partner
        if talk is None:
            return
        entry = self.people.get(talk.npc)
        if self.held.get(talk.npc, set()) & ERRANDS and (entry is None or entry.activity != "down"):
            talk.last_at = self.clock()  # away on an errand for this conversation: it comes back
            return
        if entry is None or entry.activity == "down":
            self._end("gone")
            return
        if entry.distance <= END_CM:
            return
        if not talk.walked_off and self.dialogue.speaking(talk.npc):
            talk.walked_off = True
            self.dialogue.note(talk.npc, "Герой ушёл, не дослушав тебя")
        if self.clock() - talk.last_at >= self.end_silence_s:
            self._end("walked_away")

    def _end(self, why: str, other: str | None = None) -> None:
        talk, self.partner = self.partner, None
        if talk is None:
            return
        log.info("conversation with %s over: %s", talk.npc, why)
        self.journal.add("talk_end", npc=talk.npc, name=talk.name, reason=why)
        if why == "walked_away" and not talk.walked_off:
            self.dialogue.note(talk.npc, "Герой ушёл, разговор закончился")
        elif why == "switched":
            self.dialogue.note(talk.npc, f"Герой отвернулся от тебя и заговорил с другим: {other}")
        self.free(talk.npc, "listen")
        self.free(talk.npc, "walk")
        if self.planner is not None:
            self.planner.talking(talk.npc, False)
        for listener in self.on_talk_end:
            listener(talk.npc, why)

    def _witnesses(self, text: str, to: Addressee | None, obeying: Collection[str] = ()) -> None:
        for e in self.people.values():
            npc = e.id.upper()
            if npc in obeying:
                continue  # an order: it remembers that it was given one (orders.py)
            if (to is None or npc != to.id) and e.distance <= OVERHEAR_CM and e.activity != "down":
                self.dialogue.overheard(npc, e.name, text, to.name if to else None)

    def _hero_act(self, npc: str, kind: str, detail: str | None) -> None:
        talk = self.partner
        if talk is None or talk.npc != npc or kind not in HERO_ACTS:
            return
        now = self.clock()
        if now - self._noted.get((npc, kind), float("-inf")) < NOTE_EVERY_S:
            return
        self._noted[(npc, kind)] = now
        whom = "тебя" if detail in (None, "you") else ("героя" if detail == "PLAYER" else detail)
        self.dialogue.note(npc, HERO_ACTS[kind].format(detail=whom))

    def _spawn(self, coro: Coroutine[Any, Any, Any]) -> None:
        async def run() -> None:
            try:
                await coro
            except GameError:
                pass

        task = asyncio.get_running_loop().create_task(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


def default_posture(entry: RosterEntry | None) -> str:
    """Without the behavioral model's word: an npc standing about stops and listens, a busy one keeps on."""
    if entry is None:
        return "listen"
    if entry.moving or entry.activity == "fight":
        return "keep_working"
    state = entry.state.upper()
    return "listen" if not state or state.startswith(IDLE_STATES) else "keep_working"


def doing(entry: RosterEntry | None) -> str:
    """What the npc is doing, for the behavioral model: "Right now you {doing}." """
    if entry is None or (not entry.state and not entry.moving):
        return "stand about"
    if entry.moving:
        return "are walking somewhere"
    return describe(entry.state)
