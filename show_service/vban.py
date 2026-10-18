"""VBAN rev 13 on the wire: audio, the sender heartbeat, SUBSCRIBE and piano MIDI (docs/SHOW_ENGINE.md §3.1, §3.5).

Source: VB-Audio, *VBAN Protocol* specification, revision 13 (Sep 2025), pp. 6-10 (header, SR list, data types,
codec) and pp. 19-21 (TEXT sub-protocol). Every packet is one UDP datagram on port 6980 (the spec's default),
little-endian, a 28-byte header then at most 1436 bytes of data (1464 in all):

  off len field       AUDIO (sub-protocol 0x00)                      TEXT (sub-protocol 0x40)
    0   4 "VBAN"
    4   1 format_SR   bits 0-4 rate index into SR_LIST,              bits 0-4 bit-rate index (0 = none: we send 0),
                      bits 5-7 sub-protocol (0x00)                   bits 5-7 sub-protocol (0x40)
    5   1 format_nbs  frames per packet - 1 (1..256)                 0 (not used)
    6   1 format_nbc  channels - 1 (1..256)                          channel ident (we send 0)
    7   1 format_bit  bits 0-2 data type, bit 3 zero,                data type BYTE8 (0) | text format in bits 4-7
                      bits 4-7 codec (PCM 0x00 only here)            (ASCII 0x00, UTF-8 0x10: we send UTF-8)
    8  16 streamname  ASCII, NUL padded (here 1-16 characters)
   24   4 nuFrame     uint32 counter: +1 per packet of a stream (name + sub-protocol), wraps at 2**32
   28  .. data        interleaved PCM frames (WAVE order)            one UTF-8 JSON object, <= 1436 bytes

Sample rates (spec p. 8, index: Hz): 0 6000, 1 12000, 2 24000, **3 48000**, 4 96000, 5 192000, 6 384000,
7 8000, 8 16000, 9 32000, 10 64000, 11 128000, 12 256000, 13 512000, 14 11025, 15 22050, **16 44100**, 17 88200,
18 176400, 19 352800, 20 705600; 21-31 undefined. Data types (p. 9): 0 BYTE8 (unsigned, 128 = 0), 1 INT16,
2 INT24, 3 INT32, 4 FLOAT32, 5 FLOAT64, 6 12-bit, 7 10-bit.

Our conventions on top of the spec:
  * PCM is float32 in -1..1 on our side, shaped (frames, channels). An N-bit integer's full scale is 2**(N-1):
    decode ``x / 2**(N-1)``; encode ``round(x * 2**(N-1))`` clipped to the type's range, so +1.0 lands one LSB
    under full scale and everything else round-trips within half an LSB. Floats pass through unclipped; a
    non-finite sample is replaced (NaN 0, +-inf +-1) on encode and on decode alike, so no NaN or infinity ever goes
    onto the wire (third-party receivers too) or comes off it. BYTE8 is decoded only; 12- and 10-bit are refused.
  * Packet size: senders call :func:`frames_per_packet`: 128 frames (2.7 ms at 48 kHz) when they fit in 1436 B,
    else the largest multiple of 32 that does (4 ch INT24: 96; 4 ch FLOAT32: 64), else whatever fits.
  * :func:`parse` never raises on what arrives off the network: anything it can't use is ``None``, for the
    receiver to count. The pack functions raise ``ValueError`` on bad arguments (a bug at the sender).
  * Arrays returned by :func:`parse` are always copies, so the receive buffer can be reused.

Counters. ``nuFrame`` counts per (stream name, sub-protocol): a heartbeat, a TEXT packet named after its audio
stream, has its own TEXT counter, and the audio packets' counter is continuous on its own (never shared with
heartbeats: the receiver tracks the AUDIO counter only, and a shared one would read as a lost packet every 500 ms).

Sender rules (audio). ``nuFrame`` and the sample index ``n`` stay locked to the ADC clock: one packet, one counter
step, ``frames`` more samples, always. On an input overflow (ALSA -EPIPE, a WASAPI DATA_DISCONTINUITY, a PortAudio
input overflow) the sender advances the counter and ``n`` over the packets it lost, sends nothing for them (the
receiver zero-fills them as AUDIO_GAP and its index stays on time), and counts the overflow in ``xruns``. If more
than 250 ms is lost, it starts a new ``sess`` instead. A sender must never keep counting while capture time is
lost: the timeline can only see that through the heartbeat (timeline.CaptureMap) or seconds later.

Clocks. ``t_cap_ns`` and MIDI ``t_ns`` are on the sender's own monotonic clock in integer ns, and one process uses
one clock for heartbeats and MIDI:
  * Linux (the kiosk unit): ``time.monotonic_ns()`` (CLOCK_MONOTONIC).
  * Windows, Python 3.12 (the PC22 agent, the test piano): ``time.perf_counter_ns()`` (QueryPerformanceCounter:
    system-wide, 100 ns ticks; the clock PortAudio's WASAPI ``time_info`` uses). Never ``time.monotonic_ns()``
    there: in 3.12 it is GetTickCount64, 15.625 ms steps, which would scatter notes 0-15.6 ms early and bias the
    capture map by as much.
  * ``t_cap_ns`` from PortAudio (sounddevice, PyAudioWPatch): take only the latency difference from it, never its
    absolute time: in the callback, ``t_cap_ns = now_ns - round((time_info.currentTime -
    time_info.inputBufferAdcTime) * 1e9)``, ``now_ns`` read from the clock above on entry. Without an ADC time,
    ``now_ns - in_lat`` (the device's reported input latency, the ``in_lat_ms`` it sends).

TEXT messages (one compact JSON object per packet; readers ignore keys they don't know; ``v`` is 1):
  SUBSCRIBE   stream ``sub``, receiver -> sender, every 1 s from the receive socket; the sender streams to the
              source address of the latest one and stops 5 s after it:
              {"v":1,"subscribe":["room-piano"],"off":["room-mic"],"from":"show_rt"}
                subscribe  [stream]: capture and send these (an empty list is still a SUBSCRIBE: a sender whose
                           only stream is off keeps getting them, so it keeps its off heartbeats)
                off        [stream], optional (16 Oct, the mic on demand; README S5.16): keep these CLOSED (the
                           capture device shut), but go on sending each one's heartbeat every 500 ms with state
                           "off", ``frame``/``n``/``t_cap_ns`` null and ``devices`` (so the Room page still lists
                           the mics). A stream in neither list, or no SUBSCRIBE for 5 s: stop sending, close it.
                devices    {stream: name}, optional: the capture device the receiver wants for that stream (the
                           Room page's Mic choice). A sender that can't find the name, or gets no hint, keeps its
                           default; the heartbeat's ``dev`` says what it really uses.
  heartbeat   stream = the audio stream's own name (its own TEXT counter), sender -> receiver every 500 ms:
              {"v":1,"sess","frame","n","t_cap_ns","in_lat_ms","dev","state","xruns","peak_dbfs","rms_dbfs","clips"}
                sess       str   the sender's session of this stream: new on a sender restart, a device re-open,
                                 or more than 250 ms of capture lost
                frame      int   nuFrame of the audio packet whose first sample is ``n`` (null before any audio)
                n          int   the sender's sample index of that sample: frames since ``sess`` began, counting
                                 the packets skipped over an overflow (Sender rules)
                t_cap_ns   int   the sender's clock (Clocks), ns, when sample ``n`` reached the ADC
                in_lat_ms  float the input latency between the ADC and the callback (reported for health)
                dev        str   capture device name
                state      str   ok | no_device | idle | xrun | off (the SUBSCRIBE's ``off``: device closed)
                xruns      int   input overflows since ``sess`` began
                peak_dbfs, rms_dbfs  [float per channel] over the last 500 ms, floored at -120 (never -inf)
                clips      int   samples at or beyond full scale since ``sess`` began
                devices    [str], optional: the capture devices this sender could use for the stream (the Room
                                 page's Mic choices), at most DEVICES_MAX names of at most DEVICE_NAME_MAX chars;
                                 make_heartbeat drops names from the end until the packet fits 1436 B
  MIDI events stream ``room-piano`` (or ``test-piano``), within 2 ms of an event; ``t_ns`` is the sender's clock
              (Clocks: the heartbeat's timebase); ``prev`` repeats the last two batches so one lost packet heals
              at once; at most MIDI_BATCH_MAX events per batch:
              {"v":1,"seq":1234,"ev":[[t_ns,status,d1,d2],...],"prev":[[1233,[...]],[1232,[...]]],"sess":"..."}
  MIDI state  same stream, every 500 ms: the keys physically down (88 strike velocities, index 0 = A0 = MIDI 21,
              0 = up) and the last pedal values; ``seq`` is the latest event batch sent before the snapshot (a
              state does not use up a seq); ``t_ns`` is when it was taken:
              {"v":1,"seq":1240,"state":{"keys":[...88...],"cc64":0,"cc66":0,"cc67":0},"t_ns":...,"sess":"..."}
  ``seq`` counts event batches, +1 each, from 1 per sender session. ``sess`` is mandatory from our senders (the
  kiosk unit, the test piano: :func:`midi_events_obj` and :func:`midi_state_obj` require it, 1-64 characters): new
  one per STREAM (its device session: new on a sender start or a MIDI device re-open; independent of any audio
  stream's heartbeat sess, so an audio hiccup never restarts the note table). Readers still accept MIDI without it
  (third parties).
  Receiving (:func:`midi_seq_verdict`, the one rule midi.py applies): the wire restarted when ``sess`` changed, or
  when a batch or state's ``seq`` is more than MIDI_REORDER (8, wider than MIDI_PREV) behind the last; otherwise a
  batch with ``seq`` <= the last is a duplicate and a state with ``seq`` < the last is stale. (A sender without
  ``sess`` that restarts within 8 batches of where it was loses up to 8 batches until ``seq`` passes the old one:
  hence ``sess``.)
"""

