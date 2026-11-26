"""The speaker delay, measured with the room mic, for the lyrics only (docs/KARAOKE.md; owner, 25 Nov: "use the mic to
calibrate the latency ... add a 'calibrate' option"; "it's just for the lyrics sync we need this": the picture and the
lights follow the live mic's own beat clock, a separate clock and calibration this never touches).

``settings.audio_delay_ms`` is the whole delay from the player's clock to the room: the output window places the sound
by the server's clock, and its lyrics clock is the sound's position less this (DisplayPage/karaoke/KaraokeLayer.tsx).
It plays ten chirps from every output window and times them with the room mic, through show_service's live meter
(WS /v1/levels?wave=N: the mic's newest samples with each one's arrival time on CLOCK_MONOTONIC, the same clock here;
capture = arrival - MIC_PATH_S):

* The player pauses; every output window plays a quiet noise bed from the moment it learns of the run (LEAD_S ahead
  of the first chirp: an idle Sonos buffers more as it wakes: 25 Nov, 987 ms on a first run where songs ran 410 ms
  behind), then ten chirps, each its own (CHIRPS: rising or falling sweeps over different bands; owner, 25 Nov:
  "wouldn't it be better if they were separately coded ... so you could be confident matching on longer
  latencies"), at uneven times (OFFSETS_S), each rendered at its server time as the song's sound is.
* Each chirp is found by its own template alone over the whole window (up to MAX_DELAY_S), so each gives its own
  reading and a lost chirp costs one; the first SETTLE chirps are left out of the result (the speaker settling).
* Not the song itself: matching what the mic hears against the backing track locked onto the wrong beat of a steady
  song (25 Nov, Disco 2000: 1787 ms for 410).

A clear result sets the delay; anything else says why and changes nothing. Runs on its own thread;
``Store.calibration`` carries its progress and result for the panel.
"""
from __future__ import annotations

import base64
import json
import logging
import math
import threading
import time
from typing import Any, Callable, Optional

import numpy as np

log = logging.getLogger("karaoke.calibrate")

OFFSETS_S = (0.0, 0.62, 1.71, 2.35, 3.58, 4.16, 5.47, 6.05, 7.33, 7.94)   # uneven: one lag fits
LEAD_S = 4.0            # the windows poll /now once a second, then the noise bed wakes the speaker this long
SETTLE = 3              # the first chirps, left out of the result while the speaker settles
MAX_DELAY_S = 3.0       # the longest delay looked for (Bluetooth 0.1-0.5 s; a Sonos 0.4-1 s; AirPlay ~2 s)
MIN_DELAY_S = -0.1
TAIL_S = MAX_DELAY_S + 0.4
MIC_PATH_S = 0.005      # capture -> show_service arrival (the agent, measured 16 Oct: 5 ms median)
CHIRP_S = 0.04
#: Ten different chirps (Hz from, Hz to): rising and falling, over different bands, so no two look alike.
CHIRPS = [(600, 1500), (4800, 2000), (1000, 3200), (3600, 900), (1800, 5200),
          (2600, 700), (700, 2400), (5400, 3000), (1300, 4200), (3000, 1100)]
WAVE_N = 2048
MIC_WAIT_S = 6.0        # for the mic to come on and its samples to flow
MIN_PEAK_RATIO = 6.0    # the train's correlation peak over the median of the rest
MAX_SPREAD_MS = 25.0
MIN_AGREE = 5           # settled chirps within the spread of the median


def chirp(sr: int, f0: float = 900.0, f1: float = 4500.0, dur_s: float = CHIRP_S) -> np.ndarray:
    """The calibration chirp (the display page builds the same): a linear sweep f0 -> f1 under a Hann window."""
    n = max(8, int(round(dur_s * sr)))
    t = np.arange(n) / sr
    ph = 2 * math.pi * (f0 * t + (f1 - f0) * t * t / (2 * dur_s))
    return (np.sin(ph) * np.hanning(n)).astype(np.float64)


