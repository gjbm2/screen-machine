"""artnet_out: the virtual Maestro (P0, docs/SHOW_ENGINE.md §0.1, §9.4, §9.5, §13.2).

show_service sends one ArtDmx universe, in exactly MaestroDMX's channel layout (config/room/patch.yaml), to
room_service's own Art-Net receiver. room_service decodes it as it would Maestro, so the renderer's BeatFollower,
music_direction.py and the Hue "Follow" streamer follow the room's real music with no change on their side.

**Off by default.** Nothing is sent until a target is configured (``SHOW_ARTNET_TARGET``; production
``127.0.0.1:6454``, room's receiver). Tests send to an ephemeral local UDP socket, never 6454.

Thread ``show-artnet``: a deadline loop, at most 44 Hz (Art-Net 4's limit; nodes drop excess frames). It reads the
beat clock and the feature snapshot **by reference** each frame and never locks, waits on or calls into show-rx.
Thread ``show-artnet-poll``: the yield check every 5 s and patch.yaml's mtime every 2 s, so no HTTP or file I/O ever
sits on the deadline loop.

Per frame (send time ``t``)::

  plan(t_last)  the next send time. Frames are phase-locked to the beat (§5.4): tick = P / m, m the largest multiple
                of 4 keeping the rate <= GRID_HZ, 43 Hz (120 bpm -> 40 Hz, 128 bpm -> 42.7 Hz), on a grid that runs
                through the next predicted beat, so **a frame leaves exactly at each beat** and on every 16th. Never
                closer than MIN_GAP_S (22.7 ms, Art-Net's 44 Hz) to when the last frame actually left: ``t_last`` is
                max(its due time, its send time), and GRID_HZ's 0.5 ms of slack keeps a frame that left a little late
                from pushing the next grid point; a grid point up to GRID_TOLERANCE_S (1 ms) inside the gap goes at
                last + MIN_GAP_S instead of a tick later. If the clock re-phases a beat inside that gap, its edge
                frame goes at last + MIN_GAP_S (late by less than one frame, never early). No beat: free-run at 44
                Hz.
  compose(t)    the 512 channels, per device exactly as its patch.yaml profile lays them out:

  screen    RED GREEN BLUE  the palette at t (palette_fn; without one, the fixed P0 colour: SHOW_PALETTE, else
                            DEFAULT_RGB warm amber)
            DIMMER  pulse   0 between beats. At each predicted acoustic beat b (clock.next_beat) it rises in the
                            frame sent at b: amp * exp(-(t - b) / 60 ms) for t - b < min(P / 2, 250 ms), then 0, so
                            BeatFollower's Schmitt trigger (rise 0.6, fall 0.3 of its peak) sees one edge per beat.
                            amp = 0.10 + 0.60 x features.punch, so music_direction's punch (clamp((pulse_peak - 0.10)
                            / 0.60)) reads ours back. The renderer's lead does the rest (maestro_ms stays 0).
            ACTIVITY        features.energy (loudness x gate, §6.3)
            PAN TILT        2 x smoothed brightness - 1, 2 x smoothed bass level - 1 (tau 1.5 s; a frame never moves
                            them near inputs.py's 0.25 "teleport" step)
            ZOOM            onset density: band hits per second (the sum over bands) / 8, smoothed tau 2 s
            GOBO   effect   a step counter (raw, mod 256), +1 on each section_change()
            PRISM  variant  bar parity, like show_synth, set at each beat edge in the clock's own numbering:
                            ((k - bar_anchor_beat) // beats_per_bar) % 2, k the beat's number from clock.snapshot()
                            (its music.clock body), so it flips on the clock's downbeats. A clock without snapshot():
                            our own edge count // beats_per_bar (default 4)
  hue       MASTER DIMMER   255
            RED GREEN BLUE  scheduled at t - L_hue: the frame sent at t carries the look for th = t + L_hue: the
                            palette at th x (1 - depth + depth x env(th)); env = a soft beat (attack 80 ms up to the
                            beat, decay tau 250 ms; §5.4: no hits on Hue) while the clock predicts beats, else the
                            smoothed energy. depth 0.7 (the room's test show's 0.3 floor); 0 = the palette alone.
                            L_hue is 0 until calibrated (P6) and settable from any thread.
  other     every channel at its patch ``default`` (0 unless given)

Encoding is room_service/dmx/patch.py's: a field with ``range: [lo, hi]`` -> round(255 (v - lo) / (hi - lo)), a
``raw`` field -> the integer, clamped to 0..255. A value that isn't a finite number (a NaN from upstream) is sent as
the channel's default; the inputs' clamps read NaN as 0, so it never sticks in a smoother.

**Idle.** While the director is neutral (``palette_fn`` gives no dict), nothing is sent, room's input goes stale and
holds (as before show_service; its own test show can run, and after 10 s room's Hue output hands the lamps back to the
house) when:
  * there is no fresh feature snapshot (t_ms older than ``idle_after_s``, 5 s): ``idle_reason`` "no_features";
  * the room is quiet: ``features.gate`` under QUIET_GATE_OFF (0.02) for ``quiet_after_s`` (10 s; None = never),
    until it is back over QUIET_GATE_ON (0.05): ``idle_reason`` "quiet". The mic streams in silence too, so
    without this a silent room would hold the lamps at the hue floor (1 - depth = 30 %) of the P0 colour.
While the director plays a vibe (``palette_fn`` returns a dict), neither idles: its colours keep going out (its relaxed
base look, Lights low, House lights; §8.5 "never black") with hue_depth 0, no DIMMER pulses and no beat edges, and the
features are not followed (``status()["held"]``: the reason).

**Yield** (§9.4, as fake_artnet yields to Maestro). Every 5 s ``GET http://127.0.0.1:8700/api/v1/dmx`` (its
``input``: last_from, age_ms, source), and pause (log it, ``yield.from``) when the input is fresh (``age_ms`` <=
1500, room's stale_after_ms) and ``input.last_from`` is not one of our source addresses (127.0.0.1 or the socket's
own). There is no packet-rate test (09's audit F4, 21 Oct: "over our own rate + 8" tripped on our own beat-edge
frames, 38 times from 127.0.0.1, each stopping the lamps for 5 s; Maestro is coming out).
While paused we send nothing, so any fresh real Art-Net (``source`` "artnet", any address) keeps us paused; the first
poll that shows the input stale (or only room's own test show) resumes. A failed poll changes nothing (degrade,
never refuse). A failing frame is counted (``frame_errors``) and logged once, then at most every 30 s.

**Wire.** ArtDmx, copied from room_service/dmx/artnet.py (§9.3: copied, not imported), with the sequence 1..255
(0 means "sequencing off"): ``Art-Net\\0``, OpCode 0x5000 LE, ProtVer 14 (hi 0, lo 14), Sequence, Physical 0,
SubUni = PA & 0xFF, Net = (PA >> 8) & 0x7F, Length BE (even, 2..512), data. Port-Address = patch.yaml
``artnet.universe`` (default 0, room's). Always the full 512 channels: room's receiver keeps channels a short frame
doesn't carry. Unicast only. The packet buffer is preallocated and filled in place.

Inputs (all optional; each is called from show-artnet and must be safe there):
  clock        ``clock.next_beat(t_s) -> float | None``: the first predicted acoustic beat at or after host time
               t_s (CLOCK_MONOTONIC s), or None while nothing is beat-locked (§5.3's gate: then no pulses). It must
               compute from a state swapped by reference, never a lock. Optional ``clock.snapshot()`` (the
               music.clock body, for PRISM's bar parity) and ``clock.beats_per_bar`` (the fallback's bar).
  features_fn  ``() -> contract.FeaturesData | None``: dsp.Features.latest (the snapshot, by reference).
  palette_fn   ``(t_s) -> (r, g, b) | {key: (r, g, b)} | None``, 0..1: one colour for all, or keyed by device id,
               then the device's ``screen:`` (north-screen), then ``"*"``. Missing, or None (Stage 3: __main__ passes
               Director.palette_fn, which is None while no vibe is set): ``palette`` (the constructor's fixed colour:
               config_from_env's SHOW_PALETTE, a PALETTES name or r,g,b), else DEFAULT_RGB (amber). ``status()``
               says which one the last frame used (``palette``: "fixed" | "director").
A callback that raises is counted and treated as None; it never stops the loop.

**The director's look** (Stage 3, README "Stage 3 > the artnet seam"): ``set_look(hue_depth=, beat_lock=)`` from any
thread, each value swapped by reference and read once per frame. ``hue_depth`` (0..1) replaces the constructor's.
``beat_lock`` False: no DIMMER pulse edges, no PRISM flips, and the hue swell follows the energy only; the clock stays
wired (the frame grid keeps its phase). ``reset=True`` puts both back to the constructor's (the director gone neutral).

**The composer's plan** (README S5.18, show.yaml director.compose.mode drive only): ``set_look(plan={"depth",
"middle_depth", "lamps"})``; ``plan={}`` (and ``reset``) clears it, and without one every frame is exactly as above.
With one, the hue lamps run the plan on this output's own beat ladder, read from its own clock each frame: a beat
(the clock at gate beat) swells at ``depth`` (the director's accent depth before the gate; None: ``hue_depth``); else
the clock's dmx-gate beats (``clock.next_beat(t, gate="dmx")``) swell at depth x ``middle_depth`` (show.yaml
looks.middle_depth); else none, and the no-beat swell follows the lamp's planned ``level`` instead of the raw energy
(the second loudness path, gone). Each lamp's depth is also x its planned ``pulse``. ``lamps``: ``{device id | "*":
(level, pulse)}``. Screens (DIMMER pulses, PRISM) never read the plan.
"""

