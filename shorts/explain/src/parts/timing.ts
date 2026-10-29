// The series' durations in seconds, without Remotion, so the plan's schema and the tests share them with motion.ts.

export const MOTION = {
  fast: 0.2, // a small thing appearing: a label line, a number
  base: 0.35, // a text, a panel, a fade or a flash between beats
  frame: 0.5, // the whole frame travelling: a slide or wipe between beats (twice the travel of a panel, ~40% longer)
  slow: 0.6, // a big move: a zoom, a line drawing itself
  stagger: 0.08, // between items of one group entering together
  follow: 0.06, // an attached part settling after its main body: a tag's box after its pin, words after their band
};

// a beat's transition when the plan gives no `dur`: what travels across the frame takes longer than what fades
export const TRANSITION_DUR = {
  fade: MOTION.base,
  flash: MOTION.base,
  zoom: MOTION.base,
  slide: MOTION.frame,
  wipe: MOTION.frame,
} as const;
