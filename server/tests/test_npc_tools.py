from gothic_ai.npc_tools import GESTURES, TOOLS, render_situation
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
    gesture = next(t for t in TOOLS if t.name == "gesture")
    assert gesture.parameters["properties"]["name"]["enum"] == list(GESTURES)
