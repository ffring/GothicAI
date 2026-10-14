from dataclasses import dataclass, field

from gothic_ai.brain.situation import NpcContext, SituationBuilder
from gothic_ai.dialogue import DialogueManager
from gothic_ai.executor import Intent, check_intent
from gothic_ai.llm import ToolCall, ToolResult
from gothic_ai.llm.fake import FakeBackend, FakeStep
from gothic_ai.npc_tools import render_situation
from gothic_ai.perception import RECENT_S, PerceptionDesk
from gothic_ai.persona import PersonaProvider
from gothic_ai.protocol import NpcView
from gothic_ai.protocol import Snapshot as NpcSnapshot
from tests.fake_game import FakeGame, connected
from tests.fakes import Clock
from tests.test_dialogue import _until

LOBART, HILDA, MALETH = "BAU_950_LOBART", "BAU_951_HILDA", "BAU_954_MALETH"
GIL_OUT, GIL_SLD = 12, 7


@dataclass
class Social:
    """Who knows whom (social.py's shape): Lobart meets Hilda and Maleth on the farm."""

    ties: dict[str, dict[str, object]] = field(
        default_factory=lambda: {LOBART: {HILDA: object(), MALETH: object()}}
    )
    guilds: dict[str, int] = field(default_factory=lambda: {LOBART: GIL_OUT, HILDA: GIL_OUT})


def armor(item: str, name: str) -> dict:
    return {"item": item, "name": name}


def look(id: str, name: str, metres: float, **extra) -> dict:
    return {"id": id, "name": name, "distance": metres * 100, **extra}


def lobarts_view(hero: dict | None = None, *sees: dict) -> dict:
    hero = hero or look(
        "PLAYER", "Я", 4, seen=True, faces_you=True, armor=armor("ITAR_MIL_L", "Лёгкий доспех ополчения")
    )
    return {
        "npc": LOBART,
        "name": "Лобарт",
        "waypoint": "NW_FARM1_OUT_01",
        "indoors": False,
        "state": "ZS_STAND_ARMSCROSSED",
        "hero": hero,
        "sees": list(sees)
        or [
            look(HILDA, "Хильда", 6, guild=GIL_OUT, state="ZS_COOK_CAULDRON"),
            look(
                "SLD_800_X", "Наёмник", 15, guild=GIL_SLD, weapon="melee", armor=armor("ITAR_SLD_L", "Доспех")
            ),
            look("WOLF", "Волк", 18, kind="monster", guild=36, moving=True, body="run"),
            look(MALETH, "Малет", 10, kind="body", guild=GIL_OUT, body="dead"),
        ],
    }


def desk(views: list[dict], knows_hero: bool = False, clock: Clock | None = None) -> PerceptionDesk:
    class Link:
        listeners: list = []

    d = PerceptionDesk(Link(), social=Social, knows_hero=lambda npc: knows_hero, clock=clock or Clock())  # type: ignore[arg-type]
    for v in views:
        d._store(NpcView.model_validate(v))
    return d


def test_lobart_sees_whom_it_knows_by_name_and_strangers_by_their_look():
    sight = desk([lobarts_view()]).sight(LOBART)
    assert sight is not None
    assert sight.place == "Ты под открытым небом, у точки NW_FARM1_OUT_01. Ты стоишь, скрестив руки."
    # the hero in militia armor, to one that does not know him, is a militiaman
    assert sight.hero == (
        "Герой в 4 м, смотрит на тебя: ты его не знаешь, по виду ополченец; на нём: Лёгкий доспех ополчения."
    )
    assert sight.people == (
        "Видишь: Хильда (id BAU_951_HILDA; 6 м), готовит у котла; незнакомец, по виду наёмник "
        "(id SLD_800_X; 15 м), оружие наголо; Волк (id WOLF; 18 м), бежит; "
        "тело: Малет (id BAU_954_MALETH; 10 м)."
    )


def test_the_hero_it_knows_and_the_hero_it_only_hears():
    known = desk([lobarts_view()], knows_hero=True).sight(LOBART)
    assert known is not None and known.hero is not None and "не знаешь" not in known.hero
    unseen = lobarts_view(look("PLAYER", "Я", 9, seen=False))
    sight = desk([unseen]).sight(LOBART)
    assert sight is not None and sight.hero == "Героя ты сейчас не видишь, но слышишь: он в 9 м."


