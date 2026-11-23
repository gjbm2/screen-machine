// node --experimental-strip-types --test src/pages/DisplayPage/control/ReloadScheduler.test.ts

import { test, mock } from "node:test";
import assert from "node:assert/strict";
import { ReloadScheduler } from "./ReloadScheduler.ts";

function scheduler(canReload: () => boolean) {
  const reload = mock.fn();
  const s = new ReloadScheduler({ canReloadNow: canReload, reload });
  return { s, reload };
}

test("a new build reloads at once when the page may", () => {
  mock.timers.enable({ apis: ["setTimeout"] });
  const { s, reload } = scheduler(() => true);
  s.start();
  s.requestNow("new build");
  assert.equal(reload.mock.callCount(), 1);
  s.requestNow("new build");
  assert.equal(reload.mock.callCount(), 1, "once only");
  s.stop();
  mock.timers.reset();
});

test("while live it waits, re-checking every 5 s, then reloads", () => {
  mock.timers.enable({ apis: ["setTimeout"] });
  let live = true;
  const { s, reload } = scheduler(() => !live);
  s.start();
  s.requestNow("new build");
  mock.timers.tick(30_000);
  assert.equal(reload.mock.callCount(), 0);
  live = false;
  mock.timers.tick(5_000);
  assert.equal(reload.mock.callCount(), 1);
  s.stop();
  mock.timers.reset();
});

test("it runs with timed reloads off, and a stopped page never reloads", () => {
  mock.timers.enable({ apis: ["setTimeout"] });
  let live = true;
  const { s, reload } = scheduler(() => !live);
  s.start();
  s.setEnabled(false);
  s.requestNow("new build");
  live = false;
  mock.timers.tick(5_000);
  assert.equal(reload.mock.callCount(), 1, "timed reloads off doesn't stop a new build");

  const b = scheduler(() => false);
  b.s.start();
  b.s.requestNow("new build");
  b.s.stop();
  mock.timers.tick(60_000);
  assert.equal(b.reload.mock.callCount(), 0);
  mock.timers.reset();
});