from __future__ import annotations

import json
import logging
import math
import os
import pathlib
import socket
import threading
import time
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence

import numpy as np

from .contract import REPO_ROOT

log = logging.getLogger("show_service.artnet_out")

# -- the wire (copied from room_service/dmx/artnet.py; §9.3) ----------------------------------------------------------
ARTNET_ID = b"Art-Net\x00"
OP_DMX = 0x5000
PROT_VER = 14
HEADER = 18
UNIVERSE_SIZE = 512
PORT = 6454
_ZEROS = bytes(UNIVERSE_SIZE)

# -- timing (§5.4) ----------------------------------------------------------------------------------------------------
MAX_HZ = 44.0
MIN_GAP_S = 1.0 / MAX_HZ            # 22.7 ms
GRID_HZ = 43.0                      # the beat grid's tick is chosen for <= 43 Hz: >= 0.5 ms over MIN_GAP_S, so a frame
                                    # that leaves a little late doesn't push the next grid point inside the gap
REPHASE_SKIP = 0.35                 # a beat this close (x P) after the last edge is that edge re-phased (the
                                    # follower's own refractory): never a second edge
LATE_S = 0.005                      # woke this late: re-plan from now instead of bursting to catch up
GRID_TOLERANCE_S = 0.001            # a grid point this little inside the gap (the last frame left late) goes at the
                                    # gap's end instead of a whole tick on (README S5.14)
BUILD_AHEAD_S = 0.001               # compose this long before the deadline, send at it

# -- channel behaviour (module docstring) -----------------------------------------------------------------------------
PULSE_TAU_S = 0.06
PULSE_MAX_S = 0.25
PULSE_LO, PULSE_SPAN = 0.10, 0.60   # amp = PULSE_LO + PULSE_SPAN x punch: music_direction's PEAK_LO / PEAK_HI
TAU_MOVE_S = 1.5                    # PAN and TILT
TAU_ZOOM_S = 2.0
ONSET_FULL_HZ = 8.0                 # band hits per second that read as ZOOM 1
HUE_ATTACK_S = 0.08                 # §5.4: Hue beat pulses have attack >= 80 ms
HUE_DECAY_S = 0.25
HUE_DEPTH = 0.7
TAU_ENERGY_S = 1.0                  # the hue envelope without a beat
DEFAULT_RGB = (1.0, 0.62, 0.32)     # warm amber, until the director's palette (P2); SHOW_PALETTE overrides it
try:                                # an instance's neutral (75, 17 Oct): warm white at the house CCT, not amber
    from .palette import white as _white
    NEUTRAL_RGB: tuple[float, float, float] = tuple(float(c) for c in _white(2700.0))[:3]  # type: ignore[assignment]
except Exception:                   # noqa: BLE001 - standalone: the constant
    NEUTRAL_RGB = DEFAULT_RGB
PALETTES: dict[str, tuple[float, float, float]] = {     # SHOW_PALETTE names (or "r,g,b")
    "amber": DEFAULT_RGB, "warm": (1.0, 0.78, 0.55), "white": (1.0, 1.0, 1.0), "cool": (0.55, 0.70, 1.0),
    "red": (1.0, 0.12, 0.06), "blue": (0.15, 0.30, 1.0), "green": (0.20, 1.0, 0.35), "violet": (0.62, 0.25, 1.0)}
