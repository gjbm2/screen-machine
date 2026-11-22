// Tests for the Loope's zoom arithmetic (zoomMath.ts). Plain node:
//   node --experimental-strip-types --test src/components/loope/zoomMath.test.ts

import { test } from "node:test";
import assert from "node:assert/strict";
import { clampAxis, detailScale, fitBox, zoomAt } from "./zoomMath.ts";

const phonePortrait = { w: 390, h: 700 };
const phoneLandscape = { w: 844, h: 390 };
const fourK = { w: 3840, h: 2160 };

test("a 4K picture fits a phone either way up, keeping its shape", () => {
  assert.deepEqual(fitBox(fourK, phonePortrait), { w: 390, h: 219 });
  assert.deepEqual(fitBox(fourK, phoneLandscape), { w: 693, h: 390 });
});

test("a small picture is not enlarged; nothing known gives nothing", () => {
  assert.deepEqual(fitBox({ w: 200, h: 100 }, phonePortrait), { w: 200, h: 100 });
  assert.deepEqual(fitBox({ w: 0, h: 0 }, phonePortrait), { w: 0, h: 0 });
});

test("a double tap goes to the picture's own pixels, at least 2.5x, at most the cap", () => {
  assert.equal(detailScale(fourK, { w: 390, h: 219 }, 16), 3840 / 390);
  assert.equal(detailScale(fourK, { w: 390, h: 219 }, 8), 8);
  assert.equal(detailScale({ w: 400, h: 200 }, { w: 390, h: 195 }, 8), 2.5);
});

test("content smaller than the room is centred; larger never shows a gap", () => {
  assert.equal(clampAxis(-50, 200, 400), 100);
  assert.equal(clampAxis(100, 800, 400), 0);
  assert.equal(clampAxis(-900, 800, 400), -400);
  assert.equal(clampAxis(-200, 800, 400), -200);
});

test("zooming keeps the tapped point under the finger when there is room to", () => {
  const content = { w: 390, h: 219 };
  const wrapper = phonePortrait;
  const start = { x: 0, y: (700 - 219) / 2, scale: 1 };
  const p = { x: 195, y: 350 }; // the middle of the picture
  const t = zoomAt(start, p, 4, content, wrapper);
  assert.equal(t.scale, 4);
  // the point is still under the finger
  assert.ok(Math.abs((p.x - t.x) / 4 - (p.x - start.x)) < 1e-9);
  assert.ok(Math.abs((p.y - t.y) / 4 - (p.y - start.y)) < 1e-9);
});

test("zooming at a corner stops at the edge instead of showing black", () => {
  const content = { w: 390, h: 219 };
  const t = zoomAt({ x: 0, y: 240.5, scale: 1 }, { x: 0, y: 242 }, 4, content, phonePortrait);
  assert.equal(t.x, 0);
  assert.equal(t.y, 0);
});
