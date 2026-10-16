import assert from "node:assert/strict";
import test from "node:test";
import { inactiveBy, pathKey, under, searchable } from "../src/lib/selection-state.ts";

test("Windows paths match across casing, slashes and trailing separators", () => {
  assert.equal(pathKey("C:\\Photos\\"), pathKey("c:/photos"));
  assert(under("C:\\PHOTOS\\trip\\dog.jpg", "c:/photos/"));
  assert(!under("C:\\Photos-old\\dog.jpg", "c:/photos"));
});

test("an inactive folder covers named files and identifies the parent to reactivate", () => {
  assert.equal(inactiveBy("C:\\Photos\\trip\\dog.jpg", ["C:\\Photos\\trip", "C:\\Photos"]), "C:\\Photos");
  assert.equal(inactiveBy("C:\\Notes\\one.txt", ["C:\\Photos"]), null);
});

test("cached search results obey the same specific inclusion/exclusion and suspension rules", () => {
  const selection = { folders: ["C:/Photos"], files: ["C:/Photos/private/keep.jpg"],
    removed: ["C:/Photos/private"], inactive: [] };
  assert(searchable("C:/Photos/dog.jpg", selection));
  assert(!searchable("C:/Photos/private/other.jpg", selection));
  assert(searchable("C:/Photos/private/keep.jpg", selection));
  assert(!searchable("C:/Elsewhere/dog.jpg", selection));
  selection.inactive = ["C:/Photos"];
  assert(!searchable("C:/Photos/private/keep.jpg", selection));
});
