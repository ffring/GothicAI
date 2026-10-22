"""Replay the quick first words from old and current game traces, without a game or live model."""

import json
from copy import deepcopy
from importlib import import_module
from pathlib import Path

import pytest

from gothic_ai import cli
from gothic_ai.dialogue import QUICK_HINT, REACT_HINT, SOUND_SAID
from gothic_ai.llm import FakeBackend, FakeStep, LLMError, Message, TextDelta
from gothic_ai.replay import replay_first_words

NPC = "BDT_1013_BANDIT_L"
PHRASE = "Твоё оружие меня не пугает."
SITUATION = "Ты один против двоих.\nСилы: он опасен."


def _turn(*, events=(), situation=SITUATION, npc=NPC, kind="player", llm=None, turn="one"):
    return {
        "type": "turn",
        "id": turn,
        "npc": npc,
        "instructions": "persona",
        "trigger": {"kind": kind, "text": PHRASE},
        "events": list(events),
        "steps": [
            {
                "llm": llm,
                "added": [
                    {"role": "developer", "content": "An earlier note from history."},
                    {"role": "user", "content": "Раньше ты был смелее."},
                    {"role": "assistant", "content": "Ничего ты не знаешь."},
                    {"role": "developer", "content": situation},
                    {"role": "user", "content": PHRASE},
                ],
            }
        ],
    }


@pytest.mark.parametrize("events", [(), ({"kind": "first_words", "text": "Хм..."},)])
async def test_old_trace_reconstructs_history_and_removes_spoken_and_fight_tails(events):
    quick = FakeBackend("[afraid] Хм, двести золотых? Больше мне сказать нечего.", delay=0)
    turn = _turn(events=events, situation=SITUATION + SOUND_SAID.format("Ого!") + "\n" + QUICK_HINT)
    original = deepcopy(turn)

    result = await replay_first_words(quick, turn, "Recorded persona.")

    assert turn == original
    assert result["situation"] == SITUATION and result["hint"] == REACT_HINT
    assert result["runs"][0]["words"] == "Хм, двести золотых?"
    assert result["runs"][0]["outcome"] == "said"
    assert not result["runs"][0]["pure_sound"]
    assert quick.calls[0] == (
        "Recorded persona.",
        [
            Message("developer", "An earlier note from history."),
            Message("user", "Раньше ты был смелее."),
            Message("assistant", "Ничего ты не знаешь."),
            Message("developer", SITUATION + "\n" + REACT_HINT),
            Message("user", PHRASE),
        ],
        [],
    )
    assert quick.temperatures == [0.6]


async def test_new_trace_uses_exact_quick_situation_and_replacement_hint_and_temperature():
    brief = "Твоё намерение: отношение к герою — испуганное.\nСилы: он опасен."
    turn = _turn(events=[{"kind": "first_words", "text": "—", "situation": brief, "hint": "Old hint."}])
    quick = FakeBackend("Ты знаешь слишком много!", delay=0)

    result = await replay_first_words(quick, turn, "Persona.", hint="Custom hint.", temperature=0.35)

    assert quick.calls[0][1][-2] == Message("developer", brief + "\nCustom hint.")
    assert quick.temperatures == [0.35]
    assert result["situation"] == brief
    assert result["hint"] == "Custom hint." and result["temperature"] == 0.35


@pytest.mark.parametrize(
    ("reply", "previous", "words", "outcome", "pure_sound"),
    [
        (
            "Раз два три четыре пять шесть семь восемь!", "",
            "Раз два три четыре пять шесть семь восемь!", "said", False,
        ),
        (
            "Раз два три четыре пять шесть семь восемь девять!", "",
            "Раз два три четыре пять шесть семь восемь девять!", "too_long", False,
        ),
        ("[neutral] —", "", "", "nothing", False),
        ("[angry] Убирайся!", "Убирайся.", "Убирайся!", "same", False),
        ("Хммм...", "Ага!", "Хммм...", "said", True),
    ],
)
async def test_replayed_outcomes_match_game_without_a_pure_sound_cooldown(
    reply, previous, words, outcome, pure_sound
):
    result = await replay_first_words(FakeBackend(reply, delay=0), _turn(), "Persona.", previous=[previous])

    run = result["runs"][0]
    assert (run["words"], run["outcome"], run["pure_sound"]) == (words, outcome, pure_sound)
    assert run["elapsed_ms"] >= 0


async def test_late_reaction_is_reported(monkeypatch):
    monkeypatch.setattr(import_module("gothic_ai.replay"), "REACTION_S", 0.001)
    quick = FakeBackend("Ты знаешь слишком много!", first=0.05, delay=0)

    result = await replay_first_words(quick, _turn(), "Persona.")

    assert result["runs"][0]["outcome"] == "late"
    assert result["runs"][0]["words"] == ""


async def test_model_error_is_reported_with_its_partial_text():
    class FailingBackend(FakeBackend):
        async def stream(self, **kwargs):
            yield TextDelta("Гм")
            raise LLMError("connection lost")

    result = await replay_first_words(FailingBackend(), _turn(), "Persona.")

    assert result["runs"][0]["outcome"] == "error"
    assert result["runs"][0]["text"] == "Гм"
    assert result["runs"][0]["error"] == "connection lost"


