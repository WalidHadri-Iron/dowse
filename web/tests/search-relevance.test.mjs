import assert from "node:assert/strict";
import test from "node:test";
import * as THREE from "three";
import { relevance, wallRelevance, softenBackground } from "../src/lib/search-relevance.ts";

test("the wall labels unclear answers as closest, never matches, without dropping candidates", () => {
  assert.equal(wallRelevance(true, undefined, false), "closest");
  assert.equal(wallRelevance(true, false, false), "closest");
  assert.equal(wallRelevance(true, true, false), "other");
  assert.equal(wallRelevance(true, undefined, true), "match");
  assert.equal(wallRelevance(true, true, true), "other");
  assert.equal(wallRelevance(false, false, false), "neutral");
  assert.equal(wallRelevance(false, true, true), "neutral");
});

test("cleared searches restore the neutral collection", () => {
  assert.equal(relevance("photo.jpg", null), "neutral");
});

test("all returned results, including rank zero and weaker results, are matches", () => {
  const found = new Map([["photo.jpg", 0], ["note.txt", 39]]);
  assert.equal(relevance("photo.jpg", found), "match");
  assert.equal(relevance("note.txt", found), "match");
  assert.equal(relevance("other.jpg", found), "other");
});

test("a search with no results marks every case as context", () => {
  assert.equal(relevance("photo.jpg", new Map()), "other");
});

test("Explore keeps unclear candidates selectable and distinct from red context", () => {
  const found = new Map([["zebra.jpg", 0], ["note.txt", 39]]);
  assert.equal(relevance("zebra.jpg", found, false), "closest");
  assert.equal(relevance("note.txt", found, false), "closest");
  assert.equal(relevance("other.jpg", found, false), "other");
  assert.equal(relevance("zebra.jpg", found, true), "match");
  assert.equal(relevance("zebra.jpg", null, false), "neutral");
  assert.equal(relevance("zebra.jpg", new Map(), false), "other");
  assert.equal(found.size, 2);
  assert.equal(found.get("zebra.jpg"), 0);
});

test("Search and Explore share independent preview-only blur uniforms", () => {
  const fog = new THREE.Color(), first = new THREE.MeshBasicMaterial(), second = new THREE.MeshBasicMaterial();
  const a = softenBackground(first, fog), b = softenBackground(second, fog);
  a.softness.value = 1;
  assert.equal(b.softness.value, 0);
  const shader = { uniforms: {}, fragmentShader: "#include <map_fragment>\n#include <opaque_fragment>" };
  first.onBeforeCompile(shader, null);
  assert.equal(shader.uniforms.rackSoftness, a.softness);
  assert.equal(shader.uniforms.rackFog.value, fog);
  assert.match(shader.fragmentShader, /if \(rackSoftness > 0.001\)/);
  assert.match(shader.fragmentShader, /else sampledDiffuseColor = texture2D\(map, vMapUv\)/);
  assert.equal(first.customProgramCacheKey(), second.customProgramCacheKey());
});

test('Explore can fade context further without altering Search fog or sharing uniforms', () => {
  const a = new THREE.MeshBasicMaterial(), b = new THREE.MeshBasicMaterial();
  softenBackground(a, new THREE.Color(), .45);
  softenBackground(b, new THREE.Color());
  const sa = {uniforms:{},fragmentShader:'#include <opaque_fragment>'};
  const sb = {uniforms:{},fragmentShader:'#include <opaque_fragment>'};
  a.onBeforeCompile(sa, null); b.onBeforeCompile(sb, null);
  assert.equal(sa.uniforms.rackFade.value, .45);
  assert.equal(sb.uniforms.rackFade.value, .18);
});
