"""CLI defaults start the director only for ordinary play."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gothic_ai import cli


@pytest.mark.parametrize(
    ("flags", "running"),
    [
        ([], True),
        (["--no-showrunner"], False),
        (["--scenario", "m0"], False),
        (["--scenario", "showrunner"], False),
        (["--scenario", "showrunner_live"], False),
    ],
)
def test_serve_arguments_control_showrunner_task(monkeypatch, flags, running):
    showrunner = SimpleNamespace(run=AsyncMock())

    async def serve(args):
        task = cli._start_showrunner(args, showrunner)
        assert isinstance(task, asyncio.Task) is running
        if task is not None:
            await task
        assert showrunner.run.await_count == int(running)
        return 0

    monkeypatch.setattr(cli, "serve", serve)
    monkeypatch.setattr("sys.argv", ["gothic-ai", "serve", *flags])
    with pytest.raises(SystemExit) as exit:
        cli.main()
    assert exit.value.code == 0


def test_showrunner_opt_in_flag_is_removed(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["gothic-ai", "serve", "--showrunner"])
    with pytest.raises(SystemExit) as exit:
        cli.main()
    assert exit.value.code == 2
    assert "unrecognized arguments: --showrunner" in capsys.readouterr().err


@pytest.mark.parametrize("flags", [[], ["--scenario", "questlog"], ["--no-questlog"]])
def test_questlog_is_enabled_by_default_including_scenarios(monkeypatch, flags):
    async def serve(args):
        assert args.no_questlog == ("--no-questlog" in flags)
        return 0

    monkeypatch.setattr(cli, "serve", serve)
    monkeypatch.setattr("sys.argv", ["gothic-ai", "serve", *flags])
    with pytest.raises(SystemExit) as exit:
        cli.main()
    assert exit.value.code == 0
