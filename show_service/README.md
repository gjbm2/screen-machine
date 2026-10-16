# show_service

The room's own ears (docs/SHOW_ENGINE.md). Audio and the Steinway's MIDI arrive as VBAN over UDP from senders it
subscribes to. They go onto one CLOCK_MONOTONIC timeline, become features, notes and harmony, and are published on
the show bus as `music.*` (§11.1). Maintainer: **screen-machine-c8**. Send contract changes to c8.

- **Venv:** `~/.venvs/sm-show` (python3.12). Pins are in `requirements.txt`. Build and test niced and serialised:
  ```
  flock /tmp/claude-1000/sm-build.lock nice -n 19 ~/.venvs/sm-show/bin/pip install -r show_service/requirements.txt
  flock /tmp/claude-1000/sm-build.lock nice -n 19 ~/.venvs/sm-show/bin/python -m pytest show_service/tests -q
  ```
- **Run:** `~/.venvs/sm-show/bin/python -m show_service` (from the repo root). In production it is started by
  `scripts/ctl show` (owned by ca), with `deploy/sm-show.env`: see **Deploy (P0)** at the end.
- **Ports:** VBAN on `0.0.0.0:6980/udp` (receiver-initiated, §3.1). The bus and API are on `127.0.0.1:8703`, and are
  reached only through `:8700/show/*`.

## Status

| Module | What | Status |
|---|---|---|
| `vban.py` | VBAN rev 13 wire: audio, TEXT, SUBSCRIBE, heartbeat, MIDI | **done**, tested |
| `timeline.py` | sample index / sender clock → host time (lower envelope) | **done**, tested |
| `contract.py` | the shared names: callback types, flags, stream ranks, bus shapes | **done** |
| `receiver.py` | UDP thread, SUBSCRIBE, stream registry, gaps, segments, health, dispatch | **done**, tested |
| `midi.py` | wire MIDI → note table, pedals → `music.note/pedal/notes` | **done**, tested |
| `harmony.py` | note levels (or audio chroma) → `music.harmony` | **done**, tested |
| `dsp.py` | float32 blocks → `music.features` (25 Hz), `music.hit` | **done**, tested |
| `bus.py` | aiohttp WS `/v1/show`, `/healthz`, `/api/v1/state` on 127.0.0.1:8703 | **done**, tested |
| `config.py`, `__main__.py` | args/env, threads, wiring | **done**, tested |
| `artnet_out.py` | P0 virtual Maestro: ArtDmx in patch.yaml's layout, beat-phased frames, yield | **done**, tested |
| `clock.py` | beat clock §5.3 → `music.clock`; `next_beat` for Art-Net | **done**, tested, wired |
| `tests/test_e2e.py`, `tests/synth_sender.py` | all modules end to end over localhost (below) | **done**, passing |
| later | `ring.py` (120 s shared-memory ring), show_brain (§2) | — |

**Process split.** §2 has two processes: show_rt (hot, no aiohttp) and show_brain. Until show_brain exists, everything
runs in one process. aiohttp stays on its own thread (`show-bus`), and the hot thread only ever calls
`publish()`. When the split happens, `publish()` becomes the IPC seam and `bus.py` moves to show_brain.

## Threads and data flow

```
 senders ──VBAN/UDP──► [show-rx]  Receiver.handle(buf, addr, arrival_s)
                          │  vban.parse → registry → SampleClock / CaptureMap / RemoteTimeMap
                          │  gaps (hold, zero-fill), segments, health (2 Hz snapshot)
                          ├─ on_audio(stream, n0, t0_host_s, pcm, fs, flags) ► Features.process, then Clock.process
                          │                                                     (active audio stream only, same block)
                          │      Features' music.hit bodies → Clock.observe_hit (sub/bass hits refine the phase)
                          ├─ on_midi(stream, msg, arrival_s, to_host) ─────► midi.Piano.on_midi
                          └─ on_tick(now_s) every ≤10 ms ─────────────────► Piano.tick · Features.tick · Harmony.tick
                                                                              · Clock.tick
                                                                              │ publish(msg)  (thread-safe, non-blocking)
                       [show-dns]  resolves sender names every 30 s           ▼
                       [show-bus]  asyncio + aiohttp: WS fan-out, /healthz, /api/v1/state
                       [show-artnet]       deadline loop ≤44 Hz: clock.next_beat + features.latest() (by reference)
                                           → ArtDmx → 127.0.0.1:6454 (room's receiver; only when configured)
                       [show-artnet-poll]  room's /api/v1/dmx every 5 s (yield), patch.yaml mtime every 2 s
                       [main]      signals, latency.json reload every 2 s → Receiver.set_offsets
```

- **One hot thread** (`show-rx`) runs ingest, DSP, MIDI and harmony in order. Nothing on it locks, waits or does I/O
  except `recvfrom`/`sendto`.
- `midi`, `harmony` and `dsp` objects are owned by `show-rx` and are not thread-safe. Anything other threads read
  is a snapshot dict, swapped in by reference.
- **Hot-thread rules** (§2): numpy, scipy.signal and pyebur128 only, plus the stdlib, and for the clock's rhythm lane
  soxr and madmom (aubio-ledfx as the fallback); no logging per packet; reuse arrays where it is cheap; add 1e-9
  dither before IIR filters (denormals); budget ≤0.45 ms per 256-sample hop for the whole fast lane. A block that
  carries a madmom frame holds show-rx for about 2.5 ms (p95); see "Measured" under End to end.
- Every published time is the time of the **sound**: the timeline's acoustic map, never the arrival time.
- **Imports:** a module imports `contract`, `vban` and `timeline`, never another builder's module. `__main__`
  imports everything.

## Interfaces

