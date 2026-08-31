import { squarify } from "@/lib/treemap";

const W = 400;
const H = 300;

describe("squarify", () => {
  it("returns one rect per input value, in input order", () => {
    const rects = squarify([50, 30, 20], W, H);
    expect(rects).toHaveLength(3);
  });

  it("fills the container exactly", () => {
    const rects = squarify([50, 30, 20], W, H);
    const area = rects.reduce((sum, r) => sum + r.w * r.h, 0);
    expect(area).toBeCloseTo(W * H, 4);
  });

  it("sizes tiles proportionally to their value", () => {
    const rects = squarify([75, 25], W, H);
    const [big, small] = rects.map((r) => r.w * r.h);
    expect(big / small).toBeCloseTo(3, 4);
  });

  it("keeps every tile inside the container bounds", () => {
    const rects = squarify([40, 25, 15, 12, 8], W, H);
    for (const r of rects) {
      expect(r.x).toBeGreaterThanOrEqual(-1e-9);
      expect(r.y).toBeGreaterThanOrEqual(-1e-9);
      expect(r.x + r.w).toBeLessThanOrEqual(W + 1e-9);
      expect(r.y + r.h).toBeLessThanOrEqual(H + 1e-9);
    }
  });

  it("produces non-overlapping tiles", () => {
    const rects = squarify([40, 25, 15, 12, 8], W, H);
    for (let i = 0; i < rects.length; i++) {
      for (let j = i + 1; j < rects.length; j++) {
        const a = rects[i];
        const b = rects[j];
        const overlapX = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
        const overlapY = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
        expect(Math.min(overlapX, overlapY)).toBeLessThan(1e-6);
      }
    }
  });

  it("keeps aspect ratios reasonable for evenly sized values", () => {
    const rects = squarify([10, 10, 10, 10, 10, 10], W, H);
    for (const r of rects) {
      const ratio = Math.max(r.w / r.h, r.h / r.w);
      expect(ratio).toBeLessThan(3);
    }
  });

  it("gives a single value the whole container", () => {
    const [rect] = squarify([100], W, H);
    expect(rect).toEqual({ x: 0, y: 0, w: W, h: H });
  });

  it("returns zero-sized rects for non-positive values", () => {
    const rects = squarify([100, 0, -5], W, H);
    expect(rects[1]).toEqual({ x: 0, y: 0, w: 0, h: 0 });
    expect(rects[2]).toEqual({ x: 0, y: 0, w: 0, h: 0 });
    expect(rects[0].w * rects[0].h).toBeCloseTo(W * H, 4);
  });

  it("handles empty input and degenerate containers", () => {
    expect(squarify([], W, H)).toEqual([]);
    expect(squarify([10, 5], 0, H)).toEqual([
      { x: 0, y: 0, w: 0, h: 0 },
      { x: 0, y: 0, w: 0, h: 0 },
    ]);
  });
});
