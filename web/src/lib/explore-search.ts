import { relevance } from "./search-relevance.ts";
import { scoreSize } from "./score-size.ts";
import { under } from "./selection-state.ts";
import type { SearchFilter } from "./api";

/** Hard restrictions apply to the whole collection, independently of ranking/retrieval limits. */
export function filterExplore<T extends { path: string; kind: string; fileDate?: string | null }>(
  pieces: T[], filters: readonly SearchFilter[],
): T[] {
  const folders = filters.filter(f => f.id === "folder").map(f => f.label).filter((p): p is string => !!p);
  const kinds = filters.find(f => f.id === "kind")?.kinds ?? [];
  const date = filters.find(f => f.id === "date");
  if (!folders.length && !kinds.length && !date) return pieces;
  return pieces.filter(piece => (!folders.length || folders.some(folder => under(piece.path, folder)))
    && (!kinds.length || kinds.some(kind => kind === piece.kind))
    && (!date || !!piece.fileDate && (!date.from || piece.fileDate >= date.from) && (!date.to || piece.fileDate <= date.to)));
}

/** Empty aisles disappear; surviving aisle identities and file positions never change. */
export function filterExploreAisles<T extends { index: number; count: number }>(aisles: T[], pieces: readonly { aisle: number }[]): T[] {
  const counts = new Map<number, number>();
  for (const piece of pieces) counts.set(piece.aisle, (counts.get(piece.aisle) ?? 0) + 1);
  return aisles.filter(a => counts.has(a.index)).map(a => ({ ...a, count: counts.get(a.index)! }));
}

/** Presentation only: never move files between their established aisle coordinates. */
export function exploreAppearance(path: string, found: ReadonlyMap<string, number> | null,
  matches: ReadonlySet<string> | null, clear: boolean, score: number | undefined, best: number, selected: string | null) {
  const state = relevance(path, found, clear && (matches === null || matches.has(path)));
  const activeSelection = selected !== null && !!found?.has(selected);
  return {
    state,
    size: state === "neutral" ? 1 : state === "other" ? .78 : state === "match" ? scoreSize(score, best, .9, 1.5) : 1,
    light: state === "neutral" ? 1 : state === "other" ? .35 : activeSelection && path !== selected ? .82 : 1,
    lift: state === "match" ? .35 : 0,
    outline: state === "neutral" ? 0 : state === "other" ? .22 : .95,
  };
}

export function rankedStep(paths: readonly string[], selected: string | null, step: -1 | 1): string | null {
  if (!paths.length) return null;
  const index = selected === null ? -1 : paths.indexOf(selected);
  if (index < 0) return step > 0 ? paths[0] : paths[paths.length - 1];
  return paths[Math.max(0, Math.min(paths.length - 1, index + step))];
}

/** Fit actual cases, not a fixed camera distance or the centroid of six potentially scattered hits. */
export function fitExplore(points: readonly { x: number; y: number; size?: number }[], aspect: number, fov: number, padding = .7, minimum = 3.8) {
  if (!points.length) return null;
  const ratio = Number.isFinite(aspect) && aspect > 0 ? aspect : 1;
  const left = Math.min(...points.map(p => p.x - .84 * (p.size ?? 1) / 2));
  const right = Math.max(...points.map(p => p.x + .84 * (p.size ?? 1) / 2));
  const bottom = Math.min(...points.map(p => p.y - .62 * (p.size ?? 1) / 2));
  const top = Math.max(...points.map(p => p.y + .62 * (p.size ?? 1) / 2));
  const tan = Math.tan(fov * Math.PI / 360);
  const z = Math.max(minimum, (right - left + padding * 2) / (2 * tan * ratio), (top - bottom + padding * 2) / (2 * tan)) * 1.12 + .4;
  return { x: (left + right) / 2, y: (top + bottom) / 2, z };
}
