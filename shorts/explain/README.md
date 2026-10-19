# Explainer videos

## Как работает сейчас

Shorts that show how a mechanic works: footage and stills from the test bench, cards with charts, labels over
what is in the picture, and captions that follow a recorded voice word by word. Rendered with
[Remotion](https://www.remotion.dev): every frame is drawn from a plan (JSON), so a number on screen is computed
from the engine's formula or taken from the bench's log, never typed into a picture.

The plan's `format` selects `tall` (1080 × 1920, the default) or `wide` (1920 × 1080), both at 30 fps.
`Root.tsx` calculates the composition dimensions and duration from that plan. `Explainer.tsx` lays out the
picture and overlays; `parts/` draws the cards and captions. `brand: false` disables the ending and needs no
external assets. Publication of either format uses the same `gothic_shorts.levels.publish` checks.

```bash
cd shorts/explain
npm ci
npx remotion studio --props '{"brand":false}'                           # the demo plan: the dice chart, no assets
npx remotion studio --props src/demo-wide.json                          # five wide cards, no external assets
npx remotion still Explainer --props src/demo-wide.json --frame 330 out/wide-formula.png
npx remotion still Explainer --public-dir "$W" --props "$W/plan.json" --frame 120 "$W/render/f120.png"
npx remotion render Explainer --public-dir "$W" --props "$W/plan.json" --codec h264 --crf 18 "$W/render/preview.mp4"
```

`$W` is the short's own folder (outside the repository): footage, stills and voice lines referenced by the plan
live there and are served as the public dir.

Connect the channel assets to that folder once, before rendering with branding enabled:

```bash
ln -s "$HOME/.local/share/gothic-ai/brand/sting" "$W/brand"
```

Remotion serves files through this directory symlink with `--public-dir "$W"`; no copying is needed.
The renderer uses `brand/sting.mp4` from it.

## Publication

After `npm ci` above, publish from `shorts/`:

```bash
uv run gothic-shorts explain <name-or-workspace>
uv run gothic-shorts explain "$W" --plan plan.en.json --out "$W/render/short-en.mp4"
```

The plan is relative to the workspace, as with `dub`; the workspace is a folder or a name under
`~/.local/share/gothic-ai/explain/`, as with `words` and `studio`. The default output is
`<workspace>/render/short-<plan.lang>.mp4`.

Remotion renders the muted picture at the plan's dimensions (H.264, CRF 18) and three WAV stems. The command joins the picture with
the game/ending stem, takes each speech window from the first to the last word of a nonempty `lines` entry,
and calls `gothic_shorts.levels.publish`. That shared function calibrates the voice, effects, music and ending,
masters the mix, and checks the decoded AAC: voice −14 LUFS, true peak ≤ −1 dBTP, the balance between stems,
and zero clicks. It prints the table and writes a sibling `.levels.json` report. A failed check returns a
nonzero exit code and removes the output video. Temporary pictures and stems are removed on success or failure.
Without `music` in the plan, music rows have no data, as in the shared publisher.

`stem` is optional: `voice` plays only the voice lines at 1; `game` plays the footage at each beat's `game`
level, then the brand sting at 1; `music` plays the looped bed at 1 with its `fadeIn`/`fadeOut`. Stems have no
ducking or plan mix gains. All stems cover the full video, including the sting; music and voice stop at the
end of the beats. Music fades are already rendered, so publication passes `music_fades=False` to `levels`.
The body duration is the summed beats rounded to frames; the full length adds `STING_FRAMES` (66) when branded.

For publication, `gameVolume`, `duck`, `music.volume`, `music.duck` and `brandVolume` are ignored: `levels`
sets the levels. `beat.game` remains a relative level within the effects stem. Record footage with `MUSIC=0`
and sound effects enabled, then add one music bed in the plan. Direct Remotion renders without `stem` keep
the original plan gains and ducking for Studio and drafts; they do not run the publication checks.

## The plan

`src/schema.ts` is the format; a missing or misspelt field stops the render with its path. In short:

- `hook`: the big text over the first seconds;
- `format`: `tall` (default, 1080 × 1920) or `wide` (1920 × 1080). The same `Explainer` composition and
  `gothic-shorts explain` command handle both; there is no short-video duration limit;
- `brand`: boolean, defaults to `true`. The 2.2-second letter sting plays after the last beat
  (1080 × 1920, 30 fps, its own −14 LUFS source audio). In `wide`, this vertical video is fitted to the
  full frame height, centred on black without cropping or stretching;
  Publication calibrates the sting separately from the body effects through `levels`. In drafts it uses
  `brandVolume`. The voice and footage sound stop before it; the music fades out by its start. The render's duration
  includes these extra 66 frames. `false` needs no brand assets and preserves the original frames, captions
  and duration;
- `brandVolume`: draft-only nonnegative linear gain for the sting's audio, defaults to `0.355` (about −9 dB);
- `beats`: in order, each `footage` (an mp4, `freeze` and `zoom` optional), `still` (a png), `split` (two takes,
  top/bottom in `tall`, left/right in `wide`, with labels) or `card` (a chart); `texts` on top of a beat, `tags` (a label pinned to a point
  of the picture, to its left or right with `side`, lines appearing one by one, a roll counter that stops on the
  real number), `strokes` (a line drawing itself between two points of the picture; with `cut` it stops at a wall:
  solid up to it, dashed past it, a cross on it) and `fans` (a sector opening from a point): lines of sight and the
  bow's focus from the bench's `sight` export;
- `split` keeps the same field names in either format: `src`/`from` are the first half, `srcBottom`/`fromBottom`
  the second; `labels` and `labelColors` follow that order. In `wide`, labels sit at the top of each half and
  a thin vertical line divides the pictures. Footage and stills cover their frame or half-frame;
- coordinates (`texts.y`, tag pins, strokes, fans and zoom origins) are pixels of the selected frame.
  Texts without `y` start at 260 in `tall`, 140 in `wide`; an explicit `y` is kept as supplied;
- the cards (`chart.type`): `dice` (the chance by distance, curves drawn as they are named), `formula` (the
  terms a line each as they are said, then a worked example computed the way `damagecalculator.cpp` does it:
  rounded at each step, protection not below 0, at least 5), `bars` (groups growing one by one), `dots` (marks
  filling up to a count), `stats` (the character's numbers greying out under a question); each but `stats`
  names its `source`;
- `lines`: the voice's words with their times, a sentence per line (`gothic-shorts words`); the captions are
  pages of 2-4 words of them, the word being said in gold. A page never ends on a preposition or a conjunction
  (`в`, `как`, `of the`, `than`): it goes to the next one. For captions in another language than the voice, the
  plan's maker spreads the translated words over the spoken ones (a part per pause);
- `voice`: the recorded lines and where they start; in drafts the game's sound ducks under them (`duck`),
  and `gameVolume` sets it as a whole (0: the draft footage is silent). Publication uses the word windows
  and shared `levels` rules instead;
- `music`: one looped bed under the beats (`file`, `fadeIn`, `fadeOut`); `volume` in the voice's pauses and
  `duck` times that while it speaks apply only to drafts. Publication calibrates and ducks this stem in `levels`.

`{chance:exe:40:16}` in a tag is the chance by that rule (`exe`: Gothic2.exe's dice, `opengothic`: OpenGothic's
before it) for that talent and distance, computed by `src/dice.ts` from the same formulas as
`OpenGothic/common/game/damagecalculator.cpp`.

## Look

`src/theme.ts`: the palette (the old rule grey, the new one gold, skill blue, strength red, dexterity green), the
safe area of the Shorts interface (no text in the top 150, bottom 380, right 150 px). Fonts: PT Serif Bold and
Montserrat, with full Cyrillic, shipped in `src/fonts/` under the SIL Open Font License. With `brand: true`,
all voice captions use PT Serif Bold, including words with digits. Chart labels, tags and counters keep
Montserrat; all colours stay the same. With `brand: false`, captions retain their original Montserrat.

`wide` uses 5% margins instead of the Shorts safe area. Captions sit at the bottom at up to 56 px and fit
within two lines; unusually long pages reduce the font size. Hooks use 80 px near the top, texts use the
full inner width, and cards have landscape geometry: a wide dice plot, formula terms beside the worked
example, grouped bars, a ten-column dot grid and a centred stats panel. The vertical brand sting remains
pillarboxed until a horizontal asset is available.

Validation: `npm run typecheck` in this folder and `uv run pytest -q tests/test_explain.py` in `shorts/`.
The publication tests cover the default/tall/wide dimensions, all four stems and the shared levels path.
The asset-free wide demo has five six-second cards; representative stills are frames 150 (`dice`), 330
(`formula`), 510 (`bars`), 690 (`dots`), 870 (`stats`), and 120 (two-line captions).

Remotion is under the [Remotion License](https://www.remotion.pro/license): free for individuals and companies of
up to three people. The versions are pinned in `package.json`.

## План и журнал

- 2026-10-19: added plan-driven `tall`/`wide` dimensions, landscape media, split, captions and cards,
  centred vertical branding, wide publication and the asset-free five-card demo. Tall remains the default.
