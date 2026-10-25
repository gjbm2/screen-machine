// The Music card (SHOW_ENGINE §10.1): what the show engine hears, and the few nudges a person needs, in plain words.
// It sits directly under the mode tiles: one per room with an audio source. show_service hears one room today and
// its card names none, so the page draws one. Everything comes from show_service's card through room_service's
// /show pass-through (show_service/README.md S3.9): GET /show/api/v1/card, and WS /show/v1/events (a welcome with the
// whole card, then RFC 7386 merge patches at 2 Hz, chip changes at once), with a 5 s poll while that socket is down.
//   - Auto, the room-level one (it picks the mode by itself): its switch, its line, Resume, and the
//     "Auto → Piano · the piano is playing · Undo" chip after a switch. Nothing else on the page is called "Auto".
//   - Listening: the room mic's state and its input, the piano's MIDI (connected, and each key as it arrives), then AI
//     listening (short clips to an online AI: its cost and round trips). The two switches, Mic and AI, sit in the page's
//     top bar (TopToggles.tsx, owner 24 Oct: "those are pretty central"); the menu has no controls.
//   - What it hears: "soul · relaxed" (or Quiet, Talking, No sound coming in) with a level bar;
//     with AI listening, the AI's line ("soft late-night jazz trio") and what it's ready for (the cue sheet).
//   - Calmer · Wilder, Describe it (one line), the palette, Next look · Keep this look.
//   - The active nudges as chips, each with an ×, and Clear all.
// House lights and Lights low change the lamps, so they sit in the Room page's Lights fold (RoomLightsRow.tsx).
// On the Room page (layout A, 24 Oct) the parts sit apart: AutoRow under the modes (with "How Auto picks ›": each
// mode's start rule, AutoTriggers.tsx useAutoRules), DirectorControls in Look, ListeningPanel in Listening (the piano's
// MIDI line is in the Piano fold); MusicCard still puts them together.
// Nothing is ever disabled: a tap the music side can't take yet
// is sent again for half a minute, and the card says so. The words are show_service's own or written here; never an id.

import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import { ChevronRight, Feather, Flame, Info, MessageSquareText, Pin, SkipForward, X, type LucideIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import { RoomApi, RoomApiError } from "./api";
import { Pill, Spinner, Unroll } from "./bits";
import { LevelTrace } from "./LevelTrace";
import { MicPicker } from "./MicPicker";
import { useAutoRules, type AutoRuleWords } from "./AutoTriggers";
import { CARD_CLASS, TAP_BUTTON_CLASS, THEME_CLASS } from "./theme";
import { TEXT_CLASSES, TONE_CLASSES, type Tone } from "./tones";
import type { ShowAutoNote, ShowCard, ShowChip, ShowEvent, ShowListenRound, ShowMicState, ShowPaletteChoice } from "./types";
import type { NewLogEntry } from "./useRoomConnection";

const POLL_MS = 5_000;
const PING_MS = 20_000;
/** A socket that has said nothing for this long (not even a pong) is presumed dead. */
const SILENT_MS = 45_000;
/** Reconnects: full jitter, random(0, min(15 s, 1 s·2^n)). */
const BACKOFF_BASE_MS = 1_000;
const BACKOFF_CAP_MS = 15_000;
/** While the socket is down, a card read this recently still counts as answering. */
const FRESH_MS = 12_000;
/** A tap that can't reach show_service is sent again every 2 s for this long. */
const RETRY_FOR_MS = 30_000;
const RETRY_EVERY_MS = 2_000;
const NOTE_MS = 8_000;

const NOT_ANSWERING = "The music side isn't answering; trying…";
/** The palette's "auto" choice: the room-level Auto is the only thing on the page called Auto. */
const PALETTE_AUTO_WORDS = "Let it choose";
/** Before the first card: the two choices every card has (show_service's fixed palette words). */
const FALLBACK_CHOICES: ShowPaletteChoice[] = [
  { name: "auto", label: PALETTE_AUTO_WORDS, swatch: [] },
  { name: "art", label: "From the art", swatch: [] },
];

const CHIP_ON = "border-teal-300 bg-teal-50 text-teal-900";
const CHIP_OFF = "border-stone-200 bg-white hover:bg-stone-50";

const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);
const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));
const clamp01 = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? Math.min(1, Math.max(0, v)) : 0);
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** RFC 7386: null deletes a key, objects merge, anything else (lists included) replaces. */
export function mergePatch<T>(target: T, patch: unknown): T {
  if (!isObj(patch)) return patch as T;
  const out: Record<string, unknown> = isObj(target) ? { ...target } : {};
  for (const [k, v] of Object.entries(patch)) {
    if (v === null) delete out[k];
    else out[k] = mergePatch(out[k], v);
  }
  return out as T;
}

// ── The link: one per page, read by the card and the menu's row (so a 2 Hz patch re-renders only them) ──

export interface ShowState {
  card: ShowCard | null;
  /** The events socket is open: changes arrive as they happen. */
  live: boolean;
  /** Local time of the last card read over HTTP; null before the first. */
  lastAt: number | null;
}

export class ShowFeed {
  private state: ShowState = { card: null, live: false, lastAt: null };
  private readonly subs = new Set<() => void>();
  /** Re-reads the card now (set by the link while it runs). */
  refresh: () => void = () => {};

  get = (): ShowState => this.state;

  subscribe = (fn: () => void) => {
    this.subs.add(fn);
    return () => {
      this.subs.delete(fn);
    };
  };

  set(p: Partial<ShowState>) {
    this.state = { ...this.state, ...p };
    for (const fn of this.subs) fn();
  }

  /** A tap's own answer, merged into the card at once (the socket's next patch agrees with it). */
  patch(p: Record<string, unknown>) {
    if (this.state.card) this.set({ card: mergePatch(this.state.card, p) });
  }
}

/**
 * The card's link: the events socket (reconnected with backoff; a ping every 20 s, and a silent socket is closed), and
 * GET /show/api/v1/card every 5 s while it is down and the page is visible. Returns the stop.
 */
