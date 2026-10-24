// The home page's one link to room_service (the room's LAN door, :8700).
//
// "On the LAN" means the browser can reach room_service: Flask can't tell LAN from WAN (every request
// arrives through the Windows portproxy from the same address), and :8700 is never forwarded to the WAN.
// So the page asks room_service itself: GET /api/v1/status, else /healthz (which answers any origin).
// Once it answers, the status stays current over the events socket (pushed, not polled), and every
// home-page piece (header Room button, Dev menu, tab live dots, each screen's Live panel) reads this
// one store. Off the LAN, or on an https page that can't call http://…:8700, nothing room-related shows.
//
// The base URL and the client are the Room page's own (src/pages/Room/api.ts), so both pages follow
// the same address rules (VITE_ROOM_URL; same-origin /rs behind the friendly HTTPS name).

import { useEffect, useState, useSyncExternalStore } from "react";
import { defaultRoomUrl, RoomApi } from "@/pages/Room/api";
import type { Library, Phase, RoomStatus, SceneSpec, ScreenStatus } from "@/pages/Room/types";
import type { StreamEvent } from "@/pages/Room/useRoomConnection";

const PROBE_TIMEOUT_MS = 2500;
/** Off the LAN (or room_service down), look again this often: a phone may join the Wi-Fi. */
const REPROBE_MS = 30_000;
const WS_BACKOFF_MS = [1000, 2000, 5000, 10_000, 15_000];

export interface RoomLinkState {
  /** null until the first probe answers. */
  onLan: boolean | null;
  /** True when this page may use the room API (not just reach /healthz). */
  canControl: boolean;
  status: RoomStatus | null;
  /** The events socket is open, so `status` is live. */
  connected: boolean;
}

let state: RoomLinkState = { onLan: null, canControl: false, status: null, connected: false };
const listeners = new Set<() => void>();
const streamListeners = new Set<(e: StreamEvent) => void>();
let api: RoomApi | null = null;
let started = false;
let ws: WebSocket | null = null;
let wsAttempt = 0;
let reprobeTimer: ReturnType<typeof setTimeout> | null = null;

function set(patch: Partial<RoomLinkState>) {
  state = { ...state, ...patch };
  listeners.forEach((fn) => fn());
}

export function roomApi(): RoomApi {
  if (!api) api = new RoomApi(defaultRoomUrl(), "");
  return api;
}

async function withTimeout<T>(fn: (signal: AbortSignal) => Promise<T>): Promise<T> {
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), PROBE_TIMEOUT_MS);
  try {
    return await fn(ac.signal);
  } finally {
    clearTimeout(timer);
  }
}

async function probe() {
  reprobeTimer = null;
  const a = roomApi();
  try {
    const status = await withTimeout((signal) => a.status(signal));
    set({ onLan: true, canControl: true, status });
    openSocket();
    return;
  } catch {
    /* not allowed from this origin, or not reachable: try the open health check */
  }
  try {
    await withTimeout((signal) => a.healthz(signal));
    set({ onLan: true, canControl: false, status: null, connected: false });
  } catch {
    set({ onLan: false, canControl: false, status: null, connected: false });
  }
  reprobeTimer = setTimeout(() => void probe(), REPROBE_MS);
}

function openSocket() {
  if (ws) return;
  let sock: WebSocket;
  try {
    sock = new WebSocket(roomApi().eventsUrl());
  } catch {
    scheduleReconnect();
    return;
  }
  ws = sock;
  sock.onopen = () => {
    wsAttempt = 0;
    set({ connected: true });
  };
  sock.onmessage = (m) => {
    let msg: { type?: string; data?: unknown };
    try {
      msg = JSON.parse(String(m.data));
    } catch {
      return;
    }
    if (msg.type === "status" && msg.data && typeof msg.data === "object") {
      set({ status: msg.data as RoomStatus });
    } else if (msg.type === "screen.phase" && msg.data && state.status?.screens) {
      const d = msg.data as { screen?: string; phase?: string; detail?: string | null };
      const cur = d.screen ? state.status.screens[d.screen] : undefined;
      if (cur && d.screen) {
        const screens = { ...state.status.screens, [d.screen]: { ...cur, observed: { ...cur.observed, phase: d.phase, detail: d.detail } } };
        set({ status: { ...state.status, screens } });
      }
    } else if (msg.type === "stream" && msg.data && typeof msg.data === "object") {
      streamListeners.forEach((fn) => fn(msg.data as StreamEvent));
    }
  };
  sock.onclose = () => {
    if (ws === sock) ws = null;
    set({ connected: false });
    scheduleReconnect();
  };
  sock.onerror = () => sock.close();
}

