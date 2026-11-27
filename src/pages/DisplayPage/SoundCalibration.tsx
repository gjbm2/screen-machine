// The display page's part in measuring its screen's sound delay (docs/SOUND.md; routes/sound/calibrate.py): while the
// app measures output `screen:<this screen>`, this page plays the chirps at their server times, as it schedules a
// song's sound, and the room mic times them. Nothing shows and nothing plays otherwise.
//
// - Polls GET /api/sound/calibrate every POLL_MS (a few hundred bytes); the run starts its chirps LEAD_S (4 s) after
//   it publishes them, so one poll's lag is fine. The server clock's skew comes from the quickest of the last replies.
// - The browser may hold the sound until the page is clicked (a TV kiosk started with autoplay allowed never does):
//   then a "Click for sound" pill shows while a run for this screen waits, and any click lets it play.
// - data-sm-karaoke keeps the pill visible while live video is up (index.tsx ART_GUARD_CSS).

import { useEffect, useRef, useState, type CSSProperties } from "react";
import { sound, type CalibrationRun } from "@/shared/api/sound";
import { playChirps } from "@/shared/sound/chirps";

const POLL_MS = 1000;
const SKEW_N = 8;

export function SoundCalibration({ screen }: { screen?: string }) {
  const [held, setHeld] = useState(false);
  const ctxRef = useRef<AudioContext | null>(null);

  useEffect(() => {
    if (!screen) return;
    const me = `screen:${screen}`;
    let dead = false;
    let played: string | null = null;
    const samples: { rtt: number; skew: number }[] = [];
    const ctx = () => (ctxRef.current ??= new AudioContext({ latencyHint: "playback" }));
    const unlock = () => {
      const c = ctxRef.current;
      if (c && c.state !== "running") void c.resume().catch(() => undefined);
    };
    window.addEventListener("pointerdown", unlock, { capture: true, passive: true });
    window.addEventListener("keydown", unlock, { capture: true, passive: true });
    const tick = async () => {
      let run: CalibrationRun;
      const sent = Date.now();
      try {
        run = await sound.calibration();
      } catch {
        return; // Flask restarting: next time
      }
      const got = Date.now();
      if (dead) return;
      if (typeof run.now === "number") {
        samples.push({ rtt: got - sent, skew: run.now - (sent + got) / 2000 });
        if (samples.length > SKEW_N) samples.shift();
      }
      const mine = run.state === "running" && run.output === me && !!run.clicks?.length && !!run.chirps?.length;
      if (!mine) {
        setHeld(false);
        return;
      }
      if (played === run.id) return;
      const c = ctx();
      if (c.state !== "running") void c.resume().catch(() => undefined);
      const skew = samples.length ? samples.reduce((a, b) => (b.rtt < a.rtt ? b : a)).skew : 0;
      if (playChirps(c, run.clicks!, run.chirps!, skew) > 0) {
        played = run.id ?? null;
        setHeld(false);
      } else setHeld(c.state !== "running");
    };
    void tick();
    const t = window.setInterval(() => void tick(), POLL_MS);
    return () => {
      dead = true;
      window.clearInterval(t);
      window.removeEventListener("pointerdown", unlock, { capture: true });
      window.removeEventListener("keydown", unlock, { capture: true });
    };
  }, [screen]);

  if (!held) return null;
  return (
    <div data-sm-karaoke="" style={wrap}>
      <div style={pill}>Measuring this screen's sound: click for sound</div>
    </div>
  );
}

const wrap: CSSProperties = { position: "fixed", inset: 0, zIndex: 2147483001, pointerEvents: "none" };
const pill: CSSProperties = {
  position: "absolute",
  top: "5vh",
  left: "50%",
  transform: "translateX(-50%)",
  padding: "0.6em 1.2em",
  borderRadius: "999px",
  background: "rgba(0,0,0,0.72)",
  border: "2px solid rgba(255,214,90,0.9)",
  color: "#fff",
  fontFamily: "'Segoe UI', 'Helvetica Neue', Arial, sans-serif",
  fontWeight: 700,
  fontSize: "min(2vw, 32px)",
};
