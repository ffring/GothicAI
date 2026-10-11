"""Free-form conversation with an npc: player text in, npc lines (sentence by sentence) and actions out."""

from __future__ import annotations

import asyncio
import itertools
import logging
import re
import time
from dataclasses import dataclass, field

from . import npc_tools
from .game import GameLink
from .llm import LLMBackend, LLMError, Message, TextDelta, ToolCall
from .persona import Persona, PersonaProvider
from .protocol import (
    ChatClose,
    ChatEndCmd,
    ChatOpen,
    ChatStatus,
    ChatStatusCmd,
    GameMessage,
    LineStarted,
    PlayerText,
    SayCmd,
)

log = logging.getLogger(__name__)

GREETING_CUE = "[Герой подходит к тебе и хочет поговорить.]"
MAX_HISTORY = 40  # messages kept per npc


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


class SentenceSplitter:
    """Cuts streamed text into subtitle lines: whole sentences, merged if short, split if too long."""

    def __init__(self, min_len: int = 24, max_len: int = 200) -> None:
        self.min_len = min_len
        self.max_len = max_len
        self._buf = ""

    def feed(self, text: str) -> list[str]:
        self._buf += text
        out: list[str] = []
        start = 0
        pending = ""
        for m in _SENTENCE_END.finditer(self._buf):
            pending += self._buf[start : m.end()]
            start = m.end()
            if len(pending.strip()) >= self.min_len:
                out.extend(self._limit(pending))
                pending = ""
        self._buf = pending + self._buf[start:]
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


@dataclass
class Conversation:
    npc: str
    persona: Persona
    history: list[Message] = field(default_factory=list)
    reply: asyncio.Task[None] | None = None
    last_lines: list[str] = field(default_factory=list)  # line ids of the latest reply
    actions: set[asyncio.Task[None]] = field(default_factory=set)


class DialogueManager:
    """Reacts to chat messages from the game. One reply in flight per npc; new player text interrupts it."""

    def __init__(
        self,
        link: GameLink,
        llm: LLMBackend,
        personas: PersonaProvider,
        *,
        greet: bool = True,
        tools: bool = True,
    ) -> None:
        self.link = link
        self.llm = llm
        self.personas = personas
        self.greet = greet
        self.tools = npc_tools.TOOLS if tools else []
        self.conversations: dict[str, Conversation] = {}
        self._line_ids = itertools.count(1)
        self._lines: dict[str, tuple[str, float | None]] = {}  # line id -> (text, started at)
        link.listeners.append(self._on_message)

    def _on_message(self, msg: GameMessage) -> None:
        match msg:
            case ChatOpen():
                conv = self.open(msg.npc, msg.name)
                if self.greet and not conv.history:
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

    def open(self, npc: str, name: str) -> Conversation:
        npc = npc.upper()
        conv = self.conversations.get(npc)
        if conv is None:
            conv = Conversation(npc, self.personas.get(npc, name))
            self.conversations[npc] = conv
        return conv

    def say_as_player(self, npc: str, text: str) -> asyncio.Task[None]:
        """Server-side injection of player text (console, scenarios)."""
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
        conv.reply = asyncio.create_task(self._reply(conv, user_text))
        return conv.reply

    async def _status(self, conv: Conversation, status: ChatStatus, detail: str | None = None) -> None:
        await self.link.send(ChatStatusCmd(npc=conv.npc, status=status, detail=detail))

    async def _say(self, conv: Conversation, text: str) -> None:
        text = to_game_text(text)
        if not text:
            return
        line = f"AIB_{next(self._line_ids)}"
        conv.last_lines.append(line)
        self._lines[line] = (text, None)
        log.info("%s: %s", conv.persona.name, text)
        await self.link.send(SayCmd(npc=conv.npc, line=line, text=text))

    def _commit(self, conv: Conversation, user: Message, text: str) -> None:
        # one synchronous step: an interrupted reply and its replacement cannot interleave here
        if text:
            conv.history += [user, Message("assistant", text)]
            del conv.history[:-MAX_HISTORY]

    def _note(self, conv: Conversation, note: str) -> None:
        if note:
            log.info("%s note: %s", conv.npc, note)
            conv.history.append(Message("developer", f"[{note}]"))

    def _act(self, conv: Conversation, call: ToolCall) -> None:
        """Run a tool call next to the speech; its outcome lands in the history as a note."""
        log.info("%s calls %s %s", conv.npc, call.name, call.arguments)
        snapshot = self.link.snapshots.get(conv.npc)

        reply = conv.reply

        async def run() -> None:
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
        user = Message("user", user_text)
        situation = Message("developer", npc_tools.render_situation(self.link.snapshots.get(conv.npc)))
        messages = [*conv.history, situation, user]
        conv.last_lines = []
        await self._status(conv, "thinking")
        splitter = SentenceSplitter()
        started = time.monotonic()
        first_delta: float | None = None
        first_line: float | None = None
        text = ""
        end_requested = False
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
                for line in splitter.feed(event.text):
                    if first_line is None:
                        first_line = time.monotonic() - started
                    await self._say(conv, line)
            for line in splitter.flush():
                if first_line is None:
                    first_line = time.monotonic() - started
                await self._say(conv, line)
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