from __future__ import annotations

import json
import math
import struct
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Mapping, Sequence

import numpy as np

PORT = 6980
MAGIC = b"VBAN"
HEADER_SIZE = 28
MAX_DATA = 1436
MAX_PACKET = HEADER_SIZE + MAX_DATA          # 1464
MAX_FRAMES = 256
MAX_CHANNELS = 256
STREAM_NAME_MAX = 16
COUNTER_MOD = 1 << 32

PROTO_MASK = 0xE0
PROTO_AUDIO = 0x00
PROTO_SERIAL = 0x20
PROTO_TEXT = 0x40
PROTO_SERVICE = 0x60
SR_MASK = 0x1F

SR_LIST: tuple[int, ...] = (6000, 12000, 24000, 48000, 96000, 192000, 384000,
                            8000, 16000, 32000, 64000, 128000, 256000, 512000,
                            11025, 22050, 44100, 88200, 176400, 352800, 705600)
_SR_INDEX = {sr: i for i, sr in enumerate(SR_LIST)}

BYTE8, INT16, INT24, INT32, FLOAT32, FLOAT64 = 0, 1, 2, 3, 4, 5
DATATYPE_MASK = 0x07
CODEC_MASK = 0xF0
CODEC_PCM = 0x00
SAMPLE_BYTES = {BYTE8: 1, INT16: 2, INT24: 3, INT32: 4, FLOAT32: 4, FLOAT64: 8}
FORMAT_NAMES = {BYTE8: "BYTE8", INT16: "INT16", INT24: "INT24", INT32: "INT32", FLOAT32: "FLOAT32",
                FLOAT64: "FLOAT64"}