export function runShowLink(feed: ShowFeed, api: RoomApi, log: (e: NewLogEntry) => void): () => void {
  let alive = true;
  let ws: WebSocket | null = null;
  let open = false; // the socket has delivered its welcome
  let synced = false; // patches apply only on top of a welcome, with no message missed since
  let seq = 0;
  let attempt = 0;
  let lastMsgAt = 0;
  let polling = false;
  let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
  let sleepTimer: ReturnType<typeof setTimeout> | undefined;
  const ac = new AbortController();

  const read = async () => {
    try {
      const c = await api.showCard(ac.signal);
      // While the socket is open its patches are the truth; a read that crossed a welcome is dropped.
      if (alive && !open && isObj(c)) feed.set({ card: c, lastAt: Date.now() });
    } catch {
      /* lastAt goes stale and the card says so */
    }
  };
  feed.refresh = () => void read();

  const poll = async () => {
    if (polling) return;
    polling = true;
    while (alive && !open) {
      if (document.visibilityState === "visible") await read();
      if (!alive || open) break;
      await new Promise<void>((r) => (sleepTimer = setTimeout(r, POLL_MS)));
    }
    polling = false;
  };

  const say = (m: object) => {
    try {
      if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(m));
    } catch {
      /* onclose follows */
    }
  };

  const onMessage = (raw: unknown) => {
    if (typeof raw !== "string") return;
    let m: ShowEvent;
    try {
      m = JSON.parse(raw);
    } catch {
      return;
    }
    if (!isObj(m)) return;
    const s = typeof m.seq === "number" ? m.seq : null;
    if (m.type === "welcome") {
      if (!isObj(m.card)) return;
      seq = s ?? 0;
      attempt = 0;
      open = synced = true;
      feed.set({ card: m.card, live: true });
      return;
    }
    if (s !== null) {
      // seq goes up by one per message: a gap (patches dropped) asks for a fresh welcome
      if (synced && seq > 0 && s !== seq + 1) {
        synced = false;
        say({ type: "resync" });
      }
      seq = s;
    }
    if (m.type === "card" && synced && isObj(m.patch)) {
      // the test sources' block is the console's; it changes every tick, so it's left out here
      const { tests: _tests, ...patch } = m.patch;
      if (Object.keys(patch).length) feed.patch(patch);
    } else if (m.type === "auto" && typeof m.event?.text === "string") {
      log({ type: "music", level: "info", summary: m.event.text });
    } else if (m.type === "describe" && typeof m.result?.reply === "string") {
      log({ type: "music", level: "info", summary: `Describe it: ${m.result.reply}` });
    }
  };

  const reconnectLater = () => {
    if (!alive) return;
    const ceiling = Math.min(BACKOFF_CAP_MS, BACKOFF_BASE_MS * 2 ** attempt);
    attempt += 1;
    reconnectTimer = setTimeout(connect, Math.random() * ceiling);
  };

  const connect = () => {
    reconnectTimer = undefined;
    if (!alive) return;
    let sock: WebSocket;
    try {
      sock = new WebSocket(api.showEventsUrl());
    } catch {
      reconnectLater();
      return;
    }
    ws = sock;
    seq = 0;
    synced = false;
    lastMsgAt = Date.now();
    sock.onmessage = (e) => {
      lastMsgAt = Date.now();
      onMessage(e.data);
    };
    sock.onerror = () => {
      /* onclose follows */
    };
    sock.onclose = () => {
      if (ws === sock) ws = null;
      if (!alive) return;
      if (open) {
        // it was live a moment ago: the card stays fresh until the polls say otherwise
        log({ type: "music", level: "warn", summary: "Music card: live updates lost; checking every 5 s" });
        feed.set({ live: false, lastAt: Date.now() });
      }
      open = synced = false;
      void poll();
      reconnectLater();
    };
  };

  const pinger = setInterval(() => {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    if (Date.now() - lastMsgAt > SILENT_MS) ws.close();
    else say({ type: "ping" });
  }, PING_MS);

  // A phone back from the background: try at once rather than wait out the backoff.
  const onVisible = () => {
    if (document.visibilityState !== "visible") return;
    if (!ws && reconnectTimer) {
      clearTimeout(reconnectTimer);
      connect();
    }
    if (!open) void read();
  };
  document.addEventListener("visibilitychange", onVisible);

  void poll();
  connect();

  return () => {
    alive = false;
    ac.abort();
    clearTimeout(reconnectTimer);
    clearTimeout(sleepTimer);
    clearInterval(pinger);
    document.removeEventListener("visibilitychange", onVisible);
    ws?.close();
    ws = null;
    feed.refresh = () => {};
  };
}

/** Runs the card's link for the page (while `enabled`): one feed per room_service. */
export function useShowFeed(api: RoomApi, enabled: boolean, addLog: (e: NewLogEntry) => void): ShowFeed {
  const feed = useMemo(() => new ShowFeed(), [api]);
  const logRef = useRef(addLog);
  logRef.current = addLog;
  useEffect(() => (enabled ? runShowLink(feed, api, (e) => logRef.current(e)) : undefined), [feed, api, enabled]);
  return feed;
}

export function useShowState(feed: ShowFeed): ShowState {
  return useSyncExternalStore(feed.subscribe, feed.get, feed.get);
}

// ── Taps ──

/** The tap never reached show_service (no route yet, show_service down or still starting): safe to send again. A
 *  timeout may have landed, so it isn't. */
function unreached(e: unknown): boolean {
  if (!(e instanceof RoomApiError)) return false;
  if (e.status === 0) return e.code === "network" && !e.detail.startsWith("no reply");
  if (e.status === 404 || e.status === 405) return true;
  if (e.status === 502) return e.code === "show_unreachable";
  if (e.status === 503) return e.code === "starting";
  return false;
}

function failWords(e: unknown): string {
  if (e instanceof RoomApiError) {
    if (unreached(e)) return "The music side still isn't answering. Try again in a moment.";
    if (e.status === 0 || e.status === 503 || e.status === 504) return "The music side is busy; try again in a moment.";
    // show_service's own words ("The room isn't answering, so Auto can't change that right now.")
    if (e.detail && e.code !== "bad_json") return e.detail;
  }
  return "That didn't go through; try again in a moment.";
}

export interface Note {
  tone: Tone;
  text: string;
}

/**
 * Sends taps: a spinner per control, a tap that can't reach show_service sent again every 2 s for 30 s (with "trying…"
 * on the card), and one plain line afterwards. `latest`: a newer tap on the same control replaces an older one still
 * trying (a palette pick, a switch). Resolves true when it landed, false when it gave up, undefined when replaced.
 */
