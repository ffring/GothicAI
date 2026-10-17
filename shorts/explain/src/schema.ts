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

const Point = z.tuple([z.number(), z.number()]); // px of the 1080x1920 frame, from the bench's data

// a line over the picture (a line of sight from the bench's `sight`): it draws itself from `from` toward `to`; with
// `cut` it stops there (a wall in the way): solid up to it, `cutColor` and dashed past it from `cutAt`, a cross on it
const Stroke = z.object({
  from: Point,
  to: Point,
  cut: Point.optional(),
  color: z.string(),
  cutColor: z.string().optional(),
  at: z.number().default(0),
  dur: z.number().default(0.6),
  cutAt: z.number().optional(),
  until: z.number().optional(),
  width: z.number().default(8),
});

// a fan from a point (the bow's focus from the head): it opens from the edge toward `a` to the edge toward `b`, as
// long as the one toward `b`
const Fan = z.object({
  apex: Point,
  a: Point,
  b: Point,
  color: z.string(),
  at: z.number().default(0),
  dur: z.number().default(0.8),
  until: z.number().optional(),
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

// the damage formula, built term by term, then a worked example computed from its inputs
const FormulaCard = z.object({
  type: z.literal('formula'),
  decimal: z.string().default(','), // the decimal mark of the language
  title: z.string(), // "УРОН ="
  terms: z.array(z.object({text: z.string(), at: z.number(), color: z.string().optional()})), // a line each
  legend: z.object({text: z.string(), at: z.number()}).optional(),
  example: z
    .object({
      at: z.number(),
      bow: z.number(),
      dex: z.number(),
      draw: z.number(),
      prot: z.number(),
      zones: z.array(z.object({name: z.string(), k: z.number(), at: z.number()})), // a result line each
    })
    .optional(),
  source: z.string(),
});

// bars by groups (e.g. damage per second by hand against the dice at 10 / 20 / 30 m); 1 = the reference
const BarsCard = z.object({
  type: z.literal('bars'),
  title: z.string(),
  series: z.array(z.object({name: z.string(), color: z.string()})),
  groups: z.array(z.object({label: z.string(), values: z.array(z.number()), at: z.number()})),
  max: z.number(),
  format: z.string().default('×{v}'), // how a value is written over its bar
  decimal: z.string().default(','),
  source: z.string(),
});

// marks filling one by one (e.g. 17 arrows of 30 in the head)
const DotsCard = z.object({
  type: z.literal('dots'),
  total: z.number(),
  hits: z.number(),
  at: z.number(),
  dur: z.number(),
  title: z.string(),
  caption: z.string(), // `{n}`: the counter
  source: z.string(),
});

// the character's numbers greying out, a question over them
const StatsCard = z.object({
  type: z.literal('stats'),
  rows: z.array(z.object({name: z.string(), value: z.string(), color: z.string().optional()})),
  fadeAt: z.number(),
  question: z.object({text: z.string(), at: z.number()}),
});

const Chart = z.discriminatedUnion('type', [DiceChart, FormulaCard, BarsCard, DotsCard, StatsCard]);

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
  strokes: z.array(Stroke).default([]),
  fans: z.array(Fan).default([]),
  chart: Chart.optional(),
});

const Word = z.object({text: z.string(), start: z.number(), end: z.number()});

export const Plan = z.object({
  lang: z.enum(['ru', 'en']),
  hook: z.object({text: z.string(), until: z.number()}),
  beats: z.array(Beat).min(1),
  lines: z.array(z.array(Word)).default([]), // the captions: the voice's words, a sentence per line
  voice: z.array(z.object({file: z.string(), start: z.number()})).default([]),
  duck: z.number().default(0.3), // the game's volume while the voice speaks
  gameVolume: z.number().default(1), // the footage's own sound, all of it (0: none)
  // one music bed under the whole short, ducked under the voice (linear volumes)
  music: z
    .object({
      file: z.string(),
      volume: z.number(), // in the voice's pauses
      duck: z.number().default(0.5), // times that while the voice speaks
      fadeIn: z.number().default(1),
      fadeOut: z.number().default(1.5),
    })
    .optional(),
});

export type Plan = z.infer<typeof Plan>;
export type Beat = z.infer<typeof Beat>;
export type DiceChart = z.infer<typeof DiceChart>;
export type FormulaCard = z.infer<typeof FormulaCard>;
export type BarsCard = z.infer<typeof BarsCard>;
export type DotsCard = z.infer<typeof DotsCard>;
export type StatsCard = z.infer<typeof StatsCard>;
export type TagT = z.infer<typeof Tag>;
export type StrokeT = z.infer<typeof Stroke>;
export type FanT = z.infer<typeof Fan>;
export type TextT = z.infer<typeof Text>;
export type Word = z.infer<typeof Word>;
