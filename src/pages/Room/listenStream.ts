// "This device": PC22's piano synth streamed to this browser (owner, 23 Oct: "synth on PC22 and then stream to local
// device browser", so a phone or iPad viewing the console can hear the music too). One <audio> element for the whole
// page, on show_service's live stream through room_service's /show pass-through (same origin as the page's API):
//   HEAD /show/api/v1/piano/live.mp3?instrument=<id>   probed before every connect: 404 {"error": "not_available"}
//                                                      ("Not available yet", no retry loop), 503 {"error": "busy"}
//                                                      (two other sounds are streaming already); X-Piano-Instrument
//                                                      names the sound actually used
//   GET  /show/api/v1/piano/live.mp3?instrument=<id>   chunked audio/mpeg (128 kb/s, 48 kHz stereo) of whatever the
//                                                      show bus plays: test pieces, recordings and the real Steinway.
//                                                      The first bytes take 3-7 s while the sound loads ("Getting the
//                                                      sound ready…" until the element plays); then it runs about
//                                                      0.25 s plus the browser's own buffer behind the room (lagS).
// The instrument is the page's chosen sound (usePianoSound); a new one reopens the stream. Off drops the element's
// source, so the connection closes.
//
// This device is one of the room's outputs (show_service, 23 Oct; owner: "I clicked play on PC22, and the sound came
// out of ipad"): each browser has its own id (random, kept here), "device:<id>", registered by name (POST
// …/piano/devices {id, name}: "iPad", from the browser). Picking This device makes it the room's output (the player's
// and the test piano's), so another page sees it by name; the stream (…&device=<id>) carries the notes only while it
// is the output and silence otherwise, staying open so choosing it again is instant. "Also play on this device" adds
// &also=1: it hears whatever the output. On a pause, stop or seek the element jumps to its live edge (listenFlush), so
// what it holds unplayed (1-3 s) isn't heard after the tap.
//
// On or off is remembered in this browser (like the piano's sound and output). Mobile browsers play sound only from a
// tap: the tap that turns it on plays a short silence at once, which unlocks this element for the stream that follows
// the probe; when the browser still holds it back (a reload, the phone's own pause) the page shows "Tap to start
// sound". A stall (no progress for STALL_MS), an error or the end of the stream after it has played reconnects after
// a short wait (2 s, doubling to 15 s, MAX_RETRIES times, then "Try again"); falling LAG_MAX_S behind live
// reconnects at once to catch up.

import { useSyncExternalStore } from "react";
import { resolveRoomUrl, storageGet, storageSet } from "./api";

export const HERE_LABEL = "This device";
/** show_service's output for the screens only (PC22 and Sonos quiet). */
export const SCREENS_ONLY = "none";

const ON_KEY = "sm_room_listen_here";
const ALSO_KEY = "sm_room_listen_also";
const DEVICE_KEY = "sm_room_device_id";
const DEVICES_PATH = "/show/api/v1/piano/devices";
const STREAM_PATH = "/show/api/v1/piano/live.mp3";
const PROBE_TIMEOUT_MS = 12_000;
/** From the stream's request to the element playing: the sound loads first (up to 7 s), then the browser buffers. */
const CONNECT_TIMEOUT_MS = 20_000;
/** show_service's share of the delay behind the room; the browser's buffer comes on top (lagS). */
export const SERVER_LAG_S = 0.25;
const STALL_MS = 6_000;
const WATCH_MS = 2_000;
const RETRY_FIRST_MS = 2_000;
const RETRY_CAP_MS = 15_000;
const MAX_RETRIES = 8;
/** Further behind live than this (buffered but not yet heard): reconnect to catch up. */
const LAG_MAX_S = 8;

/**
 * off · checking (asking whether the stream is there) · connecting (the sound is getting ready) · playing · tap (the
 * browser wants a tap first) · unavailable (not there yet) · busy (two other sounds are streaming already) ·
 * reconnecting (it stopped; trying again by itself) · failed (gave up; a tap retries).
 */
export type ListenStatus =
  | "off"
  | "checking"
  | "connecting"
  | "playing"
  | "tap"
  | "unavailable"
  | "busy"
  | "reconnecting"
  | "failed";

export interface ListenSnap {
  on: boolean;
  status: ListenStatus;
  /** The live music screen was closed since the sound was last turned on (it then opens only when asked). */
  dismissed: boolean;
  /** The screen is open. */
  viewOpen: boolean;
  /** The sound PC22 is actually streaming (its X-Piano-Instrument), when it said. */
  instrumentUsed: string | null;
  /** "Also play on this device": hears whatever the room's output (else only while this device is the output). */
  also: boolean;
  /** The room's output now (GET …/piano/devices, while on), and its name; null until read. */
  roomOutput: string | null;
  roomOutputLabel: string | null;
}

