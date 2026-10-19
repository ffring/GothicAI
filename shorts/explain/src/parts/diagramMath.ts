// Diagram coordinates use centimetres: X points right, Y points forward (up on screen).
export const clamp01 = (v: number) => Math.max(0, Math.min(1, v));

export type PathPoint = {t: number; x: number; y: number};

export const interpolatePath = (path: readonly PathPoint[], t: number): {x: number; y: number} => {
  const first = path[0];
  if (t <= first.t) return {x: first.x, y: first.y};
  for (let i = 1; i < path.length; i++) {
    const a = path[i - 1];
    const b = path[i];
    if (t <= b.t) {
      const k = (t - a.t) / (b.t - a.t);
      return {x: a.x + (b.x - a.x) * k, y: a.y + (b.y - a.y) * k};
    }
  }
  const last = path[path.length - 1];
  return {x: last.x, y: last.y};
};

export const inCone = (point: {x: number; y: number}, halfDeg: number, rangeCm: number, turnDeg = 0) => {
  const bearing = Math.atan2(point.x, point.y) * 180 / Math.PI;
  const angle = ((bearing - turnDeg + 180) % 360 + 360) % 360 - 180;
  return Math.hypot(point.x, point.y) <= rangeCm && Math.abs(angle) <= halfDeg;
};

type XY = {x: number; y: number};
export const quadraticPoint = (a: XY, c: XY, b: XY, k: number): XY => ({
  x: (1 - k) ** 2 * a.x + 2 * (1 - k) * k * c.x + k ** 2 * b.x,
  y: (1 - k) ** 2 * a.y + 2 * (1 - k) * k * c.y + k ** 2 * b.y,
});
