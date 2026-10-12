"""Free-form conversation with an npc: player text in, npc lines (sentence by sentence) and actions out.

Whom the hero speaks to and when a conversation starts or ends is decided in talk.py (M5e: no chat
window); here are the turns of the conversational model, the npc's memory and its speech.
"""

from __future__ import annotations

import asyncio
import base64
import itertools
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Collection, Coroutine
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from . import executor, npc_tools
from .budget import Budget
from .executor import Intent
from .game import GameError, GameLink
from .journal import Journal
from .llm import (
    Done,
    Item,
    LLMBackend,
    LLMError,
    Message,
    Role,
    TextDelta,
    Tool,
    ToolCall,
    ToolChoice,
    ToolResult,
)
from .memory import Entry, MemoryStore, NpcMemory
from .persona import MOOD_DELIVERY, Persona, PersonaProvider
from .protocol import (
    MOODS,
    ChatStatus,
    ChatStatusCmd,
    ChatTextCmd,
    GameMessage,
    HushCmd,
    LineStarted,
    MemoryCmd,
    Mood,
    SayAudioCmd,
    SayCmd,
    SayEndCmd,
    Session,
    Snapshot,
)
from .trace import CallTrace, StepTrace, TraceWriter, TurnTrace
from .tts import TtsClient, TtsError
from .turn import MAX_STEPS, ToolOutcome, go_on, tool_choice

log = logging.getLogger(__name__)

CHAT_TEXT_EVERY = 0.06  # seconds between chat_text updates while a reply streams in
TTS_PAUSE = 30.0  # after a failure, lines go out silent for a while instead of each waiting to fail
PREPARE_S = 3.0  # what an extension asks the game before a turn
MAX_HISTORY = 40  # messages in the prompt; older ones are folded into a summary
KEEP_RECENT = 16  # messages left verbatim after folding

SUMMARY_INSTRUCTIONS = (
    "Ты ведёшь память персонажа {name} из игры Gothic 2. Тебе дают прежнюю сводку и новые записи "
    'его разговоров с героем. Напиши обновлённую сводку от второго лица ("ты" — это {name}), '
    "не длиннее 1200 символов: кто такой герой и что он о себе рассказал, о чём просил, что ты обещал "
    "и что отдал, как ты к нему относишься, важные события и когда они были. Только факты из записей, "
    "ничего не выдумывай. Выведи только текст сводки."
)


# --- text shaping for the game ------------------------------------------------------

_MARKDOWN = re.compile(r"[*_#`~]+")
_SPACES = re.compile(r"\s+")


def to_game_text(text: str) -> str:
    """Plain spoken text the game's 8-bit fonts can show (cp1251): no markdown, no emoji."""
    text = _MARKDOWN.sub("", text)
    out = []
    for ch in text:
        try:
            ch.encode("cp1251")
            out.append(ch)
        except UnicodeEncodeError:
            out.append(" ")
    return _SPACES.sub(" ", "".join(out)).strip()


def line_duration(text: str) -> float:
    """Seconds the game gives a line (mirrors LineLibrary::durationFor in ai_bridge)."""
    return min(max(0.8 + 0.065 * len(text.encode("cp1251", "replace")), 1.5), 12.0)


_SENTENCE_END = re.compile(r"(?<=[.!?…])[\"»)]*\s+")
_CLAUSE_END = re.compile(r"(?<=[,;:—–])\s+")
_WORD = re.compile(r"\S+\s+")
FIRST_CLAUSE_MIN = 12  # a first line cut at a comma is at least this long
FIRST_WORDS_MAX = 10  # ...or cut after this many words when there is no punctuation
# a cut never leaves one of these at the end of the first line ("вместе с" | "армией")
_FUNCTION_WORDS = frozenset(
    "а и но да или что чтобы как когда если не же ли бы "
    "в во на с со к ко по о об от до из за для у при без над под".split()
)


class SentenceSplitter:
    """Cuts streamed text into subtitle lines: whole sentences, merged if short, split if too long.
    `first_min_len` lets the first line go out sooner (with TTS it is voiced before it is heard);
    `first_clause` goes further: the first line ends at the first clause (a comma) or after a few
    words, whichever comes first."""

    def __init__(
        self,
        min_len: int = 24,
        max_len: int = 200,
        first_min_len: int | None = None,
        first_clause: bool = False,
    ) -> None:
        self.min_len = min_len
        self.max_len = max_len
        self.first_min_len = 1 if first_clause else min_len if first_min_len is None else first_min_len
        self.first_clause = first_clause
        self._emitted = False
        self._buf = ""

    def _first_cut(self) -> int | None:
        cuts = []
        if m := _SENTENCE_END.search(self._buf):
            cuts.append(m.end())
        for m in _CLAUSE_END.finditer(self._buf):
            if len(self._buf[: m.end()].strip()) >= FIRST_CLAUSE_MIN:
                cuts.append(m.end())
                break
        words = list(_WORD.finditer(self._buf))
        n = FIRST_WORDS_MAX
        while n <= len(words) and words[n - 1].group().strip().lower() in _FUNCTION_WORDS:
            n += 1
        if n <= len(words):
            cuts.append(words[n - 1].end())
        return min(cuts) if cuts else None

    def feed(self, text: str) -> list[str]:
        self._buf += text
        out: list[str] = []
        if self.first_clause and not self._emitted and (cut := self._first_cut()) is not None:
            out.append(self._buf[:cut].strip())
            self._buf = self._buf[cut:]
            self._emitted = True
        start = 0
        pending = ""
        for m in _SENTENCE_END.finditer(self._buf):
            pending += self._buf[start : m.end()]
            start = m.end()
            if len(pending.strip()) >= (self.min_len if self._emitted or out else self.first_min_len):
                out.extend(self._limit(pending))
                pending = ""
        self._buf = pending + self._buf[start:]
        self._emitted = self._emitted or bool(out)
        return out

    def flush(self) -> list[str]:
        rest, self._buf = self._buf, ""
        return self._limit(rest) if rest.strip() else []

    def _limit(self, sentence: str) -> list[str]:
        sentence = sentence.strip()
        parts = []
        while len(sentence) > self.max_len:
            cut = sentence.rfind(", ", 0, self.max_len)
            cut = cut + 1 if cut > self.min_len else sentence.rfind(" ", 0, self.max_len)
            if cut <= 0:
                cut = self.max_len
            parts.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if sentence:
            parts.append(sentence)
        return parts


