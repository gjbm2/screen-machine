// Tests for the Room page's sections and the lights' words (pure helpers). Plain node, as Lights/lib.test.ts:
//   node --experimental-strip-types --test src/pages/Room/sections.test.ts

import { test } from "node:test";
import assert from "node:assert/strict";
import { buildSections, liveAllowed, siteHasSonos } from "./sections.ts";
import { dmxStates, lampsWord, rigLightIds } from "./lightStates.ts";
import type { FixturesView, RoomStatus } from "./types.ts";

const light = (members: Record<string, unknown>) => ({
  kind: "light",
  believed: Object.keys(members).length === 1 ? Object.values(members)[0] : members,
  members: Object.fromEntries(Object.entries(members).map(([id, v]) => [id, { believed: v }])),
});

function status(rooms: unknown[], screens: string[], dmx?: Record<string, unknown>, extra: Record<string, unknown> = {}): RoomStatus {
  return {
    rooms,
    screens: Object.fromEntries(screens.map((id) => [id, { name: id }])),
    subsystems: { ...(dmx ? { light: light(dmx) } : {}), ...extra },
  } as unknown as RoomStatus;
}

const keys = (secs: { key: string }[]) => secs.map((s) => s.key);

test("finsbury's shape is unchanged: drawing-room says 'light' and its Hue lamps, Lab has a screen", () => {
  const st = status(
    [
      { id: "drawing-room", name: "Drawing room", screens: ["north-screen", "south-screen"], lights: ["hue.ne", "hue.nw", "light"] },
      { id: "lab", name: "Lab", screens: ["devtest"] },
    ],
    ["north-screen", "south-screen", "devtest"],
    { par1: "dmx", par2: "off" },
  );
  const secs = buildSections(st, ["hue.ne", "hue.nw"]);
  assert.deepEqual(keys(secs), ["drawing-room", "lab"]);
  assert.deepEqual(secs[0].lights, ["hue.ne", "hue.nw", "light.par1", "light.par2"]);
  assert.equal(secs[1].lights, null);
});

test("F1: on a site of one room, a DMX light no room lists is that room's (the laptop's studio)", () => {
  const st = status([{ id: "studio", name: "Studio", screens: ["vj"] }], ["vj"], { par1: "dmx", par2: "off" });
  const secs = buildSections(st, []);
  assert.deepEqual(keys(secs), ["studio"]);
  assert.deepEqual(secs[0].lights, ["light.par1", "light.par2"]);
});

test("F1: with several rooms, a DMX light no room lists lands in Other", () => {
  const st = status(
    [
      { id: "studio", name: "Studio", screens: ["vj"] },
      { id: "garden", name: "Garden", screens: [], lights: ["light.par2"] },
    ],
    ["vj"],
    { par1: "dmx", par2: "off" },
  );
  const secs = buildSections(st, []);
  assert.deepEqual(keys(secs), ["studio", "garden", "_other"]);
  assert.deepEqual(secs[1].lights, ["light.par2"]);
  assert.deepEqual(secs[2].lights, ["light.par1"]);
});

test("F2 / E1: a room of DMX lights only (no screens, no Hue) is kept", () => {
  const st = status(
    [
      { id: "studio", name: "Studio", screens: ["vj"] },
      { id: "garden", name: "Garden", screens: [], lights: ["light"] },
    ],
    ["vj"],
    { par1: "dmx" },
  );
  const secs = buildSections(st, []);
  assert.ok(keys(secs).includes("garden"));
  assert.deepEqual(secs.find((s) => s.key === "garden")?.lights, ["light.par1"]);
  // and with lights by id
  const st2 = status(
    [
      { id: "studio", name: "Studio", screens: ["vj"] },
      { id: "garden", name: "Garden", screens: [], lights: ["light.par1"] },
    ],
    ["vj"],
    { par1: "dmx", par2: "off" },
  );
  assert.ok(keys(buildSections(st2, [])).includes("garden"));
});

test("F8: one DMX light never shows in two rooms ('light' and 'light.<id>', and two rooms saying 'light')", () => {
  const st = status(
    [
      { id: "a", name: "A", screens: ["s1"], lights: ["light"] },
      { id: "b", name: "B", screens: ["s2"], lights: ["light.par1"] },
      { id: "c", name: "C", screens: ["s3"], lights: ["light"] },
    ],
    ["s1", "s2", "s3"],
    { par1: "dmx", par2: "off" },
  );
  const secs = buildSections(st, []);
  assert.deepEqual(secs.find((s) => s.key === "a")?.lights, ["light.par2"]);
  assert.deepEqual(secs.find((s) => s.key === "b")?.lights, ["light.par1"]);
  assert.equal(secs.find((s) => s.key === "c")?.lights, null);
});

