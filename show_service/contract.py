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
TEST_STREAMS: tuple[str, ...] = ("test-piano", "phone-test", "test-audio")   # marked test; lowest priority
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
StreamStateName = Literal["streaming", "quiet", "stale", "down", "no_device", "off"]   # off: the mic on demand
#: keeps it closed (README S5.16): never live, never the active audio

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
    # Stage 3 (README "Stage 3 > sources.py / receiver seam"), additions:
    # ``hosts``: the ordered fallback list from config/room/show.yaml (device addresses live in config/room/, owner
    # ruling 16 Oct, like sonos.json); when non-empty it is the truth and ``host`` == hosts[0] for older readers.
    # ``on_demand``: subscribed only while sources.py runs that test source (show.yaml ``on_demand: true``).
    hosts: tuple[str, ...] = ()
    on_demand: bool = False


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
    """music.features, 50 Hz (dsp.PUBLISH_HZ; 25 Hz until 17 Oct). Levels in dB are absolute (dBFS / LUFS, floored at -70 LUFS and -120 dBFS)."""
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
    conf: float           # 0..1: dbn (the default) P(the next beat lands within 70 ms); legacy ~P(within 25 ms)
    bar_conf: float
    phrase_conf: float    # <= bar_conf
    state: str            # searching | locked | holding | free
    gate: str             # beat (conf on 0.80 / off 0.60) | dmx (0.60 / 0.35) | none; 2-bar dwell (legacy 0.70, 0.45)
    epoch: int            # +1 per new grid (first line, tempo jump, octave, tap): the count runs on within one
    stability: float      # dbn: the posterior's tempo mass; legacy: tempogram clarity at the clock's tempo
    source: str           # dbn: madmom-dbn2 (aubio-dbn on the fallback); legacy: the activation, madmom | aubio
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
SPEC_TAU_S = 0.3        # smoothing after the gate: the fall
SPEC_RISE_TAU_S = 0.035  # ...and the rise (owner, 17 Oct, via screen-machine-73: sound to glass under 0.5 s, so the
                         # screens' bars rise at once and fall softly; this replaces "tau >= 300 ms both ways")


# =====================================================================================================================
# Stage 3 (README "Stage 3"): the brain, in-process for now. director.py + palette.py + vibes.py, situations.py +
# autopilot.py, api.py + brain.py, sources.py, listener.py + ring.py + utils/llm/, and room_service/adapters/show.py.
# Additions only. These are the names the six Stage-3 builders share; a module imports them from here and never
# imports another builder's module. Shapes are exact: a key marked NotRequired may be absent, every other key is always
# present (null where the type says Optional). Times: ``*_s`` monotonic seconds, ``t_ms``/``at_ms`` monotonic ms (the
# bus's clock, acoustic where it says so), ``*_age_s``/``*_in_s`` relative seconds, ``*_at`` (persisted only) wall-clock
# epoch seconds (time.time()), because monotonic time does not survive a restart.
# =====================================================================================================================
from dataclasses import field  # noqa: E402
from typing import Any, NotRequired, Protocol  # noqa: E402

# -- files (all hot-reloaded by their reader unless it says so) -------------------------------------------------------
CONFIG_ROOM_DIR = REPO_ROOT / "config" / "room"
VIBES_FILE = CONFIG_ROOM_DIR / "vibes.yaml"         # c8: the vibes (§8.1); vibes.py reads it
SHOW_FILE = CONFIG_ROOM_DIR / "show.yaml"           # c8: show_service's own settings; brain.py reads it
PATCH_FILE = CONFIG_ROOM_DIR / "patch.yaml"         # 68's; read-only for us (lamp placement -> roles)
LLM_FILE = REPO_ROOT / "config" / "llm.yaml"        # c8: task -> model list, prices, budgets (utils/llm)
VOCAB_FILE = REPO_ROOT / "config" / "live" / "vocab.yaml"        # 04's; read-only (moods, genre, transition_style)
PALETTES_FILE = REPO_ROOT / "stream_service" / "palettes.py"     # 04's catalog; read by file path, never imported as
                                                                 # a package (palette.py)
TEST_PIANO_DIR = REPO_ROOT / "config" / "live" / "test-piano"    # the test piano's .mid library (98's)
TEST_PIANO_SCRIPT = REPO_ROOT / "tools" / "show_sender" / "show_sender.py"   # 98's sender; --test-piano PIECE
STATE_DIR = pathlib.Path.home() / ".local" / "state" / "screen-machine" / "show"
STATE_FILE = STATE_DIR / "state.json"               # brain.py, atomic (tmp + fsync + os.replace)
AUTOPILOT_LOG = STATE_DIR / "autopilot.jsonl"       # every Auto decision with its evidence (rotated at 1 MB)
DIRECTOR_LOG = STATE_DIR / "director.jsonl"         # every look change and nudge with the music context (1 MB)
LISTENER_LOG = STATE_DIR / "listener.jsonl"         # every listener verdict, failure, skip and Describe it (1 MB)
CUES_LOG = STATE_DIR / "cues.jsonl"                 # every cue armed, fired, disarmed, missed, and its outcome (1 MB)
LLM_LEDGER = pathlib.Path.home() / ".local" / "state" / "screen-machine" / "llm" / "ledger.jsonl"
TEST_MUSIC_DIR = pathlib.Path.home() / ".cache" / "screen-machine" / "test-music"   # tools/show/fetch_test_music.py
ROOM_URL = "http://127.0.0.1:8700"                  # room_service in the Flask process, over loopback (show.yaml
                                                    # room.url overrides); never a LAN address in code

# -- streams and test sources ------------------------------------------------------------------------------------------
TEST_PIANO_STREAM = "test-piano"
TEST_AUDIO_STREAM = "test-audio"    # a test stream (TEST_STREAMS; the receiver: any "test-*"), ranked after every
                                    # room stream, so sources.py lifts it with Receiver.set_priority while it plays
TEST_PIANO_PORT = 6981              # show.yaml's defaults; tests never bind 6980-6982, 6454 or 8703
TEST_AUDIO_PORT = 6982
#: The test sources of POST /api/v1/test (98's API words) -> how each plays and its documented defaults (used only when
#: show.yaml's ``tests.<source>`` or its sender leaves them out). ``kind`` picks the program (sources.py): "midi" is
#: show_sender's test piano, "audio" the test-audio child. Code looks a source up here; it never compares its name.
TEST_SOURCE_PLAYERS: dict[str, dict[str, Any]] = {
    "piano": {"kind": "midi", "stream": TEST_PIANO_STREAM, "port": TEST_PIANO_PORT, "sender": "test-piano"},
    "audio": {"kind": "audio", "stream": TEST_AUDIO_STREAM, "port": TEST_AUDIO_PORT, "sender": "test-audio"},
}
TEST_SOURCES: tuple[str, ...] = tuple(TEST_SOURCE_PLAYERS)
TestSource = Literal["piano", "audio"]
TestState = Literal["off", "starting", "playing", "stopping", "failed"]

# -- rates (show-brain) ------------------------------------------------------------------------------------------------
BRAIN_TICK_HZ = 10.0     # director
SLOW_TICK_HZ = 1.0       # situations, autopilot, sources
RELOAD_S = 2.0           # show.yaml, vibes.yaml, patch.yaml, llm.yaml, the palette catalog: mtime polls
CARD_HZ = 2.0            # /v1/events card patches (chip changes and announcements go at once)
PERSIST_MIN_S = 1.0      # state.json at most once a second, on change
BRAIN_CALL_TIMEOUT_S = 2.0   # api.py -> show-brain calls (describe, Auto on/off and Undo: their own, longer)
#: Auto's room PUTs (PUT /api/v1/mode, /api/v1/room/targets). The room answers only after its request lock, and a
#: ``skip`` with ``{"from": ...}`` first reads the devices (Sonos 4 s, blinds and Hue 5 s): the worst case plus margin.
ROOM_PUT_TIMEOUT_S = 10.0
#: api.py's brain.call for Auto on/off and Undo, which each wait on one room PUT: longer than ROOM_PUT_TIMEOUT_S, so the
#: call is never cancelled mid-PUT.
AUTO_CALL_TIMEOUT_S = ROOM_PUT_TIMEOUT_S + 2.0
GATE_REF_DEFAULT_LUFS = -30.0   # dsp.Features.gate_ref_lufs's own default: a vibe's neutral loudness_ref_lufs

# -- bus types added by the brain (§11.1) ------------------------------------------------------------------------------
ROOM_DIRECTION = "room.direction"   # DirectionData, on change (<= BRAIN_TICK_HZ) and every 5 s
ROOM_LOOK = "room.look"             # LookData, on change and every 5 s
ROOM_EVENT = "room.event"           # EventData, when decided (>= lead + 250 ms before at_ms where it can be)
MUSIC_INSIGHT = "music.insight"     # InsightData, on each listener verdict
MUSIC_CONTEXT = "music.context"     # MusicContext, 1 Hz (Stage 4: structure.py; to every client)
LATEST_ON_CONNECT_BRAIN: tuple[str, ...] = (ROOM_DIRECTION, ROOM_LOOK)   # bus.py adds these to LATEST_ON_CONNECT

# -- /v1/events (the card's WebSocket; api.py) --------------------------------------------------------------------------
EVENTS_PATH = "/v1/events"
EV_WELCOME = "welcome"       # {type, v: 1, seq, t_ms, card: CardData}: always first; again after {"type": "resync"}
EV_CARD = "card"             # {type, seq, patch: <RFC 7386 merge patch of CardData>}; seq +1 per message
EV_AUTO = "auto"             # {type, seq, event: AutoAnnouncement}: every Auto switch, suggestion, hold, undo, pause
EV_DESCRIBE = "describe"     # {type, seq, result: DescribeResult}
EV_PONG = "pong"             # answer to {"type": "ping"}

# -- vibes (§8.1; vibes.yaml) --------------------------------------------------------------------------------------------
FOLLOW_INPUTS: tuple[str, ...] = ("dynamics", "register", "harmony", "energy", "onsets")
CHANGE_LOOKS: tuple[str, ...] = ("phrase", "between_pieces", "time")
ACCENT_STYLES: tuple[str, ...] = ("none", "soft", "downbeat", "beat", "full")
PALETTE_ROLES: tuple[str, ...] = ("base", "accent", "highlight")
PALETTE_AUTO = "auto"        # a palette pick: the vibe's ranked list (the director chooses)
PALETTE_ART = "art"          # from the on-glass stills (room's snapshots), in a vibe's list or as a pick
#: Every field a vibe (or a mode's ``overrides``) may set. vibes.py owns each field's JSON schema and neutral value
#: (README "Stage 3 > vibes.yaml"); a field a vibe leaves out takes its neutral value, never a hidden default.
VIBE_FIELDS: tuple[str, ...] = (
    "label", "description", "energy", "energy_centre", "loudness_ref_lufs", "follow", "follow_amount", "beat_lock",
    "change_looks", "change_rate", "dwell_s", "relax_on_talk", "palettes", "key_cct", "lamp_chroma_max",
    "chroma_max", "saturation", "contrast", "level", "floor", "motion", "accents", "strobe", "peak_tokens_per_hour",
    "moods", "genre", "transition_style", "palette_family", "step_size", "max_steps", "persona")


class VibeSettings(TypedDict, total=False):
    """One vibe's fields as vibes.py resolves them (``effective``: the vibe, then the mode's overrides; every field
    present, neutral where unset). Ranges are closed; lists keep their order."""
    label: str
    description: str
    energy: list[float]            # [lo, hi] 0..1: the bounds of direction.energy (clamp_to_vibe, §8.2)
    energy_centre: float           # 0..1, inside energy; neutral: the middle of energy
    loudness_ref_lufs: Optional[float]   # the room's expected loudness -> dsp.Features.gate_ref_lufs (§6.3); None: leave it
    follow: list[str]              # FOLLOW_INPUTS that steer numbers inside a look
    follow_amount: float           # 0..1: the follow gain in §8.2's formula
    beat_lock: bool                # False: nothing beat-locked (no Art-Net pulses, no beat accents, no hue beat swell)
    change_looks: str              # CHANGE_LOOKS
    change_rate: float             # 0..1: min_bars 32 / 16 / 8 at 0 / 0.5 / 1 (§8.5); also direction.change_rate
    dwell_s: list[float]           # [min, max]: when changing by time (change_looks "time", or the clock unsure)
    relax_on_talk: bool
    palettes: list[str]            # ranked: names in the palette catalog, or "art"
    key_cct: float                 # kelvin, the key lamps' white (2400-3000 in calm vibes)
    lamp_chroma_max: float         # OKLCh chroma cap for key-role lamps (about 0.05 = a faint tint; 0.37 = any)
    chroma_max: float              # OKLCh chroma cap for every other lamp
    saturation: list[float]        # [lo, hi] 0..1: what arousal and energy move (lamps' chroma scale; screens' words)
    contrast: list[float]          # [lo, hi] 0..1
    level: list[float]             # [lo, hi] 0..1: lamps and DMX only, never screens
    floor: float                   # 0..1: the lamps' background floor, never screens
    motion: list[float]            # [lo, hi] 0..1: movement speed/amount within a look
    accents: str                   # ACCENT_STYLES
    strobe: bool                   # strobe allowed (L5 caps; default off)
    peak_tokens_per_hour: int      # drops that may spend a peak (§8.5)
    moods: list[str]               # vocab.yaml moods: the vibe's own words (lowest priority, §8.2)
    genre: Optional[str]           # vocab.yaml genre, or None
    transition_style: Optional[str]   # vocab.yaml transition_style, or None (the music's band decides)
    palette_family: Optional[str]  # vocab.yaml palette_family, or None
    step_size: float               # one Calmer/Wilder step, in energy units
    max_steps: int                 # steps go -max..+max
    persona: str                   # one paragraph for Describe it (the listener never sees it, §7.1)


class VibeTarget(TypedDict):
    """The show room target's value (subsystem ``show``, member ``main``) and PUT /api/v1/vibe's body, canonical:
    ``listening`` always present (False when a mode leaves it out); ``overrides`` only when non-empty, null ones left
    out (null = unset). Kept as asked (degrade, never refuse): a vibe vibes.yaml lacks plays neutral, an override that
    isn't a usable VIBE_FIELDS value (or is ``label``/``description``/``persona``) is dropped at run time; the
    read-back's ``problems`` lists them. No feel is None (PUT {"vibe": null}, DELETE)."""
    vibe: str
    listening: bool
    overrides: NotRequired[dict[str, Any]]


class VibeReadback(TypedDict):
    """GET /api/v1/vibe (and PUT's and DELETE's answer). The room adapter reads only vibe / listening / overrides (the
    target as it was asked, so its comparison holds) and ``boot_id``."""
    vibe: Optional[str]            # None: no feel (neutral; never set, or set to null). The adapter reads it as the
                                   # value {"vibe": null, "listening": false}, so the room can put "no feel" back
    listening: bool
    overrides: NotRequired[dict[str, Any]]
    source: str                    # "mode" (the adapter) | "api" | "restored" (state.json) | "none"
    set_age_s: Optional[float]
    known: bool                    # the vibe exists in vibes.yaml (False: the neutral look, and the card says so)
    label: str                     # the vibe's label, or its name
    effective: VibeSettings        # the vibe + overrides, neutral where unset
    listening_effective: bool      # what the listener does now (ListenerView.effective)
    listening_why: Optional[str]   # page words when it isn't listening
    problems: list[str]            # what of the target can't be played as asked (an unknown vibe, dropped overrides)
    boot_id: NotRequired[str]      # api.py: this show_service process (a cold start with no target: the adapter
                                   # re-applies the room's value it last applied)


class PaletteChoice(TypedDict):
    name: str                      # catalog name, "art" or "auto"
    label: str
    family: Optional[str]          # warm | cool | vivid | neutral (catalog tags); None for art/auto
    swatch: list[str]              # "#rrggbb" x5 (art: the latest clusters, [] before the first still)


class VibesCatalog(TypedDict):
    """GET /api/v1/vibes: the Feel card's choices and the editor's field schemas (room adapter's value_schema)."""
    revision: str                  # sha1 of vibes.yaml, 12 hex
    path: str                      # "config/room/vibes.yaml"
    error: Optional[str]           # the last load problem (the last good file stays in use)
    order: list[str]               # vibe names in card order
    vibes: dict[str, dict]         # name -> {"label", "description", "fields": {field: value as written}}
    fields: dict[str, dict]        # field -> JSON schema (room's catalog subset + x-ui), VIBE_FIELDS order
    neutral: dict[str, Any]        # field -> its neutral value
    palettes: list[PaletteChoice]  # the catalog, then "art"
    vocab: dict[str, list[str]]    # vocab.yaml's moods, genre, transition_style, palette_family


