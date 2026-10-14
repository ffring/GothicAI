"""The director as author, stage 1 (docs/director.md): a quest of its own for a person of the world.

The model proposes; the code builds the change, the validator checks it, the game plays it, as with the
repairs (director.py) and the amends (amends.py). The quest: someone has lost a thing and asks the hero to
find it and bring it back. It is made of
- its own story variable DIR_Q<n> (graph_patch `vars`, v29): 0 — not given, LOG_RUNNING, LOG_SUCCESS; the
  game keeps it in its save like the scripts' quest variables;
- its own journal topic;
- the giver's dialog that gives it: the talk, the quest set running, the journal, and the thing put into the
  world at a point of a place near the giver (Wld_InsertItem), so it can always be found;
- the dialog that ends it, offered once the hero has the thing: the giver takes it and pays, the quest
  succeeds, the journal closes;
- the reward within the chapter's measure: experience no more than the scripts' XP_AMBIENT (what the game
  gives for a small task in this chapter), as much gold.

The things are ITEMS: ordinary ones anybody could lose, none that a quest of the scripts uses. The quest's
state is in the game (its variable, the journal), and in the chronicle (`authored*` facts) for the giver's
conversational model; the dialogs are in the patches of the save branch: a load brings back exactly where
it was.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from .characters import Card
from .chronicle import Chronicle
from .game import GameError, GameLink
from .gamedata.storygraph import StoryGraph
from .journal import Journal
from .llm import LLMBackend, LLMError, Message, Tool, ToolCall
from .places import PlaceBook
from .protocol import DialogChosen, GameMessage, Session, Snapshot
from .story import StoryPatches
from .turn import ToolOutcome
from .validator import DIRECTOR_PREFIX, LOG_RUNNING, LOG_SUCCESS, Caps, Rules, names_in, validate

if TYPE_CHECKING:
    from .dialogue import Conversation, DialogueManager
    from .director import Director

log = logging.getLogger(__name__)

PROPOSE = "propose_quest"
ATTEMPTS = 2
# ordinary things anybody could lose; no dialog of the scripts asks for any of them
ITEMS = (
    "ITMI_SAW",
    "ITMI_HAMMER",
    "ITMI_BROOM",
    "ITMI_RAKE",
    "ITMI_SCOOP",
    "ITMI_BRUSH",
    "ITMI_PLIERS",
    "ITMI_STOMPER",
    "ITMI_LUTE",
    "ITMI_SILVERRING",
    "ITMI_GOLDRING",
)
NEAR_CM = 4000  # places around the giver's own, where the thing may lie
MAX_PLACES = 6
XP_FALLBACK = 50  # when the game does not say XP_AMBIENT
MAX_TITLE = 40
GOLD = "ITMI_GOLD"

INSTRUCTIONS = """\
Ты — сценарист Gothic II. Придумай небольшое поручение, которое житель мира даёт герою: у него пропала вещь \
— потерял, выронил, растащили звери или унёс ветер — и он просит её найти и принести. Вещь лежит в одном из \
мест рядом, и он примерно знает, где. Пиши, как написал бы сценарист оригинальной игры: просто, грубовато, \
в характере этого человека, без пафоса и современных слов.

