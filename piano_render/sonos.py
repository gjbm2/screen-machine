"""Play a rendered test-piano file on a Sonos zone through room_service, and watch it (session.py --output sonos:<zone>).

    out = SonosOutput("drawing-room", emit=print)
    t_audio0 = out.start(render_flac, lead_in_ms=750, title="Piano: Nocturne",
                         url="http://<SM_LAN_HOST>:8700/show/api/v1/piano/audio/<key>.flac")
    out.wait(); out.session.done / out.session.error
    out.stop()                  # pauses the Sonos only while it still plays our file; settle() waits for a late one

Device control goes through room_service (screen-machine-ff's; AV_PROGRAMME §11): the Sonos state is read from
``GET <ROOM_URL>/api/v1/status`` and playback is asked for with ``PUT <ROOM_URL>/api/v1/room/targets``. The player
itself is only *read* here: UPnP GetTransportInfo + GetPositionInfo on the zone's ip (config/room/sonos.json) at
WATCH_HZ, plain HTTP SOAP to UPNP_PORT (Sonos's fixed control port).

start():
  1. The room's state: the zone's volume and now_playing (state, track_id), and the volume the room stored to put back
     (status ``stored``: the "_restore" slot first, then any other); whether it is PLAYING also from UPnP (fresher).
  2. The volume: the one asked (0..100), else the current one if > 0, else the stored put-back value; else 0 and
     ``{"ev": "warning", "why": "Sonos volume is 0"}`` (never an invented level). A volume nobody knows (no reading,
     nothing stored) is left out of the PUT (the Sonos keeps its own), with a warning.
  3. The pad (silence before MIDI time 0): ``lead_in_ms`` (show_sender's --pad-s); while room_service doesn't take
     ``fade_s`` (below) at least ramp_s + FADE_PAD_EXTRA_S, so the fade-in its hand-set applies to every play
     (sonos.json "ramp_s", else RAMP_S_DEFAULT 4 s, from volume 0) covers silence only.
  4. The padded copy: ``<cache>/sonos/<pkey>.flac`` = pad of digital silence, then the render, same rate, channels and
     sample format (FLAC at compression level 0), pkey = md5("<key>:pad<ms>"): made once (blockwise, in a thread at
     nice 19), reused (and touched), the newest PADDED_KEEP kept. The URL's last segment "<key>.flac" becomes
     "<pkey>.flac" (show_service serves both: api.py ``/api/v1/piano/audio/``). A URL that doesn't name the render is
     played as it is, with no pad.
  5. ``PUT {"values": {"sonos.<zone>.playback": {"uri", "title", "fade_s": 0, "volume"}}, "source": "test piano"}``.
     Feature detection: room_service's Sonos adapter rejects keys it doesn't take with a 400 naming them ("unknown
     keys [...]"); OPTIONAL_KEYS it names are dropped for the rest of this process (UNSUPPORTED) and the PUT is sent
     again (with the fade pad when fade_s went). Any other refusal is an error.
  6. ``t_audio0 = t_put + start_ms/1000 + pad`` (+ ramp_s when fade_s is unsupported and the Sonos was playing: the
     hand-set fades that out first). t_put: just before the PUT that played. start_ms: config/room/latency.json
     ``outputs["sonos:<zone>"].start_ms`` (screen-machine-10's file, read only), else START_MS_DEFAULT (500: PUT ->
     file t=0 at the room mic, 0.499 and 0.498 s on 21 Oct, from PAUSED_PLAYBACK).
The watch (a thread per play, WATCH_HZ): our URI must be PLAYING within START_TIMEOUT_S (other URIs before it are the
old music fading out), else "Sonos didn't start". Then STOPPED near the file's end (the last RelTime, or the clock,
within END_SLACK_S of it): played; STOPPED earlier: "Sonos stopped"; PAUSED_PLAYBACK: "Sonos paused"; another TrackURI:
"Sonos is playing something else". RelTime is whole seconds: each second edge brackets the file's start between two
polls; the brackets of the last EDGES_KEPT edges are intersected (a jump or an empty intersection starts again), and
once MIN_EDGES agree, a start that differs from t_audio0 by more than REBASE_S is sent as ``{"ev": "rebase",
"t_audio0", "was"}`` (show_sender shifts its MIDI clock). A transport that can't be read for UNREADABLE_S: the end is
timed by the clock (a warning says so).
stop(): if the Sonos still plays our URI, PUT playback "paused" through room_service, once per play (room_service fades
it out over ramp_s first, and a second pause mid-fade would put back the faded volume); never otherwise. A stop during
a start that has PUT but isn't heard yet watches for our URI (until it is due plus START_GRACE_S) and pauses it then.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import unquote

log = logging.getLogger("piano_render.sonos")

REPO = Path(__file__).resolve().parent.parent
ROOM_URL = "http://127.0.0.1:8700"                 # room_service on this box (loopback is an allowed Host)
SONOS_FILE = REPO / "config" / "room" / "sonos.json"
LATENCY_FILE = REPO / "config" / "room" / "latency.json"
SOURCE = "test piano"                              # the PUT's source (room_service's log)
UPNP_PORT = 1400
AVT = ("/MediaRenderer/AVTransport/Control", "urn:schemas-upnp-org:service:AVTransport:1")
START_MS_DEFAULT = 500.0
RAMP_S_DEFAULT = 4.0                               # room_service's Sonos adapter when sonos.json has no ramp_s
FADE_PAD_EXTRA_S = 0.5
OPTIONAL_KEYS = ("fade_s", "title")                # playback keys an older room_service doesn't take
UNSUPPORTED: set = set()                           # of OPTIONAL_KEYS, refused by room_service in this process
RESTORE_SLOT = "_restore"                          # room_service's "put back afterwards" slot
PLAYING = ("PLAYING", "TRANSITIONING")
WATCH_HZ = 5.0
START_TIMEOUT_S = 30.0                             # a hand-set may wait 15 s for a running switch, then fade 4 + 4 s
START_GRACE_S = 5.0
END_SLACK_S = 2.5
REBASE_S = 0.7
MIN_EDGES = 2
EDGES_KEPT = 8
UNREADABLE_S = 5.0
HTTP_TIMEOUT_S = 10.0
UPNP_TIMEOUT_S = 2.0
PADDED_DIR = "sonos"
PADDED_KEEP = 12                                   # ~35 MB each for a typical piece
PAD_BLOCK_S = 5.0
KEY_RE = re.compile(r"^[0-9a-f]{32}$")


class SonosError(Exception):
    """Plain words for the Room page: why the Sonos can't play this."""


