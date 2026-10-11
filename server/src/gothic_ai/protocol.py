"""Game <-> AI server protocol (WebSocket, one JSON object per message).

Mirrors OpenGothic/ai_bridge. Positions are in Gothic units (centimetres).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

PROTOCOL_VERSION = 3

# --- game -> server -----------------------------------------------------------


class _GameMessage(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class Hello(_GameMessage):
    type: Literal["hello"]
    protocol: int
    client: str
    codepage: str


class WorldLoaded(_GameMessage):
    type: Literal["world_loaded"]
    world: str


class Loading(_GameMessage):
    type: Literal["loading"]


class Attached(_GameMessage):
    type: Literal["attached"]
    npc: str
    name: str


class Detached(_GameMessage):
    type: Literal["detached"]
    npc: str
    reason: str


class Result(_GameMessage):
    type: Literal["result"]
    req: str
    ok: bool
    error: str | None = None


ActionStatus = Literal["done", "failed", "timeout", "cancelled", "rejected"]


class ActionResult(_GameMessage):
    type: Literal["action_result"]
    id: str
    npc: str
    status: ActionStatus
    detail: str | None = None
    distance: float | None = None

    @property
    def ok(self) -> bool:
        return self.status == "done"


class WorldInfo(_GameMessage):
    name: str
    day: int
    hour: int
    minute: int


class InventoryItem(_GameMessage):
    item: str  # script symbol, e.g. ITMW_1H_BAU_MACE
    name: str
    count: int
    equipped: bool = False


class NpcState(_GameMessage):
    id: str
    name: str
    pos: tuple[float, float, float]
    rot: float
    hp: int
    hp_max: int
    busy: bool
    action: str | None = None
    following: bool = False
    waypoint: str | None = None
    nearest_waypoint: str | None = None
    inventory: list[InventoryItem] = Field(default_factory=list)


class PlayerState(_GameMessage):
    name: str
    pos: tuple[float, float, float]
    distance: float
    guild: int = 0
    hp: int | None = None
    hp_max: int | None = None
    armor: str | None = None
    melee: str | None = None
    ranged: str | None = None


class NearbyNpc(_GameMessage):
    id: str
    name: str
    distance: float
    guild: int = 0
    dead: bool = False


class WaypointRef(_GameMessage):
    name: str
    distance: float


class Snapshot(_GameMessage):
    type: Literal["snapshot"]
    t: int
    world: WorldInfo
    npc: NpcState
    player: PlayerState | None = None
    nearby_waypoints: list[WaypointRef] = Field(default_factory=list)
    nearby_npcs: list[NearbyNpc] = Field(default_factory=list)


class Perception(_GameMessage):
    type: Literal["perception"]
    npc: str
    perc: str
    other: str
    distance: float
    victim: str | None = None


class Pong(_GameMessage):
    type: Literal["pong"]
    req: str = ""


class ChatOpen(_GameMessage):
    """The player opened the chat with the npc they look at (the game has already attached it)."""

    type: Literal["chat_open"]
    npc: str
    name: str


class PlayerText(_GameMessage):
    type: Literal["player_text"]
    npc: str
    text: str


class ChatClose(_GameMessage):
    """The player closed the chat; the game hands the npc back to its routine."""

    type: Literal["chat_close"]
    npc: str


class FollowEnded(_GameMessage):
    """The npc stopped following: `stopped`/`replaced` by a command, or on its own
    (`target_gone`, `target_too_far`, `timeout`); then the game hands it back to its routine
    unless the chat is open."""

    type: Literal["follow_ended"]
    npc: str
    reason: str


class LineStarted(_GameMessage):
    """The npc started speaking a line sent with `say`."""

    type: Literal["line_started"]
    npc: str
    line: str


GameMessage = Annotated[
    Hello
    | WorldLoaded
    | Loading
    | Attached
    | Detached
    | Result
    | ActionResult
    | Snapshot
    | Perception
    | Pong
    | ChatOpen
    | PlayerText
    | ChatClose
    | FollowEnded
    | LineStarted,
    Field(discriminator="type"),
]

_game_message = TypeAdapter(GameMessage)


def parse_game_message(raw: str | bytes) -> GameMessage:
    """Parse one message from the game. Raises pydantic.ValidationError on unknown/malformed input."""
    return _game_message.validate_json(raw)


# --- server -> game -----------------------------------------------------------

ActionType = Literal[
    "go_to_waypoint",
    "go_to_npc",
    "turn_to",
    "play_ani",
    "wait",
    "stop",
    "follow",
    "give_item",
    "return_to_routine",
    "debug_add_item",  # game must run with -devmode
]


class _Command(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    def to_json(self) -> str:
        return self.model_dump_json(exclude_none=True)


class AttachCmd(_Command):
    type: Literal["attach"] = "attach"
    req: str
    npc: str


class DetachCmd(_Command):
    type: Literal["detach"] = "detach"
    req: str
    npc: str


class ActionCmd(_Command):
    type: Literal["action"] = "action"
    id: str
    npc: str
    action: ActionType
    args: dict[str, Any] = Field(default_factory=dict)


class SnapshotCmd(_Command):
    type: Literal["snapshot"] = "snapshot"
    npc: str


class PingCmd(_Command):
    type: Literal["ping"] = "ping"
    req: str


class SayCmd(_Command):
    """One line (usually a sentence) for the npc to speak; shown as a subtitle."""

    type: Literal["say"] = "say"
    npc: str
    line: str
    text: str


ChatStatus = Literal["thinking", "idle", "error"]


class ChatStatusCmd(_Command):
    type: Literal["chat_status"] = "chat_status"
    npc: str
    status: ChatStatus
    detail: str | None = None


class ChatEndCmd(_Command):
    """The npc ends the conversation: the game closes the chat and releases the npc (unless it follows)."""

    type: Literal["chat_end"] = "chat_end"
    npc: str
