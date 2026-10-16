/** Candidate retrieval is deliberately broad. Presentation uses the engine's existing strong flags,
 * not a new guessed score threshold, and never changes ranking or loses the optional candidates. */
export const CLOSEST_PREVIEW_LIMIT = 6;

export function focusedHits<T extends { strong: boolean }>(hits: readonly T[], clear: boolean, expanded: boolean): T[] {
  if (expanded) return [...hits];
  return clear ? hits.filter(hit => hit.strong) : hits.slice(0, CLOSEST_PREVIEW_LIMIT);
}

export function focusedSummary(clear: boolean, matching: number, shown: number, total: number): string {
  if (!total) return "No results";
  if (!clear) return `Showing ${shown} of ${total} closest candidates`;
  const other = shown - matching;
  return `${matching} clear ${matching === 1 ? "match" : "matches"}${other > 0 ? ` · ${other} other ${other === 1 ? "candidate" : "candidates"}` : ""}`;
}
