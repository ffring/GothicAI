// G2's bow dice as the chart draws it: the same formulas as the engine, in metres and percent.

// RANGED_CHANCE_MINDIST / RANGED_CHANCE_MAXDIST of G2's scripts (read by gamescript.cpp)
export const MIN_DIST = 15;
export const MAX_DIST = 45;

// Gothic2.exe (DamageCalculator::vanillaChance, damagecalculator.cpp): from 100 % point blank down to the talent at
// 15 m, then the line H - H*d/45 m: two thirds of the talent right past 15 m, none at 45 m
export const exeChance = (talent: number, d: number): number => {
  const c = d <= MIN_DIST ? 100 + ((talent - 100) * d) / MIN_DIST : talent - (talent * d) / MAX_DIST;
  return Math.max(0, Math.trunc(c));
};

// OpenGothic before (the other branch of DamageCalculator::rangeDamage, ReferenceBowRangeG2 15 m,
// MaxBowRange 45 m): the same up to 15 m, then linear from the talent to none at 45 m, no step. It measured the
// arrow's path (1-2 m shorter than the straight line), so in the game it was kinder still than this line.
export const openGothicChance = (talent: number, d: number): number => {
  if (d < MIN_DIST) {
    return 100 + ((talent - 100) * d) / MIN_DIST;
  }
  if (d < MAX_DIST) {
    return talent * (1 - (d - MIN_DIST) / (MAX_DIST - MIN_DIST));
  }
  return 0;
};

export type Curve = 'exe' | 'opengothic';
export const chance = (curve: Curve, talent: number, d: number) =>
  curve === 'exe' ? exeChance(talent, d) : openGothicChance(talent, d);
