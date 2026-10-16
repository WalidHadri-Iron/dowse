/** Match Windows' case/slash-insensitive paths without confusing sibling prefixes. */
export function pathKey(path: string): string { return path.replaceAll("\\", "/").replace(/\/+$/, "").toLowerCase(); }
export function under(path: string, parent: string): boolean {
  const key = pathKey(path), root = pathKey(parent);
  return key === root || key.startsWith(root + "/");
}
export function inactiveBy(path: string, inactive: readonly string[]): string | null {
  return [...inactive].sort((a, b) => a.length - b.length).find((root) => under(path, root)) ?? null;
}

export function searchable(path: string, selection?: { folders: readonly string[]; files: readonly string[];
  removed: readonly string[]; inactive: readonly string[] } | null): boolean {
  if (!selection) return true;
  if (inactiveBy(path, selection.inactive)) return false;
  let best = -1, included = false;
  const target = pathKey(path);
  for (const [paths, include, recursive] of [[selection.folders, true, true], [selection.files, true, false],
    [selection.removed, false, true]] as const) {
    for (const source of paths) {
      const exact = target === pathKey(source);
      if (!exact && !(recursive && under(path, source))) continue;
      const specificity = pathKey(source).length * 2 + (exact ? 1 : 0);
      if (specificity > best || (specificity === best && !include)) { best = specificity; included = include; }
    }
  }
  return included;
}
