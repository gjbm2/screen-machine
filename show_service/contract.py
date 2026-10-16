"""The contract between show_service's modules: config, callback types, flags, stream names and the bus shapes (§3.3,
§11.1).

Constants and types only, no behaviour (Sender and Config are fields only; config.py parses into them). Modules
import shared names from here (and wire types from vban.py), never from each other, so receiver.py,
midi.py/harmony.py, dsp.py and bus.py can be built in parallel. README.md says who calls what on which thread. A
change here is a contract change: tell c8 and every builder.

Times: ``*_s`` are host CLOCK_MONOTONIC seconds (``time.monotonic()``), ``t_ms`` the same in ms. Every time a
module publishes is the time of the **sound** (acoustic: the timeline's map, offsets applied), never of arrival.
"""

from __future__ import annotations

import math
import pathlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Literal, Optional, TypedDict, Union

if TYPE_CHECKING:                                   # numpy and vban only for annotations here
    import numpy as np

    from .vban import MidiBatch, MidiState

# -- streams (§3.1, §3.3) ---------------------------------------------------------------------------------------------
AUDIO_PRIORITY: tuple[str, ...] = ("room-line", "room-mic", "pc22-loop", "pc22-mic")   # best first
MIDI_STREAMS: tuple[str, ...] = ("room-piano", "test-piano")
TEST_STREAMS: tuple[str, ...] = ("test-piano", "phone-test")      # marked test; lowest priority
KNOWN_STREAMS: tuple[str, ...] = AUDIO_PRIORITY + MIDI_STREAMS + ("phone-test",)
UNKNOWN_RANK = 1000                                 # unknown names: accepted, shown in health, chosen last


def stream_rank(name: str, priority: tuple[str, ...] = ()) -> int:
    """Lower is better: a name in ``priority`` (Config.stream_priority, ``--stream-priority``) first, in that order
    (negative ranks), then the AUDIO_PRIORITY index, then known test streams, then unknown names."""
    if name in priority:
        return priority.index(name) - len(priority)
    if name in AUDIO_PRIORITY:
        return AUDIO_PRIORITY.index(name)
    return UNKNOWN_RANK - 1 if name in TEST_STREAMS else UNKNOWN_RANK


StreamKind = Literal["audio", "midi", "unknown"]
StreamStateName = Literal["streaming", "quiet", "stale", "down", "no_device"]

# -- config (fields only: config.py parses args/env into these; the receiver takes them) ------------------------------
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Sender:
    """A sender show_rt subscribes to: ``host`` a DNS name (never a literal IP in committed config), ``streams`` the
    names it asks that sender for. Spec string (config.parse_sender): ``kiosk=screen-machine-drawingroom[:6980]/
    room-mic,room-piano``."""
    name: str
    host: str
    port: int = 6980
    streams: tuple[str, ...] = ()


@dataclass(frozen=True)
class Config:
    """Everything show_service is started with (config.load builds it from args over env)."""
    senders: tuple[Sender, ...] = ()
    vban_bind: tuple[str, int] = ("0.0.0.0", 6980)
    bus_bind: tuple[str, int] = ("127.0.0.1", 8703)
    latency_file: pathlib.Path = REPO_ROOT / "config" / "room" / "latency.json"   # read-only for us (10's schema v2)
    log_level: str = "INFO"
    stream_priority: tuple[str, ...] = ()           # audio streams ranked before AUDIO_PRIORITY (stream_rank)

# -- receiver -> consumers (all called on the receive thread, "show-rx", in order) ------------------------------------
AUDIO_GAP = 1           # pcm is zero-fill for packets that never came: don't detect onsets in it
AUDIO_RESUME = 2        # the first real block after a gap: don't fire on the step from silence
AUDIO_SEGMENT = 4       # the first block of a new timeline segment, or the first after the active stream switched:
                        # n0 restarted (or another stream), reset filter and onset state

