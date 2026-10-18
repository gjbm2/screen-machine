// The Music card (SHOW_ENGINE §10.1): what the show engine hears, and the few nudges a person needs, in plain words.
// It sits directly under the mode tiles: one per room with an audio source. show_service hears one room today and
// its card names none, so the page draws one. Everything comes from show_service's card through room_service's
// /show pass-through (show_service/README.md S3.9): GET /show/api/v1/card, and WS /show/v1/events (a welcome with the
// whole card, then RFC 7386 merge patches at 2 Hz, chip changes at once), with a 5 s poll while that socket is down.
//   - Auto, the room-level one (it picks the mode by itself): its switch, its line, Resume, and the
//     "Auto → Piano · the piano is playing · Undo" chip after a switch. Nothing else on the page is called "Auto".
//   - Listening: the Room mic switch and its input, then AI listening (short clips to an online AI). Both
//     listening switches live here, together; the menu has no controls.
//   - What it hears: "soul · relaxed" (or Quiet, Talking, No sound coming in) with a level bar, and "following loosely".
//   - Calmer · Wilder, the palette, Next look · Keep this look · House lights.
//   - The active nudges as chips, each with an ×, and Back to auto.
//   - More: Lamp brightness (the Lights low toggle, c8's ruling, S3.9) and Describe it.
// Nothing is ever disabled: a tap the music side can't take yet
// is sent again for half a minute, and the card says so. The words are show_service's own or written here; never an id.