def _read_json(path: Path) -> dict:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
        return None
    return float(v)


def start_ms_for(zone: str, latency_file: Path = LATENCY_FILE) -> float:
    """latency.json ``outputs["sonos:<zone>"].start_ms`` (0..20000), else START_MS_DEFAULT."""
    ent = (_read_json(latency_file).get("outputs") or {}).get(f"sonos:{zone}")
    v = _num(ent.get("start_ms")) if isinstance(ent, dict) else None
    return v if v is not None and 0.0 <= v <= 20000.0 else START_MS_DEFAULT


def rel_s(text: Any) -> Optional[int]:
    """UPnP RelTime "H:MM:SS" -> whole seconds (None for NOT_IMPLEMENTED or anything else)."""
    m = re.match(r"^\s*(\d+):(\d{1,2}):(\d{1,2})(?:\.\d+)?\s*$", str(text or ""))
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) if m else None


def same_uri(a: Any, b: Any) -> bool:
    return bool(a) and bool(b) and unquote(str(a)).strip() == unquote(str(b)).strip()


def unknown_keys(detail: str) -> set:
    """The OPTIONAL_KEYS a 400's detail names as unknown ("playback has unknown keys ['fade_s', 'title']; it takes")."""
    m = re.search(r"unknown keys?\s*\[([^\]]*)\]", detail or "")
    named = set(re.findall(r"[A-Za-z_]+", m.group(1))) if m else set()
    return named & set(OPTIONAL_KEYS)


def padded_name(key: str, pad_s: float) -> str:
    return hashlib.md5(f"{key}:pad{int(round(pad_s * 1000))}".encode()).hexdigest()


def _lower_this_thread() -> None:
    try:
        os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 19)
    except (OSError, AttributeError):
        pass


