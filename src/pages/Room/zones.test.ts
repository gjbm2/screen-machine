// Tests for mode zones on the Room page and in Settings (zones.ts, RoomApi.forRoom, the sections' own_modes / show).
// Plain node, as sections.test.ts:
//   node --experimental-strip-types --test src/pages/Room/zones.test.ts
// Without zones (every deployment until a room sets "own_modes") the page must be as before: no caption, no line, no
// extra keys on the sections, and no "room" in any request.

import { test } from "node:test";
import assert from "node:assert/strict";
import { RoomApi } from "./api.ts";
import { buildSections } from "./sections.ts";
import {
  patchZoneStatus,
  showRoomsLine,
  siteModesCaption,
  trackFromEvent,
  trackFromStatus,
  zoneOf,
  zonesOf,
} from "./zones.ts";
import type { RoomStatus, TrackedTransition } from "./types.ts";

const FINSBURY_ROOMS = [
  { id: "drawing-room", name: "Drawing room", screens: ["north-screen", "south-screen"], lights: ["hue.ne"] },
  { id: "lab", name: "Lab", screens: ["devtest"] },
];
const LAPTOP_ROOMS = [{ id: "studio", name: "Studio", screens: ["vj"] }];

function status(rooms: unknown[], screens: string[], extra: Record<string, unknown> = {}): RoomStatus {
  return {
    mode: { current: "normal", target: null },
    rooms,
    screens: Object.fromEntries(screens.map((id) => [id, { name: id }])),
    subsystems: {},
    ...extra,
  } as unknown as RoomStatus;
}

const zone = (room: string, name: string, mode: Record<string, unknown> = { current: "gallery" }) => ({
  room,
  name,
  screens: [] as string[],
  mode,
  config: { revision: "r1", error: null, warnings: [] },
});

test("without zones: no zones, no caption, no show line (finsbury and the laptop as they ship)", () => {
  for (const st of [status(FINSBURY_ROOMS, ["north-screen", "south-screen", "devtest"]), status(LAPTOP_ROOMS, ["vj"])]) {
    assert.deepEqual(zonesOf(st), []);
    assert.equal(zoneOf(st, "lab"), undefined);
    assert.equal(siteModesCaption(st), null);
    assert.equal(showRoomsLine(st), null);
  }
  assert.deepEqual(zonesOf(null), []);
});

test("without zones the sections carry no new keys (the tree is as before)", () => {
  const secs = buildSections(status(FINSBURY_ROOMS, ["north-screen", "south-screen", "devtest"]), ["hue.ne"]);
  for (const s of secs) {
    assert.ok(!("ownModes" in s), s.key);
    assert.ok(!("show" in s), s.key);
  }
  const lap = buildSections(status(LAPTOP_ROOMS, ["vj"]), []);
  assert.deepEqual(lap, [{ key: "studio", title: "Studio", screens: ["vj"], artScreens: [], lights: null, effects: [] }]);
});

test("with the Lab as a zone: the site's caption names the drawing room, its section is marked, the show plays in both", () => {
  const rooms = [FINSBURY_ROOMS[0], { ...FINSBURY_ROOMS[1], own_modes: true }];
  const st = status(rooms, ["north-screen", "south-screen", "devtest"], { zones: [zone("lab", "Lab")] });
  assert.equal(siteModesCaption(st), "Drawing room");
  assert.equal(showRoomsLine(st), "The show plays in Drawing room and Lab");
  const secs = buildSections(st, ["hue.ne"]);
  assert.equal(secs.find((s) => s.key === "lab")?.ownModes, true);
  assert.ok(!("ownModes" in (secs.find((s) => s.key === "drawing-room") ?? {})));
  assert.equal(zoneOf(st, "lab")?.name, "Lab");
  assert.equal(zoneOf(st, "drawing-room"), undefined);
});

test("a room outside the show: its section says so, and Look names only the rooms in the show", () => {
  const rooms = [
    { id: "front", name: "Front", screens: ["fs"] },
    { id: "back", name: "Back", screens: ["bs"], own_modes: true, show: false },
  ];
  const st = status(rooms, ["fs", "bs"], { zones: [zone("back", "Back")] });
  assert.equal(siteModesCaption(st), "Front");
  assert.equal(showRoomsLine(st), "The show plays in Front");
  assert.equal(buildSections(st, []).find((s) => s.key === "back")?.show, false);
  // show false without any zone still gets the Look line
  const st2 = status([rooms[0], { id: "back", name: "Back", screens: ["bs"], show: false }], ["fs", "bs"]);
  assert.equal(siteModesCaption(st2), null);
  assert.equal(showRoomsLine(st2), "The show plays in Front");
});

test("trackFromStatus: running, finished (with its age), dropped", () => {
  const run = trackFromStatus(null, { transition: { id: "t1", from: "a", to: "b" } }, 1000);
  assert.equal(run?.phase, "running");
  const done = trackFromStatus(run, { transition: null, last_transition: { id: "t1", from: "a", to: "b", finished_age_s: 2 } as never }, 10_000);
  assert.deepEqual([done?.phase, done?.finishedAt], ["done", 8_000]);
  const dropped = trackFromStatus({ id: "t2", phase: "running" } as TrackedTransition, { transition: null }, 5);
  assert.deepEqual([dropped?.phase, dropped?.finishedAt], ["done", 5]);
  assert.equal(trackFromStatus(null, undefined), null);
});

