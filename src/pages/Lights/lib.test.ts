// Tests for the Lights setup page's pure helpers. No test runner in this repo, so they run under plain node:
//   node --experimental-strip-types --test src/pages/Lights/lib.test.ts

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  changedBody,
  checkDraft,
  doingWords,
  draftBody,
  draftFromRow,
  emptyDraft,
  footprintWords,
  nextFreeChannel,
  overlapsWith,
  placementChoices,
  placementWords,
  problemWords,
  rigProblems,
  rowsFromRig,
  statusOf,
  swatchOf,
  typesFrom,
  wiringWords,
  type LightRow,
  type LightType,
} from "./lib.ts";

/** A rig view the way show_service's GET /api/v1/rig answers today (registry_view only) plus contract rows. */
const REGISTRY_VIEW = {
  problems: ["hue.ne is in both files: the hand file's wins"],
  outputs: {
    hue: { id: "hue", driver: "hue", node: null, port_address: null },
    "dmx-a": { id: "dmx-a", driver: "artnet", node: "node-10-0-0-5", port_address: 1 },
  },
  nodes: { "node-10-0-0-5": { last_ip: "10.0.0.5" } },
  types: {
    "sm-hue": { footprint: 0, kind: "lamp" },
    "generic-rgbw": { footprint: 4, kind: "lamp" },
  },
  fixtures: {
    "screen.north": { id: "screen.north", label: "North Frame", type: "sm-screen", kind: "screen", output: "screens", zone: "north" },
    "hue.ne": { id: "hue.ne", label: "Drawing room NE", type: "sm-hue", kind: "lamp", output: "hue", light: "abc", zone: "north", placement: "above" },
    "spot.1": { id: "spot.1", label: "Ball spot", type: "generic-rgbw", kind: "lamp", output: "dmx-a", address: 10, zone: "south", placement: "opposite" },
  },
};

function row(over: Partial<LightRow>): LightRow {
  return {
    id: "x",
    label: "X",
    kind: "lamp",
    type: "generic-rgbw",
    type_label: "RGBW light (4 channels)",
    output: "dmx-a",
    node_ip: "10.0.0.5",
    universe: 1,
    address: 1,
    footprint: 4,
    placement: null,
    role: null,
    zone: null,
    follow: null,
    online: null,
    last_rgb: null,
    hue: false,
    dmx: true,
    ...over,
  };
}

test("rows from the registry view: screens left out, gaps filled from outputs, nodes and types", () => {
  const rows = rowsFromRig({ enabled: true, registry_view: REGISTRY_VIEW });
  assert.deepEqual(rows.map((r) => r.id), ["hue.ne", "spot.1"]);
  const [lamp, spot] = rows;
  assert.equal(lamp.hue, true);
  assert.equal(lamp.dmx, false);
  assert.equal(spot.dmx, true);
  assert.equal(spot.node_ip, "10.0.0.5");
  assert.equal(spot.universe, 1);
  assert.equal(spot.footprint, 4);
  assert.equal(spot.online, null);
});

test("contract rows (a list) win over the registry view, and their values are read tolerantly", () => {
  const rows = rowsFromRig({
    registry_view: REGISTRY_VIEW,
    fixtures: [
      {
        id: "spot.1",
        label: "Ball spot",
        kind: "lamp",
        type: "generic-rgbw",
        type_label: "RGBW light (4 channels)",
        output: "dmx-a",
        node_ip: "10.0.0.9",
        universe: 2,
        address: 10,
        footprint: 4,
        follow: { hex: "#ff8000", bri: 140 },
        online: true,
        last_rgb: [300, 128, -4],
      },
      { id: "hue.ne", label: "NE", output: "hue", follow: "dmx", online: null, last_rgb: "#00ff00" },
      { label: "no id, dropped" },
    ],
  });
  assert.equal(rows.length, 2);
  const spot = rows[0];
  assert.equal(spot.node_ip, "10.0.0.9");
  assert.equal(spot.universe, 2);
  assert.deepEqual(spot.follow, { hex: "#ff8000", bri: 100 });
  assert.deepEqual(spot.last_rgb, [255, 128, 0]);
  assert.equal(rows[1].hue, true);
  assert.deepEqual(rows[1].last_rgb, [0, 255, 0]);
  assert.equal(rows[1].follow, "dmx");
});

