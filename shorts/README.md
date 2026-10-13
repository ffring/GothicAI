# Shorts

Vertical videos of the game with the real AI npc's, made without a person at the keyboard: a shot file is a
scene with the hero's lines, the hero says them with a TTS voice through the talk key, the npc's hear them
through speech recognition and answer with the language model, the test bench films it with the game's
sound, and the cut makes a 1080x1920 short with subtitles.

```bash
cd shorts
uv run gothic-shorts film xardas-lie            # a take: .shorts/xardas-lie/take-NN/
uv run gothic-shorts film xardas-lie --takes 3  # three takes in a row (the answers differ every time)
uv run gothic-shorts cut xardas-lie             # the newest take -> short-ru.mp4 (+ .png sheet, .md transcript)
uv run gothic-shorts cut xardas-lie 2           # take-02
uv run gothic-shorts subs xardas-lie --lang en  # subs.en.json of the take: fill in the translations, then
uv run gothic-shorts cut xardas-lie --lang en   # -> short-en.mp4
```

What it uses: the launcher's settings and keys (`gothic-launcher config`: ChatGPT sign-in, the ASR and TTS
providers), the engine build of this checkout (`scripts/build-engine.sh`), the game folder, and the bench
save (`save_slot_1.sav` of the main checkout, Xardas' tower) unless the shot names another. The services run
on free ports, so a play session can run at the same time; the game window is minimized and silent.

## A shot

`shots/<name>.toml`:

```toml
title = "Соврал Ксардасу"
hook = "Я попробовал обмануть Ксардаса"   # big text over the first seconds
npc = "NONE_100_XARDAS"                   # {npc} in the script
music = 0.35                              # the game's music and sound volume, 0..1
sound = 1.0
# save = "my.sav"                         # relative to the shot file
# hero_voice = "A young man ..."          # a description: the voice is designed, not cloned
script = """
overlay off
ai attach {npc}          # the npc stops and stands: it answers through the game's own queue
wait 500
near {npc} 170           # the hero 1.7 m in front of it, facing it
cam shoulder {npc} 130 15 1
rec main                 # the recorded segment must be called main
hero Ксардас, меня прислал Ли.   # the hero says it
waitreply 40000          # until the npc has answered and is quiet
cam face {npc}
hero Ну, хоть пару зелий.
waitreply 40000
stop
"""
```

The script is the bench's language (`scripts/bench/README.md`) plus `hero <text>`. The commands made for
shots:

| Command | |
|---|---|
| `hero <text>` | the hero says it: a TTS recording held on the talk key (the bench's `ptt <wav> <text>`) |
| `waitreply [timeout ms] [quiet ms]` | until an npc of the AI has answered the hero's last phrase and has been quiet for `quiet` (1200) |
| `waitquiet [quiet ms] [timeout ms]` | until no npc of the AI speaks |
| `near <npc> [dist cm] [deg]` | the hero stands in front of the npc (off its facing by `deg`), facing it |
| `approach <npc>` | the hero walks up to the npc; then `waitarrive` |
| `face npc <npc>` | the hero turns to the npc |
| `cam shoulder <npc> [dist] [height] [1\|-1]` | over the hero's shoulder at the npc's face |
| `cam face <npc> [dist] [height] [deg]` | the npc's face from the front, turned towards the hero |
| `cam two <npc> [dist] [height] [1\|-1]` | both from the side |
| `cam cut`, `cam smooth <0..1>` | the next shot jumps instead of gliding; how fast the camera glides |
| `caption <text>`, `caption off` | text at the top of the short from here |
| `ai attach <npc>`, `ai detach <npc>` | the bridge's console: the npc is held (stands, answers in the game's queue) |

## Making a short (the agent's loop)

1. Write the shot: what the short shows in one sentence (the hook), the hero's lines, the camera. Keep it
   under 60 s; one idea per short.
2. `film --takes 2..3`. The summary of each take lists what the hero said, what speech recognition heard,
   and every npc line; a take with `timed out` lost an answer (the model was overloaded, nobody heard).
3. Pick the take whose answers carry the idea, `cut` it, read the transcript (`short-ru.md`) and look at
   the sheet (`short-ru.png`); look at single frames when something is off
   (`ffmpeg -ss 12 -i short-ru.mp4 -frames:v 1 f.png`).
4. If the answers miss: change the hero's lines (the npc answers what it heard), not the cut.
5. For an English short: `subs`, translate every text in `subs.en.json`, `cut --lang en`.

Honesty: the npc's answers are not edited, only the pauses while the model thinks are cut down (to ~0.5 s).
Say so in the description.

## How it works

- `film` voices the hero's lines once (`tts-service`, `gothic-tts say`, the voice `SHORTS_HERO` designed from
  `hero_voice`; kept in `.shorts/voice/` by text), writes the bench scenario, starts ASR, TTS and the AI
  server like the launcher (with `--voice-files`: a `ptt_start` may name a wav instead of the microphone),
  and the game with the bench in film mode:
  - `TEMPEST_WINDOW_SIZE`: the frame (486x864 points, 972x1728 pixels);
  - `TEMPEST_AUDIO_LOOPBACK=48000`: nothing plays; OpenAL mixes the game's sound when the bench asks, a
    frame's worth per frame, into `main.wav` next to `main.mp4`;
  - `OPENGOTHIC_BENCH_REALTIME`: the fixed steps keep to the wall clock (the AI server lives in real time);
    the window is not drawn, the captured frame is the frame; `rec stop ... speed=99%` in `bench.log` says
    how well the game kept up (below ~95% lines said on the move start too early);
  - `OPENGOTHIC_BENCH_ENCODER`: the hardware encoder (x264 slows the game down).
