import assert from "node:assert/strict";
import test from "node:test";
import { BROWSE_PAGE_SIZE, browseRange } from "../src/lib/browse.ts";

test("browse shows the full collection total, not a misleading recent-preview count", () => {
  assert.equal(BROWSE_PAGE_SIZE, 400);
  assert.equal(browseRange(0, 80, 214), "1–80 of 214 files");
  assert.equal(browseRange(80, 80, 214), "81–160 of 214 files");
  assert.equal(browseRange(160, 54, 214), "161–214 of 214 files");
});
test("small and empty collections have honest counts without claiming a page of missing files", () => {
  assert.equal(browseRange(0, 1, 1), "1 file");
  assert.equal(browseRange(0, 40, 40), "40 files");
  assert.equal(browseRange(0, 0, 0), "0 files");
  assert.equal(browseRange(0, 80, 12000), "1–80 of 12,000 files");
});
