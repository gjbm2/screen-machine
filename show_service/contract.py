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
MUSIC_CONTEXT = "music.context"     # reserved for P3 (local ML, sections); nothing publishes it in Stage 3
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
    "change_looks", "change_rate", "dwell_s", "relax_on_talk", "listener", "palettes", "key_cct", "lamp_chroma_max",
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
    listener: bool                 # this vibe lets the LLM listener run (with the AI listening switch and the target's)
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


class SectionRef(TypedDict):
    id: int                        # +1 per section (Stage 3: per look change; P3: the novelty detector's)
    label: Optional[str]           # the listener's section word when it agrees, else None
    phase: float                   # 0..1 through the section (bars_in_section / expected), 0 when unknown
    bars_in_section: int


class DirectionData(TypedDict):
    """room.direction: the renderer's direction vocabulary (stream_service/director.py validate_direction: unknown keys
    are ignored there) plus arousal/tension/section/density/groove. Numbers 0..1. intensity stays 1.0 (it is binding
    depth in the renderer, not brightness)."""
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
    beat_lock: bool                # the vibe allows it and the clock's gate is "beat"
    loose: bool                    # the card's "following loosely": the vibe wants the beat, the clock is unsure


EVENT_KINDS: tuple[str, ...] = ("look_change", "section_change", "phrase", "build", "drop_armed", "drop", "accent",
                                "cut")


class EventData(TypedDict):
    """room.event: stamped at ``at_ms`` (acoustic, the clock's timeline), published when decided."""
    kind: str                      # EVENT_KINDS
    at_ms: float
    t_ms: float                    # when it was published
    seq: int
    look_id: NotRequired[str]
    beat: NotRequired[float]       # the clock's beat number at at_ms
    bar: NotRequired[int]
    phrase: NotRequired[int]
    strength: NotRequired[float]
    detail: NotRequired[str]


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
                               "energy_above", "talking", "quiet", "between")
COMBINATORS: tuple[str, ...] = ("all", "any", "not")
SituationSource = Literal["midi", "sonos", "audio", "listener", "time"]
SITUATION_RANK: dict[str, int] = {"midi": 0, "sonos": 1, "audio": 2, "listener": 3, "time": 4}   # §8.9 priority


class SituationDef(TypedDict):
    """One word of the vocabulary (GET /api/v1/situations; the room adapter passes it to the Auto card)."""
    id: str                        # SITUATIONS
    label: str                     # page words: "the piano is playing"
    description: str
    source: str                    # SituationSource
    rank: int                      # SITUATION_RANK[source]
    params: dict[str, dict]        # param -> JSON schema (+ x-ui, default from show.yaml); for_s is on every word
    available: bool                # its inputs exist now (False: it reads unknown)


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


# -- the listener (§7) and Describe it (§7.4) ------------------------------------------------------------------------------
INSIGHT_CONTENT: tuple[str, ...] = ("music", "speech", "mixed", "silence", "noise")
SECTION_LABELS: tuple[str, ...] = ("intro", "verse", "pre_chorus", "chorus", "bridge", "breakdown", "build", "drop",
                                   "solo", "outro", "unknown")
TRAJECTORY_STATES: tuple[str, ...] = ("building", "peak", "sustaining", "releasing", "steady", "winding_down")


class InsightData(TypedDict):
    """music.insight: one listener verdict as validated (§7.3 output), with its window. The model's own words are
    checked against vocab.yaml; a word outside it becomes "unknown" (genre) or is dropped (moods)."""
    t_ms: float                    # publish time
    window: dict[str, float]       # {"t0_ms", "t1_ms"}: the clip's acoustic span
    segment_id: Optional[int]      # P3's DSP segment the clip covered (None in Stage 3)
    stream: str
    model: str
    latency_ms: float
    content: str                   # INSIGHT_CONTENT
    genre: dict[str, Any]          # {"label": vocab genre | "unknown", "detail": str, "confidence": 0..1}
    instrumentation: list[str]
    vocals: str                    # "none" | "male" | "female" | "mixed" | "choir" | "unknown"
    mood: dict[str, Any]           # {"words": [<= 3 vocab moods], "confidence"}
    section: dict[str, Any]        # {"segment_id": int | None, "label": SECTION_LABELS, "confidence"}
    trajectory: dict[str, Any]     # {"state": TRAJECTORY_STATES, "eta_to_change_s": float | None, "confidence"}
    track_change: bool
    same_track_as_previous: Optional[bool]
    evidence: str                  # <= 25 words


