import assert from "node:assert/strict";
import test from "node:test";
import { TEXTURE_LEVELS, texturePixels } from "../src/lib/texture-quality.ts";

test("whole-floor pictures use the warmed small thumbnail tier", () => {
  assert.equal(texturePixels(40, 30, 1.25, 4 / 3), 96);
  assert.equal(texturePixels(0, 0, 1, 4 / 3), 96);
});

test("zooming chooses sharper previews at the actual display density", () => {
  assert.equal(texturePixels(100, 75, 1, 4 / 3), 256);
  assert.equal(texturePixels(400, 300, 1, 4 / 3), 512);
  assert.equal(texturePixels(400, 300, 2, 4 / 3), 1024);
  assert.equal(texturePixels(800, 600, 2, 4 / 3), 2048);
});

test("portrait and panorama crops need more source pixels than the case width", () => {
  assert.equal(texturePixels(400, 300, 1, 0.5), 1024);
  assert.equal(texturePixels(400, 300, 1, 3), 1024);
  assert.equal(texturePixels(400, 300, 2, 3), 2048);
});

test("stable levels avoid a separate cache entry for each wheel tick", () => {
  assert.equal(texturePixels(510, 382, 1, 4 / 3), 512);
  assert.equal(texturePixels(512, 384, 1, 4 / 3), 512);
  assert.equal(texturePixels(513, 384, 1, 4 / 3), 1024);
  assert.equal(texturePixels(530, 397, 1, 4 / 3), 1024);
});

test("maximum quality matches the picture endpoint's 2048-pixel limit", () => {
  assert.equal(TEXTURE_LEVELS.at(-1), 2048);
  assert.equal(texturePixels(10000, 7500, 2, 4 / 3), 2048);
});
