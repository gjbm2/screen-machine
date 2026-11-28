"""An output's delay, measured with the room mic: one calibration for every sound output (docs/SOUND.md). It was
karaoke's (routes/karaoke/calibrate.py, 25 Nov); now any screen or Sonos zone, for every consumer, into one store
(smsound.latency).

A run (``Calibrator.start(output)``), on its own thread, one at a time:

1. **The mic.** Its state says "running" from the start, so show_service turns the room mic on (sound_cal_feed.py: a
   want, like karaoke's); it waits for the mic's samples (show_service ``WS /v1/levels?wave=N``: the newest samples
   with each frame's arrival time on CLOCK_MONOTONIC, this machine's; capture = arrival - MIC_PATH_S).
2. **Quiet.** The ``before`` hooks run (karaoke pauses its player).
3. **The chirps** (smsound.chirps), by the output's kind:
   * ``screen:<id>``: the run's state carries the chirps' server times (``clicks``) and sweeps (``chirps``); that
     screen's display page (DisplayPage/SoundCalibration.tsx, polling GET /api/sound/calibrate) plays a noise bed at
     once and each chirp at its time, as it schedules a song. Measured: schedule -> room.
   * ``sonos:<zone>``: the chirps as a file (bed, then the chirps), played by piano_render.sonos.SonosOutput exactly as
     a piece or a song is. It says when the room will hear the file's start (t_audio0, by the zone's current start
     delay); the chirps are looked for around that, and the zone's delay is the current one plus what they were off
     by. Measured: room_service's play request -> room (piano_render/sonos.py ``start_ms``).
   * ``in`` (a sound coming in, not an output): the page that asked (Settings › Sound & timing) plays the chirps on
     its own speaker, each leaving it at its time on show_service's clock (``clicks_mono``; the page's audio output
     timestamps), and the mic's samples are timed by the beat clock's own time for them (levels ``k``). What they
     are off by is how late the beat clock puts a sound: the stream's acoustic offset moves by minus that.
4. **The result** goes into smsound.latency (``record``; an input: ``record_source``), or the run says why not and
   nothing changes.
"""
from __future__ import annotations

import base64
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

import smconfig
from smsound import chirps as C
from smsound import latency as L
from smsound import outputs as O

log = logging.getLogger("sound.calibrate")

LEAD_S = 4.0            # the display pages poll once a second; then the noise bed wakes the speaker this long
WAVE_N = 2048
MIC_WAIT_S = 6.0
MIC_PATH_S = 0.005      # capture -> show_service arrival (the agent, measured 16 Oct: 5 ms median)
SONOS_BED_S = 2.0       # the file's noise bed before its first chirp
SONOS_WINDOW_S = (-1.5, 3.0)   # how far from the zone's current delay the chirps are looked for
SONOS_VOLUME = 25              # a zone at volume 0 plays the chirps this loud (0..100): heard across a room
SR = 48000
INPUT = "in"                   # the sound coming in (the beat clock's own lateness), not an output
IN_WINDOW_S = (-0.3, 0.6)      # how far the beat clock may be off


