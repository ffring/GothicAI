"""The hero's deeds and their actual witnesses, while vanilla still owns every reaction.

The bridge supplies ownership and pre-reaction combat state. This module records truth in the
Chronicle's save branch; it neither intercepts perceptions nor changes PETZ or anyone's knowledge.
Rules were checked against GOTHIC.DAT: C_IsTakenItemMyPossession, B_AssessTheft,
B_AssessDamage/B_Attack, B_AssessMurder, and the lifted C_NpcBelongsTo* helpers.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from .chronicle import Chronicle, Fact
from .game import GameLink
from .gamedata.storygraph import StoryGraph
from .protocol import DeedItem, GameMessage, HeroDeed

HERO = {"PLAYER", "HERO", "PC_HERO"}
PUBLIC_GUILDS = {-1, 0, 15}  # absent, GIL_NONE, GIL_PUBLIC
TOUGH_GUILDS = {6, 7, 9, 13}  # GIL_DJG, GIL_SLD, GIL_BDT, GIL_PIR (C_NpcIsToughGuy)


@dataclass(frozen=True)
class Community:
    name: str
    helper: str
    lawkeepers: tuple[str, ...]


COMMUNITIES = {
    "city": Community("город", "C_NPCBELONGSTOCITY", ("MIL_311_ANDRE", "PAL_200_HAGEN")),
    "farm": Community("ферма", "C_NPCBELONGSTOFARM", ("SLD_800_LEE",)),
    "monastery": Community("монастырь", "C_NPCBELONGSTOMONASTERY", ("KDF_504_PARLAN",)),
    "oldcamp": Community("старый лагерь", "C_NPCBELONGSTOOLDCAMP", ("PAL_250_GAROND",)),
    "bl": Community("бандиты", "C_NPCBELONGSTOBL", ()),
}


def _inventory(items: Iterable[DeedItem | Mapping[str, Any]]) -> Counter[str]:
    result: Counter[str] = Counter()
    for entry in items:
        item, count = (
            (entry.item, entry.count)
            if isinstance(entry, DeedItem)
            else (str(entry["item"]), int(entry["count"]))
        )
        result[item.upper()] += count
    return result


def pickpocket_outcome(
    before: Iterable[DeedItem | Mapping[str, Any]],
    after: Iterable[DeedItem | Mapping[str, Any]],
    picked_before: int = 0,
    picked_after: int = 0,
    caught: bool = False,
) -> Literal["success", "failure"] | None:
    """Resolve an executed pickpocket choice from its original script's before/after state.

    B_Beklauen marks AIV_PlayerHasPickedMyPocket only on success. The 28 bespoke choices give
    actual items and use AR_THEFT when caught. A real gain proves success even if AR_THEFT remains
    from an earlier attempt. A marked pocket without a gain or a caught attempt proves no new deed.
    """
    if _inventory(after) - _inventory(before):
        return "success"
    if caught:
        return "failure"
    if not picked_before and not picked_after:
        return "failure"
    return None


class Crimes:
    def __init__(self, link: GameLink, chronicle: Chronicle, *, graph: StoryGraph | None = None) -> None:
        self.link = link
        self.chronicle = chronicle
        self.graph = graph
        link.listeners.append(self._on_message)

    def facts(self) -> list[Fact]:
        """The current save branch's deeds, including those inherited at its saved head."""
        return self.chronicle.facts("crime")

    async def close(self) -> None:
        if self._on_message in self.link.listeners:
            self.link.listeners.remove(self._on_message)

    def lawkeepers(self, community: str | None) -> tuple[str, ...]:
        definition = COMMUNITIES.get(community or "")
        if definition is None:
            return ()
        if self.graph is None:
            return definition.lawkeepers
        # Use the actual graph's PETZMASTER nodes, not a name inferred from an npc's guild.
        found = {node.npc for node in self.graph.nodes.values() if node.id.endswith("_PETZMASTER")}
        return tuple(npc for npc in definition.lawkeepers if npc in found)

    def community(self, guild: int, npctype: int = 0) -> str | None:
        """The vanilla community of a victim; a guild-only owner has ordinary NPCTYPE 0."""
        if self.graph is not None:
            for key, definition in COMMUNITIES.items():
                function = self.graph.functions.get(definition.helper)
                if function and function.get("lifted"):
                    if self._community_expr(function["body"], guild, npctype):
                        return key
            # Missing helpers in a partial graph do not make ordinary people stateless.
        if guild in {1, 2, 3}:
            fallback = "oldcamp" if npctype in {3, 4} else "city"
        elif guild in {7, 8}:
            fallback = "farm"
        elif guild in {4, 5}:
            fallback = "monastery"
        else:
            fallback = "bl" if npctype in {5, 7} else None
        if fallback is not None and self.graph is not None:
            function = self.graph.functions.get(COMMUNITIES[fallback].helper)
            if function and function.get("lifted"):
                return None  # the actual DAT's helper rejected this vanilla fallback
        return fallback

    def _community_expr(self, expr: list, guild: int, npctype: int) -> int | bool:
        match expr:
            case ["int", value, *_]:
                return int(value)
            case ["field", _, "GUILD"]:
                return guild
            case ["field", _, "NPCTYPE"]:
                return npctype
            case ["fn", name, *_] if self.graph is not None:
                return self._community_expr(self.graph.functions[name]["body"], guild, npctype)
            case ["!", operand]:
                return not self._community_expr(operand, guild, npctype)
            case ["==", left, right]:
                return self._community_expr(left, guild, npctype) == self._community_expr(
                    right, guild, npctype
                )
            case ["&&", *operands]:
                return all(self._community_expr(x, guild, npctype) for x in operands)
            case ["||", *operands]:
                return any(self._community_expr(x, guild, npctype) for x in operands)
            case _:
                raise ValueError(f"unsupported vanilla community expression: {expr!r}")

    def _npc(self, symbol: str) -> str:
        symbol = symbol.upper()
        return self.graph.npc(symbol) if self.graph is not None else symbol

    def _ownership(self, deed: HeroDeed) -> tuple[str, int, int] | None:
        """Whose property was taken, strongest explicit ownership first; None means lawful.

        An explicitly owned item stays its owner's even in a public room. Conversely a dropped
        unowned item is no one's property, as C_IsTakenItemMyPossession's ITEM_DROPPED check says.
        """
        sources = [
            (deed.owner, deed.owner_npc_guild, deed.owner_npctype, deed.owner_guild),
        ]
        if deed.item_dropped and not deed.owner:
            return None
        if deed.chest:
            sources.append(
                (deed.chest_owner, deed.chest_owner_guild, deed.chest_owner_npctype, deed.chest_guild)
            )
        sources.append((deed.room_owner, deed.room_owner_guild, deed.room_owner_npctype, deed.room_guild))
        for owner, npc_guild, npctype, guild in sources:
            if owner:
                owner = self._npc(owner)
                return None if owner in HERO else (owner, npc_guild, npctype)
            if guild not in PUBLIC_GUILDS:
                return None if guild == deed.hero_guild else ("", guild, 0)
        return None

    def _on_message(self, msg: GameMessage) -> None:
        if isinstance(msg, HeroDeed):
            self.record(msg)

    def record(self, deed: HeroDeed) -> Fact | None:
        """Classify a raw bridge deed and keep one truth plus one fact per real witness."""
        if self.chronicle.branch is None:
            return None
        # Reconnect delivery or duplicate perceptions cannot turn one deed into two crimes. The
        # branch query also survives a save/load, unlike a process-wide set of event ids.
        if any(f.get("id") == deed.id for f in self.facts()):
            return None

        victim, owner = self._npc(deed.victim), ""
        community = None
        witnesses = list(dict.fromkeys(self._npc(npc) for npc in deed.witnesses if npc))
        item, count = deed.item.upper(), deed.count
        extra: dict[str, Any] = {}
        match deed.kind:
            case "take_item":
                if not item or count <= 0:
                    return None
                ownership = self._ownership(deed)
                if ownership is None:
                    return None
                owner, guild, npctype = ownership
                victim = owner
                community = self.community(guild, npctype)
                kind = "theft"
            case "attack":
                if (
                    not victim
                    or deed.victim_is_monster
                    or deed.victim_guild > 16
                    or deed.victim_in_fight
                    or deed.victim_enemy
                    or deed.victim_started_fight
                    or deed.victim_npctype == 2
                    or deed.victim_tough_guy
                    or deed.victim_guild in TOUGH_GUILDS
                ):
                    return None
                community = self.community(deed.victim_guild, deed.victim_npctype)
                kind = "attack"
            case "murder":
                if not victim:
                    return None
                if deed.victim_is_sheep or deed.victim_guild == 18:
                    if deed.victim_tough_guy:  # bought/following sheep, including Betsy
                        return None
                    kind = "sheep"
                    # Vanilla has no sheep -> npc owner. Its crime belongs to a witness's
                    # community. Keep an unwitnessed sheep's community unknown, without guessing
                    # a farmer from proximity or the sheep's waypoint.
                    community = next(
                        (
                            candidate
                            for npc in deed.witnesses_metadata
                            if self._npc(npc.npc) in witnesses
                            if (candidate := self.community(npc.guild, npc.npctype)) is not None
                        ),
                        None,
                    )
                elif deed.victim_is_monster or deed.victim_guild > 16:
                    return None
                else:
                    kind = "murder"
                    community = self.community(deed.victim_guild, deed.victim_npctype)
            case "pickpocket":
                outcome = pickpocket_outcome(
                    deed.inventory_before,
                    deed.inventory_after,
                    deed.picked_pocket_before,
                    deed.picked_pocket_after,
                    deed.caught,
                )
                if outcome is None or not victim:
                    return None
                kind, owner = "theft", victim
                community = self.community(deed.victim_guild, deed.victim_npctype)
                extra = {"method": "pickpocket", "outcome": outcome}
                gains = _inventory(deed.inventory_after) - _inventory(deed.inventory_before)
                if outcome == "success":
                    witnesses = []
                    item, count = next(iter(gains.items()))
                    if len(gains) > 1:
                        extra["items"] = [
                            {"item": symbol, "count": amount} for symbol, amount in gains.items()
                        ]
                else:
                    witnesses = list(dict.fromkeys([victim, *witnesses]))
                    item, count = "", 0
            case _:
                return None

        fact = {
            "fact": "crime",
            "id": deed.id,
            "kind": kind,
            "victim": victim,
            "owner": owner,
            "community": community,
            "item": item,
            "count": count,
            "place": deed.position.model_dump(),
            "waypoint": deed.waypoint,
            "day": deed.day,
            "hour": deed.hour,
            "minute": deed.minute,
            "witnesses": witnesses,
            **extra,
        }
        self.chronicle.record(fact)
        for npc in witnesses:
            self.chronicle.record({"fact": "crime_seen", "npc": npc, "crime": deed.id})
        return fact
