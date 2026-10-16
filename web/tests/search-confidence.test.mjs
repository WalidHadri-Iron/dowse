import assert from "node:assert/strict";
import test from "node:test";
import { CLOSEST_LABEL, hasClearMatch, resultSummary } from "../src/lib/search-confidence.ts";

test("confidence comes from the answer's strong count, never from scores or candidate count", () => {
  assert.equal(hasClearMatch({ strong: 0, hits: [1, 2, 3], score: 99 }), false);
  assert.equal(hasClearMatch({ strong: 1, score: 0 }), true);
  assert.equal(hasClearMatch({ strong: 3 }), true);
  assert.equal(hasClearMatch(null), false);
  assert.equal(hasClearMatch(undefined), false);
});

test("all search summaries call unclear candidates closest, not found", () => {
  assert.equal(resultSummary({ strong: 0 }, 5), CLOSEST_LABEL);
  assert.equal(resultSummary({ strong: 3 }, 4), "4 found");
  assert.equal(CLOSEST_LABEL, "Nothing clearly matches · closest shown");
});

test("empty visible sets are no results, not phantom matches or closest candidates", () => {
  assert.equal(resultSummary({ strong: 0 }, 0), "No results");
  assert.equal(resultSummary({ strong: 3 }, 0), "No results");
  assert.equal(resultSummary({ strong: 3 }, 1), "1 found");
});

test("pending and cleared answers have no result-confidence claim", () => {
  assert.equal(resultSummary(null, 0), "");
  assert.equal(resultSummary(undefined, 5), "");
});
