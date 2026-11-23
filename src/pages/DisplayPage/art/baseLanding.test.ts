// Tests for holding a picture's caption until the picture lands. Plain node:
//   node --experimental-strip-types --test src/pages/DisplayPage/art/baseLanding.test.ts

import { test, mock } from "node:test";
import assert from "node:assert/strict";
import { BaseLanding, GRACE_MS, MAX_WAIT_MS, RECENT_MS } from "./baseLanding.ts";

function rig() {
  mock.timers.enable({ apis: ["setTimeout"] });
  let t = 0;
  const bl = new BaseLanding(() => t);
  const shown: string[] = [];
  const tick = (ms: number) => {
    t += ms;
    mock.timers.tick(ms);
  };
  return { bl, shown, tick, done: () => mock.timers.reset() };
}

test("caption before the poll sees the file: waits for the change to start and land", () => {
  const { bl, shown, tick, done } = rig();
  bl.whenLanded(() => shown.push("cap"));
  tick(800);
  bl.started(); // poll found the file
  tick(GRACE_MS); // past the grace: still held, the change is in flight
  assert.deepEqual(shown, []);
  bl.landed();
  assert.deepEqual(shown, ["cap"]);
  done();
});

test("caption during a cross-fade: shown when it lands", () => {
  const { bl, shown, tick, done } = rig();
  bl.started();
  bl.whenLanded(() => shown.push("cap"));
  tick(1000);
  assert.deepEqual(shown, []);
  bl.landed();
  assert.deepEqual(shown, ["cap"]);
  done();
});

test("caption just after the picture landed: shown at once", () => {
  const { bl, shown, tick, done } = rig();
  bl.started();
  bl.landed();
  tick(RECENT_MS - 100);
  bl.whenLanded(() => shown.push("cap"));
  assert.deepEqual(shown, ["cap"]);
  done();
});

test("no picture change comes: shown after the grace", () => {
  const { bl, shown, tick, done } = rig();
  bl.whenLanded(() => shown.push("cap"));
  tick(GRACE_MS - 1);
  assert.deepEqual(shown, []);
  tick(1);
  assert.deepEqual(shown, ["cap"]);
  done();
});

test("a change that never lands never holds the caption past the cap", () => {
  const { bl, shown, tick, done } = rig();
  bl.whenLanded(() => shown.push("cap"));
  tick(500);
  bl.started();
  tick(MAX_WAIT_MS);
  assert.deepEqual(shown, ["cap"]);
  bl.landed(); // shown once only
  assert.deepEqual(shown, ["cap"]);
  done();
});

test("dispose drops waiting captions", () => {
  const { bl, shown, tick, done } = rig();
  bl.whenLanded(() => shown.push("cap"));
  bl.dispose();
  tick(MAX_WAIT_MS);
  assert.deepEqual(shown, []);
  done();
});
