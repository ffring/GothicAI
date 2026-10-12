"""Game <-> AI server protocol (WebSocket, one JSON object per message).

Mirrors OpenGothic/ai_bridge. Positions are in Gothic units (centimetres).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

PROTOCOL_VERSION = 9

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
    value: Any = None  # debug commands: what was asked for (dialogs, a computed expression)


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


class PlayerText(_GameMessage):
    """A line the player typed (v7: into the input line, to nobody in particular). `focus` is the npc he
    looks at, if any; the server decides whom it is said to (talk.py)."""

    type: Literal["player_text"]
    text: str
    focus: str | None = None


class FollowEnded(_GameMessage):
    """The npc stopped following: `stopped`/`replaced` by a command, or on its own
    (`target_gone`, `target_too_far`, `timeout`); then the game hands it back to its routine."""

    type: Literal["follow_ended"]
    npc: str
    reason: str


class PttStart(_GameMessage):
    """The player pressed the push-to-talk key, anywhere (v7): start recording. Followed by `ptt_stop`
    (recognize what was said) or `ptt_cancel` (it was a short tap). `focus`: the npc he looks at."""

    type: Literal["ptt_start"]
    focus: str | None = None
    # the test bench: the hero says this recording (a 16-bit mono wav on the server's machine) instead
    # of the microphone; the server takes it only when started with --voice-files
    audio: str | None = None


class PttStop(_GameMessage):
    type: Literal["ptt_stop"]
    focus: str | None = None


class PttCancel(_GameMessage):
    type: Literal["ptt_cancel"]
    focus: str | None = None


class LineStarted(_GameMessage):
    """The npc started speaking a line sent with `say` (`voiced`: aloud, with its audio)."""

    type: Literal["line_started"]
    npc: str
    line: str
    voiced: bool = False


class DialogChosen(_GameMessage):
    """The player chose a dialog or one of its choices in the dialog menu (v8). `node` is the script
    symbol of the dialog (C_INFO) or of the choice's function, or the id of a dialog the director added
    (`made`)."""

    type: Literal["dialog_chosen"]
    npc: str
    node: str
    made: bool = False


class NpcDied(_GameMessage):
    """A person died, or a creature the director put under a tag (v8). `killer`: PLAYER, an npc's symbol,
    or empty if nobody hit it."""

    type: Literal["npc_died"]
    npc: str
    name: str = ""
    tag: str | None = None
    killer: str = ""


class TagNear(_GameMessage):
    npc: str
    distance: float  # cm, to the nearest living creature of the tag


class TagState(_GameMessage):
    tag: str
    alive: int
    dead: int
    near: list[TagNear] = Field(default_factory=list)


class StoryWorld(_GameMessage):
    """The director's tagged creatures, once a second while there are any (v8): how many live, and how
    far the nearest one is from each watched npc (`story_watch`)."""

    type: Literal["story_world"]
    tags: list[TagState]


NpcActivity = Literal["routine", "attached", "dialog", "fight", "down"]


class RosterEntry(_GameMessage):
    id: str
    name: str
    guild: int = 0
    distance: float  # to the player, cm
    in_view: bool = False  # on the player's screen
    activity: NpcActivity = "routine"
    state: str = ""  # v7: the script state it is in, e.g. ZS_SIT_BENCH
    moving: bool = False  # v7: walking or running


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
    """Something an npc that is not under control noticed (v5, threats from others since v6).

    About the player: came close, entered its room, gave an item, drew a weapon (`weapon_drawn`), aimed
    at it with a weapon (`threatened`), stole, cast a spell, attacked or killed (`detail`: whom).
    Threats from others (`other` is their script symbol, `other_name` the shown name): a monster it
    senses (`monster_near`), a monster attacking it or someone near (`monster_attack`, `detail`: "you"
    or the victim), an npc attacking it (`npc_attack`), someone attacking the hero (`hero_attacked`).
    `distance` is from the npc to `other`. The engine's own perceptions still run; this is a copy."""

    type: Literal["npc_event"]
    npc: str
    kind: str
    other: str = "PLAYER"
    other_name: str | None = None
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