# The model starts a reply (or a sentence where the mood changes) with a tag: "[angry] Опять ты?". Russian
# words and near synonyms are taken too: the model sometimes writes them.
MOOD_TAG_MAX = 24  # longer text in square brackets is not a tag
_MOOD_ALIASES: dict[str, Mood] = {
    "calm": "neutral",
    "спокойно": "neutral",
    "спокойствие": "neutral",
    "нейтрально": "neutral",
    "happy": "friendly",
    "smile": "friendly",
    "дружелюбно": "friendly",
    "приветливо": "friendly",
    "радость": "friendly",
    "улыбка": "friendly",
    "anger": "angry",
    "злость": "angry",
    "гнев": "angry",
    "сердито": "angry",
    "раздражение": "angry",
    "враждебно": "hostile",
    "угроза": "hostile",
    "презрение": "hostile",
    "fear": "afraid",
    "scared": "afraid",
    "страх": "afraid",
    "испуг": "afraid",
    "испуганно": "afraid",
    "hurt": "pain",
    "боль": "pain",
}


def mood_of(tag: str) -> Mood | None:
    t = tag.strip().lower()
    for mood in MOODS:
        if t == mood:
            return mood
    return _MOOD_ALIASES.get(t)


@dataclass(frozen=True)
class MoodTag:
    mood: Mood


class MoodTags:
    """Takes mood tags out of the model's streamed text: the text goes on to the chat and the voice, a tag
    becomes the mood of the lines after it. Other short text in square brackets is a stage direction and
    is not said either. A tag split between two pieces of the stream waits for its end."""

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, text: str) -> list[str | MoodTag]:
        self._buf += text
        out: list[str | MoodTag] = []
        while self._buf:
            i = self._buf.find("[")
            if i < 0:
                out.append(self._buf)
                self._buf = ""
                break
            if i > 0:
                out.append(self._buf[:i])
                self._buf = self._buf[i:]
            j = self._buf.find("]")
            if j < 0:
                if len(self._buf) <= MOOD_TAG_MAX + 1:
                    break  # the rest of the tag has not come yet
                out.append("[")  # too long for a tag: plain text
                self._buf = self._buf[1:]
                continue
            tag, self._buf = self._buf[1:j], self._buf[j + 1 :]
            if len(tag) > MOOD_TAG_MAX:
                out.append(f"[{tag}]")
            elif (mood := mood_of(tag)) is not None:
                out.append(MoodTag(mood))
        return out

    def flush(self) -> list[str | MoodTag]:
        rest, self._buf = self._buf, ""
        return [rest] if rest else []


# --- conversation -------------------------------------------------------------------


def game_time(snap: Snapshot | None) -> str | None:
    if snap is None:
        return None
    w = snap.world
    return f"день {w.day + 1}, {w.hour:02d}:{w.minute:02d}"


@dataclass
class Conversation:
    npc: str
    persona: Persona
    branch: str  # memories of this conversation belong to this line of play
    memory: NpcMemory = field(default_factory=NpcMemory)
    reply: asyncio.Task[None] | None = None
    last_lines: list[str] = field(default_factory=list)  # line ids of the latest reply
    actions: set[asyncio.Task[None]] = field(default_factory=set)
    new_meeting: bool = True  # the next reply starts a new meeting (noted in memory)
    compacting: asyncio.Task[None] | None = None
    # lines waiting for their audio, sent to the game strictly in order (with tts)
    voice: asyncio.Queue[tuple[str, str, str, bool]] | None = None  # (line, text, reply, overlay)
    voicing: asyncio.Task[None] | None = None
    intent: Intent = field(default_factory=Intent)  # what the npc wants now (set by the model)
    pending: list[str] = field(default_factory=list)  # events that came while a turn was running
    calm: Intent | None = None  # the intent before an incident goal replaced it
    overlay_free_at: float = 0.0  # lines said on the move: when the last one is over
    reply_id: str = ""  # the latest turn's reply (its subtitle entry)
    hushed: set[str] = field(default_factory=set)  # lines cut off by the hero: never sent
    cut: str | None = None  # the hero interrupted the running turn: what the npc had said of it

    @property
    def history(self) -> list[Message]:
        msgs = []
        if self.memory.summary:
            msgs.append(
                Message("developer", f"[Что ты помнишь о прежних встречах с героем: {self.memory.summary}]")
            )
        msgs.extend(e.message() for e in self.memory.entries)
        return msgs

    def tasks(self) -> list[asyncio.Task[None]]:
        return [t for t in (self.reply, self.compacting, self.voicing, *self.actions) if t is not None]


class TurnExtension(Protocol):
    """More of the world for the conversational model (the director's quests, quests.py; the scripts'
    quests, vanilla.py): lines for the situation, tools to offer, and the handling of their calls."""

    async def prepare(self, npc: str) -> None:
        """Before a turn: what the situation and the tools need from the game."""
        ...

    def situation(self, npc: str) -> str | None: ...

    def tools(self, npc: str) -> list[Tool]: ...

    async def handle(
        self, dialogue: DialogueManager, conv: Conversation, call: ToolCall, snap: Snapshot | None
    ) -> ToolOutcome | None:
        """The outcome of a call this extension knows, None for other calls."""
        ...


