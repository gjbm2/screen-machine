// The page's link to room_service (LIVE_V1 §3):
//   - the events socket WS /api/v1/events?token=…, reconnected with full-jitter
//     backoff random(0, min(10 s, 0.5 s·2^n));
//   - GET /api/v1/status every 2 s while that socket is down;
//   - the modes, the catalog and /healthz, re-read whenever the socket (re)opens.
//
// The page never sends anything on the events socket; the server only sends.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { explainError, RoomApi, RoomApiError } from "./api";
import type {
  Catalog,
  Library,
  LogEntry,
  LogLevel,
  ModeDef,
  ModeTransitionEventData,
  RoomEvent,
  RoomStatus,
  ScreenMask,
  TrackedTransition,
} from "./types";

const POLL_MS = 2_000;
/** §3 gives the events socket no heartbeat, so silence is normal; this HTTP check catches a dead peer. */
const HEALTH_MS = 15_000;
const BACKOFF_BASE_MS = 500;
const BACKOFF_CAP_MS = 10_000;
const LOG_MAX = 200;
/** screen.mask can arrive at ~10 Hz per screen; apply it to the UI at most this often. */
const MASK_FLUSH_MS = 250;
/** A failed modes/catalog read is retried this often (on the next status after it). */
const META_RETRY_MS = 10_000;
/** stream_service's plug-in rescans can come in bursts: re-read the library once they settle. */
const LIBRARY_REFRESH_MS = 2_000;

/** A stream_service event as room_service passes it through (`stream`). */
export type StreamEvent = Record<string, unknown>;

export type WsState = "idle" | "connecting" | "open" | "closed";

export interface ConnectionState {
  ws: WsState;
  /** True while the 2 s status poll is running (socket down). */
  polling: boolean;
  authRequired: boolean;
  /** Local time of the last status received by either path. */
  lastStatusAt: number | null;
  /** The last failure to reach room_service, cleared by the next success. */
  lastError: string | null;
  /** When the next socket attempt is due (local ms), while it is down. */
  reconnectAt: number | null;
  attempt: number;
  version: string | null;
}

const INITIAL_CONN: ConnectionState = {
  ws: "idle",
  polling: false,
  authRequired: false,
  lastStatusAt: null,
  lastError: null,
  reconnectAt: null,
  attempt: 0,
  version: null,
};

export type NewLogEntry = {
  type: string;
  summary: string;
  level?: LogLevel;
  screen?: string;
  data?: unknown;
};

function str(v: unknown): string {
  return v === undefined || v === null ? "" : String(v);
}

/** One line for a stream_service event passed through as `stream` (LIVE_V1 §1.3). */
function summariseStream(d: Record<string, unknown>): string {
  const t = str(d.type);
  const parts = [t, str(d.screen), str(d.session)];
  if (t === "session.state") parts.push(`→ ${str(d.state)}`);
  else if (t === "session.transition") parts.push(`${str(d.kind)} ${str(d.phase)} @${str(d.pts)}`);
  else if (t === "session.held") parts.push(`hold_pts ${str(d.hold_pts)}`);
  else if (t === "session.ended") parts.push(`(${str(d.reason)})`);
  else if (t === "session.error") parts.push(str(d.detail));
  return parts.filter(Boolean).join(" ");
}