QUIET_GATE_OFF = 0.02               # features.gate below this for QUIET_AFTER_S: a silent room, send nothing
QUIET_GATE_ON = 0.05                # ... until it is back above this
QUIET_AFTER_S = 10.0
FRAME_LOG_S = 30.0                  # a failing frame is logged once, then at most this often

# -- config -----------------------------------------------------------------------------------------------------------
PATCH_FILE = REPO_ROOT / "config" / "room" / "patch.yaml"
ROOM_DMX_URL = "http://127.0.0.1:8700/api/v1/dmx"
POLL_S = 5.0
PATCH_CHECK_S = 2.0
FOREIGN_FRESH_MS = 1500.0           # room's artnet.stale_after_ms
IDLE_AFTER_S = 5.0


def build_artdmx(port_address: int, data: bytes | bytearray | memoryview, seq: int) -> bytes:
    """One ArtDmx packet. ``seq`` 1..255 (0 = sequencing off: we never send it). Data is cut to 512 and padded to an
    even length of at least 2."""
    if not 1 <= seq <= 255:
        raise ValueError("seq must be 1..255")
    if not 0 <= port_address < 1 << 15:
        raise ValueError("port_address must be 0..32767")
    data = bytes(data[:UNIVERSE_SIZE])
    if len(data) % 2:
        data += b"\x00"
    if not data:
        data = b"\x00\x00"
    return (ARTNET_ID + OP_DMX.to_bytes(2, "little") + bytes([0, PROT_VER, seq, 0, port_address & 0xFF,
                                                              (port_address >> 8) & 0x7F])
            + len(data).to_bytes(2, "big") + data)


def next_seq(seq: int) -> int:
    """The sequence after ``seq``: 1, 2, .. 255, 1, .. (0 -> 1)."""
    return seq % 255 + 1


class ArtDmxFrame:
    """A preallocated 512-channel ArtDmx packet, filled in place: ``data`` is a writable view of the channels."""

    def __init__(self, port_address: int = 0) -> None:
        self.buf = bytearray(build_artdmx(port_address, bytes(UNIVERSE_SIZE), 1))
        self.data = memoryview(self.buf)[HEADER:]
        self.seq = 0
        self.set_port_address(port_address)

    def set_port_address(self, port_address: int) -> None:
        if not 0 <= port_address < 1 << 15:
            raise ValueError("port_address must be 0..32767")
        self.buf[14], self.buf[15] = port_address & 0xFF, (port_address >> 8) & 0x7F

    def advance(self) -> bytearray:
        """Stamp the next sequence number and return the packet buffer (sent as is, not copied)."""
        self.seq = next_seq(self.seq)
        self.buf[12] = self.seq
        return self.buf


# -- the patch (config/room/patch.yaml; room_service/dmx/patch.py's format, read-only for us) ---------------------------
class LayoutError(ValueError):
    pass


@dataclass(frozen=True)
class Channel:
    index: int                      # 0-based in the universe
    attr: str                       # Maestro's attribute (RED, DIMMER, ...)
    field: str                      # r, pulse, global.activity, ...
    lo: float = 0.0
    hi: float = 1.0
    raw: bool = False
    default: int = 0


@dataclass(frozen=True)
class Device:
    id: str
    kind: str                       # screen | hue | other
    address: int                    # 1-based start channel
    channels: tuple[Channel, ...]
    screen: Optional[str] = None


@dataclass(frozen=True)
class Layout:
    universe: int                   # the 15-bit Port-Address
    devices: tuple[Device, ...]     # by address


def parse_layout(raw: Any) -> Layout:
    """patch.yaml's mapping -> Layout. LayoutError on anything room_service would also refuse (or that won't fit)."""
    if not isinstance(raw, dict):
        raise LayoutError("patch: expected a mapping")
    universe = int((raw.get("artnet") or {}).get("universe", 0))
    if not 0 <= universe < 1 << 15:
        raise LayoutError("artnet.universe: 0..32767")
    profiles: dict[str, list[dict]] = {}
    for name, p in (raw.get("profiles") or {}).items():
        chans = p.get("channels") if isinstance(p, dict) else None
        if not isinstance(chans, list) or not chans or not all(isinstance(c, dict) and c.get("field") for c in chans):
            raise LayoutError(f"profile {name}: needs a channels list, each with a field")
        profiles[str(name)] = chans
    devices = []
    for did, d in (raw.get("devices") or {}).items():
        if not isinstance(d, dict) or d.get("profile") not in profiles:
            raise LayoutError(f"device {did}: needs a known profile")
        address = int(d.get("address", 0))
        chans = profiles[d["profile"]]
        if address < 1 or address + len(chans) - 1 > UNIVERSE_SIZE:
            raise LayoutError(f"device {did}: address {address} + {len(chans)} channels doesn't fit 1..512")
        kind = str(d.get("kind", str(did).split(".", 1)[0]))
        out = []
        for i, c in enumerate(chans):
            lo, hi = (float(x) for x in c.get("range", (0.0, 1.0)))
            if hi == lo:
                raise LayoutError(f"device {did} channel {i + 1}: empty range")
            out.append(Channel(index=address - 1 + i, attr=str(c.get("attr", "")), field=str(c["field"]), lo=lo,
                               hi=hi, raw=bool(c.get("raw", False)), default=int(c.get("default", 0))))
        devices.append(Device(id=str(did), kind=kind if kind in ("screen", "hue") else "other", address=address,
                              channels=tuple(out), screen=d.get("screen")))
    return Layout(universe=universe, devices=tuple(sorted(devices, key=lambda x: x.address)))


def load_layout(path: pathlib.Path | str) -> Layout:
    import yaml                                     # PyYAML; only the poll thread and start() load files
    with open(path, encoding="utf-8") as f:
        return parse_layout(yaml.safe_load(f))


def encode_into(data, device: Device, values: Mapping[str, Any]) -> None:
    """Write one device's channels into ``data`` (a 512-byte buffer). Fields absent from ``values``, and values that
    are not finite numbers (a NaN from upstream), get the channel's default; ``rgb`` expands to r, g, b."""
    rgb = values.get("rgb")
    for ch in device.channels:
        f = ch.field
        v = rgb[("r", "g", "b").index(f)] if rgb is not None and f in ("r", "g", "b") else values.get(f)
        fv = float(v) if v is not None else math.nan
        if not math.isfinite(fv):
            x = ch.default
        elif ch.raw:
            x = int(fv)
        else:
            x = round(255.0 * (fv - ch.lo) / (ch.hi - ch.lo))
        data[ch.index] = 0 if x < 0 else 255 if x > 255 else x


