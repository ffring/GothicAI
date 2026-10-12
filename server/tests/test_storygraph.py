import json
import os
import subprocess
from pathlib import Path

import pytest

from gothic_ai.gamedata.storygraph import is_known, load, render, variables

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "OpenGothic" / "ai_bridge" / "storygraph" / "build" / "storygraph"
GAME_DIR = Path(os.environ.get("GOTHIC_DIR", "/Volumes/shared/projects/Gothic II"))
DAT = GAME_DIR / "_work" / "Data" / "Scripts" / "_compiled" / "GOTHIC.DAT"
# original-format saves (ZenGin), one folder each; see docs/story-graph.md, stage 2
SAVES = Path(os.environ.get("GOTHIC_SAVES", Path.home() / ".local/share/gothic-ai/saves/original"))

KAPITEL_LT_3 = ["<", ["var", "KAPITEL"], ["int", 3]]
RUEBEN_RUNNING = ["==", ["var", "MIS_LOBART_RUEBEN"], ["int", 1, "LOG_RUNNING"]]
HAS_BEET = [">=", ["call", "NPC_HASITEMS", ["inst", "OTHER"], ["sym", "ITPL_BEET"]], ["int", 1]]
KNOWS_KLEIDUNG = ["call", "NPC_KNOWSINFO", ["inst", "OTHER"], ["sym", "DIA_LOBART_KLEIDUNG"]]
HAS_20 = [">=", ["call", "NPC_HASITEMS", ["inst", "OTHER"], ["sym", "ITPL_BEET"]], ["int", 20]]

# A small piece of what storygraph writes for Lobart's turnip quest.
GRAPH = {
    "version": 1,
    "nodes": [
        {
            "id": "DIA_LOBART_WORKNOW",
            "kind": "dialog",
            "npc": "BAU_950_LOBART",
            "description": "Я ищу работу.",
            "nr": 2,
            "important": False,
            "permanent": False,
            "trade": False,
            "condition": ["&&", KNOWS_KLEIDUNG, KAPITEL_LT_3],
            "lifted": True,
            "reads": ["KAPITEL"],
            "knows": ["DIA_LOBART_KLEIDUNG"],
            "writes": [],
            "effects": [
                {
                    "do": "line",
                    "id": "DIA_LOBART_WORKNOW_15_00",
                    "text": "Я ищу работу.",
                    "speaker": ["inst", "OTHER"],
                    "listener": ["inst", "SELF"],
                },
                {"do": "choice", "node": "DIA_LOBART_WORKNOW_OK", "text": "Хорошо..."},
            ],
        },
        {
            "id": "DIA_LOBART_WORKNOW_OK",
            "kind": "choice",
            "npc": "BAU_950_LOBART",
            "description": "Хорошо...",
            "parents": ["DIA_LOBART_WORKNOW"],
            "writes": ["MIS_LOBART_RUEBEN"],
            "effects": [
                {
                    "do": "journal_topic",
                    "topic": "TOPIC_RUEBEN",
                    "title": "Убрать поле репы",
                    "section": ["int", 0, "LOG_MISSION"],
                },
                {"do": "set", "var": "MIS_LOBART_RUEBEN", "value": ["int", 1, "LOG_RUNNING"]},
            ],
        },
        {
            "id": "DIA_LOBART_RUEBENRUNNING",
            "kind": "dialog",
            "npc": "BAU_950_LOBART",
            "description": "Вот твоя репа!",
            "nr": 1,
            "important": False,
            "permanent": True,
            "trade": False,
            "condition": ["&&", KAPITEL_LT_3, RUEBEN_RUNNING, HAS_BEET],
            "lifted": True,
            "reads": ["KAPITEL", "MIS_LOBART_RUEBEN"],
            "knows": [],
            "writes": ["MIS_LOBART_RUEBEN"],
            "effects": [
                {"do": "set", "var": "MIS_LOBART_RUEBEN", "value": ["int", 2, "LOG_SUCCESS"], "when": HAS_20},
                {"do": "xp", "amount": ["int", 50, "XP_LOBARTHOLRUEBEN"], "when": HAS_20},
            ],
        },
    ],
    "functions": {},
    "instances": {"HILDA": "BAU_951_HILDA"},
    "vars": {
        "MIS_LOBART_RUEBEN": {
            "readers": ["DIA_LOBART_RUEBENRUNNING"],
            "writers": ["DIA_LOBART_RUEBENRUNNING", "DIA_LOBART_WORKNOW_OK"],
            "others": [],
        },
    },
    "quests": [
        {
            "var": "MIS_LOBART_RUEBEN",
            "topics": [{"topic": "TOPIC_RUEBEN", "title": "Убрать поле репы"}],
            "running": ["DIA_LOBART_WORKNOW_OK"],
            "success": ["DIA_LOBART_RUEBENRUNNING"],
            "failed": [],
            "obsolete": [],
            "other": [],
        },
    ],
}


@pytest.fixture
def graph(tmp_path):
    path = tmp_path / "story.json"
    path.write_text(json.dumps(GRAPH, ensure_ascii=False), encoding="utf-8")
    return load(path)