# -- the director's outputs (§8.3) ---------------------------------------------------------------------------------------
class ColourRole(TypedDict):
    hex: str                       # "#rrggbb", sRGB
    oklch: list[float]             # [L 0..1, C 0..~0.37, h degrees 0..360)


class PaletteRoles(TypedDict):
    base: ColourRole
    accent: ColourRole
    highlight: ColourRole


class LookPalette(TypedDict):
    name: str                      # catalog name | "art" | "neutral" (no palette: the key white everywhere)
    source: str                    # "vibe" | "art" | "human" | "describe" | "neutral"
    roles: PaletteRoles
    emphasis: str                  # the role arousal/energy emphasise now: PALETTE_ROLES


class LampOut(TypedDict):
    """One lamp's colour as palette_fn hands it to artnet_out: ``rgb`` already x level (lamps only)."""
    role: str                      # a show.yaml lamps.roles name ("extend", "key", ...)
    screen: Optional[str]          # patch.yaml's screen: for that lamp
    hex: str
    rgb: list[float]               # 0..1, x level and floor
    level: float


class ScreenOut(TypedDict):
    """A screen fixture's RGB (Art-Net RED/GREEN/BLUE): the palette colour, NEVER scaled by level/floor/low/house."""
    hex: str
    rgb: list[float]


class LookData(TypedDict):
    """room.look (§8.3), fixture-agnostic plus the allocation palette_fn uses. level/floor/house/low: lamps and DMX
    only; screens get direction, palette and events, never a gain (the screens rule)."""
    t_ms: float
    seq: int
    look_id: str                   # "L<n>": +1 per look change (a variation keeps the id)
    vibe: Optional[str]
    mode: Optional[str]            # the room mode, for logs only (nothing branches on it)
    palette: LookPalette
    level: float                   # lamps/DMX
    floor: float                   # lamps/DMX
    house: bool                    # House lights on (lamps at the room's stored house state, or house white)
    low: bool                      # Lights low (lamps -> director.low.level over low.fade_s)
    key_cct: float
    chroma_max: float
    lamp_chroma_max: float
    saturation: float              # 0..1, the current value inside the vibe's range
    contrast: float
    motion: dict[str, float]       # {"speed": 0..1, "amount": 0..1}
    accents: dict[str, Any]        # {"style": ACCENT_STYLES, "density": 0..1, "subdivision": 1|0.5|0.25|0.125,
                                   #  "strobe": bool, "beat_lock": bool}
    spread: float                  # 0..1 spatial spread of roles across lamps
    change_rate: float
    transition: dict[str, Any]     # {"style": "cut"|"crossfade", "beats": float|None, "seconds": float|None}
    lamps: dict[str, LampOut]      # patch.yaml device id -> its colour now
    screens: dict[str, ScreenOut]  # patch.yaml screen fixture id ("screen.north") -> RGB
    # Stage 5 review fixes (README S5.14): lamps and DMX only, never a screen
    dip: bool                      # the pre-drop dip is on (§8.5): no music.hit flashes over it
    hit: Optional["LookHit"]       # the drop's unison hit, applied per output from its caps (looks.py); None: none
    # README S5.18 (17 Oct): the composer's plan, for monitoring (dsp_monitor), never the Room page
    compose: "ComposeView"
    # README S5.24 (18 Oct): the lamps' light scene (config/live/light_scenes.yaml), lamps and DMX only
    light_scene: Optional[str]     # the scene the look engine runs (its layers and hits); None: show.yaml looks.layers
    light_scene_why: Optional[str]  # short words: why it was picked ("a new song: energy 0.6, euphoric, techno")
    light_scene_drop: Optional["LightSceneDrop"]   # the last drop line the director heard (the scene's drop flash)
    # docs/LIGHTS.md §4, §7 (18 Oct): the room's flow, and one light program per light group (show.yaml lights.groups).
    # light_scene above stays: it is the scene of the group lights.scene_group names (compat)
    flow: "FlowData"               # the same object as room.direction's
    light_programs: dict[str, "LightProgram"]   # light group -> its program; {} while neutral (the compat path)


class LightSceneDrop(TypedDict):
    """LookData.light_scene_drop: a drop line (structure's drop, a cue's forecast drop) for the light scene's drop
    flash (looks.LookEngine.trigger_hit, taken once per ``id``), whatever the peak tokens say."""
    id: str                        # "drop:<n>": +1 per drop line the director passes on
    at_ms: float                   # the line, host time (CLOCK_MONOTONIC ms), as LookData.t_ms; ahead for a forecast
    strength: float                # 0..1 (1.0: the events carry no strength)


# -- light programs and flow (docs/LIGHTS.md §4, §7; the catalogue builder's, 18 Oct) ---------------------------------
#: FlowData.source: what decided the flow (flow.py). "event": a drop, a build or a new song bypassed the dwell
FLOW_SOURCES: tuple[str, ...] = ("structure", "event", "pitch", "listener", "moods", "none")
FLOW_SYMMETRY: tuple[str, ...] = ("none", "mirror")      # FlowData.symmetry (show.yaml lights.flow.symmetry)
LIGHT_TRANSITION_KINDS: tuple[str, ...] = ("cut", "dark_move", "crossfade", "fade_dark", "sweep")
LIGHT_EFFECT_TRIGGERS: tuple[str, ...] = ("drop", "build", "breakdown", "peak", "section_change", "track_change",
                                          "outro", "every_bars")
#: a light layer's ``kind`` (docs/LIGHTS.md §5.2; a layer without one takes its ``attr``, else intensity). Shutter
#: layers live in light_effects.yaml only (a scene never strobes)
LIGHT_LAYER_KINDS: tuple[str, ...] = ("intensity", "position", "beam", "gobo", "prism", "colour", "colour_wheel",
                                      "cct", "shutter")
#: the attrs that make a light's look (cap_fit, docs/LIGHTS.md §6.1): an entry driving more of a group's wins there
EXPRESSIVE_ATTRS: tuple[str, ...] = ("pan", "tilt", "gobo", "prism", "zoom", "colour_slot", "intensity")


class FlowData(TypedDict):
    """room.direction.flow and room.look.flow: one direction of travel for every appliance (docs/LIGHTS.md §4.1). The
    shared phase every consumer evaluates at its own photon time:
      wave(t, p) = frac((beat(t) - anchor_beat) / period_beats - spread x flow_coord(p))      with a grid
                 = frac((t - at_ms / 1000) / period_s - spread x flow_coord(p))               without"""
    id: int                        # +1 per change of dir, at or symmetry (not of speed or spread)
    dir: str                       # FLOW_DIRS
    at: Optional[str]              # a Room map name: towards/away's target, cw/ccw/outward/inward's centre; None = the
                                   # room's centre (show.yaml lights.flow.centre)
    at_xyz: Optional[list[float]]  # that point resolved (m), else the room's centre; None without a space
    speed: float                   # 0..1
    spread: float                  # 0..1: 0 unison, 1 one whole cycle across the room
    coherence: float               # 0..1: how strictly every appliance should follow it (events 1.0)
    symmetry: str                  # FLOW_SYMMETRY
    period_beats: Optional[float]  # the wave's crossing time, a power of two of beats; None without a clock grid
    period_s: float                # the same in seconds (with no grid, the one to use)
    anchor_beat: Optional[float]   # the clock's beat number at at_ms (a bar line): phase 0 leaves the start then
    at_ms: float                   # when it takes effect (host time, >= the screens' lead ahead, on a bar line)
    source: str                    # FLOW_SOURCES
    why: str                       # short words: "the song builds", "rising melody", "a drop"
    screens: dict[str, "ScreenFlow"]   # the renderer's screen name (FixtureInfo.screen) -> the flow in its plane


class LightEffectRun(TypedDict):
    """One effect running over a group's scene (light_effects.yaml), fired on the music's events."""
    id: str                        # "fx:<n>": +1 per effect fired
    name: str
    label: str
    at_ms: float                   # host time: the event's line
    until_ms: Optional[float]      # None: while its phase holds (then set to when it left, and dropped after its fade)
    strength: float                # 0..1
    why: str                       # "the drop", "a new section"


class LightTransitionRun(TypedDict):
    """How a group crosses to its new scene (light_transitions.yaml), at the screens' own moment."""
    name: str
    kind: str                      # LIGHT_TRANSITION_KINDS
    params: dict[str, Any]         # resolved: {beats, seconds, out_s, in_s, in_at_ms, wave_beats, dark_move}
    at_ms: float


class LightProgram(TypedDict):
    """LookData.light_programs[group]: what one light group runs (docs/LIGHTS.md §7.2). Until ``at_ms`` the group still
    shows what it showed: the program is published ahead so every output switches at its own photon time."""
    group: str
    label: str
    members: list[str]
    caps: list[str]
    scene: Optional[str]           # light_scenes.yaml; None: no scene fits (the engine's compat path)
    scene_label: Optional[str]
    scene_why: Optional[str]
    effects: list[LightEffectRun]
    transition: LightTransitionRun
    flow: FlowData                 # the room's, or for a scene that doesn't honour flow its first tagged dir with the
                                   # room's speed, spread and timing
    since_ms: float                # when the scene was picked
    at_ms: float                   # when it lands (the look change's at_ms: the screens' too)
    why: str


class GroupInfo(TypedDict):
    """light_scenes.group_members: one light group (show.yaml lights.groups; "*" holds the lights no group takes)."""
    id: str
    label: str
    members: list[str]             # lamp fixture ids, in the selector's order
    caps: list[str]                # the union of the members' attrs
    follows: Optional[str]         # runs that group's program
    dark_level: Any                # "floor" | 0..1: where fade_dark goes
    member_caps: NotRequired[dict[str, list[str]]]   # each member's attrs (an entry's needs: one member has them all)


class ComposeView(TypedDict):
    """LookData.compose (README S5.18; compose.py): every enabled appliance's plan, a share of today's value per
    dimension (compose.DIMS), and the balance that made it. observe: published only; drive: the outputs take it."""
    mode: str                      # compose.MODES: observe | drive
    limits: str                    # "feel" (the vibe's compose block) | "defaults" (show.yaml director.compose)
    appliances: dict[str, dict[str, float]]   # enabled id -> {picture, level, pace, pulse, depth, change}
    nominal: dict[str, dict[str, float]]      # compose.CLASSES -> the same for a class's own weight (room.direction and
                                   # the lamps without a plan of their own take these)
    balance: dict[str, Any]        # {loud, range, range_lu, lift, s: {dim}, quiet: {dim}, h: {dim}, liveness, fade}:
                                   # s, quiet and h for the lead class (screens)
    enabled: list[str]             # the enabled appliances: live screen ids, then members following the show (dmx)


class LookHit(TypedDict):
    """LookData.hit: the drop's unison hit (§8.5) on the lamps, never a screen. palette_fn never carries it: each
    output takes it by its driver's caps (§5.4). An output that takes hits (DMX) shows ``lamps`` at once for
    [t0, t1); one that doesn't (Hue) shows it at once only when ``predicted`` (an armed drop line), else as a slow
    swell (attack ``swell_ms`` up, the same down after t1)."""
    t0_ms: float                   # host time (CLOCK_MONOTONIC ms), as LookData.t_ms
    t1_ms: float
    predicted: bool                # an armed drop line (structure predicted it), not a drop nobody saw coming
    swell_ms: float                # show.yaml director.build.hit_swell_ms (§5.4: >= 300 ms)
    lamps: dict[str, list[float]]  # lamp id -> rgb 0..1 at full value (the look's accent through the lamp's role)


class SectionRef(TypedDict):
    id: int                        # +1 per section (Stage 3: per look change; P3: the novelty detector's)
    label: Optional[str]           # the listener's section word when it agrees, else None
    phase: float                   # 0..1 through the section (bars_in_section / expected), 0 when unknown
    bars_in_section: int


class DirectionData(TypedDict):
    """room.direction: the renderer's direction vocabulary (stream_service/director.py validate_direction: unknown keys
    are ignored there) plus arousal/tension/section/density/groove. Numbers 0..1. intensity is binding depth in the
    renderer, not brightness: 1.0, or in compose drive mode the composer's music depth (README S5.18)."""
    t_ms: float
    seq: int
    source: str                    # "show_service"
    note: Optional[str]            # "show_service · <vibe>"
    vibe: Optional[str]
    energy: float
    pace: float
    intensity: float
    change_rate: float
    moods: list[str]
    genre: Optional[str]
    transition_style: Optional[str]
    palette_family: Optional[str]
    fx: dict[str, Any]             # {"density", "rate", "stages": None, "moods": list | None} (renderer's fx)
    arousal: float
    tension: float                 # music.harmony tension, smoothed
    section: Optional[SectionRef]
    density: float                 # onset density
    groove: float                  # drive x beat confidence
    hold: bool                     # a person holds the look (Keep this look, or their change pending; owner 17 Oct:
                                   # the renderer's directors change nothing, effects included, while it is true)
    genres: dict[str, float]       # soft genre: vocab genre word -> share (the top few, summing to 1; {} with none):
                                   # the local ML's style shares (music.context genre.words), else {genre: 1.0}
    valence: Optional[float]       # 0 (sad, dark) .. 1 (happy): music.context valence (None without the ML)
    organic: Optional[float]       # 0 (synthetic) .. 1 (acoustic): music.context acoustic (None without the ML)
    beat_lock: bool                # the vibe allows it and the clock's gate is "beat"
    loose: bool                    # the card's "following loosely": the vibe wants the beat, the clock is unsure
    flow: "FlowData"               # docs/LIGHTS.md §4: the room's direction of travel (still while neutral); published at
                                   # once on a new flow.id
    # README S5.18: only while show.yaml director.compose.mode is drive (observe leaves the message as it was)
    pulse_depth: NotRequired[float]   # the screens' beat-accent depth (neutral 1.0 = today; quiet passages soften it)
    peak: NotRequired[dict[str, Any]]  # the build/drop words: {energy, moods, transition_style} (the renderer's build
                                   # pick reads them instead of its own fixed peak, from step 3)


#: DirectionData keys present only in drive mode (README S5.18).
DIRECTION_DRIVE_KEYS: tuple[str, ...] = ("pulse_depth", "peak")


EVENT_KINDS: tuple[str, ...] = ("look_change", "section_change", "phrase", "build", "drop_armed", "drop", "accent",
                                "cut")


class EventData(TypedDict):
    """room.event: stamped at ``at_ms`` (acoustic, the clock's timeline), published when decided."""
    kind: str                      # EVENT_KINDS
    id: str                        # stable per occurrence (17 Oct, agreed with 04): a re-send (a moved line, a re-armed
                                   # end, a call-off) has the same id and replaces the earlier one. structure: "<kind>:
                                   # <track id>" for TRACK_EVENT_KINDS, "<kind>:<track id>:<section id>" for
                                   # section_change and drop, "<kind>:<track id>:<n>" (a per-track count) for build,
                                   # breakdown and drop_armed; the director: "look_change:<look_id>"
    at_ms: float
    t_ms: float                    # when it was published
    seq: int
    look_id: NotRequired[str]
    beat: NotRequired[float]       # the clock's beat number at at_ms
    bar: NotRequired[int]
    phrase: NotRequired[int]
    strength: NotRequired[float]
    detail: NotRequired[str]
    # docs/LIGHTS.md §7.3, §9 (18 Oct): on look_change and the track events, so the screens cross with the lights
    flow_id: NotRequired[int]      # the FlowData.id this change goes with
    sweep_beats: NotRequired[float]   # a sweep: each appliance starts at at_ms + its flow phase x this many beats


#: artnet_out's palette_fn (README "artnet_out.py"): (t_s) -> (r, g, b) | {device id | screen | "*": (r, g, b)} | None,
#: 0..1, called on show-artnet at <= 44 Hz with the frame's evaluation time. The director's returns a dict built on
#: show-brain and swapped by reference (only a crossfade's lerp happens in the call); None until a vibe is set, so
#: artnet_out keeps its fixed P0 colour.
PaletteFn = Callable[[float], Union[tuple[float, float, float], dict[str, tuple[float, float, float]], None]]

# -- nudges and chips (§8.4) -----------------------------------------------------------------------------------------------
STEP_DIRS: tuple[str, ...] = ("calmer", "wilder")
DIRECTOR_ACTIONS: tuple[str, ...] = ("next", "keep", "house", "low")
CLOCK_ACTIONS: tuple[str, ...] = ("tap", "half", "double", "one")      # POST /api/v1/action -> the clock (clock.py)
NUDGE_KINDS: tuple[str, ...] = ("steps", "palette", "moods", "keep", "house", "low")


