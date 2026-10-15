from types import SimpleNamespace

from gothic_ai.places import PlaceBook
from gothic_ai.protocol import Look, NpcView, PlayerState
from gothic_ai.sightings import HERO, Mind, Sighting
from gothic_ai.whereabouts import Whereabouts

CAVALORN = "BAU_4300_ADDON_CAVALORN"
BOOK = {
    "version": 2,
    "world": "NEWWORLD.ZEN",
    "places": [
        {"id": "XARDAS_STAIRS", "ru": "ступени на дороге от башни Ксардаса",
         "where_ru": "на ступенях на дороге от башни Ксардаса", "en": "steps", "where_en": "on the steps",
         "center": [0, 0, 0], "kind": "road", "neighbours": ["XARDAS_BANDITS"]},
        {"id": "XARDAS_BANDITS", "ru": "пещера бандитов под лестницей к башне Ксардаса",
         "where_ru": "в пещере бандитов под лестницей к башне Ксардаса", "en": "the bandits' cave",
         "where_en": "in the bandits' cave", "center": [0, 0, 3000], "kind": "cave", "inside": True,
         "entrances": [{"in": "NW_XARDAS_BANDITS_01", "out": "NW_XARDAS_STAIRS_04", "pos": [0, 0, 1500],
                        "side": "XARDAS_STAIRS"}],
         "neighbours": ["XARDAS_STAIRS"]},
        {"id": "XARDAS_TOWER_WATERFALL", "ru": "пещера у водопада возле башни Ксардаса",
         "where_ru": "в пещере у водопада", "en": "a cave", "where_en": "in a cave",
         "center": [9000, 0, 0], "kind": "cave", "inside": True},
        {"id": "TAVERNE_IN", "ru": "таверна «Мёртвая гарпия»", "where_ru": "в таверне", "en": "tavern",
         "where_en": "in the tavern", "center": [15000, 0, 0], "kind": "house", "inside": True},
    ],
    "points": {"NW_XARDAS_STAIRS_04": 0, "NW_XARDAS_BANDITS_01": 1, "NW_XARDAS_TOWER_WATERFALL_01": 2,
               "NW_TAVERNE_IN_01": 3},
}  # fmt: skip


class _Desk:
    def __init__(self, view: NpcView | None) -> None:
        self._view = view

    def view(self, npc: str) -> NpcView | None:
        return self._view


def _where(*, sightings: list[Sighting] = (), sees: list[Look] = (), now: int = 600) -> Whereabouts:
    hero = PlayerState(name="Я", pos=(0, 0, 0), ahead=(1, 0), right=(0, -1), distance=200)
    link = SimpleNamespace(snapshots={}, player=hero)
    view = NpcView(npc=CAVALORN, name="Кавалорн", waypoint="NW_XARDAS_STAIRS_04", sees=list(sees))
    seen = SimpleNamespace(mind=lambda npc: Mind(list(sightings)), now=now)
    return Whereabouts(link, PlaceBook(BOOK), sightings=seen, desk=_Desk(view))  # type: ignore[arg-type]


def test_a_cave_by_its_kind_the_nearest_with_its_way_in_and_who_was_seen_there():
    brago = Sighting("BDT_1014_BANDIT_L", "Браго", 580, 590, place_ids=["XARDAS_BANDITS"])
    answer = _where(sightings=[brago]).where(CAVALORN, "пещеру")
    first, near, seen = answer.split("\n")
    assert first.startswith(
        "XARDAS_BANDITS — пещера бандитов под лестницей к башне Ксардаса: пещера, 15 м от тебя"
    )
    assert "слева от героя" in first and "вход — на ступенях на дороге от башни Ксардаса" in first
    assert first.endswith("; под крышей.")
    assert near == "Рядом: ступени на дороге от башни Ксардаса (XARDAS_STAIRS)."
    assert seen == "Ты там видел: Браго — 10 минут назад."


def test_a_place_by_its_id_or_name():
    where = _where()
    assert where.where(CAVALORN, "XARDAS_TOWER_WATERFALL").startswith(
        "XARDAS_TOWER_WATERFALL — пещера у водопада"
    )
    assert where.where(CAVALORN, "пещера у водопада").startswith(
        "XARDAS_TOWER_WATERFALL"
    )  # not the nearest cave
    assert where.where(CAVALORN, "таверна").startswith("TAVERNE_IN — таверна «Мёртвая гарпия»: дом, 150 м")
    assert where.where(CAVALORN, "замок").startswith("Ты не знаешь поблизости такого места")


def test_someone_in_sight_or_last_seen():
    bandit = Look(id="BDT_1013_BANDIT_L", name="Бандит", distance=800, waypoint="NW_XARDAS_STAIRS_04")
    assert (
        _where(sees=[bandit])
        .where(CAVALORN, "бандит")
        .startswith("Бандит у тебя на виду, в 8 м — на ступенях на дороге от башни Ксардаса (XARDAS_STAIRS).")
    )
    brago = Sighting("BDT_1014_BANDIT_L", "Браго", 480, 540, place_ids=["XARDAS_BANDITS"])
    hero = Sighting(HERO, "Я", 590, 600, places=["у костра"])
    where = _where(sightings=[brago, hero])
    answer = where.where(CAVALORN, "Браго")
    assert answer.startswith(
        "Последний раз ты видел Браго около часа назад — в пещере бандитов под лестницей"
    )
    assert "\nXARDAS_BANDITS — пещера бандитов" in answer  # and the place itself
    assert where.where(CAVALORN, "где герой?") == "Последний раз ты видел героя только что — у костра."