test("contract rows as a map by id", () => {
  const rows = rowsFromRig({ fixtures: { "spot.2": { label: "Two", output: "dmx-b", address: 5, node_ip: "10.0.0.7" } } });
  assert.equal(rows[0].id, "spot.2");
  assert.equal(rows[0].dmx, true);
});

test("an unusable rig view gives no rows", () => {
  assert.deepEqual(rowsFromRig(null), []);
  assert.deepEqual(rowsFromRig({ enabled: false, why: "off" }), []);
});

test("problems in any shape, de-duplicated, with the registry's error first", () => {
  assert.deepEqual(problemWords(["a", { message: "b" }, { detail: "c", path: "x" }, { why: "d" }, 3, null]), ["a", "b", "c", "d"]);
  assert.deepEqual(problemWords("one"), ["one"]);
  const p = rigProblems({ registry: { error: "bad yaml", problems: ["x"] }, problems: ["x", "y"] });
  assert.deepEqual(p, ["bad yaml", "x", "y"]);
});

test("types: channelled only, generic ones first in the server's order, then the rest by name", () => {
  const types = typesFrom({
    types: [
      { id: "sm-hue", label: "Hue lamp", source: "builtin", footprint: 0 },
      { id: "ofl-zeta", label: "Zeta wash", source: "ofl", footprint: 7, channels: ["Dimmer"] },
      { id: "generic-rgbw", label: "RGBW light (4 channels)", source: "generic", footprint: 4, channels: ["R", "G", "B", "W"] },
      { id: "ofl-alpha", label: "Alpha spot", source: "ofl", footprint: 3 },
      { id: "generic-dimmer", label: "Dimmer (1 channel)", footprint: 1, channels: ["D"] },
    ],
  });
  assert.deepEqual(types.map((t) => t.id), ["generic-rgbw", "generic-dimmer", "ofl-alpha", "ofl-zeta"]);
  assert.deepEqual(typesFrom([{ id: "generic-rgb", channels: ["R", "G", "B"] }])[0].footprint, 3);
  assert.deepEqual(typesFrom("nonsense"), []);
});

test("placement words", () => {
  assert.equal(placementWords("above", "north"), "Above the north screen");
  assert.equal(placementWords("opposite", "south"), "Opposite the south screen");
  assert.equal(placementWords("on the mirror ball", null), "On the mirror ball");
  assert.equal(placementWords("ceiling", "north"), "Ceiling (north side)");
  assert.equal(placementWords(null, "south"), "South side");
  assert.equal(placementWords(null, null), null);
});

test("placement choices: each paired word on every side the rig has, free-text ones as they are", () => {
  const rows = [
    row({ id: "a", placement: "above", zone: "north" }),
    row({ id: "b", placement: "opposite", zone: "south" }),
    row({ id: "c", placement: "on the ball", zone: null }),
    row({ id: "d", placement: null, zone: null }),
  ];
  const words = placementChoices(rows).map((p) => p.words);
  assert.deepEqual(words, [
    "Above the north screen",
    "Opposite the north screen",
    "Above the south screen",
    "Opposite the south screen",
    "On the ball",
  ]);
});

test("status pill: not answering beats everything; following, off, online; nothing when unknown", () => {
  assert.equal(statusOf(row({ online: false, follow: "dmx" }))?.text, "Not answering");
  assert.equal(statusOf(row({ online: true, follow: "dmx" }))?.text, "Following the music");
  assert.equal(statusOf(row({ online: null, follow: "dmx" }))?.text, "Following the music");
  assert.equal(statusOf(row({ online: true, follow: "off" }))?.text, "Off");
  assert.equal(statusOf(row({ online: true, follow: { hex: "#ffffff", bri: 50 } }))?.text, "Online");
  assert.equal(statusOf(row({ online: null, follow: null })), null);
});

test("what it does, its swatch", () => {
  assert.equal(doingWords(row({ follow: "dmx" })), "Follows the music");
  assert.equal(doingWords(row({ follow: { hex: "#112233", bri: 42.4 } })), "A steady colour at 42%");
  assert.equal(doingWords(row({ follow: null })), null);
  assert.equal(swatchOf(row({ last_rgb: [255, 0, 16] })), "#ff0010");
  assert.equal(swatchOf(row({ last_rgb: [0, 0, 0] })), null);
  assert.equal(swatchOf(row({ last_rgb: null, follow: { hex: "#abcdef", bri: 10 } })), "#abcdef");
});

