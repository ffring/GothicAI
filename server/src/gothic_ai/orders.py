"""Orders to the hero's companions, carried out at once: "За мной!", "Стой!", "В атаку!" (commands.py
tells an order from other words).

The conversational model answers in seconds, too late for "Стой!" in a fight. An order skips it: the
body moves in the moment the transcript is in, and the npc says a short "Понял!" in its own voice
(fillers.py). The order goes into its memory, so the conversational model knows of it next time, but
no answer is asked of it.

**Who obeys**: an npc that goes with the hero — its intent is to follow or to guard him, it agreed to in
conversation — or that stands where he told it to. Named, only that one ("Горн, стой!"); else every
such npc within ORDER_CM. To anyone else the words are no order: they go to the conversational model,
and the npc decides (it may agree to come along). So does an attack with nobody to attack (the hero
looks at no one): the npc asks whom.

**What the body does** (bridge actions; the npc is held through talk.py while it obeys):
- follow: follows the hero; come, retreat: breaks off whatever it does (a fight, a wait) and follows
  him;
- halt: stops where it is, for HALT_S, then goes on as before; wait: stays there until the next
  order, at most WAIT_S, then goes back to its own business;
- attack: fights whom the hero looks at or names ("Убей бандита!"), else whoever struck him last (the
  game's own combat), then follows him again;
- draw, sheathe: takes its weapon out or puts it away (sheathe also ends a fight it was ordered into);
- dismiss: goes back to its own business; it no longer goes with the hero.
A new order replaces the one before (the bridge preempts the running action). "Стой!" said looking at
someone who just struck the hero is words to him, not an order.

**A companion fights without an order**, as the original party members do: whoever strikes the hero, it
or another companion, and a monster that comes close (the game's own combat), then it goes on with the
hero. It does not wait for the conversational model, which only finds out (incidents.py words it). A
companion told to wait stands up for the hero only near it; after "Отступай!" it fights no one for a
while, unless struck itself. The conversational model's own "fight X" for a companion is carried out here
too (dialogue.py hands it over), so that it ends with the companion back at the hero's side.

**Words the conversational model acts on** ("Заходи в пещеру первым"): a companion is shown the places
around it (places.py) and may `go_to` one: it walks there ahead of the hero, looks around it (walks on
through its points until it sees someone) and waits for him, and says what it finds. Whoever is there is
a target for its fight then.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from . import commands
from .commands import Order
from .dialogue import DialogueManager
from .executor import COMPANION_GOALS, INCIDENT_GOALS
from .game import GameError, GameLink
from .journal import Journal
from .llm import Tool, ToolCall
from .places import Place, PlaceBook
from .protocol import GameMessage, Loading, NpcView, Perception, Snapshot
from .talk import calls, named, words
from .turn import ToolOutcome

if TYPE_CHECKING:
    from .dialogue import Conversation
    from .talk import Addressee, TalkDesk

log = logging.getLogger(__name__)

ORDER_CM = 5000.0  # companions hear an order this far: the hero shouts it (one sent ahead too)
HALT_S = 20.0  # "Стой!" holds this long
WAIT_S = 600.0  # "Жди здесь!" at most this long
FIGHT_S = 70.0  # a fight is given up after this (the game's own deadline is 60 s)
FOE_S = 30.0  # whoever struck the hero or a companion is a foe this long
ENEMY_CM = 1000.0  # a monster this close to a companion is fought without an order
GUARD_CM = 1500.0  # a companion told to wait stands up for the hero this near it
RETREAT_S = 15.0  # after "Отступай!" a companion fights no one this long, unless struck itself
MONSTER_GUILD = 16  # GIL_SEPERATOR_HUM: the guilds above it are animals and monsters
WEAPONS = frozenset({"draw", "sheathe"})
# orders that, said looking at someone who just struck the hero, are words to him ("Стой!")
TO_FOE = frozenset({"halt", "wait", "dismiss"})
PLACES_CM = 8000.0  # a companion is shown the places this near
PLACES_MAX = 6
GO_S = 90.0  # a walk to a place is given up after this
GO_RADIUS_CM = 400
GO_WAIT_S = 180.0  # it waits there this long, then comes back to the hero
LOOK_POINTS = 4  # seeing nobody there, it walks on through this many more of the place's points
LOOK_RADIUS_CM = 200
# orders after which a companion that had to fight stays where it was told to be
STAY = frozenset({"halt", "wait", "go"})
GO_TO = "go_to"
GO_TO_TOOL = Tool(
    GO_TO,
    "Пойти первым в место рядом (из «Мест рядом» в обстановке) и ждать там героя. Тело пойдёт после твоей "
    "реплики; придёшь — расскажешь, что там. Кто нападёт по дороге, с тем подерёшься сам.",
    {
        "type": "object",
        "properties": {"place": {"type": "string", "description": "id места из «Мест рядом»"}},
        "required": ["place"],
        "additionalProperties": False,
    },
)


@dataclass(eq=False)
class Standing:
    """What an npc does on the hero's order; a later order puts another in its place."""

    kind: str  # the order (commands.KINDS), or "go" (go_to: it went ahead to a place)
    at: float = field(default_factory=time.monotonic)
    where: str = ""  # go: the place, «в пещере бандитов…»


