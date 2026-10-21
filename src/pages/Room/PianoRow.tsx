// The piano's MIDI, seen live (owner, 20 Oct: "need to know that both those ports are working ... signal / evidence on
// the rooms page itself where I can look at it while testing"). Two sources, both through room_service's /show
// pass-through:
//   - whether show_service hears the piano's stream at all: GET /show/api/v1/inputs `pianos` (the receiver's health:
//     "streaming" = a key or pedal in the last 30 s, "quiet" = connected, nothing played, anything else = nothing
//     arriving from the mini-PC), polled every 5 s;
//   - every key and pedal as it arrives: WS /show/v1/show with want music.note + music.pedal (silent while nobody
//     plays). The dot lights on each key; the line names the last key, its velocity and how long ago.
// Under it, what Auto makes of it (owner, 20 Oct: "didn't change automatically to piano after playing midi for 15s"):
// show_service's autopilot candidate for the mode whose rule is "the piano is playing" (GET /show/api/v1/autopilot,
// every 2 s: counting down, or why it won't switch) and, while someone has just played, why "the piano is playing"
// is still false (GET /show/api/v1/situations: too few notes, too few different keys), with
// show.yaml's numbers from the word's own vocabulary. The mode's name and rule come from /show/api/v1/autopilot/rules.
// Nothing to set here: the row only reports.

