// "This device": PC22's piano synth played on this browser (owner, 23 Oct: "synth on PC22 and then stream to local
// device browser", so a phone or iPad viewing the console can hear the music too). The sound itself is deviceAudio.ts
// (raw PCM over WS /show/api/v1/piano/live.ws into an AudioWorklet with a small fixed buffer: owner, 23 Oct,
// "alignment between midi and 'this device' audio is utterly terrible"); this file is the page's side of it: on or
// off, the device's identity, where the room plays, and the words.
//
// This device is one of the room's outputs (owner: "I clicked play on PC22, and the sound came out of ipad"): each
// browser has its own id (random, kept here), "device:<id>", registered by name (POST …/piano/devices {id, name}:
// "iPad", from the browser). Picking This device makes it the room's output (the player's and the test piano's), so
// another page sees it by name; the stream (…&device=<id>) carries the notes only while it is the output and silence
// otherwise ("quiet"), staying open so choosing it again is instant. "Also play on this device" adds &also=1: it hears
// whatever the output. A pause or stop hushes it at once (listenFlush: what it holds and what's in flight dropped);
// the next play, seek or skip lets it sound again (listenResume).
//
// On or off is remembered in this browser (like the piano's sound and output). Mobile browsers start sound only from a
// tap: the tap that turns it on starts the audio; when the browser still holds it back (a reload, the phone suspending
// it) the page shows "Tap to start sound". It reconnects by itself (deviceAudio.ts).

import { useSyncExternalStore } from "react";
import { resolveRoomUrl, storageGet, storageSet } from "./api";
import { getDeviceLag } from "./deviceLag";
import {
  deviceAudioNeedsTap,
  deviceAudioSnap,
  hushDeviceAudio,
  startDeviceAudio,
  stopDeviceAudio,
  subscribeDeviceAudio,
  unhushDeviceAudio,
  type DeviceAudioStatus,
} from "./deviceAudio";

export const HERE_LABEL = "This device";
/** show_service's output for the screens only (PC22 and Sonos quiet). */
export const SCREENS_ONLY = "none";

const ON_KEY = "sm_room_listen_here";
const ALSO_KEY = "sm_room_listen_also";
const DEVICE_KEY = "sm_room_device_id";
const DEVICES_PATH = "/show/api/v1/piano/devices";
const WS_PATH = "/show/api/v1/piano/live.ws";
/** Behind the room before the first measurement (seconds): the server's share plus the device's buffer. */
const LAG_GUESS_S = 0.5;
const TAP_CHECK_MS = 1_000;

/**
 * off · connecting (the sound is getting ready) · playing · quiet (connected; the room plays somewhere else) · tap (the
 * browser wants a tap first) · failed (a tap retries).
 */
export type ListenStatus = "off" | "connecting" | "playing" | "quiet" | "tap" | "failed";