let snap: ListenSnap = {
  on: storageGet(ON_KEY) === "1",
  status: "off",
  dismissed: true,
  viewOpen: false,
  instrumentUsed: null,
  also: storageGet(ALSO_KEY) === "1",
  roomOutput: null,
  roomOutputLabel: null,
};
const listeners = new Set<() => void>();
let base: string | null = null;
let instrument: string | null = null;
let configured = false;
let audio: HTMLAudioElement | null = null;
let gen = 0; // bumps on every (re)connect and on off: a late answer from an older one is dropped
let everPlayed = false; // this connect has played (so a failure reconnects rather than reporting "not available")
let retries = 0;
let retryTimer: number | undefined;
let watchTimer: number | undefined;
let connectAt = 0;
let lastTime = 0;
let lastProgressAt = 0;
let ourPause = false;
let silentUrl: string | null = null;

function set(p: Partial<ListenSnap>) {
  snap = { ...snap, ...p };
  listeners.forEach((l) => l());
}

function subscribe(l: () => void) {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
}

const getSnap = () => snap;

/** The state now, outside React (a socket handler). */
export const listenSnap = (): ListenSnap => snap;

/** The state of "This device", for a component. */
export function useListenHere(): ListenSnap {
  return useSyncExternalStore(subscribe, getSnap, getSnap);
}

const showBase = () => (base ?? resolveRoomUrl(window.location.search)).replace(/\/+$/, "");

let myId: string | null = null;
/** This browser's own id (1-64 of [A-Za-z0-9_-]), made once and kept. */
export function deviceId(): string {
  if (myId) return myId;
  let id = storageGet(DEVICE_KEY);
  if (!id || !/^[A-Za-z0-9_-]{1,64}$/.test(id)) {
    const b = new Uint8Array(9);
    crypto.getRandomValues(b);
    id = Array.from(b, (x) => x.toString(36).padStart(2, "0")).join("").slice(0, 16);
    storageSet(DEVICE_KEY, id);
  }
  myId = id;
  return id;
}

/** This device as a room output id. */
export const myOutput = (): string => `device:${deviceId()}`;

/** What other pages call this device: the kind of thing it is, from the browser. */
export function deviceName(): string {
  const ua = navigator.userAgent;
  if (/iPad/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1)) return "iPad";
  if (/iPhone/.test(ua)) return "iPhone";
  if (/Android/.test(ua)) return /Mobile/.test(ua) ? "Android phone" : "Android tablet";
  if (/Macintosh/.test(ua)) return "Mac";
  if (/Windows/.test(ua)) return "Windows PC";
  if (/CrOS/.test(ua)) return "Chromebook";
  if (/Linux/.test(ua)) return "Linux PC";
  return "A browser";
}