class ChipData(TypedDict):
    """An active nudge as the card shows it (× clears it: DELETE /api/v1/nudge/{id})."""
    id: str                        # = its kind for the singletons ("steps", "palette", "keep", ...); "describe-<n>"
    kind: str                      # NUDGE_KINDS
    label: str                     # page words: "Calmer ×2", "Palette: Ember", "Keep this look · 12 min"
    value: Any
    expires_in_s: Optional[float]  # keep only; None = until the mode changes or Back to auto


class StepsView(TypedDict):
    value: int                     # -max..+max (negative = calmer)
    min: int
    max: int
    at_limit: bool


# -- inputs the brain reads (filled by __main__/autopilot.RoomLink; read by reference) -------------------------------------
class PianoView(TypedDict):
    """From __main__'s show-rx snapshot (every 0.5 s): the voice piano."""
    present: bool
    stream: Optional[str]          # the voice stream
    source: Optional[str]          # "live" | "test"
    keys_down: int
    damper: float
    last_note_age_s: Optional[float]
    velocity: float                # 0..1: strike velocities /127, EMA tau 2 s (the "dynamics" follow input from MIDI)
    notes_per_s: float             # strikes per second over the last 4 s


class NoteOn(TypedDict):
    """One accepted note-on as situations.NoteTap logs it (after the wire seq dedupe)."""
    t_s: float                     # arrival (monotonic s); counting needs no acoustic time
    stream: str
    channel: int                   # 1..16
    pitch: int
    vel: int


class TransitionView(TypedDict):
    id: str
    from_: Optional[str]           # status "from" (renamed: a keyword)
    to: str
    source: str
    reason: Optional[str]
    status: str                    # running | done | partial | failed | cancelled | rolled_back (room's words)


class MemberView(TypedDict):
    believed: Any
    confidence: str
    age_s: Optional[float]
    sent_age_s: Optional[float]    # Auto ignores what the room itself just changed


class RoomView(TypedDict):
    """autopilot.RoomLink.view(): the slice of room_service the brain uses, refreshed from WS /api/v1/events (status
    <= 4/s) or, while that is down, GET /api/v1/status every 5 s."""
    reachable: bool
    age_s: Optional[float]         # since the last status
    error: Optional[str]
    current: Optional[str]         # mode.current
    target: Optional[str]          # mode.target (None when settled)
    since_age_s: Optional[float]
    transition: Optional[TransitionView]       # running, else None
    last_transition: Optional[TransitionView]
    last_transition_id: Optional[str]          # for expect
    autopilot_on: Optional[bool]               # subsystems.autopilot.believed == "on"; None = unknown
    revision: Optional[str]                    # status.config.revision
    modes: dict[str, dict]                     # name -> {"label": str, "autopilot": dict | None} (GET /api/v1/room/config)
    order: list[str]                           # mode names in file order
    autopilot: Optional[dict]                  # modes.json top-level "autopilot" as written
    members: dict[str, MemberView]             # "<subsystem>.<member>" -> view, every member in status
    screens: dict[str, dict]                   # screen id -> {"layer": art|live, "phase": str, "cutting": bool}
    stored_restore: dict[str, Any]             # status.stored["_restore"]: target -> stored value (read-only)


# -- situations (§8.9) ---------------------------------------------------------------------------------------------------
SITUATIONS: tuple[str, ...] = ("piano_playing", "piano_idle", "music_audible", "sonos_playing", "genre_in",
                               "energy_above", "talking", "quiet", "between", "light_below", "light_above",
                               # README S5.20 (17 Oct): the listener's words for Auto
                               "mood_in", "building", "track_changed")
COMBINATORS: tuple[str, ...] = ("all", "any", "not")
SituationSource = Literal["midi", "sonos", "audio", "listener", "time", "light"]
#: §8.9 priority; the room's light level is the setting, like the time of day (README S5.19), so it ranks with time
SITUATION_RANK: dict[str, int] = {"midi": 0, "sonos": 1, "audio": 2, "listener": 3, "time": 4, "light": 4}


class SituationDef(TypedDict):
    """One word of the vocabulary (GET /api/v1/situations; the room adapter passes it to the Auto card)."""
    id: str                        # SITUATIONS
    label: str                     # page words: "the piano is playing"
    description: str
    source: str                    # SituationSource
    rank: int                      # SITUATION_RANK[source]
    params: dict[str, dict]        # param -> JSON schema (+ x-ui, default from show.yaml); for_s is on every word.
                                   # x-ui: label, unit, unset_label, required (every param), ends (0..1 and loudness
                                   # sliders), enum + enum_labels where the choices are known (the light sensors)
    available: bool                # its inputs exist now (False: it reads unknown)
    unavailable: str               # page words while it can't be sensed: "The piano isn't connected yet" (S5.19)


class SituationReading(TypedDict):
    value: Optional[bool]          # None = unknown (never acted on)
    since_s: Optional[float]       # how long it has had this value
    via: str                       # what decided it ("midi", "dsp", "classifier", "sonos", "clock", ...)
    evidence: dict[str, Any]       # numbers for the log and the console ({"notes": 14, "span_s": 8.1, ...})


# -- Auto (§8.9) -----------------------------------------------------------------------------------------------------------
AUTOPILOT_STATES: tuple[str, ...] = ("off", "watching", "acting", "holding", "paused", "backoff", "blind")
AutopilotStateName = Literal["off", "watching", "acting", "holding", "paused", "backoff", "blind"]
AUTOPILOT_SOURCE = "autopilot"     # PUT /api/v1/mode source for Auto's own switches
UNDO_SOURCE = "auto-undo"          # PUT /api/v1/mode source for a person's Undo (POST /api/v1/autopilot/undo)
#: modes.json keys aa validates (room_service/roomstate/schema.py AUTOPILOT_KEYS / MODE_AUTOPILOT_KEYS)
AUTOPILOT_GLOBAL_KEYS: tuple[str, ...] = ("on", "quiet_hours", "min_dwell_s", "max_switches", "backoff_s", "hold",
                                          "chips")
AUTOPILOT_MODE_KEYS: tuple[str, ...] = ("enter_when", "otherwise_suggest_when", "from", "leave_when", "leave_to",
                                        "skip")

QuietHours = TypedDict("QuietHours", {"from": str, "to": str})     # "HH:MM" local time; from > to wraps midnight


class MaxSwitches(TypedDict):
    count: int
    per_s: float
    pause_s: float


class HoldSettings(TypedDict):
    until: dict                    # a condition (situations grammar): the activity has ended
    max_s: float                   # a hold never lasts longer
    correction_s: float            # a person's change within this of an Auto switch is a correction
    suppress_s: float              # a corrected (rule, mode) stays suppressed at most this long


class ChipSettings(TypedDict):
    switch_s: float                # the "Auto → Piano · … · Undo" chip
    suggest_s: float               # a "Switch to Party?" tile


class AutopilotSettings(TypedDict):
    """modes.json top-level "autopilot", resolved: a key that is unset takes its design default (README; the GET
    shows which: AutopilotState.settings[key].source "modes.json" | "default")."""
    on: bool                       # room's autopilot.main wins at run time (aa's adapter keeps it)
    quiet_hours: Optional[QuietHours]
    min_dwell_s: float
    max_switches: MaxSwitches
    backoff_s: float
    hold: HoldSettings
    chips: ChipSettings


class AutoAnnouncement(TypedDict):
    """EV_AUTO's event and AutopilotState.chip/suggest's source: every switch is said, every time."""
    kind: str                      # "switch" | "suggest" | "hold" | "undo" | "pause" | "resume" | "backoff" | "failed"
    text: str                      # "Auto → Piano · the piano is playing"
    mode: Optional[str]
    previous: Optional[str]
    rule: Optional[str]            # "piano/enter_when"
    transition_id: Optional[str]
    undo: bool                     # the chip offers Undo
    expires_in_s: Optional[float]


class AutopilotState(TypedDict):
    """GET /api/v1/autopilot (and state's "autopilot")."""
    on: Optional[bool]
    state: str                     # AUTOPILOT_STATES
    why: Optional[str]             # code: "quiet_hours" | "flap" | "person" | "undo" | "correction" | "failed" |
                                   # "room_unreachable" | "config_error" | None
    line: str                      # the /room Auto line: "Auto · watching", "Auto paused · you chose Disco · Resume"
    since_s: float
    until_in_s: Optional[float]
    entered: Optional[dict]        # {"mode", "previous", "rule", "at_age_s", "transition_id"}: a mode Auto entered
    hold: Optional[dict]           # {"mode", "by", "since_age_s", "until_in_s"}
    last: Optional[dict]           # {"age_s", "from", "to", "reason", "transition_id", "status", "source"}
    chip: Optional[AutoAnnouncement]
    suggest: Optional[AutoAnnouncement]
    suppressed: list[dict]         # [{"mode", "rule", "until_in_s"}]
    candidates: list[dict]         # [{"mode", "kind": enter|suggest|leave, "rule", "held_s", "needs_s", "blocked"}]
    settings: dict[str, dict]      # key -> {"value", "source": "modes.json" | "default"}
    room: dict                     # {"reachable", "revision", "current", "transition_running"}


# -- Auto's rules in page words (README S5.19; the Room page's Auto editor, 2f) ----------------------------------------------
#: a node's path in a mode's autopilot block: "enter_when", "enter_when.any[1]", "leave_when.all[0].not", a word of a
#: several-word object "enter_when.piano_playing", "from", "leave_to"; "" is the block itself
class RuleProblem(TypedDict):
    path: str
    text: str                      # plain words: "The piano isn't connected yet"
    level: str                     # "error" (the rule never holds, or the room would refuse it) | "warn"


class RuleView(TypedDict):
    """One mode's Auto rules (GET /api/v1/autopilot/rules ``modes[]``; POST /api/v1/autopilot/check answers the same
    keys plus ``ok`` and ``unknown_words``)."""
    mode: Optional[str]
    label: Optional[str]
    enter: Optional[str]           # the whole rule's sentence: "the piano is playing, for at least 10 s"; None: no rule
    suggest: Optional[str]
    leave: Optional[str]
    summary: str                   # the mode's Auto in one line ("Auto switches to it when …")
    from_: Optional[list]          # key "from": the modes it switches from, None = any mode
    leave_to: Optional[str]        # "previous" (the default) or a mode, None while it has no leave rule
    problems: list[RuleProblem]
    available: bool                # every word it uses can be sensed now
    live: dict[str, Optional[bool]]   # path -> what that condition node reads now (for_s left out; None = unknown)
    nodes: dict[str, str]          # path -> that node's sentence (conditions, "from", "leave_to")
    now: dict[str, Optional[bool]]    # {"enter", "suggest", "leave"}: each rule's root, as live has it


# -- the listener (§7) and Describe it (§7.4) ------------------------------------------------------------------------------
INSIGHT_CONTENT: tuple[str, ...] = ("music", "speech", "mixed", "silence", "noise")
SECTION_LABELS: tuple[str, ...] = ("intro", "verse", "pre_chorus", "chorus", "bridge", "breakdown", "build", "drop",
                                   "solo", "outro", "unknown")
TRAJECTORY_STATES: tuple[str, ...] = ("building", "peak", "sustaining", "releasing", "steady", "winding_down")
# README S5.22 (owner, 18 Oct): the listener's recommended approach, how the piece will end, and what it sees coming
APPROACH_CHANGE: tuple[str, ...] = ("slow", "medium", "fast")          # how often the looks should change
APPROACH_TRANSITIONS: tuple[str, ...] = ("gentle", "lively", "wild")   # vocab.yaml's transition_style words
ENDINGS: tuple[str, ...] = ("cold", "fade", "segue", "unknown")        # a sudden stop | fades out | runs into the next
COMING_KINDS: tuple[str, ...] = ("chorus", "drop", "climax", "breakdown", "key_lift", "band_in", "vocal_in", "stop",
                                 "outro", "end", "new_section")
COMING_SIGNS: tuple[str, ...] = ("louder", "low_in", "busier", "stop_return", "timbre_change", "key_change",
                                 "drums_in", "drums_out", "vocal_in", "vocal_out", "phrase_line")
COMING_SIZES: tuple[str, ...] = ("small", "medium", "big")


class InsightData(TypedDict):
    """music.insight: one listener verdict as validated (§7.3 output; README S5.22), with its window. The model's own
    words are checked against vocab.yaml and the tuples above after the call (lenient): a word outside its list becomes
    "unknown" (genre, ending), the field's neutral word, or is dropped (moods, signs)."""
    t_ms: float                    # publish time
    window: dict[str, float]       # {"t0_ms", "t1_ms"}: the clip's acoustic span
    segment_id: Optional[int]      # structure's section at the clip's end (None without structure)
    stream: str
    model: str
    latency_ms: float
    content: str                   # INSIGHT_CONTENT
    character: str                 # <= 12 free words ("soft late-night jazz trio"); "" when none
    genre: dict[str, Any]          # {"label": vocab genre | "unknown", "detail": str, "confidence": 0..1}
    mood: dict[str, Any]           # {"words": [<= 3 vocab moods], "confidence"}
    approach: dict[str, Any]       # {"intensity": 0..1 | None, "change": APPROACH_CHANGE | None,
                                   #  "transitions": APPROACH_TRANSITIONS | None, "confidence"}
    trajectory: dict[str, Any]     # {"state": TRAJECTORY_STATES, "confidence"}
    ending: str                    # ENDINGS
    coming: list[dict[str, Any]]   # <= 2: {"kind": COMING_KINDS, "eta_min_s", "eta_max_s" (from the clip's end),
                                   #  "signs": [COMING_SIGNS], "size": COMING_SIZES, "confidence"}
    track_change: bool
    why: str                       # <= 12 words


class Beliefs(TypedDict):
    """listener.beliefs(): the fused, decaying view the director (L2), the cue engine, the card and situations read
    (§7.1; README S5.22)."""
    state: str                     # "none" | "fresh" | "holding" | "fading"
    age_s: Optional[float]         # since the last verdict
    genre: Optional[dict]          # {"word", "conf"}: changes only on two agreeing reads (or a track change)
    moods: list[dict]              # [{"word", "conf"}]: vocab.yaml visual moods (feel through music_moods), top 3
    feel: list[dict]               # [{"word", "conf"}]: the listener's own music_moods words, conf decays (45 s)
    trajectory: Optional[dict]     # {"state", "conf"}
    content: Optional[str]
    character: Optional[str]       # the latest verdict's (music only)
    approach: Optional[dict]       # {"intensity", "intensity_conf", "change", "change_conf", "transitions",
                                   #  "transitions_conf", "conf" (the least of the three)}: agreement over time, as moods
    ending: Optional[str]          # ENDINGS less "unknown": the latest word that said something, for ENDING_HOLD_S
    coming: list[dict]             # the latest verdict's forecasts still ahead: {"id", "kind", "t0_s", "t1_s" (acoustic
                                   #  monotonic s), "in_s": [lo, hi] from now, "signs", "size", "conf"}


class ListenerView(TypedDict):
    """GET /api/v1/listening and state's "listener"."""
    on: bool                       # the AI listening switch (persisted; default False until the owner turns it on)
    mode: bool                     # room_service's show.ai as it last PUT it (persisted; False until set): shown,
                                   # never a gate since 21 Oct. The API's answers add "boot_id"
    effective: bool                # a clip would go now
    why: Optional[str]             # None while it would, else ``status``
    status: str                    # owner, 21 Oct: one of listener.STATUS_WORDS ("Listening", "Paused: quiet",
                                   # "Paused: people talking", "Paused: can't check for talking", "Off")
    detail: Optional[str]          # diagnostics only: "no model configured", "no sound coming in", "the mic is off"
    history: list[dict]            # the last round trips and skips, newest first (listener.HISTORY_N): {t_ms, at,
                                   # ok, call, model?, latency_ms?, cost_usd?, content?, character?, genre?, moods?,
                                   # coming?, why?, error?, detail?, count?}
    stream: Optional[str]
    next_in_s: Optional[float]
    cadence_s: float               # seconds between call starts now (fast_cadence_s while something develops)
    fast: Optional[str]            # README S5.22: why the fast cadence is on ("a build", "a forecast window", "the song's
                                   # end is near", "a new section"), else None
    in_flight: int                 # calls in flight now (at most show.yaml listener.max_in_flight)
    calls_today: int
    spend_today_usd: float
    budget_usd: Optional[float]
    model: Optional[str]           # the model it would call now (the first usable one, after discovery)
    last: Optional[InsightData]
    errors: int
    provider: Optional[str]        # README S5.20: who that model is with, in page words ("Google", "OpenAI")
    est_evening_usd: Optional[float]   # estimate.per_evening_usd (shown even while the switch is off)
    estimate: Optional[dict]       # {"per_call_usd", "per_hour_usd", "per_evening_usd", "evening_h", "cadence_s" (the
                                   #  blend the arithmetic uses), "steady_cadence_s", "fast_cadence_s", "fast_share",
                                   #  "fast_share_basis": "measured" | "assumed", "clip_s", "model", "provider",
                                   #  "basis": "ledger" | "price table"}, or None (no usable model, or its cost isn't
                                   #  known)
    character: Optional[str]       # README S5.22: the beliefs' character line ("soft late-night jazz trio")
    approach: Optional[dict]       # {"intensity", "change", "transitions", "conf"}: the listener's recommended approach
    armed: list[dict]              # the cue engine's sheet (cues.py): [CueLine]


