// How far behind the room a device hears PC22's piano when it plays it itself ("This device": owner, 23 Oct, "when
// playing from 'this device' we need to match latency with local score and/or pianola view because local device is
// quite a few seconds behind"). The stream (GET /show/api/v1/piano/live.mp3) carries audio whose stream second x is
// the sound time t0 + x - delay_s on PC22's clock; GET …/piano/live/clock says t0, delay_s, the stream's position now
// (pos_s) and PC22's clock now (host_now). What a device's <audio> plays now is pos_s minus what it holds unplayed
// (buffered end - currentTime), so it hears the sound time
//     t0 + pos_s - (bufferedEnd - currentTime) - delay_s,
// and is behind by host_now - that. Measured every 2 s while it plays, the median of the last 5 kept here; anything on
// the page that shows the music as it sounds (the score's light, a pianola view) draws that much later while it's set.

import { useEffect, useSyncExternalStore } from "react";

let lag: number | null = null;
const listeners = new Set<() => void>();

/** The device's lag in seconds while it plays the piano stream, else null. */
export function getDeviceLag(): number | null {
  return lag;
}

export function setDeviceLag(s: number | null): void {
  const v = s === null || !Number.isFinite(s) ? null : Math.min(15, Math.max(0, s));
  if (v === lag || (v !== null && lag !== null && Math.abs(v - lag) < 0.02)) return;
  lag = v;
  listeners.forEach((l) => l());
}

function subscribe(l: () => void) {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
}

/** The lag (seconds) while this device plays the piano stream, else null; re-renders when it changes. */
export function useDeviceLag(): number | null {
  return useSyncExternalStore(subscribe, getDeviceLag, getDeviceLag);
}

interface StreamClock {
  t0: number | null;
  delay_s: number;
  host_now: number;
  pos_s: number;
}

/** Measure the lag of ``audio`` (playing the piano stream) every 2 s; ``clockUrl`` is …/show/api/v1/piano/live/clock
 *  with the stream's ?instrument=. Clears it when the audio stops or the hook goes. */
export function useDeviceLagTracker(audio: HTMLAudioElement | null, clockUrl: string | null): void {
  useEffect(() => {
    if (!audio || !clockUrl) return;
    const seen: number[] = [];
    let stop = false;
    const measure = async () => {
      if (stop) return;
      if (audio.paused || audio.readyState < 2 || !audio.buffered.length) {
        seen.length = 0;
        setDeviceLag(null);
        return;
      }
      try {
        const r = await fetch(clockUrl, { cache: "no-store" });
        if (!r.ok) return;
        const c = (await r.json()) as StreamClock;
        if (stop || c.t0 === null || !audio.buffered.length) return;
        const held = audio.buffered.end(audio.buffered.length - 1) - audio.currentTime;
        const behind = c.host_now - (c.t0 + c.pos_s - held - c.delay_s);
        seen.push(behind);
        if (seen.length > 5) seen.shift();
        const sorted = [...seen].sort((a, b) => a - b);
        setDeviceLag(sorted[sorted.length >> 1]);
      } catch {
        /* nothing answering: keep the last */
      }
    };
    void measure();
    const t = setInterval(() => void measure(), 2000);
    const off = () => setDeviceLag(null);
    audio.addEventListener("pause", off);
    audio.addEventListener("ended", off);
    return () => {
      stop = true;
      clearInterval(t);
      audio.removeEventListener("pause", off);
      audio.removeEventListener("ended", off);
      setDeviceLag(null);
    };
  }, [audio, clockUrl]);
}
