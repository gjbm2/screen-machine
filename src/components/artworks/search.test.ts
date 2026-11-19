// Tests for the artworks search (search.ts). Plain node:
//   node --experimental-strip-types --test src/components/artworks/search.test.ts

import { test } from "node:test";
import assert from "node:assert/strict";
import { fold, indexLibrary, searchArtworks, suggest, type Artwork } from "./search.ts";

const art = (a: Partial<Artwork> & Pick<Artwork, "id" | "title" | "artist" | "year">): Artwork => ({
  movement: "Baroque", period: "Baroque", genre: "portrait", medium: "oil", people: "one", tags: [], aka: [],
  width: 100, height: 100, image: "", thumb: "", source: { article: "", file: "", license: null }, ...a,
});

const works = [
  art({ id: "pearl", title: "Girl with a Pearl Earring", artist: "Johannes Vermeer", year: 1665, movement: "Dutch Golden Age", tags: ["turban", "pearl"], aka: ["Mona Lisa of the North"] }),
  art({ id: "milkmaid", title: "The Milkmaid", artist: "Johannes Vermeer", year: 1658, genre: "interior", tags: ["kitchen", "milk"] }),
  art({ id: "mona", title: "Mona Lisa", artist: "Leonardo da Vinci", year: 1503, period: "Renaissance", movement: "High Renaissance", aka: ["La Gioconda"] }),
  art({ id: "hare", title: "Young Hare", artist: "Albrecht Dürer", year: 1502, period: "Renaissance", genre: "animals", people: "none", tags: ["rabbit"] }),
  art({ id: "wave", title: "The Great Wave off Kanagawa", artist: "Katsushika Hokusai", year: 1831, period: "Asian", movement: "Ukiyo-e", genre: "seascape", people: "group", tags: ["japanese", "wave", "fuji"] }),
];
const index = indexLibrary(works);
const ids = (q: string, f = {}) => searchArtworks(index, q, f).map((w) => w.id);

test("accents and case don't matter", () => {
  assert.equal(fold("Dürer"), "durer");
  assert.deepEqual(ids("durer"), ["hare"]);
  assert.deepEqual(ids("DÜRER"), ["hare"]);
});

test("an artist finds all their works", () => {
  assert.deepEqual(ids("vermeer").sort(), ["milkmaid", "pearl"]);
});

test("a title beats a nickname inside another title", () => {
  assert.deepEqual(ids("mona lisa"), ["mona", "pearl"]);
});

test("every word must match", () => {
  assert.deepEqual(ids("vermeer milk"), ["milkmaid"]);
  assert.deepEqual(ids("vermeer wave"), []);
});

test("tags, movement and era find works", () => {
  assert.deepEqual(ids("japanese"), ["wave"]);
  assert.deepEqual(ids("ukiyo"), ["wave"]);
  assert.deepEqual(ids("17th century").sort(), ["milkmaid", "pearl"]);
  assert.deepEqual(ids("1500s").sort(), ["hare", "mona"]);
});

test("filters: period, genre, people", () => {
  assert.deepEqual(ids("", { period: "Renaissance" }), ["hare", "mona"]);
  assert.deepEqual(ids("", { genre: "seascape" }), ["wave"]);
  assert.ok(!ids("", { people: true }).includes("hare"));
});

test("no words: everything, oldest first", () => {
  assert.deepEqual(ids(""), ["hare", "mona", "milkmaid", "pearl", "wave"]);
});

test("type-ahead: artists first, then titles, then tags", () => {
  const s = suggest(works, "ver");
  assert.equal(s[0].text, "Johannes Vermeer");
  assert.equal(s[0].kind, "artist");
  assert.deepEqual(suggest(works, "mi").map((x) => x.text), ["The Milkmaid", "milk"]);
  assert.deepEqual(suggest(works, "m"), []);
});
