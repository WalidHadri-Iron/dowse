import type { Answer, Kind, SearchFilter, SearchOptions } from "./api";
import { scopePaths } from "./search-scope.ts";

/** Manual controls update immediately; inferred chips belong only to the query that produced them. */
export function activeFilters(query: string, answer: Answer | null, kinds: Kind[], within: string | string[] | null,
                              options: SearchOptions): SearchFilter[] {
  const filters: SearchFilter[] = [];
  for (const folder of scopePaths(within)) filters.push({ id: "folder", label: folder, source: "manual" });
  if (kinds.length) filters.push({ id: "kind", kinds, source: "manual" });
  if (options.date) {
    const { from, to } = options.date;
    filters.push({ id: "date", source: "manual", from, to,
      label: from && to ? `${from} – ${to}` : from ? `From ${from}` : `Through ${to}` });
  }
  if (answer?.query === query.trim() || answer?.query === query) {
    for (const filter of answer?.filters ?? []) {
      if (filter.source === "query" && !filters.some((f) => f.id === filter.id) &&
          !options.ignoredFilters.includes(filter.id as "date" | "kind")) filters.push(filter);
    }
  }
  return filters;
}

export function evidenceLines(evidence: import("./api").Evidence[]): string[] {
  return evidence.map((e) => {
    const words = e.words.map((word) => `“${word}”`).join(", ");
    const where = e.place ? ` · ${e.place}` : "";
    switch (e.source) {
      case "filename": return `Filename contains ${words}`;
      case "folder": return `Folder name contains ${words}`;
      case "title": return `Title contains ${words}${where}`;
      case "date": return `${e.dateSource === "taken" ? "Photo taken" : "File modified"} on ${e.date} · within date filter`;
      case "ocr": return `Words read in image: ${words}${where}`;
      case "text": return `Text contains ${words}${where}`;
      case "semantic": return `${e.modality === "visual" ? "Visual similarity" : e.modality === "audio" ? "Audio similarity" : "Similar meaning"}${where} · not an exact word match`;
    }
  });
}