def test_the_view_replaces_the_omniscient_lines_of_the_situation():
    fake = FakeGame()
    snap = NpcSnapshot.model_validate(fake.snapshot(LOBART))
    sight = desk([lobarts_view()]).sight(LOBART, None)
    text = render_situation(snap, None, sight)
    assert "Рядом:" not in text and "Гильдия героя" not in text and "Ты у точки" not in text.split("\n")[1]
    assert "Видишь: Хильда" in text and text.split("\n")[1].endswith(", утро.")
    assert "Твой инвентарь:" in text  # what it has, as before
    # a free npc nobody asked a snapshot of: what it sees is all there is
    assert "Видишь: Хильда" in render_situation(None, None, sight)


def test_look_tells_only_what_is_seen():
    d = desk([lobarts_view()])
    assert d.look(LOBART, "герой") == ("Герой, 4 м: на нём: Лёгкий доспех ополчения.")
    assert d.look(LOBART, "Хильда").startswith("Хильда, 6 м: готовит у котла; без доспеха")
    assert d.look(LOBART, "незнакомец, по виду наёмник").startswith("незнакомец, по виду наёмник, 15 м")
    assert d.look(LOBART, "Вино") == "Не видно: Вино."


def test_english_lines_for_the_behavioral_model_danger_first():
    lines = desk([lobarts_view()]).english(LOBART)
    assert lines[0] == "The hero: you do not know him, he looks like a militiaman."
    assert lines[1:] == (
        "A stranger who looks like a mercenary, is 15 m away, with a weapon drawn.",  # the armed first
        "Волк is 18 m away.",
        "Хильда, whom you know, is 6 m away.",
    )
    state = SituationBuilder().react(
        NpcContext(LOBART, "Лобарт", GIL_OUT, 400, True, view=lines), [], 0, None
    )
    assert "The hero is 4 m away, looking at you. The hero: you do not know him" in state.state


def test_pushed_views_age_out():
    clock = Clock()
    d = desk([lobarts_view()], clock=clock)
    assert d.view(LOBART) is not None
    clock.t += 5
    assert d.view(LOBART) is None and d.english(LOBART) == ()


async def test_in_a_turn_the_npc_sees_and_can_look_closer():
    fake = FakeGame()
    fake.views = {"NONE_100_XARDAS": {**lobarts_view(), "npc": "NONE_100_XARDAS", "name": "Ксардас"}}
    llm = FakeBackend(
        script=[
            FakeStep("Хм…", [ToolCall("look", {"who": "герой"}, "c1")]),
            FakeStep("Ты в доспехе ополчения?"),
        ],
        delay=0,
    )
    async with connected(fake) as link:
        dialogue = DialogueManager(link, llm, PersonaProvider(None))
        perception = PerceptionDesk(link, social=Social)
        dialogue.extensions.append(perception)
        dialogue.sight = perception.sight
        dialogue.open("NONE_100_XARDAS", "Ксардас")
        await dialogue.say_as_player("NONE_100_XARDAS", "Узнаёшь меня?")
        await _until(lambda: len(llm.calls) == 2)
        await fake.push_views(lobarts_view())  # the once-a-second push
        await _until(lambda: perception.view(LOBART) is not None)

    situation = str(llm.calls[0][1][-2].content)
    assert "ты его не знаешь, по виду ополченец" in situation and "Видишь: Хильда" not in situation  # no ties
    assert "look" in [t.name for t in llm.calls[0][2]]
    assert "take_item" not in [t.name for t in llm.calls[0][2]]  # nothing on the ground
    result = next(i for i in llm.calls[1][1] if isinstance(i, ToolResult))
    assert result.output.startswith("Герой, 4 м")


def test_a_herd_is_told_once_and_creatures_draw_no_weapons():
    sheep = [look("SHEEP", "Овца", m, kind="monster", guild=40, weapon="fists") for m in (6, 9, 14)]
    sight = desk([lobarts_view(None, *sheep)]).sight(LOBART)
    assert sight is not None and sight.people == "Видишь: Овца ×3 (id SHEEP; 6–14 м)."
    bare = lobarts_view(look("PLAYER", "Я", 3, seen=True))
    sight = desk([bare]).sight(LOBART)
    assert sight is not None and sight.hero == "Герой в 3 м: ты его не знаешь; без доспеха."