class PerceptionHeld(_GameMessage):
    """A threat to a free npc the game holds back from its scripts for the behavioral model (v6, after
    `threat_control{hold:true}`): a monster near or attacking, an npc attacking. Fields as in `npc_event`.
    The server answers `hold_verdict`; without one the scripts get it after 0.6 s."""

    type: Literal["perception_held"]
    hold: str
    npc: str
    kind: str
    other: str
    other_name: str | None = None
    distance: float | None = None
    detail: str | None = None


class RoutineEntry(_GameMessage):
    start: str  # "HH:MM" game time
    end: str
    state: str  # script state, e.g. ZS_SIT_BENCH
    waypoint: str


class RoutineInfo(_GameMessage):
    """An npc's daily routine (v6), the answer to `routine`: its entries, the routine variants the scripts
    have for it (`set_routine` names, e.g. START, GUIDE) and the script state it is in now."""

    type: Literal["routine"]
    req: str = ""
    npc: str
    current: str = ""
    entries: list[RoutineEntry] = Field(default_factory=list)
    routines: list[str] = Field(default_factory=list)
    error: str | None = None


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
    | PlayerText
    | FollowEnded
    | PttStart
    | PttStop
    | PttCancel
    | LineStarted
    | NpcRoster
    | NpcEvent
    | NpcIdle
    | NpcActResult
    | RoutineInfo
    | PerceptionHeld
    | DialogChosen
    | NpcDied
    | StoryWorld,
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
    "fight",  # v6: the game's own combat (ZS_ATTACK) against args.npc, then control comes back
    "flee",  # v6: the game's own fleeing (ZS_FLEE) from args.npc
    "draw_weapon",  # v6: args.kind melee|ranged (default: what the npc has)
    "remove_weapon",  # v6
    "say_svm",  # v6: the npc's own recorded line, args.svm; args.overlay: said on the move
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


NpcActType = Literal[
    "say_svm",
    "turn_to",
    "look_at",
    "play_ani",
    "go_to_npc",
    "turn_away",
    "fight",  # v6: ZS_ATTACK against args.npc; npc_idle when the npc leaves that state
    "flee",  # v6: ZS_FLEE from args.npc
    "draw_weapon",  # v6
    "remove_weapon",  # v6
    "start_activity",  # v6: args.state (ZS_*), args.waypoint, args.minutes of game time; then the routine
    "set_routine",  # v6: args.name, e.g. START (Rtn_START_<id>), effective at once
]


class ThreatControlCmd(_Command):
    """Whether threats to free npc's are held from their scripts for the behavioral model (v6)."""

    type: Literal["threat_control"] = "threat_control"
    hold: bool


class HoldVerdictCmd(_Command):
    """What becomes of a held threat (v6): `server` — the behavioral model took the body over (fight,
    flee), repeats of it are kept from the scripts for a while; `scripts` — they get it now."""

    type: Literal["hold_verdict"] = "hold_verdict"
    hold: str
    to: Literal["server", "scripts"]


class RoutineCmd(_Command):
    """Ask for an npc's daily routine (v6); the game answers `routine`."""

    type: Literal["routine"] = "routine"
    req: str
    npc: str


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


# v9: how the npc looks while it says a line (and a moment after): a face of the game's heads
Mood = Literal["neutral", "friendly", "angry", "hostile", "afraid", "pain"]
MOODS: tuple[Mood, ...] = ("neutral", "friendly", "angry", "hostile", "afraid", "pain")


class SayCmd(_Command):
    """One line (usually a sentence) for the npc to speak, controlled or (v6) free; shown as a subtitle.
    Aloud either with
    `audio` (base64 of a 16-bit mono WAV file, the line lasts as long as it) or `stream`: the voice
    follows in `say_audio` chunks (16-bit mono PCM at `rate`) and ends with `say_end`."""

    type: Literal["say"] = "say"
    npc: str
    line: str
    text: str
    audio: str | None = None
    stream: bool | None = None
    rate: int | None = None
    reply: str | None = None  # the line is part of this reply, already shown with chat_text
    # v6: said on the move, not through the npc's AI queue: it keeps running or fighting. Such lines
    # are not ordered by the game: the server sends the next one when the previous has been said.
    overlay: bool | None = None
    mood: Mood | None = None  # v9: the face while the line is said; none: the face stays as it is


