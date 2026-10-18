"""The fast audio feed for scope and FFT effects (owner, 18 Oct: oscilloscope / real-time FFT overlays).

music.features' spectrum32 is 25 Hz and smoothed (tau >= 300 ms), and the bus carries no waveform.  show_service's
live meter (``WS /v1/levels``, show_service/levels.py) has both, raw, at up to 60 frames a second, so the renderer
opens it once per process:

  ScopeFeed   one per process (server.make_app: ``mgr.scope_feed``).  An asyncio task connects to the levels WS
              derived from the first --music-bus URL (same scheme / host / port; path /v1/levels, or the bus path
              with its /v1/show replaced; query wave=WAVE_N).  No music bus = no feed.  Connected only while
              ``wanted()`` (server.make_app: a live session shows a plug-in that listens "scope" / "fft"), and for
              LINGER_S after (3a, 18 Oct: the meter costs show_service ~25 % of its bus loop while a client is on).
              Reconnects with full-jitter backoff 0.5 s doubling to 5 s (as showbus.py).  The newest frame is ONE immutable tuple, swapped by
              reference, so render threads read ``feed.latest`` without a lock:
                  (arrival_mono, wave float32 [WAVE_N] -1..1 or None, bands float32 [FFT_BANDS] dBFS or None, peak_dbfs)
              ``arrival_mono`` is show_service's time.monotonic() of the packet (``a``; same host, same
              CLOCK_MONOTONIC), or our own receipt time when ``a`` is missing or implausible.
  ScopeState  pure numpy, GPU-free: what one session's Sound (sound.py) steps once per rendered frame on its render
              thread, turning the newest frame (or the session's smoothed spectrum32 when the feed is not live)
              into the arrays effects read: wave, wave_trigger, fft, fft_peak, fft_history + fft_row, level, live.
              Preallocated; never raises (an unexpected error is logged once and the last values stay).
  ScopeFields the Sound-side half shared by sound.Sound and tools/fxtest.FxSound: GPU copies and read-only
              properties (CONVENTIONS §2b, listens "scope" / "fft").

Nothing here gates or trips: a dead feed eases to zeros (wave) or to the smoothed spectrum (fft).
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import math
import random
import time
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit

import numpy as np

log = logging.getLogger("stream_service.scope_feed")

# Mirrors show_service/levels.py (SPEC_BANDS, SPEC_LO_HZ/SPEC_HI_HZ, the ?wave=N we ask for).  Not imported:
# stream_service never imports show_service.
WAVE_N = 1024
FFT_BANDS = 48
FFT_LO_HZ, FFT_HI_HZ = 40.0, 16000.0
FLOOR_DBFS = -120.0
# spectrum32 (show_service/contract.py SPEC_EDGES_HZ): 32 log bands 30 Hz - 16 kHz, values 0..1
SPEC32_BANDS, SPEC32_LO_HZ, SPEC32_HI_HZ = 32, 30.0, 16000.0

FFT_HIST = 128
HIST_HZ = 30.0
HIST_CATCHUP = 4
STALE_S = 0.5
WAVE_FADE_S = 0.3
AGC_TARGET = 0.7
AGC_MIN, AGC_MAX = 1.0, 40.0
AGC_UP_TAU_S = 2.0           # gain rises slowly (a quiet room comes up over ~2 s) ...
AGC_DOWN_TAU_S = 0.5         # ... and comes down faster, so a loud entry clips (softly) only briefly
SOFT_CLIP = 1.2
DB_LO, DB_HI = -80.0, -12.0
FFT_RELEASE_TAU_S = 0.12
PEAK_HOLD_S = 0.4
PEAK_FALL_PER_S = 1.5
TRIG_HYST = 0.05

BACKOFF_MIN_S = 0.5
BACKOFF_MAX_S = 5.0
WANT_POLL_S = 0.5            # how often a disconnected feed asks whether anything listens
LINGER_S = 30.0              # stays connected this long after the last listener left (no reconnect churn)

Frame = tuple  # (arrival_mono, wave | None, bands | None, peak_dbfs)


def levels_url(music_bus: Optional[str], wave_n: int = WAVE_N) -> Optional[str]:
    """The levels WS for a music-bus URL: same scheme / host / port.  ``.../v1/show`` -> ``.../v1/levels`` (a
    pass-through prefix is kept); any other path -> ``/v1/levels``.  http(s) maps to ws(s).  None: no bus."""
    if not music_bus or not isinstance(music_bus, str):
        return None
    try:
        u = urlsplit(music_bus.strip())
    except ValueError:
        return None
    scheme = {"ws": "ws", "wss": "wss", "http": "ws", "https": "wss"}.get(u.scheme.lower())
    if scheme is None or not u.netloc:
        return None
    path = u.path.rstrip("/")
    path = path[: -len("/v1/show")] + "/v1/levels" if path.endswith("/v1/show") else "/v1/levels"
    return urlunsplit((scheme, u.netloc, path, f"wave={int(wave_n)}", ""))


def _finite(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v)


def parse_frame(raw: Any, local_now: float) -> Optional[Frame]:
    """One levels frame (a str or a decoded dict) -> the immutable Frame tuple, or None when it is malformed.

    ``pk`` is required (a number).  The waveform (optional) is ``wb``, base64 little-endian int16, or the older
    ``w``, a list of ints; ``f`` (optional) is a list of FFT_BANDS numbers.  A waveform of another length is fitted to
    WAVE_N (the newest samples, zeros before).  A ``pong`` reply is not a frame (None)."""
    try:
        d = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else raw
    except ValueError:
        return None
    if not isinstance(d, dict):
        return None
    pk = _finite(d.get("pk"))
    if pk is None:
        return None
    wave = bands = None
    a = None
    wb, w = d.get("wb"), d.get("w")
    if wb is not None:
        if not isinstance(wb, str) or not wb:
            return None
        try:
            raw16 = base64.b64decode(wb, validate=True)
        except (binascii.Error, ValueError):
            return None
        if len(raw16) < 2 or len(raw16) % 2:
            return None
        a = np.frombuffer(raw16, "<i2").astype(np.float32)
    elif w is not None:
        if not isinstance(w, list) or not w:
            return None
        try:
            a = np.asarray(w, np.float32)
        except (TypeError, ValueError):
            return None
        if a.ndim != 1 or not np.isfinite(a).all():
            return None
    if a is not None:
        a *= 1.0 / 32767.0
        if a.size >= WAVE_N:
            wave = np.ascontiguousarray(a[-WAVE_N:])
        else:
            wave = np.zeros(WAVE_N, np.float32)
            wave[WAVE_N - a.size:] = a
        np.clip(wave, -1.0, 1.0, out=wave)
        wave.setflags(write=False)
    f = d.get("f")
    if f is not None:
        if not isinstance(f, list) or len(f) != FFT_BANDS:
            return None
        try:
            bands = np.asarray(f, np.float32)
        except (TypeError, ValueError):
            return None
        if bands.ndim != 1 or not np.isfinite(bands).all():
            return None
        bands.setflags(write=False)
    a_t = _finite(d.get("a"))
    arrival = a_t if a_t is not None and local_now - 5.0 <= a_t <= local_now + 0.05 else local_now
    return (arrival, wave, bands, max(FLOOR_DBFS, pk))


class ScopeFeed:
    """One per process: the newest levels frame (``latest``), read lock-free by every session's render thread."""

    def __init__(self, url: Optional[str]) -> None:
        self.url = url
        self.latest: Optional[Frame] = None
        self.wanted: Any = None             # () -> bool: something listens (None: always connected)
        self._wanted_at = -1e9              # monotonic time it was last wanted
        self.up = False
        self.frames = 0
        self.bad = 0
        self.reconnects = 0
        self.last_error: Optional[str] = None
        self.band_lo_hz: float = FFT_LO_HZ
        self.band_hi_hz: float = FFT_HI_HZ
        self.taps: list = []                # f(frame) for each new ``latest``: the render workers' copies (workers.py)

    @classmethod
    def from_music_bus(cls, music_bus: Any) -> Optional["ScopeFeed"]:
        """From --music-bus (a URL or the list of them; the first is used).  None when there is no usable bus."""
        if isinstance(music_bus, (list, tuple)):
            music_bus = music_bus[0] if music_bus else None
        url = levels_url(music_bus)
        return cls(url) if url else None

    def on_text(self, data: Any, local_now: Optional[float] = None) -> bool:
        """One received text message (loop thread).  True when it became ``latest``."""
        now = time.monotonic() if local_now is None else local_now
        try:
            d = json.loads(data) if isinstance(data, (str, bytes, bytearray)) else data
        except ValueError:
            d = None
        if isinstance(d, dict) and "pong" in d:
            return False                    # a ping reply, not a frame (we never ping; harmless)
        fr = parse_frame(d, now) if d is not None else None
        if fr is None:
            self.bad += 1
            return False
        lo, hi = _finite(d.get("fl")), _finite(d.get("fh"))
        if lo is not None and hi is not None and 0 < lo < hi:
            self.band_lo_hz, self.band_hi_hz = lo, hi
        self.latest = fr                    # one reference swap: readers see the old tuple or the new one
        self.frames += 1
        for f in self.taps:
            try:
                f(fr)
            except Exception:
                log.debug("scope feed: tap failed", exc_info=True)
        return True

    def is_wanted(self, now: Optional[float] = None) -> bool:
        """Something listens now, or did within LINGER_S (``wanted`` None: always).  Never raises."""
        now = time.monotonic() if now is None else now
        if self.wanted is None:
            return True
        try:
            if self.wanted():
                self._wanted_at = now
                return True
        except Exception:
            log.debug("scope feed: wanted() failed", exc_info=True)
            self._wanted_at = now               # unknown: stay as if wanted
            return True
        return now - self._wanted_at <= LINGER_S

    async def run(self) -> None:
        if not self.url:
            return
        import aiohttp

        backoff = BACKOFF_MIN_S
        was_up: Optional[bool] = None
        while True:
            if not self.is_wanted():
                if was_up:
                    log.info("scope feed idle: nothing listens (disconnected from %s)", self.url)
                    was_up = None
                await asyncio.sleep(WANT_POLL_S)
                continue
            reason = ""
            try:
                async with aiohttp.ClientSession() as http:
                    async with http.ws_connect(self.url, heartbeat=10.0, max_msg_size=1 << 20) as ws:
                        self.up, backoff = True, BACKOFF_MIN_S
                        if was_up is not True:
                            log.info("scope feed up: %s", self.url)
                        was_up = True
                        idle = False
                        while True:
                            try:
                                msg = await ws.receive(timeout=1.0)
                            except asyncio.TimeoutError:
                                msg = None
                            if not self.is_wanted():
                                idle = True
                                break
                            if msg is None:
                                continue
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                try:
                                    self.on_text(msg.data)
                                except Exception:
                                    self.bad += 1
                            elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING,
                                              aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
                        if idle:
                            self.up = False
                            continue                    # closed on purpose: back to waiting for a listener
                        reason = f"closed ({ws.close_code})"
            except asyncio.CancelledError:
                self.up = False
                raise
            except Exception as e:  # refused, timeout, ...: retry
                reason = f"{type(e).__name__}: {e}"
            self.up = False
            self.reconnects += 1
            self.last_error = reason
            if was_up is not False:
                log.warning("scope feed down (%s); retrying", reason)
                was_up = False
            await asyncio.sleep(random.uniform(0, backoff))
            backoff = min(BACKOFF_MAX_S, backoff * 2)

    def stats(self) -> dict:
        fr = self.latest
        age = None if fr is None else round(max(0.0, time.monotonic() - fr[0]), 3)
        return {"url": self.url, "up": self.up, "wanted": self.is_wanted(), "frames": self.frames, "bad": self.bad,
                "reconnects": self.reconnects, "age_s": age, "last_error": self.last_error,
                "wave": fr is not None and fr[1] is not None, "bands": fr is not None and fr[2] is not None}