def test_the_dead_are_told_by_who_they_were():
    """«О, смотри, мёртвый волк»: a dead beast is still a wolf, a dead stranger is told by his look."""
    d = desk([lobarts_view(
        None,
        look("WOLF_1", "Волк", 12, kind="monster", guild=36, body="run", from_hero="ahead", att="hostile"),
        look("WOLF_2", "Волк", 6, kind="body", guild=36, body="dead", from_hero="left"),
        look("WOLF_3", "Волк", 9, kind="body", guild=36, body="dead"),
        look("BDT_1", "Бандит", 7, kind="body", guild=GIL_OUT, body="dead", armor=BDT_ARMOR),
    )])  # fmt: skip
    sight = d.sight(LOBART)
    assert sight is not None and sight.people == (
        "Видишь: Волк (id WOLF_1; 12 м, перед героем), враг тебе; туша: Волк ×2 (id WOLF_2; 6–9 м); "
        "тело: незнакомец, по виду бандит (id BDT_1; 7 м)."
    )
    assert sight.targets["WOLF_2"] == "туша: Волк, 6 м"  # its intent may point to it
    assert d.look(LOBART, "мёртвый волк").startswith("туша: Волк, 6 м: мёртв")
    assert d.look(LOBART, "волк").startswith("Волк, 12 м")
    assert d.english(LOBART)[2:] == (  # the live wolf first, then the nearest of the dead
        "A dead Волк lies 6 m away, left of the hero.",
        "The body of a stranger who looks like a bandit lies 7 m away.",
    )


CAVALORN, BRAGO, BANDIT = "BAU_4300_ADDON_CAVALORN", "BDT_1014_BANDIT_L", "BDT_1015_BANDIT_L"
BDT_ARMOR = armor("ITAR_BDT_M", "Доспех бандита")


def cavalorns_view(*sees: dict, focus: str | None = None) -> dict:
    """Cavalorn in the bandits' cave (protocol v18): how each treats him and the hero, where from the hero."""
    hero = look("PLAYER", "Я", 9, seen=True, focus=focus)
    return {"npc": CAVALORN, "name": "Кавалорн", "indoors": True, "state": "ZS_STAND_WP", "hero": hero,
            "sees": list(sees)}  # fmt: skip


def brago(**extra) -> dict:
    return look(BRAGO, "Браго", 10, guild=9, armor=BDT_ARMOR, state="ZS_SIT_CAMPFIRE", att="hostile",
                att_hero="hostile", from_hero="left", hero_cm=1500, noticed=True, **extra)  # fmt: skip


def test_where_each_is_from_the_hero_and_who_is_an_enemy():
    mate = look(BANDIT, "Бандит", 7, guild=9, armor=BDT_ARMOR, att="neutral", att_hero="hostile",
                from_hero="ahead", hero_cm=800)  # fmt: skip
    d = desk([cavalorns_view(mate, brago())])
    sight = d.sight(CAVALORN)
    assert sight is not None
    assert sight.people == (
        "Видишь: незнакомец, по виду бандит (id BDT_1015_BANDIT_L; 7 м, перед героем), враждует с героем; "
        "незнакомец, по виду бандит (id BDT_1014_BANDIT_L; 10 м, слева от героя, сбоку или у тебя за "
        "спиной), враг тебе, сидит у костра."
    )
    english = " ".join(d.english(CAVALORN))
    assert "hostile to you, left of the hero" not in english  # the order: where, then what
    assert "10 m away, left of the hero, hostile to you" in english
    assert "7 m away, ahead of the hero, hostile to the hero" in english


def test_whom_the_hero_looks_at():
    sight = desk([cavalorns_view(brago(), focus=BRAGO)]).sight(CAVALORN)
    assert sight is not None and sight.hero is not None
    assert sight.hero.endswith("Герой смотрит на: незнакомец, по виду бандит (id BDT_1014_BANDIT_L).")
    # one it does not see: still a target, the hero points at him
    sight = desk([cavalorns_view(focus=BANDIT)]).sight(CAVALORN)
    assert sight is not None and sight.targets[BANDIT] == "на него смотрит герой"
    assert "кого-то, кого ты не видишь (id BDT_1015_BANDIT_L)" in (sight.hero or "")