import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { Feather, Flame, Image as ImageIcon, Lamp, Pin, SkipForward, X, type LucideIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import { RoomApi, RoomApiError } from "./api";
import { Pill, Spinner, Unroll } from "./bits";
import { LevelTrace } from "./LevelTrace";
import { MicPicker } from "./MicPicker";
import { CARD_CLASS, TAP_BUTTON_CLASS } from "./theme";
import { TEXT_CLASSES, TONE_CLASSES, type Tone } from "./tones";
import type { ShowAutoNote, ShowCard, ShowChip, ShowEvent, ShowMicState, ShowPaletteChoice } from "./types";
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

interface Note {
  tone: Tone;
  text: string;
}

/**
 * Sends taps: a spinner per control, a tap that can't reach show_service sent again every 2 s for 30 s (with "trying…"
 * on the card), and one plain line afterwards. `latest`: a newer tap on the same control replaces an older one still
 * trying (a palette pick, a switch). Resolves true when it landed, false when it gave up, undefined when replaced.
 */
function useSend(feed: ShowFeed, addLog: (e: NewLogEntry) => void) {
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

function NoteLine({ note, className }: { note: Note | null; className?: string }) {
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
  if (!cs.length) {
    return (
      <span aria-hidden className="grid h-6 place-items-center rounded-md bg-stone-100 text-stone-500">
        <ImageIcon className="h-4 w-4" />
      </span>
    );
  }
  return (
    <span aria-hidden className="flex h-6 overflow-hidden rounded-md ring-1 ring-inset ring-black/5">
      {cs.map((c, i) => (
        <span key={i} className="flex-1" style={{ background: c }} />
      ))}
    </span>
  );
}

function ActionButton({
  icon: Icon,
  label,
  sub,
  on,
  busy,
  onClick,
}: {
  icon: LucideIcon;
  label: string;
  sub?: string;
  /** A toggle that is on (aria-pressed); undefined for a plain button. */
  on?: boolean;
  busy?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={on}
      onClick={onClick}
      className={cn(
        "flex min-h-[4.5rem] flex-col items-center justify-center gap-1 rounded-xl border px-1.5 py-2 text-center text-[15px] font-medium leading-tight transition-colors",
        on ? CHIP_ON : CHIP_OFF,
      )}
    >
      {busy ? <Spinner className="h-5 w-5" /> : <Icon className="h-5 w-5 shrink-0" aria-hidden />}
      <span>{label}</span>
      {sub && <span className="text-[13px] font-normal text-muted-foreground">{sub}</span>}
    </button>
  );
}

/** Auto's announcement: a switch (with Undo), a hold, an undo, a failure. */
function AutoChip({ chip, busy, onUndo }: { chip: ShowAutoNote; busy: boolean; onUndo: () => void }) {
  const tone: Tone = chip.kind === "failed" || chip.kind === "backoff" ? "warn" : chip.kind === "switch" ? "live" : "muted";
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

// ── The card ──

export function MusicCard({
  api,
  feed,
  now,
  addLog,
}: {
  api: RoomApi;
  feed: ShowFeed;
  /** The page's 1 s clock (whether a polled card is still fresh). */
  now: number;
  addLog: (e: NewLogEntry) => void;
}) {
  const { card, live, lastAt } = useShowState(feed);
  const answering = !!card && (live || (lastAt !== null && now - lastAt < FRESH_MS));
  const main = useSend(feed, addLog);
  const autoSend = useSend(feed, addLog);
  const { send, busy } = main;
  const [pendingAuto, setPendingAuto] = useState<boolean | null>(null);
  const [pendingMute, setPendingMute] = useState<boolean | null>(null);
  const micSend = useSend(feed, addLog);
  const [pendingLow, setPendingLow] = useState<boolean | null>(null);
  const [text, setText] = useState("");

  const auto = card?.auto;
  const hearing = card?.hearing;
  const steps = card?.steps;
  const pick = card?.palette?.pick ?? "auto";
  const choices = (card?.palette?.choices?.length ? card.palette.choices : FALLBACK_CHOICES).filter((c) => !!c?.name);
  const chips: ShowChip[] = (card?.chips ?? []).filter((c) => !!c?.id);
  const describe = card?.describe;
  const vibeLabel = card?.vibe?.label || null;

  // ── The mic: on only while something needs it (Auto, or a mode that follows the music). The owner's switch reads in
  // the same sense as Auto (17 Oct): on = the room may listen; off always wins. ──
  const mic = card?.mic_state ?? null;
  const micAllowed = !(pendingMute ?? mic?.muted === true);
  const claimWords = (mic?.claims ?? []).map((c) => (c.by === "auto" ? "Auto" : c.label || mic?.mode || "This mode"));
  const micListening = !!mic?.on && micAllowed;
  // The switch says whether the room may use the mic; this line says whether it's in use right now, and for what.
  const micLine = !mic
    ? null
    : micListening
      ? `In use · ${
          (mic.claims ?? [])
            .map((c) => (c.by === "auto" ? "for Auto" : `${c.label || mic.mode || "this mode"} follows the music`))
            .join(" · ") || "for the room"
        }`
      : !micAllowed
        ? "Off"
        : mic.wanted
          ? "Not in use · waiting for the mic"
          : "Not in use · nothing needs it right now";
  // Off here while something wants it: say who loses the room's sound.
  const micMissed =
    mic && !micAllowed && (mic.wanted || claimWords.length || (pendingAuto ?? auto?.on === true))
      ? `${claimWords.length ? claimWords.join(" and ") : "Auto"} can't hear the room. It still sees the piano and Sonos.`
      : null;
  const setMicAllowed = (on: boolean) => {
    setPendingMute(!on);
    void micSend
      .send("mic", on ? "Mic on" : "Mic off", () => api.showSetMuted(!on), {
        latest: true,
        ok: (r) => {
          if (isObj(r)) feed.patch({ mic_state: r as ShowMicState });
        },
      })
      .then((r) => r !== undefined && setPendingMute(null));
  };

  // ── Auto ──
  const autoOn = pendingAuto ?? auto?.on === true;
  const rawLine = auto?.line?.trim() || (card ? "Auto" : "Auto · waiting for the music side");
  const resumable = /\s·\s*Resume$/.test(rawLine);
  const autoLine = rawLine.replace(/\s·\s*Resume$/, "");
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
  const switchTo = (mode: string, what: string, via: typeof autoSend) => void via.send("mode", what, () => api.setMode(mode));

  // ── Nudges ──
  const step = (dir: "calmer" | "wilder") =>
    void send(dir, cap(dir), () => api.showStep(dir), {
      ok: (r) =>
        r?.steps?.at_limit
          ? { tone: "muted", text: dir === "calmer" ? "That's as calm as this mode goes." : "That's as wild as this mode goes." }
          : null,
    });
  const setPalette = (c: ShowPaletteChoice) =>
    void send("palette", `palette ${c.label || c.name}`, () => api.showPalette(c.name), { latest: true });
  const next = () =>
    void send("next", "Next look", () => api.showAction("next"), {
      ok: (r) => (r?.detail ? { tone: "muted", text: r.detail } : r?.look && !r.look.next_pending ? { tone: "muted", text: "This mode has no other looks to change to." } : null),
    });
  const keep = () => void send("keep", "Keep this look", () => api.showAction("keep"));
  const house = () => void send("house", card?.house ? "back from house lights" : "House lights", () => api.showAction("house"));
  const setLow = (on: boolean) => {
    setPendingLow(on);
    void send("low", on ? "Lights low" : "lights back up", () => api.showAction("low")).then((ok) => {
      if (ok) setTimeout(() => setPendingLow(null), 3_000);
      else setPendingLow(null);
    });
  };
  useEffect(() => {
    if (pendingLow !== null && !!card?.low === pendingLow) setPendingLow(null);
  }, [card?.low, pendingLow]);
  const clear = (c: ShowChip) => void send(`chip:${c.id}`, `clear ${c.label || "a change"}`, () => api.showClearNudge(c.id));
  const backToAuto = () => void send("all", "Back to auto", () => api.showClearNudge("all"));
  const describeIt = () => {
    const t = text.trim();
    void send("describe", t ? `describe it: “${t}”` : "describe it", () => api.showDescribe(t), { ok: () => setText("") });
  };

  const changing = busy("next") || !!card?.look?.next_pending;
  const keepIn = card?.look?.keep_in_s;
  const keepMin = typeof keepIn === "number" ? Math.max(1, Math.ceil(keepIn / 60)) : null;
  const houseOn = !!card?.house;
  const lowOn = pendingLow ?? !!card?.low;
  const level = answering ? clamp01(hearing?.level) : 0;
  // The live input meter: show_service's per-packet levels (WS /v1/levels), beside the card's own socket.
  const levelsUrl = useMemo(() => api.showEventsUrl().replace(/\/v1\/events$/, "/v1/levels"), [api]);
  const ranged = !!steps && typeof steps.min === "number" && typeof steps.max === "number" && steps.min < steps.max;
  const atEnd = (dir: "calmer" | "wilder") =>
    ranged && typeof steps?.value === "number" && (dir === "calmer" ? steps.value <= steps.min! : steps.value >= steps.max!);
  // While the palette follows the mode (or the art), what it's on now, when that's one of the choices.
  const nowLabel =
    pick === "auto" || pick === "art"
      ? choices.find((c) => c.name === card?.palette?.current && c.name !== "auto" && c.name !== "art")?.label ?? null
      : null;
  const working = busy("describe") || describe?.state === "working";
  const suggestLabel = describe?.suggest ? describe.suggest.label || cap(describe.suggest.mode) : "";

  return (
    <section aria-labelledby="room-music" data-testid="music-card">
      <div className="mb-2 flex items-center justify-between gap-2">
        <h2 id="room-music" className="text-lg font-semibold text-stone-800">
          Music
        </h2>
        {answering && vibeLabel && <Pill tone="live">Feel: {vibeLabel}</Pill>}
      </div>

      <div className={cn(CARD_CLASS, "space-y-4 px-3 py-3 sm:px-4")}>
        {/* Auto: the room picks its own mode */}
        <div className="space-y-2 border-b border-stone-100 pb-3">
          <div className="flex min-h-11 items-center gap-3">
            <div className="min-w-0 flex-1">
              <p className="text-base font-semibold leading-snug" data-testid="music-auto-line">
                {autoLine}
              </p>
              {!autoOn && (
                <p className="text-sm leading-snug text-muted-foreground">On, it picks the mode by itself from what it hears.</p>
              )}
            </div>
            {resumable && (
              <button
                type="button"
                onClick={resume}
                className="inline-flex min-h-11 shrink-0 items-center gap-1.5 rounded-xl border border-stone-200 bg-white px-3 text-[15px] font-medium hover:bg-stone-50"
              >
                {autoSend.busy("resume") && <Spinner />} Resume
              </button>
            )}
            {autoSend.busy("auto") && <Spinner className="shrink-0" />}
            <Switch aria-label="Auto" checked={autoOn} onCheckedChange={setAuto} />
          </div>
          {auto?.chip?.text && <AutoChip chip={auto.chip} busy={autoSend.busy("undo")} onUndo={undo} />}
          {auto?.suggest?.text && auto.suggest.mode && (
            <button
              type="button"
              onClick={() => switchTo(auto.suggest!.mode!, auto.suggest!.text!, autoSend)}
              className="flex min-h-11 w-full items-center justify-center gap-2 rounded-xl border border-teal-300 bg-teal-50 px-3 text-[15px] font-semibold text-teal-900 hover:bg-teal-100"
            >
              {autoSend.busy("mode") && <Spinner />} {auto.suggest.text}
            </button>
          )}
          <NoteLine note={autoSend.note} />
        </div>

        {/* Listening, all in one place: the room mic (on = the room may use it; heard here, on PC22) with its input,
            then whether short clips also go to an online AI. Neither switch is anywhere else. */}
        <div className="space-y-3 border-b border-stone-100 pb-3" data-testid="music-listening">
          {mic && (
            <div className="space-y-1">
              <div className="flex min-h-11 items-center gap-3">
                <p className="min-w-0 flex-1 text-base font-semibold leading-snug">Room mic</p>
                {micSend.busy("mic") && <Spinner className="shrink-0" />}
                <Switch aria-label="Room mic" checked={micAllowed} onCheckedChange={setMicAllowed} />
              </div>
              <div className="flex items-center gap-2">
                <span
                  aria-hidden
                  className={cn("h-2.5 w-2.5 shrink-0 rounded-full", micListening ? "bg-rose-600 motion-safe:animate-pulse" : "bg-stone-300")}
                />
                <p className="min-w-0 flex-1 text-sm leading-snug text-stone-700" data-testid="music-mic-line">
                  {micLine}
                </p>
              </div>
              {micMissed && <p className="text-sm leading-snug text-amber-800">{micMissed}</p>}
              <NoteLine note={micSend.note} />
              <MicPicker api={api} />
            </div>
          )}
          <IdentifyMusicRow api={api} feed={feed} addLog={addLog} />
        </div>

        {/* What it hears */}
        <div>
          <div className="flex items-baseline justify-between gap-2">
            <span className="text-sm text-muted-foreground">What it hears</span>
            {answering && hearing?.loose && <span className="text-sm text-amber-800">following loosely</span>}
          </div>
          <p
            className={cn("text-xl font-semibold leading-snug first-letter:uppercase", !answering && "text-stone-500")}
            data-testid="music-hearing"
          >
            {/* show_service's catch-all "Listening" (sound, but nothing it recognises) would read like the mic's state */}
            {card ? (!hearing?.text || hearing.text === "Listening" ? "Not sure yet" : hearing.text) : "Nothing yet"}
          </p>
          {!mic && <MicPicker api={api} />}
          {/* The live meter and spectrum only when asked for (owner, 17 Oct): rolled up, nothing is drawn or fetched. */}
          <Unroll title="Levels and spectrum" className="mt-1" testId="music-levels">
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
          {!answering ? (
            <p className="mt-1.5 text-sm text-muted-foreground">
              {card ? "The music side stopped answering" : "The music side isn't answering yet"}; the page keeps trying.
            </p>
          ) : (
            hearing?.test && level > 0 && <p className="mt-1.5 text-sm text-muted-foreground">From the test music.</p>
          )}
        </div>

        {/* Calmer · Wilder */}
        <div className="grid grid-cols-2 gap-3">
          {(
            [
              ["calmer", "Calmer", Feather, "as calm as it goes"],
              ["wilder", "Wilder", Flame, "as wild as it goes"],
            ] as const
          ).map(([dir, label, Icon, end]) => (
            <button
              key={dir}
              type="button"
              onClick={() => step(dir)}
              className="flex min-h-[3.75rem] flex-col items-center justify-center rounded-2xl border border-stone-200 bg-white px-2 text-lg font-semibold transition-colors hover:bg-stone-50 active:bg-stone-100"
            >
              <span className="inline-flex items-center gap-2">
                {busy(dir) ? <Spinner className="h-5 w-5" /> : <Icon className="h-5 w-5" aria-hidden />} {label}
              </span>
              {atEnd(dir) && <span className="text-[13px] font-normal text-muted-foreground">{end}</span>}
            </button>
          ))}
        </div>

        {/* Palette */}
        <div>
          <div className="mb-2 flex items-baseline justify-between gap-2">
            <span className="flex items-center gap-2 text-base font-medium">
              Palette {busy("palette") && <Spinner />}
            </span>
            {nowLabel && <span className="truncate text-sm text-muted-foreground">Now: {nowLabel}</span>}
          </div>
          <div role="radiogroup" aria-label="Palette" className="grid grid-cols-3 gap-2 sm:grid-cols-4">
            {choices.map((c) => {
              const sel = c.name === pick;
              const label = c.name === "auto" ? PALETTE_AUTO_WORDS : c.label || cap(c.name);
              return (
                <button
                  key={c.name}
                  type="button"
                  role="radio"
                  aria-checked={sel}
                  onClick={() => setPalette(c)}
                  className={cn(
                    "flex min-h-[4.25rem] min-w-0 flex-col justify-between gap-1.5 rounded-xl border p-2 text-left text-[15px] leading-tight transition-colors",
                    sel ? `${CHIP_ON} font-semibold` : CHIP_OFF,
                  )}
                >
                  <Swatch colours={c.swatch} />
                  <span className="line-clamp-2">{label}</span>
                </button>
              );
            })}
          </div>
        </div>

        {/* Next look · Keep this look · House lights */}
        <div className="grid grid-cols-3 gap-2">
          <ActionButton icon={SkipForward} label={changing ? "Changing…" : "Next look"} busy={changing} onClick={next} />
          <ActionButton
            icon={Pin}
            label="Keep this look"
            sub={keepMin !== null ? `${keepMin} min left` : undefined}
            on={keepMin !== null}
            busy={busy("keep")}
            onClick={keep}
          />
          <ActionButton
            icon={Lamp}
            label="House lights"
            sub={houseOn ? "Tap to go back" : undefined}
            on={houseOn}
            busy={busy("house")}
            onClick={house}
          />
        </div>

        {/* What's been changed: each one undoes with its ×, or all at once */}
        {chips.length > 0 && (
          <div className="flex flex-wrap items-center gap-2" data-testid="music-chips">
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
              {busy("all") && <Spinner />} Back to auto
            </button>
          </div>
        )}

        <NoteLine note={main.note} />

        <Unroll title="More" className="border-t border-stone-100 pt-1" testId="music-more">
          <div className="space-y-5 px-1 pb-2">
            <div>
              <p className="text-base font-medium">Lamp brightness</p>
              <label className="mt-2 flex min-h-11 items-center justify-between gap-3 rounded-xl border border-stone-200 px-3 py-2">
                <span>
                  <span className="block text-base">Lights low</span>
                  <span className="block text-sm text-muted-foreground">Dims the lamps. The screens stay as they are.</span>
                </span>
                <span className="flex shrink-0 items-center gap-2">
                  {busy("low") && <Spinner />}
                  <Switch aria-label="Lights low" checked={lowOn} onCheckedChange={setLow} />
                </span>
              </label>
            </div>

            <div>
              <p className="text-base font-medium">Describe it</p>
              <p className="text-sm text-muted-foreground">Say how it should feel, in your own words.</p>
              <form
                className="mt-2 flex gap-2"
                onSubmit={(e) => {
                  e.preventDefault();
                  describeIt();
                }}
              >
                <Input
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  maxLength={300}
                  placeholder="e.g. smoky jazz club"
                  aria-label="Describe how it should feel"
                  enterKeyHint="send"
                  className="h-11 min-w-0 flex-1 rounded-xl bg-white text-base md:text-base"
                />
                <Button type="submit" className={cn(TAP_BUTTON_CLASS, "shrink-0")}>
                  {working && <Spinner />} Go
                </Button>
              </form>
              {working ? (
                <p className="mt-2 text-[15px] text-muted-foreground">Working on it…</p>
              ) : (
                describe?.state === "done" &&
                describe.reply && (
                  <p className="mt-2 text-[15px]">
                    {describe.text && <span className="text-muted-foreground">“{describe.text}”: </span>}
                    {describe.reply}
                  </p>
                )
              )}
              {!working && describe?.state === "done" && describe.suggest?.mode && (
                <button
                  type="button"
                  onClick={() => switchTo(describe.suggest!.mode, `switch to ${suggestLabel}`, main)}
                  className="mt-2 inline-flex min-h-11 items-center gap-2 rounded-xl border border-teal-300 bg-teal-50 px-4 text-[15px] font-semibold text-teal-900 hover:bg-teal-100"
                >
                  {busy("mode") && <Spinner />} Switch to {suggestLabel}?
                </button>
              )}
            </div>
          </div>
        </Unroll>
      </div>
    </section>
  );
}

/** The online AI switch (§7.5, the card's listening block, under the room mic): with it off, no sound leaves the house;
 *  the buttons and Describe it carry on. */
function IdentifyMusicRow({ api, feed, addLog }: { api: RoomApi; feed: ShowFeed; addLog: (e: NewLogEntry) => void }) {
  const { card } = useShowState(feed);
  const { send, busy, note } = useSend(feed, addLog);
  const [pending, setPending] = useState<boolean | null>(null);
  const l = card?.listening;
  const on = pending ?? !!l?.on;
  const sub = !card
    ? "The music side isn't answering yet."
    : !on || l?.effective || pending !== null
      ? `Lets an online AI${l?.provider ? ` (${l.provider})` : ""} listen to the music and foresee what's coming (a chorus, a climax, the end), so the screens and lights change on time`
      : `Not sending right now${l?.why ? `: ${l.why.replace(/\.+$/, "")}` : ""}`;
  // What it costs (owner, 17 Oct: "guide the user to the fact that this is going to run up the bills"), shown whether
  // it's on or off, so the price is there before switching on. Both numbers are show_service's; none is made up here.
  const money = (v: number) => `$${v.toFixed(2)}`;
  const cost = [
    typeof l?.est_evening_usd === "number" ? `about ${money(l.est_evening_usd)} an evening` : null,
    typeof l?.spend_today_usd === "number" ? `${money(l.spend_today_usd)} so far today` : null,
  ].filter(Boolean).join(" · ");
  const set = (v: boolean) => {
    setPending(v);
    void send("listening", v ? "AI listening on" : "AI listening off", () => api.showListening(v), {
      latest: true,
      ok: (r) => {
        if (r && typeof r.on === "boolean") feed.patch({ listening: { ...l, on: r.on, effective: !!r.effective, why: r.why ?? null } });
      },
    }).then((r) => r !== undefined && setPending(null));
  };
  return (
    <div className="space-y-1" data-testid="music-identify">
      <label className="flex min-h-11 items-center justify-between gap-3">
        <span className="min-w-0">
          <span className="block text-base font-semibold leading-snug">AI listening</span>
          <span className="block text-sm leading-snug text-muted-foreground">{sub}</span>
          {cost && (
            <span className="block text-sm leading-snug text-amber-800" data-testid="music-identify-cost">
              {cost}
            </span>
          )}
        </span>
        <span className="flex shrink-0 items-center gap-2">
          {busy("listening") && <Spinner />}
          <Switch aria-label="AI listening" checked={on} onCheckedChange={set} />
        </span>
      </label>
      <NoteLine note={note} />
    </div>
  );
}
