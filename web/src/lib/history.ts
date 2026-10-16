// What history adds to search, as plain functions: an earlier search to complete the typed words with, and the files
// gone back to lately put first in the collection (each once).

/** The rest of the newest earlier search that starts with `typed` (case aside: what's typed stays as typed, so
 * the rest is cut where the typing ends), or "". */
export function completion(typed: string, past: readonly { query: string; exact: boolean }[]): string {
  if (!typed.trim()) return "";
  const fold = (text: string) => text.toLocaleLowerCase();
  const start = fold(typed);
  const found = past.find((q) => !q.exact && q.query.length > typed.length && fold(q.query).startsWith(start));
  return found ? found.query.slice(typed.length) : "";
}

/** `opened` first, then the rest of `collection` without them. */
export function openedFirst<T extends { path: string }>(opened: readonly T[], collection: readonly T[]): T[] {
  const seen = new Set(opened.map((item) => item.path));
  return [...opened, ...collection.filter((item) => !seen.has(item.path))];
}
