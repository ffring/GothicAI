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

A line `off: <command>` or `on: <command>` runs in that take only: `OPENGOTHIC_BENCH_TAKE=off|on`, set by
`shorts/compare/film.sh` (without it the take is `on`). `OPENGOTHIC_MANUALAIM=0|1` and `OPENGOTHIC_NPCAIM=0|1`
set the hero's and the npc archers' manual aim over Gothic.ini.

Scenarios for foot IK: `ik-sweep` (12 slopes all over the world: run up, run down, walk across; ~2 min),
`ik-stairs` (8 stairs: walk up and down, run up), `ik-moves` (stand, turn on the spot, strafe, walk
backwards, sneak), `ik-ab` (IK off and on on the same run), `smoke` (the bench itself).
Four legs: `quad-wolf` (the player as a wolf on 6 slopes), `quad-zoo` (molerat, shadowbeast, waran,
lurker, giant bug, sheep on 2 slopes each). The player becomes the creature (`transform`), so the
usual player commands drive it. Gaze: `gaze` (Xardas and the hero talk; the hero stands in front,
goes around him, behind his back and back; then Xardas at his bookstand turns the head only),
`gaze-ambient` (no conversation: Rupert at the market notices the hero walking up, standing, walking,
running and sneaking past; a script look; the hero looks at the npc in focus; a drawn weapon).
Lean: `lean` (running in circles left and right, a slalom, walking in a circle, running and walking up
and down a 27 degree slope). With `lean off` on top of a copy it gives the same run without the lean,
for `bench_report.py diff`.
Transitions between animations: `anim` (the hero on a flat floor: start and stop, walk to run, turning
on the spot, strafing, walking backwards, jumps, sneaking, fists: attacks, a combo, a block). With
`blend off` on top of a copy it gives the old cross-fade, for `diff`; `gaze-ambient` and `quad-zoo` show
an npc's and the creatures' transitions too.
Aim: `aim` (the hero draws a bow at a sheep hung level, above, steeply above, to the side; the same with
aim off; a crossbow; a slope; down from three ledges near Xardas' tower, 17, 36 and 46 degrees, and
from a ship's deck at 70 and 80 degrees; a fire bolt; three shots in a row; last a sheep right at the
foot of a gallery's wall in Xardas' tower, 78 degrees down: hidden from the head, so without the aim no
focus; with it the hero leans over the edge, the focus takes it, the arrow kills it).
Flinch: `flinch` (a frozen bandit: blows only to the flinch from the front, behind, both sides, to the
head, off the middle, a weak one, three in a row, the same with the flinch off; an arrow in his back;
the hero's fist from the front, behind and the side as the game deals it, with its stumble; his sword
swung from the left, the right and straight (a swing pushes along the blade); the hero
struck standing and while aiming a bow, where the game has no stumble; a wolf: the head only).
Lip sync: `lips` (Xardas says a voiced line of the game, the camera at his face; then the same line
with the old talk loop), `lips-dialog` (the hero talks to Xardas through the dialog window and picks
the first choice: the lines of both are played the way the game plays them).
Faces: `faces` (the face animations of a head one after another, then two of them while Xardas speaks),
`face-fade` (faces fade in and out, one gives way to the next, three a moment apart, the lips over a
fading face), `mood-ai` (lines of an AI server with moods: start `gothic-ai serve --scenario m1 --llm fake
--no-tts` on a port of your own and run the bench with `GOTHIC_AI_URL=ws://127.0.0.1:<port>`; with a TTS
service instead of `--no-tts` the lines are voiced in their mood). Columns `face_neutral`, `face_friendly`,
`face_angry`, `face_hostile`, `face_afraid`, `face_pain` of `metrics.csv`: how much of each face of the
watched npc shows, 0..1.
The necromancer's answer: `necro` (docs/xardas-wrath.md: Xardas kills the hero with a spell and raises him as
a skeleton, the screen goes black and the end card comes up; `ai necro`, no server needed).
Blinks: `blink` (Xardas' face close up: quiet, saying a line, his eyes jumping after the hero put here
and there, a frightened face, the eyes closed; with `blink off` on top of a copy nobody blinks, as
OpenGothic was). Columns `bk_on`, `bk_w` (how shut a blink keeps the eyes, 0..1), `bk_n`, `bk_pause`,
`bk_shift`, `bk_double` (counters: blinks, of them in a pause of the voice, at a shift of the eyes, the
second of a double one).
Gestures: `gestures` (the hero picks the first dialog choice of Xardas three times, the camera at his
chest: his hands while he speaks; with `gestures off` on top of a copy the old way, a random gesture
when a line starts and none plays). For the moods, the same camera with an AI server (`mood-ai`
above): one line `friendly`, two `angry`. Columns `ge_on` (gestures go with its lines), `ge_talk` (its
line is being said), `ge_id` (the gesture playing, 1 is `T_DIALOGGESTURE_01`), `ge_mood` (0 neutral,
1 friendly, 2 angry, 3 hostile, 4 afraid, 5 pain), `ge_starts`, `ge_cuts`, `ge_stops`, `ge_nods` (counters),
`ge_pitch`, `ge_yaw`, `ge_roll` (degrees: how the voice turns the head, a nod is pitch down), `ge_listen`,
`ge_think` (it listens to the hero, it thinks of an answer), `ge_lnods` (nods as the hero's words come),
`ge_aside` (how far the eyes are aside while it thinks, 0..1).
Listening: `listen` (Xardas' face close up: quiet, the hero speaks to him 6 s, he thinks 2.5 s, he answers;
with `gestures off` on top of a copy he stands still, as before).
Deals: `deal` (the deal window of an AI server, the interface in the frames: start `gothic-ai serve
--scenario deal_demo --llm fake --no-tts` on a port of your own and run the bench with `GOTHIC_AI_URL`;
Xardas offers a thing he has for 50 gold the hero lacks, then for 30, and the hero accepts).
Two people talking: `gossip` (Lobart and Hilda talk about the hero's lie at Lobart's farm, each line said to
the other: start `gothic-ai serve --scenario gossip_demo --llm fake --no-tts` on a port of your own and run
the bench with `GOTHIC_AI_URL`; the server puts Hilda 5 m from Lobart and the hero 7 m from them; `nearby`,
`pair` and `where` every second: during each line both look at each other, `look=talk at=<the other>`).

Output goes to `.bench/<scenario>-<time>/` (not in git):

| File | What |
|---|---|
| `report.md` | one line per recorded segment, then the worst events with their contact sheets; a "Manual aim" table when the bow was aimed by hand: shots, hits by zone, the sway at full draw, the draw time; the tables of the arrows, the dice and the damage when the log has them (below) |
| `report.json` | the numbers of `report.md`: per segment, the events, and the tables of the arrows, the dice and the damage (`archery`, `archery_predicted`, `dice`, `damage`, `time_to_kill`, `fights`) |
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
scripts/bench_report.py aggregate .bench/<run a> .bench/<run b>  # the arrows, dice, damage of several runs, by segment
```

`compare` also works across runs: the same scenario before and after a change.

The arrows, the dice and the damage come from the game's notes in `bench.log` (`arrow shot`, `arrow aim`,
`arrow hit`, `arrow missed`, `dice shot`, `arrow dice`, `damage`, `fight over`; `by` names the shooter, `PC_HERO`
when a line of an older run does not say), their times from the `note` rows of `events.jsonl` (none in older
runs: no rates then). A table is there only when its lines are:

| Section | What |
|---|---|
| Archery accuracy | per segment and shooter: shots, shots a minute; each hand's arrow flown from `arrow shot` (`p = from + v·t - ½·g·t²`) to where it passes closest to the point meant (the hero: the last `aim zone` before it; an npc: `true` of its `arrow aim`), the miss as the shooter sees it in degrees, + right / + up: bias, σ (RMS about the bias, 95% CI by chi-square; MAD / 0.6745); an npc's row `- err` with its own `err` taken off (the body and the bow alone); hits by zone with Wilson 95% intervals; the hero's `stats` (skill, sway, cone) or the npc's aim (`sigma`, settled / waited / flinch) |
| Predicted hits from σ | when `zones` dumped the target in the same log: its capsules seen from the shooter in 1 cm cells (the first capsule a ray enters takes the cell, as `HitZones::rayTest`) under a Gaussian of the bias and σ above: P(head), P(body), P(limb), P(any) against the counted. The dump is moved to where the shots aimed when the target stood elsewhere (`dump moved cm`; the hero aims at the middle of its `aimat` zone, an npc's point meant counts for the trunk's); its turn is not |
| Dice | per segment and shooter: `dice shot`s, rolls (`arrow dice`: arrows that reached someone), the mean logged chance, expected hits Σp ± sqrt(Σp(1-p)), hits, z; each roll again under OpenGothic's chance (by the path) and vanilla G2's (by the straight distance, truncated; hit iff roll < chance): mean chance, hits, outcomes that flip against the log; how much longer the path is than the distance |
| Damage and fights | per segment, shooter and victim: shots (melee and spells: the blows that landed), hits by zone, net damage per hit, damage a minute (per shot times the cadence), the cadence (gaps between the shooter's shots), `fight over` of the victim |
| Time to kill | each sequence of shots (time, raw, k, hit) replayed on hero ch1 160 HP / 20 protection, ch3 400/50, ch4 520/100, Bandit_M 160/35, a paladin 520/100: from every shot as the first one, round the sequence (after its last shot the mean gap), the time and the shots until the HP is gone: mean and 5-95%. Per hit `max(round(raw·draw) - prot, 0)·zone` (head 2, body 1, limb 0.6) as the game does since 2026-10-13, or as it did before, the zone before the armour `max(round(raw·k) - prot, 0)`; at least 5 a hit (G2) |

`aggregate` pools the shots of several runs (repeats of a scenario) by segment name and prints the same tables;
gaps and sequences stay within a run.

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
- lips (under the gaze text): `F` the two mouth shapes (VISEME frames) mixed with `A`, intensity `I`;
  the thin white bar is the voice, the thick blue one how wide the mouth is shown;
- gaze: a yellow line where the face of the watched one looks, a red dot at the partner's eyes; text:
  weight, error `E`, turn `Y` and `P` (degrees), parts of the turn: head `H`, chest `C`, waist `W`;
- orange `AIM`: weight, error `E` (where the arrow points after the turn against where the shot leaves),
  where the shot has to go `Y`, `P`, the turn `T` and the bend `B` (+ up) of the upper body, degrees;
- red `HIT`: blows so far `#`, strength `S` and height `H` (0 the hips, 1 the head) of the last one, the
  trunk's bend `B` away from it and where its top goes `Y` (+ to the right of the facing), the twist `T`
  about up and the head `N` against the trunk, degrees;
- green `LEAN`: roll `R` (+ to the left) and trunk pitch `P` (+ forward) in degrees, speed `V` (cm/s),
  turn `T` (degrees per second, + to the left), slope `S` along the facing (degrees);
- violet `BLEND`: bones blending from a switch of animations `N`, the largest rotation offset `A` (degrees);
- `BLINK`: blinks `N`, of them in a pause of the voice `P`, at a shift of the eyes `S`, second ones `D`;
- `GEST`: `TALK` while the watched one speaks, the gesture playing `G`, the mood of its line, gestures
  started `S`, started over one `C`, stopped early `X`, nods `N`, the nod now `P` (degrees); `THINK` or
  `LISTEN` instead of `TALK` while it thinks of an answer or listens to the hero;
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
| `aim_off` | while the bow holds still, the arrow points more than 3 degrees past where the shot leaves although the turn could reach it |
| `gaze_jerk` | the face turns more than 10 degrees in one frame, in the world (the body may turn under a steady gaze); a turn to the partner goes up to 9 |
| `anim_pop` | a joint of the animated pose (before lean, IK, gaze) jumps at a switch of animations (a frame before it to 6 after): second difference over 3 frames above 20 cm; the event names the bone and the animations before and after |
| `lip_open` | the mouth is open wider than 0.3 (of the widest shape) while the voice is silent, 4 frames or more |
| `lip_shut` | the voice is louder than 0.7 while the mouth is shut (under 0.1), 4 frames or more |
| `face_jump` | a face comes or goes at once: its weight changes more than 0.5 in one frame (faces fade in about a second, `T_HURT` in 0.1 s) |
| `flinch_none` | a blow, and the upper body gives way less than 1.5 degrees in the 8 frames after it |
| `flinch_slow` | the upper body is not back 1.5 s after the last blow |

Contact flags (`float`, `sink`, `toe_sink`) for a single frame count only when they are at least twice
the threshold: a foot leaving the ground a frame early is not visible.

Columns of the table: `contact` is the error of planted feet against a flat animation floor (only
meaningful with IK on); `spread` is how much the height of planted feet above the ground varies (works
for a body tilted as a whole too: compare IK off and on); `limp` is how much lower the body is on the
steps of one leg of a pair than on the other's. `gaze on` is the share of frames the watched one looks
at someone, then how many times it turned to someone and why (`talk`, `script`: AI_LookAtNpc, `focus`:
the hero's, `attention`: the hero near, a weapon drawn). `gaze err` is the angle between where the face
looks and the partner, while the partner is within reach of the turn. `lean roll / pitch` is the largest
roll into a turn and the range of the trunk pitch (+ forward) of the watched one. `aim err` is the
angle between where the arrow points and where the shot leaves (while the bow holds still), `bend` the
largest bend of the upper body (+ up). `lips corr` is the correlation of the voice level and the shown
mouth opening over the frames lip sync is on (~0.8-0.9 on Xardas' lines). `anim pop` is the second
difference of the joints of the animated pose (cm, the largest joint per frame): p99 and max over the
segment, then the number of switches of animations and the largest value next to one. Within an
animation it is how the animation moves (a running human 11-25 cm, a running waran up to 80): compare
the switches, with `blend off` and on. `hits, peak, back` are the blows the watched one took, the
largest flinch (trunk bend, twist or head, degrees) and how long it took to come back after the last
blow. `gaze_jerk` is not counted for a second after a blow: it throws the head on purpose.
`blinks /min` counts the blinks seen (the eyes going half shut), with those in a pause of the voice and
at a shift of the eyes; people blink some 15-25 times a minute, more while they speak. Flag `blink_shut`:
a blink keeps the eyes shut longer than 7 frames.
`gestures talk / listen %` is how much of the time the watched one speaks a gesture plays, and how much
of the time it is quiet again (a second after its voice), then the gestures started, started over one
that played and stopped before their end (the voice is over), and the nods on stressed syllables. The head
moving with the voice adds a few degrees to `gaze err` while one speaks.

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
| `press\|release\|tap <key>`, `hold <key> <ms>`, `release all` | player input; keys: `forward back left right turnl turnr jump walk sneak weapon melee bow spell action parade breath` (`left`/`right` strafe; aiming a bow: hold `action`, a shot: `tap forward` while holding it; in the manual aim holding `action` draws, releasing shoots, `parade` lowers the bow, `breath` (= walk) holds the breath) |
| `aimwalk on\|off` | the legs stepping under the aim (graphics/mesh/aimwalk); off: forward and back do nothing while aiming |
| `animinfo <name>...` | the hero's animations of those names: layer, frames, the nodes they move (when not all), the root's way and speed, the hips' turn at a few frames |
| `wheel <delta>` | the mouse wheel as the window gets it (-1 down, 1 up): aiming a bow by hand, down cants it for a quick shot, up straightens it |
| `cam auto [dist] [height]` | follow the player from the side that sees the feet; switches sides if the feet stay hidden |
| `cam follow <dist> <height> <angle>` | follow, `angle` from the facing: 0 in front, 90 on the left |
| `cam front <dist> <height>`, `cam world <dist> <height> <yaw>`, `cam fixed`, `cam hold`, `cam look <height>`, `cam game` | `fixed` stays where it is and turns after the player; `hold` keeps the frame as it is (a scene the player may fall out of) |
| `cam arrow [dist 600] [height 40] [side 1\|-1] [ahead 80]` | from the side of the newest arrow's way (`overlay trail` records it), `ahead` cm in front of it, kept 1.2 m over the ground; from the hero until an arrow flies. The arrow's drop reads only from the side |
| `slowmo <k>` | the game runs k times slower than the video (1: normal, up to 20): a smaller game step per recorded frame. The game's sound is recorded as silence while slowed, so it stays in step with the picture |
| `track <npc\|off> [head\|body]` | each recorded frame, where the npc is on the picture: `events.jsonl` kind `track` with `x y` (the head, or the trunk with `body`), `hx hy`, `bx by`, `lx ly` (the head, the trunk, the legs: the limb capsules below the knees) in pixels of the frame, and `w h`. For labels that follow the npc in a cut |
| `view clean [margin]\|nopfx\|full` | `clean` clips away everything closer to the camera than the subject minus `margin` (default 100 cm): trees and bushes in between; particles are hidden. The ground right in front of the camera is clipped too (white) |
| `view hide static movable morph pfx animated` | hide draw types (debugging only: static and movable objects share draw commands, hiding them hides parts of creatures too) |
| `overlay on\|off`, `overlay zoom on\|off`, `overlay bright on\|off` | `bright` stretches dark images (forests, interiors) |
| `overlay trail [off] [keep ms] [line]` | the arrows' ways in gold (with the zones of `showzones`), no legs, no text; turns the overlay on. A way stays `keep` ms (1500) after its arrow stops; `line`: also the straight line the arrow left along (where the bow pointed), dashed white, as far as the arrow has flown: the drop is the gap between them |
| `rec <name> [novideo]`, `stop` | record a segment (video and metrics, or metrics only) |
| `shot <name>` | one PNG |
| `ui on\|off` | the interface in the captured frames, as the player sees it: subtitles, the input line, the deal window (off by default: only the world) |
| `label <text>` | text on the overlay |
| `ik on\|off`, `ik param <name> <value>`, `ik params` | foot IK and its parameters, without rebuilding; `ik off` on a four-legged creature brings back the old whole-body tilt |
| `transform <instance>`, `transform back` | the player becomes a creature (`Wolf`, `Molerat`, `Shadowbeast`, `Waran`, ...), like the shape change spell |
| `bones` | the player's skeleton to `bench.log`: index, parent, name, bind pose position |
| `watch <npc\|nearest\|player>` | the camera and the gaze metrics follow this npc (a script symbol, `NONE_100_XARDAS`) |
| `talk <npc\|nearest> <ms>` | the npc and the hero talk to each other that long (no words): they look at each other |
| `freeze <npc\|nearest> [off]` | the npc leaves what it does and stands still, like a body the AI server attaches |
| `place <dist> <angle>`, `orbit <deg> <ms>` | the hero stands around the watched npc (angle from its facing, 0 in front) or goes around it; not where no ground is found (inside a rock: it would fall through the world) |
| `gazeinfo` | gaze state of the watched npc to `bench.log`: why it looks, at whom |
| `lean on\|off` | the lean into turns and on slopes |
| `blend on\|off`, `blend param <name> <value>` | transitions between animations (`AnimBlend`; off: the old cross-fade); params `min`, `max`, `scale` (blend time, s), `turn` (rad/s), `move` (cm/s), `out`, `side` |
| `flinch on\|off` | the upper body giving way to blows |
| `hit <npc\|player> <angle> [strength] [height] [twist]` | a blow from that side of it (angle from its facing, 0 in front, as `place`) to the flinch only: no damage, no stumble; strength 0..1 (1), height 0 the hips .. 1 the head (0.6), twist -1..1 about up (0) |
| `strike <victim\|player> [attacker\|player]` | the attacker's blow lands as the game deals it (the stumble, the damage, the flinch); the victim gets its hit points back below a third |
| `clear [radius]` | the npcs around the hero (2500 cm) leave the world, not the watched one: rats, wolves that would join in |
| `aim on\|off` | the upper body turning to the target with a bow, a crossbow, a spell; leaning over an edge (and the focus seeing from there) |
| `manual on\|off` | the manual aim (`game/archery`, `GAME/manualAim` in Gothic.ini, on by default; `aim.bench` turns it off to test the game's aim): the hero aims a bow by hand, no dice: the arrow hits where it flies (bones), the sway comes from the skill, strength, dexterity |
| `bowdraw on\|off` | the hand draws the string as far as the bow is drawn by hand (`graphics/mesh/bowdraw`): the bow held as an archer holds it (up in the hand, the arrow on the hand at the bow's middle, the string drawn straight back: the `S_DRAWN` morph), the nock straight along the arrow as far as the arm reaches, the string with it; the hand follows through after the shot; in the reload the upper body holds the aim and the hand goes round the head to the quiver and back to the string. `bow-draw.bench` films it close by. CSV `bd_w bd_draw bd_span bd_travel bd_miss bd_follow bd_fit bd_offset bd_angle` (the string against the arrow), `hd_hand hd_fore hd_arrow` (cm from the head's capsule, < 0 in it), `bd_hold bd_nock` (the reload: held; 0..1 to the quiver, 1..2 to the string), `rh_x rh_y rh_z` (the drawing hand in the body's frame), `bd_held bd_overgrip bd_atmiddle bd_nockgap bd_square bd_cant bd_bowmove bd_bowmiss` (the archer's pose, `docs/manual-aim.md`: held so 0..1, cm the arrow is over the grip and over the bow's middle, cm from the nock to the string, degrees between the arrow and the limbs, degrees the bow leans, cm the bow hand raises the bow and does not reach; `bd_nockgap` -1: no arrow nocked) |
| `bowgeom` | the bow and the bow hand of the hero in the arrow's frame (x along it from its slot, y up, z to the side), cm: the bow's box, the grip, the string's middle at rest and drawn by `S_READY` and `S_DRAWN`, the bow hand's bones, the head |
| `animspeed <anim> <k>` | the hero's animation of that name plays k times as fast from its next start (`Pose::setAnimSpeed`: a layer keeps its speed, the next one starts at 1). The manual aim sets `T_BOWRELOAD` from dexterity (`stats` prints it: `reload x`); this overrides it until dexterity changes. The hero's animation events go to bench.log (`anim event <type> <item> <slot>`, `anim morph <anim> <slot>`): at any tempo each once, in the same order |
| `sfx <name>...` | the files a sound effect plays (its variants `_A1`..), `(missing)` if the game's data has no such file. The hero's sounds go to bench.log (`sound <name>`) |
| `stats [npc <npc>] [str <n>] [dex <n>] [bow <n>] [cbow <n>] [hp <n>] [hpmax <n>] [talent auto\|0\|1\|2]` | the hero's (an npc's) strength, dexterity, hit chances (the skill), hit points; prints what the manual aim makes of them (sway, draw, hold, arrow speed). `talent`: the trained archers' animations (`_BOWT1` at 30%, `_BOWT2` at 60% in G2; `auto` as the hit chances say); with it or `npc` also `stats of <npc>: talent, reload ms, hp, point protection` |
| `seed <n>` | the random numbers start over: the scripts' (the dice, the release cone), the bench's (`volley` jitter), the hero's sway |
| `npcequip <npc> <item> [count]` | into the npc's inventory, the first put on |
| `arc <npc> <count> <dist> <step deg>` | that many of the npc stand that far in front of the hero, a step apart round it, facing it: the crew of `fight all` (a new arc replaces it, `clear` takes the gone out) |
| `fight <npc\|all> <target\|player>` | it attacks as the game starts a fight (`ZS_ATTACK`, attack reason to kill: archers keep the bow beyond 9 m) |
| `waitdown <npc\|player> [ms]` | the scenario waits till it is down: `fight over: <npc> down after <ms>`, else `waitdown: timeout` |
| `npcaim on [second\|arrow] \| off \| sigma <deg\|off> \| lead on\|off` | npc archers aim by hand (game/archerai, `[GAME] npcManualAim`, on by default: `npcaim off` for the dice): as many hits a second as G2's dice (`second`) or as sure an arrow (`arrow`); an error forced, degrees per axis (0: the bow's own spread only); the lead on a runner. Each release logs `arrow aim: at, true <the point meant>, err <right up> deg, sigma, pv <dice %> p <target %> rho, lead, hold, disturb, settled\|waited, by <npc>` |
| `archer <npc\|all> at <npc\|player> [dice] \| archer off` | the archers stand where they are and shoot at the target every time they can, by hand (npcaim on) or by the dice: no fight AI, no perception's wait, arrows topped up (the aim's calibration) |
| `still <npc\|player> [off]` | hit, it neither stumbles nor flinches: a target that keeps its pose (as its zones were dumped) |
| `aimat <npc\|nearest> [head\|body\|limb\|leg] [raw]`, `aimat <x> <y> <z>`, `aimat ahead [dist] [height]`, `aimat off` | the manual aim aims at the middle of that zone of the npc (every frame) or at a point, instead of the mouse; the hero faces it. Over it by the drop of the arrow at full draw, as an archer holds over (`raw`: right at it). `limb` is the middle of all the limbs, which lies inside the trunk; `leg`: the limb capsules below the knees |
| `open [dist cm]` | how far it is open around the hero at head height and at the knees every 15 degrees from the facing: where to put a target |
| `volley <shots> [hold ms] [draw 0..1] [jitter ms]` | shots by hand in a row: press `action`, draw that far (1: full), hold (up to `jitter` longer at random: the sway is a function of time), release, wait for the next arrow; every arrow is in the log (`arrow shot: ..., by <npc>`, `arrow hit <npc>: <zone>, hp a -> b, draw, flew, at, by <npc>` or `arrow missed: ..., by <npc>`) |
| *(log)* | the dice of npc shots: `dice shot: by <npc> at <target>`, `arrow dice <victim>: dist <cm> path <cm> talent <%> chance <%> roll <0..99>, hit\|miss, by <npc>`; every blow: `damage <victim>: raw <n> zone <z> k <x> prot <n> net <n> hit 0\|1, hp <n>, precise\|dice\|melee\|spell, by <npc>`; every note also in `events.jsonl` (`kind: note`, with the time) |
| `immortal <npc\|nearest> [off]` | arrows hit it, it does not die: counting hits |
| `zones <npc\|nearest\|player>` | the hit zones of the npc now (`graphics/mesh/hitzones`): each capsule (radius, the guess by the bone's name) and how well they cover its body: of each zone, the points of the body in it / only in another zone / in none, and the body's mesh. The report sums them up (`Hit zones`) |
| `zones fit on\|off` | the hit zones as thick as the body's mesh (on, the default) or guessed by the bone names and the creature's size; the hero refitted at once, the others when they change their body (`transform`) |
| `showzones <npc symbol>\|player\|off`, `overlay zones [off]` | the overlay draws the hit zones of that npc (the head red, the trunk yellow, the limbs blue); `overlay zones`: nothing else (no legs, no text), and turns the overlay on. To film the zones of a distant target, stand it with `put` (`pin` floats it into a jump pose). `zones.bench`: 31 creatures with the guess and fitted |
| `corner <left\|right> [count]`, `corner <left\|right> go <i>` | corners of walls near the waypoints within 80 m: a wall right in front of the head, open 6 m ahead leaning out to that side (the manual aim's peek); lists them nearest first, or puts the hero at the i-th facing along it |
| `aim-stance.bench`, `aim-corner.bench` | the manual aim's stance: crouched (the sneak key, `ik_crouch`), leaning out (the side steps held, `a_side`, room checked by a ray beside the head); from the side, from behind and from the game's camera; at a corner of a house in the harbour. CSV `a_side ik_crouch m_crouch m_peek` |
| `archery-smoke.bench` | the stage 4 tools: who shot, the dice and the damage lines, `seed` twice the same, `volley` with jitter, an npc's `stats`/`talent`, `arc`, `fight` (by the dice), `waitdown` |
| `npc-today.bench` | npc archers by the dice as the game had them (`npcaim off`): 4 bandits with bows, addon bandits with crossbows, paladins, at 11 / 13.5 / 16 m set out (the fight AI moves them), the hero immortal and still; the Dice table checks the throw against G2's formula |
| `npc-mech.bench` | npc archers aiming by hand where they stand (`archer`): the bow's own spread (`npcaim sigma 0`), with G2's dice as aim, by the dice; a fight; one archer filmed close up |
| `hero-balance.bench` | the hero by hand against G2's dice: four tiers as G2 has them (talent 10/30/60/100 with its bow, strength and dexterity, the trained animations), a farmer at 10, 20 and 30 m, body and head, 30 shots a cell with a random hold and a seed each; the Archery accuracy table gives the hits and shots a minute |
| `npc-lead.bench` | npc archers at a runner: the hero runs sideways to and fro 12 m in front of a bandit and a paladin, by hand with the lead, without it, by the dice |
| `npc-fight.bench` | fights with 4 npc archers of a kind (bandit bow, addon crossbow, paladin): the dice, by hand per second, per arrow; damage a minute and the time to kill from the Damage table; bandits set out at 25 m running in; a real duel at the end |
| `aim-walk.bench` | steps with the bow drawn (forward and back while aiming by hand): the legs sneak under the aim, the hero moves as they step; on open grass from the side (no overlay), in front, behind, the game's camera with shots on the way, crouched; the hits at 10 m standing and stepping. CSV `m_walk aw_w aw_speed aw_twist aw_chest aw_drop` |
| `aim-cant.bench` | the bow canted for a quick shot (the mouse wheel, `wheel <delta>`: down cants, up straightens): round the arrow, the top limb toward the drawing hand; from the side, in front, as the target sees it, behind, the game's camera, aimed up and down; the hits upright and canted at 8 and 25 m. CSV `m_cant bd_cant` |
| `crossbow.bench` | the crossbow by hand: a weak and a strong shooter (`stats ... reload x`: the reload by strength), lowered loaded and raised, lowered right after a shot; the bolt lies in the left hand, the aim lines it up (`bowgeom` prints where it lies) |
| `give <item> [count]`, `equip <item>` | into the hero's inventory; `equip` puts it on whatever the strength (`ItRw_Bow_L_01`, `ItRw_Arrow`, `ItRw_Crossbow_L_01`, `ItRw_Bolt`, `ItRu_FireBolt`) |
| `put <npc\|nearest> <dist> <angle>` | the npc stands there on the ground from the hero (angle from the hero's facing), frozen |
| `pin <npc\|nearest> <dist> <angle> <height>`, `pin off` | the npc hangs there from the hero, `height` above the hero's feet, every frame: a target above or below |
| `anims` | what the watched one (or the hero) plays, its body state, weapon and target |
| `cansee <npc>` | can the hero see it: from the head, leaning over an edge (the aim's lean); what the rays hit; the bow's focus range |
| `hp <npc> [full]` | hit points of the 3 nearest npcs of that script symbol and how far they are (did the arrow hit); `full`: the nearest gets them all back first |
| `walls [count]` | spots at the edge of a wall (from a waypoint walking to where the ground falls away) with its foot 2.5..10 m below, 1 m out, hidden from the head and seen leaning over: the waypoint and the yaw for `goto`, `edge` |
| `npcgo <npc\|nearest> <waypoint> [walk\|run]` | the npc goes there by the AI path, running by default (`freeze` it first so its routine does not take it back) |
| `lookat <npc\|nearest> <npc\|player\|off>` | as the script `AI_LookAtNpc` / `AI_StopLookAt` |
| `nearby [radius]` | the npcs around the hero to `bench.log`: distance, where the hero is from their facing, where they look and why |
| `pair <a> <b>` | how two npcs stand to each other, to `bench.log`: the distance; for each, where it faces (`at_other`, 0: right at the other), where it looks and why (`look`, `at`), its script state and for how long (`state`, `t` ms), body state, whether it uses a mob, how much of its AI runs (`ai`: `normal`, `far` beyond 30 m from the hero, `far2` beyond 60 m) |
| `where <npc> [waypoint]` | where it is, how far from the hero (and from the waypoint), its state, whether its queue is busy (walking there), how much of its AI runs |
| `state <npc> <ZS_*> <waypoint> [minutes]` | it does that at that place now, as a routine entry would (`TA_*`), for that many game minutes (60), then its routine goes on (`npc_act start_activity`) |
| `come <npc>` | it walks up to the hero and turns to him (`npc_act go_to_npc`); a free one goes back to its routine after |
| `cam face <dist> [angle] [height]` | close to the face of the watched one, looking at the mouth; `angle` from its facing |
| `speak <npc\|nearest\|player> <output name\|$SVM> [to]` | it says a voiced line of the game (`DIA_Addon_Xardas_Hello_14_00`), like `AI_Output` in a script, or an SVM (`$SMALLTALK01`, like `AI_OutputSvm`); to the hero, or to the npc `to`: it turns to it first, as a line of the AI said to an npc does (`say.to`) |
| `dialog <npc\|nearest>` | the hero talks to it, as with the action key: the dialog window opens |
| `choose <n>` | runs the n-th choice (from 1) of the dialog with the watched npc, as if the player picked it |
| `faceanim <npc\|nearest\|player> <anim> [ms] [intensity]` | a face animation of the head (`S_ANGRY`, `S_FRIENDLY`, `T_HURT`...) |
| `lips on\|off`, `lips param <name> <value>`, `lips params` | lip sync (off: the old talk loop) and its parameters |
| `gestures on\|off`, `gestures param <name> <value>`, `gestures params` | the hands while one speaks (`TalkGestures`; off: a random gesture when a line starts) and its parameters: `Tail`, `Wait`, `MinLeft`, `Slack` (ms), `Long`, `RestScale`; the head: `Nod` (degrees), `NodHz`, `Stress`, `NodGap` (ms), `SwayYaw`, `SwayRoll` (degrees), `SwayTime`, `SwayLag` (ms); listening and thinking: `ListenSway`, `ListenNod` (degrees), `ListenHz`, `ListenGap` (ms), `ListenProb`, `DoubleNod`, `ThinkDelay` (ms), `ThinkYaw`, `ThinkPitch`, `ThinkTilt` (degrees), `AsideTime` (ms) |
| `blink on\|off`, `blink param <name> <value>`, `blink params` | blinking (`EyeBlink`; off: nobody blinks) and its parameters: `MinGap`, `RestMean`, `SpeakMean`, `MaxGap`, `Hold`, `PauseMs`, `SoonMs`, `DoubleGap` (ms), `PauseProb`, `ShiftProb`, `DoubleProb` |
| `hear <npc\|nearest> <ms> [every ms]` | the hero speaks to it that long, his words come every so often (600 ms), as the bridge passes the partial transcripts of his voice: it listens, nods now and then |
| `think <npc\|nearest> <ms>` | it thinks of an answer that long, as on the AI server's `thinking` status |
| `ani <npc\|nearest\|player> <anim>` | it plays an animation of its model script (`T_YES`, `T_DIALOGGESTURE_03`) |
| `foreach slope <min deg> <max deg> <count>` ... `end` | the body runs on that many slopes all over the world, after a teleport; `$I`, `$NAME`, `$ANGLE` are replaced |
| `foreach stairs <count>`, `foreach wp <name part> <count>` | the same for stairs and named waypoints |
| `slopes [min] [max]`, `stairs`, `findwp <name part>`, `pos` | print spots / state to `bench.log` |
| `edge <yaw> [max cm] [margin cm]` | the hero is put that way to where the ground falls away, `margin` (35) from the edge facing it (closer the body slips over it); prints the drop past it (a wall, a ship's side) |
| `ledges [min drop] [max drop] [count] [steep]` | waypoints at an edge with open ground that much lower 1.5..14 m away, seen from there (a target below): the yaw to `face`, the distance, the drop, the angle down; nearest first or the steepest |
| `say <text>`, `quit` | |

Talking, for shorts (`shorts/`; needs the AI server, see `shorts/README.md`):

| Command | |
|---|---|
| `ptt <wav> [text]` | the hero holds the talk key while the recording is said (`ai ptt start <wav>`: the AI server takes the file instead of the microphone, `--voice-files`) |
| `waitreply [timeout ms] [quiet ms]`, `waitquiet [quiet ms] [timeout ms]` | until an npc of the AI has answered and has been quiet that long; until none speaks. An npc thinking of its answer is not quiet: the "Хм..." it says at once is not the answer |
| `waitai <key> <stage> [timeout ms]` | until a thing of the AI bridge has got that far: `waitai necro fallen` (the necromancer's answer: cast, fallen, raise, risen, card; `ai necro <npc> [spell] [picture]` starts it without the server) |
| `near <npc> [dist] [deg]`, `approach <npc>`, `face npc <npc>` | the hero in front of an npc (teleport), walks up to it, turns to it |
| `cam shoulder\|face\|two <npc> [dist] [height] [side\|deg]`, `cam cut`, `cam smooth <k>` | shots of the hero and an npc; they glide from one to the next unless `cut`. `cam face` with an npc's name is this shot, with a number the close-up above |
| `caption <text>`, `mark <text>` | events for the cut of a short |
| `ai <...>` | the AI bridge's console: `ai attach <npc>`, `ai ptt start [<wav>]\|stop\|cancel` |

Every talking command writes `events.jsonl` (what was said when, in frames of the recording). A line of the
AI is `npc line <npc>: <text>` in `bench.log`, `npc line <npc> -> <to>: <text>` when it is said to another npc
(its `npc_line` event has `to`); a sound said at once while the model thinks («Хм...») has `"filler": true`. What the npcs say through the game's own path, lines of the game and SVMs
(`AI_Output`, `AI_OutputSvm`, the chatter of two in `ZS_SMALLTALK` too), is `output <npc> -> <target> <name>
<ms>` (0 ms when the engine did not look the length up; `far` or `far2` after it when the npc's AI is cut down
by its distance to the hero: such a line is never heard), and an `output` event.

Environment of `scripts/bench.sh`: `GOTHIC_DIR`, `SAVE` (default: `save_slot_1.sav` of the main
checkout), `NO_BUILD=1`, `TIMEOUT` (seconds, default 300), `KEEP=1`, `OPENGOTHIC_TEXTURES` (sharper
textures; by default those of `scripts/hd-textures.sh` when they have been made, as the launcher does;
empty: the game's own), `SOUND` and `MUSIC` (volumes 0..1, default 0; with `TEMPEST_AUDIO_LOOPBACK=48000` a
segment's sound goes into `<segment>.wav`).

Film mode (what `gothic-shorts film` sets): `OPENGOTHIC_BENCH_REALTIME=1` keeps the fixed steps to the wall
clock and draws only the captured frame; `TEMPEST_AUDIO_LOOPBACK=48000` records the game's sound into
`<segment>.wav` instead of playing it; `TEMPEST_WINDOW_SIZE=<w>x<h>` (points) sets the frame;
`OPENGOTHIC_BENCH_ENCODER` replaces the x264 options (e.g. `-c:v h264_videotoolbox -b:v 24M`).

Good spots near the default save (Xardas' tower): `NW_XARDAS_TOWER_VALLEY_05` (27 degree slope in a
forest), `NW_XARDAS_TOWER_IN1_*` (ramp inside the tower), `NW_XARDAS_STAIRS_01`..`_04`.

## Engine side

`OpenGothic/common/testbench.cpp`, inert unless `OPENGOTHIC_BENCH` is set. Hooks: the fixed time step and
the capture in `MainWindow` (in film mode the captured frame replaces the window's), the AI bridge's lines
and voices (`TestBench::aiLine`, `aiVoiceEnd`, `aiHeard`, `aiBusy`), an npc's lines of the game and SVMs
(`TestBench::npcOutput` in `Npc::nextAiAction`), the OpenAL loopback device in Tempest's
`SoundDevice`, `TEMPEST_WINDOW_SIZE` in Tempest's macOS backend, `FootIk::playerProbe` (legs before and after IK),
`Pose::playerProbe` (the animated pose, what plays, `AnimBlend`; `OPENGOTHIC_ANIBLEND_LOG=1` logs the hero's
switches bone by bone),
`LipSync::subjectProbe` (the mouth), `Flinch::subjectProbe` (blows), `TalkGestures::subjectProbe` (the hands), `EyeBlink::subjectProbe` (blinks), `Camera::setNearClip`
(`view clean`), `DrawCommands::hiddenTypes` (`view hide`), `Npc::transformInto` (`transform`), and
`TEMPEST_BACKGROUND` in Tempest's macOS backend (no focus, no Dock icon).
