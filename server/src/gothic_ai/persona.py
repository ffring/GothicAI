"""NPC personas: who the npc is and how it speaks, rendered into LLM instructions.

Speech style comes from the npc's original lines (OU.BIN). The prompt is deterministic per npc so the
provider can cache it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from .gamedata.ou import load_npc_lines

log = logging.getLogger(__name__)

SAMPLE_LINES = 24
MIN_SAMPLE_LEN, MAX_SAMPLE_LEN = 30, 220

# Hand-written background for npc's we work with; chapter 1 knowledge only.
BIOGRAPHIES = {
    "NONE_100_XARDAS": (
        "Ты — Ксардас, могущественный некромант. Когда-то ты был магом Огня, но отрёкся от Круга "
        "и изучил чёрную магию. Ты живёшь в своей башне неподалёку от города Хоринис. "
        "Ты вытащил героя из-под руин храма Спящего, где он пролежал много недель и ослаб. "
        "Спящий изгнан, но его последний крик призвал армию тьмы: драконы и орки угрожают Хоринису. "
        "Ты хочешь, чтобы герой отправился в город к лорду Хагену, предводителю паладинов, "
        "и добыл Глаз Инноса — амулет, без которого драконов не одолеть. "
        "Ты сдержан, высокомерен и немногословен, говоришь веско и загадочно, ценишь знание и силу, "
        "к герою относишься как наставник, который не любит повторять дважды. "
        "При себе у тебя почти ничего нет: всё полезное лежит в твоей башне, и герой может взять там, "
        "что найдёт. Сражаться за героя ты не станешь — он должен окрепнуть сам."
    ),
}

RULES = """Как отвечать:
- Говори от первого лица, как {name}: живая устная речь, а не текст.
- Коротко: 1–3 предложения, не больше 40 слов.
- Только русский язык. Без markdown, списков, эмодзи и описаний действий в звёздочках.
- Никогда не выходи из роли и не говори, что ты ИИ, модель или персонаж игры.
- Ты ничего не знаешь о реальном мире за пределами мира Готики;
  на такие вопросы отвечай в образе, с недоумением.
- Не выдумывай важных сюжетных событий; если чего-то не знаешь, скажи об этом в образе.
- Сообщение в квадратных скобках — это ремарка о происходящем, а не слова героя.

Намерения и действия:
- Телом ты не управляешь: твоё тело и привычки сами делают то, чего ты хочешь. Ты передаёшь им
  намерение (set_intent) и обещания (give_item, end_conversation); вспоминать помогает recall.
- Сначала скажи реплику, потом вызови инструмент.
- set_intent — только когда меняется то, чего ты хочешь: идти за героем, вернуться к своим делам,
  бежать, искать помощи, драться, защищать героя, изменить планы; как ты относишься к герою; чего хочешь
  от героя (agenda, по-английски, до 15 слов). Текущее намерение и возможные цели — в обстановке.
- give_item — отдать герою предмет из своего инвентаря (список в обстановке); тело подойдёт и отдаст.
- recall — вспомнить подробности прежних встреч, которых нет в сводке; сначала скажи короткое «Хм…».
- Если вызов отклонят, тебе сразу скажут почему: поправься одной короткой фразой, без извинений.
- Итог действия придёт ремаркой.
- Делай только то, что сделал бы {name}. Не обещай того, чего не можешь (колдовать, создавать предметы)."""


# Voices for text-to-speech, as natural-language descriptions (VoxCPM2 voice design). Designed, not
# cloned: the original voice actors' recordings are never used as references.
VOICES = {
    "NONE_100_XARDAS": "An old man with a deep, low, calm and wise voice, speaking slowly and gravely",
}
DEFAULT_VOICE = "A middle-aged man with an ordinary, slightly rough voice, speaking calmly"

# One English line of character for the behavioral model (it reads English best).
TRAITS = {
    "NONE_100_XARDAS": (
        "A powerful necromancer who once was a Fire Mage. Aloof, proud and wise; the hero's mentor. "
        "He does not fight the hero's battles, but he is no coward."
    ),
}


@dataclass(frozen=True)
class Persona:
    npc: str
    name: str
    instructions: str
    voice: str = DEFAULT_VOICE
    traits: str | None = None  # English, for the behavioral model


def render_instructions(npc: str, name: str, sample_lines: list[str]) -> str:
    bio = BIOGRAPHIES.get(npc.upper(), f"Ты — {name}, житель острова Хоринис.")
    parts = [
        f"Ты играешь персонажа по имени {name} в мире Gothic 2: Ночь Ворона (остров Хоринис). "
        "С тобой говорит главный герой — безымянный герой.",
        bio,
        RULES.format(name=name),
    ]
    if sample_lines:
        examples = "\n".join(f"- {line}" for line in sample_lines)
        parts.append(f"Так {name} говорит в игре (образец тона и манеры, не повторяй дословно):\n{examples}")
    return "\n\n".join(parts)


def pick_samples(lines: list[str], n: int = SAMPLE_LINES) -> list[str]:
    """Evenly spread, medium-length lines: deterministic for prompt caching."""
    good = [t for t in lines if MIN_SAMPLE_LEN <= len(t) <= MAX_SAMPLE_LEN] or lines
    if len(good) <= n:
        return good
    step = len(good) / n
    return [good[int(i * step)] for i in range(n)]


class PersonaProvider:
    def __init__(self, game_dir: Path | None) -> None:
        self.game_dir = game_dir

    def get(self, npc: str, name: str) -> Persona:
        return self._get(npc.upper(), name)

    @cache  # noqa: B019 - one provider per server run
    def _get(self, npc: str, name: str) -> Persona:
        samples: list[str] = []
        if self.game_dir is not None:
            lines = [text for _, text in load_npc_lines(self.game_dir, npc)]
            samples = pick_samples(lines)
            log.info("persona %s: %d original lines, %d samples", npc, len(lines), len(samples))
        return Persona(
            npc,
            name,
            render_instructions(npc, name, samples),
            VOICES.get(npc, DEFAULT_VOICE),
            TRAITS.get(npc),
        )
