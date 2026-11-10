// node --experimental-strip-types --test src/pages/DisplayPage/control/closePolicy.test.ts

import { test } from "node:test";
import assert from "node:assert/strict";
import { reconnectPlan, UNKNOWN_SCREEN_RETRY_MS } from "./closePolicy.ts";

test("F12: a 4404 (room_service doesn't run this screen) waits minutes, not the <= 10 s backoff", () => {
  assert.deepEqual(reconnectPlan(4404, 0, 5_000), { kind: "after", ms: UNKNOWN_SCREEN_RETRY_MS });
  assert.ok(UNKNOWN_SCREEN_RETRY_MS >= 60_000);
});

test("4008 reconnects at once only after being up a while; anything else backs off as before", () => {
  assert.deepEqual(reconnectPlan(4008, 6_000, 5_000), { kind: "now" });
  assert.deepEqual(reconnectPlan(4008, 1_000, 5_000), { kind: "backoff" });
  assert.deepEqual(reconnectPlan(1006, 60_000, 5_000), { kind: "backoff" });
  assert.deepEqual(reconnectPlan(1000, 0, 5_000), { kind: "backoff" });
});