def assemble(frames: list[tuple[float, np.ndarray]], sr: int) -> tuple[float, np.ndarray]:
    """The levels frames ((capture time of the window's last sample, samples)) as one signal: (time of sample 0,
    samples). Overlaps are the same samples; a gap stays zero."""
    if not frames:
        return 0.0, np.zeros(0)
    idx0 = min(round(t * sr) - len(w) + 1 for t, w in frames)
    idx1 = max(round(t * sr) for t, w in frames)
    out = np.zeros(idx1 - idx0 + 1)
    for t, w in frames:
        end = round(t * sr) - idx0
        out[end - len(w) + 1:end + 1] = w
    return idx0 / sr, out


def chirp_specs(n: int) -> list[dict]:
    """The chirps for ``n`` clicks, as /now carries them to the output windows: [{f0, f1, dur_s}]."""
    return [{"f0": float(CHIRPS[k % len(CHIRPS)][0]), "f1": float(CHIRPS[k % len(CHIRPS)][1]), "dur_s": CHIRP_S}
            for k in range(n)]


def measure(t0: float, sig: np.ndarray, sr: int, clicks: list[float], specs: Optional[list[dict]] = None) -> dict:
    """The delay of the chirps (``clicks``: times on t0's clock; ``specs``: each one's sweep) in ``sig`` (from t0):
    each chirp is looked for with its own template over the whole window (MIN_DELAY_S..MAX_DELAY_S) and gives its own
    reading; the readings' median is the delay. {ok, delay_ms, spread_ms, agree, of, peak_ratio, why}."""
    specs = specs or chirp_specs(len(clicks))
    if len(sig) < sr:
        return {"ok": False, "why": "The mic sent almost nothing."}
    sig = sig - np.mean(sig)
    nfft = 1 << (len(sig) + int(CHIRP_S * sr) * 2 - 1).bit_length()
    S = np.fft.rfft(sig, nfft)
    lags = np.arange(int(MIN_DELAY_S * sr), int(MAX_DELAY_S * sr))
    per, ratios = [], []
    for ck, sp in zip(clicks, specs):
        c = chirp(sr, sp["f0"], sp["f1"], sp["dur_s"])
        env = np.abs(np.fft.irfft(S * np.conj(np.fft.rfft(c, nfft)), nfft)[:len(sig)])
        ii = int(round((ck - t0) * sr)) + lags
        ok = (ii >= 0) & (ii < len(env))
        if ok.sum() < len(lags) // 2:
            continue
        win = env[ii[ok]]
        k = int(np.argmax(win))
        ratio = float(win[k] / (np.median(win) + 1e-12))
        ratios.append(ratio)
        if ratio >= MIN_PEAK_RATIO:
            per.append(lags[ok][k] / sr)
    ratio = float(np.median(ratios)) if ratios else 0.0
    if not per:
        return {"ok": False, "of": len(clicks), "peak_ratio": round(ratio, 1),
                "why": "No chirps were heard: is the output window open (reload it), and the speaker on and loud enough?"}
    readings = [round(x * 1000) for x in per]
    use = per[SETTLE:] if len(per) >= SETTLE + MIN_AGREE else per          # the speaker settled
    med = float(np.median(use))
    spread = float(np.median(np.abs(np.array(use) - med))) * 1000
    agree = int(sum(abs(p - med) * 1000 <= max(10.0, 3 * spread) for p in use))
    res = {"delay_ms": round(med * 1000), "spread_ms": round(spread, 1), "agree": agree, "used": len(use),
           "of": len(clicks), "peak_ratio": round(ratio, 1), "readings_ms": readings}
    if agree < min(MIN_AGREE, len(use)) or len(use) < 4:
        return {**res, "ok": False, "why": f"Only {agree} of {len(clicks)} chirps heard clearly: try again with the room quieter, or the speaker louder."}
    if spread > MAX_SPREAD_MS:
        return {**res, "ok": False, "why": f"The chirps came back unevenly (±{spread:.0f} ms): try again."}
    return {**res, "ok": True, "why": None}


