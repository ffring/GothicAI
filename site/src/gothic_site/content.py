"""What the site is made of: the settings and the home page (content/home.toml), the devlog posts
(content/posts/<date>-<slug>/{en,ru}.md plus their pictures and clips).

A post is Markdown with TOML between `+++` lines on top:

    +++
    title = "Three days in"
    summary = "One sentence for the list and the feed."
    date = 2026-10-12T18:00:00+03:00
    cover = "cover.jpg"          # optional, a picture next to the post
    youtube = ""                 # optional, a video id
    +++
    The text...
"""

from __future__ import annotations

import datetime as dt
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

LANGS = ("en", "ru")
MEDIA = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".webm", ".svg"}


class ContentError(ValueError):
    pass


@dataclass(frozen=True)
class Version:
    """A post in one language."""

    title: str
    summary: str
    body: str  # Markdown
    cover: str = ""
    youtube: str = ""


@dataclass(frozen=True)
class Post:
    slug: str
    date: dt.datetime
    folder: Path
    versions: dict[str, Version]
    media: list[Path] = field(default_factory=list)

    def get(self, lang: str) -> Version | None:
        return self.versions.get(lang)


@dataclass(frozen=True)
class Site:
    root: Path
    settings: dict
    home: dict[str, dict]  # per language
    hero: dict
    posts: list[Post]  # newest first

    @property
    def base(self) -> str:
        return str(self.settings.get("base", "/"))


def split_front_matter(text: str, where: Path) -> tuple[dict, str]:
    if not text.startswith("+++"):
        raise ContentError(f"{where}: no +++ front matter")
    head, sep, body = text[3:].partition("\n+++")
    if not sep:
        raise ContentError(f"{where}: the front matter is not closed with +++")
    try:
        meta = tomllib.loads(head)
    except tomllib.TOMLDecodeError as err:
        raise ContentError(f"{where}: {err}") from err
    return meta, body.lstrip("\n")


def load_post(folder: Path) -> Post:
    versions: dict[str, Version] = {}
    dates: set[dt.datetime] = set()
    for lang in LANGS:
        path = folder / f"{lang}.md"
        if not path.is_file():
            continue
        meta, body = split_front_matter(path.read_text(), path)
        for key in ("title", "summary", "date"):
            if key not in meta:
                raise ContentError(f"{path}: no {key}")
        date = meta["date"]
        if isinstance(date, dt.date) and not isinstance(date, dt.datetime):
            date = dt.datetime.combine(date, dt.time(12), dt.UTC)
        if not isinstance(date, dt.datetime) or date.tzinfo is None:
            raise ContentError(f"{path}: date needs a time zone, like 2026-10-12T18:00:00+03:00")
        dates.add(date)
        versions[lang] = Version(
            title=str(meta["title"]),
            summary=str(meta["summary"]),
            body=body,
            cover=str(meta.get("cover", "")),
            youtube=str(meta.get("youtube", "")),
        )
    if "en" not in versions:
        raise ContentError(f"{folder}: a post needs en.md (ru.md is optional)")
    if len(dates) > 1:
        raise ContentError(f"{folder}: the languages have different dates")
    slug = folder.name[11:] if folder.name[:10].count("-") == 2 and folder.name[10:11] == "-" else folder.name
    media = sorted(p for p in folder.iterdir() if p.suffix.lower() in MEDIA)
    for v in versions.values():
        if v.cover and not (folder / v.cover).is_file():
            raise ContentError(f"{folder}: no cover {v.cover}")
    return Post(slug, dates.pop(), folder, versions, media)


def load(root: Path) -> Site:
    """`root` is site/: content/home.toml, content/posts/, static/."""
    path = root / "content" / "home.toml"
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as err:
        raise ContentError(f"{path}: {err}") from err
    missing = [lang for lang in LANGS if lang not in data]
    if missing:
        raise ContentError(f"{path}: no [{'], ['.join(missing)}]")
    posts_dir = root / "content" / "posts"
    posts = [load_post(p) for p in sorted(posts_dir.iterdir()) if p.is_dir()] if posts_dir.is_dir() else []
    slugs = [p.slug for p in posts]
    if len(set(slugs)) != len(slugs):
        raise ContentError(f"two posts have the same slug: {slugs}")
    posts.sort(key=lambda p: p.date, reverse=True)
    return Site(root, data.get("site", {}), {lang: data[lang] for lang in LANGS}, data.get("hero", {}), posts)
