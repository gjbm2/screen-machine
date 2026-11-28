// A light's response measured with this device's camera (docs/calibration lighting brief, ch. 1; docs/SOUND.md "Lights";
// owner, 27 Nov: "it's easy enough to use the laptop camera as a light sensor ... all the calibration paths should be
// generalised and exposed in a sensible location in the app").
//
// One camera session, two interleaved loops on the page's clock:
//   * the light: show_service flashes it (POST /show/api/v1/rig/fixtures/<id>/probe: pulses on its own clock, the light
//     held dark around them; its output sends a frame at each edge) and the camera sees it;
//   * the screen: this page goes white, the camera sees the reflection. That loop is the camera's own delay plus a
//     refresh, so it is removed from the light's (cameraCal.estimate).
// The page and the show's clocks are matched first (GET /show/api/v1/clock, the quickest of 12 round trips). The result,
// + = seen that long after the frame was sent, goes to latency.json outputs.<light>.intensity (PUT
// /api/sound/latency/lights/<id>), for that light and the others of its model on its output: the show engine reads it.

import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Camera, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { sound } from "@/shared/api/sound";
import { acceptable, edgeAfter, estimate, type Estimate, type Frame, type Trial } from "./cameraCal";
import { showClockOffset, showJson } from "./showClock";

const TRIALS = 8;
const PERIOD_MS = 2400;            // one light flash and one screen flash per period, 1.2 s apart
const PULSE_MS = 300;
const LEAD_MS = 2500;              // the first flash this long after the plan (camera settled, request made)
const CLOCK_SAMPLES = 12;

interface RigLight {
  id: string;
  label: string;
  kind?: string;
  type?: string;
  output?: string | null;
  driver?: string | null;
}

type Phase = "idle" | "camera" | "running" | "done" | "failed";

const sleep = (ms: number) => new Promise((res) => window.setTimeout(res, ms));

/** rAF until `at` (page ms), then run `fn` in that frame; resolves with the frame's time. */
function atFrame(at: number, fn: () => void): Promise<number> {
  return new Promise((res) => {
    const tick = (ts: number) => {
      if (performance.now() >= at - 4) {
        fn();
        res(ts);
      } else requestAnimationFrame(tick);
    };
    window.setTimeout(() => requestAnimationFrame(tick), Math.max(0, at - performance.now() - 40));
  });
}

/** The display's refresh interval (ms), from rAF. */
async function refreshMs(): Promise<number> {
  const ts: number[] = [];
  await new Promise<void>((res) => {
    const f = (t: number) => {
      ts.push(t);
      if (ts.length < 31) requestAnimationFrame(f);
      else res();
    };
    requestAnimationFrame(f);
  });
  const d = ts.slice(1).map((t, i) => t - ts[i]).sort((a, b) => a - b);
  return d[Math.floor(d.length / 2)] || 16.7;
}

