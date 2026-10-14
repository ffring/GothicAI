import pytest
from pydantic import ValidationError

from gothic_ai.protocol import (
    PROTOCOL_VERSION,
    ActionCmd,
    ActionResult,
    BeastCmd,
    BeastEvent,
    Beasts,
    DebugCmd,
    GraphPatchCmd,
    HushCmd,
    NpcActCmd,
    NpcIdle,
    NpcViews,
    SayCmd,
    Snapshot,
    StoryProgramCmd,
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


def test_v22_things_lying_about():
    assert PROTOCOL_VERSION >= 22
    seen = parse_game_message(
        _json(
            {
                "type": "npc_views",
                "t": 1000,
                "views": [
                    {
                        "npc": "BAU_4300_ADDON_CAVALORN",
                        "name": "Кавалорн",
                        "items": [
                            {
                                "item": "ITWR_SATURASFIRSTMESSAGE_ADDON_SEALED",
                                "name": "Запечатанное послание",
                                "count": 1,
                                "distance": 120,
                                "hero_cm": 80,
                                "from_hero": "ahead",
                            }
                        ],
                    }
                ],
            }
        )
    )
    assert isinstance(seen, NpcViews)
    thing = seen.views[0].items[0]
    assert (thing.item, thing.hero_cm, thing.from_hero) == (
        "ITWR_SATURASFIRSTMESSAGE_ADDON_SEALED",
        80,
        "ahead",
    )
    assert (
        NpcViews.model_validate({"type": "npc_views", "t": 1, "views": [{"npc": "X", "name": "x"}]})
        .views[0]
        .items
        == []
    )
    assert ActionCmd(id="a1", npc="X", action="take_item", args={"item": "ITPL_BEET"}).action == "take_item"


def test_v21_wolves_hear_the_hero():
    seen = parse_game_message(
        _json(
            {
                "type": "beasts",
                "t": 1000,
                "hero_weapon": "melee",
                "wolves": [
                    {
                        "id": "WOLF#3",
                        "name": "Волк",
                        "distance": 950,
                        "state": "threaten",
                        "hp": 60,
                        "hp_max": 60,
                    },
                    {
                        "id": "YWOLF#4",
                        "name": "Молодой волк",
                        "distance": 400,
                        "state": "calm",
                        "sitting": True,
                        "focus": True,
                        "hit_ms": 120000,
                        "tolerant": True,
                    },
                ],
            }
        )
    )
    assert isinstance(seen, Beasts) and seen.hero_weapon == "melee"
    assert [(w.id, w.state, w.sitting) for w in seen.wolves] == [
        ("WOLF#3", "threaten", False),
        ("YWOLF#4", "calm", True),
    ]
    ended = parse_game_message('{"type":"beast_event","npc":"WOLF#3","kind":"calm_broken","detail":"close"}')
    assert isinstance(ended, BeastEvent) and (ended.kind, ended.detail) == ("calm_broken", "close")
    calm = BeastCmd(req="b1", npc="WOLF#3", do="calm", ms=120000, sit=True)
    assert calm.to_json() == '{"type":"beast","req":"b1","npc":"WOLF#3","do":"calm","ms":120000,"sit":true}'
    assert BeastCmd(req="b2", npc="WOLF#3", do="attack").to_json() == (
        '{"type":"beast","req":"b2","npc":"WOLF#3","do":"attack"}'
    )
    hp = DebugCmd(req="r1", cmd="player_hp", hp=5000)
    assert hp.to_json() == '{"type":"debug","req":"r1","cmd":"player_hp","slot":0,"hp":5000}'


def test_v23_programs_in_the_story_graph():
    assert PROTOCOL_VERSION >= 23
    ask = StoryProgramCmd(req="s1", node="DIA_LOBART_RUEBENRUNNING")
    assert ask.to_json() == (
        '{"type":"story_program","req":"s1","node":"DIA_LOBART_RUEBENRUNNING","part":"information"}'
    )
    with pytest.raises(ValidationError):
        StoryProgramCmd(req="s2", node="X", part="choice")
    steps = [{"do": "say", "speaker": "self", "text": "Спасибо."}, {"do": "return"}]
    patch = GraphPatchCmd(ops=[{"op": "program", "node": "DIA_X", "part": "condition", "steps": steps}])
    assert '"steps":[{"do":"say","speaker":"self","text":"Спасибо."},{"do":"return"}]' in patch.to_json()


def test_v24_choices_in_the_directors_dialogs():
    assert PROTOCOL_VERSION == 24
    pick = DebugCmd(req="r1", cmd="menu_pick", text="Золото")
    assert pick.to_json() == '{"type":"debug","req":"r1","cmd":"menu_pick","slot":0,"text":"Золото"}'
    assert DebugCmd(req="r2", cmd="choices", npc="X", node="DIR_X").to_json() == (
        '{"type":"debug","req":"r2","cmd":"choices","slot":0,"npc":"X","node":"DIR_X"}'
    )
    for cmd in ("talk", "menu"):
        assert DebugCmd(req="r3", cmd=cmd).cmd == cmd
