from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from gothic_ai.chronicle import Chronicle
from gothic_ai.crime_memory import CrimeMemoryRuntime, memory_value
from gothic_ai.crimes import Crimes
from gothic_ai.game import GameError
from gothic_ai.memory import MemoryStore
from gothic_ai.protocol import Loading, Session
from tests.test_crimes import Link

NPC = "BAU_951_HILDA"


class MemoryLink(Link):
    def __init__(self) -> None:
        super().__init__()
        self.effects: list[tuple[str, list[dict[str, Any]]]] = []
        self.missing: set[str] = set()

    async def story_effect(self, npc: str, effects: list[dict[str, Any]]) -> None:
        if npc in self.missing:
            raise GameError("npc in another world")
        self.effects.append((npc, effects))


@pytest.fixture
async def system():
    link = MemoryLink()
    memory = MemoryStore(":memory:")
    chronicle = Chronicle(link, memory)  # type: ignore[arg-type]
    crimes = Crimes(link, chronicle)  # type: ignore[arg-type]
    runtime = CrimeMemoryRuntime(link, crimes)  # type: ignore[arg-type]
    yield SimpleNamespace(link=link, memory=memory, chronicle=chronicle, crimes=crimes, runtime=runtime)
    await runtime.close()
    await crimes.close()
    if chronicle._background:
        await asyncio.gather(*chronicle._background)
    memory.close()


def case(system, *, crime="theft-1", kind="theft", day=3, npc=NPC, degree="seen"):
    system.chronicle.record({"fact": "crime", "id": crime, "kind": kind, "day": day})
    fact = {"fact": "crime_seen", "npc": npc, "crime": crime}
    if degree == "heard":
        fact = {"fact": "rumor", "to": npc, "crime": crime, "from": "WITNESS"}
    elif degree == "suspects":
        fact["fact"] = "crime_suspected"
    system.chronicle.record(fact)


@pytest.mark.parametrize("kind,value", [("sheep", 1), ("attack", 2), ("theft", 3), ("murder", 4)])
@pytest.mark.parametrize("degree", ["seen", "heard"])
async def test_proven_knowledge_projects_native_level_and_day(system, kind, value, degree):
    case(system, kind=kind, degree=degree)
    await system.runtime.tick()
    assert system.link.effects == [(NPC, [{"do": "crime_memory", "value": value, "day": 3}])]
    assert memory_value(system.crimes, NPC.lower()) == (value, 3)


async def test_maximum_severity_wins_then_latest_day_without_transferring_suspicion(system):
    case(system, crime="old-theft", day=2)
    case(system, crime="new-theft", day=3, degree="heard")
    case(system, crime="new-attack", kind="attack", day=4)
    case(system, crime="possible-murder", kind="murder", day=5, degree="suspects")
    assert memory_value(system.crimes, NPC) == (3, 3)
    assert memory_value(system.crimes, "UNKNOWN") == (0, 0)


@pytest.mark.parametrize("kind", ["silenced", "forgiven", "absolved", "died"])
async def test_personal_resolution_or_absolution_removes_native_memory(system, kind):
    case(system)
    await system.runtime.tick()
    fact = {"fact": kind, "npc": NPC, "crime": "theft-1"}
    if kind == "absolved":
        fact = {"fact": kind, "community": "city", "crimes": ["theft-1"]}
    system.chronicle.record(fact)
    assert system.runtime._wake.is_set()
    await system.runtime.tick()
    assert system.link.effects[-1] == (NPC, [{"do": "crime_memory", "value": 0, "day": 0}])
    assert system.crimes.known(NPC)  # the fact remains; only the native encounter projection is cleared


async def test_other_npcs_forgiveness_and_other_cases_absolution_do_not_erase_memory(system):
    case(system)
    system.chronicle.record({"fact": "forgiven", "npc": "OTHER", "crime": "theft-1"})
    system.chronicle.record({"fact": "absolved", "community": "city", "crimes": ["elsewhere"]})
    assert memory_value(system.crimes, NPC) == (3, 3)


async def test_resolution_reveals_next_unresolved_case(system):
    case(system)
    case(system, crime="assault", kind="attack", day=4)
    system.chronicle.record({"fact": "silenced", "npc": NPC, "crime": "theft-1"})
    assert memory_value(system.crimes, NPC) == (2, 4)


async def test_unchanged_projection_keeps_native_charm_clearing_until_load(system):
    case(system)
    await system.runtime.project(NPC)
    await system.runtime.tick()
    assert len(system.link.effects) == 1
    system.link.receive(Loading(type="loading"))
    await system.runtime.tick()
    assert len(system.link.effects) == 2


async def test_new_same_day_theft_reinstates_memory_after_native_charm(system):
    case(system)
    await system.runtime.project(NPC)
    case(system, crime="another-theft")
    await system.runtime.project(NPC)
    assert len(system.link.effects) == 2


async def test_missing_npc_is_retried_without_blocking_other_witness(system):
    case(system)
    case(system, crime="other", npc="VLK_OTHER")
    system.link.missing.add(NPC)
    await system.runtime.tick()
    assert system.link.effects[0][0] == "VLK_OTHER"
    system.link.missing.clear()
    await system.runtime.tick()
    assert system.link.effects[-1][0] == NPC


async def test_loading_new_branch_forgets_process_projection_and_uses_current_facts(system):
    case(system)
    await system.runtime.tick()
    system.link.receive(Session(type="session", branch="B"))
    assert system.runtime._projection == {}
    await system.runtime.tick()
    assert len(system.link.effects) == 1
    case(system, crime="new", kind="attack", day=7)
    await system.runtime.tick()
    assert system.link.effects[-1] == (NPC, [{"do": "crime_memory", "value": 2, "day": 7}])