function scheduleReconnect() {
  const delay = WS_BACKOFF_MS[Math.min(wsAttempt, WS_BACKOFF_MS.length - 1)];
  wsAttempt += 1;
  // Re-probe rather than just reconnect: it refreshes the status and notices leaving the LAN.
  if (!reprobeTimer) reprobeTimer = setTimeout(() => void probe(), delay);
}

function start() {
  if (started) return;
  started = true;
  void probe();
}

function subscribe(fn: () => void) {
  start();
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function subscribeStream(fn: (e: StreamEvent) => void): () => void {
  streamListeners.add(fn);
  return () => streamListeners.delete(fn);
}

/** The shared room link. Mounting any user starts it; it stays up for the page's life. */
export function useRoomLink(): RoomLinkState {
  return useSyncExternalStore(subscribe, () => state);
}

// ── Addresses ─────────────────────────────────────────────────────────────────

const APP_LAN_URL = ((import.meta.env.VITE_APP_LAN_URL as string | undefined) ?? "").replace(/\/+$/, "");

/** The Room console: relative (/room), so the app stays on this address and keeps its state (the dock, the socket).
 *  Only a page on a host that can't reach the room (the probe said so) goes to the app's LAN address. */
export function roomPageUrl(screen?: string | null): string {
  const base = state.onLan === false && APP_LAN_URL && APP_LAN_URL !== window.location.origin ? APP_LAN_URL : "";
  return `${base}/room${screen ? `?screen=${encodeURIComponent(screen)}` : ""}`;
}

/** The renderer's developer console (Library, FX bus, sessions), through room_service's pass-through. */
export function rendererConsoleUrl(): string {
  return `${roomApi().baseUrl}/renderer/dev`;
}

export function roomStatusUrl(): string {
  return `${roomApi().baseUrl}/api/v1/status`;
}

// ── Screens ───────────────────────────────────────────────────────────────────

export type LiveState = "art" | "entering" | "live" | "leaving" | "failed";

const ENTERING: ReadonlySet<string> = new Set<Phase>(["freezing", "snapshot", "starting", "attaching", "entering"]);
const LEAVING: ReadonlySet<string> = new Set<Phase>(["exiting", "leaving"]);

export function isLiveCapable(s: ScreenStatus | undefined): boolean {
  return !!s && (s.policy?.live_enabled ?? s.live_enabled) === true;
}

export function wantsLive(s: ScreenStatus | undefined): boolean {
  return !!s && typeof s.desired === "object" && s.desired !== null && "live" in s.desired;
}

export function liveStateOf(s: ScreenStatus | undefined): LiveState {
  if (!s) return "art";
  const phase = String(s.observed?.phase ?? "idle");
  const want = wantsLive(s);
  if (phase === "failed") return "failed";
  if (phase === "live" || (want && s.observed?.layer === "live" && !LEAVING.has(phase))) return want ? "live" : "leaving";
  if (want) return "entering";
  if (LEAVING.has(phase) || (phase === "freezing" && s.observed?.layer === "live")) return "leaving";
  return "art";
}

/** The scene asked for (and, through cues and Auto, the one on glass), as a library key. */
export function liveSceneKey(s: ScreenStatus | undefined): string | null {
  if (!s || !wantsLive(s)) return null;
  return sceneKeyOf((s.desired as { live: { scene: SceneSpec } }).live.scene);
}

export function sceneKeyOf(spec: SceneSpec | undefined | null): string | null {
  if (!spec) return null;
  if (typeof spec === "string") return spec.includes(":") ? spec : `preset:${spec}`;
  if ("preset" in spec) return `preset:${spec.preset}`;
  if ("source" in spec) return `source:${spec.source}`;
  return null;
}

/** A library key back to the scene spec room_service takes. */
export function sceneSpecOf(key: string): SceneSpec {
  if (key.startsWith("source:")) return { source: key.slice(7) };
  return key.replace(/^preset:/, "");
}

// ── Library (scene names, thumbnails, palettes, effects): fetched once, on first need ──

let library: Library | null = null;
let libraryLoading: Promise<void> | null = null;
const libraryListeners = new Set<() => void>();

export function useRoomLibrary(enabled: boolean): Library | null {
  const [, bump] = useState(0);
  useEffect(() => {
    const fn = () => bump((n) => n + 1);
    libraryListeners.add(fn);
    if (enabled && !library && !libraryLoading) {
      libraryLoading = roomApi()
        .library()
        .then((lib) => {
          library = lib ?? null;
        })
        .catch(() => {
          /* the panel still works with plain names */
        })
        .finally(() => {
          libraryLoading = null;
          libraryListeners.forEach((f) => f());
        });
    }
    return () => {
      libraryListeners.delete(fn);
    };
  }, [enabled]);
  return library;
}