Выбери вещь и место из сводки и напиши:
- title — тему в журнале героя, 2–4 слова;
- give_description — что герой говорит в меню диалога, до 60 знаков (с этих слов он и начинает \
разговор); give_lines — разговор, в котором NPC просит (2–6 реплик; hero — герой, npc — этот NPC) и говорит, \
где искать, словами места из сводки;
- done_description и done_lines — когда герой приносит вещь (1–4 реплики): NPC берёт её и платит;
- gold и xp — награду, не больше пределов из сводки (можно меньше: это мелкое дело);
- journal_given и journal_done — записи журнала от первого лица героя.
Не выдумывай людей и мест, которых нет в сводке. Ответ — один вызов propose_quest."""

_LINES = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "speaker": {"type": "string", "enum": ["hero", "npc"]},
            "text": {"type": "string"},
        },
        "required": ["speaker", "text"],
        "additionalProperties": False,
    },
}

PROPOSE_TOOL = Tool(
    PROPOSE,
    "Предложить поручение: что пропало, где лежит, разговоры, награда и журнал. Код проверит его и применит "
    "или вернёт причины отказа.",
    {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "тема журнала, 2–4 слова"},
            "item": {"type": "string", "description": "id вещи из сводки"},
            "place": {"type": "string", "description": "id места из сводки"},
            "give_description": {"type": "string", "description": "строка героя в меню, до 60 знаков"},
            "give_lines": _LINES,
            "done_description": {"type": "string", "description": "строка героя в меню, до 60 знаков"},
            "done_lines": _LINES,
            "gold": {"type": "integer"},
            "xp": {"type": "integer"},
            "journal_given": {"type": "string"},
            "journal_done": {"type": "string"},
            "reason": {"type": "string", "description": "почему такое поручение, одной фразой"},
        },
        "required": [
            "title",
            "item",
            "place",
            "give_description",
            "give_lines",
            "done_description",
            "done_lines",
            "gold",
            "xp",
            "journal_given",
            "journal_done",
            "reason",
        ],
        "additionalProperties": False,
    },
)


@dataclass
class Spot:
    """A place near the giver where the thing may lie."""

    id: str
    where: str  # «на полях фермы Лобарта»
    distance_m: int
    point: str  # where the thing is put


@dataclass
class Brief:
    """What the model is told about the one who asks."""

    npc: str
    name: str
    card: str  # «Ты — …», or ""
    home: str  # where it lives and works, or ""
    chapter: int
    items: dict[str, str]  # instance -> name in the game
    spots: list[Spot]
    xp: int  # the most experience
    gold: int  # the most gold
    given: list[str] = field(default_factory=list)  # titles of its quests the author made before

    def text(self) -> str:
        out = [f"Кто просит: {self.name} ({self.npc})."]
        if self.card:
            out.append(f"Его характер (написан ему самому): {self.card}")
        if self.home:
            out.append(f"Где живёт и работает: {self.home}.")
        out.append(f"Глава {self.chapter}.")
        out.append("Вещи, одна из которых у него пропала (id — название):")
        out += [f"- {i} — {n}" for i, n in self.items.items()]
        out.append("Места рядом, где она может лежать (id — где, как далеко от его места):")
        out += [f"- {s.id} — {s.where}, {s.distance_m} м" for s in self.spots]
        out.append(f"Награда: опыт до {self.xp}, золото до {self.gold}.")
        if self.given:
            out.append(
                "Его поручения герою уже были, не повторяйся: " + "; ".join(f"«{t}»" for t in self.given)
            )
        return "\n".join(out)


@dataclass
class Proposal:
    title: str = ""
    item: str = ""
    place: str = ""
    give_description: str = ""
    give_lines: list[dict[str, str]] = field(default_factory=list)
    done_description: str = ""
    done_lines: list[dict[str, str]] = field(default_factory=list)
    gold: int = 0
    xp: int = 0
    journal_given: str = ""
    journal_done: str = ""
    reason: str = ""

    @classmethod
    def of(cls, args: dict[str, Any]) -> Proposal:
        known = {k: args[k] for k in cls.__dataclass_fields__ if k in args}
        return cls(**known)


@dataclass
class Quest:
    """A quest the author made, and where it stands."""

    id: str  # its story variable too
    npc: str
    name: str
    title: str
    item: str
    item_name: str
    place: str
    where: str
    gold: int
    xp: int
    give: str  # the dialogs
    done: str
    stage: str = "open"  # open -> given -> done

    def reward(self) -> str:
        parts = [f"{self.gold} золотых"] if self.gold else []
        parts += [f"опыт +{self.xp}"] if self.xp else []
        return ", ".join(parts) or "ничего"


class AuthorModel:
    """The cloud model that writes the quests."""

    def __init__(self, llm: LLMBackend) -> None:
        self.llm = llm

    async def propose(
        self, brief: Brief, previous: Proposal | None = None, errors: list[str] | None = None
    ) -> Proposal | None:
        messages = [Message("user", brief.text())]
        if previous is not None and errors:
            sent = json.dumps(asdict(previous), ensure_ascii=False)
            reasons = "\n".join(f"- {e}" for e in errors)
            messages.append(Message("user", f"Твоё предложение: {sent}\nОтклонено:\n{reasons}\nИсправь его."))
        calls: list[ToolCall] = []
        try:
            async for ev in self.llm.stream(
                instructions=INSTRUCTIONS, messages=messages, tools=[PROPOSE_TOOL]
            ):
                if isinstance(ev, ToolCall) and ev.name == PROPOSE:
                    calls.append(ev)
        except LLMError as err:
            log.warning("author: the model failed: %s", err)
            return None
        return Proposal.of(calls[0].arguments) if calls else None


class Author:
    """Quests of the director's own; also a TurnExtension: the giver knows what it asked of the hero."""

    def __init__(
        self,
        link: GameLink,
        graph: StoryGraph,
        patches: StoryPatches,
        chronicle: Chronicle,
        director: Director,
        llm: LLMBackend,
        *,
        places: PlaceBook | None = None,
        cards: dict[str, Card] | None = None,
        dialogue: DialogueManager | None = None,
        journal: Journal | None = None,
    ) -> None:
        self.link = link
        self.graph = graph
        self.patches = patches
        self.chronicle = chronicle
        self.director = director
        self.model = AuthorModel(llm)
        self.places = places
        self.cards = cards or {}
        self.dialogue = dialogue
        self.journal = journal or Journal()
        self.rules = Rules.of(graph)
        self.quests: dict[str, Quest] = {}  # by id
        link.listeners.append(self._on_message)
        if dialogue is not None:
            dialogue.extensions.append(self)
        self._load()

    def _on_message(self, msg: GameMessage) -> None:
        match msg:
            case Session():
                self._load()
            case DialogChosen(made=True):
                for q in self.quests.values():
                    if msg.node == q.give and q.stage == "open":
                        self._stage(q, "given")
                    elif msg.node == q.done and q.stage != "done":
                        self._stage(q, "done")

    # --- a quest ------------------------------------------------------------------------------

    async def quest(self, npc: str) -> Quest | None:
        """A quest of the author's that `npc` gives the hero; None if the model has none that passes."""
        npc = npc.upper()
        brief = await self.brief(npc)
        if brief is None:
            return None
        proposal: Proposal | None = None
        errors: list[str] = []
        for attempt in range(ATTEMPTS):
            proposal = await self.model.propose(brief, proposal, errors)
            if proposal is None:
                break
            log.info("author: %s, proposal %d: %s", npc, attempt + 1, proposal)
            made, errors = await self._try(proposal, brief)
            if made is not None:
                return made
            log.info("author: %s, rejected: %s", npc, "; ".join(errors))
            self.chronicle.record({"fact": "authored_rejected", "npc": npc, "why": errors})
        self.journal.add("authored_none", npc=npc, name=brief.name, why=errors)
        return None

    async def brief(self, npc: str) -> Brief | None:
        state = await self.link.story_state(
            vars=["KAPITEL", "XP_AMBIENT"], npcs=[npc], symbols=[npc, *ITEMS], items=list(ITEMS)
        )
        person = (state.get("npcs") or {}).get(npc) or {}
        if person.get("state") != "alive":
            log.info("author: %s is %s", npc, person.get("state", "not in the world"))
            return None
        if self.rules.plot_npc(npc):
            log.info("author: %s is a person of the plot", npc)
            return None
        symbols = state.get("symbols") or {}
        names = state.get("items") or {}
        items = {i: str(names.get(i) or i) for i in ITEMS if symbols.get(i) == "item"}
        home, spots = await self._spots(npc)
        if not items or not spots:
            log.info("author: %s: no things (%d) or places (%d)", npc, len(items), len(spots))
            return None
        vars = state.get("vars") or {}
        xp = int(vars.get("XP_AMBIENT") or XP_FALLBACK)
        card = self.cards.get(npc)
        return Brief(
            npc=npc,
            name=str(person.get("name") or npc),
            card=card.ru if card else "",
            home=home,
            chapter=int(vars.get("KAPITEL") or 1),
            items=items,
            spots=spots,
            xp=xp,
            gold=xp,
            given=[q.title for q in self.quests.values() if q.npc == npc],
        )

    async def _spots(self, npc: str) -> tuple[str, list[Spot]]:
        """Where the npc lives and works (the place it spends most of its day at), and the places around."""
        if self.places is None:
            return "", []
        try:
            routine = await self.link.routine(npc)
        except (GameError, TimeoutError) as err:
            log.warning("author: the routine of %s: %s", npc, err)
            return "", []
        homes = Counter(p.id for e in routine.entries if (p := self.places.of(e.waypoint)) is not None)
        if not homes:
            return "", []
        home = self.places.get(homes.most_common(1)[0][0])
        if home is None or home.center is None:
            return "", []
        own = {e.waypoint.upper() for e in routine.entries}
        spots = []
        for place, d in self.places.near(home.center, NEAR_CM, MAX_PLACES + 1):
            points = sorted(p for p in place.points if p not in own)
            if place.id == home.id or not points:
                continue  # under its own feet: not lost
            spots.append(Spot(place.id, place.where_ru, round(d / 100), points[len(points) // 2]))
        return home.where_ru, spots[:MAX_PLACES]

    async def _try(self, p: Proposal, brief: Brief) -> tuple[Quest | None, list[str]]:
        """Build the quest from the proposal, check it, make it."""
        errors = []
        item = p.item.strip().upper()
        spot = next((s for s in brief.spots if s.id == p.place.strip().upper()), None)
        if item not in brief.items:
            errors.append(f"вещи {p.item!r} нет в сводке")
        if spot is None:
            errors.append(f"места {p.place!r} нет в сводке")
        title = p.title.strip()
        if not title or len(title) > MAX_TITLE:
            errors.append(f"тема журнала пустая или длиннее {MAX_TITLE} знаков")
        if not p.journal_given.strip() or not p.journal_done.strip():
            errors.append("нужны обе записи журнала: journal_given и journal_done")
        if not 0 <= p.gold <= brief.gold:
            errors.append(f"золота {p.gold}, а можно от 0 до {brief.gold}")
        if not 0 <= p.xp <= brief.xp:
            errors.append(f"опыта {p.xp}, а можно от 0 до {brief.xp}")
        if any(q.title == title for q in self.quests.values()):
            errors.append(f"поручение «{title}» уже было")
        if errors or spot is None:
            return None, errors

        story = await self.director.story()
        id = self._id(story.patches.vars)
        give, done = f"{id}_GIVE", f"{id}_DONE"
        var = ["var", id]
        reward: list[dict[str, Any]] = [{"do": "give", "item": GOLD, "count": p.gold}] if p.gold else []
        reward += [{"do": "xp", "amount": p.xp}] if p.xp else []
        ops: list[dict[str, Any]] = [
            {"op": "vars", "vars": {id: 0}},
            {
                "op": "add_node",
                "node": {
                    "id": give,
                    "npc": brief.npc,
                    "description": p.give_description,
                    "condition": ["==", var, ["int", 0]],
                    "lines": _lines(p.give_lines, p.give_description),
                    "effects": [
                        {"do": "set", "var": id, "value": LOG_RUNNING},
                        {"do": "journal", "topic": title, "text": p.journal_given, "status": "LOG_RUNNING"},
                    ],
                    # the thing is in the world from the moment he is asked for it
                    "program": [
                        {"do": "call", "call": ["call", "WLD_INSERTITEM", ["sym", item], ["str", spot.point]]}
                    ],
                },
            },
            {
                "op": "add_node",
                "node": {
                    "id": done,
                    "npc": brief.npc,
                    "description": p.done_description,
                    "condition": [
                        "&&",
                        ["==", var, ["int", LOG_RUNNING, "LOG_RUNNING"]],
                        [">=", ["call", "NPC_HASITEMS", ["inst", "OTHER"], ["sym", item]], ["int", 1]],
                    ],
                    "lines": _lines(p.done_lines, p.done_description),
                    "effects": [
                        {"do": "give", "item": item, "count": 1, "to": "self"},
                        *reward,
                        {"do": "set", "var": id, "value": LOG_SUCCESS},
                        {"do": "journal", "topic": title, "text": p.journal_done, "status": "LOG_SUCCESS"},
                    ],
                },
            },
        ]
        story = await self.director.with_symbols(story, names_in(ops))
        caps = Caps(xp=brief.xp, items={GOLD: brief.gold})
        errors = validate(ops, story, self.rules, caps=caps)
        if errors:
            return None, errors
        for op in ops:
            await self.patches.apply(op)
        q = Quest(
            id,
            brief.npc,
            brief.name,
            title,
            item,
            brief.items[item],
            spot.id,
            spot.where,
            p.gold,
            p.xp,
            give,
            done,
        )
        self.quests[q.id] = q
        self.chronicle.record({"fact": "authored", **asdict(q), "point": spot.point, "reason": p.reason})
        self.journal.add(
            "authored", npc=q.npc, name=q.name, quest=q.id, title=title, item=item, place=spot.id
        )
        if self.dialogue is not None:
            self.dialogue.remember(
                q.npc,
                q.name,
                f"У тебя пропала вещь: {q.item_name}. Она лежит {q.where}; ты знаешь это примерно.",
            )
        log.info("author: %s gives %s «%s»: %s %s", q.npc, q.id, title, item, spot.point)
        return q, []

    def _id(self, made: dict[str, int]) -> str:
        n = 1 + max(
            [0, *(int(k[len(f"{DIRECTOR_PREFIX}Q") :]) for k in [*made, *self.quests] if _numbered(k))]
        )
        return f"{DIRECTOR_PREFIX}Q{n}"

    def _stage(self, q: Quest, stage: str) -> None:
        q.stage = stage
        self.chronicle.record({"fact": f"authored_{stage}", "id": q.id, "npc": q.npc})
        self.journal.add(f"authored_{stage}", npc=q.npc, name=q.name, quest=q.id, title=q.title)
        if stage == "done" and self.dialogue is not None:
            self.dialogue.remember(
                q.npc, q.name, f"Герой нашёл и принёс твою пропавшую вещь — {q.item_name}."
            )

    # --- the save branch --------------------------------------------------------------------

    def _load(self) -> None:
        self.quests.clear()
        for f in self.chronicle.facts():
            match f.get("fact"):
                case "authored":
                    fields = {k: f[k] for k in Quest.__dataclass_fields__ if k in f}
                    q = Quest(**fields)
                    self.quests[q.id] = q
                case "authored_given" | "authored_done" if f.get("id") in self.quests:
                    self.quests[f["id"]].stage = f["fact"].removeprefix("authored_")

    # --- the conversation (TurnExtension) ------------------------------------------------------

    async def prepare(self, npc: str) -> None:
        pass

    def situation(self, npc: str) -> str | None:
        npc = npc.upper()
        lines = []
        for q in self.quests.values():
            if q.npc != npc:
                continue
            if q.stage == "given":
                lines.append(
                    f"Ты попросил героя найти твою пропавшую вещь — {q.item_name} («{q.title}»): она лежит "
                    f"{q.where}. Принесёт — заплатишь: {q.reward()}."
                )
            elif q.stage == "done":
                lines.append(
                    f"Герой нашёл и принёс твою пропавшую вещь — {q.item_name} («{q.title}»), ты заплатил."
                )
        return "\n".join(lines) or None

    all_tools: tuple[Tool, ...] = ()

    def tools(self, npc: str) -> list[Tool]:
        return []

    async def handle(
        self, dialogue: DialogueManager, conv: Conversation, call: ToolCall, snap: Snapshot | None
    ) -> ToolOutcome | None:
        return None


def _numbered(key: str) -> bool:
    return key.startswith(f"{DIRECTOR_PREFIX}Q") and key[len(f"{DIRECTOR_PREFIX}Q") :].isdigit()


def _lines(lines: list[dict[str, str]], description: str) -> list[dict[str, str]]:
    """The talk as the bridge plays it; the hero opens it with what he picked in the menu, as in the scripts,
    unless the model has him open it with words of his own."""
    out = [
        {"speaker": "other" if x.get("speaker") == "hero" else "self", "text": str(x.get("text", ""))}
        for x in lines
    ]
    if not out or out[0]["speaker"] != "other":
        out.insert(0, {"speaker": "other", "text": description})
    return out