class ChatTextCmd(_Command):
    """The npc's reply as it is generated (`text` so far), shown as its subtitle; its lines (`say`
    with the same `reply`) then voice and animate it without showing the text again."""

    type: Literal["chat_text"] = "chat_text"
    npc: str
    reply: str
    text: str
    final: bool = False


class SayAudioCmd(_Command):
    """A chunk of a streamed line's voice: base64 of 16-bit mono PCM."""

    type: Literal["say_audio"] = "say_audio"
    npc: str
    line: str
    data: str


class SayEndCmd(_Command):
    """The streamed voice of a line is complete (`audio_s` long), or broke off with `error`."""

    type: Literal["say_end"] = "say_end"
    npc: str
    line: str
    audio_s: float | None = None
    error: str | None = None


ChatStatus = Literal["recognizing", "thinking", "idle", "error", "notice"]  # notice: detail shown as is


class ChatStatusCmd(_Command):
    """A status line under the subtitles: `thinking` names the npc, the others are about the player's
    voice (`npc` empty)."""

    type: Literal["chat_status"] = "chat_status"
    npc: str = ""
    status: ChatStatus
    detail: str | None = None


class TranscriptCmd(_Command):
    """What the player says. Partial (`final=false`) while he speaks: text so far; the final one is his
    line, shown as "Ты -> `to_name`: ..." (v7: whom it is said to, decided by the server; none: to nobody)."""

    type: Literal["transcript"] = "transcript"
    text: str
    final: bool = True
    to: str | None = None
    to_name: str | None = None


class HushCmd(_Command):
    """The npc is interrupted (v7): its voice stops, its lines that have not started yet are dropped,
    its reply's subtitle keeps only what it `said`."""

    type: Literal["hush"] = "hush"
    npc: str
    reply: str | None = None
    said: str | None = None


class MemoryCmd(_Command):
    """The memory head of the branch: the game writes it into the next save."""

    type: Literal["memory"] = "memory"
    branch: str
    seq: int


DebugCommand = Literal[
    "save",
    "load",
    "spawn",
    "player_weapon",
    "player_goto",
    "ptt",
    "type",
    "dialogs",
    "choose",
    "eval",
    "check",
    "kill",
    "time",
]


class DebugCmd(_Command):
    """Test helpers, the game must run with -devmode: save or load a slot (`save_slot_<slot>.sav`);
    spawn a creature (`instance`) `distance` cm in front of an npc (`near`, default the player); make the
    player draw (`state: "melee"`) or put away (`"none"`) a weapon; move the player to a `waypoint` (or to
    the nearest one at least `distance` cm from `near`, or one `distance` cm off the npc `to`); `kill` an
    `npc` (killed by no one); set the clock to `hour`:`minute` (`time`, the next day if that is earlier, like
    sleeping); press the talk key as the player does (`ptt`, `state`: start, stop, cancel; with the hero's
    own focus) or send a typed line (`type`, `text`).

    The story graph (v8): `dialogs` — the dialogs `npc` offers the player now (`value`: their ids, as the
    menu would list them); `choose` — plays dialog `node` of `npc` as if the player chose it in the menu;
    `eval` — computes `expr` (story graph expression) with self = `npc`, other = the hero (`value`);
    `check` — every npc of the world builds its dialog menu, each script condition is compared with its
    lifted form (`value`: {npcs, computed, differ, differences})."""

    type: Literal["debug"] = "debug"
    req: str
    cmd: DebugCommand
    slot: int = 0
    instance: str | None = None
    near: str | None = None
    distance: int | None = None
    state: str | None = None
    waypoint: str | None = None
    to: str | None = None
    hour: int | None = None
    minute: int | None = None
    text: str | None = None
    npc: str | None = None
    node: str | None = None
    expr: list[Any] | None = None


