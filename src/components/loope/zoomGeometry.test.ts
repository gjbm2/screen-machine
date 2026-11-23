// node --experimental-strip-types --test src/components/loope/zoomGeometry.test.ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { fitBox, poseOver, zoomAbout, pinchPose, clampPose } from "./zoomGeometry.ts";

test("fitBox contains and centres", () => {
  assert.deepEqual(fitBox(2, 1000, 1000), { left: 0, top: 250, width: 1000, height: 500 });
  assert.deepEqual(fitBox(0.5, 1000, 1000), { left: 250, top: 0, width: 500, height: 1000 });
});

test("poseOver lands the content box on the target", () => {
  const fit = fitBox(2, 1000, 1000);
  const p = poseOver(fit, { left: 100, top: 300, width: 500, height: 250 });
  assert.deepEqual(p, { x: 100, y: 300, scale: 0.5 });
});

test("zoomAbout keeps the point under the cursor", () => {
  const p = zoomAbout({ x: 10, y: 20, scale: 1 }, 110, 220, 3);
  // content point under (110,220) was (100,200); it must still map to (110,220)
  assert.equal(100 * p.scale + p.x, 110);
  assert.equal(200 * p.scale + p.y, 220);
});

test("pinchPose scales by finger distance and follows the midpoint", () => {
  const start = { x: 0, y: 0, scale: 1 };
  const p = pinchPose(start, { x: 50, y: 50 }, 100, { x: 60, y: 50 }, 200, 0.5, 8);
  assert.equal(p.scale, 2);
  assert.equal(50 * p.scale + p.x, 60);
  assert.equal(pinchPose(start, { x: 0, y: 0 }, 100, { x: 0, y: 0 }, 1, 0.5, 8).scale, 0.5);
});

test("clampPose never leaves the media off screen", () => {
  const fit = fitBox(2, 1000, 1000); // 1000 x 500
  // zoomed out: centred
  assert.deepEqual(clampPose({ x: -999, y: 999, scale: 0.5 }, fit, 1000, 1000), { x: 250, y: 375, scale: 0.5 });
  // zoomed 3x and flung far away: pinned edge to edge on x, centred on y (1500 > 1000 so edge on y too)
  const c = clampPose({ x: 5000, y: -9999, scale: 3 }, fit, 1000, 1000);
  assert.equal(c.x, 0);
  assert.equal(c.y, 1000 - 1500);
});
