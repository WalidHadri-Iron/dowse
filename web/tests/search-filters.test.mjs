import assert from "node:assert/strict";
import test from "node:test";
import { activeFilters, evidenceLines } from "../src/lib/search-filters.ts";

const inferred = { query: "photos from last summer", filters: [
  { id: "kind", source: "query", kinds: ["image"] },
  { id: "date", source: "query", label: "summer 2026" },
] };
const options = { date: null, ignoredFilters: [] };

test("inferred chips appear even with no results, and belong only to their query", () => {
  assert.deepEqual(activeFilters(inferred.query, inferred, [], null, options), inferred.filters);
  assert.deepEqual(activeFilters("new query", inferred, [], null, options), []);
});

test("manual filters override inference and removal suppresses inferred chips immediately", () => {
  const filters = activeFilters(inferred.query, inferred, ["doc"], "C:\\Documents", {
    date: { from: "2025-01-01", to: "" }, ignoredFilters: [],
  });
  assert.deepEqual(filters.map((f) => [f.id, f.source]), [["folder", "manual"], ["kind", "manual"], ["date", "manual"]]);
  assert.equal(filters[2].label, "From 2025-01-01");
  assert.deepEqual(activeFilters(inferred.query, inferred, [], null, {
    date: null, ignoredFilters: ["date", "kind"],
  }), []);
});

test("evidence explains real words, filename, page and timestamp without scores", () => {
  const lines = evidenceLines([
    { source: "filename", words: ["sherlock"] },
    { source: "folder", words: ["Contracts"] },
    { source: "text", words: ["walnuts"], place: "Page 5", score: 3.74 },
    { source: "text", words: ["meeting"], place: "1:20" },
    { source: "tag", words: ["Night Owls"] },
    { source: "ocr", words: ["WiFi"] },
    { source: "semantic", words: [], modality: "visual", place: "Page 9" },
    { source: "date", words: [], dateSource: "taken", date: "2025-07-12" },
  ]);
  assert.deepEqual(lines, [
    "Filename contains “sherlock”", "Folder name contains “Contracts”", "Text contains “walnuts” · Page 5",
    "Text contains “meeting” · 1:20", "Music tags contain “Night Owls”", "Words read in image: “WiFi”",
    "Visual similarity · Page 9 · not an exact word match",
    "Photo taken on 2025-07-12 · within date filter",
  ]);
  assert.ok(!lines.join().includes("3.74"));
});
