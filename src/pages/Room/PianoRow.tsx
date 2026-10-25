// The piano's MIDI, seen live (owner, 20 Oct: "need to know that both those ports are working ... signal / evidence on
// the rooms page itself where I can look at it while testing"). Two sources, both through room_service's /show
// pass-through:
//   - whether show_service hears the piano's stream at all: GET /show/api/v1/inputs `pianos` (the receiver's health:
//     "streaming" = a key or pedal in the last 30 s, "quiet" = connected, nothing played, anything else = nothing
//     arriving from the mini-PC), polled every 5 s;
//   - every key and pedal as it arrives: WS /show/v1/show with want music.note + music.pedal (silent while nobody
//     plays). The dot lights on each key; the line names the last key, its velocity and how long ago.
// What Auto makes of it (its rule and countdown) is under the Auto switch's "How Auto picks ›" (MusicCard.tsx AutoRow).
// The row sits at the top of the Room page's Piano fold, above the recorder.
// Nothing to set here: the row only reports.

import { useCallback, useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import type { RoomApi } from "./api";

const POLL_MS = 5_000;
const RETRY_MS = 2_000;
const FLASH_MS = 220;
const RECENT_MS = 10_000;
const NAMES = ["C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B"];
const PEDAL_WORDS: Record<string, string> = { damper: "Sustain", soft: "Soft", sostenuto: "Middle" };
const CONNECTED = ["streaming", "quiet"];

type Piano = { stream: string; sender?: string; label?: string; state?: string };
type Key = { name: string; vel: number; at: number; test: boolean };

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

/** `onConnected`: told whether the piano's MIDI is coming in (null: no piano known), e.g. for a fold's summary. */
export function PianoRow({
  api,
  onConnected,
  className,
}: {
  api: RoomApi;
  onConnected?: (connected: boolean | null) => void;
  className?: string;
}) {
  const [pianos, setPianos] = useState<Piano[] | null>(null);
  // the music side's own clock of the last note (not just since this page opened)
  const [lastAge, setLastAge] = useState<{ s: number; at: number } | null>(null);
  const [last, setLast] = useState<Key | null>(null);
  const [recent, setRecent] = useState<number[]>([]);
  const [pedals, setPedals] = useState<Record<string, number>>({});
  const [flash, setFlash] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  const load = useCallback(async () => {
    try {
      const body = (await api.showInputs()) as { pianos?: Piano[]; piano?: { last_note_age_s?: number | null } | null };
      setPianos(Array.isArray(body.pianos) ? body.pianos : null);
      const a = body.piano?.last_note_age_s;
      setLastAge(typeof a === "number" ? { s: a, at: Date.now() } : null);
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

  const known = !!pianos?.length || !!last;
  const linked = !known ? null : !pianos?.[0] || CONNECTED.includes(pianos[0].state ?? "");
  const tell = useRef(onConnected);
  tell.current = onConnected;
  useEffect(() => tell.current?.(linked), [linked]);

  if (!pianos?.length && !last) return null;

  const piano = pianos?.[0] ?? null;
  const connected = !piano || CONNECTED.includes(piano.state ?? "");
  const keys = recent.filter((t) => now - t < RECENT_MS).length;
  const down = Object.entries(pedals)
    .filter(([, v]) => v > 0.1)
    .map(([k]) => PEDAL_WORDS[k] ?? k);

  // "Relay on" is all that the sender's heartbeat proves: the mini-PC's MIDI relay is running. Whether the piano is
  // sending shows only in its notes (owner, 25 Oct: '"Connected" but clearly they are NOT connected ... highly
  // misleading'): a dead USB link and an idle piano look the same until a key is played.
  const sinceNote = lastAge ? lastAge.s + (now - lastAge.at) / 1000 : null;
  let line: string;
  if (!connected) line = `Relay off: nothing is coming from ${piano?.label || "the mini-PC"}`;
  else if (!last && sinceNote === null) line = "Relay on · no notes received yet · play a key to check the piano";
  else if (!last && sinceNote !== null) line = `Relay on · no notes for ${ago(sinceNote * 1000).replace(" ago", "")} · if the piano is being played, its USB MIDI isn't sending`;
  else
    line = `Last key ${last.name}, velocity ${last.vel}${last.test ? " (test piano)" : ""} · ${ago(now - last.at)}${
      keys > 1 ? ` · ${keys} keys in 10 s` : ""
    }`;

  return (
    <div className={cn("space-y-1", className)} data-testid="music-piano">
      <div className="flex min-h-11 items-center gap-3">
        <p className="min-w-0 flex-1 text-base font-semibold leading-snug">Keys and pedals</p>
        <span className={cn("shrink-0 text-sm", connected ? "text-teal-800" : "text-amber-800")}>
          {connected ? "Relay on" : "Relay off"}
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
      {/* always there while connected, so the card doesn't jump with every pedal (owner, 23 Oct: "glitchy ui which
          vertical height of component changes rapidly when sustain pressed & released") */}
      {connected && (
        <p className="text-sm leading-snug text-stone-600">{down.length > 0 ? `Pedal down: ${down.join(", ")}` : "Pedals up"}</p>
      )}
    </div>
  );
}