def test_quest_and_its_nodes(graph):
    quest = graph.quest("mis_lobart_rueben")
    assert quest.title == "Убрать поле репы"
    assert quest.running == {"DIA_LOBART_WORKNOW_OK"} and quest.success == {"DIA_LOBART_RUEBENRUNNING"}
    assert graph.quests_of("BAU_950_LOBART") == [quest]

    offer = graph.node(next(iter(quest.running)))
    assert offer.is_choice and offer.parents == ["DIA_LOBART_WORKNOW"]
    work = graph.node("DIA_LOBART_WORKNOW")
    assert work.choices() == ["DIA_LOBART_WORKNOW_OK"]
    assert work.lines() == [("other", "DIA_LOBART_WORKNOW_15_00", "Я ищу работу.")]
    by_nr = [n.id for n in graph.dialogs_of("bau_950_lobart")]
    assert by_nr == ["DIA_LOBART_RUEBENRUNNING", "DIA_LOBART_WORKNOW"]
    assert graph.npc("Hilda") == "BAU_951_HILDA" and graph.npc("BAU_950_LOBART") == "BAU_950_LOBART"


def test_expressions(graph):
    done = graph.node("DIA_LOBART_RUEBENRUNNING")
    assert render(done.condition) == (
        "(KAPITEL < 3) && (MIS_LOBART_RUEBEN == LOG_RUNNING) && (NPC_HASITEMS(other, ITPL_BEET) >= 1)"
    )
    assert variables(done.condition) == {"KAPITEL", "MIS_LOBART_RUEBEN"}
    assert is_known(done.condition) and not is_known(["&&", KAPITEL_LT_3, ["unknown", "guard too complex"]])
    assert render(done.effects[1]["when"]) == "NPC_HASITEMS(other, ITPL_BEET) >= 20"
    defeated = ["field", ["inst", "ROD"], "AIVAR", 47, "AIV_DEFEATEDBYPLAYER"]
    cases = ["cases", ["==", defeated, ["int", 1, "TRUE"]], ["int", 1]]
    assert render(cases) == "case (ROD.aivar[AIV_DEFEATEDBYPLAYER] == TRUE) -> 1; else 0"


def test_other_versions_are_refused(tmp_path):
    path = tmp_path / "story.json"
    path.write_text(json.dumps({**GRAPH, "version": 2}), encoding="utf-8")
    with pytest.raises(ValueError, match="version 2"):
        load(path)


@pytest.mark.skipif(not (TOOL.exists() and DAT.exists()), reason="needs the storygraph tool and the game")
def test_the_real_game(tmp_path):
    out = tmp_path / "story.json"
    subprocess.run([str(TOOL), str(DAT), "--json", str(out)], check=True, capture_output=True)
    graph = load(out)

    dialogs = [n for n in graph.nodes.values() if not n.is_choice]
    assert len(dialogs) > 4000
    assert sum(n.lifted for n in dialogs) / len(dialogs) >= 0.95

    quest = graph.quest("MIS_LOBART_RUEBEN")
    assert quest.topics == {"TOPIC_RUEBEN": "Убрать поле репы"}
    assert quest.running == {"DIA_LOBART_WORKNOW_OK", "DIA_LOBART_WORKNOW_WANNAFOOLME"}
    assert quest.success == {"DIA_LOBART_RUEBENRUNNING"}
    done = graph.node("DIA_LOBART_RUEBENRUNNING")
    assert render(done.condition) == (
        "(KAPITEL < 3) && (MIS_LOBART_RUEBEN == LOG_RUNNING) && (NPC_HASITEMS(other, ITPL_BEET) >= 1)"
    )
    success = next(e for e in done.effects if e["do"] == "set" and e["var"] == "MIS_LOBART_RUEBEN")
    assert render(success["when"]) == "NPC_HASITEMS(other, ITPL_BEET) >= 20"
    hilda = next(e for e in done.effects if e["do"] == "set" and e["var"] == "MIS_LOBART_RUEBENTOHILDA")
    assert render(hilda["when"]) == "!NPC_ISDEAD(HILDA) && (NPC_HASITEMS(other, ITPL_BEET) >= 20)"
    assert graph.npc("HILDA") == "BAU_951_HILDA"
    # Franco's death makes two quests obsolete in ZS_Dead, outside any dialog
    assert "ZS_DEAD" in graph.vars["MIS_HLPLOGAN"].others
    # the gate guard stores AIV_PASSGATE and then checks it: the check sees the store (found by the VM check)
    assert "PAL_205_SCHONMALREINGELASSEN" in variables(graph.node("DIA_PAL_205_TORWACHE_FIRSTWARN").condition)


def _run(*args):
    return subprocess.run([str(TOOL), str(DAT), *args], capture_output=True, text=True, errors="replace")


@pytest.mark.skipif(not (TOOL.exists() and DAT.exists()), reason="needs the storygraph tool and the game")
def test_conditions_match_the_vm_in_random_states():
    r = _run("--fuzz", "200")
    assert r.returncode == 0, r.stdout[-3000:]
    assert "DIFFER in 0 dialogs" in r.stdout


def _saves():
    return sorted(p for p in SAVES.iterdir() if (p / "SAVEDAT.SAV").exists()) if SAVES.is_dir() else []


@pytest.mark.skipif(not (TOOL.exists() and DAT.exists() and _saves()), reason="needs the tool, game, saves")
def test_conditions_match_the_vm_on_original_saves():
    r = _run(*[a for s in _saves() for a in ("--check", str(s))])
    assert r.returncode == 0, r.stdout[-3000:]
    assert ", DIFFER 0;" in r.stdout.splitlines()[-1]