def _spec32_map() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """For each of the FFT_BANDS band centres: the two spectrum32 bands around it and the blend weight."""
    edges = np.geomspace(FFT_LO_HZ, FFT_HI_HZ, FFT_BANDS + 1)
    centres = np.sqrt(edges[:-1] * edges[1:])
    # spectrum32 band k's centre sits at fractional log position (k + 0.5) / 32
    pos = np.log(centres / SPEC32_LO_HZ) / np.log(SPEC32_HI_HZ / SPEC32_LO_HZ) * SPEC32_BANDS - 0.5
    pos = np.clip(pos, 0.0, SPEC32_BANDS - 1.0)
    i0 = np.floor(pos).astype(np.intp)
    i1 = np.minimum(i0 + 1, SPEC32_BANDS - 1)
    return i0, i1, (pos - i0).astype(np.float32)


class ScopeState:
    """One session's scope / FFT state, stepped once per rendered frame on its render thread (see the module doc).

    Outputs (host float32, preallocated, refreshed in place):
      wave [WAVE_N]        DC removed, slow AGC, soft clip: about -1..1 (at most +-SOFT_CLIP); zeros without a feed
      wave_trigger         index of the latest rising zero crossing in wave[:WAVE_N // 2] (hysteresis), 0 if none
      fft [FFT_BANDS]      0..1 (-80 dBFS -> 0, -12 dBFS -> 1), instant attack, release tau 0.12 s
      fft_peak [FFT_BANDS] peak hold 0.4 s, then falls at 1.5 per s
      fft_history [FFT_HIST, FFT_BANDS] a ring of fft rows, one per 1/30 s; fft_row = the newest row
      level 0..1           fast loudness (the frame's peak dBFS, same mapping); live: a frame within STALE_S
    """

    def __init__(self) -> None:
        self.wave = np.zeros(WAVE_N, np.float32)
        self.wave_trigger = 0
        self.fft = np.zeros(FFT_BANDS, np.float32)
        self.fft_peak = np.zeros(FFT_BANDS, np.float32)
        self.fft_history = np.zeros((FFT_HIST, FFT_BANDS), np.float32)
        self.fft_row = 0
        self.rows_written = 0                      # rows written by the latest update (0 = history unchanged)
        self.level = 0.0
        self.live = False
        self.gain = AGC_MIN
        self.fade = 0.0
        self.errors = 0
        self._shape = np.zeros(WAVE_N, np.float32)       # the AGC'd, clipped trace before the fade
        self._tmp = np.zeros(WAVE_N, np.float32)
        self._target = np.zeros(FFT_BANDS, np.float32)
        self._decay = np.zeros(FFT_BANDS, np.float32)
        self._hold = np.zeros(FFT_BANDS, np.float32)
        self._ftmp = np.zeros(FFT_BANDS, np.float32)
        self._fmask = np.zeros(FFT_BANDS, bool)
        self._lb = np.zeros(WAVE_N, np.intp)
        self._la = np.zeros(WAVE_N, np.intp)
        self._idx1 = np.arange(1, WAVE_N + 1, dtype=np.intp)
        self._below = np.zeros(WAVE_N, bool)
        self._above = np.zeros(WAVE_N, bool)
        self._i0, self._i1, self._wt = _spec32_map()
        self._hist_acc = 0.0
        self._agc_dt = 0.0
        self._wave_src: Any = None                 # the frame tuple whose wave _shape was built from
        self._logged = False

    # -- helpers ----------------------------------------------------------------------------------------------------
    @staticmethod
    def db_to_unit(db: float) -> float:
        return min(1.0, max(0.0, (db - DB_LO) / (DB_HI - DB_LO)))

    def _bands_target(self, bands: np.ndarray) -> None:
        t = self._target
        np.subtract(bands, DB_LO, out=t)
        np.multiply(t, 1.0 / (DB_HI - DB_LO), out=t)
        np.clip(t, 0.0, 1.0, out=t)

    def _fallback_target(self, spec: Any) -> bool:
        t = self._target
        try:
            s = np.asarray(spec, np.float32)
        except (TypeError, ValueError):
            s = None
        if s is None or s.shape != (SPEC32_BANDS,):
            t[:] = 0.0
            return False
        np.subtract(s[self._i1], s[self._i0], out=t)
        np.multiply(t, self._wt, out=t)
        np.add(t, s[self._i0], out=t)
        np.nan_to_num(t, copy=False, nan=0.0, posinf=1.0, neginf=0.0)
        np.clip(t, 0.0, 1.0, out=t)
        return True

    def _trace(self, wave: np.ndarray, dt: float) -> None:
        """DC removal, AGC (eased with dt), soft clip -> _shape; then the trigger."""
        x = self._tmp
        np.subtract(wave, float(wave.mean()), out=x)
        peak = max(float(x.max()), -float(x.min()), 1e-6)
        target = min(AGC_MAX, max(AGC_MIN, AGC_TARGET / peak))
        tau = AGC_UP_TAU_S if target > self.gain else AGC_DOWN_TAU_S
        self.gain += (target - self.gain) * (1.0 - math.exp(-min(dt, 60.0) / tau))
        self.gain = min(AGC_MAX, max(AGC_MIN, self.gain))
        np.multiply(x, self.gain / SOFT_CLIP, out=x)
        np.tanh(x, out=x)
        np.multiply(x, SOFT_CLIP, out=self._shape)
        self.wave_trigger = self._trigger(self._shape)

    def _trigger(self, s: np.ndarray) -> int:
        """Latest rising zero crossing i in [1, WAVE_N // 2) with s[i-1] < 0 <= s[i], armed by a dip below
        -TRIG_HYST since the trace was last above +TRIG_HYST.  0 if none."""
        np.less(s, -TRIG_HYST, out=self._below)
        np.greater(s, TRIG_HYST, out=self._above)
        np.multiply(self._below, self._idx1, out=self._lb)
        np.maximum.accumulate(self._lb, out=self._lb)      # 1 + the last index below -h (0: none yet)
        np.multiply(self._above, self._idx1, out=self._la)
        np.maximum.accumulate(self._la, out=self._la)
        h = WAVE_N // 2
        prev, cur = s[: h - 1], s[1:h]
        ok = (prev < 0.0) & (cur >= 0.0) & (self._lb[: h - 1] > self._la[: h - 1])
        hits = np.flatnonzero(ok)
        return int(hits[-1]) + 1 if hits.size else 0

    # -- the frame step ---------------------------------------------------------------------------------------------
    def update(self, now_mono: float, frame: Optional[Frame], fallback: Any = None, dt: float = 0.0) -> None:
        """Render thread, once per frame.  ``frame``: ScopeFeed.latest (or None); ``fallback``: the session's
        spectrum32 (0..1, 32 bands) used for fft while the fast feed is not live; ``dt``: real seconds since the
        previous update (0, negative or not finite: treated as 0).  Never raises."""
        try:
            self._update(float(now_mono), frame, fallback, dt)
        except Exception:
            self.errors += 1
            if not self._logged:
                self._logged = True
                log.exception("scope state update failed; keeping the last values")

    def _update(self, now: float, frame: Optional[Frame], fallback: Any, dt: Any) -> None:
        dt = float(dt) if isinstance(dt, (int, float)) and math.isfinite(dt) and dt > 0.0 else 0.0
        arrival = wave = bands = None
        peak_db = FLOOR_DBFS
        if frame is not None:
            arrival, wave, bands, peak_db = frame
        self.live = live = arrival is not None and now - float(arrival) <= STALE_S
        # -- wave: fade in while a live wave arrives, out (never a snap) otherwise --
        has_wave = live and wave is not None
        self._agc_dt += dt                          # real time since the trace was last built
        if has_wave and frame is not self._wave_src:
            self._trace(wave, self._agc_dt)
            self._wave_src, self._agc_dt = frame, 0.0
        elif not has_wave:
            self._agc_dt = 0.0
        step = dt / WAVE_FADE_S
        self.fade = min(1.0, self.fade + step) if has_wave else max(0.0, self.fade - step)
        if self.fade < 1e-6:
            self.fade = 0.0
        if self.fade <= 0.0:
            if self.wave.any():
                self.wave[:] = 0.0
            if not has_wave:
                self.wave_trigger = 0
        else:
            np.multiply(self._shape, self.fade, out=self.wave)
        # -- fft target: the fast bands, else the smoothed spectrum32, else silence --
        if live and bands is not None:
            self._bands_target(bands)
            fb = False
        else:
            fb = self._fallback_target(fallback)
        if live:
            lvl_target = self.db_to_unit(float(peak_db))
        else:
            lvl_target = float(self._target.max()) if fb else 0.0
        k = math.exp(-dt / FFT_RELEASE_TAU_S)
        f, t = self.fft, self._target
        np.subtract(f, t, out=self._decay)          # release toward the target ...
        np.multiply(self._decay, k, out=self._decay)
        np.add(self._decay, t, out=self._decay)
        np.maximum(self._decay, t, out=f)           # ... instant attack
        np.clip(f, 0.0, 1.0, out=f)
        self.level = lvl_target if lvl_target >= self.level else lvl_target + (self.level - lvl_target) * k
        # -- peaks: hold, then fall --
        hold, ft = self._hold, self._ftmp
        np.subtract(hold, dt, out=hold)             # hold time left after this frame (negative: falling)
        np.negative(hold, out=ft)
        np.clip(ft, 0.0, dt, out=ft)                # the part of dt spent falling
        np.maximum(hold, 0.0, out=hold)
        np.multiply(ft, PEAK_FALL_PER_S, out=ft)
        np.subtract(self.fft_peak, ft, out=self.fft_peak)
        np.greater_equal(f, self.fft_peak, out=self._fmask)
        hold[self._fmask] = PEAK_HOLD_S
        np.maximum(self.fft_peak, f, out=self.fft_peak)
        np.clip(self.fft_peak, 0.0, 1.0, out=self.fft_peak)
        # -- history: one row per 1/HIST_HZ of real time, at most HIST_CATCHUP after a stall --
        self._hist_acc += dt
        n = int(self._hist_acc * HIST_HZ)
        self.rows_written = 0
        if n > 0:
            self._hist_acc -= n / HIST_HZ
            for _ in range(min(n, HIST_CATCHUP)):
                self.fft_row = (self.fft_row + 1) % FFT_HIST
                self.fft_history[self.fft_row] = f
                self.rows_written += 1


