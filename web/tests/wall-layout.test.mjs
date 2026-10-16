import assert from "node:assert/strict";
import test from "node:test";
import { wallSlots, wallOrder, wallRadius, foregroundGroup } from "../src/lib/wall-layout.ts";

test("large racks adapt to the viewport instead of forming a vertical spiral", () => {
  for (const count of [44, 214, 400, 500]) {
    for (const aspect of [0.65, 1.2, 2.4]) {
      const slots = wallSlots(Array(count).fill(1), aspect, 1.36, 1, .16);
      assert.equal(slots.length, count);
      const w = Math.max(...slots.map(s => s.x)) - Math.min(...slots.map(s => s.x)) + 1.36;
      const h = Math.max(...slots.map(s => s.y)) - Math.min(...slots.map(s => s.y)) + 1;
      assert(Math.abs(w / h / aspect - 1) < .3, `${count}, ${aspect}: ${w / h}`);
      assert(new Set(slots.map(s => `${s.column}:${s.row}`)).size === count);
      const radius = wallRadius(w / 2);
      assert(w / 2 / radius <= .45 + 1e-10); // no folded/edge-on cases
    }
  }
});

test("score-sized cases have space without changing their spatial cell", () => {
  const normal = Array(214).fill(1);
  const sizes = normal.map((s, i) => i < 6 ? 1.8 - i * .15 : s);
  const before = wallSlots(normal, 1.2, 1.36, 1, .16);
  const after = wallSlots(sizes, 1.2, 1.36, 1, .16);
  assert.deepEqual(before.map(s => [s.column, s.row]), after.map(s => [s.column, s.row]));
  for (let i = 0; i < after.length; i++) {
    for (let j = i + 1; j < after.length; j++) {
      assert(Math.abs(after[i].x - after[j].x) >= 1.36 * (sizes[i] + sizes[j]) / 2 + .16 - 1e-8
        || Math.abs(after[i].y - after[j].y) >= (sizes[i] + sizes[j]) / 2 + .16 - 1e-8);
    }
  }
});

test("search uses the same file order without changing candidate rank or dropping context", () => {
  const collection = ["a", "b", "c", "d", "e"];
  const ranked = ["d", "b", "outside", "a", "c", "e"];
  assert.deepEqual(wallOrder(ranked, collection), [3, 1, 4, 0, 5, 2]);
  assert.deepEqual(ranked, ["d", "b", "outside", "a", "c", "e"]);
  assert.deepEqual(wallOrder(collection, collection), [0, 1, 2, 3, 4]);
});

test("empty, single-file and invalid aspect layouts stay finite", () => {
  assert.deepEqual(wallSlots([], 1, 1.36, 1, .16), []);
  assert.deepEqual(wallSlots([1], 1, 1.36, 1, .16), [{x: 0, y: -0, column: 0, row: 0}]);
  for (const aspect of [0, -1, NaN, Infinity]) {
    assert(wallSlots([1, 1, 1], aspect, 1.36, 1, .16).every(s => Number.isFinite(s.x) && Number.isFinite(s.y)));
  }
});

test("foreground groups fit a closer viewing plane with room for background context", () => {
  for (const count of [1, 4, 12, 60]) {
    for (const aspect of [.65, 1.2, 2.4]) {
      const sizes = Array.from({length: count}, (_, i) => 1.8 - (i % 7) * .12);
      const {slots, distance} = foregroundGroup(sizes, aspect, 32, 1.36, 1, .16);
      const halfH = distance * Math.tan(32 * Math.PI / 360);
      assert.equal(slots.length, count);
      assert.equal(slots[0].x, 0);
      assert.equal(slots[0].y, 0);
      for (let i = 0; i < count; i++) {
        assert(Math.abs(slots[i].x) + sizes[i] * 1.36 / 2 <= halfH * aspect * .72 + 1e-8);
        assert(Math.abs(slots[i].y) + sizes[i] / 2 <= halfH * .72 + 1e-8);
        for (let j = i + 1; j < count; j++) {
          assert(Math.abs(slots[i].x - slots[j].x) >= (sizes[i] + sizes[j]) * 1.36 / 2 + .104 - 1e-8
            || Math.abs(slots[i].y - slots[j].y) >= (sizes[i] + sizes[j]) / 2 + .104 - 1e-8);
        }
      }
    }
  }
});

test("the highest score occupies the centre, with smaller results packed around it", () => {
  const sizes = [1.8, 1.5, 1.2, .9];
  const {slots} = foregroundGroup(sizes, 1.2, 32, 1.36, 1, .16);
  assert.equal(slots[0].x, 0); assert.equal(slots[0].y, 0);
  assert(slots.slice(1).every(slot => Math.hypot(slot.x, slot.y) > 0));
  assert(slots[0].width > slots[1].width && slots[1].width > slots[2].width && slots[2].width > slots[3].width);
  assert.deepEqual(foregroundGroup(sizes, 1.2, 32, 1.36, 1, .16).slots, slots);
  const ties = foregroundGroup([1.8, 1.8, 1.8], 1.2, 32, 1.36, 1, .16).slots;
  assert(ties.every(slot => slot.width === ties[0].width));
});

test("foreground depth stays in front of the rack, and zoom never crosses its plane", () => {
  const {distance} = foregroundGroup([1.8, 1.7, 1.2, 1], 1.2, 32, 1.36, 1, .16);
  const baseDistance = 36;
  const nearDistance = Math.min(distance, baseDistance * .65);
  const depth = baseDistance - nearDistance;
  assert(depth > 0);
  for (const zoom of [.5, 1, 1.2, 4, 8]) {
    const camera = depth + (baseDistance - depth) / zoom;
    assert(camera > depth);
    assert(Math.abs(camera - depth - nearDistance / zoom) < 1e-10);
  }
});

test("many matches preserve score area ratios without shrinking away the background rack", () => {
  const sizes = Array.from({length: 60}, (_, i) => 1.8 - (i % 7) * .12);
  const {distance} = foregroundGroup(sizes, 1.2, 32, 1.36, 1, .16);
  const rackDistance = 28;
  const nearDistance = Math.min(distance, rackDistance * .65);
  const scale = nearDistance / distance;
  assert(scale < 1);
  assert(Math.abs(sizes[0] * scale / nearDistance - sizes[0] / distance) < 1e-10);
  assert(Math.abs((sizes[1] * scale) ** 2 / (sizes[0] * scale) ** 2 - sizes[1] ** 2 / sizes[0] ** 2) < 1e-10);
});