# -- the frame grid (§5.4) --------------------------------------------------------------------------------------------
def grid_tick(period_s: float, max_hz: float = GRID_HZ) -> float:
    """period / m, m the largest multiple of 4 that keeps the rate <= max_hz (GRID_HZ, 43: 0.5 ms of slack over
    Art-Net's 44 Hz for a frame that leaves late). Beats faster than 4 frames a beat: the largest m that fits."""
    n = period_s * max_hz
    m = 4 * math.floor(n / 4.0 + 1e-9)
    if m < 4:
        m = max(1, math.floor(n + 1e-9))
    return period_s / m


def foreign_sender(status: Any, ours: set[str] | frozenset[str], fresh_ms: float = FOREIGN_FRESH_MS, *,
                   yielded: bool = False) -> Optional[str]:
    """room's GET /api/v1/dmx -> who else is live on the universe (an address), else None.
    The input must be fresh (``age_ms`` <= fresh_ms), and then:
      * ``last_from`` is not one of ``ours``; or
      * ``yielded`` (we aren't sending): any fresh real Art-Net (``source`` "artnet") is someone else's, whatever
        its address. room's own test show ("fake") and API injections don't count.
    No packet-rate test (09's audit F4): ``input.pps`` over our own rate tripped on our own beat-edge frames."""
    if not isinstance(status, dict) or status.get("enabled") is False:
        return None
    inp = status.get("input")
    if not isinstance(inp, dict):
        return None
    age = inp.get("age_ms")
    fresh = age <= fresh_ms if isinstance(age, (int, float)) else inp.get("online") is True
    if not fresh:
        return None
    frm = inp.get("last_from")
    frm = frm if isinstance(frm, str) and frm else None
    if frm is not None and frm not in ours:
        return frm
    if yielded:
        return (frm or "?") if inp.get("source") == "artnet" else None
    return None


def parse_palette(spec: Optional[str]) -> Optional[tuple[float, float, float]]:
    """SHOW_PALETTE: a name in PALETTES, or ``"r,g,b"`` (0..1, or 0..255 when any is over 1); ``None``/``""`` -> None
    (DEFAULT_RGB). ValueError on anything else."""
    if spec is None or not spec.strip():
        return None
    s = spec.strip().lower()
    if s in PALETTES:
        return PALETTES[s]
    parts = [p for p in s.replace(";", ",").replace(" ", ",").split(",") if p]
    if len(parts) != 3:
        raise ValueError(f"SHOW_PALETTE {spec!r}: a name ({', '.join(PALETTES)}) or r,g,b")
    rgb = [float(p) for p in parts]
    if not all(math.isfinite(c) and c >= 0.0 for c in rgb):
        raise ValueError(f"SHOW_PALETTE {spec!r}: r,g,b must be >= 0")
    if max(rgb) > 1.0:
        rgb = [c / 255.0 for c in rgb]
    return tuple(min(1.0, c) for c in rgb)              # type: ignore[return-value]


def parse_target(spec: Optional[str]) -> Optional[tuple[str, int]]:
    """``"127.0.0.1:6454"`` | ``"host"`` (port 6454) -> (host, port); ``None``, ``""``, ``"off"`` -> None (off)."""
    if spec is None or spec.strip().lower() in ("", "off", "none", "0", "false"):
        return None
    host, sep, port = spec.strip().rpartition(":")
    if not sep:
        return spec.strip(), PORT
    return host, int(port)


@dataclass(frozen=True)
class OutConfig:
    """What VirtualMaestro is started with; config_from_env reads it (``__main__`` passes ``**vars(cfg)``)."""
    target: Optional[tuple[str, int]] = None          # None = off (the default)
    patch_path: pathlib.Path = PATCH_FILE
    hue_latency_ms: float = 0.0
    room_dmx_url: Optional[str] = ROOM_DMX_URL        # None = no yield check
    palette: Optional[tuple[float, float, float]] = None   # SHOW_PALETTE; None = DEFAULT_RGB


def config_from_env(env: Mapping[str, str] | None = None) -> OutConfig:
    """``SHOW_ARTNET_TARGET`` (host:port; empty/off = off), ``SHOW_PATCH_FILE``, ``SHOW_HUE_LATENCY_MS``,
    ``SHOW_ROOM_DMX_URL`` (empty = no yield check), ``SHOW_PALETTE`` (a PALETTES name or r,g,b; a bad value is
    logged and the default used)."""
    env = os.environ if env is None else env
    url = env.get("SHOW_ROOM_DMX_URL", ROOM_DMX_URL)
    try:
        palette = parse_palette(env.get("SHOW_PALETTE"))
    except ValueError as e:
        log.error("Art-Net out: %s: using %s", e, DEFAULT_RGB)
        palette = None
    return OutConfig(target=parse_target(env.get("SHOW_ARTNET_TARGET")),
                     patch_path=pathlib.Path(env.get("SHOW_PATCH_FILE") or PATCH_FILE),
                     hue_latency_ms=float(env.get("SHOW_HUE_LATENCY_MS") or 0.0),
                     room_dmx_url=url or None, palette=palette)


class BeatClock(Protocol):
    def next_beat(self, t_s: float) -> Optional[float]: ...


def _smoothstep(x: float) -> float:
    x = 0.0 if x < 0.0 else 1.0 if x > 1.0 else x
    return x * x * (3.0 - 2.0 * x)


def _c01(x: float) -> float:
    """Clamp to 0..1; NaN reads 0 (so it never reaches an EMA's state)."""
    return 0.0 if not x >= 0.0 else 1.0 if x > 1.0 else x


def _flush(x: float) -> float:
    """An EMA's state, denormals flushed to 0 (they are ~100x slower on x86)."""
    return x if abs(x) > 1e-12 else 0.0