test("footprint and wiring words", () => {
  assert.equal(footprintWords(10, 4), "Uses channels 10–13");
  assert.equal(footprintWords(7, 1), "Uses channel 7");
  assert.equal(footprintWords(null, 4), null);
  assert.equal(wiringWords(row({ address: 10, footprint: 4 })), "Box 10.0.0.5 · universe 1 · channels 10–13");
  assert.equal(wiringWords(row({ dmx: false, hue: true })), null);
});

test("next free channel and overlaps are per box and universe", () => {
  const rows = [
    row({ id: "a", address: 1, footprint: 4 }),
    row({ id: "b", address: 10, footprint: 3 }),
    row({ id: "c", address: 100, footprint: 4, universe: 2 }),
    row({ id: "d", address: 200, footprint: 4, node_ip: "10.0.0.6" }),
  ];
  assert.equal(nextFreeChannel(rows, "10.0.0.5", 1), 13);
  assert.equal(nextFreeChannel(rows, " 10.0.0.5 ", 2), 104);
  assert.equal(nextFreeChannel(rows, "10.0.0.99", 1), 1);
  assert.equal(nextFreeChannel(rows, "10.0.0.5", 1, "b"), 5);
  assert.equal(nextFreeChannel(rows, "", 1), null);
  assert.equal(nextFreeChannel([row({ address: 509, footprint: 4 })], "10.0.0.5", 1), null);
  assert.deepEqual(overlapsWith(rows, "10.0.0.5", 1, 3, 8).map((o) => [o.id, o.from, o.to]), [
    ["a", 1, 4],
    ["b", 10, 12],
  ]);
  assert.deepEqual(overlapsWith(rows, "10.0.0.5", 1, 5, 5), []);
  assert.deepEqual(overlapsWith(rows, "10.0.0.5", 1, 1, 4, "a"), []);
});

const RGBW: LightType = { id: "generic-rgbw", label: "RGBW light (4 channels)", source: "generic", footprint: 4, channels: [], kind: "lamp" };

test("the form's checks, in words, by field", () => {
  const blank = checkDraft(emptyDraft(), null);
  assert.deepEqual(Object.keys(blank).sort(), ["address", "label", "node_ip", "type"]);
  const good = { type: "generic-rgbw", node_ip: "10.0.0.5", universe: "1", address: "509", label: "Spot", placement: null, zone: null };
  assert.deepEqual(checkDraft(good, RGBW), {});
  assert.match(checkDraft({ ...good, address: "510" }, RGBW).address ?? "", /start at 509 at the latest/);
  assert.ok(checkDraft({ ...good, address: "0" }, RGBW).address);
  assert.ok(checkDraft({ ...good, address: "513" }, RGBW).address);
  assert.ok(checkDraft({ ...good, universe: "0" }, RGBW).universe);
  assert.ok(checkDraft({ ...good, universe: "1.5" }, RGBW).universe);
  assert.ok(checkDraft({ ...good, node_ip: "10.0.0.256" }, RGBW).node_ip);
  assert.ok(checkDraft({ ...good, node_ip: "box.local" }, RGBW).node_ip);
  assert.ok(checkDraft({ ...good, label: "  " }, RGBW).label);
});

test("POST body: trimmed, numbers as numbers, placement only when set", () => {
  const d = { type: "generic-rgbw", node_ip: " 10.0.0.5 ", universe: "2", address: "10", label: " Spot ", placement: null, zone: null };
  assert.deepEqual(draftBody(d), { label: "Spot", type: "generic-rgbw", node_ip: "10.0.0.5", universe: 2, address: 10 });
  assert.deepEqual(draftBody({ ...d, placement: "above", zone: "north" }), {
    label: "Spot",
    type: "generic-rgbw",
    node_ip: "10.0.0.5",
    universe: 2,
    address: 10,
    placement: "above",
    zone: "north",
  });
});

test("PUT body: only what changed; cleared placement goes as null", () => {
  const r = row({ id: "s", label: "Spot", address: 10, placement: "above", zone: "north" });
  const d = draftFromRow(r);
  assert.deepEqual(changedBody(d, r), {});
  assert.deepEqual(changedBody({ ...d, address: "20", label: "Spot 2" }, r), { address: 20, label: "Spot 2" });
  assert.deepEqual(changedBody({ ...d, placement: null, zone: null }, r), { placement: null, zone: null });
  assert.deepEqual(changedBody({ ...d, node_ip: "10.0.0.6", universe: "3" }, r), { node_ip: "10.0.0.6", universe: 3 });
});
