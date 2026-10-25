// The top bar's two switches, Mic and AI (owner, 24 Oct: "Mic on at top should be a toggle. AI on should prob. also be
// a top toggle -- those are pretty central"). In every page's header (PageHeader.tsx); each switch is only there.
//   - Mic: on = the room may use the mic (PUT /show/api/v1/mic {muted}); it listens while Auto or a mode claims it. A
//     red pulsing dot while it is in use right now.
//   - AI: AI listening (PUT /show/api/v1/listening {on}); on = it sends short clips to an online AI. A teal dot while
//     it is sending.
// The header's status line carries what they add in words ("mic in use for Auto"; topToggleWords); a tap on that line,
// or a long press on a switch, opens the details (each page says where: the Room page's Listening fold, which also
// has what the AI costs; elsewhere /room#listening). Both read the show
// card the page already has (no new polling); a tap shows at once and is sent with the card's own retry pattern.

import { useRef, useState, type MouseEvent as ReactMouseEvent, type PointerEvent as ReactPointerEvent } from "react";
import { cn } from "@/lib/utils";
import type { RoomApi } from "./api";
import { Spinner } from "./bits";
import { aiWords, micWords, useSend, useShowState, type Note, type ShowFeed } from "./MusicCard";
import type { ShowMicState } from "./types";
import type { NewLogEntry } from "./useRoomConnection";

const LONG_PRESS_MS = 550;

export interface TopToggles {
  /** The music side's card has arrived (until then the switches show off and say so). */
  known: boolean;
  mic: { allowed: boolean; listening: boolean; line: string | null; busy: boolean; set: (on: boolean) => void };
  ai: { on: boolean; sending: boolean; state: string | null; cost: string; busy: boolean; set: (on: boolean) => void };
  note: Note | null;
}

export function useTopToggles(api: RoomApi, feed: ShowFeed, addLog: (e: NewLogEntry) => void): TopToggles {
  const { card } = useShowState(feed);
  const { send, busy, note } = useSend(feed, addLog);
  const [pendingMute, setPendingMute] = useState<boolean | null>(null);
  const [pendingAi, setPendingAi] = useState<boolean | null>(null);
  const m = micWords(card?.mic_state, pendingMute);
  const l = card?.listening;
  const aiOn = pendingAi ?? !!l?.on;
  const w = aiWords(l, aiOn);

  const setMic = (on: boolean) => {
    setPendingMute(!on);
    void send("mic", on ? "Mic on" : "Mic off", () => api.showSetMuted(!on), {
      latest: true,
      ok: (r) => {
        if (r && typeof r === "object" && !Array.isArray(r)) feed.patch({ mic_state: r as ShowMicState });
      },
    }).then((r) => r !== undefined && setPendingMute(null));
  };
  const setAi = (on: boolean) => {
    setPendingAi(on);
    void send("listening", on ? "AI listening on" : "AI listening off", () => api.showListening(on), {
      latest: true,
      ok: (r) => {
        if (r && typeof r.on === "boolean") feed.patch({ listening: { ...l, on: r.on, effective: !!r.effective, why: r.why ?? null } });
      },
    }).then((r) => r !== undefined && setPendingAi(null));
  };

  return {
    known: !!card,
    mic: { allowed: m.allowed, listening: m.listening && pendingMute === null, line: m.line, busy: busy("mic"), set: setMic },
    ai: {
      on: aiOn,
      sending: aiOn && pendingAi === null && !!l?.effective,
      state: pendingAi !== null ? null : w.state,
      cost: w.cost,
      busy: busy("listening"),
      set: setAi,
    },
    note,
  };
}

/** A long press calls `onLong` (and the click that follows is dropped); a plain tap calls `onTap`. */
function usePress(onTap: () => void, onLong?: () => void) {
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const fired = useRef(false);
  const clear = () => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
  };
  return {
    onPointerDown: (e: ReactPointerEvent) => {
      if (!onLong || e.button !== 0) return;
      fired.current = false;
      clear();
      timer.current = setTimeout(() => {
        fired.current = true;
        onLong();
      }, LONG_PRESS_MS);
    },
    onPointerUp: clear,
    onPointerLeave: clear,
    onPointerCancel: clear,
    onContextMenu: (e: ReactMouseEvent) => {
      if (onLong) e.preventDefault();
    },
    onClick: () => {
      clear();
      if (fired.current) {
        fired.current = false;
        return;
      }
      onTap();
    },
  };
}