def _http_json(url: str, timeout: float = 2.0) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def _clean_plan(plan: Any) -> Optional[dict]:
    """set_look's ``plan`` -> {"depth": float | None, "middle_depth": float, "lamps": {key: (level, pulse)}}, each
    number clamped to 0..1, junk dropped; None (no plan) when it is empty or nothing in it is usable."""
    if not isinstance(plan, Mapping) or not plan:
        return None

    def unit(v: Any) -> Optional[float]:
        try:
            x = float(v)
        except (TypeError, ValueError):
            return None
        return _c01(x) if math.isfinite(x) else None
    lamps: dict[str, tuple[float, float]] = {}
    raw = plan.get("lamps")
    for key, v in (raw.items() if isinstance(raw, Mapping) else ()):
        if isinstance(v, (list, tuple)) and len(v) == 2:
            lv, pu = unit(v[0]), unit(v[1])
            if lv is not None and pu is not None:
                lamps[str(key)] = (lv, pu)
    md = unit(plan.get("middle_depth"))
    if not lamps and md is None:
        return None
    return {"depth": unit(plan.get("depth")), "middle_depth": md if md is not None else 0.0, "lamps": lamps}


def _plan_view(plan: Optional[dict]) -> Optional[dict]:
    if plan is None:
        return None
    return {"depth": plan["depth"], "middle_depth": plan["middle_depth"],
            "lamps": {k: [round(x, 3) for x in v] for k, v in plan["lamps"].items()}}


