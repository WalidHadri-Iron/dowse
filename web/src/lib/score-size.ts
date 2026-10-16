/** Scores are relative ranking values, not calibrated probabilities. Only compare within one answer. */
export function topScore(scores: Iterable<number | undefined>): number {
  let top = 0;
  for (const score of scores) if (score !== undefined && Number.isFinite(score)) top = Math.max(top, score);
  return top;
}

/** Visible AREA follows score / best score. Square-root side lengths avoid exaggerating score differences.
 * The minimum keeps weak results usable; unscored/date-only answers stay uniform, rather than faking certainty. */
export function scoreSize(score: number | undefined, top: number, minimum = 0.78, maximum = 1.8): number {
  if (score === undefined || !Number.isFinite(score) || !Number.isFinite(top) || top <= 0) return 1;
  return Math.max(minimum, maximum * Math.sqrt(Math.max(0, Math.min(1, score / top))));
}

export interface ScoreCase { x: number; y: number; width: number; height: number }

/** Pack continuously sized cases around the best match. Adjacent edge candidates avoid a fixed 2x2 winner
 * and let near-ties sit together; gaps prevent large cases covering smaller ones. Runs only on layout changes. */
export function packScoreCases(sizes: readonly number[], width: number, height: number, gap: number, aspect?: number): ScoreCase[] {
  const placed: ScoreCase[] = [];
  for (const size of sizes) {
    const w = width * size, h = height * size;
    let best = { x: 0, y: 0 }, distance = Infinity;
    const candidates = [{ x: 0, y: 0 }];
    for (const p of placed) {
      const dx = (p.width + w) / 2 + gap, dy = (p.height + h) / 2 + gap;
      for (const x of [p.x - dx, p.x + dx]) {
        for (const y of [p.y, p.y - (p.height - h) / 2, p.y + (p.height - h) / 2]) candidates.push({ x, y });
      }
      for (const y of [p.y - dy, p.y + dy]) {
        for (const x of [p.x, p.x - (p.width - w) / 2, p.x + (p.width - w) / 2]) candidates.push({ x, y });
      }
    }
    for (const at of candidates) {
      const weight = aspect && Number.isFinite(aspect) && aspect > 0
        ? at.x * at.x / (aspect * aspect) + at.y * at.y : at.x * at.x * 0.55 + at.y * at.y * 1.4;
      if (weight >= distance) continue;
      if (placed.some((p) => Math.abs(at.x - p.x) < (w + p.width) / 2 + gap - 1e-8
        && Math.abs(at.y - p.y) < (h + p.height) / 2 + gap - 1e-8)) continue;
      best = at; distance = weight;
    }
    placed.push({ ...best, width: w, height: h });
  }
  return placed;
}