class CueLine(TypedDict):
    """One armed cue on the cue sheet (cues.Cues.sheet; README S5.22), for the card's listening block."""
    kind: str                      # COMING_KINDS, or a standing cue: "stop" | "fade" | "segue"
    words: str                     # a plain line: "Big change at the chorus, likely in 10-25 s", "Ready: slow fade if
                                   # the music fades"
    window_s: Optional[list[float]]   # [lo, hi] seconds from now (a forecast), None (a standing cue)
    size: Optional[str]            # COMING_SIZES (a forecast), None (a standing cue)


class DescribeIntent(TypedDict):
    """What Describe it may set (§7.4: only what the card can set, within the mode), from the LLM's forced tool or the
    local keyword match."""
    steps: int                     # -3..3, relative
    palette: Optional[str]         # a catalog name, "art", or None
    moods: Optional[list[str]]     # vocab moods, or None
    keep: bool
    switch_mode: Optional[str]     # a mode name to OFFER ("Switch to Party?"), never applied
    reply: str                     # one short sentence for the card


class DescribeResult(TypedDict):
    """POST /api/v1/describe's answer and EV_DESCRIBE's result. Never an error: a failed LLM falls back to keywords."""
    ok: bool                       # always True (degrade, never refuse)
    via: str                       # "llm" | "keywords"
    text: str
    reply: str
    applied: list[ChipData]        # the chips it set (undoable)
    suggest: Optional[dict]        # {"mode", "label"}: the one-tap tile, or None
    working_ms: float


# -- test sources (sources.py; POST/DELETE/GET /api/v1/test) -----------------------------------------------------------------
class PieceInfo(TypedDict):
    id: str                        # piano: piano_replay ids ("file:<name>.mid" or a phrase); audio: "file:<name>"
    label: str
    kind: str                      # piano: "file" | "phrase"; audio: "file"
    genre: NotRequired[str]        # audio: from test-music/index.json
    artist: NotRequired[str]
    license: NotRequired[str]
    duration_s: NotRequired[float]


class SourceStatus(TypedDict):
    source: str                    # TEST_SOURCES
    state: str                     # TestState
    piece: Optional[str]
    label: Optional[str]
    stream: str                    # "test-piano" | "test-audio"
    sender: str                    # its show.yaml senders entry
    pid: Optional[int]
    since_s: Optional[float]
    error: Optional[str]           # the child's last log lines when it failed


# -- the ring (§3.3; ring.py) ------------------------------------------------------------------------------------------------
RING_SECONDS = 120.0
RING_FS = 16000                    # mono int16: the listener's clips and later the slow worker
RING_MAX_STREAMS = 3               # the delivered (active) streams; the oldest-written ring is dropped beyond this

# -- the card (§10.1; api.py builds it from the components' snapshots) ---------------------------------------------------------
class CardData(TypedDict):
    """GET /api/v1/card, EV_WELCOME's card, and what EV_CARD patches. Everything the Music card draws, in page words."""
    hearing: dict[str, Any]        # {"text": "soul · relaxed" | "Quiet" | "Talking" | "No sound coming in" | api._input_words,
                                   #  "level": 0..1, "loose": bool, "stream": str | None, "test": bool}
    vibe: dict[str, Any]           # {"name": str | None, "label": str, "known": bool}
    steps: StepsView
    palette: dict[str, Any]        # {"pick": "auto"|"art"|name, "current": name, "choices": [PaletteChoice] (Auto,
                                   #  From the art, then the vibe's first 5)}
    look: dict[str, Any]           # {"id": str, "keep_in_s": float | None, "next_pending": bool}
    house: bool
    low: bool
    chips: list[ChipData]
    listening: dict[str, Any]      # {"on": bool, "effective": bool, "why": str | None, "mode": bool (the mode's own
                                   #  permission, show.ai; owner 20 Oct), "spend_today_usd": float,
                                   #  "est_evening_usd": float | None, "provider": "Google" | "OpenAI" | None,
                                   #  "character": str | None, "approach": {...} | None, "armed": [CueLine]}
                                   #  (README S5.20: the price shows even while the switch is off; S5.22: the
                                   #  listener's character and approach, and the cue sheet)
    describe: dict[str, Any]       # {"state": "idle" | "working" | "done", "text": str | None, "reply": str | None,
                                   #  "suggest": {"mode", "label"} | None}
    auto: dict[str, Any]           # {"on": bool | None, "state", "line", "chip": AutoAnnouncement | None,
                                   #  "suggest": AutoAnnouncement | None}
    tests: dict[str, SourceStatus]   # by source (the console's; the card ignores it)
    mic_state: Optional[dict[str, Any]]   # README S5.16: GET /api/v1/mic's body ({on, muted, wanted, why, claims, mode,
                                          #  streams: {capturing, off}, error}); None without mic.py


# -- the brain's wiring (fields only; brain.py and __main__ fill it; components read it) -------------------------------------
Notify = Callable[[str, Optional[dict]], None]     # (kind, payload): "card" (recompute now), "auto", "describe"; any
                                                   # thread; api.py's events hub
ClockAction = Callable[[str, float], None]         # (CLOCK_ACTIONS word, t_s): clock.tap / half / double / one


@dataclass
class BrainContext:
    """What a Stage-3 component may reach. Every slot is optional: None means that piece is missing, and the component
    degrades (the neutral look, unknown situations, no clips) instead of failing. Slots are callables that read a
    snapshot by reference or do one thread-safe thing; none of them blocks. Filled by __main__ (engine side) and by
    brain.py after every component is built (component side)."""
    publish: Publish                                                   # bus.publish (any thread)
    # engine side (show-rx owns the objects; these read by reference)
    features: Optional[Callable[[], Optional[FeaturesData]]] = None   # dsp.Features.latest
    clock: Optional[Callable[[], Optional[ClockData]]] = None         # clock.Clock.snapshot
    harmony: Optional[Callable[[], Optional[HarmonyData]]] = None     # harmony.Harmony.latest
    piano: Optional[Callable[[], PianoView]] = None                   # __main__'s 0.5 s snapshot
    health: Optional[Callable[[], dict]] = None                       # Receiver.health
    rings: Any = None                                                  # ring.Rings (listener reads clips)
    notes: Any = None                                                  # situations.NoteTap (reader side)
    clock_action: Optional[ClockAction] = None
    section_change: Optional[Callable[[], None]] = None               # VirtualMaestro.section_change (GOBO +1)
    set_look_out: Optional[Callable[..., None]] = None                # VirtualMaestro.set_look (hue_depth, beat_lock, reset,
                                                                       # plan: README S5.18's drive mode)
    set_gate_ref: Optional[Callable[[float], None]] = None            # Features.gate_ref_lufs = lufs
    set_senders: Optional[Callable[[tuple[Sender, ...]], None]] = None   # Receiver.set_senders
    set_priority: Optional[Callable[[tuple[str, ...]], None]] = None     # Receiver.set_priority
    static_senders: tuple[Sender, ...] = ()                           # --sender / SHOW_SENDERS (kept as extras)
    # brain side (brain.py fills these after every component is built)
    loop: Any = None                                                   # the show-brain asyncio loop
    settings: dict = field(default_factory=dict)                      # show.yaml, parsed (swapped on reload)
    room: Optional[Callable[[], RoomView]] = None                     # autopilot.RoomLink.view
    room_get: Optional[Callable[[str], Any]] = None                   # async (path) -> (status, bytes): RoomLink.get
    room_put: Optional[Callable[[str, dict], Any]] = None             # async (path, body) -> (status, dict): RoomLink.put
    room_patch: Optional[Callable[[str, dict], Any]] = None           # async (path, body) -> (status, dict): RoomLink.patch
                                                                       # (Stage 4: the director's screens palette)
    situations: Optional[Callable[[], dict[str, SituationReading]]] = None   # situations' snapshot (default params)
    beliefs: Optional[Callable[[], Optional[Beliefs]]] = None         # listener.beliefs
    vibe: Optional[Callable[[], Optional[VibeSettings]]] = None       # director: the effective vibe now
    target: Optional[Callable[[], Optional[VibeTarget]]] = None       # director: the show target now
    engaged: Optional[Callable[[], bool]] = None                      # director: a nudge is active (Auto won't leave)
    on_mode_change: list[Callable[[Optional[str], str], None]] = field(default_factory=list)  # RoomLink calls each
                                                                       # (old, new) on show-brain when mode.current changes
    notify: Optional[Notify] = None                                   # api.py
    persist: Optional[Callable[[], None]] = None                      # brain.dirty(): write state.json soon
    # Stage 4 additions (README "Stage 4"; the shapes are in the Stage-4 block below). None or empty: that piece is
    # missing, and its readers degrade (README S4.3 "Without the worker").
    # engine side (__main__)
    rhythm: Optional[Callable[[], Optional["RhythmData"]]] = None     # dsp.Features.rhythm: the 10 Hz medium lane
    ml_feed: Any = None                                                # mllink.MLFeed (show-rx writer; MLLink attaches
                                                                       # the worker's stdin to it)
    clock_correct: Optional[Callable[..., None]] = None                # clock.Clock.apply_integer_correction (any thread)
    # brain side (provided by components)
    ml: Optional[Callable[[], "MLView"]] = None                        # mllink.MLLink.view
    music_context: Optional[Callable[[], Optional["MusicContext"]]] = None   # structure.Structure.context
    on_music_event: list[Callable[[dict], None]] = field(default_factory=list)   # structure calls each with every
                                                                       # MusicEventData it publishes (show-brain, guarded);
                                                                       # brain.add appends what components provide
    # Stage 5 additions (README "Stage 5"; the shapes are in the Stage-5 block below). None or empty: that piece is
    # missing, and its readers degrade (the director keeps patch.yaml's lamps; no screen-pick hold).
    # brain side (provided by components)
    rig: Optional[Callable[[], Optional["RigView"]]] = None           # outputs.Rig.view: the fixture registry (any thread)
    room_boot: Optional[Callable[[], Optional[str]]] = None           # autopilot.RoomLink: room status.service.started_at
                                                                       # (str), None until a status; changes per room boot
    on_room_event: list[Callable[[str, dict], None]] = field(default_factory=list)   # RoomLink calls each with (type,
                                                                       # data) for every room WS event other than status
                                                                       # and mode.transition (screen.look, ...), on
                                                                       # show-brain, guarded; brain.add appends
    # Song ends (README S5.17; SHOW_ENGINE §8.5 "Song ends"). None: no director, and the track events carry no
    # "transition" (the renderer keeps the mode's default).
    # README S5.22: the cue engine (cues.py) and the hold it respects
    held: Optional[Callable[[], bool]] = None                          # director: a person holds the look (Keep this
                                                                       # look, or their change pending): room.direction's
                                                                       # ``hold`` (any thread)
    cue_sheet: Optional[Callable[[], list]] = None                     # cues.Cues.sheet: [CueLine] (any thread)
    track_transition: Optional[Callable[[dict, dict], Optional[dict]]] = None   # director.Director.track_transition:
                                                                       # structure calls it (show-brain, synchronous,
                                                                       # guarded) while it builds a TRACK_EVENT_KINDS
                                                                       # event, with (the event, TrackFacts); returns
                                                                       # {"transition": TrackTransition} | {"held": True}
                                                                       # | None, which structure puts on the event


class Component(Protocol):
    """A Stage-3 piece the brain runs. Every method runs on show-brain unless it says so; none blocks (I/O is an asyncio
    task on ctx.loop). A module exposes ``attach(ctx: BrainContext) -> Component | list[Component]``."""
    name: str                      # "director" | "situations" | "autopilot" | "room" | "sources" | "listener"
    tick_hz: float                 # 0 = no tick

    def provides(self) -> dict[str, Any]: ...          # BrainContext brain-side slots it fills, {"room": self.view, ...};
                                                       # "on_mode_change" is appended, the rest set (main thread, at add)
    def configure(self, settings: dict) -> None: ...   # the whole parsed show.yaml: at start and on every change
    def restore(self, saved: dict) -> None: ...        # before start(): what persist() returned last run
    def start(self) -> None: ...                       # once, after every component is attached and ctx is filled
    def tick(self, now_s: float) -> None: ...          # at tick_hz
    def snapshot(self) -> dict: ...                    # ANY thread: the last dict swapped in by reference
    def persist(self) -> Optional[dict]: ...           # what state.json keeps under ``name`` (None: nothing)
    def stop(self) -> None: ...                        # stop tasks, kill children it started, then return


# =====================================================================================================================
# Stage 4 (README "Stage 4"): local ML in a child worker (its own venv), dsp's medium lane, sections and phases
# (structure.py), and their wiring into the clock, situations, the listener and the director. Additions only: the
# names the five Stage-4 builders share (B7 the worker, B8 mllink and the wiring, B9 the show-rx lanes, B10 structure,
# B11 the consumers). The worker imports this file too (``python -m show_service.ml.worker`` under
# ~/.venvs/sm-show-ml), so it stays stdlib-only at import. BrainContext's Stage-4 slots are at the end of BrainContext.
# =====================================================================================================================
import struct  # noqa: E402

# -- files -------------------------------------------------------------------------------------------------------------
MODELS_DIR = pathlib.Path.home() / ".cache" / "screen-machine" / "models"   # tools/show/fetch_models.py; never git
MODELS_INDEX = MODELS_DIR / "index.json"           # fetch_models.py: {name: {file, sha256, license, ..., facts}}; the
                                                   # worker reads each model's facts (labels, "yes" class, ranges) here
ML_PYTHON = pathlib.Path.home() / ".venvs" / "sm-show-ml" / "bin" / "python"   # show.yaml ml.python's default
ML_WORKER_MODULE = "show_service.ml.worker"        # [ml.python, "-m", ML_WORKER_MODULE, "--models", <dir>,
                                                   #  "--parent", <show_service pid>, "--nice", <ml.nice>]
ML_LOG = STATE_DIR / "ml-worker.log"               # the worker's stderr (truncated at each start; mllink rotates at 1 MB)

# -- the worker's stdin: binary frames (README S4.3) -------------------------------------------------------------------
ML_PROTOCOL_V = 1
ML_FS = 16000                      # the feed: mono float32 at 16 kHz (the ring's rate, contract.RING_FS)
ML_AUDIO_MAGIC = b"SMLA"
ML_CTRL_MAGIC = b"SMLC"
#: magic 4s | version B | flags B | n H (audio: samples; control: payload bytes) | stream 16s (VBAN name, ASCII,
#: NUL-padded; zeros on control) | seg I (the feed's segment) | n0 q (16 kHz index of the payload's first sample in the
#: segment) | t0 d (acoustic host s of that sample; sample i at t0 + i / ML_FS) | dropped I (frames dropped so far)
ML_HEADER_FMT = "<4sBBH16sIqdI"
ML_HEADER_SIZE = struct.calcsize(ML_HEADER_FMT)                      # 48
ML_PIPE_ATOMIC = 4096              # Linux PIPE_BUF: a write of at most this is all-or-nothing, even with O_NONBLOCK
ML_FRAME_SAMPLES = 800             # what mllink sends: 50 ms per frame (48 + 3200 bytes)
ML_FRAME_MAX_SAMPLES = (ML_PIPE_ATOMIC - ML_HEADER_SIZE) // 4       # 1012: every frame is one atomic write
ML_CTRL_MAX_BYTES = ML_PIPE_ATOMIC - ML_HEADER_SIZE                  # 4048 bytes of UTF-8 JSON
ML_FLAG_SEGMENT = 1                # the first frame of a feed segment (a new worker, stream, rate, AUDIO_SEGMENT): n0 0
ML_FLAG_GAP = 2                    # the frame holds zero-fill (contract.AUDIO_GAP blocks)
ML_FLAG_AFTER_DROP = 4             # frames were dropped since the last one written (n0 still counts them)
ML_CMD_CONFIG = "config"           # {"cmd": "config", "v": 1, "ml": <show.yaml ml, resolved>}: after spawn and on change
ML_CMD_STOP = "stop"               # {"cmd": "stop"}: finish the current line, exit 0 (EOF on stdin does the same)
ML_LINE_MAX = 256 * 1024           # the longest stdout line mllink reads (asyncio StreamReader limit)

