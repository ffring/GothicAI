import pytest
from pydantic import ValidationError

from gothic_ai.protocol import ActionCmd, ActionResult, Snapshot, parse_game_message
from tests.fake_game import FakeGame


def test_parses_snapshot_from_game():
    msg = parse_game_message(
        '{"type":"snapshot","t":5,"world":{"name":"NEWWORLD.ZEN","day":0,"hour":8,"minute":30},'
        '"npc":{"id":"NONE_100_XARDAS","name":"Ксардас","pos":[1,2,3],"rot":0,"hp":10,"hp_max":10,'
        '"busy":false,"action":null,"waypoint":null,"nearest_waypoint":"WP"},"nearby_waypoints":[]}'
    )
    assert isinstance(msg, Snapshot)
    assert msg.npc.name == "Ксардас"
    assert msg.player is None


def test_fake_game_snapshot_matches_protocol():
    assert isinstance(parse_game_message(_json(FakeGame().snapshot("X"))), Snapshot)


def test_action_result_ok_only_when_done():
    done = parse_game_message('{"type":"action_result","id":"a1","npc":"X","status":"done","distance":12.5}')
    timeout = parse_game_message('{"type":"action_result","id":"a2","npc":"X","status":"timeout"}')
    assert isinstance(done, ActionResult) and done.ok
    assert isinstance(timeout, ActionResult) and not timeout.ok


def test_unknown_message_type_is_rejected():
    with pytest.raises(ValidationError):
        parse_game_message('{"type":"nope"}')


def test_action_command_omits_empty_fields():
    cmd = ActionCmd(id="a1", npc="X", action="wait", args={"ms": 500})
    assert cmd.to_json() == '{"type":"action","id":"a1","npc":"X","action":"wait","args":{"ms":500}}'


def _json(obj) -> str:
    import json

    return json.dumps(obj)
