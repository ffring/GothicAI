#!/usr/bin/env python3
"""Stage 9: film a policy's arena duels in the rendered game.

The headless runner has no picture. A lockstep episode recorded with the RL collector
(--keep-journals) is replayed in Gothic2Notr by the bench command `bodyreplay`
(OpenGothic/common/game/bodyreplay.h): the same arena reset at the same scenario line, the
primary's accepted commands issued after the same number of steps. The rendered run writes the
runner's journals, so scripts/body_schedule_equivalence.py checks the film against the
recording before anyone looks at it.

  prepare  NAME=EPISODE_DIR ... --output DIR   replay files and bench scenes
  render   DIR/NAME ... --game Gothic2Notr     macOS: Metal + game audio; Linux: Xvfb + lavapipe
  compose  LEFT.mp4 RIGHT.mp4 OUT.mp4          side by side, labels and outcomes

Replay file: {"arena": {scenario, seed, side, distance_cm, timeout_ms[, geometry, ...]}, "provider",
"commands": [{step, command_id, action_id, goal_version, target_entity_id, observation_seq,
ttl_ms[, actor: "opponent"]}]}, where step counts the native steps after the reset sample (arena_start
is step 0). A self-play duel (arena opponent_owned) also replays the opponent's commands.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
SCENARIO = ROOT/"scripts"/"bench"/"body-arena.bench"
ARENA_KEYS = ("scenario", "seed", "side", "distance_cm", "timeout_ms")
# A custom arena reset (near_wall, offsets), as the runner reports it under "setup".
SETUP_KEYS = ("geometry", "wall_clearance_cm", "wall_tangent_sign", "yaw_offset_deg", "opponent_yaw_offset_deg",
              "separation_yaw_deg", "center_offset_cm")
COMMAND_KEYS = ("command_id", "action_id", "goal_version", "target_entity_id", "observation_seq", "ttl_ms")
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def replay_file(episode: Path) -> dict:
    arena = rows(episode/"arena-events.jsonl")
    initial = next(row["arena"] for row in arena if row.get("kind") == "reset_ready")
    terminal = next((row["arena"] for row in arena if row.get("kind") == "terminal"), None)
    primary, opponent = initial["primary_entity_id"], initial.get("opponent_entity_id")
    owned = initial.get("opponent_owned") is True
    # The reset sample is the first observation row: its step is the arena's step zero.
    start = rows(episode/"observations.jsonl")[0]["sim_step"]
    # In a self-play duel the opponent's commands too, in the order the runner accepted them.
    actors = {primary: "primary", **({opponent: "opponent"} if owned else {})}
    accepted = [row for row in rows(episode/"body-control-events.jsonl")
                if row.get("entity_id") in actors and row.get("phase") == "accepted"]
    if initial["scenario"] != "native_duel" or not accepted:
        raise SystemExit(f"{episode}: not a policy-driven native duel")
    return {"schema_version": 1, "source": str(episode), "provider": accepted[0]["provider"],
            "arena": {**{key: initial[key] for key in ARENA_KEYS}, **{key: value for key, value in (initial.get("setup") or {}).items()
                                                                  if key in SETUP_KEYS}, **({"opponent_owned": True} if owned else {})},
            "outcome": {key: terminal.get(key) for key in ("outcome", "reason", "elapsed_game_ms")} if terminal else None,
            "commands": [{"step": row["sim_step"]-start, **{key: row[key] for key in COMMAND_KEYS},
                          **({"actor": "opponent"} if actors[row["entity_id"]] == "opponent" else {})} for row in accepted]}


def scene(scenario: Path, replay: Path, *, camera: str, tail_ms: int) -> str:
    """The arena scenario up to its `bodyarena` line, which the replay takes over."""
    lines = scenario.read_text().splitlines()
    cut = next(index for index, line in enumerate(lines) if line.split()[:1] == ["bodyarena"])
    return "\n".join(lines[:cut]+[
        "overlay off",
        f"bodyreplay {replay} {tail_ms}",
        f"cam {camera}",
        "rec duel",
        "wait 600100",
        "stop",
    ])+"\n"


def prepare(args) -> int:
    for item in args.episodes:
        name, _, folder = item.partition("=")
        out = (args.output/name).resolve()
        out.mkdir(parents=True, exist_ok=True)
        replay = replay_file(Path(folder))
        (out/"replay.json").write_text(json.dumps(replay, indent=1)+"\n")
        (out/"scene.bench").write_text(scene(args.scenario, out/"replay.json", camera=args.camera, tail_ms=args.tail_ms))
        print(f"{name}: seed {replay['arena']['seed']} side {replay['arena']['side']} "
              f"distance {replay['arena']['distance_cm']}: {len(replay['commands'])} commands, {replay['outcome']}")
    return 0


def macos_window_size(size: str) -> str:
    """Tempest's macOS window uses points; --size specifies the captured pixels."""
    scale = float(subprocess.check_output([
        "osascript", "-l", "JavaScript", "-e",
        'ObjC.import("AppKit"); $.NSScreen.mainScreen.backingScaleFactor',
    ], text=True).strip())
    width, height = (int(value) for value in size.split("x"))
    if scale <= 0 or any(value <= 0 or not (value/scale).is_integer() for value in (width, height)):
        raise SystemExit(f"--size {size} cannot use whole window points at display scale {scale:g}")
    return f"{int(width/scale)}x{int(height/scale)}"


