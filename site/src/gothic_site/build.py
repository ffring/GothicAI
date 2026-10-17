"""The site as files: the home page and the devlog in English (/) and Russian (/ru/), a page per post, the
standing pages (the privacy policy), an RSS feed per language, the static files and the posts' pictures.
Every link starts with the base path (/GothicAI/ on GitHub Pages, / for a local preview)."""

from __future__ import annotations

import datetime as dt
import email.utils
import re
import shutil
from html import escape
from pathlib import Path

import markdown

from .content import LANGS, Page, Post, Site

FONTS = (
    "https://fonts.googleapis.com/css2?family=Alegreya:ital,wght@0,400;0,500;0,700;1,400"
    "&family=Alegreya+Sans:wght@400;500;700&display=swap"
)
MONTHS = {
    "en": "January February March April May June July August September October November December".split(),
    "ru": "января февраля марта апреля мая июня июля августа сентября октября ноября декабря".split(),
}
UI = {
    "en": {
        "devlog": "Devlog", "source": "Source", "other": "Русский", "other_lang": "ru", "home": "Gothic AI",
        "all_posts": "All posts", "feed": "RSS feed", "play": "Play the short", "back": "Back to the devlog",
        "telegram": "Telegram", "not_found": "There is no such page.", "to_home": "Go to the home page",
    },
    "ru": {
        "devlog": "Дневник", "source": "Код", "other": "English", "other_lang": "en", "home": "Gothic AI",
        "all_posts": "Все записи", "feed": "RSS", "play": "Смотреть шортс", "back": "Ко всем записям",
        "telegram": "Telegram", "not_found": "Такой страницы нет.", "to_home": "На главную",
    },
}  # fmt: skip


def prefix(lang: str) -> str:
    return "" if lang == "en" else f"{lang}/"


def date_text(d: dt.datetime, lang: str) -> str:
    return (
        f"{MONTHS[lang][d.month - 1]} {d.day}, {d.year}"
        if lang == "en"
        else f"{d.day} {MONTHS[lang][d.month - 1]} {d.year}"
    )


def md(text: str) -> str:
    return markdown.markdown(text, extensions=["extra", "sane_lists", "smarty"], output_format="html")


def media_links(html: str, base: str, post: Post) -> str:
    """Relative pictures and links of a post point at its media folder."""

    def fix(m: re.Match[str]) -> str:
        attr, url = m.group(1), m.group(2)
        if re.match(r"^([a-z]+:|/|#)", url):
            return m.group(0)
        return f'{attr}="{base}media/{post.slug}/{url}"'

    return re.sub(r'\b(src|href|poster)="([^"]+)"', fix, html)