@dataclass(eq=False)
class Fight:
    """A companion's fight: whom it fights now (the next foe after this one); a newer fight replaces it."""

    target: str


def failed(status: str) -> str | None:
    """What went wrong with an action (`Orders._act`), None if nothing did."""
    return None if status in ("done", "cancelled") else status


class Orders:
    def __init__(
        self,
        link: GameLink,
        talk: TalkDesk,
        dialogue: DialogueManager,
        *,
        journal: Journal | None = None,
        places: PlaceBook | None = None,
        halt_s: float = HALT_S,
        wait_s: float = WAIT_S,
        go_wait_s: float = GO_WAIT_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.link = link
        self.talk = talk
        self.dialogue = dialogue
        self.journal = journal or dialogue.journal
        self.places = places
        self.halt_s, self.wait_s, self.go_wait_s = halt_s, wait_s, go_wait_s
        self.clock = clock
        self.standing: dict[str, Standing] = {}
        self.foes: dict[str, float] = {}  # who struck the hero or a companion, and when
        self.fights: dict[str, Fight] = {}  # companion -> its fight now
        # what an npc sees now, asked afresh (perception.py): a companion sent somewhere looks around there
        self.view_of: Callable[[str], Awaitable[NpcView | None]] | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        link.listeners.append(self.on_message)
        # the conversational model's "fight X" for a companion is carried out here (dialogue.py)
        dialogue.companion_fight = self.fight_intent
        dialogue.fighting = lambda npc: npc.upper() in self.fights

    # --- who obeys -----------------------------------------------------------------------------

    def companion(self, npc: str) -> bool:
        """The npc goes with the hero (or stands on his order)."""
        npc = npc.upper()
        return npc in self.standing or self._following(npc)

    def foe(self, npc: str) -> bool:
        """The npc struck the hero or a companion a moment ago and is still on its feet."""
        npc = npc.upper()
        if self.clock() - self.foes.get(npc, float("-inf")) > FOE_S:
            return False
        e = self.talk.people.get(npc)
        return e is None or e.activity != "down"

    def squad(self, to: Addressee | None, heard: str = "") -> list[tuple[str, str]]:
        """Who obeys an order: the npc called by name if it is a companion (else nobody: it is words to
        it), else every companion near enough, or the ones of them `heard` calls by name: a name carries
        farther than talk when it is shouted after one sent ahead. (id, name)."""
        if to is not None and to.why == "name":
            return [(to.id, to.name)] if self.companion(to.id) else []
        near = [
            e
            for e in sorted(self.talk.people.values(), key=lambda e: e.distance)
            if e.activity != "down" and e.distance <= ORDER_CM and self.companion(e.id)
        ]
        if heard:
            phrase, by = words(heard), calls(heard)
            near = [e for e in near if named(phrase, e.name, by) is not None] or near
        return [(e.id.upper(), e.name) for e in near]

    def target(self, focus: str | None, squad: list[tuple[str, str]]) -> str | None:
        """Whom to attack: the one the hero looks at, if not himself or one of his own."""
        if focus is None:
            return None
        focus = focus.upper()
        if focus == "PLAYER" or focus in {npc for npc, _ in squad} or self.companion(focus):
            return None
        e = self.talk.people.get(focus)
        return None if e is not None and e.activity == "down" else focus

    def latest_foe(self, near: str | None = None) -> str | None:
        """Whoever struck the hero or a companion last and is still about (near the companion `near`, if
        given: a monster is not in the roster, only the companion sees it)."""
        seen = self._near(near) if near is not None else None
        for npc, _ in sorted(self.foes.items(), key=lambda f: -f[1]):
            if not self.foe(npc):
                continue
            e = self.talk.people.get(npc)
            if e is not None and e.distance > ORDER_CM:
                continue
            if seen is not None and npc not in seen:
                continue
            return npc
        return None

    def take(self, order: Order, focus: str | None, to: Addressee | None) -> list[tuple[str, str]]:
        """Carry the order out at once, if anyone obeys it; who does ([]: nobody, it is words)."""
        if order.kind in TO_FOE and to is not None and to.why == "focus" and self.foe(to.id):
            return []  # "Стой!" to whoever is beating him
        target = None
        if order.kind == "attack" and to is not None and to.why == "name" and not self.companion(to.id):
            target, squad = to.id, self.squad(None)  # "Убей бандита!": the one named is the target
        else:
            squad = self.squad(to, order.heard)
            if order.kind == "attack":
                target = self.target(focus, squad) or self.latest_foe()
        if not squad or (order.kind == "attack" and target is None):
            return []
        for npc, name in squad:
            before = self.standing.get(npc)
            if order.kind in WEAPONS:
                standing = before  # the weapon changes no standing order: it waits, follows... as before
            else:
                standing = self.standing[npc] = Standing(order.kind)
            self.journal.add("order", npc=npc, name=name, order=order.kind, text=order.heard, target=target)
            log.info("order %s -> %s (%s)%s", order.kind, npc, name, f" at {target}" if target else "")
            self._spawn(self._obey(npc, name, order, standing, before, target))
        return squad

    # --- the body ------------------------------------------------------------------------------

    def _current(self, npc: str, standing: Standing) -> bool:
        return self.standing.get(npc) is standing

    def _following(self, npc: str) -> bool:
        """It goes with the hero: it agreed to, and a goal of the moment (a fight) has not changed that."""
        conv = self.dialogue.conversations.get(npc.upper())
        if conv is None:
            return False
        goal = conv.intent.goal
        if goal in INCIDENT_GOALS and goal not in COMPANION_GOALS and conv.calm is not None:
            goal = conv.calm.goal
        return goal in COMPANION_GOALS

    async def _obey(
        self,
        npc: str,
        name: str,
        order: Order,
        standing: Standing | None,
        before: Standing | None,
        target: str | None,
    ) -> None:
        self._spawn(self.dialogue.acknowledge(npc, name))
        what = commands.SAID[order.kind]
        self.dialogue.remember(npc, name, f"Герой приказал тебе: «{order.heard}» — {what}. Ты подчинился.")
        if order.kind in WEAPONS:
            failed = await self._weapon(npc, order.kind, before)
        else:
            assert standing is not None
            failed = await self._carry_out(npc, name, order.kind, standing, target)
        if failed:
            self.dialogue.remember(npc, name, f"Приказ героя ({what}) ты выполнить не смог: {failed}")

    async def _carry_out(
        self, npc: str, name: str, kind: str, standing: Standing, target: str | None
    ) -> str | None:
        """The body's part of the order; what did not work, None if all did."""
        if kind in ("follow", "come", "retreat"):
            return await self._follow(npc, stop=kind != "follow")
        if kind == "dismiss":
            self.standing.pop(npc, None)
            await self._act(npc, "stop")
            if self._following(npc):
                self.dialogue.change_intent(npc, name, goal="keep_routine", target=None, body=False)
            self.talk.free(npc, "follow")
            self.talk.free(npc, "order")
            return None
        if kind == "attack":
            assert target is not None
            return await self._fight(npc, Fight(target), standing)
        if not await self.talk.hold(npc, "order"):
            return "тебя не отпускают"
        # halt, wait
        status = await self._act(npc, "stop")
        self._later(self.halt_s if kind == "halt" else self.wait_s, npc, standing)
        return failed(status)

    async def _weapon(self, npc: str, kind: str, before: Standing | None) -> str | None:
        """Take the weapon out or put it away; put away, it ends a fight the npc was ordered into. An npc
        not held for anything is held only for this."""
        held = npc in self.talk.held
        if not await self.talk.hold(npc, "order"):
            return "тебя не отпускают"
        if kind == "sheathe" and before is not None and before.kind == "attack":
            await self._resume(npc, before)
        status = await self._act(npc, "draw_weapon" if kind == "draw" else "remove_weapon")
        if not held:
            self.talk.free(npc, "order")
        return failed(status)

    async def _follow(self, npc: str, *, stop: bool) -> str | None:
        """Follow the hero; `stop`: break off what it does first (a fight, a wait)."""
        if not await self.talk.hold(npc, "follow"):
            return "тебя не отпускают"
        if stop and (err := failed(await self._act(npc, "stop"))) is not None:
            return err
        err = failed(await self._act(npc, "follow", npc="PLAYER"))
        self.talk.free(npc, "order")
        return err

    def _start(self, npc: str, target: str, standing: Standing | None) -> None:
        fight = self.fights[npc] = Fight(target)  # at once: the next perception finds it fighting
        self._spawn(self._fight(npc, fight, standing))

    async def _fight(self, npc: str, fight: Fight, standing: Standing | None) -> str | None:
        """Fight its target, then the next foe still about, then go on as before the fight: a companion
        follows the hero again, one told to wait or stop stays where it is. What went wrong, if anything."""
        self.fights[npc] = fight
        try:
            status = await self._fights(npc, fight, standing)
        finally:
            if self.fights.get(npc) is fight:
                del self.fights[npc]
        if status is None:
            return None  # a later order or fight took over
        if standing is not None and standing.kind in STAY:
            await self._act(npc, "stop")  # back to standing there, until its order runs out
        else:
            await self._resume(npc, standing)
        return failed(status)

    async def _fights(self, npc: str, fight: Fight, standing: Standing | None) -> str | None:
        """The fights themselves: the last one's status, None if something else took over."""
        if not await self.talk.hold(npc, "order"):
            return "тебя не отпускают"
        while True:
            status = await self._act(npc, "fight", npc=fight.target, timeout=FIGHT_S)
            if status == "cancelled" or self.fights.get(npc) is not fight:
                return None
            if standing is not None and not self._current(npc, standing):
                return None
            if status == "done":
                self.foes.pop(fight.target, None)  # down or gone
            nxt = self.latest_foe(npc)
            if nxt is None or nxt == fight.target or failed(status):
                return status
            fight.target = nxt
            log.info("%s fights on: %s", npc, nxt)

    async def _resume(self, npc: str, standing: Standing | None) -> None:
        """After an order that ends (attack, halt): a companion follows the hero again, an npc that only
        stood on his order goes back to its business."""
        if standing is not None and not self._current(npc, standing):
            return
        self.standing.pop(npc, None)
        conv = self.dialogue.conversations.get(npc)
        if conv is not None and conv.intent.goal in INCIDENT_GOALS - COMPANION_GOALS:
            self.dialogue.incident_over(npc, body=False)  # its fight is over: it means to go with him again
        if self._following(npc):
            await self._follow(npc, stop=True)
        else:
            await self._act(npc, "stop")
            self.talk.free(npc, "order")

    # --- fights without an order -----------------------------------------------------------------

    def on_message(self, msg: GameMessage) -> None:
        match msg:
            case Perception():
                self._perceived(msg)
            case Loading():
                self.foes.clear()

    def _perceived(self, p: Perception) -> None:
        npc, other = p.npc.upper(), p.other.upper()
        if other == "PLAYER" or other == npc or self.companion(other):
            return
        if p.perc in ("ASSESSDAMAGE", "ASSESSOTHERSDAMAGE"):
            victim = npc if p.perc == "ASSESSDAMAGE" else (p.victim or "").upper()
            if victim != "PLAYER" and not self.companion(victim):
                return
            self.foes[other] = self.clock()
            if npc in self.talk.attached and self.companion(npc):
                did = (
                    "напал на тебя"
                    if victim == npc
                    else "напал на героя"
                    if victim == "PLAYER"
                    else (f"напал на: {self._name(npc, victim)}")
                )
                self._stand_up(npc, other, did, struck=victim == npc)
        elif p.perc == "ASSESSENEMY" and p.distance <= ENEMY_CM and self._monster(npc, other):
            if npc in self.talk.attached and self.companion(npc):
                self._stand_up(npc, other, "бросается на вас", struck=False)

    def _stand_up(self, npc: str, foe: str, did: str, *, struck: bool) -> None:
        """A companion fights `foe` without an order: it struck the hero, the companion or another one, or it
        is a monster close by. Not when it fights already, retreats, or waits away from the hero."""
        if npc in self.fights:
            return
        standing = self.standing.get(npc)
        if standing is not None and not struck:
            if standing.kind == "retreat" and self.clock() - standing.at < RETREAT_S:
                return
            if standing.kind == "wait" and self._hero_cm(npc) > GUARD_CM:
                return
        conv = self.dialogue.conversations.get(npc)
        name = conv.persona.name if conv else npc
        foe_name = self._name(npc, foe)
        log.info("%s stands up for the hero: fights %s", npc, foe)
        self.journal.add("defend", npc=npc, name=name, target=foe)
        self.dialogue.remember(npc, name, f"{foe_name} {did} — ты бросился в бой.")
        self._start(npc, foe, standing)

    def fight_intent(self, npc: str, target: str | None) -> bool:
        """The conversational model set a companion on `target`: it fights it here and comes back to the hero
        after. False for an npc that does not go with the hero, or a target that is no foe for a companion."""
        npc = npc.upper()
        if not self._following(npc) and npc not in self.standing:
            return False
        if target is None:
            return True  # whoever: it stands up for the hero anyway
        target = target.upper()
        if target == "PLAYER" or target == npc or self.companion(target):
            return False
        if (fight := self.fights.get(npc)) is not None and fight.target == target:
            return True
        self._start(npc, target, self.standing.setdefault(npc, Standing("attack")))
        return True

    def fighting_ru(self, npc: str) -> str | None:
        """What the companion's body does in the fight, for its words (incidents.py)."""
        fight = self.fights.get(npc.upper())
        return f"ты дерёшься с: {self._name(npc, fight.target)}" if fight else None

    def _near(self, npc: str) -> set[str]:
        snap = self.link.snapshots.get(npc.upper())
        return {o.id.upper() for o in snap.nearby_npcs if not o.dead} if snap else set()

    def _monster(self, npc: str, other: str) -> bool:
        if other in self.talk.people:
            return False  # people are in the roster, animals and monsters never are
        snap = self.link.snapshots.get(npc)
        seen = next((o for o in snap.nearby_npcs if o.id.upper() == other), None) if snap else None
        return seen is None or seen.guild > MONSTER_GUILD

    def _name(self, npc: str, other: str | None) -> str:
        if other is None:
            return "кто-то"
        if (e := self.talk.people.get(other.upper())) is not None:
            return e.name
        snap = self.link.snapshots.get(npc.upper())
        seen = next((o for o in snap.nearby_npcs if o.id.upper() == other.upper()), None) if snap else None
        return seen.name if seen else other

    def _hero_cm(self, npc: str) -> float:
        e = self.talk.people.get(npc.upper())
        return e.distance if e is not None else float("inf")

    # --- places: what the conversational model sends a companion to ------------------------------

    async def prepare(self, npc: str) -> None:
        return None

    def nearby(self, npc: str) -> list[tuple[Place, float]]:
        """The places around a companion, nearest first, but the one it is in."""
        snap = self.link.snapshots.get(npc.upper())
        if self.places is None or snap is None:
            return []
        here = self.places.of(snap.npc.waypoint, snap.npc.nearest_waypoint)
        near = self.places.near(snap.npc.pos, PLACES_CM, PLACES_MAX + 1)
        return [(p, d) for p, d in near if here is None or p.id != here.id][:PLACES_MAX]

    def situation(self, npc: str) -> str | None:
        if not self.companion(npc):
            return None
        lines = [
            "Ты идёшь с героем. В драке тело само защищает его и тебя; напасть на кого-то — set_intent "
            "с целью fight и его id из целей."
        ]
        if near := self.nearby(npc):
            listed = "; ".join(f"{p.id} — {p.ru}, {max(1, round(d / 100))} м" for p, d in near)
            lines.append(f"Места рядом (go_to — пойти туда первым и ждать героя): {listed}.")
        if (standing := self.standing.get(npc.upper())) is not None and standing.kind == "go":
            lines.append(f"Ты пошёл первым и ждёшь героя: {standing.where}.")
        return "\n".join(lines)

    def tools(self, npc: str) -> list[Tool]:
        return [GO_TO_TOOL] if self.places is not None and self.companion(npc) else []

    async def handle(
        self, dialogue: DialogueManager, conv: Conversation, call: ToolCall, snap: Snapshot | None
    ) -> ToolOutcome | None:
        if call.name != GO_TO:
            return None
        if not self.companion(conv.npc):
            return ToolOutcome("Отклонено: ты не идёшь с героем.", "rejected")
        near = {p.id: p for p, _ in self.nearby(conv.npc)}
        place = near.get(str(call.arguments.get("place", "")).strip().upper())
        if place is None:
            listed = ", ".join(f"{k} ({p.ru})" for k, p in near.items()) or "нет"
            return ToolOutcome(f"Отклонено: такого места рядом нет. Места рядом: {listed}.", "rejected")
        self.journal.add(
            "action", npc=conv.npc, name=conv.persona.name, action=GO_TO, arguments={"place": place.id}
        )
        log.info("%s goes ahead to %s", conv.npc, place.id)
        standing = self.standing[conv.npc] = Standing("go", where=place.where_ru)
        dialogue.after_speech(conv, lambda: self._go(conv.npc, conv.persona.name, place, standing))
        return ToolOutcome(f"Принято: пойдёшь {place.where_ru} после реплики.", "accepted")

    async def _go(self, npc: str, name: str, place: Place, standing: Standing) -> tuple[str, bool]:
        """Walk to the place ahead of the hero and wait there; on arrival, say what is there."""
        if not self._current(npc, standing):
            return "", True  # an order came first
        if not await self.talk.hold(npc, "order"):
            return "Ты не смог пойти: тебя не отпускают", False
        assert place.center is not None
        status = await self._act(
            npc,
            "go_to_waypoint",
            waypoint=place.points[0],
            points=list(place.points),
            pos=list(place.center),
            radius=GO_RADIUS_CM,
            timeout_ms=int(GO_S * 1000),
            timeout=GO_S + 10,
        )
        if status == "cancelled" or not self._current(npc, standing):
            return "", True
        if failed(status):
            await self._resume(npc, standing)
            return f"Ты не дошёл ({status}) и вернулся к герою.", False
        # it stays facing the way it came in, into the place: turned to the hero, it would have whoever is
        # there at its back (Cavalorn saw nobody in the bandits' cave, 2026-10-13)
        if await self._look_around(npc, place, standing) is None:
            return "", True  # an order came meanwhile
        self._later(self.go_wait_s, npc, standing)
        self.dialogue.event_turn(
            npc,
            f"[Событие: ты пришёл первым — {place.where_ru} — и ждёшь героя. Скажи ему, что здесь.]",
            overlay=True,
        )
        return f"Ты дошёл: {place.where_ru}.", True

    async def _look_around(self, npc: str, place: Place, standing: Standing) -> bool | None:
        """Walk on through the place's other points until it sees someone there, a few at most: from its
        middle a cave's corner hides who sits at its fire (the bandits' cave, 2026-10-14). Whether it saw
        anyone; None if an order came meanwhile."""
        if self.view_of is None:
            return False
        snap = self.link.snapshots.get(npc)
        here = {snap.npc.waypoint, snap.npc.nearest_waypoint} if snap is not None else set()
        for wp in [None, *[p for p in place.points if p not in here][:LOOK_POINTS]]:
            if wp is not None:
                status = await self._act(
                    npc, "go_to_waypoint", waypoint=wp, radius=LOOK_RADIUS_CM, timeout_ms=20000, timeout=30
                )
                if status == "cancelled" or not self._current(npc, standing):
                    return None
            try:
                view = await self.view_of(npc)
            except (GameError, TimeoutError):
                return False
            if view is not None and any(o.kind != "body" and not self.companion(o.id) for o in view.sees):
                return True
        return False

    def _later(self, seconds: float, npc: str, standing: Standing) -> None:
        async def run() -> None:
            await asyncio.sleep(seconds)
            if not self._current(npc, standing):
                return
            if standing.kind == "wait":  # waited long enough: back to its own business
                self.standing.pop(npc, None)
                await self._act(npc, "stop")
                self.talk.free(npc, "follow")
                self.talk.free(npc, "order")
                return
            await self._resume(npc, standing)

        self._spawn(run())

    async def _act(self, npc: str, action: str, /, *, timeout: float = 10, **args: Any) -> str:
        """ "done", "cancelled" (a later order took over) or what went wrong."""
        try:
            res = await self.link.act(npc, action, timeout=timeout, **args)  # type: ignore[arg-type]
        except (GameError, TimeoutError) as err:
            log.info("%s %s failed: %s", npc, action, err or "no answer")
            return f"{action}: {err or 'нет ответа'}"
        if res.status in ("done", "cancelled"):
            return res.status
        log.info("%s %s: %s %s", npc, action, res.status, res.detail or "")
        return f"{action}: {res.detail or res.status}"

    def _spawn(self, coro: Coroutine[Any, Any, Any]) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