#: on_audio(stream, n0, t0_host_s, pcm, fs, flags): ``pcm`` float32 (frames, channels), a block the receiver no
#: longer touches; ``n0`` its first sample's index in the stream's current segment; ``t0_host_s`` = that sample's
#: acoustic host time (SampleClock.to_host(n0)); ``fs`` the stream's rate in Hz (it can differ per stream, e.g.
#: pc22-loop at 44100; a change comes with AUDIO_SEGMENT); sample i is at t0_host_s + i / fs. Only the active
#: stream's blocks are delivered (Receiver.active_audio()).
OnAudio = Callable[[str, int, float, "np.ndarray", int, int], None]
#: on_midi(stream, msg, arrival_s, to_host): ``msg`` a validated vban.MidiBatch or vban.MidiState; ``to_host(t_ns)``
#: maps the sender's t_ns to acoustic host seconds (timeline.RemoteTimeMap.to_host, NaN until it can).
OnMidi = Callable[[str, Union["MidiBatch", "MidiState"], float, Callable[[int], float]], None]
#: on_tick(now_s): at least every TICK_S from the receive thread, between packets: periodic publishing lives here.
OnTick = Callable[[float], None]
TICK_S = 0.01
#: publish(msg): bus.Bus.publish; thread-safe, never blocks, never raises; ``msg`` is an Envelope.
Publish = Callable[[dict], None]

# -- the show bus (§11.1) ---------------------------------------------------------------------------------------------
BUS_HOST = "127.0.0.1"
BUS_PORT = 8703
BUS_PATH = "/v1/show"
BUS_SCHEMA = 2
RENDERER_QUEUE = 64     # the renderer role is never coalesced: 64 queued, then close CLOSE_BEHIND
OTHER_QUEUE = 256       # other roles: drop the oldest when full (counted)
CLOSE_BEHIND = 4008     # the client reconnects at once and heals from the next music.notes
HELLO_TIMEOUT_S = 5.0

MUSIC_NOTE = "music.note"
MUSIC_PEDAL = "music.pedal"
MUSIC_NOTES = "music.notes"
MUSIC_HARMONY = "music.harmony"
MUSIC_FEATURES = "music.features"
MUSIC_HIT = "music.hit"           # only to clients whose hello ``want`` lists it (lights, never the renderer)
MUSIC_CLOCK = "music.clock"       # the beat clock (§5.3; clock.py): on re-anchor + 10 Hz
WANT_ONLY: tuple[str, ...] = (MUSIC_HIT,)

Source = Literal["live", "test"]
PedalName = Literal["damper", "soft", "sostenuto"]
PEDAL_CC: dict[int, str] = {64: "damper", 66: "sostenuto", 67: "soft"}


class Envelope(TypedDict):
    """Every bus message, exactly as stream_service/sound_view.py and piano_replay.py read and write it."""
    type: str
    data: dict


class NoteData(TypedDict):
    """music.note, sent at once. ``seq``: one increasing space per ``sess`` shared by notes and pedals (the bus's
    own, renumbered by midi.py, not the wire's batch seq). ``vel``: the strike (hot-note capped) on note-on, the
    release velocity on note-off (0 if the device sends none)."""
    seq: int
    t_ms: float
    on: bool
    pitch: int
    vel: int
    source: Source
    sess: str


class PedalData(TypedDict):
    """music.pedal, sent at once, on change. ``value`` = CC/127 (the damper is proportional)."""
    seq: int
    t_ms: float
    pedal: PedalName
    value: float
    source: Source
    sess: str


class NotesData(TypedDict):
    """music.notes, 2-10 Hz: the keys physically down at ``t_ms`` as [pitch, vel, t_on_ms]; ``seq`` = the last
    note/pedal event it reflects; ``present`` = the piano is playing (MIDI activity, §3.5)."""
    seq: int
    t_ms: float
    keys: list[list[float]]
    pedals: dict[str, float]
    present: bool
    source: Source
    sess: str


KEY_NAMES: tuple[str, ...] = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


class HarmonyData(TypedDict):
    """music.harmony, 10 Hz. ``chroma12``: C..B, 0..1, max-normalised (all 0 in silence); from MIDI while the
    piano plays, else audio. ``key`` in KEY_NAMES or None; ``mode`` "major"|"minor"|None. ``tension``,
    ``chord_change``, ``register`` (level-weighted mean pitch, A0 0 .. C8 1), ``spread`` (weighted pitch spread,
    two octaves = 1) all 0..1. The first seven keys are what sound_view.py reads."""
    chroma12: list[float]
    key: Optional[str]
    mode: Optional[str]
    tension: float
    chord_change: float
    register: float
    spread: float
    t_ms: float
    source: Literal["midi", "audio"]
    conf: float