def render(args) -> int:
    failures = 0
    macos = sys.platform == "darwin"
    window_size = macos_window_size(args.size) if macos else args.size
    for folder in args.scenes:
        out = folder.resolve()
        run = out/"run"
        shutil.rmtree(run, ignore_errors=True)
        run.mkdir()
        shutil.copy(args.save, run/"save_slot_1.sav")
        width, height = args.size.split("x")
        env = {**os.environ, "GOTHIC_AI": "0", "TEMPEST_BACKGROUND": "1",
               "TEMPEST_WINDOW_SIZE": window_size, "OPENGOTHIC_TEXTURES": args.textures,
               "OPENGOTHIC_BODY_TRACE": "1", "OPENGOTHIC_BODY_ANIMATION_RNG": "isolated",
               "OPENGOTHIC_BODY_SCRIPT_RNG": "isolated", "OPENGOTHIC_BENCH_REALTIME": "1",
               "OPENGOTHIC_BENCH": str(out/"scene.bench"), "OPENGOTHIC_BENCH_OUT": str(out)}
        for key in ("DISPLAY", "WAYLAND_DISPLAY"):
            env.pop(key, None)
        argv = [str(args.game.resolve()), "-g", str(args.assets.resolve()), "-g2", "-window", "-devmode", "-save", "1"]
        if macos:
            # The macOS engine selects Metal itself. Do not inherit Linux's software renderer
            # or null audio device. Full resolution prevents each realtime screenshot from
            # scaling the previous screenshot's already reduced viewport a second time.
            for key in ("VK_DRIVER_FILES", "VK_ICD_FILENAMES", "LP_NUM_THREADS", "ALSOFT_DRIVERS"):
                env.pop(key, None)
            env["TEMPEST_AUDIO_LOOPBACK"] = "48000"
            # Match the headless runner's Jolt schedule when replaying its recorded commands.
            env["OPENGOTHIC_PHYSICS_THREADS"] = "0"
            env.setdefault("OPENGOTHIC_BENCH_ENCODER", "-c:v h264_videotoolbox -b:v 24M")
            (run/"Gothic.ini").write_text("[INTERNAL]\nvidResIndex=0\n[SOUND]\n"
                                         "soundVolume=1\nmusicVolume=0\nmusicEnabled=0\nworldSounds=0\n")
        else:
            env.update({"VK_DRIVER_FILES": "/usr/share/vulkan/icd.d/lvp_icd.json",
                        "VK_ICD_FILENAMES": "/usr/share/vulkan/icd.d/lvp_icd.json", "LP_NUM_THREADS": str(args.threads),
                        "ALSOFT_DRIVERS": "null"})
            argv = ["nice", "-n", "10", "xvfb-run", "-a", "-s", f"-screen 0 {width}x{height}x24", *argv]
        # A failed rerun must not pass using a previous take's video, audio or replay result.
        for name in ("duel.mp4", "duel.wav", "replay.jsonl"):
            (out/name).unlink(missing_ok=True)
        with open(out/"launcher.log", "w") as log:
            code = subprocess.run(argv, cwd=run, env=env, stdout=log, stderr=subprocess.STDOUT,
                                  timeout=args.timeout_s).returncode
        log_rows = rows(out/"replay.jsonl") if (out/"replay.jsonl").exists() else []
        end = next((row for row in log_rows if row["kind"] in ("replay_end", "replay_error")), None)
        video, audio = (out/"duel.mp4").exists(), (out/"duel.wav").exists()
        ok = code == 0 and end is not None and end["kind"] == "replay_end" and end["rejected"] == 0 and video and (audio or not macos)
        failures += not ok
        summary = {key: end.get(key) for key in ("kind", "accepted", "rejected", "observation_mismatches", "reason")} if end else None
        print(f"{out.name}: exit {code}, {summary}, video {video}, audio {audio}", flush=True)
    return 1 if failures else 0


