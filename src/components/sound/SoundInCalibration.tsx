// How late the beat clock puts a sound it hears (docs/SOUND.md "Sound in"; the latency audit, 27 Nov: no tool measured
// a sound channel's own delay, so every layer landed late by the mic chain). Calibrate: this device plays the ten
// chirps on its own speaker, each leaving it at a time on show_service's clock (its audio output timestamps, the
// clocks matched first), and routes/sound/calibrate.py (output "in") times them in the mic by the beat clock's own
// time for those samples. What they are off by moves the stream's acoustic offset (latency.json sources.<stream>).

import { useEffect, useRef, useState } from "react";
import { Gauge, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { sound, type CalibrationRun } from "@/shared/api/sound";
import { playChirpsAt } from "@/shared/sound/chirps";
import { showClockOffset } from "./showClock";

const POLL_MS = 500;

export function SoundInCalibration({ onDone }: { onDone?: () => void }) {
  const [run, setRun] = useState<CalibrationRun | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const ctxRef = useRef<AudioContext | null>(null);
  const played = useRef<string | null>(null);
  const mine = run?.output === "in";
  const running = mine && run?.state === "running";

  useEffect(() => {
    if (!running) return;
    let dead = false;
    const t = window.setInterval(async () => {
      let r: CalibrationRun;
      try {
        r = await sound.calibration();
      } catch {
        return;
      }
      if (dead) return;
      setRun(r);
      if (r.output === "in" && r.state === "running" && r.clicks_mono?.length && r.chirps?.length && played.current !== r.id) {
        played.current = r.id ?? null;
        try {
          const co = await showClockOffset(10);
          const c = ctxRef.current as AudioContext;
          if (!playChirpsAt(c, r.clicks_mono.map((s) => s * 1000 - co.offset), r.chirps)) setErr("This device's sound didn't start");
        } catch (e) {
          setErr(String((e as Error).message || e));
        }
      }
      if (r.state !== "running") onDone?.();
    }, POLL_MS);
    return () => {
      dead = true;
      window.clearInterval(t);
    };
  }, [running, onDone]);

  const start = async () => {
    setErr(null);
    played.current = null;
    const c = (ctxRef.current ??= new AudioContext({ latencyHint: "interactive" }));
    await c.resume().catch(() => undefined);
    try {
      setRun(await sound.calibrate("in"));
    } catch (e) {
      setErr(String((e as Error).message || e));
    }
  };

  return (
    <div className="space-y-1" data-testid="sound-in-calibration">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[15px] text-muted-foreground">The beat clock against this device's speaker</span>
        <Button variant="outline" size="sm" disabled={running} onClick={() => void start()} title="Plays ten chirps on this device and times them in the room mic">
          {running ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Gauge className="mr-1 h-4 w-4" />} Calibrate
        </Button>
      </div>
      {mine && run?.message && run.state !== "idle" && (
        <p className={run.state === "failed" ? "text-sm text-red-700" : "text-sm text-muted-foreground"}>{run.message}</p>
      )}
      {err && <p className="text-sm text-red-700">{err}</p>}
    </div>
  );
}
