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
  reached only through `:8700/show/*`. The test sources (Stage 3) are children on `127.0.0.1:6981/udp` (piano) and
  `127.0.0.1:6982/udp` (audio) while they play.
- **Files it writes:** `~/.local/state/screen-machine/show/` (`--state-dir`): `state.json`, `director.jsonl`,
  `autopilot.jsonl`, `listener.jsonl`, `test-piano.log`, `test-audio.log`. It reads `config/room/show.yaml`,
  `config/room/vibes.yaml`, `config/room/patch.yaml`, `config/llm.yaml` and `config/live/vocab.yaml`, each re-read when
  it changes.

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
| **Stage 3** (below) | | |
| `brain.py` | the show-brain thread: components, ticks, `Brain.call`, show.yaml reloads, state.json | **done**, tested, wired |
| `director.py`, `palette.py`, `vibes.py` | the art director (§8): vibes, looks, lamps and screens, `palette_fn` | **done**, tested, wired |
| `situations.py`, `autopilot.py` | Auto (§8.9): the situation vocabulary, `NoteTap`, `RoomLink`, the supervisor | **done**, tested, wired |
| `api.py`, `state_store.py` | the Music card's API and `/v1/events` (§10, §11.2); state.json | **done**, tested, wired |
| `sources.py`, `test_audio.py`; the receiver seam | test piano and test audio as children; `set_senders`, `set_priority`, host fallback | **done**, tested, wired |
| `listener.py`, `ring.py`, `utils/llm/` | the LLM listener (§7) and its 120 s ring | **done**, tested, wired (AI listening off by default) |
| `tests/test_e2e.py::test_brain_end_to_end` | Stage 3 end to end against a fake room | **done**, passing |
| `room_service/adapters/show.py` | the room's "Feel" adapter (S3.11) | **done**, tested; live at the next Flask restart |

**Process split.** §2 has two processes: show_rt (hot, no aiohttp) and show_brain. For now everything runs in one
process: the brain is a thread (`show-brain`), aiohttp stays on its own thread (`show-bus`), and the hot thread only
ever calls `publish()` and the two taps. When the split happens, `publish()` and BrainContext's engine-side slots
become the IPC seam.

## Threads and data flow

```
 senders ──VBAN/UDP──► [show-rx]  Receiver.handle(buf, addr, arrival_s)
                          │  vban.parse → registry → SampleClock / CaptureMap / RemoteTimeMap
                          │  gaps (hold, zero-fill), segments, health (2 Hz snapshot)
                          ├─ on_audio(stream, n0, t0_host_s, pcm, fs, flags) ► Features.process, then Clock.process,
                          │                                                     then Rings.write (Stage 3; returns at
                          │                                                     once while the listener hasn't armed
                          │                                                     it) (active audio stream only, same
                          │                                                     block)
                          │      Features' music.hit bodies → Clock.observe_hit (sub/bass hits refine the phase)
                          ├─ on_midi(stream, msg, arrival_s, to_host) ─────► NoteTap.on_midi (Stage 3), then
                          │                                                   midi.Piano.on_midi
                          └─ on_tick(now_s) every ≤10 ms ─────────────────► Piano.tick · Features.tick · Harmony.tick
                                                                              · Clock.tick · (0.5 s) snapshot, PianoView
                                                                              │ publish(msg)  (thread-safe, non-blocking)
                       [show-dns]  resolves sender hosts every 30 s           ▼
                                   (at once after Receiver.set_senders)
                       [show-bus]  asyncio + aiohttp: WS fan-out, /healthz, /api/v1/state, api.py's routes and
                                   /v1/events (Stage 3: they reach the brain only through Brain.call and snapshots)
                       [show-brain] (Stage 3) one asyncio loop: room · situations · autopilot · sources · listener
                                   at 1 Hz, director at 10 Hz; RoomLink's WS, listener calls, art fetches, children
                       [show-artnet]       deadline loop ≤44 Hz: clock.next_beat + features.latest() +
                                           Director.palette_fn (all by reference)
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
    # Stage 3 (S3.10, built 16 Oct by the integrator):
    def set_senders(self, senders: tuple[contract.Sender, ...]) -> None   # any thread; the whole list, next tick
    def set_priority(self, names: tuple[str, ...]) -> None     # any thread; ranked before cfg.stream_priority
    def senders(self) -> tuple[contract.Sender, ...]           # the list show-rx uses now
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
- **Stage 3:** the list starts as `cfg.senders` and `set_senders` replaces it (sources.py, from show.yaml). Each of a
  sender's `hosts` is resolved, and SUBSCRIBE goes to the first host that has answered within 3 s, else to all of them
  (S3.10 "The receiver seam").

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
 "senders": {name: {"host", "port", "addr", "resolve_error", "send_error", "streams": [...], "last_subscribe_ms",
                    "hosts": [{"host", "addr", "resolve_error", "answered_age_ms"}], "using": host|None,
                    "on_demand": bool}},                                         # host: "using", else hosts[0]
 "priority": [...],                                                              # set_priority's, then --stream-priority
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
  `__main__` passes show.yaml's `situations.midi.channels` (`[1]`; review, 16 Oct: it was a code constant). A state
  then releases or resets only keys and pedals last set on
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
                                                     #   idle_reason (no_features|quiet), held (the same words while
                                                     #   the director's look goes out through them), target, universe,
                                                     #   packets, seq,
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
- **Idle.** While the director is neutral (`palette_fn` gives no dict: P0's fixed colour), nothing is sent (room's
  input goes stale and holds, as before show_service; its test show can start, and after 10 s room's Hue output hands
  the lamps back to the house) when:
  - there are no fresh `features` (5 s): `idle_reason` `no_features`;
  - the room is quiet: `features.gate` under 0.02 for 10 s, until it is back over 0.05: `idle_reason` `quiet`. The
    mic streams in silence too, so without this a silent room held every lamp at 30 % (1 − depth) of the colour.
  **While the director plays a vibe** (`palette_fn` returns a dict; review, 16 Oct) neither idles: its colours keep
  going out, so its relaxed base look, Lights low and House lights reach the lamps in a quiet room too (§8.5 "never
  black"). Through such a stretch (`status()["held"]`: `quiet` or `no_features`) the frame is steady: `hue_depth` 0,
  no DIMMER pulses, no beat edges or PRISM flips; PAN, TILT and ZOOM hold, ACTIVITY is 0.
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
  clock=<the Clock>, features_fn=features.latest, palette_fn=<the Director's palette_fn, None without a director>)`,
  then `vm.start()`; `ctx.set_look_out = vm.set_look` and `ctx.section_change = vm.section_change` (Stage 3). It is stopped before rx on
  SIGTERM, and `vm.status()` is the state's `artnet`. Env: `SHOW_ARTNET_TARGET` (`127.0.0.1:6454` in production; empty or
  `off` = off), `SHOW_PATCH_FILE`, `SHOW_HUE_LATENCY_MS` (0 until P6), `SHOW_ROOM_DMX_URL` (empty = no yield
  check), `SHOW_PALETTE` (the P0 colour; a bad value is logged and amber used).
- *Measured* (niced, under the 4K render): plan + compose 36 µs p50, 58 µs p95; over a real socket, edges land
  0.2-0.4 ms after the beat at 41 Hz (124 bpm).

