"""What an npc says at once to the hero's phrase, while the conversational model thinks of its answer:
"Хм...", "Что?!", "Ой ли?".

The first line of an answer is heard a second or three after the hero's phrase (the model's first
sentence, then its voice). People do not stand silent that long: they react at once. How, the
behavioral model decides from the phrase, in the same question as the gesture (brain/reflex.py,
`voice`): nothing to a greeting (the answer is one itself), "Хм..." to a question, "Вот как." to news,
"Что?!" to alarming news, "Тьфу!" to an insult, "Ой ли?" to a boast. Without its choice a question gets
"Хм..." and anything else nothing. Not every time, never the same one twice in a row.

The conversational model starts after the sound and is told it (dialogue.py): its answer goes on from
"Что?!" instead of starting over, and the two sound as one reply. So no sound promises anything
("Сейчас...", "Дай подумать...": the answer follows at once).

Said at once, so voiced before it is needed, each kind in its tone: an npc's sounds are made once with
its own voice (the TTS service, a connection of their own: the lines of answers do not wait for them)
and kept on disk. Until they are made, the npc says none (a silent "Хм..." would only be a subtitle).
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
from dataclasses import dataclass
from pathlib import Path

from .protocol import Mood
from .tts import TtsClient, TtsError

log = logging.getLogger(__name__)

DEFAULT_CACHE = Path.home() / ".local" / "share" / "gothic-ai" / "fillers"
MIN_GAP_S = 6.0  # an npc makes such sounds at most this often


@dataclass(frozen=True)
class Kind:
    lines: tuple[str, ...]
    chance: float  # how often one is said (each time would grow tiresome)
    style: str = ""  # how the TTS says it, English; empty: the voice's usual manner
    mood: Mood | None = None  # the npc's face meanwhile


# by the behavioral model's choices (VOICE_OPTIONS in brain/reflex.py)
KINDS: dict[str, Kind] = {
    "think": Kind(("Хм...", "М-м...", "Так..."), 0.7, "say slowly and thoughtfully, pondering"),
    # news or a report: the model tells them apart badly, so sounds that fit either
    "ack": Kind(("Вот как.", "Хм.", "Так..."), 0.5),
    "surprise": Kind(("Что?!", "Как?!", "Вот как?!"), 1.0, "exclaim in sudden surprise and alarm"),
    "displeasure": Kind(("Хм!", "Тьфу!", "Ишь ты!"), 1.0, "say with irritation and contempt", "angry"),
    "doubt": Kind(("Ой ли?", "Неужто?", "Ну-ну..."), 0.9, "say skeptically, with mocking doubt"),
}


@dataclass(frozen=True)
class Sound:
    kind: str
    text: str

    @property
    def style(self) -> str:
        return KINDS[self.kind].style

    @property
    def mood(self) -> Mood | None:
        return KINDS[self.kind].mood


ALL = [Sound(kind, text) for kind, k in KINDS.items() for text in k.lines]

_QUESTION_WORDS = frozenset(
    "кто что где куда откуда когда почему зачем как сколько какой какая какое какие чей чья чьё чьи "
    "можно можешь сможешь знаешь скажи расскажи объясни".split()
)
_GREETINGS = ("привет", "здравствуй", "здорово", "доброе утро", "добрый день", "добрый вечер", "приветствую")
_WORD = re.compile(r"[а-яёa-z-]+")
_LETTERS = re.compile(r"[а-яёa-z]+", re.IGNORECASE)
# what a model's answer may start with that the npc said already ("Хм... Хм, ты прав.")
_INTERJECTION = re.compile(
    r"^\s*(?:(?:(?:хм+|м+)(?:-м+)*|э+)(?:[.,!?…]+|(?=\s))|(?:ну|так|угу|ага)[.,!?…]+)\s*", re.IGNORECASE
)


def trivial(phrase: str) -> bool:
    """A greeting or a word or two: the answer comes as quick as a sound would."""
    t = phrase.strip().lower()
    return len(_WORD.findall(t)) < 2 or any(t.startswith(g) for g in _GREETINGS)


def question(phrase: str) -> bool:
    t = phrase.strip().lower()
    words = _WORD.findall(t)
    return "?" in t or (bool(words) and words[0] in _QUESTION_WORDS) or " ли " in f" {t} "


def strip_said(line: str, said: str) -> str:
    """The first line of an answer without what the npc said at once: the same sound again ("Что?! Что?!
    Орки?") or another "Хм..." ("Хм... Хм, ты прав.")."""
    if words := _LETTERS.findall(said):
        again = r"^\s*" + r"[\s,-]*".join(map(re.escape, words)) + r"[.,!?…]+\s*"
        line = re.sub(again, "", line, count=1, flags=re.IGNORECASE)
    rest = _INTERJECTION.sub("", line, count=1)
    return rest[:1].upper() + rest[1:] if rest else rest


def trim(wav: bytes, quiet: float = 0.1, keep_s: float = 0.06, gap_s: float = 0.3) -> bytes:
    """The WAV (16-bit mono) cut to its sound: TTS ends "Так..." with half a second of silence, or with a
    breath after a pause, and the answer waits in the game's queue until the sound is over. The sound is
    the first stretch of 10 ms windows louder than `quiet` of the loudest, with pauses shorter than
    `gap_s`; `keep_s` around it is kept."""
    try:
        with wave.open(io.BytesIO(wav)) as w:
            if w.getsampwidth() != 2 or w.getnchannels() != 1:
                return wav
            rate = w.getframerate()
            pcm = array.array("h", w.readframes(w.getnframes()))
    except (wave.Error, EOFError):
        return wav
    win = max(1, rate // 100)
    levels = [max((abs(v) for v in pcm[i : i + win]), default=0) for i in range(0, len(pcm), win)]
    if not levels or max(levels) == 0:
        return wav
    limit = max(levels) * quiet
    loud = [i for i, v in enumerate(levels) if v > limit]
    first = last = loud[0]
    for i in loud[1:]:
        if (i - last - 1) * win > gap_s * rate:
            break
        last = i
    keep = int(keep_s * rate)
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm[max(0, first * win - keep) : (last + 1) * win + keep].tobytes())
    return out.getvalue()


class Fillers:
    """The npc's sounds, picked for the hero's phrase. `tts_url` None: no voices (the server runs without
    TTS), the sounds are subtitles."""

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
        self._last: dict[str, tuple[str, float]] = {}  # npc -> (text, when)
        self._warming: dict[str, asyncio.Task[None]] = {}
        self._tts: TtsClient | None = None

    def pick(self, npc: str, phrase: str, kind: str | None) -> Sound | None:
        """What the npc says at once to the hero's `phrase`: now and then a sound of `kind` (the behavioral
        model's choice, "none": nothing; None: no choice — a question gets "Хм...", anything else
        nothing). Nothing to a greeting or a word or two, whatever the choice."""
        if trivial(phrase):
            return None
        if kind is None:
            kind = "think" if question(phrase) else "none"
        k = KINDS.get(kind)
        if k is None:
            return None
        last, at = self._last.get(npc, ("", -MIN_GAP_S))
        if self.clock() - at < MIN_GAP_S or self.rng.random() >= k.chance:
            return None
        return Sound(kind, self.rng.choice([t for t in k.lines if t != last]))

    def said(self, npc: str, sound: Sound) -> None:
        self._last[npc] = (sound.text, self.clock())

    def path(self, npc: str, voice: str, sound: Sound) -> Path:
        key = hashlib.sha1(f"{npc}\n{voice}\n{sound.style}\n{sound.text}".encode()).hexdigest()[:16]
        return self.cache / f"{npc.lower()}-{key}.wav"

    def audio(self, npc: str, voice: str, sound: Sound) -> bytes | None:
        """The voiced sound (a WAV file) without the silence around it, None if not made yet."""
        try:
            return trim(self.path(npc, voice, sound).read_bytes())
        except OSError:
            return None

    def warm(self, npc: str, voice: str) -> None:
        """Make the npc's sounds in the background, those not on disk yet."""
        if self.tts_url is None or npc in self._warming:
            return
        missing = [s for s in ALL if not self.path(npc, voice, s).is_file()]
        if missing:
            self._warming[npc] = asyncio.create_task(self._make(npc, voice, missing))

    async def _make(self, npc: str, voice: str, sounds: list[Sound]) -> None:
        if self._tts is None:
            self._tts = TtsClient(self.tts_url or "")
        self.cache.mkdir(parents=True, exist_ok=True)
        for sound in sounds:
            try:
                speech = await self._tts.speak(sound.text, npc, voice, sound.style)
            except TtsError as err:
                log.warning("%s: no fillers for now: %s", npc, err)
                self._warming.pop(npc, None)  # tried again with the next conversation
                return
            p = self.path(npc, voice, sound)
            tmp = p.with_suffix(".tmp")
            tmp.write_bytes(speech.wav)
            tmp.replace(p)
        log.info("%s: %d fillers voiced", npc, len(sounds))

    async def aclose(self) -> None:
        for task in self._warming.values():
            task.cancel()
        if self._tts is not None:
            await self._tts.aclose()
