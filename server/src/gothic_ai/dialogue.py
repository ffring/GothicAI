"""Free-form conversation with an npc: player text in, npc lines (sentence by sentence) and actions out."""

from __future__ import annotations

import asyncio
import base64
import itertools
import logging
import re
import time
import uuid
from collections.abc import Coroutine
from dataclasses import dataclass, field
from typing import Any

from . import npc_tools
from .game import GameError, GameLink
from .llm import LLMBackend, LLMError, Message, Role, TextDelta, ToolCall
from .memory import Entry, MemoryStore, NpcMemory
from .persona import Persona, PersonaProvider
from .protocol import (
    ChatClose,
    ChatEndCmd,
    ChatOpen,
    ChatStatus,
    ChatStatusCmd,
    ChatTextCmd,
    GameMessage,
    LineStarted,
    MemoryCmd,
    PlayerText,
    SayAudioCmd,
    SayCmd,
    SayEndCmd,
    Session,
    Snapshot,
)
from .tts import TtsClient, TtsError

log = logging.getLogger(__name__)

GREETING_CUE = "[Герой подходит к тебе и хочет поговорить.]"
CHAT_TEXT_EVERY = 0.06  # seconds between chat_text updates while a reply streams in
TTS_PAUSE = 30.0  # after a failure, lines go out silent for a while instead of each waiting to fail
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
    voice: asyncio.Queue[tuple[str, str, str]] | None = None  # (line, text, reply)
    voicing: asyncio.Task[None] | None = None

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


