# Test bench

Runs a scenario inside the game and records what happens: a video with a debug overlay, per-frame
metrics and a report that points at suspicious frames. Built so that an agent can find bugs and edge
cases of a mechanic (foot IK first) without a person watching the game.

```bash
scripts/bench.sh smoke                      # a scenario from scripts/bench/<name>.bench, or a path
NO_BUILD=1 scripts/bench.sh ik-sweep        # skip the incremental build
```

A run takes the game start (~10 s from a save) plus the scenario time. The game window is minimized,
does not take the focus and plays no sound, so it can run while someone works on the same Mac.
The time step is fixed (30 fps), so a run does not depend on the frame rate and repeats (up to the
random idle animations the game picks).

Scenarios for foot IK: `ik-sweep` (12 slopes all over the world: run up, run down, walk across; ~2 min),
`ik-stairs` (8 stairs: walk up and down, run up), `ik-moves` (stand, turn on the spot, strafe, walk
backwards, sneak), `ik-ab` (IK off and on on the same run), `smoke` (the bench itself).
Four legs: `quad-wolf` (the player as a wolf on 6 slopes), `quad-zoo` (molerat, shadowbeast, waran,
lurker, giant bug, sheep on 2 slopes each). The player becomes the creature (`transform`), so the
usual player commands drive it. Gaze: `gaze` (Xardas and the hero talk; the hero stands in front,
goes around him, behind his back and back; then Xardas at his bookstand turns the head only),
`gaze-ambient` (no conversation: Rupert at the market notices the hero walking up, standing, walking,
running and sneaking past; a script look; the hero looks at the npc in focus; a drawn weapon).

Output goes to `.bench/<scenario>-<time>/` (not in git):

| File | What |
|---|---|
| `report.md` | one line per recorded segment, then the worst events with their contact sheets |
| `sheets/<segment>.png` | 16 frames of a segment in a 4x4 grid |
| `sheets/evNN-*.png` | frames around an event |
| `<segment>.mp4` | the video, 800x600, 30 fps |
| `metrics.csv` | a row per frame: player state, IK state, joints before and after IK, ground |
| `bench.log`, `game.log` | what the scenario did, the engine log |

More frames or a comparison:

```bash
scripts/bench_report.py frames .bench/<run> <segment> 20 40      # frames 20..40 in one sheet
scripts/bench_report.py compare <run>/off.mp4 <run>/on.mp4        # side by side, a on the left
scripts/bench_report.py .bench/<run>                              # report again (e.g. after changing a threshold)
```

`compare` also works across runs: the same scenario before and after a change.

Before and after a change, with the same scenarios (the version before is built from `main` in a
temporary worktree, the scenario files are taken from the current one):

```bash
git worktree add --detach /tmp/before main && (cd /tmp/before && scripts/build-engine.sh)
W=$PWD; (cd /tmp/before && scripts/bench.sh $W/scripts/bench/ik-sweep.bench $W/.bench/before-sweep)
scripts/bench.sh ik-sweep
scripts/bench_report.py diff .bench/before-sweep .bench/ik-sweep-<time>   # events per segment, before -> after
git worktree remove --force /tmp/before
```

## Overlay

- thin white: the legs as the animation has them, before IK;
- thick blue (left) and orange (right): the legs after IK;
- crosses on the ground: where the ground rays hit under the ankle (cross) and the toes (dot), green
  under a planted foot, yellow under a swinging one; the short line is the ground normal;
- bottom right: the feet zoomed in;
- top left: frame number and time in the segment (they match `frame` in `metrics.csv`);
- gaze: a yellow line where the face of the watched one looks, a red dot at the partner's eyes; text:
  weight, error `E`, turn `Y` and `P` (degrees), parts of the turn: head `H`, chest `C`, waist `W`;
- top right: IK on/off, weight `W`, pelvis offset `P` and for four legs shoulders offset `C` (cm), per
  leg planted/swinging, contact error `E` (cm), knee deviation from the animation `K` (degrees); `HIDDEN`
  if something is between the camera and an ankle. Four legs: front paws in violet (left) and pink.

## Events (scripts/bench_report.py)

| Flag | Fires when |
|---|---|
| `float` / `sink` | a planted ankle is more than 3 cm above / below the ground it should stand on |
| `pelvis` | the body height (hips or shoulders) jumps more than 2 cm in one frame |
| `toe_sink` | the toes go 3 cm deeper into the ground than in the animation (steps lower than 25 cm) |
| `pop` | the IK correction of a knee, ankle or toe changes abruptly: second difference over 3 frames above 12 cm |
| `knee` | the knee bends more than 25 degrees away from the animated direction |
| `toe_twist` | planted toes turn sideways more than 12 degrees beyond the intended outward turn |
| `stretch` | a planted leg is straight and still more than 2 cm short of the ground |
| `gaze_off` | looking more than 15 degrees past the partner although it is within reach of the turn |
| `gaze_jerk` | the face turns more than 10 degrees in one frame, in the world (the body may turn under a steady gaze); a turn to the partner goes up to 9 |

Contact flags (`float`, `sink`, `toe_sink`) for a single frame count only when they are at least twice
the threshold: a foot leaving the ground a frame early is not visible.

