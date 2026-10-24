// Tests for the Lights setup page's pure helpers. No test runner in this repo, so they run under plain node:
//   node --experimental-strip-types --test src/pages/Lights/lib.test.ts

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  emptySetup,
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
  identifyWhy,
  lightInModes,
  mapWhereWords,
  modeUseLines,
  roomOfLight,
  shortName,
  whereWords,
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
    caps: null,
    setup: emptySetup(),
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

// -- moving lights: Move it's controls, the sender, the Room map's plan, the words (docs/LIGHTS.md §8.4) --------------

import {
  clampToRoom,
  controlsFor,
  flowWords,
  fromPlan,
  FRONTS,
  holdWords,
  limitWords,
  lightsUiFrom,
  parseCaps,
  parseLive,
  parseSetup,
  parseSpace,
  placeablesFromRig,
  placeId,
  planFrame,
  provisionalName,
  snapToWall,
  toPlan,
  toSide,
  fromSide,
  turnWords,
  wallSide,
  type Vec3,
} from "./lib.ts";
import { JogSender, type JogClock } from "./jog.ts";

/** generic-mover-full's caps as show_service sends them (docs/LIGHTS.md §2.5). */
const MOVER_FULL = {
  attrs: ["intensity", "colour", "colour_slot", "colour_spin", "pan", "tilt", "pt_speed", "shutter", "gobo", "gobo_spin", "gobo_shake", "gobo_scroll", "prism", "zoom", "focus"],
  pan: { min: -270, max: 270, fine: true, limits: null },
  tilt: { min: -135, max: 135, fine: true, limits: null },
  pt_speed: true,
  zoom: { min: 10, max: 40, known: false },
  focus: true,
  iris: false,
  frost: false,
  colour: "wheel",
  colour_slots: Array.from({ length: 8 }, (_, i) => ({ slot: i + 1, name: i === 0 ? "Open" : `Colour ${i + 1}`, hex: i === 0 ? "#ffffff" : "#ff0000" })),
  gobo_slots: Array.from({ length: 8 }, (_, i) => ({ slot: i + 1, name: i === 0 ? "Open" : `Gobo ${i + 1}`, shake: i > 0, open: i === 0 })),
  gobo2_slots: [],
  prism_slots: [{ slot: 1, name: "Prism", facets: 3 }],
  spins: ["gobo_spin", "colour_spin"],
  cct: null,
  shutter: ["closed", "open", "strobe"],
  strobe_hz: [1, 20],
  macros: [],
  moves: true,
};
const RGB_PAR = { attrs: ["intensity", "colour"], pan: null, tilt: null, colour: "mix", colour_slots: [], gobo_slots: [], prism_slots: [], spins: [], shutter: [], macros: [], moves: false };
/** test_ofl's colour-wheel spot: a wheel, gobos, a strobe shutter with no closed range, a macro channel. */
const WHEEL_SPOT = {
  attrs: ["intensity", "colour", "colour_slot", "pan", "tilt", "gobo", "shutter", "macro"],
  pan: { min: -270, max: 270, fine: true },
  tilt: { min: -90, max: 90, fine: true },
  colour: "wheel",
  colour_slots: [
    { slot: 1, name: "White", hex: "#ffffff" },
    { slot: 2, name: "Red", hex: "#ff0000" },
  ],
  gobo_slots: [
    { slot: 1, name: "Open", open: true },
    { slot: 2, name: "Stars", shake: false },
  ],
  shutter: ["open", "strobe"],
  macros: [{ channel: "Effect", name: "Effect", choices: [{ word: "Auto 1", dmx: 10 }, { word: "Sound", dmx: 200 }] }],
};

const kinds = (caps: unknown) => controlsFor(parseCaps(caps)).map((c) => `${c.kind}:${c.attr}`);

