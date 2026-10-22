// The Music page's client: show_service's library, playlists and player (docs: the music SPEC, §2–§3), reached through
// room_service's /show pass-through exactly as the Room page reaches show_service. The room's address is the Room
// page's own (resolveRoomUrl: ?room=, then the remembered one, then same-origin /rs over HTTPS or this host's :8700).
// No auth: a setup key is sent only when one was entered on the Room page for this server (loadToken), as RoomApi does.
//
//   GET    /show/api/v1/library?q=&genre=&composer=&kind=&pinned=&sort=&limit=&offset=   search, with facets
//   GET    /show/api/v1/library/item/<id>         one piece
//   PUT    /show/api/v1/library/pins/<id>         pin (DELETE unpins)
//   POST   /show/api/v1/library/upload?filename=&title=&composer=&genre=   raw MIDI body (≤ 1 MB)
//   PATCH  /show/api/v1/library/item/<id>         rename (uploads and recordings)      DELETE: remove
//   GET    /show/api/v1/library/item/<id>.mid     download
//   GET    /show/api/v1/library/web?q=            find MIDI on the web; POST …/web/import {ref, title?}
//   GET    /show/api/v1/playlists                 POST {name, items?}; GET/PUT/DELETE …/<id>
//   GET    /show/api/v1/player                    POST {action, …}

import { explainError, loadToken, RoomApiError } from "@/pages/Room/api";

// ── Shapes (the spec's) ─────────────────────────────────────────────────────

export type PieceKind = "lib" | "user" | "rec" | "file" | "phrase";

export interface MusicItem {
  id: string;
  title: string;
  composer?: string | null;
  performer?: string | null;
  genre?: string | null;
  genre_label?: string | null;
  era?: string | null;
  year?: number | null;
  duration_s?: number | null;
  kind: PieceKind | string;
  pinned: boolean;
  editable: boolean;
  added_at?: string | null;
  source?: string | null;
}

export interface Facets {
  genres: { id: string; label: string; count: number }[];
  composers: { name: string; count: number }[];
  kinds: { id: string; label: string; count: number }[];
}

export interface SearchResult {
  items: MusicItem[];
  total: number;
  facets: Facets;
}

export type SortOrder = "rank" | "title" | "composer" | "recent";

export interface SearchQuery {
  q?: string;
  genre?: string | null;
  composer?: string | null;
  kind?: "all" | PieceKind;
  pinned?: boolean;
  sort?: SortOrder;
  limit?: number;
  offset?: number;
}

export interface UploadResult {
  item: MusicItem;
  cleaned?: { words: string | null } | null;
}

export interface WebResult {
  ref: string;
  title: string;
  source_label: string;
  detail?: string | null;
  size?: number | null;
}

export interface WebSearch {
  results: WebResult[];
  errors: { source_label: string; words: string }[];
}

export interface PlaylistSummary {
  id: string;
  name: string;
  count: number;
  duration_s: number | null;
}

export interface PlaylistEntry {
  piece: string;
  title: string;
  composer?: string | null;
  genre_label?: string | null;
  duration_s?: number | null;
  missing: boolean;
}

export interface Playlist {
  id: string;
  name: string;
  items: PlaylistEntry[];
}

export type PlayerState = "stopped" | "preparing" | "playing" | "stepped_aside" | "error";
export type Repeat = "off" | "all" | "one";

export interface PlayerChoices {
  instruments?: { id: string; label: string; kind: string }[];
  default_instrument?: string;
  outputs?: { id: string; label: string }[];
}

export interface PlayerView {
  state: PlayerState | string;
  words: string;
  queue: { playlist: { id: string; name: string } | null; name: string | null; count: number; index: number };
  current: MusicItem | null;
  next: MusicItem | null;
  upcoming: MusicItem[];
  progress: number | null;
  elapsed_s: number | null;
  duration_s: number | null;
  instrument: string | null;
  output: string | null;
  repeat: Repeat;
  shuffle: boolean;
  error: string | null;
  choices: PlayerChoices;
}

export type PlayerAction =
  | { action: "play"; playlist?: string; index?: number }
  | { action: "play"; piece: string }
  | { action: "play"; items: string[]; name?: string }
  | { action: "stop" | "next" | "prev" }
  | { action: "add" | "play_next"; piece: string }
  | { action: "set"; instrument?: string; output?: string; repeat?: Repeat; shuffle?: boolean };

// ── Words ───────────────────────────────────────────────────────────────────

/** The one line shown while show_service predates the music routes. */
export const OLD_SHOW_WORDS = "Music needs the newer show service.";

/** show_service answered, but doesn't have this route (an older one): aiohttp's own 404/405, not a JSON refusal. */
export function isOldShow(e: unknown): boolean {
  return e instanceof RoomApiError && (e.status === 404 || e.status === 405) && /^http_40[45]$/.test(e.code);
}

/** Nothing answered, or room_service couldn't reach show_service. */
export function isUnreached(e: unknown): boolean {
  return e instanceof RoomApiError && [0, 502, 503, 504].includes(e.status);
}

