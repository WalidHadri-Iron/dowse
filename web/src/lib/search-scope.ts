import { pathKey } from "./selection-state.ts";

/** Single-folder links remain compatible; folder names may contain commas or other delimiters. */
export function scopePaths(scope: string | readonly string[] | null): string[] {
  const paths = typeof scope === "string" ? [scope] : scope ?? [];
  const unique = new Map<string, string>();
  for (const path of paths) if (path.trim() && !unique.has(pathKey(path))) unique.set(pathKey(path), path);
  return [...unique.values()];
}
export function scopesFromRoute(params: URLSearchParams): string[] { return scopePaths(params.getAll("in")); }
export function appendScopes(params: URLSearchParams, paths: readonly string[]): void {
  for (const path of scopePaths(paths)) params.append("in", path);
}
export function withoutScope(paths: readonly string[], removed: string): string[] {
  return paths.filter((path) => pathKey(path) !== pathKey(removed));
}