### vban.py (done)
The module docstring is the wire spec: the header table, SR indices, scaling, and the TEXT message shapes with
every field's meaning.
```python
PORT=6980; HEADER_SIZE=28; MAX_DATA=1436; MAX_PACKET=1464; SR_LIST (21 rates; 48000→3, 44100→16)
INT16=1; INT24=2; INT32=3; FLOAT32=4; FLOAT64=5; PROTO_AUDIO=0x00; PROTO_TEXT=0x40; TXT_UTF8=0x10
sr_index(sr) -> int;  counter_delta(prev, cur) -> int (mod 2**32);  frames_per_packet(channels, fmt) -> int
pack_audio(stream, counter, pcm (frames×ch float), sr, fmt=INT16) -> bytes
pack_text(stream, counter, obj: dict) -> bytes                      # ValueError over 1436 B
parse(buf) -> AudioPacket | TextPacket | None                         # never raises
AudioPacket(stream, counter, sr, channels, fmt, frames, pcm float32 (frames, ch) copy)
TextPacket(stream, counter, obj, text_fmt, channel)
classify(pkt_or_obj) -> "subscribe" | "heartbeat" | "midi_events" | "midi_state" | None
subscribe_packet(streams, counter, sender="show_rt") -> bytes; subscribed_streams(pkt) -> list[str] | None; is_subscribe
make_heartbeat(*, sess, frame, n, t_cap_ns, in_lat_ms, dev, state, xruns, peak_dbfs, rms_dbfs, clips) -> dict
parse_heartbeat(pkt_or_obj) -> Heartbeat | None; is_heartbeat
midi_events_obj(seq, events, prev=(), *, sess) -> dict; pack_midi_events(stream, counter, obj) -> bytes (trims prev)
midi_state_obj(seq, keys88, cc64, cc66, cc67, t_ns, *, sess) -> dict     # sess mandatory (1-64 chars) from our senders
parse_midi_events(..) -> MidiBatch(seq, ev[(t_ns,status,d1,d2)], prev[(seq, ev)], sess, v) | None   # sess may be None
parse_midi_state(..) -> MidiState(seq, keys[88], cc64, cc66, cc67, t_ns, sess, v) | None
midi_seq_verdict(seq, sess, last_seq, last_sess, *, state=False) -> "restart" | "old" | "new"   # MIDI_REORDER = 8
```
Senders (98's kiosk unit, the PC22 agent and the test piano) can import `vban.py` as-is: it needs only numpy and
the stdlib. Its docstring also holds the **sender rules** they must follow: which clock to stamp with (Linux
`time.monotonic_ns()`; Windows on Python 3.12 `time.perf_counter_ns()`, never `time.monotonic_ns()`; PortAudio's
`time_info` for the latency difference only), one clock per process, counters per stream and sub-protocol, and on an
input overflow advancing the counter and `n` over the lost packets (more than 250 ms lost: a new `sess`).

### timeline.py (done)
```python
SampleClock(fs=48000, acoustic_offset_ms=0.0)
  .observe(n_first, arrival_s, frames) -> bool    # False if it re-anchored itself ("step")
  .to_host(n, offset=True) -> float               # acoustic host seconds of sample n (NaN before any packet); arrays ok
  .to_n(t_host, offset=True) -> float
  .segment(reason, *, keep_skew=False, fs=None) -> int     # new segment id
  .seg, .reason, .ready, .skew_ppm, .skew_alarm, .steps, .steps_last_min, .jitter_ms, .late_ms,
  .acoustic_offset_ms (settable), .state()
RemoteClock(acoustic_offset_ms=0.0).observe(t_remote_ns, arrival_s) / .to_host(t_ns) / .to_remote_ns(t) / .segment()
CaptureMap(fs)
  .observe(n, t_cap_ns, seg) -> bool              # False: the sender stepped (2 pairs in a row off the line)
  .residual_ms(n, t_cap_ns) -> float; .tolerance_ms   # max(2 ms, 3 x pair jitter)
  .to_n(t_ns) / .to_remote_ns(n) / .reset() / .ready / .seg / .state()
RemoteTimeMap(acoustic_offset_ms=0.0)
  .observe(t_ns, arrival_s); .attach_audio(sample_clock, capture_map); .detach_audio()
  .to_host(t_ns, offset=True) -> float; .via -> "audio" | "remote" | None; .bridge_s; .state()
```
Accuracy, *tested*: the map stays within 0.5 ms of truth under 3 ms mean exponential jitter with 60 ms spikes. A
40-seed sweep showed errors under 10 µs. Skew is tracked within 3 ppm at ±100 ppm. Skew beyond ±300 ppm is
clamped and `skew_alarm` is set. A sender that loses 50-450 ms of capture and keeps counting re-anchors within 6 s
with no alarm (the backstop: three buckets in a row over max(5 ms, 3 × jitter)); 108 runs of 300 s under heavy
jitter showed no false step. The alarm latches across self-steps, and 2 steps in 60 s set it. MIDI moves under
0.5 ms when `via` switches, with a 20 ms input latency on the audio side (the bridge).

### contract.py (done)
`Sender` and `Config` (frozen dataclasses, fields only: config.py fills them, the receiver takes them; `Config`
has `stream_priority`, from `--stream-priority`), `AUDIO_GAP=1`, `AUDIO_RESUME=2`, `AUDIO_SEGMENT=4`, `OnAudio`
(with `fs`), `OnMidi`, `OnTick`, `TICK_S=0.01`, `Publish`, `stream_rank(name, priority=())` (names in `priority`
first, as negative ranks, then `AUDIO_PRIORITY`, test streams, unknown names), `AUDIO_PRIORITY`, `MIDI_STREAMS`,
`TEST_STREAMS`, the bus constants (`BUS_PORT=8703`, `BUS_PATH`, `RENDERER_QUEUE=64`, `CLOSE_BEHIND=4008`), the
message type names, and TypedDicts for every `data` body: `NoteData`, `PedalData`, `NotesData`, `HarmonyData`,
`FeaturesData` (with `BandData`, `punch`, `drive`), `HitData` and `ClockData` (`music.clock`, moved here from clock.py
on 16 Oct; `clock.ClockData` is the same class). It also
holds `BAND_EDGES_HZ`, `KEY_NAMES`, `PEDAL_CC` and the spectrum32 constants (`SPEC_EDGES_HZ`, …). **The TypedDicts
are the exact emitted shapes.** A bus message is always `{"type": <name>, "data": <body>}`, which is what
`stream_service/sound_view.py` reads.

### receiver.py (done)
```python
class Receiver:
    def __init__(self, cfg: contract.Config, *, on_audio: OnAudio, on_midi: OnMidi, on_tick: OnTick,
                 clock: Callable[[], float] = time.monotonic, sock: socket.socket | None = None) -> None
    def start(self) -> None                  # bind cfg.vban_bind (unless sock given); threads "show-rx", "show-dns"
    def stop(self, timeout: float = 2.0) -> None
    def handle(self, buf: bytes, addr: tuple[str, int], arrival_s: float) -> None   # one datagram; show-rx (tests call it)
    def tick(self, now_s: float) -> None     # SUBSCRIBE due? hold deadlines, health, then on_tick(now_s); show-rx
    def health(self) -> dict                 # latest 2 Hz snapshot; any thread
    def active_audio(self) -> str | None     # show-rx
    def set_offsets(self, offsets: dict[str, float]) -> None   # any thread; applied at the next tick
```
**Socket and SUBSCRIBE**
- One UDP socket bound to `cfg.vban_bind` (`0.0.0.0:6980`). Ask for `SO_RCVBUF` 4 MB, then read it back with
  `getsockopt` and report that in `health.socket.rcvbuf`. Linux caps it silently at `net.core.rmem_max` (212992 on
  this box: about 416 KB as read back, roughly 0.5 s of 4-channel packets). Never use `SO_RCVBUFFORCE` (no
  elevation in WSL).
- The loop is `recvfrom` with a 10 ms timeout. Stamp `clock()` right after each packet arrives.
- Every 1 s, send `vban.subscribe_packet(sender.streams, counter)` from that socket to each sender's resolved
  `(addr, port)`. Use one counter for the `sub` stream.
- `show-dns` resolves each sender's host every 30 s into a dict that `show-rx` reads. DNS never blocks
  `show-rx`. A failed lookup shows in health, and the last good address is kept.

**Registry**
- Each stream name has its own entry, created on its first valid packet. That includes names we never subscribed
  to (push-only senders), which go at `contract.stream_rank(name, cfg.stream_priority)` order: `--stream-priority`
  names first, unknown names last, and `test-*` marked test.
- Kind comes from the first packet: AUDIO means audio; TEXT that classifies as MIDI means midi.
- A heartbeat's TEXT packet has the **audio stream's own name**. It updates that stream and is not dispatched.
- SUBSCRIBE packets and junk are counted, then dropped.

**Audio: counters, segments and sample index `n`**
- Per stream, keep the last counter `c` and the next sample index `n` (the receiver's own count, per segment,
  starting at 0). `c` tracks AUDIO packets only: the heartbeat, a TEXT packet with the same name, has its own
  counter (vban.py "Counters").
- For a packet with counter `c'`, let `d = vban.counter_delta(c, c')`:
  - `d == 1`: in order.
  - `d == 0`, or `d > 2**31`: a duplicate, or late. Fill a held hole if it matches one; otherwise count it
    `late`/`dup` and drop it.
  - `2 ≤ d ≤ fill_max` (packets in 250 ms): a hole of `d-1` packets. Hold later packets until the hole fills or
    `hold = clamp(3 × clock.jitter_ms, 4, 40) ms` passes after the hole was seen.
    - Then deliver the hole as zero-fill blocks with `AUDIO_GAP`.
    - Flag the next real block `AUDIO_RESUME`.
    - Count `gaps` and `lost`.
  - `d > fill_max`: a discontinuity, so start a new segment.
- **New segment.** Call `clock.segment(reason)` and set `n = 0`. The first block delivered carries
  `AUDIO_SEGMENT`. Reasons:
  - `"counter"` (a discontinuity);
  - `"format"` (sr, channels, fmt or frames changed; also pass `fs=`);
  - `"sess"` (the heartbeat `sess` changed; `keep_skew=True`);
  - `"idle"` (no packet for ≥ 1 s, or the heartbeat's state left `idle`/`no_device`);
  - `"sender-step"` (`keep_skew=True`; see heartbeats below).
  `"counter"` and `"idle"` also pass `keep_skew=True` when the heartbeat `sess` is unchanged (same device, same
  clock: keep the skew).
  Also watch for `clock.seg` changing by itself (`"step"`). Re-sync the CaptureMap then, but `n` continues.
- **Active stream switch.** The first block delivered after `active_audio()` changes also carries `AUDIO_SEGMENT`,
  so dsp resets (another stream, other filters). The receiver owns that flag; nobody else resets Features.
- Call `clock.observe(n_first, arrival_s, frames)` once per real packet, at arrival, in arrival order. Never call
  it for zero-fill.
- A delivered block's time is `t0_host_s = clock.to_host(n0)`, and its `fs` is the stream's rate. Blocks are
  contiguous within a segment.
- **Heartbeat → CaptureMap.**
  - Convert the heartbeat's `frame` (a packet counter) to the receiver's `n` for that packet. Use the
    last-delivered packet: `n_rx = n_last + signed(counter_delta(c_last, frame)) × frames`.
  - Drop the pair if `n_rx < 0` (its packet came before this segment).
  - Then `ok = capture.observe(n_rx, t_cap_ns, seg=clock.seg)`.
  - **Sender step.** Start a `"sender-step"` segment (`keep_skew=True`) when any of these holds:
    - `ok` is False: two pairs in a row lay more than `capture.tolerance_ms` (max(2 ms, 3 × jitter)) off the line.
      That is capture time lost while the sender kept counting, which only the heartbeat can show within a second
      (the clock's own backstop takes 4-6 s, and only for losses over 5 ms);
    - the heartbeat's `xruns` went up (same `sess`);
    - the sender's `n` minus `n_rx` changed. It is constant within a sess, because our zero-fill keeps the indices
      aligned, so a change means the sender skipped samples without counting them. Re-baseline this difference at
      the first heartbeat of each receiver segment, since `n_rx` restarts at 0 with every segment (counter, idle,
      format, step).

**MIDI**
- Parse with `vban.parse_midi_events` / `parse_midi_state`.
- Feed the stream's `RemoteTimeMap.observe(t_ns, arrival_s)`:
  - for a batch, use the newest `t_ns` in `ev`;
  - for a state, use `t_ns`.
- Attach the audio clock of the best-ranked audio stream from **the same source IP** that has a ready
  CaptureMap: `tmap.attach_audio(clock, capture)`. That puts notes and sound on one map (§3.5). Detach when that
  stream goes `down`.
- Then call `on_midi(stream, msg, arrival_s, tmap.to_host)`. Pass duplicates through, because midi.py dedupes
  by `seq`.

**Offsets.** `acoustic_offset_ms` is set per stream from `set_offsets()`. The default is 0, and a missing stream
counts as 0. It is applied to `SampleClock.acoustic_offset_ms`, and for MIDI to `RemoteTimeMap.acoustic_offset_ms`.

**Health** (§3.3; rebuilt at 2 Hz on show-rx; `health()` returns the last dict):
```
{"t_ms", "active_audio": str|None,
 "socket": {"bind", "error", "rx", "junk", "subscribe_rx", "bytes", "rcvbuf"},   # rcvbuf as read back
 "errors": {"audio", "midi", "tick", "handle"},                                  # callback exceptions (logged, never fatal)
 "senders": {name: {"host", "port", "addr", "resolve_error", "send_error", "streams": [...], "last_subscribe_ms"}},
 "streams": {name: {
    "kind": "audio"|"midi"|"unknown", "rank", "test": bool, "addr": "ip:port",
    "state": "streaming"|"quiet"|"stale"|"down"|"no_device",
    "format": {"sr", "channels", "fmt": "INT24", "frames"},                          # audio
    "net": {"packets", "loss_pct" (10 s window), "late", "dup", "gaps", "lost", "jitter_ms", "hold_ms",
            "drift_ppm", "skew_alarm", "age_ms", "segments", "sender_steps" (audio)},
    "audio": {"peak_dbfs", "rms_dbfs", "clips", "silent", "floor_dbfs"},           # from delivered pcm, 500 ms
    "sender": {"sess", "dev", "state", "xruns", "in_lat_ms", "age_ms"} | None,      # last heartbeat
    "clock": {**SampleClock.state(), "capture": CaptureMap.state()} | {**RemoteTimeMap.state(), "attached"},
    "line": "room-mic · 48k/1ch · −24 dBFS · 0.3 % loss · 4 ms jitter"}}}
```
- **States:**
  - `no_device`: the last heartbeat (under 2 s old) says so.
  - `down`: no packet for > 5 s, or ever.
  - `stale`: no packet for > 0.5 s.
  - `quiet`: RMS below −60 dBFS for > 5 s (audio), or no note or pedal for 30 s (midi).
  - Otherwise `streaming`.
- **`active_audio()`:** the best-ranked audio stream that is `streaming` or `quiet`.
  - Switch away only after the current one has been `stale`, `down` or `no_device` for 1 s.
  - Switch up only after a better-ranked one has been `streaming` for 3 s.
  - Never fall to nothing while anything is live. MIDI is never a fallback for audio.

**Tests (shared plumbing, real):** drive `handle()` with `vban.pack_*` bytes and fake times. Cover:
- in order, holes filled late, holes zero-filled after the hold, and duplicates;
- segment reasons, `keep_skew` on counter/idle with an unchanged sess, and `AUDIO_SEGMENT` after a stream switch;
- heartbeat → CaptureMap, including all three sender-step triggers and no false sender-step after a segment;
- MIDI dispatch with `to_host`;
- unknown streams, the health shape and the SUBSCRIBE schedule.

**Built** (`tests/test_receiver.py`, 30, incl. a threaded end-to-end over ephemeral 127.0.0.1 ports with an
in-process sender). Choices the spec left open:
- `health.socket.error` shows a receive error (an ICMP refusal, say) only until the next packet arrives; a failed
  bind stays until a bind succeeds.
- `audio` metering covers every real packet of every audio stream (not only the delivered, active one), so the
  card shows standby sources too. Zero-fill is never metered.
- A hole goes out as **one** zero-fill block of `missing × frames` (flagged `AUDIO_GAP`), not one per packet.
- A packet late for a hole already zero-filled is still fed to `clock.observe` (it is a real arrival), then dropped.
- More than `2 × fill_max` behind (a counter reset) is a `"counter"` segment, like a jump ahead.
- A heartbeat `sess` change doesn't add a `"sess"` segment when a counter/idle segment began since the previous
  heartbeat (a sender restart: the counter jumps first). Its `(frame, n)` pair is skipped (new sess, old index).
- `active_audio()` is None only after the current stream has been bad for 1 s with nothing else live. A stream that
  arrives while nothing is active is taken at once (its first block is delivered).
- `show-dns` retries every 5 s while a sender fails to resolve; a new or changed address triggers a SUBSCRIBE at the
  next tick. A failed bind is retried every 2 s (ticks continue meanwhile).
- `_resolve_all()` is the show-dns step (blocking); tests call it directly.

### midi.py (done)
```python
class Piano:
    def __init__(self, stream: str, publish: Publish, *, source: Source | None = None,
                 ignore_channels: Collection[int] = (), state_channels: Collection[int] | None = None) -> None
        # source defaults to "test" for contract.TEST_STREAMS (and any "test-*"), else "live"
        # ignore_channels: MIDI channels (1-16) dropped and counted (stats "ignored"); default none (below)
        # state_channels: the channels a state's keys/pedals describe (None = all); __main__ passes (1,) (below)
    def on_midi(self, msg: vban.MidiBatch | vban.MidiState, arrival_s: float, to_host: Callable[[int], float]) -> None
    def tick(self, now_s: float) -> None       # music.notes while present: every 0.1 s while anything changed in the last
                                               # 1 s, else 0.5 s; at once when present flips; nothing without the voice
    def levels(self, t_s: float) -> np.ndarray  # (128,) sounding amplitude 0..1 at host time t_s, for harmony
    table: np.ndarray                          # (128, 6) float64, columns LEVEL, VEL_ON, T_ON, RELEASED, T_OFF, VEL_OFF
                                               # (times host s, NaN = never; RELEASED 0/1; LEVEL = amplitude at the last event)
    pedals: dict[str, float]                   # damper, soft, sostenuto 0..1
    present: bool; sess: str; seq: int         # sess "" before the first packet; seq = the BUS seq (wire seq in stats)
    voice: bool                                # may publish (default True); __main__ gives it to one Piano at a time
    def set_voice(self, on: bool, now_s: float) -> None   # gaining it: this packet's muted note/pedal messages, then a
                                               # music.notes snapshot, at once
    def present_at(self, now_s: float) -> bool # what present would be at now_s (no side effects)
    hot_repeat_s = 0.25; hot_jump = 32; soft_scale = 0.25   # class attributes, tunable per instance
    def notes(self, now_s: float) -> NotesData  # the music.notes body tick() publishes
    def held(self) -> np.ndarray               # pitches physically down
    def stats(self) -> dict                    # batches, states, old, restarts, events, lost, healed_prev,
                                               # healed_state, capped, stale_releases, ignored, muted, voiced,
                                               # wire_seq, voice, ...
```
**Events in**
- All channels feed one keyboard (adsilent: ch1 is the keys, ch2 its own playback). `0x90` with velocity 0 is a
  note-off.
- CC 64, 66 and 67 are the pedals (`contract.PEDAL_CC`). Everything else is ignored.
- Time is `to_host(t_ns)`. If that is NaN, use `arrival_s`.

**Wire `seq`.** One rule, `vban.midi_seq_verdict(seq, sess, last_seq, last_sess, state=...)`, as vban.py's
docstring states it. Keep `last_seq` (the highest taken) and `last_sess` per wire session.
- `"restart"`: the first packet, a changed wire `sess`, or `seq` more than `MIDI_REORDER` (8) behind `last_seq`,
  for a batch or a state. Start a new wire session (below), then take the packet as its first: a batch's `prev`
  heals what it covers, a state heals the keys.
- `"old"`: a batch with `seq ≤ last` (a duplicate) or a state with `seq < last` (stale). Ignore it.
- `"new"` batch with `seq > last + 1`: first apply its `prev` batches in `(last, seq)`, oldest first, then the
  batch. Count anything still missing as `lost`.
- `"new"` state (`seq ≥ last`) heals: it diffs `keys` against the held keys and synthesises note-on, note-off or
  pedal events at the state's `t_ns`, then sets `last = seq`.
- Our senders always send `sess` (vban.py makes it mandatory), so a restart is caught at its first packet. For a
  sender without `sess`, a restart within 8 batches of where it was costs up to 8 batches.

**Bus sess and seq**
- A new bus `sess` (`f"{stream}-{uuid4().hex[:8]}"`) starts with every wire `"restart"` (the first packet
  included).
- A new sess releases every held key.
- The bus `seq` is our own counter, +1 per published note or pedal.

**Hot-note cap.** A re-strike of the same key within 0.25 s is capped at the previous velocity + 32. This matches
`sound_view.py`'s guard of HOT_REPEAT_S 0.25 and HOT_JUMP 0.25×127.

**Amplitude model.** It is the same as `stream_service/sound_view.py`, so the director and the screens agree:
- `(vel / 127) ** 1.2`;
- τ_sus 5 s at A0, halving every 24 semitones, with a floor of 0.3 s;
- released keys damp with `0.12 + (τ_sus − 0.12)·damper²`;
- keys from MIDI 89 up have no dampers;
- sostenuto latches the keys that are down when its value crosses up through 0.5 (`value ≥ 0.5`, sound_view's
  `SOSTENUTO_ON`), and releases them when it falls back below 0.5.

**Publishing**
- Publish `music.note` and `music.pedal` immediately: pedals on change only, notes on every strike and release
  of a held key.
- Publish `music.notes` on `tick` while `present`, and once more when `present` flips to False; then nothing (no
  keys can be down then). An idle kiosk sender's 2 Hz states never reach the bus, and sound_view's `present` falls
  5 s after the playing stops.
- **One voice (review, 16 Oct).** `stream_service/sound_view.py` keeps one session: a message with another `sess`
  releases every held key and the next snapshot re-strikes them, so two pianos publishing made the renderer thrash
  (a held chord gave 15 onsets in 3 s instead of 2). Only the Piano with `voice` publishes. A muted Piano keeps its
  whole table, pedals, `present` and `levels()` (harmony). `set_voice(True, now)` publishes the note/pedal messages
  muted since the current packet (or tick) began, under the current sess, then a `music.notes` snapshot, so a
  hand-over within the packet that made the piano present loses no strike. `__main__` picks the voice (below).
- Shapes are exactly `contract.NoteData`, `PedalData` and `NotesData`. `t_ms` is the host time × 1000.
- A key's `t_ms` is computed once, at its strike, and stored. `keys[i][2]` (`t_on_ms`) in every `music.notes` is
  exactly the `t_ms` of that key's `music.note`, never recomputed from `to_host`: sound_view re-strikes a held key
  whose `t_on` differs by more than 1 ms.
- `present` is True while any key is down or a pedal is down (value > 0.1), or there was MIDI activity in the last
  10 s. Activity is a note or pedal-change event, including those a state synthesises when it heals. A state that
  changes nothing is not activity, so a resting proportional damper (a few counts) and the 2 Hz states never hold
  `present` up, and Harmony falls back to audio chroma.
- If no packet arrives for 5 s, release every held key with vel 0 and set `present` False.

**Tests:** prev healing, state healing (a stuck note clears within one state), the hot-note cap, a sess restart, a
sessless restart (seq 1 after 40), note-on vel 0, `present` falling with a resting damper and idle states,
`t_on_ms` equal to the note's `t_ms`, and the shapes.

**Built** (`tests/test_midi.py`, 29: the voice, a muted piano, the strike that hands over, an idle piano going
quiet). Choices the spec left open:
- **Channel 2 (the adsilent replaying itself) feeds the keyboard by default**, as above, so the end-to-end check
  (§3.5) lights the screens. `Piano(..., ignore_channels=(2,))` drops it instead. `contract.Source` has no
  "replay" value, so replayed notes can't be marked.
- **`state_channels`** (integration, 16 Oct): 98's sender (tools/show_sender) puts only channel 1's keys and pedals
  in a state, so with the default (`None`, every channel) each state released the playback's notes within 0.5 s.
  `__main__` passes `state_channels=(1,)`. A state then releases or resets only keys and pedals last set on
  channel 1, or by a state. Notes struck on channel 2 are released by their own note-off, by a restart or by the
  5 s silence rule.
- A state heals pedals first, then keys. Synthesised note-ons go through the hot-note cap like any strike.
- A restart publishes the releases under the **old** bus sess, then switches. The bus `seq` never resets.
- `music.notes` `t_ms` = max(now, the latest event's `t_ms`); it is also published at once when `present` flips.
- The soft pedal scales a strike's level by `1 − 0.25 × soft` in `levels()` only (harmony weights); no published
  velocity changes, and sound_view.py's model is otherwise identical.

### harmony.py (done)
```python
def chroma_from_levels(levels: np.ndarray) -> np.ndarray           # (128,) -> (12,), C=0, max-normalised, 0s if silent
def key_estimate(chroma: np.ndarray) -> tuple[str | None, str | None, float]   # Krumhansl-Kessler: (key, mode, conf 0..1)
def key_scores(chroma) -> np.ndarray | None   # (24,) Pearson r: 0-11 major C..B, 12-23 minor; None if flat
def roughness(chroma) -> float; def key_fit(chroma, k: int | None) -> float     # tension's two terms
class Harmony:
    def __init__(self, publish: Publish, *, rate_hz: float = 10.0) -> None
    def tick(self, now_s: float, piano: "midi.Piano | None", audio_chroma: np.ndarray | None = None) -> None
        # publishes music.harmony at rate_hz: from piano.levels(now_s) while piano.present, else from audio_chroma
        # (dsp.Features.chroma12(); None until the medium lane exists), else nothing
    def compute(self, chroma12: np.ndarray, levels: np.ndarray | None, t_s: float, source: str) -> HarmonyData
    latest: HarmonyData | None; key -> (key, mode)                 # the last frame; the current key
```
Output is `contract.HarmonyData`:
- `key` is in `KEY_NAMES`, from a chroma averaged over about 8 s, with hysteresis (change only on a clear win held
  for 2 s).
- `tension` is 0.6 × interval roughness of the sounding pitch classes, plus 0.4 × (1 − key fit).
- `chord_change` is 1 − the cosine between the chroma now and 0.3 s ago. It rises fast and decays with τ 0.5 s.
- `register` is the level-weighted mean pitch, `(p−21)/87`. It reads 0.5 when silent.
- `spread` is the level-weighted pitch standard deviation / 24 semitones, clamped.
- When `piano` is passed, `__main__` passes the present one, preferring `live` over `test`.

**Tests:** a C major triad gives C/major, and an A minor scale gives A/minor; silence gives a valid dict;
crash-level checks.

**Built** (`tests/test_harmony.py`, 10). The module docstring has every formula. Choices the spec left open:
- **First key:** taken once one key leads the runner-up by 0.05 (in r) for 1 s with r > 0.3. A lone note (as
  major as it is minor) names none; a played scale gets its key after 2-3 s.
- **Roughness:** the pair-weighted mean dissonance by interval class (m2/M7 1.0, M2/m7 0.6, m3/M6 0.15, M3/m6 0.1,
  P4/P5 0, tritone 0.8), × min(1, 2 × the pairs' share of the weight), so a faint ringing neighbour adds little.
  Key fit: the chroma share inside the key's scale (minor = natural + raised 6th/7th); 1 with no key.
- **conf** = r(key) × clamp((r(key) − r(runner-up)) / 0.1): 0 while another key fits better.
- From audio chroma, `register` is 0.5 and `spread` 0 (a chroma has no octave).
- `chord_change` reads 1 when sound starts from silence and 0 going into it.
- `compute()` costs about 60 µs (niced, under render load), at 10 Hz on show-rx.

### dsp.py (done)
```python
class Features:
    def __init__(self, *, publish: Publish | None = None, hop: int = 256, publish_hz: float = 25.0) -> None
    def process(self, stream: str, n0: int, t0_host_s: float, pcm: np.ndarray, fs: int, flags: int) -> None
        # = contract.OnAudio, so __main__ passes features.process as on_audio; any block size; mono mix
    def tick(self, now_s: float) -> None                 # music.features at publish_hz when there is fresh audio;
                                                         # when the source stops, a 1.5 s fade, then nothing (below)
    def latest(self) -> FeaturesData | None
    def chroma12(self) -> np.ndarray | None              # None until the medium lane (§4) exists
    def reset(self) -> None                              # also on AUDIO_SEGMENT; keeps R128 and the normalisation
                                                         # history (a new `stream` name clears the history)
    gate_ref_lufs: float = -30.0                         # set by the director per mode (§6.3)
    def stats(self) -> dict                              # blocks, hops, gaps, publishes, fades, hits {band: n},
                                                         # hop_ms_p50/p95, block_ms_p95, tick_ms_p95, fs, channels,
                                                         # stream, kernel
```
The module docstring is the full spec: every constant, threshold and formula. As built (16 Oct):
- **Hit path:** 2nd-order Butterworth band-passes. Envelopes are fast (2-pole, τ 2.5 ms) and slow (τ 150 ms; it is
  also the band's `level`). Detection runs in pieces of ≤ 128 samples, so hits don't depend on block size. The on
  threshold is per band (+10 dB sub, +8 bass and low_mid, +7 above; the sub band is 40 Hz wide and swings in noise),
  off is +3 dB, the refractory 60 ms, and the floor −70 dB. The filters run through scipy's in-place `_sosfilt`
  kernel (≈3 µs a call against ≈60 µs for the public `sosfilt`), which is checked against `sosfilt` at import, with
  the public function as the fallback. Measured on a kick (110 → 50 Hz) over pink noise: a bass hit in 3.4 ms and
  a sub hit in about 14 ms. In stationary pink noise there are about 3 false sub hits per 30 s.
- **Fast lane:** a 2048-point Hann FFT (not 512) → 40-band log-mel → SuperFlux with lag 2 hops (madmom's rule)
  and a 3-band max filter. A transient peaks the flux a quarter window after the sound, so an onset's time is the
  hop's end − 512/fs. Clicks then land within ±1 hop.
- **Measured** at nice 19 under the 4K render, 128-frame blocks, publishing included: CPU 5.6-7.7 % of a core
  (560-770 µs per 10 ms of audio); hop p50 0.21-0.33 ms; hop p95 0.33-0.67 ms, depending on load; publish p95
  0.55-0.75 ms (it includes the 5 Hz `lufs_s` read).

**Flags, rate and stream**
- `AUDIO_GAP` blocks advance time, but feed no onsets or hits. Nothing sees them: no filter, ring or R128.
- `AUDIO_RESUME` suppresses hits for the block plus one hop. It suppresses onsets until the splice has passed the
  2048-point window's centre, plus a hop: one hop is not enough, because the splice keeps raising the flux while
  it climbs the window.
- `AUDIO_SEGMENT` resets state. The receiver also sets it on the first block after the active stream switches.
- `fs` and the channel count come with every block. When either changes (a 44.1 kHz `pc22-loop`, say), rebuild
  the band filters, mel bank, FFT bins and the R128 state for the new rate; never run 48 kHz filters on other
  audio. `stream` goes into `FeaturesData.stream`.

**Every delivered block (the hit path, §4: per packet, 2.7 ms at 128 frames):**
- 6 band levels (`contract.BAND_EDGES_HZ`), from `scipy.signal.sosfilt` with kept state plus envelope followers;
- band hits, from an adaptive threshold with hysteresis and a 60 ms refractory. A hit's `t_ms` is the acoustic time
  of the sample where its envelope crossed. Running per block, not per hop, saves up to a hop (5.3 ms) of latency;
- `pyebur128`, fed per block: `R128State(channels, fs, pyebur128.MeasurementMode.MODE_M |
  pyebur128.MeasurementMode.MODE_S)`, then `state.add_frames(np.ascontiguousarray(pcm).reshape(-1), frames)`. It
  takes a 1-D interleaved buffer only: a `(frames, ch)` array raises TypeError in the installed 0.1.1.

**Each hop (256 samples):**
- SuperFlux onset on a 2048-point Hann FFT's 40-band log-mel (see "As built");
- peak and RMS (accumulated per block, reported per publish).

**At publish time (25 Hz):**
- spectrum32 (below);
- `lufs_m = pyebur128.get_loudness_momentary(state)`, about 55 µs;
- `lufs_s = pyebur128.get_loudness_shortterm(state)`, read at 5 Hz or less and held between reads. It costs
  0.2-0.45 ms a read (measured niced, under load): half or more of a hop's budget.
- These are module functions, not methods. `-inf` is floored at −70.

**Normalisation (§4)**
- Rolling p05/p95 over 60 s, mixed with a 10-minute pair, then × gate.
- `gate = smoothstep(gate_ref_lufs − 15, gate_ref_lufs, lufs_s)`.

**spectrum32 (§11.1, exactly)**
- Compute at the publish rate from an FFT of ≥ 4096 points (Hann), because the lowest band is 7 Hz wide.
- Band power is the summed bin power whose centre falls in `SPEC_EDGES_HZ[k]..[k+1]`. A band with no bin takes the
  nearest bin's power.
- Convert to dB. The shared ref is the p95 of the loudest band over 60 s.
- `clamp((dB − (ref − 60))/60)`, × gate, then smooth with τ 300 ms both ways.

**A stopped source fades (review, 16 Oct).** When no hop has arrived for 0.1 s (2.5 publish periods, on tick's host
clock), `tick` keeps publishing at 25 Hz with gate 0 until 1.5 s after the latest hop: spectrum32 and presence fall
with their own taus (300 ms, 1 s) by the time since the sound stopped; energy, onset, punch, drive, band levels and
hits read 0; LUFS −70, dBFS −120, brightness and noisiness 0; `t_ms` runs on from the last real frame at the host's
rate. Then one all-zero frame, and nothing until audio returns (`stats()["fades"]`). Before this, music.features
just stopped, and sound_view kept (and kept pushing into its history) the last spectrum for its STALE_S of 5 s.

**Other outputs**
- brightness = `clamp(log2(centroid/50)/log2(16000/50))`.
- noisiness = spectral flatness.
- energy = normalised loudness × gate.
- punch and drive, exactly as `contract.FeaturesData` defines them: the same formulas as
  `stream_service/music_direction.py`, fed by low-end (sub or bass) hits instead of Maestro's pulse.
- t_ms is the acoustic time of the hop's last sample. For stream index `m` in the block that starts at `n0`,
  that is `(t0_host_s + (m − n0)/fs) × 1000`. Skew within one block is negligible.
- It may publish `music.hit` (`contract.HitData`) per band hit, and the bus sends it only to `want`-ers.

**Budget:** the whole hop, its blocks included, in ≤ 0.45 ms p95 under render load. Keep `stats()` honest.

**Tests:** crash-level checks, and a sine in band k lights spectrum32 band k most. A click track gives onsets at the
clicks (±1 hop) and hits within one block. The same sine at 44.1 kHz lands in the same band as at 48 kHz.

### bus.py (done)
```python
class Bus:
    def __init__(self, host: str = BUS_HOST, port: int = BUS_PORT, *, state_fn: Callable[[], dict | None] | None = None,
                 health_fn: Callable[[], dict] | None = None) -> None   # health_fn = Receiver.health (show.state audio,
                                                                        # /healthz streams); port 0 = ephemeral (tests)
    def start(self) -> None          # thread "show-bus" with its own asyncio loop; returns after the first bind attempt
                                     # (bind_error set on failure; it retries every 2 s; publish() keeps working)
    def stop(self, timeout: float = 2.0) -> None
    def publish(self, msg: dict) -> None   # any thread; loop.call_soon_threadsafe; never blocks or raises; caller
                                           # must not mutate msg afterwards; json.dumps happens once, on show-bus
    def stats(self) -> dict          # url, bind_error, clients [{role, client_id, want, queued, dropped, sent, age_s}],
                                     # published {type: n}, state_frames, kicked, bad, inbox_dropped
    def latest(self, mtype: str) -> dict | None   # the latest published envelope of that type (show-bus thread)
    .port (bound), .url, state_frame() -> dict, audio_summary() -> dict
```
- **Routes:**
  - `GET /v1/show` (WS);
  - `GET /healthz` → `{"ok": true, "service": "show_service", "version", "uptime_s", "clients", "active_audio",
    "streams": {name: state}}` (200 whenever the process is up: scripts/ctl's probe);
  - `GET /api/v1/state` → `state_fn()`, or 503 `{"error": "starting"}` while it returns None;
  - the other §11.2 routes (`/v1/events`, `step`, `palette`, `action`, `nudge/{id}`, `vibe`, `describe`,
    `listening`, `hue/release`, `autopilot`, `autopilot/undo`, `autopilot/resume`) are 501 stubs with a plain
    message. Paths have no `/show` prefix: room's `:8700/show/*` pass-through strips it.
  - Bind 127.0.0.1 only. No auth (CLAUDE.md rule 5): `hello.token` is ignored.
- **Handshake:**
  - The client sends `{"type": "hello", "v": 1|2, "role": str, "client_id": str, "want"?: [types]}` within 5 s,
    otherwise the server closes 4400 (also for anything that isn't a hello).
  - The server answers `{"type": "welcome", "schema": 2, "source": "show_service", "version", "types": [...],
    "rate_hz": 25, "stale_after_ms": 500, "fixtures": [], "t_ms"}`. It is always the first message (showbus.py
    reads one). `fixtures` is empty until P4 (room's welcome lists its own), so a v1 client sees there are none.
  - Straight after that it sends the latest `music.notes`, `music.harmony`, `music.features` and `music.clock` it
    holds, so a reconnect heals at once.
- **Filter:** with no `want` (or an empty one), a client gets every type except `contract.WANT_ONLY`
  (`music.hit`). With `want`, an entry matches a type exactly or as a prefix (`"music"`, `"music."`, `"music.*"`,
  `"*"`); a WANT_ONLY type only by its exact name. `show.state` is a type like the others.
- **Queues:** role `renderer` gets 64, and on overflow the connection is closed 4008. Other roles get 256, and on
  overflow the oldest is dropped and counted. The drain yields every 16 messages, so a burst after a stall on our
  side (show-bus starved of the GIL) never closes a renderer that keeps up; only one that doesn't read is closed.
- **`show.state`, 25 Hz,** K3 shape with `global` at the top level and no `fixtures`:
  `{"v": 1, "type": "show.state", "seq", "t_ms", "feed": "live", "input": {"kind": "show", "online": true},
  "audio": {"active", "streams": {name: {kind, state, test, line}}}, "global": {"activity": energy, "music":
  {chroma12, key, mode (from music.harmony), spectrum32, energy, gate, onset, brightness, noisiness, lufs_m, lufs_s,
  punch, drive, bands {name: level}, stream, t_ms (from music.features)}}}`. Features or harmony older than 1 s read
  as silence (zeros, −70 LUFS), so a stopped source never freezes a spectrum. `feed` stays `live` whatever the audio
  does (§11.1).
- **`global.music.chroma12` is music.harmony's chroma** (with its key and mode): the piano's while it plays. §11.1
  wants the *audio* chroma there. That comes from `dsp.Features.chroma12()` once the medium lane exists; today it
  returns None, so with audio only there is no music.harmony and chroma12 reads zeros (key/mode null). sound_view
  reads it only as a fallback, so nothing on the screens depends on it yet.
- **JSON is strict:** NaN/inf become null and numpy values become lists or numbers, so browsers can parse it.

**Tests** (`tests/test_bus.py`, 17; a real Bus on an ephemeral port, aiohttp clients): the renderer's exact hello
and welcome-first, token ignored, 4400, latest-on-connect, the want filter (exact, prefix, `music.hit` only to
want-ers), `publish()` from another thread in order, NaN/numpy cleaning, publish before start and after stop,
renderer overflow → 4008, a burst after our own stall doesn't close a renderer, other roles drop the oldest and
stay, the show.state shape at 25 Hz and its silence when features stop, /healthz, /api/v1/state 503/200, a 501
stub, and the bind retry.

### artnet_out.py (done)
The P0 "virtual Maestro" (SHOW_ENGINE §0.1, §9.4, §9.5, §13.2). It sends one ArtDmx universe in exactly
`config/room/patch.yaml`'s profiles to room_service's own receiver, so the BeatFollower, music_direction and Hue
"Follow" run unchanged on the real music. The module docstring is the full channel spec.
```python
VirtualMaestro(*, target=None,                       # (host, port); None = OFF (the default): start() logs and returns
               patch_path=<repo>/config/room/patch.yaml, clock=None, features_fn=None, palette_fn=None,
               hue_latency_ms=0.0, hue_depth=0.7, room_dmx_url="http://127.0.0.1:8700/api/v1/dmx",
               poll_fn=None, poll_s=5.0, idle_after_s=5.0, layout=None, time_fn=time.monotonic,
               palette=None,                         # (r, g, b) 0..1: the fixed P0 colour (SHOW_PALETTE); None = amber
               quiet_after_s=10.0)                   # features.gate ~0 this long: idle ("quiet"); None = never
  .start() / .stop(timeout=2.0)                      # threads "show-artnet", "show-artnet-poll"
  .section_change()                                  # any thread: GOBO steps (+1, raw, mod 256)
  .set_hue_latency_ms(ms)                            # any thread
  .status() -> dict                                  # any thread: state off|stopped|no_patch|idle|sending|yielded,
                                                     #   idle_reason (no_features|quiet), target, universe, packets, seq,
                                                     #   rate_hz, late_ms p50/p95, frame_errors, rgb, beats, period_ms,
                                                     #   gobo, yield {from, since_s, count}, poll, patch
  .plan(t_last) -> float; .compose(t) -> bool; .channels() -> bytes; .check_foreign()   # loop steps (tests)
OutConfig(target=None, patch_path, hue_latency_ms=0.0, room_dmx_url, palette=None)   # config_from_env(env) builds it
build_artdmx(port_address, data, seq) -> bytes;  next_seq(seq);  ArtDmxFrame(pa)   # seq 1..255, never 0
parse_layout(raw) / load_layout(path) -> Layout(universe, devices);  grid_tick(period_s, max_hz=GRID_HZ 43)
foreign_sender(st, ours, fresh_ms=1500, *, own_hz=None, yielded=False) -> str | None
parse_palette(spec) -> (r, g, b) | None              # PALETTES name or "r,g,b" (0..1, or 0..255); ValueError if bad
```
- **Inputs, read by reference each frame (never a lock, never a call into show-rx):**
  - `clock.next_beat(t_s) -> float | None`: the first predicted *acoustic* beat at or after `t_s`, or None while
    nothing is beat-locked (§5.3's gate). clock.py must answer it from a state swapped by reference, from any
    thread. `clock.Clock` provides it (gate `beat`: conf on 0.70 / off 0.60). With `clock=None`: no pulses, frames
    free-run at 44 Hz.
  - **PRISM** (bar parity) is set at each beat edge in the clock's own numbering: `((k − bar_anchor_beat) //
    beats_per_bar) % 2`, k from the optional `clock.snapshot()` (the music.clock body), so it flips on the clock's
    downbeats and keeps its phase across idles, yields and restarts (review, 16 Oct). A clock without `snapshot()`:
    our own edge count // `clock.beats_per_bar` (default 4).
  - `features_fn = dsp.Features.latest`. `energy` → ACTIVITY, `brightness` → PAN, `bands.bass.level` → TILT, band
    hits per second → ZOOM, `punch` → the pulse's height.
  - `palette_fn(t_s)`: `(r, g, b)` or `{device id | screen name | "*": (r, g, b)}`, 0..1. Without one (P0, until
    the director in P2) every screen RGB and Hue lamp takes the fixed `palette` colour: `SHOW_PALETTE` (a name in
    `PALETTES`: amber, warm, white, cool, red, blue, green, violet; or `r,g,b`), else warm amber `(1.0, 0.62, 0.32)`.
- **Frames.** The grid is phase-locked to the beat (§5.4: tick = P/m, m a multiple of 4, ≤ GRID_HZ 43 Hz), so a
  frame leaves exactly at each beat. Each frame keeps 22.7 ms (Art-Net's 44 Hz) from when the last one actually
  left (`t_last` = max(due, sent)); the grid's 0.5 ms of slack means a frame that left a little late doesn't move
  the next. The screens' DIMMER `pulse` rises in the beat's frame. If the clock re-phases a beat into the 22.7 ms
  after the last frame, its edge goes one minimum gap later: never early, under one frame late. The pulse's height
  is 0.10 + 0.60 × punch, so music_direction's punch reads ours back. Hue channels carry the look for `t + L_hue`
  (MASTER DIMMER 255). It is a soft beat swell (80 ms attack, no hits), or the energy without a beat.
- **Bad values.** A NaN or inf reaching a channel sends the channel's default, and the inputs' clamps read NaN as 0,
  so it never sticks in a smoother. A failing frame is counted (`frame_errors`) and logged once, then at most every
  30 s.
- **Idle.** Nothing is sent (room's input goes stale and holds, as before show_service; its test show can start,
  and after 10 s room's Hue output hands the lamps back to the house) when:
  - there are no fresh `features` (5 s): `idle_reason` `no_features`;
  - the room is quiet: `features.gate` under 0.02 for 10 s, until it is back over 0.05: `idle_reason` `quiet`. The
    mic streams in silence too, so without this a silent room held every lamp at 30 % (1 − depth) of the colour.
  While we send, room's test show yields to us (fake_artnet treats any other sender as real Art-Net).
- **Yield.** Every 5 s it polls `GET :8700/api/v1/dmx` (`input`: last_from, age_ms, pps, source). With the input
  fresh (≤ 1500 ms), it pauses and logs when:
  - `input.last_from` is not one of ours (127.0.0.1 or our socket's address); or
  - while we send, `input.pps` is over our own rate + 8 (review, 16 Oct): a second sender interleaves with us. With
    us at ~42 packets/s and a Maestro at ~44, last_from showed us about half the time, which left 10-30 s of two
    senders flickering on one universe; and a sender relayed from 127.0.0.1 was never seen at all.
  While paused we send nothing, so any fresh real Art-Net (`source` "artnet", any address) keeps us paused; the
  first poll that shows the input stale (or only room's own test show) resumes. A failed poll changes nothing.
- **patch.yaml** is read-only for us. It is re-read on an mtime change, and a bad edit keeps the last good layout.
  It needs PyYAML (pinned in requirements.txt).
- **Wired in `__main__`:** `vm = VirtualMaestro(**vars(artnet_out.config_from_env()), target=<--artnet-target>,
  clock=<the Clock>, features_fn=features.latest, palette_fn=None)`, then `vm.start()`. It is stopped before rx on
  SIGTERM, and `vm.status()` is the state's `artnet`. Env: `SHOW_ARTNET_TARGET` (`127.0.0.1:6454` in production; empty or
  `off` = off), `SHOW_PATCH_FILE`, `SHOW_HUE_LATENCY_MS` (0 until P6), `SHOW_ROOM_DMX_URL` (empty = no yield
  check), `SHOW_PALETTE` (the P0 colour; a bad value is logged and amber used).
- *Measured* (niced, under the 4K render): plan + compose 36 µs p50, 58 µs p95; over a real socket, edges land
  0.2-0.4 ms after the beat at 41 Hz (124 bpm).

**Tests** (`tests/test_artnet_out.py`, 37): packet bytes (header, OpCode 0x5000 LE, ProtVer 14, Length BE even ≤ 512),
Port-Address split, seq 1..255 wrapping 255 → 1; the layout against patch.yaml (screen.north 1-10,
screen.south 17-26, hue.ne/nw/se/sw at 33/37/41/45, MASTER DIMMER 255), a reordered profile, layout errors, and
reload. The frame grid: `grid_tick` (≥ 0.5 ms over the minimum gap), edges exactly on the beat at 60-174 bpm,
re-phase (never early, < 1 frame late, no double edge), no lock (no pulse, 44 Hz), and the deadline loop on a
simulated clock with late sends (gaps ≥ 22.7 ms as sent, a 0.3 ms late frame costs no grid point). GOBO, PRISM
(fallback count, and the clock's own bar numbering), hue at `t − L_hue`, the hue swell, idle, the quiet room,
palette (env and constructor), NaN inputs, failing callbacks, a failing frame (counted, logged once), and slow
stand-ins. The yield rule (pure: address, rate, while yielded; paused then resumed, by address over UDP and by rate).
Off by default. End to end against an ephemeral local UDP socket: never 6454, and never room's API.

### clock.py (done)
The §5.3 beat clock. The module docstring is the full spec: pipeline, every constant, conf and the gates.
```python
Clock(publish=None, *, activation="auto"|"madmom"|"aubio"|<Activation>, nets=2, beats_per_bar=4, phrase_bars=8,
      prior_bpm=120.0, publish_hz=10.0, resample_quality="HQ")
  # show-rx:
  .process(stream, n0, t0_host_s, pcm, fs, flags)   # = contract.OnAudio (any block size, any fs, mono mix)
  .tick(now_s)                                      # music.clock at 10 Hz (+ at once on every re-anchor); coasting
  .reset()
  # any thread (reads the last published body, swapped by reference; never a lock):
  .next_beat(t_s, lead_s=0.0, *, gate="beat") -> float | None   # artnet_out's BeatClock: acoustic time of the first
                                                    # beat with t_k - lead_s >= t_s; None below the gate
  .next_beat_k(t_s, lead_s=0.0, *, gate="none") -> (k, t_k) | None
  .beat_at(t_s) -> float (NaN before a grid);  .beats_per_bar;  .snapshot() -> ClockData | None
  .state() -> {"clock": ClockData | None, "stats": stats()}
  # any thread (queued, applied on show-rx):
  .apply_integer_correction(beat_is_one=None, bar_len=None, octave=1.0)  # slow lane (Beat This!): a published beat
                                                    # index that is a one, beats per bar, octave 1|2|0.5; at a bar line
  .observe_hit(t_s, band, strength)                 # dsp's music.hit bodies (sub/bass refine phase and learn the lag)
  .tap(t_s) / .half() / .double() / .one(t_s)       # the show console's human actions
  .stats() -> dict   # source, state, gate, conf, bpm, epoch, lag_adj_ms, agc_db, cost_ms_per_10ms {p50,p95,max},
                     # errors, counts (frames, peaks, seeds, hits, misses, outliers, relocks, cold_searches, cold_hits,
                     # cold_alt, seed_halved, octave_changes, octave_kicks, octave_kick_refused, ...)
beat_at(body, t_s); next_beat(body, after_s, lead_s=0.0) -> (k, t_k) | None; position_at(body, t_s)   # consumers
Activation (Protocol: name, lag_s, floor, process_hop(441 samples @ 44.1 kHz) -> float, reset());
MadmomActivation(nets=2), AubioActivation(), make_activation(prefer="auto", nets=2), Tempogram (sliding DFT)
```
- **`music.clock`** = `clock.ClockData`: §5.3's `{bpm, anchor_ms, beat_at_anchor, beats_per_bar, bar_anchor_beat,
  phrase_bars, conf, bar_conf, phrase_conf, state: searching|locked|holding|free}`, plus `gate: beat|dmx|none` (the
  hysteresis 0.70/0.60 and 0.45/0.35, with a 2-bar dwell), `epoch` (+1 per published jump), `stability`, `source`
  and `t_ms`. `bpm` is 0.0 before the first grid. `beat_at_anchor` is an integer beat. `bar_anchor_beat` starts a
  bar *and* a phrase. The TypedDict is `contract.ClockData` (moved there on 16 Oct; clock.py re-exports it).
- **Wired in `__main__` (16 Oct):**
  - `on_audio` calls `features.process(...)`, then `clock.process(...)` with the same block, each under its own guard.
  - The publish given to Features also hands each `music.hit` body to
    `clock.observe_hit(d["t_ms"] / 1000, d["band"], d["strength"])` (guarded), then publishes it.
  - `Clock(publish=...)` with the defaults (madmom, 2 nets, about 0.4 s to load at start-up); `.tick` from `on_tick`,
    `.state()` in the snapshot, and the Clock itself is artnet_out's `clock` (`next_beat`, gate `beat`).
  - Measured phase (tests/test_e2e.py and offline on the same loop): the published beats sit about +3.5 to +4.3 ms
    after the synthetic kick's onset, with or without the hit tap (with it, slightly closer). That is madmom's lag on
    this timbre; §5.2's budget is ±25 ms p95, and latency.json's `acoustic_offset_ms` absorbs the rest.
- **As built (16 Oct):**
  - madmom (git 27f032e, pinned in requirements.txt) builds and runs on numpy 2.5.4. It is preferred, and aubio-ledfx
    0.4.12 is the fallback if it doesn't import.
  - Lock from cold in about 4.1 s on a loop. Steady phase error is within ±3.5 ms on synthetic kicks.
  - 120→124 bpm re-locks with no re-seed: < 15 ms within 4-8 s.
  - A 16-bar breakdown (pads or silence) holds the count and re-locks within 4 beats.
  - Random onsets and rubato stay at conf ≤ 0.30.
- **Song changes (review, 16 Oct).** A held grid used to re-seed only when its hits stopped. A new song near 3:4 or
  4:3 of the old tempo keeps catching hits on the old grid by coincidence, so 124 bpm drums → 8 s silence → 92 bpm
  soft chords sat at 122.7 bpm "holding" for 180 s, and 124 → 96 bpm drums sat at 128 bpm reported "locked" (conf
  0.27): no Art-Net beats for whole songs. Now a second trigger (`cold_alt`) starts the cold search when another
  tempo (not within 4 % of ours, its double or half) is ≥ 0.30 clear and ≥ 2× ours for 4 s, with rhythm present
  and conf low (hold below 0.45, fire below 0.35). Strong peaks are counted against the current peak level, and a
  cold search forgets the old song's level. The locked level and the peak floor fade (τ 20 s) from 8 s after the
  last locked hit; on a held grid the faded level applies only while our own tempo is clear in the tempogram, so
  pads still can't drag it (the aubio pads breakdown did, with an unconditional fade) and madmom's ghost beats
  after the drums stop face the full level. *Measured* (madmom, synthetic): re-locked (bpm ± 2 %, locked, gate
  dmx or beat, staying) 8.9 s after the change to 92 bpm chords, 7.9 s to 96 bpm drums, 8.2 s to 100, 3.3 s to
  140, 8.8 s to 100 after a 30 s gap; the same tempo back at 15 % level 2.2 s. aubio (the fallback) takes 12-32 s.
- **Octave from the kicks (review, 16 Oct).** A 96 bpm loop (kick every beat, snare on 2 and 4, 8th-note hats)
  locked at 191.9 bpm and never corrected: Art-Net fired on every 8th note. Above 160 bpm the kicks (dsp's
  sub/bass hits through `observe_hit`) now vote: on alternate beats only (< 70 % of the grid's beats) → the half,
  through the kicks, at the seed (the fitted fast grid, refitted at 2T with no comb, since madmom can peak as high
  on the off-beat hats) or by the octave check (8 beats, onto the kicks' parity). With no kick evidence a seed or a
  doubling never goes above 180 bpm. *Measured*: 96 bpm hip-hop → 96 (7.6 s without hits: the cap; 4.4 s with
  them); 88 bpm with hits → 88 on the kicks (4.8 s; 176 without hits: under the cap, no evidence); 174 bpm with a
  kick on every beat stays 174.
- **Decision (deviates from §4):** the tracker-lane AGC aims at **-12 dBFS RMS**, not -20 LUFS. madmom was trained on
  mastered audio. At -20 dBFS RMS a single LSTM took off-beat hats for the beat and two LSTMs couldn't decide; at
  -10, every ensemble took the kicks (measured).
- **Cost** (niced, under the 4K render; `tests/test_clock.py -s` prints it), per 10 ms of audio:
  - madmom 2 nets: 0.69-0.72 ms mean (7 % of a core). Blocks that carry a madmom frame cost 2.5 ms at p95, up to
    5-9 ms at peak.
  - aubio: 0.13-0.16 ms.
  - The single net was 0.45 ms a frame but flipped to off-beats; 8 nets cost 1.7 ms p50 and 5.7 ms p99 a frame.

**Tests** (`tests/test_clock.py`, 42):
- the Link formula, `next_beat` with a lead, and bar/phrase position;
- the sliding DFT against a brute-force windowed DFT (filling, full, past the phasor re-derivation);
- tempogram clarity and phase, and the peak picker;
- lock and accuracy on madmom and aubio, the published shape, the 10 Hz cadence, and no jump within an epoch;
- the tempo step, half-time with and without a snare, and the breakdown with pads and with silence;
- random onsets, rubato;
- bar and octave votes, the human one/half/double, tap without audio, no-audio → free → resume;
- gaps, a segment at 44.1 kHz, a failing activation, `observe_hit` learning the lag, and gated, any-thread reads;
- song changes (madmom): 124 bpm drums, 8 s silence, then 92 bpm soft chords or 96 bpm drums, re-locked within
  10.5 s by `cold_alt`;
- the octave: 96 bpm with 8th-note hats at 96 (with and without kick hits, beats on the kicks); 88 bpm at 176
  without hits and 88 on the kicks with them; 174 bpm with a kick on every beat unchanged.
- Known (pre-existing, not from this change): at 190 bpm with kick hits, conf wobbles 0.57-0.91 (gate beat/dmx);
  the same with the kick rules off.

### config.py and `__main__.py` (done)
`Sender` and `Config` are in contract.py (fields only), so the receiver needs nothing from config.py:
```python
contract.Sender(name, host, port=6980, streams=())
contract.Config(senders=(), vban_bind=("0.0.0.0", 6980), bus_bind=("127.0.0.1", 8703),
                latency_file=<repo>/config/room/latency.json,        # read-only for us (10's schema v2)
                log_level="INFO", stream_priority=())                # audio streams ranked first (receiver)
def parse_sender(spec: str) -> contract.Sender   # "kiosk=screen-machine-drawingroom[:6980][/room-mic,room-piano]"
                                                 # ValueError if bad; no streams = every contract.KNOWN_STREAMS name
def parse_bind(text: str, default: tuple[str, int]) -> tuple[str, int]   # "host:port" | ":port" | "host"
def load(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> contract.Config
def load_options(argv=None, env=None) -> Options  # Options(config, stream_priority (= config's), artnet_target,
                                                 # notes); never raises on bad values: they are skipped/defaulted
                                                 # and listed in notes
def acoustic_offsets(path: pathlib.Path) -> dict[str, float]   # sources.<stream>.acoustic_offset_ms; {} if absent; never raises
class LatencyWatch(path): .poll(force=False) -> dict | None     # offsets when mtime/size changed; None otherwise
                                                 # and over a torn/invalid file (the last offsets stay); .last, .errors
```
- **Env** (from `deploy/sm-show.env`; ca does the wiring, c8 the flags):
  - `SHOW_SENDERS` (`;`-separated specs);
  - `SHOW_VBAN_BIND`, `SHOW_BUS_BIND` (`host:port`);
  - `SHOW_LATENCY_FILE`, `SHOW_LOG_LEVEL`;
  - `SHOW_ARTNET_TARGET` (`host[:port]`, empty or `off` = off, the default). artnet_out's other keys
    (`SHOW_PATCH_FILE`, `SHOW_HUE_LATENCY_MS`, `SHOW_ROOM_DMX_URL`) are read by `artnet_out.config_from_env()`;
  - `SHOW_STREAM_PRIORITY` (`a,b,c`).
- **Args** override env: `--sender` (repeatable; any replaces `SHOW_SENDERS`), `--vban-bind`, `--bus-bind`,
  `--host` and `--port` (the bus, over `--bus-bind`: **scripts/ctl always passes `--port $SHOW_PORT`**),
  `--latency-file` (relative = from the repo root), `--log-level`, `--artnet-target` (over `SHOW_ARTNET_TARGET`),
  `--stream-priority a,b,c` (over `SHOW_STREAM_PRIORITY`; into `Config.stream_priority`: those audio streams rank
  first, in that order, then `contract.AUDIO_PRIORITY`; names over 16 characters are skipped with a note). Unknown
  arguments are logged and ignored.
- Sender hosts are DNS names, never literal IPs, in committed config (no hardcoded IPs).

`__main__.py` builds (each module imported lazily; a missing or failing one is logged, shown in the state's
`modules`, and left out: degrade, never refuse):
- `Bus(host, port, state_fn=…, health_fn=rx.health)`, started first, so `/healthz` answers during the slow imports;
- one `Clock(publish=…)` (clock.py, imported like the others);
- one `Features(publish=…)`, whose publish also hands `music.hit` bodies to `Clock.observe_hit`;
- `Receiver(cfg, on_audio, on_midi, on_tick)`; `on_audio` = `features.process`, then `clock.process` (the same block).
  The receiver delivers the active stream's blocks only, and flags the first after a switch `AUDIO_SEGMENT`, so
  `__main__` never rebuilds or resets Features or the Clock;
- `Piano(stream, publish, state_channels=(1,))` per MIDI stream (lazily; the kiosk sender's states are channel
  1's, see midi.py), muted (`set_voice(False)`) until it is the voice;
- one `Harmony`;
- `VirtualMaestro(**vars(config_from_env()), target=Options.artnet_target, clock=<the Clock>,
  features_fn=features.latest, palette_fn=None)`, started after rx and stopped before it.

Then:
- **One piano voice** (midi.py "Publishing"): only the voice Piano publishes music.note/pedal/notes. The voice is the
  present piano, live before test (harmony's pick). The packet that makes a piano present hands it the voice at once
  in `on_midi` when it outranks the current one (none, not present, or test against live); otherwise `on_tick`
  re-picks after the pianos tick. With no piano present the last voice keeps it (quiet after its present=False
  frame). So the bus carries one sess at a time, and a hand-over is one clean session change for sound_view.
- `on_tick` calls `piano.tick` (each), picks the voice, then `features.tick`, `harmony.tick(now_s, <present piano,
  live before test, else None>, features.chroma12())` and `clock.tick`; every callback runs under a guard (`features.process`,
  `clock.process`, `clock.observe_hit`, `on_midi`, `piano.tick`, `features.tick`, `harmony.tick`, `clock.tick`,
  `snapshot`: counted, logged at most every 30 s, shown in the state's `errors`), and every 0.5 s it swaps in the
  snapshot (pianos, Features stats, clock state) that the API reads.
- `GET /api/v1/state`: `service, version, t_ms, uptime_s, modules, config (senders, binds, latency_file,
  offsets_ms), active_audio, streams, socket, senders` (from `rx.health()`), `piano {present, voice (stream or
  null), streams {name: {present, voice, sess, seq, pedals, stats}}}`, `clock`, `features` (the latest
  music.features' summary, with `age_ms`),
  `features_stats`, `harmony` (the latest music.harmony), `artnet` (`vm.status()`), `bus` (`bus.stats()`),
  `errors` (guard counts).
- The main thread reloads `acoustic_offsets()` when latency.json's mtime changes (checked every 2 s) and calls
  `rx.set_offsets`.
- SIGTERM or SIGINT stops Art-Net out, then rx, then bus, and exits 0 (also when it arrives during start-up).
- It logs to stderr (`scripts/ctl` owns the log file).
- BLAS at 1 thread (env set before numpy loads), `sys.setswitchinterval(0.001)`. `Show.start(freeze_gc=True)`
  (main() passes it) runs one `gc.collect(); gc.freeze()` after building every module and before show-rx and
  show-artnet start: a full collection holds the GIL for about 50 ms (measured 48 ms with clock and dsp loaded), and
  at 10 s it landed on the live hot threads (one late Art-Net frame, late receive stamps). After 10 s only
  `gc.freeze()` (no collection: cheap) moves the warm-up's objects out of later collections.

**Tests** (`tests/test_config.py`, 24; `tests/test_main.py`, 6): sender specs and rejects, binds, env then args,
ctl's `--port`, bad values degrade with notes, the Art-Net target (off by default, flag over env), the stream
priority (env, flag, bad names), latency.json offsets and the watch (torn write keeps the last). The wiring with fake
modules (callbacks, guards, the clock after Features on the same block, `music.hit` to `observe_hit`,
live-before-test piano, offsets, state, /healthz); room-piano and test-piano together with the real midi.py (one
sess on the bus while live plays, the strike that hands over published, an idle piano quiet, two clean hand-overs:
the reviewer's case that gave sound_view 15 onsets in 3 s now gives 2), every module missing, a constructor failing, and
`python -m show_service` on an ephemeral port: up, /healthz, SIGTERM → exit 0. No senders and no Art-Net target
in tests, so nothing is sent anywhere.

## End to end (`tests/test_e2e.py`, `tests/synth_sender.py`)

**The test.** Every real module, wired by `__main__.Show`, runs in-process on ephemeral 127.0.0.1 ports (never 8703,
6980 or 6454). `SHOW_ROOM_DMX_URL` is empty, so nothing polls room's API.
- `synth_sender.SynthSender` behaves like 98's kiosk unit. It waits for the service's SUBSCRIBE, then sends:
  - `room-mic` in real time (48 kHz mono INT16, 128 frames) with heartbeats every 0.5 s, carrying
    `frame`/`n`/`t_cap_ns` on `time.monotonic_ns()`;
  - `test-piano` MIDI from the MAESTRO Bach prelude in config/live/test-piano/, read with mido (pinned): batches
    with `prev`, and a state every 0.5 s.
- The audio is `drum_and_bass_loop()`, 30 s at 124 bpm: a kick on every beat, snare on 2 and 4, hats, and a bass
  line on the 8ths.
- A WS client (role `e2e`) checks:
  - the welcome first;
  - `music.features` at 25 Hz, with `spectrum32` 32 values in 0..1;
  - `music.clock` locked at 124 ± 2 bpm within 15 s, with the median of the last 10 s within 1 bpm;
  - `music.note` with source `test`, `t_ms` non-decreasing, `seq` increasing, and every key on the sender's own
    timeline within a 5 ms p5-p95 spread;
  - `music.notes` and `music.harmony` (from MIDI, with a key);
  - `show.state` in K3 shape, with `global.music` and no `fixtures`.
- Mid-run, `/healthz` must be ok with both streams `streaming`, and `/api/v1/state` must show every module `ok`, no
  guard errors, Art-Net `sending`, and MIDI mapped `via` audio.
- Art-Net out goes to an ephemeral UDP socket standing in for room's receiver. The frames must be ≤ 44 Hz, and the
  screen DIMMER's pulse edges must land on the clock's predicted beats (median < 3 ms, p95 < 10 ms) and on the loop's
  own beats, within a constant offset (15 ms p5-p95 spread).
- **Run:** about 35 s. `-s` prints the numbers.

**Measured** (16 Oct, flock + nice 19, host rendering 4K; four e2e runs, all passing and within ±0.3 ms of these):
- **e2e:**
  - `music.features`: 25.0 Hz.
  - Clock: locked 4.2 s after the first packet; 124.00 bpm over the last 10 s; activation madmom.
  - Notes: `t_ms` is +0.13 ms from the key's true time (p5-p95 −0.01 to +0.20 ms).
  - Harmony: F minor, the piece's key, with a few frames of A♯ minor.
  - Art-Net: 41.4 Hz on the 124 bpm grid (sent ≥ 22.7 ms apart, counted from when each frame actually left since
    16 Oct; the receiving thread saw gaps down to 21.5 ms, its own scheduling jitter, against 19.4 ms when the gap
    was counted from the due time). Pulse edges are +0.5 ms from the
    predicted beat (p95 1.6 ms), and +4.3 ms after the loop's kicks (p5-p95 +3.9 to +6.2 ms; madmom's lag on this
    kick, see clock.py).
- **The real process.** `python -m show_service` ran as a subprocess with the synthetic sender, Art-Net to an
  ephemeral socket, and one `renderer` WS client connected. Over 20 s after a 10 s warm-up (`/proc/<pid>/stat`):
  - **33 % of one core** in all: show-rx 28 %, show-bus 3.4 %, show-artnet 1.2 %;
  - RSS 127 MB, 6 threads, exit 0 on SIGTERM, no ERROR lines in the log.
- **Where show-rx goes** (cProfile, single thread, 20 s of audio):
  - the clock's madmom about 9 % of a core;
  - dsp about 7 %;
  - receiver, VBAN parse and ticks about 4 %.
- **Under the GIL with all threads:** dsp's hop p95 reads 1.0-1.4 ms, against 0.37 ms alone, so the 0.45 ms budget
  is met only without contention (open issue).

## Contract decisions taken in this foundation (16 Oct)

The spec left these open; vban.py's docstring is the authority. They are also noted in SHOW_ENGINE.md §3.1 and §3.5.

1. **Heartbeat identity.** A heartbeat travels as a TEXT packet named after its audio stream, with its own TEXT
   counter. `frame` is the nuFrame of the audio packet whose first sample is `n`. `t_cap_ns` is the ADC time of
   sample `n`, on the sender's clock (decision 9). dBFS values are floored at −120 (strict JSON has no −inf).
   `frame`, `n` and `t_cap_ns` may be null before any audio.
2. **MIDI `seq`** counts event batches. A state packet's `seq` is the last batch it reflects and does not use one
   up. `prev` is newest first. A batch holds at most 32 events, so it always fits. **`sess` is mandatory** on MIDI
   from our senders (1-64 characters), and readers still accept it missing. A changed `sess`, or a `seq` more than
   8 behind, is a restart (`vban.midi_seq_verdict`). `keys[0]` is A0 (MIDI 21), and the keys are the ones
   physically down.
3. **TEXT header:** we send bit-rate index 0, channel ident 0, and UTF-8 (`0x10`). We read ASCII or UTF-8 and any
   bit-rate index (Voicemeeter sends `0x52`).
4. **Packet size:** 128 frames when they fit, otherwise the largest multiple of 32 (4 ch INT24 → 96, which is
   §3.1's figure).
5. **Integer full scale is 2^(N−1)** both ways, so +1.0 clips one LSB short. BYTE8 is decode-only.
6. **`on_audio` and `on_midi` take more arguments than the first sketch.**
   - `on_audio` gets `fs` (a stream may run at 44.1 kHz) and `flags` (`AUDIO_GAP`, `AUDIO_RESUME`,
     `AUDIO_SEGMENT`), so DSP can rebuild its filters, reset and skip onsets.
   - `on_midi` gets `to_host`, so midi.py places events without owning clocks.
   - `on_tick` is added, so all periodic publishing stays on the hot thread, lock-free.
7. **The CaptureMap works in the receiver's index space.** The heartbeat's sender `n` is used only to detect
   sender-side sample loss. The CaptureMap's residual is what detects capture time lost while the sender kept
   counting.
8. **One acoustic offset per MIDI stream** applies on both paths (through the audio map, or its own clock). The
   RemoteTimeMap's bridge puts the remote path on the audio path's constant, which includes the input buffering
   (`in_lat − frames/fs`), so the two paths agree.
9. **Sender clocks and overflows** (vban.py "Clocks" and "Sender rules"; also §3.1 and §3.5). On Linux, use
   `time.monotonic_ns()`. On Windows with Python 3.12, use `time.perf_counter_ns()`: `time.monotonic_ns()` there is
   GetTickCount64, with 15.6 ms steps. Use one clock per process. Take only the latency difference from PortAudio.
   On an input overflow the sender advances the counter and `n` over the lost packets, and starts a new `sess` if
   more than 250 ms was lost.
10. **`Sender` and `Config` live in contract.py**, so the receiver imports only contract, vban and timeline.

## Deploy (P0)

ca writes `deploy/sm-show.env` and the ctl entry; c8 owns the flags. `scripts/ctl` reads the file as KEY=VALUE lines
and never sources it:
- `SHOW_PORT` becomes `--port`;
- `SHOW_ARGS` is split into flags (never put `--port` in it: ctl refuses);
- `SHOW_HOLD` blocks start;
- every other `KEY=VALUE` line becomes an environment variable.

**Production**, once 98's kiosk sender (`tools/show_sender/`, `show-sender.service` on udp 6980) is deployed:
```
SHOW_PORT=8703
SHOW_ARGS="--sender kiosk=screen-machine-drawingroom:6980/room-mic,room-piano --artnet-target 127.0.0.1:6454 --log-level INFO"
PYTHONUNBUFFERED=1
CUDA_VISIBLE_DEVICES=
```
- **`--sender`** names the kiosk by its DNS name, never an IP. It must resolve inside WSL
  (`getent hosts screen-machine-drawingroom`). **On 16 Oct it doesn't** (the lookup times out). Until it does,
  health shows `senders.kiosk.resolve_error`, the receiver retries every 5 s and nothing else breaks.
- **`--artnet-target 127.0.0.1:6454`** is the only target, and it is what turns the virtual Maestro on (P0: room's
  own Art-Net receiver, universe 0, patch.yaml's layout).
  - Without it (or with `off`), nothing is sent.
  - With it, nothing is sent while there is no audio (5 s without features), or while the room is quiet (the
    loudness gate under 0.02 for 10 s; back over 0.05 resumes), so room's test show still runs and, 10 s later,
    room's Hue output hands the lamps back to the house.
  - **While it sends, every screen's RGB and all four Hue lamps take one fixed colour** (P0 has no palette until
    the director, P2): `SHOW_PALETTE` (amber, warm, white, cool, red, blue, green, violet, or `r,g,b`), default
    warm amber `(1.0, 0.62, 0.32)`. The lamps swell on the beat between 30 % and 100 % of it (energy without a
    beat). Room's own test show, which this displaces, cycles section colours.
  - It yields to a real Maestro: room's `:8700/api/v1/dmx` is polled every 5 s (its `last_from`, and its `pps`
    against our own rate, so a Maestro relayed from 127.0.0.1 is seen too).
- **Before the kiosk sender exists:** `SHOW_ARGS="--log-level INFO"`. With no senders it listens and serves
  `/healthz`, and Art-Net is off.
- **PC22's test piano** (`tools/show_sender/show_sender.py --test-piano … --port 6981`) can be added as a second
  `--sender pc22=<PC22's DNS name>:6981/test-piano`. Only one piano publishes at a time (the present one, the room's
  before the test piano), so the renderer never sees two note sessions at once.
- **Optional lines:**
  - `SHOW_STREAM_PRIORITY=a,b` (audio streams ranked first);
  - `SHOW_PALETTE=amber` (the P0 colour of screens and lamps, above);
  - `SHOW_HUE_LATENCY_MS=0` (until P6);
  - `SHOW_ROOM_DMX_URL=` (empty means no yield check; the default is room's);
  - `SHOW_LATENCY_FILE` (the default is config/room/latency.json, read-only for us).
- `CUDA_VISIBLE_DEVICES=` (empty) follows §11.3: show_service is CPU only.

**What ca's `scripts/ctl` `show` entry must contain** (the same pattern as `stream`):
```
    show)
      SESSION="sm-show$SUFFIX"; MODULE=show_service; PY="$HOME/.venvs/sm-show/bin/python"
      ENVF="$ENV_DIR/sm-show.env"; KEY=SHOW; LOG="$LOG_DIR/show_service.log"; WAIT=30 ;;
```
- **Names:** `show` is accepted by `for_each` and included in `all`. It is started at boot, with no hold line (§13.1).
  `ports` lists `show 8703 localhost (bus + API)` and `vban 6980/udp WSL socket, receiver-initiated`.
- **Start:** ctl's usual `cd $REPO && exec $PY -m show_service --port $SHOW_PORT $SHOW_ARGS`. svc_pid's cmdline
  match on `show_service` works.
- **Health:** `/healthz` answers about 1 s after start, because the bus comes up before the slow imports. Every
  module is ready within about 3 s; madmom loads in about 0.4 s.
  - `GET http://127.0.0.1:8703/healthz` returns 200
    `{"ok": true, "service": "show_service", "version", "uptime_s", "clients", "active_audio", "streams"}` whenever
    the process is up.
  - The details are in `GET /api/v1/state`: modules, streams, clock, features, harmony, artnet, bus and errors.
- **Stop:** SIGTERM (ctl's `kill -TERM`). It stops Art-Net out, then the receiver, then the bus, and exits 0 within
  about 1-3 s.
- **Logs:** it logs to stderr only. The screen logfile (`show_service.log`) is the log.
- **Venv:** `~/.venvs/sm-show`, from `show_service/requirements.txt`. madmom builds from git source, about 2 min;
  §13.4 wants it kept as a local wheel.
- **Ports** (ca adds the PORTS.md rows):
  - 8703 binds `127.0.0.1` only, and the LAN reaches it through room's `:8700/show/*` pass-through (P2, 2f).
  - 6980/udp needs no portproxy or firewall rule. The receiver sends SUBSCRIBE out and the kiosk answers to that
    source; WSL's NAT carries the replies.
- **The renderer is already pointed at it:** `deploy/sm-stream.env` passes `--music-bus ws://127.0.0.1:8703/v1/show`.
  stream_service connects as a `renderer` as soon as show_service is up, and its sound views start following.

**Dev smoke run** (no kiosk; never on 8703, which the renderer reads):
```
~/.venvs/sm-show/bin/python -m show_service.tests.synth_sender --bind 127.0.0.1:6990 --seconds 60 &
~/.venvs/sm-show/bin/python -m show_service --port <free port> --vban-bind 127.0.0.1:0 \
    --sender synth=localhost:6990/room-mic,test-piano
curl -s 127.0.0.1:<free port>/api/v1/state
```
