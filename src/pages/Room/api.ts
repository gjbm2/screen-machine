// room_service HTTP client (LIVE_V1 §3), plus where the page finds room_service
// and its token.

import type {
  Catalog,
  RendererDiag,
  RendererState,
  CueBody,
  Desired,
  FxBus,
  FixturesView,
  FxNextBody,
  FxNextResult,
  FxPut,
  HueValue,
  Library,
  Look,
  LookPatch,
  ModeDef,
  Params,
  RawRoomConfig,
  RemoteDevice,
  RemoteLearn,
  RemoteLightState,
  RemotesView,
  RoomDescribe,
  RoomStatus,
  SceneSpec,
  SessionView,
  ShowActionResult,
  ShowCard,
  ShowDescribeResult,
  ShowInputs,
  ShowMicState,
  ShowListeningView,
  ShowStepResult,
  ShowTestSource,
  ShowTestStartOpts,
  ShowTestState,
  ShowTestView,
  ShowUndoResult,
  PianoRecording,
  PianoRecordAsk,
  PianoRecordingsView,
  TestPianoView,
  TestShowView,
  TransitionSpec,
  UpNextEdit,
  UpNextEditResult,
  UpNextPlan,
} from "./types";

export const URL_KEY = "sm_room_url";
export const TOKEN_KEY = "sm_room_token";
/** The room_service URL the saved token was entered for (see loadToken). */
export const TOKEN_URL_KEY = "sm_room_token_url";

// localStorage can throw (private mode, blocked storage); the page must still work.
export function storageGet(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function storageSet(key: string, value: string | null): void {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    /* ignore */
  }
}

/** room_service's LAN address, from VITE_ROOM_URL in .env (baked in at build time). */
export function defaultRoomUrl(): string {
  // Behind the friendly HTTPS name (Caddy on PC22), room_service is same-origin under /rs: an https page can't
  // call http://…:8700 (mixed content).
  if (window.location.protocol === "https:") return `${window.location.origin}/rs`;
  const env = import.meta.env.VITE_ROOM_URL as string | undefined;
  return env ? env.replace(/\/+$/, "") : `${window.location.protocol}//${window.location.hostname}:8700`;
}