def compose(args) -> int:
    """Two films side by side; each gets its label and, below, its replay's outcome."""
    texts = args.output.parent/(args.output.stem+"-text")
    texts.mkdir(parents=True, exist_ok=True)

    def caption(index, film):
        replay = json.loads((film.parent/"replay.json").read_text()) if (film.parent/"replay.json").exists() else {}
        outcome = replay.get("outcome") or {}
        result = {"opponent_down": "win", "primary_down": "loss"}.get(outcome.get("reason"), outcome.get("reason") or "")
        hp = next((row["arena"]["actors"][0]["hp"] for row in rows(film.parent/"arena-events.jsonl")
                   if row.get("kind") == "terminal"), None) if (film.parent/"arena-events.jsonl").exists() else None
        seconds = (outcome.get("elapsed_game_ms") or 0)/1000
        (texts/f"label{index}.txt").write_text(args.labels[index])
        (texts/f"caption{index}.txt").write_text(f"{result}  {hp} HP  {seconds:.1f} s" if result else "")
        box = "fontcolor=white:box=1:boxcolor=black@0.45:boxborderw=10"
        return (f"drawtext=fontfile={FONT}:textfile={texts}/label{index}.txt:x=24:y=20:fontsize=36:{box},"
                f"drawtext=fontfile={FONT}:textfile={texts}/caption{index}.txt:x=24:y=h-th-24:fontsize=28:{box}")

    graph = (f"[0:v]{caption(0, args.left)}[a];[1:v]{caption(1, args.right)}[b];"
             "[a][b]hstack=inputs=2:shortest=0[v]")
    argv = ["ffmpeg", "-loglevel", "error", "-y", "-i", str(args.left), "-i", str(args.right),
            "-filter_complex", graph, "-map", "[v]", "-r", "30", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(args.output)]
    return subprocess.run(argv).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("episodes", nargs="+", help="NAME=EPISODE_DIR of a collector run with --keep-journals")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--scenario", type=Path, default=SCENARIO)
    p.add_argument("--camera", default="two arena 320 110 1")
    p.add_argument("--tail-ms", type=int, default=3000)
    p.set_defaults(run=prepare)
    r = sub.add_parser("render")
    r.add_argument("scenes", nargs="+", type=Path)
    r.add_argument("--game", type=Path, required=True)
    r.add_argument("--assets", type=Path, required=True)
    r.add_argument("--save", type=Path, required=True)
    r.add_argument("--size", default="960x540", help="output video size in pixels, including on Retina displays")
    r.add_argument("--threads", type=int, default=4)
    r.add_argument("--textures", default="")
    r.add_argument("--timeout-s", type=int, default=1800)
    r.set_defaults(run=render)
    c = sub.add_parser("compose")
    c.add_argument("left", type=Path)
    c.add_argument("right", type=Path)
    c.add_argument("output", type=Path)
    c.add_argument("--labels", nargs=2, default=("BC", "R2u"))
    c.set_defaults(run=compose)
    args = parser.parse_args()
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