test("controls come from the caps alone: the full generic mover", () => {
  assert.deepEqual(kinds(MOVER_FULL), [
    "slider:intensity",
    "slots:colour_slot",
    "spin:colour_spin",
    "slots:gobo",
    "spin:gobo_spin",
    "switch:gobo_shake",
    "switch:prism",
    "slider:zoom",
    "slider:focus",
    "switch:shutter",
  ]);
  const controls = controlsFor(parseCaps(MOVER_FULL));
  const zoom = controls.find((c) => c.attr === "zoom");
  assert.ok(zoom && zoom.kind === "slider" && zoom.min === 10 && zoom.max === 40 && zoom.label === "Beam size");
  const shutter = controls.find((c) => c.attr === "shutter");
  assert.ok(shutter && shutter.kind === "switch" && shutter.label === "Shutter open");
  assert.deepEqual(shutter.on, { mode: "open" });
  assert.equal(controls.find((c) => c.attr === "gobo")?.group, "Pattern");
  assert.equal(parseCaps(MOVER_FULL)?.moves, true);
});

test("controls: an RGB par is brightness and colour; the wheel spot gets slots, no shutter switch, and its macro", () => {
  assert.deepEqual(kinds(RGB_PAR), ["slider:intensity", "colour:colour"]);
  assert.deepEqual(kinds(WHEEL_SPOT), ["slider:intensity", "slots:colour_slot", "slots:gobo", "macro:macro"]);
  const macro = controlsFor(parseCaps(WHEEL_SPOT)).find((c) => c.kind === "macro");
  assert.ok(macro && macro.kind === "macro" && macro.group === "More");
  assert.deepEqual(macro.choices, ["Auto 1", "Sound"]);
  assert.equal(parseCaps(RGB_PAR)?.moves, false);
  assert.deepEqual(controlsFor(null), []);
  assert.equal(parseCaps("nope"), null);
});

/** A clock the test moves by hand. */
function fakeClock(): JogClock & { advance: (ms: number) => void } {
  let t = 0;
  let id = 0;
  let timers: { at: number; fn: () => void; id: number }[] = [];
  return {
    now: () => t,
    setTimeout: (fn, ms) => {
      id += 1;
      timers.push({ at: t + ms, fn, id });
      return id;
    },
    clearTimeout: (h) => {
      timers = timers.filter((x) => x.id !== h);
    },
    advance(ms: number) {
      const end = t + ms;
      for (;;) {
        const due = timers.filter((x) => x.at <= end).sort((a, b) => a.at - b.at)[0];
        if (!due) break;
        t = due.at;
        timers = timers.filter((x) => x !== due);
        due.fn();
      }
      t = end;
    },
  };
}

const flush = () => new Promise<void>((r) => setImmediate(r));

test("JogSender: latest wins, one in flight, at most hz a second", async () => {
  const clock = fakeClock();
  const sent: number[] = [];
  const replies: (() => void)[] = [];
  const s = new JogSender<number>(
    (v) => {
      sent.push(v);
      return new Promise<void>((r) => replies.push(r));
    },
    { hz: 10, clock },
  );
  s.push(1);
  assert.deepEqual(sent, [1]);
  s.push(2);
  s.push(3);
  assert.deepEqual(sent, [1], "one in flight");
  replies.shift()?.();
  await flush();
  assert.deepEqual(sent, [1], "the rate holds the next back");
  clock.advance(99);
  assert.deepEqual(sent, [1]);
  clock.advance(1);
  assert.deepEqual(sent, [1, 3], "the latest, 2 dropped");
  let idle = false;
  void s.whenIdle().then(() => (idle = true));
  await flush();
  assert.equal(idle, false);
  replies.shift()?.();
  await flush();
  assert.equal(idle, true);
  assert.equal(s.busy, false);
});

