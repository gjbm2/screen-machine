// Tests for the admin page's kiosk status parser. Plain node, as Lights/lib.test.ts:
//   node --experimental-strip-types --test src/pages/adminStatus.test.ts

import { test } from "node:test";
import assert from "node:assert/strict";
import { parseStatus } from "./adminStatus.ts";

test("finsbury's status reads as before: NORTH_/SOUTH_ rows by short name", () => {
  const s = parseStatus(
    ["UPTIME=up 2 days", "KIOSK=active", "NORTH_CHROME=running", "NORTH_URL=http://x/display/north-screen",
     "SOUTH_CHROME=stopped", "SOUTH_URL=unknown", "MEM_PERCENT=40", "DISPLAY_LINE=HDMI-1 connected"].join("\n"),
  );
  assert.deepEqual(s.chrome, { north: "running", south: "stopped" });
  assert.deepEqual(s.urls, { north: "http://x/display/north-screen", south: "unknown" });
  assert.equal(s.kiosk, "active");
  assert.equal(s.mem, "40");
  assert.deepEqual(s.displays, ["HDMI-1 connected"]);
});

test("a kiosk screen name with '_' (the admin API allows [a-z][a-z0-9_]*) is read whole", () => {
  const s = parseStatus("NORTH_TV_CHROME=running\nNORTH_TV_URL=http://x/display/n\nSOUTH_CHROME=running\n");
  assert.deepEqual(s.chrome, { north_tv: "running", south: "running" });
  assert.deepEqual(s.urls, { north_tv: "http://x/display/n" });
});