@pytest.mark.parametrize(
    ("flags", "quick_env", "expected_url"),
    [
        ([], "http://quick:9000/v1", "http://quick:9000/v1"),
        ([], None, "http://gpu:47834/v1"),
        (["--quick-url", "http://override:9000/v1"], "http://quick:9000/v1", "http://override:9000/v1"),
    ],
)
def test_cli_filters_turns_runs_independently_and_prints_and_writes_results(
    monkeypatch, tmp_path, capsys, flags, quick_env, expected_url
):
    trace = tmp_path / "traces.jsonl"
    out = tmp_path / "results.jsonl"
    hint = tmp_path / "hint.txt"
    hint.write_text("Alternative hint.\n", encoding="utf-8")
    rows = [
        {"type": "instructions", "hash": "persona", "text": "Recorded persona."},
        _turn(turn="one"),
        _turn(turn="two"),
        _turn(turn="three"),
        _turn(llm="quick", turn="fight"),
        _turn(kind="incident", turn="incident"),
        _turn(npc="ANOTHER_NPC", turn="another"),
        {"type": "turn", "id": "empty", "trigger": {"kind": "player"}, "steps": []},
    ]
    rows[1]["trigger"]["text"] = "А" * 100
    trace.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    quick = FakeBackend(
        script=[
            FakeStep("Хм..."), FakeStep("Ничего себе!"),
            FakeStep("Ха!"), FakeStep("Ничего себе!"),
            FakeStep("Ха!"), FakeStep("Ты шутишь!"),
        ],
        delay=0,
    )
    urls = []

    def make_quick(url):
        urls.append(url)
        return quick

    monkeypatch.setattr(cli, "make_quick", make_quick)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    settings = tmp_path / ".config/gothic-ai/settings.toml"
    settings.parent.mkdir(parents=True)
    settings.write_text("[llm]\nquick_url = 'http://settings:9000/v1'\n", encoding="utf-8")
    monkeypatch.setenv("GOTHIC_DECIDER_URL", "ws://gpu:47832")
    if quick_env:
        monkeypatch.setenv("GOTHIC_QUICK_LLM_URL", quick_env)
    else:
        monkeypatch.delenv("GOTHIC_QUICK_LLM_URL", raising=False)
    monkeypatch.setattr("sys.argv", [
        "gothic-ai", "replay-first-words", str(trace), "--hint-file", str(hint), "--runs", "2",
        "--temperature", "0.7", "--npc", NPC.lower(), "--out", str(out), *flags,
    ])

    with pytest.raises(SystemExit) as exit:
        cli.main()

    assert exit.value.code == 0
    assert urls == [expected_url]
    assert len(quick.calls) == 6 and quick.temperatures == [0.7] * 6
    assert all(c[0] == "Recorded persona." for c in quick.calls)
    assert all(c[1][-2].content.endswith("\nAlternative hint.") for c in quick.calls)
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 3
    assert lines[0].split(" | ")[1] == "А" * 80
    assert "pure_sound" in lines[0] and "same" in lines[1]
    results = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert [r["turn"] for r in results] == ["one", "two", "three"]
    assert [r["outcome"] for r in results[1]["runs"]] == ["said", "same"]
    assert [r["outcome"] for r in results[2]["runs"]] == ["same", "said"]


@pytest.mark.parametrize(
    ("setting", "expected_url"),
    [
        ("quick_url = 'http://configured:9000/v1'", "http://configured:9000/v1"),
        ("decider_url = 'ws://configured:47832'", "http://configured:47834/v1"),
    ],
)
def test_cli_reads_quick_address_from_launcher_settings(monkeypatch, tmp_path, setting, expected_url):
    monkeypatch.delenv("GOTHIC_QUICK_LLM_URL", raising=False)
    monkeypatch.delenv("GOTHIC_DECIDER_URL", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    settings = tmp_path / ".config/gothic-ai/settings.toml"
    settings.parent.mkdir(parents=True)
    settings.write_text("[llm]\n" + setting + "\n", encoding="utf-8")
    trace = tmp_path / "traces.jsonl"
    trace.write_text(
        json.dumps({"type": "instructions", "hash": "persona", "text": "Persona."}) + "\n"
        + json.dumps(_turn()),
        encoding="utf-8",
    )
    quick = FakeBackend("Ты шутишь!", delay=0)
    urls = []

    def make_quick(url):
        urls.append(url)
        return quick

    monkeypatch.setattr(cli, "make_quick", make_quick)
    monkeypatch.setattr("sys.argv", ["gothic-ai", "replay-first-words", str(trace)])
    with pytest.raises(SystemExit) as exit:
        cli.main()
    assert exit.value.code == 0
    assert urls == [expected_url] and len(quick.calls) == 1


@pytest.mark.parametrize("flags", [[], ["--runs", "0"]])
def test_cli_reports_missing_quick_address_or_invalid_run_count(monkeypatch, tmp_path, capsys, flags):
    monkeypatch.delenv("GOTHIC_QUICK_LLM_URL", raising=False)
    monkeypatch.delenv("GOTHIC_DECIDER_URL", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("sys.argv", ["gothic-ai", "replay-first-words", "missing.jsonl", *flags])
    with pytest.raises(SystemExit) as exit:
        cli.main()
    assert exit.value.code == 2
    assert ("--runs" if flags else "--quick-url") in capsys.readouterr().err