class Builder:
    def __init__(self, site: Site, out: Path, base: str | None = None) -> None:
        self.site = site
        self.out = out
        self.base = base if base is not None else site.base
        if not self.base.endswith("/"):
            self.base += "/"

    def build(self) -> list[Path]:
        if self.out.exists():
            shutil.rmtree(self.out)
        self.out.mkdir(parents=True)
        written: list[Path] = []
        shutil.copytree(self.site.root / "static", self.out / "static")
        for post in self.site.posts:
            folder = self.out / "media" / post.slug
            folder.mkdir(parents=True)
            for f in post.media:
                shutil.copy(f, folder / f.name)
        for lang in LANGS:
            written.append(self._write(f"{prefix(lang)}index.html", self.home(lang)))
            written.append(self._write(f"{prefix(lang)}posts/index.html", self.devlog(lang)))
            written.append(self._write(f"{prefix(lang)}feed.xml", self.feed(lang)))
            for post in self.site.posts:
                if post.get(lang) is not None:
                    written.append(
                        self._write(f"{prefix(lang)}posts/{post.slug}/index.html", self.post(post, lang))
                    )
            for page in self.site.pages:
                if page.get(lang) is not None:
                    rel = f"{prefix(lang)}{page.slug}/index.html"
                    written.append(self._write(rel, self.standing(page, lang)))
        written.append(self._write("404.html", self.not_found()))
        (self.out / ".nojekyll").write_text("")
        return written

    def _write(self, rel: str, text: str) -> Path:
        path = self.out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    # --- pieces ---------------------------------------------------------------------------------------

    def url(self, lang: str, rel: str = "") -> str:
        return f"{self.base}{prefix(lang)}{rel}"

    def page(
        self, lang: str, title: str, body: str, *, other: str, description: str = "", cls: str = ""
    ) -> str:
        ui, s = UI[lang], self.site.settings
        home = self.site.home[lang]
        desc = escape(description or home.get("description", ""))
        full_title = escape(title if title == s.get("title") else f"{title} | {s.get('title', 'Gothic AI')}")
        links = [f'<a href="{self.url(lang, "posts/")}">{ui["devlog"]}</a>']
        if s.get("telegram"):
            links.append(f'<a href="{escape(s["telegram"])}">{ui["telegram"]}</a>')
        if s.get("repo"):
            links.append(f'<a href="{escape(s["repo"])}">{ui["source"]}</a>')
        links.append(
            f'<a href="{other}" hreflang="{ui["other_lang"]}" lang="{ui["other_lang"]}">{ui["other"]}</a>'
        )
        poster = self.site.hero.get("poster")
        site_url = escape(str(s.get("url", "")).rstrip("/"))
        og = f'<meta property="og:image" content="{site_url}/static/{escape(poster)}">' if poster else ""
        feed = (
            f'<link rel="alternate" type="application/rss+xml" title="{escape(home.get("feed_title", ""))}"'
        )
        github = f' <a href="{escape(s["repo"])}">GitHub</a>' if s.get("repo") else ""
        for p in self.site.pages:
            at = lang if p.get(lang) else "en"
            github += f' <a href="{self.url(at, f"{p.slug}/")}">{escape(p.versions[at].title)}</a>'
        return f"""<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{full_title}</title>
<meta name="description" content="{desc}">
<meta property="og:title" content="{escape(title)}">
<meta property="og:description" content="{desc}">
{og}
{feed} href="{self.url(lang, "feed.xml")}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="{FONTS}">
<link rel="stylesheet" href="{self.base}static/style.css">
<link rel="icon" href="{self.base}static/brand/favicon.ico" sizes="48x48">
<link rel="icon" href="{self.base}static/brand/favicon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="{self.base}static/brand/apple-touch-icon.png">
<link rel="manifest" href="{self.base}static/brand/site.webmanifest">
<meta name="theme-color" content="#17110D">
</head>
<body class="{cls}">
<a class="skip" href="#main">{"Skip to content" if lang == "en" else "К содержанию"}</a>
<header class="bar">
  <a class="mark" href="{self.url(lang)}"><img src="{self.base}static/brand/lockup.svg" width="174" height="44" alt="{escape(s.get("title", "Gothic AI"))}"></a>
  <nav>{"".join(links)}</nav>
</header>
<main id="main">
{body}
</main>
<footer class="foot">
  <p>{escape(home.get("status", ""))}</p>
  <p><a href="{self.url(lang, "feed.xml")}">{ui["feed"]}</a>{github}</p>
</footer>
<script src="{self.base}static/site.js" defer></script>
</body>
</html>
"""

    def player(self, youtube: str, poster: str, loop: str, label: str, alt: str) -> str:
        """The short: a YouTube video behind a click (nothing loads from YouTube before it), else a muted loop
        that plays unless the reader asked for less motion, else the still."""
        img = f'<img src="{poster}" alt="{escape(alt)}" width="540" height="960">' if poster else ""
        if youtube:
            attrs = f'class="yt" data-id="{escape(youtube)}" aria-label="{escape(label)}"'
            play = '<span class="play" aria-hidden="true"></span>'
            return f'<div class="short"><button {attrs}>{img}{play}</button></div>'
        if loop:
            return (
                f'<div class="short"><video class="loop" muted loop playsinline preload="metadata" '
                f'poster="{poster}" aria-label="{escape(alt)}" width="540" height="960">'
                f'<source src="{loop}" type="video/mp4"></video></div>'
            )
        return f'<div class="short">{img}</div>' if img else ""

    # --- pages ----------------------------------------------------------------------------------------

    def home(self, lang: str) -> str:
        h, hero, ui = self.site.home[lang], self.site.hero, UI[lang]
        lines = []
        for i, line in enumerate(h.get("dialogue", [])):
            who = "by-hero" if line.get("hero") else "by-npc"
            last = " last" if i == len(h["dialogue"]) - 1 else ""
            lines.append(
                f'<p class="line {who}{last}"><span class="who">{escape(line["name"])}</span>'
                f'<span class="said">{escape(line["text"])}</span></p>'
            )
        poster = f"{self.base}static/{hero['poster']}" if hero.get("poster") else ""
        loop = f"{self.base}static/{hero[f'loop_{lang}']}" if hero.get(f"loop_{lang}") else ""
        player = self.player(hero.get("youtube", ""), poster, loop, ui["play"], h.get("video_alt", ""))
        features = "".join(
            f"<div><dt>{escape(f['title'])}</dt><dd>{escape(f['text'])}</dd></div>"
            for f in h.get("features", [])
        )
        steps = "".join(f"<li>{escape(step)}</li>" for step in h.get("how_steps", []))
        posts = self.post_list(lang, limit=4)
        actions = [f'<a class="button" href="{self.url(lang, "posts/")}">{escape(h["read_devlog"])}</a>']
        if self.site.settings.get("repo"):
            repo = escape(self.site.settings["repo"])
            actions.append(f'<a class="button quiet" href="{repo}">{escape(h["see_code"])}</a>')
        body = f"""
<section class="hero">
  <div class="scene">
    <div class="dialogue" aria-label="{escape(h.get("dialogue_label", ""))}">
      {"".join(lines)}
    </div>
    <p class="note">{escape(h.get("dialogue_note", ""))}</p>
  </div>
  {player}
  <div class="pitch">
    <h1>{escape(h["headline"])}</h1>
    <p class="lede">{escape(h["lede"])}</p>
    <p class="actions">{"".join(actions)}</p>
  </div>
</section>
<section class="works">
  <h2>{escape(h["features_title"])}</h2>
  <dl class="features">{features}</dl>
</section>
<section class="how">
  <h2>{escape(h["how_title"])}</h2>
  <p>{escape(h["how_text"])}</p>
  <ol class="flow">{steps}</ol>
</section>
<section class="log">
  <h2>{escape(h["devlog_title"])}</h2>
  {posts}
  <p><a href="{self.url(lang, "posts/")}">{ui["all_posts"]}</a></p>
</section>
"""
        other = self.url(UI[lang]["other_lang"])
        return self.page(lang, self.site.settings.get("title", "Gothic AI"), body, other=other, cls="home")

    def post_list(self, lang: str, limit: int | None = None) -> str:
        items = []
        for post in [p for p in self.site.posts if p.get(lang)][:limit]:
            v = post.get(lang)
            assert v is not None
            items.append(
                f'<li><time datetime="{post.date.date().isoformat()}">{date_text(post.date, lang)}</time>'
                f'<a href="{self.url(lang, f"posts/{post.slug}/")}">{escape(v.title)}</a>'
                f"<p>{escape(v.summary)}</p></li>"
            )
        if not items:
            return f"<p>{'Nothing here yet.' if lang == 'en' else 'Пока пусто.'}</p>"
        return f'<ol class="posts" reversed>{"".join(items)}</ol>'

    def devlog(self, lang: str) -> str:
        h = self.site.home[lang]
        body = (
            f'<section class="log page"><h1>{escape(h["devlog_title"])}</h1>{self.post_list(lang)}</section>'
        )
        return self.page(lang, h["devlog_title"], body, other=self.url(UI[lang]["other_lang"], "posts/"))

    def post(self, post: Post, lang: str) -> str:
        v = post.get(lang)
        assert v is not None
        ui = UI[lang]
        cover = ""
        if v.youtube or v.cover:
            poster = f"{self.base}media/{post.slug}/{v.cover}" if v.cover else ""
            cover = self.player(v.youtube, poster, "", ui["play"], v.title)
        body_html = media_links(md(v.body), self.base, post)
        other_lang = ui["other_lang"]
        other = (
            self.url(other_lang, f"posts/{post.slug}/")
            if post.get(other_lang)
            else self.url(other_lang, "posts/")
        )
        body = f"""
<article class="post">
  <header>
    <time datetime="{post.date.date().isoformat()}">{date_text(post.date, lang)}</time>
    <h1>{escape(v.title)}</h1>
    <p class="lede">{escape(v.summary)}</p>
  </header>
  {cover}
  <div class="text">{body_html}</div>
  <p class="back"><a href="{self.url(lang, "posts/")}">{ui["back"]}</a></p>
</article>
"""
        return self.page(lang, v.title, body, other=other, description=v.summary, cls="article")

    def standing(self, page: Page, lang: str) -> str:
        v = page.get(lang)
        assert v is not None
        other_lang = UI[lang]["other_lang"]
        other = self.url(other_lang, f"{page.slug}/" if page.get(other_lang) else "")
        body = f"""
<article class="post">
  <header>
    <h1>{escape(v.title)}</h1>
    <p class="lede">{escape(v.summary)}</p>
  </header>
  <div class="text">{md(v.body)}</div>
</article>
"""
        return self.page(lang, v.title, body, other=other, description=v.summary, cls="article")

    def feed(self, lang: str) -> str:
        s, h = self.site.settings, self.site.home[lang]
        site_url = str(s.get("url", "")).rstrip("/")
        items = []
        for post in self.site.posts:
            v = post.get(lang)
            if v is None:
                continue
            link = f"{site_url}/{prefix(lang)}posts/{post.slug}/"
            html = media_links(md(v.body), f"{site_url}/", post)
            items.append(
                f"<item><title>{escape(v.title)}</title><link>{link}</link>"
                f'<guid isPermaLink="true">{link}</guid>'
                f"<pubDate>{email.utils.format_datetime(post.date)}</pubDate>"
                f"<description>{escape(v.summary)}</description>"
                f"<content:encoded>{escape(html)}</content:encoded></item>"
            )
        return (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/" '
            'xmlns:atom="http://www.w3.org/2005/Atom"><channel>'
            f"<title>{escape(h.get('feed_title', s.get('title', '')))}</title>"
            f"<link>{site_url}/{prefix(lang)}</link>"
            f'<atom:link href="{site_url}/{prefix(lang)}feed.xml" rel="self" type="application/rss+xml"/>'
            f"<description>{escape(h.get('description', ''))}</description><language>{lang}</language>"
            f"{''.join(items)}</channel></rss>\n"
        )

    def not_found(self) -> str:
        ui = UI["en"]
        body = (
            f'<section class="page"><h1>404</h1><p>{ui["not_found"]}</p>'
            f'<p><a href="{self.url("en")}">{ui["to_home"]}</a></p></section>'
        )
        return self.page("en", "404", body, other=self.url("ru"))
