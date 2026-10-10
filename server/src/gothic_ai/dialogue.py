"""Free-form conversation with an npc: player text in, npc lines (sentence by sentence) out."""

from __future__ import annotations

import asyncio
import itertools
import logging
import re
import time
from dataclasses import dataclass, field

from .game import GameLink
from .llm import LLMBackend, LLMError, Message, TextDelta
from .persona import Persona, PersonaProvider
from .protocol import ChatClose, ChatOpen, ChatStatus, ChatStatusCmd, GameMessage, PlayerText, SayCmd

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


class DialogueManager:
    """Reacts to chat messages from the game. One reply in flight per npc; new player text interrupts it."""

    def __init__(
        self, link: GameLink, llm: LLMBackend, personas: PersonaProvider, *, greet: bool = True
    ) -> None:
        self.link = link
        self.llm = llm
        self.personas = personas
        self.greet = greet
        self.conversations: dict[str, Conversation] = {}
        self._line_ids = itertools.count(1)
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
        if text:
            line = f"AIB_{next(self._line_ids)}"
            conv.last_lines.append(line)
            await self.link.send(SayCmd(npc=conv.npc, line=line, text=text))

    def _commit(self, conv: Conversation, user: Message, text: str) -> None:
        # one synchronous step: an interrupted reply and its replacement cannot interleave here
        if text:
            conv.history += [user, Message("assistant", text)]
            del conv.history[:-MAX_HISTORY]

    async def _reply(self, conv: Conversation, user_text: str) -> None:
        user = Message("user", user_text)
        messages = [*conv.history, user]
        conv.last_lines = []
        await self._status(conv, "thinking")
        splitter = SentenceSplitter()
        started = time.monotonic()
        first_delta: float | None = None
        first_line: float | None = None
        text = ""
        try:
            async for event in self.llm.stream(instructions=conv.persona.instructions, messages=messages):
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
