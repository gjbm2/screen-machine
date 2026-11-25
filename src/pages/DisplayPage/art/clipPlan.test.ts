// When a still's clip plays (clipPlan.ts). Plain node:
//   node --experimental-strip-types --test src/pages/DisplayPage/art/clipPlan.test.ts

import { test, mock } from "node:test";
import assert from "node:assert/strict";
import { ClipPlan, FIRST_REST_S, PLAY_GUARD_S } from "./clipPlan.ts";

function rig(opts: { random?: number } = {}) {
  mock.timers.enable({ apis: ["setTimeout"] });
  const log: string[] = [];
  let free = true;
  const plan: ClipPlan = new ClipPlan(
    {
      start: () => log.push("start"),
      stop: (ms) => log.push(`stop ${ms}`),
      slotFree: () => free,
    },
    () => opts.random ?? 0,
  );
  const s = (sec: number) => mock.timers.tick(sec * 1000);
  return { plan, log, s, setFree: (f: boolean) => (free = f), done: () => mock.timers.reset() };
}

const CLIP = { url: "/output/_screens/n/000002.mp4", rest_s: [35, 55] as [number, number], play_s: 30 };

test("no clip, nothing plays", () => {
  const r = rig();
  r.plan.setClip(null);
  r.s(600);
  assert.deepEqual(r.log, []);
  assert.equal(r.plan.phase, "none");
  r.done();
});

test("a clip plays after a short first rest, then rests for its own rest_s between plays", () => {
  const r = rig({ random: 0 });
  r.plan.setClip(CLIP);
  r.s(FIRST_REST_S[0] - 0.1);
  assert.deepEqual(r.log, []);
  r.s(0.2);
  assert.deepEqual(r.log, ["start"]);
  r.plan.played();
  r.s(34.9);
  assert.deepEqual(r.log, ["start"]);
  r.s(0.2);
  assert.deepEqual(r.log, ["start", "start"]);
  r.done();
});

test("frozen or live: a playing clip fades out at once and nothing starts; afterwards a fresh rest", () => {
  const r = rig({ random: 0 });
  r.plan.setClip(CLIP);
  r.s(FIRST_REST_S[0] + 0.1);
  r.plan.setQuiet(true);
  assert.deepEqual(r.log, ["start", "stop 300"]);
  r.plan.played();
  r.s(600);
  assert.deepEqual(r.log, ["start", "stop 300"], "nothing plays while quiet");
  r.plan.setQuiet(false);
  r.s(FIRST_REST_S[0] + 0.1);
  assert.deepEqual(r.log, ["start", "stop 300", "start"]);
  r.done();
});

test("a hidden (sleeping) page plays nothing and catches up on nothing", () => {
  const r = rig({ random: 0 });
  r.plan.setClip(CLIP);
  r.plan.setHidden(true);
  r.s(3600);
  assert.deepEqual(r.log, []);
  r.plan.setHidden(false);
  r.s(FIRST_REST_S[0] + 0.1);
  assert.deepEqual(r.log, ["start"], "one play after a fresh rest, not a backlog");
  r.done();
});

test("a transition has the screen: a due play waits for the slot", () => {
  const r = rig({ random: 0 });
  r.plan.setClip(CLIP);
  r.setFree(false);
  r.s(FIRST_REST_S[0] + 0.1);
  assert.deepEqual(r.log, []);
  assert.equal(r.plan.canPlayNow(), false);
  r.setFree(true);
  r.s(FIRST_REST_S[0] + 0.1);
  assert.deepEqual(r.log, ["start"]);
  r.done();
});

test("Play now starts a resting clip at once, never a second copy", () => {
  const r = rig({ random: 0 });
  r.plan.setClip(CLIP);
  assert.equal(r.plan.playNow(), true);
  assert.equal(r.plan.playNow(), false);
  assert.deepEqual(r.log, ["start"]);
  r.done();
});

test("a stuck play is ended after its expected length plus the guard", () => {
  const r = rig({ random: 0 });
  r.plan.setClip(CLIP);
  r.plan.playNow();
  r.s(CLIP.play_s + PLAY_GUARD_S - 0.1);
  assert.deepEqual(r.log, ["start"]);
  r.s(0.2);
  assert.deepEqual(r.log, ["start", "stop 600"]);
  r.done();
});

test("a new picture's clip (or none) ends the playing one", () => {
  const r = rig({ random: 0 });
  r.plan.setClip(CLIP);
  r.plan.playNow();
  r.plan.setClip(null);
  assert.deepEqual(r.log, ["start", "stop 600"]);
  r.plan.played();
  r.s(600);
  assert.deepEqual(r.log, ["start", "stop 600"]);
  assert.equal(r.plan.phase, "none");
  r.done();
});

test("another video played once over the still: then the still's own clip rests as usual", () => {
  const r = rig({ random: 0 });
  r.plan.setClip(CLIP);
  assert.equal(r.plan.playOnce("/t/into.mp4"), true);
  assert.equal(r.plan.playingUrl, "/t/into.mp4");
  assert.equal(r.plan.playOnce("/t/other.mp4"), false, "one at a time");
  r.plan.played();
  assert.equal(r.plan.playingUrl, CLIP.url);
  r.s(35.1);
  assert.deepEqual(r.log, ["start", "start"], "its own clip after its rest");
  r.done();
});

test("a still with no clip can still play a transition once", () => {
  const r = rig({ random: 0 });
  r.plan.setClip(null);
  assert.equal(r.plan.playOnce("/t/out.mp4"), true);
  r.plan.played();
  r.s(600);
  assert.deepEqual(r.log, ["start"]);
  assert.equal(r.plan.phase, "none");
  r.done();
});