export function CameraCalibration({ onSaved }: { onSaved?: () => void }) {
  const [lights, setLights] = useState<RigLight[] | null>(null);
  const [pick, setPick] = useState<string>("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [msg, setMsg] = useState<string>("");
  const [est, setEst] = useState<Estimate | null>(null);
  const [level, setLevel] = useState(0);
  const [white, setWhite] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const framesRef = useRef<Frame[]>([]);
  const streamRef = useRef<MediaStream | null>(null);

  useEffect(() => {
    showJson<{ fixtures?: RigLight[] }>("GET", "/api/v1/rig").then(
      (b) => {
        const ls = (b.fixtures || []).filter((f) => f.kind === "screen" || f.driver === "artnet");
        setLights(ls);
        setPick((p) => p || ls[0]?.id || "");
      },
      () => setLights([]),
    );
    return () => stopCamera();
  }, []);

  const light = lights?.find((l) => l.id === pick);
  const isScreen = light?.kind === "screen";
  const screenName = isScreen && light ? light.id.split(".").slice(-1)[0] : "";
  const refreshRef = useRef(16.7);
  const twins = useMemo(
    () => (lights || []).filter((l) => light && light.kind !== "screen" && l.id !== light.id && l.type === light.type && l.output === light.output),
    [lights, light],
  );

  function stopCamera() {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
  }

  async function startCamera(): Promise<HTMLVideoElement> {
    const s = await navigator.mediaDevices.getUserMedia({ video: { width: 640, height: 480, frameRate: { ideal: 60 } }, audio: false });
    streamRef.current = s;
    const v = videoRef.current as HTMLVideoElement;
    v.srcObject = s;
    await v.play();
    const c = document.createElement("canvas");
    c.width = 32;
    c.height = 24;
    const g = c.getContext("2d", { willReadFrequently: true }) as CanvasRenderingContext2D;
    const onFrame = (now: number) => {
      if (!streamRef.current) return;
      g.drawImage(v, 0, 0, 32, 24);
      const d = g.getImageData(0, 0, 32, 24).data;
      let sum = 0;
      for (let i = 0; i < d.length; i += 4) sum += d[i] + d[i + 1] + d[i + 2];
      const val = sum / (d.length / 4) / 3;
      framesRef.current.push({ t: now, v: val });
      if (framesRef.current.length > 4000) framesRef.current.splice(0, 1000);
      setLevel(val);
      (v as HTMLVideoElement & { requestVideoFrameCallback: (cb: (n: number) => void) => void }).requestVideoFrameCallback(onFrame);
    };
    (v as HTMLVideoElement & { requestVideoFrameCallback: (cb: (n: number) => void) => void }).requestVideoFrameCallback(onFrame);
    return v;
  }

  async function lockCamera() {
    const tr = streamRef.current?.getVideoTracks()[0];
    const caps = (tr?.getCapabilities?.() || {}) as Record<string, unknown>;
    const want: Record<string, string> = {};
    for (const k of ["exposureMode", "whiteBalanceMode", "focusMode"]) {
      const modes = caps[k];
      if (Array.isArray(modes) && modes.includes("manual")) want[k] = "manual";
    }
    if (tr && Object.keys(want).length) await tr.applyConstraints({ advanced: [want as MediaTrackConstraintSet] }).catch(() => undefined);
  }

  async function run() {
    if (!light) return;
    setEst(null);
    setSaved(null);
    framesRef.current = [];
    try {
      setPhase("camera");
      setMsg("Camera");
      if (!streamRef.current) await startCamera();
      await sleep(1500);
      await lockCamera();
      await sleep(500);
      setPhase("running");
      setMsg("Clock");
      const co = await showClockOffset(CLOCK_SAMPLES);
      const refresh = await refreshMs();
      refreshRef.current = refresh;
      const t0 = performance.now() + (isScreen ? LEAD_MS + 2000 : LEAD_MS);   // a display page polls once a second
      const lightOn = Array.from({ length: TRIALS }, (_, k) => t0 + k * PERIOD_MS);
      const screenOn = lightOn.map((x) => x + PERIOD_MS / 2);
      if (isScreen) {
        await sound.flash(screenName, lightOn.map((t) => (t + co.offset) / 1000), PULSE_MS / 1000);
      } else {
        const got = await showJson<{ ok: boolean; why?: string }>("POST", `/api/v1/rig/fixtures/${encodeURIComponent(light.id)}/probe`, {
          pulses: lightOn.map((t) => [(t + co.offset) / 1000, PULSE_MS / 1000]),
          dark_s: 1.5,
        });
        if (!got.ok) throw new Error(got.why || "the light can't be flashed");
      }
      setMsg("Flashing");
      const screen: Trial[] = [];
      for (const at of screenOn) {
        const on = await atFrame(at, () => setWhite(true));
        const off = await atFrame(at + PULSE_MS, () => setWhite(false));
        screen.push({ on, off, seenOn: null, seenOff: null });
      }
      await sleep(900);
      const f = framesRef.current;
      const seen = (x: Trial): Trial => ({ ...x, seenOn: edgeAfter(f, x.on, true), seenOff: edgeAfter(f, x.off, false) });
      const lt = lightOn.map((on) => seen({ on, off: on + PULSE_MS, seenOn: null, seenOff: null }));
      const e = estimate(lt, screen.map(seen), refresh);
      setEst(e);
      setPhase(e ? "done" : "failed");
      setMsg(e ? "" : `Seen ${lt.filter((x) => x.seenOn != null).length}/${TRIALS} light flashes, ${screen.map(seen).filter((x) => x.seenOn != null).length}/${TRIALS} screen flashes`);
    } catch (err) {
      setPhase("failed");
      setMsg(String((err as Error).message || err));
      setWhite(false);
    }
  }

  async function save() {
    if (!light || !est) return;
    try {
      if (isScreen) {
        // its page draws the white a refresh before it is presented: presented -> light is the rest
        await sound.setScreenLatency(screenName, {
          presented_to_photon_ms: Math.round(Math.max(1, est.riseMs - refreshRef.current) * 10) / 10,
          evidence: { camera_ms: Math.round(est.cameraMs), spread_ms: Math.round(est.spreadMs), used: est.used, of: est.of, refresh_ms: Math.round(refreshRef.current * 10) / 10 },
        });
        setSaved(light.label);
        onSaved?.();
        return;
      }
      await sound.setLightLatency(light.id, {
        rise_ms: Math.round(est.riseMs * 10) / 10,
        fall_ms: est.fallMs == null ? null : Math.round(Math.max(0, est.fallMs) * 10) / 10,
        also: twins.map((t) => t.id),
        evidence: { camera_ms: Math.round(est.cameraMs), spread_ms: Math.round(est.spreadMs), used: est.used, of: est.of, screen_used: est.screenUsed },
      });
      setSaved(`${light.label}${twins.length ? ` + ${twins.length}` : ""}`);
      onSaved?.();
    } catch (err) {
      setMsg(String((err as Error).message || err));
    }
  }

  if (lights && !lights.length) return null;
  const busy = phase === "camera" || phase === "running";
  return (
    <div className="space-y-2" data-testid="camera-calibration">
      <div className="flex flex-wrap items-center gap-2">
        <select
          className="h-9 min-w-0 flex-1 rounded-md border border-stone-300 bg-white px-2 text-[15px]"
          value={pick}
          disabled={busy}
          onChange={(e) => {
            setPick(e.target.value);
            setEst(null);
            setSaved(null);
          }}
          aria-label="Light"
        >
          {(lights || []).map((l) => (
            <option key={l.id} value={l.id}>
              {l.label}
            </option>
          ))}
        </select>
        <Button variant="outline" size="sm" disabled={!light || busy} onClick={() => void run()} title="Times the light with this device's camera, in a dark room">
          {busy ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Camera className="mr-1 h-4 w-4" />} Calibrate
        </Button>
      </div>
      <div className={phase === "idle" ? "hidden" : "flex items-center gap-3"}>
        <video ref={videoRef} muted playsInline className="h-16 w-20 rounded bg-black object-cover" />
        <div className="h-2 flex-1 rounded bg-stone-200" title="What the camera sees">
          <div className="h-2 rounded bg-amber-500" style={{ width: `${Math.min(100, (level / 255) * 100)}%` }} />
        </div>
        <span className="w-20 text-right text-sm text-muted-foreground">{msg}</span>
      </div>
      {est && (
        <div className="flex flex-wrap items-center justify-between gap-2 text-[15px]">
          <span className={acceptable(est) ? "" : "text-red-700"}>
            {isScreen
              ? `Picture ${Math.round(Math.max(1, est.riseMs - refreshRef.current))} ms`
              : `On ${Math.round(est.riseMs)} ms${est.fallMs != null ? ` · off ${Math.round(est.fallMs)} ms` : ""}`}{" "}
            · ±{Math.round(est.spreadMs)} ms ·{" "}
            {est.used}/{est.of}
          </span>
          {acceptable(est) && !saved && (
            <Button size="sm" onClick={() => void save()}>
              Save{twins.length ? ` for ${twins.length + 1}` : ""}
            </Button>
          )}
          {saved && <span className="text-sm text-muted-foreground">Saved: {saved}</span>}
        </div>
      )}
      {phase === "failed" && msg && <p className="text-sm text-red-700">{msg}</p>}
      {white && createPortal(<div className="fixed inset-0 z-[10000] bg-white" />, document.body)}
    </div>
  );
}