class GraphPatchCmd(_Command):
    """Changes to the story graph (v8): which dialogs npc's offer. `replace` drops the earlier ones first.

    Operations (`op`): `disable` / `enable` a dialog or a choice (`node`); `condition`: add `expr` to the
    dialog's script condition (`expr` null drops the added ones); `add_node` (`node` = {id, npc,
    description, nr, important, permanent, condition, lines, effects}); `remove_node`; `told`: the hero
    has chosen that added dialog. With `req`, the game answers `result` (`error` lists rejected ops)."""

    type: Literal["graph_patch"] = "graph_patch"
    req: str | None = None
    replace: bool = False
    ops: list[dict[str, Any]]


class StorySpawnCmd(_Command):
    """Creatures for the director (v8): `count` of `instance` `distance` cm from `near` (default the
    player), or at `waypoint` as the scripts insert them, under `tag`. The game answers `result` (`value`:
    how many came)."""

    type: Literal["story_spawn"] = "story_spawn"
    req: str
    instance: str
    tag: str
    count: int = 1
    near: str | None = None
    distance: int = 600
    waypoint: str | None = None


class StoryWatchCmd(_Command):
    """Npc's whose distance to the living creatures of every tag the game reports (v8). Replaces the list of
    the same `owner`; the lists of all owners are reported."""

    type: Literal["story_watch"] = "story_watch"
    npcs: list[str]
    owner: str = ""


class StoryEffectCmd(_Command):
    """Effects on an npc (v8), through the scripts' own functions, the npc as self and the hero as other:
    the same as in a dialog of the director (give, xp, set, journal, end) and `attack` (`reason`: an AR_*
    constant, AR_NONE by default: until the hero is down) and `refuse_talk` (`seconds`) — an npc held by
    the server is let go for these two; `routine` (`npc`: an instance symbol, default this npc; `routine`:
    START...) through B_StartOtherRoutine, as the dialogs change routines. The game answers `result`."""

    type: Literal["story_effect"] = "story_effect"
    req: str
    npc: str
    effects: list[dict[str, Any]]


class StoryStateCmd(_Command):
    """What the director's rules read (v8, stage 5). `result.value`: {"vars": {name: int}, "npcs": {symbol:
    {"state": "alive" | "dead" | "absent", "name", "distance": cm from `near`, alive or dead, if given}},
    "knows": {dialog: bool} (the hero has heard it), "symbols": {name: "npc" | "item" | "info" | "var" |
    "const" | "func" | "other"} (unknown names are left out), "items": {instance: name in the game},
    "dialog": bool, "cutscene": bool, "day", "hour", "minute": the game time}."""

    type: Literal["story_state"] = "story_state"
    req: str
    vars: list[str] = []
    npcs: list[str] = []
    near: str | None = None
    knows: list[str] = []
    symbols: list[str] = []
    items: list[str] = []


class StoryEvalCmd(_Command):
    """Story graph expressions computed on the live VM, self = `npc`, other = the hero (v8): what the
    director's rules ask. `result.value`: one value per expression (int, string, instance symbol, or null
    if it can't be computed)."""

    type: Literal["story_eval"] = "story_eval"
    req: str
    npc: str
    exprs: list[Any]


class StoryTagCmd(_Command):
    """Npc's already in the world (`npcs`, symbols) watched under `tag` like the director's creatures (v8):
    their deaths and how near they are come in npc_died and story_world. `result.value`: how many were
    found."""

    type: Literal["story_tag"] = "story_tag"
    req: str
    tag: str
    npcs: list[str]


class StoryNearbyCmd(_Command):
    """People within `radius` cm of an npc (v8); `result.value`: [{npc, name, distance, guild}]."""

    type: Literal["story_nearby"] = "story_nearby"
    req: str
    npc: str
    radius: int = 2000
