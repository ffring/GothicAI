import {z} from 'zod';

// The plan of a short (plan.json in the short's workspace). Times are seconds: `at`, `until` and keyframes from the
// start of their beat, words and voice lines from the start of the short. A missing field fails the render.

const Text = z.object({
  text: z.string(),
  at: z.number().default(0),
  until: z.number().optional(),
  y: z.number().default(260), // the top of the text, px
  size: z.number().default(64),
  color: z.string().optional(),
});

const TagLine = z.object({
  text: z.string(), // `{n}` is replaced by the counter
  at: z.number().default(0),
  color: z.string().optional(),
  count: z.object({to: z.number(), dur: z.number().default(0.8)}).optional(), // a roll that runs and stops
});

// a label over a thing in the picture; x, y: where the thing is (px of the 1080x1920 frame), from the bench's data
const Tag = z.object({
  x: z.number(),
  y: z.number(),
  lines: z.array(TagLine).min(1),
  at: z.number().default(0),
  until: z.number().optional(),
  side: z.enum(['center', 'right', 'left']).default('center'), // where the box opens from the pin
});

const CurveKind = z.enum(['exe', 'opengothic']);

const DiceChart = z.object({
  type: z.literal('dice'),
  talent: z.number(),
  axisX: z.string(),
  axisY: z.string(),
  source: z.string(), // small print in the corner
  // each curve is drawn up to the distance its keyframes give at the time: [[t, metres], ...]
  curves: z.array(z.object({curve: CurveKind, name: z.string(), draw: z.array(z.tuple([z.number(), z.number()]))})),
  talentMark: z.object({at: z.number(), text: z.string()}).optional(), // "навык": the talent's level at 15 m
  stepMark: z.object({at: z.number(), text: z.string()}).optional(), // "−⅓" at the step
  markers: z.array(z.object({d: z.number(), curve: CurveKind, at: z.number(), text: z.string()})).default([]), // `{v}`: the value
});

const Beat = z.object({
  id: z.string(),
  kind: z.enum(['footage', 'still', 'card', 'split']),
  dur: z.number().positive(),
  src: z.string().optional(), // footage mp4 or a still png in the workspace (the public dir)
  from: z.number().default(0), // where the footage starts
  srcBottom: z.string().optional(), // split: the lower half
  fromBottom: z.number().default(0),
  labels: z.tuple([z.string(), z.string()]).optional(), // split: top, bottom
  labelColors: z.tuple([z.string(), z.string()]).optional(), // default: the old rule's grey, the new one's gold
  freeze: z.number().optional(), // footage stops here (seconds into the beat)
  zoom: z.object({at: z.number(), dur: z.number(), scale: z.number(), x: z.number(), y: z.number()}).optional(),
  game: z.number().default(1), // the game's volume in this beat (ducked under the voice on top of it)
  texts: z.array(Text).default([]),
  tags: z.array(Tag).default([]),
  chart: DiceChart.optional(),
});

const Word = z.object({text: z.string(), start: z.number(), end: z.number()});

export const Plan = z.object({
  lang: z.enum(['ru', 'en']),
  hook: z.object({text: z.string(), until: z.number()}),
  beats: z.array(Beat).min(1),
  lines: z.array(z.array(Word)).default([]), // the captions: the voice's words, a sentence per line
  voice: z.array(z.object({file: z.string(), start: z.number()})).default([]),
  duck: z.number().default(0.3), // the game's volume while the voice speaks
});

export type Plan = z.infer<typeof Plan>;
export type Beat = z.infer<typeof Beat>;
export type DiceChart = z.infer<typeof DiceChart>;
export type TagT = z.infer<typeof Tag>;
export type TextT = z.infer<typeof Text>;
export type Word = z.infer<typeof Word>;
