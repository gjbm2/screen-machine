// The lights' camera calibration maths. Run: node --experimental-strip-types --test src/components/sound/cameraCal.test.ts
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { acceptable, clockOffset, edgeAfter, estimate, type Frame, type Trial } from "./cameraCal.ts";

/** A 30 fps camera trace: dark (20) with a bright step (200) from `on` to `off` (ms), seen `lag` ms late. */
function trace(steps: [number, number][], lag: number, span = 20000, phase = 0): Frame[] {
  const out: Frame[] = [];
  for (let t = phase; t < span; t += 1000 / 30) {
    const lit = steps.some(([a, z]) => t - lag >= a && t - lag < z);
    out.push({ t, v: lit ? 200 : 20 });
  }
  return out;
}

describe("the lights' camera calibration", () => {
  it("finds a flash's edges within a frame", () => {
    const f = trace([[1000, 1300]], 80);
    const on = edgeAfter(f, 1000, true) as number;
    const off = edgeAfter(f, 1300, false) as number;
    assert.ok(Math.abs(on - 1080) < 34);
    assert.ok(Math.abs(off - 1380) < 34);
    assert.equal(edgeAfter(trace([], 0), 1000, true), null);          // nothing changed
  });

  it("takes the clock from the quickest round trip", () => {
    const got = clockOffset([
      { sent: 0, got: 40, server: 1000 + 30 },                         // a slow answer, late on the way back
      { sent: 100, got: 104, server: 1102 },
    ]);
    assert.deepEqual(got, { offset: 1000, rtt: 4 });
  });

  it("removes the camera's own delay using the screen's flashes", () => {
    const cam = 70;                                                    // the camera's pipeline
    const present = 16.7;
    const lightLag = 45;
    const lightOn = Array.from({ length: 8 }, (_, k) => 1000 + k * 2400);
    const screenOn = lightOn.map((x) => x + 1200);
    const steps: [number, number][] = [
      ...lightOn.map((a) => [a + lightLag, a + lightLag + 300] as [number, number]),
      ...screenOn.map((a) => [a + present, a + present + 300] as [number, number]),
    ];
    for (const phase of [0, 11, 23]) {
      const f = trace(steps, cam, 22000, phase);
      const seen = (on: number): Trial => ({ on, off: on + 300, seenOn: edgeAfter(f, on, true), seenOff: edgeAfter(f, on + 300, false) });
      const e = estimate(lightOn.map(seen), screenOn.map(seen), present);
      assert.ok(e);
      assert.ok(Math.abs((e?.riseMs ?? 0) - lightLag) < 20);  // within the camera's frame
      assert.equal(acceptable(e), true);
    }
  });

  it("keeps nothing from too few trials or a negative result", () => {
    const t: Trial = { on: 0, off: 300, seenOn: null, seenOff: null };
    assert.equal(estimate([t, t, t], [t, t, t], 16), null);
    assert.equal(acceptable({ riseMs: -5, fallMs: null, cameraMs: 60, spreadMs: 3, used: 8, of: 8, screenUsed: 8 }), false);
  });
});
