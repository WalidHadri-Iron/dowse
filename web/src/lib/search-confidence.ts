/** Answer-level confidence is supplied by the search engine, never inferred from score or result count. */
export const CLOSEST_LABEL = "Nothing clearly matches · closest shown";

export function hasClearMatch(answer: { strong: number } | null | undefined): boolean {
  return (answer?.strong ?? 0) > 0;
}

/** Use the visible count, so hidden/inactive or off-floor results cannot inflate the summary. */
export function resultSummary(answer: { strong: number } | null | undefined, count: number): string {
  if (!answer) return "";
  if (!count) return "No results";
  return hasClearMatch(answer) ? `${count} found` : CLOSEST_LABEL;
}
