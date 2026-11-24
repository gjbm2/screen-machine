// Tests for Start again's recipe (recipe.ts). Plain node:
//   node --experimental-strip-types --test src/components/recent/recipe.test.ts

import { test } from "node:test";
import assert from "node:assert/strict";
import { recipeOf, type Made } from "./recipe.ts";

const refs = (...names: string[]) => names.map((n, i) => ({ index: i + 1, stored_path: `${n}.ref.${i + 1}` }));

test("a still comes back with the words as written, its workflow, settings, helper and pictures in order", () => {
  const still: Made = {
    promptKey: "A lavish rewrite of the prompt by the helper",
    bucketId: "_recent",
    reference_images: [{ index: 2, stored_path: "x.ref.2" }, { index: 1, stored_path: "x.ref.1" }],
    metadata: {
      original_prompt: "make it snow",
      workflow: "qwen21-edit.json",
      original_params: { shape: "landscape", fill: "extend", publish_destination: "north-screen" },
      original_refiner: null,
    },
  };
  assert.deepEqual(recipeOf(still), {
    kind: "create",
    prompt: "make it snow",
    workflow: "qwen21-edit.json",
    params: { shape: "landscape", fill: "extend" },
    refiner: "auto",
    pictures: ["/output/_recent/x.ref.1", "/output/_recent/x.ref.2"],
  });
});

test("an older still without its words keeps the prompt it shows", () => {
  const r = recipeOf({ promptKey: "a fox", metadata: {} });
  assert.equal(r.kind, "create");
  assert.equal(r.kind === "create" && r.prompt, "a fox");
  assert.equal(r.kind === "create" && r.workflow, "auto");
});

const segment = (link: number | undefined, from: string, to: string, words: string, seconds: number, createdAt = "1"): Made => ({
  bucketId: "_recent",
  createdAt,
  reference_images: refs(from, to),
  metadata: {
    workflow: "h3-film.json",
    batch_id: "film1",
    batch_index: 0,                      // the server says 0 for every segment: not their order
    original_prompt: words,
    original_refiner: null,
    original_params: { video_seconds: seconds, keep_with_picture: "no", video_aspect: 1.78, sound: "no_music",
                       ...(link === undefined ? {} : { film_link: link }) },
  },
});

test("a film segment alone comes back as its two pictures and its link", () => {
  const r = recipeOf(segment(1, "b", "c", "the dog leaves", 3));
  assert.equal(r.kind, "film");
  if (r.kind !== "film") return;
  assert.deepEqual(r.pictures, ["/output/_recent/b.ref.1", "/output/_recent/c.ref.2"]);
  assert.deepEqual(r.links, [{ text: "the dog leaves", seconds: 3 }]);
  assert.deepEqual(r.params, { sound: "no_music" });
  assert.equal(r.refiner, "auto");
});

test("from its batch, the whole film comes back in link order", () => {
  const ab = segment(0, "a", "b", "the dog comes in", 4);
  const bc = segment(1, "b", "c", "the dog leaves", 3);
  const cd = segment(2, "c", "d", "the man sits", 6);
  const r = recipeOf(bc, [cd, bc, ab]);
  assert.equal(r.kind, "film");
  if (r.kind !== "film") return;
  assert.deepEqual(r.pictures.map((p) => p?.split("/").pop()), ["a.ref.1", "b.ref.2", "c.ref.2", "d.ref.2"]);
  assert.deepEqual(r.links.map((l) => [l.text, l.seconds]), [["the dog comes in", 4], ["the dog leaves", 3], ["the man sits", 6]]);
});

test("a link made again comes back as its newest take; a missing link leaves its words empty", () => {
  const first = segment(0, "a", "b", "first take", 4, "1");
  const again = segment(0, "a", "b", "second take", 6, "2");
  const third = segment(2, "c", "d", "the man sits", 6);
  const r = recipeOf(third, [first, again, third]);
  if (r.kind !== "film") return assert.fail("not a film");
  assert.deepEqual(r.links.map((l) => l.text), ["second take", "", "the man sits"]);
  assert.deepEqual(r.pictures.map((p) => p?.split("/").pop() ?? null), ["a.ref.1", "b.ref.2", "c.ref.1", "d.ref.2"]);
});

test("the joined film comes back as the whole film", () => {
  const ab = segment(0, "a", "b", "the dog comes in", 4);
  const bc = segment(1, "b", "c", "the dog leaves", 3);
  const joined: Made = { bucketId: "_recent", metadata: { workflow: "h3-film.json", joined_film: true, batch_id: "film1" } };
  const r = recipeOf(joined, [joined, ab, bc]);
  if (r.kind !== "film") return assert.fail("not a film");
  assert.deepEqual(r.links.map((l) => l.text), ["the dog comes in", "the dog leaves"]);
  assert.equal(r.pictures.length, 3);
  assert.deepEqual(r.params, { sound: "no_music" });
});

test("a segment made before segments said where they go stands alone, whatever its batch", () => {
  const old = segment(undefined, "a", "b", "the dog comes in", 4);
  const r = recipeOf(old, [old, segment(undefined, "b", "c", "x", 3)]);
  if (r.kind !== "film") return assert.fail("not a film");
  assert.equal(r.links.length, 1);
  assert.equal(r.links[0].text, "the dog comes in");
});