test("trackFromEvent: started, a step, done", () => {
  let t = trackFromEvent(null, { id: "t1", phase: "started", from: "gallery", to: "live-test", source: "web:mode-tile" }, 1);
  assert.deepEqual([t?.id, t?.phase, t?.to, t?.source], ["t1", "running", "live-test", "web:mode-tile"]);
  t = trackFromEvent(t, { id: "t1", phase: "step", step: { id: "s1", subsystem: "screens.devtest", action: "set", status: "running" } }, 2);
  assert.equal(t?.steps?.length, 1);
  t = trackFromEvent(t, { id: "t1", phase: "step", step: { id: "s1", subsystem: "screens.devtest", action: "set", status: "ok" } }, 3);
  assert.deepEqual(t?.steps?.map((s) => s.status), ["ok"]);
  t = trackFromEvent(t, { id: "t1", phase: "done", status: "ok" }, 4);
  assert.deepEqual([t?.phase, t?.finishedAt, t?.status], ["done", 4, "ok"]);
  // another transition's event leaves it alone
  assert.equal(trackFromEvent(t, { id: "t9", phase: "step" }), t);
});

test("patchZoneStatus patches only that zone's running transition, never the site's", () => {
  const st = status(FINSBURY_ROOMS, [], {
    mode: { current: "normal", transition: { id: "site1", steps: [] } },
    zones: [zone("lab", "Lab", { current: "gallery", transition: { id: "z1", steps: [] } })],
  });
  const step = { id: "s1", subsystem: "screens.devtest", action: "set", status: "running" };
  const next = patchZoneStatus(st, { room: "lab", id: "z1", phase: "step", step });
  assert.notEqual(next, st);
  assert.deepEqual(next?.zones?.[0].mode.transition?.steps, [step]);
  assert.equal(next?.mode, st.mode, "the site's mode is untouched");
  assert.equal(patchZoneStatus(st, { room: "lab", id: "other", step }), st);
  assert.equal(patchZoneStatus(st, { room: "nope", id: "z1", step }), st);
  const plain = status(FINSBURY_ROOMS, []);
  assert.equal(patchZoneStatus(plain, { room: "lab", id: "z1", step }), plain);
});

test("RoomApi: the site's modes calls are as before; a zone's name its room", async () => {
  const calls: { url: string; method: string; body: unknown }[] = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = (async (url: string, init: RequestInit) => {
    calls.push({ url, method: init.method ?? "GET", body: init.body ? JSON.parse(String(init.body)) : undefined });
    return new Response(JSON.stringify({ modes: {}, config: {}, revision: "r", targets: {} }), { status: 200 });
  }) as typeof fetch;
  try {
    const site = new RoomApi("http://rs", "");
    assert.equal(site.forRoom(null), site);
    assert.equal(site.forRoom(undefined), site);
    const lab = site.forRoom("lab");
    assert.equal(site.forRoom("lab"), lab, "one per room");
    assert.equal(lab.room, "lab");

    await site.modes();
    await site.setMode("normal", "mode-tile");
    await site.roomConfig();
    await site.saveRoomConfig({}, "r", "n");
    await site.describe();
    assert.deepEqual(
      calls.map((c) => `${c.method} ${c.url}`),
      [
        "GET http://rs/api/v1/modes",
        "PUT http://rs/api/v1/mode",
        "GET http://rs/api/v1/room/config",
        "PUT http://rs/api/v1/room/config",
        "GET http://rs/api/v1/room/describe",
      ],
    );
    assert.deepEqual(calls[1].body, { mode: "normal", source: "web:mode-tile" }, "no room key for the site");
    assert.equal(site.validateConfigUrl(), "http://rs/api/v1/room/config/validate");

    calls.length = 0;
    await lab.modes();
    await lab.setMode("live-test", "mode-tile");
    await lab.roomConfig();
    await lab.saveRoomConfig({}, "r", "n");
    await lab.describe();
    await lab.status();
    assert.deepEqual(
      calls.map((c) => `${c.method} ${c.url}`),
      [
        "GET http://rs/api/v1/modes?room=lab",
        "PUT http://rs/api/v1/mode",
        "GET http://rs/api/v1/room/config?room=lab",
        "PUT http://rs/api/v1/room/config?room=lab",
        "GET http://rs/api/v1/room/describe?room=lab",
        "GET http://rs/api/v1/status",
      ],
    );
    assert.deepEqual(calls[1].body, { mode: "live-test", source: "web:mode-tile", room: "lab" });
    assert.equal(lab.validateConfigUrl(), "http://rs/api/v1/room/config/validate?room=lab");
    // a zone's modes from the site's api (the connection's per-zone read)
    calls.length = 0;
    await site.modes(undefined, "lab");
    assert.equal(calls[0].url, "http://rs/api/v1/modes?room=lab");
  } finally {
    globalThis.fetch = realFetch;
  }
});