def test_its_intent_points_only_to_whom_it_sees_or_saw_a_moment_ago():
    clock = Clock()
    d = desk([cavalorns_view(brago())], clock=clock)
    clock.t += 10
    d._store(NpcView.model_validate(cavalorns_view()))  # it turned away: Brago out of sight
    sight = d.sight(CAVALORN)
    assert sight is not None and sight.targets == {
        "PLAYER": "герой",
        BRAGO: "незнакомец, по виду бандит, видел только что",
    }
    fight = Intent("fight", BRAGO, "friendly")
    assert check_intent(fight, None, sight.targets)[0] == fight
    intent, why = check_intent(Intent("fight", "NW_XARDAS_BANDITS_01", "friendly"), None, sight.targets)
    assert intent is None and why is not None and "BDT_1014_BANDIT_L" in why  # a point is no one to fight
    clock.t += RECENT_S
    d._store(NpcView.model_validate(cavalorns_view()))
    sight = d.sight(CAVALORN)
    assert sight is not None and sight.targets == {"PLAYER": "герой"}


LETTER = "ITWR_SATURASFIRSTMESSAGE_ADDON_SEALED"


def letter(metres: float = 2, hero_cm: float = 80, **extra) -> dict:
    return {
        "item": LETTER,
        "name": "Запечатанное послание",
        "distance": metres * 100,
        "hero_cm": hero_cm,
        **extra,
    }


def test_things_lying_about():
    # 2026-10-14: the hero threw Cavalorn's letter down at his feet; Cavalorn: «я даже не вижу, куда ты его
    # швырнул»
    herbs = {
        "item": "ITPL_HEALTH_HERB_01",
        "name": "Лечебная трава",
        "count": 3,
        "distance": 800,
        "from_hero": "left",
    }
    view = {**lobarts_view(), "items": [letter(), herbs]}
    sight = desk([view]).sight(LOBART)
    assert sight is not None and sight.ground == (
        f"На земле: Запечатанное послание ({LETTER}; 2 м, у ног героя); "
        "Лечебная трава ×3 (ITPL_HEALTH_HERB_01; 8 м, слева от героя)."
    )
    assert set(sight.things) == {LETTER, "ITPL_HEALTH_HERB_01"}
    assert sight.ground in render_situation(None, None, sight)
    assert "On the ground: Запечатанное послание, 2 m away at the hero's feet." in desk([view]).english(
        LOBART
    )
    nothing = desk([lobarts_view()]).sight(LOBART)
    assert (
        nothing is not None
        and nothing.ground is None
        and "На земле" not in render_situation(None, None, nothing)
    )


async def test_it_picks_up_what_the_hero_threw_down_and_follows_him_again():
    xardas = "NONE_100_XARDAS"
    fake = FakeGame()
    fake.views = {xardas: {**lobarts_view(), "npc": xardas, "name": "Ксардас", "items": [letter()]}}
    llm = FakeBackend(
        script=[
            FakeStep("Это ещё что?", [ToolCall("take_item", {"item": "ITPL_BEET"}, "c1")]),
            FakeStep("Ладно, подниму.", [ToolCall("take_item", {"item": LETTER.lower()}, "c2")]),
        ],
        delay=0,
    )
    async with connected(fake) as link:
        dialogue = DialogueManager(link, llm, PersonaProvider(None))
        perception = PerceptionDesk(link, social=Social)
        dialogue.extensions.append(perception)
        dialogue.sight = perception.sight
        await link.attach(xardas)
        conv = dialogue.open(xardas, "Ксардас")
        conv.intent = Intent("follow_hero", "PLAYER", "friendly")
        await dialogue.say_as_player(xardas, "Вот твоё письмо, под ногами. Бери.")
        await _until(lambda: any("поднял" in m.content for m in conv.history if m.role == "developer"))

    assert "take_item" in [t.name for t in llm.calls[0][2]]  # offered: it sees something lying there
    rejected = next(i for i in llm.calls[1][1] if isinstance(i, ToolResult))
    assert rejected.output == f"Отклонено: на земле нет ITPL_BEET; ты видишь там: {LETTER}."
    acts = [(m["action"], m["args"]) for m in fake.received if m["type"] == "action"]
    assert acts == [("take_item", {"item": LETTER}), ("follow", {"npc": "PLAYER"})]
    assert "[Ты поднял с земли: Запечатанное послание]" in [m.content for m in conv.history]
