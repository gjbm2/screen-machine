"""Karaoke on a Sonos room (docs/KARAOKE.md; owner, 26 Nov: "output to e.g. north-screen or living-room (video) and
living room sonos audio ... same logic / code paths as we use when e.g. playing midi files").

When the karaoke sound is ``sonos:<zone>`` (settings.output), the Sonos plays the song and the player's clock follows
what the room hears. It is the Music page's Play on Sonos, the same code:

* **The mix.** The backing track with the lead vocal at the guide level and child mode's mutes in it, from the place
  the song is at, made with ffmpeg as ``<key>.flac`` (key: 32 hex digits of what went into it) in piano_render's
  render cache ($PIANO_RENDER_CACHE, else ~/.cache/screen-machine/piano/renders): the folder show_service serves to
  the Sonos at ``/api/v1/piano/audio/<key>.flac`` (api.py), reached through room_service's LAN door
  (``http://<SM_LAN_HOST>:<room port>/show/...``), exactly as a rendered piano piece.
* **The play.** piano_render.sonos.SonosOutput(zone).start(mix, lead_in_ms, url, title): room_service plays it on the
  zone (volume as the room has it); the song's lead-in (Store.lead_in) is the padding SonosOutput puts before it (its
  padded copy, served the same way). It returns t_audio0, when the room hears the mix's start (latency.json's
  ``outputs["sonos:<zone>"].start_ms``), and later sends "rebase" as the Sonos's own position (RelTime edges) pins it.
* **The clock.** The player's position is moved, without a new rev, to say: the mix's start (the song at ``from``)
  is heard at t_audio0 (Store.anchor). The output windows show the lyrics by that clock with no delay of their own
  (KaraokeLayer: output sonos), and the song ends by it (Store.tick).
* **The player's moves.** A new rev while playing (play, seek, next, resume), or the guide level or child mode
  changing while it plays: a new mix from where the song is, played again. Paused or stopped: SonosOutput.stop()
  (room_service fades it out). Another output: stopped. Polled at POLL_S on its own thread.

Only where the deployment has Sonos (its ``sonos`` capability and room/sonos.json); elsewhere sonos outputs are refused
(Store.settings).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

import smconfig

from .store import Store, song_dir

log = logging.getLogger("karaoke.sonos")

POLL_S = 0.2
SR = 48000
SONOS_PREFIX = "sonos:"
# The render cache, the Sonos's address for a file in it, and the Sonos zones: the app's (smsound), shared with the
# piano and the speaker calibration.
from smsound.files import audio_url, cache_dir, lan_host  # noqa: E402,F401


def sonos_zones() -> dict[str, str]:
    """{zone: its room's name} of the deployment's Sonos zones (smsound.outputs.sonos_zones); {} without Sonos."""
    from smsound.outputs import sonos_zones as zones
    return {z: v["room"] for z, v in zones().items()}


def mix_key(sid: str, from_s: float, guide: float, mutes: list) -> str:
    return hashlib.md5(json.dumps(["karaoke-mix", 1, sid, round(from_s, 2), round(guide, 3), mutes]).encode()).hexdigest()


def make_mix(sid: str, from_s: float, guide: float, mutes: list, folder: Optional[Path] = None) -> Path:
    """The song's mix from ``from_s`` (module docstring) as <folder>/<key>.flac; made once."""
    folder = folder or cache_dir()
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{mix_key(sid, from_s, guide, mutes)}.flac"
    if out.is_file() and out.stat().st_size > 0:
        out.touch()
        return out
    d = song_dir(sid)
    inst, voc = d / "instrumental.m4a", d / "vocals.m4a"
    tmp = out.with_suffix(".part.flac")
    args = ["ffmpeg", "-v", "error", "-y", "-ss", f"{from_s:.3f}", "-i", str(inst)]
    if voc.is_file() and guide > 0:
        args += ["-ss", f"{from_s:.3f}", "-i", str(voc)]
        vf = f"volume={guide:.3f}"
        for a, b in mutes:                               # child mode: the lead vocal silent over each word
            if b > from_s:
                vf += f",volume=0:enable='between(t,{max(0.0, a - from_s):.3f},{b - from_s:.3f})'"
        fc = f"[1:a]{vf}[v];[0:a][v]amix=inputs=2:normalize=0:duration=first[m]"
        args += ["-filter_complex", fc, "-map", "[m]"]
    args += ["-ar", str(SR), "-ac", "2", "-c:a", "flac", "-compression_level", "0", str(tmp)]
    subprocess.run(args, check=True, timeout=120, capture_output=True)
    tmp.replace(out)
    return out


class KaraokeSonos:
    """The driver (module docstring): ``start()`` its thread."""

    def __init__(self, store: Store, *, output_factory: Optional[Callable[..., Any]] = None,
                 mixer: Callable[..., Path] = make_mix, url_for: Callable[[str], str] = audio_url,
                 mono: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time) -> None:
        self.store = store
        self._factory = output_factory
        self._mixer, self._url_for, self._mono, self._wall = mixer, url_for, mono, wall
        self._out: Any = None
        self._zone: Optional[str] = None
        self._placed: Optional[tuple] = None             # what is playing on the Sonos: (zone, rev, guide, child)
        self._play: Optional[tuple[int, float]] = None   # (rev, from_s) of the play the rebase lines belong to
        self.error: Optional[str] = None
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, name="karaoke-sonos", daemon=True).start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.step()
            except Exception as e:  # noqa: BLE001 - never let the driver die: say why, next time
                log.exception("karaoke sonos")
                self.error = str(e)
            self._stop.wait(POLL_S)

    def _output(self, zone: str) -> Any:
        if self._out is None or self._zone != zone:
            self._halt()
            if self._factory is not None:
                self._out = self._factory(zone, emit=self._on_event)
            else:
                from piano_render.sonos import SonosOutput
                self._out = SonosOutput(zone, emit=self._on_event)
            self._zone = zone
        return self._out

    def _halt(self) -> None:
        if self._out is not None and self._placed is not None:
            try:
                self._out.stop()
            except Exception:  # noqa: BLE001
                log.exception("karaoke sonos: stop")
        self._placed = None
        self._play = None

    def step(self) -> None:
        st = self.store
        with st.lock:
            p = dict(st.s["player"])
            settings = dict(st.s["settings"])
            song = dict(st.s["songs"].get(p.get("song_id") or "") or {})
        out = st.output()
        if not out.startswith(SONOS_PREFIX):
            self._halt()
            return
        zone = out[len(SONOS_PREFIX):]
        if p.get("state") != "playing" or not song:
            self._halt()
            return
        child = bool(settings.get("child_mode"))
        guide = float(settings.get("guide") or 0.0)
        want = (zone, p.get("rev"), song.get("id"), round(guide, 3), child)
        if want == self._placed:
            return
        self._place(zone, p, song, guide, child, want)

    def _place(self, zone: str, p: dict, song: dict, guide: float, child: bool, want: tuple) -> None:
        st = self.store
        pos = st.position()
        from_s = max(0.0, pos)
        lead = max(0.0, -pos)                            # still in the song's lead-in: the Sonos's padding
        mutes: list = []
        if child:
            from .profanity import mark
            sung = None
            try:
                raw = json.loads((song_dir(song["id"]) / "sung.json").read_text())
                sung = raw.get("words") if isinstance(raw, dict) else raw
            except (OSError, ValueError):
                pass
            mutes = mark(song.get("lyrics") or {"lines": []}, sung if isinstance(sung, list) else None)["mutes"]
        self.error = None
        mix = self._mixer(song["id"], from_s, guide, mutes)
        out = self._output(zone)
        title = str(song.get("title") or "Karaoke")
        if child:
            from .profanity import mask_text
            title = mask_text(title)
        t_audio0 = out.start(mix, lead_in_ms=lead * 1000.0, url=self._url_for(mix.stem), title=f"Karaoke: {title}")
        self._placed = want
        self._play = (int(p.get("rev") or 0), from_s)
        self._anchor(t_audio0)
        log.info("karaoke on sonos:%s: %s from %.1f s (lead %.1f s), heard in %+.0f ms", zone, song.get("id"),
                 from_s, lead, (t_audio0 - self._mono()) * 1000)

    def _anchor(self, t_audio0: float) -> None:
        """The song at the mix's start (from_s) is heard at t_audio0 (monotonic): the player's clock, same rev."""
        if self._play is None:
            return
        rev, from_s = self._play
        # As "now the song is at ...": before the mix is heard that's earlier than from_s (the lead-in's countdown
        # runs on), never a start time in the future (the clocks hold a position still until their start time).
        self.store.anchor(rev, from_s - (float(t_audio0) - self._mono()), self._wall())

    def _on_event(self, msg: dict) -> None:
        """SonosOutput's lines: a rebase moves the clock; a warning is shown."""
        if msg.get("ev") == "rebase" and msg.get("t_audio0") is not None:
            self._anchor(float(msg["t_audio0"]))
        elif msg.get("ev") == "warning":
            self.error = str(msg.get("why") or "") or None
