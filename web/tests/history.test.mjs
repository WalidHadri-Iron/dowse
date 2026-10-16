import assert from "node:assert/strict";
import test from "node:test";
import { completion, openedFirst } from "../src/lib/history.ts";

const past = [
  { query: "electricity bill March", exact: false },
  { query: "Electric guitar lessons", exact: false },
  { query: "lease", exact: true },
];

test("typed words are completed with the newest earlier search they start", () => {
  assert.equal(completion("elec", past), "tricity bill March");
  assert.equal(completion("ELECTRIC G", past), "uitar lessons");   // case aside, what's typed is kept as typed
  assert.equal(completion("electricity bill ", past), "March");
  assert.equal(completion("electricity  bill", past), "");      // not how it was written: no rest to add
  assert.equal(completion("electricity bill March", past), "");   // nothing left to add
  assert.equal(completion("lease", past), "");                    // typed in full: nothing to suggest
  assert.equal(completion("", past), "");
  assert.equal(completion("   ", past), "");
  assert.equal(completion("rent", past), "");
});

test("files gone back to lately lead the collection, each once", () => {
  const opened = [{ path: "b" }, { path: "z" }];
  const collection = [{ path: "a" }, { path: "b" }, { path: "c" }];
  assert.deepEqual(openedFirst(opened, collection).map((f) => f.path), ["b", "z", "a", "c"]);
  assert.deepEqual(openedFirst([], collection).map((f) => f.path), ["a", "b", "c"]);
});
