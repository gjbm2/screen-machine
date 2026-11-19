// Tests for the drop target under the pointer (pickDrop.ts). Plain node:
//   node --experimental-strip-types --test src/dnd/pickDrop.test.ts
// The page itself is checked by tools/ui_evals/dnd_zones.py.

import { test } from "node:test";
import assert from "node:assert/strict";
import { pickDrop, type NodeLike, type Zone } from "./pickDrop.ts";

/** A DOM-ish tree: n("a", n("b")) — contains() is ancestry, as in the DOM (a node contains itself). */
class N implements NodeLike {
  parent: N | null = null;
  name: string;
  constructor(name: string, kids: N[] = []) {
    this.name = name;
    for (const k of kids) k.parent = this;
  }
  contains(other: NodeLike | null): boolean {
    for (let o = other as N | null; o; o = o.parent) if (o === this) return true;
    return false;
  }
}
const n = (name: string, ...kids: N[]) => new N(name, kids);
const zone = (id: string, node: N, rect: Zone["rect"] = null, sortable = false): Zone =>
  ({ id, node, sortable, rect, list: sortable ? node.parent : null });
const r = (left: number, top: number, width: number, height: number) => ({ left, top, width, height });

// the page: prompt box; tab bar with two tabs (one with an icon inside); the strip's picture; a dated section of two
// pictures and a favourites section of two sortable pictures
const promptText = n("textarea");
const prompt = n("prompt", promptText);
const tabIcon = n("tab-north-icon");
const tabNorth = n("tab-north", tabIcon);
const tabSouth = n("tab-south");
const tabBar = n("tabbar", tabNorth, tabSouth);
const stripPic = n("strip-pic");
const d1img = n("d1-img");
const d1 = n("d1", d1img);
const d2 = n("d2");
const datedGrid = n("dated-grid", d1, d2);
const dated = n("dated", datedGrid);
const f1 = n("f1");
const f2 = n("f2");
const favGrid = n("fav-grid", f1, f2);
const favs = n("favs", favGrid);
const appBar = n("app-bar");
n("page", appBar, prompt, tabBar, stripPic, dated, favs);

const zones: Zone[] = [
  zone("drop-prompt", prompt),
  zone("drop-tab-north", tabNorth),
  zone("drop-tab-south", tabSouth),
  zone("drop-published", stripPic),
  zone("drop-section-dated", dated),
  zone("d1", d1),
  zone("d2", d2),
  zone("drop-section-favs", favs),
  zone("f1", f1, r(0, 600, 100, 60), true),
  zone("f2", f2, r(110, 600, 100, 60), true),
];
const P = { x: 0, y: 0 }; // the point only matters for sortable slots

test("whatever is drawn under the pointer is the target, at its edges too", () => {
  assert.equal(pickDrop(P, promptText, zones, "d2")[0], "drop-prompt");
  assert.equal(pickDrop(P, tabIcon, zones, "d2")[0], "drop-tab-north");
  assert.equal(pickDrop(P, tabSouth, zones, "d2")[0], "drop-tab-south");
  assert.equal(pickDrop(P, stripPic, zones, "d2")[0], "drop-published");
});

test("innermost first: a dated picture before its section", () => {
  assert.deepEqual(pickDrop(P, d1img, zones, "d2"), ["d1", "drop-section-dated"]);
});

test("the gap between pictures is the section", () => {
  assert.deepEqual(pickDrop(P, datedGrid, zones, "d1"), ["drop-section-dated"]);
});

test("over nothing droppable: no target (a drop there does nothing)", () => {
  assert.deepEqual(pickDrop(P, n("footer"), zones, "d1"), []);
  assert.deepEqual(pickDrop(P, null, zones, "d1"), []);
});

test("the dragged card is never its own target", () => {
  assert.deepEqual(pickDrop(P, d1img, zones, "d1"), ["drop-section-dated"]);
});

test("sortable favourites match their measured slot, not the card slid into it", () => {
  // f2 has slid left into f1's slot while f1 is dragged... the pointer in f2's measured slot is f2
  assert.equal(pickDrop({ x: 150, y: 620 }, f1, zones, "d1")[0], "f2");
  // ...and in the empty slot the dragged card left, the slot is still its own (f1), not the section
  assert.equal(pickDrop({ x: 50, y: 620 }, favGrid, zones, "d2")[0], "f1");
});

test("a pinned tab drawn over the favourites wins over the slot beneath it", () => {
  assert.equal(pickDrop({ x: 50, y: 620 }, tabIcon, zones, "d2")[0], "drop-tab-north");
});

test("the app's top bar drawn over the favourites: no target, not the slot beneath it", () => {
  assert.deepEqual(pickDrop({ x: 50, y: 620 }, appBar, zones, "d2"), []);
});

test("sortable batches with no zone around their list still reorder (the Generated tab's batches)", () => {
  const b1 = n("b1"), b2 = n("b2"), list = n("batch-list", b1, b2);
  n("generated", list);
  const bz = [zone("b1", b1, r(0, 0, 300, 100), true), zone("b2", b2, r(0, 110, 300, 100), true)];
  // b1 dragged; b2 has slid up into b1's place, the pointer over it there: the target is b1's slot
  assert.deepEqual(pickDrop({ x: 50, y: 50 }, b2, bz, "x"), ["b1", "b2"]);
  // the gap between them belongs to neither
  assert.deepEqual(pickDrop({ x: 50, y: 105 }, list, bz, "b1"), []);
});
