// What the screen shows, as one document (screenState.ts). Plain node:
//   node --experimental-strip-types --test src/pages/DisplayPage/art/screenState.test.ts

import { test } from "node:test";
import assert from "node:assert/strict";
import { baseUrl, isState, newer, stillAspect, transitionToPlay, TRANSITION_FRESH_S, type ScreenState } from "./screenState.ts";

const NOW = 1_800_000_000;
function st(seq: number, extra: Partial<ScreenState> = {}): ScreenState {
  return {
    v: 1, seq, at: NOW - 10,
    still: { url: `/output/_screens/n/${seq}.jpg`, width: 3840, height: 2160, origin: `north-screen/${seq}.jpg` },
    clip: null, transition: null, video: null, ...extra,
  };
}

test("the newer document wins, whichever order a push and a read arrive in", () => {
  assert.equal(newer(st(3), st(2))?.seq, 3);
  assert.equal(newer(st(2), st(3))?.seq, 3);
  assert.equal(newer(null, st(1))?.seq, 1);
  assert.equal(newer(st(1), null)?.seq, 1);
  // rewritten from scratch: a lower seq, written later
  assert.equal(newer(st(40), { ...st(1), at: NOW })?.seq, 1);
});

test("the base is the published video, else the still; a still's clip is never the base", () => {
  assert.equal(baseUrl(st(2, { clip: { url: "/c.mp4" } })), "/output/_screens/n/2.jpg");
  assert.equal(baseUrl({ ...st(2), still: null, video: { url: "/v.mp4" } }), "/v.mp4");
  assert.equal(stillAspect(st(1)), 3840 / 2160);
  assert.equal(isState({ seq: 1 }), false);
  assert.equal(isState(st(1)), true);
});

test("a transition plays once, only for a new still this page saw arrive, fresh, from the picture shown", () => {
  const t = { url: "/t.mp4", from: "north-screen/1.jpg", to: "north-screen/2.jpg", at: NOW - 5 };
  const played = new Set<string>();
  assert.equal(transitionToPlay(null, st(2, { transition: t }), NOW, played), null, "first load: no transition");
  assert.equal(transitionToPlay(st(1), st(2, { transition: t }), NOW, played)?.url, "/t.mp4");
  assert.equal(transitionToPlay(st(1), st(2, { transition: t }), NOW, played), null, "once");
  const stale = { ...t, url: "/t2.mp4", at: NOW - TRANSITION_FRESH_S - 1 };
  assert.equal(transitionToPlay(st(1), st(2, { transition: stale }), NOW, new Set()), null, "woke long after");
  const other = { ...t, url: "/t3.mp4", from: "north-screen/7.jpg" };
  assert.equal(transitionToPlay(st(1), st(2, { transition: other }), NOW, new Set()), null, "missed a change between");
  // the clip arriving for the same still is not a new picture
  const same = st(2, { transition: { ...t, url: "/t4.mp4" } });
  assert.equal(transitionToPlay(same, { ...same, seq: 3, clip: { url: "/c.mp4" } }, NOW, new Set()), null);
});