function TopSwitch({
  label,
  name,
  checked,
  active,
  activeClass,
  dotClass,
  busy,
  onChange,
  onDetails,
  testId,
}: {
  label: string;
  /** The full name, for screen readers and the tooltip. */
  name: string;
  checked: boolean;
  /** Working right now (the mic in use, the AI sending): a pulsing dot and a tinted pill. */
  active: boolean;
  activeClass: string;
  dotClass: string;
  busy: boolean;
  onChange: (on: boolean) => void;
  onDetails?: () => void;
  testId: string;
}) {
  const press = usePress(() => onChange(!checked), onDetails);
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={name}
      title={`${name}: ${checked ? "on" : "off"}${onDetails ? " (hold for details)" : ""}`}
      data-testid={testId}
      className="group grid h-11 shrink-0 touch-manipulation select-none place-items-center rounded-full px-0.5 focus-visible:outline-none [-webkit-touch-callout:none]"
      {...press}
    >
      <span
        className={cn(
          "inline-flex h-9 items-center gap-1.5 rounded-full border px-2.5 text-[15px] font-semibold shadow-sm group-focus-visible:ring-2 group-focus-visible:ring-ring",
          active ? activeClass : "border-stone-200/80 bg-card text-stone-700 group-hover:bg-stone-50",
        )}
      >
        {busy ? (
          <Spinner className="h-3.5 w-3.5" />
        ) : (
          active && <span aria-hidden className={cn("h-2 w-2 shrink-0 rounded-full motion-safe:animate-pulse", dotClass)} />
        )}
        {label}
        <span
          aria-hidden
          className={cn("relative h-[18px] w-8 shrink-0 rounded-full transition-colors", checked ? "bg-primary" : "bg-stone-300")}
        >
          <span
            className={cn(
              "absolute left-0.5 top-0.5 h-3.5 w-3.5 rounded-full bg-white shadow transition-transform motion-reduce:transition-none",
              checked && "translate-x-3.5",
            )}
          />
        </span>
      </span>
    </button>
  );
}

/** The two switches, for the title row. */
export function TopToggleButtons({ t, onDetails }: { t: TopToggles; onDetails?: () => void }) {
  return (
    <div className="flex shrink-0 items-center" data-testid="top-toggles">
      <TopSwitch
        label="Mic"
        name="Room mic"
        checked={t.mic.allowed}
        active={t.mic.listening}
        activeClass="border-rose-200 bg-rose-50 text-rose-800"
        dotClass="bg-rose-600"
        busy={t.mic.busy}
        onChange={t.mic.set}
        onDetails={onDetails}
        testId="top-mic"
      />
      <TopSwitch
        label="AI"
        name="AI listening"
        checked={t.ai.on}
        active={t.ai.sending}
        activeClass="border-teal-200 bg-teal-50 text-teal-900"
        dotClass="bg-teal-600"
        busy={t.ai.busy}
        onChange={t.ai.set}
        onDetails={onDetails}
        testId="top-ai"
      />
    </div>
  );
}

/** What the switches say beyond on and off, for the header's status line (PageHeader.tsx): "mic in use for Auto",
 *  "mic not in use", "AI on · <why it isn't sending>"; plain "Mic off" or "AI on" would only repeat the switch, so they
 *  say nothing. Before the music side's card arrives, that it hasn't. */
export function topToggleWords(t: TopToggles): string | null {
  if (!t.known) return "the music side isn't answering yet";
  const line = t.mic.line;
  const mic = !t.mic.allowed || !line
    ? null
    : line.startsWith("In use · ")
      ? `mic in use ${line.slice("In use · ".length)}`
      : line.startsWith("Not in use · waiting")
        ? "mic waiting to start"
        : line.startsWith("Not in use")
          ? "mic not in use"
          : `mic ${line.charAt(0).toLowerCase()}${line.slice(1)}`;
  const ai = t.ai.on && t.ai.state && t.ai.state !== "Listening" ? `AI ${t.ai.state.charAt(0).toLowerCase()}${t.ai.state.slice(1)}` : null;
  return [mic, ai].filter(Boolean).join(" · ") || null;
}