export function useRoomConnection(baseUrl: string, token: string) {
  const api = useMemo(() => new RoomApi(baseUrl, token), [baseUrl, token]);

  const [status, setStatus] = useState<RoomStatus | null>(null);
  const [modes, setModes] = useState<Record<string, ModeDef> | null>(null);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  // The live controls' library (GET /api/v1/library). null until read; "absent" on a room_service without it.
  const [library, setLibrary] = useState<Library | null | "absent">(null);
  const streamSubs = useRef(new Set<(e: StreamEvent) => void>());
  const [modesError, setModesError] = useState<string | null>(null);
  const [conn, setConn] = useState<ConnectionState>(INITIAL_CONN);
  const [log, setLog] = useState<LogEntry[]>([]);
  const [statusCount, setStatusCount] = useState(0);
  const [transition, setTransition] = useState<TrackedTransition | null>(null);
  // Bumped to force a fresh connection (e.g. the same token saved again).
  const [nonce, setNonce] = useState(0);

  const logId = useRef(0);
  // Set by the connection effect: stops everything and shows the token prompt.
  const onUnauthorizedRef = useRef<() => void>(() => {});

  const patchConn = useCallback((p: Partial<ConnectionState>) => setConn((c) => ({ ...c, ...p })), []);

  const addLog = useCallback((e: NewLogEntry) => {
    const entry: LogEntry = {
      id: ++logId.current,
      t: Date.now(),
      type: e.type,
      screen: e.screen,
      level: e.level ?? "info",
      summary: e.summary,
      data: e.data,
      count: 1,
    };
    setLog((prev) => {
      // Coalesce mask pushes. Several screens push interleaved at up to 10 Hz each, so within
      // the run of screen.mask lines at the top, a push replaces that screen's line (moved to
      // the top) instead of adding one; otherwise they would flush everything else out of the
      // 200-line log within seconds.
      if (entry.type === "screen.mask") {
        for (let i = 0; i < prev.length && prev[i].type === "screen.mask"; i++) {
          if (prev[i].screen === entry.screen) {
            const merged = { ...entry, id: prev[i].id, count: prev[i].count + 1 };
            return [merged, ...prev.slice(0, i), ...prev.slice(i + 1)];
          }
        }
      }
      return [entry, ...prev].slice(0, LOG_MAX);
    });
  }, []);

  const clearLog = useCallback(() => setLog([]), []);

  /** For action handlers: a 401 anywhere means the token is wrong. */
  const reportError = useCallback((e: unknown) => {
    if (e instanceof RoomApiError && e.status === 401) onUnauthorizedRef.current();
  }, []);

  const reconnect = useCallback(() => setNonce((n) => n + 1), []);
  // Set by the connection effect: re-reads modes, catalog and status through that effect, so a
  // late reply from a previous server (or an older status than a socket push) is dropped.
  const manualRefreshRef = useRef<() => void>(() => {});

  useEffect(() => {
    // A new server or token: nothing from the previous one is trustworthy.
    setStatus(null);
    setModes(null);
    setCatalog(null);
    setLibrary(null);
    setCatalogError(null);
    setModesError(null);
    setTransition(null);
    setStatusCount(0);
    // No token is fine: operating the room is open on the LAN (LIVE_V1 §3). A 401 (setup edits,
    // or a server that still requires one) brings up the token prompt.
    setConn(INITIAL_CONN);

    let alive = true;
    let ws: WebSocket | null = null;
    let attempt = 0;
    let everOpened = false;
    let metaOk = false;
    let metaInFlight = false;
    /** performance.now() of the last modes/catalog read, for retrying a failed one. */
    let metaTriedAt = -Infinity;
    /** stream_service.reachable from the last status, to re-read the catalog when it comes back. */
    let streamReachable: boolean | undefined;
    let statusInFlight = false;
    /** performance.now() of the last status received on the socket. */
    let lastWsStatusAt = -1;
    /** performance.now() of the last message of any kind on the socket. */
    let lastWsMsgAt = -Infinity;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let pollTimer: ReturnType<typeof setInterval> | null = null;
    let healthTimer: ReturnType<typeof setInterval> | null = null;
    let maskTimer: ReturnType<typeof setTimeout> | null = null;
    let libraryTimer: ReturnType<typeof setTimeout> | null = null;
    const pendingMasks = new Map<string, ScreenMask>();
    const ac = new AbortController();

    const applyStatus = (s: RoomStatus) => {
      setStatus(s);
      patchConn({ lastStatusAt: Date.now(), lastError: null });
      const t = s?.mode?.transition;
      // Not in LIVE_V1 §3, read if present: room_service keeps the finished transition here
      // (transition goes null when it ends), with its real outcome.
      const last = s?.mode?.last_transition;
      setTransition((prev) => {
        if (t && t.id) {
          const finished = prev && prev.id === t.id && (prev.phase === "done" || prev.phase === "cancelled");
          return { ...t, phase: finished ? prev.phase : "running", finishedAt: finished ? prev.finishedAt : undefined };
        }
        if (last && last.id) {
          // Also covers a page opened after the transition ended, or one that missed the events.
          const seen = prev && prev.id === last.id ? prev.finishedAt : undefined;
          const at = seen ?? (last.finished_at ? Date.parse(last.finished_at) : NaN);
          return {
            ...last,
            phase: last.phase === "cancelled" ? "cancelled" : "done",
            finishedAt: Number.isFinite(at) ? at : Date.now(),
          };
        }
        // Status dropped the transition, so it is over. Keep it on screen with its outcome.
        if (prev && prev.phase === "running") return { ...prev, phase: "done", finishedAt: Date.now() };
        return prev;
      });
      // stream_service came back: its catalog may have changed (or never loaded).
      const reachable = s?.stream_service?.reachable;
      const cameBack = reachable === true && streamReachable === false;
      streamReachable = reachable;
      maybeRefreshMeta(cameBack);
    };

    const stopAll = () => {
      alive = false;
      ac.abort();
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (pollTimer) clearInterval(pollTimer);
      if (healthTimer) clearInterval(healthTimer);
      if (maskTimer) clearTimeout(maskTimer);
      if (libraryTimer) clearTimeout(libraryTimer);
      reconnectTimer = pollTimer = healthTimer = maskTimer = libraryTimer = null;
      if (ws) {
        const sock = ws;
        sock.onmessage = sock.onclose = sock.onerror = null;
        // Closing a socket that is still connecting logs a browser warning; close it once it opens instead.
        if (sock.readyState === WebSocket.CONNECTING) sock.onopen = () => sock.close();
        else {
          sock.onopen = null;
          sock.close();
        }
        ws = null;
      }
    };

    onUnauthorizedRef.current = () => {
      if (!alive) return;
      stopAll();
      setConn((c) => ({ ...c, ws: "closed", polling: false, authRequired: true, reconnectAt: null }));
      addLog({ type: "conn", level: "error", summary: "room_service refused the token (401)" });
    };

    /** Resolves to the failure, or null on success (or when a read is already running). */
    const refreshStatus = async (): Promise<RoomApiError | Error | null> => {
      if (statusInFlight) return null;
      statusInFlight = true;
      const startedAt = performance.now();
      try {
        const s = await api.status(ac.signal);
        if (!alive) return null;
        // A socket push that arrived while this read was in flight is newer; keep it.
        if (lastWsStatusAt > startedAt) patchConn({ lastStatusAt: Date.now(), lastError: null });
        else applyStatus(s);
        return null;
      } catch (e) {
        if (!alive) return null;
        if (e instanceof RoomApiError && e.status === 401) {
          onUnauthorizedRef.current();
          return e;
        }
        patchConn({ lastError: explainError(e) });
        return e instanceof Error ? e : new Error(String(e));
      } finally {
        statusInFlight = false;
      }
    };

    /** The library is optional: an older room_service has no /api/v1/library (404), and the page falls back. */
    const refreshLibrary = async () => {
      try {
        const lib = await api.library(ac.signal);
        if (alive) setLibrary(lib && Array.isArray(lib.scenes) ? lib : "absent");
      } catch (e) {
        if (!alive) return;
        if (e instanceof RoomApiError && e.status === 404) setLibrary("absent");
        // else keep the last good one; the next meta refresh retries
      }
    };

    const refreshMeta = async () => {
      void refreshLibrary();
      metaInFlight = true;
      metaTriedAt = performance.now();
      const [m, c, h] = await Promise.allSettled([api.modes(ac.signal), api.catalog(ac.signal), api.healthz(ac.signal)]);
      metaInFlight = false;
      if (!alive) return;
      if (m.status === "fulfilled") {
        setModes(m.value?.modes ?? {});
        setModesError(null);
      } else {
        // Keep the last good modes, but say the read failed (else "Loading modes…" forever).
        setModesError(explainError(m.reason));
        reportError(m.reason);
      }
      if (c.status === "fulfilled") {
        setCatalog(c.value ?? {});
        setCatalogError(null);
      } else {
        // Keep the last good catalog: the pickers stay usable if stream_service blips.
        setCatalogError(explainError(c.reason));
        reportError(c.reason);
      }
      if (h.status === "fulfilled") patchConn({ version: h.value?.version ?? null });
      metaOk = m.status === "fulfilled" && c.status === "fulfilled";
    };

    /**
     * Re-reads modes and catalog if the last read failed (e.g. stream_service was down, so the
     * catalog 502'd), at most every META_RETRY_MS; or at once when `force`. Without this the
     * pickers stayed empty until the person pressed Refresh.
     */
    const maybeRefreshMeta = (force: boolean) => {
      if (!alive || metaInFlight) return;
      if (force || (!metaOk && performance.now() - metaTriedAt > META_RETRY_MS)) void refreshMeta();
    };

    manualRefreshRef.current = () => {
      if (!alive) return;
      if (!metaInFlight) void refreshMeta();
      void refreshStatus();
    };

    const startPolling = () => {
      if (pollTimer || !alive) return;
      patchConn({ polling: true });
      pollTimer = setInterval(() => void refreshStatus(), POLL_MS);
    };

    const stopPolling = () => {
      if (pollTimer) clearInterval(pollTimer);
      pollTimer = null;
      patchConn({ polling: false });
    };

    const flushMasks = () => {
      maskTimer = null;
      if (!pendingMasks.size) return;
      const batch = new Map(pendingMasks);
      pendingMasks.clear();
      setStatus((prev) => {
        if (!prev?.screens) return prev;
        const screens = { ...prev.screens };
        for (const [id, m] of batch) {
          if (screens[id]) screens[id] = { ...screens[id], mask: { ...(screens[id].mask ?? {}), ...m } };
        }
        return { ...prev, screens };
      });
    };

    const handleEvent = (ev: RoomEvent) => {
      const d = (ev.data ?? {}) as Record<string, unknown>;
      switch (ev.type) {
        case "status":
          if (!ev.data || typeof ev.data !== "object") break; // never replace the status with nothing
          lastWsStatusAt = performance.now();
          applyStatus(ev.data as RoomStatus);
          setStatusCount((n) => n + 1);
          break;
        case "mode.transition": {
          const td = d as ModeTransitionEventData;
          const step = td.step;
          addLog({
            type: ev.type,
            level: step?.status === "failed" ? "error" : step?.status === "unconfirmed" ? "warn" : "info",
            summary: step
              ? `${td.id ?? ""} step ${step.subsystem} ${step.action}: ${step.status}${step.detail ? ` (${step.detail})` : ""}`
              : `${td.id ?? ""} ${td.phase ?? ""}`,
            data: ev.data,
          });
          // Steps carry an id (room_service); match on it, else on subsystem + action. A step not
          // seen yet (the started event has only a step count) is appended; the next status
          // push brings the full list anyway.
          const patchSteps = <T extends { id?: string; steps?: TrackedTransition["steps"] }>(t: T): T => {
            if (!step || typeof step !== "object") return t;
            const steps = t.steps ?? [];
            const i = steps.findIndex((s) =>
              step.id && s.id ? s.id === step.id : s.subsystem === step.subsystem && s.action === step.action,
            );
            return {
              ...t,
              steps: i >= 0 ? steps.map((s, j) => (j === i ? { ...s, ...step } : s)) : [...steps, step],
            };
          };
          setTransition((prev) => {
            if (!prev || prev.id !== td.id) {
              return td.phase === "started" && td.id
                ? {
                    id: td.id,
                    from: td.from ?? null,
                    to: td.to ?? null,
                    steps: [],
                    phase: "running",
                    ...(td.source !== undefined ? { source: td.source } : {}),
                    ...(td.reason !== undefined ? { reason: td.reason } : {}),
                    ...(td.skip !== undefined ? { skip: td.skip } : {}),
                  }
                : prev;
            }
            const next = patchSteps(prev);
            if (td.phase === "done" || td.phase === "cancelled")
              return {
                ...next,
                phase: td.phase,
                finishedAt: Date.now(),
                ...(td.status !== undefined ? { status: td.status } : {}),
                ...(td.detail !== undefined ? { detail: td.detail } : {}),
              };
            return next;
          });
          setStatus((prev) => {
            const t = prev?.mode?.transition;
            if (!prev || !t || t.id !== td.id) return prev;
            return { ...prev, mode: { ...prev.mode, transition: patchSteps(t) } };
          });
          break;
        }
        case "screen.phase": {
          const screen = str(d.screen);
          addLog({
            type: ev.type,
            screen,
            level: d.phase === "failed" ? "error" : "info",
            summary: `${screen} → ${str(d.phase)}${d.detail ? ` (${str(d.detail)})` : ""}`,
            data: ev.data,
          });
          setStatus((prev) => {
            const sc = prev?.screens?.[screen];
            if (!sc) return prev;
            const observed = { ...(sc.observed ?? {}), phase: str(d.phase) };
            return { ...prev, screens: { ...prev.screens, [screen]: { ...sc, observed } } };
          });
          break;
        }
        case "screen.mask": {
          const screen = str(d.screen);
          const { screen: _s, ...mask } = d;
          pendingMasks.set(screen, { ...(pendingMasks.get(screen) ?? {}), ...(mask as ScreenMask) });
          if (!maskTimer) maskTimer = setTimeout(flushMasks, MASK_FLUSH_MS);
          const b = typeof d.brightness === "number" ? d.brightness.toFixed(3) : str(d.brightness);
          addLog({ type: ev.type, screen, level: "debug", summary: `${screen} brightness ${b} warm ${str(d.warm_hex)} α${str(d.warm_alpha)}`, data: ev.data });
          break;
        }
        case "stream":
          if (d.type === "catalog.changed" && !libraryTimer) {
            libraryTimer = setTimeout(() => {
              libraryTimer = null;
              void refreshLibrary();
            }, LIBRARY_REFRESH_MS);
          }
          for (const fn of streamSubs.current) {
            try {
              fn(d);
            } catch {
              /* a card's handler must not break the socket */
            }
          }
          addLog({
            type: ev.type,
            screen: str(d.screen) || undefined,
            level: d.type === "session.error" ? "error" : "info",
            summary: summariseStream(d),
            data: ev.data,
          });
          break;
        case "log": {
          const lvl = str(d.level).toLowerCase();
          const level: LogLevel = lvl.startsWith("err") ? "error" : lvl.startsWith("warn") ? "warn" : lvl === "debug" ? "debug" : "info";
          addLog({ type: ev.type, level, summary: str(d.msg), data: ev.data });
          break;
        }
        default:
          addLog({ type: ev.type || "unknown", level: "debug", summary: JSON.stringify(ev.data ?? null).slice(0, 200), data: ev.data });
      }
    };

    const scheduleReconnect = () => {
      if (!alive) return;
      // Full jitter: random(0, min(cap, base·2^n)).
      const ceiling = Math.min(BACKOFF_CAP_MS, BACKOFF_BASE_MS * 2 ** attempt);
      attempt += 1;
      const delay = Math.random() * ceiling;
      patchConn({ reconnectAt: Date.now() + delay, attempt });
      reconnectTimer = setTimeout(connect, delay);
    };

    const startHealth = () => {
      if (healthTimer) return;
      healthTimer = setInterval(async () => {
        const err = await refreshStatus();
        // Can't reach room_service over HTTP and the socket has been silent too: presume it is
        // dead. A socket that is still delivering stays up (HTTP can fail on its own, e.g. a
        // CORS refusal, and closing a working socket would only make the page flap).
        if (err instanceof RoomApiError && err.status === 0 && ws && performance.now() - lastWsMsgAt > HEALTH_MS) {
          ws.close();
        }
      }, HEALTH_MS);
    };

    const connect = () => {
      reconnectTimer = null;
      if (!alive) return;
      patchConn({ ws: "connecting", reconnectAt: null });
      let opened = false;
      let sawMessage = false;
      let sock: WebSocket;
      try {
        sock = new WebSocket(api.eventsUrl());
      } catch (e) {
        patchConn({ ws: "closed", lastError: explainError(e) });
        startPolling();
        scheduleReconnect();
        return;
      }
      ws = sock;
      sock.onopen = () => {
        opened = true;
        stopPolling();
        patchConn({ ws: "open" });
        addLog({ type: "conn", summary: "events socket connected" });
        // The first open follows the initial read; re-read after a reconnect, or if that read failed.
        if (everOpened || (!metaOk && !metaInFlight)) void refreshMeta();
        everOpened = true;
        startHealth();
      };
      sock.onmessage = (msg) => {
        lastWsMsgAt = performance.now();
        if (!sawMessage) {
          // A message, not just an open, proves the socket works: reset the backoff.
          sawMessage = true;
          attempt = 0;
        }
        if (typeof msg.data !== "string") return;
        let ev: RoomEvent;
        try {
          ev = JSON.parse(msg.data);
        } catch {
          return;
        }
        if (ev && typeof ev.type === "string") handleEvent(ev);
      };
      sock.onerror = () => {
        /* onclose follows */
      };
      sock.onclose = (e) => {
        if (ws === sock) ws = null;
        if (healthTimer) clearInterval(healthTimer);
        healthTimer = null;
        if (!alive) return;
        patchConn({ ws: "closed" });
        if (opened) addLog({ type: "conn", level: "warn", summary: `events socket closed (code ${e.code}); polling status every 2 s` });
        startPolling();
        scheduleReconnect();
      };
    };

    // A phone back from the background, or the network back: read status at once, and reconnect now rather than wait
    // out the backoff (a socket still connecting is dropped first, so there's never two).
    const wake = () => {
      if (!alive) return;
      void refreshStatus();
      if (ws && ws.readyState === WebSocket.OPEN) return;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      reconnectTimer = null;
      if (ws) {
        const old = ws;
        old.onopen = old.onmessage = old.onclose = old.onerror = null;
        if (old.readyState === WebSocket.CONNECTING) old.onopen = () => old.close();
        else old.close();
        ws = null;
      }
      connect();
    };
    const onVisibility = () => {
      if (document.visibilityState === "visible") wake();
    };
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("online", wake);

    // Polling covers the gap until the socket opens; the first poll paints the page.
    void refreshStatus();
    void refreshMeta();
    startPolling();
    connect();

    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("online", wake);
      stopAll();
      onUnauthorizedRef.current = () => {};
      manualRefreshRef.current = () => {};
    };
  }, [api, token, nonce, addLog, patchConn, reportError]);

  /** Stream events (passed through by room_service) for the live cards; returns the unsubscribe. */
  const subscribeStream = useCallback((fn: (e: StreamEvent) => void) => {
    const subs = streamSubs.current;
    subs.add(fn);
    return () => {
      subs.delete(fn);
    };
  }, []);

  /** Re-reads modes, catalog and status on demand (e.g. after editing presets). */
  const refreshMeta = useCallback(() => manualRefreshRef.current(), []);

  return {
    api,
    status,
    modes,
    modesError,
    catalog,
    catalogError,
    library: library === "absent" ? null : library,
    libraryAbsent: library === "absent",
    subscribeStream,
    conn,
    log,
    statusCount,
    transition,
    addLog,
    clearLog,
    reportError,
    reconnect,
    refreshMeta,
  };
}

export type RoomConnection = ReturnType<typeof useRoomConnection>;
