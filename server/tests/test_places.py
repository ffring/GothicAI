import json

from gothic_ai.llm.fake import FakeBackend, FakeStep
from gothic_ai.perception import PerceptionDesk
from gothic_ai.places import PlaceBook, book, group, label, tokens
from gothic_ai.protocol import NpcView
from tests.fakes import Clock


def point(name: str, x: float, z: float, y: float = 0) -> dict:
    return {"name": name, "x": x, "y": y, "z": z, "fp": name.startswith("FP_")}


def test_what_a_point_name_says_of_where_it_is():
    assert tokens("FP_ROAM_NW_FARM1_SHEEP_02") == ["FARM1", "SHEEP"]
    assert tokens("NW_XARDAS_TOWER_IN1_18") == ["XARDAS", "TOWER", "IN1"]
    assert tokens("FP_STAND_NW_FARM1_LOBART") == ["FARM1", "LOBART"]


def test_points_named_alike_and_close_are_one_place():
    points = [
        *(point(f"NW_FARM1_OUT_0{i}", i * 500, 0) for i in range(4)),  # the farmyard
        *(point(f"NW_FARM1_OUT_1{i}", 90000 + i * 500, 0) for i in range(3)),  # the same name, far away
        point("NW_FARM1_LOBART", 800, 300),  # a stray: joins the nearest place of the farm
        point("NW_CITY_SMITH_01", 300000, 0),
        *(point(f"NW_CITY_SMITH_0{i}", 300000 + i * 300, 0) for i in range(2, 5)),
    ]
    people = [
        {"npc": "BAU_950_LOBART", "name": "Лобарт", "routine": [
            {"start": 420, "end": 1380, "state": "ZS_STAND_GUARDING", "waypoint": "NW_FARM1_LOBART"},
        ]},
    ]  # fmt: skip
    groups = {g.id: g for g in group(points, people)}
    assert sorted(groups) == ["CITY_SMITH", "FARM1_OUT", "FARM1_OUT#2"]
    assert "NW_FARM1_LOBART" in {p["name"] for p in groups["FARM1_OUT"].points}
    assert groups["FARM1_OUT"].residents == ["Лобарт: ZS_STAND_GUARDING 07–23 ч"]


def test_a_big_part_of_the_world_goes_by_its_third_word_too():
    tower = [point(f"NW_XARDAS_TOWER_IN1_{i}", i * 100, 0, 1000) for i in range(30)]
    tower += [point(f"NW_XARDAS_TOWER_IN2_{i}", i * 100, 0, 3000) for i in range(30)]
    assert sorted(g.id for g in group(tower)) == ["XARDAS_TOWER_IN1", "XARDAS_TOWER_IN2"]


async def test_the_model_names_places_and_a_broken_answer_is_asked_again():
    points = [point(f"NW_FARM1_OUT_0{i}", i * 500, 0) for i in range(4)]
    groups = group(points)
    named = [
        {"id": "FARM1_OUT", "ru": "двор фермы Лобарта", "where_ru": "во дворе фермы Лобарта",
         "en": "Lobart's farmyard", "where_en": "in Lobart's farmyard"},
    ]  # fmt: skip
    llm = FakeBackend(script=[FakeStep("не JSON"), FakeStep(json.dumps(named, ensure_ascii=False))], delay=0)
    names = await label(groups, llm)
    assert names["FARM1_OUT"]["where_ru"] == "во дворе фермы Лобарта"
    assert "NW_FARM1_OUT_01" in llm.calls[0][1][0].content  # the model sees the points' names

    places = PlaceBook(book(groups, names, "newworld.zen"))
    place = places.of(None, "nw_farm1_out_02")
    assert place is not None and place.ru == "двор фермы Лобарта"
    assert places.of("NW_CITY_SMITH_01") is None

    # perception says it in place of the waypoint
    desk = PerceptionDesk(type("Link", (), {"listeners": []})(), places=places, clock=Clock())  # type: ignore[arg-type]
    view = NpcView(
        npc="BAU_950_LOBART", name="Лобарт", waypoint="NW_FARM1_OUT_02", state="ZS_STAND_ARMSCROSSED"
    )
    desk._views["BAU_950_LOBART"] = (desk.clock(), view)
    sight = desk.sight("BAU_950_LOBART")
    assert sight is not None
    assert sight.place == "Ты во дворе фермы Лобарта, под открытым небом. Ты стоишь, скрестив руки."
    assert desk.english("BAU_950_LOBART")[0] == "You are in Lobart's farmyard."


def test_the_places_around_a_spot_nearest_first():
    book = PlaceBook.load()
    assert book is not None
    cave = book.get("xardas_bandits")
    assert (
        cave is not None and cave.ru.startswith("пещера бандитов") and "NW_XARDAS_BANDITS_01" in cave.points
    )
    # Cavalorn on the steps below Xardas's tower, where the hero asked him to go into the cave
    near = book.near((20529.0, 3040.0, -21386.0), 8000, 6)
    assert [p.id for p, _ in near[:3]] == ["XARDAS_STAIRS", "WP_XXX", "XARDAS_BANDITS"]
    assert 1500 < near[2][1] < 2500
    assert [d for _, d in near] == sorted(d for _, d in near)
    assert "XARDAS_TOWER_WATERFALL" not in {p.id for p, _ in near}  # the other cave is 111 m off