test("JogSender: a second of drags at 100 Hz sends at most hz, merging what waits", async () => {
  const clock = fakeClock();
  const sent: Record<string, number>[] = [];
  const s = new JogSender<Record<string, number>>(
    async (v) => {
      sent.push(v);
    },
    { hz: 20, clock, merge: (a, b) => ({ ...a, ...b }) },
  );
  for (let i = 0; i < 100; i++) {
    s.push(i % 2 ? { pan: i } : { tilt: i });
    await flush();
    clock.advance(10);
    await flush();
  }
  await flush();
  assert.ok(sent.length <= 21 && sent.length >= 15, `${sent.length} sends`);
  const last = sent[sent.length - 1];
  assert.ok("pan" in last || "tilt" in last);
  clock.advance(100);
  await flush();
  const final = sent[sent.length - 1];
  assert.equal(final.pan ?? 99, 99, "the very last pan arrives");
  s.stop();
  s.push({ pan: 1 });
  assert.equal(sent[sent.length - 1], final, "nothing after stop");
});

test("the Room map's plan: room <-> SVG round-trips on every front, front wall at the top", () => {
  const size: Vec3 = [6, 8, 3];
  for (const front of FRONTS) {
    const fr = planFrame(size, front, 360);
    assert.equal(wallSide(fr, front), "top", front);
    for (const [x, y] of [
      [0, 0],
      [6, 8],
      [1.5, 7.25],
      [3, 4],
    ]) {
      const [px, py] = toPlan(fr, x, y);
      const [bx, by] = fromPlan(fr, px, py);
      assert.ok(Math.abs(bx - x) < 1e-9 && Math.abs(by - y) < 1e-9, `${front} ${x},${y}`);
      assert.ok(px >= fr.pad - 1e-9 && px <= fr.w - fr.pad + 1e-9 && py >= fr.pad - 1e-9 && py <= fr.h - fr.pad + 1e-9);
    }
    const [, sy] = toSide(fr, 1, 1, 2.4);
    assert.ok(Math.abs(fromSide(fr, sy) - 2.4) < 1e-9);
  }
  const north = planFrame(size, "north", 360);
  assert.equal(toPlan(north, 3, 8)[1], north.pad, "the north wall is the top");
  assert.ok(toPlan(north, 6, 4)[0] > toPlan(north, 0, 4)[0], "facing north, east is on the right");
  const south = planFrame(size, "south", 360);
  assert.ok(toPlan(south, 0, 4)[0] > toPlan(south, 6, 4)[0], "facing south, west is on the right");
  assert.equal(Math.round(north.w), 360);
  assert.ok(Math.abs(north.h - (8 * north.s + 2 * north.pad)) < 1e-9);
  assert.deepEqual(clampToRoom(size, -1, 9), [0, 8]);
  assert.deepEqual(snapToWall(size, 3, 7.6), { x: 3, y: 8, wall: "north", normal: [0, -1, 0] });
  assert.deepEqual(snapToWall(size, 0.2, 4), { x: 0, y: 4, wall: "west", normal: [1, 0, 0] });
});

test("Move it's words: the hand-back countdown, the readout, limits, the flow", () => {
  assert.deepEqual(holdWords("holding", 40), { text: "The show leaves this light alone while this is open.", warn: false });
  assert.deepEqual(holdWords("holding", 12), { text: "Handing it back to the show in 12 s", warn: true });
  assert.deepEqual(holdWords("holding", 11.2), { text: "Handing it back to the show in 12 s", warn: true });
  assert.deepEqual(holdWords("released", null), { text: "Handed back to the show. Tap to hold it again.", warn: false });
  assert.equal(holdWords("cant", null, null).text, "This light can't be moved from here yet.");
  assert.equal(holdWords("cant", null, "Its output isn't running").text, "Its output isn't running");
  assert.equal(turnWords(45, -20), "Turned 45° · Tilted −20°");
  const setup = parseSetup({ limits: { pan: [40, -120] }, home: { pan: 1, tilt: 2 }, positions: { piano: { pan: 3, tilt: 4 }, bad: {} }, jog_flip: { tilt: true } });
  assert.equal(limitWords(setup, "pan"), "Turns from −120° to 40°");
  assert.equal(limitWords(setup, "tilt"), "Tilts anywhere (no limits)");
  assert.deepEqual(setup.positions, { piano: { pan: 3, tilt: 4 } });
  assert.deepEqual(setup.jog_flip, { pan: false, tilt: true });
  assert.equal(flowWords("up"), "Flowing upwards");
  assert.equal(flowWords("cw"), "Turning clockwise");
  assert.equal(flowWords("towards", "The piano"), "Flowing towards The piano");
  assert.equal(flowWords(null), "Still");
});