class Beliefs(TypedDict):
    """listener.beliefs(): the fused, decaying view the director (L2), the card and situations read (§7.1)."""
    state: str                     # "none" | "fresh" | "holding" | "fading"
    age_s: Optional[float]         # since the last verdict
    genre: Optional[dict]          # {"word", "conf"}: changes only on two agreeing reads (or a track change)
    moods: list[dict]              # [{"word", "conf"}], conf decays (half-life 45 s)
    instruments: list[str]
    vocals: Optional[str]
    section: Optional[dict]        # {"label", "conf"}
    trajectory: Optional[dict]     # {"state", "conf", "eta_s"}
    content: Optional[str]


class ListenerView(TypedDict):
    """GET /api/v1/listening and state's "listener"."""
    on: bool                       # the AI listening switch (persisted; default False until the owner turns it on)
    effective: bool                # clips are going now
    why: Optional[str]             # page words when not: "AI listening is off", "this mode doesn't listen", ...
    stream: Optional[str]
    next_in_s: Optional[float]
    cadence_s: float
    calls_today: int
    spend_today_usd: float
    budget_usd: Optional[float]
    model: Optional[str]           # the first model llm.yaml lists for the task, after the startup probe
    last: Optional[InsightData]
    errors: int


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
    hearing: dict[str, Any]        # {"text": "soul · relaxed" | "Quiet" | "Talking" | "No sound coming in",
                                   #  "level": 0..1, "loose": bool, "stream": str | None, "test": bool}
    vibe: dict[str, Any]           # {"name": str | None, "label": str, "known": bool}
    steps: StepsView
    palette: dict[str, Any]        # {"pick": "auto"|"art"|name, "current": name, "choices": [PaletteChoice] (Auto,
                                   #  From the art, then the vibe's first 5)}
    look: dict[str, Any]           # {"id": str, "keep_in_s": float | None, "next_pending": bool}
    house: bool
    low: bool
    chips: list[ChipData]
    listening: dict[str, Any]      # {"on": bool, "effective": bool, "why": str | None}
    describe: dict[str, Any]       # {"state": "idle" | "working" | "done", "text": str | None, "reply": str | None,
                                   #  "suggest": {"mode", "label"} | None}
    auto: dict[str, Any]           # {"on": bool | None, "state", "line", "chip": AutoAnnouncement | None,
                                   #  "suggest": AutoAnnouncement | None}
    tests: dict[str, SourceStatus]   # by source (the console's; the card ignores it)


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
    set_look_out: Optional[Callable[..., None]] = None                # VirtualMaestro.set_look (hue_depth, beat_lock, reset)
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
    situations: Optional[Callable[[], dict[str, SituationReading]]] = None   # situations' snapshot (default params)
    beliefs: Optional[Callable[[], Optional[Beliefs]]] = None         # listener.beliefs
    vibe: Optional[Callable[[], Optional[VibeSettings]]] = None       # director: the effective vibe now
    target: Optional[Callable[[], Optional[VibeTarget]]] = None       # director: the show target now
    engaged: Optional[Callable[[], bool]] = None                      # director: a nudge is active (Auto won't leave)
    on_mode_change: list[Callable[[Optional[str], str], None]] = field(default_factory=list)  # RoomLink calls each
                                                                       # (old, new) on show-brain when mode.current changes
    notify: Optional[Notify] = None                                   # api.py
    persist: Optional[Callable[[], None]] = None                      # brain.dirty(): write state.json soon


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