PACKABLE = (INT16, INT24, INT32, FLOAT32, FLOAT64)

TXT_ASCII = 0x00
TXT_UTF8 = 0x10

DEFAULT_FRAMES = 128
SUBSCRIBE_STREAM = "sub"
DEVICES_MAX = 32                             # heartbeat ``devices``: names, and each name's length
DEVICE_NAME_MAX = 128
SUBSCRIBE_EVERY_S = 1.0
SUBSCRIBE_TTL_S = 5.0
HEARTBEAT_EVERY_S = 0.5
HEARTBEAT_STATES = ("ok", "no_device", "idle", "xrun", "off")   # "off": the SUBSCRIBE keeps it closed (S5.16)
DBFS_FLOOR = -120.0

MIDI_STATE_EVERY_S = 0.5
MIDI_BATCH_MAX = 32                          # 32 worst-case events (34 B each) + envelope fit in 1436 B
MIDI_PREV = 2
MIDI_REORDER = 8                             # seq further behind the last than this: a restart, not a straggler
MIDI_SESS_MAX = 64                           # our senders' sess length: a worst-case batch still fits
PIANO_KEYS = 88
PIANO_LOW = 21                               # MIDI note of A0, keys[0]

_HEADER = struct.Struct("<4sBBBB16sI")


# -- dataclasses ------------------------------------------------------------------------------------------------------
@dataclass(slots=True)
class AudioPacket:
    stream: str
    counter: int
    sr: int
    channels: int
    fmt: int                                 # INT16 ... FLOAT64 (BYTE8 decoded too)
    frames: int
    pcm: np.ndarray                          # float32 (frames, channels), a private copy


@dataclass(slots=True)
class TextPacket:
    stream: str
    counter: int
    obj: dict                                # the JSON object
    text_fmt: int = TXT_UTF8
    channel: int = 0                         # format_nbc: the TEXT channel ident


@dataclass(slots=True)
class Heartbeat:
    v: int
    sess: str
    frame: int | None
    n: int | None
    t_cap_ns: int | None
    in_lat_ms: float
    dev: str
    state: str
    xruns: int
    peak_dbfs: list[float] = field(default_factory=list)
    rms_dbfs: list[float] = field(default_factory=list)
    clips: int = 0
    devices: list[str] | None = None          # the sender's capture choices (None: it doesn't say)


MidiEvent = tuple[int, int, int, int]        # (t_ns, status, d1, d2)


@dataclass(slots=True)
class MidiBatch:
    seq: int
    ev: list[MidiEvent]
    prev: list[tuple[int, list[MidiEvent]]]  # [(seq - 1, events), (seq - 2, events)], newest first
    sess: str | None = None
    v: int = 1


@dataclass(slots=True)
class MidiState:
    seq: int
    keys: list[int]                          # 88 strike velocities, 0 = up; keys[i] is MIDI note 21 + i
    cc64: int                                # damper
    cc66: int                                # sostenuto
    cc67: int                                # soft
    t_ns: int
    sess: str | None = None
    v: int = 1


# -- small helpers ----------------------------------------------------------------------------------------------------
def sr_index(sr: int) -> int:
    """The SR_LIST index of ``sr`` (48000 -> 3, 44100 -> 16); ValueError if VBAN has no index for it."""
    try:
        return _SR_INDEX[int(sr)]
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"VBAN has no sample-rate index for {sr!r}") from None


