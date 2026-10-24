// The piano's Record / Stop, shared by the Room page's Piano card and a piece's score on /music (owner, 24 Oct:
// "record should be on score page as well (if it's 'mine')"). One recorder in show_service (POST
// /show/api/v1/piano/record {on, piece?, section?}); these are its looks and words: the button (Record, or Stop with
// the time), the line beside it (a dot that flashes on each key, the notes so far), and a link to a take's score.

import { useEffect, useState } from "react";
import { Circle, Square } from "lucide-react";
import { cn } from "@/lib/utils";
import { Spinner } from "./bits";
import { TAP_BUTTON_CLASS } from "./theme";

const FLASH_MS = 220;
const RETRY_MS = 2000;
const SECONDARY = "border border-stone-300 bg-white hover:bg-stone-50";

/** 83 -> "1:23"; 3725 -> "1:02:05". */
export function clock(s: number | null | undefined): string {
  const t = Math.max(0, Math.floor(typeof s === "number" && Number.isFinite(s) ? s : 0));
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const ss = String(t % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

const plural = (n: number, one: string) => `${n} ${one}${n === 1 ? "" : "s"}`;

/** Where a score was opened from, in the words its back control uses ("Room", "Music", …). */
export function pageFrom(pathname: string = window.location.pathname): string {
  const p = pathname.replace(/\/+$/, "") || "/";
  if (p === "/" || p === "/room") return "Room";
  if (p === "/music") return "Music";
  if (p === "/lights") return "Lights";
  if (p === "/scheduler") return "Schedules";
  return "Back";
}

/** What a score's link carries: it was opened from the app (Back goes back there), and from which page. */
export interface ScoreState {
  score: true;
  from: string;
}

/** A piece's score on the Music page (written from its MIDI the first time it's opened), keeping ?room=, with the
 *  page it was opened from in the history state: <Link to={l.to} state={l.state}> or navigate(l.to, {state: l.state}). */
export function scoreLink(pieceId: string, from: string = pageFrom()): { to: string; state: ScoreState } {
  const q = new URLSearchParams(window.location.search);
  const out = new URLSearchParams();
  const room = q.get("room");
  if (room) out.set("room", room);
  out.set("score", pieceId);
  return { to: `/music?${out.toString()}`, state: { score: true, from } };
}

/** room_service's address -> the show bus socket (WS /show/v1/show). */
export function showSocketUrl(baseUrl: string): string {
  return `${baseUrl.replace(/\/+$/, "").replace(/^http/i, "ws")}/show/v1/show`;
}

/** True for a moment on each key while `on` (the show bus's music.note, the room's piano only). */
export function useKeyFlash(socketUrl: string, on: boolean): boolean {
  const [flash, setFlash] = useState(false);
  useEffect(() => {
    if (!on) return;
    let sock: WebSocket | null = null;
    let retry: number | undefined;
    let off: number | undefined;
    let closed = false;
    const open = () => {
      sock = new WebSocket(socketUrl);
      sock.onopen = () =>
        sock?.send(JSON.stringify({ type: "hello", v: 2, role: "room-page", client_id: "room-piano-recorder", want: ["music.note"] }));
      sock.onmessage = (ev) => {
        try {
          const m = JSON.parse(ev.data as string) as { type?: string; data?: Record<string, unknown> };
          if (m.type === "music.note" && m.data?.on === true && m.data?.source !== "test") {
            setFlash(true);
            window.clearTimeout(off);
            off = window.setTimeout(() => setFlash(false), FLASH_MS);
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
      setFlash(false);
    };
  }, [socketUrl, on]);
  return flash && on;
}

/** Record (rose) or Stop with the time recorded. */
export function RecordButton({
  recording,
  busy,
  disabled,
  elapsed,
  onClick,
  label = "Record",
}: {
  recording: boolean;
  busy: boolean;
  disabled?: boolean;
  elapsed: number;
  onClick: () => void;
  label?: string;
}) {
  return (
    <button
      type="button"
      disabled={busy || disabled}
      onClick={onClick}
      className={cn(
        TAP_BUTTON_CLASS,
        "inline-flex h-12 min-w-[9.5rem] items-center justify-center gap-2 text-[17px] font-semibold disabled:opacity-60",
        recording ? SECONDARY : "bg-rose-700 text-white hover:bg-rose-800",
      )}
    >
      {busy ? (
        <Spinner />
      ) : recording ? (
        <Square className="h-4 w-4 fill-current text-rose-700" aria-hidden />
      ) : (
        <Circle className="h-4 w-4 fill-current" aria-hidden />
      )}
      {recording ? (
        <>
          Stop <span className="font-mono tabular-nums">{clock(elapsed)}</span>
        </>
      ) : (
        label
      )}
    </button>
  );
}

/** "● Recording · 12 notes" (the dot flashes on each key). */
export function RecordingLine({ notes, flash }: { notes: number; flash: boolean }) {
  return (
    <p className="flex min-w-0 flex-1 items-center gap-2 text-base" role="status">
      <span
        aria-hidden
        className={cn(
          "h-2.5 w-2.5 shrink-0 rounded-full transition-transform duration-150",
          flash ? "scale-150 bg-rose-500" : "bg-rose-600 motion-safe:animate-pulse",
        )}
      />
      <span className="min-w-0">
        Recording · <span className="tabular-nums">{plural(notes, "note")}</span>
      </span>
    </p>
  );
}
