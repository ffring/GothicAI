# Explainer shorts

Shorts that show how a mechanic works: footage and stills from the test bench, cards with charts, labels over
what is in the picture, and captions that follow a recorded voice word by word. Rendered with
[Remotion](https://www.remotion.dev): every frame is drawn from a plan (JSON), so a number on screen is computed
from the engine's formula or taken from the bench's log, never typed into a picture.

```bash
cd shorts/explain
npm ci
npx remotion studio                                                    # the demo plan: the dice chart
npx remotion still Explainer --public-dir "$W" --props "$W/plan.json" --frame 120 "$W/render/f120.png"
npx remotion render Explainer --public-dir "$W" --props "$W/plan.json" --codec h264 --crf 18 "$W/render/short-ru.mp4"
```

`$W` is the short's own folder (outside the repository): footage, stills and voice lines referenced by the plan
live there and are served as the public dir.

## The plan

`src/schema.ts` is the format; a missing or misspelt field stops the render with its path. In short:

- `hook`: the big text over the first seconds;
- `beats`: in order, each `footage` (an mp4, `freeze` and `zoom` optional), `still` (a png), `split` (two takes,
  top and bottom, with labels) or `card` (a chart); `texts` on top of a beat, `tags` (a label pinned to a point
  of the picture, lines appearing one by one, a roll counter that stops on the real number);
- `lines`: the voice's words with their times, a sentence per line (from `gothic-shorts align`); the captions
  are pages of 2-4 words of them, the word being said in gold;
- `voice`: the recorded lines and where they start; the game's sound ducks under them.

`{chance:exe:40:16}` in a tag is the chance by that rule (`exe`: Gothic2.exe's dice, `opengothic`: OpenGothic's
before it) for that talent and distance, computed by `src/dice.ts` from the same formulas as
`OpenGothic/common/game/damagecalculator.cpp`.

## Look

`src/theme.ts`: the palette (the old rule grey, the new one gold, skill blue, strength red, dexterity green), the
safe area of the Shorts interface (no text in the top 150, bottom 380, right 150 px). Fonts: PT Serif Bold and
Montserrat, with full Cyrillic, shipped in `src/fonts/` under the SIL Open Font License.

Remotion is under the [Remotion License](https://www.remotion.pro/license): free for individuals and companies of
up to three people. The versions are pinned in `package.json`.
