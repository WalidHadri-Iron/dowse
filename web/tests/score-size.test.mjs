import assert from "node:assert/strict";
import test from "node:test";
import { topScore, scoreSize, packScoreCases } from "../src/lib/score-size.ts";

test("card area follows the score ratio, not the rank", () => {
  const a = scoreSize(10, 10), b = scoreSize(5, 10), c = scoreSize(2.5, 10);
  assert(Math.abs(b*b / (a*a) - 0.5) < 1e-10);
  assert(Math.abs(c*c / (a*a) - 0.25) < 1e-10);
  assert(a > b && b > c);
});

test("ties and near-ties are equally sized without an arbitrary oversized first result", () => {
  assert.equal(scoreSize(5, 5), scoreSize(5, 5));
  assert(scoreSize(4.99, 5) / scoreSize(5, 5) > 0.99);
});

test("weak cases remain readable, extreme values stay bounded, and unscored answers stay uniform", () => {
  assert.equal(scoreSize(0.001, 10), 0.78);
  assert.equal(scoreSize(-5, 10), 0.78);
  assert.equal(scoreSize(200, 10), 1.8);
  for (const score of [0, -1, undefined, NaN, Infinity]) assert.equal(scoreSize(score, 0), 1);
  assert.equal(scoreSize(NaN, 10), 1);
  assert.equal(scoreSize(10, Infinity), 1);
  assert.equal(topScore([NaN, Infinity, undefined, -2, 3, 10]), 10);
  assert.equal(topScore([]), 0);
});

test("score scaling is invariant to the query's numeric score units", () => {
  assert.equal(scoreSize(5, 10), scoreSize(500, 1000));
  assert.equal(scoreSize(5, 10, 0.9, 1.5), scoreSize(500, 1000, 0.9, 1.5));
});

test("packing accommodates variable result sizes without overlaps and keeps the best centred", () => {
  const sizes = [1.8, 1.79, 1.3, 1.1, ...Array.from({length: 84}, (_, i) => 0.78 + (i % 7) * 0.15)];
  const boxes = packScoreCases(sizes, 1.36, 1, 0.16);
  assert.equal(boxes.length, sizes.length);
  assert.equal(boxes[0].x, 0); assert.equal(boxes[0].y, 0);
  for (let i = 0; i < boxes.length; i++) {
    const a = boxes[i];
    assert.equal(a.width, sizes[i] * 1.36);
    for (const b of boxes.slice(i + 1)) {
      assert(Math.abs(a.x - b.x) >= (a.width+b.width)/2 + 0.16 - 1e-7
        || Math.abs(a.y - b.y) >= (a.height+b.height)/2 + 0.16 - 1e-7);
    }
  }
  assert.deepEqual(packScoreCases(sizes, 1.36, 1, 0.16), boxes);
  assert.deepEqual(packScoreCases([], 1.36, 1, 0.16), []);
});

test("near-ties and zero-score filters pack safely at a full-size result count", () => {
  for (const score of [0, 1]) {
    const sizes = Array.from({length: 88}, () => scoreSize(score, score));
    const boxes = packScoreCases(sizes, 1.36, 1, 0.16);
    assert(boxes.every((b) => Number.isFinite(b.x) && Number.isFinite(b.y)));
  }
});
