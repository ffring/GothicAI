"""The director's decision: occasion -> budget -> proposal -> canon -> builders -> chronicle.

Only the queue and an in-flight decision live in memory. Lines, partial casting and both clocks belong
to the save branch, so loading restores the same budget without replaying a builder.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from .authoring import Author
from .casting import Casting
from .chains import ChainAuthor
from .chronicle import Fact
from .game import GameError
from .llm import LLMBackend, LLMError, Message, Tool, ToolCall, open_ahead
from .occasions import DEEDS, NEAREST, Occasions
from .protocol import GameMessage, Loading, Session

log = logging.getLogger(__name__)

PROPOSE = "propose_line"
CACHE_KEY = "showrunner"
ATTEMPTS = 2
MAX_OPEN_LINES = 2
MAX_LINES_PER_CHAPTER = 4
MAX_PEOPLE_PER_CHAPTER = 8
MIN_GAME_MINUTES = 24 * 60
MIN_REAL_SECONDS = 15 * 60
NEAR_CM = 5000
MAX_PLACES = 6
RECENT_DEEDS = 10
CLOSED = {"done", "failed", "obsolete", "closed"}
OUTCOMES = {"line", "line_none", "line_skipped", "line_failed", "line_rejected"}

INSTRUCTIONS = """\
Ты — режиссёр-автор Gothic II. По поводу из сводки реши, нужна ли сейчас новая линия сюжета.
Пиши в духе оригинальной игры: простые человеческие беды, грубоватые люди, без пафоса и современных слов.
Не повторяй темы и людей прежних линий. Учитывай текущие квесты, главу и остаток бюджета.
Ответ — один вызов propose_line:
- kind: quest — небольшое поручение; chain — цепочка из 2–3 квестов; stranger — новый человек и его
  поручение; none — сейчас ничего не нужно.
