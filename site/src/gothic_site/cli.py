"""`gothic-site`: build the site, preview it, start a post, take pictures and clips from a video."""

from __future__ import annotations

import argparse
import datetime as dt
import functools
import http.server
import subprocess
import sys
from pathlib import Path

from .build import Builder
from .content import ContentError, load

ROOT = Path(__file__).resolve().parents[2]  # site/


def cmd_build(args: argparse.Namespace) -> int:
    site = load(ROOT)
    written = Builder(site, Path(args.out), args.base).build()
    print(f"{len(written)} pages, {len(site.posts)} post(s) -> {args.out}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    out = Path(args.out)
    Builder(load(ROOT), out, "/").build()
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out))
    print(f"http://127.0.0.1:{args.port}/ (rebuild: gothic-site build --base /, or restart)")
    http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler).serve_forever()
    return 0


def cmd_new(args: argparse.Namespace) -> int:
    now = dt.datetime.now().astimezone().replace(microsecond=0)
    folder = ROOT / "content" / "posts" / f"{now.date().isoformat()}-{args.slug}"
    if folder.exists():
        print(f"error: {folder} exists", file=sys.stderr)
        return 1
    folder.mkdir(parents=True)
    for lang, title in (("en", args.title), ("ru", args.title_ru or args.title)):
        (folder / f"{lang}.md").write_text(
            f'+++\ntitle = "{title}"\nsummary = ""\ndate = {now.isoformat()}\n# cover = "cover.jpg"\n'
            f'# youtube = ""\n+++\n'
        )
    print(folder)
    return 0


def cmd_frame(args: argparse.Namespace) -> int:
    """A still from a video, scaled to `width`, as a JPEG (covers and pictures in posts)."""
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(args.at), "-i", args.video,
         "-frames:v", "1", "-vf", f"scale={args.width}:-2", "-q:v", "4", str(out)],
        check=True,
    )  # fmt: skip
    print(f"{out} ({out.stat().st_size // 1024} KB)")
    return 0


def cmd_clip(args: argparse.Namespace) -> int:
    """A short muted loop from a video (the home page's short until it is on YouTube): small, so it can live
    in the repository."""
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(args.start),
         "-t", str(args.length), "-i", args.video, "-an", "-vf", f"scale={args.width}:-2,fps=30",
         "-c:v", "libx264", "-preset", "slow", "-crf", str(args.crf), "-pix_fmt", "yuv420p",
         "-movflags", "+faststart", str(out)],
        check=True,
    )  # fmt: skip
    size = out.stat().st_size
    print(f"{out} ({size // 1024} KB)")
    if size > 3 * 1024 * 1024:
        print("warning: over 3 MB, use a shorter clip or a higher --crf", file=sys.stderr)
    return 0


def main() -> None:
    p = argparse.ArgumentParser(prog="gothic-site", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("build", help="build the site")
    s.add_argument("--out", default=str(ROOT / "_build"))
    s.add_argument("--base", help="path the site lives under (default: base from content/home.toml)")
    s.set_defaults(fn=cmd_build)

    s = sub.add_parser("serve", help="build for / and serve it locally")
    s.add_argument("--out", default=str(ROOT / "_build"))
    s.add_argument("--port", type=int, default=47860)
    s.set_defaults(fn=cmd_serve)

    s = sub.add_parser("new", help="start a post: content/posts/<today>-<slug>/{en,ru}.md")
    s.add_argument("slug")
    s.add_argument("--title", required=True)
    s.add_argument("--title-ru")
    s.set_defaults(fn=cmd_new)

    s = sub.add_parser("frame", help="a still from a video as a JPEG")
    s.add_argument("video")
    s.add_argument("out")
    s.add_argument("--at", type=float, default=0.0, help="seconds")
    s.add_argument("--width", type=int, default=1080)
    s.set_defaults(fn=cmd_frame)

    s = sub.add_parser("clip", help="a short muted loop from a video")
    s.add_argument("video")
    s.add_argument("out")
    s.add_argument("--start", type=float, default=0.0)
    s.add_argument("--length", type=float, default=8.0)
    s.add_argument("--width", type=int, default=540)
    s.add_argument("--crf", type=int, default=28)
    s.set_defaults(fn=cmd_clip)

    args = p.parse_args()
    try:
        sys.exit(args.fn(args))
    except ContentError as err:
        print(f"error: {err}", file=sys.stderr)
        sys.exit(1)
