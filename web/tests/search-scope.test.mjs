import assert from "node:assert/strict";
import test from "node:test";
import { scopePaths, appendScopes, scopesFromRoute, withoutScope } from "../src/lib/search-scope.ts";
import { activeFilters } from "../src/lib/search-filters.ts";

test("scopes deduplicate Windows casing/slashes without collapsing separate selected folders", () => {
  assert.deepEqual(scopePaths(["C:\\Docs", "c:/docs/", "C:\\Docs\\Reports", "D:\\Pictures"]),
    ["C:\\Docs", "C:\\Docs\\Reports", "D:\\Pictures"]);
  assert.deepEqual(scopePaths("C:\\Docs"), ["C:\\Docs"]);
  assert.deepEqual(scopePaths(null), []);
});

test("route round-trips multiple folders and legacy links, even with commas, ampersands and Unicode", () => {
  const paths = ["C:\\A, B", "D:\\Pictures & photos", "D:\\Été"];
  const params = new URLSearchParams();
  appendScopes(params, paths);
  assert.deepEqual(scopesFromRoute(new URLSearchParams(params.toString())), paths);
  assert.deepEqual(scopesFromRoute(new URLSearchParams("in=C%3A%5CDocs")), ["C:\\Docs"]);
  assert.deepEqual(scopesFromRoute(new URLSearchParams("q=dog")), []);
});

test("each selected folder has a removable chip; removing one keeps the others", () => {
  const paths = ["C:\\Docs", "D:\\Photos"];
  const filters = activeFilters("dog", null, [], paths, { date: null, ignoredFilters: [] });
  assert.deepEqual(filters, paths.map((label) => ({ id: "folder", label, source: "manual" })));
  assert.deepEqual(withoutScope(paths, "c:/docs/"), ["D:\\Photos"]);
  assert.deepEqual(paths, ["C:\\Docs", "D:\\Photos"]); // never mutate the live/draft array
});