# -- the worker's stdout: one JSON object per line ---------------------------------------------------------------------
ML_HELLO = "hello"
ML_CED = "ced"
ML_TAGS = "tags"
ML_KEY = "key"
ML_BEATS = "beats"
ML_STATS = "stats"
ML_ERROR = "error"
ML_TYPES: tuple[str, ...] = (ML_HELLO, ML_CED, ML_TAGS, ML_KEY, ML_BEATS, ML_STATS, ML_ERROR)
ML_TASKS: tuple[str, ...] = ("ced", "tags", "key", "beats")   # show.yaml ml.tasks
ML_STATES: tuple[str, ...] = ("disabled", "starting", "running", "backoff", "failed")   # MLLink (MLView.state)
#: CED-mini's seven words: each the max of the AudioSet classes show.yaml ml.ced_groups lists for it.
CED_GROUPS: tuple[str, ...] = ("music", "speech", "conversation", "singing", "crowd", "applause", "silence")
#: Discogs' 15 top-level genres (Discogs-EffNet's style labels are "<parent>---<style>"). The worker takes the parents
#: from the model's own labels; this list is for checking show.yaml genre_map's keys. Code never compares against one.
DISCOGS_PARENTS: tuple[str, ...] = ("Blues", "Brass & Military", "Children's", "Classical", "Electronic",
                                    "Folk, World, & Country", "Funk / Soul", "Hip Hop", "Jazz", "Latin", "Non-Music",
                                    "Pop", "Reggae", "Rock", "Stage & Screen")
EMB_DIM = 1280                     # Discogs-EffNet's embedding


class MlHello(TypedDict):
    """{type: "hello"}: the first line, once the models are loaded. mllink attaches the feed only after it."""
    type: str
    v: int                         # ML_PROTOCOL_V
    pid: int
    t: float                       # time.monotonic(): one CLOCK_MONOTONIC per host, comparable with ours
    models: dict[str, str]         # catalogue name -> "ok" | "missing" | "failed: <why>" | "fake"
    tasks: dict[str, bool]         # ML_TASKS -> it can run (its model loaded)
    versions: dict[str, str]       # {"python", "numpy", "onnxruntime", "essentia"}: as imported ("" when absent)
    threads: dict[str, int]        # {"compute": 2, "intra_op": 1}
    nice: int


class MlCed(TypedDict):
    """{type: "ced"}: CED-mini over the window that ends at ``t``. Each ``*_p`` is a CED_GROUPS word, 0..1. A window
    under ml.silence_dbfs runs no model: silence_p 1, the rest 0, ``skipped`` "silence"."""
    type: str
    t: float                       # acoustic host s of the window's last sample
    window_s: float
    stream: str
    seg: int
    music_p: float
    speech_p: float
    conversation_p: float
    singing_p: float
    crowd_p: float
    applause_p: float
    silence_p: float
    skipped: NotRequired[str]
    cost_ms: float


class MlTags(TypedDict):
    """{type: "tags"}: Discogs-EffNet and its heads (show.yaml ml.tasks.tags.heads) over the window that ends at ``t``.
    A head that isn't loaded gives None (lists: []). Labels are the models' own, never vocab words."""
    type: str
    t: float
    window_s: float
    stream: str
    seg: int
    genre_parents: dict[str, float]    # every parent in the model's labels -> the max of its styles' activations,
                                       # mean over the window's patches, 0..1 (multi-label sigmoids: not a distribution)
    styles_top3: list[list[Any]]       # [["Electronic---House", 0.61], ...]: the 400-style head's three highest
    styles_top: list[list[Any]]        # the same, its ml.tasks.tags.styles_top highest (structure's genre words)
    moods_top5: list[list[Any]]        # [[label, p], ...]: the mood/theme head's raw labels
    instruments_top5: list[list[Any]]  # [[label, p], ...]
    dance: Optional[float]             # 0..1: the head's "yes" class (index.json facts say which)
    voice_p: Optional[float]           # 0..1: voice (vs instrumental)
    engagement: Optional[float]        # 0..1
    bright: Optional[float]            # 0..1: bright (vs dark)
    acoustic: Optional[float]          # 0..1: acoustic (vs not)
    arousal: Optional[float]           # 0..1: the regression's range (index.json facts) mapped onto 0..1
    emb_id: int                        # +1 per embedding in this worker run
    emb: str                           # base64 of emb_dim little-endian float16: the embedding, mean over the window's
                                       # patches, L2-normalised (mllink.decode_emb -> float32)
    emb_dim: int                       # EMB_DIM
    cost_ms: float


class MlKey(TypedDict):
    """{type: "key"}: Essentia HPCP + Key (show.yaml ml.tasks.key.profile) over the window that ends at ``t``."""
    type: str
    t: float
    window_s: float
    stream: str
    seg: int
    key: Optional[str]                 # KEY_NAMES (sharps); None without tonal content
    mode: Optional[str]                # "major" | "minor" | None
    strength: float                    # 0..1, Essentia's key strength
    cost_ms: float


class MlBeats(TypedDict):
    """{type: "beats"}: Beat This! over [window_t0, window_t1], acoustic host s. Only the interior (the window less
    ml.tasks.beats.interior_margin_s at each end) is reported. The worker never sees our clock: mllink turns this into a
    BeatVote."""
    type: str
    window_t0: float
    window_t1: float
    interior: list[float]              # [t0, t1]
    stream: str
    seg: int
    beats: list[float]                 # beat times in the interior, ascending
    downbeats: list[float]             # the subset that start a bar
    bpm: Optional[float]               # 60 / the median inter-beat interval; None with fewer than 4 beats
    bar_len: Optional[int]             # the median beats per bar between downbeats; None with fewer than 2 downbeats
    model: str                         # catalogue name
    cost_ms: float


class MlStats(TypedDict):
    """{type: "stats"}: every 10 s."""
    type: str
    t: float
    cpu_share: float                   # the worker's CPU (every thread) / wall over the last 10 s, in cores
    cpu_s: float                       # process CPU seconds since start
    rss_mb: float
    frames: int                        # audio frames read
    bytes: int
    gaps: int                          # frames flagged ML_FLAG_GAP
    holes: int                         # n0 jumps zero-filled (<= 1 s); longer ones start a segment
    drops_seen: int                    # the latest header's ``dropped``
    ring_s: float                      # audio held for the current segment
    stretch: float                     # 1..4: every interval x this (the ml.cpu_max guard)
    tasks: dict[str, dict]             # task -> {"runs", "skipped", "over_budget", "errors", "cost_ms_p50",
                                       #          "cost_ms_p95", "every_s" (now, after doubling and stretch)}
    errors: int
    last_error: Optional[str]


class MlError(TypedDict):
    """{type: "error"}: a task failed (counted; it runs again at its next grid point)."""
    type: str
    t: float
    task: Optional[str]
    error: str                         # <= 300 chars


class BeatVote(TypedDict):
    """mllink.beat_vote: one MlBeats read against the clock (§5.3: integer corrections only, never a phase or a
    tempo). The three values are Clock.apply_integer_correction's arguments."""
    window_t0: float
    window_t1: float
    beat_is_one: Optional[int]         # a beat index in the clock's published numbering that starts a bar; None: no vote
    bar_len: Optional[int]
    octave: Optional[float]            # 1 | 2 (the beat is twice as fast) | 0.5; None: the tempi don't relate
    conf: float                        # share of the beats of the interior's last local_s within match_ms of the
                                       # clock's line at ``octave``, carried back from its anchor (README S4.5)
    applied: bool                      # this vote went to clock_correct
    why: Optional[str]                 # page words when not: "no grid", "too few beats", "low agreement", "waiting
                                       # for a second run", "the clock agrees", "cooldown", "the tempi don't relate",
                                       # "no clock here"


class MLView(TypedDict):
    """ctx.ml(): mllink's latest results for the measured stream, swapped by reference on show-brain (any thread
    reads; nothing is mutated after the swap)."""
    state: str                         # ML_STATES
    why: Optional[str]                 # page words when not running ("the ML venv is missing", "restarting in 15 s")
    stream: Optional[str]              # the stream the results are for
    ced: Optional[MlCed]               # the latest
    ced_recent: list[MlCed]            # the last 30 s for ``stream``, oldest first
    tags: Optional[MlTags]             # the latest (``emb`` as sent)
    emb_recent: list[tuple[int, float, Any]]   # [(emb_id, t, float32 ndarray (EMB_DIM,))], the last 30 s, oldest first
    key: Optional[MlKey]
    beats: Optional[MlBeats]
    vote: Optional[BeatVote]           # the latest
    ages: dict[str, Optional[float]]   # ML_TASKS -> seconds since that result's t (None: never)
    hello: Optional[MlHello]
    stale_s: NotRequired[dict[str, float]]   # ML_TASKS -> how old that result may be and still be "now": max(show.yaml
                                       # ml.results_stale_s, ml.stale_periods x the task's interval in the last stats
                                       # line, which the worker's CPU guard and over-budget doubling stretch). Every
                                       # reader uses it (``ml_stale_s``), so none flaps while the worker degrades


def ml_stale_s(view: Any, task: str, default: float) -> float:
    """How old ``task``'s result may be and still count as now: the MLView's ``stale_s`` for it, never under
    ``default`` (show.yaml ml.results_stale_s); ``default`` when the view has none."""
    s = view.get("stale_s") if isinstance(view, dict) else None
    v = s.get(task) if isinstance(s, dict) else None
    if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and v > default:
        return float(v)
    return default


#: CED words that count as people talking (README S4.8, §7.5): a reading's speech is the max over them, so babble and
#: conversation under music count, not only "Speech". Starting point; show.yaml listener.ced.speech_words (the
#: listener's upload gate) and situations.talking.speech_words (talking, quiet and the genre speech scale) override it.
SPEECH_WORDS: tuple[str, ...] = ("speech", "conversation", "crowd")


def speech_words(raw: Any, where: str) -> tuple[tuple[str, ...], Optional[str]]:
    """A show.yaml ``speech_words`` list -> (its CED_GROUPS words, a problem line or None). Left out: SPEECH_WORDS.
    Not a list, or no known word in it: SPEECH_WORDS and a problem line. Unknown words are left out and named."""
    if raw is None:
        return SPEECH_WORDS, None
    if not isinstance(raw, list):
        return SPEECH_WORDS, f"{where}: {raw!r} is not a list of CED words: {list(SPEECH_WORDS)} used"
    ok = tuple(dict.fromkeys(w for w in raw if isinstance(w, str) and w in CED_GROUPS))
    bad = [w for w in raw if not (isinstance(w, str) and w in CED_GROUPS)]
    if not ok:
        return SPEECH_WORDS, f"{where}: no CED word in {raw!r} ({', '.join(CED_GROUPS)}): {list(SPEECH_WORDS)} used"
    return ok, (f"{where}: {bad!r} not CED words: left out" if bad else None)


def speech_of(reading: Any, words: tuple[str, ...]) -> Optional[float]:
    """The max of a CED reading's numbers for ``words`` (keys ``<word>`` or MlCed's ``<word>_p``); None when it has
    none of them."""
    if not isinstance(reading, dict):
        return None
    vals = []
    for w in words:
        v = reading.get(w, reading.get(f"{w}_p"))
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v:
            vals.append(float(v))
    return max(vals) if vals else None


# -- the medium lane (dsp.py; §4) ---------------------------------------------------------------------------------------
MUSIC_RHYTHM = "music.rhythm"      # RhythmData, one per medium frame (10 Hz); want-only (bus: WANT_ONLY + this tuple)
WANT_ONLY_STAGE4: tuple[str, ...] = (MUSIC_RHYTHM,)
ROOM_WHY = "room.why"              # WhyData, 1 Hz (docs/LIVE_DIAGNOSTICS.md v2; e1 / 04, 17 Oct); want-only
WANT_ONLY_STAGE5: tuple[str, ...] = (ROOM_WHY,)
WHY_TEXT_MAX = 80                  # each words field is cut to this many characters (the panel's line is 64)


class WhyData(TypedDict):
    """room.why: the room's reasons in words, for the renderer's diagnostics panel (one line). About 300 B."""
    t_ms: float                    # wall-clock ms
    auto: dict                     # {"on": bool, "line": the card's Auto line, "last": the last switch and its reason
                                   #  ("Piano -> Gallery: the piano has stopped") or None}
    listening: dict                # {"on": the AI listening switch, "effective": clips going now, "why": why not, or None}
    mic: dict                      # {"on": bool, "why": the mic's reason in words}
    look: dict                     # {"why": the last look change's reason in words, or None, "age_s": int or None}
RHYTHM_HZ = 10.0
CONTEXT_HZ = 1.0                   # music.context


class RhythmData(TypedDict):
    """music.rhythm and dsp.Features.rhythm(): the medium lane, one frame per 100 ms of stream time (README S4.6 has
    every formula). Gated values are x the loudness gate; the rest are raw."""
    t_ms: float                        # acoustic time of the frame's last sample
    stream: str
    chroma12: list[float]              # C..B: the harmonic part's chroma this frame, max-normalised (all 0 when silent)
    chroma12_avg: list[float]          # the same, mean over the last 1 s (structure's fallback descriptor)
    harmonic_db: float                 # the harmonic part's power, dBFS (floored -120)
    percussive_ratio: float            # 0..1: P^2 / (H^2 + P^2) over the last 1 s, 30 Hz-8 kHz
    harm_change: float                 # 0..1: mean over 4 s of 1 - cos(chroma now, chroma 0.3 s before); while the
                                       # clock's gate is beat|dmx, beat-synchronous (each beat against the one before)
    onset_rate: float                  # onsets per second over the last 4 s (ungated)
    density: float                     # 0..1: onset_rate, rolling-percentile normalised, x gate
    pulse_clarity: float               # 0..1: the onset envelope's autocorrelation peak (0.25-1.5 s lags)
    swing: Optional[float]             # 1 (straight) .. 3; None unless the clock gate is beat|dmx with >= 4 off-beats
    syncopation: Optional[float]       # 0..1; None unless the clock gate is beat|dmx
    low_db: float                      # 20-250 Hz (the sub and bass bands' slow envelopes), dB re a full-scale sine
    mfcc13: list[float]                # c1..c13 of the 40-band log-mel, mean over the last 1 s
    gate: float                        # the loudness gate now (FeaturesData.gate)


# -- sections and the context (structure.py; §6.1, §6.2, §11.1) ---------------------------------------------------------
SECTION_PHASES: tuple[str, ...] = ("calm", "build", "peak", "drop", "breakdown", "outro")
MUSIC_EVENT_KINDS: tuple[str, ...] = ("section_change", "build", "drop_armed", "drop", "breakdown", "track_change",
                                       "track_end_armed", "track_end")
EVENT_KINDS_STAGE4: tuple[str, ...] = ("breakdown", "track_change", "track_end_armed",
                                       "track_end")             # room.event kinds beyond EVENT_KINDS (song ends: 17 Oct)
# -- song ends (README S5.17; SHOW_ENGINE §8.5 "Song ends", 17 Oct; the event shape agreed with 04) ------------------
TRACK_EVENT_KINDS: tuple[str, ...] = ("track_end_armed", "track_end", "track_change")   # each carries "transition"
TRACK_VIA: tuple[str, ...] = ("sonos", "dsp")
TRACK_END_DETAILS: tuple[str, ...] = ("disarmed", "sound_back", "stop", "fade")   # track_end_armed called off;
                                   # track_end: sound again; cues.py's standing cues (README S5.22): an abrupt stop, a
                                   # fade-out
TRANSITION_STYLES: tuple[str, ...] = ("fade-dark", "crossfade", "cut")
TRANSITION_ON: tuple[str, ...] = ("downbeat", "now")
CHANGE_SIZES: tuple[str, ...] = ("big", "small")


class TrackTransition(TypedDict):
    """How the screens cross a song change (the director's choice, show.yaml director.track_transitions), on every
    TRACK_EVENT_KINDS event. Each event carries the choice as it stands then; a later one replaces an earlier one.
      fade-dark  out_s: the old song's scene fades to dark (ending at an armed end's at_ms, else from the track_end);
                 in_s: dark -> the new song's scene, from the track_change's at_ms (or a track_end "sound_back").
                 Both halves are on all three events; the renderer runs the half that applies. dark_max_s: never dark
                 longer than this (then back, in_s).
      crossfade  seconds, at the track_change (the junction).
      cut        seconds 0, on "downbeat": the new song's first downbeat at or after the track_change's at_ms ("now"
                 only for a feel whose cut rule is "always" while the clock is unsure)."""
    style: str                         # TRANSITION_STYLES
    out_s: Optional[float]             # fade-dark only
    in_s: Optional[float]              # fade-dark only
    seconds: Optional[float]           # crossfade: its length; cut: 0; fade-dark: None
    on: str                            # TRANSITION_ON
    change: str                        # CHANGE_SIZES: big = a new scene family and palette, small = the same family.
                                       # On track_end_armed and track_end the new song isn't known yet: show.yaml's
                                       # director.track_transitions.change.unknown
    dark_max_s: Optional[float]        # fade-dark only (additive to the shape agreed with 04, 17 Oct)