class DialogueManager:
    """Turns of the npc's conversational model. One reply in flight per npc; new player text interrupts it.

    Every message is stored in `memory` under the game's current branch (see memory.py), so npc's
    remember earlier conversations of the same save game.
    """

    def __init__(
        self,
        link: GameLink,
        llm: LLMBackend,
        personas: PersonaProvider,
        *,
        memory: MemoryStore | None = None,
        tts: TtsClient | None = None,
        tools: bool = True,
        journal: Journal | None = None,
        trace: TraceWriter | None = None,
        budget: Budget | None = None,
    ) -> None:
        self.link = link
        self.journal = journal or Journal()  # lines and actions for the launcher (see journal.py)
        self.trace = trace  # every turn as a JSON line (see trace.py)
        self.budget = budget  # calls nobody asked for (summaries); the player's turns are only counted
        # the npc's intent changed (e.g. for the planner: what it wants from the hero after the chat)
        self.on_intent: Callable[[str, Intent], None] | None = None
        # the hero said something to the npc (the body may react before the answer)
        self.on_player_phrase: Callable[[str, str], None] | None = None
        # something the npc's body was to do did not work (npc, what)
        self.on_body_failed: Callable[[str, str], None] | None = None
        # whether the npc is under our control (attached): only then do intents move its body directly
        self.controls: Callable[[str], bool] | None = None
        # the intent of an npc we do not control changed (npc, old, new): who runs its body carries it out
        self.on_free_intent: Callable[[str, Intent, Intent], None] | None = None
        # a free npc's body is needed for a commitment (give an item, follow): hold it (attach) for `why`,
        # and let it go again; without them only controlled npc's act on commitments (talk.py)
        self.take_body: Callable[[str, str], Awaitable[bool]] | None = None
        self.free_body: Callable[[str, str], None] | None = None
        # the npc ended the conversation (its goodbye has been said)
        self.on_end: Callable[[str], None] | None = None
        self.extensions: list[TurnExtension] = []
        self.llm = llm
        self.personas = personas
        self.memory = memory or MemoryStore(":memory:")
        self.tts = tts
        self._tts_paused_until = 0.0
        self.tools = npc_tools.TOOLS if tools else []
        self.branch: str | None = None
        self.conversations: dict[str, Conversation] = {}
        self._line_ids = itertools.count(1)
        self._reply_ids = itertools.count(1)
        self._lines: dict[str, tuple[str, float | None]] = {}  # line id -> (text, started at)
        self._background: set[asyncio.Task[Any]] = set()
        link.listeners.append(self._on_message)
        if link.session is not None:
            self._switch_branch(link.session)

    def _on_message(self, msg: GameMessage) -> None:
        match msg:
            case Session():
                self._switch_branch(msg)
            case LineStarted():
                if msg.line in self._lines:
                    self._lines[msg.line] = (self._lines[msg.line][0], time.monotonic())

    # --- memory -------------------------------------------------------------------------

    def _switch_branch(self, session: Session) -> None:
        if session.branch != self.branch:
            # another line of play (new game or a load): conversations are read again from memory
            for conv in self.conversations.values():
                for task in conv.tasks():
                    task.cancel()
            self.conversations.clear()
            self.branch = session.branch
        head = self.memory.ensure_branch(session.branch, session.parent, session.parent_seq)
        log.info(
            "memory: branch %s (%s%s), head %d",
            session.branch,
            session.reason,
            f" from {session.parent}@{session.parent_seq}" if session.parent else "",
            head,
        )
        self._send_head(session.branch, head)

    def _current_branch(self) -> str:
        if self.branch is None:
            self.branch = f"unsaved-{uuid.uuid4().hex[:12]}"
            self.memory.ensure_branch(self.branch)
            log.warning("the game did not report a session; memories go to %s", self.branch)
        return self.branch

    def _remember(self, conv: Conversation, role: Role, content: str) -> None:
        seq = self.memory.append(
            conv.branch, conv.npc, role, content, game_time(self.link.snapshots.get(conv.npc))
        )
        conv.memory.entries.append(Entry(seq, role, content))
        self._send_head(conv.branch, seq)

    def _send_head(self, branch: str, seq: int) -> None:
        self._spawn(self.link.send(MemoryCmd(branch=branch, seq=seq)))

    def _spawn(self, coro: Coroutine[Any, Any, Any]) -> None:
        async def run() -> None:
            try:
                await coro
            except GameError:
                pass  # the game is gone; it learns the head again on reconnect

        task = asyncio.create_task(run())
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    def _maybe_compact(self, conv: Conversation) -> None:
        if len(conv.memory.entries) <= MAX_HISTORY or (conv.compacting and not conv.compacting.done()):
            return
        if self.budget is not None and not self.budget.allow(conv.npc):
            return  # later: the next message tries again
        conv.compacting = asyncio.create_task(self._compact(conv))

    async def _compact(self, conv: Conversation) -> None:
        """Fold older messages into the summary (one extra LLM call per ~25 messages)."""
        old = conv.memory.entries[:-KEEP_RECENT]
        speaker = {"user": "Герой", "assistant": conv.persona.name, "developer": ""}
        records = "\n".join(f"{speaker[e.role]}: {e.content}" if speaker[e.role] else e.content for e in old)
        prompt = f"Прежняя сводка: {conv.memory.summary or 'нет'}\n\nНовые записи:\n{records}"
        text = ""
        try:
            async for event in self.llm.stream(
                instructions=SUMMARY_INSTRUCTIONS.format(name=conv.persona.name),
                messages=[Message("user", prompt)],
            ):
                if isinstance(event, TextDelta):
                    text += event.text
        except LLMError as err:
            log.warning("%s: summary failed, will retry later: %s", conv.npc, err)
            return
        text = text.strip()
        if not text:
            return
        covers = old[-1].seq
        seq = self.memory.add_summary(conv.branch, conv.npc, text, covers)
        conv.memory.summary, conv.memory.summary_seq = text, covers
        conv.memory.entries = [e for e in conv.memory.entries if e.seq > covers]
        log.info("%s: %d messages folded into the summary (%d chars)", conv.npc, len(old), len(text))
        self._send_head(conv.branch, seq)

    # --- conversation -------------------------------------------------------------------

    def open(self, npc: str, name: str) -> Conversation:
        npc = npc.upper()
        conv = self.conversations.get(npc)
        if conv is None:
            branch = self._current_branch()
            conv = Conversation(npc, self.personas.get(npc, name), branch, self.memory.load(branch, npc))
            if conv.memory.intent:
                conv.intent = Intent.from_json(conv.memory.intent)
            self.conversations[npc] = conv
            if conv.memory.entries or conv.memory.summary:
                log.info(
                    "%s remembers %d message(s)%s",
                    npc,
                    len(conv.memory.entries),
                    " and a summary" if conv.memory.summary else "",
                )
        return conv

    def say_as_player(
        self, npc: str, text: str, *, overlay: bool = False, body: str | None = None
    ) -> asyncio.Task[None]:
        """The hero says `text` to the npc (talk.py, console, scenarios). `overlay`: the npc answers on the
        move (it walks or works); `body`: what its body does meanwhile, for the situation."""
        conv = self.conversations.get(npc.upper())
        if conv is None:
            raise KeyError(f"no conversation with {npc}")
        # square brackets are remarks about what really happens: the hero's words can't pass for one
        text = text.replace("[", "(").replace("]", ")")
        return self._start_reply(conv, text, overlay=overlay, body=body)

    def _cancel(self, conv: Conversation) -> None:
        if conv.reply is not None and not conv.reply.done():
            conv.reply.cancel()
        conv.reply = None

    def _drop_unvoiced(self, conv: Conversation) -> None:
        if conv.voice is not None:  # lines of the interrupted reply that were not voiced yet
            while not conv.voice.empty():
                conv.voice.get_nowait()
                conv.voice.task_done()

    def _start_reply(
        self, conv: Conversation, user_text: str, *, overlay: bool = False, body: str | None = None
    ) -> asyncio.Task[None]:
        self._cancel(conv)
        self._drop_unvoiced(conv)
        if self.budget is not None:
            self.budget.note(conv.npc)
        conv.reply = asyncio.create_task(
            self._turn(conv, Message("user", user_text), "player", overlay=overlay, body=body)
        )
        if self.on_player_phrase is not None:
            self.on_player_phrase(conv.npc, user_text)
        return conv.reply

    def _said(self, conv: Conversation) -> tuple[list[str], bool, bool]:
        """The latest reply: the texts of its lines that started, whether one is being said now and
        whether some were not said yet."""
        now = time.monotonic()
        said, speaking, unsaid = [], False, False
        for line in conv.last_lines:
            text, at = self._lines.get(line, ("", None))
            if line in conv.hushed:
                continue
            if at is None:
                unsaid = True
                continue
            said.append(text)
            speaking = speaking or at + line_duration(text) > now
        return said, speaking, unsaid

    def speaking(self, npc: str) -> bool:
        """The npc is answering: thinking of it, saying it or about to."""
        conv = self.conversations.get(npc.upper())
        if conv is None:
            return False
        _, speaking, unsaid = self._said(conv)
        return speaking or unsaid or (conv.reply is not None and not conv.reply.done())

    def interrupt(self, npc: str) -> bool:
        """The hero speaks while the npc answers: it stops at once (its voice, the lines it did not say
        yet, the turn if it is still thinking) and remembers being interrupted. False if it was silent."""
        conv = self.conversations.get(npc.upper())
        if conv is None or not self.speaking(npc):
            return False
        said, _, _ = self._said(conv)
        spoken = " ".join(said)
        conv.hushed.update(line for line in conv.last_lines if self._lines.get(line, ("", None))[1] is None)
        running = conv.reply is not None and not conv.reply.done()
        if running:
            conv.cut = spoken  # the turn remembers only this (see _turn)
        self._cancel(conv)
        self._drop_unvoiced(conv)
        log.info("%s interrupted after %d line(s)", conv.npc, len(said))
        self._spawn(self.link.send(HushCmd(npc=conv.npc, reply=conv.reply_id or None, said=spoken or None)))
        if not running:
            self._note(conv, f"Герой перебил тебя{f'; ты успел сказать: «{spoken}»' if spoken else ''}")
        return True

    def remember(self, npc: str, name: str, text: str) -> None:
        """A remark in the memory of any npc, also one nobody talks to (a rumor, something it saw)."""
        self._note(self.open(npc, name), text)

    def change_intent(self, npc: str, name: str, **fields: Any) -> None:
        """Change what the npc wants or how it sees the hero, from outside a turn (a lie found out)."""
        conv = self.open(npc, name)
        self._change_intent(conv, replace(conv.intent, **fields), self.link.snapshots.get(conv.npc))

    def after_speech(self, conv: Conversation, job: Callable[[], Awaitable[tuple[str, bool]]]) -> None:
        """For extensions: a body job once the npc has said its lines; its note goes into the history."""
        self._after_speech(conv, job)

    async def give(self, conv: Conversation, call: ToolCall, snap: Snapshot | None) -> tuple[str, bool]:
        """For extensions: hand an item over (give_item's checks are the caller's)."""
        return await self._give(conv, call, snap)

    async def borrow_body(self, conv: Conversation, why: str) -> bool:
        """For extensions: the npc's body for a job of theirs (going somewhere); give it back with
        return_body."""
        return await self._hold(conv, why)

    def return_body(self, conv: Conversation, why: str) -> None:
        self._free(conv, why)

    def overheard(self, npc: str, name: str, text: str, to_name: str | None) -> None:
        """A witness: the npc heard the hero say something to someone else (no model call, only memory)."""
        conv = self.open(npc, name)
        whom = f" собеседнику ({to_name})" if to_name else " вслух"
        self._note(conv, f"Ты слышал, как герой сказал{whom}: «{text}»")

    def event_turn(
        self,
        npc: str,
        cue: str,
        relevant: Callable[[], bool] | None = None,
        *,
        overlay: bool = False,
        tools: Collection[str] | None = None,
    ) -> asyncio.Task[None] | None:
        """A turn about something that happened (an incident): the npc may speak and change its intent.
        While a turn is running the event waits for the next one's situation instead of interrupting.
        `tools` limits what the model is offered (an npc nobody controls cannot hand over items)."""
        conv = self.conversations.get(npc.upper())
        if conv is None:
            return None
        if conv.reply is not None and not conv.reply.done():
            conv.pending.append(cue)
            return None
        offered = self.tools if tools is None else [t for t in self.tools if t.name in tools]
        conv.reply = asyncio.create_task(
            self._turn(conv, Message("developer", cue), "incident", relevant, overlay=overlay, tools=offered)
        )
        return conv.reply

    def note(self, npc: str, text: str) -> None:
        """A remark in the npc's history (what happened to it)."""
        if conv := self.conversations.get(npc.upper()):
            self._note(conv, text)

    def has_spoken(self, npc: str) -> bool:
        """The npc's current turn has sent a line already."""
        conv = self.conversations.get(npc.upper())
        return conv is not None and bool(conv.last_lines)

    def body_busy(self, npc: str) -> bool:
        """A commitment is being carried out (a gesture now would cancel it)."""
        conv = self.conversations.get(npc.upper())
        return conv is not None and bool(conv.actions)

    async def _status(self, conv: Conversation, status: ChatStatus, detail: str | None = None) -> None:
        await self.link.send(ChatStatusCmd(npc=conv.npc, status=status, detail=detail))

    async def _say(
        self, conv: Conversation, text: str, reply: str, overlay: bool = False, mood: Mood | None = None
    ) -> None:
        text = to_game_text(text)
        if not text:
            return
        line = f"AIB_{next(self._line_ids)}"
        conv.last_lines.append(line)
        self._lines[line] = (text, None)
        log.info("%s: %s", conv.persona.name, text)
        if self.tts is None:
            if overlay:  # the game does not order lines said on the move
                await asyncio.sleep(max(0.0, conv.overlay_free_at - time.monotonic()))
                conv.overlay_free_at = time.monotonic() + line_duration(text)
            await self.link.send(
                SayCmd(npc=conv.npc, line=line, text=text, reply=reply, overlay=overlay or None, mood=mood)
            )
            return
        if conv.voice is None:
            conv.voice = asyncio.Queue()
            conv.voicing = asyncio.create_task(self._voice(conv, conv.voice))
        conv.voice.put_nowait((line, text, reply, overlay, mood))

    async def _voice(
        self, conv: Conversation, queue: asyncio.Queue[tuple[str, str, str, bool, Mood | None]]
    ) -> None:
        """Voices the lines one by one and streams them to the game: `say` goes out with the first
        audio, chunks follow as they are generated, `say_end` gives the length. A line that gets no
        audio still goes out, silent. Lines said on the move are not ordered by the game: the next one
        waits until the previous has been said."""
        assert self.tts is not None
        while True:
            line, text, reply, overlay, mood = await queue.get()
            started = False
            spoken_at = 0.0
            if overlay:
                await asyncio.sleep(max(0.0, conv.overlay_free_at - time.monotonic()))
            try:
                if line in conv.hushed:
                    continue  # the hero interrupted the npc before this line
                if time.monotonic() >= self._tts_paused_until:

                    async def start(
                        rate: int,
                        _provider: str,
                        line: str = line,
                        text: str = text,
                        reply: str = reply,
                        overlay: bool = overlay,
                        mood: Mood | None = mood,
                    ) -> None:
                        nonlocal started, spoken_at
                        if line in conv.hushed:
                            return  # interrupted while its voice was being made
                        started = True
                        spoken_at = time.monotonic()
                        await self.link.send(
                            SayCmd(
                                npc=conv.npc,
                                line=line,
                                text=text,
                                stream=True,
                                rate=rate,
                                reply=reply,
                                overlay=overlay or None,
                                mood=mood,
                            )
                        )

                    async def chunk(pcm: bytes, line: str = line) -> None:
                        if line in conv.hushed:
                            return
                        await self.link.send(
                            SayAudioCmd(npc=conv.npc, line=line, data=base64.b64encode(pcm).decode())
                        )

                    try:
                        speech = await self.tts.stream(
                            text,
                            conv.npc,
                            conv.persona.voice,
                            start,
                            chunk,
                            MOOD_DELIVERY[mood] if mood else "",
                        )
                        await self.link.send(
                            SayEndCmd(npc=conv.npc, line=line, audio_s=round(speech.audio_s, 3))
                        )
                        if overlay:
                            conv.overlay_free_at = spoken_at + speech.audio_s + 0.25
                        log.info(
                            "%s voice %s: first audio after %.2fs, %.2fs of audio in %.2fs (%s)",
                            conv.npc,
                            line,
                            speech.first_chunk_s,
                            speech.audio_s,
                            speech.latency_s,
                            speech.provider,
                        )
                    except TtsError as err:
                        self._tts_paused_until = time.monotonic() + TTS_PAUSE
                        log.warning("%s: no voice, subtitles only for %.0fs: %s", conv.npc, TTS_PAUSE, err)
                        self.journal.add(
                            "tts_off",
                            npc=conv.npc,
                            name=conv.persona.name,
                            seconds=TTS_PAUSE,
                            detail=str(err),
                        )
                        if started:
                            await self.link.send(SayEndCmd(npc=conv.npc, line=line, error=str(err)))
                if not started and line not in conv.hushed:
                    if overlay:
                        conv.overlay_free_at = time.monotonic() + line_duration(text)
                    await self.link.send(
                        SayCmd(
                            npc=conv.npc,
                            line=line,
                            text=text,
                            reply=reply,
                            overlay=overlay or None,
                            mood=mood,
                        )
                    )
            except GameError:
                pass
            finally:
                queue.task_done()

    def _commit(self, conv: Conversation, trigger: Message, text: str) -> None:
        # one synchronous step: an interrupted reply and its replacement cannot interleave here.
        # What started the turn is remembered even when the npc said nothing (it may have acted).
        self._remember(conv, trigger.role, trigger.content)
        if text:
            self._remember(conv, "assistant", text)
        self._maybe_compact(conv)

    def _note(self, conv: Conversation, note: str) -> None:
        if note:
            log.info("%s note: %s", conv.npc, note)
            self._remember(conv, "developer", f"[{note}]")

    def _after_speech(self, conv: Conversation, job: Callable[[], Awaitable[tuple[str, bool]]]) -> None:
        """Run a body job once the npc has said its queued lines; the note it returns goes into the
        history after the current turn. A failure is reported (`on_body_failed`)."""
        reply = conv.reply

        async def run() -> None:
            if conv.voice is not None:
                await conv.voice.join()  # the npc says what it said before acting on it
            note, ok = await job()
            if reply is not None and not reply.done() and reply is not asyncio.current_task():
                await asyncio.wait({reply})  # the note goes after this turn's reply in the history
            self._note(conv, note)
            if not ok and self.on_body_failed is not None:
                self.on_body_failed(conv.npc, note)

        task = asyncio.create_task(run())
        conv.actions.add(task)
        task.add_done_callback(conv.actions.discard)

    def _journal_call(self, conv: Conversation, name: str, arguments: dict[str, Any]) -> None:
        log.info("%s calls %s %s", conv.npc, name, arguments)
        self.journal.add("action", npc=conv.npc, name=conv.persona.name, action=name, arguments=arguments)

    async def _end_after_speech(self, conv: Conversation, lines: list[str]) -> None:
        """The conversation ends once the npc has said its goodbye."""
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            started = [self._lines.get(line, ("", None)) for line in lines]
            if all(at is not None for _, at in started):
                last_text, last_at = started[-1] if started else ("", time.monotonic())
                await asyncio.sleep(max(0.0, (last_at or 0) + line_duration(last_text) - time.monotonic()))
                break
            await asyncio.sleep(0.2)
        self._note(conv, "Ты закончил разговор")
        if self.on_end is not None:
            self.on_end(conv.npc)

    async def _turn(
        self,
        conv: Conversation,
        trigger: Message,
        kind: str,
        relevant: Callable[[], bool] | None = None,
        *,
        overlay: bool = False,
        tools: list[Tool] | None = None,
        body: str | None = None,
    ) -> None:
        """One turn of the conversational model: up to MAX_STEPS calls (see turn.py). Text streams to the
        subtitles and the voice as it comes; tool calls are handled after their step. `body`: what the
        npc's body does meanwhile (the behavioral model's choice)."""
        if conv.new_meeting:
            conv.new_meeting = False
            if conv.memory.entries or conv.memory.summary:
                when = game_time(self.link.snapshots.get(conv.npc))
                self._note(conv, f"Новая встреча с героем{f', {when}' if when else ''}")
        snap = self.link.snapshots.get(conv.npc)
        situation_text = npc_tools.render_situation(snap, conv.intent)
        for ext in self.extensions:
            try:
                await asyncio.wait_for(ext.prepare(conv.npc), PREPARE_S)
            except (GameError, TimeoutError) as err:
                log.warning("%s: %s not ready: %s", conv.npc, type(ext).__name__, err)
        for ext in self.extensions:
            if extra := ext.situation(conv.npc):
                situation_text += "\n" + extra
        if body:
            situation_text += f"\nТвоё тело сейчас: {body} — это уже происходит, не противоречь этому."
        if conv.pending:  # events that came during the last turn
            situation_text += "\nТолько что: " + "; ".join(c.strip("[]") for c in conv.pending) + "."
            conv.pending = []
        situation = Message("developer", situation_text)
        request: list[Item] = [*conv.history, situation, trigger]
        conv.last_lines = []
        state = TurnState(
            relevant=relevant,
            overlay=overlay,
            tools=self._offered(conv.npc) if tools is None else tools,
            reply=f"R{next(self._reply_ids)}",
            # voiced, the first line is synthesized before it is heard: cut it at the first clause
            splitter=SentenceSplitter(first_clause=self.tts is not None),
            started=time.monotonic(),
        )
        conv.reply_id = state.reply
        trace = TurnTrace(
            self.trace,
            npc=conv.npc,
            branch=conv.branch,
            trigger={"kind": kind, "text": trigger.content},
            instructions=conv.persona.instructions,
            tools=[t.name for t in state.tools],
            snapshot=snap.model_dump(mode="json") if snap is not None else None,
            llm=self.llm.name,
            model=getattr(self.llm, "model", None),
        )
        await self._status(conv, "thinking")
        status = "completed"
        added: list[Item] = list(request)
        try:
            for step in range(MAX_STEPS):
                st = trace.step(added, tool_choice(step))
                done = await self._step(conv, state, request, tool_choice(step), st)
                if not state.text.strip() and state.relevant is not None and not state.relevant():
                    raise StaleTurn  # nothing said yet and the incident is over: no late intents either
                calls = [i for i in done.items if isinstance(i, ToolCall)]
                outcomes = [await self._tool(conv, call, state) for call in calls]
                for call, outcome in zip(calls, outcomes, strict=True):
                    st.calls.append(
                        CallTrace(call.name, call.arguments, call.call_id, outcome.output, outcome.status)
                    )
                if not go_on(outcomes, bool(state.text.strip())):
                    break
                if step == MAX_STEPS - 1:
                    status = "step_cap"
                    break
                results = [ToolResult(c.call_id, o.output) for c, o in zip(calls, outcomes, strict=True)]
                added = [*done.items, *results]
                request += added
            if state.text:
                await self.link.send(
                    ChatTextCmd(npc=conv.npc, reply=state.reply, text=to_game_text(state.text), final=True)
                )
        except LLMError as err:
            log.error("%s: %s", conv.npc, err)
            self.journal.add("llm_error", npc=conv.npc, name=conv.persona.name, detail=str(err))
            if state.text:  # a turn that failed before a word was said is not remembered
                self._commit(conv, trigger, state.text)
            trace.close("llm_error")
            await self._status(conv, "error", str(err))
            return
        except StaleTurn:
            # what the turn was about is over: words now would be out of place
            self._commit(conv, trigger, state.text)
            trace.close("stale")
            await self._status(conv, "idle")
            return
        except asyncio.CancelledError:
            if conv.cut is not None:  # the hero interrupted: only what was said counts
                said, conv.cut = conv.cut, None
                self._commit(conv, trigger, said)
                self._note(conv, "Герой перебил тебя" + ("" if said else ", ты не успел ничего сказать"))
                trace.close("interrupted")
                raise
            self._commit(conv, trigger, state.text)  # the npc remembers what it already said
            trace.close("cancelled")
            raise

        self._commit(conv, trigger, state.text)
        trace.close(status)
        total = time.monotonic() - state.started
        if state.text:
            self.journal.add(
                "npc",
                npc=conv.npc,
                name=conv.persona.name,
                text=to_game_text(state.text),
                llm=self.llm.name,
                first_token_s=round(state.first_delta or 0.0, 2),
                total_s=round(total, 2),
            )
        log.info(
            "%s replied via %s in %d step(s): first token %.2fs, first line %.2fs, total %.2fs",
            conv.npc,
            self.llm.name,
            len(trace.steps),
            state.first_delta or 0.0,
            state.first_line or 0.0,
            total,
        )
        await self._status(conv, "idle")
        if state.end_requested:
            task = asyncio.create_task(self._end_after_speech(conv, list(conv.last_lines)))
            conv.actions.add(task)
            task.add_done_callback(conv.actions.discard)

    async def _step(
        self,
        conv: Conversation,
        state: TurnState,
        request: list[Item],
        choice: ToolChoice | None,
        st: StepTrace,
    ) -> Done:
        """One model call. Its text continues the turn's reply: the chat shows it as it is written, voice
        and animation follow line by line; what is left of the last sentence is said at the end."""
        started = time.monotonic()
        first: float | None = None
        done: Done | None = None
        if state.text and not state.text.endswith((" ", "\n")):
            state.text += " "
        try:
            async for event in self.llm.stream(
                instructions=conv.persona.instructions,
                messages=request,
                tools=state.tools,
                tool_choice=choice,
            ):
                if isinstance(event, Done):
                    done = event
                if not isinstance(event, TextDelta):
                    continue
                now = time.monotonic()
                if first is None and state.relevant is not None and not state.relevant():
                    raise StaleTurn
                first = first if first is not None else now - started
                if state.first_delta is None:
                    state.first_delta = now - state.started
                pieces = self._spoken(state, state.moods.feed(event.text))
                # the chat shows the reply as it is written
                if (
                    now - state.shown_at >= CHAT_TEXT_EVERY
                    and (shown := to_game_text(state.text)) != state.shown
                ):
                    state.shown, state.shown_at = shown, now
                    await self.link.send(ChatTextCmd(npc=conv.npc, reply=state.reply, text=shown))
                await self._feed_lines(conv, state, pieces)
            await self._feed_lines(conv, state, self._spoken(state, state.moods.flush()))
            for line in state.splitter.flush():
                await self._line(conv, state, line)
        finally:
            TurnTrace.finish_step(st, done, first, time.monotonic() - started)
        if done is None:
            raise LLMError(f"{self.llm.name}: the stream ended without a result", retryable=True)
        return done

    @staticmethod
    def _spoken(state: TurnState, pieces: list[str | MoodTag]) -> list[str | MoodTag]:
        """What the model wrote, without its tags, goes to the turn's text (what is shown and remembered)."""
        out: list[str | MoodTag] = []
        for p in pieces:
            if isinstance(p, str):
                if not state.text or state.text[-1].isspace():
                    p = p.lstrip()  # no double space where a tag was taken out
                if not p:
                    continue
                state.text += p
            out.append(p)
        return out

    async def _feed_lines(self, conv: Conversation, state: TurnState, pieces: list[str | MoodTag]) -> None:
        """Lines as they complete. A new mood starts a new line: what was said before the tag goes out
        with the old mood (short sentences are not merged across it)."""
        for p in pieces:
            if isinstance(p, MoodTag):
                if p.mood != state.mood:
                    for line in state.splitter.flush():
                        await self._line(conv, state, line)
                    state.mood = p.mood
                continue
            for line in state.splitter.feed(p):
                await self._line(conv, state, line)

    async def _line(self, conv: Conversation, state: TurnState, line: str) -> None:
        if state.first_line is None:
            state.first_line = time.monotonic() - state.started
        await self._say(conv, line, state.reply, state.overlay, state.mood)

    async def _tool(self, conv: Conversation, call: ToolCall, state: TurnState) -> ToolOutcome:
        """Handle a call of the model. Checks run here, against what the npc has and sees: a rejection
        goes back to the model at once. Accepted commitments are carried out after the npc's line."""
        snap = self.link.snapshots.get(conv.npc)
        match call.name:
            case npc_tools.END_CONVERSATION:
                state.end_requested = True
                return ToolOutcome("Разговор закончится, когда ты договоришь.", "accepted")
            case npc_tools.SET_INTENT:
                return self._set_intent(conv, call, snap)
            case npc_tools.RECALL:
                if state.recalled:
                    return ToolOutcome("Больше ничего не вспоминается.", "ignored")
                state.recalled = True
                return ToolOutcome(self._recall(conv, str(call.arguments.get("query", ""))), "read")
            case npc_tools.GIVE_ITEM:
                args, reason = executor.check_give(call.arguments, snap)
                if args is None:
                    return ToolOutcome(f"Отклонено: {reason}", "rejected")
                key = (args["item"], state.reply)
                if key in state.given:  # the same item again in this turn: already on its way
                    return ToolOutcome("Этот предмет ты уже отдаёшь.", "accepted")
                state.given.add(key)
                self._journal_call(conv, call.name, args)
                give = ToolCall(call.name, args, call.call_id)
                self._after_speech(conv, lambda: self._give(conv, give, snap))
                return ToolOutcome("Принято: отдашь после реплики.", "accepted")
        for ext in self.extensions:
            if (outcome := await ext.handle(self, conv, call, snap)) is not None:
                return outcome
        return ToolOutcome(f"Отклонено: нет такого инструмента {call.name}.", "rejected")

    def _offered(self, npc: str) -> list[Tool]:
        """The tools for a turn with the hero: the npc's own and those of the extensions."""
        return [*self.tools, *(t for ext in self.extensions for t in ext.tools(npc))]

    def _recall(self, conv: Conversation, query: str) -> str:
        """Older messages than the npc has in mind (folded into the summary, or beyond what was loaded)."""
        first = conv.memory.entries[0].seq if conv.memory.entries else conv.memory.summary_seq + 1
        found = self.memory.search(conv.branch, conv.npc, query, min(conv.memory.summary_seq, first - 1))
        log.info("%s recalls %r: %d message(s)", conv.npc, query, len(found))
        if not found:
            return "Ничего такого не вспоминается."
        speaker = {"user": "Герой", "assistant": "Ты", "developer": ""}
        lines = []
        for r in found:
            who = speaker.get(r.role, "")
            lines.append(f"[{r.game_time or '?'}] {who + ': ' if who else ''}{r.content}")
        return "Ты вспоминаешь:\n" + "\n".join(lines)

    async def _give(self, conv: Conversation, call: ToolCall, snap: Snapshot | None) -> tuple[str, bool]:
        if not await self._hold(conv, "give"):
            return "Тело не смогло отдать предмет: ты занят", False
        try:
            outcome = await npc_tools.execute(self.link, conv.npc, call, snap)
        finally:
            self._free(conv, "give")
        return outcome.note, outcome.ok

    def _controlled(self, npc: str) -> bool:
        return self.controls is None or self.controls(npc)

    async def _hold(self, conv: Conversation, why: str) -> bool:
        """The npc's body for a commitment: a free npc is held (attached) for it, if it can be."""
        if self.take_body is None:
            return self._controlled(conv.npc)
        return await self.take_body(conv.npc, why)

    def _free(self, conv: Conversation, why: str) -> None:
        if self.free_body is not None:
            self.free_body(conv.npc, why)

    def _set_intent(self, conv: Conversation, call: ToolCall, snap: Snapshot | None) -> ToolOutcome:
        intent, reason = executor.check_intent(executor.parse_intent(call.arguments, conv.intent), snap)
        if intent is None:
            return ToolOutcome(f"Отклонено: {reason}", "rejected")
        if executor.same(conv.intent, intent):
            return ToolOutcome("Намерение не изменилось.", "accepted")
        self._journal_call(conv, call.name, call.arguments)
        self._change_intent(conv, intent, snap)
        return ToolOutcome(f"Принято: {intent.describe()}.", "accepted")

    def _change_intent(self, conv: Conversation, intent: Intent, snap: Snapshot | None) -> None:
        old = conv.intent
        if intent.goal in executor.INCIDENT_GOALS and old.goal not in executor.INCIDENT_GOALS:
            conv.calm = old  # what to go back to when the incident is over
        conv.intent = intent
        seq = self.memory.set_intent(
            conv.branch, conv.npc, intent.to_json(), game_time(self.link.snapshots.get(conv.npc))
        )
        self._send_head(conv.branch, seq)
        if self.on_intent is not None:
            self.on_intent(conv.npc, intent)
        following = snap.npc.following if snap else False
        acts = executor.intent_actions(old, intent, following)
        if old.goal == "follow_hero" and intent.goal != "follow_hero":
            self._free(conv, "follow")  # after `stop`, if it is still following (below)
        if intent.goal in executor.INCIDENT_GOALS and not self._controlled(conv.npc):
            # a free npc's goal of the moment (flee, fight, seek help) is carried out by whoever runs its body
            # (the planner): held for it, the npc would be out of the scripts in the middle of the danger
            if self.on_free_intent is not None:
                self.on_free_intent(conv.npc, old, intent)
            return
        if not acts:
            return
        if self.take_body is None and not self._controlled(conv.npc):
            return  # nobody can lend us a free npc's body
        why = "follow" if intent.goal == "follow_hero" else "intent"

        async def job() -> tuple[str, bool]:
            if not await self._hold(conv, why):
                return "Тело не смогло выполнить намерение: ты занят", False
            try:
                return await self._body(conv, acts)
            finally:
                if why != "follow":
                    self._free(conv, why)

        self._after_speech(conv, job)

    def incident_over(self, npc: str) -> None:
        """Goals that only hold while something is going on (flee, fight...) end with it: the npc goes
        back to what it wanted before, keeping its attitude and agenda."""
        conv = self.conversations.get(npc.upper())
        if conv is None or conv.intent.goal not in executor.INCIDENT_GOALS:
            return
        calm = conv.calm or Intent()
        intent = Intent(calm.goal, calm.target, conv.intent.attitude, conv.intent.agenda)
        log.info("%s: the incident is over, intent back to %s", conv.npc, intent.goal)
        self._change_intent(conv, intent, self.link.snapshots.get(conv.npc))

    async def _body(self, conv: Conversation, acts: list[executor.Action]) -> tuple[str, bool]:
        """Game actions for an intent; only what did not work is noted."""
        failed = []
        for action, args in acts:
            try:
                res = await self.link.act(conv.npc, action, **args)  # type: ignore[arg-type]
            except (GameError, TimeoutError) as err:
                failed.append(f"{action}: {err}")
                continue
            if res.status != "done":
                failed.append(f"{action}: {res.detail or res.status}")
        if failed:
            return f"Тело не выполнило намерение ({'; '.join(failed)})", False
        return "", True


class StaleTurn(Exception):
    """The incident a turn was about is over before the npc said a word."""


@dataclass
class TurnState:
    reply: str  # chat entry id of this turn's text
    splitter: SentenceSplitter
    started: float
    moods: MoodTags = field(default_factory=MoodTags)
    mood: Mood | None = None  # of the lines being said (the model's last tag)
    text: str = ""  # everything said in the turn, all steps
    shown: str = ""
    shown_at: float = 0.0
    first_delta: float | None = None
    first_line: float | None = None
    end_requested: bool = False
    given: set[tuple[str, str]] = field(default_factory=set)  # (item, reply) handed over in this turn
    relevant: Callable[[], bool] | None = None  # an event turn: is it still worth saying?
    recalled: bool = False  # recall is used once per turn
    overlay: bool = False  # said on the move (an urgent incident): the body does not stop for it
    tools: list[Tool] = field(default_factory=list)  # offered to the model in this turn
