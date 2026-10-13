"""Free-form conversation with an npc: player text in, npc lines (sentence by sentence) and actions out.

Whom the hero speaks to and when a conversation starts or ends is decided in talk.py (M5e: no chat
window); here are the turns of the conversational model, the npc's memory and its speech, and the lines
two npc's say to each other near the hero, in turn (`play_scene`: what they say is gossip.py's).
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
from typing import TYPE_CHECKING, Any, Protocol

from . import executor, npc_tools
from .budget import Budget
from .executor import Intent
from .fillers import Fillers, Sound, strip_said
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
    with_deadlines,
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

if TYPE_CHECKING:
    from .perception import Sight

log = logging.getLogger(__name__)

CHAT_TEXT_EVERY = 0.06  # seconds between chat_text updates while a reply streams in
TTS_PAUSE = 30.0  # after a failure, lines go out silent for a while instead of each waiting to fail
PREPARE_S = 3.0  # what an extension asks the game before a turn
KILLERS = frozenset({9})  # GIL_BDT: bandits attack the hero to kill, as their scripts do
# the hero's phrase: the model starts after the npc's first sound (the behavioral model's choice, ~50 ms)
# so that it goes on from it, but waits for that choice no longer than this
FIRST_REACTION_S = 0.25
# a model call that goes quiet: no answer this long after the request, or no more of it this long; such a
# step is asked again once per turn if the npc has said nothing of it yet (a stalled stream would otherwise
# leave the npc "thinking" for the 10 minutes of the HTTP read timeout)
LLM_FIRST_S, LLM_IDLE_S, STEP_RETRIES = 15.0, 15.0, 1
SUMMARY_FIRST_S = 60.0  # the summary is not waited for by anyone: more patience
MAX_HISTORY = 40  # messages in the prompt; older ones are folded into a summary
KEEP_RECENT = 16  # messages left verbatim after folding
# two npc's talking to each other (play_scene): the gap between their voices (the bridge breathes 250 ms
# after a line itself), and how long a sent line may take to start (a turn, a walk, a dialog; the game
# never starts one far from the hero) before the scene ends
LINE_LOST_S = 20.0  # a line sent this long ago that has not started is not coming
SCENE_GAP_S = 0.35
SCENE_START_S = 8.0
QUIET_STYLE = "say quietly, in a low hushed voice, as if not to be overheard"

SUMMARY_INSTRUCTIONS = (
    "Ты ведёшь память персонажа {name} из игры Gothic 2. Тебе дают прежнюю сводку и новые записи: его "
    "разговоры с героем и то, что он слышал о герое от других. Напиши обновлённую сводку от второго лица "
    '("ты" — это {name}), не длиннее 1200 символов: кто такой герой и что он о себе рассказал, о чём '
    "просил, что ты обещал и что отдал, как ты к нему относишься, важные события и когда они были. Только "
    "факты из записей, ничего не выдумывай. Выведи только текст сводки."
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
    trigger: Message | None = None  # what started the running turn (remembered when it ends)

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
    quests, vanilla.py; what the npc knows, knowledge.py): lines for the situation, tools to offer, and the
    handling of their calls. One may also answer recall: `recall(npc, query) -> str | None`."""

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


Voice = tuple[int, list[bytes], float]  # a line's whole voice: sample rate, 16-bit mono PCM chunks, seconds


@dataclass
class SceneLine:
    """A line one npc says to another in a scene (play_scene)."""

    line: str  # its id, as the game knows it
    npc: str
    name: str
    to: str  # the npc it is said to
    text: str
    mood: Mood | None
    started: float | None = None  # when the game started it (time.monotonic())
    audio_s: float | None = None  # its voice's length; None: said without a voice