class Calibrator:
    """One run at a time (``start``); progress and the result in ``store.calibration``."""

    def __init__(self, store: Any, levels_url: str, *, connect: Optional[Callable[[str], Any]] = None) -> None:
        self.store = store
        base = levels_url.rstrip("/")
        base = "ws" + base[4:] if base.startswith("http") else base       # https -> wss, http -> ws
        self.levels_url = base + f"/v1/levels?wave={WAVE_N}"
        self._connect = connect
        self._thread: Optional[threading.Thread] = None

    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> dict:
        if self.running():
            return self.store.calibration or {}
        cid = f"c{int(time.time() * 1000)}"
        self.store.set_calibration({"id": cid, "state": "starting", "message": "Turning the mic on…"})
        self._thread = threading.Thread(target=self._run, args=(cid,), name="karaoke-calibrate", daemon=True)
        self._thread.start()
        return self.store.calibration

    def _ws(self) -> Any:
        if self._connect is not None:
            return self._connect(self.levels_url)
        import websocket                                  # websocket-client
        return websocket.create_connection(self.levels_url, timeout=2)

    def _run(self, cid: str) -> None:
        try:
            res = self._listen(cid)
        except Exception as e:  # noqa: BLE001
            log.exception("calibration failed")
            res = {"ok": False, "why": f"The calibration broke: {e}"}
        how = f"{res.get('agree')} of {res.get('used')} settled chirps agree within ±{res.get('spread_ms', 0):.0f} ms"
        done = {"id": cid, "state": "done" if res.get("ok") else "failed", "at": time.time(), **res,
                "message": f"Speaker delay set to {res['delay_ms']} ms ({how})" if res.get("ok") else res.get("why")}
        if res.get("ok"):
            self.store.settings(audio_delay_ms=int(res["delay_ms"]))
        log.info("calibration %s: %s", done["state"], json.dumps({k: done.get(k) for k in
                 ("delay_ms", "spread_ms", "agree", "used", "readings_ms", "peak_ratio", "why")}))
        self.store.set_calibration(done)

    def _listen(self, cid: str) -> dict:
        mono_to_wall = time.time() - time.monotonic()
        # The run wants the mic (karaoke_feed reads /now): wait for its samples to flow.
        self.store.set_calibration({"id": cid, "state": "running", "message": "Turning the mic on…", "clicks": []})
        ws = self._ws()
        frames: list[tuple[float, np.ndarray]] = []
        sr = 48000
        try:
            deadline = time.monotonic() + MIC_WAIT_S
            got = 0
            while time.monotonic() < deadline and got < 10:
                if self._frame(ws, frames, mono_to_wall) is not None:
                    got += 1
            if got < 10:
                return {"ok": False, "why": "The room mic isn't sending: check Settings → Room mic and levels."}
            self.store.pause_for_calibration()
            t_first = time.time() + LEAD_S
            clicks = [round(t_first + o, 4) for o in OFFSETS_S]
            self.store.set_calibration({"id": cid, "state": "running", "clicks": clicks, "chirps": chirp_specs(len(clicks)),
                                        "message": "Listening: keep the room quiet for 10 seconds…"})
            end = clicks[-1] + TAIL_S
            while time.time() < end:
                self._frame(ws, frames, mono_to_wall)
        finally:
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass
        frames = [(t, w) for t, w in frames if t >= clicks[0] - 1.0]
        t0, sig = assemble(frames, sr)
        return measure(t0, sig, sr, clicks)

    @staticmethod
    def _frame(ws: Any, frames: list, mono_to_wall: float) -> Optional[float]:
        """One levels frame with a wave into ``frames`` as (wall capture time of its last sample, samples)."""
        try:
            raw = ws.recv()
        except Exception:  # noqa: BLE001 - a quiet moment (timeout): nothing this time
            return None
        try:
            o = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if not isinstance(o, dict) or not o.get("wb") or o.get("a") is None:
            return None
        w = np.frombuffer(base64.b64decode(o["wb"]), dtype="<i2").astype(np.float64) / 32767.0
        t = float(o["a"]) + mono_to_wall - MIC_PATH_S
        frames.append((t, w))
        return t