class DialogueManager:
    """Reacts to chat messages from the game. One reply in flight per npc; new player text interrupts it.

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
        greet: bool = True,
        tools: bool = True,
    ) -> None:
        self.link = link
        self.llm = llm
        self.personas = personas
        self.memory = memory or MemoryStore(":memory:")
        self.tts = tts
        self._tts_paused_until = 0.0
        self.greet = greet
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
            case ChatOpen():
                conv = self.open(msg.npc, msg.name)
                conv.new_meeting = True
                if self.greet and not conv.memory.entries and not conv.memory.summary:
                    self._start_reply(conv, GREETING_CUE)
            case PlayerText():
                conv = self.conversations.get(msg.npc.upper())
                if conv is None:
                    log.warning("player_text for npc without open chat: %s", msg.npc)
                    return
                self._start_reply(conv, msg.text)
            case ChatClose():
                if conv := self.conversations.get(msg.npc.upper()):
                    self._cancel(conv)
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
            self.conversations[npc] = conv
            if conv.memory.entries or conv.memory.summary:
                log.info(
                    "%s remembers %d message(s)%s",
                    npc,
                    len(conv.memory.entries),
                    " and a summary" if conv.memory.summary else "",
                )
        return conv

    def say_as_player(self, npc: str, text: str) -> asyncio.Task[None]:
        """Player text that did not come as `player_text`: voice, console, scenarios."""
        conv = self.conversations.get(npc.upper())
        if conv is None:
            raise KeyError(f"no conversation with {npc}")
        return self._start_reply(conv, text)

    def _cancel(self, conv: Conversation) -> None:
        if conv.reply is not None and not conv.reply.done():
            conv.reply.cancel()
        conv.reply = None

    def _start_reply(self, conv: Conversation, user_text: str) -> asyncio.Task[None]:
        self._cancel(conv)
        if conv.voice is not None:  # lines of the interrupted reply that were not voiced yet
            while not conv.voice.empty():
                conv.voice.get_nowait()
                conv.voice.task_done()
        conv.reply = asyncio.create_task(self._reply(conv, user_text))
        return conv.reply

    async def _status(self, conv: Conversation, status: ChatStatus, detail: str | None = None) -> None:
        await self.link.send(ChatStatusCmd(npc=conv.npc, status=status, detail=detail))

    async def _say(self, conv: Conversation, text: str, reply: str) -> None:
        text = to_game_text(text)
        if not text:
            return
        line = f"AIB_{next(self._line_ids)}"
        conv.last_lines.append(line)
        self._lines[line] = (text, None)
        log.info("%s: %s", conv.persona.name, text)
        if self.tts is None:
            await self.link.send(SayCmd(npc=conv.npc, line=line, text=text, reply=reply))
            return
        if conv.voice is None:
            conv.voice = asyncio.Queue()
            conv.voicing = asyncio.create_task(self._voice(conv, conv.voice))
        conv.voice.put_nowait((line, text, reply))

    async def _voice(self, conv: Conversation, queue: asyncio.Queue[tuple[str, str, str]]) -> None:
        """Voices the lines one by one and streams them to the game: `say` goes out with the first
        audio, chunks follow as they are generated, `say_end` gives the length. A line that gets no
        audio still goes out, silent."""
        assert self.tts is not None
        while True:
            line, text, reply = await queue.get()
            started = False
            try:
                if time.monotonic() >= self._tts_paused_until:

                    async def start(
                        rate: int, _provider: str, line: str = line, text: str = text, reply: str = reply
                    ) -> None:
                        nonlocal started
                        started = True
                        await self.link.send(
                            SayCmd(npc=conv.npc, line=line, text=text, stream=True, rate=rate, reply=reply)
                        )

                    async def chunk(pcm: bytes, line: str = line) -> None:
                        await self.link.send(
                            SayAudioCmd(npc=conv.npc, line=line, data=base64.b64encode(pcm).decode())
                        )

                    try:
                        speech = await self.tts.stream(text, conv.npc, conv.persona.voice, start, chunk)
                        await self.link.send(
                            SayEndCmd(npc=conv.npc, line=line, audio_s=round(speech.audio_s, 3))
                        )
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
                        if started:
                            await self.link.send(SayEndCmd(npc=conv.npc, line=line, error=str(err)))
                if not started:
                    await self.link.send(SayCmd(npc=conv.npc, line=line, text=text, reply=reply))
            except GameError:
                pass
            finally:
                queue.task_done()

    def _commit(self, conv: Conversation, user: Message, text: str) -> None:
        # one synchronous step: an interrupted reply and its replacement cannot interleave here
        if text:
            self._remember(conv, "user", user.content)
            self._remember(conv, "assistant", text)
            self._maybe_compact(conv)

    def _note(self, conv: Conversation, note: str) -> None:
        if note:
            log.info("%s note: %s", conv.npc, note)
            self._remember(conv, "developer", f"[{note}]")

    def _act(self, conv: Conversation, call: ToolCall) -> None:
        """Run a tool call next to the speech; its outcome lands in the history as a note."""
        log.info("%s calls %s %s", conv.npc, call.name, call.arguments)
        snapshot = self.link.snapshots.get(conv.npc)

        reply = conv.reply

        async def run() -> None:
            if conv.voice is not None:
                await conv.voice.join()  # the npc says what it said before acting on it
            outcome = await npc_tools.execute(self.link, conv.npc, call, snapshot)
            if reply is not None and not reply.done():
                await asyncio.wait({reply})  # the note goes after this turn's reply in the history
            self._note(conv, outcome.note)

        task = asyncio.create_task(run())
        conv.actions.add(task)
        task.add_done_callback(conv.actions.discard)

    async def _end_after_speech(self, conv: Conversation, lines: list[str]) -> None:
        """Close the chat only once the npc has said its goodbye (closing clears its speech queue)."""
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            started = [self._lines.get(line, ("", None)) for line in lines]
            if all(at is not None for _, at in started):
                last_text, last_at = started[-1] if started else ("", time.monotonic())
                await asyncio.sleep(max(0.0, (last_at or 0) + line_duration(last_text) - time.monotonic()))
                break
            await asyncio.sleep(0.2)
        self._note(conv, "Ты закончил разговор")
        await self.link.send(ChatEndCmd(npc=conv.npc))

    async def _reply(self, conv: Conversation, user_text: str) -> None:
        if conv.new_meeting:
            conv.new_meeting = False
            if conv.memory.entries or conv.memory.summary:
                when = game_time(self.link.snapshots.get(conv.npc))
                self._note(conv, f"Новая встреча с героем{f', {when}' if when else ''}")
        user = Message("user", user_text)
        situation = Message("developer", npc_tools.render_situation(self.link.snapshots.get(conv.npc)))
        messages = [*conv.history, situation, user]
        conv.last_lines = []
        await self._status(conv, "thinking")
        # voiced, the first line is synthesized before it is heard: cut it at the first clause
        splitter = SentenceSplitter(first_clause=self.tts is not None)
        started = time.monotonic()
        first_delta: float | None = None
        first_line: float | None = None
        text = ""
        end_requested = False
        reply = f"R{next(self._reply_ids)}"
        shown, shown_at = "", 0.0
        try:
            async for event in self.llm.stream(
                instructions=conv.persona.instructions, messages=messages, tools=self.tools
            ):
                if isinstance(event, ToolCall):
                    if event.name == npc_tools.END_CONVERSATION:
                        end_requested = True
                    else:
                        self._act(conv, event)
                    continue
                if not isinstance(event, TextDelta):
                    continue
                if first_delta is None:
                    first_delta = time.monotonic() - started
                text += event.text
                # the chat shows the reply as it is written; voice and animation follow line by line
                if time.monotonic() - shown_at >= CHAT_TEXT_EVERY and (now := to_game_text(text)) != shown:
                    shown, shown_at = now, time.monotonic()
                    await self.link.send(ChatTextCmd(npc=conv.npc, reply=reply, text=shown))
                for line in splitter.feed(event.text):
                    if first_line is None:
                        first_line = time.monotonic() - started
                    await self._say(conv, line, reply)
            for line in splitter.flush():
                if first_line is None:
                    first_line = time.monotonic() - started
                await self._say(conv, line, reply)
            if text:
                await self.link.send(
                    ChatTextCmd(npc=conv.npc, reply=reply, text=to_game_text(text), final=True)
                )
        except LLMError as err:
            log.error("%s: %s", conv.npc, err)
            await self._status(conv, "error", str(err))
            return
        except asyncio.CancelledError:
            self._commit(conv, user, text)  # the npc remembers what it already said
            raise

        self._commit(conv, user, text)
        log.info(
            "%s replied via %s: first token %.2fs, first line %.2fs, total %.2fs",
            conv.npc,
            self.llm.name,
            first_delta or 0.0,
            first_line or 0.0,
            time.monotonic() - started,
        )
        await self._status(conv, "idle")
        if end_requested:
            task = asyncio.create_task(self._end_after_speech(conv, list(conv.last_lines)))
            conv.actions.add(task)
            task.add_done_callback(conv.actions.discard)