import { useCallback, useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import type { RoomApi } from "./api";

const POLL_MS = 5_000;
const RETRY_MS = 2_000;
const FLASH_MS = 220;
const RECENT_MS = 10_000;
const AUTO_MS = 2_000;
const RULES_MS = 60_000;
/** Why "the piano is playing" is false is fetched only this long after the last key. */
const WHY_FOR_MS = 30_000;
const PLAYING = "piano_playing";
const NAMES = ["C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B"];
const PEDAL_WORDS: Record<string, string> = { damper: "Sustain", soft: "Soft", sostenuto: "Middle" };
const CONNECTED = ["streaming", "quiet"];

type Piano = { stream: string; sender?: string; label?: string; state?: string };
type Key = { name: string; vel: number; at: number; test: boolean };
type Candidate = { mode: string; kind: string; rule?: string; held_s?: number | null; needs_s?: number | null; blocked?: string | null; holds?: boolean };
type Autopilot = { on?: boolean; state?: string; line?: string; candidates?: Candidate[] };
type RuleMode = { mode: string; label?: string; enter?: string | null };
type Reading = { value?: boolean | null; evidence?: Record<string, unknown> | null };
type Word = { id: string; label?: string; params?: Record<string, { default?: unknown }> };

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

async function getJson<T>(api: RoomApi, path: string): Promise<T> {
  const res = await fetch(`${api.baseUrl}${path}`, { signal: AbortSignal.timeout(5_000) });
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return (await res.json()) as T;
}

/** Why "the piano is playing" is false, in words, from its evidence and show.yaml's numbers (the word's defaults). */
function notYet(ev: Record<string, unknown>, word: Word | null): string | null {
  const d = (k: string) => num(word?.params?.[k]?.default);
  const window = num(ev.window_s) ?? d("window_s");
  const within = window ? ` in ${window} s` : "";
  if (typeof ev.why === "string") return ev.why;
  const notes = num(ev.notes) ?? 0;
  const pitches = num(ev.pitches) ?? 0;
  const span = num(ev.span_s) ?? 0;
  const [minNotes, minPitches, minSpan] = [d("min_notes"), d("min_pitches"), d("min_span_s")];
  if (minPitches !== null && pitches < minPitches)
    return `only ${pitches} different key${pitches === 1 ? "" : "s"}${within}; it needs ${minPitches} (a tune, not one or two keys)`;
  if (minNotes !== null && notes < minNotes) return `only ${notes} notes${within}; it needs ${minNotes}`;
  if (minSpan !== null && span < minSpan) return `keep playing (${Math.round(span)} of ${minSpan} s)`;
  return null;
}

/** MIDI pitch -> "C4" (middle C = 60). */
function noteName(p: number): string {
  return `${NAMES[((p % 12) + 12) % 12]}${Math.floor(p / 12) - 1}`;
}

function ago(ms: number): string {
  const s = Math.round(ms / 1000);
  if (s < 2) return "just now";
  if (s < 60) return `${s} s ago`;
  const m = Math.round(s / 60);
  return m < 60 ? `${m} min ago` : "over an hour ago";
}

export function PianoRow({ api }: { api: RoomApi }) {
  const [pianos, setPianos] = useState<Piano[] | null>(null);
  const [last, setLast] = useState<Key | null>(null);
  const [recent, setRecent] = useState<number[]>([]);
  const [pedals, setPedals] = useState<Record<string, number>>({});
  const [flash, setFlash] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const [auto, setAuto] = useState<Autopilot | null>(null);
  const [rules, setRules] = useState<RuleMode[]>([]);
  const [word, setWord] = useState<Word | null>(null);
  const [reading, setReading] = useState<Reading | null>(null);
  const lastKey = useRef(0);

  const load = useCallback(async () => {
    try {
      const body = (await api.showInputs()) as { pianos?: Piano[] };
      setPianos(Array.isArray(body.pianos) ? body.pianos : null);
    } catch {
      /* show_service not answering: the row keeps what it had */
    }
  }, [api]);

  useEffect(() => {
    void load();
    const id = window.setInterval(() => void load(), POLL_MS);
    const tick = window.setInterval(() => setNow(Date.now()), 1000);
    return () => {
      window.clearInterval(id);
      window.clearInterval(tick);
    };
  }, [load]);

  // What Auto makes of it: the candidate every 2 s; why the word is false only while someone has just played.
  useEffect(() => {
    const rulesNow = async () => {
      try {
        const r = await getJson<{ modes?: RuleMode[] }>(api, "/show/api/v1/autopilot/rules");
        setRules(Array.isArray(r.modes) ? r.modes : []);
      } catch {
        /* older show_service or not answering: no Auto line */
      }
    };
    const situations = async () => {
      try {
        const s = await getJson<{ vocabulary?: Word[]; readings?: Record<string, Reading> }>(api, "/show/api/v1/situations");
        setWord(s.vocabulary?.find((w) => w.id === PLAYING) ?? null);
        setReading(s.readings?.[PLAYING] ?? null);
      } catch {
        /* not answering: keep the last */
      }
    };
    const autoNow = async () => {
      try {
        setAuto(await getJson<Autopilot>(api, "/show/api/v1/autopilot"));
      } catch {
        /* not answering: keep the last */
      }
      if (Date.now() - lastKey.current < WHY_FOR_MS) void situations();
    };
    void rulesNow();
    void situations();
    void autoNow();
    const a = window.setInterval(() => void autoNow(), AUTO_MS);
    const r = window.setInterval(() => void rulesNow(), RULES_MS);
    return () => {
      window.clearInterval(a);
      window.clearInterval(r);
    };
  }, [api]);

  // The keys, live: reconnects every 2 s while the socket is down.
  useEffect(() => {
    const url = api.showEventsUrl().replace(/\/v1\/events$/, "/v1/show");
    let sock: WebSocket | null = null;
    let retry: number | undefined;
    let off: number | undefined;
    let closed = false;
    const open = () => {
      sock = new WebSocket(url);
      sock.onopen = () =>
        sock?.send(JSON.stringify({ type: "hello", v: 2, role: "room-page", client_id: "room-piano-row", want: ["music.note", "music.pedal"] }));
      sock.onmessage = (ev) => {
        try {
          const m = JSON.parse(ev.data as string) as { type?: string; data?: Record<string, unknown> };
          const d = m.data ?? {};
          if (m.type === "music.note" && d.on === true && typeof d.pitch === "number") {
            const at = Date.now();
            lastKey.current = at;
            setLast({ name: noteName(d.pitch), vel: typeof d.vel === "number" ? d.vel : 0, at, test: d.source === "test" });
            setRecent((r) => [...r.filter((t) => at - t < RECENT_MS), at]);
            setNow(at);
            setFlash(true);
            window.clearTimeout(off);
            off = window.setTimeout(() => setFlash(false), FLASH_MS);
          } else if (m.type === "music.pedal" && typeof d.pedal === "string" && typeof d.value === "number") {
            setPedals((p) => ({ ...p, [d.pedal as string]: d.value as number }));
          }
        } catch {
          /* a message we can't read: skipped */
        }
      };
      sock.onclose = () => {
        if (!closed) retry = window.setTimeout(open, RETRY_MS);
      };
    };
    open();
    return () => {
      closed = true;
      window.clearTimeout(retry);
      window.clearTimeout(off);
      sock?.close();
    };
  }, [api]);

  if (!pianos?.length && !last) return null;

  const piano = pianos?.[0] ?? null;
  const connected = !piano || CONNECTED.includes(piano.state ?? "");
  const keys = recent.filter((t) => now - t < RECENT_MS).length;
  const down = Object.entries(pedals)
    .filter(([, v]) => v > 0.1)
    .map(([k]) => PEDAL_WORDS[k] ?? k);

  let line: string;
  if (!connected) line = `Not connected: nothing is coming from ${piano?.label || "the mini-PC"}`;
  else if (!last) line = "Connected · play a key to check it";
  else
    line = `Last key ${last.name}, velocity ${last.vel}${last.test ? " (test piano)" : ""} · ${ago(now - last.at)}${
      keys > 1 ? ` · ${keys} keys in 10 s` : ""
    }`;

  // Auto's line: the mode whose rule is "the piano is playing" (its sentence names the word's label).
  const wordLabel = word?.label ?? "the piano is playing";
  const target = rules.find((m) => typeof m.enter === "string" && m.enter.includes(wordLabel)) ?? null;
  const cand = target ? auto?.candidates?.find((c) => c.kind === "enter" && c.mode === target.mode) ?? null : null;
  const modeLabel = target?.label || target?.mode || "";
  const played = !!last && now - last.at < WHY_FOR_MS;
  let autoLine: string | null = null;
  if (target && auto) {
    const needs = num(cand?.needs_s);
    if (auto.on === false) autoLine = `Auto is off, so it won't switch to ${modeLabel} by itself.`;
    // paused, waiting after a switch that didn't finish, switching now: Auto's own row above says it, so no line here
    else if (auto.state && auto.state !== "watching") autoLine = null;
    else if (cand?.blocked === "it's the current mode") autoLine = `Auto: in ${modeLabel} now.`;
    else if (cand?.blocked) autoLine = `Auto won't switch to ${modeLabel}: ${cand.blocked}.`;
    else if (cand?.holds && needs !== null)
      autoLine = `Auto: switching to ${modeLabel} in ${Math.max(0, Math.ceil(needs - (num(cand.held_s) ?? 0)))} s.`;
    else if (played && !last?.test && reading?.value === false && reading.evidence) {
      const why = notYet(reading.evidence, word);
      autoLine = `Auto isn't counting yet${why ? `: ${why}` : ""}.`;
    } else if (target.enter) autoLine = `Auto switches to ${modeLabel} when ${target.enter}.`;
  }

  return (
    <div className="space-y-1" data-testid="music-piano">
      <div className="flex min-h-11 items-center gap-3">
        <p className="min-w-0 flex-1 text-base font-semibold leading-snug">Piano</p>
        <span className={cn("shrink-0 text-sm", connected ? "text-teal-800" : "text-amber-800")}>
          {connected ? "Connected" : "Not connected"}
        </span>
      </div>
      <div className="flex items-center gap-2">
        <span
          aria-hidden
          className={cn(
            "h-2.5 w-2.5 shrink-0 rounded-full transition-colors duration-150",
            flash ? "scale-125 bg-teal-400" : connected ? "bg-teal-700" : "bg-stone-300",
          )}
        />
        <p className="min-w-0 flex-1 text-sm leading-snug text-stone-700" data-testid="music-piano-line">
          {line}
        </p>
      </div>
      {connected && down.length > 0 && <p className="text-sm leading-snug text-stone-600">Pedal down: {down.join(", ")}</p>}
      {connected && autoLine && (
        <p className="text-sm leading-snug text-stone-600" data-testid="music-piano-auto">
          {autoLine}
        </p>
      )}
    </div>
  );
}