class TrackProfile(TypedDict):
    """One song as structure heard it, for the change size (TrackFacts.old / new). None where it isn't known."""
    genre: Optional[str]               # the genre word (genre_map.parents; the old song's smoothed, the new one's recent)
    key: Optional[str]                 # KEY_NAMES, at structure.track.key_min_conf or more
    mode: Optional[str]
    bpm: Optional[float]               # the clock's, while it gave beats
    energy: Optional[float]            # the mean energy while it sounded
    artist: Optional[str]              # Sonos now-playing's, when Sonos says


class TrackFacts(TypedDict):
    """What structure hands ctx.track_transition with a track event (not on the event itself)."""
    silent: bool                       # the room is in a gap now (structure.track.gap_gate / gap_lufs_m)
    phase: str                         # SECTION_PHASES: "outro" (falling, quiet) says a gap is coming
    gate: str                          # the clock's gate as structure read it this tick
    old: Optional[TrackProfile]        # the song that ends
    new: Optional[TrackProfile]        # track_change only: the new song as far as it is heard (Sonos at once: only
                                       # the artist)
    back: bool                         # a track_end "sound_back" already brought the screens back for this junction
GENRE_VIA: tuple[str, ...] = ("ml", "ml+listener", "listener", "none")


class GenreReading(TypedDict):
    """music.context's genre (structure.genre_reading; README S4.8 "The genre rule")."""
    word: Optional[str]                # a vocab.yaml genre word, or None (unknown, or a parent genre_map doesn't map)
    conf: float                        # 0..1
    parent: Optional[str]              # the leading Discogs parent (smoothed, with hysteresis), or None
    parent_p: float                    # its smoothed activation (the style rule: the top style's parent, its activation)
    via: str                           # GENRE_VIA
    words: dict[str, float]            # word -> share, the top genre_map.words_top summing to 1 ({} with none): the
                                       # soft genre room.direction's genres carries (the listener's: {word: 1.0})
    style: Optional[str]               # the newest tags line's top Discogs style (the style rule), else None


class SectionInfo(TypedDict):
    """music.context's section. ``phase`` is a SECTION_PHASES word (room.direction's SectionRef.phase stays its 0..1
    position)."""
    id: int                            # +1 per section in this process (a confirmed or predicted boundary, a drop, a
                                       # track change); 0 is the first
    letter: Optional[str]              # "A", "B", ... per track; a repeat gets its first time's letter; None until
                                       # identified (structure.identify_after_s)
    repeat_of: Optional[int]           # the id of the section it repeats, or None
    novelty: float                     # the boundary detector's curve now (1 - cos), >= 0
    phase: str                         # SECTION_PHASES
    build_progress: float              # 0..1 (0 outside a build)
    drop_eta_beats: Optional[float]    # beats to the predicted drop line (a build with a grid), else None
    bars_in_section: Optional[int]     # bars since the section began (None without a bar grid)
    started_ms: float                  # acoustic ms the section began (the snapped boundary)
    expected_bars: Optional[int]       # a known repeat's length, from its first time through
    chorus: bool                       # its letter is the probable chorus (most repeated x most energetic)
    label: Optional[str]               # the listener's SECTION_LABELS word when its verdict for this section agrees


class TrackInfo(TypedDict):
    id: int                            # +1 per track change (0: since start)
    changed: bool                      # True only in the first music.context after a change (compare ids)
    age_s: Optional[float]             # since this track began
    via: Optional[str]                 # "sonos" | "dsp" | None (the first track)


class MusicContext(TypedDict):
    """music.context, 1 Hz (§11.1), and ctx.music_context(). Numbers are smoothed (structure.tags_tau_s) and frozen
    while CED music is under genre_map.freeze_below_music; None where no source has them."""
    t_ms: float
    stream: Optional[str]
    source: str                        # what sections run on: "ml" (embeddings) | "dsp" (chroma + MFCC) | "none"
    genre: GenreReading
    moods: list[str]                   # vocab moods: the listener's (conf >= 0.6), else []
    instruments: list[str]             # the ML's top labels while fresh, else the listener's
    vocal: Optional[float]             # voice_p
    dance: Optional[float]
    arousal: Optional[float]
    engagement: Optional[float]
    bright: Optional[float]
    acoustic: Optional[float]
    valence: Optional[float]           # 0 (sad, dark) .. 1 (happy): structure.valence's weights over the mood head
    mood_energy: Optional[float]       # 0 (calm) .. 1 (energetic): structure.mood_energy's, the same way (the band
                                       # words' base hears a presto in legato strings by it)
    key: Optional[str]                 # KEY_NAMES
    mode: Optional[str]
    key_strength: float
    key_via: Optional[str]             # "midi" (the piano) | "ml" (Essentia) | "audio" (music.harmony) | None
    talking: Optional[bool]            # situations' talking reading
    ced: Optional[dict[str, float]]    # CED_GROUPS -> the mean of the last 3 s, or None
    section: SectionInfo
    track: TrackInfo
    ml: dict[str, Any]                 # for the console and logs: {"state", "styles_top3", "moods_top5",
                                       #  "instruments_top5", "ages"}


class MusicEventData(EventData):
    """room.event from structure.py (MUSIC_EVENT_KINDS), also handed to every ctx.on_music_event. ``at_ms`` is when
    outputs act: a confirmed section_change's next bar line (>= structure.events.min_lead_s ahead), a predicted one's
    or drop_armed's line; build, drop, breakdown and track_change report what was heard (at_ms in the past).
    Song ends (README S5.17): track_end_armed's at_ms is the predicted end (ahead, from Sonos now-playing); track_end's
    is the end (via sonos: the armed end as it passes; via dsp: the gap's start, sent once the gap has lasted
    structure.track.end_gap_s; detail "sound_back": the first sound after it, re-sent with the same id);
    track_change's is the new song's first beat while the clock's gate is beat|dmx, else its first sound."""
    section_id: NotRequired[int]
    letter: NotRequired[Optional[str]]
    repeat_of: NotRequired[Optional[int]]
    predicted: NotRequired[bool]       # section_change: a known repeat's predicted line
    boundary_ms: NotRequired[float]    # section_change: acoustic ms the section actually began
    phase: NotRequired[str]            # SECTION_PHASES
    build_progress: NotRequired[float]
    eta_beats: NotRequired[Optional[float]]   # drop_armed: beats from t_ms to at_ms; None: the line is off (``detail``
                                       # "disarmed": the build ended over drop.disarm_bars before it, or a new
                                       # track; "missed": the line passed with no drop), at_ms = that line
    track_id: NotRequired[int]         # track_end_armed, track_end: the ENDING song's track id; track_change: the new
    via: NotRequired[str]              # TRACK_EVENT_KINDS: TRACK_VIA ("sonos": now-playing; "dsp": the sound)
    # song ends (README S5.17, 17 Oct; agreed with 04)
    prev_track_id: NotRequired[int]    # track_change: the song that ended
    eta_s: NotRequired[Optional[float]]   # track_end_armed: seconds from t_ms to at_ms (the predicted end); None:
                                       # called off (``detail`` "disarmed": a pause, skip, seek or another track first)
    conf: NotRequired[float]           # track_end_armed: 0..1, how sure the predicted end is (1 - half the estimate's
                                       # spread in s: 0.5 after one now-playing read, toward 1 with more)
    transition: NotRequired["TrackTransition"]   # TRACK_EVENT_KINDS: the director's choice (absent: no director, a
                                       # call-off, or ``held``)
    held: NotRequired[bool]            # TRACK_EVENT_KINDS: a person holds the look (Keep this look, or their change
                                       # pending): no transition; the renderer leaves this change alone


# =====================================================================================================================
# Stage 5 (README "Stage 5"): the rig. The fixture registry (config/room/fixtures.yaml, OFL types, tag groups; fixtures.py),
# the look engine (looks.py, emitters.py), the output drivers and their runners (outputs.py; hue_out.py, artnet_lan.py),
# and the screens' hand-pick hold (director.py + autopilot.RoomLink). Additions only: the names the five Stage-5
# builders share (B12 the rig and the wiring, B13 the look engine, B14 Hue, B15 Art-Net on the LAN, B16 the director
# and RoomLink seams). BrainContext's Stage-5 slots are at the end of BrainContext. Every output is OFF until
# fixtures.yaml enables it: a restart with this code changes nothing anyone can see.
# =====================================================================================================================

# -- files --------------------------------------------------------------------------------------------------------------
FIXTURES_FILE = CONFIG_ROOM_DIR / "fixtures.yaml"   # c8: the registry (§9.2); fixtures.py reads it (hot-reloaded)
FIXTURE_TYPES_DIR = CONFIG_ROOM_DIR / "fixture-types"   # c8: OFL fixture JSON (MIT), ofl/<maker>/<model>.json; a type's
                                                    # ``file`` is relative to this directory
LATENCY_FILE = CONFIG_ROOM_DIR / "latency.json"     # 10's schema v2, read-only for us: outputs.<key>.<attribute>
NODES_STATE_FILE = STATE_DIR / "nodes.json"         # artnet_lan: what discovery last saw per node (atomic); never config
FIXTURES_ADDED_FILE = CONFIG_ROOM_DIR / "fixtures.added.yaml"   # the Lights page's lights (README S5.23): written
                                                    # only by the rig API (fixtures.Registrar), merged under
                                                    # fixtures.yaml
FIXTURES_SETUP_FILE = CONFIG_ROOM_DIR / "fixtures.setup.yaml"   # per-light settings from the Lights page (home, limits,
                                                    # beam, slot names, positions; docs/LIGHTS.md §2.6): written only
                                                    # by the rig API (fixtures.SetupStore); any fixture, hand-file too
FOLLOW_STATE_FILE = STATE_DIR / "follow.json"       # outputs.Rig: who follows the music (POST /api/v1/rig/follow;
                                                    # atomic)
HUE_SECRETS_DIR = pathlib.Path.home() / ".config" / "screen-machine" / "hue"   # room_service.hue.pair writes
                                                    # <bridge_id>.json (0600); hue_out reads it only through room's
                                                    # BridgeSecrets.load (room_service.hue.client), read-only
SHOW_SOURCE = "show_service"                        # the ``source`` on our room PATCHes; room's screen.look events carry it

# -- vocabularies -------------------------------------------------------------------------------------------------------
DRIVERS: tuple[str, ...] = ("showbus", "hue", "artnet", "ddp")    # fixtures.yaml outputs.<id>.driver
DRIVERS_BUILT: tuple[str, ...] = ("hue", "artnet")  # Stage 5. showbus (P4) and ddp (LED strips, later) may be configured:
                                                    # they show state "not_built" and never start
FIXTURE_KINDS: tuple[str, ...] = ("screen", "lamp")   # a TYPE's kind. "screen" is never dimmed: no level, floor, master,
                                                    # cap, dip, hit or intensity layer ever reaches it (the screens rule)
ATTR_KINDS: tuple[str, ...] = ("intensity", "colour", "position")   # what a latency applies to (§5.4)
#: latency.json ``outputs.<key>.<attribute>`` names read for each kind, first found wins. "brightness" is the TV
#: routine's name for the intensity step (tools/room/latency_cal). Colour falls back to the intensity rise when unmeasured.
LATENCY_ATTRS: dict[str, tuple[str, ...]] = {"intensity": ("intensity", "brightness"), "colour": ("colour",),
                                             "position": ("position",)}
FAILSAFE_MODES: tuple[str, ...] = ("hold", "zero", "full", "scene")   # a node's ArtAddress AcFail* (§9.3)
#: POST /api/v1/rig/follow, per fixture (README S5.23): "dmx" follows the music (the Room page's "Follows the music"),
#: "off" is dark; or a static colour {"hex": "#rrggbb", "bri": 0..100}. room_service's DMX-lights device sends them.
FOLLOW_WORDS: tuple[str, ...] = ("dmx", "off")
#: OutputStatus.state words (HueStatus.state too). Room's hue adapter never sees these: the Hue routes answer the four
#: HUE_ROOM_STATES words (hue_room_state), and the richer word stays in /api/v1/rig.
OUTPUT_STATES: tuple[str, ...] = (
    "off",           # enabled: false (the default for every output)
    "not_built",     # a driver this stage doesn't build (showbus, ddp), or its module is missing
    "no_config",     # enabled, but something it needs is missing (node, port_address, bridge secrets): ``detail`` says
    "idle",          # enabled, nothing to send (hue: no lamp asked to follow)
    "waiting_room",  # hue: patch.yaml hue.streamer isn't "show", so room_service streams the area (never fight)
    "waiting_input", # hue: lamps asked, but no look to show for stop_after_s (P0's idle rules): area stopped
    "starting",      # hue: resolving and starting the area
    "streaming",     # hue: 50 Hz frames going out
    "sending",       # artnet: frames going out (the beat grid, or keepalive while idle)
    "releasing",     # hue: stopping the area for POST /api/v1/hue/release
    "taken_over",    # hue: another app stopped or holds the area; nothing restarts until the wanted set changes
    "error")         # retried after a backoff; ``detail`` says what
FX_SHAPES: tuple[str, ...] = ("sine", "saw", "square", "triangle", "swell")   # looks.py oscillators (README S5.5)
FX_PHASES: tuple[str, ...] = ("beat", "bar", "phrase", "time")                # the phase an oscillator runs on
FX_NEEDS: tuple[str, ...] = ("beat", "grid", "none")   # beat: the clock's gate beat (dmx: hit outputs only, reduced);
                                                    # grid: any clock grid; none: free-runs on time without one
#: Inputs a show.yaml looks layer may take a number from (``depth``, ``spread``); anything else must be a number.
#: ``flow.*``: room.look's flow (docs/LIGHTS.md §4.1, §5.2; 0 without one).
LOOK_INPUTS: tuple[str, ...] = ("swell", "motion.amount", "motion.speed", "accents.density", "look.spread", "energy",
                                "flow.speed", "flow.spread", "flow.coherence")
#: OFL ColorIntensity colours -> our emitter names (lower snake case). An OFL colour not listed is an "other" channel.
EMITTERS: tuple[str, ...] = ("red", "green", "blue", "white", "warm_white", "cold_white", "amber", "lime", "cyan",
                             "magenta", "yellow", "indigo", "uv")
#: The attribute vocabulary (docs/LIGHTS.md §2.1): what layers, the hold (jog) and the frame set on a fixture, each in
#: its normalised unit (pan/tilt centred degrees, zoom beam degrees, spins -1..1, slots 1-based). A fixture has an
#: attribute when its type has a DMX range serving it (fixture_encode.caps); ``macro`` is the page's "More" only.
ATTRS: tuple[str, ...] = ("intensity", "colour", "colour_slot", "colour_spin", "cct", "shutter", "pan", "tilt",
                          "pan_spin", "tilt_spin", "pt_speed", "zoom", "focus", "iris", "frost", "gobo", "gobo2",
                          "gobo_spin", "gobo2_spin", "gobo_shake", "gobo2_shake", "gobo_scroll", "prism", "prism_spin",
                          "macro")
SHUTTER_MODES: tuple[str, ...] = ("open", "closed", "strobe", "pulse", "random", "ramp", "burst")   # shutter.mode
#: A channel's ``attr``: what drives it (colour_mix: intensity, emitter, wheel; fixture_encode: the rest, by its
#: ``ranges``), from its first capability that isn't NoFunction; "other": mixed functions, a fine byte, macros.
CHANNEL_ATTRS: tuple[str, ...] = ("intensity", "emitter", "wheel", "shutter", "pan", "tilt", "pan_spin", "tilt_spin",
                                  "pt_speed", "zoom", "focus", "iris", "frost", "cct", "colour_spin", "gobo", "gobo2",
                                  "gobo_spin", "gobo2_spin", "gobo_shake", "gobo2_shake", "gobo_scroll", "prism",
                                  "prism_spin", "other")
CURVES: tuple[str, ...] = ("srgb", "linear")        # emitter curve: linear light -> DMX fraction; also "gamma:<g>"
NUDGE_KINDS_STAGE5: tuple[str, ...] = ("screen_pick",)   # a person's palette pick on a screen holds the screens' palette
SCREEN_PICK = "screen_pick"                         # its chip id and kind (DELETE /api/v1/nudge/screen_pick clears it)
ROOM_SCREEN_LOOK = "screen.look"                    # room's event (2f): {screen, session, source (default "web"),
                                                    # changed: [keys], palette (only when the PATCH carried one)}
LOOK_SOURCE_STATES: tuple[str, ...] = ("unknown", "yes", "no")   # does this room boot accept PATCH ... {source}?

