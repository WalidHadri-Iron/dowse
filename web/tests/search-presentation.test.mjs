import assert from "node:assert/strict";
import test from "node:test";
import { focusedHits, focusedSummary, CLOSEST_PREVIEW_LIMIT } from "../src/lib/search-presentation.ts";

test("default results use engine-confirmed strong matches, not guessed score cutoffs", () => {
  const hits = [{path: 'best', strong: true, score: 12}, {path: 'second', strong: true, score: 9},
    {path: 'unclear', strong: false, score: 8}, {path: 'weak', strong: false, score: 1}];
  assert.deepEqual(focusedHits(hits, true, false).map(h => h.path), ['best', 'second']);
  assert.deepEqual(focusedHits(hits, true, true), hits);
  assert.equal(hits.length, 4);
});

test("nothing clear shows a few closest candidates with every candidate available explicitly", () => {
  const hits = Array.from({length: 60}, (_, i) => ({path: `file-${i}`, strong: false, score: 100-i}));
  assert.equal(CLOSEST_PREVIEW_LIMIT, 6);
  assert.deepEqual(focusedHits(hits, false, false), hits.slice(0, 6));
  assert.deepEqual(focusedHits(hits, false, true), hits);
  assert.deepEqual(focusedHits([], false, false), []);
});

test("genuine large match sets are not arbitrarily cut off, and zero scores do not fake confidence", () => {
  const hits = Array.from({length: 40}, () => ({strong: true, score: 0}));
  assert.equal(focusedHits(hits, true, false).length, 40);
  assert.equal(focusedHits(hits, false, false).length, 6);
});

test("summaries distinguish matches from expanded candidates without showing scores", () => {
  assert.equal(focusedSummary(true, 1, 1, 9), '1 clear match');
  assert.equal(focusedSummary(true, 3, 3, 9), '3 clear matches');
  assert.equal(focusedSummary(true, 3, 9, 9), '3 clear matches · 6 other candidates');
  assert.equal(focusedSummary(true, 3, 4, 4), '3 clear matches · 1 other candidate');
  assert.equal(focusedSummary(false, 0, 6, 40), 'Showing 6 of 40 closest candidates');
  assert.equal(focusedSummary(false, 0, 40, 40), 'Showing 40 of 40 closest candidates');
  assert.equal(focusedSummary(false, 0, 0, 0), 'No results');
});