/** Tell show_service this device exists, by name (before it's made the output). False if it didn't answer. */
export async function registerDevice(inst: string | null): Promise<boolean> {
  try {
    const r = await fetch(`${showBase()}${DEVICES_PATH}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: deviceId(), name: deviceName(), ...(inst ? { instrument: inst } : {}) }),
      signal: AbortSignal.timeout(5_000),
    });
    return r.ok;
  } catch {
    return false;
  }
}

export interface DevicesView {
  devices: { id: string; label: string; streaming?: boolean; seen_s?: number }[];
  output: string | null;
  hears?: unknown;
}

/** GET …/piano/devices: the room's output now and the devices it knows; null if it didn't answer. */
export async function fetchDevices(signal?: AbortSignal): Promise<DevicesView | null> {
  try {
    const r = await fetch(`${showBase()}${DEVICES_PATH}`, { cache: "no-store", signal });
    if (!r.ok) return null;
    const v = (await r.json()) as DevicesView;
    return v && Array.isArray(v.devices) ? v : null;
  } catch {
    return null;
  }
}

/** The room's output as last read (ListenHere, while on). Moving away from this device drops what it holds. */
export function setRoomOutput(id: string | null, label: string | null) {
  if (id === snap.roomOutput && label === snap.roomOutputLabel) return;
  const leftMe = snap.roomOutput === myOutput() && id !== myOutput();
  set({ roomOutput: id, roomOutputLabel: label });
  if (leftMe && !snap.also) listenFlush();
}

function streamUrl(): string {
  const q = new URLSearchParams();
  if (instrument) q.set("instrument", instrument);
  q.set("device", deviceId());
  q.set("name", deviceName());
  if (snap.also) q.set("also", "1");
  return `${showBase()}${STREAM_PATH}?${q.toString()}`;
}

/** 0.1 s of silence as a WAV (8 kHz, 16-bit mono): played from the tap so the browser lets this element play later. */
function silence(): string {
  if (silentUrl) return silentUrl;
  const n = 800;
  const buf = new ArrayBuffer(44 + n * 2);
  const v = new DataView(buf);
  const str = (o: number, s: string) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
  str(0, "RIFF");
  v.setUint32(4, 36 + n * 2, true);
  str(8, "WAVE");
  str(12, "fmt ");
  v.setUint32(16, 16, true);
  v.setUint16(20, 1, true);
  v.setUint16(22, 1, true);
  v.setUint32(24, 8000, true);
  v.setUint32(28, 16000, true);
  v.setUint16(32, 2, true);
  v.setUint16(34, 16, true);
  str(36, "data");
  v.setUint32(40, n * 2, true);
  silentUrl = URL.createObjectURL(new Blob([buf], { type: "audio/wav" }));
  return silentUrl;
}

function element(): HTMLAudioElement {
  if (audio) return audio;
  const a = new Audio();
  a.preload = "none";
  a.setAttribute("playsinline", "");
  const live = () => !!snap.on && !!a.src && a.src !== silentUrl;
  a.addEventListener("playing", () => {
    if (!live()) return;
    everPlayed = true;
    retries = 0;
    lastProgressAt = performance.now();
    lastTime = a.currentTime;
    set({ status: "playing" });
  });
  a.addEventListener("timeupdate", () => {
    if (a.currentTime !== lastTime) {
      lastTime = a.currentTime;
      lastProgressAt = performance.now();
    }
  });
  a.addEventListener("error", () => {
    if (live()) failed();
  });
  a.addEventListener("ended", () => {
    if (live()) failed();
  });
  a.addEventListener("pause", () => {
    // the phone paused it (a call, the lock screen's pause, headphones out): it needs a tap again. Not ours, and still
    // paused when the event arrives (a pause queued by a source change is followed at once by our play()).
    if (ourPause || !live() || !a.paused) return;
    if (snap.status === "playing" || snap.status === "connecting") set({ status: "tap" });
  });
  audio = a;
  return a;
}

function pause(a: HTMLAudioElement) {
  ourPause = true;
  try {
    a.pause();
  } finally {
    ourPause = false;
  }
}

/** Drop the stream (and its connection). */
function release() {
  window.clearTimeout(retryTimer);
  retryTimer = undefined;
  const a = audio;
  if (!a) return;
  pause(a);
  if (a.getAttribute("src")) {
    a.removeAttribute("src");
    try {
      a.load();
    } catch {
      /* nothing to drop */
    }
  }
}

type Probe = { ok: boolean; busy: boolean; instrument: string | null };

async function probe(url: string): Promise<Probe> {
  try {
    const res = await fetch(url, { method: "HEAD", cache: "no-store", signal: AbortSignal.timeout(PROBE_TIMEOUT_MS) });
    return { ok: res.ok, busy: res.status === 503, instrument: res.headers.get("X-Piano-Instrument") };
  } catch {
    return { ok: false, busy: false, instrument: null };
  }
}

function playNow(a: HTMLAudioElement, g: number) {
  a.play().catch((e: unknown) => {
    if (g !== gen || !snap.on) return;
    if (e instanceof DOMException && e.name === "NotAllowedError") set({ status: "tap" });
    // AbortError (a newer connect took over) needs nothing; anything else arrives as the element's error too
  });
}

/**
 * Probe, then open the stream. `tap`: called from a tap, so a short silence plays first to unlock the element.
 * `again`: a reconnect (the status says so while it tries).
 */
function connect(opts: { tap?: boolean; again?: boolean } = {}) {
  const a = element();
  const g = ++gen;
  window.clearTimeout(retryTimer);
  retryTimer = undefined;
  everPlayed = opts.again ? everPlayed : false;
  if (opts.tap) {
    a.src = silence();
    a.play().catch(() => undefined); // interrupted by the stream's own load below: expected
  } else pause(a);
  set({ status: opts.again ? "reconnecting" : "checking" });
  const url = streamUrl();
  void probe(url).then((p) => {
    if (g !== gen || !snap.on) return;
    if (!p.ok) {
      release();
      // two other sounds streaming: say so, no retrying; it played earlier and has gone (a restart on PC22): keep
      // trying for a while; never played: not there yet
      if (p.busy) set({ status: "busy" });
      else if (opts.again && retries < MAX_RETRIES) retry();
      else set({ status: opts.again ? "failed" : "unavailable" });
      return;
    }
    connectAt = performance.now();
    lastProgressAt = connectAt;
    a.src = url;
    set({ instrumentUsed: p.instrument, ...(opts.again ? {} : { status: "connecting" as const }) });
    playNow(a, g);
  });
  watch();
}

function retry() {
  window.clearTimeout(retryTimer);
  const wait = Math.min(RETRY_CAP_MS, RETRY_FIRST_MS * 2 ** retries);
  retries += 1;
  set({ status: "reconnecting" });
  retryTimer = window.setTimeout(() => {
    retryTimer = undefined;
    if (snap.on) connect({ again: true });
  }, wait);
}

/** The stream errored, ended or stalled. */
function failed() {
  if (!snap.on) return;
  release();
  if (everPlayed && retries < MAX_RETRIES) retry();
  else if (everPlayed) set({ status: "failed" });
  else set({ status: "unavailable" });
}

/** Every 2 s while on: a stall, a connect that never starts, or too far behind live. */
function watch() {
  if (watchTimer !== undefined) return;
  watchTimer = window.setInterval(() => {
    if (!snap.on) {
      window.clearInterval(watchTimer);
      watchTimer = undefined;
      return;
    }
    const a = audio;
    if (!a) return;
    const now = performance.now();
    // the stream asked for (not a probe or a wait between tries) and still not playing
    const asked = retryTimer === undefined && !!a.getAttribute("src") && a.src !== silentUrl;
    if ((snap.status === "connecting" || snap.status === "reconnecting") && asked && now - connectAt > CONNECT_TIMEOUT_MS) failed();
    else if (snap.status === "playing") {
      if (now - lastProgressAt > STALL_MS) failed();
      else if (lagS() > LAG_MAX_S) connect({ again: true });
    }
  }, WATCH_MS);
}

/** Jump to the live edge, so what the element holds unplayed isn't heard (a pause, a stop, a seek, the output moving
 *  away). Nothing while it isn't playing the stream. */
export function listenFlush() {
  const a = audio;
  if (!a || !snap.on || !a.src || a.src === silentUrl || !a.buffered?.length) return;
  try {
    const edge = a.buffered.end(a.buffered.length - 1) - 0.05;
    if (edge > a.currentTime + 0.1) a.currentTime = edge;
  } catch {
    /* not seekable just now: it plays out */
  }
}

/** The element playing the stream (null before the first connect): deviceLag.ts measures it. */
export const listenAudio = (): HTMLAudioElement | null => audio;

/** GET …/piano/live/clock for the stream's sound (deviceLag.ts); ``inst`` null: show_service's default. */
export function listenClockUrl(inst: string | null): string {
  return `${showBase()}${STREAM_PATH.replace(/\.mp3$/, "")}/clock${inst ? `?instrument=${encodeURIComponent(inst)}` : ""}`;
}

/** How far behind the newest sound received the element is playing (s): the browser's own share of the delay. */
export function lagS(): number {
  const a = audio;
  if (!a || !a.buffered?.length) return 0;
  try {
    return Math.max(0, a.buffered.end(a.buffered.length - 1) - a.currentTime);
  } catch {
    return 0;
  }
}

/** Where the page reaches show_service, and the chosen sound (null: show_service's default). A new sound reopens. */
export function configureListen(baseUrl: string, inst: string | null) {
  const first = !configured;
  const changed = inst !== instrument || baseUrl !== base;
  configured = true;
  base = baseUrl;
  instrument = inst;
  if (!snap.on) return;
  // remembered on from an earlier visit: try at once (the browser may want a tap: "Tap to start sound")
  if (first && snap.status === "off") connect();
  else if (changed && ["playing", "connecting", "reconnecting", "checking"].includes(snap.status)) {
    retries = 0;
    connect({ again: snap.status === "playing" || snap.status === "reconnecting" });
  }
}

/** Turn "This device" on: as the output (`also` false) or as well as it ("Also play on this device"). Call it from
 *  the tap itself (mobile browsers play sound only from a tap). */
export function listenOn(also = false) {
  storageSet(ON_KEY, "1");
  storageSet(ALSO_KEY, also ? "1" : "0");
  retries = 0;
  set({ on: true, dismissed: false, also });
  connect({ tap: true });
}

/** Turn it off: the stream stops and the music screen closes. */
export function listenOff() {
  storageSet(ON_KEY, "0");
  gen += 1;
  set({ on: false, status: "off", viewOpen: false, dismissed: true });
  release();
}

/** "Tap to start sound" / "Try again": from a tap. */
export function listenTap() {
  if (!snap.on) {
    listenOn(snap.also);
    return;
  }
  // a fresh connect from this tap, at the live edge (a held-back or paused element would resume behind it)
  retries = 0;
  connect({ tap: true });
}

/** Open or close the live music screen. Closing it keeps the sound and keeps it closed until it's turned on again. */
export function setListenView(open: boolean) {
  set(open ? { viewOpen: true } : { viewOpen: false, dismissed: true });
}
