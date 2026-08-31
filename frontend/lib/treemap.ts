export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** Worst (largest) aspect ratio produced by a candidate row. */
function worstRatio(sum: number, min: number, max: number, side: number): number {
  if (sum <= 0 || side <= 0) return Infinity;
  const s2 = sum * sum;
  const w2 = side * side;
  return Math.max((w2 * max) / s2, s2 / (w2 * min));
}

/**
 * Squarified treemap (Bruls, Huizing & van Wijk). Returns one rect per input
 * value, in input order, sized proportionally to the value and packed to keep
 * tiles as close to square as possible.
 *
 * Non-positive values are given a rect of zero size so the caller can skip them
 * without the indices shifting.
 */
export function squarify(values: number[], width: number, height: number): Rect[] {
  const out: Rect[] = values.map(() => ({ x: 0, y: 0, w: 0, h: 0 }));
  if (width <= 0 || height <= 0) return out;

  const entries = values
    .map((value, index) => ({ value, index }))
    .filter((e) => e.value > 0)
    .sort((a, b) => b.value - a.value);

  const total = entries.reduce((s, e) => s + e.value, 0);
  if (total <= 0) return out;

  // Convert values to pixel areas once; each packed row consumes exactly its
  // own area, so the remaining rectangle always matches the remaining values.
  const scale = (width * height) / total;
  const areas = new Map(entries.map((e) => [e.index, e.value * scale]));

  let x = 0;
  let y = 0;
  let w = width;
  let h = height;
  let i = 0;

  while (i < entries.length) {
    const side = Math.min(w, h);
    if (side <= 0) break;

    // Grow the row while it keeps improving the worst aspect ratio.
    let sum = 0;
    let min = Infinity;
    let max = 0;
    let end = i;
    while (end < entries.length) {
      const area = areas.get(entries[end].index)!;
      const nextSum = sum + area;
      const nextMin = Math.min(min, area);
      const nextMax = Math.max(max, area);
      if (
        end === i ||
        worstRatio(nextSum, nextMin, nextMax, side) <= worstRatio(sum, min, max, side)
      ) {
        sum = nextSum;
        min = nextMin;
        max = nextMax;
        end += 1;
      } else {
        break;
      }
    }

    const thickness = sum / side;
    if (w >= h) {
      // Row runs down the left edge as a column of width `thickness`.
      let cursor = y;
      for (let k = i; k < end; k++) {
        const area = areas.get(entries[k].index)!;
        const tileHeight = area / thickness;
        out[entries[k].index] = { x, y: cursor, w: thickness, h: tileHeight };
        cursor += tileHeight;
      }
      x += thickness;
      w -= thickness;
    } else {
      // Row runs across the top edge as a strip of height `thickness`.
      let cursor = x;
      for (let k = i; k < end; k++) {
        const area = areas.get(entries[k].index)!;
        const tileWidth = area / thickness;
        out[entries[k].index] = { x: cursor, y, w: tileWidth, h: thickness };
        cursor += tileWidth;
      }
      y += thickness;
      h -= thickness;
    }

    i = end;
  }

  return out;
}
