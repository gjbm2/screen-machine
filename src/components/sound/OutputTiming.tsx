// An output's delay, the same control wherever it's shown (docs/SOUND.md): how late the room hears it (measured +
// trim, smsound.latency), Calibrate (chirps timed with the room mic: routes/sound/calibrate.py; screens and Sonos) with
// the run's progress, and a trim nudge. Karaoke's Settings and Settings › Sound & timing both use it.

import { useCallback, useEffect, useState } from "react";
import { Gauge, Loader2, Minus, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { latencyText, sound, type CalibrationRun, type OutputLatency, type SoundOutput } from "@/shared/api/sound";

const TRIM_STEP_MS = 10;
const RUN_POLL_MS = 1000;

export function OutputTiming({
  output,
  onChanged,
  confirmText,
  compact = false,
}: {
  output: SoundOutput;
  /** After a calibration or a trim: the caller fetches its rows again. */
  onChanged?: () => void;
  /** The question before calibrating (what pauses, what plays). */
  confirmText?: string;
  compact?: boolean;
}) {
  const [lat, setLat] = useState<OutputLatency | undefined>(output.latency);
  const [run, setRun] = useState<CalibrationRun | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => setLat(output.latency), [output.latency]);

  const mine = run?.output === output.id;
  const running = mine && run?.state === "running";

  // Follow the app's calibration while it measures this output (and pick up one started elsewhere).
  const poll = useCallback(async () => {
    try {
      const r = await sound.calibration();
      setRun(r);
      return r;
    } catch {
      return null;
    }
  }, []);
  useEffect(() => {
    void poll();
  }, [poll]);
  useEffect(() => {
    if (!running) return;
    const t = window.setInterval(async () => {
      const r = await poll();
      if (r && r.state !== "running") onChanged?.();
    }, RUN_POLL_MS);
    return () => window.clearInterval(t);
  }, [running, poll, onChanged]);

  const calibrate = async () => {
    if (!window.confirm(confirmText ?? `Measure ${output.label}'s delay? It plays ten chirps (about 12 s): keep the room quiet.`))
      return;
    setErr(null);
    try {
      setRun(await sound.calibrate(output.id));
    } catch (e) {
      setErr(String((e as Error).message || e));
    }
  };
  const trim = async (by: number) => {
    setErr(null);
    try {
      const r = await sound.setLatency(output.id, { trim_ms: (lat?.trim_ms ?? 0) + by });
      setLat(r.latency);
      onChanged?.();
    } catch (e) {
      setErr(String((e as Error).message || e));
    }
  };

  const canMeasure = output.calibrate === "chirp";
  const canTrim = output.kind === "screen" || output.kind === "sonos";
  return (
    <div className={cn("space-y-1.5", compact ? "text-sm" : "text-[15px]")}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="min-w-0 text-muted-foreground" data-testid="output-latency">
          {latencyText(lat) || "—"}
        </span>
        <div className="flex items-center gap-1.5">
          {canMeasure && (
            <Button variant="outline" size="sm" disabled={running} onClick={() => void calibrate()} title="Plays ten chirps and times them with the room mic">
              {running ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Gauge className="mr-1 h-4 w-4" />} Calibrate
            </Button>
          )}
          {canTrim && (
            <>
              <Button variant="outline" size="sm" aria-label={`Trim ${TRIM_STEP_MS} ms earlier`} onClick={() => void trim(-TRIM_STEP_MS)}>
                <Minus className="h-3.5 w-3.5" />
              </Button>
              <span className="w-14 text-center tabular-nums text-xs text-muted-foreground" title="Your trim on top of the measured delay">
                {(lat?.trim_ms ?? 0) > 0 ? "+" : ""}
                {Math.round(lat?.trim_ms ?? 0)} ms
              </span>
              <Button variant="outline" size="sm" aria-label={`Trim ${TRIM_STEP_MS} ms later`} onClick={() => void trim(TRIM_STEP_MS)}>
                <Plus className="h-3.5 w-3.5" />
              </Button>
            </>
          )}
        </div>
      </div>
      {mine && run?.message && run.state !== "idle" && (
        <p className={cn("text-sm", run.state === "failed" ? "text-red-700" : "text-muted-foreground")}>
          {running ? "Calibrating: " : ""}
          {run.message}
        </p>
      )}
      {err && <p className="text-sm text-red-700">{err}</p>}
    </div>
  );
}