class ScenePlay:
    """Lines of a scene being said in turn (DialogueManager.play_scene). `task` ends when the last one has
    been heard, or early: cut, or a line the game did not start (`why`)."""

    task: asyncio.Task[None]

    def __init__(self, dialogue: DialogueManager, lines: list[SceneLine], quiet: bool) -> None:
        self.lines = lines
        self.quiet = quiet
        self.sent = 0  # lines sent to the game so far
        self.why = ""  # why it ended early: "cut", "not_started" (the game never started a line), "gone"
        self.next_voice: asyncio.Task[Voice | None] | None = None  # made while the line before plays
        self._dialogue = dialogue

    def said(self) -> list[SceneLine]:
        """The lines the game started (heard, at least in part)."""
        for line in self.lines[: self.sent]:
            line.started = line.started or self._dialogue.started_at(line.line)
        return [line for line in self.lines[: self.sent] if line.started is not None]

    def cut(self, *, hush: bool = True) -> list[SceneLine]:
        """Stop at once: lines not sent yet are never heard. `hush`: both voices stop too, the line being
        said keeps its subtitle as said; without it nothing more goes to the game (it is loading). Nothing
        after the scene has ended. The lines said."""
        if self.task.done() or self.why == "cut":
            return self.said()
        self.why = "cut"
        self.task.cancel()
        if self.next_voice is not None:
            self.next_voice.cancel()
        said = self.said()
        if hush and self.sent:
            last = self.lines[self.sent - 1]
            if last.started is not None:
                self._dialogue.hush(HushCmd(npc=last.npc, line=last.line, said=last.text))
            else:  # sent, not started: if it starts later the game cuts it at once
                self._dialogue.hush(HushCmd(npc=last.npc))
            for npc in dict.fromkeys(line.npc for line in self.lines):
                if npc != last.npc:
                    self._dialogue.hush(HushCmd(npc=npc))
        return said


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
        fillers: Fillers | None = None,
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
        # the hero said something to the npc (npc, words, whether a sound is wanted): its first reaction,
        # before the answer (incidents.py asks the behavioral model)
        self.first_reaction: Callable[[str, str, bool], Awaitable[FirstReaction]] | None = None
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
        # a companion's fight (npc, target): carried out by orders.py, which brings it back to the hero
        # after; False if it is not the companions' business
        self.companion_fight: Callable[[str, str | None], bool] | None = None
        # the npc is in such a fight: a goal of going with the hero waits for it to end
        self.fighting: Callable[[str], bool] = lambda npc: False
        # the npc's guild (the roster's, talk.py), None if not known: whom it attacks to kill (`KILLERS`)
        self.guild_of: Callable[[str], int | None] = lambda npc: None
        # the npc ended the conversation (its goodbye has been said)
        self.on_end: Callable[[str], None] | None = None
        # an npc's turn is over and it said something (npc, name, the text said, the turn's kind): whoever
        # stood by heard it (group.py)
        self.on_said: list[Callable[[str, str, str, str], None]] = []
        self.llm_first_s, self.llm_idle_s = LLM_FIRST_S, LLM_IDLE_S
        # what the npc sees (perception.py), in place of the snapshot's omniscient lines
        self.sight: Callable[[str, Snapshot | None], Sight | None] | None = None
        self.extensions: list[TurnExtension] = []
        self.llm = llm
        self.personas = personas
        self.memory = memory or MemoryStore(":memory:")
        self.tts = tts
        self.fillers = fillers  # "Хм...", "Что?!" at once, while the model thinks (fillers.py)
        self._tts_paused_until = 0.0
        self.tools = npc_tools.TOOLS if tools else []
        self.branch: str | None = None
        self.conversations: dict[str, Conversation] = {}
        self._line_ids = itertools.count(1)
        self._reply_ids = itertools.count(1)
        self._lines: dict[str, tuple[str, float | None]] = {}  # line id -> (text, started at)
        self._sent: dict[str, float] = {}  # line id -> when it was sent (a line that never starts is lost)
        # scenes (play_scene): how long a line lasts without a voice (tests make it short), the lines
        # waiting for the game to start them, and a TTS connection of their own (made on first use): a
        # scene line does not wait behind an answer to the hero, nor the other way round
        self.scene_pace: Callable[[str], float] = line_duration
        self._starts: dict[str, asyncio.Event] = {}
        self._scene_tts: TtsClient | None = None
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
                if (started := self._starts.pop(msg.line, None)) is not None:
                    started.set()

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
            events = self.llm.stream(
                instructions=SUMMARY_INSTRUCTIONS.format(name=conv.persona.name),
                messages=[Message("user", prompt)],
            )
            async for event in with_deadlines(
                events, first_s=SUMMARY_FIRST_S, idle_s=SUMMARY_FIRST_S, name=self.llm.name
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

    def open(self, npc: str, name: str, *, warm: bool = True) -> Conversation:
        """The npc's conversation, read from memory the first time. `warm`: it is about to talk to the hero,
        its sounds are made now if they are not yet (not for a note: a rumor or a witness would voice them
        for every npc around); on every warm open, so an npc first opened for a note gets them later."""
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
        if warm and self.fillers is not None and self.tts is not None:
            self.fillers.warm(npc, conv.persona.voice)  # once: those made or being made are skipped
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
                # still to come, unless it never will: the game does not start a line of an npc far from
                # the hero, or one the bridge refused
                unsaid = unsaid or now - self._sent.get(line, now) < LINE_LOST_S
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
        self._note(self.open(npc, name, warm=False), text)

    def change_intent(self, npc: str, name: str, *, body: bool = True, **fields: Any) -> None:
        """Change what the npc wants or how it sees the hero, from outside a turn (a lie found out).
        `body` False: the body has done its part already (an order, orders.py)."""
        conv = self.open(npc, name)
        self._change_intent(
            conv, replace(conv.intent, **fields), self.link.snapshots.get(conv.npc), body=body
        )

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
        conv = self.open(npc, name, warm=False)
        whom = f" собеседнику ({to_name})" if to_name else " вслух"
        self._note(conv, f"Ты слышал, как герой сказал{whom}: «{text}»")

    def heard(self, npc: str, name: str, speaker: str, text: str, whom: str) -> None:
        """A witness: the npc heard another npc (`speaker`, its name) say something, `whom`: "герою",
        "вслух". No model call, only memory."""
        self._note(self.open(npc, name, warm=False), f"Ты слышал, как {speaker} говорит {whom}: «{text}»")

    def overheard_talk(
        self, npc: str, name: str, names: tuple[str, str], said: list[tuple[str, str]]
    ) -> None:
        """A witness: the npc heard two others talk about the hero (a scene, gossip.py): `said` is
        (speaker's name, line). No model call, only memory."""
        if not said:
            return
        lines = " — ".join(f"{who}: «{text}»" for who, text in said)
        self._note(
            self.open(npc, name, warm=False),
            f"Ты слышал, как {names[0]} и {names[1]} говорили о герое: {lines}",
        )

    def event_turn(
        self,
        npc: str,
        cue: str,
        relevant: Callable[[], bool] | None = None,
        *,
        overlay: bool = False,
        tools: Collection[str] | None = None,
        kind: str = "incident",
    ) -> asyncio.Task[None] | None:
        """A turn about something that happened (an incident): the npc may speak and change its intent.
        While a turn is running the event waits for the next one's situation instead of interrupting.
        `tools` limits what the model is offered (an npc nobody controls cannot hand over items); the names
        may be of the extensions' tools too (deals.py: an offer declined, the npc may offer again). `kind`
        names the trigger in the turn's trace (an npc that comes up to the hero: "initiative")."""
        conv = self.conversations.get(npc.upper())
        if conv is None:
            return None
        if conv.reply is not None and not conv.reply.done():
            conv.pending.append(cue)
            return None
        offered = self.tools if tools is None else [t for t in self._offered(npc) if t.name in tools]
        conv.reply = asyncio.create_task(
            self._turn(conv, Message("developer", cue), kind, relevant, overlay=overlay, tools=offered)
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

    async def _first_reaction(self, conv: Conversation, phrase: str, voice: bool) -> str:
        """The npc's first reaction to the hero's phrase, before its answer: the behavioral model's gesture
        and, if `voice`, a sound ("Хм...", "Что?!", fillers.py). Both or none within FIRST_REACTION_S (a
        sound without the model's choice: "Хм..." to a question). The sound goes first: the game's queue
        would hold it until the gesture is over. What was said, "" for nothing."""
        reaction = FirstReaction()
        if self.first_reaction is not None:
            try:
                reaction = await asyncio.wait_for(
                    self.first_reaction(conv.npc, phrase, voice), FIRST_REACTION_S
                )
            except TimeoutError:
                log.info("%s: no first reaction within %.2fs", conv.npc, FIRST_REACTION_S)
        said = await self._sound(conv, phrase, reaction.sound) if voice else ""
        if reaction.gesture is not None:
            reaction.gesture()
        return said

    async def _sound(self, conv: Conversation, phrase: str, kind: str | None) -> str:
        """Say a sound of `kind` at once (fillers.py picks it, now and then). Voiced from the npc's sounds
        made beforehand (none until they are made); without TTS a subtitle."""
        assert self.fillers is not None
        sound = self.fillers.pick(conv.npc, phrase, kind)
        if sound is None:
            log.debug("%s: no sound for %r (%s)", conv.npc, phrase, kind or "no choice")
            return ""
        return await self._say_sound(conv, sound)

    async def acknowledge(self, npc: str, name: str) -> None:
        """The npc's short "Понял!" to the hero's order (orders.py), at once, now and then."""
        if self.fillers is None:
            return
        conv = self.open(npc, name)
        if (sound := self.fillers.obey(conv.npc)) is not None:
            # on the move: queued, it would wait while the npc walks and hold up the order's actions
            await self._say_sound(conv, sound, overlay=True)

    async def _say_sound(self, conv: Conversation, sound: Sound, *, overlay: bool = False) -> str:
        """Say a sound picked by fillers.py; what was said, "" if it is not voiced yet."""
        assert self.fillers is not None
        wav = None
        if self.tts is not None:
            wav = self.fillers.audio(conv.npc, conv.persona.voice, sound)
            if wav is None or time.monotonic() < self._tts_paused_until:
                log.debug("%s: no sound, not voiced yet", conv.npc)
                return ""
        self.fillers.said(conv.npc, sound)
        line = f"AIB_{next(self._line_ids)}"
        self._lines[line] = (sound.text, None)
        self._sent[line] = time.monotonic()
        log.info("%s (%s): %s", conv.persona.name, sound.kind, sound.text)
        try:
            await self.link.send(
                SayCmd(
                    npc=conv.npc,
                    line=line,
                    text=sound.text,
                    audio=base64.b64encode(wav).decode() if wav is not None else None,
                    mood=sound.mood,
                    filler=True,
                    overlay=overlay or None,
                )
            )
        except GameError:
            return ""
        return sound.text

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
        self._sent[line] = time.monotonic()
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
                        self._tts_off(conv.npc, conv.persona.name, err)
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

    def _tts_off(self, npc: str, name: str, err: TtsError) -> None:
        self._tts_paused_until = time.monotonic() + TTS_PAUSE
        log.warning("%s: no voice, subtitles only for %.0fs: %s", npc, TTS_PAUSE, err)
        self.journal.add("tts_off", npc=npc, name=name, seconds=TTS_PAUSE, detail=str(err))

    # --- scenes: two npc's talk to each other -------------------------------------------

    def play_scene(
        self, lines: list[tuple[str, str, str, str, Mood | None]], *, quiet: bool = False
    ) -> ScenePlay:
        """Two npc's talk near the hero (gossip.py): lines (npc, name, to, text, mood) said in turn, each
        to the npc `to`. The game plays two npc's voices over each other, so a line is sent only once the
        one before has been heard; it goes out voiced whole (the next one is voiced while one plays).
        `quiet`: they lower their voices. Nothing here goes into anyone's memory."""
        scene = [
            SceneLine(f"AIB_{next(self._line_ids)}", npc.upper(), name, to.upper(), said, mood)
            for npc, name, to, text, mood in lines
            if (said := to_game_text(text))
        ]
        play = ScenePlay(self, scene, quiet)
        play.task = asyncio.create_task(self._play(play))
        return play

    def started_at(self, line: str) -> float | None:
        """When the game started a line sent by this server; None if it has not."""
        return self._lines.get(line, ("", None))[1]

    def hush(self, cmd: HushCmd) -> None:
        """Send a hush without waiting for it (a scene is cut synchronously)."""
        self._spawn(self.link.send(cmd))

    async def _play(self, play: ScenePlay) -> None:
        lines = play.lines
        try:
            if lines:
                play.next_voice = asyncio.create_task(self._scene_voice(lines[0], play.quiet))
            free_at = 0.0
            for i, line in enumerate(lines):
                assert play.next_voice is not None
                voice = await play.next_voice
                play.next_voice = (
                    asyncio.create_task(self._scene_voice(lines[i + 1], play.quiet))
                    if i + 1 < len(lines)
                    else None
                )
                await asyncio.sleep(max(0.0, free_at - time.monotonic()))
                started = self._starts[line.line] = asyncio.Event()
                self._lines[line.line] = (line.text, None)
                self._sent[line.line] = time.monotonic()
                play.sent = i + 1
                log.info("%s -> %s: %s", line.name, line.to, line.text)
                await self._send_scene_line(line, voice)
                try:
                    await asyncio.wait_for(started.wait(), SCENE_START_S)
                except TimeoutError:
                    # the game dropped it (the npc cannot speak) or holds it: the scene is over, and the
                    # line is cut if it starts later
                    log.info(
                        "%s: line %s not started in %.0fs, the scene ends", line.npc, line.line, SCENE_START_S
                    )
                    play.why = "not_started"
                    await self.link.send(HushCmd(npc=line.npc))
                    return
                line.started = self.started_at(line.line) or time.monotonic()
                free_at = line.started + (line.audio_s or self.scene_pace(line.text)) + SCENE_GAP_S
            await asyncio.sleep(max(0.0, free_at - time.monotonic()))
        except GameError:
            play.why = "gone"
        finally:
            if play.next_voice is not None:
                play.next_voice.cancel()
            for line in lines:
                self._starts.pop(line.line, None)

    async def _scene_voice(self, line: SceneLine, quiet: bool) -> Voice | None:
        """A scene line's whole voice, None to say it without one (no TTS, or it failed)."""
        if self.tts is None or time.monotonic() < self._tts_paused_until:
            return None
        if self._scene_tts is None:
            self._scene_tts = TtsClient(self.tts.url, self.tts.timeout)
        rate, chunks = 0, []

        # nothing else is awaited here: the client's lock is held while these run
        async def start(r: int, _provider: str) -> None:
            nonlocal rate
            rate = r

        async def chunk(pcm: bytes) -> None:
            chunks.append(pcm)

        mood = MOOD_DELIVERY[line.mood] if line.mood else ""
        style = "; ".join(s for s in (mood, QUIET_STYLE if quiet else "") if s)
        voice = self.personas.get(line.npc, line.name).voice
        try:
            speech = await self._scene_tts.stream(line.text, line.npc, voice, start, chunk, style)
        except TtsError as err:
            self._tts_off(line.npc, line.name, err)
            return None
        log.info(
            "%s voice %s: %.2fs of audio in %.2fs (%s)",
            line.npc,
            line.line,
            speech.audio_s,
            speech.latency_s,
            speech.provider,
        )
        return rate, chunks, speech.audio_s

    async def _send_scene_line(self, line: SceneLine, voice: Voice | None) -> None:
        """Say, all of its audio and its end at once: the game drains them in one tick, so the line starts
        with its whole voice and its length known (never a WAV `audio`: a hush stops only streamed voices)."""
        if voice is None:
            await self.link.send(
                SayCmd(npc=line.npc, line=line.line, text=line.text, mood=line.mood, to=line.to)
            )
            return
        rate, chunks, audio_s = voice
        line.audio_s = audio_s
        await self.link.send(
            SayCmd(
                npc=line.npc,
                line=line.line,
                text=line.text,
                stream=True,
                rate=rate,
                mood=line.mood,
                to=line.to,
            )
        )
        for pcm in chunks:
            await self.link.send(
                SayAudioCmd(npc=line.npc, line=line.line, data=base64.b64encode(pcm).decode())
            )
        await self.link.send(SayEndCmd(npc=line.npc, line=line.line, audio_s=round(audio_s, 3)))

    def _commit(self, conv: Conversation, trigger: Message, text: str, kind: str) -> None:
        # one synchronous step: an interrupted reply and its replacement cannot interleave here.
        # What started the turn is remembered even when the npc said nothing (it may have acted).
        self._remember(conv, trigger.role, trigger.content)
        if text := text.strip():
            self._remember(conv, "assistant", text)
            for listener in self.on_said:
                listener(conv.npc, conv.persona.name, to_game_text(text), kind)
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
        conv.trigger = trigger
        await self._status(conv, "thinking")
        # the hero's phrase: the npc's first reaction comes at once, the model starts after its sound
        voice = kind == "player" and not overlay and self.fillers is not None
        first = (
            asyncio.create_task(self._first_reaction(conv, trigger.content, voice))
            if kind == "player"
            else None
        )
        try:
            situation_text, snap = await self._situation(conv, body)
            sound = await first if first is not None and voice else ""
        except asyncio.CancelledError:
            if first is not None:
                first.cancel()
            raise
        if sound:
            situation_text += f"\nТы уже сказал вслух: «{sound}» — ответ продолжает это, не повторяй его."
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
            filler=sound,
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
        status = "completed"
        added: list[Item] = list(request)
        retries = STEP_RETRIES
        try:
            for step in range(MAX_STEPS):
                st = trace.step(added, tool_choice(step))
                while True:
                    said = state.text.strip()
                    try:
                        done = await self._step(conv, state, request, tool_choice(step), st)
                        break
                    except LLMError as err:
                        # asked again only if nothing of this step was said: else the npc would repeat it
                        if not err.retryable or retries <= 0 or state.text.strip() != said:
                            raise
                        retries -= 1
                        log.warning("%s: %s; asking the model again", conv.npc, err)
                        trace.event("retry", error=str(err))
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
                self._commit(conv, trigger, state.text, kind)
            trace.close("llm_error")
            await self._status(conv, "error", str(err))
            return
        except StaleTurn:
            # what the turn was about is over: words now would be out of place
            self._commit(conv, trigger, state.text, kind)
            trace.close("stale")
            await self._status(conv, "idle")
            return
        except asyncio.CancelledError:
            if first is not None:
                first.cancel()  # its gesture too: the hero speaks again
            if conv.cut is not None:  # the hero interrupted: only what was said counts
                said, conv.cut = conv.cut, None
                self._commit(conv, trigger, said, kind)
                self._note(conv, "Герой перебил тебя" + ("" if said else ", ты не успел ничего сказать"))
                trace.close("interrupted")
                raise
            self._commit(conv, trigger, state.text, kind)  # the npc remembers what it already said
            trace.close("cancelled")
            raise

        self._commit(conv, trigger, state.text, kind)
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

    async def _situation(self, conv: Conversation, body: str | None) -> tuple[str, Snapshot | None]:
        """The situation of a turn, from what the game and the extensions say now."""
        if conv.new_meeting:
            conv.new_meeting = False
            if conv.memory.entries or conv.memory.summary:
                when = game_time(self.link.snapshots.get(conv.npc))
                self._note(conv, f"Новая встреча с героем{f', {when}' if when else ''}")
        for ext in self.extensions:
            try:
                await asyncio.wait_for(ext.prepare(conv.npc), PREPARE_S)
            except (GameError, TimeoutError) as err:
                log.warning("%s: %s not ready: %s", conv.npc, type(ext).__name__, err)
        snap = self.link.snapshots.get(conv.npc)
        sight = self.sight(conv.npc, snap) if self.sight is not None else None
        situation_text = npc_tools.render_situation(snap, conv.intent, sight)
        for ext in self.extensions:
            if extra := ext.situation(conv.npc):
                situation_text += "\n" + extra
        if body:
            situation_text += f"\nТвоё тело сейчас: {body} — это уже происходит, не противоречь этому."
        if conv.pending:  # events that came during the last turn
            situation_text += "\nТолько что: " + "; ".join(c.strip("[]") for c in conv.pending) + "."
            conv.pending = []
        return situation_text, snap

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
            try:
                events = self.llm.stream(
                    instructions=conv.persona.instructions,
                    messages=request,
                    tools=state.tools,
                    tool_choice=choice,
                )
                async for event in with_deadlines(
                    events, first_s=self.llm_first_s, idle_s=self.llm_idle_s, name=self.llm.name
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
            except LLMError:
                if first is not None:  # the model went quiet mid-answer: what it wrote is said, as remembered
                    await self._say_rest(conv, state)
                raise
            await self._say_rest(conv, state)
        finally:
            TurnTrace.finish_step(st, done, first, time.monotonic() - started)
        if done is None:
            raise LLMError(f"{self.llm.name}: the stream ended without a result", retryable=True)
        return done

    async def _say_rest(self, conv: Conversation, state: TurnState) -> None:
        """What is left of the step's text once its stream is over: the last sentence."""
        await self._feed_lines(conv, state, self._spoken(state, state.moods.flush()))
        for line in state.splitter.flush():
            await self._line(conv, state, line)

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
        if state.filler:  # said at once already: not "Хм... Хм, ты прав." nor "Что?! Что?! Орки?"
            line = strip_said(line, state.filler)
            if not line:
                return
            state.filler = ""
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
        """Older messages than the npc has in mind (folded into the summary, or beyond what was loaded), and
        what extensions that answer recall know of it (knowledge.py: the npc's dialogs in full)."""
        first = conv.memory.entries[0].seq if conv.memory.entries else conv.memory.summary_seq + 1
        found = self.memory.search(conv.branch, conv.npc, query, min(conv.memory.summary_seq, first - 1))
        parts = []
        if found:
            speaker = {"user": "Герой", "assistant": "Ты", "developer": ""}
            lines = []
            for r in found:
                who = speaker.get(r.role, "")
                lines.append(f"[{r.game_time or '?'}] {who + ': ' if who else ''}{r.content}")
            parts.append("Ты вспоминаешь:\n" + "\n".join(lines))
        for ext in self.extensions:
            recall = getattr(ext, "recall", None)
            if recall is not None and (known := recall(conv.npc, query)):
                parts.append(known)
        log.info("%s recalls %r: %d message(s), %d more part(s)", conv.npc, query, len(found), len(parts))
        return "\n\n".join(parts) or "Ничего такого не вспоминается."

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
        sight = self.sight(conv.npc, snap) if self.sight is not None else None
        seen = sight.targets if sight is not None else None  # whom it perceives, not all within 15 m
        intent, reason = executor.check_intent(executor.parse_intent(call.arguments, conv.intent), snap, seen)
        if intent is None:
            return ToolOutcome(f"Отклонено: {reason}", "rejected")
        if executor.same(conv.intent, intent):
            return ToolOutcome("Намерение не изменилось.", "accepted")
        self._journal_call(conv, call.name, call.arguments)
        self._change_intent(conv, intent, snap)
        return ToolOutcome(f"Принято: {intent.describe()}.", "accepted")

    def _change_intent(
        self, conv: Conversation, intent: Intent, snap: Snapshot | None, *, body: bool = True
    ) -> None:
        old = conv.intent
        if intent.goal in executor.INCIDENT_GOALS and old.goal not in executor.INCIDENT_GOALS:
            conv.calm = old  # what to go back to when the incident is over
        conv.intent = intent
        with_hero = old.goal in executor.COMPANION_GOALS or (
            old.goal in executor.INCIDENT_GOALS
            and conv.calm is not None
            and conv.calm.goal in executor.COMPANION_GOALS
        )
        seq = self.memory.set_intent(
            conv.branch, conv.npc, intent.to_json(), game_time(self.link.snapshots.get(conv.npc))
        )
        self._send_head(conv.branch, seq)
        if self.on_intent is not None:
            self.on_intent(conv.npc, intent)
        following = snap.npc.following if snap else False
        acts = executor.intent_actions(old, intent, following)
        if body and intent.goal == "fight" and with_hero and self.companion_fight is not None:
            if self.companion_fight(conv.npc, intent.target):
                return  # a companion's fight: it stays with the hero (orders.py)
        if old.goal in executor.COMPANION_GOALS and intent.goal not in executor.COMPANION_GOALS:
            self._free(conv, "follow")  # after `stop`, if it is still following (below)
        if not body:
            return
        if (
            intent.goal == "fight"
            and intent.target == "PLAYER"
            and (old.goal, old.target) != ("fight", "PLAYER")
        ):
            self._after_speech(conv, lambda: self._attack_hero(conv, intent))
            return
        if self.fighting(conv.npc) and intent.goal in executor.COMPANION_GOALS:
            return  # it is fighting for the hero: it follows him again once the fight is over (orders.py)
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

    def incident_over(self, npc: str, *, body: bool = True) -> None:
        """Goals that only hold while something is going on (flee, fight...) end with it: the npc goes
        back to what it wanted before, keeping its attitude and agenda. `body` False: the caller moves
        the body (orders.py)."""
        conv = self.conversations.get(npc.upper())
        if conv is None or conv.intent.goal not in executor.INCIDENT_GOALS:
            return
        calm = conv.calm or Intent()
        intent = Intent(calm.goal, calm.target, conv.intent.attitude, conv.intent.agenda)
        log.info("%s: the incident is over, intent back to %s", conv.npc, intent.goal)
        self._change_intent(conv, intent, self.link.snapshots.get(conv.npc), body=body)

    async def _attack_hero(self, conv: Conversation, intent: Intent) -> tuple[str, bool]:
        """The npc turns on the hero, after its words: the scripts' own attack (B_Attack), held or free, so
        the fight goes as the game's do. A bandit means to kill and its gang joins it; anyone else gives him
        a beating until he is down."""
        if conv.intent is not intent:
            return "", True  # it changed its mind before it was done talking
        reason = "AR_SUDDENENEMYINFERNO" if self.guild_of(conv.npc) in KILLERS else "AR_NONE"
        try:
            await self.link.story_effect(conv.npc, [{"do": "attack", "reason": reason}])
        except (GameError, TimeoutError) as err:
            return f"Тело не напало на героя ({err})", False
        log.info("%s attacks the hero (%s)", conv.npc, reason)
        self.journal.add("attack", npc=conv.npc, name=conv.persona.name, target="PLAYER", reason=reason)
        return "Ты напал на героя.", True

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
class FirstReaction:
    """The behavioral model's first reaction to the hero's phrase (incidents.py)."""

    # the kind of sound the npc makes at once (fillers.KINDS, "none" for nothing); None: no choice
    sound: str | None = None
    gesture: Callable[[], None] | None = None  # makes its gesture, if any


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
    filler: str = ""  # said at once ("Хм...", "Что?!"): the answer does not start with it again
    overlay: bool = False  # said on the move (an urgent incident): the body does not stop for it
    tools: list[Tool] = field(default_factory=list)  # offered to the model in this turn