# -- the registry (fixtures.py) -------------------------------------------------------------------------------------------
class LatencyView(TypedDict):
    """L(fixture, kind) in ms: from a frame leaving show_service to the perceived onset of the light (README S5.2).
    Every output evaluates its look at ``t_send + L`` (§5.4). Unmeasured and not hand-set: 0, with source "unset"."""
    rise_ms: float                 # intensity rising edge
    fall_ms: float                 # intensity falling edge (only sets min_pulse_ms)
    colour_ms: float
    position_ms: float
    min_pulse_ms: float            # max(looks.min_visible_ms, rise - fall + looks.min_visible_ms)
    source: dict[str, str]         # "rise" | "fall" | "colour" | "position" -> "latency.json outputs.<key>.<attr>" |
                                   # "fixtures.yaml" | "unset"


class OutputConfig(TypedDict):
    """fixtures.yaml outputs.<id>, resolved."""
    id: str
    driver: str                    # DRIVERS
    enabled: bool                  # False unless fixtures.yaml says true
    built: bool                    # driver in DRIVERS_BUILT and its module imported
    node: Optional[str]            # artnet: the nodes entry
    port_address: Optional[int]    # artnet: 15-bit Port-Address, >= 1 (0 is refused: room's universe)
    latency_key: str               # latency.json outputs key for the output as a whole (default: the id)
    latency: dict[str, Any]        # hand-set ms as written ({} when unset): {intensity: {rise_ms, fall_ms}, colour_ms,
                                   # position_ms}
    hits: Optional[bool]           # None: the driver's own (hue False, artnet True)
    min_attack_ms: Optional[float] # None: the driver's own (hue 80, §5.4; artnet 0)
    rate_hz: Optional[float]       # None: the driver's own (hue 50; artnet: the beat grid <= 44 Hz)
    settings: dict[str, Any]       # every other key as written; hue: bridge, area, gamma, stop_after_s (each unset key
                                   # read from patch.yaml's hue block, 68's, until P5)
    problems: list[str]


class NodeConfig(TypedDict):
    """fixtures.yaml nodes.<id>: an Art-Net node, matched by MAC or short name; frames go to ``last_ip`` (or the address
    discovery last matched it at: nodes.json)."""
    id: str
    match: dict[str, str]          # {"mac": "aa:bb:cc:dd:ee:ff"} | {"short_name": "..."} (ArtPollReply's)
    last_ip: Optional[str]         # the address in config; None: virtual, or unknown (nothing is sent)
    port: int                      # 6454 (tests: an ephemeral port; a loopback address on 6454 is never sent to)
    full_512: bool                 # send all 512 channels (default false: up to the highest patched channel, even)
    failsafe: Optional[str]        # FAILSAFE_MODES, sent once as ArtAddress; None: leave the node's own setting
    virtual: bool                  # previz: frames stay in memory (GET /api/v1/rig/frame), never on a socket


class AttrChannel(TypedDict):
    """One DMX channel of a type's mode."""
    offset: int                    # 0-based within the fixture's footprint
    name: str                      # the OFL channel name ("Red", "Dimmer", "Strobe")
    attr: str                      # CHANNEL_ATTRS
    emitter: NotRequired[str]      # attr "emitter": an EMITTERS name
    fine: NotRequired[int]         # the offset of its fine (LSB) channel, when the mode carries it
    default: int                   # 0..255: OFL defaultValue; a shutter with none: its "Open" capability's DMX value;
                                   # otherwise 0. What an undriven channel sends
    ranges: NotRequired[list["CapRange"]]   # every capability on the channel, normalised (docs/LIGHTS.md §2.2);
                                   # absent on fine and switching channels
    res: NotRequired[int]          # bytes per value of ``ranges``' dmx (2: 16-bit, MSB first); absent = 1


class TypeInfo(TypedDict):
    id: str
    source: str                    # "builtin" | "ofl"
    kind: str                      # FIXTURE_KINDS
    file: Optional[str]            # ofl: relative to FIXTURE_TYPES_DIR
    mode: Optional[str]            # ofl: the mode's name or shortName
    footprint: int                 # DMX channels (0 for the builtin types)
    attrs: list[str]               # the ATTRS this type can show, in ATTRS order ("intensity", "colour" as before:
                                   # from its driven intensity, emitter and wheel channels; the rest: a range serves it)
    channels: list[AttrChannel]
    emitters: dict[str, dict[str, Any]]   # emitter -> {"xy": [x, y], "Y": relative luminance, "source": "type" |
                                   # "starting point"}
    wheel: list[dict[str, Any]]    # colour slots only: [{"dmx": int, "hex": "#rrggbb", "name": str}]
    curve: str                     # CURVES or "gamma:<g>"; neutral "srgb" (the palette's values straight across)
    problems: list[str]
    wheels: dict[str, dict[str, Any]]   # the mode's wheels: name -> {"kind": colour | gobo | prism | other, "attr":
                                   # the ATTRS word its slots set or None, "slots": [{"slot", "type", "name", "hex"?,
                                   # "facets"?}]}
    physical: dict[str, Any]       # {"lens_deg": [min, max] | None, "pan_deg": float | None, "tilt_deg": float | None,
                                   # "zoom_deg": [min, max] | None}: None = the file doesn't say (setup ``range`` and
                                   # the starting points stand in)


class FixtureInfo(TypedDict):
    id: str                        # as in patch.yaml, rooms.json, the Hue adapter, the renderer and the Room page
    label: str
    type: str
    kind: str                      # its type's
    output: Optional[str]
    address: Optional[int]         # artnet: 1-based start channel
    light: Optional[str]           # hue: the bridge's light id
    screen: Optional[str]          # the screen it belongs to / hangs above
    zone: Optional[str]
    placement: Optional[str]       # above | opposite | ... (show.yaml lamps.placement_roles -> role)
    role: Optional[str]            # unset: from placement
    tags: list[str]
    order: Optional[float]         # for ordered groups without an explicit order
    wb: dict[str, float]           # emitter -> gain (white balance); {} = 1.0 each
    curve: Optional[str]           # overrides the type's
    pin: dict[str, int]            # OFL channel name -> DMX value (an undriven channel held there)
    house: Optional[dict[str, Any]]   # {"intensity": 0..1, "hex": "#rrggbb"}: what it shows with no look; None = dark
    latency: LatencyView
    setup: "FixtureSetup"          # fixtures.setup.yaml's entry for it (the Lights page's; docs/LIGHTS.md §2.6); {}


class RigView(TypedDict):
    """ctx.rig() and GET /api/v1/rig's ``registry``: the last good fixtures.yaml, resolved. Swapped by reference on
    show-brain; never mutated after the swap."""
    revision: str                  # sha1 of fixtures.yaml, 12 hex
    path: str
    error: Optional[str]           # the last load error (the last good registry stays)
    problems: list[str]            # page words, one per thing it couldn't use as written
    outputs: dict[str, OutputConfig]
    nodes: dict[str, NodeConfig]
    types: dict[str, TypeInfo]
    fixtures: dict[str, FixtureInfo]
    groups: dict[str, list[str]]   # group -> fixture ids in order


class RigFixtureRow(TypedDict):
    """GET /api/v1/rig ``fixtures`` (README S5.23): one row per fixture in the registry, in its order, for the Lights
    page and room_service's DMX-lights device."""
    id: str
    label: str
    kind: str                      # its type's (FIXTURE_KINDS)
    type: str
    type_label: Optional[str]      # page words (fixtures.type_label)
    output: Optional[str]
    driver: Optional[str]          # its output's (DRIVERS); None: unrouted
    node_ip: Optional[str]         # artnet: where frames go (discovery's address, else last_ip); None otherwise
    universe: Optional[int]        # artnet: its output's Port-Address; None otherwise
    address: Optional[int]
    footprint: int
    placement: Optional[str]
    role: Optional[str]            # as the director decides it: its own, else its placement's, else the default role
    zone: Optional[str]
    follow: Any                    # FOLLOW_WORDS or {"hex", "bri"}; None: never set (the look engine's default)
    online: Optional[bool]         # its node's address answered the last ping (every ONLINE_CHECK_S); None: no node
                                   # address, or not checked yet
    last_rgb: Optional[str]        # "#rrggbb": the last frame sent for it (colour x intensity); None: none yet
    editable: bool                 # it is in fixtures.added.yaml: the Lights page may change or remove it
    caps: Optional["FixtureCaps"]  # what it can do, its setup applied (fixture_encode.caps): the page builds its
                                   # controls from this alone; None: no type, or fixture_encode can't be imported
    setup: "FixtureSetup"          # its fixtures.setup.yaml entry ({} = nothing set)


class RigTypeRow(TypedDict):
    """GET /api/v1/rig/types (README S5.23): a type a DMX light can be registered with."""
    id: str
    label: str                     # page words: "RGBW light (4 channels)"
    source: str                    # "builtin" (fixtures.GENERIC_TYPES) | "ofl" (a fixtures.yaml type)
    footprint: int
    channels: list[str]            # channel names in DMX order
    kind: str                      # FIXTURE_KINDS
    caps: Optional["FixtureCaps"]  # the type's capabilities with no setup (None: fixture_encode can't be imported)


# -- fixture capabilities (docs/LIGHTS.md §2) ----------------------------------------------------------------------------
class CapRange(TypedDict):
    """One OFL capability on a channel, normalised (ofl.parse; fixtures.GENERIC_RANGES for the generic types)."""
    dmx: list[int]                 # [lo, hi] at the channel's full resolution (0..65535 for 16-bit: AttrChannel.res)
    cap: str                       # the OFL capability type ("Pan", "WheelSlot", "WheelShake", ...)
    attr: str                      # the ATTRS word it serves ("macro" for page-only ones)
    lo: float                      # the attr's value at dmx lo (centred degrees, -1..1 spin, 0..1, kelvin, Hz, slot)
    hi: float                      # ...and at dmx hi (equal for a stepped capability)
    slot: NotRequired[int]         # WheelSlot / WheelShake / WheelSlotRotation / Prism: the slot number
    mode: NotRequired[str]         # shutter: SHUTTER_MODES
    word: NotRequired[str]         # page words: the OFL comment, effectName, shutterEffect or the slot's name


class FixtureCaps(TypedDict):
    """What one fixture can show (fixture_encode.caps(type, setup)): the Lights page's controls, the engine's
    capability-aware layers and the catalogue's ``needs`` read this alone, never a type or channel name."""
    attrs: list[str]               # ATTRS this fixture can show (after setup overrides), in ATTRS order
    pan: Optional[dict]            # {"min": deg, "max": deg, "fine": bool, "limits": [lo, hi] | None}: centred degrees
    tilt: Optional[dict]           # the same
    pt_speed: bool
    zoom: Optional[dict]           # {"min": deg, "max": deg, "known": bool} (known: the file's degrees, not a guess)
    focus: bool
    iris: bool
    frost: bool
    colour: str                    # "mix" (emitters) | "wheel" | "none"
    colour_slots: list[dict]       # [{"slot", "name", "hex"}] (setup names and colours win)
    gobo_slots: list[dict]         # [{"slot", "name", "shake": bool, "open": bool}] (setup names win); [] = no wheel
    gobo2_slots: list[dict]
    prism_slots: list[dict]        # [{"slot", "name", "facets"}]; a plain Prism channel: [{"slot": 1, "name": "Prism"}]
    spins: list[str]               # the spin attrs present: gobo_spin, gobo2_spin, prism_spin, colour_spin, ...
    cct: Optional[dict]            # {"min_k", "max_k"}
    shutter: list[str]             # the SHUTTER_MODES it has
    strobe_hz: Optional[list[float]]   # [lo, hi] (setup strobe_hz_max caps hi)
    macros: list[dict]             # [{"channel", "name", "choices": [{"word", "dmx"}]}]: the page's "More" only
    moves: bool                    # has pan and tilt


class FixtureSetup(TypedDict, total=False):
    """config/room/fixtures.setup.yaml ``fixtures.<id>`` (fixtures.SetupStore; docs/LIGHTS.md §2.6). Every key is
    owner-set on the Lights page; an absent key means "not set" (no default limits, speeds or names are invented)."""
    home: dict                     # {"pan": deg, "tilt": deg}: where it rests (house, inactive, uncalibrated patterns)
    limits: dict                   # {"pan": [lo, hi], "tilt": [lo, hi]}: soft limits, centred degrees; absent = the
                                   # full range
    max_speed: dict                # {"pan": deg_s, "tilt": deg_s}: measured; absent = show.yaml lights.motion's
    range: dict                    # {"pan": deg, "tilt": deg}: the full mechanical range when the type doesn't say
    beam: dict                     # {"zoom": deg, "focus": 0..1, "iris": 0..1, "frost": 0..1}: its default beam
    slots: dict                    # {"gobo": {"<n>": "Stars"}, "gobo2": {...}, "colour": {"<n>": {"name", "hex"}},
                                   #  "prism": {...}, "count": {"gobo": n, "colour": n}} (count: generic types only)
    positions: dict                # {"<name>": {"pan": deg, "tilt": deg, "label"?: str}}: named, in its own pan/tilt
    strobe_hz_max: float           # owner-set, per light; absent = none
    jog_flip: dict                 # {"pan": bool, "tilt": bool}: the page's drag direction (page convenience only)


# -- frames (looks.py -> drivers) -----------------------------------------------------------------------------------------
class FixtureFrame(TypedDict):
    intensity: float               # 0..1 after layers, master and caps
    colour: list[float]            # sRGB 0..1 at full value (largest channel 1), or [0, 0, 0]
    rgb: list[float]               # colour x intensity: what room's Hue path took from Art-Net (hue's xy_bri input)
    dmx: NotRequired[bytes]        # types with channels: the footprint, encoded (len = footprint)
    t_eval_ms: float               # when its intensity was evaluated (t_send + rise), for previz and logs
    attrs: NotRequired[dict[str, Any]]   # docs/LIGHTS.md §5.8: the normalised values sent (pan, tilt, zoom, gobo, ...),
                                   # for a type with ranges beyond intensity, colour and the colour wheel


class Frame(TypedDict):
    """LookEngine.evaluate(output, t_send) -> Driver.set_frame. Built and read on that output's runner thread only."""
    output: str
    seq: int                       # +1 per evaluate for this output
    t_send_ms: float
    active: bool                   # something to show: a vibe plays, or (no vibe) fresh, non-quiet features
    why: Optional[str]             # inactive: "no_features" | "quiet" | "no_vibe" (caps.idle_without_vibe) |
                                   # "no_look" | "rig_off"
    fixtures: dict[str, FixtureFrame]   # this output's fixtures (kind screen never appears)
    busy: bool                     # docs/LIGHTS.md §5.5: a fixture moves under a hold or a glide home: the runner plans
                                   # at its active rate (outputs._Runner), active or not


class DriverCaps(TypedDict):
    hits: bool                     # takes music.hit flashes (§5.4: DMX yes, Hue never)
    min_attack_ms: float           # an intensity rise is never faster than this (Hue 80, §5.4)
    grid: str                      # "beat": frames phase-locked to the beat (artnet) | "fixed": rate_hz (hue)
    rate_hz: float
    keepalive_s: Optional[float]   # idle: resend unchanged frames this often (artnet 1 s); None: always rate_hz
    # README S5.14 (review fixes, 16 Oct); a driver without them reads 0 and False
    min_period_ms: NotRequired[float]      # no oscillator on this output cycles faster (hue: 2 x min_attack_ms:
                                           # "never strobes", §5.4); the engine coarsens a faster layer
    idle_without_vibe: NotRequired[bool]   # no vibe -> the house look (artnet: nothing room-side gates DMX, §8.6);
                                           # False (hue): room's hue adapter decides which lamps follow


# -- motion and the hold (docs/LIGHTS.md §5) ------------------------------------------------------------------------------
class HoldView(TypedDict):
    """LookEngine.holds() / Rig.holds(): a light held by the Lights page's Move it (§5.7). Out of everything else
    (layers, effects, hits, master, caps, follow, identify) until released or idle."""
    target: dict[str, Any]         # what was asked (attr -> value), after clamping
    now: dict[str, Any]            # the smoothed values on the last frame
    until_s: float                 # monotonic: released at this time unless another hold call comes
    free: bool                     # soft limits ignored (the page's Set limits panel)


class LiveFixture(TypedDict):
    """Rig.live_view()'s per light (§8.1): where it is and where its beam lands, for the Room map."""
    at: Optional[list[float]]      # its room position (m); None: not placed
    dir: Optional[list[float]]     # the beam's direction (calibrated movers)
    hit: Optional[list[float]]     # where the beam lands on a room surface
    surface: Optional[str]
    pan: Optional[float]           # centred degrees, as sent
    tilt: Optional[float]
    zoom_deg: Optional[float]
    gobo: Optional[str]            # the gobo slot's name
    prism: bool
    intensity: float
    hex: Optional[str]             # colour x intensity, "#rrggbb"
    held: bool
    calibrated: bool               # it has a solved pose