Columns of the table: `contact` is the error of planted feet against a flat animation floor (only
meaningful with IK on); `spread` is how much the height of planted feet above the ground varies (works
for a body tilted as a whole too: compare IK off and on); `limp` is how much lower the body is on the
steps of one leg of a pair than on the other's. `gaze on` is the share of frames the watched one looks
at someone, then how many times it turned to someone and why (`talk`, `script`: AI_LookAtNpc, `focus`:
the hero's, `attention`: the hero near, a weapon drawn). `gaze err` is the angle between where the face
looks and the partner, while the partner is within reach of the turn.

A segment marked `(still)` hardly moved: the player got stuck, the scenario did not do what it meant to.
One reason: an npc with an important dialog came to the hero, and the dialog holds the player;
`bench.log` then says `dialog started: <npc>`. `freeze` that npc first (Lothar at the market).

## Scenario commands

One command per line, `#` starts a comment. The scenario starts once the save is loaded and ends the
game when it is done (`KEEP=1` keeps it open). Anything not listed is passed to the Marvin console
(`cheat god`, `set time 12 0`, `toggle footik`, ...).

| Command | |
|---|---|
| `wait <ms>` | game time |
| `waitarrive [timeout ms]` | until the player has walked where it was sent |
| `goto <waypoint>` | teleport |
| `walkto <waypoint>` | walk there (AI path) |
| `walkmode walk\|run\|sneak` | |
| `face uphill\|downhill\|across\|<yaw deg>`, `face wp <waypoint>`, `face rel <deg>`, `turn <deg>` | turn the player at once; `rel`: the watched npc's facing turned by that much (the angles of `place`) |
| `press\|release\|tap <key>`, `hold <key> <ms>`, `release all` | player input; keys: `forward back left right turnl turnr jump walk sneak weapon action` (`left`/`right` strafe) |
| `cam auto [dist] [height]` | follow the player from the side that sees the feet; switches sides if the feet stay hidden |
| `cam follow <dist> <height> <angle>` | follow, `angle` from the facing: 0 in front, 90 on the left |
| `cam front <dist> <height>`, `cam world <dist> <height> <yaw>`, `cam fixed`, `cam look <height>`, `cam game` | |
| `view clean [margin]\|nopfx\|full` | `clean` clips away everything closer to the camera than the subject minus `margin` (default 100 cm): trees and bushes in between; particles are hidden. The ground right in front of the camera is clipped too (white) |
| `view hide static movable morph pfx animated` | hide draw types (debugging only: static and movable objects share draw commands, hiding them hides parts of creatures too) |
| `overlay on\|off`, `overlay zoom on\|off`, `overlay bright on\|off` | `bright` stretches dark images (forests, interiors) |
| `rec <name> [novideo]`, `stop` | record a segment (video and metrics, or metrics only) |
| `shot <name>` | one PNG |
| `label <text>` | text on the overlay |
| `ik on\|off`, `ik param <name> <value>`, `ik params` | foot IK and its parameters, without rebuilding; `ik off` on a four-legged creature brings back the old whole-body tilt |
| `transform <instance>`, `transform back` | the player becomes a creature (`Wolf`, `Molerat`, `Shadowbeast`, `Waran`, ...), like the shape change spell |
| `bones` | the player's skeleton to `bench.log`: index, parent, name, bind pose position |
| `watch <npc\|nearest\|player>` | the camera and the gaze metrics follow this npc (a script symbol, `NONE_100_XARDAS`) |
| `talk <npc\|nearest> <ms>` | the npc and the hero talk to each other that long (no words): they look at each other |
| `freeze <npc\|nearest> [off]` | the npc leaves what it does and stands still, like a body the AI server attaches |
| `place <dist> <angle>`, `orbit <deg> <ms>` | the hero stands around the watched npc (angle from its facing, 0 in front) or goes around it |
| `gazeinfo` | gaze state of the watched npc to `bench.log`: why it looks, at whom |
| `lookat <npc\|nearest> <npc\|player\|off>` | as the script `AI_LookAtNpc` / `AI_StopLookAt` |
| `nearby [radius]` | the npcs around the hero to `bench.log`: distance, where the hero is from their facing, where they look and why |
| `foreach slope <min deg> <max deg> <count>` ... `end` | the body runs on that many slopes all over the world, after a teleport; `$I`, `$NAME`, `$ANGLE` are replaced |
| `foreach stairs <count>`, `foreach wp <name part> <count>` | the same for stairs and named waypoints |
| `slopes [min] [max]`, `stairs`, `findwp <name part>`, `pos` | print spots / state to `bench.log` |
| `say <text>`, `quit` | |

Environment of `scripts/bench.sh`: `GOTHIC_DIR`, `SAVE` (default: `save_slot_1.sav` of the main
checkout), `NO_BUILD=1`, `TIMEOUT` (seconds, default 300), `KEEP=1`.

Good spots near the default save (Xardas' tower): `NW_XARDAS_TOWER_VALLEY_05` (27 degree slope in a
forest), `NW_XARDAS_TOWER_IN1_*` (ramp inside the tower), `NW_XARDAS_STAIRS_01`..`_04`.

## Engine side

`OpenGothic/common/testbench.cpp`, inert unless `OPENGOTHIC_BENCH` is set. Hooks: the fixed time step and
the capture in `MainWindow`, `FootIk::playerProbe` (legs before and after IK), `Camera::setNearClip`
(`view clean`), `DrawCommands::hiddenTypes` (`view hide`), `Npc::transformInto` (`transform`), and
`TEMPEST_BACKGROUND` in Tempest's macOS backend (no focus, no Dock icon).