- The bench writes `events.jsonl`: the hero's phrases, what the server heard, every npc line as it starts
  and its voice's end, captions, in frames of the recording.
- `cut` builds the timeline (`timeline.py`), cuts the wait between the hero's phrase and the first answer,
  mixes the hero's recordings into the game's sound, burns in the hook, captions and subtitles as images
  (Pillow; Homebrew's ffmpeg has no libass), normalizes to -14 LUFS.

## Before and after (no AI)

For what you see rather than hear (foot IK, the gaze, the lean): the same bench scenario filmed twice,
the animation work off (OpenGothic as it is) and on, stacked top and bottom.

```bash
shorts/compare/film.sh bodies                                   # .shorts/compare/bodies/{off,on}/<segment>.mp4|.wav
uv run --project shorts python shorts/compare/cut.py bodies ru  # -> .shorts/compare/bodies/short-ru.mp4 (+ .png)
```

`bodies`: the animation work off and on. `archery`: the bow as Gothic II has it (the auto aim and the dice,
`OPENGOTHIC_MANUALAIM=0 OPENGOTHIC_NPCAIM=0`) and aimed by hand: the draw, a head shot from the game's
camera (`ui on`: the crosshair), the cant, steps with the bow drawn, leaning out round a corner, paladins
at a runner. A line break in the hook makes it two lines.

- `compare/<name>.bench`: the scenario; every `rec` is a beat. Both takes run the same steps on the fixed
  time step, so the halves stay in sync; the "off" take sets `OPENGOTHIC_FOOTIK=0`, `_GAZE`, `_LEAN`, `_AIM`,
  `_FLINCH`, `_ANIBLEND`, `_LIPSYNC` to 0, or what `"off_env"` of `compare/<name>.json` names. A line
  `off: <command>` / `on: <command>` runs in that take only (the two ways of doing the same thing, e.g. the
  auto aim shoots on action + forward, the hand lets go of the action key). Frames 1080x960 (`TEMPEST_WINDOW_SIZE`), the game's sound into
  `<segment>.wav` (`TEMPEST_AUDIO_LOOPBACK`, `SOUND`/`MUSIC` of `scripts/bench.sh`).
- `compare/<name>.json`: the hook, the two labels, the beats in order with their trims and titles, per language.
- `cut.py` stacks each beat, grades both halves the same (a little brighter), takes the sound of the "on"
  take, normalizes to -14 LUFS.

Pick spots in daylight and open ground (`clear` sends rats and wolves away), and look at both takes before
cutting: a difference that does not read at phone size (a flinch on top of the game's stumble) stays out.

## Uploading to YouTube

```bash
uv run gothic-shorts youtube login                     # once: the browser opens Google's consent page
uv run gothic-shorts upload .shorts/compare/bodies/short-ru.mp4 --meta .shorts/compare/bodies/youtube.json --lang ru --privacy public
```

The OAuth client is a Desktop app client of a Google Cloud project (YouTube Data API v3); its JSON and the
token live in `~/.local/share/gothic-ai/` (`youtube-client.json`, `youtube-token.json`), never in the
repository. Publish the app (Google Auth Platform, Audience, Publish app): a testing app's token lasts 7 days,
a published one's does not, and Google's verification is not needed for the channel's own account (the
consent page warns that the app is not verified: Advanced, go on). `login` signs in again and says how long
the token lasts. The metadata live next to the short, outside the
repository (`.shorts/<name>/youtube.json`), one section per language: title (up to 100 characters),
description, tags, `synthetic` (altered or synthetic content: set it for shorts with AI voices). Uploads are
private unless `--privacy public` (or `unlisted`). YouTube no longer locks the uploads of a project without
its audit to private, and `upload` reads the status back: if YouTube kept the video private, it says so (then
the project needs YouTube's API audit, and Studio can not change it). `upload` shows the channel, the file and the title and asks before it sends anything; it refuses any
channel but the project's (GothicAI, `UCbeAoPh3V7yFVpiuWNI-wkw`; `--channel` or `GOTHIC_YOUTUBE_CHANNEL`).