class LiveView(TypedDict):
    """GET /api/v1/rig/live (§8.1): from the runners' last frames and the space's poses."""
    t_ms: float
    flow: Optional["FlowData"]
    programs: dict[str, dict]      # group -> {"label", "scene_label", "effects": [label]}
    fixtures: dict[str, LiveFixture]


#: A smoothstep's peak slope. No intensity rise is steeper than SLEW_PEAK / min_attack (per second): the look engine's
#: rise limit and hue_out's own (one number, so the driver never clips the engine's swell).
SLEW_PEAK = 1.5


def hit_weight(t_s: float, t0_s: float, t1_s: float, step: bool, swell_s: float) -> float:
    """How much of LookData.hit's colour shows at ``t_s`` (0..1): the one rule for every output (looks.py) and the
    virtual Maestro's lamp channels (director.maestro_palette_fn). ``step`` (an output that takes hits, or a predicted
    drop line): 1 for [t0, t1). Otherwise a swell (§5.4: Hue never gets a reactive hit): a smoothstep up over
    ``swell_s`` from t0, and back down over ``swell_s`` from t1."""
    if t_s < t0_s:
        return 0.0
    if step:
        return 1.0 if t_s < t1_s else 0.0
    if swell_s <= 0.0 or t_s >= t1_s + swell_s:
        return 0.0

    def ss(x: float) -> float:
        x = 0.0 if x < 0.0 else 1.0 if x > 1.0 else x
        return x * x * (3.0 - 2.0 * x)
    return max(0.0, min(ss((t_s - t0_s) / swell_s), 1.0 - ss((t_s - t1_s) / swell_s)))


class OutputStatus(TypedDict):
    id: str
    driver: str
    enabled: bool
    state: str                     # OUTPUT_STATES
    detail: Optional[str]          # page words
    frames: int                    # sent (or kept, virtual)
    rate_hz: float                 # measured over the last 5 s
    late_ms: dict[str, Optional[float]]   # {"p50", "p95"}: how late frames left against their planned time
    errors: int
    last_frame_age_s: Optional[float]
    extra: dict[str, Any]          # the driver's own (hue: HueStatus; artnet: {"node": NodeStatus, "length", ...})


class NodeStatus(TypedDict):
    id: str
    ip: Optional[str]              # where frames go now
    ip_from: str                   # "config" | "discovery" | "virtual" | "none"
    matched: Optional[bool]        # the last reply's MAC / short name matched; None: no reply yet
    replies: int
    reply_age_s: Optional[float]
    short_name: Optional[str]
    long_name: Optional[str]
    mac: Optional[str]
    refresh_hz: Optional[float]    # ArtPollReply RefreshRate (Art-Net 4), when given
    failsafe: Optional[str]        # what the node reports (Status3), when it does
    merging: Optional[bool]
    detail: Optional[str]          # "no replies: they need ca's relay (README S5.7)", "moved: update last_ip", ...


class HueStatus(TypedDict):
    """The hue output's own view (``/api/v1/rig`` ``.hue``, ``OutputStatus.extra``). The Hue routes answer
    ``hue_answer(<this>)`` (HueAnswer), never this."""
    state: str                     # OUTPUT_STATES (the driver's own word)
    detail: Optional[str]
    enabled: bool
    output: Optional[str]          # the hue output's id
    wanted: list[str]              # fixture ids room asked to follow (POST /api/v1/hue/stream) that are hue fixtures
                                   # of this output (the ones streamed)
    asked: list[str]               # what room asked to follow, unfiltered (the routes' ``lights``): the driver's own,
                                   # or (the output off) the Rig's saved set; [] with no rig
    streaming: bool
    area: Optional[str]
    channels: dict[str, int]       # light id -> entertainment channel
    frames: int
    dtls: bool
    unknown: list[str]             # asked ids that aren't hue fixtures in the registry (ignored)
    room_state: str                # HUE_ROOM_STATES: what the routes answer room (hue_room_state)
    words: str                     # HUE_WORDS[room_state]: the console's and the card's words


class HueAnswer(TypedDict):
    """The Hue routes' answer to room's hue adapter (68's contract and its addendum, 16 Oct): ``POST
    /api/v1/hue/stream`` and ``GET /api/v1/hue/status``. Room reads ``state``, ``lights`` and ``boot_id`` (its
    ``_sync_show``: first contact adopts ``lights``; a new ``boot_id`` or ``lights`` unlike its own set re-sends it);
    the rest is for people (curl, the console)."""
    state: str                     # HUE_ROOM_STATES, exactly
    lights: list[str]              # what room asked to follow, unfiltered (HueStatus.asked): never ``wanted``, so an id
                                   # that isn't a hue fixture here never makes room re-send on every read
    boot_id: Optional[str]         # this show_service process (api.Api.boot_id): a restart reads as a new one
    words: str                     # HUE_WORDS[state]
    detail: Optional[str]          # why, in plain words (the driver's own state and the rest: /api/v1/rig)
    wanted: list[str]              # the lamps following the show (what room asked, that are hue fixtures here)
    unknown: list[str]             # asked ids that aren't hue fixtures of the hue output (ignored)


class HueRelease(TypedDict):
    """POST /api/v1/hue/release {lights?}: ``released`` true only once the lamps are writable again (our stream is
    over, the bridge reads the area inactive, and ``release_settle_s`` has passed since; §9.4 step 3)."""
    released: bool
    state: str                     # HUE_ROOM_STATES, as HueAnswer
    why: Optional[str]             # not released: "shared_area" (other lamps still follow: the whole area streams),
                                   # "taken_over" (the area reads active and isn't ours: another app holds it),
                                   # "bridge_unreachable", "timeout", "starting" (show_service starting under
                                   # patch.yaml hue.streamer: show: room checks the bridge itself)
    still_following: list[str]
    waited_ms: float
    via: str                       # "show" | "room" (no hue output on: room_service streams, nothing held here)


#: What the Hue routes answer room's hue adapter (68's contract): ``armed`` = following but silent (no sound yet,
#: starting, or retrying after an error); ``idle`` = following nothing (nothing asked, the output off, room streams
#: (hue.streamer isn't show), not paired, not built). Room acts on ``taken_over`` (never fought).
HUE_ROOM_STATES: tuple[str, ...] = ("idle", "armed", "streaming", "taken_over")
HUE_WORDS: dict[str, str] = {"idle": "Not following", "armed": "Ready: waiting for the music",
                             "streaming": "Following the music", "taken_over": "Taken over by another app"}
HUE_NOT_FOLLOWING: tuple[str, ...] = ("off", "waiting_room", "not_built", "no_config")   # driver states read "idle"
HUE_RELEASE_MAX_S = 17.0           # a release never waits longer (room's adapter waits 20 s; the API adds 2 s)


def hue_room_state(status: Any) -> str:
    """The HUE_ROOM_STATES word for a HueStatus (or anything with ``state`` and ``wanted``)."""
    s = status if isinstance(status, dict) else {}
    st = s.get("state")
    if st == "taken_over":
        return "taken_over"
    if not s.get("wanted") or st in HUE_NOT_FOLLOWING:
        return "idle"
    return "streaming" if st == "streaming" else "armed"


def hue_answer(status: Any, boot_id: Optional[str] = None) -> HueAnswer:
    """The Hue routes' answer (HueAnswer) for a HueStatus; ``boot_id`` is the API's (this process)."""
    s = status if isinstance(status, dict) else {}
    rs = hue_room_state(s)
    detail = s.get("detail")
    return {"state": rs, "lights": [x for x in (s.get("asked") or []) if isinstance(x, str)],
            "boot_id": boot_id if isinstance(boot_id, str) else None,
            "words": HUE_WORDS[rs], "detail": detail if isinstance(detail, str) else None,
            "wanted": [x for x in (s.get("wanted") or []) if isinstance(x, str)],
            "unknown": [x for x in (s.get("unknown") or []) if isinstance(x, str)]}


def hue_off_status(output: Optional[str], detail: str, asked: Optional[list] = None) -> HueStatus:
    """The HueStatus with no hue output running (it reads ``off``; the routes answer ``idle``). ``asked``: what room
    asked while the output is off (the Rig keeps it for on again), reported as the routes' ``lights``."""
    return {"state": "off", "detail": detail, "enabled": False, "output": output, "wanted": [],
            "asked": [x for x in (asked or []) if isinstance(x, str)], "streaming": False,
            "area": None, "channels": {}, "frames": 0, "dtls": False, "unknown": [], "room_state": "idle",
            "words": HUE_WORDS["idle"]}


class ScreenPick(TypedDict):
    """The director's hold after a person picked a palette on a screen (README S5.8). Persisted in state.json under
    director.nudges.screen_pick."""
    palette: Any                   # what the room's event carried (a catalog name, or the renderer's own shape)
    label: str                     # page words for the chip: the catalog label, the name, or "custom colours"
    screen: str                    # where it was picked
    at: float                      # epoch s
    source: Optional[str]          # the event's source ("web", ...), None when the room didn't send one


class Driver(Protocol):
    """One output (README S5.4). outputs.py builds one per enabled, built output from its driver kind; a runner
    thread ("show-out-<id>") calls plan/set_frame; show-brain calls configure/tick; status from any thread."""
    kind: str                      # DRIVERS
    output_id: str
    caps: DriverCaps

    def configure(self, out: OutputConfig, rig: RigView) -> None: ...   # show-brain: at start and on every reload
    def start(self) -> None: ...                       # show-brain: before its runner starts (no I/O that blocks)
    def stop(self, timeout: float = 2.0) -> None: ...  # show-brain: after its runner stopped; leaves the device in its
                                                       # own failsafe (hue: stops the area; artnet: just stops sending)
    def plan(self, t_last_s: float, now_s: float, active: bool) -> float: ...   # runner: the next send time (monotonic)
    def set_frame(self, frame: Frame) -> None: ...     # runner: encode and send now; never blocks over 1 ms
    def tick(self, now_s: float) -> None: ...          # show-brain, 1 Hz: discovery, takeover checks, health
    def status(self) -> OutputStatus: ...              # any thread


# -- room space (docs/LIGHTS.md §3) ------------------------------------------------------------------------------------
# One room frame in metres: x east, y north, z up, origin the south-west floor corner. space.py reads config/room/
# space.yaml (hand) with space.added.yaml (the Lights page's Room map and Move it) under it; every appliance kind reads
# directions and places through it, so "upwards" or "the piano" means the same thing to a screen, a lamp and a mover.
#: A flow's direction (FlowData.dir). left/right are as seen facing the room's front; cw/ccw as seen from above (the
#: floor plan, N -> E -> S -> W); towards/away are about the flow's ``at`` (cw/ccw/outward/inward turn round it).
FLOW_DIRS: tuple[str, ...] = ("still", "up", "down", "left", "right", "cw", "ccw", "outward", "inward", "towards",
                              "away")
FLOW_OPPOSITE: dict[str, str] = {"up": "down", "down": "up", "left": "right", "right": "left", "cw": "ccw", "ccw": "cw",
                                 "outward": "inward", "inward": "outward", "towards": "away", "away": "towards"}
SPACE_FILE = CONFIG_ROOM_DIR / "space.yaml"         # hand-written: the room, its places and appliances (space.py)
SPACE_ADDED_FILE = CONFIG_ROOM_DIR / "space.added.yaml"   # written only by the rig API (the Room map, Move it's teach
                                                    # points and poses), whole and atomically; merged under space.yaml
MOUNTS: tuple[str, ...] = ("ceiling", "floor", "wall", "truss")   # how a mover hangs (the pose solve's hint)


class Surface(TypedDict):
    """A plane in the room: the floor, the ceiling, wall-n/e/s/w (from the room's size) or a named one (space.yaml
    ``surfaces``). A point on it is origin + u·û + v·v̂ (space.surface_point)."""
    name: str
    label: str                     # page words ("Floor", "North wall", "TV wall")
    origin: list[float]            # m
    normal: list[float]            # unit, into the room (the floor's is up, the ceiling's down)
    u: list[float]                 # unit, along it: left to right as seen from inside facing it (floor, ceiling: east)
    v: list[float]                 # unit: up on a wall; north on the floor and ceiling
    size: Optional[list[float]]    # [w, h] along u, v from origin; None = unbounded


class SpaceView(TypedDict):
    """space.Store.view(): the last good space.yaml + space.added.yaml, merged and resolved. Swapped by reference,
    never mutated after the swap; GET /api/v1/rig/space answers it (+ the page's ``targets``)."""
    revision: str                  # sha1 of what's in use, 12 hex ("" before the first load)
    error: Optional[str]           # the last load error (the last good view stays)
    problems: list[str]            # page words, one per thing it couldn't use as written
    room: dict                     # {"size": [X, Y, Z] | None, "front": "north"|"east"|"south"|"west",
                                   #  "centre": [x, y, z] | None, "right": [x, y, z], "front_vec": [x, y, z]}
    surfaces: dict[str, Surface]
    points: dict[str, dict]        # name -> {"label", "at", "device"?, "from": "hand" | "page" | <fixture id>}
    regions: dict[str, dict]       # name -> {"label", "surface", "centre", "size"}
    appliances: dict[str, dict]    # id -> {"at": [x, y, z] | None, "kind"?, "label"?, "mount"?, "plane"?: {normal,
                                   #        up, size}, "teach": [TeachSample], "pose": MoverPose | None,
                                   #        "editable": bool}  (editable: its place isn't set in the hand file)
    extent: Optional[list[list[float]]]   # [[min xyz], [max xyz]]: the room box, else the placed things' bounding
                                   # box; None: nothing is placed and the room's size isn't set


class TeachSample(TypedDict):
    """One taught place for a mover (Move it's "Point it at…"): where it was held when its beam was on the place."""
    pan: float                     # centred degrees, as it was held
    tilt: float
    point: Optional[str]           # the space name it was pointed at
    at: list[float]                # that place's room position when taught (kept if the place moves later)
    t: str                         # ISO time


class MoverPose(TypedDict):
    """A mover's solved mounting (space.solve_pose): the beam for (pan, tilt) is rot · b_f(pan_sign·pan,
    tilt_sign·tilt + tilt_offset), b_f(θ, φ) = (sin φ cos θ, sin φ sin θ, cos φ) in its own frame (z = the pan axis)."""
    at: list[float]                # solved position, m
    rot: list[float]               # rotation vector (axis × angle, rad): fixture frame -> room
    tilt_offset_deg: float
    pan_sign: int                  # +1 | -1 (pan turns the other way)
    tilt_sign: int                 # +1 | -1
    rms_deg: float                 # the fit over its samples
    worst_deg: float
    samples: int
    solved_at: str                 # ISO time


class PoseFit(TypedDict):
    """space.solve_pose's answer (the teach route's ``fit``)."""
    pose: Optional[MoverPose]      # None while it needs more places
    rms_deg: Optional[float]
    worst_deg: Optional[float]
    worst_index: Optional[int]     # the sample that fits worst (the page can offer to drop it)
    spread_deg: float              # the widest angle between two taught directions
    samples: int
    level: str                     # "good" | "ok" | "rough" | "bad" | "need_more"
    words: str                     # page words ("Spot on (within 1.5°).")
    pos_m: Optional[float]         # how far its solved position may be off (m; taught angles good to
                                   # calibration.angle_sigma_deg); over pos_sigma_m the fit reads rough. None: unknown


class Aim(TypedDict):
    """space.aim's answer: the pan and tilt (centred degrees) that put the beam on a room point."""
    pan: float
    tilt: float
    reachable: bool                # False: the nearest it can get inside its range and limits (never refused)
    error_deg: float               # the angle between that beam and the point (0 when reachable)


class ScreenFlow(TypedDict):
    """The flow as one screen sees it (space.screen_flow; FlowData.screens values, docs/LIGHTS.md §3.5, §9)."""
    fixture: str                   # the screen fixture id ("screen.north")
    x: float                       # the flow in the screen's plane: x right, y up, as a viewer facing it sees
    y: float
    angle_deg: float               # atan2(y, x): 0 = towards its right, 90 = up (counter-clockwise as seen)
    inplane: float                 # 0..1: |(x, y)|
    depth: float                   # -1..1: + toward the viewer (out of the screen), - into it
    radial: int                    # +1 expand from the screen's centre (outward, away), -1 contract (inward, towards),
                                   # 0 otherwise
    spin: int                      # +1 cw, -1 ccw (cw/ccw flows; the same sense on every screen), else 0
    phase: float                   # 0..1: the screen's place along the flow (flow_coord of its centre)
