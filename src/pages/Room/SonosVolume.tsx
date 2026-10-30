// Sonos volume (owner, 30 Oct: "when sonos is playing as the output, we prob. need a volume control within our app"):
// one slider for a Sonos zone's volume (the room target sonos.<zone>.volume). Play on (PlayOn.tsx) shows it while the
// zone is where the music plays, or while the zone plays anything of its own, so every page with Play on has it (the
// Room page, /music, the score page, the docked player's ⋯). The zone comes from show_service's output id
// ("sonos:<zone>"); nothing here names a device.
//   - The volume shown is the room's reading (GET /api/v1/status, every 4 s while the page is visible).
//   - A move is sent on release (PUT /api/v1/room/targets: a change by hand; Sonos ramps to it while playing) and
//     shows at once, held until the room reports it (8 s at most), so the thumb never jumps back.
//   - A mode that put the volume aside (Piano) still puts back the one from before it on leaving.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Volume1, Volume2, VolumeX } from "lucide-react";
import { Slider } from "@/components/ui/slider";
import { cn } from "@/lib/utils";
import { explainError, RoomApi } from "./api";
import { Spinner } from "./bits";

const POLL_MS = 4_000;
const PENDING_MS = 8_000;
const PLAYING = ["playing", "transitioning"];

/** The zone in a show_service output id ("sonos:drawing-room" -> "drawing-room"), else null. */
export function sonosZone(output: string | null | undefined): string | null {
  return output?.startsWith("sonos:") ? output.slice("sonos:".length) || null : null;
}

interface ZoneView {
  volume: number | null;
  playing: boolean;
}

/** The zone's volume and whether it plays, from the room's status; null until read (or with no such zone). */
export function useSonosZone(baseUrl: string, zone: string | null): [ZoneView | null, () => void] {
  const api = useMemo(() => new RoomApi(baseUrl), [baseUrl]);
  const [view, setView] = useState<ZoneView | null>(null);
  const [n, bump] = useState(0);
  useEffect(() => {
    if (!zone) {
      setView(null);
      return;
    }
    let stop = false;
    let timer: number | undefined;
    const ac = new AbortController();
    const poll = async () => {
      if (document.visibilityState === "visible") {
        const s = await api.status(ac.signal).catch(() => null);
        if (stop) return;
        const b = s?.subsystems?.sonos?.believed as Record<string, unknown> | undefined;
        if (b && `${zone}.volume` in b) {
          const v = b[`${zone}.volume`];
          const np = b[`${zone}.now_playing`] as { state?: string } | undefined;
          setView({ volume: typeof v === "number" ? v : null, playing: PLAYING.includes(np?.state ?? "") });
        } else if (s) setView(null);
      }
      timer = window.setTimeout(() => void poll(), POLL_MS);
    };
    void poll();
    return () => {
      stop = true;
      ac.abort();
      window.clearTimeout(timer);
    };
  }, [api, zone, n]);
  return [view, useCallback(() => bump((x) => x + 1), [])];
}

/** The slider row. `label`: the zone's name as Play on says it ("Sonos (Drawing Room)"). */
export function SonosVolume({
  baseUrl,
  zone,
  view,
  refresh,
  label,
  className,
}: {
  baseUrl: string;
  zone: string;
  view: ZoneView;
  refresh: () => void;
  label: string;
  className?: string;
}) {
  const api = useMemo(() => new RoomApi(baseUrl), [baseUrl]);
  const [drag, setDrag] = useState<number | null>(null);
  const [sent, setSent] = useState<{ v: number; at: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const timer = useRef<number | undefined>(undefined);

  // the sent value shows until the room reports it, or PENDING_MS
  useEffect(() => {
    if (sent && view.volume === sent.v) setSent(null);
  }, [sent, view.volume]);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  const send = async (v: number) => {
    setSent({ v, at: Date.now() });
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setSent(null), PENDING_MS);
    setBusy(true);
    setErr(null);
    try {
      await api.setTargets({ [`sonos.${zone}.volume`]: v });
      window.setTimeout(refresh, 1_000);
    } catch (e) {
      setSent(null);
      setErr(explainError(e));
    } finally {
      setBusy(false);
    }
  };

  const shown = drag ?? sent?.v ?? view.volume;
  if (shown == null) return null;
  const Icon = shown === 0 ? VolumeX : shown < 40 ? Volume1 : Volume2;
  return (
    <div className={cn("space-y-1", className)} data-testid="sonos-volume">
      <div className="flex min-h-11 items-center gap-3">
        <Icon className="h-5 w-5 shrink-0 text-stone-500" aria-hidden />
        <span className="shrink-0 text-[15px] font-medium text-stone-700">Volume</span>
        <Slider
          aria-label={`${label} volume`}
          className="flex-1 py-3 [&_[role=slider]]:h-6 [&_[role=slider]]:w-6 [&_[role=slider]]:shadow-sm"
          min={0}
          max={100}
          step={1}
          value={[shown]}
          onValueChange={([v]) => setDrag(v)}
          onValueCommit={([v]) => {
            setDrag(null);
            if (v !== view.volume || sent) void send(v);
          }}
        />
        <span className="w-10 shrink-0 text-right text-[15px] tabular-nums text-stone-700">{shown}%</span>
        {busy && <Spinner />}
      </div>
      {err && <p className="text-sm text-rose-700">{err}</p>}
    </div>
  );
}