def padded_copy(src: "str | os.PathLike", pad_s: float, *, keep: int = PADDED_KEEP) -> Path:
    """<src dir>/sonos/<md5(key:pad<ms>)>.flac: pad_s of digital silence, then ``src`` (module docstring, start 4)."""
    import numpy as np
    import soundfile as sf
    src = Path(src)
    d = src.parent / PADDED_DIR
    out = d / f"{padded_name(src.stem, pad_s)}.flac"
    try:
        if out.stat().st_size > 0:
            os.utime(out)
            return out
    except OSError:
        pass
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f".{out.stem}.{os.getpid()}.{threading.get_ident()}.tmp"
    err: list = []

    def work() -> None:
        _lower_this_thread()
        try:
            with sf.SoundFile(str(src)) as f:
                sr, ch = f.samplerate, f.channels
                with sf.SoundFile(str(tmp), "w", samplerate=sr, channels=ch, subtype=f.subtype, format="FLAC",
                                  compression_level=0.0) as g:
                    left = int(round(pad_s * sr))
                    block = max(1, int(PAD_BLOCK_S * sr))
                    while left > 0:
                        n = min(block, left)
                        g.write(np.zeros((n, ch), dtype=np.int32))
                        left -= n
                    for b in f.blocks(blocksize=block, dtype="int32", always_2d=True):
                        g.write(b)
            os.replace(tmp, out)
        except BaseException as e:                       # noqa: BLE001  said by the caller
            err.append(e)
            try:
                tmp.unlink()
            except OSError:
                pass

    th = threading.Thread(target=work, name="sonos-pad", daemon=True)
    th.start()
    th.join()
    if err:
        raise SonosError(f"couldn't make the padded copy of {src.name}: {err[0]}")
    _prune(d, keep, out)
    return out


