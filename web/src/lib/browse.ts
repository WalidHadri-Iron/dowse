export const BROWSE_PAGE_SIZE = 400;   // a whole library of a few hundred files at once; pages beyond
export function browseRange(offset: number, shown: number, total: number): string {
  if (!total || !shown) return "0 files";
  if (offset === 0 && shown >= total) return `${total.toLocaleString()} ${total === 1 ? "file" : "files"}`;
  return `${(offset + 1).toLocaleString()}–${(offset + shown).toLocaleString()} of ${total.toLocaleString()} files`;
}
