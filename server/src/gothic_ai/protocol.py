"""Game <-> AI server protocol (WebSocket, one JSON object per message).

Mirrors OpenGothic/ai_bridge. Positions are in Gothic units (centimetres).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

PROTOCOL_VERSION = 5

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
    """A world is being loaded (new game, save game or a world change); npc's were released."""

    type: Literal["loading"]


SessionReason = Literal["new_game", "load", "connect"]


class Session(_GameMessage):
    """The line of play the game is on. A new game and every load start a new `branch`; a load
    branches off `parent` at `parent_seq` (the memory head written into the save)."""

    type: Literal["session"]
    branch: str
    parent: str | None = None
    parent_seq: int = 0
    reason: SessionReason = "connect"


class Saved(_GameMessage):
    """The game was saved with this memory head in it."""

    type: Literal["saved"]
    branch: str
    seq: int


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


class PttStart(_GameMessage):
    """The player pressed the push-to-talk key in the chat with `npc`: start recording.
    Followed by `ptt_stop` (recognize what was said) or `ptt_cancel` (it was a short tap)."""

    type: Literal["ptt_start"]
    npc: str


class PttStop(_GameMessage):
    type: Literal["ptt_stop"]
    npc: str


class PttCancel(_GameMessage):
    type: Literal["ptt_cancel"]
    npc: str


class LineStarted(_GameMessage):
    """The npc started speaking a line sent with `say`."""

    type: Literal["line_started"]
    npc: str
    line: str


NpcActivity = Literal["routine", "attached", "dialog", "fight", "down"]


class RosterEntry(_GameMessage):
    id: str
    name: str
    guild: int = 0
    distance: float  # to the player, cm
    in_view: bool = False  # on the player's screen
    activity: NpcActivity = "routine"


class NpcRoster(_GameMessage):
    """People within ~60 m of the player, twice a second (v5). Npc's missing from a full roster are gone
    (out of range or out of the world); monsters are never listed."""

    type: Literal["npc_roster"]
    t: int
    npcs: list[RosterEntry]
    full: bool = True
    hour: int | None = None  # game time
    minute: int | None = None


class NpcEvent(_GameMessage):
    """Something an npc that is not under control noticed about the player (v5): came close, entered its
    room, gave an item, drew a weapon, stole, cast a spell, attacked or killed (`detail`: whom). The
    engine's own perceptions still run; this is a copy."""

    type: Literal["npc_event"]
    npc: str
    kind: str
    other: str = "PLAYER"
    distance: float | None = None
    detail: str | None = None


class NpcIdle(_GameMessage):
    """A soft action (`npc_act`) is over (v5): a good moment to decide what the npc does next.
    `reason`: done (its AI queue ran empty), gone (down, removed, taken over) or timeout."""

    type: Literal["npc_idle"]
    npc: str
    act: str | None = None
    reason: str | None = None


class NpcActResult(_GameMessage):
    """Reply to `npc_act`: queued into the npc's AI queue, or rejected (busy, down, unknown...)."""

    type: Literal["npc_act_result"]
    id: str
    npc: str
    status: Literal["queued", "rejected"]
    detail: str | None = None


GameMessage = Annotated[
    Hello
    | WorldLoaded
    | Loading
    | Session
    | Saved
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
    | PttStart
    | PttStop
    | PttCancel
    | LineStarted
    | NpcRoster
    | NpcEvent
    | NpcIdle
    | NpcActResult,
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


NpcActType = Literal["say_svm", "turn_to", "look_at", "play_ani", "go_to_npc", "turn_away"]


class NpcActCmd(_Command):
    """A soft action for an npc that follows its routine (v5): queued the way scripts do it, the routine
    resumes by itself afterwards. `args.npc` is the target (default the player); say_svm takes `svm`
    (e.g. NOTNOW), play_ani `name`, look_at `ms`."""

    type: Literal["npc_act"] = "npc_act"
    id: str
    npc: str
    action: NpcActType
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


ChatStatus = Literal["recognizing", "thinking", "idle", "error", "notice"]  # notice: detail shown as is


class ChatStatusCmd(_Command):
    type: Literal["chat_status"] = "chat_status"
    npc: str
    status: ChatStatus
    detail: str | None = None


class ChatEndCmd(_Command):
    """The npc ends the conversation: the game closes the chat and releases the npc (unless it follows)."""

    type: Literal["chat_end"] = "chat_end"
    npc: str


class TranscriptCmd(_Command):
    """What the player says (push-to-talk). Partial (`final=false`) while speaking: text so far;
    the final one is shown in the chat as the hero's line."""

    type: Literal["transcript"] = "transcript"
    npc: str
    text: str
    final: bool = True


class MemoryCmd(_Command):
    """The memory head of the branch: the game writes it into the next save."""

    type: Literal["memory"] = "memory"
    branch: str
    seq: int


DebugCommand = Literal["save", "load"]


class DebugCmd(_Command):
    """Save or load a slot (`save_slot_<slot>.sav`) for automated checks; the game must run with -devmode."""

    type: Literal["debug"] = "debug"
    req: str
    cmd: DebugCommand
    slot: int