class Calibrator:
    """One run at a time; ``state()`` is its progress and result (GET /api/sound/calibrate)."""

    def __init__(self, levels_url: str, *, connect: Optional[Callable[[str], Any]] = None,
                 sonos_factory: Optional[Callable[[str], Any]] = None) -> None:
        base = levels_url.rstrip("/")
        self.levels_base = ("ws" + base[4:] if base.startswith("http") else base) + f"/v1/levels?wave={WAVE_N}"
        self._connect = connect
        self._sonos_factory = sonos_factory
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._state: dict = {"state": "idle"}
        self.before: list[Callable[[str], None]] = []    # run before the chirps, with the output id
        self._sonos_put_back: Optional[int] = None       # the volume a zone turned up for the chirps goes back to

    # ---- state
    def state(self) -> dict:
        with self._lock:
            return dict(self._state)

    def _set(self, **kw: Any) -> None:
        with self._lock:
            self._state = {**self._state, **kw}

    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, output: str, mic: Optional[str] = None) -> dict:
        """Start measuring ``output`` (ValueError: it can't be measured from here)."""
        k = O.kind(output)
        if output == INPUT:
            k = INPUT
        elif O.CALIBRATE.get(k or "") != "chirp":
            raise ValueError({"pc22": "The PC's speakers are measured by the piano itself while it plays.",
                              "device": "A phone or tablet measures its own delay.",
                              "none": "There's no sound to measure."}.get(k or "", f"Not a sound output: {output}"))
        if output != INPUT and not O.known(output):
            raise ValueError(f"This deployment has no {output}.")
        if self.running():
            return self.state()
        if not mic and output != INPUT:
            mic = O.room_mic(O.room_of(output))         # the output's room's own mic (rooms.json mics)
        cid = f"c{int(time.time() * 1000)}"
        with self._lock:
            self._state = {"id": cid, "state": "running", "output": output,
                           "label": "The sound coming in" if output == INPUT else O.label_of(output),
                           "mic": mic, "phase": "mic", "message": "Turning the mic on…", "clicks": [], "chirps": [],
                           "started_at": time.time()}
        self._thread = threading.Thread(target=self._run, args=(cid, output, mic), name="sound-calibrate", daemon=True)
        self._thread.start()
        return self.state()

    # ---- the run
    def _run(self, cid: str, output: str, mic: Optional[str]) -> None:
        try:
            res = self._listen(output, mic)
        except Exception as e:  # noqa: BLE001
            log.exception("calibration failed")
            res = {"ok": False, "why": f"The calibration broke: {e}"}
        label = "The sound coming in" if output == INPUT else O.label_of(output)
        if res.get("ok") and output == INPUT:
            stream = res.get("stream") or "?"
            cur = float(((L.read()["sources"].get(stream) or {}).get("acoustic_offset_ms")) or 0.0)
            new = round(cur - float(res["delay_ms"]), 1)
            extra = {k: res[k] for k in ("spread_ms", "agree", "used", "of", "readings_ms", "peak_ratio") if k in res}
            L.record_source(stream, new, method="chirp", extra={**extra, "was_ms": cur, "off_by_ms": res["delay_ms"]})
            msg = f"{stream}: the beat clock was {res['delay_ms']:+.0f} ms off; its offset is now {new:+.0f} ms"
        elif res.get("ok"):
            extra = {k: res[k] for k in ("spread_ms", "agree", "used", "of", "readings_ms", "peak_ratio") if k in res}
            if mic:
                extra["mic"] = mic
            L.record(output, float(res["delay_ms"]), method="chirp", extra=extra)
            eff = L.effective_ms(output)
            how = f"{res.get('agree')} of {res.get('used')} chirps agree within ±{res.get('spread_ms', 0):.0f} ms"
            msg = f"{label}: {res['delay_ms']:.0f} ms ({how})" + (
                f"; with your trim, {eff:.0f} ms" if eff is not None and abs(eff - res["delay_ms"]) >= 1 else "")
        else:
            msg = res.get("why") or "The calibration failed."
        log.info("calibration %s %s: %s", output, "done" if res.get("ok") else "failed", json.dumps(
            {k: res.get(k) for k in ("delay_ms", "spread_ms", "agree", "used", "readings_ms", "peak_ratio", "why")}))
        with self._lock:
            if self._state.get("id") == cid:
                self._state = {**self._state, **{k: v for k, v in res.items() if k != "ok"},
                               "state": "done" if res.get("ok") else "failed", "phase": None, "message": msg,
                               "at": time.time(), "clicks": [], "chirps": []}

    def _ws(self, mic: Optional[str]) -> Any:
        url = self.levels_base + (f"&stream={mic}" if mic else "")
        if self._connect is not None:
            return self._connect(url)
        import websocket                                  # websocket-client
        return websocket.create_connection(url, timeout=2)

    def _listen(self, output: str, mic: Optional[str]) -> dict:
        mono_to_wall = time.time() - time.monotonic()
        if output == INPUT:
            return self._listen_in(mic)
        ws = self._ws(mic)
        frames: list[tuple[float, np.ndarray]] = []
        out_obj = None
        try:
            deadline = time.monotonic() + MIC_WAIT_S
            got = 0
            while time.monotonic() < deadline and got < 10:
                if self._frame(ws, frames, mono_to_wall) is not None:
                    got += 1
            if got < 10:
                return {"ok": False, "why": "The room mic isn't sending: check the Room page's mic, and its levels."}
            for fn in list(self.before):
                try:
                    fn(output)
                except Exception:  # noqa: BLE001
                    log.exception("calibration: a before hook failed")
            specs = C.chirp_specs(len(C.OFFSETS_S))
            lo, hi = C.MIN_DELAY_S, C.MAX_DELAY_S
            base_ms = 0.0
            if O.kind(output) == "sonos":
                self._set(phase="sonos", message="Starting the Sonos…")
                base_ms = float(L.effective_ms(output) or 0.0)
                out_obj, t_audio0 = self._play_sonos(O.zone(output), specs)
                t_file0 = t_audio0 + mono_to_wall
                clicks = [round(t_file0 + SONOS_BED_S + o, 4) for o in C.OFFSETS_S]
                lo, hi = SONOS_WINDOW_S
                self._set(phase="listening", message="Listening: keep the room quiet for 15 seconds…")
            else:
                t_first = time.time() + LEAD_S
                clicks = [round(t_first + o, 4) for o in C.OFFSETS_S]
                self._set(phase="listening", clicks=clicks, chirps=specs,
                          message="Listening: keep the room quiet for 12 seconds…")
            end = clicks[-1] + hi + 0.4
            while time.time() < end:
                self._frame(ws, frames, mono_to_wall)
        finally:
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass
            if out_obj is not None:
                try:
                    out_obj.stop()
                except Exception:  # noqa: BLE001
                    log.exception("calibration: stopping the Sonos")
                if self._sonos_put_back is not None:
                    try:
                        out_obj.set_volume(self._sonos_put_back)
                    except Exception:  # noqa: BLE001
                        log.exception("calibration: putting the Sonos volume back")
        frames = [(t, w) for t, w in frames if t >= clicks[0] - 1.5]
        t0, sig = C.assemble(frames, SR)
        res = C.measure(t0, sig, SR, clicks, specs, min_delay_s=lo, max_delay_s=hi)
        if res.get("ok") and base_ms:
            res = {**res, "offset_ms": res["delay_ms"], "delay_ms": round(base_ms + res["delay_ms"])}
        return res

    def _listen_in(self, mic: Optional[str]) -> dict:
        """The sound coming in: chirps the asking page plays at show_service-clock times (``clicks_mono``), timed
        in the mic's samples by the beat clock's own time for them (levels ``k``)."""
        ws = self._ws(mic)
        frames: list[tuple[float, np.ndarray]] = []
        streams: dict[str, int] = {}
        try:
            deadline = time.monotonic() + MIC_WAIT_S
            got = 0
            while time.monotonic() < deadline and got < 10:
                s = self._frame(ws, frames, 0.0, on_clock=True, streams=streams)
                if s is not None:
                    got += 1
            if got < 10:
                return {"ok": False, "why": "The room mic isn't sending, or its clock isn't settled yet: try again in "
                                            "a minute."}
            for fn in list(self.before):
                try:
                    fn(INPUT)
                except Exception:  # noqa: BLE001
                    log.exception("calibration: a before hook failed")
            specs = C.chirp_specs(len(C.OFFSETS_S))
            t_first = time.monotonic() + LEAD_S
            clicks = [round(t_first + o, 4) for o in C.OFFSETS_S]
            self._set(phase="listening", clicks_mono=clicks, chirps=specs,
                      message="Listening: keep the room quiet for 12 seconds…")
            end = clicks[-1] + IN_WINDOW_S[1] + 0.4
            while time.monotonic() < end:
                self._frame(ws, frames, 0.0, on_clock=True, streams=streams)
        finally:
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass
        frames = [(t, w) for t, w in frames if t >= clicks[0] - 1.5]
        if not frames:
            return {"ok": False, "why": "The mic's samples stopped."}
        t0, sig = C.assemble(frames, SR)
        res = C.measure(t0, sig, SR, clicks, specs, min_delay_s=IN_WINDOW_S[0], max_delay_s=IN_WINDOW_S[1])
        stream = max(streams, key=streams.get) if streams else None
        return {**res, "stream": stream}

    def _play_sonos(self, zone: Optional[str], specs: list[dict]) -> tuple[Any, float]:
        """The chirps as a file on the zone: (the SonosOutput, t_audio0 on CLOCK_MONOTONIC)."""
        from smsound import files
        sig = C.render(SR, list(C.OFFSETS_S), specs, bed_s=SONOS_BED_S, tail_s=SONOS_WINDOW_S[1] + 0.5)
        path = files.write_flac(sig, SR, files.cache_dir(), "sound-calibration")
        if self._sonos_factory is not None:
            out = self._sonos_factory(zone)
        else:
            from piano_render.sonos import SonosOutput
            out = SonosOutput(zone)
        # Chirps at volume 0 are silent and the mic times noise (28 Nov: the Piano mode leaves the zone at 0): a zone
        # that is off plays them at its put-back volume, else SONOS_VOLUME, and goes back to 0 afterwards.
        self._sonos_put_back = None
        vol = None
        try:
            st = out.room_state()
            if not st.get("volume"):
                vol = st["put_back"] if st.get("put_back") else SONOS_VOLUME
                self._sonos_put_back = st.get("volume")
        except Exception:  # noqa: BLE001 - the play says what's wrong
            log.debug("calibration: the zone's state", exc_info=True)
        self._set(volume=vol)
        t_audio0 = out.start(path, lead_in_ms=0.0, url=files.audio_url(path.stem), title="Calibrating the speaker",
                             volume=vol)
        return out, float(t_audio0)

    @staticmethod
    def _frame(ws: Any, frames: list, mono_to_wall: float, *, on_clock: bool = False,
               streams: Optional[dict] = None) -> Optional[float]:
        """One levels frame with a wave into ``frames`` as (wall capture time of its last sample, samples); with
        ``on_clock``, the beat clock's own time for that sample (``k``: frames without one are skipped)."""
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
        if on_clock:
            if o.get("k") is None:
                return None
            t = float(o["k"])
            if streams is not None and o.get("s"):
                streams[o["s"]] = streams.get(o["s"], 0) + 1
            frames.append((t, w))
            return t
        t = float(o["a"]) + mono_to_wall - MIC_PATH_S
        frames.append((t, w))
        return t


_calibrator: Optional[Calibrator] = None


def calibrator() -> Calibrator:
    global _calibrator
    if _calibrator is None:
        _calibrator = Calibrator(smconfig.url("show"))
    return _calibrator