**Tests** (`tests/test_artnet_out.py`, 42): packet bytes (header, OpCode 0x5000 LE, ProtVer 14, Length BE even ≤ 512),
Port-Address split, seq 1..255 wrapping 255 → 1; the layout against patch.yaml (screen.north 1-10,
screen.south 17-26, hue.ne/nw/se/sw at 33/37/41/45, MASTER DIMMER 255), a reordered profile, layout errors, and
reload. The frame grid: `grid_tick` (≥ 0.5 ms over the minimum gap), edges exactly on the beat at 60-174 bpm,
re-phase (never early, < 1 frame late, no double edge), no lock (no pulse, 44 Hz), and the deadline loop on a
simulated clock with late sends (gaps ≥ 22.7 ms as sent, a 0.3 ms late frame costs no grid point). GOBO, PRISM
(fallback count, and the clock's own bar numbering), hue at `t − L_hue`, the hue swell, idle, the quiet room (and the
director's look held through it and through stale features, steady and without pulses; idle again when neutral),
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
  - `SHOW_STREAM_PRIORITY` (`a,b,c`);
  - Stage 3: `SHOW_BRAIN` (`on`, the default, or `off`: the rollback switch), `SHOW_FILE` (show.yaml),
    `SHOW_VIBES_FILE` (vibes.yaml), `SHOW_STATE_DIR` (state.json and the logs).
- **Args** override env: `--sender` (repeatable; any replaces `SHOW_SENDERS`), `--vban-bind`, `--bus-bind`,
  `--host` and `--port` (the bus, over `--bus-bind`: **scripts/ctl always passes `--port $SHOW_PORT`**),
  `--latency-file` (relative = from the repo root), `--log-level`, `--artnet-target` (over `SHOW_ARTNET_TARGET`),
  `--stream-priority a,b,c` (over `SHOW_STREAM_PRIORITY`; into `Config.stream_priority`: those audio streams rank
  first, in that order, then `contract.AUDIO_PRIORITY`; names over 16 characters are skipped with a note),
  `--no-brain` / `--brain` (over `SHOW_BRAIN`), `--show-file`, `--vibes-file`, `--state-dir`. Unknown arguments are
  logged and ignored.
- `Options` gains `brain`, `show_file`, `vibes_file` and `state_dir`. `load_options` turns the brain on unless told
  otherwise; an `Options` built in code (the tests) has `brain=False` unless it says so, so no test can reach the real
  show.yaml, room or state directory by accident.
- Sender hosts are DNS names, never literal IPs, in committed config (no hardcoded IPs).

`__main__.py` builds (each module imported lazily; a missing or failing one is logged, shown in the state's
`modules`, and left out: degrade, never refuse):
- `Bus(host, port, state_fn=…, health_fn=rx.health)`, started first, so `/healthz` answers during the slow imports;
- one `Clock(publish=…)` (clock.py, imported like the others);
- one `Features(publish=…)`, whose publish also hands `music.hit` bodies to `Clock.observe_hit`;
- `Receiver(cfg, on_audio, on_midi, on_tick)`; `on_audio` = `features.process`, then `clock.process` (the same block).
  The receiver delivers the active stream's blocks only, and flags the first after a switch `AUDIO_SEGMENT`, so
  `__main__` never rebuilds or resets Features or the Clock;
- `Piano(stream, publish, state_channels=<show.yaml situations.midi.channels>)` per MIDI stream (lazily; the keys'
  channel, [1] for the adsilent, whose own playback on channel 2 then stays held; see midi.py; read from the brain's
  settings, or with `--no-brain` from show.yaml once at start; unset: every channel), muted (`set_voice(False)`)
  until it is the voice. No channel is a code constant (review, 16 Oct);
- one `Harmony`;
- `VirtualMaestro(**vars(config_from_env()), target=Options.artnet_target, clock=<the Clock>,
  features_fn=features.latest, palette_fn=<Director.palette_fn | None>)`, started after rx and stopped before it
  (Stage 3: the brain is built before it, so the director's `palette_fn` exists; see "Stage 3 wiring" below).

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

**Stage 3 wiring** (README S3.4 "`__main__`", as built): with the brain on, `Show.__init__` builds `api.routes(lambda:
self.brain, self._engine, log_files=...)` and hands it to `Bus(routes=...)`; `Show.start` then builds clock, dsp,
midi, harmony and the receiver, then `ring.Rings()` and `situations.NoteTap()`, the `BrainContext` (engine side below),
`brain.Brain(ctx, show_file=, state_file=<state_dir>/state.json)`, then `attach(ctx, **kw)` for autopilot, sources,
listener and director (each `brain.add`ed), then Art-Net with `palette_fn=<Director>.palette_fn`, and finally fills
`ctx.section_change = vm.section_change` and `ctx.set_look_out = vm.set_look` (the method itself, so the director's
`reset=True` reaches it). Then gc freeze, `rx.start()`, `artnet.start()`, `brain.start()`. Stop: brain (children and
state.json), then Art-Net, rx, bus.
- **Paths per component** (from the options): director `log_path=<state_dir>/director.jsonl`,
  `vibes_path=<vibes_file>`, `patch_path=<artnet_out's SHOW_PATCH_FILE>`; autopilot `log_path=<state_dir>/autopilot.jsonl`;
  listener `log_path=<state_dir>/listener.jsonl`; sources `log_dir=<state_dir>`. `Show(opts, brain_kw={name: {...}})`
  adds keyword args per module (tests only: a fake LLM, fake clocks). Every `attach` takes `**kw` for this.
- **Engine-side slots:** `features=Features.latest`, `clock=Clock.snapshot`, `harmony=lambda: Harmony.latest`,
  `health=Show.health`, `rings`, `notes` (the NoteTap), `piano` (the PianoView below), `clock_action` (tap → `clock.tap(t)`,
  half, double, one → `clock.one(t)`), `set_gate_ref` (`Features.gate_ref_lufs`), `set_senders`/`set_priority` (the
  receiver's), `static_senders=cfg.senders`, `notify=api.notify`.
- **PianoView** (built on show-rx with the 0.5 s snapshot): the voice piano's `present`, `stream`, `source`,
  `keys_down`, `damper`, `last_note_age_s` (its table's newest T_ON, or the NoteTap's newest strike), `velocity` (EMA,
  τ 2 s, of the strikes since the last view, from the NoteTap) and `notes_per_s` (the voice stream's NoteTap strikes in
  the last 4 s, / 4).
- **Guards** gain `ring.write`, `notetap` and `piano.view`.
- **`GET /api/v1/state`** gains `brain`: `Brain.snapshot()` plus `ring` (`Rings.info()`), `notes` (`NoteTap.stats()`)
  and `piano_view`; null with `--no-brain`. It also gains `api` (`api.stats()`: the card socket's `clients`,
  `patches`, `refreshes`, `card_errors`, `refused`, `notified`, `overflows`, and `mounted`), null with `--no-brain`, so
  the Spectre card sees a card that fails to build without reading the log. `modules` lists the Stage-3 modules too (`api`, `ring`, `situations`,
  `brain`, `autopilot`, `sources`, `listener`, `director`).

**Tests** (`tests/test_config.py`, 24; `tests/test_main.py`, 11; `tests/test_brain.py`, 12): sender specs and rejects, binds, env then args,
ctl's `--port`, bad values degrade with notes, the Art-Net target (off by default, flag over env), the stream
priority (env, flag, bad names), latency.json offsets and the watch (torn write keeps the last). The wiring with fake
modules (callbacks, guards, the clock after Features on the same block, `music.hit` to `observe_hit`,
live-before-test piano, offsets, state, /healthz); room-piano and test-piano together with the real midi.py (one
sess on the bus while live plays, the strike that hands over published, an idle piano quiet, two clean hand-overs:
the reviewer's case that gave sound_view 15 onsets in 3 s now gives 2), every module missing, a constructor failing, and
`python -m show_service` on an ephemeral port: up, /healthz, SIGTERM → exit 0. No senders and no Art-Net target
in tests, so nothing is sent anywhere.
- **Stage 3 (test_main.py):** the brain's wiring with fake components and the real brain.py and api.py (the paths
  each component gets, every engine-side slot, Art-Net's palette is the director's, the ring after the clock and the
  note tap before the piano, the card and the state answering, the brain stopping before Art-Net); a missing Stage-3
  module left out (Art-Net keeps the fixed colour without a director); `--no-brain` builds nothing and writes nothing;
  the PianoView from the real midi.py and NoteTap; `python -m show_service --no-brain` has no Stage-3 routes (bus.py's
  501 stubs), and `python -m show_service` with the brain on, with HOME, show.yaml (a dead room port) and the state
  directory all in tmp, answers the card and writes state.json at SIGTERM.
- **brain.py (test_brain.py):** provides into ctx, duplicates refused; restore → configure → start on show-brain;
  ticks at their rates in add order, guarded (a raising tick is counted, the rest carry on); `Brain.call` from another
  loop (the result, an awaited coroutine, fn's own ValueError, a timeout that cancels, 20 at once); `dirty()` writes
  state.json within a second and the next start restores it; a torn state.json starts cold (`.bad-<epoch>`); show.yaml
  hot reload, a bad edit keeps the last good, a missing or bad file at start is neutral (`{}`); stop in reverse order
  with a final state.json.

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

**Stage 3 end to end (`test_brain_end_to_end`, about 30 s).** The same wiring with the brain on, against
`test_autopilot.FakeRoom` on its own thread and an ephemeral port (modes `gallery`, `piano` with
`enter_when: {piano_playing: {for_s: 3}}` and a `skip`, `disco`; Auto on in the fake). show.yaml and vibes.yaml are
written to tmp (c8's `lamps` and `director` sections copied from config/room/show.yaml; two test vibes), the state
directory is tmp, and the listener gets a fake LLM with no usable model, so nothing is ever uploaded.
1. `POST /api/v1/test {audio}` plays a fetched track through the real test-audio child (a steady-beat genre, copied
   into tmp; a synthetic loop when nothing is fetched). The receiver subscribes it (`set_senders`) and ranks it first
   (`rank_first` → `set_priority`), so `test-audio` becomes the active stream; features and the clock follow it.
2. Neutral first: room.look with `vibe: null`, `palette_fn` None. `PUT /api/v1/vibe` → the readback; an unknown vibe
   → 400 with choices; room.look for the vibe, and Art-Net's screen RGB leaves the fixed amber.
3. Nudges: Wilder (steps 1), a palette pick (a look_change room.event, then room.look with that palette), Next look,
   two clock words. The card shows them.
4. A restart of the in-process service: the vibe comes back `restored`, with the steps and the palette pick; the test
   source is off.
5. `POST /api/v1/test {piano}` plays a .mid through 98's show_sender as a child; music.note arrives (source test).
   Auto PUTs `/api/v1/mode {mode: piano, source: autopilot, reason: "the piano is playing (piano/enter_when)",
   expect: {current: gallery, last_transition_id: null}, skip}`; the nudges clear with the mode; a person's switch to
   Disco puts Auto in `holding`, and no further PUT comes in 4 s while the piano still plays.

**Measured, Stage 3** (16 Oct, flock + nice 19, host rendering 4K):
- **e2e:** the techno track ("Storm", Mr.Dee): the clock locked at 128.3 bpm; 248 features from test-audio in ~10 s;
  brain tick p95: director 1.19 ms, listener 2.7 ms, sources 0.9 ms, situations 1.0 ms, autopilot 0.5 ms; loop lag
  p95 2.0 ms.
- **The real process** (`python -m show_service`, brain on, HOME and every file in tmp, a fake room, Art-Net to a local
  sink, one `renderer` WS client, the `party` vibe with listening off), over 30 s after a 12 s warm-up while the test
  audio played:
  - **show_service 37-38 % of one core** (two runs; P0 was 33 %): per thread (an in-process run, same load) show-rx
    31.4 % (with the ring copy), show-bus 3.5 %, show-artnet 1.4 %, **show-brain 1.2 %**; RSS 152-156 MB, 7 threads.
  - The test-audio child 13.5-14.3 % and its ffmpeg 3.3 % (only while a test plays).
  - Brain tick p95: director 1.7-2.0 ms (over its 1 ms budget under the GIL; 0.6-0.8 ms alone, see S3.7),
    situations 0.6-0.8 ms, autopilot 0.3-0.5 ms, sources 0.6-1.1 ms, listener 0.1-0.6 ms; loop lag p95 2.1-2.2 ms.
  - Art-Net 39-39.5 Hz with the director's colours (`palette: director`), frames late p50 0.19 ms, p95 0.86 ms, none
    late by a frame; dsp hop p95 1.2-1.3 ms (as P0 under the GIL); exit 0 on SIGTERM; no ERROR lines. At exit nanobind
    prints "leaked 2 instances" for soxr's two resamplers (the clock's and the ring's) after "stopped": harmless.

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
  - **While no vibe is set, every screen's RGB and all four Hue lamps take one fixed colour**: `SHOW_PALETTE`
    (amber, warm, white, cool, red, blue, green, violet, or `r,g,b`), default warm amber `(1.0, 0.62, 0.32)`. The
    lamps swell on the beat between 30 % and 100 % of it (energy without a beat). Room's own test show, which this
    displaces, cycles section colours. **Stage 3:** once a mode (or `PUT /api/v1/vibe`) sets a vibe, the director's
    `palette_fn` gives every screen and lamp its colour, and `set_look` sets the swell depth and beat lock per vibe;
    `status()["palette"]` says which (`fixed` | `director`).
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
- **Stage 3 lines** (all optional): `SHOW_BRAIN=off` (the rollback: no director, Auto, test sources, listener or
  Stage-3 routes; exactly P0), `SHOW_FILE`, `SHOW_VIBES_FILE`, `SHOW_STATE_DIR` (their defaults are the production
  paths). The exact going-live steps are S3.14.

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

## Stage 3: the brain (director, vibes, Auto, API, test sources, listener)

**Interfaces, 16 Oct (c8).** This section defines Stage 3 so that six builders can work in parallel, each in its own
files.
- The shared names and the **exact dict shapes** are the TypedDicts in **contract.py's "Stage 3" block**.
- This section covers the files and threads, every signature, the HTTP and WS bodies, the config schemas and the rules.
- It supersedes the Status table's "later" row for `ring.py` and show_brain.
- SHOW_ENGINE.md is the design (§7, §8, §10, §11.2). Where the two differ, this section is the build spec.

### S3.0 Rulings every builder keeps (owner, 15-16 Oct)

1. **No special cases; everything is config.** No code branches on a mode name, a vibe name, a device name or
   "piano".
   - Behaviours are fields (`contract.VIBE_FIELDS`, §8.1's table).
   - Situations are a fixed vocabulary; which mode uses which is config.
   - Lamp roles come from patch.yaml's `placement`, through show.yaml.
2. **No invented values in live config.** Offer real choices, and leave unknowns unset. An unset field takes its
   documented **neutral** value (S3.5).
   - Design defaults are marked "starting point" where they are written (vibes.yaml, show.yaml), or listed as Auto's
     design defaults (S3.8).
   - The GET answers say which values are defaults.
3. **The show never dims a screen.**
   - `level`, `floor`, Lights low, House lights and dips go to lamps and DMX only.
   - `palette_fn`'s screen entries and `LookData.screens` are never scaled by any of them. A test proves it (B1).
4. **Degrade, never refuse.**
   - A missing module, file or service gives a quieter behaviour, never an error page, and no control is ever
     disabled.
   - **No auth.**
   - **No hard-coded IPs:** device addresses live in `config/room/*` (show.yaml, sonos.json). The loopback URLs in
     contract are defaults that show.yaml overrides.
5. **Auto** acts only through room `PUT /api/v1/mode {mode, source: "autopilot", reason, expect, skip}`.
   - It is off by default.
   - It holds after a person's choice.
   - It treats Undo as a correction.
   - It never re-requests the current mode.
   - It announces every switch.
6. **Put-back belongs to the room:** the generic per-row `{"set": v, "restore": true}`. show_service never stores or
   restores device state. The director reads the room's stored house state to colour lamps during House lights; that
   is read-only.

### S3.1 Builders and files

Each builder edits only its own files, plus the seams listed in the last column. Nobody else edits those seams.

| Builder | Its files (and tests named for them) | Seams it alone edits |
|---|---|---|
| **B1 director** | `director.py`, `palette.py`, `vibes.py`; `tests/test_director.py`, `test_palette.py`, `test_vibes.py` | `artnet_out.py`: `VirtualMaestro.set_look()`, with tests in `test_artnet_out.py`; `requirements.txt` (Pillow) |
| **B2 auto** | `situations.py`, `autopilot.py`; `tests/test_situations.py`, `test_autopilot.py` | none |
| **B3 api and wiring** | `api.py`, `brain.py`; `tests/test_api.py`, `test_brain.py` | `bus.py` (the `routes` seam and `LATEST_ON_CONNECT`); `__main__.py` (Stage-3 wiring); `config.py` (`--no-brain` / `SHOW_BRAIN`); additions to `test_bus.py`, `test_main.py` and `test_config.py` |
| **B4 sources** | `sources.py`, `tools/show/fetch_test_music.py`; `tests/test_sources.py` | `receiver.py`: `set_senders`, `set_priority` and `Sender.hosts` fallback, with additions to `test_receiver.py` |
| **B5 room adapter** | `room_service/adapters/show.py`; `room_service/tests/roomstate/test_show_adapter.py` | `room_service/adapters/__init__.py`: one `show` parameter in `build_adapters` (tell aa) |
| **B6 listener** | `listener.py`, `ring.py`, `utils/llm/` (package, its tests in `utils/llm/tests/`), `config/llm.yaml`; `tests/test_listener.py`, `test_ring.py` | `requirements.txt` (openai 1.77 and its pins) |

c8 owns `contract.py`, `config/room/show.yaml`, `config/room/vibes.yaml` and this README.
- A builder that needs a contract change asks c8; it never edits contract.py itself.
- `requirements.txt` is append-only: one commented block per builder.
- Builders don't commit; c8 does.

### S3.2 Module map and imports

- **Imports.** A Stage-3 module imports `contract` (plus `vban` or `timeline` if needed), the stdlib and its own
  pinned dependencies. It never imports another builder's module.
  - Within one builder, imports are fine: director → vibes and palette; autopilot → situations.
  - Only listener imports `utils.llm`, and lazily.
- **Components.** Each component module exposes `attach(ctx: contract.BrainContext) -> Component | list[Component]`
  (`contract.Component`: `name`, `tick_hz`, `provides`, `configure`, `restore`, `start`, `tick`, `snapshot`,
  `persist`, `stop`).
- **Calls between components** go through `ctx` slots (filled from `provides()`) and through the component methods
  that api.py calls on `brain.components[name]`, with the signatures below.

| Module | `attach(ctx)` returns | Component names | tick_hz | Also exports (made by `__main__`) |
|---|---|---|---|---|
| autopilot.py | `[RoomLink, Situations, Autopilot]` | `room`, `situations`, `autopilot` | 0, 1, 1 | none |
| situations.py | none (autopilot builds `Situations`) | none | none | `NoteTap` (show-rx writer) |
| sources.py | `Sources` | `sources` | 1 | `main()`: the test-audio child |
| listener.py | `Listener` | `listener` | 1 | none |
| director.py | `Director` | `director` | 10 | `Director.palette_fn` (artnet_out) |
| ring.py | none | none | none | `Rings` (show-rx writer) |

Brain add order (ticks within one beat run in this order): `room`, `situations`, `autopilot`, `sources`, `listener`,
`director`.

### S3.3 Threads

```
[show-rx]      unchanged, plus two guarded taps:
               on_midi  -> NoteTap.on_midi (before the Piano)          guard "notetap"
               on_audio -> Features, Clock, then Rings.write            guard "ring.write" (a no-op while the
                                                                        listener hasn't armed the ring)
[show-bus]     plus api.py's routes and the /v1/events hub. Handlers never touch brain state: they
               `await brain.call(fn, ...)` and read snapshots.
[show-brain]   NEW (brain.py): one thread with one asyncio loop.
               Ticks:  director 10 Hz; room/situations/autopilot/sources/listener 1 Hz; reloads every RELOAD_S;
                       state.json at most 1/s.
               Tasks:  RoomLink (WS /api/v1/events, GETs, PUTs); listener calls (AsyncOpenAI); Describe it;
                       art-still fetches; child-process waits.
[show-artnet]  Director.palette_fn(t) at <= 44 Hz: a dict swapped by reference; only a crossfade's lerp runs in the call
children       test piano (tools/show_sender/show_sender.py --test-piano) and test audio
               (python -m show_service.sources audio): separate processes, started and killed only by sources.py
```
- **Ownership.** Everything a component owns lives on show-brain. Other threads read `snapshot()` (a dict built on
  show-brain, swapped by reference, never mutated afterwards), or call the few any-thread methods named below:
  - `palette_fn`;
  - the readers of `NoteTap` and `Rings`;
  - `Brain.call`, `dirty` and `snapshot`.
- **Nothing on show-brain blocks.**
  - HTTP and WS go through aiohttp on `ctx.loop`, and subprocesses through asyncio.
  - File writes are small: state.json and the jsonl appends.
  - OpenAI goes through `AsyncOpenAI`.
- **Budgets** (p95, niced, under the 4K render), reported by `brain.snapshot()`. Each tick is timed twice:
  `cpu_p95_ms` (`time.thread_time()`: show-brain's own work, what the tick budgets apply to) and `wall_p95_ms` (=
  `p95_ms`, `perf_counter`: includes waiting for the GIL behind show-rx, the lag signal). A wall figure over budget
  with a small CPU figure is contention, not the component (review, 16 Oct: the director's own tick is 0.25 ms p50,
  0.5 ms p95 unloaded, while wall read 1.7-2.0 ms).

  | What | p95 |
  |---|---|
  | director tick | ≤ 1 ms |
  | each 1 Hz tick | ≤ 3 ms |
  | brain loop lag | ≤ 20 ms |
  | `NoteTap.on_midi` (show-rx) | ≤ 20 µs a batch |
  | `Rings.write` (show-rx) | ≤ 60 µs a 128-frame block |

  The two show-rx figures are measured in their own tests (`-s` prints them).
- **The two-process split.** §2's later split turns BrainContext's engine-side slots into IPC, so keep them as
  "latest value" reads.

### S3.4 brain.py, `__main__`, the bus seam (B3)

```python
class Brain:
    def __init__(self, ctx: contract.BrainContext, *, show_file: Path = contract.SHOW_FILE,
                 state_file: Path = contract.STATE_FILE, wall: Callable[[], float] = time.time) -> None
    def add(self, comp: contract.Component) -> None    # main thread, before start(); a duplicate name raises ValueError;
                                                       # applies comp.provides() to ctx (on_mode_change appended)
    components: dict[str, contract.Component]          # add order; read-only after start()
    def start(self) -> None                            # thread "show-brain"; returns once the loop runs
    def stop(self, timeout: float = 3.0) -> None       # comp.stop() in reverse order, a final state.json, loop stopped
    async def call(self, fn, *args, timeout: float = contract.BRAIN_CALL_TIMEOUT_S) -> Any
        # from another loop (show-bus): fn(*args) on show-brain, awaited there if it returns an awaitable;
        # raises asyncio.TimeoutError, or fn's own exception (api: ValueError/LookupError -> 4xx)
    def dirty(self) -> None                            # any thread (= ctx.persist): state.json within PERSIST_MIN_S
    def snapshot(self) -> dict                         # any thread:
        # {"components": {name: comp.snapshot()}, "ticks": {name: {"hz", "p95_ms" (= wall_p95_ms), "wall_p95_ms",
        #  "cpu_p95_ms", "late", "errors"}},
        #  "loop_lag_ms_p95", "settings": {"path", "revision", "error"},
        #  "state_file": {"path", "saved_age_s", "error"}, "modules": {name: "ok" | "missing: …" | "failed: …"}}
```
**Start** (on show-brain), in this order:
1. Set `ctx.loop` and `ctx.persist = self.dirty`.
2. Read state.json.
   - Missing: `{}`.
   - Unreadable or invalid: rename it to `state.json.bad-<epoch>`, start cold, and show the error.
3. `comp.restore(saved.get(name) or {})` for each component.
4. Parse show.yaml into `ctx.settings`, then `comp.configure(settings)` for each.
   - A bad file at start gives `{}` and an error, and the components use neutral values.
5. `comp.start()` for each.
6. Start the tick schedules: one fixed-rate loop per rate, components in add order.
   - Every call is guarded: an exception is counted and logged at most every 30 s, like `__main__._Guard`.
7. Re-read show.yaml every `RELOAD_S` (mtime or size changed).
   - Valid: swap `ctx.settings`, then `configure()` every component.
   - Invalid: keep the last good settings and show the error.

**state.json** (`contract.STATE_FILE`):
- **Written** atomically: `state.json.tmp` in the same directory (created with mode 0700), write, flush, fsync,
  `os.replace`.
- **Shape:** `{"v": 1, "saved_at": <epoch s>, "version": <show_service version>, "<component name>": comp.persist(),
  ...}`.
- **What persists** (§11.2: vibe, steps, palette pick, chips with absolute expiry, listening; plus Auto's runtime):

  | Key | Shape |
  |---|---|
  | `director` | `{"target": VibeTarget \| null, "target_source": "mode" \| "api", "target_set_at": epoch \| null, "mode": str \| null, "nudges": {"steps": int, "palette": "auto" \| "art" \| <name>, "moods": [str] \| null, "keep_until": epoch \| null, "house": bool, "low": bool}}`. Chips are rebuilt from the nudges. At restore, a passed `keep_until` is dropped, and the first RoomView whose mode differs from `mode` clears every nudge. |
  | `listener` | `{"on": bool, "set_at": epoch}`. With nothing saved: `{"on": false}`. |
  | `autopilot` | `{"entered": {"mode", "previous", "rule", "at", "transition_id", "by": "auto" \| "person"} \| null, "hold": {"mode", "by": "person" \| "undo" \| "correction", "since", "until"} \| null, "pause": {"why": "flap", "until"} \| null, "backoff_until": epoch \| null, "suppressed": [{"mode", "rule", "since", "until"}], "switches": [epoch, ...], "last": {"at", "from", "to", "reason", "rule", "transition_id", "status"} \| null}` |
  | `sources` | Nothing: tests stop on restart. |

- **Starts cold, deliberately:** the clock, sections, LLM beliefs, situation timers and the RoomView.

**`__main__` (Show.start), in order:**
1. `bus` (with `routes=api.routes(brain_ref, engine_ref)`).
2. clock, dsp, midi, harmony, receiver.
3. `rings = ring.Rings()` and `tap = situations.NoteTap()`.
4. `ctx = BrainContext(...)` with the engine-side slots filled, then `brain = Brain(ctx)`.
5. `mod.attach(ctx)` for `autopilot`, `sources`, `listener`, `director`, each passed to `brain.add`.
6. artnet, with `palette_fn = <the Director>.palette_fn` (None without one).
7. gc freeze, `rx.start()`, `artnet.start()`, `brain.start()`.

**Stop:** `brain.stop()` (this stops the test children and writes state.json), then artnet, rx, bus.
- Every Stage-3 module is imported lazily through `_import`, so a missing one shows in `modules` and is left out.
- `--no-brain` (or `SHOW_BRAIN=off`) builds no brain, no taps and no Stage-3 routes. The process is then exactly as
  before Stage 3, which makes it the rollback switch.

**Engine-side slots** (B3 fills them; S3.4's names are `contract.BrainContext`'s):
- `features=features.latest`, `clock=clock.snapshot`, `harmony=lambda: harmony.latest`, `health=self.health`.
- `rings`, `notes=tap`.
- `piano=self._piano_view`: the PianoView built in `_snapshot` on show-rx every 0.5 s:
  - the voice piano's `present`, `stream` and `source`;
  - `keys_down = len(held())`;
  - `damper = pedals["damper"]`;
  - `last_note_age_s` from the table's newest T_ON;
  - `velocity`: EMA (τ 2 s) of VEL_ON/127 for strikes since the last snapshot;
  - `notes_per_s`: strikes with T_ON in the last 4 s, divided by 4.
- `clock_action`: maps `tap` → `clock.tap(t)`, `half` → `half()`, `double` → `double()`, `one` → `one(t)`.
- `section_change=vm.section_change`, `set_look_out=vm.set_look`,
  `set_gate_ref=lambda l: setattr(features, "gate_ref_lufs", float(l))`.
- `set_senders=rx.set_senders`, `set_priority=rx.set_priority`, `static_senders=cfg.senders`.
- `Show.state()` gains `"brain": brain.snapshot()` and `"api": api.stats()` (the card socket's health).

**bus.py seam:**
- `Bus(host, port, *, state_fn=None, health_fn=None, routes: Callable[[web.Application], None] | None = None)`.
- `_startup` registers the existing routes, then calls `routes(app)` under a guard: if it raises, that is logged and
  the stubs stay.
- Then it adds `STUBS` only for (method, path) pairs not already registered. aiohttp raises on a duplicate route.
- `LATEST_ON_CONNECT` gains `contract.LATEST_ON_CONNECT_BRAIN` (`room.direction`, `room.look`).
- Tests go in `test_bus.py`: a route replaces its stub, a failing `routes` keeps the stubs, and latest-on-connect
  includes room.look.

**As built (integration, 16 Oct): `brain.py` and the `__main__` wiring.** Built to the spec above (details in
"config.py and `__main__.py`"); where it was open:
- **Brain** uses state_store.StateStore for state.json (`ctx.persist = Brain.dirty`; a housekeeping task every 0.25 s
  writes it when due, at most once per `PERSIST_MIN_S`, and once more at stop). Extra constructor keyword: `mono`,
  `reload_s` (tests).
- **`running`** (bool) is what api.py reads: False until every component has started, False again at stop. `call()`
  refuses with `RuntimeError("brain_not_running")` before that, runs `fn` directly when already on show-brain, and on
  a timeout cancels the call on show-brain before raising `asyncio.TimeoutError`.
- **Ticks:** one asyncio task per rate (10 Hz, 1 Hz), components in add order, each call timed and guarded; a loop
  that falls a whole period behind skips ahead (no burst) and counts `late`. Every tick round ends with a yield, so a
  slow tick never starves the API's calls.
- **show.yaml:** compared by (mtime, size) every `RELOAD_S`; `settings.revision` is the first 12 hex of its SHA-1. A
  missing or bad file at start gives `{}` (every component's neutral values) and the error; later, the last good
  settings stay and the error says "keeping the last good".
- **`snapshot()`** adds `running`, per-tick `ticks` and `last_error`, and `errors` (restore/configure/start/persist/
  stop/settings guard counts).
- **Start-up failure** of a component (restore, configure or start raising) is counted and logged; it still ticks.
- **Paths** come from the options (`--show-file`, `--vibes-file`, `--state-dir`), so the tests never touch production
  files; `Options()` built in code has the brain off.
- **The readback's listening** (S3.7's open point): api.py's `GET`/`PUT /api/v1/vibe` overlay the listener's own gate
  (`effective`, `why`, from its last 1 Hz snapshot) when the director's part allows listening.
- **contract.TEST_STREAMS** gains `test-audio`, so it ranks with the test streams (after every room stream, before
  unknown names) and sources.py lifts it with `set_priority` while it plays. That list is about ranking and the
  receiver's `test` mark only: no privacy decision reads a stream's name (S3.12).

### S3.5 vibes.yaml (schema; c8's file, read by vibes.py)

`version: 1`, `order: [names]` (vibes missing from it are appended alphabetically), `vibes: {name: {field: value}}`.
- **Names** match `^[a-z0-9][a-z0-9_-]{0,31}$`.
- **Flat:** nothing inherits.
- **Load rules:**
  - A YAML error keeps the last good file, with `error` set.
  - A bad value drops that field to neutral and adds a line to `problems` (`vibes.party.level: [0.9, 0.2] is not a
    range`).
  - An unknown field is ignored, with a problem line.
  - An unknown palette name or vocab word is dropped from its list, with a problem line.

| Field | Type | Neutral (unset) | Meaning |
|---|---|---|---|
| `label` | str | the name | Feel card word |
| `description` | str | `""` | |
| `energy` | [lo, hi] in 0..1 | [0, 1] | direction.energy bounds (§8.2 clamp_to_vibe) |
| `energy_centre` | 0..1 | middle of `energy` | where the vibe rests with the music neutral |
| `loudness_ref_lufs` | -60..-5 or null | null (Features keeps its -30) | §6.3 gate reference → `ctx.set_gate_ref` |
| `follow` | list from `FOLLOW_INPUTS` | `[energy]` | what steers numbers inside a look |
| `follow_amount` | 0..1 | 0.5 | gain in `f = clamp(centre + follow·conf·liveness·(music − 0.5) + steps·step_size)` |
| `beat_lock` | bool | true (the clock's gate still decides) | false: no pulses, no beat accents, no beat swell |
| `change_looks` | `phrase` \| `between_pieces` \| `time` | `phrase` (falls back to time while the clock is unsure) | §8.1, §8.5 |
| `change_rate` | 0..1 | 0.5 | min_bars 32/16/8 at 0/0.5/1; direction.change_rate |
| `dwell_s` | [min, max] s | [45, 180] | changing by time |
| `relax_on_talk` | bool | true | |
| `listener` | bool | true | the vibe lets the listener run (S3.12) |
| `palettes` | ranked list of catalog names and/or `art` | `[]`: the neutral palette (key white in every role) | |
| `key_cct` | 1500..6500 K | 2700 | key lamps' white |
| `lamp_chroma_max` | 0..0.37 | 0.05 | key lamps' tint cap (OKLCh C) |
| `chroma_max` | 0..0.37 | 0.37 | every other lamp's cap |
| `saturation` | [lo, hi] | [0.5, 1] | moved by arousal and energy |
| `contrast` | [lo, hi] | [0.3, 0.7] | |
| `level` | [lo, hi] | [0.5, 1] | **lamps/DMX only** |
| `floor` | 0..1 | 0.1 | **lamps/DMX only** |
| `motion` | [lo, hi] | [0.1, 0.5] | |
| `accents` | `ACCENT_STYLES` | `none` | today only the depth of the lamps' beat swell (show.yaml `director.accent_depth`); the form says so ("Lamps' beat swell": None, Gentle, Medium, Strong, Full) |
| `strobe` | bool | false | L5 cap (default off). **Not built yet:** only published; the form keeps it behind More, "(not built yet)" |
| `peak_tokens_per_hour` | int ≥ 0 | 0 | **Not built yet:** nothing spends them; behind More, "(not built yet)"; vibes.yaml leaves it unset |
| `moods` | vocab moods | `[]` | lowest-priority words |
| `genre` | vocab genre, `auto` or null | null | `auto` = null ("the music decides"), a real choice in the form |
| `transition_style` | vocab, `auto` or null | null (the music's band decides) | as genre |
| `palette_family` | vocab, `auto` or null | null | as genre ("Any") |
| `step_size` | 0..0.5 | 0.1 | one Calmer/Wilder step |
| `max_steps` | 0..6 | 3 | |
| `persona` | str | `""` | **Describe it only** (§7.1: the listener never sees direction) |

**The mode's show target** is `contract.VibeTarget`: `{"vibe": str, "listening": bool, "overrides"?: {field: value}}`.
- Canonical form: `listening` is always present (false when left out), and `overrides` appears only when non-empty.
  `label`, `description` and `persona` can't be overridden.
- **Degrade, never refuse** (review, 16 Oct). A target is kept **as asked**, so the room adapter's comparison holds:
  - a vibe vibes.yaml doesn't have plays the neutral look (`known: false`), so the previous mode's feel never
    lingers;
  - an override vibes.py can't use (an unknown field, a bad value, a word vocab.yaml lacks, `label`) is dropped at
    run time, as at load;
  - the read-back's `problems` lists both in page words (`"the feel 'x' isn't in vibes.yaml: the neutral look
    plays"`, `"overrides.level: [1, 0] is not a range [low, high]: the vibe's own value plays"`).
- **No feel** is `{"vibe": null}` (or `DELETE /api/v1/vibe`): the director goes neutral. The room adapter reads it as
  the value `{"vibe": null, "listening": false}`, so the room's generic put-back can restore "no feel" when a mode
  with a show row is left for one without.
- **Null in overrides** means unset (the vibe's own value), in show_service and in the adapter alike, as the room's
  form means it. A field's neutral is offered as a real choice instead: `auto` for a word field, and the gate's own
  -30 LUFS (preset "The usual") for `loudness_ref_lufs`. Lists keep the first of each item, in both.
- In modes.json (2f's file) it is the row
  `"show": {"set": <VibeTarget>, "restore": true, "policy": "ignore"}`.
  `ignore` means the value is applied on arrival and retried until it lands; after that it belongs to the user, so
  nudges never show as drift.
- Proposed rows, for the owner and 2f to decide; nothing in Stage 3 writes them:

  | Mode | Row |
  |---|---|
  | gallery | `{"vibe": "home", "listening": true}` |
  | lights-only | `{"vibe": "dinner", "listening": false}` |
  | ambient | `{"vibe": "chill", "listening": true}` |
  | piano | `{"vibe": "notes", "listening": false}` |
  | party | `{"vibe": "party", "listening": true}` |
  | disco | `{"vibe": "disco", "listening": true}` |
  | live-test, off | no row |

  Nothing is uploaded until the owner turns AI listening on (S3.12): test streams included, with no exception.

### S3.6 show.yaml (schema; c8's file, read by brain.py and handed to every component)

The file itself documents every key. Its sections:

| Section | Read by | Keys |
|---|---|---|
| `senders` | sources (→ `Receiver.set_senders`); listener (`room_sound`) | `<name>: {hosts: [str, ...], port, streams: [...], on_demand?: bool, room_sound?: bool}`. `room_sound: false`: its audio can't carry anyone in the room (a music file), so the listener skips only the music/speech check for its streams. Unset or bad = room sound. `kiosk` ships commented out (the go-live step, S3.14) |
| `tests` | sources | `piano: {sender, pieces}`; `audio: {sender, music, rank_first}` |
| `room` | RoomLink | `url` (default `contract.ROOM_URL`) |
| `situations` | Situations; `__main__` (`midi.channels`: the MIDI state channels) | `midi` (renamed from `piano`, 16 Oct: no code reads a key called piano), `music`, `sonos`, `quiet`: each situation's default params (S3.8) |
| `lamps` | director | `placement_roles: {placement: role}`, `default_role`, `roles: {role: {palette_role, follow_screen?, white_k?, chroma_max?, avoid_hues?, motion}}`. A value that is a string names a vibe field. |
| `director` | director | `steps.size_from`, `steps.max_from`, `keep_s`, `low {level, fade_s}`, `house {white_k, level}`, `next_max_wait_s`, `art {poll_s, colours}`, `republish_s`, `between_pieces {pause_s, damper_up}`, `new_song_after_s`, `novelty`, `variation_bars`, `entry_fade_s`, `emphasis_edges [lo, hi]`, `accent_depth {style: 0..1}`, `bands {edges, hysteresis, split {band, by, up, down}, words {band: {moods, fx_moods, transition_style}}}`, `transitions {style word: {style: cut \| crossfade, beats, seconds}}`, `listener_bias {trajectory word: energy nudge}` |
| `listener` | listener | `cadence_s`, `clip_s`, `min_gap_s`, `relaxed_cadence_s`, `agree_reads`, `quiet_gate`, `min_clip_s`, `music_gate` (`classifier` \| `dsp`), `describe_timeout_s` |

**Starting points.** Every number in show.yaml is a design default to be tuned by ear, unless its comment says
"measured" or "owner"; each says where it comes from. The behaviour thresholds that used to live only in code are
keys now (review, 16 Oct): the director's pause, new-song silence, novelty, variation bars, entry fade and emphasis
edges; the listener's quiet gate and first-clip length; `situations.midi.idle_note_s`. The code keeps the same numbers
as its fallbacks, marked "starting point" where they are defined.

These live elsewhere, never here:
- Auto's globals and per-mode rules: modes.json.
- Auto on/off: room target `autopilot.main`.
- Put-back.
- Model ids: llm.yaml.
- Latency.

### S3.7 director.py, palette.py, vibes.py, and the artnet seam (B1)

**vibes.py**
```python
FIELD_SCHEMAS: dict[str, dict]       # VIBE_FIELDS -> JSON schema (room's catalog subset: type, minimum, maximum, enum,
                                     # items, default; x-ui {label, widget, unit, labels}); served in VibesCatalog.fields
NEUTRAL: contract.VibeSettings       # S3.5's neutral column, every field
NAME_RE = r"^[a-z0-9][a-z0-9_-]{0,31}$"
class Vibes:
    def __init__(self, path: Path = contract.VIBES_FILE, *, vocab_path: Path = contract.VOCAB_FILE,
                 palette_names: Callable[[], Collection[str]] = lambda: ()) -> None
    def poll(self, force: bool = False) -> bool        # show-brain, every RELOAD_S; True when it reloaded
    revision: str; error: str | None; problems: list[str]; order: list[str]
    def names(self) -> list[str]
    def get(self, name: str) -> contract.VibeSettings | None            # NEUTRAL + the vibe's valid fields
    def effective(self, target: contract.VibeTarget | None) -> tuple[contract.VibeSettings, bool]   # (settings, known)
    def check_target(self, body: Any) -> contract.VibeTarget | None
        # canonical, AS ASKED; accepts {"vibe", "listening"?, "overrides"?}, §11.2's {"name"}, a bare name;
        # None / {"vibe": null} -> None (no feel); null overrides left out (unset);
        # ValueError("bad_target: <why>") only for a body that isn't a target (not a name, a bad shape)
    def target_problems(self, target) -> list[str]   # page words: an unknown vibe, each dropped or trimmed override
    def catalog(self, palettes: list[contract.PaletteChoice]) -> contract.VibesCatalog
```

**palette.py** (pure functions, plus a catalog). OKLab/OKLCh is implemented here (Ottosson's matrices, about 30 lines),
with no colour library.
```python
Rgb = tuple[float, float, float]                       # sRGB 0..1
def to_oklab(rgb: Rgb) -> tuple[float, float, float];  def from_oklab(lab) -> Rgb          # from_oklab clips
def to_oklch(rgb: Rgb) -> tuple[float, float, float]   # (L, C, h degrees)
def from_oklch(L: float, C: float, h: float) -> Rgb    # in gamut by reducing C (bisection), never shifting h
def hex_of(rgb: Rgb) -> str;  def rgb_of(hex_: str) -> Rgb
def mix(a: Rgb, b: Rgb, t: float) -> Rgb               # in OKLab
def white(kelvin: float) -> Rgb                        # CCT -> sRGB, max channel 1
def limit_chroma(rgb: Rgb, c_max: float) -> Rgb
def avoid_hues(rgb: Rgb, ranges: list[list[float]]) -> Rgb   # a hue inside a range moves to its nearer edge
def scale_saturation(rgb: Rgb, s: float) -> Rgb        # OKLCh C x s
def role(rgb: Rgb) -> contract.ColourRole
def roles_from(colours: list[Rgb]) -> contract.PaletteRoles
    # base = the median-L colour; accent = the highest-C colour at least 60° from base (else the highest C);
    # highlight = the lightest
class Catalog:                                         # stream_service/palettes.py, loaded by FILE PATH
    def __init__(self, path: Path = contract.PALETTES_FILE) -> None   # (importlib spec), never `import stream_service`
    def poll(self) -> bool;  error: str | None
    def names(self) -> list[str];  def choice(self, name: str) -> contract.PaletteChoice
    def colours(self, name: str, n: int = 5) -> list[Rgb]   # the cosine palette at t = 0, 1/n, ...
    def roles(self, name: str) -> contract.PaletteRoles
def from_image(data: bytes, k: int = 5) -> list[Rgb] | None
    # Pillow decode, 64 px wide, k-means in OKLab (8 iterations, fixed seed), largest cluster first; None on any failure
```

**director.py**
```python
def attach(ctx: contract.BrainContext) -> Director
class Director:                                        # Component "director", tick_hz BRAIN_TICK_HZ
    def palette_fn(self, t_s: float) -> dict[str, tuple[float, float, float]] | None   # show-artnet (any thread)
    def provides(self) -> dict    # {"vibe": <effective VibeSettings now | None>, "target": <VibeTarget | None>,
                                  #  "engaged": <a nudge is active>, "on_mode_change": self.on_mode_change}
    # show-brain, called by api.py through brain.call:
    def set_target(self, body: Any, source: str = "api") -> contract.VibeReadback       # kept as asked (degrade);
                                                       # None = no feel; ValueError (bad_target) -> 400
    def readback(self) -> contract.VibeReadback
    def catalog(self) -> contract.VibesCatalog
    def step(self, direction: str) -> dict             # {"steps": StepsView, "chips": [ChipData]}; at a limit: at_limit
    def set_palette(self, name: str) -> dict           # {"palette": {"pick", "current"}, "chips"}; ValueError("unknown_palette")
    def action(self, do: str) -> dict                  # next|keep|house|low -> {"done", "chips", "look": {"id", "next_pending"}}
    def clear(self, nudge_id: str) -> list[str]        # the ids cleared; "all" = Back to auto; unknown id -> []
    def apply_describe(self, intent: contract.DescribeIntent) -> list[contract.ChipData]
    def release_hue(self) -> dict                      # {"released": True, "via": "room"} until P5 (room streams Hue)
    def on_mode_change(self, old: str | None, new: str) -> None    # clears every nudge (§8.4)
    def log(self, n: int = 50) -> list[dict]
    def snapshot(self) -> dict
        # {"target", "readback": VibeReadback, "direction": DirectionData | None, "look": LookData | None,
        #  "steps": StepsView, "palette": {"pick", "current", "choices": [PaletteChoice]}, "chips": [ChipData],
        #  "house", "low", "next_pending", "keep_in_s", "engaged", "vibes": {"revision", "error", "problems"},
        #  "art": {"age_s", "screens": {screen: [hex]}}, "stats": {"tick_ms_p95", "looks", "events", "published"}}
```
**The tick (10 Hz).** This follows §8.2-§8.6. A builder reads those sections for the musical rules.
1. **Reload** vibes, the palette catalog and patch.yaml (lamps: `profile: hue` devices with `screen` and `placement`)
   every `RELOAD_S`.
2. **Inputs**, each of which may be None: `ctx.features()`, `ctx.clock()`, `ctx.harmony()`, `ctx.piano()`,
   `ctx.room()`, `ctx.beliefs()`, `ctx.situations()`.
3. **The vibe:** `vibes.effective(target)`. With no target, or an unknown vibe, the director is **neutral and
   hands-off**:
   - `palette_fn` returns None, so artnet_out keeps today's fixed colour;
   - no `set_look_out` and no `set_gate_ref`;
   - room.direction and room.look are still published, with `vibe: null`.
4. **L1 music.** The `follow` inputs:
   - `energy`: features.energy;
   - `onsets`: onset and band-hit density;
   - `dynamics`: PianoView.velocity while the piano plays, else energy;
   - `register`: harmony.register;
   - `harmony`: tension and chord_change.

   They feed music_direction.py-style numbers (energy, pace, change_rate, fx.density, fx.rate). Liveness: hold 10 s
   after features go stale, then fade over 20 s (§8.5 "No audio").
5. **Numbers:** §8.2's formula, clamped to the vibe. `intensity` stays 1.0.
6. **Words**, highest priority first:
   1. a human's moods (nudge or Describe);
   2. the LLM's beliefs (two agreeing reads at ≥ 0.6);
   3. show.yaml `director.bands`;
   4. the vibe's `moods`.

   `transition_style` and `genre` follow the same order.
7. **The look.**
   - Palette: the pick (human), else the vibe's ranked list. `art` uses the latest stills, fetched through
     `ctx.room_get("/api/v1/screens/<screen>/snapshots/anchor.png")` every `art.poll_s` while art is in play, then
     `from_image`.
   - The roles, and the emphasis by arousal.
   - Saturation, contrast, level and motion inside the vibe's ranges.
   - The accent style, with density from energy.
   - L5 caps: no strobe unless `strobe`.
8. **When looks change** (§8.5).
   - `phrase`: on phrase lines from the clock body (`bar_anchor_beat`, `beats_per_bar`, `phrase_bars`; gate `beat` or
     `dmx` and `bar_conf` ≥ 0.5), at least `min_bars` apart.
   - `time`: inside `dwell_s`, at energy-novelty points.
   - `between_pieces`: no strike for ≥ 2 s with the damper < 0.1 (PianoView), once per pause.
   - Next look lands on the next bar (≤ `next_max_wait_s`).
   - Keep this look stops changes (variations go on).
   - Each change is announced as `room.event {kind: "look_change", at_ms}`, as early as the clock allows (≥ lead +
     250 ms when it can be). At `at_ms` comes the new room.look, `ctx.section_change()` (GOBO +1), and the
     crossfade.
9. **Lamps.** For each lamp: placement → role (show.yaml `lamps`) → the role's fields.
   - `extend`: the palette role of the screen it hangs above (that screen's art roles under `art`), limited to
     `chroma_max`.
   - `key`: `white(key_cct)` mixed toward base, limited to `lamp_chroma_max`, kept out of `avoid_hues`.
   - Then × level, with floor applied.
   - **House:** the room's stored put-back value for that lamp (`RoomView.stored_restore["hue.<x>"]`:
     `{on, bri, xy|mirek}` → rgb), else `director.house`.
   - **Low:** `low.level` over `low.fade_s`.
   - **Screens:** the palette's colour, **unscaled**.
10. **Outputs.**
    - room.direction and room.look on change (≥ 0.02 in a number, or any word or role change) or every
      `republish_s`.
    - `ctx.set_look_out(hue_depth=…, beat_lock=…)`:
      - `beat_lock` = the vibe's `beat_lock` and the clock gate is `beat`;
      - `hue_depth` = `director.accent_depth[accents]` while beat_lock holds and one phrase has passed since the vibe
        was entered (§8.6), else 0.
    - `ctx.set_gate_ref(loudness_ref_lufs)` when it changes.
    - Every look change and nudge, with the music context, is logged to `contract.DIRECTOR_LOG`.
11. **Silence and talk** (§8.5).
    - `quiet` or `talking` true: relax to the base look (the energy centre, the first palette), unless `relax_on_talk`
      is false.
    - No audio: hold 10 s, then drift to the base over 20 s; then relaxed (whatever `relax_on_talk` says) until sound
      comes back, so an empty room stops changing looks (review, 16 Oct).
    - Never black.
12. **Around screen cuts** (§8.6): while any `RoomView.screens[*].cutting`, no look_change events and no GOBO steps.

**palette_fn(t_s)** returns the current dict, keyed by patch.yaml device id (`hue.ne`, `screen.north`, ...). During a
crossfade it computes `mix(from, to, (t_s − t0) / dur)` per device (≤ 6 devices, ≤ 30 µs). It returns None while
neutral.

**artnet seam:** `VirtualMaestro.set_look(*, hue_depth: float | None = None, beat_lock: bool | None = None) -> None`.
- It can be called from any thread; each value is swapped by reference and read once per frame.
- `beat_lock=False` means:
  - no DIMMER pulse edges;
  - no PRISM flips;
  - the hue swell follows energy only.

  The clock stays wired.
- `hue_depth` (0..1) replaces the constructor's.
- `status()` gains `"look": {"hue_depth", "beat_lock"}`.

**Tests (B1):**
- OKLab round trip; gamut mapping keeps hue; `avoid_hues`; `roles_from` on catalog palettes; `from_image` on a
  synthetic two-colour PNG.
- vibes: load, bad file (keeps the last good), bad field (falls to neutral), `check_target` cases, hot reload.
- director with a fake ctx at 10 Hz:
  - steps clamp in range;
  - a mode change clears nudges;
  - **House, Low and level change lamp colours and never `screens`** (the screens rule);
  - neutral → `palette_fn` None;
  - looks change only on phrase lines (fake clock body);
  - `beat_lock` false → `set_look_out(beat_lock=False)`.
- artnet `set_look` (real tests: no pulses with beat_lock false; depth applied).

**As built (B1, 16 Oct).** Built to the spec above; where it was open, this is what the code does.
- **Names.** `Director.palettes` is the `palette.Catalog`, because `Director.catalog()` is the GET /vibes method.
  `Director.chips()` exists (api.py's `_chips_after` uses it). `step()` also answers `"done": bool`.
- **Nothing knows a name.** Lamps are the patch.yaml devices whose profile carries r/g/b and whose kind isn't
  `screen`; the screens are `kind: screen`. A device's kind is its `kind:` or its id's prefix, which is
  room_service/dmx/patch.py's own rule. Lamp behaviour comes only from the role's fields.
  `tests/test_director.py` checks this with an AST scan of director, palette and vibes, and (since the review, 16 Oct)
  of api, autopilot, situations, listener, sources and brain too. Any string constant that equals a vibe,
  mode, device, screen, role or band name from the live config, or `piano`, fails the test. The fixed vocabularies
  (contract tuples, vocab.yaml, contract keys) are allowed.
- **The palette catalog.**
  - The API: `ctx.room_get("/renderer/api/catalog")`, through the room's one front door, at start and every 300 s.
    Only the `palettes` object is decoded (`palette.block_from_catalog`, about 4 ms). The whole 2.5 MB document
    would hold the GIL for about 45 ms, and the audio thread shares it.
  - Until the API answers, and whenever it can't be reached: palettes.py, loaded by file path.
  - A usable API block wins from then on.
- **The art.** For each screen, `GET /api/v1/screens/<screen>/art` is tried first: the live art feed, the TV's art
  now, mask included. `.../snapshots/anchor.png`, the still from the last cut, is the fallback.
  - The image is decoded in an executor thread (Pillow; a JPEG decodes in draft mode).
  - Near-black pixels are dropped unless they are at least 90 % of the picture.
  - Each screen keeps its own roles. Lamps with `follow_screen` take their screen's; screens take their own.
  - Until a still arrives, `art` uses the vibe's next catalog palette, and the lamps glide over to the art when it
    lands.
- **Roles.** A "far from base" accent must carry chroma ≥ max(0.03, half the most colourful sample's). Without this,
  Ember's only far colour was a near-black whose hue is noise, and it made the lamps grey-green.
- **Lamps**, at full value (largest channel 1), from the role's fields, in this order:
  1. the palette role, moved on by the look's variation when the role has `motion`;
  2. a slow drift toward the next role (a 120..15 s cycle by motion speed, depth up to half the motion amount);
  3. × saturation;
  4. `white_k`: white first, tinted toward the colour by the saturation;
  5. the `chroma_max` cap;
  6. `avoid_hues`.

  Then the level: × max(floor, level), so a lamp's brightness is its largest channel, as room's Hue output reads it.
  `hue_depth` is capped at 1 − floor/level, so the swell's trough never goes under the floor. Colours are cached on
  quantised inputs (0.005 steps).
- **Screens** get the emphasised role (arousal 0.4 / 0.75, ± 0.05) at full value, with its chromaticity kept. Level,
  floor, Lights low and House lights never touch them; a test proves it.
- **House lights** use `stored_restore[<device id>]`: the room's target for a lamp is `<kind>.<member>`, which is the
  patch id. The value is `{on, bri, xy|mirek}`, or `{"value": ...}`. `dmx` or `taken_over` gives house white; any
  other word means off.
  - House is a toggle (0.3 s glide), and Lights low fades over `low.fade_s`. Turning one on turns the other off.
  - Both set `hue_depth` 0.
- **Entering a vibe.**
  - From neutral, the lamps fade from that house colour over 8 s.
  - From another vibe, they cross over from what shows now.
- **Words.** The first non-empty source wins: a person's moods; then the listener's at ≥ 0.6 (fresh or holding);
  then the band (only while liveness ≥ 0.5); then the vibe's. `transition_style` follows the same order, band
  before vibe.
- **Three more show.yaml keys** (in config/room/show.yaml since the integration, 16 Oct, with the code's own values,
  marked starting points; the code keeps the same fallbacks, marked "starting point" in director.py):
  - `director.bands.split.band`: which band splits (default 1, as in music_direction.py);
  - `director.transitions` `{style: {style: cut|crossfade, beats, seconds}}`: gentle 4 beats, lively 1 beat,
    wild a cut;
  - `director.listener_bias` `{trajectory word: energy nudge}`.
- **When looks change.**
  - A phrase line is decided when it is at most min(4 s, one bar) away and at least 0.35 s ahead.
  - The director's own changes keep the palette and give the moving roles a new allocation (`variation`).
  - The palette moves through the vibe's list only on a song change (sound back after ≥ 8 s of silence), on Next look
    (with pick `auto`), or on a pick.
  - `between_pieces`: once per pause, once the look is `dwell_s[0]` old. With no notes ever seen, it changes by time.
  - Relax (`quiet` or `talking`, with `relax_on_talk`):
    - the follow term fades out (τ 3 s);
    - there is one change to the vibe's first palette;
    - nothing else changes until the relax ends.
  - A director's change that would land during a screen cut is dropped. A person's change waits for the cut to end.
- **No audio** (review, 16 Oct): once liveness has run down (features stale for the 10 s hold and the 20 s fade) and
  nobody is playing (no note in 4 s), the director relaxes as for a quiet room: one change to the base palette, then
  no look changes, no new allocations and no GOBO steps until sound comes back (`music.no_audio`). Before this,
  `change_looks: time` (and phrase or between_pieces falling back to time) changed the look at every `dwell_s[1]` in
  an empty room. Art-Net keeps sending the relaxed look (artnet_out "Idle").
- **Targets degrade** (review, 16 Oct; S3.5): `set_target` keeps any target as asked; an unknown vibe is neutral at
  once (the previous mode's feel never lingers), and `readback()["problems"]` (cached with `effective`) says what.
  `set_target(None)` (or `{"vibe": null}`) is no feel: `source` "none". api.py adds `boot_id`.
- **Thresholds from show.yaml** (S3.6): `between_pieces {pause_s, damper_up}`, `new_song_after_s`, `novelty`,
  `variation_bars`, `entry_fade_s`, `emphasis_edges`, with the module constants as fallbacks.
- **Neutral again.** If we had moved them, `set_look_out(reset=True)` and `set_gate_ref(-30)` are each sent once.
  - The seam is `VirtualMaestro.set_look(*, hue_depth=None, beat_lock=None, reset=False)`. None leaves a value;
    `reset` restores the constructor's depth and beat_lock True.
  - `status()["look"]` shows both.
  - With beat_lock off, the clock stays wired, so frames stay on the beat grid.
- **Readback.** `listening_effective`/`listening_why` hold only the director's part: the target's `listening` and the
  vibe's `listener`. api.py overlays the listener's own `effective` and `why` when those allow it (S3.4 "As built").
- **Not built** (no detectors yet): section memory, builds and drops, peak tokens. `strobe` is only published. The
  Fine-tune form says so: `strobe` and `peak_tokens_per_hour` carry `x-ui.not_built` and "(not built yet)" and sit
  behind More (the room adapter honours `not_built`); `accents` is labelled as what it does today, the lamps' beat
  swell depth.
- **Measured** (4 lamps, 2 screens, not under the render; `pytest -s -k "budget or cheap"` prints them):
  - the tick: p50 0.38-0.45 ms, p95 0.61-0.77 ms;
  - `palette_fn` mid-crossfade: 10-17 µs;
  - `palette_fn` otherwise: 0.2 µs.
- **Tests:** test_palette 20, test_vibes 26, test_director 42 (the name scan runs over nine modules now), and 4 in
  test_artnet_out (42 there in all). The review's (16 Oct): a target kept as asked (an unknown vibe neutral, bad
  overrides dropped and listed, no feel), `auto` as a word's neutral, no audio stops the look changes, the thresholds
  from show.yaml, and Art-Net holding the director's look in a quiet room. A beat_lock
  that comes back on mid-beat never starts a pulse partway down (only an edge seen while locked pulses).
- **requirements.txt:** `Pillow==12.3.0`, as in sm-room.

### S3.8 situations.py and autopilot.py (B2)

**NoteTap** (situations.py; made by `__main__`, written on show-rx):
```python
class NoteTap:
    def __init__(self, size: int = 4096) -> None
    def on_midi(self, stream: str, msg: Any, arrival_s: float, to_host: Callable[[int], float]) -> None
        # = contract.OnMidi. Batches only (states never count). Per stream, vban.midi_seq_verdict:
        # "old" dropped; "new" and "restart" taken (prev not replayed). Each 0x9n with d2 > 0 ->
        # contract.NoteOn(t_s=arrival_s, stream, channel=n+1, pitch=d1, vel=d2).
    def since(self, t_s: float) -> list[contract.NoteOn]
        # any thread: a fixed ring plus a write counter, copied and re-checked (never iterate a deque show-rx appends to)
    def stats(self) -> dict                            # {"batches", "notes", "old", "restarts", "streams": {name: n}}
```

**The situation vocabulary.**
- Every word takes `for_s`.
- Params come from show.yaml's section, then the rule's own params.
- A reading is a `contract.SituationReading`; `null` (unknown) is never acted on.
- A stream counts as "up" when its health state is `streaming` or `quiet`.

| id (label) | source, rank | params | true | false | unknown |
|---|---|---|---|---|---|
| `piano_playing` ("the piano is playing") | midi, 0 | `situations.midi`: `streams`, `channels`, `include_test` (false), `window_s`, `min_notes`, `min_span_s`, `min_pitches`, `heard_by`, `heard_min_lufs` | In the last `window_s`, NoteTap shows ≥ `min_notes` note-ons on `streams` × `channels`, spanning ≥ `min_span_s`, on ≥ `min_pitches` distinct pitches. While `heard_by` is up and is the features' stream, the window's max `lufs_s` must also be ≥ `heard_min_lufs`. | otherwise, with a listed stream up | no listed stream up |
| `piano_idle` ("the piano has stopped") | midi, 0 | `streams`, `channels`, `include_test`, `idle_note_s` (1) | No note-on on them in the last `idle_note_s`, and no key or damper down (PianoView, when the voice is a listed stream). `for_s` is the idle length. | a note-on in the last `idle_note_s` | no listed stream up |
| `music_audible` ("music is playing") | audio, 2 | `situations.music`: `stream`, `min_prob`, `dsp_min_gate`, `include_test`; plus `situations.sonos` | With a classifier (P1): music ≥ `min_prob` on `stream`. Without one: `stream` is the features' stream, gate ≥ `dsp_min_gate`, and the clock gate is `beat` or `dmx` (or harmony conf ≥ 0.3). Or: `sonos_playing` and the mic agrees (gate ≥ `dsp_min_gate`). | stream up and neither | stream down and Sonos unknown |
| `sonos_playing` ("Sonos is playing") | sonos, 1 | `situations.sonos`: `now_playing`, `volume`, `own_change_s` | RoomView member `now_playing.state` is PLAYING or TRANSITIONING, not muted, and the `volume` member > 0 | otherwise | room unreachable, a member unknown, or `sent_age_s` < `own_change_s` on either |
| `genre_in` ("the music is jazz or soul") | listener, 3 | `genres` (vocab genres, required), `min_conf` (0.6), `include_test` | `beliefs.genre.word` ∈ genres with conf ≥ min_conf | a confident other genre | beliefs `none` or `fading` |
| `energy_above` ("the music is lively") | audio, 2 | `value` (0..1, required), `stream`, `include_test` | features.energy EMA (τ 10 s) > value | ≤ value | features stale (> 5 s) |
| `talking` ("people are talking") | audio, 2 | `stream`, `min_prob` (0.5), `include_test` | classifier: speech ≥ min_prob and music < 0.5 | classifier: otherwise | no classifier (**always unknown in Stage 3**) |
| `quiet` ("the room is quiet") | audio, 2 | `situations.quiet`: `stream`, `gate_below`, `include_test` | stream up and gate < `gate_below` (or no fresh features while it is up) | gate ≥ gate_below | stream down |
| `between` ("it's 18:00-23:00") | time, 4 | `from`, `to` ("HH:MM", local; wraps midnight) | local time in [from, to) | otherwise | never |

**Test sound never switches the room** (review, 16 Oct). While the sound being measured (the features' stream,
else the receiver's active audio) is a test stream (the receiver's `test` mark), the audio words (`music_audible`,
`energy_above`, `talking`, `quiet` with no `stream` set) and the listener word (`genre_in`) read unknown, unless the
rule sets `include_test: true`, as the piano words already did. A test played "from another room" (rank_first) can't
change this room's mode.

**Conditions (the grammar of `enter_when`, `otherwise_suggest_when`, `leave_when` and `hold.until`):**
```
cond   := {word: params, ...}                 several words in one object: all must hold
        | {"all": [cond, ...], "for_s"?: s} | {"any": [cond, ...], "for_s"?: s} | {"not": cond, "for_s"?: s}
params := {} | true | {"for_s"?: s, <param>: value, ...}       overrides show.yaml's params for this rule only
```
- **Three-valued logic (Kleene):**
  - `all` is false if any child is false, else unknown if any is unknown, else true.
  - `any` is true if any child is true, else unknown if any is unknown, else false.
  - `not` swaps true and false and keeps unknown.
- **Holding.** A node holds when it has been **true** continuously for its `for_s` (a leaf's sits in its params).
  False or unknown resets the timer.
- **Timers** are keyed by rule path (`"piano/enter_when/all/0/piano_playing"`). A rule whose text changes in a new
  config revision is reset.
- **Bad rules.** An unknown word or param means the rule never holds. The problem is logged and shown in
  `AutopilotState.candidates[].blocked`.
```python
class Situations:                                     # Component "situations", tick_hz 1
    def vocabulary(self) -> list[contract.SituationDef]
    def read(self, sid: str, params: dict | None, now_s: float) -> contract.SituationReading
    def snapshot(self) -> dict                        # {"readings": {id: SituationReading (show.yaml params)},
                                                      #  "vocabulary": [SituationDef]}
    def provides(self) -> dict                        # {"situations": lambda: self.snapshot()["readings"]}
class Conditions:
    def __init__(self, situations: Situations) -> None
    def check(self, cond: Any) -> list[str]           # problems; never raises
    def holds(self, cond: Any, path: str, now_s: float) -> tuple[bool, dict]
        # (held long enough, {"value": True|False|None, "held_s", "needs_s", "words": {word: SituationReading}})
    def reset(self, prefix: str = "") -> None
```

**Per-mode Auto config in modes.json** (exactly aa's keys, `room_service/roomstate/schema.py` `MODE_AUTOPILOT_KEYS`).
aa checks the mode names in `from` and `leave_to` and that the `skip` selectors are real; it does not check
conditions.
```json
"<mode>": {"autopilot": {
  "enter_when": <cond>,                        // Auto switches to this mode when it holds
  "otherwise_suggest_when": <cond>,            // else offer a one-tap "Switch to <label>?" while this holds
  "from": "<mode>" | ["<mode>", ...],          // only from these (unset: any)
  "leave_when": <cond>,                        // leave it (only a mode Auto entered) when this holds
  "leave_to": "previous" | "<mode>",           // unset: "previous"
  "skip": ["<selector>", {"<selector>": {"from": <pattern> | [<patterns>]}}]   // passed to PUT /mode as is
}}
```
**Globals:** the top-level `autopilot` in modes.json (aa's `AUTOPILOT_KEYS`; only `on` is type-checked there).
- A key that is **absent** takes its design default below, a starting point.
- A **bad** value takes the default too, and is reported (`settings[key].source = "default (bad value)"`).

| Key | Shape | Design default when absent |
|---|---|---|
| `on` | bool | false. At run time the room target `autopilot.main` wins (aa's adapter seeds itself from this). |
| `quiet_hours` | `{"from": "HH:MM", "to": "HH:MM"}` or `null` (none) | `{"from": "23:00", "to": "08:00"}` (§8.9) |
| `min_dwell_s` | s | 300 |
| `max_switches` | `{"count", "per_s", "pause_s"}` | `{"count": 4, "per_s": 1800, "pause_s": 1800}` |
| `backoff_s` | s | 1800 |
| `hold` | `{"until": <cond>, "max_s", "correction_s", "suppress_s"}` | `{"until": {"all": [{"piano_idle": {}}, {"not": {"music_audible": {}}}], "for_s": 600}, "max_s": 7200, "correction_s": 60, "suppress_s": 14400}` |
| `chips` | `{"switch_s", "suggest_s"}` | `{"switch_s": 45, "suggest_s": 120}` |

**RoomLink (autopilot.py).** It is the only room client in show_service.
```python
class RoomLink:                                       # Component "room", tick_hz 0 (asyncio tasks)
    def view(self) -> contract.RoomView                # any thread
    async def get(self, path: str, *, timeout: float = 3.0) -> tuple[int, bytes]      # (0, b"") when unreachable
    async def put(self, path: str, body: dict, *, timeout: float = contract.ROOM_PUT_TIMEOUT_S) -> tuple[int, dict]
                                                       # (0, {"error": "room_unreachable"}) when unreachable
    def provides(self) -> dict    # {"room": self.view, "room_get": self.get, "room_put": self.put}
```
| Call | When | Answer used |
|---|---|---|
| WS `ws://<room.url host>/api/v1/events` | always (reconnect after 1, 2, then every 5 s) | `{"type": "status", "data": <status>}` (on connect and on change, ≤ 4/s) → RoomView; `{"type": "mode.transition", "data": {id, phase, from, to, source, reason, skip}}` → person detection |
| `GET /api/v1/status` | every 5 s while the WS is down | the same status |
| `GET /api/v1/room/config` | at start, and whenever `status.config.revision` changes | `{config, revision, error}`: modes' `autopilot` blocks, `label`s, the top-level `autopilot`, file order |
| `PUT /api/v1/mode` | Auto's switch, or Undo | 202 `{transition_id, reapply}`; 409 `stale`; 404 `unknown_mode`; 400 `bad_skip` / `bad_request` |
| `PUT /api/v1/room/targets` | `set_on` (Auto on/off) | `{"values": {"autopilot.main": "on" \| "off"}, "source": "show"}` → 202 `{accepted}` |
| `GET /api/v1/screens/{id}/art`, then `GET /api/v1/screens/{id}/snapshots/anchor.png` | the director's art (through `room_get`), every `director.art.poll_s` while a vibe uses `art` | an image, or 404 |
| `GET /renderer/api/catalog` | the director's palette catalog (through `room_get`): at start and every 300 s | the renderer's catalog (only its `palettes` block is decoded); on failure, stream_service/palettes.py by file path |

- `on_mode_change(old, new)` callbacks run on show-brain whenever `mode.current` changes.
- `RoomView.screens[id].cutting` = the room's `observed.phase` is not `idle`, `live` or `failed`.
- `RoomView.autopilot_on` = `subsystems.autopilot.believed == "on"`.
- `stored_restore` = `stored["_restore"]`, as `{target: value}`.

**The supervisor**
```python
class Autopilot:                                      # Component "autopilot", tick_hz 1
    async def set_on(self, on: bool) -> contract.AutopilotState   # RuntimeError("room_unreachable"); ValueError(room's error)
    async def undo(self) -> dict          # {"undone": True, "mode", "transition_id"}; LookupError("nothing_to_undo")
    def resume(self) -> contract.AutopilotState
    def state(self) -> contract.AutopilotState        # = snapshot()
    def log(self, n: int = 50) -> list[dict]
```
**States** (`contract.AUTOPILOT_STATES`; the /room Auto line is in brackets):

| State | Meaning | Leaves for |
|---|---|---|
| `off` ("Auto is off") | `autopilot.main` isn't "on" | watching when it turns on (suppressions are kept) |
| `watching` ("Auto · watching") | it evaluates rules | acting, holding, paused, backoff, blind |
| `acting` ("Auto → Piano…") | its PUT got 202 and the transition runs | watching (done); backoff (partial, failed or rolled back); holding (cancelled by someone else's request) |
| `holding` ("Auto paused · you chose Disco · Resume") | a person picked a mode | watching when `hold.until` holds, at `hold.max_s`, or on Resume |
| `paused`, why `quiet_hours` ("Auto paused · quiet hours") | inside quiet hours: it may **leave** a mode it entered, never enter one | watching at the end of quiet hours |
| `paused`, why `flap` ("Auto paused · too many changes · Resume") | `max_switches.count` switches within `per_s` | watching after `pause_s`, or on Resume |
| `backoff` ("Auto waiting · the last switch didn't finish · Resume") | after a partial, failed or rolled-back switch | watching after `backoff_s`, or on Resume |
| `blind` ("Auto · not listening" / "Auto · modes.json has an error") | room unreachable, or `status.config.error` | the previous state when it comes back |

**Each tick (1 Hz):**
1. Room unreachable or a config error: `blind`. `autopilot_on` not true: `off`.
2. Expire holds, pauses, backoff and suppressions, and compute the quiet-hours flag.
3. If a transition is running (`transition` or `target` not null), only wait. In `acting`, the outcome comes from the
   transition with Auto's own id.
4. Evaluate candidates for every mode that has an `autopilot` block (no code knows any mode):
   - **enter:** mode ≠ current; `from` allows the current mode; not suppressed; `enter_when` holds.
   - **suggest:** `otherwise_suggest_when` holds and enter doesn't.
   - **leave:** `current == entered.mode`, `leave_when` holds, and `ctx.engaged()` is false (nudges stop Auto
     leaving).
5. **In `watching`:**
   1. a leave first;
   2. else the best enter candidate: lowest situation rank in its rule (§8.9: MIDI > Sonos > mic > LLM), then file
      order. It also needs: the current mode's `since_age_s` ≥ `min_dwell_s`, under the flap limit, and outside quiet
      hours;
   3. else a suggestion, announced once per episode per mode.

   In `paused` (quiet hours): leave only. In every other state: nothing.
6. **The request:**
   ```json
   PUT /api/v1/mode {"mode": M, "source": "autopilot", "reason": "<the rule's situation labels> (<rule path>)",
                     "expect": {"current": <current>, "last_transition_id": <RoomView.last_transition_id>},
                     "skip": <M's own autopilot.skip, or []; for a leave, the union with the left mode's skip>}
   ```
   - It never names the current mode.
   - 202 → `acting`.
   - 409 `stale` → stay `watching` (timers kept; no backoff).
   - 404 or 400 → log, and block that rule until the config revision changes.
   - Unreachable → `blind`.
7. **When Auto's transition ends:**
   - `done` → `watching`. An enter sets `entered = {mode, previous, rule, at, transition_id, by: "auto"}`; a leave
     clears it. Announce `switch` (a chip with Undo for `chips.switch_s`) and record the switch time for the flap
     guard.
   - `partial`, `failed` or rolled back → `backoff`, and announce `failed`.
   - `cancelled` → `holding` (`by: "person"`).

**People win** (a `mode.transition` `started` whose source isn't `autopilot`, `auto-undo`, `boot`, `resume` or
`rollback`):
- **Straight back to the mode Auto just left, within `hold.correction_s`:** always a correction (checked first, so a
  rule that holds for that mode can't make it "agreeing"; review, 16 Oct).
- **It picks the mode Auto would enter now** (an enter candidate that holds): not an override. Auto records `entered`
  with `by: "person"`, so it can still go back when the activity ends (§8.9 flow, step 3).
- **Within `hold.correction_s` of Auto's last switch:** a **correction**. That (rule, mode) pair is suppressed until
  its `enter_when` has been false for `hold.until`'s `for_s` (the episode ends), capped at `suppress_s`. Then
  `holding`.
- **Otherwise:** `holding` (`{mode: to, by: "person"}`).

**Undo** (`POST /api/v1/autopilot/undo`). It needs the last switch's target to be current, and no transition running.
- It sends `PUT /api/v1/mode {mode: last.from, source: "auto-undo", reason: "Undo Auto", expect}`. There is no
  `skip`: a person's undo applies the mode fully.
- Then a correction, as above, and `holding` (`by: "undo"`).

**Resume** clears any hold, pause or backoff. The state goes to `watching`, with timers restarted.

**Announcements:** every switch, suggestion, hold, undo, pause, backoff and failure goes to
`ctx.notify("auto", AutoAnnouncement)`, to `AutopilotState.chip`/`suggest`, and to `contract.AUTOPILOT_LOG` (one
JSON line: `{at, kind, mode, rule, reason, evidence: {word: SituationReading}, room: {current, revision}}`).

**Exposed:** `contract.AutopilotState`.

**Tests (B2)**, against a fake room (an aiohttp test server on an ephemeral port: status, room/config, mode,
room/targets, and a WS that pushes status and `mode.transition`):
- NoteTap: dedupe (new, old, restart), the channel filter, test streams excluded.
- `piano_playing` thresholds, and the heard-by check.
- The grammar: all/any/not, `for_s`, unknown.
- The supervisor:
  - enter after `for_s`, sending `expect` and `skip`;
  - 409 → no backoff;
  - a person → holding; undo → suppressed;
  - the flap guard; quiet hours (leave only);
  - `leave_to` previous;
  - it never re-requests the current mode;
  - Auto off → no PUT.

**Built (B2, 16 Oct):** `situations.py`, `autopilot.py`, `tests/test_situations.py` (25) and `tests/test_autopilot.py`
(23, against a fake room on an ephemeral port). The review's additions (16 Oct): test sound ignored by the audio and
listener words unless `include_test`; `merge_skips`; straight back within `correction_s` is a correction even when a
rule agrees; a room whose PUT answer comes after Auto's timeout (the fake room's `answer_delay`): the switch is adopted
from its events (announced, entered, counted, undoable) and so is a slow Undo.
- **Measured:** `NoteTap.on_midi` takes 2.9 µs per 4-note batch. With a full 4096-note ring and 8 modes carrying
  distinct rules, the p95 tick is 0.6 ms for situations and 1.6 ms for autopilot.
- **Additions to the spec'd signatures:**
  - `attach(ctx, **kw)`: `wall`, `mono`, `local` and `log_path` are for tests; the brain passes none of them.
  - NoteTap also has `raw_since(t)` (tuples) and `last_note_times()` (per stream and channel).
  - RoomLink also has `add_listener(fn)` (transition notices, once per id and phase), `status_count()` and
    `rules_error()`.
  - Situations has a `classifier` attribute, a hook for P1's CED: `(stream) -> {"music", "speech"}`. With None,
    `talking` is unknown and `music_audible` uses the DSP guess.
- **Situation params** resolve in this order: `situations.DESIGN_DEFAULTS` (marked "design default (starting point)"
  in the vocabulary's x-ui `source`), then show.yaml's section, then the rule. Null means unset: any stream, every
  channel, or no check. No device or stream name is ever a default.
- **Self-cause:** while the room changed `situations.sonos`' targets within `own_change_s`, `sonos_playing`,
  `music_audible` and `quiet` read unknown.
- **Decisions where the table was open:**
  - `music_audible`:
    - mic down and Sonos stopped → false;
    - mic down and Sonos playing → unknown (the mic can't confirm it);
    - the stream is up but another stream is being measured → unknown, or false if Sonos says stopped.
  - `quiet` with another stream measured: the health state `quiet` → true, else unknown.
  - `between` with from == to → never true.
  - `genre_in` below `min_conf` → unknown.
- **Conditions:**
  - A node that is true but not yet held for its `for_s` counts as false for its parent.
  - More than 3 s without evaluation resets a timer.
  - A word object may carry its own `for_s`.
  - Params may be `{}`, `true` or `null`.
  - Readings are memoised per `now_s`, so rules that share a leaf share its reading.
- **The supervisor:**
  - **State precedence:** blind > off > acting > holding > paused (flap) > backoff > paused (quiet hours) > watching.
  - **`on`:** the room's `autopilot.main`. While the room has no such target, modes.json's `autopilot.on` (default
    false). Turning Auto on clears a hold, pause or backoff, but keeps suppressions.
  - **Agreeing:** a person who picks a mode whose `enter_when` or `otherwise_suggest_when` holds (taking the tile
    counts), or who re-picks the mode Auto entered, is agreeing. That is not a hold, and `entered.by` becomes
    "person". Except: a pick of `last.from` within `correction_s` is a correction first (review, 16 Oct).
  - **A leave's skip** is the union (`merge_skips`) of the left mode's `autopilot.skip` (the mode whose rule entered
    it) and the destination's: one entry per selector, a plain selector wins over `{selector: {"from": ...}}`, two
    conditional ones join their patterns. So leaving Party never lowers the blinds or spins the ball (§8.9: automation
    never moves them), even when the mode it goes back to has no skip of its own (review, 16 Oct).
  - **Slow room answers** (review, 16 Oct). The room answers PUT /api/v1/mode only after its request lock, and a
    `skip` with `{"from": ...}` first reads the devices (Sonos 4 s, blinds and Hue 5 s). So:
    - every room PUT waits `contract.ROOM_PUT_TIMEOUT_S` (10 s);
    - a PUT with no answer (or one cancelled mid-way) leaves a **maybe**: `{source, to, rule, reason, sent_at}`. The
      room's next `started` with Auto's source (or Undo's, for an Undo) and the same `to`, within `MAYBE_S` (30 s),
      is adopted as Auto's own (also when it came before the PUT gave up): `acting`, `last`, then on `done` the
      announcement, `entered`, the flap count and Undo, exactly as after a 202 (the log's `request` says
      `adopted`). An adopted Undo records the correction and the hold. An Undo whose PUT got no answer still answers
      502 `room_unreachable` (the room may or may not have done it; if it did, the card shows "Undone" from the
      announcement);
    - api.py's `brain.call` for `set_on` and `undo` waits `contract.AUTO_CALL_TIMEOUT_S` (12 s), longer than that
      PUT: a slow room no longer gives a 503 `busy` with the PUT cut off mid-way and Undo's correction lost.
  - **A cancelled Auto switch:** the person's own `started` decides between agreeing, a correction (within
    `correction_s` of Auto's request) and a hold.
  - **Corrections and undo:** a correction or Undo after a leave suppresses that `leave_when`. Undo of a leave puts
    `entered` back.
  - **A partial switch:** it still sets `entered` and counts as a switch, then backs off.
  - **The flap guard:** checked at the next attempt. Leaves count too.
  - **Announcements:**
    - the quiet-hours pause, once per night;
    - a hold's end, as `resume` ("Auto · watching again");
    - a 5xx or no answer from PUT /mode: `blind` for 5 s, then it tries again;
    - an Auto switch with no word from the room for 60 s: given up (`last.status` "unknown").
  - **Event order:** room_service sends `mode.transition` at once but coalesces `status` (up to 250 ms later), so
    after any event Auto waits for the next status, at most 10 s, before it decides. It drops `entered` only on a
    status received after it set it. A test covers this with a lagging status.
  - **Extra keys:** `AutopilotState.entered.by`, `candidates[].holds` and `room.rules_error` (the GET
    /api/v1/room/config problem: "Auto has no rules").

### S3.9 api.py: every route (B3)

- **Mounting.** `api.routes(brain_ref, engine_ref) -> Callable[[web.Application], None]` is handed to `Bus(routes=…)`.
  `engine_ref()` returns `{"active_audio", "features": {"energy", "age_ms", "stream"}, "clock": ClockData | None}`
  for the card.
- **Bodies.** Every body is JSON. Errors are `{"error": <code>, "detail": str, ...}`, like room's.
- **Before the brain runs,** every route below answers 503 `{"error": "starting"}`.
- **Busy.** A `brain.call` timeout gives 503 `{"error": "busy"}`.
- **Paths.** No `/show` prefix: room's pass-through strips it. Relative URLs only. No auth.

| Method and path | Body | 200 answer | Errors |
|---|---|---|---|
| `GET /api/v1/state` (bus.py, existing) | none | the engine state, plus `"brain": brain.snapshot()` | 503 starting |
| `GET /api/v1/card` | none | `CardData` | none |
| `WS /v1/events` | see below | | 503 `too_many_clients` (more than 64) |
| `POST /api/v1/step` | `{"dir": "calmer" \| "wilder"}` | `{"steps": StepsView, "chips": [ChipData]}` (at a limit: `at_limit` true) | 400 `bad_request` |
| `PUT /api/v1/palette` | `{"name": "auto" \| "art" \| <catalog name>}` | `{"palette": {"pick", "current"}, "chips"}` | 400 `unknown_palette` `{choices}` |
| `POST /api/v1/action` | `{"do": next \| keep \| house \| low \| tap \| half \| double \| one}` | director words: `{"done", "chips", "look": {"id", "next_pending"}}`; clock words (stamped on arrival): `{"done", "clock": ClockData \| null}` | 400 `unknown_action` |
| `DELETE /api/v1/nudge/{id}` (`all` = Back to auto) | none | `{"cleared": [ids], "chips"}` (an unknown id clears nothing) | none |
| `GET /api/v1/vibes` | none | `VibesCatalog` | none |
| `GET /api/v1/vibe` | none | `VibeReadback` (with `problems` and `boot_id`) | none |
| `PUT /api/v1/vibe` (the show adapter; modes) | `VibeTarget` (also `{"name"}`); `{"vibe": null}` = no feel | `VibeReadback`, the target as asked: an unknown vibe plays neutral (`known: false`), a bad override is dropped; `problems` says what | 400 `bad_target` `{choices}` only for a body that isn't a target |
| `DELETE /api/v1/vibe` | none | `VibeReadback` with `vibe: null` (no feel: the director neutral) | none |
| `POST /api/v1/describe` | `{"text": str}` (up to 300 chars; longer is cut) | `DescribeResult`, also sent as EV_DESCRIBE; within `describe_timeout_s` + 1 s. Empty text: 200 with a hint in `reply` | 400 `bad_request` only when the body isn't JSON or `text` isn't a string |
| `GET /api/v1/listening` | none | `ListenerView` | none |
| `PUT /api/v1/listening` | `{"on": bool}` | `ListenerView` | 400 `bad_request` |
| `POST /api/v1/hue/release` | none | `{"released": true, "via": "room", "detail": "room_service streams Hue until P5"}` | none |
| `GET /api/v1/autopilot` | none | `AutopilotState` | none |
| `PUT /api/v1/autopilot` | `{"on": bool}` | `AutopilotState` once the room accepts (the room target `autopilot.main` is the truth) | 502 `room_unreachable`; the room's own 4xx passed through as `{error, detail}`. Its `brain.call` waits `AUTO_CALL_TIMEOUT_S` (12 s), as Undo's does |
| `POST /api/v1/autopilot/undo` | none | `{"undone": true, "mode", "transition_id"}`; nothing to undo: 200 `{"undone": false, "why": "nothing_to_undo", "detail"}` | 502 `room_unreachable` (no answer from the room: S3.8 "maybe") |
| `POST /api/v1/autopilot/resume` | none | `AutopilotState` | none |
| `GET /api/v1/situations` | none | `{"vocabulary": [SituationDef], "readings": {id: SituationReading}, "combinators": ["all", "any", "not"]}` | none |
| `GET /api/v1/test` | none | `{"sources": {"piano": SourceStatus, "audio": SourceStatus}, "pieces": {"piano": [PieceInfo], "audio": [PieceInfo]}}` | none |
| `POST /api/v1/test` (98's API) | `{"source": "piano" \| "audio", "piece"?: id}` (no piece: the first); a running source switches piece | `SourceStatus` (`state` `starting`) | 400 `unknown_source` \| `unknown_piece` `{choices}` |
| `DELETE /api/v1/test`, `DELETE /api/v1/test/{source}` | optional `{"source"}` (none: all) | `{"stopped": [sources]}` | none |
| `GET /api/v1/look` (console) | none | `{"direction": DirectionData \| null, "look": LookData \| null}` | none |
| `GET /api/v1/log?kind=director\|autopilot\|listener&n=50` (console) | none | `{"entries": [...]}`, newest last | none |

**Describe it, end to end:**
1. `intent, via = await brain.call(listener.describe, text, context)`. The context is built from the director snapshot
   and the RoomView: `{"vibe", "persona", "palettes", "moods", "modes": [{"name", "label"}], "current_mode"}`.
2. `chips = await brain.call(director.apply_describe, intent)`.
3. The answer is `DescribeResult(ok=True, via, text, reply, applied=chips, suggest={"mode", "label"} if intent.switch_mode else None)`.
   It never switches a mode itself (§7.4).

**WS /v1/events (the card's socket):**
- No handshake. The first message is `{"type": "welcome", "v": 1, "seq", "t_ms", "card": CardData}`.
- Then:
  - `{"type": "card", "seq", "patch"}`: an RFC 7386 merge patch against the last card sent, at most `CARD_HZ`, and
    at once on `notify("card")` (chip changes);
  - `{"type": "auto", "seq", "event": AutoAnnouncement}`;
  - `{"type": "describe", "seq", "result": DescribeResult}`.
- `seq` goes up by 1 per message, per client. Lists (chips) are replaced whole; null deletes a key.
- From the client: `{"type": "ping"}` → `{"type": "pong", "seq"}`, and `{"type": "resync"}` → a fresh welcome.
  Anything else is ignored.
- Each client has a queue of 256. When it is full, the queued patches are dropped and a welcome is sent next.
- The hub lives on show-bus. `notify(kind, payload)` is thread-safe (`call_soon_threadsafe`).

**Card rules** (a pure `api.card(brain_snapshot, engine) -> CardData`):
- `hearing.text`, first match wins:
  1. confident beliefs (`fresh`/`holding`, genre conf ≥ 0.6) → `"<genre> · <first mood>"`;
  2. `talking` → "Talking";
  3. `quiet` → "Quiet";
  4. no active audio, or features older than 5 s → "No sound coming in";
  5. `music_audible` → "Music";
  6. else "Listening".
- `hearing.level` is energy (0 when stale). `hearing.loose` is `direction.loose`, shown as "following loosely".
- `palette.choices` = Auto, From the art, then the vibe's first 5.
- The rest maps straight from the director, listener, autopilot and sources snapshots.
- **"More: lamp brightness" (§10.1 item 6) is Lights low** (c8's decision, 16 Oct): `POST /api/v1/action {"do":
  "low"}` toggles it, `CardData.low` shows it, and its chip "Lights low" clears it. It dims the lamps to
  `director.low.level` over `low.fade_s`, never the screens, and reaches the lamps in a quiet room too. No separate
  brightness route or slider: a level nudge can follow if the owner wants finer steps (2f: draw the More unroll's
  "Lamp brightness" as the Lights low toggle).

**Tests (B3):**
- Every route, against fake components.
- `/v1/events`: welcome, patch, ping, resync, overflow.
- `brain.call` across loops; ticks guarded.
- state.json: atomic, torn file → `.bad`, and a restore round trip.
- show.yaml: hot reload, and a bad edit keeps the last good.
- The bus seam.
- `--no-brain`.
- `__main__` with fake Stage-3 modules (the existing `test_main.py` pattern), on ephemeral ports.

**As built (16 Oct): `api.py`, `state_store.py` and the bus seam.** Tested in `tests/test_api.py` (every route
against fake components, `/v1/events`, the card, merge patches, the bus seam) and `tests/test_state_store.py`. Both
were also smoke-run against the real Director and Listener.
- **Wiring (for `__main__`):**
  - `self.api = api.routes(lambda: self.brain, self._engine)` and `Bus(..., routes=self.api)`; `Show.state()["api"]` is
    `self.api.stats()`.
  - `ctx.notify = self.api.notify` (any thread).
  - Clock words use `routes(..., clock_action=)` when it is given, else `brain.ctx.clock_action`.
  - api.py needs only `brain.components`, `brain.call(fn, *args, timeout=)`, `brain.snapshot()` and `brain.ctx`.
  - The brain counts as running when it has an explicit `running`/`started`/`ready` bool; without one, when
    `ctx.loop` is set.
- **state_store.py (for brain.py):**
  - At start: `store = StateStore(contract.STATE_FILE)`, then `saved = store.load()`. A torn file is renamed
    `.bad-<epoch>`, and keys of the wrong type are dropped and listed in `problems`.
  - `ctx.persist = store.dirty`.
  - Each tick: `store.flush(lambda: {n: c.persist() for n, c in comps.items()})`. It writes at most once per
    `PERSIST_MIN_S`.
  - At stop: `flush(..., force=True)`.
  - `store.status()` is the snapshot's `state_file`.
  - It also provides `append_jsonl`/`tail_jsonl` (1 MB rotation).
- **Where it differs from the table (degrade, never refuse).** A normal tap never gets a 4xx; 400 only for a body the
  route can't take, with `choices`.
  - Undo with nothing to undo: 200 `{"undone": false, "why": "nothing_to_undo", "detail"}` (the table above says the
    same).
  - Describe:
    - empty text: a 200 `DescribeResult` with a hint;
    - over 300 chars: cut, not refused;
    - a slow listener: a "took too long" reply.
  - A missing component: 200 with a neutral body and plain words (`detail`, `why` or `line`).
    `PUT /api/v1/vibe` answers 503 `unavailable` instead, so the adapter retries.
  - `PUT /api/v1/vibe` (review, 16 Oct): an unknown vibe or a bad override is 200, kept as asked and played as well as
    it can be; the read-back's `known` and `problems` say what. `{"vibe": null}` and `DELETE /api/v1/vibe` are no
    feel. Every read-back carries `boot_id` (this process; the room adapter's cold-start check, S3.11).
  - `POST /api/v1/hue/release` answers `released` even before the brain runs.
  - `PUT /api/v1/autopilot` when the room refuses: 502 `{error: <room's code>, detail, room_status}`.
  - `DELETE /api/v1/test/<unknown>`: 200 `{"stopped": []}`.
- **Other details:**
  - `PUT /api/v1/vibe` passes `source="mode"`; `?source=api` marks other callers.
  - Describe's context adds `palette_labels`.
  - An unknown palette's `choices` are auto, art and the whole catalog.
  - bus.py: `TYPES` lists the brain's four types, and `stats()` has `routes_error`.

### S3.10 sources.py, the receiver seam, fetch_test_music.py (B4)

```python
def attach(ctx: contract.BrainContext) -> Sources
class Sources:                                         # Component "sources", tick_hz 1
    def pieces(self) -> dict[str, list[contract.PieceInfo]]          # {"piano": [...], "audio": [...]}
    def start_test(self, source: str, piece: str | None = None) -> contract.SourceStatus
                                                       # ValueError("unknown_source" | "unknown_piece")
    def stop_test(self, source: str | None = None) -> list[str]
    def status(self) -> dict[str, contract.SourceStatus]
    def snapshot(self) -> dict    # {"senders": [{"name", "hosts", "port", "streams", "on_demand", "subscribed"}],
                                  #  "tests": {source: SourceStatus}, "priority": [...], "errors": [...]}
def senders_from_settings(settings: dict, static: tuple[contract.Sender, ...],
                          running: Collection[str]) -> tuple[contract.Sender, ...]      # pure
def main(argv: list[str] | None = None) -> int         # "audio ...": the test-audio child (test_audio.main)
```
**Senders, hot-reloaded.**
- On `configure()`, and on every test start or stop, the effective list is built, and `ctx.set_senders` is called
  when it changed. The effective list is:
  - the static senders whose names aren't in show.yaml;
  - plus show.yaml's senders that are not `on_demand`;
  - plus the `on_demand` senders whose test is running.
- Each becomes `Sender(name, host=hosts[0], port, streams, hosts=tuple(hosts), on_demand)`.
- A bad entry is skipped and listed in `errors`.
- While test audio plays with `tests.audio.rank_first`, `ctx.set_priority(("test-audio",))`; otherwise `()`.

**Children** (each started with `start_new_session=True`, `cwd=REPO_ROOT`, `PYTHONUNBUFFERED=1`):

| Source | argv | Log |
|---|---|---|
| piano | `[sys.executable, contract.TEST_PIANO_SCRIPT, "--test-piano", piece, "--port", <its sender's port>, "--bind", "127.0.0.1"]` | `STATE_DIR/test-piano.log`, truncated at each start |
| audio | `[sys.executable, "-m", "show_service.test_audio", "--file", <path>, "--port", <port>, "--bind", "127.0.0.1", "--stream", <its sender's first stream>]` (`-m show_service.sources audio …` is the same program) | `STATE_DIR/test-audio.log` |

**Child states:**
- `starting` until the stream shows `streaming` in `ctx.health()`, then `playing`.
- `stopping`: SIGTERM sent; SIGKILL to the child's whole process group after 2 s (`os.killpg`: it leads one, so test
  audio's ffmpeg goes too).
- `failed`: it exited by itself (`error` = the last 5 log lines).
- `off`.

Only the `Popen` objects sources.py made are ever signalled (never `pkill`). `stop()` ends them all.

**Pieces:**
- **piano:** every `.mid` or `.midi` in `tests.piano.pieces` → `{"id": "file:<name>", "label": <stem, "_" → space,
  capitalised>, "kind": "file"}`, then the built-in phrases (`PHRASE_LABELS`, loaded from
  stream_service/piano_replay.py by file path; on failure, none). These ids match `piano_replay.library()`.
- **audio:** `mp3`, `ogg`, `opus`, `flac`, `wav` and `m4a` files in `tests.audio.music` → `{"id": "file:<name>",
  "label", "kind": "file", "genre", "artist", "license", "duration_s"}` (from `index.json`, else the file stem).
- An id not in the list → `ValueError("unknown_piece")`.

**The test-audio child** (`show_service/test_audio.py`: `python -m show_service.test_audio --file F --port P
[--bind 127.0.0.1] [--stream test-audio] [--channels 1|2]`). At module level it imports only the stdlib, numpy and
vban.
- It binds UDP on (bind, port) and streams only to the source address of the latest SUBSCRIBE that names its stream,
  with a TTL of 5 s (show_sender's rule).
- **Decode:** `ffmpeg -nostdin -v error -i F -map 0:a:0 -f f32le -ac C -ar 48000 -` through a pipe, looping. A new
  ffmpeg runs at EOF; counters, `n` and `sess` carry on.
- **Send:** `vban.pack_audio(stream, counter, pcm, 48000, INT16)`, 128 frames a packet, paced on `time.monotonic`: 4
  packets every 10.67 ms, never more than 4 packets ahead of real time. With no subscriber, decoding pauses where it
  is.
- **Heartbeat** every 0.5 s while subscribed: `vban.make_heartbeat(sess, frame, n, t_cap_ns=<the monotonic_ns when
  sample n was due>, in_lat_ms=0, dev="file:<name>", state="ok", xruns=0, peak/rms, clips)`.
- **SIGTERM:** kill ffmpeg, exit 0.

**The receiver seam (receiver.py):**
```python
Receiver.set_senders(senders: tuple[contract.Sender, ...]) -> None
    # any thread; applied at the next tick. show-dns resolves new hosts at once (then every 30 s). A removed sender
    # gets no more SUBSCRIBEs; its streams stay in the registry and go down by the usual rules.
Receiver.set_priority(names: tuple[str, ...]) -> None
    # any thread; these rank before Config.stream_priority (which stays); the usual 3 s switch-up rule applies
```
- **Host fallback.** For a sender with `hosts`, each host is resolved.
  - A host "answers" when a packet arrives from its resolved address.
  - While some host has answered within 3 s, SUBSCRIBE goes to the first such host in list order only. Otherwise it
    goes to every resolved host.
- **Health:** `senders.<name>` gains `"hosts": [{"host", "addr", "resolve_error", "answered_age_ms"}]` and
  `"using": host | None`. `host` keeps its old meaning: the host in use, else `hosts[0]`.

**tools/show/fetch_test_music.py.**
- `SOURCES = [{"id", "title", "artist", "genre" (a vocab genre), "license" (CC0 | CC BY | CC BY-SA | public domain),
  "url" (the audio file), "page" (where the licence is stated), "sha256" | None}]`. There must be at least one track
  per vocab genre: pop, techno, house, jazz, ambient, classical, rock, soul.
- Every URL and licence is checked by the builder: no placeholders.
- `python tools/show/fetch_test_music.py [--dir D] [--only id,...] [--list] [--force]` downloads to
  `D/<id>.<ext>` (default `contract.TEST_MUSIC_DIR`) with stdlib `urllib`, then writes
  `D/index.json` = `{id: {title, artist, genre, license, page, file, bytes, sha256, fetched_at}}`.
- Idempotent: a present file with a matching sha256 is skipped.

**Tests (B4):**
- `senders_from_settings`.
- The receiver: fallback (subscribe-all, then the answering host), `set_senders` (add, remove), `set_priority` (a test
  stream takes over after 3 s).
- A child: a small stand-in script, or the real show_sender on an ephemeral port; stopped cleanly; a failure reported.
- The test-audio child: an ffmpeg-made WAV in tmp, a SUBSCRIBE from a test socket, VBAN audio and a heartbeat back.
- fetch: `--list` and a local `file://` URL (no network).

**As built (16 Oct, B4): `sources.py`, `test_audio.py`, `tools/show/fetch_test_music.py`,
`config/live/test-music/SOURCES.md`.** Tested in `tests/test_sources.py` (27 tests, about 8 s). They cover:
- the sender parsing, the shipped show.yaml, and the hot reload;
- stand-in children: start, play, stop, a failure, a piece switch, SIGKILL after an ignored SIGTERM, `rank_first`,
  fast polling on a brain loop, and `stop()`;
- the decoder, prefetch and pacing on a fake clock (subscribe, pause, resume, stall, underrun);
- the real test-audio child into the real `Receiver` over ephemeral localhost ports;
- fetch with `file://` URLs.

Since the review (16 Oct), also: the shim execs the real argv and a child's grandchild dies with its group's SIGKILL;
the shim exits at once when its parent is already gone; the shipped show.yaml subscribes no room sender (the go-live
step) and marks test-audio `room_sound: false`.

It was also smoke-run by hand: Sources drove the real `show_sender --test-piano` and the test-audio child (a fetched
track) into a real Receiver, through a switch and a stop. The library is fetched: 9 tracks, 39 MB, in
`~/.cache/screen-machine/test-music/`.
- **The receiver seam: built by the integrator (16 Oct)** in `receiver.py`, wired by `__main__`
  (`ctx.set_senders=rx.set_senders`, `ctx.set_priority=rx.set_priority`). Tests in `test_receiver.py`: a sender added
  and removed (SUBSCRIBEs start at once and stop), the host fallback (every host until one answers; then that host
  alone; the first in list order when two answer; every host again after 3 s of silence; a packet from another port
  doesn't count as an answer), and `set_priority` lifting a test stream by the usual 3 s switch-up rule and dropping it
  again. As built:
  - "Answers" means a packet whose source (address **and** port) is a host's resolved address; a sender that sends
    from another port is simply never "using" one host, and every host keeps getting SUBSCRIBEs (still works).
  - DNS results are keyed (sender, host); a host that stops resolving keeps its last good address; hosts no longer
    listed are dropped.
  - Health: `senders.<name>` adds `hosts`, `using` and `on_demand`; the top level adds `priority`.
  - Without sources.py (or with `--no-brain`) the receiver keeps `cfg.senders`, as in P0.
- **Senders.**
  - `parse_senders(settings) -> (senders, problems)` is the validating half of `senders_from_settings`.
  - It accepts `hosts:` as a list or a single string (`host:` too), and `streams:` as a list or a string.
  - A static sender is shadowed only by a *valid* show.yaml entry of the same name, so a typo falls back to the CLI's.
  - A test whose `tests.<source>.sender` names no valid entry plays as a localhost sender on the contract's default
    port (`TEST_PIANO_PORT`/`TEST_AUDIO_PORT`), and is added while it runs.
  - The child's stream is the sender's first stream.
  - `rank_first: true` works for either source.
- **No name branching.** `contract.TEST_SOURCE_PLAYERS` maps each test source (98's API words) to its player kind
  (`midi` → show_sender, `audio` → test_audio) and documented defaults (stream, port, sender); `sources.PLAYERS` joins
  that with the kind's folder key and files (`KINDS`). api.py's `neutral_source` reads the same table. Nothing looks at
  a source's name (review, 16 Oct).
- **Children die with show_service** (review, 16 Oct). A child runs in its own session, so `scripts/ctl`'s
  `kill -KILL -- -$pid` never reaches it. Each is started as `python -c PDEATH_SHIM <show_service pid> <argv>`: the
  shim sets `PR_SET_PDEATHSIG` (SIGKILL; it survives the exec), checks its parent is still the one it was given, and
  execs the real program, so a crash, a SIGKILL or a stop slower than ctl's 15 s never leaves a child holding UDP
  6981/6982. The log's first line shows the real argv. test_audio also stops by itself when its parent changes
  (`_watch_parent`, every second), for a start by hand.
- **Children.**
  - A piece switch SIGTERMs the old child and spawns the new one only once the old has exited, so the port is free.
    Meanwhile the status is `starting` with `pid: null`.
  - `playing` needs `ctx.health()` to show the stream `streaming` or `quiet`, with a packet newer than this child's
    start (`t_ms - age_ms`), so a restart isn't fooled by the previous run. With no `ctx.health`, 2 s alive counts as
    playing.
  - While anything is starting or stopping, `_poll` also runs every 0.1 s via `ctx.loop.call_later`, but only when
    called on that loop. Otherwise the 1 Hz tick does it.
  - The log's first line is `# show_service starts: <argv>`, which is left out of `error`.
  - A piano file from a folder other than `contract.TEST_PIANO_DIR` is passed to show_sender as an absolute path
    (`file:` ids resolve only in its own library).
- **The test-audio child.**
  - A `Prefetch` thread keeps 0.5 s decoded ahead, so an ffmpeg restart at EOF never delays the pacing. If it ever
    runs dry, the packet goes out as silence, on time, and counts as an underrun in the log.
  - With no subscriber it sends nothing, and decoding blocks where it is.
  - The next subscriber gets a **new sess** with the counter and `n` from 0, as does a stall of more than 250 ms
    (`xruns` +1). Both follow vban's sender rule (> 250 ms of capture lost).
  - The first packet is decoded before the loop, so a file that won't decode exits 1 with ffmpeg's last lines in the
    log; a missing file or ffmpeg exits 2.
  - It logs `test-audio on udp <ip>:<port>`; `--port 0` binds any free port (the tests use that).
  - SUBSCRIBEs naming other streams are ignored.
- **fetch_test_music.py.**
  - Each `SOURCES` entry also has `license_url`, `credit`, `note` and `bytes`; all nine `sha256` are pinned.
  - Every download sends its `page` as the Referer (ccMixter refuses hotlinks) and a descriptive User-Agent
    (Wikimedia).
  - A checksum mismatch is not installed (`--no-verify` takes it). `--timeout` is also accepted.
  - `index.json` adds `license_url`, `credit` and `duration_s` (from ffprobe).
  - It needs only the stdlib (`python3 tools/show/fetch_test_music.py`, any venv).
  - The licences were checked on each page on 16 Oct, and the list with credit lines is in
    `config/live/test-music/SOURCES.md`:
    - CC BY: ccMixter (3), Internet Archive netlabels (Shoki, Lee Rosevere) and Jamendo mirrors (Josh Woodward,
      Lilly Wolf). Since the review (16 Oct) the evidence for Josh Woodward is the artist's own page (CC BY 4.0); Lilly
      Wolf's Jamendo page (track 1093616) draws its licence in the browser only and is still to be read by a person
      (SOURCES.md says how);
    - CC BY-SA: Monofónicos;
    - public domain: Musopen via Wikimedia Commons;
    - nothing NonCommercial.

### S3.11 room_service/adapters/show.py (B5; aa's contract, room venv)

```python
class ShowAdapter(Adapter):           # room_service.roomstate.contract.Adapter
    subsystem = "show"; subsystem_label = "Feel"; category = "setting"; kind = "level"; members = ["main"]
    change_time_s = 2.0; poll_s = 5.0; read_timeout_s = 2.0
    def __init__(self, url: str = "http://127.0.0.1:8703", vibes_file: Path = <repo>/config/room/vibes.yaml) -> None
    def label(self, member: str) -> str                 # "Feel"
    def value_schema(self, member: str) -> dict         # the Feel card's form (below)
    def normalize(self, member: str, value: Any) -> dict   # canonical VibeTarget; ValueError
    def show(self, member: str, value: Any) -> str      # "Party · listening", "Dinner"
    async def read(self, members: list[str]) -> dict[str, Reading]
    async def apply(self, desired: dict[str, Any], opts: dict) -> Outcome
    def situations(self) -> list[dict]                  # cached SituationDef list ([] before the first fetch)
```
- **read:** `GET {url}/api/v1/vibe` within `read_timeout_s`.
  - It returns `Reading({vibe, listening[, overrides]})`: only those keys, in canonical form.
  - `vibe: null` gives `Reading({"vibe": null, "listening": false})`: no feel, a fact (it was `Reading(None)`,
    unknown, until the review of 16 Oct, which left the room unable to store and put back "no feel").
  - It **raises** when show_service is unreachable or answers non-200, so the belief goes stale.
  - It also refreshes its caches, at most every 60 s, best effort: `GET /api/v1/vibes` (names, labels, `fields`) and
    `GET /api/v1/situations`.
- **apply:** `PUT {url}/api/v1/vibe` with `desired["main"]`, then compare the read-back's `{vibe, listening,
  overrides}`.
  - Equal → `Outcome("ok", members={"main": Outcome("ok")})`.
  - 400 → `Outcome("failed", detail, retry=False)`: since the review only a body that isn't a target at all (an
    unknown vibe is 200 and plays neutral).
  - Connection error, timeout or 5xx → `Outcome("failed", "show_service unreachable")` (retry allowed).
  - It never raises, except `CancelledError` (contract rule 6).
- **normalize** (pure and offline):
  - Accepts `"party"`, `{"vibe", "listening"?, "overrides"?}` and `{"name"}`.
  - `vibe` must match `NAME_RE`. When names are known (the cache, else the names parsed from vibes.yaml, mtime-cached)
    it must be one of them.
  - `listening` defaults to false.
  - `overrides` must be an object; its keys are checked against the cached `fields` when present (offline: shape
    only, and apply resolves the rest). An empty one is dropped.
- **value_schema** (the Feel card; enums from the `GET /api/v1/vibes` cache, empty before it):
  ```json
  {"type": "object", "required": ["vibe"], "x-ui": {"label": "Feel", "default_policy": "ignore"},
   "properties": {
     "vibe": {"type": "string", "enum": [<order>], "x-ui": {"label": "Feel", "widget": "select", "labels": {<name>: <label>}}},
     "listening": {"type": "boolean", "default": false, "x-ui": {"label": "Listen with AI", "widget": "checkbox",
                   "description": "Short music clips go to OpenAI while this mode is on (only with AI listening on)"}},
     "overrides": {"type": "object", "x-ui": {"label": "Fine-tune", "advanced": true},
                   "properties": {<field>: <VibesCatalog.fields[field]>}}}}
  ```
- **Registration:** `build_adapters(..., show: Adapter | bool | None = None)`.
  - None → `ShowAdapter()`.
  - False → `Placeholder("show", ["main"])` (tests).
  - It is live only at the next announced Flask restart, which no builder does.
- **The Auto card:** aa is asked to surface `ShowAdapter.situations()` in `GET /api/v1/room/describe` (under
  `subsystems.show.situations`) and as `x-ui.situations` on `mode_autopilot_schema`'s conditions.
- **Tests:** a fake show_service (an aiohttp test server on an ephemeral port).
  - read: canonical value; `Reading(None)`; raises when down.
  - apply: ok, a 400 → no retry, down → retry.
  - normalize cases.
  - value_schema enums from the fake `/vibes`, and the vibes.yaml fallback.

**As built (B5, 16 Oct).** `room_service/adapters/show.py`, registered in `build_adapters(..., show=...)`; tests in
`room_service/tests/test_show_adapter.py` (18, against a fake show_service on an ephemeral port).
- **Where it differs from the spec above:**
  - `normalize` checks the shape only. It does **not** refuse a vibe name that vibes.yaml lacks. A normalize error
    fails the whole modes.json load, and at a Flask restart that means the fallback config, so renaming a vibe would
    take the room's modes down. The editor offers only real names (the schema's `enum`), and show_service plays an
    unknown name neutral and says so in its read-back (review, 16 Oct: it used to answer 400 and the previous feel
    lingered). Override keys are likewise checked for shape only, plus `label`, `description` and `persona` being
    refused.
  - **No feel** (review, 16 Oct): `{"vibe": null}` canonicalises to `{"vibe": null, "listening": false}` (`NO_FEEL`),
    read and applied like any value (`show()` says "No feel"). The room stores it before a mode with a show row and
    puts it back on leaving, so the feel never sticks. A value with no `vibe` key at all is still refused.
  - **The same rules as show_service** (review, 16 Oct): a null override is unset and left out (show_service drops
    it too); a list override keeps the first of each item, in order (show_service's rule), so the read-back always
    compares equal.
  - **A cold show_service** (review, 16 Oct): every read-back carries `boot_id`. After an apply lands, the adapter
    keeps the value and that boot id. A read with a new boot id and no feel (show_service started with no state.json:
    a first enable, a wiped or torn state dir) re-applies that value once per boot, in the background: the room never
    retries an "ignore" row that already landed. A normal restart restores its own target from state.json, and a
    deliberate "no feel" is never re-applied over.
  - **The Fine-tune form:** `BASIC_FIELDS` (up front) are `beat_lock`, `change_looks`, `follow`, `relax_on_talk`,
    `listener` and `accents`; a field whose schema carries `x-ui.not_built` (strobe, peak tokens) goes behind More with
    every other field.
  - `__init__` takes an extra `cache_file`. `build_adapters` passes `<state_dir>/show_catalog.json`, which holds the
    last good catalog (names, labels, fields) and the situations, so the Feel form and the Auto vocabulary survive a
    restart while show_service is down.
  - Names come from show_service's live catalog, else vibes.yaml (mtime-cached; a bad edit keeps the last good), else
    that saved file. The fields and situations come from the live catalog, else the saved file.
- **The Feel form:** the overrides object is "Fine-tune" (behind More), with `unset_label: "As the feel"`. It also
  carries `unset_label_by: {"vibe": {name: "As the <label> feel"}}` for the page to name the chosen vibe (2f: a new
  hint, optional to read). Within it, §8.1's behaviour fields that do something today (`beat_lock`, `change_looks`,
  `follow`, `relax_on_talk`, `listener`, `accents`) are up front, and every other field is `advanced` (strobe and peak
  tokens: "(not built yet)").
  `listening` carries `x-ui.help` (the page reads it), as well as the `description`.
- **apply:** a 200 whose read-back differs is re-read for `change_time_s`, then `unconfirmed`. A 404 (show_service
  without its Stage-3 API) is `failed` and retried.
- **The Auto vocabulary, in aa's modules** (three minimal, generic edits; nothing names `show`):
  - `roomstate/contract.py`: `Adapter.vocabulary() -> dict[str, list]` (default `{}`);
  - `roomstate/schema.py`: `mode_schema(modes, situations=None)` → `mode_autopilot_schema(modes, situations)` puts the
    list on each condition as `x-ui.situations`;
  - `roomstate/coordinator.py` `describe()`: each adapter's vocabulary is merged into `subsystems.<sub>` (so
    `subsystems.show.situations`), and the situations go to `mode_schema`.
- **Until show_service serves `/api/v1/vibe`,** the `show` subsystem reads as unavailable ("show_service answered 404").
  That is harmless: no mode names `show` yet.
- **Room tests** (review, 16 Oct): `room_service/tests/roomstate/test_adapters.py` and `test_rooms.py` pass
  `show=False` to `build_adapters`, so they never build a real ShowAdapter against the production :8703 (aa to be
  told: S3.14).

### S3.12 listener.py, ring.py, utils/llm/, config/llm.yaml (B6)

**ring.py** (made by `__main__`, written on show-rx):
```python
class Rings:
    def __init__(self, *, seconds: float = contract.RING_SECONDS, fs: int = contract.RING_FS,
                 max_streams: int = contract.RING_MAX_STREAMS) -> None
    armed: bool       # any thread (the listener sets it): False -> write returns at once; True again forgets what was
                      # held before, so a clip never spans the pause
    def write(self, stream: str, n0: int, t0_host_s: float, pcm: np.ndarray, fs: int, flags: int) -> None
        # = contract.OnAudio (show-rx): mono mix -> soxr ResampleStream to 16 kHz (rebuilt on a new fs or AUDIO_SEGMENT)
        # -> int16 into a preallocated ring. AUDIO_GAP blocks write zeros and record a gap; a segment adds a time
        # anchor (index, t_host). Over max_streams, the ring written longest ago is dropped.
    def clip(self, stream: str | None, t0_s: float, t1_s: float) -> Clip | None    # any thread; None if not held
    def latest(self, stream: str | None = None, seconds: float = 24.0) -> Clip | None   # None stream = last written
    def info(self) -> dict      # {stream: {"fs", "held_s", "t_first_s", "t_last_s", "writes", "gaps", "lapped_reads"}}
class Clip(NamedTuple):
    stream: str; fs: int; t0_s: float; pcm: np.ndarray; gaps: list[tuple[float, float]]
    # t0_s = acoustic host s of pcm[0]; pcm int16 mono
```
Readers use a seqlock: they read the write count before and after the copy, and cut off any lapped part.

**listener.py**
```python
def attach(ctx: contract.BrainContext) -> Listener
class Listener:                                        # Component "listener", tick_hz 1
    def set_on(self, on: bool) -> contract.ListenerView    # the AI listening switch (persisted; default False)
    def view(self) -> contract.ListenerView
    def beliefs(self) -> contract.Beliefs | None       # any thread (snapshot)
    async def describe(self, text: str, context: dict) -> tuple[contract.DescribeIntent, str]   # (intent, "llm" | "keywords")
                                                       # never raises; LLM within describe_timeout_s, else keywords
    def provides(self) -> dict                         # {"beliefs": self.beliefs}
    def log(self, n: int = 50) -> list[dict]
```
**Gating** (§7.3, §7.5). Clips go only when all of these hold, **for every stream, test streams included** (review,
16 Oct: privacy was decided by a stream's name, so `phone-test` or any `test-*` stream would have uploaded clips with
AI listening off):
- the AI listening switch is on (the switch stops every upload; nothing is uploaded until the owner turns it on);
- `ctx.target().listening` (the mode's own wish; no show target set: no clips);
- `ctx.vibe().listener`;
- the music gate passes, except for a stream whose show.yaml sender says `room_sound: false` (a music file: no one's
  speech can be in it; shipped on `test-audio` only):
  - `music_gate: classifier` needs CED (P1). There is none in Stage 3, so room-mic clips never go;
  - `dsp`: the clock gate is `beat` or `dmx` (or harmony conf ≥ 0.3), and gate ≥ `situations.music.dsp_min_gate`;
- features are fresh, with gate ≥ `listener.quiet_gate` (0.1) over the clip's last 10 s;
- the ring holds at least min(`clip_s`, `listener.min_clip_s`) of the stream.

**The ring records only while a clip could go** (review, 16 Oct). The listener sets `Rings.armed` each tick to
whether the first three gates pass (switch, mode, vibe), so with AI listening off (the default) show-rx does no
resampling for nothing. Recording resumes from nothing (what was held before the pause is forgotten), so a first
clip waits `min_clip_s` (10 s).

`why` holds the first failing reason, in page words:
- "AI listening is off"
- "this mode doesn't listen"
- "this feel doesn't listen"
- "waiting for the music/speech check (P1)"
- "too quiet"
- "no sound coming in"
- "no model configured"

**Cadence.**
- A call is due every `cadence_s`, at least `min_gap_s` after the last one, with one in flight at most.
- After `agree_reads` agreeing, confident (≥ 0.6) verdicts, it moves to `relaxed_cadence_s`.
- Over the daily budget (`utils.llm.over_budget("listen")`), the cadence is ×3. The budget never stops it.

**One call:**
1. `rings.latest(<active stream>, clip_s)`.
2. Normalise to −16 LUFS (pyebur128 integrated loudness; the gain is clamped to ±24 dB). The true LUFS goes in the
   text.
3. MP3 at 64 kbps through `ffmpeg -f s16le -ar 16000 -ac 1 -i - -b:a 64k -f mp3 -` (asyncio, 5 s timeout), else WAV.
4. `utils.llm.acall_json("listen", system=<static prompt: field definitions, vocab words, the rules>,
   user=[<120 s DSP timeline at 2 s resolution from its own 1 Hz samples, < 600 tokens>, <the previous verdict,
   marked "hypothesis, may be wrong", left out every 5th call>], tool=<§7.3's output schema>, audio=…,
   audio_format="mp3")`.
5. Check it against vocab: an unknown genre becomes "unknown"; unknown moods are dropped.
6. Publish `InsightData` as `music.insight`, and fuse it into the beliefs.

**Beliefs** (§7.1):
- Votes decay with half-lives: genre 120 s, mood 45 s, instruments 60 s.
- A word changes only when a new leader wins two reads in a row by 0.15 of vote weight, or at once on `track_change`.
- After the last verdict, the beliefs hold for 60 s, then fade to neutral over 60 s: `fresh` → `holding` → `fading`
  → `none`.

**Describe it's keyword fallback:**
- Calmer words (calm, quiet, soft, gentle, relax, mellow, dim…) give −1 step each; wilder words (wild, loud,
  energy, more, bright, dance…) give +1. The total is clamped to ±3. No step word is a mode or vibe name (review,
  16 Oct: "party" and "rave" were wilder words, "chill" a calmer one), and at run time a word that names a mode in
  the context is never a step, so naming a mode only offers the switch.
- A palette name or label in the text sets the palette ("art", "painting" and "picture" → art).
- Vocab moods in the text, up to 3.
- keep, stay or hold → `keep`.
- A mode's name or label that isn't the current mode → `switch_mode` (offered only).
- `reply` says what changed, or "I didn't catch that: try a mood like 'mellow' or a palette like 'ember'."

**utils/llm/** (openai 1.x SDK; it must import under both Flask's `.venv` and `~/.venvs/sm-show`):
```python
def call_json(task: str, *, system: str, user: str | list[dict], tool: dict, audio: bytes | None = None,
              audio_format: str = "mp3", timeout_s: float | None = None) -> LLMResult          # sync; never raises
async def acall_json(task: str, *, ...same...) -> LLMResult                                     # AsyncOpenAI
def status() -> dict      # {"config": {"path", "revision", "error"}, "tasks": {task: {"models", "probed": {id: "ok" |
                          #  "missing" | "no_tools"}}}, "breakers": {id: {"failures", "open_for_s"}},
                          #  "spend_today_usd": {task: x}, "budgets": {task: x}}
def over_budget(task: str) -> bool
def reload(force: bool = False) -> None
@dataclass
class LLMResult:
    ok: bool; data: dict | None; error: str | None; task: str; provider: str | None; model: str | None
    latency_ms: float; usage: dict; cost_usd: float; attempts: list[dict]    # [{"model", "error"}]
```
- **The forced tool.** `tool` is `{"name", "description", "parameters": <JSON schema>}`. It is sent as
  `tools=[{"type": "function", "function": tool}]` with
  `tool_choice={"type": "function", "function": {"name": tool["name"]}}` and `store=False`. The output-token limit
  comes from the task.
  - With audio: `modalities=["text"]`, and the user content is `[{"type": "text", …},
    {"type": "input_audio", "input_audio": {"data": <b64>, "format": audio_format}}]`.
- **Validation.** The tool call's arguments are parsed and checked by a small local validator (type, required, enum,
  properties, items, maxItems, minimum/maximum; no new dependency). A failure counts as that model failing.
- **Fallback.** It walks down the task's model list.
  - A breaker per model: 3 failures in a row skip that model for 60 s.
  - `models.list()` is probed at first use, then hourly. A missing id is skipped and logged once.
  - No retries: the next window is the retry. The timeout is the task's `timeout_s`.
- **The key.** `OPENAI_API_KEY` from the environment, else from the repo's `.env` (KEY=VALUE lines).
  - It is never logged, and never put in an exception, the ledger or a status.
  - Error strings are redacted (`sk-…` becomes `sk-***`).
- **The ledger.** One JSON line per call to `contract.LLM_LEDGER`: `{at, task, provider, model, ok, latency_ms,
  usage, cost_usd, error}`. Spend = usage × llm.yaml's prices.

**config/llm.yaml** (B6 writes it):
```yaml
version: 1
providers:
  openai: {key_env: OPENAI_API_KEY, base_url: null}          # null = the SDK's default
tasks:
  listen:   {models: [{provider: openai, model: <id>}], timeout_s: 12, max_output_tokens: 400, audio: true}
  describe: {models: [{provider: openai, model: <id>}], timeout_s: 8, max_output_tokens: 300}
prices:     # USD per 1M tokens, with a "retrieved <date>" comment; model ids appear ONLY in this file
  <id>: {text_in: <x>, text_out: <y>, audio_in: <z>}
budgets:    # USD per day per task: reaching it slows the cadence, never stops it
  listen: <n>
```
- Real ids and prices go in only after B6 has checked them (`models.list()` with the key, and the pricing page).
- An id or price it can't verify stays out. An empty `models` list means "no model configured": the listener says
  so, and Describe it uses keywords.

**Tests (B6):**
- Rings: write and read across segments, gaps, a lapped read, `latest`, and the cost per block.
- Listener gating:
  - the switch off → no clip, for every stream (room-mic, test-audio, phone-test, any test-*);
  - no show target, or the mode's listening false → no clip, for every stream;
  - `room_sound: false` skips only the music check; a name never does;
  - no classifier → no room-mic clip;
  - the vibe's listening false;
  - the ring armed only while a clip could go.
- Cadence and backoff, with a fake clock.
- Beliefs: the two-reads rule, decay.
- The keyword fallback.
- utils/llm, against a fake OpenAI-compatible server on an ephemeral port (`base_url`):
  - forced-tool parsing;
  - a validation failure → the next model;
  - the breaker;
  - the ledger;
  - **the key never appears in logs** (caplog).

**As built (B6, 16 Oct).** `ring.py`, `listener.py`, `utils/llm/` (`core.py`, `__init__.py`, `pytest.ini`, `tests/`),
`config/llm.yaml`; `tests/test_ring.py` (12), `tests/test_listener.py` (40), `utils/llm/tests/test_llm.py` (19, passing
under both `~/.venvs/sm-show` and Flask's `.venv`). openai 1.77.0 and its pins are in `requirements.txt` and installed.
- **Smoke call** (one real call, 16 Oct): gpt-audio-1.5, a 5 s synthetic clip as 24 kHz MP3, with the forced tool,
  `store: false` and `modalities: ["text"]`. It answered valid JSON in 3.5 s; 50 audio tokens (10/s) and $0.0056.
  - A 24 s clip with the 120 s timeline comes to about $0.013 a call, so about $9-10 for 4 h at one call every 20 s.
  - The model ids in llm.yaml are checked against `models.list()`; the prices were retrieved 16 Oct from OpenAI's
    pricing page.
- **Rings.**
  - soxr runs at MQ.
  - soxr's output is delay-compensated, so the anchors are exact. The newest ~30 ms stays inside the resampler until
    the next block, and is dropped at a segment switch.
  - Measured on this box under `nice 19`: p50 24 µs, p95 63 µs per 128-frame 48 kHz stereo block. The threaded
    seqlock test saw no torn reads.
  - Extras for readers: `Rings.last_stream` and `Rings.streams()`.
- **Gating decisions.**
  - With no show target, no stream is allowed: "this mode doesn't listen" (the target's neutral `listening` is
    false). Since the review (16 Oct) that holds for test streams too.
  - "no sound coming in" is checked before the music gate.
  - With `music_gate: dsp`, a loud sound that has no beat and no key gets one extra page word: "no music heard".
  - The gate is checked again right before the upload. Turning the switch off cancels any call in flight.
  - A call reads only the ring of the stream it was gated for, so room sound can never go out under a test.
- **Cadence extras.**
  - Failures in a row stretch the interval to `cadence_s × 2^n`, capped at 300 s.
  - The previous verdict is left out of every 5th call.
- **Encoding.** llm.yaml's `tasks.listen.audio` holds `{format: mp3, sample_rate: 24000, bitrate: 64k}`.
  - ffmpeg runs under `nice -n 10` with `-threads 1`, and has 5 s.
  - If ffmpeg fails, the clip goes as a WAV, resampled by soxr.
  - The loudness measurement runs in the loop's executor, in 0.5 s pyebur128 pieces.
- **Beliefs.**
  - Genre confidence is `share × min(1, total votes / 2)`, so one read gives 0.5 and two agreeing reads give 1.0.
  - Mood confidence is `min(1, weight / 2)`.
  - `fresh` is under 30 s.
  - A section label lapses 30 s after its clip's end.
  - The trajectory's `eta_s` counts down from the clip's end.
- **Listener log.** Every verdict, failure, skip, Describe it and switch goes to `STATE_DIR/listener.jsonl` (rotated at
  1 MB) and to `log(n)`. contract has no constant for that path; c8 may add one.
- **`utils.llm` extras** (additive):
  - functions: `configure(config_path=, env_file=, ledger_path=)` (tests and tools), `task_settings(task)`,
    `validate`, `redact`;
  - new `status()` keys: `tasks[t].usable`, `calls_today`, `probes`, and `providers` (with key presence only);
  - per-model `options` (extra request fields), and per-provider `omit` (request fields that provider rejects).
- **Wiring (done by the integrator, 16 Oct):**
  - `Listener.start()` starts a daemon thread, `show-llm-warm`. It imports utils.llm and openai and reads today's
    ledger, all off show-brain.
  - `describe` gets `describe_timeout_s + 1` on its `brain.call` (api.py).
  - In `__main__`, `rings.write` runs after Features and the Clock, under the guard `ring.write`. `attach(ctx, **kw)`
    passes `log_path=<state_dir>/listener.jsonl`; the e2e test passes a fake `llm`.

### S3.13 Rules for every builder

- **Venvs:** `~/.venvs/sm-show` (the room adapter: `~/.venvs/sm-room`).
  - Every install and test run goes under
    `flock /tmp/claude-1000/sm-build.lock nice -n 19 …`.
  - Pins are appended to `show_service/requirements.txt` in a commented block per builder:
    - B1: `Pillow`.
    - B6: `openai==1.77.0` (as Flask has), and its dependencies as installed.

    Nothing else without asking c8.
- **Tests** are at the "does it work" level. Shared plumbing gets real tests: the bus seam, `Brain.call` and
  persistence, the receiver seam, NoteTap, Rings, utils/llm, the condition evaluator, palette maths, and artnet's
  `set_look`.
- **Never:**
  - bind 8703, 6980, 6981, 6982, 6454 (or 8700-8702) in a test: use ephemeral 127.0.0.1 ports;
  - send to a real device;
  - PUT, POST or open a WS to the real room API (:8700): use fakes;
  - call OpenAI;
  - read or write the real state dir or test-music dir: tests pass tmp paths;
  - restart Flask, stream_service or show_service;
  - kill a process you didn't start;
  - commit to git.

### S3.14 Going live, and who to tell

1. **show_service (c8).** The integration is done (16 Oct); deploy it with `scripts/ctl restart show`. Steps, in order:
   1. The venv already has the Stage-3 pins (Pillow, openai and its dependencies). To be sure:
      `flock /tmp/claude-1000/sm-build.lock nice -n 19 ~/.venvs/sm-show/bin/pip install -r show_service/requirements.txt`.
   2. The suite: `flock /tmp/claude-1000/sm-build.lock nice -n 19 ~/.venvs/sm-show/bin/python -m pytest show_service/tests -q`
      (710 passed on 16 Oct after the review's fixes, about 3.5 min).
   3. **`senders.kiosk` ships commented out** (review, 16 Oct). This restart subscribes no room sender from
      show.yaml (only the CLI's `testpiano`, until step 4), so nothing in the room drives Art-Net. **Uncommenting
      `kiosk` in show.yaml is the go-live step** for the room's own mic and piano: announce it first. From then
      (show.yaml hot-reloads; no restart) show_service SUBSCRIBEs the kiosk's two addresses on udp 6980 every second,
      and whenever 98's show-sender.service runs there the room's sound drives the screens' and lamps' Art-Net (the
      fixed amber while no vibe is set; the director's look once a mode sets one).
   4. `deploy/sm-show.env` (c8 with ca; tell 98): drop `--sender testpiano=localhost:6981/test-piano` from
      `SHOW_ARGS`. The test piano is now show_service's child (`POST /show/api/v1/test`) and show.yaml's on_demand
      `test-piano` sender subscribes it while it plays. Keeping the flag is harmless (the same socket asks, so it
      streams once), but it also keeps subscribing a test piano started by hand. Leave `SHOW_BRAIN` unset (on).
   5. `scripts/ctl restart show`.
   6. Check: `curl -s 127.0.0.1:8703/healthz` says version `0.2.0`; `curl -s 127.0.0.1:8703/api/v1/state | jq
      '.modules, .brain.running, .brain.settings, .brain.state_file, .artnet.palette'`: every module `ok`, `running` true, settings `error` null, state.json under
      `~/.local/state/screen-machine/show/`, palette `fixed`. `curl -s 127.0.0.1:8703/api/v1/card`: vibe null, Auto
      off, listening off.

   What changes at this restart:
   - Nothing visible: no room sender is subscribed from show.yaml (step 3); with no `show` rows in modes.json and no
     `PUT /vibe` the director stays neutral (`palette_fn` None: today's fixed colour, and today's idle in a quiet
     room). Auto has no rules (no mode has an `autopilot` block), so it does nothing even if the room's
     `autopilot.main` is on. AI listening is off.
   - New traffic to room_service: RoomLink's WS on `:8700/api/v1/events`, `GET /api/v1/room/config` on each config
     revision, and the director's `GET /renderer/api/catalog` (about 2.5 MB through :8700) at start and every 300 s.
   - The renderer gets `room.direction`, `room.look` and `room.event` on the bus (sound_view ignores them).
   - **The LLM.** Nothing is uploaded while AI listening is off, test audio included (review, 16 Oct: test streams
     no longer pass the switch, and no show target means no clips). With the switch on, a mode that listens and a
     vibe that allows it, test audio (`room_sound: false`) is clipped every 20 s (about $0.013 a call, about $2.30 an
     hour); the room mic waits for the music/speech check (P1).
   - Rollback: `SHOW_BRAIN=off` in `deploy/sm-show.env`, then `scripts/ctl restart show` (exactly P0 again).
2. **The room adapter (B5): one announced Flask restart, after step 1.**
   1. Tell aa: `build_adapters` gained a `show` parameter (room_service/adapters/__init__.py), and the roomstate edits
      (`contract.Adapter.vocabulary()`, `schema.mode_schema`/`mode_autopilot_schema` with `situations`,
      `coordinator.describe()` merging vocabularies; S3.11 "As built" has the places). Also (review, 16 Oct):
      `show.main` now reads `{"vibe": null, "listening": false}` ("No feel") as a value, so the generic put-back stores
      and restores it; the adapter re-applies its last value itself after a cold show_service (a settled "ignore" row
      is never retried by the room); and `tests/roomstate/test_adapters.py` and `test_rooms.py` pass `show=False` to
      `build_adapters` (two call sites, so those tests never touch :8703).
   2. Ask the owner of `room_service/tests/conftest.py` to add `self.extra_adapters = [Placeholder("show", ["main"])]`
      in `Harness.__init__`; until then the full room suite reads the production show_service (GETs only).
   3. Tests: `cd room_service && flock /tmp/claude-1000/sm-build.lock nice -n 19 ~/.venvs/sm-room/bin/python -m pytest
      tests/test_show_adapter.py tests/roomstate -q -m "not browser"` (283 passed, 3 skipped on 16 Oct, after the review's fixes).
   4. With 2f: their `/show/*` and `/rs/show/*` pass-through ships in the same restart. Tell the other sessions, then
      restart Flask (`start.sh`; CLAUDE.md: room_service runs inside Flask).
   5. Check: `curl -s 127.0.0.1:8700/api/v1/room/describe | jq '.subsystems.show.situations | length'` (9), the room's
      `show.main` belief (`{"vibe": null, "listening": false}`, "No feel", until a mode sets one), and `curl -s
      127.0.0.1:8700/show/api/v1/card`.
   6. Only then do modes.json `show` rows (2f, as the owner decides) take the director out of neutral.
3. **2f:**
   - the `/show/*` pass-through on :8700 (prefix stripped) and `/rs/show/*`;
   - the Music card from `GET /show/api/v1/card` and `WS /show/v1/events`;
   - the Auto line and chips;
   - modes.json `show` rows (S3.5's proposal) and `autopilot` blocks, as the owner decides;
   - (review, 16 Oct) the card's "More: lamp brightness" is the Lights low toggle (S3.9 card rules); `PUT
     /api/v1/vibe` never refuses a name now: the console can show `known: false` and the read-back's `problems`
     ("the feel 'x' isn't in vibes.yaml"); `DELETE /api/v1/vibe` is no feel; the Fine-tune form's strobe and peak
     tokens say "(not built yet)" (`x-ui.not_built`) and accents read "Lamps' beat swell".

   The show adapter and the pass-through go live together, at one announced Flask restart (never a builder's).
4. **aa:** review `build_adapters`' `show` line; surface `situations` in describe (S3.11). `reason`, `expect`, `skip`
   and `sent_age_s` are already done.
5. **98:** `POST`/`DELETE`/`GET /show/api/v1/test` (S3.9) is the promised API. The test piano runs as a child of
   show_service on localhost:6981. (Review, 16 Oct) Each child now runs through `sources.PDEATH_SHIM`, so it dies
   with show_service; a stop SIGKILLs its process group. Test audio reaches the LLM listener only with AI listening on
   and a mode that listens, like the room mic.
6. **04:** room.direction, room.look and room.event are on our bus now. sound_view ignores unknown types; P4 reads
   them.
7. **68:** the Art-Net layout is unchanged. Once a vibe is set, screens and lamps take the director's colours, and
   `hue_depth` and `beat_lock` vary per vibe. (Review, 16 Oct) While a vibe is set, a quiet room or stale features no
   longer stop Art-Net: the director's relaxed look keeps going out, steady, with no pulses (artnet_out "Idle"), so
   room's Hue output keeps following; with no vibe, the P0 idle is as before.
