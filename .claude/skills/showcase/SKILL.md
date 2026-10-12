---
name: showcase
description: Show the project's work to an audience - film a short of the game with the real AI npc's (gothic-shorts), write a devlog post for the site on GitHub Pages (site/), write the Telegram post or the YouTube title and description for it. Use whenever the user wants to present, demo or announce progress, a feature or a milestone, make a video, short, clip or trailer, update the site or devlog, or write a post about the project - also in Russian (шортс, ролик, видео, показать, демо, анонс, пост, дневник, сайт, телеграм, ютуб), even when they do not name the tools.
---

# Showcase

The audience: Gothic fans (Polish, German, Russian, Czech), people who run local models (r/LocalLLaMA),
Russian developers (Habr, Telegram). Most gamers distrust generative AI in games, so every piece has to
read as proof, not as a pitch: a real take of the real game, what was cut said plainly, nothing staged.

What exists:
- `shorts/` - `gothic-shorts`: a scene file, the hero's lines voiced by TTS, the npc's hearing them
  through speech recognition and answering live, filmed on the test bench with the game's sound, cut to
  1080x1920 with subtitles (`shorts/README.md`).
- `site/` - `gothic-site`: the site, English with a Russian copy, a devlog with RSS
  (`site/README.md`). It is published from `main` by `.github/workflows/pages.yml`.
- `references/shots.md` - scene ideas per feature, camera recipes, what goes wrong on set.
- `references/copy.md` - templates and rules for posts, Telegram, YouTube, in both languages.

## Pick the piece

Ask what changed since the last piece (`git log --since=<date of the newest post> --no-merges --oneline`,
the newest post is in `site/content/posts/`). Then:

| What changed | Piece |
|---|---|
| a feature you can see or hear in the game | a short, then a devlog post that embeds it |
| a milestone, several features, a decision | a devlog post (+ a Telegram text) |
| tooling, internals | a devlog post only if it changes what players will get; else nothing |

One idea per short. "Xardas sees through a lie" is a short; "the AI npc's" is not.

## A short

1. **Write the shot**: `shorts/shots/<name>.toml`. Start from the closest scene in `references/shots.md`.
   The hook is the idea in under six words. Keep the scene under 45 s of talk: two or three hero lines.
   Hero lines are what a player would say out loud: short, with the npc's name first (it tells the server
   whom you talk to). Hold the npc with `ai attach` before `rec`, unless the short is about an npc doing
   something else while it talks.
2. **Film takes**: `cd shorts && uv run gothic-shorts film <name> --takes 3`. A take is ~1 minute plus a
   first build or voice design. The summary of each take prints the hero's lines, what speech recognition
   heard and every npc line.
3. **Pick a take** by reading the summaries: the one whose answers carry the hook. A take with
   `timed out` lost an answer: film again. If no take carries it, change the hero's lines (the npc answers
   what it heard), not the cut, and film again.
4. **Cut**: `uv run gothic-shorts cut <name> <take>`; read `short-ru.md` (the transcript of the cut) and
   look at `short-ru.png` (frames over the whole short). Check a frame where something looks off:
   `ffmpeg -ss 12 -i short-ru.mp4 -frames:v 1 /tmp/f.png`.
5. **English**: `uv run gothic-shorts subs <name> <take> --lang en`, translate every text in the take's
   `subs.en.json` (plain, spoken English; keep names as the English game has them: Xardas, Lee, Lord
   Hagen), then `cut ... --lang en`.
6. **Hand it over**: the full file stays in `.shorts/<name>/take-NN/` (for upload, 1080x1920, -14 LUFS).
   To show it on the user's phone send a copy under 30 MB:
   `ffmpeg -i short-ru.mp4 -c:v libx264 -preset slow -crf 25 -c:a aac -b:a 128k short-ru-preview.mp4`.
   Write the YouTube title and description (`references/copy.md`) into the `youtube` section of the short's
   JSON. Uploading: `gothic-shorts upload <short> --meta <json> --lang <ru|en>` sends it to the user's channel
   as a private video, and only after the user said yes to that upload in the conversation (name the file,
   the channel and the title). Making it public is the user's, in YouTube Studio. Never post anywhere else.

## A devlog post

1. Facts first: the commits since the last post, the docs they touched (`docs/`), the takes. Write only
   what the code does today; plans go under "Next" and say they are plans.
2. `cd site && uv run gothic-site new <slug> --title "..." --title-ru "..."`, then write `en.md` and
   `ru.md` (`references/copy.md`: structure and voice). Both languages say the same things; write each
   natively rather than translating word by word.
3. Media: a still from a take as the cover (`gothic-site frame <main.mp4> <post>/cover.jpg --at <s> --width 540`),
   the cut's frames as a picture (`ffmpeg -i short-en.png -vf scale=1200:-2 -q:v 4 <post>/sheet-en.jpg`).
   A short goes in as `youtube = "<id>"` once the user has uploaded it; until then the cover stands in.
   Keep pictures under ~250 KB: the repository keeps them forever.
4. When the post comes with a new short, offer to update the home page (`content/home.toml`): its
   conversation, `[hero] youtube`, the loops (`gothic-site clip`, under 3 MB).
5. Check it: `uv run pytest -q`, then preview (the `site` preview in `.claude/launch.json`, or
   `uv run gothic-site serve`) and look at the post at phone width and at desktop width.
6. Commit on a branch and open a pull request; the site publishes when it is merged into `main`.

## A Telegram post

Russian, short, from the facts of the post: `references/copy.md`. Give it to the user as text to paste;
add the link to the post (`https://kaifaty.github.io/GothicAI/ru/posts/<slug>/`) once it is published.

## Rules that hold for every piece

- **Real takes only.** Never edit or splice an npc's answer, never pick lines from different takes into
  one short. Cutting the pauses while the model thinks is fine, and the description says so.
- **No cloned voices.** The hero's and the npc's voices are designed from descriptions. Say so when
  voices come up; never make a voice that imitates the original actors or anyone real.
- **Say the status.** Not playable yet, macOS from source, needs the player's own copy of Gothic II: Night
  of the Raven. Do not promise dates.
- **Keep private things out**: no IP addresses or host names of the GPU server, no keys, emails or home
  paths in texts, frames or descriptions; never film the launcher's settings or logs.
- **Credit the base**: the game is Piranha Bytes' and THQ Nordic's, the engine is OpenGothic (Try); link
  them in posts, do not imply an affiliation.