- giver: id живого несюжетного заказчика из кандидатов для quest/chain, иначе пустая строка.
- place: id места из сводки для stranger, иначе пустая строка.
- new_people: для chain нужны новые люди (включая бандитов); false — только существующие.
- premise: 2–3 фразы, что случилось и почему именно сейчас. Это замысел для строителя квестов.
- reason: почему такая линия или почему сейчас не нужна.
Людей и места выбирай только из сводки. При нехватке бюджета на людей выбери quest, chain без новых
людей или none. Отказ кода исправь, не повторяй его."""


class Proposal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: str
    giver: str = ""
    place: str = ""
    new_people: bool = False
    premise: str = ""
    reason: str = ""

    @classmethod
    def of(cls, raw: dict[str, Any]) -> Proposal:
        return cls.model_validate(raw)


PROPOSE_TOOL = Tool(
    PROPOSE,
    "Решить, нужна ли новая линия, выбрать её вид, заказчика и замысел.",
    {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["quest", "chain", "stranger", "none"]},
            "giver": {"type": "string"},
            "place": {"type": "string"},
            "new_people": {"type": "boolean"},
            "premise": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": ["kind", "giver", "place", "new_people", "premise", "reason"],
        "additionalProperties": False,
    },
)


def minutes(at: Fact) -> int:
    return int(at["day"]) * 1440 + int(at["hour"]) * 60 + int(at["minute"])


@dataclass
class Budget:
    open_left: int
    lines_left: int
    people_left: int
    busy_givers: set[str]
    game_wait: int
    real_wait: float
    reasons: list[str]

    def text(self) -> str:
        return (
            f"Остаток бюджета: открытых линий {self.open_left}, линий за главу {self.lines_left}, "
            f"новых людей за главу {self.people_left} (каждый бандит отдельно). "
            f"Ждать: {self.game_wait} игровых минут, {self.real_wait:.0f} секунд по часам."
        )


@dataclass
class Brief:
    occasion: Fact
    chapter: int
    day: int
    hour: int
    minute: int
    places: dict[str, str]
    residents: list[str]
    candidates: dict[str, str]
    scripted: list[str]
    quests: list[str]
    deeds: list[Fact]
    history: list[str]
    budget: Budget

    def text(self) -> str:
        return "\n".join(
            [
                "Повод (вид, место, время; deed — поступок героя): "
                + json.dumps(self.occasion, ensure_ascii=False),
                f"Глава {self.chapter}, день {self.day}, {self.hour:02}:{self.minute:02}.",
                "Место повода и места рядом (id — название и вид):",
                *[f"- {p} — {s}" for p, s in self.places.items()],
                "Кто здесь живёт (карточки написаны самим людям):",
                *[f"- {s}" for s in self.residents],
                "Кандидаты в заказчики (id — имя и характер):",
                *[f"- {n} — {s}" for n, s in self.candidates.items()],
                "Открытые квесты скриптов (темы журнала):",
                *[f"- {s}" for s in self.scripted],
                "Открытые квесты режиссёра (тема, состояние, стадия):",
                *[f"- {s}" for s in self.quests],
                "Последние поступки героя: " + json.dumps(self.deeds, ensure_ascii=False),
                "Что режиссёр уже сделал на ветке, не повторяться:",
                *[f"- {s}" for s in self.history],
                self.budget.text(),
            ]
        )


class ShowrunnerModel:
    def __init__(self, llm: LLMBackend) -> None:
        self.llm = llm

    async def propose(
        self, brief: Brief, previous: dict[str, Any] | None = None, errors: list[str] | None = None
    ) -> dict[str, Any] | None:
        messages = [Message("user", brief.text())]
        if previous is not None:
            messages.append(
                Message(
                    "user",
                    "Твоё предложение: "
                    + json.dumps(previous, ensure_ascii=False)
                    + "\nОтклонено:\n"
                    + "\n".join(f"- {e}" for e in errors or [])
                    + "\nИсправь его.",
                )
            )
        calls = []
        try:
            async for ev in self.llm.stream(
                instructions=INSTRUCTIONS, messages=messages, tools=[PROPOSE_TOOL], cache_key=CACHE_KEY
            ):
                if isinstance(ev, ToolCall) and ev.name == PROPOSE:
                    calls.append(ev)
        except LLMError as error:
            log.warning("showrunner: model failed: %s", error)
            return None
        return calls[0].arguments if len(calls) == 1 else {}


class Showrunner:
    def __init__(
        self,
        occasions: Occasions,
        author: Author,
        chains: ChainAuthor,
        casting: Casting,
        llm: LLMBackend,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.occasions, self.author, self.chains, self.casting = occasions, author, chains, casting
        self.link, self.chronicle, self.places = occasions.link, occasions.chronicle, occasions.places
        self.book, self.rules = author.book, author.rules
        self.model, self.clock = ShowrunnerModel(llm), clock
        self._lock = asyncio.Lock()
        self._generation = 0
        self._work: asyncio.Task[Fact | None] | None = None
        self.link.listeners.append(self._on_message)

    def _on_message(self, message: GameMessage) -> None:
        if isinstance(message, (Session, Loading)):
            self._generation += 1
            if self._work is not None:
                self._work.cancel()

    @property
    def lines(self) -> list[Fact]:
        return self.chronicle.facts("line")

    def is_open(self, line: Fact) -> bool:
        return any(self.book.progress(q).state not in CLOSED for q in line.get("quests", []))

    def budget(self, chapter: int, at: Fact) -> Budget:
        # A failed builder may already have cast people or booked quests. Count those receipts too.
        attempts = [f for f in self.chronicle.facts() if f.get("fact") in {"line", "line_failed"}]
        lines = [f for f in attempts if f.get("fact") == "line" or f.get("quests")]
        opened = [f for f in lines if self.is_open(f)]
        chapter_lines = [f for f in lines if f.get("chapter") == chapter]
        people = {n for f in attempts if f.get("chapter") == chapter for n in f.get("characters", [])}
        last = lines[-1] if lines else None
        game_wait = max(0, MIN_GAME_MINUTES - (minutes(at) - minutes(last))) if last else 0
        real_wait = max(0, MIN_REAL_SECONDS - (self.clock() - last["wall_time"])) if last else 0
        reasons = []
        if len(opened) >= MAX_OPEN_LINES:
            reasons.append(f"открытых линий уже {len(opened)}, предел {MAX_OPEN_LINES}")
        if len(chapter_lines) >= MAX_LINES_PER_CHAPTER:
            reasons.append(f"линий за главу уже {len(chapter_lines)}, предел {MAX_LINES_PER_CHAPTER}")
        if game_wait:
            reasons.append(f"рано: между линиями нужны игровые сутки, ещё {game_wait} минут")
        if real_wait:
            reasons.append(f"рано: между линиями нужны 15 минут по часам, ещё {real_wait:.0f} секунд")
        return Budget(
            max(0, MAX_OPEN_LINES - len(opened)),
            max(0, MAX_LINES_PER_CHAPTER - len(chapter_lines)),
            max(0, MAX_PEOPLE_PER_CHAPTER - len(people)),
            {f["giver"] for f in opened},
            game_wait,
            real_wait,
            reasons,
        )

    async def _state(self) -> Fact:
        return await self.link.story_state(vars=["KAPITEL", *self.author.graph.quests])

    async def brief(self, occasion: Fact, budget: Budget | None = None) -> Brief:
        state = await self._state()
        chapter = int(state.get("vars", {}).get("KAPITEL") or 1)
        return await self._brief(occasion, budget or self.budget(chapter, state), state)

    async def _brief(self, occasion: Fact, budget: Budget, state: Fact) -> Brief:
        place = self.places.get(occasion.get("place") or "")
        nearby = {place.id: place} if place is not None else {}
        if place is not None and place.center is not None:
            nearby.update({p.id: p for p, _ in self.places.near(place.center, NEAR_CM, MAX_PLACES)})
        world = {p["npc"]: p for p in await self.link.story_people()}
        # Own characters are residents too, including a newly introduced person before the next poll.
        own = self.casting.cast.characters()
        for spec in own:
            world.setdefault(
                spec.npc,
                {
                    "npc": spec.npc,
                    "name": spec.name,
                    "routine": [e.model_dump() for e in spec.routine] or [{"waypoint": spec.at}],
                },
            )
        residents = {
            n
            for n, person in world.items()
            if any(
                (p := self.places.of(e.get("waypoint"))) is not None and p.id in nearby
                for e in person.get("routine", [])
            )
        }
        names = sorted(residents | set(occasion.get("people", [])) | {s.npc for s in own})
        context = next(iter(world), "PC_HERO")
        [hero] = await self.link.story_eval(context, [["inst", "HERO"]])
        live = (await self.link.story_state(npcs=names, near=hero)).get("npcs", {})
        own_cards = self.casting.cast.cards()

        def description(npc: str) -> str:
            name = live.get(npc, {}).get("name") or world.get(npc, {}).get("name") or npc
            card = self.author.cards.get(npc) or own_cards.get(npc)
            return str(name) + (f"; {card.ru}" if card else "")

        candidates = {
            n: description(n)
            for n in names
            if live.get(n, {}).get("state") == "alive"
            and live[n].get("distance", float("inf")) <= NEAR_CM
            and not self.rules.plot_npc(n)
            and n not in budget.busy_givers
        }
        quests = []
        for q in self.book.all().values():
            progress = self.book.progress(q.id)
            if progress.state not in CLOSED:
                quests.append(f"{q.id}: «{q.title}», {progress.state}, стадия {progress.stage}")
        scripted = [
            title
            for var, q in self.author.graph.quests.items()
            if state.get("vars", {}).get(var) == 1
            for title in q.topics.values()
        ]
        facts = self.chronicle.facts()
        deeds = [
            f
            for f in facts
            if f.get("fact") in DEEDS or (f.get("fact") == "died" and f.get("killer") == "PLAYER")
        ][-RECENT_DEEDS:]
        history = []
        line_quests = set()
        for f in facts:
            if f.get("fact") in {"line", "line_failed"}:
                line_quests.update(f.get("quests", []))
                titles = [self.book.all()[q].title for q in f.get("quests", []) if q in self.book.all()]
                history.append(
                    f"{f.get('kind')}, заказчик {f.get('giver')}: {', '.join(titles)}; "
                    f"замысел {f.get('premise', '')}; персонажи {', '.join(f.get('characters', []))}"
                )
        history += [
            f"Квест {q.id}: «{q.title}», заказчик {q.giver}"
            for q in self.book.all().values()
            if q.id not in line_quests
        ]
        history += [f"Прежнее поручение «{q.title}», заказчик {q.npc}" for q in self.author.old.values()]
        history += [f"Персонаж {s.npc}: {s.name}" for s in self.casting.cast.characters()]
        return Brief(
            occasion,
            int(state.get("vars", {}).get("KAPITEL") or 1),
            state["day"],
            state["hour"],
            state["minute"],
            {p.id: f"{p.ru}; {p.kind} ({p.kind_ru})" for p in nearby.values()},
            [f"{n} — {description(n)}" for n in sorted(residents)],
            candidates,
            list(dict.fromkeys(scripted)),
            quests,
            deeds,
            history,
            budget,
        )

    def canon(self, p: Proposal, brief: Brief) -> list[str]:
        errors = []
        if p.kind not in {"quest", "chain", "stranger", "none"}:
            errors.append(f"неизвестный вид линии {p.kind!r}")
        if p.kind in {"quest", "chain"}:
            if p.giver not in brief.candidates:
                errors.append(f"заказчика {p.giver!r} нет в кандидатах сводки")
            if self.rules.plot_npc(p.giver):
                errors.append(f"{p.giver} — сюжетный NPC, его не трогаем")
        if p.kind == "stranger" and p.place not in brief.places:
            errors.append(f"места {p.place!r} нет в сводке")
        if p.kind != "none":
            errors += brief.budget.reasons
            if not p.premise.strip():
                errors.append("нужен замысел режиссёра: premise")
            needs_people = p.kind == "stranger" or (p.kind == "chain" and p.new_people)
            if needs_people and not brief.budget.people_left:
                errors.append("исчерпан бюджет новых людей за главу")
        return errors

    async def run(self) -> None:
        while True:
            await self.decide(await self.occasions.next())

    async def pitch(self) -> Fact | None:
        """An occasion now at the hero's actual place, independently of the queue."""
        generation = self._generation
        state = await self._state()
        people = await self.link.story_people()
        context = people[0]["npc"] if people else "PC_HERO"
        waypoint, hero = await self.link.story_eval(context, [NEAREST, ["inst", "HERO"]])
        live = await self.link.story_state(npcs=[p["npc"] for p in people], near=hero)
        if generation != self._generation:
            return None
        place = self.places.of(waypoint)
        occasion = {
            "fact": "occasion",
            "kind": "now",
            "place": place.id if place else None,
            "waypoint": waypoint,
            "wall_time": self.clock(),
            "people": [
                n
                for n, s in live.get("npcs", {}).items()
                if s.get("state") == "alive" and s.get("distance", float("inf")) <= NEAR_CM
            ],
            **{k: state[k] for k in ("day", "hour", "minute")},
        }
        self.chronicle.record(occasion)
        return await self.decide(occasion)

    async def decide(self, occasion: Fact) -> Fact | None:
        generation = self._generation
        async with self._lock:
            if generation != self._generation:
                return None
            self._work = asyncio.create_task(self._decide(occasion))
            try:
                return await self._work
            except asyncio.CancelledError:
                if generation == self._generation:
                    raise
                return None  # load revoked this decision, including its late model response
            finally:
                self._work = None

    def _record(self, fact_kind: str, occasion: Fact, **details: Any) -> Fact:
        fact = {"fact": fact_kind, "occasion": occasion, **details}
        self.chronicle.record(fact)
        return fact

    async def _decide(self, occasion: Fact) -> Fact | None:
        # A failed or refused occasion is used, too. Explicitly re-submitting it never replays a write.
        used = next(
            (
                f
                for f in reversed(self.chronicle.facts())
                if f.get("fact") in OUTCOMES and f.get("occasion") == occasion
            ),
            None,
        )
        if used is not None:
            return used
        try:
            state = await self._state()
            budget = self.budget(int(state.get("vars", {}).get("KAPITEL") or 1), state)
            if budget.reasons:
                return self._record("line_skipped", occasion, why=budget.reasons)
            brief = await self._brief(occasion, budget, state)
            if not brief.candidates and not budget.people_left:
                return self._record(
                    "line_skipped",
                    occasion,
                    why=[
                        "нет свободных живых заказчиков: у заказчика одна открытая линия; "
                        "бюджет новых людей за главу исчерпан"
                    ],
                )
        except (GameError, TimeoutError) as error:
            return self._record("line_failed", occasion, why=[f"не удалось прочитать мир: {error}"])
        open_ahead(self.model.llm, CACHE_KEY, keep=True)
        previous = None
        errors: list[str] = []
        for attempt in range(1, ATTEMPTS + 1):
            raw = await self.model.propose(brief, previous, errors)
            if raw is None:
                return self._record("line_failed", occasion, why=["модель не ответила"])
            try:
                proposal = Proposal.of(raw)
                errors = self.canon(proposal, brief)
            except ValidationError as error:
                proposal = None
                errors = [f"структура предложения: {e['loc']}: {e['msg']}" for e in error.errors()]
            if errors:
                rejected = self._record("line_rejected", occasion, proposal=raw, attempt=attempt, why=errors)
                previous = raw
                continue
            assert proposal is not None
            if proposal.kind == "none":
                return self._record("line_none", occasion, reason=proposal.reason)
            return await self._build(proposal, brief)
        return rejected

    async def _build(self, p: Proposal, brief: Brief) -> Fact:
        before_people = {s.npc for s in self.casting.cast.characters()}
        before_quests = set(self.book.all())
        giver = p.giver
        made = None
        errors = []
        try:
            if p.kind == "quest":
                made = await self.author.quest(giver, premise=p.premise)
            elif p.kind == "chain":
                made = await self.chains.chain(
                    giver,
                    new_people=p.new_people,
                    premise=p.premise,
                    max_people=brief.budget.people_left if p.new_people else 0,
                )
            elif p.kind == "stranger":
                person = await self.casting.character(p.place, about=p.premise, premise=p.premise)
                if person is not None:
                    giver = person.npc
                    made = await self.author.quest(giver, premise=p.premise)
            if not made:
                errors.append("строитель не справился")
        except (GameError, TimeoutError) as error:
            errors.append(f"строитель не справился: {error}")
        quests = list(self.book.all().keys() - before_quests)
        # Keep the builder's receipts, including all quests of a completed chain.
        if made:
            quests = [q.id for q in made] if isinstance(made, list) else [made.id]
        characters = sorted({s.npc for s in self.casting.cast.characters()} - before_people)
        at = {k: getattr(brief, k) for k in ("day", "hour", "minute")}
        try:
            current = await self.link.story_state()
            at = {k: current[k] for k in at}
        except (GameError, TimeoutError):
            pass
        return self._record(
            "line_failed" if errors else "line",
            brief.occasion,
            kind=p.kind,
            giver=giver,
            premise=p.premise,
            reason=p.reason,
            quests=sorted(quests),
            characters=characters,
            chapter=brief.chapter,
            **at,
            wall_time=self.clock(),
            **({"why": errors} if errors else {}),
        )