def counter_delta(prev: int, cur: int) -> int:
    """Packets from ``prev`` to ``cur`` modulo 2**32: 1 = the next one; a value above 2**31 is a step backwards."""
    return (int(cur) - int(prev)) % COUNTER_MOD


def frames_per_packet(channels: int, fmt: int, preferred: int = DEFAULT_FRAMES) -> int:
    """Frames per packet that keep the data within 1436 B: ``preferred`` (128) if it fits, else the largest
    multiple of 32 that fits (4 ch INT24 -> 96), else the largest count that fits. ValueError if not even one
    frame fits (too many channels for the type)."""
    if fmt not in SAMPLE_BYTES:
        raise ValueError(f"unknown VBAN data type {fmt!r}")
    if not 1 <= channels <= MAX_CHANNELS:
        raise ValueError(f"channels {channels} outside 1..{MAX_CHANNELS}")
    fit = min(MAX_FRAMES, MAX_DATA // (channels * SAMPLE_BYTES[fmt]))
    if fit < 1:
        raise ValueError(f"{channels} ch of {FORMAT_NAMES[fmt]} don't fit one frame in {MAX_DATA} B")
    preferred = max(1, min(MAX_FRAMES, int(preferred)))
    if fit >= preferred:
        return preferred
    if fit >= 32:
        return fit // 32 * 32
    return fit


def _name_bytes(stream: str) -> bytes:
    if not isinstance(stream, str):
        raise ValueError("stream name must be a str")
    try:
        raw = stream.encode("ascii")
    except UnicodeEncodeError:
        raise ValueError(f"stream name {stream!r} is not ASCII") from None
    if not 1 <= len(raw) <= STREAM_NAME_MAX or b"\x00" in raw:
        raise ValueError(f"stream name {stream!r} must be 1-{STREAM_NAME_MAX} ASCII characters")
    return raw


def _header(proto_sr: int, nbs: int, nbc: int, bit: int, stream: str, counter: int) -> bytes:
    return _HEADER.pack(MAGIC, proto_sr, nbs, nbc, bit, _name_bytes(stream), int(counter) % COUNTER_MOD)


def _is_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _is_num(x: Any) -> bool:
    return (isinstance(x, (int, float)) and not isinstance(x, bool)) and math.isfinite(x)


# -- PCM codecs (vectorised) ------------------------------------------------------------------------------------------
def encode_pcm(pcm: np.ndarray, fmt: int) -> bytes:
    """Interleaved little-endian bytes for float ``pcm`` (frames, channels) in ``fmt``."""
    x = np.ascontiguousarray(pcm)
    if fmt in (FLOAT32, FLOAT64):
        f = x.astype("<f4" if fmt == FLOAT32 else "<f8", copy=False)   # (a float64 overflow becomes inf here)
        if not np.isfinite(f).all():
            f = np.nan_to_num(f, nan=0.0, posinf=1.0, neginf=-1.0)       # a copy: the caller's array is untouched
        return f.tobytes()
    if fmt not in (INT16, INT24, INT32):
        raise ValueError(f"can't pack VBAN data type {fmt!r}")
    f = np.nan_to_num(x.astype(np.float64).reshape(-1), nan=0.0, posinf=1.0, neginf=-1.0)
    if fmt == INT16:
        return np.clip(np.rint(f * 32768.0), -32768, 32767).astype("<i2").tobytes()
    if fmt == INT24:
        v = np.clip(np.rint(f * 8388608.0), -8388608, 8388607).astype("<i4")
        return v.view(np.uint8).reshape(-1, 4)[:, :3].tobytes()
    return np.clip(np.rint(f * 2147483648.0), -2147483648, 2147483647).astype("<i4").tobytes()


def decode_pcm(data: bytes | memoryview, fmt: int, frames: int, channels: int) -> np.ndarray:
    """float32 (frames, channels) from interleaved little-endian ``data``; the caller has checked the length."""
    if fmt == INT16:
        out = np.frombuffer(data, "<i2").astype(np.float32) * np.float32(1.0 / 32768.0)
    elif fmt == INT24:
        b = np.frombuffer(data, np.uint8).reshape(-1, 3)
        w = np.zeros((b.shape[0], 4), np.uint8)
        w[:, 1:] = b                                       # the 24 bits in the top of an int32 ...
        out = (w.view("<i4").reshape(-1) >> 8).astype(np.float32) * np.float32(1.0 / 8388608.0)  # ... sign kept
    elif fmt == FLOAT32:
        out = np.frombuffer(data, "<f4").astype(np.float32)          # astype copies
    elif fmt == INT32:
        out = (np.frombuffer(data, "<i4").astype(np.float64) * (1.0 / 2147483648.0)).astype(np.float32)
    elif fmt == FLOAT64:
        out = np.frombuffer(data, "<f8").astype(np.float32)
    elif fmt == BYTE8:
        out = (np.frombuffer(data, np.uint8).astype(np.float32) - np.float32(128.0)) * np.float32(1.0 / 128.0)
    else:
        raise ValueError(f"can't decode VBAN data type {fmt!r}")
    if fmt in (FLOAT32, FLOAT64) and not np.isfinite(out).all():
        np.nan_to_num(out, copy=False, nan=0.0, posinf=1.0, neginf=-1.0)
    return out.reshape(frames, channels)


# -- packing ----------------------------------------------------------------------------------------------------------
def pack_audio(stream: str, counter: int, pcm: np.ndarray, sr: int, fmt: int = INT16) -> bytes:
    """One AUDIO packet. ``pcm``: float (frames, channels) in -1..1 (1-D = mono); ``counter`` is taken mod 2**32.
    ValueError on a rate VBAN can't name, more than 256 frames or channels, or data over 1436 B."""
    if fmt not in PACKABLE:
        raise ValueError(f"can't pack VBAN data type {fmt!r}")
    x = np.asarray(pcm)
    if x.ndim == 1:
        x = x.reshape(-1, 1)
    if x.ndim != 2:
        raise ValueError("pcm must be (frames, channels)")
    frames, channels = x.shape
    if not 1 <= frames <= MAX_FRAMES:
        raise ValueError(f"{frames} frames outside 1..{MAX_FRAMES}")
    if not 1 <= channels <= MAX_CHANNELS:
        raise ValueError(f"{channels} channels outside 1..{MAX_CHANNELS}")
    size = frames * channels * SAMPLE_BYTES[fmt]
    if size > MAX_DATA:
        raise ValueError(f"{size} B of audio data exceeds {MAX_DATA} B (use frames_per_packet)")
    head = _header(PROTO_AUDIO | sr_index(sr), frames - 1, channels - 1, fmt | CODEC_PCM, stream, counter)
    return head + encode_pcm(x, fmt)


def text_bytes(obj: dict) -> bytes:
    """The compact UTF-8 JSON we put in a TEXT packet (strict JSON: no NaN or infinities)."""
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def pack_text(stream: str, counter: int, obj: dict) -> bytes:
    """One TEXT packet carrying ``obj`` as UTF-8 JSON. ValueError if it isn't a dict, isn't strict JSON, or is
    over 1436 B (one message per packet, as Voicemeeter does: no multi-packet text)."""
    if not isinstance(obj, dict):
        raise ValueError("a VBAN text message is one JSON object")
    data = text_bytes(obj)
    if len(data) > MAX_DATA:
        raise ValueError(f"text message of {len(data)} B exceeds {MAX_DATA} B")
    return _header(PROTO_TEXT, 0, 0, TXT_UTF8, stream, counter) + data


# -- parsing ----------------------------------------------------------------------------------------------------------
def parse(buf: bytes | bytearray | memoryview) -> AudioPacket | TextPacket | None:
    """An AudioPacket (PCM only) or a TextPacket (ASCII/UTF-8 JSON object), else None: never raises."""
    try:
        size = len(buf)
        if size < HEADER_SIZE or size > MAX_PACKET:
            return None
        magic, proto_sr, nbs, nbc, bit, raw_name, counter = _HEADER.unpack_from(buf, 0)
        if magic != MAGIC:
            return None
        name = raw_name.split(b"\x00", 1)[0]
        if not name:
            return None
        stream = name.decode("ascii")
        proto = proto_sr & PROTO_MASK
        if proto == PROTO_AUDIO:
            idx = proto_sr & SR_MASK
            fmt = bit & DATATYPE_MASK
            if idx >= len(SR_LIST) or bit & CODEC_MASK != CODEC_PCM or fmt not in SAMPLE_BYTES:
                return None
            frames, channels = nbs + 1, nbc + 1
            if size - HEADER_SIZE != frames * channels * SAMPLE_BYTES[fmt]:
                return None
            pcm = decode_pcm(memoryview(buf)[HEADER_SIZE:], fmt, frames, channels)
            return AudioPacket(stream, counter, SR_LIST[idx], channels, fmt, frames, pcm)
        if proto == PROTO_TEXT:
            text_fmt = bit & CODEC_MASK
            if text_fmt not in (TXT_ASCII, TXT_UTF8) or bit & DATATYPE_MASK != BYTE8:
                return None
            data = bytes(memoryview(buf)[HEADER_SIZE:]).rstrip(b"\x00")
            obj = json.loads(data.decode("utf-8"))
            if not isinstance(obj, dict):
                return None
            return TextPacket(stream, counter, obj, text_fmt, nbc)
        return None
    except (ValueError, TypeError, struct.error, UnicodeDecodeError, RecursionError):
        return None


def _obj(x: TextPacket | dict) -> dict | None:
    if isinstance(x, TextPacket):
        return x.obj
    return x if isinstance(x, dict) else None


def _version(o: dict) -> int | None:
    v = o.get("v")
    return v if _is_int(v) and v >= 1 else None


# -- SUBSCRIBE --------------------------------------------------------------------------------------------------------
def subscribe_obj(streams: Iterable[str], sender: str = "show_rt",
                  devices: Mapping[str, str] | None = None, off: Iterable[str] | None = None) -> dict:
    """The SUBSCRIBE object: ``streams`` to capture and send (may be empty), ``off`` to keep closed with their off
    heartbeats (the key only when there are some), ``devices`` hints (only when there are some)."""
    o: dict[str, Any] = {"v": 1, "subscribe": [str(s) for s in streams]}
    off_l = [str(s) for s in off or ()]
    if off_l:
        o["off"] = off_l
    if devices:
        o["devices"] = {str(k): str(v)[:DEVICE_NAME_MAX] for k, v in devices.items()}
    o["from"] = sender
    return o


def subscribe_packet(streams: Iterable[str], counter: int, sender: str = "show_rt",
                     devices: Mapping[str, str] | None = None, off: Iterable[str] | None = None) -> bytes:
    """The SUBSCRIBE TEXT packet (stream ``sub``) asking a sender for ``streams``, keeping ``off`` closed (their off
    heartbeats only), with an optional device hint per stream (``devices``)."""
    return pack_text(SUBSCRIBE_STREAM, counter, subscribe_obj(streams, sender, devices, off))


def subscribed_off(pkt: TextPacket | dict) -> list[str]:
    """The streams a SUBSCRIBE keeps off (device closed, heartbeats with state "off"); [] for none, a bad value, or
    not a SUBSCRIBE. Senders use it with :func:`subscribed_streams`: a stream in neither list stops."""
    if subscribed_streams(pkt) is None:
        return []
    off = _obj(pkt).get("off")                           # type: ignore[union-attr]
    if not isinstance(off, list) or not all(isinstance(s, str) and 1 <= len(s) <= STREAM_NAME_MAX for s in off):
        return []
    return list(off)


def subscribed_devices(pkt: TextPacket | dict) -> dict[str, str]:
    """A SUBSCRIBE's device hints ({stream: device name}); {} for none, a bad value, or not a SUBSCRIBE. Senders use
    it: an unknown name keeps the default device."""
    if subscribed_streams(pkt) is None:
        return {}
    d = _obj(pkt).get("devices")                         # type: ignore[union-attr]
    if not isinstance(d, dict):
        return {}
    return {k: v for k, v in d.items() if isinstance(k, str) and isinstance(v, str) and 1 <= len(v) <= DEVICE_NAME_MAX}


def subscribed_streams(pkt: TextPacket | dict) -> list[str] | None:
    """The stream names a SUBSCRIBE asks for, or None if it isn't one (stream name is checked for packets)."""
    if isinstance(pkt, TextPacket) and pkt.stream != SUBSCRIBE_STREAM:
        return None
    o = _obj(pkt)
    if o is None or _version(o) is None:
        return None
    subs = o.get("subscribe")
    if not isinstance(subs, list) or not all(isinstance(s, str) and 1 <= len(s) <= STREAM_NAME_MAX for s in subs):
        return None
    return list(subs)


def is_subscribe(pkt: TextPacket | dict) -> bool:
    return subscribed_streams(pkt) is not None


# -- heartbeat --------------------------------------------------------------------------------------------------------
def _dbfs(values: Sequence[float] | None) -> list[float]:
    out = []
    for x in values or ():
        x = float(x)
        out.append(DBFS_FLOOR if not math.isfinite(x) or x < DBFS_FLOOR else round(x, 2))
    return out


def make_heartbeat(*, sess: str, frame: int | None, n: int | None, t_cap_ns: int | None, in_lat_ms: float = 0.0,
                   dev: str = "", state: str = "ok", xruns: int = 0, peak_dbfs: Sequence[float] = (),
                   rms_dbfs: Sequence[float] = (), clips: int = 0, devices: Sequence[str] | None = None) -> dict:
    """The heartbeat object (field meanings in the module docstring); -inf/NaN dBFS are floored at -120. ``devices``
    keeps at most DEVICES_MAX names (each cut to DEVICE_NAME_MAX), then drops names from the end until the packed
    message fits MAX_DATA."""
    if state not in HEARTBEAT_STATES:
        raise ValueError(f"heartbeat state {state!r} not in {HEARTBEAT_STATES}")
    o = {"v": 1, "sess": str(sess), "frame": None if frame is None else int(frame) % COUNTER_MOD,
         "n": None if n is None else int(n), "t_cap_ns": None if t_cap_ns is None else int(t_cap_ns),
         "in_lat_ms": round(float(in_lat_ms), 3), "dev": str(dev), "state": state, "xruns": int(xruns),
         "peak_dbfs": _dbfs(peak_dbfs), "rms_dbfs": _dbfs(rms_dbfs), "clips": int(clips)}
    if devices is not None:
        names = [str(d)[:DEVICE_NAME_MAX] for d in devices if str(d)][:DEVICES_MAX]
        o["devices"] = names
        while names and len(text_bytes(o)) > MAX_DATA:
            names.pop()
    return o


def _devices(x: Any) -> list[str] | None:
    if not isinstance(x, list):
        return None
    return [d for d in x[:DEVICES_MAX] if isinstance(d, str) and 1 <= len(d) <= DEVICE_NAME_MAX]


def _opt_int(o: dict, key: str, lo: int = 0, hi: int | None = None) -> tuple[bool, int | None]:
    x = o.get(key)
    if x is None:
        return True, None
    if not _is_int(x) or x < lo or (hi is not None and x > hi):
        return False, None
    return True, x


def _num_list(x: Any) -> list[float] | None:
    if x is None:
        return []
    if not isinstance(x, list) or len(x) > MAX_CHANNELS or not all(_is_num(v) for v in x):
        return None
    return [float(v) for v in x]


def parse_heartbeat(pkt: TextPacket | dict) -> Heartbeat | None:
    """A validated Heartbeat, or None. Required: v, sess, state; frame/n/t_cap_ns may be null (no audio yet)."""
    o = _obj(pkt)
    if o is None or "subscribe" in o or "ev" in o:
        return None
    v = _version(o)
    sess, state = o.get("sess"), o.get("state")
    if v is None or not isinstance(sess, str) or not sess or state not in HEARTBEAT_STATES:
        return None
    ok_f, frame = _opt_int(o, "frame", 0, COUNTER_MOD - 1)
    ok_n, n = _opt_int(o, "n", 0)
    ok_t, t_cap = _opt_int(o, "t_cap_ns", 0)
    ok_x, xruns = _opt_int(o, "xruns", 0)
    ok_c, clips = _opt_int(o, "clips", 0)
    lat, dev = o.get("in_lat_ms", 0.0), o.get("dev", "")
    peak, rms = _num_list(o.get("peak_dbfs")), _num_list(o.get("rms_dbfs"))
    if not (ok_f and ok_n and ok_t and ok_x and ok_c) or peak is None or rms is None:
        return None
    if not _is_num(lat) or not isinstance(dev, str):
        return None
    return Heartbeat(v, sess, frame, n, t_cap, float(lat), dev, state, xruns or 0, peak, rms, clips or 0,
                     _devices(o.get("devices")))


def is_heartbeat(pkt: TextPacket | dict) -> bool:
    return parse_heartbeat(pkt) is not None


# -- piano MIDI -------------------------------------------------------------------------------------------------------
def _event(e: Any) -> MidiEvent | None:
    if not isinstance(e, (list, tuple)) or len(e) != 4 or not all(_is_int(x) for x in e):
        return None
    t_ns, status, d1, d2 = e
    if t_ns < 0 or not 0x80 <= status <= 0xFF or not 0 <= d1 <= 127 or not 0 <= d2 <= 127:
        return None
    return (t_ns, status, d1, d2)


def _events(x: Any, limit: int = 4 * MIDI_BATCH_MAX) -> list[MidiEvent] | None:
    if not isinstance(x, list) or len(x) > limit:
        return None
    out = [_event(e) for e in x]
    return None if any(e is None for e in out) else out  # type: ignore[return-value]


def _clean_events(events: Iterable[Sequence[int]]) -> list[list[int]]:
    out = []
    for e in events:
        ev = _event(list(e))
        if ev is None:
            raise ValueError(f"bad MIDI event {e!r}: want [t_ns>=0, status 0x80-0xFF, d1 0-127, d2 0-127]")
        out.append(list(ev))
    return out


def _sender_sess(sess: Any) -> str:
    if not isinstance(sess, str) or not 1 <= len(sess) <= MIDI_SESS_MAX:
        raise ValueError(f"sess must be a str of 1-{MIDI_SESS_MAX} characters (mandatory from our senders: new on "
                         "every sender start)")
    return sess


def midi_events_obj(seq: int, events: Iterable[Sequence[int]],
                    prev: Iterable[tuple[int, Iterable[Sequence[int]]]] = (), *, sess: str) -> dict:
    """The MIDI events object: batch ``seq`` with ``events`` [[t_ns, status, d1, d2], ...] and ``prev``
    [(seq-1, events), (seq-2, events)] newest first (fewer at the start of a session); ``sess`` is mandatory."""
    ev = _clean_events(events)
    if not ev or len(ev) > MIDI_BATCH_MAX:
        raise ValueError(f"a batch has 1..{MIDI_BATCH_MAX} events (split bigger ones into more batches)")
    if not _is_int(seq) or seq < 0:
        raise ValueError("seq must be an int >= 0")
    # keep "v" then "seq" first: receiver.py's reuseport BPF steers TEXT with 'seq"' at byte 36 to the MIDI socket
    return {"v": 1, "seq": seq, "ev": ev, "prev": [[int(s), _clean_events(e)] for s, e in list(prev)[:MIDI_PREV]],
            "sess": _sender_sess(sess)}


def pack_midi_events(stream: str, counter: int, obj: dict) -> bytes:
    """pack_text for a MIDI events object, dropping ``prev`` batches (oldest first) until it fits 1436 B."""
    obj = dict(obj)
    prev = list(obj.get("prev") or [])
    while True:
        obj["prev"] = prev
        try:
            return pack_text(stream, counter, obj)
        except ValueError:
            if not prev:
                raise
            prev = prev[:-1]


def midi_state_obj(seq: int, keys: Sequence[int], cc64: int, cc66: int, cc67: int, t_ns: int, *,
                   sess: str) -> dict:
    """The periodic MIDI state object: ``keys`` = 88 strike velocities of the keys down (keys[0] = A0), 0 = up;
    ``sess`` is mandatory."""
    k = [int(x) for x in keys]
    if len(k) != PIANO_KEYS or not all(0 <= x <= 127 for x in k):
        raise ValueError(f"keys must be {PIANO_KEYS} velocities 0-127")
    ccs = [int(cc64), int(cc66), int(cc67)]
    if not all(0 <= c <= 127 for c in ccs):
        raise ValueError("pedal values must be 0-127")
    if not _is_int(seq) or seq < 0 or int(t_ns) < 0:
        raise ValueError("seq and t_ns must be ints >= 0")
    # keep "v" then "seq" first: receiver.py's reuseport BPF steers TEXT with 'seq"' at byte 36 to the MIDI socket
    return {"v": 1, "seq": seq, "state": {"keys": k, "cc64": ccs[0], "cc66": ccs[1], "cc67": ccs[2]},
            "t_ns": int(t_ns), "sess": _sender_sess(sess)}


def _sess(o: dict) -> tuple[bool, str | None]:
    s = o.get("sess")
    if s is None:
        return True, None
    return (isinstance(s, str) and bool(s)), (s if isinstance(s, str) else None)


def parse_midi_events(pkt: TextPacket | dict) -> MidiBatch | None:
    """A validated MidiBatch (``prev`` entries newer than ``seq`` or malformed make it None), else None."""
    o = _obj(pkt)
    if o is None:
        return None
    v, seq = _version(o), o.get("seq")
    if v is None or not _is_int(seq) or seq < 0:
        return None
    ev = _events(o.get("ev"))
    ok_s, sess = _sess(o)
    if ev is None or not ok_s:
        return None
    prev_raw = o.get("prev", [])
    if not isinstance(prev_raw, list) or len(prev_raw) > 8:
        return None
    prev: list[tuple[int, list[MidiEvent]]] = []
    for item in prev_raw:
        if not isinstance(item, list) or len(item) != 2 or not _is_int(item[0]) or not 0 <= item[0] < seq:
            return None
        pev = _events(item[1])
        if pev is None:
            return None
        prev.append((item[0], pev))
    return MidiBatch(seq, ev, prev, sess, v)


def parse_midi_state(pkt: TextPacket | dict) -> MidiState | None:
    """A validated MidiState, else None."""
    o = _obj(pkt)
    if o is None:
        return None
    v, seq, t_ns, st = _version(o), o.get("seq"), o.get("t_ns"), o.get("state")
    if v is None or not _is_int(seq) or seq < 0 or not _is_int(t_ns) or t_ns < 0 or not isinstance(st, dict):
        return None
    keys = st.get("keys")
    if not isinstance(keys, list) or len(keys) != PIANO_KEYS or not all(_is_int(k) and 0 <= k <= 127 for k in keys):
        return None
    ccs = [st.get("cc64", 0), st.get("cc66", 0), st.get("cc67", 0)]
    if not all(_is_int(c) and 0 <= c <= 127 for c in ccs):
        return None
    ok_s, sess = _sess(o)
    if not ok_s:
        return None
    return MidiState(seq, list(keys), ccs[0], ccs[1], ccs[2], t_ns, sess, v)


def midi_seq_verdict(seq: int, sess: str | None, last_seq: int | None, last_sess: str | None, *,
                     state: bool = False) -> Literal["restart", "old", "new"]:
    """How a receiver takes a MIDI batch (or, with ``state``, a state) against the last ``seq`` and ``sess`` it
    took from that stream (``last_seq`` None: nothing yet):

      "restart"  the first packet, a changed ``sess`` (None counts as a value), or ``seq`` more than MIDI_REORDER
                 behind ``last_seq``: a new wire session (midi.py releases what it holds and takes this packet
                 as the session's first; a batch's ``prev`` heals what it covers);
      "old"      a batch with ``seq`` <= ``last_seq`` (a duplicate, or a straggler ``prev`` already healed), or a
                 state with ``seq`` < ``last_seq`` (stale): ignore it;
      "new"      otherwise: a batch ahead of the last (``prev`` first if it skipped some), or a state at or ahead
                 of it (it heals).
    """
    if last_seq is None or sess != last_sess or seq < last_seq - MIDI_REORDER:
        return "restart"
    if seq < last_seq or (seq == last_seq and not state):
        return "old"
    return "new"


def classify(pkt: TextPacket | dict) -> str | None:
    """Which of our TEXT messages this is: "subscribe" | "heartbeat" | "midi_events" | "midi_state" | None."""
    o = _obj(pkt)
    if o is None:
        return None
    if "subscribe" in o:
        return "subscribe" if is_subscribe(pkt) else None
    if "ev" in o:
        return "midi_events" if parse_midi_events(o) is not None else None
    if isinstance(o.get("state"), dict):
        return "midi_state" if parse_midi_state(o) is not None else None
    return "heartbeat" if parse_heartbeat(o) is not None else None