/** An error in the page's words: never a code or an id. */
export function musicWords(e: unknown): string {
  if (isOldShow(e)) return OLD_SHOW_WORDS;
  if (e instanceof RoomApiError) {
    if (e.status === 0) return "Can't reach the room. Check you're on the house Wi-Fi; the page keeps trying.";
    if (isUnreached(e)) return "The music side isn't answering just now. Try again in a moment.";
    if (e.status === 413 || e.code === "too_big") return e.detail || "That file is too big. The room takes MIDI files up to 1 MB.";
    if (e.code === "unknown_piece") return "That piece isn't in the library any more.";
    if (e.code === "unknown_playlist") return "That playlist isn't there any more.";
    if (e.status >= 400 && e.status < 500 && e.detail) return e.detail;
    if (e.status === 404) return "That isn't there any more.";
    if (e.status === 409) return "It was refused just now. Try again in a moment.";
    if (e.status >= 500) return "The music side had a problem with that. Try again in a moment.";
  }
  return explainError(e);
}

// ── Client ──────────────────────────────────────────────────────────────────

/** A request that hasn't answered in this long counts as a network failure (keeps polls from piling up). */
const REQUEST_TIMEOUT_MS = 10_000;
/** Web search asks every source in parallel (6 s each); an import downloads and cleans the file. */
const WEB_TIMEOUT_MS = 20_000;
const UPLOAD_TIMEOUT_MS = 30_000;
/** The room's pass-through takes bodies up to 1 MB. */
export const MAX_UPLOAD_BYTES = 1024 * 1024;

/** A piece id in a path: ":" stays readable (lib:…, user:…), everything else is encoded. */
const seg = (id: string) => encodeURIComponent(id).replace(/%3A/gi, ":");

function queryString(params: Record<string, string | number | boolean | null | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    q.set(k, typeof v === "boolean" ? (v ? "1" : "0") : String(v));
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}

export class MusicApi {
  /** room_service's address (the Room page's). */
  readonly baseUrl: string;
  private readonly token: string;

  constructor(baseUrl: string, token: string = loadToken(baseUrl).token) {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
    this.token = token;
  }

