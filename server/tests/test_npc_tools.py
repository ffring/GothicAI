from gothic_ai.executor import ATTITUDES, GOALS, Intent
from gothic_ai.npc_tools import TOOLS, render_situation
from gothic_ai.protocol import Snapshot
from tests.fake_game import FakeGame


def test_situation_lists_what_the_npc_perceives():
    snap = Snapshot.model_validate(FakeGame().snapshot("NONE_100_XARDAS"))
    text = render_situation(snap)
    assert "день 1, 08:00" in text
    assert "Гильдия героя: без гильдии" in text and "Оружие: Палка" in text
    assert "Эликсир лечения (ITPO_HEALTH_01) ×3" in text
    assert "Двуручник" not in text  # equipped items are not offered
    assert "Диего (6 м)" in text


def test_situation_without_snapshot():
    assert render_situation(None) == "[Обстановка неизвестна]"


def test_tools_are_strict_json_schemas():
    for tool in TOOLS:
        params = tool.parameters
        assert params["type"] == "object" and params["additionalProperties"] is False
        assert sorted(params["required"]) == sorted(params["properties"])
    intent = next(t for t in TOOLS if t.name == "set_intent").parameters["properties"]
    assert intent["goal"]["enum"] == list(GOALS) and intent["attitude"]["enum"] == list(ATTITUDES)
    assert intent["target"]["type"] == ["string", "null"]


def test_situation_shows_the_intent_and_its_possible_targets():
    snap = Snapshot.model_validate(FakeGame().snapshot("NONE_100_XARDAS"))
    text = render_situation(
        snap, Intent("follow_hero", attitude="friendly", agenda="wants the hero to see Hagen")
    )
    assert "Твоё намерение: цель — идти за героем; отношение к герою — дружеское" in text
    assert "agenda — wants the hero to see Hagen" in text
    assert "PLAYER (герой)" in text and "PC_THIEF (Диего, 6 м)" in text and "NW_XARDAS_TOWER_04" in text
