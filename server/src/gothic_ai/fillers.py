"""Quick words while the conversational model thinks of an answer: "Хм...", "Дай подумать...", "Угу.".

The hero's phrase goes to the model, and the first line of its answer is heard a second or three later
(the model's first sentence, then its voice). People do not stand silent that long: they say "hm" at
once. A filler is said when no line of the answer is on its way a moment after the phrase: to a
question something of thinking, to a statement a short acknowledgement, to a greeting nothing (the
answer itself is a greeting). Not every time, never the same one twice in a row.

Said at once, so voiced before it is needed: each npc's fillers are made once with its own voice (the
TTS service, a connection of their own: the lines of answers do not wait for them) and kept on disk.
Until they are made, the npc says none (a silent "Хм..." would only be a subtitle).
"""

from __future__ import annotations

import array
import asyncio
import hashlib
import io
import logging
import random
import re
import time
import wave
from collections.abc import Callable
from pathlib import Path

from .tts import TtsClient, TtsError

log = logging.getLogger(__name__)

DEFAULT_CACHE = Path.home() / ".local" / "share" / "gothic-ai" / "fillers"
DELAY_S = 0.35  # no line of the answer on its way by then: a filler
MIN_GAP_S = 6.0  # an npc says fillers at most this often

THINK = ("Хм...", "Дай подумать...", "Так...", "Сейчас...", "Погоди-ка...")
ACK = ("Угу.", "Так-так.", "Хм.", "Вот как.", "Ясно...")
CHANCE = {"think": 0.75, "ack": 0.45}
ALL = THINK + ACK

_QUESTION_WORDS = frozenset(
    "кто что где куда откуда когда почему зачем как сколько какой какая какое какие чей чья чьё чьи "
    "можно можешь сможешь знаешь скажи расскажи объясни".split()
)
_GREETINGS = ("привет", "здравствуй", "здорово", "доброе утро", "добрый день", "добрый вечер", "приветствую")
_WORD = re.compile(r"[а-яёa-z-]+")
# what a model's answer may start with that a filler already said ("Хм... Хм, ты прав.")
_INTERJECTION = re.compile(
    r"^\s*(?:(?:хм+|э+|м+)(?:[.,!…]+|(?=\s))|(?:ну|так|угу|ага)[.,!…]+)\s*", re.IGNORECASE
)


def kind_of(phrase: str) -> str | None:
    """What fits the hero's phrase: "think" (a question, a request), "ack" (he tells something), None
    (a greeting, a word or two: the answer comes as quick as a filler would)."""
    t = phrase.strip().lower()
    words = _WORD.findall(t)
    if len(words) < 2 or any(t.startswith(g) for g in _GREETINGS):
        return None
    if "?" in t or words[0] in _QUESTION_WORDS or " ли " in f" {t} ":
        return "think"
    return "ack"


def strip_interjection(line: str) -> str:
    """The first line of an answer without the "Хм..." a filler already said."""
    rest = _INTERJECTION.sub("", line, count=1)
    return rest[:1].upper() + rest[1:] if rest else rest


def trim(wav: bytes, quiet: float = 0.03, keep_s: float = 0.06) -> bytes:
    """The WAV (16-bit mono) without the silence before and after the sound: TTS ends "Так..." with half
    a second of it, and the answer waits in the game's queue until the filler is over. `quiet`: below
    this part of the loudest sample it is silence; `keep_s` of it is kept at either end."""
    try:
        with wave.open(io.BytesIO(wav)) as w:
            if w.getsampwidth() != 2 or w.getnchannels() != 1:
                return wav
            rate = w.getframerate()
            pcm = array.array("h", w.readframes(w.getnframes()))
    except (wave.Error, EOFError):
        return wav
    if not pcm:
        return wav
    limit = max(abs(v) for v in pcm) * quiet
    loud = [i for i, v in enumerate(pcm) if abs(v) > limit]
    if not loud:
        return wav
    keep = int(keep_s * rate)
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm[max(0, loud[0] - keep) : loud[-1] + keep].tobytes())
    return out.getvalue()


class Fillers:
    """The fillers of the npc's voices, picked for the hero's phrase. `tts_url` None: no voices (the
    server runs without TTS), the fillers are subtitles."""

    def __init__(
        self,
        tts_url: str | None,
        cache: Path = DEFAULT_CACHE,
        rng: random.Random | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.tts_url = tts_url
        self.cache = cache
        self.rng = rng or random.Random()
        self.clock = clock
        self._last: dict[str, tuple[str, float]] = {}  # npc -> (filler, when)
        self._warming: dict[str, asyncio.Task[None]] = {}
        self._tts: TtsClient | None = None

    def pick(self, npc: str, phrase: str) -> str | None:
        kind = kind_of(phrase)
        if kind is None:
            return None
        last, at = self._last.get(npc, ("", -MIN_GAP_S))
        now = self.clock()
        if now - at < MIN_GAP_S or self.rng.random() >= CHANCE[kind]:
            return None
        options = [f for f in (THINK if kind == "think" else ACK) if f != last]
        return self.rng.choice(options)

    def said(self, npc: str, text: str) -> None:
        self._last[npc] = (text, self.clock())

    def path(self, npc: str, voice: str, text: str) -> Path:
        key = hashlib.sha1(f"{npc}\n{voice}\n{text}".encode()).hexdigest()[:16]
        return self.cache / f"{npc.lower()}-{key}.wav"

    def audio(self, npc: str, voice: str, text: str) -> bytes | None:
        """The voiced filler (a WAV file) without the silence around it, None if not made yet."""
        p = self.path(npc, voice, text)
        return trim(p.read_bytes()) if p.is_file() else None

    def warm(self, npc: str, voice: str) -> None:
        """Make the npc's fillers in the background, those not on disk yet."""
        if self.tts_url is None or npc in self._warming:
            return
        missing = [t for t in ALL if not self.path(npc, voice, t).is_file()]
        if missing:
            self._warming[npc] = asyncio.create_task(self._make(npc, voice, missing))

    async def _make(self, npc: str, voice: str, texts: list[str]) -> None:
        if self._tts is None:
            self._tts = TtsClient(self.tts_url or "")
        self.cache.mkdir(parents=True, exist_ok=True)
        for text in texts:
            try:
                speech = await self._tts.speak(text, npc, voice)
            except TtsError as err:
                log.warning("%s: no fillers for now: %s", npc, err)
                self._warming.pop(npc, None)  # tried again with the next conversation
                return
            p = self.path(npc, voice, text)
            tmp = p.with_suffix(".tmp")
            tmp.write_bytes(speech.wav)
            tmp.replace(p)
        log.info("%s: %d fillers voiced", npc, len(texts))

    async def aclose(self) -> None:
        for task in self._warming.values():
            task.cancel()
        if self._tts is not None:
            await self._tts.aclose()