BAND_NAMES: tuple[str, ...] = ("sub", "bass", "low_mid", "mid", "high_mid", "air")
BAND_EDGES_HZ: dict[str, tuple[float, float]] = {
    "sub": (20.0, 60.0), "bass": (60.0, 250.0), "low_mid": (250.0, 500.0), "mid": (500.0, 2000.0),
    "high_mid": (2000.0, 6000.0), "air": (6000.0, 16000.0)}


class BandData(TypedDict):
    level: float          # 0..1, rolling-percentile normalised, loudness-gated
    hits: int             # onsets in this band since the previous music.features
    presence: float       # 0..1, slow (tau ~1 s): how much this band is part of the sound


class FeaturesData(TypedDict):
    """music.features, 25 Hz. Levels in dB are absolute (dBFS / LUFS, floored at -70 LUFS and -120 dBFS)."""
    t_ms: float
    stream: str
    bands: dict[str, BandData]
    onset: float          # 0..1 SuperFlux onset strength, normalised
    lufs_m: float         # momentary (400 ms)
    lufs_s: float         # short-term (3 s)
    peak_dbfs: float
    rms_dbfs: float
    brightness: float     # 0..1 spectral centroid on a log axis, 50 Hz .. 16 kHz
    noisiness: float      # 0..1 spectral flatness
    gate: float           # 0..1 the loudness gate g (§6.3)
    energy: float         # 0..1 normalised loudness x gate
    # punch and drive mean what stream_service/music_direction.py's do (it derives them from Maestro today), from
    # low-end hits instead of the DIMMER pulse; a "low hit" is a sub or bass hit (both within 60 ms count once):
    punch: float          # 0..1 clamp((peak - 0.10) / 0.60), peak = the low hits' strength, held, decaying tau 4 s
    drive: float          # 0..1 EMA tau 8 s of 0.7 x clamp(rate / 2.2) + 0.3 x punch, rate = low hits per second
                          # over the last 8 s (0 until 4 s of audio); both computed ungated, published x gate
    spectrum32: list[float]


class HitData(TypedDict):
    """music.hit, per hit, to ``want``-ers only."""
    t_ms: float
    band: str
    strength: float       # 0..1
    stream: str


class ClockData(TypedDict):
    """music.clock (§5.3, §11.1), on every re-anchor and at 10 Hz: a shared clock, not triggers. A consumer computes
    ``beat(t) = beat_at_anchor + (t_ms - anchor_ms) / 1000 * bpm / 60`` at its own ``now + lead`` (clock.beat_at,
    clock.next_beat, clock.position_at do exactly that from a body)."""
    bpm: float            # 0.0 before the first grid
    anchor_ms: float      # CLOCK_MONOTONIC ms (acoustic) of integer beat ``beat_at_anchor``
    beat_at_anchor: int
    beats_per_bar: int
    bar_anchor_beat: int  # a beat that starts a bar and a phrase
    phrase_bars: int      # 4 | 8 | 16
    conf: float           # 0..1 ~P(the next beat lands within 25 ms)
    bar_conf: float
    phrase_conf: float    # <= bar_conf
    state: str            # searching | locked | holding | free
    gate: str             # beat (conf on 0.70 / off 0.60) | dmx (0.45 / 0.35) | none; 2-bar dwell
    epoch: int            # +1 per published jump
    stability: float      # tempogram clarity at the clock's tempo
    source: str           # the activation: madmom | aubio
    t_ms: float           # publish time


# spectrum32 (§11.1, agreed with b8; stream_service/plugins/_lib/glsl/runtime.py SPEC_LO_HZ/SPEC_HI_HZ)
SPEC_BANDS = 32
SPEC_LO_HZ = 30.0
SPEC_HI_HZ = 16000.0
SPEC_EDGES_HZ: tuple[float, ...] = tuple(SPEC_LO_HZ * (SPEC_HI_HZ / SPEC_LO_HZ) ** (k / SPEC_BANDS)
                                         for k in range(SPEC_BANDS + 1))
SPEC_CENTRES_HZ: tuple[float, ...] = tuple(math.sqrt(a * b) for a, b in zip(SPEC_EDGES_HZ, SPEC_EDGES_HZ[1:]))
SPEC_WINDOW_DB = 60.0   # value = clamp((dB - (ref - 60)) / 60), ref = rolling p95 of the loudest band over 60 s
SPEC_REF_S = 60.0
SPEC_TAU_S = 0.3        # smoothing, rise and fall alike, after the gate
