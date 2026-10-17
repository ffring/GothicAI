import pytest

from gothic_ai.scenarios import _trace_evidence_line
from gothic_ai.sightings import Sighting

FARM = "во дворе фермы Лобарта"
AT_9_07 = 1440 + 9 * 60 + 7


def arrow_observation() -> Sighting:
    return Sighting(
        "TRACE:wall:new:arrow",
        "Стена",
        AT_9_07,
        AT_9_07,
        [FARM],
        trace_kind="arrow",
    )


@pytest.mark.parametrize("hero_rows", [1, 3])
def test_trace_evidence_uses_actual_number_after_hero_and_older_wall_trace(hero_rows):
    hero = [f"- В{i}: сегодня 09:00, только что — {FARM}; без доспеха." for i in range(1, hero_rows + 1)]
    old = f"- В{hero_rows + 1}: сегодня 08:03, около часа назад — застрявшая стрела: Стена; {FARM}."
    sid = f"В{hero_rows + 2}"
    expected = f"- {sid}: сегодня 09:07, только что — застрявшая стрела: Стена; {FARM}."
    prompt = "\n".join(
        [
            "Что ты видел сам, своими глазами (не с чужих слов):",
            "Героя:",
            *hero,
            "Следы, которые ты видел:",
            old,
            expected,
        ]
    )

    assert _trace_evidence_line(prompt, arrow_observation()) == (sid, expected)


def test_trace_evidence_rejects_time_name_and_place_scattered_across_lines():
    prompt = "\n".join(
        [
            "Героя:",
            f"- В1: сегодня 09:07, только что — {FARM}; без доспеха.",
            "Следы, которые ты видел:",
            f"- В2: сегодня 08:03, около часа назад — застрявшая стрела: Стена; {FARM}.",
            "- В3: сегодня 09:07, только что — застрявшая стрела: Стена; на ферме Онара.",
            f"- В4: сегодня 09:07, только что — сдвинутый предмет: Стол; {FARM}.",
        ]
    )

    assert _trace_evidence_line(prompt, arrow_observation()) == ("", "")


def test_a_hero_sighting_at_the_same_time_and_place_is_not_trace_evidence():
    prompt = f"Героя:\n- В1: сегодня 09:07, только что — {FARM}; с оружием наголо."

    assert _trace_evidence_line(prompt, arrow_observation()) == ("", "")


def test_broken_evidence_uses_its_own_line_after_hero_and_older_debris():
    observed = Sighting(
        "TRACE:NEWWORLD:vob:19355:broken",
        "сломанный забор",
        AT_9_07,
        AT_9_07,
        [FARM],
        trace_kind="broken",
    )
    expected = f"- В3: сегодня 09:07, только что — сломанный забор; {FARM}; давность при наблюдении 1 мин."
    prompt = "\n".join(
        [
            "Героя:",
            f"- В1: сегодня 09:07, только что — {FARM}; с оружием наголо.",
            "Следы, которые ты видел:",
            f"- В2: сегодня 08:03, около часа назад — сломанный забор; {FARM}; "
            "давность при наблюдении 2 мин.",
            expected,
        ]
    )

    assert _trace_evidence_line(prompt, observed) == ("В3", expected)