class ScopeFields:
    """The listens "scope" / "fft" half of a Sound (sound.Sound, tools/fxtest.FxSound): GPU copies of a
    ScopeState and read-only views of it.  The owner sets ``self.scope`` (a ScopeState)."""

    scope: ScopeState
    wave_gpu: Any = None
    fft_gpu: Any = None
    fft_peak_gpu: Any = None
    fft_history_gpu: Any = None

    def scope_alloc(self, cp) -> None:
        """Worker thread, on the session's device (never on the render thread, R20)."""
        if self.wave_gpu is None:
            self.wave_gpu = cp.zeros((WAVE_N,), cp.float32)
            self.fft_gpu = cp.zeros((FFT_BANDS,), cp.float32)
            self.fft_peak_gpu = cp.zeros((FFT_BANDS,), cp.float32)
            self.fft_history_gpu = cp.zeros((FFT_HIST, FFT_BANDS), cp.float32)

    def scope_upload(self, full_history: bool = False) -> None:
        """Render thread: small host-to-device copies on the current stream (no sync, no .get())."""
        st = self.scope
        if self.wave_gpu is None:
            return
        self.wave_gpu.set(st.wave)                           # 4 KB
        self.fft_gpu.set(st.fft)
        self.fft_peak_gpu.set(st.fft_peak)
        if st.rows_written or full_history:
            self.fft_history_gpu.set(st.fft_history)         # 24 KB, only when a row was written

    @property
    def wave(self) -> np.ndarray:
        return self.scope.wave

    @property
    def fft(self) -> np.ndarray:
        return self.scope.fft

    @property
    def fft_peak(self) -> np.ndarray:
        return self.scope.fft_peak

    @property
    def fft_history(self) -> np.ndarray:
        return self.scope.fft_history

    @property
    def fft_row(self) -> int:
        return self.scope.fft_row

    @property
    def wave_trigger(self) -> int:
        return self.scope.wave_trigger

    @property
    def level(self) -> float:
        return self.scope.level

    @property
    def scope_live(self) -> bool:
        return self.scope.live