test("names: place ids as show_service makes them, and a new light's provisional name", () => {
  assert.equal(placeId("The piano"), "piano");
  assert.equal(placeId("Dance floor"), "dance-floor");
  assert.equal(placeId("mirror-ball"), "mirror-ball");
  assert.equal(placeId("A spot by the door!"), "spot-by-the-door");
  const t = { id: "generic-mover-full", label: "Moving head: pan, tilt (fine), speed (13 channels)", source: "builtin", footprint: 13, channels: [], kind: "lamp" };
  assert.equal(provisionalName(t, []), "Moving head 1");
  assert.equal(provisionalName(t, [row({ label: "Moving head 1" }), row({ id: "y", label: "moving head 2" })]), "Moving head 3");
  assert.equal(provisionalName(null, []), "Light 1");
});

test("the room, the live view and the rig's extras read tolerantly", () => {
  const sp = parseSpace({
    room: { size: [6, 8, 3], front: "east" },
    points: { piano: { label: "The piano", at: [3, 2, 0.8] }, broken: { at: [1] } },
    regions: { "dance-floor": { label: "Dance floor", surface: "floor", centre: [3, 5, 0], size: [2, 2] } },
    appliances: { "dmx.mover": { at: [2, 3, 2.9], mount: "ceiling", teach: [{ pan: 1, tilt: 2, point: "piano" }], pose: { at: [2, 3, 2.9], rms_deg: 0.4, samples: 3 } } },
    targets: [{ name: "piano", label: "The piano", kind: "point", at: [3, 2, 0.8] }],
  });
  assert.equal(sp.front, "east");
  assert.deepEqual(sp.points.map((p) => p.name), ["piano"]);
  assert.equal(sp.regions[0].size[0], 2);
  assert.equal(sp.appliances["dmx.mover"].teach.length, 1);
  assert.equal(sp.appliances["dmx.mover"].pose?.rms_deg, 0.4);
  assert.equal(parseSpace(null).size, null);
  const live = parseLive({ flow: { dir: "cw", at: null }, fixtures: { "dmx.mover": { at: [2, 3, 2.9], hit: [2, 3, 0], intensity: 1, hex: "#ffffff" } } });
  assert.equal(live.flow?.dir, "cw");
  assert.deepEqual(live.fixtures["dmx.mover"].hit, [2, 3, 0]);
  assert.deepEqual(lightsUiFrom({ lights_ui: { send_hz: 10, nudge_deg: { fine: 2 } } }), {
    send_hz: 10,
    idle_s: 60,
    nudge_deg: { fine: 2, coarse: 5 },
    drag_deg_per_px: { fine: 0.1, coarse: 0.5 },
  });
  const things = placeablesFromRig({
    fixtures: [
      { id: "screen.north", label: "North Frame", kind: "screen", zone: "north" },
      { id: "dmx.mover", label: "Mover", kind: "lamp", caps: MOVER_FULL },
      { id: "hue.ne", label: "NE", kind: "lamp", caps: null },
    ],
  });
  assert.deepEqual(things.map((t) => t.kind), ["screen", "mover", "lamp"]);
  const rows = rowsFromRig({ fixtures: [{ id: "dmx.mover", label: "Mover", output: "dmx-a", address: 1, caps: MOVER_FULL, setup: { home: { pan: 5, tilt: 6 } } }] });
  assert.equal(rows[0].caps?.moves, true);
  assert.deepEqual(rows[0].setup.home, { pan: 5, tilt: 6 });
});

