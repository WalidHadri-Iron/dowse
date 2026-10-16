import { packScoreCases } from "./score-size.ts";

export interface WallSlot { x: number; y: number; column: number; row: number }

/** A viewport-shaped rack, not an unbounded spiral. Keep file order independent of search rank;
 * variable column/row sizes reserve enough room for score-scaled cases without overlaps. O(n). */
export function wallSlots(sizes: readonly number[], aspect: number, width: number, height: number, gap: number): WallSlot[] {
  if (!sizes.length) return [];
  const ratio = Number.isFinite(aspect) && aspect > 0 ? aspect : 1;
  const columns = Math.min(sizes.length, Math.max(1, Math.round(Math.sqrt(sizes.length * ratio * height / width))));
  const rows = Math.ceil(sizes.length / columns);
  const widths = Array(columns).fill(0) as number[], heights = Array(rows).fill(0) as number[];
  sizes.forEach((size, i) => {
    const safe = Number.isFinite(size) && size > 0 ? size : 1;
    widths[i % columns] = Math.max(widths[i % columns], width * safe);
    heights[Math.floor(i / columns)] = Math.max(heights[Math.floor(i / columns)], height * safe);
  });
  function centres(lengths: number[]): number[] {
    const total = lengths.reduce((a, b) => a + b, 0) + gap * (lengths.length - 1);
    let edge = -total / 2;
    return lengths.map((length) => { const centre = edge + length / 2; edge += length + gap; return centre; });
  }
  const xs = centres(widths), ys = centres(heights);
  return sizes.map((_, i) => ({ x: xs[i % columns], y: -ys[Math.floor(i / columns)], column: i % columns, row: Math.floor(i / columns) }));
}

/** Retain the collection's order during search. Candidates from other browse pages follow the loaded rack.
 * The incoming array remains in result rank order, so selecting a wall case still selects the correct hit. */
export function wallOrder(paths: readonly string[], collection: readonly string[]): number[] {
  const rank = new Map(collection.map((path, index) => [path, index]));
  return paths.map((_, i) => i).sort((a, b) => (rank.get(paths[a]) ?? collection.length + a) - (rank.get(paths[b]) ?? collection.length + b));
}

/** Never wrap a wide collection around the cylinder: edge cases remain front-facing and distinct. */
export function wallRadius(halfWidth: number): number {
  return Math.max(9, halfWidth / 0.45);
}

/** Gather clear matches on a flat, close viewing plane. One depth preserves score-derived area ratios;
 * reserve an outer band of the viewport for the collection behind them, rather than zooming the rack away. */
export function foregroundGroup(sizes: readonly number[], aspect: number, fov: number, width: number, height: number, gap: number) {
  // Start the best match at the origin, then pack lower ranks around it, not into a top-left grid.
  const ratio = Number.isFinite(aspect) && aspect > 0 ? aspect : 1;
  const slots = packScoreCases(sizes, width, height, gap * .65, ratio);
  const x = Math.max(0, ...slots.map((slot, i) => Math.abs(slot.x) + width * sizes[i] / 2));
  const y = Math.max(0, ...slots.map((slot, i) => Math.abs(slot.y) + height * sizes[i] / 2));
  const tan = Math.tan(fov * Math.PI / 360);
  const distance = Math.max(5.2, x / (tan * ratio * .72), y / (tan * .72));
  return { slots, distance };
}
