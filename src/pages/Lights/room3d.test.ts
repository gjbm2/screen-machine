// Tests for the 3D view's geometry (room3d.ts). Plain node, like lib.test.ts:
//   node --experimental-strip-types --test src/pages/Lights/room3d.test.ts

import { test } from "node:test";
import assert from "node:assert/strict";
import { aimAngles, aimFrom, barFrame, cross, derbyDirs, dot, hitRoom, reportBar, type Picture, type V3 } from "./room3d.ts";

const ROOM: V3 = [5, 10, 5];
const NORTH: Picture = { id: "screen.north", label: "North Frame", at: [0, 7.17, 2.11], normal: [1, 0, 0], up: [0, 0, 1], size: [2, 1] };

test("a beam lands on the face it meets first, and on a screen's picture when it's there", () => {
  assert.equal(hitRoom([2.5, 5, 2], [0, 0, -1], ROOM)?.surface, "floor");
  assert.equal(hitRoom([2.5, 5, 2], [1, 0, 0], ROOM)?.surface, "east");
  const h = hitRoom([2.5, 7.17, 2.11], [-1, 0, 0], ROOM, [NORTH]);
  assert.equal(h?.surface, "screen.north");
  assert.ok(Math.abs((h?.dist ?? 0) - 2.5) < 1e-9);
  assert.equal(hitRoom([2.5, 5, 2.11], [-1, 0, 0], ROOM, [NORTH])?.surface, "west");
});

test("a bar's frame is right-handed and its aim round-trips through turn and tilt", () => {
  const f = barFrame([1, 1, 2], aimFrom(30, -15));
  const z = cross(f.right, f.up);
  assert.ok(dot(z, f.fwd) > 0.999);
  assert.deepEqual(aimAngles(f.fwd), { turn: 30, tilt: -15 });
});

test("derby beamlets stay inside the room's half-space in front of the bar", () => {
  const f = barFrame([0.45, 1.35, 2], aimFrom(45, -10));
  for (const d of derbyDirs(f, 77, 0)) assert.ok(dot(d, f.fwd) > 0.7);
});

test("a bar aimed at a screen lights it; one aimed away doesn't; a laser at head height is counted", () => {
  const at: V3 = [3, 7.17, 2.11];
  const facing = reportBar(barFrame(at, [-1, 0, 0]), ROOM, [NORTH]);
  assert.ok(facing.parsOnScreens > 0.5);
  const away = reportBar(barFrame(at, [1, 0, 0]), ROOM, [NORTH]);
  assert.equal(away.parsOnScreens, 0);
  assert.ok(reportBar(barFrame([3, 5, 1.5], [1, 0, 0]), ROOM, [NORTH]).laserAtHeads > 0.5);
  const up = reportBar(barFrame([2.5, 5, 2.4], aimFrom(0, 40)), ROOM, [NORTH]);
  assert.equal(up.laserAtHeads, 0);
});