// -- the room around a light (B10) ---------------------------------------------------------------------------------------

test("shortName drops the containing room's name, never everything", () => {
  assert.equal(shortName("Drawing room NE", "Drawing room"), "NE");
  assert.equal(shortName("drawing room: Par 1", "Drawing room"), "Par 1");
  assert.equal(shortName("Drawing room", "Drawing room"), "Drawing room");
  assert.equal(shortName("Drawing roomette", "Drawing room"), "Drawing roomette");
  assert.equal(shortName("Spot", null), "Spot");
});

test("roomOfLight: Hue lamps by id, DMX lights by 'light' or 'light.<id>'", () => {
  const rooms = [
    { id: "lab", name: "Lab", lights: ["light.par1"] },
    { id: "dr", name: "Drawing room", lights: ["hue.ne", "light"] },
  ];
  assert.equal(roomOfLight(rooms, "hue.ne", false)?.id, "dr");
  assert.equal(roomOfLight(rooms, "par1", true)?.id, "lab");
  assert.equal(roomOfLight(rooms, "par2", true)?.id, "dr");
  assert.equal(roomOfLight(rooms, "hue.sw", false), null);
});

test("mapWhereWords reads the drawing room as placed on the map", () => {
  const size: [number, number, number] = [5, 10, 5];
  const screens = [
    { name: "North screen", at: [0, 7.17, 2.11] as [number, number, number] },
    { name: "South screen", at: [0, 3.19, 2.03] as [number, number, number] },
  ];
  assert.equal(mapWhereWords([0.77, 7.96, 3.5], size, screens), "Above the north screen");
  assert.equal(mapWhereWords([5, 7.9, 3.53], size, screens), "Opposite the north screen");
  assert.equal(mapWhereWords([4.74, 2.55, 3.58], size, screens), "Opposite the south screen");
  assert.equal(mapWhereWords([0, 2.51, 3.89], size, screens), "Above the south screen");
  assert.equal(mapWhereWords([4.5, 9.5, 3], size, []), "In the north-east corner");
  assert.equal(mapWhereWords([2.5, 9.5, 3], size, []), "On the north wall");
  assert.equal(mapWhereWords([2.5, 5, 3], size, screens), "Near the middle of the room");
  assert.equal(mapWhereWords([2.5, 5, 3], null, screens), null);
});

test("whereWords falls back to the placement words off the map", () => {
  assert.equal(whereWords("hue.ne", null, [], "above", "north"), "Above the north screen");
});

test("identifyWhy never shows a file or config path", () => {
  assert.equal(identifyWhy("outputs.hue.enabled is false in config/room/fixtures.yaml"), "This light can't flash right now.");
  assert.equal(identifyWhy(null), "This light can't flash right now.");
  assert.equal(identifyWhy("bridge busy"), "This light can't flash right now: bridge busy");
});

test("lightInModes: own entry first, else its kind's, in mode order", () => {
  const modes = {
    party: { label: "Party", order: 2, entries: { "hue.ne": { value: "dmx", words: "Follows the music", selector: "hue" } } },
    gallery: { label: "Normal", order: 0, entries: { "screens.n": { value: "art" } } },
    off: { label: "Off", order: 1, entries: { "light.x": { value: "off", words: "Off", selector: "light" } } },
    base: { abstract: true, entries: { "hue.ne": { value: "off" } } },
  };
  assert.deepEqual(lightInModes(modes, "hue.ne", []).map((u) => u.label), ["Party"]);
  const fresh = lightInModes(modes, "light.new", ["light"]);
  assert.deepEqual(fresh.map((u) => [u.label, u.words, u.follows]), [["Off", "Off", false]]);
  assert.deepEqual(modeUseLines(lightInModes(modes, "hue.ne", [])), [["Follows the music", ["Party"]]]);
});
