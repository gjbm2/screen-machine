// Live diagnostics for one screen (docs/LIVE_DIAGNOSTICS.md, owner-approved 17 Oct): the renderer burns a small panel
// into the stream itself (D-B unchanged: nothing is drawn over the TV's video).
//   * DetailsSwitch: on the screen card itself, always in sight (owner 18 Oct: under More → Technical details it
//     wasn't findable): the diag.<screen> room target, written through PUT /api/v1/room/targets, and where it
//     stands.  Never disabled.
//   * LiveDetails (under More → Technical details): the same lines as text, read once a second while that section
//     is open, so they work with the TV panel off too (the per-viewer view).
//   * TransitionDetails (under Technical details): the chosen transitions with their parameters; the pickers show
//     the label only (24 Oct walkthrough).
// Where the switch sits (24 Oct walkthrough): on the card's face while the screen is live, and at the top of
// Technical details while it shows art, so it is in one place at a time.

import { useEffect, useState } from "react";
import { Switch } from "@/components/ui/switch";
import { explainError, type RoomApi } from "./api";
import { transitionSpecKey, type TransitionChoice } from "./catalog";
import type { ScreenStatus, TransitionSpec } from "./types";

interface HudView {
  on?: boolean;
  failed?: string | null;
  at?: number | string | null;
  lines?: string[];
  warn?: number[];
}

const STALE_MS = 3000;

export function DetailsSwitch({
  api,
  screenId,
  screen,
  className = "px-4 pb-3",
}: {
  api: RoomApi;
  screenId: string;
  screen: ScreenStatus;
  className?: string;
}) {
  const hud = screen.hud;
  const [want, setWant] = useState<boolean | null>(null); // a tap not yet reflected in status
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (want !== null && hud && hud.wanted === want) setWant(null);
  }, [hud, want]);
  const wanted = want ?? !!hud?.wanted;

  const line = !wanted
    ? "Scene, effects, tempo, inputs and frame rate, drawn on the screen"
    : hud?.applied
      ? "On the screen now"
      : "Shows when it goes live";

  const toggle = (on: boolean) => {
    setWant(on);
    setErr(null);
    api.setTargets({ [`diag.${screenId}`]: on }).catch((e) => {
      setWant(null);
      setErr(`That didn't go through: ${explainError(e)}`);
    });
  };

  return (
    <div className={className} data-testid={`details-switch-${screenId}`}>
      <label className="flex min-h-11 items-center justify-between gap-3 rounded-xl border border-stone-200 bg-white px-3 py-1.5">
        <span className="min-w-0">
          <span className="block text-[15px] font-medium">Details on the screen</span>
          <span className="block text-sm leading-snug text-muted-foreground">{line}</span>
        </span>
        <Switch aria-label="Details on the screen" checked={wanted} onCheckedChange={toggle} />
      </label>
      {err && <p className="mt-1 text-sm text-rose-700">{err}</p>}
    </div>
  );
}

export function LiveDetails({ api, screenId, screen }: { api: RoomApi; screenId: string; screen: ScreenStatus }) {
  // The text view: the same lines, polled while this section is open (it unmounts when closed).
  const sid = screen.observed?.session ?? null;
  const [view, setView] = useState<{ hud: HudView; got: number } | null>(null);
  const [readErr, setReadErr] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    setView(null);
    if (!sid) return;
    let gone = false;
    let ac: AbortController | null = null;
    const read = async () => {
      ac?.abort();
      ac = new AbortController();
      try {
        const h = await api.hud(sid, ac.signal);
        if (!gone) {
          setView({ hud: h, got: Date.now() });
          setReadErr(false);
        }
      } catch (e) {
        if (!gone && !(e instanceof DOMException && e.name === "AbortError")) setReadErr(true);
      }
    };
    void read();
    const t = setInterval(() => {
      setNow(Date.now());
      if (document.visibilityState === "visible") void read();
    }, 1000);
    return () => {
      gone = true;
      ac?.abort();
      clearInterval(t);
    };
  }, [api, sid]);
  const stale = !!view && (readErr || now - view.got > STALE_MS);
  const warn = new Set(view?.hud.warn ?? []);

  return (
    <div className="space-y-2 pb-3" data-testid={`live-details-${screenId}`}>
      <p className="text-[15px] font-medium">Live details</p>
      {!sid ? (
        <p className="text-sm text-muted-foreground">Not live, so there are no live details to show.</p>
      ) : !view ? (
        <p className="text-sm text-muted-foreground">{readErr ? "The renderer isn't answering for its details." : "Reading the details…"}</p>
      ) : (
        <div className="rounded-xl border border-stone-200 bg-stone-50 p-2">
          {stale && <p className="mb-1 text-xs font-medium text-amber-800">Not updating: these may be out of date.</p>}
          <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words font-mono text-[12px] leading-snug text-stone-800">
            {(view.hud.lines ?? []).map((l, i) => (
              <span key={i} className={warn.has(i) ? "text-amber-800" : undefined}>
                {l}
                {"\n"}
              </span>
            ))}
          </pre>
        </div>
      )}
    </div>
  );
}

/** The transitions this screen will use, with their parameters (the pickers in More show the label only). */
export function TransitionDetails({
  screenId,
  inSpec,
  outSpec,
  choices,
}: {
  screenId: string;
  inSpec: TransitionSpec | null;
  outSpec: TransitionSpec | null;
  choices: TransitionChoice[];
}) {
  const row = (label: string, spec: TransitionSpec | null) => {
    const key = transitionSpecKey(spec);
    const c = key ? choices.find((x) => x.key === key) : undefined;
    const params = c ? Object.entries(c.params).map(([k, v]) => `${k} ${String(v)}`) : [];
    return (
      <p className="break-words">
        <span className="text-muted-foreground">{label}: </span>
        {c ? (
          <>
            {c.label} <span className="font-mono text-[12px] text-stone-700">({[c.transition, ...params].join(" · ")})</span>
          </>
        ) : key ? (
          <span className="font-mono text-[12px]">{key.replace(/^raw:/, "")}</span>
        ) : (
          "default"
        )}
      </p>
    );
  };
  return (
    <div className="space-y-1 pb-3 text-sm" data-testid={`transition-details-${screenId}`}>
      <p className="text-[15px] font-medium">Transitions</p>
      {row("Going live", inSpec)}
      {row("Back to art", outSpec)}
    </div>
  );
}