export function useSend(feed: ShowFeed, addLog: (e: NewLogEntry) => void) {
  const [busy, setBusy] = useState<Record<string, number>>({});
  const [note, setNote] = useState<Note | null>(null);
  const gen = useRef<Record<string, number>>({});
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useEffect(() => {
    if (!note || note.text === NOT_ANSWERING) return;
    const t = setTimeout(() => setNote(null), NOTE_MS);
    return () => clearTimeout(t);
  }, [note]);

  const send = useCallback(
    async <T,>(
      key: string,
      what: string,
      fn: () => Promise<T>,
      opts: { latest?: boolean; ok?: (r: T) => Note | null | void } = {},
    ): Promise<boolean | undefined> => {
      const g = (gen.current[key] = (gen.current[key] ?? 0) + 1);
      const replaced = () => !mounted.current || (!!opts.latest && gen.current[key] !== g);
      const bump = (d: number) => setBusy((b) => ({ ...b, [key]: Math.max(0, (b[key] ?? 0) + d) }));
      bump(1);
      setNote(null);
      const until = Date.now() + RETRY_FOR_MS;
      try {
        for (;;) {
          try {
            const r = await fn();
            if (!mounted.current) return undefined;
            setNote((opts.ok?.(r) as Note | null | undefined) ?? null);
            addLog({ type: "music", level: "info", summary: `Music: ${what}` });
            if (!feed.get().live) feed.refresh();
            return true;
          } catch (e) {
            if (replaced()) return undefined;
            if (unreached(e) && Date.now() < until) {
              setNote({ tone: "warn", text: NOT_ANSWERING });
              await sleep(RETRY_EVERY_MS);
              if (replaced()) return undefined;
              continue;
            }
            setNote({ tone: "bad", text: failWords(e) });
            addLog({ type: "music", level: "warn", summary: `Music: ${what} didn't go through (${e instanceof Error ? e.message : String(e)})` });
            return false;
          }
        }
      } finally {
        bump(-1);
      }
    },
    [feed, addLog],
  );
  const isBusy = useCallback((k: string) => (busy[k] ?? 0) > 0, [busy]);
  return { send, busy: isBusy, note };
}

export function NoteLine({ note, className }: { note: Note | null; className?: string }) {
  if (!note) return null;
  return (
    <p role="status" className={cn("text-sm leading-snug", TEXT_CLASSES[note.tone], className)}>
      {note.text}
    </p>
  );
}

// ── Pieces ──

