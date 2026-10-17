# Site

The project's page on GitHub Pages (https://kaifaty.github.io/GothicAI/): what works now and the devlog,
in English (`/`) and Russian (`/ru/`), with an RSS feed for each. Built by a small generator in this folder
and published by `.github/workflows/pages.yml` when `site/` changes on `main`; pull requests only build it.

```bash
cd site
uv run gothic-site serve                       # http://127.0.0.1:47860/ (the preview "site" in .claude/launch.json)
uv run gothic-site new first-quest --title "The first quest" --title-ru "Первый квест"
uv run gothic-site frame ../.shorts/xardas-lie/take-02/main.mp4 content/posts/<post>/cover.jpg --at 41 --width 540
uv run gothic-site clip ../.shorts/xardas-wrath/take-09/short-ru.mp4 static/hero/wrath-ru.mp4 --start 33.6 --length 20
uv run gothic-site build                       # _build/ for /GothicAI/ (what the workflow publishes)
uv run pytest -q
```

## Content

- `content/home.toml`: the settings (`[site]`: base path, address, repository, Telegram), the short on the
  home page (`[hero]`), and the home page's text per language (`[en]`, `[ru]`): the conversation shown
  first, the pitch, what works now, how a conversation goes.
- `content/posts/<date>-<slug>/en.md` and `ru.md` (Russian optional, English required), with TOML front
  matter between `+++` lines: `title`, `summary` (one sentence: the list and the feed), `date` with a time
  zone, optionally `cover` (a picture in the post's folder) and `youtube` (a video id). Pictures next to
  the post are linked by their name: `![Frames of the short](sheet-en.jpg)`.
- `content/pages/<slug>/en.md` and `ru.md`: pages outside the devlog at `/<slug>/`, linked from every
  footer, with `title` and `summary` only. `privacy` is the privacy policy that the Google Cloud project of
  the YouTube uploader points to (`shorts/README.md`): change it when the uploader asks for more.

## Media

The repository keeps every file forever, so media stay small: pictures as JPEG, 540 px wide for a 9:16
frame and 1200 px for wide ones (under ~250 KB); a muted loop under 3 MB (`clip` warns). Whole shorts go
to YouTube: set `youtube` in the post, or `[hero] youtube` for the home page, and the page shows a still
that loads the video from youtube-nocookie.com only when it is clicked.

`static/brand/` is the channel's mark: the wax seal with the G as favicon (`.ico`, `.svg`), app icons and the
manifest, and `lockup.svg` (the seal and the lettering) in the bar. They are exported from the brand kit
outside the repository (`~/.local/share/gothic-ai/brand/logo/letter/kit/`): change them there and copy.

## Publishing

Once: Settings → Pages → Source: GitHub Actions. Then a push to `main` that touches `site/` publishes it.
The page shows real takes: no edited answers, and say when pauses were cut (`shorts/README.md`).
