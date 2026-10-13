"""`gothic-shorts`: shorts from the game, see shorts/README.md."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from gothic_launcher.commands import LaunchError, find_layout, load_config, tools
from gothic_launcher.settings import Config, SettingsError

from . import cut as cutting
from . import youtube
from .film import FilmError, film, shorts_dir
from .shot import Shot, ShotError, load
from .timeline import build, read_events
from .voice import VoiceError, voice_lines
from .youtube import YouTubeError

SHOTS = Path(__file__).resolve().parents[2] / "shots"


def _shot(arg: str) -> Shot:
    path = Path(arg)
    if not path.is_file():
        path = SHOTS / f"{arg}.toml"
    if not path.is_file():
        raise ShotError(f"no shot {arg} (a .toml file, or a name from {SHOTS})")
    return load(path)


def _config() -> Config:
    config, _ = load_config()
    return config


def _take(config: Config, shot: Shot, arg: str | None) -> Path:
    if arg and Path(arg).is_dir():
        return Path(arg)
    repo, _ = tools(config)
    takes = sorted((shorts_dir(repo) / shot.name).glob("take-*"))
    if arg:
        want = f"take-{int(arg):02d}" if arg.isdigit() else arg
        for t in takes:
            if t.name == want:
                return t
        raise ShotError(f"no take {arg} of {shot.name}")
    if not takes:
        raise ShotError(f"{shot.name} has no takes yet: gothic-shorts film {shot.name}")
    return takes[-1]


def cmd_voice(args: argparse.Namespace) -> int:
    shot, config = _shot(args.shot), _config()
    repo, _ = tools(config)
    voiced = voice_lines(config, shorts_dir(repo) / "voice", shot.hero_voice, shot.hero_lines)
    for text, path in voiced.items():
        print(f"{path}  {text}")
    return 0


def cmd_film(args: argparse.Namespace) -> int:
    shot, config = _shot(args.shot), _config()
    layout = find_layout(config)
    decider = args.decider_url or os.environ.get("GOTHIC_DECIDER_URL")
    for _ in range(args.takes):
        take = film(shot, config, layout, decider_url=decider, verbose=args.verbose)
        _summary(take)
        if args.cut:
            print(cutting.cut(shot, take, args.lang))
    return 0


def cmd_cut(args: argparse.Namespace) -> int:
    shot, config = _shot(args.shot), _config()
    take = _take(config, shot, args.take)
    out = cutting.cut(shot, take, args.lang, Path(args.out) if args.out else None)
    print(out)
    print(out.with_suffix(".md").read_text())
    return 0


def cmd_subs(args: argparse.Namespace) -> int:
    shot, config = _shot(args.shot), _config()
    take = _take(config, shot, args.take)
    print(cutting.write_subs_template(shot, take, args.lang))
    return 0


def cmd_youtube_login(args: argparse.Namespace) -> int:
    token = youtube.access_token()
    cid, title = youtube.channel(token)
    print(f"signed in: channel {title} ({cid}); token in {youtube.TOKEN}")
    return 0


def cmd_upload(args: argparse.Namespace) -> int:
    video = Path(args.video)
    if not video.is_file():
        raise YouTubeError(f"no video {video}")
    meta = youtube.load_meta(Path(args.meta), args.lang)
    meta.privacy = args.privacy
    token = youtube.access_token()
    cid, title = youtube.channel(token)
    youtube.check_channel(cid, title, args.channel)
    print(f"channel: {title} ({cid})")
    print(f"video:   {video} ({video.stat().st_size / 1e6:.1f} MB)")
    print(f"title:   {meta.title}")
    print(f"privacy: {meta.privacy}, synthetic media: {meta.synthetic}")
    if not args.yes and input("upload? [y/N] ").strip().lower() != "y":
        print("not uploaded")
        return 1
    vid = youtube.upload(video, meta, token)
    print(f"uploaded: https://youtu.be/{vid}  (id {vid})")
    return 0


def _summary(take: Path) -> None:
    events = take / "events.jsonl"
    if not events.is_file():
        print(f"{take}: no events (did the scenario start? see {take / 'logs'})")
        return
    tl = build(read_events(events))
    print(f"{take}: {tl.duration:.1f} s recorded, {tl.cut_duration:.1f} s after the cut")
    heard = iter(tl.heard)
    for line in tl.lines:
        if line.hero:
            print(f"  {line.start:5.1f} hero: {line.text}  [heard: {next(heard, '?')}]")
        else:
            print(f"  {line.start:5.1f} {line.name}: {line.text}")
    if tl.timeouts:
        print(f"  waiting for an answer timed out {tl.timeouts} time(s)")
    log = take / "bench.log"
    if log.is_file():
        for row in log.read_text().splitlines():
            if row.startswith(("realtime:", "ptt: not", "can not", "unknown", "near: no", "cam ")):
                print(f"  bench: {row}")


def main() -> None:
    p = argparse.ArgumentParser(prog="gothic-shorts", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("voice", help="voice the hero's lines of a shot (done by film too)")
    s.add_argument("shot", help="a shot file, or a name from shorts/shots/")
    s.set_defaults(fn=cmd_voice)

    s = sub.add_parser("film", help="film takes of a shot with the AI services")
    s.add_argument("shot")
    s.add_argument("--takes", type=int, default=1, help="how many takes, one after another")
    s.add_argument("--cut", action="store_true", help="cut each take right away")
    s.add_argument("--lang", default="ru", help="language of the cut (with --cut)")
    s.add_argument("--decider-url", help="npc decision service (default: GOTHIC_DECIDER_URL); off without it")
    s.add_argument("-v", "--verbose", action="store_true", help="print the services' and the game's output")
    s.set_defaults(fn=cmd_film)

    s = sub.add_parser("cut", help="cut a take into a short")
    s.add_argument("shot")
    s.add_argument("take", nargs="?", help="take folder or number (default: the newest)")
    s.add_argument("--lang", default="ru", help="ru, or a language with subs.<lang>.json in the take")
    s.add_argument("--out", help="the output file (default: <take>/short-<lang>.mp4)")
    s.set_defaults(fn=cmd_cut)

    s = sub.add_parser("subs", help="write subs.<lang>.json of a take: the texts to translate")
    s.add_argument("shot")
    s.add_argument("take", nargs="?")
    s.add_argument("--lang", default="en")
    s.set_defaults(fn=cmd_subs)

    s = sub.add_parser("youtube", help="YouTube: sign in once (the browser opens), show the channel")
    s.add_argument("action", choices=["login"])
    s.set_defaults(fn=cmd_youtube_login)

    s = sub.add_parser("upload", help="upload a cut short to YouTube (private unless --privacy)")
    s.add_argument("video", help="the cut short, e.g. .shorts/compare/bodies/short-ru.mp4")
    s.add_argument("--meta", required=True,
                   help="the short's .shorts/<name>/youtube.json: <lang>: title, description, tags, "
                        "synthetic")
    s.add_argument("--lang", default="ru")
    s.add_argument("--privacy", default="private", choices=["private", "unlisted", "public"],
                   help="an API project without YouTube's audit can upload private only")
    s.add_argument("--channel", default=youtube.CHANNEL,
                   help="upload only to this channel id (GOTHIC_YOUTUBE_CHANNEL, the project's GothicAI)")
    s.add_argument("--yes", action="store_true", help="do not ask before uploading")
    s.set_defaults(fn=cmd_upload)

    args = p.parse_args()
    try:
        sys.exit(args.fn(args))
    except (ShotError, FilmError, VoiceError, cutting.CutError, LaunchError, SettingsError,
            YouTubeError) as err:
        print(f"error: {err}", file=sys.stderr)
        sys.exit(1)