function Swatch({ colours }: { colours?: string[] }) {
  const cs = (colours ?? []).filter((c) => /^#[0-9a-f]{3,8}$/i.test(c));
  if (!cs.length) return <span aria-hidden className="block h-2.5 rounded-sm bg-stone-200" />;
  return (
    <span aria-hidden className="flex h-2.5 overflow-hidden rounded-sm ring-1 ring-inset ring-black/5">
      {cs.map((c, i) => (
        <span key={i} className="flex-1" style={{ background: c }} />
      ))}
    </span>
  );
}

function ActionButton({
  icon: Icon,
  label,
  title,
  on,
  busy,
  dim,
  onClick,
  children,
}: {
  icon: LucideIcon;
  label: string;
  title?: string;
  /** A toggle that is on (aria-pressed); undefined for a plain button. */
  on?: boolean;
  busy?: boolean;
  /** At its end (as calm or as wild as it goes): still tappable, drawn quieter. */
  dim?: boolean;
  onClick: () => void;
  /** After the label, small (Keep's minutes left). */
  children?: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={on}
      title={title}
      onClick={onClick}
      className={cn(
        "flex min-h-11 items-center justify-center gap-1.5 whitespace-nowrap rounded-xl border px-2.5 py-1 text-center text-[15px] font-medium leading-tight transition-colors",
        on ? CHIP_ON : CHIP_OFF,
        dim && "text-muted-foreground",
      )}
    >
      {busy ? <Spinner className="h-[18px] w-[18px] shrink-0" /> : <Icon className="h-[18px] w-[18px] shrink-0 lg:hidden" aria-hidden />}
      <span>{label}</span>
      {children}
    </button>
  );
}

/** Auto's announcement: a switch (with Undo), a hold, an undo, a failure. */
function AutoChip({ chip, busy, onUndo }: { chip: ShowAutoNote; busy: boolean; onUndo: () => void }) {
  const tone: Tone = chip.kind === "failed" ? "warn" : chip.kind === "switch" ? "live" : "muted";
  return (
    <div className={cn("flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl border px-3 py-1.5", TONE_CLASSES[tone])} data-testid="music-auto-chip">
      <span className="min-w-0 flex-1 py-1 text-[15px] leading-snug">{chip.text}</span>
      {chip.undo && (
        <button
          type="button"
          onClick={onUndo}
          className="inline-flex min-h-11 items-center gap-1.5 rounded-lg px-2 text-[15px] font-semibold underline underline-offset-2"
        >
          {busy && <Spinner />} Undo
        </button>
      )}
    </div>
  );
}

// ── The card's parts (owner, 24 Oct, layout A): Auto sits under the modes, the room director's controls in Look,
// the listening rows in Listening. Each part reads the same feed; MusicCard puts them back together. ──

/** Hearing words that are a state of the sound, not music. */
const NOT_MUSIC = new Set(["Quiet", "Talking", "No sound coming in", "Listening"]);

function paletteNow(card: ShowCard | null): string | null {
  const pick = card?.palette?.pick ?? "auto";
  const choices = (card?.palette?.choices?.length ? card.palette.choices : FALLBACK_CHOICES).filter((c) => !!c?.name);
  if (pick === "auto" || pick === "art") {
    const cur = choices.find((c) => c.name === card?.palette?.current && c.name !== "auto" && c.name !== "art")?.label;
    return cur ?? (pick === "art" ? "From the art" : null);
  }
  const c = choices.find((x) => x.name === pick);
  return c?.label || cap(pick);
}

/**
 * Who is choosing the room's look, in words: the line in Look ("Director is choosing · Dusk · follows the music") and
 * its closed summary ("Director choosing · Dusk"). Yours once a nudge is on or a palette is picked.
 */
export function directorWords(card: ShowCard | null, answering: boolean): { line: string; summary: string } {
  if (!card) return { line: "The music side isn't answering yet", summary: "Waiting for the music side" };
  const chips = (card.chips ?? []).filter((c) => !!c?.id);
  const pick = card.palette?.pick ?? "auto";
  const yours = chips.length > 0 || (pick !== "auto" && pick !== "art");
  const palette = paletteNow(card);
  const heard = card.hearing?.text;
  const follows = answering && !!heard && !NOT_MUSIC.has(heard) ? "follows the music" : null;
  const line = [yours ? "Your picks are on" : "Director is choosing", palette, follows].filter(Boolean).join(" · ");
  const summary = [yours ? "Your picks" : "Director choosing", palette].filter(Boolean).join(" · ");
  return { line, summary };
}

/** The room mic in words. `allowed`: the switch (on = the room may use the mic, owner 17 Oct; off always wins).
 *  `line`: whether it's in use right now and for what ("In use · for Auto"); `listening`: in use now (the red dot). */
export function micWords(mic: ShowMicState | null | undefined, pendingMute: boolean | null = null) {
  const allowed = !(pendingMute ?? mic?.muted === true);
  const listening = !!mic?.on && allowed;
  const line = !mic
    ? null
    : listening
      ? `In use · ${
          (mic.claims ?? [])
            .map((c) =>
              c.by === "auto"
                ? "for Auto"
                : c.by === "listening"
                  ? "for AI listening"
                  : `${c.label || mic.mode || "this mode"} follows the music`,
            )
            .join(" · ") || "for the room"
        }`
      : !allowed
        ? "Off"
        : mic.wanted
          ? "Not in use · waiting for the mic"
          : "Not in use · nothing needs it right now";
  return { allowed, listening, line };
}

/** AI listening in words: `state` ("Listening", or show_service's words for a pause), `about` (what it does, while
 *  off), and `cost` (show_service's own numbers, shown on or off so the price is there before switching on). */
export function aiWords(l: ShowCard["listening"] | null | undefined, on: boolean) {
  const about = `Lets an online AI${l?.provider ? ` (${l.provider})` : ""} listen to the music and foresee what's coming (a chorus, a climax, the end), so the screens and lights change on time`;
  const state = !on ? null : l?.effective ? "Listening" : l?.why ? l.why.replace(/\.+$/, "") : "Not sending right now";
  const money = (v: number) => `$${v.toFixed(2)}`;
  const cost = [
    typeof l?.est_evening_usd === "number" ? `about ${money(l.est_evening_usd)} an evening` : null,
    typeof l?.spend_today_usd === "number" ? `${money(l.spend_today_usd)} so far today` : null,
  ]
    .filter(Boolean)
    .join(" · ");
  return { about, state, cost };
}

/** Listening's closed summary: only what the top bar's Mic and AI switches don't already say (owner, 25 Oct: no
 *  element restates another): what it hears and whether the piano's MIDI is connected, "Hears soul · piano connected".
 *  `piano`: the piano's MIDI is connected (PianoRow). */
export function listeningWords(card: ShowCard | null, piano: boolean | null): string {
  if (!card) return "The music side isn't answering yet";
  const heard = card.hearing?.text;
  const hears = heard && heard !== "Listening" ? `Hears ${heard.charAt(0).toLowerCase()}${heard.slice(1)}` : null;
  return [hears, piano ? "piano connected" : null].filter(Boolean).join(" · ") || "Room mic, AI listening and what it hears";
}

/** Auto's line while it watches, in plain words (show_service says "Auto · watching"). */
const AUTO_WATCHING = "Auto · picks the mode from what it hears";

/**
 * Auto, the room-level one: it picks the mode by itself. Its switch, its line, Resume, its chip and suggestion, and
 * "How Auto picks ›": each mode's start rule in words. `onEdit`: opens that mode's editor (its "Starts by itself").
 */
export function AutoRow({
  api,
  feed,
  addLog,
  onEdit,
  current,
}: {
  api: RoomApi;
  feed: ShowFeed;
  addLog: (e: NewLogEntry) => void;
  onEdit?: (mode: string) => void;
  /** The room's mode now (its rule reads "On now"). */
  current?: string | null;
}) {
  const { card } = useShowState(feed);
  const autoSend = useSend(feed, addLog);
  const [pendingAuto, setPendingAuto] = useState<boolean | null>(null);
  const auto = card?.auto;
  const autoOn = pendingAuto ?? auto?.on === true;
  const rawLine = auto?.line?.trim() || (card ? "Auto" : "Auto · waiting for the music side");
  const resumable = /\s·\s*Resume$/.test(rawLine);
  const autoLine = auto?.state === "watching" && autoOn ? AUTO_WATCHING : rawLine.replace(/\s·\s*Resume$/, "");
  const setAuto = (on: boolean) => {
    setPendingAuto(on);
    void autoSend
      .send("auto", on ? "Auto on" : "Auto off", () => api.showAutopilot(on), {
        latest: true,
        ok: (r) => {
          if (isObj(r) && "line" in r) {
            const { on: o, state, line, chip, suggest } = r as NonNullable<ShowCard["auto"]>;
            feed.patch({ auto: { on: o ?? null, state: state ?? null, line: line ?? null, chip: chip ?? null, suggest: suggest ?? null } });
          }
        },
      })
      .then((r) => r !== undefined && setPendingAuto(null));
  };
  const undo = () =>
    void autoSend.send("undo", "Undo Auto", () => api.showAutoUndo(), {
      ok: (r) =>
        r && r.undone === false
          ? { tone: "muted", text: r.detail || (r.why === "nothing_to_undo" ? "There's nothing to undo." : "Auto can't undo that now.") }
          : null,
    });
  const resume = () => void autoSend.send("resume", "Resume Auto", () => api.showAutoResume());
  const switchTo = (mode: string, what: string) => void autoSend.send("mode", what, () => api.setMode(mode, "auto-suggestion"));

  // One row (owner, 25 Oct: vertical space is at a premium): the line (with Resume in it), "How Auto picks" behind the
  // ⓘ, and the switch. While off, what it does is in the line itself, not a second line.
  const offWords = !autoOn && !/\s·\s/.test(autoLine) ? " · on, it picks the mode from what it hears" : "";
  return (
    <div className={cn(CARD_CLASS, "py-0.5 pl-3 pr-2 sm:pl-4")} data-testid="music-auto">
      <div className="flex min-h-11 items-center gap-1">
        <p className="line-clamp-2 min-w-0 flex-1 py-1 text-[15px] leading-snug" data-testid="music-auto-line">
          <span className="font-semibold">{autoLine}</span>
          {offWords && <span className="text-muted-foreground">{offWords}</span>}
          {resumable && (
            <>
              {" · "}
              <button
                type="button"
                onClick={resume}
                className="-my-2 inline-flex min-h-11 items-center gap-1 px-0.5 align-middle font-semibold text-teal-800 underline underline-offset-2 hover:text-teal-900"
              >
                {autoSend.busy("resume") && <Spinner />}Resume
              </button>
            </>
          )}
        </p>
        <Popover>
          <PopoverTrigger
            aria-label="How Auto picks"
            title="How Auto picks"
            data-testid="music-auto-rules"
            className="grid h-11 w-10 shrink-0 place-items-center rounded-full text-stone-500 hover:bg-stone-100 hover:text-stone-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <Info className="h-[18px] w-[18px]" aria-hidden />
          </PopoverTrigger>
          <PopoverContent align="end" className={cn(THEME_CLASS, "max-h-[70vh] w-[min(26rem,calc(100vw-2rem))] overflow-y-auto rounded-2xl p-2")}>
            <p className="px-1 pb-1.5 pt-0.5 text-[15px] font-semibold">How Auto picks</p>
            <AutoRules api={api} onEdit={onEdit} current={current} />
          </PopoverContent>
        </Popover>
        {autoSend.busy("auto") && <Spinner className="shrink-0" />}
        <span className="grid h-11 w-12 shrink-0 place-items-center">
          <Switch aria-label="Auto" checked={autoOn} onCheckedChange={setAuto} />
        </span>
      </div>
      {(auto?.chip?.text || (auto?.suggest?.text && auto.suggest.mode) || autoSend.note) && (
        <div className="space-y-1.5 pb-2 pr-1">
      {auto?.chip?.text && <AutoChip chip={auto.chip} busy={autoSend.busy("undo")} onUndo={undo} />}
      {auto?.suggest?.text && auto.suggest.mode && (
        <button
          type="button"
          onClick={() => switchTo(auto.suggest!.mode!, auto.suggest!.text!)}
          className="flex min-h-11 w-full items-center justify-center gap-2 rounded-xl border border-teal-300 bg-teal-50 px-3 text-[15px] font-semibold text-teal-900 hover:bg-teal-100"
        >
          {autoSend.busy("mode") && <Spinner />} {auto.suggest.text}
        </button>
      )}
      <NoteLine note={autoSend.note} />
        </div>
      )}
    </div>
  );
}

/** Each mode's start rule (show_service's sentence), what Auto is doing about it now, and an Edit link. Read every
 *  2 s, only while unrolled. */
function AutoRules({ api, onEdit, current }: { api: RoomApi; onEdit?: (mode: string) => void; current?: string | null }) {
  const rules = useAutoRules(api, 2_000);
  if (rules === null)
    return (
      <p className="flex items-center gap-2 px-1 pb-2 text-[15px] text-muted-foreground">
        <Spinner /> Reading the rules…
      </p>
    );
  const ruled = rules.filter((r) => r.enter);
  const byHand = rules.filter((r) => !r.enter);
  const now = (r: AutoRuleWords) =>
    r.mode === current || r.blocked === "it's the current mode"
      ? "On now"
      : r.inS !== null
        ? r.inS > 0
          ? `Counting now: switches in ${r.inS} s`
          : "Switching now"
        : r.blocked
          ? `Won't switch now: ${r.blocked}`
          : null;
  return (
    <div className="space-y-2 px-1 pb-2">
      {ruled.length === 0 && <p className="text-[15px] text-muted-foreground">No mode starts by itself yet.</p>}
      {ruled.length > 0 && (
        <ul className="space-y-2" aria-label="Modes that start by themselves">
          {ruled.map((r) => (
            <li key={r.mode} className="flex items-start gap-2 rounded-xl border border-stone-200 bg-white px-3 py-2">
              <span className="min-w-0 flex-1">
                <span className="block text-[15px] leading-snug">{r.summary ?? `${r.label}: when ${r.enter}.`}</span>
                {now(r) && <span className="block text-sm leading-snug text-teal-800">{now(r)}</span>}
              </span>
              {onEdit && (
                <button
                  type="button"
                  onClick={() => onEdit(r.mode)}
                  aria-label={`Edit when ${r.label} starts`}
                  className="inline-flex min-h-11 shrink-0 items-center gap-0.5 rounded-lg px-1.5 text-[15px] font-medium text-teal-800 hover:underline"
                >
                  Edit <ChevronRight className="h-4 w-4" aria-hidden />
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {byHand.length > 0 && (
        <p className="text-sm leading-snug text-muted-foreground">
          Only when someone picks them: {byHand.map((r) => r.label).join(", ")}.
        </p>
      )}
    </div>
  );
}

/**
 * The room director's controls: Calmer · Wilder, Describe it (one line under them), the palette, Next look · Keep this
 * look, and the nudges that are on (each with ×, and Clear all). House lights and Lights low are in the Lights fold.
 */
export function DirectorControls({ api, feed, addLog }: { api: RoomApi; feed: ShowFeed; addLog: (e: NewLogEntry) => void }) {
  const { card } = useShowState(feed);
  const main = useSend(feed, addLog);
  const { send, busy } = main;
  const [text, setText] = useState("");
  // Describe it: on an iPad or wider, behind a "Describe…" button in the actions row until tapped; always out on a phone.
  const [describeOpen, setDescribeOpen] = useState(false);

  const steps = card?.steps;
  const pick = card?.palette?.pick ?? "auto";
  const choices = (card?.palette?.choices?.length ? card.palette.choices : FALLBACK_CHOICES).filter((c) => !!c?.name);
  const chips: ShowChip[] = (card?.chips ?? []).filter((c) => !!c?.id);
  const describe = card?.describe;

  const step = (dir: "calmer" | "wilder") =>
    void send(dir, cap(dir), () => api.showStep(dir), {
      ok: (r) =>
        r?.steps?.at_limit
          ? { tone: "muted", text: dir === "calmer" ? "That's as calm as it goes." : "That's as wild as it goes." }
          : null,
    });
  // The palette tapped last: its tile shows the spinner while the pick is on its way.
  const [palTapped, setPalTapped] = useState<string | null>(null);
  const setPalette = (c: ShowPaletteChoice) => {
    setPalTapped(c.name);
    void send("palette", `palette ${c.label || c.name}`, () => api.showPalette(c.name), { latest: true });
  };
  const next = () =>
    void send("next", "Next look", () => api.showAction("next"), {
      ok: (r) => (r?.detail ? { tone: "muted", text: r.detail } : r?.look && !r.look.next_pending ? { tone: "muted", text: "No colours are playing yet, so there's nothing to change." } : null),
    });
  const keep = () =>
    void send("keep", typeof card?.look?.keep_in_s === "number" ? "stop keeping this look" : "Keep this look", () =>
      api.showAction("keep"),
    );
  const clear = (c: ShowChip) => void send(`chip:${c.id}`, `clear ${c.label || "a change"}`, () => api.showClearNudge(c.id));
  const backToAuto = () => void send("all", "Clear all", () => api.showClearNudge("all"));
  const describeIt = () => {
    const t = text.trim();
    void send("describe", t ? `describe it: “${t}”` : "describe it", () => api.showDescribe(t), { ok: () => setText("") });
  };
  const switchTo = (mode: string, what: string) => void send("mode", what, () => api.setMode(mode, "describe-suggestion"));

  const changing = busy("next") || !!card?.look?.next_pending;
  const keepIn = card?.look?.keep_in_s;
  const keepMin = typeof keepIn === "number" ? Math.max(1, Math.ceil(keepIn / 60)) : null;
  const ranged = !!steps && typeof steps.min === "number" && typeof steps.max === "number" && steps.min < steps.max;
  const atEnd = (dir: "calmer" | "wilder") =>
    ranged && typeof steps?.value === "number" && (dir === "calmer" ? steps.value <= steps.min! : steps.value >= steps.max!);
  // While the palette follows the mode (or the art), what it's on now, when that's one of the choices.
  const nowLabel =
    pick === "auto" || pick === "art"
      ? choices.find((c) => c.name === card?.palette?.current && c.name !== "auto" && c.name !== "art")?.label ?? null
      : null;
  const working = busy("describe") || describe?.state === "working";
  // Out while opened, while something is typed, or while it's working on one or has answered one asked since the page
  // opened (show_service keeps its last answer until the next, so an old one doesn't hold it out).
  const replyKey = describe?.state === "done" ? `${describe.text ?? ""}|${describe.reply ?? ""}|${describe.suggest?.mode ?? ""}` : "";
  const firstReply = useRef<string | null>(null);
  if (card && firstReply.current === null) firstReply.current = replyKey;
  const describeShown = describeOpen || !!text || working || (!!replyKey && firstReply.current !== null && replyKey !== firstReply.current);
  const suggestLabel = describe?.suggest ? describe.suggest.label || cap(describe.suggest.mode) : "";

  // Owner, 25 Oct ("vertical space is at a HIGH PREMIUM"): the four nudges share one row (two on a phone), the palette is
  // one row of slim swatches ("Let it choose" first, what it's on now under it), and Describe it is one line.
  const keepLabel = keepMin !== null ? `Keep this look · ${keepMin} min left` : "Keep this look";
  const actions: Array<{ key: string; icon: LucideIcon; label: string; title?: string; on?: boolean; busy: boolean; dim?: boolean; onClick: () => void }> = [
    { key: "calmer", icon: Feather, label: "Calmer", title: atEnd("calmer") ? "As calm as it goes" : undefined, dim: atEnd("calmer"), busy: busy("calmer"), onClick: () => step("calmer") },
    { key: "wilder", icon: Flame, label: "Wilder", title: atEnd("wilder") ? "As wild as it goes" : undefined, dim: atEnd("wilder"), busy: busy("wilder"), onClick: () => step("wilder") },
    { key: "next", icon: SkipForward, label: changing ? "Changing…" : "Next look", busy: changing, onClick: next },
    { key: "keep", icon: Pin, label: "Keep this look", title: keepLabel, on: keepMin !== null, busy: busy("keep"), onClick: keep },
  ];

  return (
    <div className={cn(CARD_CLASS, "space-y-2.5 px-3 py-2.5 sm:px-3.5")} data-testid="music-director">
      {/* Calmer · Wilder · Next look · Keep this look */}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-[repeat(5,auto)]">
        {actions.map((a) => (
          <ActionButton key={a.key} icon={a.icon} label={a.label} title={a.title} on={a.on} busy={a.busy} dim={a.dim} onClick={a.onClick}>
            {a.key === "keep" && keepMin !== null && <span className="text-[13px] font-normal opacity-80">· {keepMin} min left</span>}
          </ActionButton>
        ))}
        <button
          type="button"
          aria-expanded={describeShown}
          onClick={() => setDescribeOpen((v) => !v)}
          title="Describe how it should feel, in your own words"
          className={cn(
            "hidden min-h-11 items-center justify-center gap-1.5 whitespace-nowrap rounded-xl border px-2.5 text-[15px] font-medium transition-colors sm:flex",
            describeShown ? CHIP_ON : CHIP_OFF,
          )}
        >
          <MessageSquareText className="h-[18px] w-[18px] shrink-0 lg:hidden" aria-hidden /> Describe…
        </button>
      </div>

      {/* Palette: one row of slim swatches (wrapping on a phone) */}
      {/* a phone and the narrow two-column layout: an even grid ("Let it choose" two wide); an iPad's full width: one
          row at each name's own width */}
      <div role="radiogroup" aria-label="Palette" className="grid grid-cols-[repeat(auto-fit,minmax(4.75rem,1fr))] gap-1.5 sm:flex lg:grid">
        {choices.map((c) => {
          const sel = c.name === pick;
          const isAuto = c.name === "auto";
          const label = isAuto ? PALETTE_AUTO_WORDS : c.label || cap(c.name);
          const spin = busy("palette") && palTapped === c.name;
          return (
            <button
              key={c.name}
              type="button"
              role="radio"
              aria-checked={sel}
              title={sel && nowLabel ? `Palette: ${label} · now ${nowLabel}` : `Palette: ${label}`}
              onClick={() => setPalette(c)}
              className={cn(
                "flex min-h-11 min-w-0 flex-col justify-center gap-1 whitespace-nowrap rounded-lg border px-2 py-1 text-left text-sm leading-tight transition-colors",
                isAuto && "col-span-2",
                "sm:flex-auto",
                sel ? `${CHIP_ON} font-semibold` : CHIP_OFF,
              )}
            >
              {isAuto ? (
                <>
                  <span className="flex items-center gap-1 truncate">
                    {spin && <Spinner className="h-3.5 w-3.5" />}
                    {label}
                  </span>
                  <span className="truncate text-xs font-normal text-muted-foreground">{pick === "auto" && nowLabel ? `Now: ${nowLabel}` : "Palette"}</span>
                </>
              ) : (
                <>
                  <Swatch colours={c.swatch} />
                  <span className="flex items-center gap-1 truncate">
                    {spin && <Spinner className="h-3.5 w-3.5" />}
                    {label}
                  </span>
                </>
              )}
            </button>
          );
        })}
      </div>

      {/* Describe it: say how it should feel, in your own words (out of More: owner's walk, 24 Oct) */}
      <div data-testid="music-describe" className={cn(!describeShown && "sm:hidden")}>
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            describeIt();
          }}
        >
          <Input
            value={text}
            onChange={(e) => setText(e.target.value)}
            maxLength={300}
            placeholder="Describe it, e.g. smoky jazz club"
            aria-label="Describe how it should feel"
            enterKeyHint="send"
            autoFocus={describeOpen}
            className="h-11 min-w-0 flex-1 rounded-xl bg-white text-base md:text-base"
          />
          <Button type="submit" className={cn(TAP_BUTTON_CLASS, "shrink-0 px-4")}>
            {working && <Spinner />} Go
          </Button>
        </form>
        {working ? (
          <p className="mt-1.5 text-sm text-muted-foreground">Working on it…</p>
        ) : (
          describe?.state === "done" &&
          describe.reply && (
            <p className="mt-1.5 text-sm leading-snug">
              {describe.text && <span className="text-muted-foreground">“{describe.text}”: </span>}
              {describe.reply}
            </p>
          )
        )}
        {!working && describe?.state === "done" && describe.suggest?.mode && (
          <button
            type="button"
            onClick={() => switchTo(describe.suggest!.mode, `switch to ${suggestLabel}`)}
            className="mt-1.5 inline-flex min-h-11 items-center gap-2 rounded-xl border border-teal-300 bg-teal-50 px-4 text-[15px] font-semibold text-teal-900 hover:bg-teal-100"
          >
            {busy("mode") && <Spinner />} Switch to {suggestLabel}?
          </button>
        )}
      </div>

      {/* What's been changed: each one undoes with its ×, or all at once */}
      {chips.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5" data-testid="music-chips">
          {chips.map((c) => (
            <span
              key={c.id}
              className="inline-flex min-h-11 max-w-full items-center gap-0.5 rounded-full border border-teal-200 bg-teal-50 pl-3.5 pr-1 text-[15px] text-teal-900"
            >
              <span className="truncate">{c.label || "A change"}</span>
              <button
                type="button"
                aria-label={`Clear ${c.label || "this change"}`}
                onClick={() => clear(c)}
                className="grid h-9 w-9 shrink-0 place-items-center rounded-full hover:bg-teal-100"
              >
                {busy(`chip:${c.id}`) ? <Spinner /> : <X className="h-4 w-4" aria-hidden />}
              </button>
            </span>
          ))}
          <button
            type="button"
            onClick={backToAuto}
            className="inline-flex min-h-11 items-center gap-1.5 rounded-full px-3 text-[15px] font-medium text-stone-700 underline underline-offset-2 hover:text-stone-900"
          >
            {busy("all") && <Spinner />} Clear all
          </button>
        </div>
      )}

      <NoteLine note={main.note} />
    </div>
  );
}

/**
 * Listening, all in one place: the room mic (whether it's in use, and for what; heard here, on PC22) with its input,
 * AI listening (short clips to an online AI: cost and round trips), then what it hears and the levels. The Mic and AI
 * switches are the top bar's (TopToggles.tsx), nowhere else. The piano's MIDI line is in the Room page's Piano fold.
 */
export function ListeningPanel({
  api,
  feed,
  now,
}: {
  api: RoomApi;
  feed: ShowFeed;
  /** The page's 1 s clock (whether a polled card is still fresh). */
  now: number;
  addLog?: (e: NewLogEntry) => void;
}) {
  const { card, live, lastAt } = useShowState(feed);
  const answering = !!card && (live || (lastAt !== null && now - lastAt < FRESH_MS));
  const hearing = card?.hearing;
  const vibeLabel = card?.vibe?.label || null;

  // ── The mic: on only while something needs it (Auto, or a mode that follows the music). Its switch is the top bar's
  // Mic (on = the room may listen; off always wins); here, whether it's in use right now, and for what. ──
  const mic = card?.mic_state ?? null;
  const { allowed: micAllowed, listening: micListening, line: micLine } = micWords(mic);
  const claimWords = (mic?.claims ?? []).map((c) => (c.by === "auto" ? "Auto" : c.label || mic?.mode || "This mode"));
  // Off while something wants it: say who loses the room's sound.
  const micMissed =
    mic && !micAllowed && (mic.wanted || claimWords.length || card?.auto?.on === true)
      ? `${claimWords.length ? claimWords.join(" and ") : "Auto"} can't hear the room. It still sees the piano and Sonos.`
      : null;

  const level = answering ? clamp01(hearing?.level) : 0;
  // The live input meter: show_service's per-packet levels (WS /v1/levels), beside the card's own socket.
  const levelsUrl = useMemo(() => api.showEventsUrl().replace(/\/v1\/events$/, "/v1/levels"), [api]);

  return (
    <div className={cn(CARD_CLASS, "space-y-1.5 px-3 py-2 sm:px-3.5")} data-testid="music-card">
      {/* Owner, 25 Oct: compact rows. The room mic and its input on one row (wrapping on a phone), AI listening in one
          line with its cost, what it hears in one line; the levels stay rolled up. */}
      <div className="space-y-1.5 border-b border-stone-100 pb-2" data-testid="music-listening">
        {mic && (
          <div>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <p className="flex min-h-11 min-w-[13rem] flex-1 items-center gap-2 text-[15px] leading-snug" title="Switch it with Mic at the top">
                <span
                  aria-hidden
                  className={cn("h-2.5 w-2.5 shrink-0 rounded-full", micListening ? "bg-rose-600 motion-safe:animate-pulse" : "bg-stone-300")}
                />
                <span className="min-w-0">
                  <span className="font-semibold">Room mic</span>
                  {micLine && (
                    <span className="text-stone-700">
                      {" · "}
                      <span data-testid="music-mic-line">{micLine}</span>
                    </span>
                  )}
                </span>
              </p>
              <MicPicker api={api} className="w-full sm:w-auto sm:min-w-[15rem] sm:max-w-[22rem] sm:flex-1" />
            </div>
            {micMissed && <p className="text-sm leading-snug text-amber-800">{micMissed}</p>}
          </div>
        )}
        <AiListeningRow api={api} feed={feed} />
      </div>

      {/* What it hears */}
      <div>
        <div className="flex min-h-11 flex-wrap items-center gap-x-2 gap-y-1">
          <p className="min-w-0 flex-1 text-[15px] leading-snug">
            <span className="text-muted-foreground">Hears </span>
            <span
              className={cn("text-base font-semibold first-letter:uppercase", !answering && "text-stone-500")}
              data-testid="music-hearing"
            >
              {/* show_service's catch-all "Listening" (sound, but nothing it recognises) would read like the mic's state */}
              {card ? (!hearing?.text || hearing.text === "Listening" ? (hearing?.test && level > 0 ? "Test music" : "Not sure yet") : hearing.text) : "Nothing yet"}
            </span>
            {answering && hearing?.test && level > 0 && !!hearing.text && hearing.text !== "Listening" && (
              <span className="text-sm text-muted-foreground"> · from the test music</span>
            )}
          </p>
          {answering && vibeLabel && <Pill tone="live">Feel: {vibeLabel}</Pill>}
        </div>
        {answering && <AiHears listening={card?.listening} />}
        {!mic && <MicPicker api={api} />}
        {!answering && (
          <p className="text-sm text-muted-foreground">
            {card ? "The music side stopped answering" : "The music side isn't answering yet"}; the page keeps trying.
          </p>
        )}
        {/* The live meter and spectrum only when asked for (owner, 17 Oct): rolled up, nothing is drawn or fetched. */}
        <Unroll title="Levels and spectrum" className="-mx-1" testId="music-levels">
          <LevelTrace url={levelsUrl} />
          <a
            href={levelsUrl.replace(/^ws/i, "http").replace(/\/v1\/levels$/, "/dev/dsp")}
            target="_blank"
            rel="noreferrer"
            className="mt-1 inline-block text-sm text-stone-600 underline underline-offset-2 hover:text-stone-900"
          >
            DSP monitor ↗
          </a>
        </Unroll>
      </div>
    </div>
  );
}

/** The whole Music card in one piece (Auto, Listening, the director's controls), for a page that wants it whole. */
export function MusicCard({
  api,
  feed,
  now,
  addLog,
}: {
  api: RoomApi;
  feed: ShowFeed;
  now: number;
  addLog: (e: NewLogEntry) => void;
}) {
  return (
    <section aria-label="Music" className="space-y-3">
      <AutoRow api={api} feed={feed} addLog={addLog} />
      <ListeningPanel api={api} feed={feed} now={now} addLog={addLog} />
      <DirectorControls api={api} feed={feed} addLog={addLog} />
    </section>
  );
}

/** What the online AI makes of the music (README S5.22), in show_service's words: its line ("soft late-night jazz
 *  trio") and the cues it has armed ("Big change at the chorus, likely in 10-25 s"). Nothing until it has said something. */
function AiHears({ listening }: { listening: ShowCard["listening"] }) {
  const character = listening?.character?.trim();
  const armed = (listening?.armed ?? []).filter((c) => c?.words?.trim());
  if (!character && !armed.length) return null;
  return (
    <div className="mt-1 space-y-1" data-testid="music-ai-hears">
      {character && (
        <p className="text-base leading-snug text-stone-700" data-testid="music-character">
          <span className="text-muted-foreground">The AI hears </span>
          {character}
        </p>
      )}
      {armed.length > 0 && (
        <ul className="space-y-0.5" data-testid="music-armed">
          {armed.map((c, i) => (
            <li key={`${c.kind}-${i}`} className="flex items-start gap-2 text-sm leading-snug text-teal-900">
              <span aria-hidden className="mt-[0.45rem] h-1.5 w-1.5 shrink-0 rounded-full bg-teal-600" />
              <span className="min-w-0">{c.words}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** AI listening (§7.5, the card's listening block, under the room mic): what it's doing, what it costs, and each round
 *  trip. Its switch is the top bar's AI (with it off, no sound leaves the house; the buttons and Describe it carry on). */
function AiListeningRow({ api, feed }: { api: RoomApi; feed: ShowFeed }) {
  const { card } = useShowState(feed);
  const l = card?.listening;
  const on = !!l?.on;
  const { about, state, cost } = aiWords(l, on);
  // Owner, 21 Oct: on = it sends; the line just says Listening, or show_service's own words for a pause. One line with
  // the cost (owner, 25 Oct); what it does is the line's tooltip.
  const st = !card ? "The music side isn't answering yet" : on ? (state ?? "On") : "Off";
  return (
    <div className="space-y-1" data-testid="music-identify">
      <p className="flex min-h-11 items-center text-[15px] leading-snug" title={`${about}. Switch it with AI at the top.`}>
        <span className="min-w-0">
          <span className="font-semibold">AI listening</span>
          <span className="text-stone-700"> · {st}</span>
          {cost && (
            <span className="text-sm text-amber-800" data-testid="music-identify-cost">
              {" · "}
              {cost}
            </span>
          )}
        </span>
      </p>
      {on && <ListenRounds rounds={l?.history} base={api.baseUrl} />}
    </div>
  );
}

/** Each AI listening round trip in one plain line, newest first (owner, 21 Oct: see what it returns on each round
 *  trip). A skip or failure shows its words; nothing here decides anything. */
function ListenRounds({ rounds, base }: { rounds?: ShowListenRound[] | null; base: string }) {
  const list = (rounds ?? []).slice(0, 10);
  const [playing, setPlaying] = useState<string | null>(null);
  const player = useRef<HTMLAudioElement | null>(null);
  useEffect(() => () => player.current?.pause(), []);
  if (!list.length) return null;
  // ▶ plays the exact clip that round trip sent (show_service keeps the last few); ■ stops it.
  const play = (url: string) => {
    player.current?.pause();
    if (playing === url) { setPlaying(null); return; }
    const a = new Audio(`${base}${url}`);
    a.onended = () => setPlaying((p) => (p === url ? null : p));
    a.onerror = () => setPlaying((p) => (p === url ? null : p));
    player.current = a;
    setPlaying(url);
    a.play().catch(() => setPlaying(null));
  };
  const when = (r: ShowListenRound) => {
    if (typeof r.at !== "number") return "";
    const d = new Date(r.at * 1000);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  };
  const genre = (g: ShowListenRound["genre"]) =>
    typeof g === "string" ? g : g ? [g.label, g.detail && g.detail !== g.label ? g.detail : null].filter(Boolean).join(", ") : "";
  const moods = (m: ShowListenRound["moods"]) => (Array.isArray(m) ? m : m?.words ?? []).filter(Boolean).join(", ");
  const coming = (c: ShowListenRound["coming"]) =>
    (c ?? []).filter((x) => x?.kind).map((x) =>
      typeof x.eta_min_s === "number" && typeof x.eta_max_s === "number"
        ? `${x.kind} in ${Math.round(x.eta_min_s)}-${Math.round(x.eta_max_s)} s` : `${x.kind}`).join(", ");
  return (
    <ul className="space-y-1 rounded-md bg-stone-50 px-2 py-1.5" data-testid="music-identify-rounds">
      {list.map((r, i) => {
        const said = [r.character || r.content, genre(r.genre), moods(r.moods)].filter(Boolean).join(" · ");
        const next = coming(r.coming);
        const meta = [
          typeof r.lufs === "number" ? `heard ${Math.round(r.lufs)} LUFS${typeof r.gain_db === "number" ? ` +${Math.round(r.gain_db)} dB` : ""}` : null,
          r.gaps ? `${r.gaps} dropout${r.gaps === 1 ? "" : "s"} (${(r.gaps_s ?? 0).toFixed(1)} s)` : null,
          r.device || null,
          typeof r.latency_ms === "number" ? `${(r.latency_ms / 1000).toFixed(1)} s` : null,
          typeof r.cost_usd === "number" ? `$${r.cost_usd.toFixed(4)}` : null,
        ].filter(Boolean).join(" · ");
        return (
          <li key={`${r.t_ms ?? r.at ?? i}-${i}`} className="text-sm leading-snug">
            {r.clip_url && (
              <button type="button" onClick={() => play(r.clip_url!)} aria-label={playing === r.clip_url ? "Stop" : "Play what the AI heard"}
                className="mr-1 inline-flex h-6 w-6 items-center justify-center rounded-full bg-teal-700 text-xs text-white align-middle">
                {playing === r.clip_url ? "■" : "▶"}
              </button>
            )}
            <span className="tabular-nums text-muted-foreground">{when(r)} </span>
            {r.ok === false ? (
              <span className="text-muted-foreground">{r.why || r.error || "nothing came back"}</span>
            ) : (
              <span className="text-stone-800">{said || "(no words)"}</span>
            )}
            {next && <span className="text-teal-800"> · expects {next}</span>}
            {meta && <span className="text-xs text-muted-foreground"> · {meta}</span>}
          </li>
        );
      })}
    </ul>
  );
}
