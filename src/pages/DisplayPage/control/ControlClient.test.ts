// The control socket's resilience (ControlClient.ts, owner 25 Nov: "a resilient browser loop as a failsafe to restore
// connections on client side"). Plain node, a fake WebSocket and a fake clock:
//   node --import ./scripts/node-ts-resolve.mjs --experimental-strip-types --test src/pages/DisplayPage/control/ControlClient.test.ts

import { test, mock } from "node:test";
import assert from "node:assert/strict";

let now = 0;
const sockets: FakeSocket[] = [];

class FakeSocket {
  static OPEN = 1;
  readyState = 0;
  sent: string[] = [];
  url: string;
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: ((ev: { code: number; reason: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(url: string) {
    this.url = url;
    sockets.push(this);
  }
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
  message(type: string) {
    this.onmessage?.({ data: JSON.stringify({ v: 1, type, data: {} }) });
  }
  serverClose(code = 1006) {
    this.readyState = 3;
    this.onclose?.({ code, reason: "" });
  }
  send(s: string) {
    this.sent.push(s);
  }
  close() {
    this.readyState = 3;
  }
}

Object.assign(globalThis, {
  WebSocket: FakeSocket,
  window: { addEventListener() {}, removeEventListener() {} },
  performance: { now: () => now },
});
const { ControlClient } = await import("./ControlClient.ts");

function rig() {
  mock.timers.reset();                       // a failed test before must not leave its timers on
  mock.timers.enable({ apis: ["setInterval", "setTimeout"] });
  sockets.length = 0;
  now = 0;
  const statuses: string[] = [];
  const c = new ControlClient({
    url: "ws://room/v1/screen/devtest?role=kiosk",
    hello: () => ({ role: "kiosk", screen: "devtest" }) as never,
    onMessage: () => {},
    onStatus: (s) => statuses.push(s),
  });
  const pass = (ms: number) => {
    for (let t = 0; t < ms; t += 100) {
      now += 100;
      mock.timers.tick(100);
    }
  };
  return { c, statuses, pass, done: () => { c.stop(); mock.timers.reset(); } };
}

test("it connects, says hello, and sends a heartbeat when it has been quiet", () => {
  const r = rig();
  r.c.start();
  assert.equal(sockets.length, 1);
  sockets[0].open();
  assert.match(sockets[0].sent[0], /"hello"/);
  r.pass(2500);
  sockets[0].message("hb");
  assert.ok(sockets[0].sent.some((s) => s.includes('"hb"')));
  r.done();
});

test("a socket the server closes is replaced, within the backoff", () => {
  const r = rig();
  r.c.start();
  sockets[0].open();
  sockets[0].serverClose(1006);
  r.pass(10_500);
  assert.ok(sockets.length >= 2, "reconnected (a new socket that never opens is itself replaced after 6 s)");
  r.done();
});

test("a socket that goes silent is dropped as dead and replaced", () => {
  const r = rig();
  r.c.start();
  sockets[0].open();
  r.pass(6_600);                         // no inbound frame for over 6 s
  r.pass(10_500);
  assert.ok(sockets.length >= 2, "a new socket after the dead one");
  r.done();
});

test("it keeps trying while room_service is down, and is up as soon as it answers", () => {
  const r = rig();
  r.c.start();
  for (let i = 0; i < 6; i++) {
    const n = sockets.length;
    sockets[n - 1].serverClose(1006);                // refused, again and again
    for (let t = 0; t < 12_000 && sockets.length === n; t += 100) r.pass(100);
    assert.ok(sockets.length > n, `tried again (${i + 1})`);
  }
  sockets[sockets.length - 1].open();                // room_service answers
  assert.equal(r.statuses.at(-1), "up");
  r.done();
});

test("restart with a new address reconnects there at once (a hidden page becoming a viewer)", () => {
  const r = rig();
  r.c.start();
  sockets[0].open();
  r.c.setUrl("ws://room/v1/screen/devtest?role=viewer");
  r.c.restart();
  assert.equal(sockets.length, 2);
  assert.match(sockets[1].url, /role=viewer/);
  r.done();
});

test("stopped, it stays stopped (an unmounted page never reconnects)", () => {
  const r = rig();
  r.c.start();
  sockets[0].open();
  r.c.stop();
  r.pass(30_000);
  assert.equal(sockets.length, 1);
  mock.timers.reset();
});
