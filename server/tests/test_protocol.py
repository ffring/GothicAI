import pytest
from pydantic import ValidationError

from gothic_ai.protocol import (
    PROTOCOL_VERSION,
    ActionCmd,
    ActionResult,
    DebugCmd,
    HushCmd,
    NpcActCmd,
    NpcIdle,
    SayCmd,
    Snapshot,
    parse_game_message,
)
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


def test_v15_npc_to_npc():
    assert PROTOCOL_VERSION == 18
    say = SayCmd(
        npc="BAU_950_LOBART", line="AIB_1", text="Слыхала?", stream=True, rate=24000, to="BAU_951_HILDA"
    )
    assert '"to":"BAU_951_HILDA"' in say.to_json()
    assert '"to"' not in SayCmd(npc="X", line="AIB_2", text="Эй").to_json()  # to the hero, as before
    hush = HushCmd(npc="BAU_950_LOBART", line="AIB_1", said="Слыхала?")
    assert hush.to_json() == '{"type":"hush","npc":"BAU_950_LOBART","said":"Слыхала?","line":"AIB_1"}'
    give = NpcActCmd(id="n1", npc="X", action="give", args={"npc": "Y", "item": "ITFO_BREAD", "count": 1})
    assert '"action":"give"' in give.to_json()
    goto = DebugCmd(
        req="r1", cmd="npc_goto", npc="BAU_951_HILDA", waypoint="NW_FARM1_PATH_01", face="BAU_950_LOBART"
    )
    assert goto.to_json() == (
        '{"type":"debug","req":"r1","cmd":"npc_goto","slot":0,"waypoint":"NW_FARM1_PATH_01",'
        '"npc":"BAU_951_HILDA","face":"BAU_950_LOBART"}'
    )

    failed = parse_game_message(
        '{"type":"npc_idle","npc":"X","act":"n1","reason":"failed","detail":"could not reach the target"}'
    )
    arrived = parse_game_message('{"type":"npc_idle","npc":"X","act":"n2","reason":"arrived"}')
    assert isinstance(failed, NpcIdle) and (failed.reason, failed.detail) == (
        "failed",
        "could not reach the target",
    )
    assert isinstance(arrived, NpcIdle) and (arrived.reason, arrived.detail) == ("arrived", None)


def _json(obj) -> str:
    import json

    return json.dumps(obj)