test("without a light subsystem in the status, rooms.json's DMX entries pass through as they are", () => {
  const st = status([{ id: "dr", name: "DR", screens: ["n"], lights: ["hue.ne", "light"] }], ["n"]);
  assert.deepEqual(buildSections(st, ["hue.ne"])[0].lights, ["hue.ne", "light"]);
});

test("F5: an art-only screen a room lists gets that room's art card; one in no room is left out", () => {
  const st = status([{ id: "studio", name: "Studio", screens: ["vj", "art"] }], ["vj"]);
  const art = [
    { id: "art", room: "studio" },
    { id: "the-beast", room: null },
  ];
  const secs = buildSections(st, [], art);
  assert.deepEqual(secs[0].screens, ["vj"]);
  assert.deepEqual(secs[0].artScreens, ["art"]);
  assert.ok(!secs.some((s) => s.artScreens.includes("the-beast")));
  // a room of only an art-only screen is kept (so its Today's art card stays)
  const st2 = status(
    [
      { id: "studio", name: "Studio", screens: ["vj"] },
      { id: "hall", name: "Hall", screens: ["art"] },
    ],
    ["vj"],
  );
  assert.deepEqual(keys(buildSections(st2, [], [{ id: "art", room: "hall" }])), ["studio", "hall"]);
});

test("E8: a live screen in no room is in Other", () => {
  const st = status([{ id: "studio", name: "Studio", screens: ["vj"] }], ["vj", "loose"]);
  const secs = buildSections(st, []);
  assert.deepEqual(secs.find((s) => s.key === "_other")?.screens, ["loose"]);
});

test("F14: liveAllowed counts a screen whose live is switched off as not able to go live", () => {
  assert.equal(liveAllowed({ policy: { live_enabled: false } }), false);
  assert.equal(liveAllowed({ live_enabled: false }), false);
  assert.equal(liveAllowed({ policy: { live_enabled: true } }), true);
  assert.equal(liveAllowed({}), true);
});

test("dmxStates reads a subsystem of ONE light (room_service gives its believed value bare)", () => {
  const st = status([], [], { par1: { hex: "#ffffff", bri: 50 } });
  assert.deepEqual(dmxStates(st), ["on"]);
  assert.deepEqual(rigLightIds(st), ["par1"]);
  assert.equal(rigLightIds(status([], [])), null);
});

const view = (lamps: Array<{ status?: string; on?: boolean; control?: string }>): FixturesView =>
  ({
    enabled: true,
    fixtures: lamps.map((l, i) => ({ id: `hue.${i}`, kind: "hue", status: l.status, control: l.control, light: { on: !!l.on } })),
  }) as unknown as FixturesView;

test("F4: the top line's lights word counts DMX lights as well as Hue lamps", () => {
  // DMX only (no Hue): it used to say nothing
  assert.equal(lampsWord(null, status([], [], { par1: "off" })), "lights off");
  assert.equal(lampsWord(null, status([], [], { par1: { hex: "#ff0000", bri: 40 }, par2: "off" })), "lights on");
  assert.equal(lampsWord(null, status([], [], { par1: "dmx", par2: "off" })), "lights follow the music");
  // Hue off and a DMX light on: on
  assert.equal(lampsWord(view([{ status: "off" }]), status([], [], { par1: { hex: "#ff0000", bri: 40 }, par2: "off" })), "lights on");
  // as before for Hue alone
  assert.equal(lampsWord(view([{ status: "off" }]), status([], [])), "lights off");
  assert.equal(lampsWord(view([{ status: "house" }]), status([], [])), "lights on");
  assert.equal(lampsWord(null, status([], [])), null);
});

test("siteHasSonos: room_service's memberless sonos placeholder is no Sonos; finsbury's zones are", () => {
  assert.equal(siteHasSonos({ sonos: { members: {} } }), false, "laptop: the placeholder has no members");
  assert.equal(siteHasSonos({ sonos: { members: { "living-room": {} } } }, true), true, "finsbury");
  assert.equal(siteHasSonos({ sonos: { members: { x: {} } } }, false), false, "the capability off wins");
  assert.equal(siteHasSonos({}), false);
  assert.equal(siteHasSonos(undefined), true, "not known yet: the words stay as they were");
});