  private async request<T>(
    method: string,
    path: string,
    opts: { body?: unknown; raw?: Blob; signal?: AbortSignal; timeoutMs?: number } = {},
  ): Promise<T> {
    const { body, raw, signal, timeoutMs = REQUEST_TIMEOUT_MS } = opts;
    const headers: Record<string, string> = this.token ? { Authorization: `Bearer ${this.token}` } : {};
    if (raw) headers["Content-Type"] = "audio/midi";
    else if (body !== undefined) headers["Content-Type"] = "application/json";
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
      const res = await fetch(`${this.baseUrl}/show/api/v1${path}`, {
        method,
        headers,
        body: raw ?? (body === undefined ? undefined : JSON.stringify(body)),
        signal: ac.signal,
        cache: "no-cache",
      });
      const text = res.status === 204 ? "" : await res.text();
      if (!res.ok) {
        let code = res.status === 401 ? "unauthorized" : `http_${res.status}`;
        let detail = "";
        try {
          const j = JSON.parse(text);
          if (j && typeof j.error === "string") code = j.error;
          if (j && typeof j.detail === "string") detail = j.detail;
        } catch {
          /* not JSON: an older show_service's plain 404 */
        }
        throw new RoomApiError(res.status, code, detail);
      }
      return (text ? JSON.parse(text) : undefined) as T;
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

  // ── Library ──

  search(query: SearchQuery, signal?: AbortSignal) {
    const qs = queryString({
      q: query.q?.trim() || undefined,
      genre: query.genre,
      composer: query.composer,
      kind: query.kind && query.kind !== "all" ? query.kind : undefined,
      pinned: query.pinned ? true : undefined,
      sort: query.sort,
      limit: query.limit,
      offset: query.offset || undefined,
    });
    return this.request<SearchResult>("GET", `/library${qs}`, { signal });
  }

  item(id: string, signal?: AbortSignal) {
    return this.request<MusicItem>("GET", `/library/item/${seg(id)}`, { signal });
  }

  pins(signal?: AbortSignal) {
    return this.request<{ pins: MusicItem[] }>("GET", "/library/pins", { signal });
  }

  pin(id: string, on: boolean) {
    return this.request<{ pins: MusicItem[] }>(on ? "PUT" : "DELETE", `/library/pins/${seg(id)}`);
  }

  upload(file: Blob, meta: { filename: string; title?: string; composer?: string; genre?: string }) {
    const qs = queryString({
      filename: meta.filename,
      title: meta.title?.trim(),
      composer: meta.composer?.trim(),
      genre: meta.genre,
    });
    return this.request<UploadResult>("POST", `/library/upload${qs}`, { raw: file, timeoutMs: UPLOAD_TIMEOUT_MS });
  }

  /** Uploads and recordings only (a recording's rename is the recorder's). */
  edit(id: string, patch: { title?: string; composer?: string | null; genre?: string | null }) {
    return this.request<MusicItem>("PATCH", `/library/item/${seg(id)}`, { body: patch });
  }

  remove(id: string) {
    return this.request<unknown>("DELETE", `/library/item/${seg(id)}`);
  }

  /** The file (an <a href>: the route is open, so no key goes in it). */
  downloadUrl(id: string): string {
    return `${this.baseUrl}/show/api/v1/library/item/${seg(id)}.mid`;
  }

  webSearch(q: string, signal?: AbortSignal) {
    return this.request<WebSearch>("GET", `/library/web${queryString({ q: q.trim() })}`, { signal, timeoutMs: WEB_TIMEOUT_MS });
  }

  webImport(body: { ref: string; title?: string; composer?: string; genre?: string }) {
    return this.request<UploadResult>("POST", "/library/web/import", { body, timeoutMs: UPLOAD_TIMEOUT_MS });
  }

  // ── Playlists ──

  playlists(signal?: AbortSignal) {
    return this.request<{ playlists: PlaylistSummary[] }>("GET", "/playlists", { signal });
  }

  playlist(id: string, signal?: AbortSignal) {
    return this.request<Playlist>("GET", `/playlists/${seg(id)}`, { signal });
  }

  createPlaylist(name: string, items?: string[]) {
    return this.request<Playlist>("POST", "/playlists", { body: items ? { name, items } : { name } });
  }

  /** Full replace: reorder by sending the new order. */
  savePlaylist(id: string, patch: { name?: string; items?: string[] }) {
    return this.request<Playlist>("PUT", `/playlists/${seg(id)}`, { body: patch });
  }

  deletePlaylist(id: string) {
    return this.request<unknown>("DELETE", `/playlists/${seg(id)}`);
  }

  // ── Player ──

  player(signal?: AbortSignal) {
    return this.request<PlayerView>("GET", "/player", { signal });
  }

  playerAction(body: PlayerAction) {
    // a sound change while playing restarts the piece; give it time to answer
    return this.request<PlayerView>("POST", "/player", { body, timeoutMs: 15_000 });
  }
}

// ── Small formatting helpers ────────────────────────────────────────────────

/** 83 -> "1:23"; 3725 -> "1:02:05"; nothing for an unknown length. */
export function clock(s: number | null | undefined): string {
  if (typeof s !== "number" || !Number.isFinite(s) || s < 0) return "";
  const t = Math.floor(s);
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const ss = String(t % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

/** 0..1 -> 0..100, or null when unknown. */
export function percent(p: number | null | undefined): number | null {
  return typeof p === "number" && Number.isFinite(p) ? Math.round(Math.min(1, Math.max(0, p)) * 100) : null;
}

/** The player's state in one line of page words. */
export function playerWords(v: PlayerView): string {
  switch (v.state) {
    case "preparing": {
      const pct = percent(v.progress);
      return `Preparing the sound${pct !== null ? ` ${pct}%` : "…"}`;
    }
    case "playing": {
      const at = clock(v.elapsed_s);
      const of = clock(v.duration_s ?? v.current?.duration_s);
      return at && of ? `Playing · ${at} / ${of}` : "Playing";
    }
    case "stepped_aside":
      return v.words || "Stepped aside: something else started the piano";
    case "error":
      return v.error || v.words || "It didn't play.";
    default:
      return v.words || "Stopped";
  }
}

/** A playlist's total in words: "48 min", "1 hr 12 min", "under a minute". */
export function totalWords(s: number | null | undefined): string {
  if (typeof s !== "number" || !Number.isFinite(s) || s <= 0) return "";
  const m = Math.round(s / 60);
  if (m < 1) return "under a minute";
  if (m < 60) return `${m} min`;
  return `${Math.floor(m / 60)} hr${m % 60 ? ` ${m % 60} min` : ""}`;
}

export const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** "Frédéric Chopin · Martha Argerich · Classical · 4:32": what's known, in that order. */
export function itemLine(it: Pick<MusicItem, "composer" | "performer" | "genre_label" | "duration_s">, opts: { genre?: boolean } = {}): string {
  return [it.composer, it.performer, opts.genre === false ? null : it.genre_label, clock(it.duration_s)]
    .filter((x): x is string => !!x)
    .join(" · ");
}

/** "Today 22:15", "Yesterday 09:03", "Tue 14 Oct" (local time). */
export function whenWords(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const now = new Date();
  const hm = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (d.toDateString() === now.toDateString()) return `Today ${hm}`;
  if (d.toDateString() === yesterday.toDateString()) return `Yesterday ${hm}`;
  return d.toLocaleDateString("en-GB", {
    weekday: "short",
    day: "numeric",
    month: "short",
    ...(d.getFullYear() !== now.getFullYear() ? { year: "numeric" as const } : {}),
  });
}

/** The fixed genres (spec §1), for the upload form before the library has answered. */
export const GENRES: { id: string; label: string }[] = [
  { id: "classical", label: "Classical" },
  { id: "ragtime", label: "Ragtime & stride" },
  { id: "jazz", label: "Jazz" },
  { id: "standards", label: "Standards" },
  { id: "pop", label: "Pop & rock" },
  { id: "film", label: "Film, TV & games" },
];