export interface ListenSnap {
  on: boolean;
  status: ListenStatus;
  /** The live music screen was closed since the sound was last turned on (it then opens only when asked). */
  dismissed: boolean;
  /** The screen is open. */
  viewOpen: boolean;
  /** The sound PC22 is actually streaming, when it said. */
  instrumentUsed: string | null;
  /** "Also play on this device": hears whatever the room's output (else only while this device is the output). */
  also: boolean;
  /** The room's output now (GET …/piano/devices), and its name; null until read. */
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
let tapTimer: number | undefined;

function set(p: Partial<ListenSnap>) {
  const next = { ...snap, ...p };
  if ((Object.keys(p) as (keyof ListenSnap)[]).every((k) => next[k] === snap[k])) return;
  snap = next;
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

const AUDIO_STATUS: Record<DeviceAudioStatus, ListenStatus> = {
  off: "connecting", // on, but the audio hasn't started yet
  connecting: "connecting",
  buffering: "connecting",
  playing: "playing",
  quiet: "quiet",
  failed: "failed",
};

/** deviceAudio's state as the page's words. */
function follow() {
  if (!snap.on) return;
  const a = deviceAudioSnap();
  set({ status: deviceAudioNeedsTap() ? "tap" : AUDIO_STATUS[a.status], instrumentUsed: a.instrument ?? snap.instrumentUsed });
}
subscribeDeviceAudio(follow);

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

/** The name this device was given in Settings › This device ("Greg's iPad"), kept in this browser. */
export const DEVICE_NAME_KEY = "sm_device_name";

/** What other pages call this device: the name it was given in Settings, else the kind of thing it is. */
export function deviceName(): string {
  const own = storageGet(DEVICE_NAME_KEY)?.trim();
  return own ? own.slice(0, 40) : guessDeviceName();
}

/** The kind of thing this device is, from the browser ("iPad"): the name until it's given one. */
export function guessDeviceName(): string {
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
export async function registerDevice(inst: string | null, baseUrl?: string): Promise<boolean> {
  try {
    const r = await fetch(`${(baseUrl ?? showBase()).replace(/\/+$/, "")}${DEVICES_PATH}`, {
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
export async function fetchDevices(signal?: AbortSignal, baseUrl?: string): Promise<DevicesView | null> {
  try {
    const r = await fetch(`${(baseUrl ?? showBase()).replace(/\/+$/, "")}${DEVICES_PATH}`, { cache: "no-store", signal });
    if (!r.ok) return null;
    const v = (await r.json()) as DevicesView;
    return v && Array.isArray(v.devices) ? v : null;
  } catch {
    return null;
  }
}

/** The room's output as last read. Moving away from this device silences it at once (unless it also plays). */
export function setRoomOutput(id: string | null, label: string | null) {
  // the same output, a mount that doesn't know its name yet: keep the name (two Play on mounts, one with the list
  // and one without, set it back and forth for ever: the score page's "Maximum update depth", 23 Oct)
  if (id === snap.roomOutput && (label === snap.roomOutputLabel || label === null)) return;
  const leftMe = snap.roomOutput === myOutput() && id !== myOutput();
  const toMe = id === myOutput() && snap.roomOutput !== myOutput();
  set({ roomOutput: id, roomOutputLabel: label });
  if (snap.also) return;
  if (leftMe) hushDeviceAudio();
  else if (toMe) unhushDeviceAudio();
}

function streamUrl(): string {
  const q = new URLSearchParams();
  if (instrument) q.set("instrument", instrument);
  q.set("device", deviceId());
  q.set("name", deviceName());
  if (snap.also) q.set("also", "1");
  return `${showBase()}${WS_PATH}?${q.toString()}`;
}

/** Seconds this device hears the music behind the room: measured, else a fair guess. */
export const behindS = (): number => getDeviceLag() ?? LAG_GUESS_S;

/** Silence now (a pause or stop): what it holds and what's on its way isn't heard. */
export function listenFlush() {
  if (snap.on) hushDeviceAudio();
}

/** Sound again (a play, seek or skip): what arrives from now plays. */
export function listenResume() {
  if (snap.on) unhushDeviceAudio();
}

function start() {
  set({ status: "connecting" });
  void startDeviceAudio(streamUrl()).then(follow);
  window.clearInterval(tapTimer);
  // a phone may suspend the audio (the lock screen, a call): then it needs a tap
  tapTimer = window.setInterval(() => (snap.on ? follow() : window.clearInterval(tapTimer)), TAP_CHECK_MS);
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
  if (first || changed) start();
}

/** Turn "This device" on: as the output (`also` false) or as well as it ("Also play on this device"). Call it from
 *  the tap itself (mobile browsers play sound only from a tap). */
export function listenOn(also = false) {
  storageSet(ON_KEY, "1");
  storageSet(ALSO_KEY, also ? "1" : "0");
  set({ on: true, dismissed: false, also });
  start();
}

/** Turn it off: the sound stops and the music screen closes. */
export function listenOff() {
  storageSet(ON_KEY, "0");
  window.clearInterval(tapTimer);
  set({ on: false, status: "off", viewOpen: false, dismissed: true });
  stopDeviceAudio();
}

/** "Tap to start sound" / "Try again": from a tap. */
export function listenTap() {
  if (!snap.on) listenOn(snap.also);
  else start();
}

/** Open the pianola (the live music screen) to watch the notes, whether or not this device plays the sound. */
export function openPianola() {
  set({ viewOpen: true, dismissed: false });
}

/** Open or close the live music screen. Closing it keeps the sound and keeps it closed until it's turned on again. */
export function setListenView(open: boolean) {
  set(open ? { viewOpen: true } : { viewOpen: false, dismissed: true });
}
