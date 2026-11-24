// Tests for the Film mode's slots and links (film.ts). Plain node:
//   node --experimental-strip-types --test src/components/film/film.test.ts

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  addPicture, addSlot, emptyFilm, estimateSeconds, filmAspect, fromStored, placePictures, removeSlot, requestPictures,
  segments, setAspect, setLink, setPicture, slotLetter, toStored, type SlotPicture,
} from "./film.ts";

const url = (name: string): SlotPicture => ({ kind: "url", url: `/output/_recent/${name}.jpg` });
const file = (name: string): SlotPicture =>
  ({ kind: "file", file: { name } as unknown as File, preview: `blob:${name}` });

test("a new film has two slots and one link (owner, 23 Nov: start with two, add more)", () => {
  const film = emptyFilm();
  assert.equal(film.slots.length, 2);
  assert.equal(film.links.length, 1);
  assert.deepEqual(film.links.map((l) => l.seconds), [5]);
  assert.equal(estimateSeconds(film), 0);
});

test("each pair of filled neighbours is one video, in slot order", () => {
  let film = placePictures(emptyFilm(), 0, [url("before"), file("during"), url("after")]);
  assert.equal(film.slots.length, 3);
  film = setLink(setLink(film, 0, { text: " the ice melts ", seconds: 3 }), 1, { seconds: 8 });
  const segs = segments(film);
  assert.deepEqual(segs.map((s) => [s.index, s.text, s.seconds]), [[0, "the ice melts", 3], [1, "", 8]]);
  // made side by side, so the film takes as long as its longest video: 30 + 15 x 8 seconds
  assert.equal(estimateSeconds(film), 150);
  // A -> B: a link then a file; B -> C: a file then a link. The request keeps that order.
  assert.deepEqual(requestPictures(segs[0]).order, ["url", "file"]);
  assert.deepEqual(requestPictures(segs[1]).order, ["file", "url"]);
  assert.deepEqual(requestPictures(segs[1]).urls, ["/output/_recent/after.jpg"]);
});

test("an empty slot in the middle makes no video on either side of it", () => {
  const film = setPicture(setPicture(emptyFilm(3), 0, url("a")), 2, url("c"));
  assert.equal(segments(film).length, 0);
  assert.equal(estimateSeconds(film), 0);
});

test("seconds stay within what one pass draws", () => {
  const film = setLink(setLink(emptyFilm(3), 0, { seconds: 99 }), 1, { seconds: 0 });
  assert.deepEqual(film.links.map((l) => l.seconds), [10, 2]);
});

test("pictures dropped together fill the slots from where they land, adding slots as needed", () => {
  const film = placePictures(emptyFilm(3), 1, [url("b"), url("c"), url("d")]);
  assert.equal(film.slots.length, 4);
  assert.equal(film.links.length, 3);
  assert.deepEqual(film.slots.map((s) => !!s.picture), [false, true, true, true]);
});

test("a picture sent to the film goes in the first empty slot, then in a new one", () => {
  let film = addPicture(emptyFilm(2), url("a"));
  film = addPicture(film, url("b"));
  assert.deepEqual(film.slots.map((s) => !!s.picture), [true, true]);
  film = addPicture(film, url("c"));
  assert.equal(film.slots.length, 3);
  assert.equal(film.links.length, 2);
});

test("more videos than workers are made in turns", () => {
  let film = placePictures(emptyFilm(), 0, ["a", "b", "c", "d", "e"].map(url));   // four videos of 5 seconds
  assert.equal(segments(film).length, 4);
  assert.equal(estimateSeconds(film), 2 * (30 + 15 * 5));
  film = setLink(film, 3, { seconds: 10 });
  assert.equal(estimateSeconds(film), (30 + 15 * 5) + (30 + 15 * 10));
});

test("removing a slot takes one link with it, and two slots are the least", () => {
  let film = placePictures(emptyFilm(), 0, [url("a"), url("b"), url("c")]);
  film = setLink(setLink(film, 0, { text: "a to b" }), 1, { text: "b to c" });
  const middleGone = removeSlot(film, 1);
  assert.equal(middleGone.slots.length, 2);
  assert.deepEqual(middleGone.links.map((l) => l.text), ["a to b"]);
  const lastGone = removeSlot(film, 2);
  assert.deepEqual(lastGone.links.map((l) => l.text), ["a to b"]);
  const emptied = removeSlot(middleGone, 0);
  assert.equal(emptied.slots.length, 2);
  assert.equal(emptied.slots[0].picture, null);
});

test("the film's shape is its first picture's", () => {
  let film = placePictures(emptyFilm(3), 1, [url("b"), url("c")]);
  assert.equal(filmAspect(film), undefined);
  film = setAspect(film, film.slots[2].id, 0.75);
  film = setAspect(film, film.slots[1].id, 1.5);
  assert.equal(filmAspect(film), 1.5);
});

test("a reload keeps the links and the app's own pictures, not files from the device", () => {
  let film = placePictures(emptyFilm(), 0, [url("a"), file("b"), url("c")]);
  film = setLink(film, 0, { text: "a to b", seconds: 7 });
  const back = fromStored(JSON.parse(JSON.stringify(toStored(film))));
  assert.ok(back);
  assert.deepEqual(back.slots.map((s) => s.picture?.kind ?? null), ["url", null, "url"]);
  assert.deepEqual(back.links.map((l) => [l.text, l.seconds]), [["a to b", 7], ["", 5]]);
  assert.equal(fromStored({ slots: [{ url: null }], links: [] }), null);
  // empty slots at the end go, back to the two a film starts with (23 Nov: an empty three-slot film had been kept)
  const empty3 = fromStored(JSON.parse(JSON.stringify(toStored(emptyFilm(3)))));
  assert.equal(empty3?.slots.length, 2);
  assert.equal(empty3?.links.length, 1);
  const written = fromStored(JSON.parse(JSON.stringify(toStored(setLink(emptyFilm(4), 1, { text: "then" })))));
  assert.equal(written?.slots.length, 3);
  assert.equal(fromStored("nonsense"), null);
});

test("slots are lettered", () => {
  assert.deepEqual([0, 1, 25].map(slotLetter), ["A", "B", "Z"]);
  assert.equal(addSlot(emptyFilm()).slots.length, 3);
});