class VirtualMaestro:
    """The P0 sender. ``start()`` / ``stop()``; ``plan`` and ``compose`` are the loop's steps (show-artnet; tests call
    them with fake times). ``section_change``, ``set_hue_latency_ms``, ``status`` are safe from any thread."""

    def __init__(self, *, target: Optional[tuple[str, int]] = None, patch_path: pathlib.Path | str = PATCH_FILE,
                 clock: Optional[BeatClock] = None,
                 features_fn: Optional[Callable[[], Optional[Mapping[str, Any]]]] = None,
                 palette_fn: Optional[Callable[[float], Any]] = None, hue_latency_ms: float = 0.0,
                 hue_depth: float = HUE_DEPTH, room_dmx_url: Optional[str] = ROOM_DMX_URL,
                 poll_fn: Optional[Callable[[], Any]] = None, poll_s: float = POLL_S,
                 idle_after_s: Optional[float] = IDLE_AFTER_S, layout: Optional[Layout] = None,
                 time_fn: Callable[[], float] = time.monotonic,
                 palette: Optional[Sequence[float]] = None, quiet_after_s: Optional[float] = QUIET_AFTER_S) -> None:
        self.target = target
        self.patch_path = pathlib.Path(patch_path)
        self.clock = clock
        self.features_fn = features_fn
        self.palette_fn = palette_fn
        self.palette_used = "fixed"                  # the last frame's screen colours: "fixed" | "director"
        self.default_rgb: tuple[float, float, float] = (tuple(float(c) for c in palette)  # type: ignore[assignment]
                                                        if palette is not None and len(palette) == 3 else NEUTRAL_RGB)
        self.quiet_after_s = quiet_after_s
        self.hue_latency_s = hue_latency_ms / 1000.0
        self.hue_depth = hue_depth
        self._hue_depth0 = hue_depth
        self.beat_lock = True                        # set_look: False = nothing beat-locked (pulses, PRISM, swell)
        self.lamp_plan: Optional[dict] = None        # set_look(plan=): the composer's lamps plan (drive mode, S5.18)
        self._edge_locked = True                     # the last edge happened while beat-locked (else no pulse for it)
        self.poll_fn = poll_fn if poll_fn is not None else (
            (lambda: _http_json(room_dmx_url)) if room_dmx_url else None)
        self.room_dmx_url = room_dmx_url if poll_fn is None else "(injected)"
        self.poll_s = poll_s
        self.idle_after_s = idle_after_s
        self._now = time_fn
        self.frame = ArtDmxFrame(0)
        self.layout: Optional[Layout] = None
        self.patch_error: Optional[str] = None
        self._patch_mtime: Optional[float] = None
        if layout is not None:
            self._set_layout(layout)
        # loop state (show-artnet only)
        self._upcoming: Optional[float] = None       # the beat the planned frame may carry
        self._last_beat: Optional[float] = None
        self._period: Optional[float] = None
        self._pulse_amp = PULSE_LO
        self._beats = 0
        self._t_prev: Optional[float] = None
        self._pan = self._tilt = 0.0
        self._onset_rate = 0.0
        self._energy = 0.0
        self._feat_t: Optional[float] = None
        self._seeded = False
        self._variant = 0                            # PRISM: bar parity, set at each beat edge
        self._quiet = False                          # a silent room (features.gate ~ 0 for quiet_after_s)
        self._quiet_since: Optional[float] = None
        self.active = False
        self.idle_reason: Optional[str] = "starting"  # no_features | quiet | None (sending)
        self.held: Optional[str] = None              # sending the director's look through no_features | quiet
        # cross-thread (plain attributes, swapped whole)
        self._sections = 0
        self.yielded_to: Optional[str] = None
        self.yielded_since: Optional[float] = None
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._sock: Optional[socket.socket] = None
        self.ours: frozenset[str] = frozenset({"127.0.0.1"})
        # counters
        self.packets = 0
        self.send_errors = 0
        self.last_send_error: Optional[str] = None
        self.late = 0
        self.callback_errors = 0
        self.polls = 0
        self.poll_errors = 0
        self.last_poll_error: Optional[str] = None
        self.yields = 0
        self.frame_errors = 0
        self._frame_log_next = 0.0
        self._late_ms = np.zeros(256)
        self._late_n = 0
        self._sent_t: Optional[float] = None
        self._interval_s: Optional[float] = None
        self._sleep = time.sleep                     # the deadline loop's sleep (tests drive a simulated clock)

    # -- any thread ---------------------------------------------------------------------------------------------------
    def section_change(self) -> None:
        """A section boundary: GOBO steps to its next value (follow_dmx's "section change")."""
        self._sections += 1

    def set_hue_latency_ms(self, ms: float) -> None:
        self.hue_latency_s = float(ms) / 1000.0

    def set_look(self, *, hue_depth: Optional[float] = None, beat_lock: Optional[bool] = None,
                 reset: bool = False, plan: Optional[Mapping[str, Any]] = None) -> None:
        """The director's look (any thread; module docstring). A None argument leaves that value; a hue_depth that
        isn't a finite number is ignored; ``reset`` restores the constructor's depth and beat_lock True first, and
        clears the plan. ``plan``: the composer's (module docstring), swapped whole; ``{}`` clears it; junk in it is
        dropped (a plan with nothing usable clears it)."""
        if reset:
            self.hue_depth, self.beat_lock, self.lamp_plan = self._hue_depth0, True, None
        if plan is not None:
            self.lamp_plan = _clean_plan(plan)
        if hue_depth is not None:
            try:
                d = float(hue_depth)
            except (TypeError, ValueError):
                d = math.nan
            if math.isfinite(d):
                self.hue_depth = _c01(d)
        if beat_lock is not None:
            self.beat_lock = bool(beat_lock)

    @property
    def gobo(self) -> int:
        return self._sections % 256

    def status(self) -> dict:
        n = min(self._late_n, len(self._late_ms))
        late = self._late_ms[:n] if n else None
        lay = self.layout
        state = ("off" if self.target is None else "stopped" if not self._threads
                 else "no_patch" if lay is None else "yielded" if self.yielded_to
                 else "sending" if self.active else "idle")
        return {"state": state, "target": f"{self.target[0]}:{self.target[1]}" if self.target else None,
                "universe": lay.universe if lay else None, "packets": self.packets, "seq": self.frame.seq,
                "rate_hz": round(1.0 / self._interval_s, 1) if self._interval_s else None,
                "late_ms": {"p50": round(float(np.percentile(late, 50)), 2),
                            "p95": round(float(np.percentile(late, 95)), 2)} if late is not None else None,
                "late_frames": self.late, "send_errors": self.send_errors, "last_send_error": self.last_send_error,
                "frame_errors": self.frame_errors,
                "idle_reason": self.idle_reason if state == "idle" else None,
                "held": self.held if state == "sending" else None,
                "rgb": [round(c, 3) for c in self.default_rgb] if self.palette_used == "fixed" else "director",
                "palette": self.palette_used,
                "beats": self._beats, "period_ms": round(self._period * 1000, 1) if self._period else None,
                "gobo": self.gobo, "hue_latency_ms": round(self.hue_latency_s * 1000, 1),
                "yield": {"from": self.yielded_to, "since_s": None if self.yielded_since is None
                          else round(self._now() - self.yielded_since, 1), "count": self.yields},
                "poll": {"url": self.room_dmx_url, "polls": self.polls, "errors": self.poll_errors,
                         "last_error": self.last_poll_error},
                "patch": {"path": str(self.patch_path), "error": self.patch_error,
                          "devices": [d.id for d in lay.devices] if lay else []},
                "callback_errors": self.callback_errors,
                "look": {"hue_depth": round(self.hue_depth, 3), "beat_lock": self.beat_lock},
                "plan": _plan_view(self.lamp_plan)}

    # -- lifecycle ----------------------------------------------------------------------------------------------------
    def start(self) -> None:
        """Load the patch, open the socket and start show-artnet and show-artnet-poll. Off (no target): logs, no
        threads."""
        if self.target is None:
            log.info("Art-Net out: off (no target configured)")
            return
        if self._threads:
            return
        if self.layout is None:
            self._check_patch(force=True)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(self.target)                      # fixes our source address (and reports ICMP refusals)
        self.ours = frozenset({"127.0.0.1", s.getsockname()[0]})
        self._sock = s
        self._stop.clear()
        self._threads = [threading.Thread(target=self._run, name="show-artnet", daemon=True),
                         threading.Thread(target=self._poll_loop, name="show-artnet-poll", daemon=True)]
        for t in self._threads:
            t.start()
        log.info("Art-Net out: -> udp %s:%d universe %d, %s", self.target[0], self.target[1],
                 self.layout.universe if self.layout else -1,
                 f"yield check {self.room_dmx_url}" if self.poll_fn else "no yield check")

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout)
        self._threads = []
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    # -- show-artnet: the deadline loop -------------------------------------------------------------------------------
    def _run(self) -> None:
        t_last = self._now()
        while not self._stop.is_set():
            try:
                t_next = self.plan(t_last)
                now = self._now()
                if now > t_next + LATE_S:           # woke late (a stall): re-plan from now, never burst
                    self.late += 1
                    t_next = self.plan(now - MIN_GAP_S)
                self._sleep_until(t_next - BUILD_AHEAD_S)
                ok = self.layout is not None and self.yielded_to is None and self.compose(t_next)
                self._sleep_until(t_next)
                # the next frame keeps MIN_GAP_S from when this one actually left, not from when it was due
                t_last = max(t_next, self._send(t_next)) if ok else t_next
            except Exception:                       # a bug here must never take show_rt down
                self.frame_errors += 1
                now = time.monotonic()
                if now >= self._frame_log_next:
                    self._frame_log_next = now + FRAME_LOG_S
                    log.exception("Art-Net out: frame failed (%d so far; logged at most every %.0f s)",
                                  self.frame_errors, FRAME_LOG_S)
                self._stop.wait(1.0)
                t_last = self._now()

    def _sleep_until(self, t: float) -> None:
        d = t - self._now()
        if d > 0:
            self._sleep(d)

    def _send(self, t_sched: float) -> float:
        """Send the composed frame; returns the time it left (after the send call)."""
        buf = self.frame.advance()
        try:
            self._sock.send(buf)
            self.packets += 1
        except OSError as e:                         # nothing listening (ICMP refused), interface down: carry on
            self.send_errors += 1
            self.last_send_error = f"{type(e).__name__}: {e}"
        now = self._now()
        self._late_ms[self._late_n % len(self._late_ms)] = (now - t_sched) * 1000.0
        self._late_n += 1
        if self._sent_t is not None and now - self._sent_t < 1.0:     # not across an idle or yield pause
            dt = now - self._sent_t
            self._interval_s = dt if self._interval_s is None else self._interval_s + 0.05 * (dt - self._interval_s)
        self._sent_t = now
        return now

    # -- the loop's steps (show-artnet; tests call them) ----------------------------------------------------------------
    def _next_beat(self, t: float, gate: Optional[str] = None) -> Optional[float]:
        try:
            b = self.clock.next_beat(t) if gate is None else self.clock.next_beat(t, gate=gate)
        except Exception:
            self.callback_errors += 1
            return None
        return float(b) if b is not None and math.isfinite(b) else None

    def plan(self, t_last: float) -> float:
        """The send time of the frame after one at ``t_last``, on the beat-phased grid (module docstring)."""
        earliest = t_last + MIN_GAP_S
        self._upcoming = None
        if self.clock is None:
            return earliest
        after = t_last
        if self._last_beat is not None and self._period:
            after = max(after, self._last_beat + REPHASE_SKIP * self._period)   # a re-phased beat already pulsed
        b = self._next_beat(after + 1e-6)
        if b is None:
            return earliest
        b2 = self._next_beat(b + MIN_GAP_S)
        if b2 is not None and b2 > b:
            self._period = b2 - b
        self._upcoming = b
        if b <= earliest:                           # inside the minimum gap: the edge frame goes at earliest
            return earliest
        tick = grid_tick(self._period) if self._period else MIN_GAP_S
        # the first grid point at or after earliest - GRID_TOLERANCE_S, never before earliest: a frame that left a
        # little late moves the next one by that much, not by a whole tick (README S5.14: the screens' feed kept its
        # 42.7 Hz, not 40 or 32)
        lo = earliest - GRID_TOLERANCE_S
        return max(earliest, b - math.floor((b - lo) / tick + 1e-9) * tick)

    def _features(self) -> Optional[Mapping[str, Any]]:
        if self.features_fn is None:
            return None
        try:
            return self.features_fn()
        except Exception:
            self.callback_errors += 1
            return None

    def _palette(self, t: float) -> Any:
        if self.palette_fn is None:
            return None
        try:
            return self.palette_fn(t)
        except Exception:
            self.callback_errors += 1
            return None

    def _colour(self, pal: Any, dev: Device) -> Sequence[float]:
        if isinstance(pal, Mapping):
            for k in (dev.id, dev.screen, "*"):
                c = pal.get(k) if k else None
                if c is not None and len(c) == 3:
                    return c
            return self.default_rgb
        if pal is not None and len(pal) == 3:
            return pal
        return self.default_rgb

    def _bar_parity(self, b: float) -> Optional[int]:
        """PRISM for the beat at ``b``: ((k - bar_anchor_beat) // beats_per_bar) % 2 in the clock's own numbering
        (``clock.snapshot()``, a music.clock body), so it flips on the clock's downbeats. None without one."""
        snap = getattr(self.clock, "snapshot", None)
        if snap is None:
            return None
        try:
            d = snap()
            bpm = float(d["bpm"]) if d else 0.0
            if not bpm > 0.0:
                return None
            k = round(d["beat_at_anchor"] + (b * 1000.0 - d["anchor_ms"]) * bpm / 60000.0)
            bpb = max(1, int(d.get("beats_per_bar") or 4))
            return ((k - int(d.get("bar_anchor_beat") or 0)) // bpb) % 2
        except Exception:
            self.callback_errors += 1
            return None

    def _quiet_room(self, f: Optional[Mapping[str, Any]], t: float) -> bool:
        """features.gate below QUIET_GATE_OFF for quiet_after_s: True (send nothing) until it is back over
        QUIET_GATE_ON. Without a gate in the snapshot (or quiet_after_s None): never quiet."""
        gate = f.get("gate") if f else None
        if self.quiet_after_s is None or not isinstance(gate, (int, float)) or not math.isfinite(gate):
            self._quiet, self._quiet_since = False, None
            return False
        if self._quiet:
            if gate >= QUIET_GATE_ON:
                self._quiet, self._quiet_since = False, None
                log.info("Art-Net out: sound again (gate %.2f): sending", gate)
        elif gate < QUIET_GATE_OFF:
            if self._quiet_since is None:
                self._quiet_since = t
            elif t - self._quiet_since >= self.quiet_after_s:
                self._quiet = True
                log.info("Art-Net out: a quiet room for %.0f s (gate %.2f): idle (room's input goes stale, the "
                         "lights go back to the house)", self.quiet_after_s, gate)
        else:
            self._quiet_since = None
        return self._quiet

    def _soft_beat(self, th: float, gate: Optional[str] = None) -> Optional[float]:
        """Hue's beat envelope at th: attack up to each beat, then decay; None without a predicted beat. ``gate``:
        the clock's gate to ask at (None: its default, beat; the plan's dmx rung asks "dmx")."""
        if self.clock is None:
            return None
        b = self._next_beat(th, gate)
        if b is None:
            return None
        p = self._period
        if not p:
            b2 = self._next_beat(b + MIN_GAP_S, gate)
            p = b2 - b if b2 is not None and b2 > b else None
        attack = _smoothstep(1.0 - (b - th) / HUE_ATTACK_S) if b - th < HUE_ATTACK_S else 0.0
        since = th - (b - p) if p else -1.0                 # since the previous beat (< 0: none yet)
        decay = math.exp(-since / HUE_DECAY_S) if since >= 0.0 else 0.0
        return max(attack, decay)

    def compose(self, t: float) -> bool:
        """Fill the universe for send time ``t``. False (send nothing) without a layout or, with a features_fn, without
        a fresh snapshot (idle)."""
        lay = self.layout
        if lay is None:
            return False
        feats = self._features()
        pal_t = self._palette(t)
        directed = isinstance(pal_t, Mapping)                    # the director is playing a vibe
        why = None
        if self.features_fn is not None and self.idle_after_s is not None:
            ft = feats.get("t_ms") if feats else None
            if not isinstance(ft, (int, float)) or t - ft / 1000.0 > self.idle_after_s:
                why = "no_features"
        if why is None and self._quiet_room(feats, t):
            why = "quiet"
        if why is not None and not directed:                     # neutral: today's P0 idle
            if self.active:
                log.info("Art-Net out: %s, idle (room's input goes stale and holds)",
                         "no fresh features" if why == "no_features" else "a quiet room")
            self.active, self.idle_reason, self.held = False, why, None
            return False
        if why is not None and self.held != why:                 # the director's relaxed look keeps going out
            log.info("Art-Net out: %s, holding the director's colours (no pulses, no swell)",
                     "no fresh features" if why == "no_features" else "a quiet room")
        self.held = why
        if not self.active:
            if self.features_fn is not None:
                log.info("Art-Net out: %s, sending", "the director's look" if why else "features fresh")
            self.active, self.idle_reason = True, None
        if why is not None:
            feats = None                                         # stale or silent: nothing follows the sound
        dt = 0.0 if self._t_prev is None else min(1.0, max(0.0, t - self._t_prev))
        self._t_prev = t
        depth, locked = self.hue_depth, self.beat_lock          # the director's look, read once a frame
        plan = self.lamp_plan                                    # the composer's (drive), read once a frame
        if why is not None:
            depth, locked = 0.0, False                           # held: steady colour, no pulses, no beat edges

        # features -> smoothed stand-ins
        f = feats or {}
        bands = f.get("bands") or {}
        energy = _c01(float(f.get("energy", 0.0) or 0.0))
        bright = _c01(float(f.get("brightness", 0.0) or 0.0))
        bass = _c01(float((bands.get("bass") or {}).get("level", 0.0) or 0.0))
        ft = f.get("t_ms")
        if isinstance(ft, (int, float)) and ft != self._feat_t:          # a new snapshot: count its hits once
            if self._feat_t is not None:
                span = min(1.0, max(0.01, (ft - self._feat_t) / 1000.0))
                hits = sum(int((b or {}).get("hits", 0) or 0) for b in bands.values())
                self._onset_rate = _flush(self._onset_rate + (hits / span - self._onset_rate)
                                          * (1.0 - math.exp(-span / TAU_ZOOM_S)))
            self._feat_t = ft
        if why is not None:
            pass                                     # held: PAN, TILT and ZOOM stay where they were; ACTIVITY 0
        elif not self._seeded:
            self._pan, self._tilt, self._energy, self._seeded = 2 * bright - 1, 2 * bass - 1, energy, True
        else:
            a = 1.0 - math.exp(-dt / TAU_MOVE_S)
            self._pan = _flush(self._pan + (2 * bright - 1 - self._pan) * a)
            self._tilt = _flush(self._tilt + (2 * bass - 1 - self._tilt) * a)
            self._energy = _flush(self._energy + (energy - self._energy) * (1.0 - math.exp(-dt / TAU_ENERGY_S)))

        # the beat edge: the frame at (or first after) the planned beat carries it
        b = self._upcoming
        if b is not None and t >= b - 1e-9 and (self._last_beat is None or b > self._last_beat + 1e-6):
            self._last_beat = b
            self._beats += 1
            self._pulse_amp = PULSE_LO + PULSE_SPAN * _c01(float(f.get("punch", 0.0) or 0.0))
            self._edge_locked = locked
            if locked:                               # beat_lock off: PRISM holds
                v = self._bar_parity(b)
                if v is None:                        # no clock numbering: our own edge count
                    bpb = getattr(self.clock, "beats_per_bar", 4) or 4
                    v = (max(0, self._beats - 1) // int(bpb)) % 2
                self._variant = v
        pulse = 0.0
        if self._last_beat is not None and locked and self._edge_locked:   # beat_lock off: no DIMMER pulse edges
            age = t - self._last_beat
            width = min(0.5 * self._period, PULSE_MAX_S) if self._period else PULSE_MAX_S
            if 0.0 <= age < width:
                pulse = self._pulse_amp * math.exp(-age / PULSE_TAU_S)
        screen_vals = {"pulse": pulse, "global.activity": energy, "pan": self._pan, "tilt": self._tilt,
                       "zoom": _c01(self._onset_rate / ONSET_FULL_HZ), "effect": self._sections % 256,
                       "variant": self._variant}

        # hue: the look for t + L_hue
        th = t + self.hue_latency_s
        plan_lamps: Optional[Mapping[str, tuple[float, float]]] = None
        if plan is None:
            env = self._soft_beat(th) if locked else None   # beat_lock off: the swell follows the energy only
            k = 1.0 - depth + depth * (self._energy if env is None else env)
        else:                                            # the plan on this output's own ladder (module docstring)
            if why is None and plan["depth"] is not None:
                depth = plan["depth"]
            env = self._soft_beat(th) if locked else None
            if env is None and locked:
                env = self._soft_beat(th, "dmx")
                if env is not None:
                    depth *= plan["middle_depth"]
            plan_lamps = plan["lamps"]
            k = 1.0
        pal_h = pal_t if th == t else self._palette(th)
        self.palette_used = "fixed" if pal_t is None else "director"

        data = self.frame.data
        data[:] = _ZEROS
        for dev in lay.devices:
            if dev.kind == "screen":
                encode_into(data, dev, {**screen_vals, "rgb": self._colour(pal_t, dev)})
            elif dev.kind == "hue":
                r, g, bl = self._colour(pal_h, dev)
                kd = k
                if plan_lamps is not None:
                    lv, pu = plan_lamps.get(dev.id) or plan_lamps.get("*") or (1.0, 1.0)
                    d = depth * pu
                    kd = 1.0 - d + d * (lv if env is None else env)
                encode_into(data, dev, {"dimmer": 1.0, "rgb": (r * kd, g * kd, bl * kd)})
            else:
                encode_into(data, dev, {})
        return True

    def channels(self) -> bytes:
        """A copy of the last composed universe (512 bytes)."""
        return bytes(self.frame.data)

    # -- show-artnet-poll -----------------------------------------------------------------------------------------------
    def _set_layout(self, lay: Layout) -> None:
        self.layout = lay
        self.frame.set_port_address(lay.universe)

    def _check_patch(self, force: bool = False) -> None:
        try:
            m = self.patch_path.stat().st_mtime
        except OSError as e:
            if self.patch_error is None or force:
                log.error("Art-Net out: patch %s: %s", self.patch_path, e)
            self.patch_error = str(e)
            return
        if not force and m == self._patch_mtime:
            return
        self._patch_mtime = m
        try:
            lay = load_layout(self.patch_path)
        except Exception as e:                       # keep the last good layout
            self.patch_error = f"{type(e).__name__}: {e}"
            log.error("Art-Net out: patch %s not loaded (%s); keeping the last good one", self.patch_path, e)
            return
        self.patch_error = None
        if lay != self.layout:
            log.info("Art-Net out: patch loaded: universe %d, %d devices", lay.universe, len(lay.devices))
        self._set_layout(lay)

    def check_foreign(self) -> None:
        """One yield check (show-artnet-poll; tests call it)."""
        try:
            st = self.poll_fn()
        except Exception as e:                       # room down or busy: change nothing
            self.poll_errors += 1
            self.last_poll_error = f"{type(e).__name__}: {e}"
            return
        self.polls += 1
        yielded = self.yielded_to is not None
        who = foreign_sender(st, self.ours, yielded=yielded)
        if who and self.yielded_to is None:
            self.yields += 1
            self.yielded_since = self._now()
            self.yielded_to = who
            log.warning("Art-Net out: another sender (%s) is live on the universe: paused", who)
        elif who is None and self.yielded_to is not None:
            log.info("Art-Net out: %s has gone quiet: sending again", self.yielded_to)
            self.yielded_to, self.yielded_since = None, None

    def _poll_loop(self) -> None:
        next_patch = next_poll = 0.0
        step = min(0.25, self.poll_s, PATCH_CHECK_S)
        while not self._stop.is_set():
            now = time.monotonic()
            try:
                if now >= next_patch:
                    next_patch = now + PATCH_CHECK_S
                    self._check_patch()
                if self.poll_fn is not None and now >= next_poll:
                    next_poll = now + self.poll_s
                    self.check_foreign()
            except Exception:
                log.exception("Art-Net out: poll failed")
            self._stop.wait(step)
