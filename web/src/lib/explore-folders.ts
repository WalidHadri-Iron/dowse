import { pathKey, under } from "./selection-state.ts";
import type { MapPiece, MapAisle } from "../components/MapFloor.svelte";

/** A display-only collection for files added one by one, even inside watched folders. */
export const DOWSED_FILES = "Dowsed files";

function parentPath(path: string): string {
  const normal = path.replaceAll("\\", "/");
  const slash = normal.lastIndexOf("/");
  return slash < 0 ? "" : normal.slice(0, slash + (slash === 0 || normal[slash - 1] === ":" ? 1 : 0));
}

/** Build once for the collection, before filters/search; neither can rearrange the surviving cases. */
export function folderFloor(pieces: readonly MapPiece[], roots: readonly string[], named: readonly string[] = []): { pieces: MapPiece[]; aisles: MapAisle[] } {
  const groups = new Map<string, { path: string; files: MapPiece[] }>();
  const individual = new Set(named.map(pathKey));
  for (const piece of pieces) {
    const parent = !individual.has(pathKey(piece.path)) && roots.some(root => under(piece.path, root)) ? parentPath(piece.path) : "";
    const key = parent ? `folder:${pathKey(parent)}` : "dowsed-files";
    const group = groups.get(key) ?? { path: parent, files: [] };
    group.files.push(piece); groups.set(key, group);
  }
  const ordered = [...groups].sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0);
  // Equal-size file slots leave room for the largest score-sized cases. Pack groups in shelves, not a tall column.
  const tiles = ordered.map(([key, group]) => {
    const columns = Math.ceil(Math.sqrt(group.files.length * 1.35));
    const rows = Math.ceil(group.files.length / columns);
    return { key, ...group, columns, rows, width: Math.max(4.8, columns * 1.65 + 1.4), height: rows * 1.3 + 2.4 };
  });
  const targetWidth = Math.max(0, ...tiles.map(t => t.width), Math.sqrt(tiles.reduce((sum, t) => sum + t.width * t.height, 0) * 1.65));
  const suffixes = new Map<string, number>();
  for (const tile of tiles) {
    const parts = tile.path.toLowerCase().split("/").filter(Boolean);
    for (let depth = 1; depth <= parts.length; depth++) {
      const suffix = parts.slice(-depth).join("/");
      suffixes.set(suffix, (suffixes.get(suffix) ?? 0) + 1);
    }
  }
  const out: MapPiece[] = [], aisles: MapAisle[] = [];
  let left = 0, top = 0, shelfHeight = 0;
  for (const [index, tile] of tiles.entries()) {
    if (left && left + tile.width > targetWidth) { left = 0; top += shelfHeight; shelfHeight = 0; }
    const x = left + tile.width / 2, y = -top - tile.height / 2;
    const parts = tile.path.split("/").filter(Boolean);
    const basename = parts.at(-1) ?? tile.path;
    // Two identically named folders must not look like one group; the tooltip always keeps the full path.
    let depth = 1;
    while (depth < parts.length && (suffixes.get(parts.slice(-depth).join("/").toLowerCase()) ?? 0) > 1) depth++;
    const label = !tile.path ? DOWSED_FILES : basename.toLowerCase() === DOWSED_FILES.toLowerCase()
      ? `${parts.slice(-depth).join(" / ")} (folder)` : parts.slice(-depth).join(" / ") || tile.path;
    aisles.push({ index, id: tile.key, label, count: tile.files.length, x, y,
      radius: Math.hypot(tile.width, tile.height) / 2,
      about: tile.path || "Files you added one by one, from anywhere. Originals stay where they are." });
    const files = [...tile.files].sort((a, b) => pathKey(a.path) < pathKey(b.path) ? -1 : pathKey(a.path) > pathKey(b.path) ? 1 : 0);
    for (const [slot, piece] of files.entries()) out.push({ ...piece, aisle: index,
      x: x + (slot % tile.columns - (tile.columns - 1) / 2) * 1.65,
      y: y + ((tile.rows - 1) / 2 - Math.floor(slot / tile.columns)) * 1.3 });
    left += tile.width; shelfHeight = Math.max(shelfHeight, tile.height);
  }
  return { pieces: out, aisles };
}