/** Trims whitespace and trailing slashes; adds http:// if no scheme was given. Returns null if unusable. */
export function normaliseRoomUrl(raw: string): string | null {
  let s = raw.trim().replace(/\/+$/, "");
  if (!s) return null;
  if (!/^https?:\/\//i.test(s)) s = `http://${s}`;
  try {
    const u = new URL(s);
    return `${u.protocol}//${u.host}${u.pathname.replace(/\/+$/, "")}`;
  } catch {
    return null;
  }
}

/**
 * The base URL, in priority order: ?room=<url> (which is also remembered),
 * then the remembered sm_room_url, then the default.
 */
export function resolveRoomUrl(search: string): string {
  const fromQuery = new URLSearchParams(search).get("room");
  if (fromQuery) {
    const n = normaliseRoomUrl(fromQuery);
    if (n) {
      storageSet(URL_KEY, n);
      return n;
    }
  }
  const stored = storageGet(URL_KEY);
  const n = stored ? normaliseRoomUrl(stored) : null;
  return n ?? defaultRoomUrl();
}

/**
 * The saved token, if it may be sent to `baseUrl`.
 *
 * ?room= comes from the address bar, so a crafted link could point the page at
 * another host. The token therefore goes only to the server it was entered for.
 * A token saved with no binding (e.g. seeded by a test) is used only for a
 * room_service on this page's own hostname. Otherwise the page asks again and
 * `foreign` says which server the saved token belongs to.
 */
export function loadToken(baseUrl: string): { token: string; foreign: string | null } {
  const token = storageGet(TOKEN_KEY) ?? "";
  if (!token) return { token: "", foreign: null };
  const boundTo = storageGet(TOKEN_URL_KEY);
  let sameHost = false;
  try {
    sameHost = new URL(baseUrl).hostname === window.location.hostname;
  } catch {
    /* unusable URL */
  }
  if (boundTo ? boundTo === baseUrl : sameHost) return { token, foreign: null };
  return { token: "", foreign: boundTo ?? "this page's own host" };
}

export function saveToken(baseUrl: string, token: string): void {
  storageSet(TOKEN_KEY, token);
  storageSet(TOKEN_URL_KEY, baseUrl);
}

export class RoomApiError extends Error {
  /** HTTP status, or 0 for a network failure. */
  status: number;
  /** The contract's `error` code (e.g. "live_disabled"), or a local one. */
  code: string;
  detail: string;
  /** An invalid config's problems ([{path, message}]), when the server lists them. */
  errors?: { path: string; message: string }[];

  constructor(status: number, code: string, detail: string, errors?: { path: string; message: string }[]) {
    super(detail ? `${code}: ${detail}` : code);
    this.status = status;
    this.code = code;
    this.detail = detail;
    this.errors = errors;
  }
}

/** Plain-language explanations for the error codes a person is likely to hit. */
export function explainError(err: unknown): string {
  if (!(err instanceof RoomApiError)) return err instanceof Error ? err.message : String(err);
  switch (err.code) {
    case "live_disabled":
      return "Live is turned off for this screen. The owner turns it on in the room's setup.";
    case "not_live":
      return err.detail.includes("cut is under way")
        ? "The screen is changing over. Try again in a moment."
        : "The screen isn't live just now.";
    case "fx_conflict":
      return "Someone else changed the effects at the same moment. Try again.";
    case "unknown_preset":
      return `That isn't in the library any more${err.detail ? ` (${err.detail.split(" (known")[0]})` : ""}.`;
    case "too_many_effects":
      return "That's as many effects as the screen can run at once.";
    case "no_effect":
      return "There's no other effect to bring in.";
    case "auto_unavailable":
      return "This screen can't pick by itself just now; use Change scene.";
    case "bad_state":
      return "The screen is changing over. Try again in a moment.";
    case "unauthorized":
      return "The room token was refused. Enter it again.";
    case "stream_unreachable":
      return `room_service can't reach stream_service${err.detail ? ` (${err.detail})` : ""}.`;
    case "network":
      // fetch() reports a CORS refusal (this page's origin missing from room_service's
      // web_origins) exactly like a network failure, so name both causes.
      return (
        `Can't reach room_service (${err.detail || "network error"}). If it is running, check that this ` +
        `page's origin, ${window.location.origin}, is one room_service accepts.`
      );
  }
  if (err.status === 409) {
    return `Refused in the screen's current state (409 ${err.code})${err.detail ? `: ${err.detail}` : "."}`;
  }
  if (err.status === 400) return `Rejected (400 ${err.code})${err.detail ? `: ${err.detail}` : ""}`;
  return err.message;
}

/** A request that hasn't answered in this long counts as a network failure (keeps polls from piling up). */
const REQUEST_TIMEOUT_MS = 10_000;
/** Snapshots are 4K PNGs; give them longer. */
const SNAPSHOT_TIMEOUT_MS = 30_000;

export interface LivePatch {
  scene?: SceneSpec;
  params?: Params;
  transition?: TransitionSpec;
}

/**
 * Thin fetch wrapper. Operating the room is open on the LAN (LIVE_V1 §3, owner 13 Oct): the bearer
 * token is sent only when one has been entered, and is needed only for setup edits (a 401 asks for it).
 */
export class RoomApi {
  readonly baseUrl: string;
  private readonly token: string;

  constructor(baseUrl: string, token: string) {
    this.baseUrl = baseUrl;
    this.token = token;
  }

  /** The events socket can't send headers, so a token (if any) goes in ?token= (LIVE_V1 §3). */
  eventsUrl(): string {
    const ws = this.baseUrl.replace(/^http/i, "ws");
    const q = this.token ? `?token=${encodeURIComponent(this.token)}` : "";
    return `${ws}/api/v1/events${q}`;
  }

  /**
   * One request: sends, checks the status, and reads the body with `read`,
   * all under one timeout and the caller's abort signal.
   */
  private async request<T>(
    method: string,
    path: string,
    read: (res: Response) => Promise<T>,
    opts: { body?: unknown; signal?: AbortSignal; timeoutMs?: number } = {},
  ): Promise<T> {
    const { body, signal, timeoutMs = REQUEST_TIMEOUT_MS } = opts;
    const headers: Record<string, string> = this.token ? { Authorization: `Bearer ${this.token}` } : {};
    if (body !== undefined) headers["Content-Type"] = "application/json";
    // One controller for both the caller's abort and our timeout.
    const ac = new AbortController();
    const onAbort = () => ac.abort();
    signal?.addEventListener("abort", onAbort);
    if (signal?.aborted) ac.abort();
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      ac.abort();
    }, timeoutMs);
    try {
      const res = await fetch(`${this.baseUrl}${path}`, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: ac.signal,
        cache: "no-cache",
      });
      if (!res.ok) {
        // Errors are {"error": code, "detail": "…"} (LIVE_V1 §0); tolerate anything else.
        let code = res.status === 401 ? "unauthorized" : `http_${res.status}`;
        let detail = "";
        let errors: { path: string; message: string }[] | undefined;
        try {
          const j = await res.json();
          if (j && typeof j.error === "string") code = j.error;
          if (j && typeof j.detail === "string") detail = j.detail;
          if (j && Array.isArray(j.errors)) errors = j.errors;
        } catch {
          /* not JSON */
        }
        throw new RoomApiError(res.status, code, detail, errors);
      }
      return await read(res);
    } catch (e) {
      if (e instanceof RoomApiError) throw e;
      if (timedOut) throw new RoomApiError(0, "network", `no reply in ${timeoutMs / 1000} s`);
      if (e instanceof DOMException && e.name === "AbortError") throw e;
      if (e instanceof SyntaxError) throw new RoomApiError(502, "bad_json", e.message);
      throw new RoomApiError(0, "network", e instanceof Error ? e.message : String(e));
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
    }
  }

  private json<T>(method: string, path: string, body?: unknown, signal?: AbortSignal, timeoutMs?: number): Promise<T> {
    return this.request(
      method,
      path,
      async (res) => {
        const text = res.status === 204 ? "" : await res.text();
        return (text ? JSON.parse(text) : undefined) as T;
      },
      { body, signal, timeoutMs },
    );
  }

  /** The PC's load and the live screens' frame rates, for the header's "PC busy" (GET /renderer/api/diag and
   *  /renderer/api/state through room_service's pass-through). */
  async pcLoad(signal?: AbortSignal): Promise<{ diag: RendererDiag; state: RendererState }> {
    const [diag, state] = await Promise.all([
      this.json<RendererDiag>("GET", "/renderer/api/diag", undefined, signal, 8_000),
      this.json<RendererState>("GET", "/renderer/api/state", undefined, signal, 8_000),
    ]);
    return { diag, state };
  }

  healthz(signal?: AbortSignal) {
    return this.json<{ ok: boolean; version?: string }>("GET", "/healthz", undefined, signal);
  }

  status(signal?: AbortSignal) {
    return this.json<RoomStatus>("GET", "/api/v1/status", undefined, signal);
  }

  modes(signal?: AbortSignal) {
    return this.json<{ modes?: Record<string, ModeDef> }>("GET", "/api/v1/modes", undefined, signal);
  }

  catalog(signal?: AbortSignal) {
    return this.json<Catalog>("GET", "/api/v1/catalog", undefined, signal);
  }

  /** ``via``: which control asked (room_service logs it as the switch's source, "web:<via>"), so a switch nobody
   *  remembers making can be traced to the button that sent it. */
  setMode(mode: string, via: string) {
    return this.json<{ transition_id?: string }>("PUT", "/api/v1/mode", { mode, source: `web:${via}` });
  }

  setDesired(screen: string, desired: Desired) {
    return this.json<unknown>("PUT", `/api/v1/screens/${encodeURIComponent(screen)}`, { desired });
  }

  patchLive(screen: string, patch: LivePatch) {
    return this.json<unknown>("PATCH", `/api/v1/screens/${encodeURIComponent(screen)}/live`, patch);
  }

  // ── Live controls (room_service live_api.py) ──

  library(signal?: AbortSignal) {
    return this.json<Library>("GET", "/api/v1/library", undefined, signal);
  }

  /** A thumbnail's URL (an <img> src; the route is open, so no token goes in it). */
  thumbUrl(path: string | null | undefined): string | null {
    return path && path.startsWith("/api/v1/thumb/") ? `${this.baseUrl}${path}` : null;
  }

  look(screen: string, signal?: AbortSignal) {
    return this.json<Look>("GET", `/api/v1/screens/${encodeURIComponent(screen)}/look`, undefined, signal);
  }

  patchLook(screen: string, patch: LookPatch) {
    return this.json<Look>("PATCH", `/api/v1/screens/${encodeURIComponent(screen)}/look`, patch);
  }

  cue(screen: string, body: CueBody) {
    return this.json<{ cue_id?: string; skipped?: boolean }>("POST", `/api/v1/screens/${encodeURIComponent(screen)}/cue`, body);
  }

  /** The live diagnostics panel's lines for a session (docs/LIVE_DIAGNOSTICS.md), through the /renderer pass-through. */
  hud(session: string, signal?: AbortSignal) {
    return this.json<{ on?: boolean; failed?: string | null; at?: number | string | null; lines?: string[]; warn?: number[] }>(
      "GET",
      `/renderer/api/sessions/${encodeURIComponent(session)}/hud`,
      undefined,
      signal,
    );
  }

  /** The renderer's "next effect" for a live session (owner, 16 Oct): swaps the oldest auto effect for a fresh pick, or
   *  adds one; hand-placed effects stay. Through the /renderer pass-through; in a room the leader moves and the
   *  followers mirror it. `body` (screen-machine-04, 17 Oct) chooses the effect or the slot; an older renderer ignores it. */
  nextEffect(session: string, body: FxNextBody = {}) {
    return this.json<FxNextResult>("POST", `/renderer/api/sessions/${encodeURIComponent(session)}/fx/next`, body);
  }

  /** Take effects off every screen in the session's room (screen-machine-04, 17 Oct): all, or {effect: a preset or a
   *  slot id}. A renderer without the route answers 404 (see roomFx.ts). */
  fxClear(session: string, body: Record<string, unknown> = {}) {
    return this.json<{ cleared?: string[]; stack?: unknown }>(
      "POST",
      `/renderer/api/sessions/${encodeURIComponent(session)}/fx/clear`,
      body,
    );
  }

  /** The live session's whole summary from the renderer: the look's fields plus its room and its Up next. */
  session(session: string, signal?: AbortSignal) {
    return this.json<SessionView>("GET", `/renderer/api/sessions/${encodeURIComponent(session)}`, undefined, signal);
  }

  /** Up next (owner, 16 Oct): the art director's forward plan for a live session, through the /renderer
   *  pass-through. In a room either screen's session answers with the leader's plan. */
  upnext(session: string, signal?: AbortSignal) {
    return this.json<UpNextPlan>("GET", `/renderer/api/sessions/${encodeURIComponent(session)}/upnext`, undefined, signal);
  }

  /** Change the plan ({op: add | play | move | keep | set | remove | reroll, ...}); answers with the new plan. */
  upnextEdit(session: string, body: UpNextEdit) {
    return this.json<UpNextEditResult>("POST", `/renderer/api/sessions/${encodeURIComponent(session)}/upnext`, body);
  }

  fx(screen: string, signal?: AbortSignal) {
    return this.json<FxBus>("GET", `/api/v1/screens/${encodeURIComponent(screen)}/fx`, undefined, signal);
  }

  putFx(screen: string, body: FxPut) {
    return this.json<FxBus>("PUT", `/api/v1/screens/${encodeURIComponent(screen)}/fx`, body);
  }

  inputs(screen: string, signal?: AbortSignal) {
    return this.json<Record<string, unknown>>("GET", `/api/v1/screens/${encodeURIComponent(screen)}/inputs`, undefined, signal);
  }

  // ── Virtual DMX fixtures (room_service dmx/api.py) ──

  /** One row per patched device; null when this room_service has no fixtures route (404). */
  async fixtures(signal?: AbortSignal): Promise<FixturesView | null> {
    try {
      return await this.json<FixturesView>("GET", "/api/v1/fixtures", undefined, signal);
    } catch (e) {
      if (e instanceof RoomApiError && e.status === 404) return null;
      throw e;
    }
  }

  /** Set room targets by hand (ROOM_STATE_MODEL §4.11), e.g. {"hue.ne": "dmx"}. Open on the LAN. */
  setTargets(values: Record<string, HueValue | unknown>) {
    return this.json<{ accepted: string[] }>("PUT", "/api/v1/room/targets", { values, source: "web" });
  }

  /** The modes config as stored (modes.json) and its revision, for the mode editor. */
  roomConfig(signal?: AbortSignal) {
    return this.json<{ config: RawRoomConfig; revision: string; error: string | null }>(
      "GET",
      "/api/v1/room/config",
      undefined,
      signal,
    );
  }

  /** Save the whole modes config; refused (409) if someone saved since `baseRevision`, (400) if invalid. */
  saveRoomConfig(config: RawRoomConfig, baseRevision: string, note: string) {
    return this.json<{ revision: string }>("PUT", "/api/v1/room/config", { config, base_revision: baseRevision, note });
  }

  /** Every target's label and value schema, and every mode's resolved entries (the modes.json view). */
  describe(signal?: AbortSignal) {
    return this.json<RoomDescribe>("GET", "/api/v1/room/describe", undefined, signal);
  }

  /** Tell the room what an edge device is doing, without sending anything (a toggle remote after a restart). */
  assume(target: string, value: unknown) {
    return this.json<{ target: string; value: unknown }>("POST", "/api/v1/room/assume", {
      target,
      value,
      cause: "said on the Room page",
    });
  }

  // ── Remote-controlled devices on the RM4 Pro (room_service remotes_api.py) ──

  /** Every appliance added (its buttons learned or not), what can be added, the RM4 Pros and rooms; null when this
   *  room_service has no such route. */
  async remotes(signal?: AbortSignal): Promise<RemotesView | null> {
    try {
      return await this.json<RemotesView>("GET", "/api/v1/remotes", undefined, signal);
    } catch (e) {
      if (e instanceof RoomApiError && e.status === 404) return null;
      throw e;
    }
  }

  /** Add an appliance of a type to a room (host: the RM4 Pro, when the room doesn't know one yet). */
  addRemote(model: string, label: string, room: string, host?: string) {
    return this.json<RemoteDevice>("POST", "/api/v1/remotes", { model, label, room, ...(host ? { host } : {}) });
  }

  /** Remove an appliance: its learned buttons and its card go too. Refused (409) while a mode sets it. */
  removeRemote(remote: string) {
    return this.json<{ removed: string }>("DELETE", `/api/v1/remotes/${encodeURIComponent(remote)}`);
  }

  /** Start learning one button (the RM4 Pro listens for up to a minute); sweep finds an RF remote's frequency again. */
  learnButton(remote: string, button: string, sweep = false) {
    return this.json<RemoteLearn>("POST", `/api/v1/remotes/${encodeURIComponent(remote)}/learn`, { button, sweep });
  }

  learnProgress(remote: string, signal?: AbortSignal) {
    return this.json<RemoteLearn>("GET", `/api/v1/remotes/${encodeURIComponent(remote)}/learn`, undefined, signal);
  }

  stopLearning(remote: string) {
    return this.json<RemoteLearn>("DELETE", `/api/v1/remotes/${encodeURIComponent(remote)}/learn`);
  }

  /** Transmit a learned button (a light's record follows it). */
  sendButton(remote: string, button: string, times = 1) {
    return this.json<{ status: string; detail: string; state?: RemoteLightState }>(
      "POST",
      `/api/v1/remotes/${encodeURIComponent(remote)}/send`,
      { button, times },
      undefined,
      30_000,
    );
  }

  /** A light's reset after it lost power at the wall: on, white, dimmed to the floor, off. */
  resetLight(remote: string) {
    return this.json<{ status: string; detail: string; state?: RemoteLightState }>(
      "POST",
      `/api/v1/remotes/${encodeURIComponent(remote)}/reset`,
      {},
      undefined,
      30_000,
    );
  }

  /** Rename a light's colours ({colour id: name}); their ids stay, so modes keep working. */
  nameColours(remote: string, colours: Record<string, string>) {
    return this.json<RemoteDevice>("PUT", `/api/v1/remotes/${encodeURIComponent(remote)}/colours`, { colours });
  }

  identify(fixture: string) {
    return this.json<{ ok: boolean }>("POST", `/api/v1/fixtures/${encodeURIComponent(fixture)}/identify`, {});
  }

  /** The test show (fake Art-Net); null when this room_service has no such route (404). */
  async testShow(signal?: AbortSignal): Promise<TestShowView | null> {
    try {
      return await this.json<TestShowView>("GET", "/api/v1/dmx/fake", undefined, signal);
    } catch (e) {
      if (e instanceof RoomApiError && e.status === 404) return null;
      throw e;
    }
  }

  /** {action: "start" | "stop"}, or a control while it plays: {bpm}, {song}, {pulses}, {next_section: true}. */
  testShowSend(body: Record<string, unknown>) {
    return this.json<TestShowView>("POST", "/api/v1/dmx/fake", body);
  }

  /** show_service's test sources (through room_service's /show pass-through); null until that exists or while
   *  show_service is down (404 / 405 / 502 / 503). */
  async showTest(signal?: AbortSignal): Promise<ShowTestView | null> {
    try {
      const v = await this.json<ShowTestView>("GET", "/show/api/v1/test", undefined, signal);
      return v && v.sources && v.pieces ? v : null;
    } catch (e) {
      if (e instanceof RoomApiError && [404, 405, 502, 503].includes(e.status)) return null;
      throw e;
    }
  }

  /** Start (or switch) a test source: {source: "audio" | "piano", piece: an id from showTest().pieces}. */
  showTestStart(source: ShowTestSource, piece: string, opts?: ShowTestStartOpts) {
    // The piano's sound, output and play-once go only when given, so an older show_service gets today's body.
    const extra = Object.fromEntries(Object.entries(opts ?? {}).filter(([, v]) => v !== undefined && v !== null));
    return this.json<ShowTestState>("POST", "/show/api/v1/test", { source, piece, ...extra });
  }

  showTestStop(source: ShowTestSource) {
    return this.json<{ stopped: string[] }>("DELETE", `/show/api/v1/test/${source}`);
  }

  // ── The Steinway's recorder (show_service, through the /show pass-through). No auth. ──

  /** The recorder and the saved takes; null until show_service has the route or while it is down (404 / 405 / 502 / 503). */
  async pianoRecordings(signal?: AbortSignal): Promise<PianoRecordingsView | null> {
    try {
      const v = await this.json<PianoRecordingsView>("GET", "/show/api/v1/piano/recordings", undefined, signal);
      return v && v.recording && Array.isArray(v.recordings) ? v : null;
    } catch (e) {
      if (e instanceof RoomApiError && [404, 405, 502, 503].includes(e.status)) return null;
      throw e;
    }
  }

  /** Record (on) or Stop (off); a stop saves the take and returns it as `last` (null when nothing was played). */
  pianoRecord(on: boolean, ask?: PianoRecordAsk) {
    return this.json<PianoRecordingsView>("POST", "/show/api/v1/piano/record", on || !ask ? { on } : { on, ...ask });
  }

  pianoRename(id: string, title: string) {
    return this.json<PianoRecording>("PATCH", `/show/api/v1/piano/recordings/${encodeURIComponent(id)}`, { title });
  }

  /** A take's details: title, composer (null clears it), genre. */
  pianoEdit(id: string, changes: { title?: string; composer?: string | null; genre?: string | null }) {
    return this.json<PianoRecording>("PATCH", `/show/api/v1/piano/recordings/${encodeURIComponent(id)}`, changes);
  }

  pianoDelete(id: string) {
    return this.json<{ deleted: string }>("DELETE", `/show/api/v1/piano/recordings/${encodeURIComponent(id)}`);
  }

  /** The take as a Standard MIDI File (an <a href>: the route is open, so no token goes in it). */
  pianoRecordingUrl(id: string): string {
    return `${this.baseUrl}/show/api/v1/piano/recordings/${encodeURIComponent(id)}.mid`;
  }

  // ── The Music card: show_service through the /show pass-through (show_service/README.md S3.9). No auth. ──

  /** The card's socket: a welcome with the whole card, then merge patches (no token: show_service is open). */
  showEventsUrl(): string {
    return `${this.baseUrl.replace(/^http/i, "ws")}/show/v1/events`;
  }

  showCard(signal?: AbortSignal) {
    return this.json<ShowCard>("GET", "/show/api/v1/card", undefined, signal);
  }

  showStep(dir: "calmer" | "wilder") {
    return this.json<ShowStepResult>("POST", "/show/api/v1/step", { dir });
  }

  /** "auto", "art" or a palette's name from the card's choices. */
  showPalette(name: string) {
    return this.json<{ palette?: ShowCard["palette"] }>("PUT", "/show/api/v1/palette", { name });
  }

  /** next: Next look · keep: Keep this look · house: House lights (a toggle) · low: Lights low (a toggle). */
  showAction(action: "next" | "keep" | "house" | "low", on?: boolean) {
    return this.json<ShowActionResult>("POST", "/show/api/v1/action", on === undefined ? { do: action } : { do: action, on });
  }

  /** × on a chip; "all" is Clear all. */
  showClearNudge(id: string) {
    return this.json<{ cleared: string[] }>("DELETE", `/show/api/v1/nudge/${encodeURIComponent(id)}`);
  }

  /** Describe it: the answer comes within show.yaml's describe_timeout_s (8 s) + 1 s. */
  showDescribe(text: string) {
    return this.json<ShowDescribeResult>("POST", "/show/api/v1/describe", { text }, undefined, 15_000);
  }

  showListening(on: boolean) {
    return this.json<ShowListeningView>("PUT", "/show/api/v1/listening", { on });
  }

  /** Auto on/off: answers once the room takes it (show_service waits up to 12 s for the room). */
  showAutopilot(on: boolean) {
    return this.json<unknown>("PUT", "/show/api/v1/autopilot", { on }, undefined, 15_000);
  }

  showAutoUndo() {
    return this.json<ShowUndoResult>("POST", "/show/api/v1/autopilot/undo", undefined, undefined, 15_000);
  }

  showAutoResume() {
    return this.json<unknown>("POST", "/show/api/v1/autopilot/resume");
  }

  /** The room mic: on only while something needs it; Mute (the page's control) always wins. */
  showMic(signal?: AbortSignal) {
    return this.json<ShowMicState>("GET", "/show/api/v1/mic", undefined, signal);
  }

  showSetMuted(muted: boolean) {
    return this.json<ShowMicState>("PUT", "/show/api/v1/mic", { muted });
  }

  /** The mics show_service can hear (each sender's devices) and the one chosen. */
  showInputs(signal?: AbortSignal) {
    return this.json<ShowInputs>("GET", "/show/api/v1/inputs", undefined, signal);
  }

  /** Listen through this mic: a sender's stream and, optionally, one of its devices. */
  showSetInput(stream: string, device?: string | null) {
    return this.json<ShowInputs>("PUT", "/show/api/v1/input", { stream, ...(device ? { device } : {}) }, undefined, 15_000);
  }

  /** The test piano (the renderer, through room_service's /renderer pass-through); null when unreachable or
   *  too old to report its pieces (404 / 405). */
  async testPiano(signal?: AbortSignal): Promise<TestPianoView | null> {
    try {
      return await this.json<TestPianoView>("GET", "/renderer/api/sound/test", undefined, signal);
    } catch (e) {
      if (e instanceof RoomApiError && [404, 405, 502, 503].includes(e.status)) return null;
      throw e;
    }
  }

  /** {on: true, piece} starts (or switches) the test piano; {on: false} stops it. */
  testPianoSend(body: { on: boolean; piece?: string }) {
    return this.json<TestPianoView>("POST", "/renderer/api/sound/test", body);
  }

  /** The PNG as a Blob, or null if there is none yet (404). */
  async snapshot(screen: string, kind: "anchor" | "target", signal?: AbortSignal): Promise<Blob | null> {
    try {
      const path = `/api/v1/screens/${encodeURIComponent(screen)}/snapshots/${kind}.png`;
      return await this.request("GET", path, (res) => res.blob(), { signal, timeoutMs: SNAPSHOT_TIMEOUT_MS });
    } catch (e) {
      if (e instanceof RoomApiError && e.status === 404) return null;
      throw e;
    }
  }
}