def _prune(d: Path, keep: int, spare: Path) -> None:
    try:
        files = sorted((p for p in d.glob("*.flac") if p != spare), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return
    for p in files[max(0, keep - 1):]:
        try:
            p.unlink()
        except OSError:
            pass


class _Play:
    """One start(): what was played and how it went."""

    def __init__(self, url: str, pad_s: float, file_s: float, t_put: float, t_audio0: float, appear_by: float) -> None:
        self.url, self.pad_s, self.file_s, self.t_put, self.t_audio0 = url, pad_s, file_s, t_put, t_audio0
        self.appear_by = appear_by                       # when our URI should be playing at the latest (stop's watch)
        self.seen = False
        self.paused = False                              # we asked room_service to pause it (once: its fade-out
        self.halt = threading.Event()                    # still plays our file, and a second pause would restore the
                                                         # volume it had faded to)
        self.exited = threading.Event()
        self.done: Optional[dict] = None
        self.error: Optional[str] = None
        self.rebased = 0


class SonosOutput:
    """PC22Output's shape for session.py: start(path, lead_in_ms, url, title, volume) -> t_audio0; wait(); stop();
    ``session`` (the last play: ``done``, ``error``); ``pad_s`` (the pad the last start used). ``emit`` gets the
    protocol's "warning" and "rebase" lines."""

    def __init__(self, zone: str, *, emit: Optional[Callable[[dict], Any]] = None, room_url: str = ROOM_URL,
                 sonos_file: Path = SONOS_FILE, latency_file: Path = LATENCY_FILE, upnp_port: int = UPNP_PORT,
                 clock: Callable[[], float] = time.monotonic) -> None:
        cfg = _read_json(sonos_file)
        z = (cfg.get("zones") or {}).get(zone)
        if not isinstance(z, dict) or not isinstance(z.get("ip"), str) or not z["ip"].strip():
            raise SonosError(f"there's no Sonos zone “{zone}” in {Path(sonos_file).name}")
        self.zone, self.ip = zone, z["ip"].strip()
        self.room_name = z.get("room") if isinstance(z.get("room"), str) else zone
        ramp = _num(cfg.get("ramp_s"))
        self.ramp_s = ramp if ramp is not None and ramp >= 0 else RAMP_S_DEFAULT
        self.emit = emit or (lambda msg: None)
        self.room_url, self.latency_file, self.upnp_port = room_url.rstrip("/"), Path(latency_file), int(upnp_port)
        self.clock = clock
        self.target = f"sonos.{zone}.playback"
        self.pad_s = 0.0
        self._lock = threading.Lock()
        self._p: Optional[_Play] = None
        self._epoch = 0                                  # bumped by every stop(): a start in flight stops before its PUT
        self._pending: Optional[threading.Thread] = None

    # -- what the last start learnt (PC22Output's names) --
    @property
    def session(self) -> Optional[_Play]:
        return self._p

    @property
    def done(self) -> Optional[dict]:
        return self._p.done if self._p else None

    @property
    def t_audio0(self) -> Optional[float]:
        return self._p.t_audio0 if self._p else None

    # -- HTTP --
    def _http(self, method: str, path: str, body: Optional[dict] = None) -> tuple[int, dict]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.room_url + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as r:
                status, raw = r.status, r.read()
        except urllib.error.HTTPError as e:
            status, raw = e.code, e.read()
        except (urllib.error.URLError, OSError) as e:
            raise SonosError(f"room_service isn't answering ({getattr(e, 'reason', e)})") from None
        try:
            doc = json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            doc = {}
        return status, doc if isinstance(doc, dict) else {}

    def upnp(self, action: str) -> dict:
        """One read-only AVTransport query (GetTransportInfo | GetPositionInfo) -> its fields."""
        if action not in ("GetTransportInfo", "GetPositionInfo"):
            raise ValueError(action)
        path, urn = AVT
        env = (f'<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
               f's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body><u:{action} xmlns:u="{urn}">'
               f'<InstanceID>0</InstanceID></u:{action}></s:Body></s:Envelope>')
        req = urllib.request.Request(f"http://{self.ip}:{self.upnp_port}{path}", data=env.encode(), method="POST",
                                     headers={"Content-Type": 'text/xml; charset="utf-8"',
                                              "SOAPACTION": f'"{urn}#{action}"'})
        with urllib.request.urlopen(req, timeout=UPNP_TIMEOUT_S) as r:
            root = ET.fromstring(r.read())
        resp = root.find(".//{%s}%sResponse" % (urn, action))
        if resp is None:
            raise ValueError(f"no {action}Response")
        return {c.tag.split("}")[-1]: (c.text or "") for c in resp}

    def transport(self) -> Optional[tuple[str, str, Optional[int], float, float]]:
        """(state, TrackURI, RelTime s, t before, t after) from UPnP, or None when it can't be read."""
        try:
            state = self.upnp("GetTransportInfo").get("CurrentTransportState", "")
            t1 = self.clock()
            pi = self.upnp("GetPositionInfo")
            t2 = self.clock()
        except Exception as e:                           # noqa: BLE001  unreadable now
            log.debug("sonos %s: UPnP: %s", self.zone, e)
            return None
        return state, pi.get("TrackURI", ""), rel_s(pi.get("RelTime")), t1, t2

    def room_state(self) -> dict:
        """The zone as room_service believes it (start 1): volume, state, track, put_back."""
        status, doc = self._http("GET", "/api/v1/status")
        if status != 200:
            raise SonosError(f"room_service answered {status} for its status")
        sub = (doc.get("subsystems") or {}).get("sonos")
        if not isinstance(sub, dict):
            raise SonosError("room_service has no Sonos")
        believed = sub.get("believed") or {}
        now = believed.get(f"{self.zone}.now_playing")
        now = now if isinstance(now, dict) else {}
        stored = doc.get("stored") if isinstance(doc.get("stored"), dict) else {}
        put_back = None
        for slot in sorted(stored, key=lambda s: s != RESTORE_SLOT):
            e = (stored.get(slot) or {}).get(f"sonos.{self.zone}.volume")
            v = _num(e.get("value")) if isinstance(e, dict) else None
            if v is not None and 0 <= v <= 100:
                put_back = int(round(v))
                break
        vol = _num(believed.get(f"{self.zone}.volume"))
        return {"volume": None if vol is None else int(round(vol)), "state": str(now.get("state") or ""),
                "track": now.get("track_id"), "put_back": put_back, "available": sub.get("available") is not False}

    def choose_volume(self, asked: Any, state: dict) -> Optional[int]:
        """start 2: asked, else the current if > 0, else the stored put-back; else 0 (warning). None: unknown."""
        v = _num(asked)
        if v is not None:
            if not 0 <= v <= 100:
                raise SonosError(f"volume {asked!r} isn't 0..100")
            return int(round(v))
        cur = state.get("volume")
        if cur is not None and cur > 0:
            return cur
        if state.get("put_back") is not None and state["put_back"] > 0:
            return state["put_back"]
        if cur is None:
            self.emit({"ev": "warning", "why": "Sonos volume isn't known: it plays at its own"})
            return None
        self.emit({"ev": "warning", "why": "Sonos volume is 0"})
        return 0

    def _playing_now(self, state: dict) -> bool:
        t = self.transport()
        return (t is not None and t[0] in PLAYING) or state.get("state", "").upper() in PLAYING

    # -- playing --
    def start(self, path, lead_in_ms: float = 0.0, url: Optional[str] = None, title: Optional[str] = None,
              volume: Any = None) -> float:
        """Plays ``path`` (a render) on the zone from ``url``; returns t_audio0 (module docstring). SonosError."""
        if not url:
            raise SonosError("no URL to play")
        with self._lock:
            epoch = self._epoch
            old = self._p
        if old is not None:
            old.halt.set()
        state = self.room_state()
        if not state["available"]:
            raise SonosError(f"room_service can't reach the {self.room_name} Sonos now")
        vol = self.choose_volume(volume, state)
        was_playing = self._playing_now(state)
        src = Path(path)
        file_s = self._duration(src)
        while True:
            fade = "fade_s" not in UNSUPPORTED
            pad = max(0.0, float(lead_in_ms) / 1000.0)
            if not fade:
                pad = max(pad, self.ramp_s + FADE_PAD_EXTRA_S)
            play_url, pad = self._padded_url(src, url, pad)
            value: dict = {"uri": play_url}
            if title and "title" not in UNSUPPORTED:
                value["title"] = str(title)
            if fade:
                value["fade_s"] = 0
            if vol is not None:
                value["volume"] = vol
            with self._lock:
                if epoch != self._epoch:
                    raise SonosError("stopped before it played")
            t_put = self.clock()
            status, doc = self._http("PUT", "/api/v1/room/targets", {"values": {self.target: value}, "source": SOURCE})
            if status in (200, 202):
                break
            detail = str(doc.get("detail") or doc.get("error") or "")
            drop = unknown_keys(detail) & set(value) if status == 400 else set()
            if not drop:
                raise SonosError(f"room_service refused the play ({status}: {detail or 'no detail'})")
            UNSUPPORTED.update(drop)
            log.info("sonos %s: room_service doesn't take %s yet: playing without", self.zone, sorted(drop))
        fade_out = self.ramp_s if (not fade and was_playing) else 0.0
        t_audio0 = t_put + start_ms_for(self.zone, self.latency_file) / 1000.0 + pad + fade_out
        appear_by = t_put + fade_out + START_GRACE_S
        p = _Play(play_url, pad, pad + file_s, t_put, t_audio0, appear_by)
        with self._lock:
            self._p, self.pad_s = p, pad
        threading.Thread(target=self._watch, args=(p,), name="sonos-watch", daemon=True).start()
        log.info("sonos %s: %s at volume %s, pad %.2f s%s; t_audio0 in %+.0f ms", self.zone, play_url,
                 vol, pad, f", after a {fade_out:.0f} s fade-out" if fade_out else "",
                 (t_audio0 - self.clock()) * 1000)
        return t_audio0

    @staticmethod
    def _duration(src: Path) -> float:
        try:
            import soundfile as sf
            i = sf.info(str(src))
            return float(i.frames) / float(i.samplerate)
        except Exception:                                # noqa: BLE001  the watch then has no file end to go by
            return 0.0

    @staticmethod
    def _padded_url(src: Path, url: str, pad: float) -> tuple[str, float]:
        """The padded copy's URL and the pad (start 4); the URL as it is and no pad when it doesn't name ``src``."""
        head, sep, last = url.rpartition("/")
        if pad <= 0 or not sep or last != f"{src.stem}.flac" or not KEY_RE.match(src.stem):
            if pad > 0:
                log.warning("sonos: %s doesn't name %s: played as it is, with no pad", url, src.name)
            return url, 0.0
        return f"{head}/{padded_copy(src, pad).name}", pad

    def _watch(self, p: _Play) -> None:
        period = 1.0 / WATCH_HZ
        last_ok = self.clock()
        prev: Optional[tuple[int, float]] = None          # (RelTime s, t before the last poll, which read it)
        edges: list[tuple[float, float]] = []             # brackets of the file's start, (lo, hi)
        last_rel: Optional[int] = None
        warned = False
        try:
            while not p.halt.wait(period):
                now = self.clock()
                t = self.transport()
                if t is None:
                    if now - last_ok >= UNREADABLE_S:
                        if not warned:
                            warned = True
                            self.emit({"ev": "warning", "why": "Sonos isn't answering: its end is timed by the clock"})
                        if now >= p.t_audio0 - p.pad_s + p.file_s + END_SLACK_S:
                            p.done = {"by": "clock", "rel_s": last_rel, "rebased": p.rebased}
                            return
                    continue
                last_ok = now
                state, uri, rel, t1, t2 = t
                ours = same_uri(uri, p.url)
                if not p.seen:
                    if ours and state in PLAYING:
                        p.seen = True
                    elif now > p.t_put + START_TIMEOUT_S:
                        p.error = "Sonos didn't start"
                        return
                    else:
                        continue
                if not ours:
                    p.error = "Sonos is playing something else"
                    return
                if state in PLAYING:
                    if rel is not None:
                        if prev is not None and rel == prev[0] + 1:     # the second ticked between the two polls
                            edges = self._edge(p, edges, (prev[1] - rel, t2 - rel))
                        elif prev is not None and rel != prev[0]:
                            edges = []                    # a seek, or a missed second: start the brackets again
                        prev, last_rel = (rel, t1), rel
                    continue
                end = p.t_audio0 - p.pad_s + p.file_s
                near_end = (p.file_s > 0 and ((last_rel is not None and last_rel >= p.file_s - END_SLACK_S)
                                              or now >= end - END_SLACK_S))
                if state == "STOPPED" and near_end:
                    p.done = {"by": "sonos", "rel_s": last_rel, "rebased": p.rebased}
                    return
                p.error = "Sonos paused" if state == "PAUSED_PLAYBACK" else "Sonos stopped"
                return
        finally:
            p.exited.set()

    def _edge(self, p: _Play, edges: list, bracket: tuple[float, float]) -> list:
        """One second edge: the file started within ``bracket``; intersect it with the last ones, rebase if needed."""
        edges = (edges + [bracket])[-EDGES_KEPT:]
        lo, hi = max(e[0] for e in edges), min(e[1] for e in edges)
        if lo > hi:                                      # they disagree (a stall or a seek): start again from this one
            edges, (lo, hi) = [bracket], bracket
        if len(edges) >= MIN_EDGES:
            est = (lo + hi) / 2.0 + p.pad_s              # MIDI time 0: the file's start plus the pad
            if abs(est - p.t_audio0) > REBASE_S:
                was, p.t_audio0 = p.t_audio0, est
                p.rebased += 1
                log.info("sonos %s: its position puts MIDI time 0 %+.0f ms from the estimate: rebased", self.zone,
                         (est - was) * 1000)
                self.emit({"ev": "rebase", "t_audio0": est, "was": was})
        return edges

    def wait(self, timeout: Optional[float] = None) -> bool:
        p = self._p
        return True if p is None else p.exited.wait(timeout)

    # -- stopping --
    def ours_playing(self, url: str) -> bool:
        """The Sonos plays ``url`` now (UPnP, else room_service's now_playing); False when neither can tell."""
        t = self.transport()
        if t is not None:
            return t[0] in PLAYING and same_uri(t[1], url)
        try:
            st = self.room_state()
        except SonosError:
            return False
        return st["state"].upper() in PLAYING and same_uri(st["track"], url)

    def _pause(self, p: _Play) -> None:
        with self._lock:
            if p.paused:
                return
            p.paused = True
        try:
            status, doc = self._http("PUT", "/api/v1/room/targets", {"values": {self.target: "paused"},
                                                                     "source": SOURCE})
            if status not in (200, 202):
                log.warning("sonos %s: room_service refused the pause (%s: %s)", self.zone, status, doc.get("detail"))
            else:
                log.info("sonos %s: paused (it was still playing our file)", self.zone)
        except SonosError as e:
            log.warning("sonos %s: couldn't pause: %s", self.zone, e)

    def stop(self, timeout: float = 1.0) -> None:
        """Our file off the Sonos, if it still plays it (module docstring); safe to call any time."""
        with self._lock:
            self._epoch += 1
            p = self._p
        if p is None:
            return
        p.halt.set()
        if p.exited.is_set() and (p.done is not None or p.error is not None):
            return                                       # it ended, or left our file, by itself: nothing of ours plays
        if p.paused:
            return
        if self.ours_playing(p.url):
            self._pause(p)
        elif not p.seen and self.clock() < p.appear_by:
            th = threading.Thread(target=self._pause_when_heard, args=(p,), name="sonos-late-pause", daemon=True)
            self._pending = th
            th.start()

    def _pause_when_heard(self, p: _Play) -> None:
        while self.clock() < p.appear_by:
            t = self.transport()
            if t is not None and same_uri(t[1], p.url) and t[0] in PLAYING:
                self._pause(p)
                return
            time.sleep(1.0 / WATCH_HZ)

    def settle(self, timeout: float = 2.0) -> None:
        """At exit: wait (at most ``timeout``) for a pause that waits for our file to be heard."""
        th = self._pending
        if th is not None:
            th.join(timeout)

    def close(self) -> None:
        self.stop()
