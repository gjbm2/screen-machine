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
  `127.0.0.1:6982/udp` (audio) while they play. Stage 5 opens **no listening port**: Art-Net goes unicast from one
  ephemeral socket to the nodes fixtures.yaml names (none yet), and Hue Entertainment (DTLS) goes to the bridge in its
  pairing file, both only once fixtures.yaml enables that output (`rig.artnet.reply_bind`, ca's relay port, stays
  unset until ca registers one).
- **Files it writes:** `~/.local/state/screen-machine/show/` (`--state-dir`): `state.json`, `director.jsonl`,
  `autopilot.jsonl`, `listener.jsonl`, `test-piano.log`, `test-audio.log`, `ml-worker.log` (Stage 4: the worker's
  stderr, appended per run after a "--- ml-worker start ---" line, rotated to `.1` past 1 MB), `nodes.json` (Stage 5:
  where artnet_lan's discovery last found each node; written only when a node answers from a new address). It reads
  `config/room/show.yaml`, `config/room/vibes.yaml`, `config/room/patch.yaml`, `config/llm.yaml`,
  `config/live/vocab.yaml` and (Stage 5) `config/room/fixtures.yaml`, `config/room/fixture-types/` and
  `config/room/latency.json`, each re-read when it changes; (the worker) `~/.cache/screen-machine/models/` (Stage 4;
  `tools/show/fetch_models.py`); and (Stage 5, only once the Hue output streams) the bridge's pairing file
  `~/.config/screen-machine/hue/<bridge>.json`.
- **The ML worker** (Stage 4) is show_service's child, `python -m show_service.ml.worker` in its own venv
  `~/.venvs/sm-show-ml`, at nice 19 in its own session, fed over a pipe. No port.

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
| **Stage 4** (below) | | |
| `ml/` (`worker.py`, `models.py`, `protocol.py`, `supervisor.py`), `tools/show/fetch_models.py`, `export_onnx.py` | the ML worker child in `~/.venvs/sm-show-ml`: CED, Discogs-EffNet + heads, Essentia key, Beat This! (S4.4) | **done**, tested; models fetched |
| `mllink.py` | `MLFeed` (show-rx → the worker's stdin) and `MLLink` (component `ml`: supervises the worker, `ctx.ml`, Beat This! votes → the clock, CPU) | **done**, tested, wired |
| `dsp.py` medium lane, `clock.py` show-beat | `music.rhythm` (10 Hz, want-only), the audio chroma; madmom off show-rx | **done**, tested, wired |
| `structure.py` | `music.context` (1 Hz), sections, phases, track changes, structure's `room.event`s, GOBO on sections while no vibe plays | **done**, tested, wired |
| director, situations, listener, api card, vibes | Stage 4's consumers (S4.8) | **done**, tested, wired |
| `tests/test_e2e.py::test_stage4_end_to_end` | three real tracks back to back through the real worker, against a fake room | **done**, passing |
| Stage 4 review fixes (S4.13) | the 24 review issues of 16 Oct: GOBO ownership, track changes, the CED speech words, hysteresis, the ML genre, the meter, tokens, freshness, the worker's guards, memory, hand-off, budget, the owner's 0.8 | **done**, tested (948 passed); not committed |
| **Stage 5** (below) | every output **off** in config/room/fixtures.yaml: the restart that ships it changes nothing anyone can see | |
| `fixtures.py`, `ofl.py`, `tools/show/add_fixture.py` | the fixture registry (fixtures.yaml, OFL types, selectors, groups, latency), hot-reloaded | **done**, tested, wired |
| `looks.py`, `colour_mix.py` | the look engine (layers, hits, caps, latency per fixture, the screens rule) and colour to emitters | **done**, tested, wired |
| `outputs.py` | the `rig` component: registry, engine, drivers, one runner thread per enabled output | **done**, tested, wired (c8) |
| `hue_out.py` | Hue Entertainment from show_service (off until fixtures.yaml **and** patch.yaml `hue.streamer: show`) | **done**, tested with a fake bridge, wired; aligned with 68's hand-over contract (S5.6b: room's Hue code imported, the four route states); live at the Hue cut-over (S5.12) |
| `artnet_lan.py` | ArtDmx to LAN nodes on the beat grid, ArtPoll, failsafe, the virtual node | **done**, tested against local sinks, wired; live when a node is bought |
| director, RoomLink | the screens' hand-pick hold, the `source` probe, room events, the lamps from the registry, `look_fn` | **done**, tested, wired |
| `api.py` | `/api/v1/rig`, `/rig/frame`, `/rig/dmx`, `/rig/nodes/{id}/record-failsafe`, `/hue/stream`, `/hue/release`, `/hue/status` (68's contract: `state` is `idle`, `armed`, `streaming` or `taken_over`, with `lights` and `boot_id` for room's re-sync; S5.6b, S5.6c) | **done**, tested, wired |
| `tests/test_e2e.py::test_stage5_end_to_end` | test music -> director -> engine -> DMX on the beat grid and Hue at 50 Hz (fakes and sinks), the screens rule, the hold | **done**, passing |
| Stage 5 review fixes (S5.14) | the 20 review issues of 16 Oct: the drop hit per output's caps, the key light's white, no vibe on DMX, the wheel, House for DMX fixtures, Hue's rise limit and min period, the DMX confidence policy, House/Low/dip steady, the hold, measured emitters, the persisted Hue set, the grid's tolerance, the rate cap, the debounce, per-output hit wakes, the build-ahead, the CPU caveats | **done**, tested; two owner calls open (S5.14); not committed |

**Process split.** §2 has two processes: show_rt (hot, no aiohttp) and show_brain. For now everything runs in one
process: the brain is a thread (`show-brain`), aiohttp stays on its own thread (`show-bus`), and the hot thread only
ever calls `publish()` and the two taps. When the split happens, `publish()` and BrainContext's engine-side slots
become the IPC seam.

## Threads and data flow

```
 senders ──VBAN/UDP──► [show-rx]  Receiver.handle(buf, addr, arrival_s)
                          │  vban.parse → registry → SampleClock / CaptureMap / RemoteTimeMap
                          │  gaps (hold, zero-fill), segments, health (2 Hz snapshot)
                          ├─ on_audio(stream, n0, t0_host_s, pcm, fs, flags) ► Features.process (fast + Stage 4's
                          │                                                     medium lane), then Clock.process (AGC,
                          │                                                     soxr, hops → show-beat), then
                          │                                                     Rings.write (Stage 3; returns at once
                          │                                                     while the listener hasn't armed it),
                          │                                                     then MLFeed.write (Stage 4; returns at
                          │                                                     once while no worker is attached)
                          │                                                     (active audio stream only, same block)
                          │      Features' music.hit bodies → Clock.observe_hit (sub/bass hits refine the phase)
                          │      Features.clock_fn = Clock.snapshot (swing, syncopation)
                          ├─ on_midi(stream, msg, arrival_s, to_host) ─────► NoteTap.on_midi (Stage 3), then
                          │                                                   midi.Piano.on_midi
                          └─ on_tick(now_s) every ≤10 ms ─────────────────► Piano.tick · Features.tick (music.features
                                                                              25 Hz, music.rhythm 10 Hz) · Harmony.tick
                                                                              (the audio chroma when no piano plays)
                                                                              · Clock.tick (drains show-beat's results)
                                                                              · (0.5 s) snapshot, PianoView
                                                                              │ publish(msg)  (thread-safe, non-blocking)
                       [show-beat] (Stage 4, clock.py) madmom's activation per 10 ms hop, off show-rx; results back
                                   through a deque (SHOW_BEAT_THREAD=off: inline on show-rx, as in Stage 3)
                       [show-dns]  resolves sender hosts every 30 s           ▼
                                   (at once after Receiver.set_senders)
                       [show-bus]  asyncio + aiohttp: WS fan-out, /healthz, /api/v1/state, api.py's routes and
                                   /v1/events (Stage 3: they reach the brain only through Brain.call and snapshots)
                       [show-brain] (Stage 3) one asyncio loop: room · situations · autopilot · sources · ml ·
                                   listener at 1 Hz, structure and director at 10 Hz; RoomLink's WS, listener calls,
                                   art fetches, children; (Stage 4) mllink's worker supervisor and line reader,
                                   structure's GOBO steps (loop.call_at)
                       [child]     (Stage 4) python -m show_service.ml.worker (~/.venvs/sm-show-ml, nice 19, its own
                                   session): reader · ml-fast (CED, EffNet tags, key) · ml-beats (Beat This!);
                                   stdin = MLFeed's frames, stdout = one JSON line per result
                       [show-artnet]       deadline loop ≤44 Hz: clock.next_beat + features.latest() +
                                           Director.palette_fn (all by reference)
                                           → ArtDmx → 127.0.0.1:6454 (room's receiver; only when configured)
                       [show-artnet-poll]  room's /api/v1/dmx every 5 s (yield), patch.yaml mtime every 2 s
                       (Stage 5, the rig: S5.3)
                       [show-brain]  + rig (1 Hz): fixtures.Registry.poll, LookEngine.configure on a change, drivers
                                       built/started/stopped as fixtures.yaml enables outputs; HueOut's control task
                       [show-out-<id>] one per enabled output (none until fixtures.yaml enables one): driver.plan ->
                                       wait (an Event: a hit or a reconfigure re-plans) -> LookEngine.evaluate(<id>, t)
                                       -> sleep to t -> driver.set_frame (HueOut: send_colors; ArtNetLan: sendto)
                       [hue-dtls-sender] hue-entertainment's own, while the Hue output streams
                       show-rx's music.hit bodies → Clock.observe_hit, then Rig.on_hit (the engine's ring; the hit
                       outputs' runners woken only for a hit the look would flash)
                       [main]      signals, latency.json reload every 2 s → Receiver.set_offsets
```

- **One hot thread** (`show-rx`) runs ingest, DSP, MIDI and harmony in order. Nothing on it locks, waits or does I/O
  except `recvfrom`/`sendto`.
- `midi`, `harmony` and `dsp` objects are owned by `show-rx` and are not thread-safe. Anything other threads read
  is a snapshot dict, swapped in by reference.
- **Hot-thread rules** (§2): numpy, scipy.signal and pyebur128 only, plus the stdlib, and for the clock's rhythm lane
  soxr and madmom (aubio-ledfx as the fallback); no logging per packet; reuse arrays where it is cheap; add 1e-9
  dither before IIR filters (denormals); budget ≤0.45 ms per 256-sample hop for the whole fast lane. Since Stage 4
  madmom's activation runs on `show-beat`, so a madmom frame no longer holds show-rx (it held a block about 2.5 ms
  p95); what is left is the GIL they share (S4.6 "As built: the clock", S4.12 "Measured").
- Every published time is the time of the **sound**: the timeline's acoustic map, never the arrival time.
- **Imports:** a module imports `contract`, `vban` and `timeline`, never another builder's module. `__main__`
  imports everything. (Stage 4: `mllink` imports `ml.supervisor` and `ml.protocol`, which it wraps: c8's ruling.)

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

**Stage 4 end to end (`test_stage4_end_to_end`, about 2.5 minutes):** three real tracks through the real ML worker;
see S4.12.

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
- **Stage 4 line** (optional): `SHOW_BEAT_THREAD=on` runs the clock's activation on its own thread (off by default:
  S4.12). The ML worker needs no line: show.yaml's `ml` section drives it. The going-live steps are S4.11.

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
  `"show.main": {"set": <VibeTarget>, "restore": true, "policy": "ignore"}`.
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

## Stage 4: local ML, the medium lane and sections (P3)

**Interfaces, 16 Oct (c8).** This section defines Stage 4 so that five builders can work in parallel, each in its own
files. It turns SHOW_ENGINE §4 (the medium lane), §5.3 (Beat This!: integer corrections only), §6 (local ML, sections,
build/drop, loudness), §7.3 and §7.5 (the CED gate, the section call), §8.5 (section rules, section memory, peak tokens,
build → drop), §8.9 (situations from CED) and §11.1 (`music.context`) into a build spec.
- The shared names and the **exact shapes** are contract.py's **"Stage 4" block**, plus the Stage-4 slots at the end of
  `BrainContext`.
- The config is show.yaml's Stage-4 keys (S4.9). They are in the file now; nothing reads them until this code ships.
- Where this section and SHOW_ENGINE.md differ, this section is the build spec.

| Piece | Builder | Status |
|---|---|---|
| `show_service/ml/` (the worker), `tools/show/fetch_models.py`, `tools/show/export_onnx.py` | B7 | **built**, tested (S4.4a) |
| `mllink.py`, and the `__main__` / brain / bus / config seams | B8 → c8 | **built**, tested (S4.5 "As built") |
| dsp's medium lane and `music.rhythm`; clock's `show-beat` thread | B9 | **built**, tested (S4.6 "As built") |
| `structure.py` (`music.context`, sections, phases, events, GOBO) | B10 | **built**, tested (S4.7 "As built") |
| director, situations, listener, vibes, and the card line | B11, c8 | **built**, tested (S4.8 "As built") |
| integration: wiring, reconciliation, the Stage-4 e2e, the CPU measurement | c8 | **done** 16 Oct (S4.12); not committed, not deployed |
| the review's 24 issues (every section below now says what the code does) | c8's fixer | **done** 16 Oct (S4.13); not committed, not deployed |

### S4.0 Rulings every builder keeps (owner)

S3.0 still holds. For Stage 4:
1. **No special cases.** No code branches on a mode, vibe, device, stream or genre name.
   - A model-label → our-word mapping is config:
     - Discogs parent → vocab genre (`genre_map`);
     - AudioSet class → CED word (`ml.ced_groups`);
     - music.context field → model head (`ml.tasks.tags.heads`).
   - A model's own facts (labels, which class means "yes", a regression's range) come from its `index.json` entry.
   - The name scan in test_director.py adds `mllink.py` and `structure.py` (B11 edits that list).
2. **No invented values in live config.**
   - Every threshold is a show.yaml key marked "starting point", with the § it comes from. The code keeps the same
     numbers as fallbacks, marked the same way.
   - An unknown stays unset. For example, `peak_tokens_per_hour` stays unset in vibes.yaml until the owner sets it.
3. **Screens are never dimmed.**
   - Dips, hits, floors and the build grammar reach lamps and DMX only.
   - A section, build or drop reaches a screen only as a look change (palette, words), an event, or GOBO.
4. **Degrade, never refuse.**
   - With no worker, no models, or a failing model, every reader falls back (S4.3 "Without the worker").
   - Nothing answers an error and no control is disabled because ML is missing.
5. **No auth. No GPU.**
   - The worker runs with `CUDA_VISIBLE_DEVICES=` (empty) and CPU onnxruntime only.
   - torch, CPU wheels only, appears only in a scratch export venv.
6. **The host renders 4K.**
   - The worker runs at nice 19 with **two compute threads** (one per lane, intra-op 1 each).
   - It has a **hard per-cycle budget that skips work, never queues it**.
   - **show_service and the worker together stay under 0.8 of one core**, measured at go-live (S4.11).

### S4.1 Builders and files

| Builder | Its files (and the tests named for them) | Seams it alone edits |
|---|---|---|
| **B7 worker** | `show_service/ml/` (a package: `__init__.py`, `worker.py` for `python -m show_service.ml.worker`, and its other modules), `show_service/ml/requirements.txt`, `show_service/ml/tests/`; `tools/show/fetch_models.py`, `tools/show/export_onnx.py` | none |
| **B8 link and wiring** | `show_service/mllink.py`; `tests/test_mllink.py` | `__main__.py` (Stage-4 wiring), `config.py` (`SHOW_BEAT_THREAD`), `brain.py` (`add` appends list slots), `bus.py` (`TYPES`, `WANT_ONLY`); additions to `test_main.py`, `test_brain.py`, `test_bus.py`, `test_config.py` |
| **B9 show-rx lanes** | `dsp.py` (the medium lane), `clock.py` (the `show-beat` thread); additions to `tests/test_dsp.py`, `tests/test_clock.py` | none. harmony.py needs no change: it already takes `features.chroma12()` |
| **B10 structure** | `show_service/structure.py`; `tests/test_structure.py` | none |
| **B11 consumers** | `director.py`, `vibes.py`, `situations.py`, `listener.py`; additions to their tests (including the name scan) | `api.py`: only `card()`'s hearing rule 1 (S4.8), with an addition to `test_api.py` |

- c8 owns `contract.py`, `config/room/show.yaml`, `config/room/vibes.yaml` and this README. A builder that needs a change
  to any of them asks c8.
- **Parallel seams.** B8 codes against the signatures B9 and B10 are given here and tests with fakes. It reads every
  new piece with `getattr` (for example `features.rhythm`, `clock.stop`), so a piece that hasn't landed leaves its slot
  None. c8 integrates and runs the full suite.
- Builders don't commit; c8 does.

### S4.2 Processes, threads and the CPU budget

```
[show-rx]    + MLFeed.write (on_audio, after Rings.write): mono -> soxr 16 kHz -> 800-sample frames -> one os.write
               each to the worker's stdin (O_NONBLOCK; a full pipe drops the frame and counts it)     guard "ml.feed"
             + dsp's medium lane: a frame per 100 ms of stream time, inside Features.process; music.rhythm from tick
             + clock: the AGC, soxr 44.1 kHz and the 441-sample hops stay here. Each hop goes to show-beat, and the
               activations come back through a deque that Clock.process and Clock.tick drain first
[show-beat]  NEW (clock.py): the activation only (madmom's 2 nets, or aubio). It never touches clock state.
[show-brain] + ml (mllink.MLLink, 1 Hz): spawns and supervises the worker (asyncio), reads its stdout lines, keeps
               ctx.ml's view, and turns Beat This! results into Clock.apply_integer_correction
             + structure (10 Hz; its 1 Hz work on whole seconds): music.context, sections, phases, room.event; GOBO
               through loop.call_at(at_s)
[child]      python -m show_service.ml.worker (~/.venvs/sm-show-ml, nice 19, its own session):
               [reader]    stdin frames -> its 16 kHz ring (I/O only)
               [ml-fast]   ced -> tags -> key on a 1 s grid, 250 ms per cycle at most
               [ml-beats]  Beat This! every 10 s on 30 s, 5.5 s at most (an overrun skips its next turn)
               stdout: one JSON line per result; stderr: <state_dir>/ml-worker.log
```
- **Brain add order:** room, situations, autopilot, sources, **ml, structure**, listener, director. In one beat of the
  10 Hz loop, structure runs before the director. Events reach the director and the listener at once, through
  `on_music_event`.
- **Imports:** mllink and structure import `contract` and their own dependencies (numpy; soxr for mllink), never
  another builder's module. The worker imports only `show_service.contract` from show_service.

**The CPU budget.** One core = 1.0. It is measured at go-live on test music, with the render running.

| Piece | Today (measured in Stage 3) | Stage-4 target |
|---|---|---|
| show_service, all threads | 0.37-0.38 | ≤ 0.42. Medium lane ≤ 0.005, MLFeed ≤ 0.01, structure ≤ 0.01, the ml link ≤ 0.005. madmom moves to show-beat at the same cost |
| the worker | none | ≤ `ml.cpu_max` (0.35). Expected about 0.25: ced ~3 %, tags ~2 %, key < 1 %, Beat This! small0 ~20 % |
| **total** | | **< 0.8 (owner)** |

Measured at integration (S4.12): show_service 0.43-0.44 with the beat thread off (its default since), the worker
0.25-0.26, **total 0.68-0.69**. With the thread on, show_service was 0.57-0.58 and the total 0.80-0.84. After the review
fixes (S4.13): show_service 0.41, the worker 0.23-0.25, **total 0.64-0.67**; over 0.8 for 60 s, mllink now cuts the
worker's cpu_max to what show_service leaves.

### S4.3 The ML worker protocol (B7 and B8 both build to it)

**The process.**
- mllink starts `[ml.python, "-m", "show_service.ml.worker", "--models", <ml.models_dir>, "--parent", <our pid>,
  "--nice", <ml.nice>]` (`contract.ML_WORKER_MODULE`).
- It runs with `cwd=REPO_ROOT` and `start_new_session=True`.
- stdin is the read end of a pipe mllink made with `os.pipe()`, grown to 1 MB (`F_SETPIPE_SZ`, Linux's default
  pipe-max-size: about 16 s of frames, so a starved nice-19 reader drops nothing). The write end stays with us, set
  O_NONBLOCK.
- stdout is an asyncio PIPE. stderr is `<state_dir>/ml-worker.log` (`contract.ML_LOG`), appended after a
  "--- ml-worker start (attempt n) ---" line, so the last run's traceback survives the next start.
- The environment is ours plus `PYTHONUNBUFFERED=1`, `CUDA_VISIBLE_DEVICES=` (empty), `OMP_NUM_THREADS=1`,
  `OPENBLAS_NUM_THREADS=1`, `MKL_NUM_THREADS=1`, `NUMEXPR_NUM_THREADS=1` and `MALLOC_ARENA_MAX=2` (S4.13).
- **At start the worker:**
  1. sets its niceness to `--nice`;
  2. dups its stdout for the protocol and points fd 1 at stderr, so a library's stray print can never corrupt a line;
  3. sets `PR_SET_PDEATHSIG` to SIGTERM (ctypes `prctl`), and exits if `getppid()` isn't `--parent`;
  4. starts the stdin reader;
  5. loads the models (lazy imports);
  6. writes `hello`.
- **It dies with us,** since no group kill reaches its session:
  - EOF on stdin: it exits 0 within 1 s;
  - a failed stdout write (EPIPE): it exits.

**stdin: binary frames.** Each frame is one `os.write` of at most `ML_PIPE_ATOMIC` bytes (4096, Linux PIPE_BUF). Even a
non-blocking write of that size is all-or-nothing, so no torn frame ever reaches the worker.
```
header  ML_HEADER_FMT "<4sBBH16sIqdI", ML_HEADER_SIZE = 48 bytes, little-endian
  magic    4s  b"SMLA" (ML_AUDIO_MAGIC) | b"SMLC" (ML_CTRL_MAGIC)
  version  B   ML_PROTOCOL_V (1)
  flags    B   ML_FLAG_SEGMENT 1 | ML_FLAG_GAP 2 | ML_FLAG_AFTER_DROP 4   (audio; 0 on control)
  n        H   audio: samples in the payload, 1..ML_FRAME_MAX_SAMPLES (1012); mllink sends ML_FRAME_SAMPLES (800 = 50 ms)
               control: payload bytes, 1..ML_CTRL_MAX_BYTES (4048)
  stream   16s the stream's VBAN name, ASCII, NUL-padded (zeros on control)
  seg      I   the feed's segment: +1 at every ML_FLAG_SEGMENT
  n0       q   the payload's first sample: its 16 kHz index within the segment (0 at ML_FLAG_SEGMENT)
  t0       d   that sample's acoustic host seconds (CLOCK_MONOTONIC); sample i sounds at t0 + i / 16000
  dropped  I   the frames this feed has dropped so far (cumulative)
payload  audio:   n x float32, little-endian, mono, ML_FS (16000 Hz), -1..1
         control: n bytes of UTF-8 JSON
```
- **Segments.** mllink starts a segment (flag SEGMENT, `n0` 0, `seg` + 1) on:
  - a new worker (its first frame);
  - another stream;
  - another input rate;
  - `AUDIO_SEGMENT`.

  Within a segment `n0` is continuous. Frames we drop still advance it, so the next written frame carries the jump and
  `ML_FLAG_AFTER_DROP`.
- **On a jump, the worker:**
  - zero-fills a hole of up to 1 s and counts it (`holes`);
  - starts a new segment in its ring for a longer hole, or for `n0` going backwards.

  A window never spans two segments. A beats window never contains a hole or a GAP frame.
- **Control frames** are JSON objects with a `cmd`:
  - `{"cmd": "config", "v": 1, "ml": <the ml keys the worker reads>}` (`ML_CMD_CONFIG`: `cpu_max`, `silence_dbfs`,
    `fast_lane`, `tasks`, `ced_groups`; `supervisor.WORKER_KEYS`) goes right after spawn, before any audio, and on
    every change to them. Until one arrives, the worker uses the starting points in S4.9 (the same as show.yaml). A
    section that can't make a frame (over `ML_CTRL_MAX_BYTES`, a NaN, a value JSON can't carry) isn't sent: mllink
    keeps the last good one and lists the problem.
  - `{"cmd": "stop"}` (`ML_CMD_STOP`): the worker finishes the current line and exits 0. EOF does the same.
  - Unknown commands are counted and ignored.

**stdout: one JSON object per line.**
- Each line is UTF-8, at most `ML_LINE_MAX`, with no NaN (null instead).
- The shapes are contract's TypedDicts, exactly.
- mllink counts and skips any line that isn't one of them (`bad_lines`).

| `type` | When | Shape | Notes |
|---|---|---|---|
| `hello` | once, after the models load | `MlHello` | mllink feeds audio only after it |
| `ced` | every `tasks.ced.every_s` (1 s) | `MlCed` | `t` = the acoustic time of the window's end. Each of the seven `CED_GROUPS` words is the max of its `ml.ced_groups` classes |
| `tags` | every 1 s | `MlTags` | `genre_parents`, `styles_top3`, `moods_top5`, `instruments_top5` (the models' raw labels); `dance`, `voice_p`, `engagement`, `bright`, `acoustic`, `arousal` (0..1, None when the head isn't loaded); `emb_id`, `emb` (base64 float16 × 1280), `emb_dim` |
| `key` | every 2 s, over 20 s | `MlKey` | Essentia HPCP + Key (`tasks.key.profile`) |
| `beats` | every 10 s, over 30 s | `MlBeats` | times only: beats and downbeats in the interior, plus `bpm` and `bar_len`. The worker never sees our clock; mllink makes the vote (S4.5) |
| `stats` | every 10 s | `MlStats` | `cpu_share`; per task: runs, skipped, over_budget, cost p50/p95, `every_s`; `stretch` |
| `error` | when a task fails | `MlError` | counted; the task runs again at its next grid point |

**Cadences and budgets** (§6's table; show.yaml `ml.tasks` and `ml.fast_lane`):

| Task | Lane (order) | every_s | window_s | Model (catalogue name) | budget_ms |
|---|---|---|---|---|---|
| ced | fast, 1st | 1 | 2.0 | `ced-mini` (CED-mini on AudioSet, Apache-2.0) | 100 |
| tags | fast, 2nd | 1 | 2.1 (one EffNet patch: 128 frames × 256 samples at 16 kHz) | `discogs-effnet`, plus the heads in `tasks.tags.heads` | 80 |
| key | fast, 3rd | 2 | 20 | Essentia HPCP + Key (no model file) | 60 |
| beats | beats | 10 | 30 | `beat-this-small0` (MIT) | 5500 (about 2x its p95 under the render) |

- **A grid, not a queue.**
  - Each lane runs on a fixed grid of `every_s × stretch`.
  - A cycle that overruns starts the next one at the next grid point after it: never back to back, never catching up.
  - A task with no new audio since its last run skips without counting.
- **A hard budget.**
  - In the fast lane, the cycle's tasks share `ml.fast_lane.budget_ms`. A task whose p95 cost doesn't fit in what is
    left is skipped (`skipped`).
  - Every onnxruntime run takes a `RunOptions`. A watchdog sets its `terminate = True` at the task's `budget_ms`; the
    run raises and its result is dropped (`over_budget`), and the task's next turn is skipped (counted `skipped`):
    no retry at full cost.
  - Three over-budget runs in a row double that task's `every_s`, up to ×8. Ten good runs in a row halve it back.
- **The CPU guard.**
  - Over 30 s, a `cpu_share` above `ml.cpu_max` multiplies every interval by 1.5 (`stretch`, up to 4).
  - Under 0.7 × `cpu_max` for 60 s divides it by 1.5, down to 1.
- **Silence.** A window under `ml.silence_dbfs` runs no model:
  - `ced` reports `silence_p` 1, the rest 0, and `skipped: "silence"`;
  - tags, key and beats skip.
- **Threads.** Two compute threads, `ml-fast` and `ml-beats`.
  - Each onnxruntime session has `intra_op_num_threads=1`, `inter_op_num_threads=1` and sequential execution.
  - essentia is single-threaded.
  - The stdin reader and the watchdog only wait. No other thread runs model work.

**The restart policy (mllink).**
- **When:** the worker exits, or sends nothing for `ml.restart.stall_s` (30 s) while it is fed (stats come every 10 s).
  The worker exits by itself (code 70) when one of its lanes has made no cycle in max(3 x its period, its period +
  its budget + 5 s): a native call that never returns. Its reader and its stats would otherwise keep it looking alive.
- **Stop sequence:**
  1. detach the feed, and wait for a write under way on show-rx to end (`FrameFeeder.busy`, at most 0.5 s; past that
     `/dev/null` is put over our fd, which is closed only once the write is done, so its number is never reused under
     it);
  2. `{"cmd": "stop"}`, then close our pipe end;
  3. wait 2 s, then SIGTERM its process group;
  4. wait 2 s, then SIGKILL (and wait 2 s more). At most 6.5 s in all (`supervisor.STOP_MAX_S`); brain.py waits 7.
- **Backoff:** start again after `ml.restart.backoff_s[n]` (2, 5, 15, 60, 300 s, then 300 s for ever; none under 1 s),
  where n is the failures in a row. `ml.restart.healthy_s` (600 s) of running resets n. It never gives up for good:
  a value it can't use takes its starting point, and a fault in the supervisor itself ends that run (state `failed`)
  and is retried on the same backoff. An exit with a code over 0 keeps its last log line in `last_exit.why`.
- A change to `ml.python`, `ml.models_dir` or `ml.nice` restarts it. `ml.enabled: false` stops it (`state: disabled`).
- Only the Popen that mllink made is ever signalled, never with `pkill`.

**Without the worker** (disabled, starting, backoff, failed, or a model missing), each reader degrades:

| Reader | With the worker | Without it |
|---|---|---|
| situations `music_audible` | CED music (3 s mean) ≥ `min_prob` | the DSP guess (a beat or a key over the gate), as in Stage 3 |
| situations `talking` | CED speech (the max over `situations.talking.speech_words`: speech, conversation, crowd) ≥ `min_prob` and music < `music_below` | unknown, as in Stage 3 |
| situations `quiet` | the gate < `gate_below`, or CED music and speech (the same words) both under their `*_below` | the gate only, as in Stage 3 |
| listener (`music_gate: classifier`) | room-sound clips of music-dominant seconds only (speech: the max over `listener.ced.speech_words`) | no room-sound clips ("the music/speech check isn't running") |
| structure | EffNet embeddings (`source: ml`) | chroma + MFCC from music.rhythm (`source: dsp`) |
| genre (music.context, `genre_in`, the card) | EffNet's parents through `genre_map`, plus the listener's words for Electronic | the listener's genre, as in Stage 3 |
| key (music.context) | the piano's while it plays, else Essentia's (held: S4.7) | music.harmony's |
| clock | Beat This! integer corrections | none (the kick votes and the human actions stay) |

### S4.4 The worker, its venv and its models (B7)

**The venv.** `~/.venvs/sm-show-ml` (python3.12), pinned in `show_service/ml/requirements.txt`:
- `essentia==2.1b6.dev1389` (the last cp312 build);
- `onnxruntime` (CPU);
- `soxr`;
- numpy: the version essentia's wheel imports with (check `python -c "import essentia.standard"`), pinned.

There is no torch in it.
```
flock /tmp/claude-1000/sm-build.lock nice -n 19 python3.12 -m venv ~/.venvs/sm-show-ml
flock /tmp/claude-1000/sm-build.lock nice -n 19 ~/.venvs/sm-show-ml/bin/pip install -r show_service/ml/requirements.txt
```

**Imports.**
- `show_service/ml/*` imports `show_service.contract` (stdlib only) and its own venv's packages, never another
  show_service module.
- essentia and onnxruntime are imported lazily. So `python -m show_service.ml.worker --fake` also runs under
  `~/.venvs/sm-show` (numpy only), and the other builders' tests use that.

**`--fake`** is for tests only, never production.
- It needs no model files.
- It gives deterministic results from cheap numpy features:
  - ced from the window's loudness;
  - tags with an embedding from band energies, so novelty moves with the music;
  - key from a numpy chroma;
  - beats from onset autocorrelation.
- `hello.models` reads `"fake"` for every model. Its exact behaviour goes in B7's "As built".

**Models** come from `tools/show/fetch_models.py`. It needs only the stdlib, like fetch_test_music.py.
- **The catalogue.** `CATALOGUE = [{"name", "file", "url", "page", "license", "license_url", "sha256", "bytes", "task",
  "facts"}]`.
  - `facts` is what the worker needs to read a model:
    - its input rate and shape;
    - its labels (or the metadata JSON fetched with it);
    - which class is "yes" for a binary head (danceable, voice, bright, acoustic);
    - a regression's range (DEAM's arousal 1..9 → 0..1).
  - B7 checks every URL, sha256 and licence. No placeholders.
- **The command:** `python tools/show/fetch_models.py [--dir D] [--only a,b] [--list] [--verify] [--force]`.
  - It downloads to `D/<file>` (default `contract.MODELS_DIR`) and verifies the sha256. A mismatch is not installed.
  - It writes `D/index.json` (`contract.MODELS_INDEX`) = `{name: {file, sha256, bytes, license, license_url, page,
    source: "fetch" | "export", facts, fetched_at}}`.
  - It writes `D/LICENSES.md`: one entry per model, with its credit line.
  - It is idempotent. Models never go into git.
- **Exports**, where no ONNX file is published (Beat This!; CED-mini if it has none). `tools/show/export_onnx.py <name>`
  runs once:
  1. in a scratch venv under `/tmp/claude-1000/onnx-export/` (torch CPU from the CPU index, and the model's own code at
     a pinned tag or commit);
  2. it exports with a dynamic time axis;
  3. it checks the ONNX against torch on three clips (max abs difference ≤ 1e-3 on the outputs);
  4. it writes `D/<file>` and the index entry, with `source: "export"` and the upstream checkpoint's URL and sha256.

  fetch_models.py `--list` shows the exported models and how to make them. torch never goes into `~/.venvs/sm-show-ml`
  unless an export proves impossible; then CPU wheels only, and c8 is asked first.
- **Licences** (re-verify at build; §15):
  - Essentia's models: CC BY-NC-SA 4.0. That is fine for this private install, and is flagged if it is ever sold.
  - The essentia library the worker runs (not a model): AGPL-3.0. Fine for this private install; flag it before any
    distribution (also in `ml/requirements.txt` and fetch_models.py's LICENSES.md).
  - CED: Apache-2.0.
  - Beat This!: MIT; check the weights' terms on its page.
- **Catalogue names** that show.yaml uses:
  - `ced-mini`;
  - `discogs-effnet`;
  - `beat-this-small0` (and `beat-this-final0` if B7 measures it inside the budget);
  - the heads: `genre_discogs400`, `mtg_jamendo_moodtheme`, `mtg_jamendo_instrument`, `danceability`,
    `voice_instrumental`, `engagement_regression`, `nsynth_bright_dark`, `mood_acoustic`, `deam` (valence/arousal).

  A head with no published ONNX file is exported the same way, or left out; its field then reads null. B7 sends c8
  any name that differs from these, for show.yaml.

**Genre parents** come from the 400-style head's own labels (`"Parent---Style"`, split on `---`). No parent name is in
code. `genre_parents[p]` = the max of p's styles' activations, averaged over the window's patches.

**Beat This!** reads 22.05 kHz log-mel, and our feed is 16 kHz (the ring's rate), so it sees nothing above 8 kHz.
- The worker resamples 16 → 22.05 kHz.
- B7 measures the cost once, in the scratch venv: `beat-this-small0`'s beat F-measure on the fetched test tracks,
  limited to 16 kHz, against full-band input.
- The numbers go in B7's "As built". A large loss is c8's call: feeding 22.05 kHz is a contract change.
- Post-processing is Beat This!'s "minimal" one (peaks of the logits), reimplemented in numpy and checked once against
  the reference.

**Tests (B7)** run under `~/.venvs/sm-show-ml`, in `show_service/ml/tests/`:
- frame parsing: every flag, a hole, a backwards `n0`, a control frame;
- the ring's time map;
- the scheduler on a fake clock: the grid, a skip on budget, the over-budget doubling, the CPU stretch;
- the protocol over real pipes with `--fake`: hello, results, stats, the exit on EOF, stop;
- when the models are present (skipped otherwise): one real run of each task on a test track, with its cost printed
  (`-s`).

Tests never use the real models dir: they pass `--models <tmp>` (a symlinked or copied subset is fine).

#### S4.4a As built (B7, 16 Oct)

**Files.**
- `show_service/ml/`: `protocol.py` (frames, lines, the shape check, the embedding codec; stdlib), `models.py` (the
  back ends, real and `--fake`), `worker.py` (the process), `supervisor.py` (the show_service side, below) and
  `requirements.txt`.
- `tools/show/fetch_models.py` and `tools/show/export_onnx.py`.
- Tests: `show_service/tests/test_ml_{protocol,worker,supervisor,models}.py` (the job's file list put them there, not
  in `ml/tests/`). They run under `~/.venvs/sm-show` with numpy only. The real-model test spawns the ML venv's
  python, and skips when the venv or the models are missing.

**The venv** is `~/.venvs/sm-show-ml`. It pins essentia 2.1b6.dev1389, onnxruntime 1.31.0 (CPU), numpy 2.5.4 (checked
with `import essentia.standard` and KeyExtractor) and soxr 1.1.0, plus their dependencies. There is no torch in it.
The export venv is `/tmp/claude-1000/onnx-export/venv`, with torch 2.14.1+cpu and beat-this 1.1.0.

**Models.** They are in `~/.cache/screen-machine/models`, about 50 MB. Every sha256 matched its pin.

| Catalogue name | File | Licence | Note |
|---|---|---|---|
| `ced-mini` | `ced-mini.onnx` (HF `mispeech/ced-mini` @ `26c3ebc`) | Apache-2.0 | Published ONNX, int8. Raw 16 kHz in, 527 **probabilities** out (its head applies the sigmoid) |
| `discogs-effnet` | `discogs-effnet-bsdynamic-1.onnx` | CC BY-NC-SA 4.0 | Mel `[n,128,96]` in; `activations` (400) and `embeddings` (1280) out |
| `genre_discogs400` | its `.json` only | CC BY-NC-SA 4.0 | The head is published as `.pb` only. EffNet's own 400-style output has the same labels in the same order (checked), so `genre_parents` comes from it |
| `mtg_jamendo_moodtheme`, `mtg_jamendo_instrument`, `danceability`, `voice_instrumental`, `mood_acoustic`, `engagement_regression` | `*-discogs-effnet-1.onnx` | CC BY-NC-SA 4.0 | |
| `timbre` | `timbre-discogs-effnet-1.onnx` | CC BY-NC-SA 4.0 | bright/dark, trained on music. Replaces `nsynth_bright_dark` (below) |
| `approachability_regression` | ONNX | CC BY-NC-SA 4.0 | Fetched; no MlTags field reads it |
| `msd-musicnn` + `deam` | ONNX | CC BY-NC-SA 4.0 | Arousal (DEAM, range 1..9 → 0..1). MusiCNN shares EffNet's mel front end |
| `beat-this-small0` | `beat-this-small0.onnx`, exported | MIT (code and weights) | Checkpoint `small0.ckpt`, sha256 `6074be2c…` |

- **Facts** live in each index.json entry: kind, input, outputs, labels, `yes`, `range` and `via`. The worker reads
  only the kinds. It never names a model or a label.
- **CED's labels** are AudioSet's short names ("Male speech"), and show.yaml's `ced_groups` uses the full display
  names. A class therefore matches a label that equals it, or that equals its text before the first comma.
  - All 16 classes in show.yaml match.
  - A class the model lacks is logged.
- **The Beat This! export** used the TorchScript exporter (opset 17, a dynamic batch and time axis). torch.export's
  decomposition fails on this model.
  - ONNX against torch on three clips: max |d| 7e-5.
  - The worker's numpy log-mel against `LogMelSpect`: 8e-5.
  - The numpy "minimal" post-processing gives exactly the reference's beats and downbeats.
- **`beat-this-final0`** costs 2.9-3.9 s per 30 s window (about 0.35 core at every 10 s), so it is not used.

**For c8 (show.yaml `ml.tasks.tags.heads`; nothing else changes).** Done at integration (S4.12): `bright: timbre`,
`arousal` off, `engagement` left null.
1. `bright: timbre`. `nsynth_bright_dark` (and `nsynth_acoustic_electronic`) have no ONNX file and were trained on
   single NSynth notes. Until then, `bright` reads null and hello says `nsynth_bright_dark: missing`.
2. `arousal: deam` is **not cheap**. MSD-MusiCNN costs 47 ms per run, 8× EffNet. With it, tags take 56 ms p50 and
   75 ms p95 against an 80 ms budget, and the worker uses 0.29-0.36 core. Without it: 11 ms and 18 ms, and
   0.23-0.30 core.
   - Recommendation: drop it (arousal reads null), or accept about +0.05 core.
   - A secondary backbone only ever runs when its own p95 fits what is left of the tags budget, so it can't push
     EffNet's results over.
3. `engagement` reads **null**. The regression's output is linear, and Essentia doesn't publish its range. The 9 test
   tracks gave raw values from -0.01 to 1.01. Setting a range in the catalogue is c8's or the owner's call.

**Costs.** Measured at nice 19 on one thread, with the 4K render running (`python -m show_service.ml.worker --bench
FILE` reproduces them).

| Task | ms per call | Of which |
|---|---|---|
| ced (2 s) | 22-25 p50, 39 p95 | |
| tags (1 patch) | 11 p50, 17 p95 | Mel 2 ms for a new second (5-7 cold); EffNet 5-7; each head 0.02-0.2 |
| tags with MusiCNN arousal | 56 p50, 75 p95 | MusiCNN 45-55; DEAM 0.01 |
| key (20 s) | 13-14 p50, 16-22 p95 | |
| beats (30 s) | 1900-2400 p50, 2400-2750 p95 | soxr 11 ms, the numpy mel 29 ms; the rest is the model |

- **CPU over 75 s of pop at production cadences**, from the worker's own stats:
  - 0.04-0.05 core before the first 30 s beats window;
  - 0.23-0.30 core after it, with the recommended heads;
  - 0.29-0.36 core with show.yaml as it stands.
- **RSS** is about 260 MB with every model loaded; load takes 3.3 s. As first built, the first Beat This! runs grew it
  to about 1.6 GB for the evening (onnxruntime's CPU arena kept the 30 s window's peak). With the arena off for the
  beat tracker and `MALLOC_ARENA_MAX=2` (S4.13) it stays near 300-335 MB (`stats.rss_mb`, also the snapshot's
  `rss_mb`), at the same cost per run.
- **The watchdog** stops a Beat This! run within about 200 ms of its deadline: one attention node is that long. A
  150 ms budget stopped at 159 ms, and a 600 ms budget at 814 ms.

**Beat This! at 16 kHz.** The 16 kHz-limited input (resampled to 22.05 kHz) was compared with full-band 22.05 kHz
input, using F-measure at 70 ms over the interior. The test was 27 windows of 30 s across the 9 test tracks
(`export_onnx.py --measure-16k`).
- Beats: mean 0.972. The minimum was 0.66, on the orchestral track's opening.
- Downbeats: mean 0.926, minimum 0.67.

The vote needs `agree_runs` agreeing runs, so isolated misses don't apply. Feeding 22.05 kHz is still c8's call.

**Behaviour the spec left open.**
- **Silence** is the window's RMS in dBFS, with a full-scale square wave at 0.
- **Windows.**
  - Key and beats wait for a full window in the segment: 20 s and 30 s after a new segment.
  - A beats window waits for clean audio (no hole, no GAP frame).
  - With a MusiCNN head the tags window is 3.02 s (187 frames plus one hop). `MlTags.window_s` still reports 2.1.
- **The fast lane.** Its first task in a cycle always runs (its own budget caps it), so a stale p95 can't lock a
  task out.
- **The CPU guard** compares the 30 s and 60 s means, one step per window.
- **`stats.tasks[*]`** also has `silent` and `factor`.
- **hello** goes out even when stdin closes during the load, so S4.11's `< /dev/null` check prints it, then exits 0.
- **Config.**
  - A config frame whose model set changes reloads the models on the lane that sees it first.
  - A bad value keeps the starting point and is logged.
  - `DEFAULTS` equals show.yaml's `ml` (test_ml_worker checks this).
- **`--fake`** behaves exactly as `models.py`'s docstring says. hello reads `fake` for every configured model.
- **`--stats-every`** is for tests only. **`--bench FILE`** is not for production.

**`supervisor.py`** is in the job's file list. It is a stdlib, numpy, contract and protocol implementation of S4.3's
show_service side:
- `FrameFeeder`: 800-sample frames, drops, AFTER_DROP, GAP, segments. It costs 1.6 µs p50 and 6 µs p95 per block.
- `WorkerSupervisor`: the spawn, the config frame, the shape check, `bad_lines`, the stall, the stop sequence, the
  backoff and `healthy_s`, `failed` when the venv is missing, and `disabled`.

mllink (B8) can wrap it or follow it; S4.2 says builders don't import each other's modules, so that is c8's choice.

**Go-live check** (S4.11.1): `~/.venvs/sm-show-ml/bin/python -m show_service.ml.worker --models
~/.cache/screen-machine/models < /dev/null` prints hello, then exits 0.
- Every model is `ok` except `nsynth_bright_dark: missing`, which is fixed by item 1 above.
- `tasks` is all true, and nice is 19.

### S4.5 mllink.py and the wiring (B8)

```python
class MLFeed:                               # made by __main__ (like Rings); written on show-rx
    def __init__(self, *, fs: int = contract.ML_FS, frame_samples: int = contract.ML_FRAME_SAMPLES) -> None
    def write(self, stream, n0, t0_host_s, pcm, fs, flags) -> None     # = contract.OnAudio; returns at once while no fd
    def attach(self, fd: int | None) -> None    # any thread (MLLink): the worker's stdin write end (O_NONBLOCK), or None;
                                                # taken at the next write; a new fd starts a new segment
    def stats(self) -> dict                     # any thread: frames, bytes, drops, broken, segments, write_us_p95,
                                                # block_us_p95, attached (bool)
class MLLink:                               # Component "ml", tick_hz SLOW_TICK_HZ
    def view(self) -> contract.MLView           # any thread: swapped by reference
    def provides(self) -> dict                  # {"ml": self.view}
    def snapshot(self) -> dict                  # {"state", "why", "pid", "since_s", "restarts", "failures_in_row",
                                                #  "next_start_in_s", "last_exit", "log_tail": [<= 5 lines], "hello",
                                                #  "stats": <the last MlStats>, "feed": MLFeed.stats(), "lines": {type: n},
                                                #  "bad_lines", "votes": [<= 10 BeatVote], "cpu": {"worker", "service",
                                                #  "total"}, "problems": [show.yaml ml lines]}
                                                #  (S4.13 adds "cpu_limit", "stale_s", "rss_mb", "config_errors",
                                                #  "run_errors")
def attach(ctx: contract.BrainContext, **kw) -> MLLink      # kw: log_path (tests: python, a fake clock)
def beat_vote(msg: contract.MlBeats, clock: contract.ClockData | None, cfg: dict) -> contract.BeatVote   # pure
def decode_emb(b64: str, dim: int) -> np.ndarray                                                      # pure: float32
```

**MLFeed (show-rx: no locks, no waits).**
- **Resampling.** A mono mix goes through `soxr.ResampleStream(fs, 16000, 1, dtype="float32", quality="MQ")`.
  - It is rebuilt on a new fs, on `AUDIO_SEGMENT`, or on a new fd; the next frame is then a SEGMENT.
  - `AUDIO_GAP` blocks go through as zeros, so the index stays continuous, and flag every frame they reach
    `ML_FLAG_GAP`.
- **Time.** soxr's output is delay-compensated (ring.py's finding), so output sample m maps to input position
  `p = n_start + m × fs / 16000` (n_start: the receiver's index of the resampler's first input).
  - A frame's `t0` = `t0_blk + (p − n0_blk) / fs`, from the newest block.
  - These are the receiver's skew-tracked times, never a free-running count.
- **Writing.** A frame of `ML_FRAME_SAMPLES` (800) goes out as soon as it is full: one preallocated buffer,
  `struct.pack_into` for the header, one `os.write`.
- **A full pipe** (`BlockingIOError`): drop the frame, count it, and flag the next written frame `AFTER_DROP`; `n0`
  still advances.
- **A dead worker** (`BrokenPipeError` or another `OSError`): forget the fd and count `broken`; MLLink sees the exit.
- It never retries and never blocks.
- **Budget:** ≤ 40 µs p95 per 128-frame 48 kHz block, frame writes included (`-s` prints it).

**MLLink (show-brain).**
- **`configure`** resolves the `ml` section over the starting points; a bad value takes the starting point and adds a
  `problems` line, as the listener does. On a change it either sends a config frame (the same non-blocking write from
  show-brain; a full pipe retries at the next tick) or restarts the worker (S4.3). The frame carries only the keys
  the worker reads; one that can't be made keeps the last good section (a `problems` line). It never raises.
- **`start`:** unless it is disabled, spawn the worker with asyncio's `create_subprocess_exec`. stdin is our pipe's
  read end, closed in the parent after the spawn. The pipe is grown to 1 MB (about 16 s of audio; S4.13: the default
  64 KB was 1 s, and a starved nice-19 reader dropped frames). `ctx.ml_feed.attach(fd)` comes only after `hello`.
- **Lines.** An asyncio task reads stdout:
  1. `readline()` with limit `ML_LINE_MAX`, then `json.loads`;
  2. dispatch by `type`;
  3. swap in a new `MLView`.

  `ced_recent` and `emb_recent` keep 30 s for the view's `stream`; a result for another stream resets them.
  `decode_emb` runs once per tags line. Each `stats` line sets the view's `stale_s` (S4.13): per task,
  max(`results_stale_s`, `stale_periods` x its interval now), which every reader uses for freshness.
- **Beat votes.** Each `beats` line goes through `beat_vote(msg, ctx.clock(), cfg["beat_votes"])`, then the apply rule
  below. The vote goes into the view and the snapshot.
  - No clock grid (bpm 0, or gate `none`): `why` "no grid".
  - **Octave:** r = the clock's period / Beat This!'s period.
    - r within `octave_tol` of 2 → octave 2;
    - r within `octave_tol` of 0.5 → octave 0.5;
    - r within `octave_tol` of 1 → octave 1;
    - otherwise None ("the tempi don't relate").
  - **`conf`** = the share of the interior's beats within `match_ms` of the clock's grid at the voted octave
    (`beat_at(t) × octave` near an integer).
  - **The one and the bar** (octave 1 only):
    - each interior downbeat d whose `beat_at(clock, d)` is within `match_ms` of an integer k votes k mod `bar_len`;
    - when at least `one_agree` of them share one residue, `beat_is_one` = the latest such k;
    - `bar_len` = the worker's, if it is in `bar_lens`.
  - **Apply** when all of these hold:
    - `conf ≥ min_conf`;
    - the same vote (octave, bar_len, residue) came from `agree_runs` runs in a row; a new bar length (the meter)
      needs a one and `bar_agree_runs` (4) runs in a row (S4.13), and without a one it never applies;
    - it differs from the clock now: octave ≠ 1, or `bar_len` ≠ `beats_per_bar`, or
      `(beat_is_one − bar_anchor_beat) mod bar_len` ≠ 0;
    - the last apply is at least `cooldown_s` old.

    Then call `ctx.clock_correct(...)` with only the parts that differ. An octave goes alone; bar and one wait for the
    next run, under the new numbering. Never a phase, never a tempo.
- **CPU.** Every 10 s it reads `/proc/<worker pid>/stat` and our own: `snapshot()["cpu"]` = `worker`, `service` and
  `total`, in cores. A total over 0.8 for 60 s is logged (at most every 10 min) and shown. Since S4.13 it is also
  enforced, by degrading: while the 60 s total is over 0.8, the worker's cpu_max (a config frame) is 0.8 less
  show_service's 60 s share, within [`ml.cpu_floor`, `ml.cpu_max`], and the worker's own guard stretches its
  intervals; it goes back once show_service leaves room (`snapshot()["cpu_limit"]`).
- **`stop()`:** S4.3's stop sequence, then `ml_feed.attach(None)`.
- **Budgets:** the tick ≤ 3 ms p95; a tags line ≤ 1 ms.

**`__main__`, brain, bus and config (B8's seams).**
- **`__main__`:**
  - `self.mlfeed = mllink.MLFeed()`, imported lazily like ring (missing: left out). `on_audio` calls
    `mlfeed.write(...)` after `rings.write`, under the guard `ml.feed`.
  - `features.clock_fn = clock.snapshot` (an attribute B9 reads: swing and syncopation).
  - `Clock(publish=…, beat_thread=opts.beat_thread)`; at stop, `clock.stop()` after `rx.stop()`.
  - The BrainContext engine-side slots: `rhythm=features.rhythm`, `ml_feed=self.mlfeed`,
    `clock_correct=clock.apply_integer_correction`.
  - `BRAIN_MODULES = ("autopilot", "sources", "mllink", "structure", "listener", "director")`.
    `_component_kw("mllink")` passes `log_path=<state_dir>/ml-worker.log`.
  - `/api/v1/state` gains `ml` (the MLLink snapshot, also under `brain.components`); `features_stats` gains the
    medium lane's; the clock's stats gain the beat thread's.
- **`brain.py` `add`:** a provided key whose ctx slot holds a list is appended, not only `on_mode_change`, so
  `on_music_event` works the same way.
- **`bus.py`:**
  - `TYPES` gains `MUSIC_CONTEXT` and `MUSIC_RHYTHM`;
  - `WANT_ONLY` becomes `contract.WANT_ONLY + contract.WANT_ONLY_STAGE4`. music.rhythm goes only to clients that ask
    for it by name, so the renderer's traffic grows by music.context's 1 Hz only.
- **`config.py`:** `SHOW_BEAT_THREAD` (`on`, the default, or `off`: the activation inline on show-rx, as in Stage 3)
  and `--beat-thread on|off` → `Options.beat_thread`. An Options built in code has it True. **Changed at
  integration (S4.12): off is the default**, in load_options and in code, because the thread measured worse in the
  real process.

**Tests (B8):**
- MLFeed into an `os.pipe()`, read back by a test parser:
  - the shapes and segments;
  - times within 0.1 ms of the blocks' times;
  - GAP flags;
  - a full pipe drops and flags AFTER_DROP;
  - a closed pipe;
  - the cost.
- `beat_vote` on synthetic beats: each octave, a 3:2 tempo with no vote, bar and one, the agree and cooldown rules.
- MLLink against `python -m show_service.ml.worker --fake` (under `sys.executable`, a tmp models dir): hello, the feed
  attached, the view filled, a killed worker restarted on the backoff, stop.
- MLLink against a stand-in script that prints junk, then stalls: bad lines counted, then the stall restart.
- `__main__` with a fake mllink (the wiring, the guard); brain's list slots; bus `TYPES` and want-only.

**As built: mllink and the seams (c8's integration, 16 Oct; B8's piece).** Built to the spec above. Where it differs
or the spec was open:
- **It wraps B7's `ml/supervisor.py`** (c8's ruling on B7's open point; S4.2's import rule is waived for this one
  pair). `MLFeed` = soxr MQ + `supervisor.FrameFeeder`; `MLLink` = `supervisor.WorkerSupervisor` + the view, the
  votes, the CPU reading and the log rotation. The supervisor gained three things:
  - a `feeder=` argument (MLLink passes MLFeed's feeder, so the frames come from show-rx);
  - the log is opened `O_APPEND`, so MLLink rotates it in place (to `.1` past 1 MB); since S4.13 it is appended per
    run after a header line, never truncated;
  - between detaching the feed and closing our pipe end, a write already under way on show-rx is waited out
    (S4.13: `FrameFeeder.busy`, at most `FEED_RELEASE_S` 0.5 s, then `/dev/null` over the fd), so it never lands on
    a reused fd number. (As first built: a fixed 50 ms sleep.)
- **Stop.** `MLLink.stop()` returns the supervisor's stop coroutine, and brain.py now awaits a `stop()` that returns an
  awaitable (`STOP_AWAIT_S` 7 s, the stop sequence's 6.5 s worst case plus a margin; `Brain.stop`'s timeout went from
  5 to 10 s, `STOP_TIMEOUT_S`; scripts/ctl waits 15). Any other component is as before.
- **The view goes neutral at once** on any state but `running` (no ced/tags/key, empty `ced_recent`/`emb_recent`, no
  hello), so every reader degrades the moment the worker goes, and comes back with its first lines.
- **`beat_vote`** adds two `why`s: "too few beats" (fewer than 4, or no bpm) and, in the apply rule, "no clock here".
  An octave is matched relative to the ratio (`|r / o − 1| ≤ octave_tol`). A `bar_len` outside `bar_lens` votes with
  the clock's own bar but reports `bar_len` None.
- **The apply rule.** A run of `agree_runs` equal votes survives a cooldown (the first vote after it applies); an
  apply restarts the run. A new bar length carries the one it was voted with, and needs that one and
  `bar_agree_runs` runs (S4.13: the e2e's jazz track got a wrong 3/4 from two runs). **A vote that agrees with the clock in
  full** (with a one) is sent as a confirmation, at most every `cooldown_s`: the clock raises its `bar_conf` on an
  agreeing one, which it otherwise never hears (B9's open point). The snapshot counts `applied` and `confirmed`.
- **`MLFeed.stats()`**: frames, bytes, drops, broken, segments, attached, blocks, rebuilds, `block_us_p50/p95` (one
  timing per write call, frames included; no separate `write_us`).
- **The snapshot** has S4.5's keys plus `applied`, `confirmed`, `over_0_8_for_s`, `stream`, `ages` and `stats_ms` (tick
  and line p95). The counters refresh at 1 Hz; the log tail and the cost percentiles every 10 s.
- **`__main__`** as specified, plus `/api/v1/state` gains `rx_ms` (show-rx's own cost per `on_audio` block and per
  `on_tick`: p50, p95, max, n over the last 6000) and `threads` ({name: native id}, for per-thread CPU from
  `/proc/<pid>/task/<tid>/stat`). `ml` is the MLLink snapshot (null without the brain or mllink). The MLFeed exists
  only with the brain: with `--no-brain` there is no worker.
- **Tests:** `tests/test_mllink.py` (14): the feed into a real pipe (the receiver's skewed times through soxr within
  0.1 ms, the sine itself, segments on AUDIO_SEGMENT and a new rate, GAP flags, a full pipe, a dead reader, the cost),
  `beat_vote` (each octave, 3:2, wrong phase, no grid, too few beats, bar 3 and 7), the apply rule (agree_runs,
  cooldown, only what differs, an octave alone, a new bar length, confirmations), configure's problems, and MLLink
  against `--fake` (hello, the feed attached, the view filled, the votes, CPU, a SIGKILL restart with the view neutral
  meanwhile, `enabled: false` and back, the awaited stop) and against a missing venv. test_main (the wiring with a fake
  mllink and structure, the guard, the feed after the ring, the awaited stop in order, `python -m show_service` with
  the brain: `ml` failed under a tmp HOME), test_brain (list slots, an awaited stop and one that overruns), test_bus
  (types, want-only), test_config (`--beat-thread`).
- **Cost:** `MLFeed.write` 6 µs p50, 27-33 µs p95 per 128-frame 48 kHz block alone (`test_mllink.py -s`, niced, the
  render running); in the real process (wall, under the GIL) 23 µs p50, 270 µs p95.

### S4.6 The show-rx lanes: dsp's medium lane and the clock's show-beat (B9)

**dsp.py gains:**
```python
Features.clock_fn: Callable[[], ClockData | None] | None = None   # set by __main__ (swing, syncopation)
Features.rhythm() -> contract.RhythmData | None     # any thread: the latest medium frame, swapped by reference
Features.chroma12() -> np.ndarray | None             # the latest frame's harmonic chroma (12,), max-normalised; None
                                                     # under the harmonic floor or when older than 0.5 s
```
**The medium lane** (§4). One frame per 100 ms of stream time, at the hop that crosses each multiple of fs/10, inside
`process`, from the analysis ring (`RING_N` grows to fit the frame):
1. **Spectrum:** an `N_M`-point Hann FFT, N_M being the power of two nearest 0.17 s (8192 at 44.1 and 48 kHz),
   magnitudes up to 8 kHz.
2. **HPSS-lite, causal** (Fitzgerald's median filtering, made causal):
   - H = the median over the last 9 frames per bin (0.9 s, past frames only);
   - P = the median over 17 bins in this frame;
   - mask M = H² / (H² + P² + ε); harmonic = |X|·M, percussive = |X|·(1 − M).
3. **`chroma12`:** the harmonic magnitudes from 55 Hz to 5 kHz, each bin to its nearest semitone (A4 = 440 Hz) and
   pitch class, summed, max-normalised (C = 0). `chroma12_avg` = its mean over the last 1 s.
4. **`percussive_ratio`** = ΣP² / Σ(H² + P²) over the last 1 s (30 Hz-8 kHz).
5. **`harm_change`** = the mean over the last 4 s of 1 − cos(c_t, c_{t−0.3 s}), on the unnormalised harmonic chroma.
   Frames under the harmonic floor count 0.
6. **Onsets:** the fast lane's SuperFlux per hop, peak-picked (a local max over ±3 hops, above 1.5 × the median of the
   last 1 s plus a floor).
   - `onset_rate` = onsets per second over the last 4 s;
   - `density` = that rate through the dsp's own p05/p95 normalisation, × gate.
7. **`pulse_clarity`:** the autocorrelation (by FFT, mean removed) of the per-hop SuperFlux over the last 6 s; the max
   over lags of 0.25-1.5 s divided by lag 0, clamped 0..1. It needs no clock.
8. **`swing`**, while `clock_fn()`'s gate is `beat` or `dmx`:
   - take the onsets of the last 8 beats whose beat phase φ = frac(beat(t)) is in [0.4, 0.8];
   - with at least 4 of them, swing = median φ / (1 − median φ), clamped 1..3;
   - otherwise None.

   Beat positions use contract.ClockData's formula; dsp never imports clock.py.
9. **`syncopation`**, with the gate `beat` or `dmx`:
   - a 16th grid over the last 4 bars (`beats_per_bar × 4` steps from `bar_anchor_beat`), each step's mean onset
     strength;
   - metrical levels: the downbeat 0, other beats 1, 8ths 2, 16ths 3;
   - syncopation = the share of the pattern's strength on levels 2-3 whose next stronger step carries less than half
     of it, 0..1;
   - otherwise None.

   The same 16-step pattern is §9.5's bar pattern, for a later `music.pattern`.
10. **`low_db`** = 10·log10(slow_sub + slow_bass) + 3.01, from the hit path's envelopes (dB re a full-scale sine,
    floored at −120): the 20-250 Hz level that structure's build and drop rules read.
11. **`mfcc13`** = the DCT-II (orthonormal) of the fast lane's 40 log-mel bands, c1..c13, mean over the last 1 s:
    structure's fallback descriptor.
- **Publishing.** `tick` publishes `{"type": "music.rhythm", "data": RhythmData}` once per new medium frame (at most
  10 Hz). When the source stops (the fade), it stops; a consumer treats a frame over 1 s old as stale.
- **Constants.** Window lengths and floors stay dsp constants, like the rest of dsp: they are DSP internals, not
  behaviour thresholds. The module docstring gets every formula.
- **Budget:** a medium frame ≤ 0.5 ms p95 unloaded (≤ 1 ms under the render). The hop's existing p95 must not move.
  `stats()` gains `medium_ms_p50/p95` and `medium_frames`.
- **harmony.py needs nothing.** `__main__` already passes `features.chroma12()`, so music.harmony now comes from audio
  when no piano plays, and show.state's `global.music.chroma12` carries the audio chroma (§11.1).

**dsp's medium lane, as built (B9, 16 Oct; not committed).** dsp.py's docstring has every formula. Where it differs
from or adds to the spec above:
- **Spectrum.** At 44.1 and 48 kHz the 0.17 s window is first halved in rate: [1 3 3 1], every other sample, with its
  gain and droop divided back out of |X|². The FFT is then 4096 points, with the same window and the same 5.9 Hz bins
  up to 8 kHz. Below 40 kHz there is no decimation. This halves the FFT's cost; the chroma is identical.
- **HPSS.** As specified. H is computed in float32 (each stored |X|² floored at 1e-30, so nothing goes denormal), as 3
  groups of 3 kept sorted, with med9 = med3(the groups' max low, med3 of the middles, min high). P comes from scipy's
  private 1-D rank filter, checked against `median_filter` at import, with the public function as the fallback.
- **harm_change.** Without a lock it is the specified time-based rule. While the clock's gate is beat or dmx it is
  beat-synchronous: each beat's chroma is the sum of its frames, d compares a beat with the one before it, and the
  mean is over the beats that ended in the last 4 s (at least 2, else the time-based value). A clock epoch or a lost
  lock restarts it. The contract comment on `RhythmData.harm_change` still describes only the time-based rule.
- **Onsets.** There is one median per frame (the last 1 s), floor `ONSET_FLOOR` = 20 SuperFlux units. Pink noise's
  flux p99 is about 22, so noise gives no onsets.
- **pulse_clarity** runs on the flux summed over pairs of hops (94 Hz; lags to 11 ms). It reads 0 when that
  envelope's RMS is under `PULSE_FLOOR` (2.0): a held chord's beating otherwise read 0.55.
- **Cadence.** pulse_clarity is computed on even frames, swing and syncopation on odd ones (5 Hz each), and each
  value is held in between. They are None from the first frame without a lock, and the first locked frame computes
  them at once. Swing and syncopation share one plain-Python pass over the recent onsets.
- **swing** follows the spec's formula, so a 16th-note hat at phase 0.75 also falls in [0.4, 0.8]: straight 16ths
  read above 1, and off-beat 16ths alone read 3.
- **density** is a new row (onset_rate) in dsp's own rolling percentiles, pushed at 25 Hz.
- **chroma12()** is stale 0.5 s after the tick that published its frame (tick's clock). It is a new array per
  frame.
- **Publishing.** tick builds RhythmData. process only computes numbers, so it allocates no buffers per block.
- **Cost.** Measured on PC22 under the 4K render at nice 19, in-process A/B on drums + chord + noise:
  - the medium frame: p50 0.42-0.47 ms, p95 0.68-0.77 ms (0.47-0.50 / 0.75-0.85 with a locked clock);
  - the whole dsp path: 6.08 % → 6.64 % of a core (6.75 % locked);
  - hop p50: 0.247 → 0.239 ms; hop p95: 0.403 → 0.432 ms median of 4 runs, while runs spread 0.34-0.50;
  - the fast lane alone: p95 140-159 µs before, 139-151 µs after.
  hop_ms leaves out the medium frame's own time (that is in medium_ms); block_ms keeps everything. No unloaded host
  was available.
- **Tests** are in `tests/test_dsp_medium.py` (21). The README's B9 row names `tests/test_dsp.py`, so that file was
  left alone. Its Stage-3 line `assert f.chroma12() is None` (in `test_shapes_are_exactly_the_contract`) now fails
  and should become a shape check.

**clock.py: the activation on its own thread.**
```python
Clock(..., beat_thread: bool = False)         # True: show-beat (SHOW_BEAT_THREAD=on). False, the default since S4.13
                                              # as config.Options: inline on show-rx, exactly as in Stage 3
Clock.stop(timeout: float = 1.0) -> None      # joins show-beat (a sentinel); any thread; idempotent
```
- **The split.**
  1. show-rx keeps the AGC, soxr and hop assembly. It copies each full hop and puts it on a `queue.SimpleQueue` (whose
     `put` never blocks or waits) as `(epoch, seq, hop, <the frame's time inputs>)`.
  2. **show-beat** (a daemon thread named "show-beat") `get`s it, runs `act.process_hop(hop)`, and appends
     `(epoch, seq, a, …)` to a `collections.deque`.
  3. show-rx drains that deque (`popleft` until empty, never waiting) at the start of every `process` and `tick`, and
     runs the rest of `_frame` (peaks, tempogram, estimator) with each `a`, in `seq` order.

  Clock state never leaves show-rx. The activation object never leaves show-beat.
- **Resets.** `_lane_reset` bumps `epoch` and puts a reset marker. show-beat calls `act.reset()` when it reaches it, and
  results from an older epoch are dropped.
- **Back-pressure.** outstanding = `put_seq − show_beat.done_seq` (each written by one thread only).
  - Over `BEAT_Q_MAX` (50 frames, 0.5 s), show-rx drops hops (`beat_drops`).
  - Before the first hop it sends again, it puts a reset marker: madmom never sees a splice.
  - The tempogram's existing gap fill covers the missing frames by their times.
- **Times.** Frame times are computed on show-rx at put time (as `_frame` does now), so a result's time is the sound's,
  however late it comes back (at most a tick).
- **Measure** (`test_clock.py -s`): show-rx's block cost, p95 and max, with and without the thread, under the render.
  The e2e must still pass with it on: a lock at 124 ± 2 within 15 s, pulse edges median < 3 ms.
- `stats()` gains `beat_thread: {"on", "queued", "drops", "lag_ms_p95", "act_ms_p95"}`.

**As built: the clock (B9, 16 Oct).** clock.py's module docstring has the full spec. Built as above, except:
- **Back-pressure** counts hops put minus hops drained (both counters are show-rx's), so one bound covers the queue and
  the result deque. A reset marker comes back through the deque in order: at a splice, the peak picker restarts there
  too. Results of an older epoch are counted (`beat_stale`).
- **Degrade.** If show-beat dies, or has exited after `stop()`, show-rx runs the activation inline (`beat_thread_died`,
  `beat_inline_after_stop`). A Clock that is garbage-collected stops its thread.
- **Extras.**
  - `Clock.wait_beat(timeout=5.0) -> bool` blocks until show-beat has caught up. It is for tests and offline feeds
    only; test_clock.py's feeders call it, so every existing test now runs on the thread.
  - `stats()` adds `block_ms {p50, p95, max}` (show-rx's cost per `process`), `beat_thread.act_ms_p50` and `act_errors`.
- **Frames.** They are identical either way: test_clock_thread.py checks every count and the published line, inline
  against threaded, on the lock loop and the breakdown. Threaded, a frame is published at the next `process` or
  `tick`, a few ms later.
- **Integer corrections** (§5.3; the clock's side of mllink's votes).
  - Each call is checked: whole numbers, `bar_len` 2-16, and an octave of exactly 1, 2 or 0.5.
  - It is gated. It needs a confirmed grid. A one that precedes the last numbering break (a new grid, an octave change
    or a re-lock), or that lies more than a bar past the upcoming beat, is stale. A break drops a waiting correction,
    and so does a human "one".
  - It switches at the upcoming beat that starts a bar under the new hypothesis: `((K − one) × octave) mod bar_len ==
    0`. For octave 2 with `bar_len` 3, the old code switched mid-bar.
  - A correction that misses its bar line expires and is never applied off one. An out-of-range octave is refused, and
    nothing else moves.
  - Any accepted call holds the clock's own octave check off for `SLOW_OCT_HOLD_S` (30 s).
  - `stats()["last_correction"]` is `{one, bar_len, octave, result, at_beat}`, and each outcome is counted as
    `corr_<result>`.
- **Measured** on PC22 with the 4K render running (load about 3), niced. Each run fed 124 bpm drums in real time:
  128-frame 48 kHz blocks with 10 ms ticks, madmom 2 nets, skipping the first 1-2 s. All figures are in ms. "Block" is
  show-rx's work per block: `process` plus the ticks it runs.

  | show-rx block | inline: p95, p99, max | threaded: p95, p99, max |
  |---|---|---|
  | clock only (30 s) | 2.39, 3.36, 6.45 | **0.33, 0.75, 2.98** |
  | dsp + clock, as on_audio/on_tick (20-30 s, 4 runs) | 2.25-2.80, 3.10-3.85, 7.1-10.6 | **1.19-1.52, 2.23-2.74, 3.9-8.4** |
  | blocks longer than their 2.67 ms (dsp + clock) | 2.1-6.1 % | 0.3-1.1 % |

  - **Per stage** (dsp + clock): `clock.process` p95 fell from 2.05 to 0.22 ms, so a madmom frame no longer rides on
    show-rx. What is left is the GIL: while a madmom frame runs into the next block, features.process p95 rose from
    0.61 to 0.79 ms and the ticks' p99 from 0.64 to 1.07 ms. A switch interval of 1 or 0.5 ms didn't change that (p95
    1.52 and 1.37 ms), so don't set one.
  - **show-beat:** a frame takes p50 1.0-1.1 ms and p95 2.0-3.4 ms; from put to drained, p95 is 5.1-5.5 ms; no drops.
  - **CPU:** the thread costs **+0.015-0.025 core**. In alternating runs, the clock went from 0.167 core inline to
    0.18-0.19 threaded. Pinned to one core the difference is +0.004-0.014, so most of it is cross-core cache and
    wake-ups. That is against S4.2's "the same cost" (c8's budget).
  - The e2e was not run here (CLAUDE.md rule 6); c8 runs it at integration.
- **In the real process (c8's integration, 16 Oct; S4.12) the thread is off by default.** With every thread running
  (show-bus serving a renderer, show-brain with structure and the director, show-artnet) and the ML worker beside it,
  show-beat cost **0.17-0.19 of a core** against about 0.05 for the same activation inline on show-rx: every BLAS
  call in madmom's LSTMs hands the GIL over and back. It also made dsp's hop p95 2-3x worse (1.3-1.5 → 3.5-3.7 ms)
  and on_tick's p95 2x worse, while `on_audio`'s p95 stayed the same (2.6-3.0 ms either way). With it on,
  show_service + the worker measured 0.80 and 0.84 of a core, over the owner's 0.8; off, 0.68 and 0.69.
  `SHOW_BEAT_THREAD=on` still runs it (test_clock_thread.py keeps it tested); a separate process would be the real
  fix (B9's note), if show-rx's block time ever needs it.

**Tests (B9):**
- The medium lane on synthetic audio:
  - a held C major chord: chroma peaks at C, E and G, and harmony finds the key from audio;
  - drums only: percussive_ratio > 0.7; pads only: < 0.3;
  - a click track: pulse_clarity > 0.8; noise: < 0.3;
  - with a fake clock, a swung (2:1) loop reads swing 1.8-2.2, and straight 8ths about 1.0;
  - a chord change every 2 s: harm_change above a steady chord's;
  - rhythm at 10 Hz; 44.1 kHz input works; the cost.
- The clock: the threaded clock locks like the inline one on the existing loops; a reset mid-run; back-pressure drops
  (a slow fake activation) recover; `stop()`.

### S4.7 structure.py (B10)

```python
def attach(ctx: contract.BrainContext, **kw) -> Structure
class Structure:                                  # Component "structure", tick_hz BRAIN_TICK_HZ (10)
    def context(self) -> contract.MusicContext | None   # any thread: the latest music.context body
    def provides(self) -> dict                    # {"music_context": self.context}
    def snapshot(self) -> dict                    # {"context", "source", "sections": [this track's, no vectors],
                                                  #  "novelty": [[t_s, v], ...] (the last 120 s at 1 Hz), "threshold",
                                                  #  "boundaries": [<= 20 {t_s, novelty, snapped, kind: confirmed |
                                                  #  predicted | drop | track, missed}], "build": {...},
                                                  #  "events": {kind: n}, "gobo": {"steps", "skipped_cut",
                                                  #  "skipped_bar", "scheduled", "director"},
                                                  #  "stats": {"tick_ms_p95", "second_ms_p95"}}
    def persist(self) -> None                     # nothing: sections start cold (§11.2)
def novelty(vectors: np.ndarray, recent: int, before: int) -> float          # pure (§6.1)
def genre_reading(parents: dict | None, beliefs: dict | None, ced: dict | None,
                  cfg: dict, state: dict) -> contract.GenreReading          # pure but for its small hysteresis state
```
**Inputs** (each optional):
- `ctx.ml()`: tags, embeddings, CED, key;
- `ctx.rhythm()`, `ctx.features()`, `ctx.clock()`, `ctx.harmony()`, `ctx.piano()`;
- `ctx.room()`: the Sonos now-playing member that show.yaml `situations.sonos.now_playing` names (code knows no member
  name);
- `ctx.situations()`: `talking`, `sonos_playing`;
- `ctx.beliefs()`.

**Every second** (on the tick that crosses a whole second):
1. **The descriptor.**
   - With the worker: the newest unseen embedding in `ml.emb_recent` for the measured stream, fresh within the view's
     `stale_s` for tags (at least `ml.results_stale_s`; S4.13) (`source: ml`).
   - Otherwise (`source: dsp`): [chroma12_avg L2-normalised × w_chroma, mfcc13 z-scored over the last 60 s × w_mfcc]
     (`structure.dsp_weights`).
   - A switch of source or stream clears the novelty history: the vectors are in different spaces.
2. **Novelty** (§6.1) = 1 − cos(the mean over the last `recent_s`, the mean over the `before_s` before that).
   - A **candidate boundary** is a local maximum over ±`peak_s` that exceeds both `novelty.min[source]` and
     median + `k_mad` × MAD of the last `history_s`.
   - It is **confirmed** once the curve has fallen for one second after the peak: about 5 s after the change (§6.1:
     3-5 s).
3. **The boundary's time.**
   1. τ = the second in [peak − 6 s, peak − 2 s] where 1 − cos(v_s, mean(v[s−4..s−1])) is largest.
   2. It snaps to the nearest bar line (clock gate `beat` or `dmx`, and bar_conf ≥ `grid_conf` 0.5); else to the
      nearest beat (gate not `none`); else it stays unsnapped.
   3. A boundary within `min_section_bars` of the last one (`min_section_s` without a grid) is dropped, unless it is a
      track change.
4. **Identity.** `identify_after_s` into a section, its running centroid (leaving out its first and last 2 s) is
   compared with this track's earlier sections.
   - The best cosine ≥ `repeat_sim[source]` makes it a repeat: `repeat_of`, and the same `letter`.
   - Otherwise it gets the next new letter (A, B, …; after Z it stays Z).
   - `chorus` marks the sections of the letter that maximises count × mean energy, among letters seen at least twice
     (§6.1: "the most-repeated high-energy cluster").
5. **Known repeats.** A repeat whose first occurrence had a bar length (`expected_bars`) is predicted to end at its start
   + `expected_bars` bars.
   - When that line is at most `predict_lead_bars` ahead (and at least `events.min_lead_s`), a `section_change` goes out
     with `predicted: true` and `at_ms` = the line.
   - A confirmed boundary within 2 bars of a predicted one is the same boundary: no second event.
   - A predicted line with no boundary within 2 bars counts as `missed` in the snapshot.
6. **Track changes** (§6.1):
   - (a) the Sonos member's track (its `track_id`, else title + artist) changes while `sonos_playing` is true: at once,
     `via: sonos`;
   - (b) a silence (gate < `track.gap_gate`, or the momentary loudness lufs_m ≤ `track.gap_lufs_m`: S4.13) of at least
     `track.gap_s`, then a boundary within `track.window_s` of the sound's return, with a jump:
     - the clock's bpm off by more than `tempo_jump` (and not ×2 or ×0.5);
     - another key (strength ≥ `track.key_min_conf` on both sides) that isn't the same, parallel or relative key
       (S4.13; music.context's key, itself held);
     - or another leading genre parent of the last `track.parent_window_s` of tags, leading the next by
       `track.parent_margin` (S4.13: a near tie is no evidence);
   - (c) without a gap: a novelty peak of at least `spike_k` × the threshold (`spike_k` null: off), with
     `track.spike_jumps` (2) of those jumps at once, the genre by genre_reading's smoothed leader (S4.13: an in-song
     section change can move the key or one window's parent, not two things at once).

   A change clears the section memory, restarts letters at A, adds 1 to `track.id`, sets `track.changed` in the next
   context only, and starts a new section.
7. **Genre and the smoothed numbers.**
   - Genre: `genre_reading`, S4.8's rule.
   - dance, arousal, engagement, bright, acoustic and vocal (= voice_p) are EMAs with τ `tags_tau_s`, frozen while CED
     music is under `genre_map.freeze_below_music`; so are the instruments' scores (S4.13: `instruments` is the top 5
     of them, not one window's top 5).
   - **Key:**
     - the piano's (music.harmony, source midi) while it plays: `key_via` midi;
     - else the worker's, at strength ≥ `key_min_strength`: `ml`;
     - else music.harmony's: `audio`.
     - With hysteresis (S4.13; `structure.key_hold`): the worker stays the source until its strength is
       `strength_margin` under the minimum; another key (or none) shows only after `hold_s`; the piano's, and the
       first key after none, show at once.
   - Another ML stream (a test track, then the room) clears the genre, the numbers, the instruments and the key
     (S4.13).
8. **Publish** `music.context` (`MusicContext`), 1 Hz.

**Every tick (10 Hz): the phases** (§6.2), from the rhythm frames and features since the last tick. Builds and drops
need the clock gate `beat` or `dmx`. Music without a grid gets none: that is §6.2's jazz and classical, decided by
evidence, not by genre.
- **build:** over the last `build.window_s`, the least-squares slopes of lufs_m (dB/s), brightness (/s) and onset_rate
  (/s) are all above `build.min_slope`, while low_db's slope stays ≤ `low_max_slope_db_s`.
  - `build_progress` = the elapsed share from the build's start to the predicted drop line; without a line, the rise in
    loudness so far / `full_rise_db`.
  - Event `build` at its start, once per build.
- **The drop prediction**, while a build holds:
  - the line: the next 16-bar line (from `bar_anchor_beat`) when phrase_conf ≥ `phrase16_conf` and it is at most 16
    bars from the build's start; else the next 8-bar line;
  - `drop_eta_beats` = the beats to it;
  - `drop_armed` (`at_ms` = the line, with `eta_beats`) goes out once the line is at most `arm_beats` ahead and at least
    2 beats and `events.min_lead_s` ahead;
  - if the line moves (a clock epoch), a new `drop_armed` replaces it;
  - structure schedules a GOBO step at that line;
  - S4.13: the line is called off with a `drop_armed` whose `eta_beats` is None and `detail` says why: `disarmed` when
    the build ends more than `drop.disarm_bars` before it (or a new build or track starts; its GOBO is cancelled),
    `missed` when it passes with no drop.
- **drop:** within `drop.within_beats` after a build (or of an armed line), low_db rises at least `low_jump_db` and
  lufs_m at least `lufs_jump_db` above the build's last 2 s mean.
  - Event `drop` at the time heard, snapped to the nearest beat within `drop.snap_beats` (¼ beat).
  - A drop starts a new section at once, with no novelty wait. A confirmed boundary within `same_bars` of it later is
    the same boundary.
  - A drop at an armed line steps no second GOBO; an unarmed one steps it at the first bar line `events.min_lead_s`
    ahead (S4.13; it was at once, a second or more off the beat for the screens).
- **breakdown:** low_db at least `low_drop_db` under its 30 s median and percussive_ratio < `perc_ratio_max`, while the
  gate stays ≥ `min_gate`, for `for_s`. Event `breakdown` at its start. Left once that has failed for `leave_s`
  (S4.13: 4 s, never faster out than in; it was 1 s, and the lamps pumped on sparse sections with kick fills).
- **peak:** energy (EMA 4 s) ≥ `peak.energy_min` for `peak.for_s`, after a drop or in a chorus section; out after
  `peak.leave_s` of failing (S4.13).
- **outro:** the slope of lufs_s over `outro.window_s` is at most `max_slope_db_s`, with energy < `energy_max`; in after
  `outro.for_s`, out after `outro.leave_s` (S4.13).
- **calm:** otherwise.
- The phase word goes into `section.phase`. `drop` holds for `drop.hold_bars`, then becomes `peak` or `calm`. The word
  holds at least `phase_dwell_s` (2 s), but drop, build and breakdown (each announced by an event) show at once
  (S4.13).
- B10 ports the detectors from FastLED's audio suite (MIT) to numpy, as §6.2 says. Where that port has its own
  constants, they replace these starting points, and B10 sends c8 the values for show.yaml.

**Events** (`room.event`, `contract.MusicEventData`, kinds `MUSIC_EVENT_KINDS`). Each is published when it is decided,
then handed to every `ctx.on_music_event` callback (on show-brain, each guarded).

| kind | at_ms | extra keys |
|---|---|---|
| `section_change` | confirmed: the next bar line ≥ now + `events.min_lead_s` (else the next beat, else now + the min lead); predicted: the predicted line | `section_id`, `letter`, `repeat_of`, `predicted`, `boundary_ms`, `bar`, `beat` |
| `build` | its start (past) | `phase`, `build_progress` |
| `drop_armed` | the predicted line | `eta_beats` (None: the line is off, `detail` disarmed or missed), `section_id` |
| `drop` | when heard (past, by up to a beat) | `section_id`, `strength` |
| `breakdown` | its start (past) | `section_id` |
| `track_change` | when it changed | `track_id`, `via` |

**GOBO** (artnet_out's `section_change`, the screens' scene change, §9.5). **Since S4.13: structure steps it only while
the director is neutral** (`ctx.vibe()` None, §9.5's P0 path); while a vibe plays the director owns it and steps it
with every look that lands (S4.8), so the vibe's change_looks, Keep this look, relaxing, the late rule and the peak
tokens hold the screens too. A step structure leaves to the director is counted (`gobo.director`). Neutral:
- Structure steps it once per section change, at `at_ms`, through `ctx.loop.call_at(at_s)` (the loop's clock is
  time.monotonic). It also steps it at an armed drop line, and for an unarmed drop at the first bar line
  `events.min_lead_s` ahead.
- Never while any `RoomView.screens[*].cutting` (§8.6: skipped and counted), and never twice within one bar.
- `structure.gobo_on_sections: false` turns it off.
- (As first built, structure stepped it on every section and drop whatever the vibe, and the director skipped the looks
  structure caused: the screens changed scene mid-piece in Dinner and Notes, and Keep this look didn't hold them.)

**Budget:** a 10 Hz tick ≤ 1 ms p95; the 1 Hz work ≤ 3 ms p95 (1280-d vectors, at most 64 sections).

**Tests (B10):**
- novelty on synthetic descriptor sequences: an A/B/A/B pattern gives boundaries within 1 s of the truth before
  snapping, letters A B A B, `repeat_of`, and the chorus;
- snapping to a fake clock's bar lines; a predicted boundary landing on its line; the minimum section length;
- a track change by Sonos (a fake RoomView), and by a gap and a tempo jump;
- the dsp fallback (a chroma change);
- build and drop on a synthetic riser and drop (loudness, brightness and onsets rising, then a bass jump), with
  drop_armed at least 2 beats ahead; breakdown; no build without a grid;
- GOBO timing and the cut rule;
- the genre rule (S4.8);
- `music.context`'s shape: every key, JSON-safe.

**As built (B10, 16 Oct).** `show_service/structure.py`, built to the spec above; its module docstring has every
rule. `tests/test_structure.py` has 45 tests. Where the spec was open, or where testing showed it needed a rule, the
code does this:
- **Descriptor source.** It is `ml` whenever the worker runs (`MLView.state` is `running` and its tags task can run),
  else `dsp`. It does not switch on per-second freshness, which would clear the history across every silence and lose
  the before-window that a gap's track change needs.
  - It takes every unseen embedding that is fresh, oldest first, not only the newest, so none is lost to the beat
    between the two 1 Hz clocks.
  - Vectors are stamped at their window's centre: ml at t − `ml.tasks.tags.window_s`/2, dsp at t − 0.5 s.
  - dsp's z-scored mfcc13 is L2-normalised before × `w_mfcc`, so the two weights mean what they say.
- **Three candidate rules added**, each found by a test:
  1. **The threshold's background.** The threshold is median + `k_mad` × MAD over the history *before* the peak's
     rise. Each earlier real boundary's own response is left out: τ to τ + `recent_s` + `before_s`, while the old
     section is still in the before-window. Without this, sections shorter than about 40 s lifted the threshold above
     every peak. Until there are 5 background values, `novelty.min` applies alone.
  2. **Local contrast.** The recent window must also differ from the `recent_s` vectors just before it, by the same
     threshold. A short blip leaves a 15 s plateau in the before-window; without this rule that plateau made a
     spurious boundary 8 bars later.
  3. **Evidence wins.** A confirmed boundary (or a drop, or a track change) cancels a predicted line that is still
     pending, and its GOBO.
- **Events.**
  - section_change carries `detail`: `section` (confirmed), `repeat` (predicted) or `track` (a track change's new
    section).
  - A drop starts its section **without** a section_change. The `drop` event carries `section_id`, so the director's
    token rules alone decide the cut.
  - A track change sends `track_change`, then `section_change` (detail `track`).
- **Track changes.**
  - Sonos counts while `sonos_playing` doesn't read false. situations reads unknown for `own_change_s` after the room
    itself touches Sonos, and the track id is still the truth then.
  - A post-gap boundary with no jump yet keeps checking until return + `window_s`. On a jump, that boundary's section
    becomes the new track's first (its letter is reassigned), with no second section_change.
- **Genre.**
  - `genre_reading` takes a keyword `now_s` (default `state["now_s"]`, else `time.monotonic()`).
  - `conf` is 0 when there is no word.
  - The jump test's "leading genre parent" was the raw argmax of the fresh tags, not the smoothed leader (τ 30 s is
    too slow for a 20 s window). S4.13: after a gap it is the clear leader of the last `parent_window_s` of tags (a
    near tie is no evidence); without a gap it is the smoothed leader, with two jumps needed.
- **Phases.**
  - A build starts at the ramp's foot: the last sample within 0.5 dB of the window's quietest `lufs_m`.
  - A build holds while `lufs_m`'s slope stays above half its start threshold (FastLED BuildupDetector's end rule).
  - At the drop line, the line is kept while a drop can still be read on it, so progress doesn't jump to the next
    8-bar line one tick before the drop.
  - Breakdown's 30 s median is frozen on entry. It was left after 1 s off its rule; S4.13: after `leave_s` (4 s).
  - Drop `strength` is FastLED DropDetector's impact blend: 0.40 loudness, 0.35 bass, 0.15 band change, 0.10 level,
    with a flux of twice the threshold = 1. FastLED's 2 s cooldown between drops applies.
- **No show.yaml change proposed.** FastLED's own thresholds work in per-frame (23 ms) relative-energy units: rise
  0.3, intensity 0.6, impact 0.75, bass 0.6, flux 0.5. They don't translate to S4.7's dB/s slopes, so show.yaml's
  starting points stand. The code's fallbacks equal show.yaml's numbers, and a test checks this. Since S4.13 the
  port's own numbers (the build end 0.5, the drop's impact weights, its band weights, the 2 s cooldown) are show.yaml
  `structure.fastled`, and the other musical numbers the code held (grid_conf, same_bars, ced_mean_s, tags_hold_s,
  listener_conf, track.key_min_conf, build.foot_db, drop.snap_beats, breakdown.leave_s) are show.yaml keys too.
- **ctx.music_context()** refreshes its `section` and `track` at 10 Hz, so the director sees phase, build_progress
  and drop_eta_beats each tick. Only the 1 Hz body goes on the bus.
- **Costs** (`-s`, niced, on this host as loaded): the 10 Hz tick p95 is 0.5-0.6 ms; the 1 Hz work p95 is 2.1-2.7 ms
  with 50-57 sections of 1280-d vectors. On three real test tracks through the dsp fallback (computed offline, with a
  locked fake clock), the tick p95 is 0.2-0.5 ms and the 1 Hz p95 is 1.3-2.3 ms.
- **Not run:** a real-music smoke through the worker, which isn't built yet. That is S4.11 step 3's check at go-live.

### S4.8 The consumers (B11)

**The genre rule** (`structure.genre_reading`). It is written here because situations, the card and the director all
read its result, `music_context.genre`.
1. The worker's `genre_parents` are smoothed (EMA τ `genre_map.tau_s`), and update only while CED music ≥
   `freeze_below_music`.
2. The leading parent changes only when a new one leads by `switch_margin` for `switch_dwell_s`.
3. **Its word** is `genre_map.parents[parent]`:
   - a vocab word;
   - or `{from_listener: [words]}`: the listener's belief genre if it is one of those words (state fresh or holding),
     else no word;
   - a parent not in the table gives no word.
4. **`conf`** = the leader's smoothed activation × the speech scale (1 at speech ≤ `speech_scale_above`, falling
   linearly to 0 at speech 1; speech is the max over `situations.talking.speech_words`, S4.13).
   - For `from_listener`, it is also × the listener's conf.
   - `via` is `ml` or `ml+listener`.
5. **No worker** (or no fresh tags): the listener's genre and conf (`via: listener`), as in Stage 3. Neither:
   `{word: None, conf: 0, parent: None, parent_p: 0, via: "none"}`.
6. When both have a word and they differ, the ML's wins (§8.9: mic classifiers rank above the LLM). `from_listener` is
   how the LLM's finer words come in. The director's room.direction genre follows this too (S4.13: as first built it
   read only the listener's word, so with AI listening off the screens never got the local genre).

**situations.py**
- **The classifier.** `Situations.classifier` is set from `ctx.ml`:
  `(stream) -> {"music", "speech", "conversation", "singing", "crowd", "applause", "silence"}`.
  - It is the mean of the `ml.ced_recent` readings for that stream (None: the measured one) with `t` in the last 3 s
    (S4.13: or the view's `stale_s` for ced, when the worker has stretched its cadence).
  - It is None when there are none. Its readings' `via` is `classifier`.
- **`talking`** reads show.yaml's new section `situations.talking` (`stream`, `min_prob`, `music_below`): true when
  speech ≥ min_prob and music < music_below. `music_below` (0.5) replaces the code's constant. Without a classifier it
  is unknown, as now. S4.13: "speech" is the max over `situations.talking.speech_words` (speech, conversation, crowd),
  for talking, quiet and the genre speech scale alike.
- **`quiet`**, with a classifier, is also true when music < `music_below` and speech < `speech_below`
  (`situations.quiet`), so a room with only noise above the gate reads quiet. Without one, as now.
- **`music_audible`** is unchanged: it already takes the classifier first.
- **`genre_in`** reads `ctx.music_context()["genre"]` first: the word is in `genres` and conf ≥ min_conf; a None word or
  a low conf reads unknown. Its evidence adds `via` and `parent`. S4.13: once confident it stays so until its conf is
  `situations.ced.hysteresis` under min_conf.
  - Without a context: the beliefs, as now.
  - Its source and rank stay `listener` (3).
- The vocabulary's descriptions lose "(P1)" and say what each word reads now.

**listener.py**
- **The CED gate** (`music_gate: classifier`, §7.5), for room-sound streams:
  - no fresh CED for the stream: "the music/speech check isn't running" (this replaces WHY_P1);
  - the latest reading's speech ≥ `listener.ced.speech_max`: "people are talking". S4.13: a reading's speech is the
    max over `listener.ced.speech_words` (speech, conversation, crowd): guests talking over music show up as
    conversation and babble while "Speech" stays low;
  - **the clip** is the longest contiguous run of seconds, within the last `clip_s`, whose CED reading says music ≥
    `ced.music_min` and speech < `ced.speech_max`. Each reading speaks for the last `ced.speaks_s` (1 s) of its window;
    the latest run wins a tie. S4.13: the run's last second is also in the next reading's window, so it counts only
    when that reading exists and passed (or doesn't reach back into it); otherwise the run ends a second earlier. It
    is cut from the ring with `rings.clip(stream, t0, t1)`;
  - a run shorter than `min_clip_s` is skipped: "not enough music without talking";
  - the gate is checked again just before the upload, as now.

  `music_gate: dsp` and `room_sound: false` keep their Stage-3 meanings.
- **The section call** (§7.3). On a confirmed `section_change` event (through `on_music_event`; never a predicted one):
  - one extra call is due at `boundary + section_call_after_s`;
  - it still waits at least `min_gap_s` after the last call, with at most one in flight; S4.13: and it slows with the
    cadence (relaxed, over budget, backed off): at least half the current interval after the last call started, and
    at most one section call per interval;
  - its clip ends at that time;
  - the user text gains the **segment map**: the sections the clip spans (id, letter, and from-to seconds within the
    clip).
- `InsightData.segment_id` = the section at the clip's end, from `music_context`, on every call.
- On `track_change`, the beliefs' two-reads rule lets the next verdict's genre win at once (§7.1).

**director.py** (§8.5). Everything below touches lamps and DMX only; screens get looks, words and events.
- **Inputs:** `ctx.music_context()` (section, phase, build_progress, drop_eta_beats, track), and the events through
  `on_music_event` (in `provides()`; brain.add appends it).
- **Section changes**, for a vibe whose `change_looks` is `phrase` (§8.5's grammar). With `time` or `between_pieces`, a
  section only shows inside the look.
  - A predicted `section_change` (a known repeat) lands a look change at its `at_ms`, with that letter's remembered look
    when there is one. min_bars doesn't apply.
  - A confirmed one whose `at_ms` falls more than `director.sections.max_late_bars` after `boundary_ms` makes no new
    look. The section shows inside the look: level, accent density and spread move by `express_gain` × (this section's
    energy − the last one's), within the vibe's ranges, within one beat.
  - Any other confirmed one lands a look change at its `at_ms`.
  - Keep this look, relaxing (quiet, talking) and a pending person's change also keep the look (and so the screens'
    scene).
  - Without sections, the Stage-3 rules (phrase lines and min_bars, time, between_pieces) carry on unchanged.
- **Section memory:** per track, letter → {palette, variation (the allocation), look_id}. A returning letter gets its
  look back. It is cleared on `track_change`, and starts cold (nothing is persisted).
- **Song changes.** When structure is present, `track_change` replaces the director's own test (sound back after
  `new_song_after_s` of silence), which stays as the fallback. The palette may move on at the next look change.
- **GOBO.** Since S4.13 the director owns it while a vibe plays: every look that lands calls `ctx.section_change()`
  once, whatever caused it (`section`, `repeat`, `drop`, `track`, or its own), and nothing it holds back does
  (change_looks time or between_pieces, Keep this look, relaxing, a late section, a drop without a token). structure
  steps GOBO itself only while the director is neutral (S4.7). (As first built, the looks structure caused didn't step
  it, because structure stepped it on every section whatever the vibe.)
- **Peak tokens.** The vibe's `peak_tokens_per_hour` (neutral 0: none) is a sliding 60-minute budget: at most that many
  spends in any 60 minutes.
  - Only a drop that comes within `director.peaks.after_build_s` of a build may spend one.
  - vibes.py drops `x-ui.not_built` from `peak_tokens_per_hour`; strobe keeps it.
  - S4.13: a line structure calls off (`drop_armed` with `eta_beats` None) releases a reservation (`disarmed`: no dip,
    no hit, no cut), or refunds the token its line spent (`missed`: the look landed on the line, as §5.4's predictive
    landing, but no drop came).
- **The build → drop grammar**, only while a vibe plays, beat_lock holds and the clock's gate is `beat`:
  - **during a build:**
    - the accent subdivision steps ½ → ¼ → ⅛ every `build.step_bars` bars (`step_bars_late` once build_progress ≥
      0.5);
    - saturation moves toward the vibe's high end by build_progress;
    - the lamps' roles converge on the accent colour by build_progress × `build.narrow`;
  - **`drop_armed` with a token available** reserves it:
    - the lamps dip to `build.dip_level` for the last `dip_bars` before the line;
    - at the line: a hard cut (transition `cut`) to a new look, and a unison hit on lamps and DMX for `hit_beats`;
    - the token is spent at the line;
  - **an unarmed `drop`:**
    - with a token: the lamps' hit at once, and the look cut (and its GOBO step) at the next bar line at least
      `structure.events.min_lead_s` away (S4.13: the screens' lead; it was 0.35 s);
    - without one: no cut and no hit; the drop shows inside the look (accent density and saturation up);
  - **`breakdown`:** the floor + `breakdown.floor_add`, motion × `motion_scale`, saturation × `saturation_scale` (−20 %),
    until the phase leaves breakdown.
- **room.direction:**
  - `section` = `{id: <structure's section id>, label: <the context's label>, phase: bars_in_section / expected_bars (0
    when unknown), bars_in_section}`; SectionRef keeps its 0..1 `phase`;
  - `density` = rhythm.density while it is fresh;
  - `genre` = music.context's genre (the local ML's: via `ml` or `ml+listener`, fresh, conf ≥ 0.6), else the
    listener's, else the vibe's (S4.13; the snapshot's `words_from` says which).
- **Logged:** every section-driven look change, every kept or expressed decision, and every token spend or refusal goes
  to director.jsonl with the music context.

**api.py** (one seam). `card()`'s hearing rule 1 takes `music_context.genre` first: a word with conf ≥ 0.6 gives
"<genre> · <the listener's first mood>", or the genre alone. Then the beliefs' rule, as now. The context comes from
`brain_snapshot["components"]["structure"]["context"]`.

**Tests (B11):**
- situations with a fake `ml` view: talking true, false and unknown; quiet by CED; music_audible by CED; genre_in from
  the context, then from the beliefs;
- the listener with fake CED:
  - the clip cut (talking in the middle: the longer run goes);
  - a run under min_clip_s is skipped;
  - the section call is due at the boundary + 8 s, and min_gap holds;
  - the segment map's text;
- the director with a fake context and events:
  - a predicted repeat restores its letter's look;
  - a late confirmed boundary keeps the look and moves the level;
  - GOBO only from the causes that are the director's own;
  - the token window;
  - the dip and the hit on lamps, **never on screens** (the screens-rule test is extended);
  - breakdown;
- the name scan over the new modules;
- the card line.

**As built (B11's director, situations, listener and RoomLink.patch; 16 Oct).** Built to the spec above; where it was
open, this is what the code does. vibes.py's `x-ui.not_built` and api.py's card line were outside this job; c8 did
both at integration (S4.12).
- **Two seams c8 must add** (until then the code degrades; **both added at integration**, S4.12):
  - `contract.BrainContext.room_patch: Optional[Callable[[str, dict], Any]] = None` (async (path, body) -> (status,
    dict): `RoomLink.patch`). `RoomLink.provides()` already offers it; brain.add ignores it, with a warning, until the
    slot exists. Until then the director's snapshot says "no room PATCH route here".
  - B8's brain.add must append to the `on_music_event` list. The director and the listener both provide it; today's
    brain.add would set the slot to the last one's method.
- **show.yaml keys the code reads with fallbacks** (c8's file; the same numbers as the code; **in the file since the
  integration**):
  ```yaml
  situations:
    ced:                                    # Stage 4: the CED classifier that talking, quiet and music_audible read
      window_s: 3                           # README S4.8: the mean of the readings in the last 3 s
      hysteresis: 0.05                      # starting point: a CED word that holds keeps holding until its number is
                                            # back past its threshold by this much
  director:
    screens:                                # the screens' palette (agreed with 2f and 04, 16 Oct)
      send_palette: true                    # false: the director never touches the screens' palette (hot-reloaded)
      palette_transition_s: 4               # starting point (the renderer's own default, LIVE_V1 §1.11)
  ```
- **RoomLink.patch(path, body, timeout)** is `put` with PATCH: (status, JSON object); `0` and `room_unreachable` when
  the room doesn't answer. It never raises (but for cancellation).
- **situations.py.**
  - `Situations.classifier` binds to `ctx.ml` at `start()` (or the first tick). Its reading is the mean of the CED rows
    for the stream (None: the one the worker measures) whose `t` is in the last `situations.ced.window_s`. It adds
    `stream` and `n` to the seven words. A test may still set its own classifier.
  - **Hysteresis.** A reading remembers what it said last (per word and params). While it holds, its thresholds
    relax by `situations.ced.hysteresis`:
    - `talking`: speech >= min_prob - h and music < music_below + h;
    - `quiet`'s CED path: music < music_below + h and speech < speech_below + h;
    - `music_audible`: music >= min_prob - h.
  - **`music_audible`.** With a fresh CED reading, CED decides (S4.3's table, §8.9): music, or Sonos playing with the
    mic over `dsp_min_gate` (`sonos+mic`), else False via `classifier`. Without a reading, the Stage-3 DSP guess runs
    exactly as before.
  - **`quiet`.** The gate decides first, as before. Over the gate, with a reading and both `music_below` and
    `speech_below` set, the CED path decides. With either unset, it reads False via `dsp`, as in Stage 3.
  - **Test streams.** With `stream` unset, the CED words read unknown when the classifier's stream is a test stream,
    unless `include_test` is set. Otherwise a test played from another room could switch this one.
  - **`genre_in`.** It reads music.context's genre while the context is fresh (3 s). The reading's `via` is the
    genre's (`ml`, `ml+listener`, `listener`); its evidence adds `via` and `parent`. A missing word or a low conf
    reads unknown. With no fresh context, it reads the beliefs, as before.
  - **Params.** `talking` gains `music_below` (section `situations.talking`); `quiet` gains `music_below` and
    `speech_below`. The snapshot gains `classifier` (on, window, hysteresis, the mean now) and `problems`.
- **listener.py.**
  - **Reasons.** `WHY_P1` is now `WHY_NO_CED` ("the music/speech check isn't running"; the old name is kept as an
    alias). New: `WHY_TALKING` ("people are talking") and `WHY_SHORT` ("not enough music without talking").
  - **The run.** Readings join a run while each is within its window (2 s) of the last, so a CED cadence the
    worker's CPU guard has stretched still counts as contiguous. The run is (first t - 1, last t], inside the clip_s
    window (S4.13: last t - 1 unless the next reading passed). The gate's thresholds are `listener.ced`; freshness is
    the view's `stale_s` for ced, at least show.yaml `ml.results_stale_s` (3).
  - **Section history.** From the events and from music.context: id, letter (filled in once named), start. Each
    call's user text gets the segment map when the clip spans a known section: "#12 A 0-9 s, #13 B 9-24 s".
    `segment_id` (and `section.segment_id`) is the section at the clip's end.
  - **The section call.** It is due at boundary + `section_call_after_s`, and takes precedence over the cadence call
    when both are due. One is pending at most (the newest boundary wins). It expires `cadence_s` after it was due.
  - **`track_change`** sets `BeliefState.track_pending`: the next verdict counts as a track change, so its genre wins
    at once.
  - **Logs.** Each verdict with a `genre.detail` also logs `kind: genre_detail`, beside the ML's word, parent and
    top styles. The snapshot gains `ced` (why, run, latest) and `sections`.
- **director.py.**
  - **Events.** They are queued by `on_music_event` and read at the start of the director's own tick, which follows
    structure's in the same beat. Context older than 3 s counts as "no structure".
  - **Phases.** An event's phase (build, drop, breakdown) holds for `director.sections.phase_hint_s` (1.5 s), until
    the 1 Hz context catches up.
  - **The silence song-test** runs only while there is no fresh context.
  - **Section changes.**
    - A confirmed one that isn't late, or a predicted one, is announced at its at_ms as `section` or `repeat`. If
      at_ms is under 250 ms away, the next bar line is used instead.
    - It replaces a pending phrase change. It never replaces a person's pending change, a pending `drop` cut or a
      pending `track` change: then the section is shown inside the look.
    - Late ones (and every section under `time` or `between_pieces`, Keep this look, or a pending person's change)
      are expressed: an offset in the vibe's u units, eased over one beat. It moves level, accent density and
      spread, and resets when a look lands. Nothing is expressed while relaxed.
    - Section energy is the mean of the smoothed features energy since the boundary, against the previous section's
      (or the 30 s before).
    - A confirmed boundary within 2 bars of a `drop` look's or a new song's boundary is that look's own: no second
      look and no expression. structure's own section for a new song (detail `track`) moves a pending `track` look to
      its line, where structure steps GOBO, so the look and the scene change land together.
  - **Memory.** A letter is remembered, the first time it is named, only if the look showing then landed in that
    section. So a late-expressed section doesn't borrow the previous section's look. On recall, a palette the vibe no
    longer lists isn't restored (the variation still is).
  - **Track changes.** One comes from a `track_change` event (its at_ms is the song's start), or a new `track.id` in
    the context with no event that tick (start = now - track.age_s); repeats of one id are ignored.
    - It clears the memory and sets the song change, so the palette moves on.
    - A `phrase` vibe on a clock grid gets a `track` look at the next bar, unless a person's change or a structure
      change is pending; that one then takes the song change.
    - A pending repeat loses its recall.
  - **GOBO.** As built, looks with reason `section`, `repeat`, `drop` or `track` didn't call `ctx.section_change()`
    (`gobo: false` in the log). S4.13: every look that lands steps it (`gobo: true`; `stats.gobo_steps`).
  - **Peak tokens.**
    - A deque of spend times, pruned to the last hour. A reservation counts as used.
    - Keep this look refuses a token.
    - A drop heard before its armed line releases the reservation and is treated as unarmed.
    - A drop within one beat of the armed line, before or after it, does nothing more.
  - **The grammar** is gated on a known vibe, `beat_lock`, a clock grid and gate `beat`.
    - The subdivision steps every `step_bars` from the build's start bar, or every `step_bars_late` while
      build_progress >= 0.5.
    - Saturation is s + (top - s) x build_progress.
    - Each lamp's role colour is mixed toward the look's accent by build_progress x `narrow`, before saturation and
      the role's white, chroma cap and avoided hues, so a key lamp stays a key lamp.
    - The dip is the lamps' level at min(level, `dip_level`) for the last `dip_bars` (not under House lights or
      Lights low).
  - **The hit.**
    - It is `(t0, t1, {lamp: rgb})`, read by `palette_fn` and overlaid on the lamps only, exactly from the line
      (show-artnet's frame time), for `hit_beats`.
    - Each lamp shows the accent at full value through its own role's limits.
    - Screens are never in it. A test checks the screens at full value through the whole dip, line and hit.
    - The look cut lands on the first tick at or after the line (`transition: cut`).
  - **A drop without a token** sets accent density to 1 and saturation to the vibe's top while the phase is `drop`.
  - **The screens' palette.** The chosen palette (the pick, else the director's) goes to every room screen whose
    `layer` is `live` and which isn't cutting, when it is a catalog name.
    - `art`, even while a catalog palette stands in for it on the lamps, sends nothing; the snapshot says why.
    - One PATCH per screen per change, with a per-screen record. A failure is retried after 15 s.
    - A screen that leaves live is forgotten, so it gets the palette again when it comes back. A room blip
      (`reachable: false`) keeps the record.
    - Entering a vibe clears the record, so a show_service restart with a vibe re-sends the palette once.
  - **Snapshot.** It gains `sections` (structure, context, phase, track, memory, express, build, drop_inside,
    armed, tokens, stats) and `screens_palette` (palette, sent, busy, why, error, live, sends).
  - **The director.jsonl kinds:** `section_look`, `section_kept` (why, express, late_bars), `section_memory`,
    `track`, `token` (reserved, spent, refused, moved, released, with why), `drop`, and `screens_palette`. Each
    carries the context brief.
- **Tests.** Built: `test_director.py` (+18), `test_situations.py` (+7), `test_listener.py` (+6, and one updated for
  the genre_detail line), `test_autopilot.py` (+1, against the fake room's new PATCH route). The name scan adds
  mllink.py and structure.py, skipped until they land; a new scan rejects Discogs parent names in code.
  - Run, niced, with the render running: director, situations, listener and autopilot pass.
  - Two failures belong to other sessions' uncommitted edits: test_autopilot::test_config_error_is_blind
    (autopilot.py's Auto line) and test_dsp::test_shapes_are_exactly_the_contract (B9's dsp.py).
- **Costs** (niced, the render running, `-s`): the director's tick is p50 0.40-0.42 / p95 0.6-0.8 ms without
  sections, and p50 0.51-0.52 / p95 0.86-0.97 ms with a build and an armed drop (the 1 ms budget holds). The hit's
  colours are only computed from the dip on. situations and the listener add well under 0.1 ms a second (a mean over
  at most 30 CED rows per word read).

### S4.9 show.yaml's Stage-4 keys (c8's file; in place since 16 Oct)

| Section | Read by | Keys |
|---|---|---|
| `ml` | mllink (the worker's keys go to it as the config frame: `cpu_max`, `silence_dbfs`, `fast_lane`, `tasks`, `ced_groups`) | `enabled`, `python`, `models_dir`, `nice`, `cpu_max`, `cpu_floor`, `silence_dbfs`, `results_stale_s`, `stale_periods`, `fast_lane {every_s, budget_ms}`, `tasks {ced, tags, key, beats}` (each `{every_s, window_s, model, budget_ms}`; `tags.heads {field: head}`, `key.profile`, `beats.interior_margin_s`), `ced_groups {word: [AudioSet class, ...]}`, `beat_votes {match_ms, octave_tol, min_conf, one_agree, agree_runs, bar_agree_runs, bar_lens, cooldown_s}`, `restart {backoff_s, healthy_s, stall_s}`. S4.13 removed `threads` (nothing read it: the worker has two lanes) and added `cpu_floor`, `stale_periods` and `beat_votes.bar_agree_runs`; `tasks.beats.budget_ms` is 5500 |
| `genre_map` | structure (`genre_reading`) | `parents {Discogs parent: word, or {from_listener: [words]}}`, `tau_s`, `freeze_below_music`, `speech_scale_above`, `switch_margin`, `switch_dwell_s` |
| `structure` | structure | `gobo_on_sections`, `novelty {recent_s, before_s, k_mad, history_s, min {ml, dsp}, peak_s}`, `dsp_weights {chroma, mfcc}`, `min_section_bars`, `min_section_s`, `identify_after_s`, `repeat_sim {ml, dsp}`, `max_sections`, `predict_lead_bars`, `grid_conf`, `same_bars`, `phase_dwell_s`, `tags_tau_s`, `tags_hold_s`, `ced_mean_s`, `listener_conf`, `key_min_strength`, `key_hold {strength_margin, hold_s}`, `events {min_lead_s}`, `track {gap_s, gap_gate, gap_lufs_m, window_s, tempo_jump, key_min_conf, parent_window_s, parent_margin, spike_k (null: off), spike_jumps}`, `build {window_s, min_slope {lufs_db_s, brightness_s, onset_rate_s}, low_max_slope_db_s, full_rise_db, phrase16_conf, arm_beats, foot_db}`, `drop {low_jump_db, lufs_jump_db, within_beats, hold_bars, snap_beats, disarm_bars}`, `breakdown {low_drop_db, perc_ratio_max, min_gate, for_s, leave_s}`, `peak {energy_min, for_s, leave_s}`, `outro {window_s, max_slope_db_s, energy_max, for_s, leave_s}`, `fastled {build_end, drop_weights, band_weights, drop_cooldown_s}` (S4.13 added every key after `predict_lead_bars` but `tags_tau_s` and `key_min_strength`, and the track, build, drop, breakdown, peak and outro keys after the first ones) |
| `situations.talking` (new; `speech_words` since S4.13, also read by structure); `situations.quiet` gains `music_below`, `speech_below` | situations | S4.8 |
| `listener` gains `ced {music_min, speech_max, speech_words, speaks_s}`, `section_call_after_s` | listener | S4.8 |
| `director` gains `sections {max_late_bars, express_gain, phase_hint_s}`, `peaks {after_build_s}`, `build {step_bars, step_bars_late, narrow, dip_level, dip_bars, hit_beats}`, `breakdown {floor_add, motion_scale, saturation_scale}` (it also reads `structure.events.min_lead_s`) | director | S4.8 |

- The file documents every key and where its number comes from. Each is a starting point unless its comment says
  "owner" or "measured".
- **Checked on 16 Oct** (show.yaml revision `e2ac1e756a21`):
  - the running show_service reloaded the file with no settings error;
  - the Stage-3 readers (listener, situations, director, sources) parse it exactly as before; they ignore keys they
    don't know;
  - `genre_map`'s parents are all in `DISCOGS_PARENTS`, and its words are all vocab.yaml genres;
  - `ml.tasks` matches `ML_TASKS`, and `ml.ced_groups` matches `CED_GROUPS`.
- **Not in show.yaml** (unknowns stay unset): the vibes' `peak_tokens_per_hour` (vibes.yaml, the owner's call), and
  every model fact (index.json, from fetch_models.py).

### S4.10 Rules for every builder

S3.13's rules hold. Also:
- **Venvs.**
  - The hot venv `~/.venvs/sm-show` gets **no** new package: mllink and structure need numpy and soxr, which it has, and
    dsp's medians use scipy.
  - The worker's venv is `~/.venvs/sm-show-ml`. B7 alone installs into it.
  - Every install, export, model fetch and test run goes under `flock /tmp/claude-1000/sm-build.lock nice -n 19 …`.
- **Never:**
  - start a worker against production: tests use `--fake` or a stand-in, a tmp models dir and their own pipe;
  - write `~/.cache/screen-machine/models` from a test (B7's fetch writes it, by hand, once);
  - bind 8703, 6980, 6981, 6982 or 6454 (or 8700-8702);
  - PUT, POST or open a WS to the real room API;
  - kill a process you didn't start;
  - restart show_service, Flask or stream_service;
  - commit.
- **No GPU:** `CUDA_VISIBLE_DEVICES=` in every command that could see torch or onnxruntime.
- **Costs** are printed with `-s` and go in each builder's "As built", measured niced with the render running, as in
  Stage 3.

### S4.11 Going live, and who to tell

The exact steps for c8 (as of 16 Oct the venv, the models and the integration are done; nothing is committed and
production still runs Stage 3). Every build, fetch and test goes under `flock /tmp/claude-1000/sm-build.lock nice -n 19`.

1. **The ML venv and the models** (B7 built both on PC22; redo only if missing).
   ```
   flock /tmp/claude-1000/sm-build.lock nice -n 19 python3.12 -m venv ~/.venvs/sm-show-ml
   flock /tmp/claude-1000/sm-build.lock nice -n 19 ~/.venvs/sm-show-ml/bin/pip install -r show_service/ml/requirements.txt
   flock /tmp/claude-1000/sm-build.lock nice -n 19 ~/.venvs/sm-show/bin/python tools/show/fetch_models.py
   flock /tmp/claude-1000/sm-build.lock nice -n 19 ~/.venvs/sm-show/bin/python tools/show/fetch_models.py --verify
   ```
   Beat This! has no published ONNX: `beat-this-small0.onnx` is exported once from the scratch venv
   (`/tmp/claude-1000/onnx-export/venv`, recipe in `tools/show/export_onnx.py`'s docstring):
   `flock … nice -n 19 /tmp/claude-1000/onnx-export/venv/bin/python tools/show/export_onnx.py beat-this-small0`.
   **The check:** `CUDA_VISIBLE_DEVICES= ~/.venvs/sm-show-ml/bin/python -m show_service.ml.worker --models
   ~/.cache/screen-machine/models < /dev/null` prints `hello` with every configured model `ok` (11: `timbre` took
   `nsynth_bright_dark`'s place; `deam` is fetched but not loaded, arousal is off), `tasks` all true and nice 19, then
   exits 0.
2. **The suite:** `flock … nice -n 19 ~/.venvs/sm-show/bin/python -m pytest show_service/tests -q` (S4.12: all pass;
   about 9 minutes, the three end-to-end tests about 4 of them).
3. **Commit** (c8; builders don't): `show_service/` (the new `mllink.py`, `structure.py`, `ml/`, the tests
   `test_mllink.py`, `test_structure.py`, `test_dsp_medium.py`, `test_clock_thread.py`, `test_ml_*.py`, and the edits
   listed in S4.12), `tools/show/fetch_models.py`, `tools/show/export_onnx.py`, `config/room/show.yaml`. Never the
   models, the venvs or `/tmp`.
4. **Restart:** `scripts/ctl restart show`. That is show_service's own restart; nothing else restarts.
   `deploy/sm-show.env` needs no new line: the beat thread is off by default (`SHOW_BEAT_THREAD=on` would turn it on),
   and `CUDA_VISIBLE_DEVICES=` is already there.
5. **Check:**
   - `curl -s 127.0.0.1:8703/api/v1/state | jq '.modules, .ml.state, .ml.hello.models, .ml.cpu,
     .brain.components.structure.context.section, .rx_ms'`: every module ok, `running`, every model ok.
   - Then play test audio for 60 s (`curl -s -X POST 127.0.0.1:8703/api/v1/test -H 'Content-Type: application/json'
     -d '{"source": "audio"}'`, then `curl -s -X DELETE 127.0.0.1:8703/api/v1/test`):
     - `.ml.cpu.total` stays under 0.8 (rehearsed at 0.68-0.69, S4.12);
     - `.ml.stats.tasks[]` shows no `over_budget`, `.ml.feed.drops` stays 0, `.ml.bad_lines` 0;
     - music.context is on the bus, and `.brain.components.structure.sections` fills;
     - `.errors` stays `{}`.
6. **What changes at this restart** (in production):
   - **GOBO** (the screens' scene change, §9.5) with no vibe set: structure steps it once per section change whenever
     Art-Net is sending, so the renderer's music_direction changes scenes on sections. While a vibe plays, it steps
     only with a look that lands (S4.13): never in Dinner or Notes mid-piece, never while Keep this look holds.
   - **Chroma.** show.state's `global.music.chroma12`, and music.harmony, carry the audio chroma when no piano plays.
   - **On the bus:**
     - music.context, 1 Hz, to every client;
     - the room.event kinds `section_change`, `build`, `drop_armed`, `drop`, `breakdown` and `track_change`
       (structure has its own `seq` beside the director's; no reader dedupes room.event by `seq` today);
     - music.rhythm, only to clients that `want` it.
   - **The screens' palette.** While a vibe plays, every live, non-cutting screen gets the director's catalog palette
     (PATCH `/api/v1/screens/{id}/look` on room_service, once per change, and once again after this restart). That
     can replace a palette someone picked on a screen's card. `director.screens.send_palette: false` stops it.
   - **Auto's** `talking`, `quiet` and `music_audible` read CED. No mode's rule uses `talking` yet.
   - **The listener** can clip room-mic music once the owner turns AI listening on and a mode listens; Stage 3 never
     could. Test audio is as before.
   - **The card's hearing line** shows the local genre ("jazz · relaxed") when music.context is confident, and so does
     room.direction's `genre` (S4.13).
   - **Peaks:** no drop spends a token until a vibe sets `peak_tokens_per_hour`; unset, drops never cut or hit and
     only show inside the look. vibes.yaml leaves it unset: the owner's call (§8.5 suggests Party 4-6 an hour). The
     Feel form shows it as built now ("Big moments per hour"). A line no drop came to gives its token back (S4.13).
   - **The clock** runs exactly as in Stage 3 (inline: the beat thread is off), plus Beat This!'s integer corrections.
   - **Rollback** (any one of these):
     - `ml.enabled: false`: a hot reload; the worker stops and every reader degrades;
     - `structure.gobo_on_sections: false`: a hot reload;
     - `director.screens.send_palette: false`: a hot reload;
     - `SHOW_BRAIN=off`, then a restart: no brain, no worker, no structure (the medium lane and music.rhythm stay).
7. **Tell:**
   - **04:** music.context; the audio chroma; GOBO on sections; the new event kinds; music.rhythm on request; the
     screens' palette PATCH while a vibe plays.
   - **68:** GOBO steps on sections; lamp dips and hits come only inside a vibe that has tokens.
   - **2f:** the card's hearing line can show the local genre; Auto's `talking` works; the Feel form's peak tokens are
     built; the director PATCHes `/api/v1/screens/{id}/look` while a vibe plays.
   - **aa:** the situations vocabulary's descriptions changed; the adapter's cache refreshes itself.
   - **ca:** no new port. The worker is show_service's child, shown in the show card's state under `ml` (state, cpu,
     restarts). `~/.venvs/sm-show-ml` and `~/.cache/screen-machine/models` (about 50 MB) are new on disk.
   - **98:** nothing.

### S4.12 Integration and measurements (c8, 16 Oct)

**Wired** (S4.5's seams; "As built" under S4.5): mllink (MLFeed on show-rx after the ring, guard `ml.feed`; MLLink
as the brain's `ml`), structure after it, `features.clock_fn`, `Clock(beat_thread=)`, `clock.stop()` after rx, the
context's `rhythm` / `ml_feed` / `clock_correct`, brain.add's list slots, the awaited stop, bus `TYPES` and
`WANT_ONLY`, `--beat-thread`. Brain add order: room, situations, autopilot, sources, ml, structure, listener,
director.

**Reconciled** (the builders' open points):
- **B7.** show.yaml `ml.tasks.tags.heads`: `bright: timbre`; `arousal` (deam) off (MSD-MusiCNN is 8x EffNet, +0.05
  core); `engagement` stays null (Essentia publishes no range; the 0..1 range seen on the test tracks is the owner's
  call, not set). The worker's `DEFAULTS` match. Beat This! stays at 16 kHz (beat F 0.972, downbeat 0.926 against
  full band; the vote needs two agreeing runs). mllink wraps `supervisor.py`. B7's tests stay in
  `show_service/tests/test_ml_*.py`. `approachability_regression`: still no field.
- **B9.** test_dsp's Stage-3 chroma line is a shape check now; contract's `harm_change` comment names the
  beat-synchronous rule; music.rhythm is want-only on the bus. Left as built, for c8 later: swing's [0.4, 0.8] phase
  window (16th hats read above 1), and chroma12() over the harmonic floor in a noisy room (a flat chroma, no key).
  **The beat thread is off by default** (S4.6's note): measured in the real process it cost more than it saved.
- **B10.** Its event choices stand (a drop sends no section_change; a track change sends `track_change` then a
  `section_change` with detail `track`); Sonos counts while `sonos_playing` isn't false (confirmed); its three extra
  candidate rules are recorded in S4.7's "As built" (for SHOW_ENGINE §6.1 later).
- **B11.** contract `BrainContext.room_patch` added (the director's screens palette works); brain.add appends to
  `on_music_event`; show.yaml gains `situations.ced` and `director.screens`; vibes.py's `peak_tokens_per_hour` is no
  longer `not_built` (label "Big moments per hour", behind More); api.py's card hearing rule 1 reads
  `music_context.genre` first (test_api).
- **Others' edits** in show_service's tree: autopilot.py's blind line (another session's wording, "Auto is paused: a
  mode has a problem; open its editor") is kept and test_autopilot follows it.

**The Stage-4 end to end** (`tests/test_e2e.py::test_stage4_end_to_end`, about 2.5 minutes, skipped without ffmpeg,
the ML venv, the models or the test tracks). The real wiring in-process on ephemeral ports, a fake room on its own
thread, a fake LLM, every file in tmp_path; the real worker from `~/.venvs/sm-show-ml` reading the real models through
a tmp directory of symlinks (never written). Three fetched tracks back to back through the real test-audio child,
each a 45 s excerpt from 40 s in with 4 s of silence after it: Galería Disco "Memorias" (house, time-stretched with
ffmpeg atempo to 145.8 bpm), Doxent "1973" (jazz, 145.8 bpm) and Lilly Wolf "Play the Game" (pop, 135.1 bpm). So
the first change keeps the tempo and the second changes it by 7 %; the genre parents differ across both. A phrase vibe
plays. It checks, and with `-s` prints:
- the worker's hello (every task, nice 19, at most 2 compute threads);
- per track: CED's music median ≥ 0.5, and the leading Discogs parent among the plausible ones (logged with the
  top styles);
- no bad lines, no restart, the tasks' runs;
- music.context's exact shape, sections on the ML embeddings (≥ 80 % of the in-music contexts), at least 3 section
  ids;
- at least one track change, each within a real change's window, and structure's `section_change` and `track_change`
  on the bus; GOBO stepped;
- music.rhythm only to the client that asked for it;
- the director reacting: a structure look (`track`, `section`, ...) or a section expressed inside the look;
- the clock locked on each track within 15 s at its tempo ± 2 %, with no body without a grid through the same-tempo
  change, and re-locked within 8 s of the second track.

**Measured (16 Oct, flock + nice 19, the 4K render running):**
- **The Stage-4 e2e** passed (three runs; the numbers are the last, with the beat thread off). Worker: every model
  ok. CED music medians 0.93 (house), 0.74 (jazz), 0.92 (pop). Leading parents: Electronic 0.71 (Bassline, UK Garage:
  the stretched house), Jazz 0.20 with Rock 0.19 (Contemporary Jazz, Math Rock; an earlier run of the jazz stretched
  to 120 bpm led with Rock, hence Rock among the plausible ones for this track), Electronic 0.26 with Pop 0.17
  (Electro, J-pop). Track changes at 48.8 s and 97.5 s (the real ones at 49 s and 98 s), both `via dsp`. The
  director made two `track` looks; the confirmed sections, heard about 4 bars late, were expressed inside the look.
  The clock locked 3.6, 2.2 and 2.9 s after each track's start, at 145.8, 145.9 and 135.0 bpm, with no body without
  a grid through the first change (1 relock, no cold search). One Beat This! correction applied (bar length 3, on the
  jazz). Worker tasks over the run: ced 133 runs p95 39 ms, tags 133 p95 19 ms, key 60 p95 16 ms, beats 10 p95
  2.7 s; nothing skipped or over budget; no feed drops. show-rx (in-process, with the test's own WS clients):
  `on_audio` p95 2.9 ms, `on_tick` p95 1.2 ms.
- **The P0 and Stage-3 e2e** pass unchanged (features 25.0 Hz; the clock locked 4.2 s after the first packet at
  124.00 bpm; Art-Net 41.4 Hz with pulse edges +0.47 ms from the predicted beat, p95 1.35 ms). The Stage-3 one runs
  with `ml.enabled: false` (it never starts the worker). With the beat thread on, the P0 e2e's Art-Net rate once
  read 36.7 Hz (its floor is 38): the same GIL contention.
- **The real process** (scratch harness: `python -m show_service` as a subprocess, niced, HOME and every file in tmp,
  a fake room, Art-Net to a local sink, one `renderer` WS client, the phrase vibe, the same three tracks through the
  test-audio child, the real worker). 60 s of /proc after a 45 s warm-up (the first Beat This! window filled):

  | beat thread | ML worker | show_service | worker | **total** (owner: < 0.8) |
  |---|---|---|---|---|
  | **off (the default)** | on | 0.430, 0.438, 0.445 | 0.250, 0.255, 0.256 | **0.680, 0.693, 0.702** |
  | on | on | 0.568, 0.583 | 0.232, 0.259 | 0.800, 0.842 |
  | on | off | 0.529 | | 0.529 |
  | off | off | 0.389 | | 0.389 |

  - **Per thread** (off, on): show-rx 0.344-0.360, show-bus 0.041-0.042, show-brain 0.026-0.028, show-artnet 0.016;
    the worker's beats lane 0.20-0.21, its fast lane 0.044-0.047, its reader 0.003. With the thread on: show-rx 0.29-0.31
    and show-beat 0.19. The test-audio child and its ffmpeg (show-brain's children) run only while a test plays and
    were not counted here (Stage 3 measured them at about 0.17).
  - **show-rx per block** (`/api/v1/state` `rx_ms`; wall time, GIL waits included): `on_audio` p50 0.34-0.41 ms, p95
    2.6-3.0 ms, max 11-15 ms; `on_tick` p95 1.0-1.1 ms, max 10-13 ms. dsp hop p95 1.3-1.5 ms, the medium frame p95
    1.05-1.21 ms; MLFeed 18-20 µs p50, 92-98 µs p95 a block. (Thread on: `on_audio` p95 2.6 ms, max 10-11 ms; `on_tick` p95 2.0-2.3 ms; dsp hop p95 3.5-3.7 ms.)
  - **The worker** over 60 s: ced 93 runs p95 34-38 ms, tags 93 p95 15-20 ms, key 40 p95 17-21 ms, beats 6 p95
    2.3-2.8 s; nothing skipped or over budget, stretch 1, no feed drops.
  - **Brain ticks**, CPU p95 (`thread_time`): situations 1.2-1.5 ms, autopilot 0.4, sources 0.4-0.5, ml 0.7 (2.0-2.9
    before its snapshot's log tail and percentiles went to every 30 s), structure 2.2-2.6 (its 1 Hz seconds),
    listener 0.4-0.5, director 1.3 (over its 1 ms; B11 measured 0.9-1.0 alone); loop lag p95 2.2 ms.
  - Art-Net 38.8-39.5 Hz, late p95 1.0-1.4 ms; exit 0 on SIGTERM; no ERROR lines.
- **The suite:** 910 passed (`flock … nice -n 19 … pytest show_service/tests -q`, 9 minutes). The P0 e2e now starts
  its Show with `freeze_gc=True`, as main() does: after the whole suite the pytest heap is big, and twice its
  Art-Net rate read 35-36.7 Hz against the floor of 38 (alone it reads 41.4).
- The headroom: 0.10-0.12 of a core under the owner's 0.8 with test music. Beat This! is most of the worker
  (0.2 core at every 10 s, §5.3's slowest end); the worker's own guard (`ml.cpu_max` 0.35) never had to act.

### S4.13 Review fixes (16 Oct; c8's fixer)

The Stage-4 review (24 issues) is applied; none was skipped. The sections above now say what the code does, each
change marked "S4.13". In short:

| # | Issue | What changed |
|---|---|---|
| 1 | GOBO stepped on every section and drop whatever the vibe | structure steps it only while the director is neutral (`ctx.vibe()` None); with a vibe the director steps it with every look that lands, the structure-caused ones included, so change_looks, Keep, relaxing, the late rule and tokens hold the screens (S4.7, S4.8) |
| 2 | the no-gap track change fired inside a song | keys that are the same, parallel or relative are no jump; music.context's key is held; after a gap the genre is the clear leader of the last 5 s of tags; without a gap it takes two jumps, the genre by the smoothed leader; `spike_k: null` turns it off. Found while testing: the gap test read the gate (3 s loudness), which notices silence only ~3 s in, so 4 s gaps were never gaps; it now also reads lufs_m (`track.gap_lufs_m` -60) |
| 3 | the privacy gate read only "speech" | a reading's speech is the max over `listener.ced.speech_words` / `situations.talking.speech_words` (speech, conversation, crowd): the upload gate, talking, quiet and the genre speech scale (`contract.speech_words`, `speech_of`) |
| 4 | categories flickered at 1 Hz | key: a sticky source and `key_hold`; instruments: smoothed like the tag numbers; breakdown, peak, outro: in after for_s, out after leave_s, and the phase word holds `phase_dwell_s` (event phases show at once); genre_in: the CED hysteresis |
| 5 | room.direction's genre was the LLM's only | music.context's genre (ML, fresh, conf ≥ 0.6) > the listener's > the vibe's; `words_from` in the snapshot |
| 6 | a meter change from two runs, with no one | a new bar length needs a one and `beat_votes.bar_agree_runs` (4) runs; `bar_lens` stays [3, 4] |
| 7 | an armed line was never disarmed | `drop_armed` with `eta_beats` None: `disarmed` (the build ended over `drop.disarm_bars` before the line, or a new build or track) releases the reservation; `missed` refunds the spent token |
| 8 | an unarmed drop stepped GOBO at once | at the first bar line `events.min_lead_s` ahead; the director's drop and track looks use the same lead |
| 9 | section calls ignored the cadence's slowing | at least half the current interval after the last call, at most one per interval |
| 10 | a run's last second rode on a failed reading | it counts only when the next reading (which covers it too) passed |
| 11 | freshness was a fixed 3 s while the worker stretched | MLView `stale_s` per task (max(results_stale_s, `stale_periods` x its interval in the last stats line)); structure, the listener and situations' CED window use it (`contract.ml_stale_s`) |
| 12 | a test track's genre carried over to the room | another ML stream clears the genre, the tag numbers, the instruments, the parents window and the key |
| 13 | musical thresholds in code | show.yaml `structure` (grid_conf, same_bars, ced_mean_s, tags_hold_s, listener_conf, phase_dwell_s, key_hold, track.*, build.foot_db, drop.snap_beats/disarm_bars, breakdown/peak/outro leave_s, `fastled`), `director.sections.phase_hint_s`, `listener.ced.speaks_s` |
| 14 | stale comments | vibes.yaml (tokens are spent; unset = drops never cut or hit), show.yaml's structure header |
| 15 | essentia's licence unrecorded | AGPL-3.0 in S4.4, `ml/requirements.txt` and fetch_models.py's LICENSES.md |
| 16 | a bad `ml` section killed the supervisor for good | `resolve_checked` never raises (a 1 s backoff floor); frames carry `WORKER_KEYS` only and one that can't be made isn't sent (mllink keeps the last good); a fault in the loop ends that run and is retried; `stop()` cleans up after a failed loop |
| 17 | the worker sat at 1.6 GB | the CPU arena off for the beat tracker and `MALLOC_ARENA_MAX=2`: about 300-335 MB at the same cost |
| 18 | a wedged lane was never restarted | the worker's lane watchdog exits 70 when a lane makes no cycle in max(3 x period, period + budget + 5 s) |
| 19 | each spawn truncated the last run's traceback | the log is appended per run after a header; an exit code over 0 keeps its last log line in `last_exit.why` |
| 20 | the feed hand-off relied on timing | attach is a (generation, fd) store, never lost; `FrameFeeder.busy` marks a write, and the stop waits it out (else `/dev/null` over the fd, closed later) |
| 21 | thin Beat This! headroom; a 1 s pipe | `budget_ms` 5500 (about 2x its p95); an overrun skips the task's next turn; the pipe is 1 MB (16 s) |
| 22 | nothing enforced the owner's 0.8 | over 0.8 for 60 s, the worker's cpu_max becomes 0.8 less show_service's share, within [`cpu_floor` 0.1, `cpu_max`], and comes back |
| 23 | the stop could outlast its await | `STOP_AWAIT_S` 7 s (the sequence's 6.5 s worst case), `Brain.stop` 10 s |
| 24 | docs and config that didn't match | `Clock(beat_thread=False)` by default (test_clock_thread passes True where it means it); `ml.threads` removed; the RSS line fixed |

**Tests** (+38, 910 → 948): test_structure (+14: GOBO with a vibe, the unarmed drop's bar, disarmed and missed lines,
the relative and parallel keys, path (c) in both directions and `spike_k: null`, the short gap, a stream switch,
smoothed instruments, a flickering key, stretched freshness, the babble speech scale, peak's leave), test_director
(+3, and the `time` test extended: Dinner-like `time` and Keep this look give no GOBO, a released and a refunded
token, the ML genre), test_listener (+4: babble and conversation, the last second, the stretched cadence, section calls
slowing), test_situations (+3), test_mllink (+4: stale_s, configure's last good section, the 0.8 cut and back, a bar
length needs a one), test_ml_supervisor (+8: the hand-off, busy, the release wait and `/dev/null`, `resolve_checked`,
settings it can't send, a loop fault, a crash's reason and log, the stop budget), test_ml_worker (+2: the skipped turn,
the lane watchdog). Updated: the GOBO counts
in test_director, the breakdown leave and the key hold in test_structure, the clip ends in test_listener, the 4-run
bar length in test_mllink, 1 s backoffs in the ML tests, and the e2e's GOBO check (the director's steps count).

**Run** (flock + nice 19, the 4K render running): `pytest show_service/tests -q` **948 passed** (8.7 min), the Stage-4
e2e included: track changes at 48.8 and 98.0 s, both through the gap path now (the jazz track's key E minor →
G# minor, then the tempo), no meter applied to the jazz (one confirmation), every model ok, nothing skipped or over
budget, no feed drops.

**Measured** (the S4.12 real-process harness, beat thread off, 60 s after a 45 s warm-up, two runs): show_service
0.407 / 0.413, the worker 0.234 / 0.254, **total 0.641 / 0.668** (S4.12: 0.680-0.702); show-rx 0.33, show-bus 0.04,
show-brain 0.025, show-artnet 0.016; `on_audio` p95 2.5-2.7 ms; MLFeed 17-18 µs p50, 94-95 µs p95 a block (S4.12:
18-20 / 92-98); brain ticks CPU p95: structure 2.3-2.5 ms, director 1.2-1.3, situations 1.0-1.2; Art-Net 38.7-39.2 Hz;
the worker's RSS **334 MB** (`rss_mb`), stretch 1, cpu_limit not cut; exit 0, no ERROR lines. Beat This! alone (the
ML venv, 12 runs, both lanes on threads): p50 1.86-1.94 s, p95 2.1-2.5 s, RSS 303 MB with `MALLOC_ARENA_MAX=2` (574 MB
with glibc's per-thread arenas, 1.6 GB with onnxruntime's arena as first built).

**Who to tell** (S4.11 step 7 still holds), and in addition:
- **04:** `drop_armed` can come with `eta_beats` null (`detail` disarmed or missed: the line is off); GOBO steps on
  sections only with no vibe set (with a vibe, one per look change); room.direction's `genre` can come from the local
  ML now.
- **68:** lamp dips and hits stop when a build gives out before its line.

## Stage 5: the rig (fixtures, the look engine, Hue and Art-Net out) (P5)

**Interfaces, 16 Oct (c8).** This section defines Stage 5 so that five builders can work in parallel, each in its own
files. It turns SHOW_ENGINE §5.4 (rules per output, the DMX frame grid, Hue's rules), §8.2-§8.3 (the look; screens never
dimmed), §8.8 (colour, lamp roles), §9 (the look engine, fixtures.yaml, Art-Net out, Hue) and §11 into a build spec.
- The shared names and the **exact shapes** are contract.py's **"Stage 5" block**, plus the Stage-5 slots at the end
  of `BrainContext` (`rig`, `room_boot`, `on_room_event`).
- The config is **`config/room/fixtures.yaml`** (new, S5.2) and show.yaml's Stage-5 keys (S5.10: `rig`, `looks`, and
  two `director.screens` keys). Both are in place now; nothing reads them until this code ships.
- **Every output is off** (`enabled: false`) in fixtures.yaml, so the restart that ships Stage 5 changes nothing anyone
  can see (S5.0 rule 6). Each output is switched on later, in config, with the session that owns the other side.
- Where this section and SHOW_ENGINE.md differ, this section is the build spec.

| Piece | Builder | Status |
|---|---|---|
| `fixtures.py` (the registry), `ofl.py`, `tools/show/add_fixture.py` | B12 | **done** (S5.4a) |
| `outputs.py` (the `rig` component, drivers' runners), the `__main__` and `api.py` seams | c8 (B12's spec) | **done** (S5.4b) |
| `looks.py` (the look engine), `colour_mix.py` (S5.5's emitters.py: colour to emitters, encoding) | B13 | **done** (S5.5a) |
| `hue_out.py` (Entertainment streaming from show_service) | B14 | **done** (S5.6a) |
| `artnet_lan.py` (ArtDmx to LAN nodes, ArtPoll, the virtual node) | B15 | **done** (S5.7a) |
| the screens' hand-pick hold, RoomLink's room events | B16 | **done** (S5.8) |
| the director's lamps from the registry, `look_fn`, the name scan's Stage-5 additions | c8 (B16's spec) | **done** (S5.8) |
| integration, the Stage-5 e2e, the CPU measurement | c8 | **done** (S5.13); not committed |
| the review fixes | c8's fixer | **done** (S5.14); not committed |
| going live | c8, with 68 and 2f | S5.12 |

### S5.0 Rulings every builder keeps (owner)

S3.0 and S4.0 still hold. For Stage 5:
1. **No special cases.** No code branches on a mode, vibe, device, fixture, output, node, group, zone or screen name.
   - A fixture's behaviour comes from its **type** (kind, attributes, channels, emitters), its **role** (placement →
     show.yaml `lamps`), its output's **driver kind and caps**, and show.yaml's `looks` layers.
   - A driver kind (`hue`, `artnet`) is a fixed vocabulary word (`contract.DRIVERS`), like a band name; an output's *id*
     is config.
   - The name scan in test_director.py adds the Stage-5 modules and fixtures.yaml's ids, groups, zones, outputs and
     nodes (B16).
2. **No invented values in live config.**
   - fixtures.yaml holds exactly the six existing devices, copied from patch.yaml; nodes and DMX are empty, with
     commented examples. Roles stay unset (placement decides). Latency stays unset unless measured (latency.json).
   - Every Stage-5 number in show.yaml is a starting point with its §; the code keeps the same numbers as fallbacks,
     marked the same way.
   - Code defaults that are physics, not taste (an LED emitter's typical chromaticity), are listed in the code as
     "starting point (typical datasheet value); measure", and a type in fixtures.yaml overrides them.
3. **Screens are never dimmed.** A fixture whose type's kind is `screen` never gets a level, floor, master, cap, dip,
   hit or intensity layer. In Stage 5 the look engine doesn't evaluate screens at all (their drivers are `showbus`,
   not built); a test proves a screen fixture never appears in a Frame (B13).
4. **Degrade, never refuse.**
   - A bad fixtures.yaml keeps the last good registry, with the error shown.
   - A fixture, type, group or layer that can't be used as written is left out with a problem line; the rest works.
   - A missing driver module reads `not_built`; a bridge or node that doesn't answer reads `error` or `silent` and is
     retried. No mode transition ever fails because of the rig: room's hue adapter reads `unconfirmed`, never `failed`
     (except `taken_over`, which is the truth).
   - No control is disabled.
5. **No auth. No hard-coded IPs.**
   - An Art-Net node is matched by MAC or short name, with `last_ip` in fixtures.yaml.
   - The Hue bridge's address is its pairing file's (`~/.config/screen-machine/hue/<bridge>.json`), found by the bridge
     id that patch.yaml (or fixtures.yaml) names.
   - Loopback is used only for a test's ephemeral sink. **A loopback address on port 6454 is never sent to**: it is
     room_service's receiver, and DMX meant for the LAN must never land in the virtual Maestro's universe.
6. **Off by default.** Every output is `enabled: false` until fixtures.yaml says otherwise. At the restart that ships
   Stage 5 the only behaviour changes are the director's (S5.8): the `source` probe on its next screens-palette PATCH
   (one extra request per room boot against the old room code), and the hold, which reacts only to a person's pick.
   The review fixes (S5.14) add two director changes that reach today's lamps through the virtual Maestro (room
   streams its hue channels): a key lamp's colour (warm white at the feel's `key_cct`, tinted at most
   `lamp_chroma_max`, where it used to be cut to ~5000 K, lavender or lime), and an unforeseen drop's hit, which
   swells over 300 ms instead of stepping (§5.4). Both are the design's own rules, fixed.
7. **The host renders 4K.** With every output on, Stage 5 adds at most 0.03 of a core (measured at go-live; S5.12).
   Runner threads run at nice 0, like show-artnet; nothing new uses a GPU; nothing new spawns a process.

### S5.1 Builders and files

| Builder | Its files (and tests named for them) | Seams it alone edits |
|---|---|---|
| **B12 rig and wiring** | `fixtures.py`, `outputs.py`; `tests/test_fixtures.py`, `tests/test_outputs.py`; `tests/data/ofl/` (test OFL types, never config/) | `__main__.py` (Stage-5 wiring), `api.py` (every Stage-5 route, S5.4), additions to `test_main.py` and `test_api.py` |
| **B13 look engine** | `looks.py`, `emitters.py`; `tests/test_looks.py`, `tests/test_emitters.py` | none |
| **B14 Hue** | `hue_out.py`; `tests/test_hue_out.py` | `requirements.txt` (one block: `hue-entertainment==0.1.2` and its pins as installed in `~/.venvs/sm-room`) |
| **B15 Art-Net on the LAN** | `artnet_lan.py`; `tests/test_artnet_lan.py` | none |
| **B16 director and room seams** | none of its own | `director.py` (S5.8: the hold, the source probe, lamps from the registry, `look_fn`), `autopilot.py` (`RoomLink`: `on_room_event`, `room_boot`); additions to `test_director.py` (the hold, the probe, the name scan) and `test_autopilot.py` |

- c8 owns `contract.py`, `config/room/show.yaml`, `config/room/fixtures.yaml`, `config/room/fixture-types/` and this
  README. A builder that needs a change to any of them asks c8; it never edits them.
- **Imports.** A Stage-5 module imports `contract`, the stdlib and its pinned dependencies, plus these c8 rulings:
  - `looks.py` may import `emitters.py` (same builder), `clock.py`'s pure consumer helpers (`beat_at`, `next_beat`,
    `position_at`), `palette.py`'s pure functions (`to_oklab`, `mix`) and artnet_out's P0 constants (`HUE_ATTACK_S`,
    `HUE_DECAY_S`, `HUE_DEPTH`, `TAU_ENERGY_S`, `QUIET_GATE_OFF`, `QUIET_GATE_ON`, `QUIET_AFTER_S`, `IDLE_AFTER_S`,
    `DEFAULT_RGB`), so the parity with the virtual Maestro can't drift;
  - `emitters.py` may import `palette.py`'s pure functions;
  - `artnet_lan.py` imports `artnet_out.py`'s wire helpers (`build_artdmx`, `next_seq`, `grid_tick`, `MIN_GAP_S`,
    `GRID_HZ`, `PORT`): one copy of the ArtDmx wire in show_service (§9.3);
  - `hue_out.py` imports `room_service.hue.stream` (`xy_bri`, `xy_streamer`) and `room_service.hue.client`
    (`HueClient`, `BridgeSecrets`, `resolve_area`, `HueError`) **lazily, in its default factories only**. stream.py was
    written for this ("shared by room_service's HueOutput and show_service's driver"), so both streamers encode alike.
    68 owns those files: tell 68 before relying on anything else in them;
  - `outputs.py` imports `fixtures.py` (same builder) and loads the look engine and the drivers by import path, lazily
    (S5.4), so a missing one reads `not_built` instead of failing the rig.
- **Parallel seams.** B12 codes against the signatures in S5.5-S5.7 and tests with fakes; B13, B14 and B15 test their
  classes alone (a fake engine, a fake clock, fake frames). B16 tests with a fake room. Every seam is read with
  `getattr`, so a piece that hasn't landed leaves its slot None. c8 integrates and runs the full suite.
- `requirements.txt` stays append-only. Builders don't commit; c8 does.

### S5.2 fixtures.yaml (schema; c8's file, read by fixtures.py)

`version: 1`, then `outputs`, `nodes`, `types`, `fixtures`, `groups`. The file itself documents every key.
- **Ids** match `^[a-z0-9][a-z0-9_.-]{0,63}$` (`hue.ne` has a dot). The fixture ids are the existing ones, which
  rooms.json, room's hue adapter, the renderer and the Room page use.
- **Load rules** (fixtures.py, as vibes.py's):
  - a YAML error keeps the last good registry, with `error` set;
  - a value it can't use drops that thing (or that key, to its unset value) with a page-words line in `problems`
    (`"fixtures.spot.ball1: address 510 + footprint 4 runs past 512: left out"`);
  - an unknown key is ignored with a problem line, except under `outputs.<id>`, where every key the schema doesn't
    name goes to `settings` for the driver;
  - reloaded on any mtime change of fixtures.yaml, a used type file, latency.json or patch.yaml (every `RELOAD_S`).

**`outputs.<id>`** (one per driver instance: the Hue area, or one Art-Net universe):

| Key | Type | Unset | Meaning |
|---|---|---|---|
| `driver` | `showbus` \| `hue` \| `artnet` \| `ddp` | required (else left out) | `contract.DRIVERS`; only `hue` and `artnet` are built in Stage 5 |
| `enabled` | bool | **false** | the output's on switch (hot-reloaded); a non-bool is false, with a problem line |
| `node` | node id | none | artnet: its node |
| `port_address` | 1..32767 | none | artnet: the 15-bit Port-Address. **0 is refused** (room_service's virtual universe), with a problem line; the output then can't start (`no_config`) |
| `latency_key` | str | the output id | latency.json `outputs.<key>` for the output as a whole |
| `latency` | `{intensity: {rise_ms, fall_ms}, colour_ms, position_ms}` | `{}` | hand-set ms, used only where latency.json has nothing |
| `hits` | bool | the driver's (hue false, artnet true) | takes `music.hit` flashes. It can only turn them off: `true` on a driver whose caps say false (Hue, §5.4) is a problem line |
| `min_attack_ms` | ms | the driver's (hue 80, §5.4; artnet 0) | no intensity rise is faster. It can only raise the driver's floor |
| `rate_hz` | Hz | the driver's (hue 50; artnet: the beat grid) | |
| anything else | | | `settings`, the driver's own. **hue:** `bridge`, `area`, `gamma`, `stop_after_s`; each one left out is read from patch.yaml's `hue` block (68's, the one truth until P5 moves it here). `streamer` is room's switch and is always read from patch.yaml (a `streamer` here is a problem line) |

**`nodes.<id>`** (Art-Net nodes; empty until one is bought):

| Key | Type | Unset | Meaning |
|---|---|---|---|
| `match` | `{mac: "aa:bb:cc:dd:ee:ff"}` or `{short_name: str}` | `{}` (no check) | what an ArtPollReply must say for the node to count as found |
| `last_ip` | IPv4 | none (nothing is sent, `no_config`) | where frames and ArtPolls go. Discovery confirms it; a matching reply from another address is kept in `nodes.json` (state dir) and used, and the status says "moved: update last_ip". **Code never writes fixtures.yaml.** |
| `port` | int | 6454 | only tests set it (an ephemeral sink) |
| `full_512` | bool | false | always 512 channels; otherwise up to the highest patched channel, even (§5.4) |
| `failsafe` | `hold` \| `zero` \| `full` \| `scene` | none (the node's own) | sent once as ArtAddress (S5.7) |
| `virtual` | bool | false | previz: frames stay in memory, served by `GET /api/v1/rig/frame`; never on a socket |

**`types.<id>`:**

| Key | Meaning |
|---|---|
| `source: builtin` | one of fixtures.py's `BUILTIN_TYPES` (below) |
| `source: ofl`, `file`, `mode` | an Open Fixture Library JSON (MIT) under `config/room/fixture-types/`, and the mode by `name` or `shortName` |
| `emitters` | optional `{<emitter>: {xy: [x, y], Y: <relative luminance>}}`: overrides the starting points (S5.5) |
| `curve` | optional: `srgb` (neutral: the palette's values go straight across), `linear`, or `gamma:<g>` |

The builtin types:

| Type | kind | attrs | Driven by |
|---|---|---|---|
| `sm-screen` | screen | colour | `showbus` (P4). Never evaluated in Stage 5 |
| `sm-hue` | lamp | intensity, colour | `hue`: `FixtureFrame.rgb` → room_service.hue.stream's `xy_bri(rgb, gamma)` |

**OFL → channels** (`fixtures.load_ofl`; OFL's schema: `availableChannels`, `modes[].channels`, `wheels`):
- Each mode channel becomes an `AttrChannel` by its first capability:
  - `Intensity` → `intensity`;
  - `ColorIntensity` → `emitter`, named from its `color` (`Red` → `red`, `Warm White` → `warm_white`; `contract.EMITTERS`;
    any other colour → `other`);
  - `WheelSlot` on a wheel whose slots carry `colors` → `wheel` (each colour slot's DMX value is the middle of its
    range; `Open` counts as white);
  - `ShutterStrobe` → `shutter`;
  - `Pan`, `Tilt` → `pan`, `tilt` (position: not driven in Stage 5);
  - anything else → `other`.
- `fineChannelAliases` in the mode give the coarse channel its `fine` offset (16-bit values, MSB first). A `null` in a
  mode's channel list is a gap.
- **Defaults** (what an undriven channel sends): OFL's `defaultValue` (a number, or a percentage string); for a shutter
  without one, the start of its `shutterEffect: "Open"` capability (§9.2: a shutter left at 0 is dark); otherwise 0.
  A fixture's `pin` overrides any of them by channel name.
- Strobe is never driven in Stage 5: a shutter stays at its default (open). `LookData.accents.strobe` is published
  only, as before.
- Matrix fixtures (`matrix`, `templateChannels`) aren't built: the type reads a problem line and its fixtures are inert.
- `attrs` is `intensity` when it has an intensity channel or any emitter; `colour` when it has emitters or a colour
  wheel. A type with neither drives nothing (its defaults still go out).

**`fixtures.<id>`:**

| Key | Type | Unset | Meaning |
|---|---|---|---|
| `type` | type id | required (else inert, problem line) | |
| `label` | str | the id | |
| `output` | output id | none (registered, unrouted) | |
| `address` | 1..512 | required on an artnet output | 1-based start channel. `address + footprint - 1 > 512` leaves it out; an overlap with another fixture is a problem line (both kept; the later address wins on the shared channels) |
| `light` | str | required on a hue output | the bridge's light id |
| `screen` | screen id | none | the screen it belongs to, or hangs above (lamps with `follow_screen` take that screen's colours) |
| `zone`, `placement` | str | none | `placement` → role through show.yaml `lamps.placement_roles` |
| `role` | a show.yaml `lamps.roles` name | from placement, else `lamps.default_role` | |
| `tags` | [str] | `[]` | for selectors |
| `order` | number | none | position in ordered groups without an explicit order |
| `wb` | `{<emitter>: gain}` | 1.0 each | white balance (S5.5) |
| `curve` | as the type's | the type's | |
| `pin` | `{<OFL channel name>: 0..255}` | `{}` | holds an undriven channel |
| `house` | `{intensity: 0..1, hex: "#rrggbb"}` | none (dark) | what a DMX fixture shows while the rig has no look (S5.7 idle) |

**`groups.<id>`** = `{select: <selector>, order?: [fixture ids]}`. The ordered result is the group's member list (for
phase spread). `order` lists first, in its order (ids not selected are skipped); the rest follow by `order`, then id.

**Selectors** (LX-style tag selectors; `fixtures.parse_selector`, `fixtures.select`):
- clauses separated by `|` (OR); within a clause, terms separated by spaces (AND); `!` before a term negates it;
- a term is `key:value` with key one of `id`, `kind`, `type`, `output`, `driver`, `zone`, `placement`, `role`,
  `screen`, `tag`, `group`; a bare word means `tag:<word>`; `*` matches every fixture;
- a value ending in `*` is a prefix (`id:hue.*`);
- examples: `"kind:lamp"`, `"zone:north kind:lamp"`, `"kind:lamp !role:key"`, `"group:around"`, `"ball | role:ball-spot"`.
- A selector that doesn't parse is a problem line, and whatever used it selects nothing.

**Latency** (`fixtures.resolve_latency` → each fixture's `LatencyView`; §5.4, §5.5). For each edge or kind, the first
of these that has it:
1. latency.json `outputs.<fixture id>.<attr>` (per lamp: §9.4's "the lamps' measured lead");
2. latency.json `outputs.<the output's latency_key>.<attr>`;
3. fixtures.yaml `outputs.<id>.latency` (hand-set ms);
4. 0, with source `unset`.

`<attr>` is `contract.LATENCY_ATTRS[kind]`, the first present (intensity reads `intensity`, then `brightness`, the TV
routine's name). A measured edge's value is its perceived onset: `t10 + (t50 - t10) x (align_pct - 10) / 40`
(show.yaml `looks.align_pct`, 25: §5.5's ~t20-t30), plus its `trim_ms`. `colour` with nothing measured takes the
intensity rise. `min_pulse_ms = max(min_visible_ms, rise - fall + min_visible_ms)`.

### S5.3 Threads and the frame path

```
[show-rx]        + Rig.on_hit(music.hit body): appended to each hits-capable output's deque, that runner woken (<= 5 µs)
[show-brain]     + rig (1 Hz): Registry.poll (fixtures.yaml, types, latency.json, patch.yaml), Program compile on a
                   change, driver.configure / tick (hue: start, stop, takeover, release; artnet: ArtPoll, replies,
                   failsafe), runners started and stopped as outputs are enabled or disabled; HueOut's control task
[show-out-<id>]  one per enabled, built output: the deadline loop
                     t = driver.plan(t_last, now, active)
                     wait until t (an Event, so a hit can wake it)
                     frame = engine.evaluate(<id>, t, driver.caps)     # every input read by reference
                     driver.set_frame(frame)                           # encode, sendto / send_colors
[hue-dtls-sender] hue-entertainment's own thread, one per streaming hue output (send_colors only queues)
[show-bus]       + api.py's Stage-5 routes (S5.4), through brain.call and snapshots
```
- **Ownership.** The Program (layers compiled from show.yaml `looks` and the registry), the `RigView` and the drivers'
  send maps are built on show-brain and swapped by reference. A runner owns its output's engine state (smoothers, the
  hit deque's cursor, the last frame) and touches nothing else. `evaluate` never locks and never calls into show-rx.
- **Inputs, by reference:** `palette_fn(t)` (the director's: per-fixture colour × level, crossfades, House, Low and
  dips; **not** the drop's hit since S5.14), `look_fn()` (the director's latest `LookData`, with the drop's hit in
  `hit` and the pre-drop dip in `dip`: each output takes the hit by its caps), the `set_look_out` values (`hue_depth`,
  `beat_lock`, fanned out to artnet_out's and the rig's; `beat_depth` to the rig's only), `clock.snapshot()` /
  `next_beat`, and `features.latest()`.
- **Budgets** (p95, niced, under the 4K render; each module's tests print them with `-s`):

  | What | p95 |
  |---|---|
  | `evaluate`, 4 Hue lamps | ≤ 150 µs |
  | `evaluate`, 32 DMX fixtures with emitters | ≤ 600 µs |
  | `set_frame`, hue (4 lamps) | ≤ 100 µs |
  | `set_frame`, artnet (a universe) | ≤ 100 µs |
  | rig tick (1 Hz, show-brain) | ≤ 3 ms |
  | `Rig.on_hit` (show-rx) | ≤ 5 µs |
  | a runner's lateness against its plan | ≤ 1.5 ms |

- **The two-process split.** The runners belong to show_rt (§2's output thread). Keep their inputs "latest value"
  reads, so they become IPC reads later.

### S5.4 fixtures.py and outputs.py, the wiring and the routes (B12)

**fixtures.py** (pure; no threads, no network):
```python
BUILTIN_TYPES: dict[str, contract.TypeInfo]            # sm-screen, sm-hue (S5.2's table)
def load(path: Path = contract.FIXTURES_FILE, *, types_dir: Path = contract.FIXTURE_TYPES_DIR,
         latency: dict | None = None, patch: dict | None = None, looks: dict | None = None) -> contract.RigView
    # never raises: a bad file gives error + problems and empty maps (Registry keeps the last good one)
def load_ofl(path: Path, mode: str) -> contract.TypeInfo     # problems inside, never raises
def parse_selector(expr: str) -> Selector                   # ValueError("bad_selector: <why>")
def select(expr: str | Selector, rig: contract.RigView) -> list[str]   # ordered fixture ids
def resolve_latency(fixture_id: str, out: contract.OutputConfig | None, latency_doc: dict | None, *,
                    align_pct: float, min_visible_ms: float) -> contract.LatencyView
def hue_settings(out: contract.OutputConfig, patch: dict | None) -> dict
    # {bridge, area, gamma, stop_after_s, streamer}: fixtures.yaml's settings over patch.yaml's hue block
class Registry:
    def __init__(self, path=contract.FIXTURES_FILE, *, types_dir=..., latency_path=contract.LATENCY_FILE,
                 patch_path=contract.PATCH_FILE) -> None
    def poll(self, looks: dict | None = None, force: bool = False) -> bool   # show-brain, every RELOAD_S; True: new view
    def view(self) -> contract.RigView | None                                # any thread
```

**outputs.py:**
```python
DRIVER_MODULES = {"hue": "show_service.hue_out:HueOut", "artnet": "show_service.artnet_lan:ArtNetLan"}
ENGINE_MODULE = "show_service.looks:LookEngine"
def attach(ctx: contract.BrainContext, *, palette_fn=None, look_fn=None, clock=None, features_fn=None,
           neutral: tuple[tuple[float, float, float], float] | None = None,   # artnet_out's P0 colour and hue depth
           registry=None, engine=None, driver_factories=None, mono=time.monotonic, **kw) -> Rig
class Rig:                                                 # Component "rig", tick_hz SLOW_TICK_HZ
    def provides(self) -> dict                             # {"rig": self.view}
    def configure(self, settings: dict) -> None            # show.yaml rig + looks -> enabled, engine.configure, drivers
    def start(self) -> None;  def tick(self, now_s: float) -> None;  def stop(self) -> None   # runners, then drivers
    def view(self) -> contract.RigView | None              # any thread
    def set_look(self, *, hue_depth: float | None = None, beat_lock: bool | None = None, reset: bool = False) -> None
                                                           # any thread: artnet_out's set_look signature
    def on_hit(self, d: contract.HitData) -> None          # show-rx
    def drivers_of(self, kind: str) -> list[contract.Driver]
    def frame_view(self, output_id: str) -> dict | None    # any thread: GET /api/v1/rig/frame's body
    def record_failsafe(self, node_id: str) -> dict        # show-brain: {"recorded": bool, "why"}
    def snapshot(self) -> dict                             # any thread (below)
    def persist(self) -> dict | None                       # S5.14: {"outputs": {id: driver.persist_state()}} (hue:
                                                           # what room asked to follow); everything else from config
```
- **Drivers** are built from `DRIVER_MODULES` by import path, lazily: `cls(output_id, *, ctx, clock, settings, shared,
  **kw)`. `settings` is show.yaml `rig.<kind>`. `shared` is one dict per Rig in which a driver module keeps
  cross-output objects (artnet_lan keeps its `Nodes` under `"artnet.nodes"`). A kind not in `DRIVERS_BUILT`, or a
  module that fails to import or construct: state `not_built`, with the reason, and the rest runs.
- **Runners.** One thread `show-out-<id>` per output that is enabled and built (and the rig enabled). The deadline loop
  of S5.3:
  - It sleeps on an `Event` with a timeout, so `on_hit` and a reconfigure wake it.
  - It records lateness (sent − planned) and the rate.
  - A raising `plan`, `evaluate` or `set_frame` is counted and logged at most every 30 s; three in a row within 10 s
    put the output in `error` for `RUNNER_BACKOFF_S` (5 s, a starting point: room's own error backoff), then it
    carries on.
  - It stops within 0.5 s of `stop` (the Event).
  - An output switched off, or whose driver kind changes, stops its runner and its driver (`driver.stop`); switched on,
    a new runner starts after `driver.start`.
- **The engine** is built once (`ENGINE_MODULE`), with the inputs above; `neutral` is artnet_out's
  `(OutConfig.palette or DEFAULT_RGB, HUE_DEPTH)` so a rig with no vibe looks exactly like the virtual Maestro.
- **`snapshot()`** (also `GET /api/v1/rig`): `{"enabled", "registry": {"revision", "path", "error", "problems",
  "counts": {outputs, nodes, types, fixtures, groups}}, "outputs": {id: OutputStatus}, "nodes": {id: NodeStatus},
  "engine": engine.stats(), "errors": {guard: n}}`. It is in `brain.components.rig` too.

**`__main__` (the Stage-5 wiring).** The modules are imported lazily through `_import`, so a missing one shows in
`modules` and is left out (no rig: exactly Stage 4).
1. After the director: `rig = outputs.attach(ctx, palette_fn=director.palette_fn, look_fn=getattr(director, "look_fn",
   None), clock=clock, features_fn=features.latest, neutral=...)`, `brain.add(rig)`. Brain add order: room, situations,
   autopilot, sources, ml, structure, listener, director, **rig**.
2. `ctx.set_look_out` becomes a fan-out: `vm.set_look(**kw)`, then `rig.set_look(**kw)`, each under its own guard.
3. The publish that already hands `music.hit` bodies to `Clock.observe_hit` also hands them to `rig.on_hit` (guard
   `rig.hit`).
4. Stop: `brain.stop()` stops the rig (runners, then drivers: Hue stops its area so the bridge gives the lamps back;
   Art-Net stops sending, so each node's failsafe takes over), then Art-Net out, rx, bus.
5. `--no-brain` / `SHOW_BRAIN=off` builds no rig.
6. `GET /api/v1/state` gains `rig` (`rig.snapshot()`, null without one).

**api.py's Stage-5 routes** (B12 alone edits api.py; paths have no `/show` prefix; no auth):

| Method and path | Body | 200 answer | Errors |
|---|---|---|---|
| `GET /api/v1/rig` | none | `rig.snapshot()` plus `"registry_view": RigView` | none (no rig: `{"enabled": false, "why": "..."}`) |
| `GET /api/v1/rig/frame?output=<id>` | none | the output's last frame as JSON: `{output, seq, t_send_ms, age_s, active, why, fixtures: {id: {intensity, hex, rgb, dmx: [int] \| null, t_eval_ms}}, universe: {port_address, length, channels: [int], to, virtual} \| null}` | 404 `unknown_output` `{choices}`; no frame yet: 200 with `fixtures: {}` |
| `POST /api/v1/rig/nodes/{id}/record-failsafe` | none | `{"recorded": bool, "why": str \| null}` (S5.7) | 404 `unknown_node` |
| `POST /api/v1/hue/stream` (room's hue adapter, mode show) | `{"lights": [fixture ids], "on": bool}` | `HueStatus` at once (it doesn't wait for streaming) | 400 `bad_request` when `lights` isn't a list of strings |
| `POST /api/v1/hue/release` | `{"lights"?: [fixture ids]}` (none: all) | `HueRelease`, once the lamps are writable (≤ `rig.hue.release_timeout_s` + 1) | none |
| `GET /api/v1/hue/status` | none | `HueStatus` | none |

- The hue routes go to the first enabled `hue` driver (`rig.drivers_of("hue")`; more than one hue output is a problem
  line in Stage 5). Their `brain.call` waits `release_timeout_s + 2` for release, the usual timeout otherwise.
- **With no hue output on** (no rig, `enabled: false`, or `not_built`): `stream` and `status` answer `HueStatus` with
  `state: "off"`, `enabled: false` and a `detail` naming the switch (`outputs.hue.enabled is false in
  config/room/fixtures.yaml`); `release` answers exactly as Stage 3 did: `{"released": true, "via": "room", ...}`.
- **Before the brain runs:** the rig routes answer 503 `starting`; `hue/release` answers as with no hue output (nothing
  can be streaming yet), as it does today.

**Tests (B12):**
- fixtures: the real fixtures.yaml loads with no problems and exactly patch.yaml's six devices (ids, labels, lights,
  screens, placements); every output off; the `around` group's order. A test OFL type (`tests/data/ofl/`: an RGBW
  par with dimmer, fine dimmer and a strobe channel with an Open range; a colour-wheel spot) maps to the right
  `AttrChannel`s and defaults; a bad mode, a matrix type, an unknown capability. Selectors: AND, OR, `!`, prefixes,
  `group:`, bad grammar. Latency: per-fixture over per-output over hand-set over unset, `align_pct`, `trim_ms`, the
  colour fallback, `min_pulse_ms`. Problems: Port-Address 0, a footprint past 512, an overlap, an unknown type, output
  or node, a `streamer` key. A bad edit keeps the last good view; hot reload on a type file's change. `hue_settings`
  over a fake patch.yaml.
- outputs: with fake drivers and a fake engine: runners only for enabled, built outputs; switching on and off at
  reload; a driver kind that isn't built; a driver that raises (counted, `error`, backoff, carries on); `on_hit`
  wakes only hits-capable runners; `plan` honoured (a fake clock); stop order; `set_look` reaches the engine; the
  snapshot's shape. Nothing is sent anywhere.
- main and api: the wiring with fakes (add order, the fan-out, the hit tap, stop order, `--no-brain`); every route's
  shape, the no-hue answers, 404s, 503 before the brain.

#### S5.4a As built: the registry (fixtures.py, ofl.py, tools/show/add_fixture.py; 16 Oct)

`show_service/fixtures.py` (the registry), `show_service/ofl.py` (the OFL reader; `fixtures.load_ofl` is
`ofl.load_ofl`), `tools/show/add_fixture.py`, `tests/test_fixtures.py` and `tests/test_ofl.py` (59 tests, under 1 s
niced, plus one network test that runs only with `SM_OFL_NET=1`). The test OFL types are trimmed copies of two real
fixtures (MIT), embedded in test_ofl.py instead of `tests/data/ofl/`: an RGBW par with a dimmer, a fine dimmer, a
strobe with an Open range, switching channels and a null gap, and a colour-wheel spot. On 16 Oct the tool fetched
three real fixtures from open-fixture-library.org into a scratch directory, and they loaded through the registry with
no problems.

**The S5.4 signatures, plus these optional additions:**
- `load(..., lamps=None)`, `Registry.poll(looks=None, force=False, lamps=None)` and `select(expr, rig, *, lamps=None)`
  take show.yaml's `lamps`.
  - With them, `role:` follows the director's rule: the fixture's own role, else `placement_roles[placement]`, else
    `default_role`, for lamps only. A written `role` that isn't in `lamps.roles` is a problem line.
  - Without them, `role:` matches written roles only.
  - **outputs.py and looks.py should pass show.yaml's `lamps`**, so that `"kind:lamp !role:key"` works by placement.
- `select` on a string that doesn't parse raises `ValueError("bad_selector: ...")`, as `parse_selector` does.
  looks.py writes the problem line, and the layer selects nothing (checked against looks.py on 16 Oct).
- `summary(view)` gives the Rig snapshot's `registry` block: `{revision, path, error, problems, counts}`.
- `Registry.hue_settings(out)` is `hue_settings` over the patch.yaml it last read. `Registry` also exposes `patch`,
  `latency` and `generation`.
- Also public: `parse_curve`, `Selector`, `Term`, and in ofl.py `parse(doc, mode, *, type_id, file) -> (TypeInfo,
  usable)`, `modes(doc)`, `find_mode` and `read`.

**Rulings where S5.2 left room:**
- **Selector order.** Members of the selector's positive `group:` terms come first, in the group's order, then the rest
  by `order`, then id. So `group:around` keeps its clockwise order for phase spread.
- **Left out:** a fixture with no type, or a type that can't be read (no file, no such mode, a file outside
  fixture-types/); an artnet fixture with no `address`; a hue fixture with no `light`; a fixture running past
  channel 512.
- **Kept unrouted:** a fixture naming an unknown output.
- **Kept inert:** a matrix type, with footprint 0. A matrix fixture's plain modes do work.
- **Not driven.** A channel's attribute comes from its first capability that isn't `NoFunction`. An intensity or
  emitter channel that mixes in another function (a Dimmer/Strobe) or runs backwards reads `other`, because driving it
  to full would strobe. It holds OFL's default, else its brightest intensity value, with a problem line saying to pin
  it.
- **Fine bytes.** A fine channel holds its byte of the coarse default, and 0 under `dmxValueResolution: 8bit`. On a
  24-bit channel only the first fine is driven.
- **Switching channels** are `other`. They hold the default of the channel that their controller's default selects.
- **`channels` leaves out null gaps** (and unknown names, with a problem line). The encoder starts from footprint
  zero bytes, as colour_mix does.
- **`TypeInfo.emitters` holds only fixtures.yaml's overrides** (`source: "type"`), and colour_mix fills the rest from
  its starting points. An override for an emitter the type doesn't have is a problem line.
- **`OutputConfig.settings` is as written.** patch.yaml's values aren't merged in; `hue_settings` merges them (B14
  calls it).
- **`built`** is `driver in DRIVERS_BUILT`; outputs.py refines it with the import.
- **The caps checks belong to outputs.py.** `hits: true` on a driver that takes no hits, and `min_attack_ms` only
  raising the driver's floor, need the driver's caps, which fixtures.py doesn't have.
- **A node at a loopback address on 6454** reads `virtual: true`, with a problem line, as artnet_lan treats it.
- **Latency.** Colour with nothing measured takes the intensity rise, with the source "<rise's source> (the intensity
  rise)". latency.json's colour and position entries are read from their `rise` edge, else from their own t10/t50.
- **`house`** needs `hex`; `intensity` defaults to 1.0.
- **`revision`** is the sha1 of fixtures.yaml, as the contract says. A change to a type file, latency.json or
  patch.yaml gives a new view (`poll` returns True) under the same revision. **Consumers key on `poll()`'s True, or on
  the view's identity, never on the revision.**
- **YAML loader.** YAML goes through libyaml's `CSafeLoader` when it is there: the same result, about 10x faster.

**The name scan (B16).** fixtures.py names the builtin type ids (`BUILTIN_TYPES`'s keys) in code; they are fixed
vocabulary, like `DRIVERS`. When the scan adds fixtures.yaml's type ids, it should subtract `fixtures.BUILTIN_TYPES`,
as test_fixtures.py's own scan of fixtures.py and ofl.py does.

**Costs** (niced, under the host's load):

| What | Cost |
|---|---|
| `poll` with no change (the 1 Hz rig tick) | p95 ~40-60 µs |
| a reload of the real file | p50 2.2 ms, p95 3.9 ms (about 20 ms with PyYAML's pure-Python loader) |
| a reload, 5 fixtures and 2 OFL types | p50 2.6 ms |
| `select("group:around")` | p50 28 µs |

**tools/show/add_fixture.py** (stdlib urllib, like fetch_models.py):
- `add_fixture.py <maker>/<model>` (or the fixture's site URL) saves `ofl/<maker>/<model>.json` under
  `--dest`, which defaults to `config/room/fixture-types/`. The JSON is re-serialised with indent 2.
- It lists every mode with show_service's own channel map (attribute, emitter, fine channel, default, colour wheel,
  problem lines), and prints the `types:` entry to paste.
- `--mode` shows one mode only. `--file` reads a local file and writes nothing. A saved copy that differs is kept
  unless `--force` is given.
- **It never edits fixtures.yaml.**
- Exit codes: 0 done; 2 bad arguments; 3 the fetch failed or the file isn't an OFL fixture; 4 a saved copy differs.

#### S5.4b As built: outputs.py, the wiring and the routes (c8, 16 Oct)

B12's `outputs.py` hadn't landed when the builders finished, so c8 built it to S5.4, with `tests/test_outputs.py` (13
tests: fake registry, engine and drivers, plus one test with the real registry, engine and Art-Net driver against an
ephemeral sink and a virtual node).
- **`Rig`** (component `rig`, 1 Hz): `attach(ctx, palette_fn, look_fn, clock, features_fn, neutral, registry,
  engine, driver_factories, driver_kw, fixtures_path, types_dir, latency_path, patch_path, mono, sleep)`. The registry,
  the engine and the drivers are imported lazily (`REGISTRY_MODULE`, `ENGINE_MODULE`, `DRIVER_MODULES`); a missing one
  reads `not_built` with its reason in the output's status, and `snapshot().modules` lists them.
- **Each tick** (and at once on a show.yaml change): `Registry.poll(looks, lamps=)`, `LookEngine.configure(looks,
  view, lamps=)` when the view or show.yaml's `looks`/`lamps` changed, then the outputs are reconciled: an enabled
  output whose kind is in `DRIVERS_BUILT` gets its driver (`cls(output_id, ctx=, clock=, settings=<show.yaml
  rig.<kind>>, shared=, mono=, **driver_kw[kind]`), `configure`, `start`, then its runner; one switched off, or whose
  kind changed, stops its runner, then its driver; a new view or new `rig.<kind>` settings reconfigure the running
  drivers (`set_settings` first where a driver has it) and wake their runners; then each driver's `tick`.
- **show.yaml `rig.enabled: false`** (or a value that isn't a bool, with a problem line) stops every output and
  makes `view()` None, so `ctx.rig()` is None and the director falls back to patch.yaml: the rollback.
- **The runner** (`show-out-<id>`): plan (a woken hit: `driver.plan_hit`, else `driver.on_hit` + `plan`), wait on
  its Event until `t - ahead`, `evaluate(<id>, t, driver.caps)`, sleep to `t`, `set_frame`. `ahead` (S5.14) is
  `max(BUILD_AHEAD_S, the runner's own evaluate p95 + sys.getswitchinterval())`, at most `BUILD_AHEAD_MAX_S` (4 ms, a
  starting point), re-read every 64 frames (`extra.runner.build_ahead_ms`): BUILD_AHEAD_S (1 ms, artnet_out's) alone
  made 16 pars late (p95 2.2 ms against the 1.5 ms budget). A plan more than 1 s in the past is re-anchored on now; a plan never repeats the last time
  (`MIN_STEP_S`). Three failures within 10 s: `error` for `RUNNER_BACKOFF_S` (5 s), then on. Stats per runner:
  frames, rate, lateness p50/p95 (sent − planned), hits, errors, under `OutputStatus.extra.runner`.
- **Hits:** `Rig.on_hit` (show-rx) hands every body to the engine's ring, and wakes a hits-capable runner only when
  `LookEngine.takes_hit(d, output)` says the look would flash it now **on that output** (S5.5a, c8's addition; per
  output since S5.14: an output with no fixture in `looks.hits.select` stays on its beat grid).
- **State across a restart** (S5.14): `persist` keeps each running driver's `persist_state()` by output id in
  state.json's `rig` (and the last one of an output switched off since); `restore` hands it to the driver's
  `restore_state` when that output is built again. Only hue_out has one (what room asked to follow).
- **`snapshot()`** is built on show-brain after each tick (any thread reads it); `snapshot_now()` builds one for
  `GET /api/v1/rig`. Keys: `enabled`, `registry` (`fixtures.summary`), `outputs` (an `OutputStatus` per configured
  output: `off` with the switch's words, `not_built` with the reason, else the driver's own with the runner's stats),
  `nodes`, `engine` (`LookEngine.stats()`), `errors`, `problems` (show.yaml, two hue outputs), `modules`,
  `tick_ms_p95`, `hue` (the HueStatus the routes answer).
- **Stop** returns an awaitable when a driver's stop is still running on show-brain (HueOut's PUT stop): brain.py
  awaits it (STOP_AWAIT_S), then the Art-Net socket is closed.
- **More than one enabled hue output** is a problem line; the Hue routes use the first by id.

**`__main__`** (S5.4's six steps, as specified):
1. `outputs` is the last of `BRAIN_MODULES`: brain add order room, situations, autopilot, sources, ml, structure,
   listener, director, **rig**. Its inputs: the director's `palette_fn` and `look_fn`, the Clock (it has `snapshot` and
   `next_beat(t, lead_s, gate)`), `Features.latest`, `neutral` = (artnet_out's `SHOW_PALETTE` or `DEFAULT_RGB`,
   `HUE_DEPTH`), `latency_path` = `--latency-file`, `patch_path` = artnet_out's (`SHOW_PATCH_FILE`), `driver_kw` =
   `{artnet: {state_file: <state dir>/nodes.json}, hue: {patch_path}}`. Tests add `brain_kw["outputs"]` on top
   (`driver_kw` merged per kind).
2. `ctx.set_look_out` is `Show._set_look_out`: Art-Net out's `set_look`, then the rig's, each guarded
   (`artnet.set_look`, `rig.set_look`).
3. The music.hit bodies that reach `Clock.observe_hit` reach `Rig.on_hit` (guard `rig.hit`).
4. Stop: the brain stops the rig first (reverse add order), then Art-Net out, rx, the clock, the bus.
5. `--no-brain` builds no rig; a missing `outputs.py` is exactly Stage 4.
6. `GET /api/v1/state` gains `rig`.

**api.py** (S5.4's table, plus one route): `GET /api/v1/rig`, `GET /api/v1/rig/frame?output=<id>` (404
`unknown_output` with `choices`; `?output=` missing names it), **`GET /api/v1/rig/dmx`** (c8's addition: the virtual
nodes' universes decoded from the packets the drivers kept, `{nodes: {node: {port_address: {seq, length, channels,
age_s}}}}`, the previz feed), `POST /api/v1/rig/nodes/{id}/record-failsafe` (404 `unknown_node` with `choices`),
`POST /api/v1/hue/stream`, `POST /api/v1/hue/release`, `GET /api/v1/hue/status`. With no hue output running, stream
and status answer HueStatus `off` (detail: `outputs.<id>.enabled is false in config/room/fixtures.yaml`, or the rig's
own switch, or the driver's `not_built` reason) and release answers as Stage 3 (`released: true, via: room`). A
running hue output's release goes through `brain.call` with `rig.hue.release_timeout_s + 2` (a timeout answers
`released: false, why: timeout`). Without a rig component, `GET /api/v1/rig` answers `{"enabled": false, "why"}`.

### S5.5 looks.py and emitters.py (B13)

**looks.py:**
```python
class LookEngine:
    def __init__(self, *, palette_fn=None, look_fn=None, clock=None, features_fn=None,
                 neutral: tuple[tuple[float, float, float], float] = ((1.0, 0.62, 0.32), 0.7),
                 mono=time.monotonic) -> None
    def configure(self, looks: dict, rig: contract.RigView) -> list[str]   # show-brain: compile the Program; problems
    def set_look(self, *, hue_depth=None, beat_lock=None, reset=False) -> None   # any thread (VirtualMaestro.set_look's)
    def on_hit(self, d: contract.HitData) -> None          # any thread (a deque per hits-capable output)
    def evaluate(self, output_id: str, t_send_s: float, caps: contract.DriverCaps) -> contract.Frame
                                                           # only ever from that output's runner
    def active(self, t_s: float) -> tuple[bool, str | None]   # any thread: the activity rule below
    def stats(self) -> dict                                # {"program": {revision, layers, problems}, "per_output":
                                                           #  {id: {"eval_us": {p50, p95}, "frames", "hits"}}}
```

**`evaluate(output, t)`**, per fixture of that output (kind `screen` never: rule 3):
1. **Times.** `ti = t + rise_ms/1000`, `tc = t + colour_ms/1000` (the fixture's `LatencyView`). `palette_fn` is called
   once per distinct time in this frame (uncalibrated: once).
2. **Base.** `rgb = palette(t)[fixture id]`, else `palette(t)["*"]`. With no vibe (`palette_fn` returns no dict): the
   neutral colour at full value. With a vibe but no entry (a fixture the director doesn't know): the fixture's `house`,
   else dark. `level = max(rgb at ti)`, `colour = rgb at tc / max(rgb at tc)` (or zeros). **S5.14:** the drop's
   unison hit (`look_fn().hit`) is laid over the director's colours here, per output: at once on an output whose caps
   take hits; on one that doesn't (Hue) at once only for a predicted drop line, else as a swell (§5.4).
3. **Activity** (`active`), exactly artnet_out's P0 idle rule: with a vibe, always active (its relaxed look, House and
   Low go out in a quiet room: §8.5 "never black"). With no vibe: inactive (`why`) when the features are older than 5 s
   (`no_features`), or the room is quiet (gate under 0.02 for 10 s, until it is back over 0.05: `quiet`): artnet_out's
   own constants (S5.1). **S5.14:** on an output whose caps say `idle_without_vibe` (artnet), no vibe is inactive
   whatever the sound (`no_vibe`): nothing room-side decides which DMX fixtures follow (§8.6). An inactive frame carries every fixture at its `house` look (or dark, intensity 0), with no
   layers or hits: that is what DMX shows idle (S5.7). The hue driver ignores an inactive frame's colours and holds
   the last active ones until it stops the area (S5.6).
4. **Layers** (show.yaml `looks.layers`, compiled; S5.10). For each layer whose `select` holds the fixture and that is
   `enabled`:
   - `attr: intensity` (the only one built in Stage 5; `colour` layers are kept for later and skipped with a problem
     line): `intensity *= 1 - d + d x shape(p)`;
   - `p` is the layer's phase at `ti`: `beat` (× 1/subdivision when `subdivide`), `bar`, `phrase` (the clock body via
     clock.py's helpers), or `time` (seconds over its period). Phase spread: member `i` of `n` in the layer's ordered
     selection adds `spread x i / n` cycles;
   - `d` is the layer's `depth`: a number, or a `contract.LOOK_INPUTS` word (`swell` = the `set_look_out` depth, or
     the neutral depth with no vibe; `motion.amount`, `accents.density`, `look.spread` from `look_fn()`; `energy` from
     the features), × `depth_scale` (default 1);
   - **`needs`** and the clock's gate (§5.4's confidence policy): `beat` runs at full depth on gate `beat`; on gate
     `dmx` only on outputs whose `caps.hits`, × `looks.middle_depth`; on `none`, or with `beat_lock` false, it uses
     `without_beat` (`energy`: d × the energy smoothed with τ 1 s, as artnet_out does; `none`: d = 0). `grid` needs
     any grid; `none` free-runs on time at the last tempo when there is none. **S5.14:** `beat_lock` is the vibe's own
     (the engine reads the gate itself), and `swell` reads `beat_depth` (the accent depth before the gate) while the
     layer runs beat-locked, so the gate-`dmx` branch has a depth; every oscillator on an output with
     `caps.min_period_ms` (hue: 2 × attack) runs no faster than that;
   - **parameter changes** (depth, subdivision, motion speed and amount, spread) glide to their new values over the
     look's `transition` (its `beats` at the clock's tempo, else its `seconds`; `cut`: at once), so a look change
     crossfades in beats. The base colour's crossfade is the director's (`palette_fn`).
5. **Shapes** (`p` in [0, 1), 1 at the beat): `sine` 0.5 + 0.5 cos 2πp; `saw` 1 − p; `square` 1 for p < 0.5; `triangle`
   1 − 2 min(p, 1 − p); `swell`: artnet_out's envelope, max(smoothstep(1 − (b − ti)/attack) while the next beat `b` is
   within `attack`, exp(−(ti − b_prev)/decay)), with `attack = max(layer attack_ms, caps.min_attack_ms)`. On an output
   whose period at the current subdivision is under 2 × attack, the next coarser subdivision is used.
6. **Hits** (only when `caps.hits`; §5.4: DMX yes, Hue never). For each hit in the output's deque with `strength ≥
   min_strength`, a band in `bands` (unset: any), while the look's `accents.style` is in `looks.hits.styles` and the
   gate isn't `none`: `flash = depth x strength`, held for `min_pulse_ms` from the hit's time, then decaying with
   `decay_ms`. `intensity = max(intensity, min(1, level + flash))` (HTP). A hit arrives 8-20 ms after the sound
   (§5.1); it is shown at once, never delayed to its own latency.
7. **Master and caps** (kind `lamp` only): × `looks.master`; `caps.flash_hz_max` (unset: off) lets an intensity rise
   of more than 0.5 happen at most that often per fixture. Then each output's rise limit: an intensity rise is never
   faster than `caps.min_attack_ms` (a slew limit per fixture, on the runner's own state).
8. **Encode.** `FixtureFrame{intensity, colour, rgb = colour x intensity, t_eval_ms}`, and for a type with channels
   `dmx = emitters.encode(fixture, type, intensity, colour)`.

**emitters.py** (pure):
```python
EMITTER_DEFAULTS: dict[str, dict]          # emitter -> {"xy": [x, y], "Y": relative luminance}: typical LED datasheet
                                           # values, "starting point; measure" (a type's emitters override them)
def srgb_to_linear(c: float) -> float; def linear_to_srgb(c: float) -> float
def solve(colour: Rgb, t: contract.TypeInfo, wb: dict[str, float]) -> tuple[dict[str, float], float]
    # emitter -> 0..1 (linear light), and the scale the solve lost (folded into intensity when the type has no dimmer)
def wheel_slot(colour: Rgb, slots: list[dict]) -> int      # the nearest colour slot's DMX value (OKLab distance)
def curve_out(v: float, curve: str) -> float               # linear light -> DMX fraction: srgb | linear | gamma:<g>
def encode(f: contract.FixtureInfo, t: contract.TypeInfo, intensity: float, colour: Rgb) -> bytes
```
- **Colour to emitters** (§9.2), in linear light (sRGB decoded):
  - **RGB** (red, green, blue only): straight across.
  - **RGBW:** white = min(r, g, b), subtracted from each.
  - **Any other set** (amber, lime, cyan, the whites...): non-negative least squares on the emitters' XYZ (from `xy`,
    `Y`) against the colour's XYZ, bounded to [0, 1] (`scipy.optimize.lsq_linear`, scipy is in the venv), scaled so
    the largest is 1 with the chromaticity kept. `uv` never takes part (it is pinned to its default).
  - **A colour wheel:** the nearest colour slot in OKLab; the wheel's channel takes that slot's value.
  - Then the fixture's `wb` gains, then the curve (the fixture's, else the type's, else `srgb`: with it, RGB values
    reach DMX exactly as the palette has them).
- **Intensity.** A type with an intensity channel gets it there (16-bit when it has a fine channel; linear) and its
  emitters at full colour. A type without one gets the emitters × intensity (before the curve).
- **The bytes.** Start from every channel's default, apply the fixture's `pin`s, then the driven channels. The result
  is always `footprint` bytes.
- Solves are cached on the colour quantised to 0.005 (a fixture's colour moves slowly).

**Tests (B13)** (fake palette_fn, look_fn, clock and features; `-s` prints the budgets):
- **The screens rule:** a `screen` fixture never appears in a Frame, whatever the layers, master, caps and hits.
- **Parity with the virtual Maestro:** with no vibe and the neutral colour, Hue lamps' `rgb` equals artnet_out's hue
  channels (÷ 255) for the same clock, features and depth, within 1/255, through a beat, energy without a beat, and
  the quiet and no-features idles.
- Latency: a lamp with `rise_ms` 100 is evaluated 100 ms ahead (its swell peaks 100 ms before the beat in send time);
  per-lamp leads differ within one frame.
- The gate policy: `beat` everywhere; `dmx` only on hits outputs at `middle_depth`; `none` and `beat_lock` false →
  `without_beat`.
- Shapes, subdivision, the 2 × attack rule, phase spread across `group:around` (each lamp a quarter cycle apart at
  spread 1), glides over a transition in beats and a cut.
- Hits: none on an output without `caps.hits`; HTP over the base; `min_pulse_ms`; styles and strength filters.
- Caps and the slew limit (`min_attack_ms` 80: no rise faster).
- emitters: RGB identity through `srgb`; RGBW white extraction; RGBA/RGBAW NNLS hits the target chromaticity within
  0.005 xy for in-gamut colours and is non-negative; the wheel's nearest slot; wb; curves; 16-bit fine channels; pins
  and defaults (a shutter's Open value); the footprint length.

#### S5.5a As built (B13, 16 Oct)

`show_service/looks.py` (the engine) and **`show_service/colour_mix.py`**, which is S5.5's emitters.py under the name
the build assigned, with the same API (`EMITTER_DEFAULTS`, `srgb_to_linear`, `linear_to_srgb`, `solve`, `wheel_slot`,
`curve_out`, `encode`). Tests: `tests/test_looks.py` (37) and `tests/test_colour_mix.py` (18). Nothing is committed.
- **The S5.5 signatures, plus:**
  - `LookEngine.configure(looks, rig, *, lamps=None)`. Pass show.yaml's `lamps`, so that `role:` selectors (through
    `fixtures.select(..., lamps=)`) and the allocation fallback below follow placements as the director does.
  - `stats()` also carries `errors` (per failing input), and each output's `held` and `why`.
  - Public helpers: `looks.shape(fx, cycles, period_s, attack_s, decay_s)`, `looks.role_of(fixture, lamps)` and
    `looks.DEFAULT_LAYERS`.
- **Selectors** go through `fixtures.select` only (one copy of the grammar). If fixtures.py can't be imported, every
  layer and the hits select nothing, each with a problem line, and the rest runs.
- **Rulings where S5.5 left room:**
  - **Allocation.** A fixture the director's `palette_fn` doesn't name takes, in order: a lamp the director gives the
    same role (`look_fn().lamps[*].role`), then a registry lamp with the same written role or placement, then the same
    decided role (same screen first, then zone, then id), then `"*"`, then its `house`, else dark. The six existing
    devices are all named by the director, so nothing changes for them.
  - **Held.** With a vibe but stale or silent features, frames stay active with no layers and no hits, as artnet_out
    sends `hue_depth` 0 then.
  - **Resolve.** Oscillators multiply in file order, as S5.5 step 4 says. Hits are HTP over that. Colour is the base's
    only (LTP, for later colour layers).
  - **Phase spread** is a lag: member `i` runs `spread x i / n` cycles behind member 0, so the wave walks the group in
    its order (clockwise round `group:around`).
  - **The rise limit** is a smoothstep's peak slope, 1.5 / `min_attack_ms` (`contract.SLEW_PEAK`, hue_out's own limit
    too since S5.14). So the swell itself (attack ≥ `min_attack_ms`) is never clipped, and parity holds exactly. A
    full 0→1 step takes at least 53 ms on Hue.
  - **The 2 x attack rule** applies to every subdivided beat layer, with attack = max(layer `attack_ms`,
    `caps.min_attack_ms`).
  - **Glides.** Depth and spread glide linearly over the look's transition. Subdivision and a period picked from a
    `[slow, fast]` pair crossfade the old wave into the new, so a wave never jumps phase. That period moves in
    halvings of the slow one (a power of two), so `[8, 1]` gives 8, 4, 2 or 1 bars and stays on bar lines.
  - **`needs: grid`** without a grid uses `without_beat`. **`needs: none`** free-runs on the clock's last body
    (FREE_RUN_BPM 120 before any). `needs` defaults to `beat` on phase beat, else `none`; `without_beat` to `none`.
  - **No `looks.layers` key at all** gives artnet_out's swell (`DEFAULT_LAYERS`); an empty list gives no layers. No
    `hits` block: no hits.
  - **Hits** are evaluated at the send time, from the hit's own time (a hit 20 ms late is held 20 ms less). Flashes
    are dropped 8 decay constants after their hold.
  - **`flash_hz_max`** counts a rise of more than 0.5 above the fixture's low since its last counted rise.
  - **Inactive frames** carry the house look and leave the slew memory alone. So a Hue lamp coming back from an idle
    isn't ramped from the house look the bridge never showed.
- **colour_mix:**
  - **NNLS** uses `scipy.optimize.nnls` with a 1e-4 ridge, normalised so the largest emitter is 1. The ridge gives a
    unique answer that moves smoothly with the colour (no metameric jumps). Measured chromaticity error is ≤ 0.001 xy.
  - **`solve`'s scale** is the factor the raw solve was multiplied by (1.0 for RGB and RGBW). It is informational, not
    folded into intensity: a dimmer-less type already gets emitters × intensity, which gives the same light as a
    dimmer.
  - **A dimmer-less RGB or RGBW type** encodes the frame's `rgb` (colour × intensity) straight across, so through
    `srgb` an RGB par shows exactly what the palette has. A dimmer-less least-squares type takes its emitters ×
    intensity in linear light.
  - **The wheel:** the nearest slot of all in OKLab, white (Open) slots included (S5.14: whites used to be compared
    only under the threshold, so 4000 K took yellow and a pale pink orange). Two thresholds force the white slot: a
    colour under OKLCh chroma 0.05 (`WHEEL_WHITE_C`), and one whose nearest slot is a coloured one more than 60° away
    in hue (`WHEEL_MAX_HUE_DEG`), when there is a white slot. Both are starting points.
  - **Measured emitters** (S5.14): a type that declares any emitter's `xy` or `Y` goes through the least-squares
    solve, RGB and RGBW sets included (a warm-white W at xy 0.437, 0.404 turned a 6500 K request into ~3000 K
    through white = min(r, g, b)). The solve is in linear light and `curve` encodes it, so the result is right only
    when the type's `curve` matches the fixture's response: `srgb` fits one that dims like sRGB; a linear-dimming RGBA
    misses #ff8040 by ~0.1 xy under it. fixtures.yaml says to measure each type's curve.
  - **`EMITTER_DEFAULTS`** are starting points (typical datasheet values; measure). A type's `emitters` override them
    per emitter, and an override that doesn't read is ignored.
  - **Caches.** The plan per type, the template per fixture (defaults, then pins) and the colour part per fixture
    (the emitters at full colour and the wheel, keyed on the exact colour) are all cached by identity. So a frame with
    an unchanged colour only rewrites the dimmer. The NNLS solves are cached on the colour quantised to 0.005.
- **Parity, checked** (test_looks): the real `VirtualMaestro` and the engine are driven at the same planned send times
  with the same fake clock and features, using show.yaml's `pulse` layer and the patch.yaml hue devices. Through beat
  lock, gate dmx and gate none (energy), the quiet idle, the return and the no-features idle, every Hue `rgb` equals
  artnet_out's hue channels ÷ 255 within ½/255 (byte rounding), and the active flags and `why` agree.
- **Costs** (`-s`; niced, load average ~2 on 20 threads; I couldn't confirm the render was running):

  | What | p50 | p95 | Budget |
  |---|---|---|---|
  | `evaluate`, 4 Hue lamps, show.yaml's looks | 42-47 µs | 85-103 µs | 150 µs |
  | `evaluate`, 32 RGBW pars with dimmers, a hit every 230 ms | 259-326 µs | 413-643 µs | 600 µs |
  | an uncached NNLS solve (RGBA, RGBAW, RGBLWw) | 83-94 µs | | |

  The 32-par p95 went over 600 µs in one run of four, under load.
- **For c8:**
  - The name scan (B16, S5.8) lists `emitters.py`; it should list `colour_mix.py`. (Done: S5.8, c8's part.)
  - The scan with fixtures.yaml's ids, outputs, groups, zones, screens and placements, plus show.yaml's layer ids,
    finds nothing in looks.py or colour_mix.py.
- **c8's integration rulings (16 Oct), found by the Stage-5 e2e:**
  - **No hits under House lights or Lights low.** A `music.hit` flashes only while the look (LookData) has neither
    `house` nor `low`: they are steady, as the director's own drop hit and its swell depth (0 under House and Low)
    already are. Before this, DMX flashed up to level + 0.5 over Lights low. `test_no_hits_under_house_lights_or_lights_low`.
    Since S5.14 House and Low also run **no layer** (like a held frame), and no hit flashes over the pre-drop dip
    (LookData `dip`).
  - **`LookEngine.takes_hit(d)`** (any thread, a few µs): would this hit flash now (the hits block's strength and
    bands, the look's style, House and Low, the clock's gate)? `Rig.on_hit` wakes the hit outputs' runners only for
    these, so the many hits a look doesn't show (every band's onsets reach show-rx) never pull DMX frames off the beat
    grid. `test_takes_hit_follows_the_hits_block_the_look_and_the_gate`.

### S5.6 hue_out.py (B14)

Entertainment streaming from show_service (§9.4, P5), following room_service/hue/output.py's rules exactly where they
apply. The room side is built (68, 16 Oct; S5.6b is its hand-over contract, which wins over anything below): room's hue
adapter, with patch.yaml `hue.streamer: show`, sends `POST /api/v1/hue/stream {lights, on}` (the complete set of `hue.*`
lamps that follow, every time), calls `POST /api/v1/hue/release {lights}` before writing a lamp, polls `GET
/api/v1/hue/status` (2 s timeout) while any lamp follows, and reads our `state`: exactly `idle`, `armed`, `streaming`
or `taken_over` (`taken_over` matters to it).
```python
class HueOut:                                              # contract.Driver, kind "hue"
    caps = {"hits": False, "min_attack_ms": 80.0, "grid": "fixed", "rate_hz": 50.0, "keepalive_s": None}   # §5.4, §9.4
    def __init__(self, output_id: str, *, ctx, clock=None, settings: dict | None = None, shared: dict | None = None,
                 client_factory=None,                      # (BridgeSecrets) -> a HueClient-like (async get/put, and
                                                           #  inventory for room's resolve_area)
                 streamer_factory=None,                    # () -> an xy_streamer-like (connect/send_colors/disconnect,
                                                           #  is_connected)
                 secrets_loader=None,                      # (bridge id, host) -> BridgeSecrets | None (S5.6b, S5.6c)
                 patch_path: Path = contract.PATCH_FILE,
                 mono=time.monotonic) -> None
    # Driver: configure, start, stop, plan, set_frame, tick, status (contract.Driver)
    def request(self, lights: list[str], on: bool) -> contract.HueStatus     # show-brain (POST /api/v1/hue/stream)
    async def release(self, lights: list[str] | None) -> contract.HueRelease # show-brain (POST /api/v1/hue/release)
    def hue_status(self) -> contract.HueStatus                              # any thread
```
- **Defaults are room's own** (S5.6b). `client_factory` and `streamer_factory` default to room_service.hue.client's
  `HueClient` and room_service.hue.stream's `xy_streamer` (hue-entertainment 0.1.2's `HueDtlsStreamer` in the
  xy+brightness colour space, imported when the area first starts). Secrets: room's `BridgeSecrets.load(<patch.yaml
  hue.bridge>, <patch.yaml hue.host>)`, read-only, as room's DmxStack calls it (S5.6c). No secrets for the bridge: state `no_config`, detail "the bridge … isn't paired (python -m
  room_service.hue.pair)".
- **Settings.** `fixtures.hue_settings` gives `bridge`, `area`, `gamma`, `stop_after_s` and `streamer`; show.yaml
  `rig.hue` gives `takeover_check_s`, `start_wait_s`, `release_settle_s`, `release_timeout_s`, `error_backoff_s`.
- **The control task** (an asyncio task on `ctx.loop`, started by `start()`, woken by `request`, `release`, a
  reconfigure, or every 0.5 s) decides the state (`contract.OUTPUT_STATES`):
  1. `enabled` false → `off` (stop the area if we hold it).
  2. patch.yaml `hue.streamer` isn't `show` → `waiting_room`: room_service streams the area; we never start (and stop
     within one reload if we were streaming: the flip back is the rollback).
  3. No wanted lamp → `idle` (stop the area if we hold it).
  4. Wanted, but the engine has been inactive (frames with `active` false) for `stop_after_s` (patch.yaml's 10 s) →
     stop the area, `waiting_input`: the bridge gives the lamps back to the house, exactly as room's P0 path did.
     Wanted lamps start only while frames are active (room's output started only on fresh Art-Net); active again →
     start.
  5. Otherwise start, if not streaming: `starting` → resolve the area's channels (`resolve_area`) → GET the area: if it
     is already active (someone else, or room's output still stopping after the switch), wait up to `start_wait_s`
     for it to go inactive, else `taken_over` → PUT `{"action": "start"}` → `connect` in an executor (the DTLS
     handshake blocks) → note the area's `active_streamer` → `streaming`. (S5.6c: patch.yaml is read again on each
     start-wait poll and right after the handshake; flipped back to room meanwhile, the area is stopped before a frame.)
  6. While streaming, every `takeover_check_s`: the area's status isn't `active`, or its `active_streamer` changed →
     disconnect, `taken_over`. Nothing restarts until the wanted set changes (room re-applying the mode). **Never
     fight**: we never stop or take an area someone else holds.
  7. Any exception: `error`, the stream stopped, retried after `error_backoff_s`.
- **Frames** (the runner thread; `plan` = every 1/rate_hz while `starting` or `streaming`, else every 0.1 s, which is
  enough to track activity): `set_frame` sends `(channel, *xy_bri(rgb,
  gamma))` for every wanted lamp the area has a channel for, **every frame, changed or not** (§5.4: a lost packet heals
  at the next one). Only while `streaming`; otherwise it returns at once. Inactive frames keep sending the last active
  colours until step 4 stops the area (room's input held its last values the same way). The send map (light →
  channel, wanted) is swapped by reference by the control task. No hits ever reach Hue (`caps.hits` false); the
  engine's slew limit keeps every rise ≥ 80 ms (§5.4).
- **`request(lights, on)`**: `lights` is the complete set that follows (not a delta); wanted = the given ids that are hue
  fixtures on this output (others listed in `unknown`), or none when `on` is false or the list is empty. It answers at
  once (the route: `HueAnswer`, S5.6b); the state follows.
- **`release(lights)`** (§9.4 step 3): remove them from wanted. `released: true` only once the lamps are writable
  (S5.6b): our stream is over, the bridge reads the area inactive, and `release_settle_s` (1 s, the bridge's
  post-entertainment write) has passed since.
  - No stop of ours pending (none since our last start, or one already seen inactive) → the area is still read once,
    whenever a client and an area are known (S5.6c): inactive → `released: true` at once; active (we don't hold it)
    → `why: "taken_over"`. Before our first start (no client or area yet) → `released: true` with no read.
  - Wanted now empty → the control task disconnects and PUTs `{"action": "stop"}` (`releasing`); the area is read every
    0.25 s until it reads inactive, then `release_settle_s` → `released: true`. The same when the control task stopped
    the area a moment before the release arrived (room's order: the new set first, then the release).
  - Other lamps still follow → `{"released": false, "why": "shared_area", "still_following": [...]}`: the bridge holds
    every lamp of an area while it streams, so one lamp can't be given back alone. room's own HueOutput has the same
    limit; room's adapter then reads the lamp's mode from the bridge and says `unconfirmed`.
  - Another app holds the area → `why: "taken_over"`; the bridge doesn't answer → `why: "bridge_unreachable"`; past
    `release_timeout_s` (at most `contract.HUE_RELEASE_MAX_S`, 17 s) → `why: "timeout"`.
- **Stop** (show_service stopping, or the output switched off): disconnect and PUT stop, so the bridge gives the lamps
  back.
- **Status** (`OutputStatus.extra` = `HueStatus`, `/api/v1/rig` `.hue`): state, detail, enabled, wanted, `asked`
  (what room asked, unfiltered; S5.6c), streaming, area, channels, frames, dtls, unknown, and (S5.6b) `room_state` (the
  four-word state the routes answer) and `words`.

**Tests (B14)**, only with fakes (rule: **never the real bridge, never an Entertainment stream**): a fake client
(aiohttp-free: `get`, `put` recording calls, an area whose status and `active_streamer` the test sets), a fake
streamer (records `connect` and every `send_colors`), and secrets in `tmp_path`. A guard in the test module makes any
default factory raise.
- the state machine: off, waiting_room (and stopping when the switch flips back), idle, starting → streaming,
  waiting_input after `stop_after_s` of inactive frames and back, an area already active (waited, then taken_over),
  takeover while streaming (disconnect; no restart until the wanted set changes), error and backoff;
- frames: 50 Hz continuous with unchanged colours, only wanted lamps with channels, `xy_bri`'s values, nothing while
  not streaming;
- release: none streaming, all (stop, inactive, settle, released), shared area, unreachable, timeout;
- request: unknown ids; `on` false;
- stop gives the area back.

**requirements.txt** (B14's block): `hue-entertainment==0.1.2` and its pins as installed in `~/.venvs/sm-room`
(cryptography, cffi, pycparser, zeroconf, ifaddr), installed under `flock … nice -n 19`.

#### S5.6a As built (B14, 16 Oct)

`show_service/hue_out.py`, `tests/test_hue_out.py` (47 tests, fakes only; ~10 s niced). The requirements block is
appended and installed in `~/.venvs/sm-show` (hue-entertainment 0.1.2, cryptography 50.0.2, cffi 2.1.1, pycparser 3.11,
zeroconf 0.151.5, ifaddr 0.2.0; aiohttp was already pinned).

Where it differs from the spec above (the first three bullets are superseded by S5.6b, kept here as history):
- ~~**Copied, not imported**~~ (B14's brief). Since S5.6b hue_out **imports** room's `room_service.hue.{stream, client,
  colour}`; the copies and their two parity tests are gone.
- ~~**The area resolve has no name fallback**~~. Since S5.6b it is room's `resolve_area`, unchanged: the configured
  `area` when the bridge has it, else the area named "Screen Machine", else the only one (the detail says when the
  streamed area isn't patch.yaml's). No area found, or none of the asked lamps having a channel in the area, reads
  `no_config` and is retried after `error_backoff_s`.
- **Secrets** follow room's rule (since S5.6b, room's own `BridgeSecrets.load`): the file for the bridge id, else the
  only file. ~~The bridge's address is the pairing file's; patch.yaml's `host` is never read.~~ Since S5.6c,
  patch.yaml's `hue.host` wins over the file's address, as in room. A file that doesn't read is `no_config` ("the
  pairing file … doesn't read"); no key ever reaches a log or a detail.
- **Constructor:** it also takes `patch_path` (default `contract.PATCH_FILE`) and `secrets_loader` (S5.6b; it
  replaced `secrets_dir`), and ignores any other `**kw`.
  - The driver reads patch.yaml itself (an mtime poll on each control step, 0.5 s), because `RigView` doesn't
    carry it. So the switch back to `streamer: room` stops the area within about 0.5 s.
  - It calls `fixtures.hue_settings(out, patch)` when that is importable, else its own copy of the same rule.
    `streamer` always comes from patch.yaml; unset means `room`.
  - show.yaml `rig.hue` is read from `ctx.settings` at `configure` and on every `tick`, else from the constructor's
    `settings`. Each bad or missing number falls back to show.yaml's own starting point.
- **`stop()`:** the DTLS session closes at once, synchronously. The PUT stop is async.
  - On show-brain, `stop()` returns the `asyncio.Task` that finishes the PUT. **B12: `Rig.stop` should return or
    gather it** so that `brain._shutdown` awaits it, as it already awaits an awaitable stop. Otherwise the loop
    teardown may cancel the PUT, and the bridge then ends the unfed area by itself.
  - From another thread, `stop()` waits up to `timeout`.
  - A DTLS handshake still in its executor is waited for (`CONNECT_WAIT_S`), then closed.
- **The driver keeps its own rise limit.** `max(rgb)` never rises faster than `contract.SLEW_PEAK` (1.5) × full scale
  per `caps.min_attack_ms` (80 ms, or the output's higher value): the engine's own limit, one shared constant, so it
  changes nothing when the engine complies. Falls are never slowed. (Until S5.14 it was 1 × full scale per attack,
  which clipped the engine's own swell whenever depth × level > 2/3: Disco's 0.9 by up to 0.11 mid-attack.
  `test_sent_brightness_matches_the_virtual_maestros_hue_channels` now checks what set_frame sends.)
- **Caps** (S5.14): `min_period_ms` = 2 × the attack (no oscillator on a Hue output cycles faster: "never strobes",
  §5.4) and `idle_without_vibe` false (room's hue adapter decides which lamps follow).
- **What room asked survives a restart** (S5.14): `persist_state()` / `restore_state(saved)` through the Rig and
  state.json, taken back only while patch.yaml says `hue.streamer: show`. `request` and `release` call
  `ctx.persist`. So `scripts/ctl restart show`, a crash, or the output switched off and on comes back `streaming`
  without room's next mode apply (room re-sends only then). Room's side of the re-sync is 68's (S5.12 step 7), now
  built against `lights` and `boot_id` (S5.6c).
- **Never fight, extended.** Before every PUT stop the area is read: an area that another `active_streamer` holds is
  never stopped.
  - A dropped DTLS session is handled three ways: if the bridge already ended the area, it restarts (room's rule); if
    the area is still ours, it is stopped and restarted; if someone else holds it, the output goes `taken_over`.
  - **Limit:** room_service and show_service pair with the same application key, so `active_streamer` is the same
    `auth_v1` rid for both. The check sees other apps (the Hue app), not room. The exclusive `hue.streamer` switch is
    what keeps the two apart (S5.9).
- **Release.** A release whose stop fails answers `bridge_unreachable`, but only after the driver's own retry has also
  failed. The stop is then retried after the backoff (`error` until it succeeds). (S5.6b: the inactive check and the
  settle now apply to every release after our stop, however the stop started.)
- **`request` and `release`** raise `ValueError("bad_request: ...")` when `lights` isn't a list of strings.
- A `kind: screen` fixture never joins the stream: it reads as `unknown` (the screens rule).

**Costs** (`-s`, niced, under the host's load):

| What | p50 | p95 | Budget (p95) |
|---|---|---|---|
| `set_frame`, 4 lamps, beat pulse (the xy is cached on the full-level colour, so a pulse computes only the brightness) | ~30 µs | 70-92 µs | 100 µs |
| `set_frame`, 4 lamps, colour moving every frame (worst case) | ~45 µs | 85-125 µs | 100 µs |
| a real-time 50 Hz runner (test thread) | lateness 0.15 ms | lateness 0.2 ms | 1.5 ms |

- The worst case's p95 is scheduling jitter under nice 19; production runners run at nice 0.
- The real-time runner measured 50.0 Hz.

#### S5.6b Aligned with room: 68's hand-over contract (16 Oct)

68 built room's side (517 room tests; it loads at the announced Flask restart, S5.12 step 4; `hue.streamer: room`, the
default, changes nothing). Its contract, and what hue_out does for each point:

1. **Room's Hue code is imported, not copied.**
   - From `room_service.hue.stream`: `xy_bri`, `xy_streamer`, `GAMMA` and `COLOR_SPACE_BYTE`.
   - From `room_service.hue.client`: `BridgeSecrets`, `HueClient`, `HueError` and `resolve_area`.
   - From `room_service.hue.colour`: `rgb_to_xy`, which the xy cache in `set_frame` uses.
   - These modules need only the stdlib, aiohttp and hue-entertainment 0.1.2. All three are pinned in
     requirements.txt and installed in `~/.venvs/sm-show`; nothing new was needed.
   - Importing hue_out loads `room_service`, `room_service.hue` and `.client`, `.colour`, `.pair` and `.stream`, and
     nothing else of room_service. A test checks this in a fresh interpreter. `scripts/ctl` runs `python -m
     show_service` from the repo root, so `room_service` is importable there.
   - hue_out is still loaded lazily, only when a hue output is enabled. If it can't import, the output reads
     `not_built` with the reason, and everything else runs.
2. **The pairing is room's, read-only.**
   - hue_out reads it with `BridgeSecrets.load(<patch.yaml hue.bridge>, <patch.yaml hue.host>)`, which uses the file
     `~/.config/screen-machine/hue/fc268cfffe106fa2.json` with patch.yaml's host over the file's (S5.6c: exactly
     room's call).
   - `HueOut(secrets_loader=…)` replaces `secrets_dir`.
   - Tests point `room_service.hue.client.SECRETS_DIR` at `tmp_path` and never read the real files.
3. **The route states** (`contract.hue_answer`, `contract.HUE_ROOM_STATES`).
   - `POST /api/v1/hue/stream` and `GET /api/v1/hue/status` answer `HueAnswer`: `{state, lights, boot_id, words,
     detail, wanted, unknown}` (`lights` and `boot_id`: 68's addendum, S5.6c). `state` is always exactly one of these:

     | `state` | driver states | words |
     |---|---|---|
     | `streaming` | `streaming` | "Following the music" |
     | `armed` (following but silent) | `waiting_input` (no sound yet), `starting`, `idle` a step after a request, `error` (retried) | "Ready: waiting for the music" |
     | `taken_over` | `taken_over` | "Taken over by another app" |
     | `idle` (following nothing) | nothing asked, or `off`, `waiting_room`, `no_config`, `not_built` | "Not following" |

   - The richer driver state stays in `GET /api/v1/rig` `.hue`, the HueStatus. It also carries `room_state` and
     `words`, for the console and the card.
   - `HueRelease.state` uses the same four words.
   - With no rig, or no hue output running, the routes answer `idle` with a `detail` naming the switch.
4. **Release.**
   - `{released: true, state}` only once the bridge reads the area inactive, plus `release_settle_s` (1 s).
   - This holds whichever came first: room's order is the new set first, then the release, so the control task often
     stops the area before the release arrives. The race this fixes: before, such a release answered `released` at
     once, before the bridge's own post-entertainment write.
   - A stop first seen inactive more than `OLD_STOP_S` (5 s) after it was made counts as inactive since the stop. So a
     release long after the music stopped answers after a single GET.
   - Otherwise `released: false` with `why`: `shared_area`, `taken_over` (new: another app holds the area, so the lamps
     aren't writable; since S5.6c also read from the bridge when no stop of ours is pending), `bridge_unreachable`,
     `timeout`, or (S5.6c) `starting` before the brain runs under `hue.streamer: show`.
   - Never longer than `release_timeout_s`, capped at `contract.HUE_RELEASE_MAX_S` (17 s). The route adds 2 s, which
     is at most 19 s against room's 20 s.
5. **Takeover.**
   - `active_streamer.rid` is captured right after our own `action: start`, before the DTLS handshake, as output.py
     does. If it is missing then, it is captured after the handshake.
   - The area is read again after the handshake. If it isn't active, or another streamer holds it, the output goes
     `taken_over` at once and is never fought: no stop, and nothing restarts until room asks for a different set.
   - Then every `takeover_check_s` (2 s), as before.
6. **Stops.**
   - **Output switched off** (fixtures.yaml `outputs.hue.enabled: false`): `configure` stops the frames at once, and
     the control task stops the area. In production the Rig's reconcile stops the driver at the hot reload, within
     about 2 s of the edit.
     - A release in the next moments waits for that stop plus `rig.hue.release_settle_s`
       (`Rig._released_after_stop`).
     - What room asks while the output is off is kept, so turning it on again follows room's latest set.
   - **Flip back to `hue.streamer: room`** (the rollback; room sends no release, and its HueOutput simply takes over):
     - the frames stop at once;
     - the area is stopped within about 0.5 s;
     - what room asked is forgotten and persisted that way. A later flip to `show` therefore never resumes a stale set;
       room's reconcile re-applies its `dmx` members then.
   - A request polls patch.yaml first, so a set room POSTs right after its own flip to `show` answers `armed`, not
     a stale `idle`.
7. **Restore** of the persisted set happens only while patch.yaml `hue.streamer` is `show`. This is unchanged from
   S5.14.

Tests (fakes only):
- `test_hue_out.py`: 65 tests. They cover the imports, the fresh-interpreter import check, room's pairing by bridge
  id, room's area rule, the four states and their words, a release right after room empties the set (stop first, and
  release first), an old stop, a release while taken over, the cap, takeover during the handshake, the output
  switched off, the flip back forgetting the set, and a request seeing a fresh switch.
- `test_outputs.py`: the clamp, a set kept while the output is off, and a release right after the switch-off.
- `test_api.py`: every driver state onto the four words.
- `test_e2e.py`: the Stage-5 end to end, through room's `BridgeSecrets.load` and `resolve_area` on the fake bridge.

#### S5.6c Hand-over review fixes (16 Oct; c8's fixer)

The review of the hand-over against room's actual code (68's `room_service/adapters/hue.py`, `hue/output.py`,
`hue/client.py`, `dmx/__init__.py`, and 68's addendum) raised five issues. All five are fixed here; nothing is
deployed, every output is still off.

1. **`lights` and `boot_id` on the Hue routes** (blocker; 68's addendum).
   - Room's `_sync_show` reads `GET /api/v1/hue/status` on every read under `show`. On first contact after a room
     restart it adopts our `lights`. After that, a new `boot_id`, `lights` unlike its set, or `idle` while it wants
     lamps makes it re-send its full set. Without the two fields, room's `show_wanted` stayed empty after a room
     restart:
     - lamps we still streamed read `taken_over`;
     - a later state write sent no release and was overwritten (unconfirmed) until a mode change.
   - `HueAnswer` now carries:
     - `lights`: `HueStatus.asked`, what room asked, **unfiltered**. That is the driver's `_asked`; while the hue output
       is off, the Rig's saved set (`Rig._saved[oid]["asked"]`, kept for on again); with no rig, `[]`. It is never
       `wanted`: an id that isn't a hue fixture here would otherwise make room re-send on every read.
     - `boot_id`: the API's own (`api.Api.boot_id`, this process), the same one the vibe read-back carries.
   - Both are on the stream and status replies. `contract.hue_answer(status, boot_id)`; `contract.hue_off_status(…,
     asked)`.
   - Note for 68: while the hue output is off (or `waiting_room`, `no_config`, `not_built`), status reads `idle`, so
     room's rule 3 re-POSTs its set on each read. That is harmless, and the Rig keeps the set for on again.
2. **A release never answers a false `released: true` during a takeover** (major).
   - In room's order (the new set first, which clears our `taken_over`, then the release) a release found no stop of
     ours pending and answered `released: true` at once, while another app's streamer still held the area. The same
     happened after a start-wait `taken_over` (an area already active that we never started).
   - `_given_back` now reads the area once whenever a client and an area are known, whatever `_stop_t` is. If it is
     active and we don't hold it, the answer is `released: false`, `why: "taken_over"`, and room checks the bridge
     itself. If it is inactive, the answer is released (after the settle, when a stop of ours is the reason).
   - Not taken: keeping the `taken_over` state until the bridge reads inactive. The area read covers the release,
     and the control task's start wait already handles a new set while the other app still holds the area.
   - Left open: a release before our first start has resolved the area (no client or area yet: under a second after a
     start) still answers released with no read.
3. **No frame after a flip back during the DTLS handshake** (minor).
   - `_start` re-reads patch.yaml (`_still_ours`: poll, then `_should_stream`) on each start-wait poll, before the PUT
     start, and right after the handshake. Flipped back to room (or the output switched off) during the handshake:
     the area is stopped before a single frame is sent.
   - Why it matters: room and show_service pair with the same application key, so the bridge names both as the same
     `active_streamer` and neither side's takeover check can tell the other apart (S5.6a). Had we streamed until our
     next step (up to 0.5 s), room's 2 s patch watch could have started its HueOutput in that window. Our following
     PUT stop would then have ended room's fresh stream, and room's `_check_takeover` would have parked it in
     `taken_over`, so neither side would stream.
4. **The bridge's address follows patch.yaml, as room's does** (minor).
   - Room loads the pairing with `BridgeSecrets.load(hue.bridge, hue.host)`, so patch.yaml's `host` overrides the
     file's. hue_out passed only the bridge id.
   - `hue_settings` (fixtures.py, and hue_out's own copy) now gives `host`: patch.yaml's `hue.host`, only while the
     bridge is patch.yaml's own. A fixtures.yaml output naming another bridge uses its pairing file's address.
   - `default_secrets_loader(bridge_id, host)` is room's call, and a `secrets_loader` now takes `(bridge, host)`.
   - A host change in patch.yaml while streaming stops the area and starts it again at the new address, as a bridge
     or area change does.
   - (Room reads `host` only at its own start. We follow it at the reload.)
   - config/room/fixtures.yaml's header still says the address is the pairing file's. That is c8's file, outside
     show_service/: fix the wording at the next fixtures.yaml edit.
5. **No release answers `released: true` while show_service is starting under `show`** (minor).
   - Before the brain runs, `POST /api/v1/hue/release` reads patch.yaml and fixtures.yaml (`Api(patch_path=,
     fixtures_path=)`, defaulting to contract's):
     - `hue.streamer: show` with a hue output `enabled: true` → `{released: false, why: "starting", via: "show"}`. A
       predecessor that crashed while streaming may still hold the area, and room then checks the bridge itself;
     - a file that's there but doesn't read counts as show, which is always safe;
     - otherwise (`room`, no hue output on, or a missing file) → `released: true` at once, as before.

Tests (fakes and tmp files only):
- `test_hue_out.py`:
  - `test_release_in_rooms_order_while_another_app_holds_the_area_is_not_released` (taken while streaming, and active
    at start);
  - `test_release_while_waiting_to_start_never_starts`, now `taken_over`;
  - `test_a_flip_back_to_room_during_the_handshake_gives_the_area_back_before_a_frame`;
  - `test_the_start_wait_reads_a_fresh_switch`;
  - the pairing test's `host_in_patch` case;
  - `test_a_new_bridge_address_in_patch_yaml_restarts_the_stream_there`;
  - the `hue_answer` shape.
  The release, handshake and start-wait tests fail with the old logic patched back in (checked).
- `test_api.py`:
  - `test_rooms_hue_adapter_adopts_our_set_then_re_sends_after_a_new_boot_or_a_stale_set` drives **room's real
    `HueAdapter`** (a read-only import of `room_service.adapters.hue`, with a fake bridge client) against these routes.
    It checks the adoption on first contact (no POST; the lamps read `dmx`), a re-send after a new `boot_id`, and a
    re-send over a stale set;
  - `test_a_hue_release_before_the_brain_runs` (six patch and fixtures cases);
  - `lights` and `boot_id` with no rig, and with the output off.
- `test_outputs.py`: the Rig reports the saved set as `asked` while the output is off.
- `test_fixtures.py`: `host` in `hue_settings`.
- `test_e2e.py`: its tmp patch.yaml copy sets `hue.host` to the test address (the real one names the bridge's).

The suite (16 Oct, `flock … nice -n 19 … pytest show_service/tests -q`): **1279 passed, 1 skipped** (the network OFL
test) in 9 min 15 s; `test_hue_out.py` now has 71 tests.

### S5.7 artnet_lan.py (B15)

Our own Art-Net sender to LAN nodes (§9.3), with the frame grid of §5.4.
```python
class Nodes:                                               # one per Rig (shared["artnet.nodes"])
    def __init__(self, *, settings: dict, state_file: Path = contract.NODES_STATE_FILE, sock=None,
                 mono=time.monotonic) -> None              # sock: a test's own; else one UDP socket bound 0.0.0.0:0
    def configure(self, nodes: dict[str, contract.NodeConfig], used: set[str]) -> None   # show-brain
    def dest(self, node_id: str) -> tuple[str, int] | None # any thread: where frames go now; None: virtual or unknown
    def send(self, node_id: str, packet: bytes) -> bool    # runner: sendto, or kept (virtual); never to loopback:6454
    def tick(self, now_s: float) -> None                   # show-brain 1 Hz: ArtPolls due, drain replies, failsafe
    def on_reply(self, data: bytes, addr: tuple[str, int]) -> None   # the reply hook (any source of OpPollReply)
    def record_failsafe(self, node_id: str, frames: list[bytes]) -> dict
    def status(self, node_id: str) -> contract.NodeStatus
class ArtNetLan:                                           # contract.Driver, kind "artnet": one universe on one node
    caps = {"hits": True, "min_attack_ms": 0.0, "grid": "beat", "rate_hz": 44.0, "keepalive_s": 1.0}
    def __init__(self, output_id: str, *, ctx=None, clock=None, settings: dict | None = None,
                 shared: dict | None = None, mono=time.monotonic) -> None
def build_artpoll() -> bytes;  def parse_artpollreply(data: bytes) -> dict | None;  def build_artaddress(cmd: int) -> bytes
```
- **The wire.** ArtDmx from artnet_out's `build_artdmx` (seq 1..255, never 0, OpCode 0x5000, ProtVer 14). ArtPoll
  (OpCode 0x2000): `Art-Net\0`, opcode LE, ProtVer 14, Flags 0, DiagPriority 0. ArtPollReply (0x2100): IP, port,
  short name (18 bytes), long name (64), the ports' good-output bytes (merge), Status3 (failsafe), MAC, and, from Art-Net
  4, RefreshRate: offsets as the Art-Net 4 spec (verify against it; tests check packet bytes). ArtAddress (0x6000)
  `Command`: AcFailHold 0x08, AcFailZero 0x09, AcFailFull 0x0a, AcFailScene 0x0b, AcFailRecord 0x0c.
- **Unicast only** (WSL can't broadcast; there is no ArtSync with one universe). Port-Address ≥ 1, from the output.
- **The frame grid** (`plan`, §5.4), when the frame is active and the clock predicts beats:
  - `L` = the output's intensity rise latency (its `latency_key`'s, else hand-set, else 0).
  - The next beat `b` from `clock.next_beat(now, lead_s=L)`; tick = `grid_tick(P)` (P / m, m the largest multiple of 4
    keeping ≤ 43 Hz); grid points `b − L − j·tick`, so **a frame leaves exactly at t_beat − L_dmx and on every 16th**.
  - Never closer than `MIN_GAP_S` (22.7 ms) to when the last frame actually left. A beat re-phased into that gap goes
    one gap later (never early, under a frame late): artnet_out's rule. A grid point at most 1 ms inside the gap goes
    at the gap's end rather than a tick later (S5.14).
  - No beat (gate none, or no clock): free-run at 43 Hz while active.
  - **Hits pull the next frame forward:** a runner woken by `on_hit` plans `max(now, t_last + MIN_GAP_S)`.
  - **Idle** (an inactive frame): plan every `1 / rig.artnet.idle_eval_hz`; `set_frame` sends only when the universe
    changed or `keepalive_s` (1 s) passed. What it sends idle: each fixture's `house` (or dark), with the defaults and
    pins: the engine already gives that, since inactive frames carry the house look for DMX fixtures.
- **Short frames.** The universe runs up to its highest patched channel, rounded up to even (at least 2), unless the
  node's `full_512`. Channels no fixture covers are 0.
- **`set_frame`** builds the universe in a preallocated buffer (defaults, then each fixture's `dmx` at `address − 1`),
  stamps the sequence and sends through `Nodes.send`. A send error is counted and logged at most every 30 s.
- **Discovery and health** (`Nodes.tick`, 1 Hz on show-brain, only for nodes an enabled output uses):
  - a unicast ArtPoll to the node every `rig.artnet.poll_s` (3 s);
  - drain the socket (non-blocking) and, when `rig.artnet.reply_bind` is set, that socket too; each OpPollReply goes
    to `on_reply`;
  - match by the node's `match` (MAC, else short name). A match from another address: use that address (kept in
    `nodes.json`, written atomically; "moved: update last_ip"). A reply at `last_ip` that doesn't match: status
    `matched: false` and a detail; frames keep going (no stop gates);
  - no reply for `silent_after_s`: detail "silent". **Replies need ca's relay** (below) until proved otherwise:
    detail "no replies yet: nodes answer on PC22's udp 6454, which ca's relay forwards to room_service (README S5.7)".
    Frames go out regardless.
- **Why replies may not arrive.** Art-Net nodes answer ArtPoll on udp 6454 of the poller's address, not its source
  port. From WSL that address is PC22's, where ca's Art-Net relay (`artnet-relay`, Spectre) forwards 6454 to WSL:6454,
  which is room_service's receiver; show_service can't bind 6454 (room has it). Some nodes reply to the source port,
  which WSL's NAT carries back to our socket; those work today. For the rest, ca's relay must also forward
  OpPollReply packets (OpCode 0x2100) to a show_service port: ca picks it, registers it in docs/PORTS.md, and its
  `host:port` goes into show.yaml `rig.artnet.reply_bind`. Room's receiver ignores non-ArtDmx packets, so nothing
  breaks meanwhile.
- **Failsafe** (§9.3). For a node with `failsafe` set, one ArtAddress with that mode's command goes out when an enabled
  output first uses the node (and again when the setting changes). `record_failsafe` (route `POST
  /api/v1/rig/nodes/{id}/record-failsafe`) sends the node's current universes once more, then AcFailRecord, so the node
  stores what shows now as its scene. Nothing records on its own.
- **The virtual node** (`virtual: true`): frames are built and kept, never sent; `GET /api/v1/rig/frame` serves them
  (previz in the console, or a later BlenderDMX bridge on another PC as a normal node).
- **Stop:** nothing more is sent; each node's failsafe takes over after its own timeout.

**Tests (B15)** (rule: **Art-Net never to the LAN or to 127.0.0.1:6454**; nodes at `127.0.0.1` with `port` = an
ephemeral sink socket the test binds; no test binds 6454):
- packet bytes: ArtDmx (short lengths, even, Port-Address split, seq 1..255 wrapping 255 → 1), ArtPoll, ArtAddress;
  `parse_artpollreply` on hand-built replies (names, MAC, IP, Status3, RefreshRate) and on junk (None);
- the grid on a fake clock: edges exactly at `t_beat − L` at 60-180 bpm, the minimum gap, re-phase never early, the
  free run, a hit pulling a frame forward but never under 22.7 ms;
- short frames and `full_512`; keepalive (1 s idle, on change at once); the universe assembled from fixtures, defaults
  and pins;
- discovery: polls every 3 s only for used nodes, a match by MAC and by short name, a moved node (nodes.json), a
  mismatch keeps sending, silent;
- failsafe ArtAddress once per node and setting; record;
- loopback:6454 refused (a node configured there is treated as virtual, with a problem line); virtual frames kept.

#### S5.7a As built (B15, 16 Oct)

`artnet_lan.py` and `tests/test_artnet_lan.py` (58 tests). The spec above is built as written. Below are the additions
and the choices it left open.
- **Imports.** From artnet_out: `build_artdmx`, `next_seq`, `grid_tick`, `MIN_GAP_S`, `GRID_HZ`, `PORT`, plus its wire
  constants (`ARTNET_ID`, `OP_DMX`, `PROT_VER`, `HEADER`, `UNIVERSE_SIZE`, `MAX_HZ`) and `REPHASE_SKIP`. A beat that
  the last frame already carried isn't taken again when the clock nudges it.
- **Extra driver methods, for B12** (each read with `getattr`):
  - `on_hit(d=None)` (any thread) makes the next `plan` (while active) answer `max(now, last send + gap)`.
    `plan_hit(t_last, now)` does the same for a runner that plans the hit itself. **The Rig must call one of them**
    when it wakes a runner for a hit; `plan` alone can't know about the hit.
  - `universe_view()` is `/rig/frame`'s `universe`: `{port_address, length, channels, to, virtual, seq, age_s}`.
  - `last_packet()`, `record_failsafe()` (this output's node), and `set_settings(rig.artnet)` for a show.yaml reload.
- **Extra Nodes methods:**
  - `claim(output, node, packet_fn)` and `unclaim(output)`: drivers register, so `record_failsafe(node)` with no
    `frames` resends every universe on that node.
  - `snapshot()`, `dmx_view()` (the virtual nodes' universes decoded from our own packets: the previz JSON, for
    whichever route B12 serves it on), `kept(node, pa)`, `is_virtual(node)`, `close()`.
- **Pure helpers:** `decode_artdmx`, `parse_settings`, `output_lead`, `norm_mac`, `unicast_ip`, `is_room_receiver`.
- **`L` for the grid.** `output_lead(out, fixtures)` uses the rise of any fixture on the output whose LatencyView
  source is `latency.json outputs.<latency_key>.*` (fixtures.py already applied `align_pct` to it). Otherwise it uses
  the hand-set `latency.intensity.rise_ms`, else 0 (`unset`). An output whose fixtures all have their own measurements
  falls through to hand-set, which is a small gap until OutputConfig carries its own LatencyView.
- **The clock.** `next_beat(t, lead_s=L, gate="dmx")`, so DMX stays beat-locked at §5.4's middle confidence. A clock
  with artnet_out's `next_beat(t)` signature gets `next_beat(t + L) - L`.
- **Spacing in `set_frame` too.** A frame due inside the gap by ≤ 1 ms waits it out. A frame due earlier than that
  isn't sent, and the next `plan` answers "as soon as allowed". An idle `plan` counts from the last `set_frame`, so a
  runner passing a stale `t_last` never spins.
- **The wire, where the spec left a choice:**
  - ArtAddress uses BindIndex 1 (the root) and AcnPriority 255 (no change).
  - A Status3 failsafe is reported only when bit 5 says the node supports one.
  - `refresh_hz` is RefreshRate as given (0 = None).
  - `merging` is any GoodOutputA bit 3.
  - A reply's address is its own IP field, else the UDP source.
- **Status.** The state is `off`, `no_config` (with the reason), `error` (3 failed sends in a row; frames keep being
  tried), `sending` (a frame in the last keepalive + 1.5 s) or `idle`. `extra` carries the node status, length, seq,
  lead and where it came from, period, keepalives, too_soon, problems and caps.
- **Costs** (niced, `-s`): `set_frame` on 32 fixtures × 16 channels (512) p50 24.5 µs, p95 51.5 µs (budget 100);
  `plan` on the beat grid p95 5.4 µs.
- **The grid's tolerance (S5.14).** A frame must be a gap (22.7 ms) after the last one's actual send, and at 128 bpm
  the tick is 23.44 ms: 0.74 ms of slack. Any runner lateness above that (sleep overshoot, a GIL hand-over,
  evaluate) put the next grid point inside the gap, and the frame moved a whole tick on (simulated: 0.8 ms of
  lateness gave 30.6 Hz; the e2e read 32.5 Hz with intervals p95 47 ms, so 16ths were skipped). `_grid` now takes the
  first grid point at or after `earliest - SPACING_WAIT_S` (1 ms) and answers `max(that, earliest)`; set_frame's wait
  of up to 1 ms keeps the gap exact. Simulated: 0.3 ms lateness 39.4 → 42.7 Hz, 0.8 ms 30.6 → 37 Hz, every beat
  carried, no gap under 22.7 ms. artnet_out's `plan` has the same tolerance (`GRID_TOLERANCE_S`), so the virtual
  Maestro (the screens' feed) keeps its 42.7 Hz. `test_a_little_lateness_doesnt_skip_grid_ticks`,
  `test_a_little_lateness_keeps_the_screens_feed_on_its_grid`.
- **A rate cap keeps the grid's slack (S5.14).** With `outputs.<id>.rate_hz`, the grid's ceiling is
  `1 / (1/rate + GRID_SLACK_S)` (GRID_SLACK_S = 1/GRID_HZ − MIN_GAP_S, 0.53 ms), not `rate` itself: `rate_hz: 40` at
  120 bpm used to tick at exactly its 25 ms gap, and any lateness at all ran it at 20.1 Hz; now it ticks at 31.25 ms
  (32 Hz) and holds. `test_a_rate_cap_keeps_the_grids_slack`.
- **c8's integration ruling (16 Oct): a hit never pushes the beat frame.** A hit pulls the next frame forward to
  `max(now, last + gap)` (S5.7) **unless the next beat frame is less than a gap after that**: pulling would push the
  beat frame late (one gap after the pulled frame), so the flash rides the beat frame instead, at most one gap
  (22.7 ms) later. Found by the e2e: with test music the hats' and kicks' hits re-planned nearly every frame, and the
  beat frame stopped leaving at `t_beat − L`. `test_a_hit_just_before_a_beat_rides_the_beat_frame`.

### S5.8 The screens' hand-pick hold (B16; agreed with 2f, 16 Oct)

Since Stage 4 the director PATCHes `/api/v1/screens/{id}/look {palette, palette_transition_s}` on every live screen
while a vibe plays, which can replace a palette someone picked on a screen's card. Agreed with 2f: a person's pick now
holds.

**Room's side** (2f, on disk; live at the next Flask restart): `PATCH /api/v1/screens/{id}/look` takes an optional
`source` (1-64 characters; never sent on to the renderer), and room emits `screen.look {screen, session, source
(default "web"), changed: [keys], palette (only when the PATCH carried one)}` on `WS /api/v1/events`. Until that
restart, the old code answers a PATCH with an unknown key `400 {"error": "bad_request"}`, and may emit no `screen.look`
at all.

**RoomLink** (autopilot.py):
- `_on_message`: every event type other than `status` and `mode.transition` with a dict body goes to each
  `ctx.on_room_event` callback as `(type, data)`, guarded and counted per type (`stats["room_events"]`).
- `provides()` adds `room_boot`: `status.service.started_at` as a string (None until a status). It changes when
  room_service restarts.

**The director** (`provides()` adds `on_room_event`):
- **Sending, with the probe.** `look_source` is `unknown`, `yes` or `no` (`contract.LOOK_SOURCE_STATES`), for the
  current `room_boot`:
  - the body gains `"source": "show_service"` (`contract.SHOW_SOURCE`) unless `look_source` is `no`;
  - a 2xx with `source` → `yes`;
  - a 400 whose `error` is `bad_request` with `source` in the body → `no` (the old room), and the same PATCH again at
    once without it;
  - any `screen.look` event that carries a `source` key → `yes` (the new room);
  - a new `room_boot` → `unknown` (probe again).
  Each result is logged once (`director.jsonl` kind `look_source`).
- **Our own echo.** For each screen the director remembers the palette it last sent there and when (`sent`,
  `sent_at`).
- **A hand pick** is a `screen.look` with `palette` in it (any type: a catalog name, the renderer's own shape, or null)
  that is not ours. It is ours when its `source` is `show_service`, or when its palette equals what we last sent that
  screen within `director.screens.own_echo_s` (3 s, agreed with 2f: this covers the old room, whose events carry no
  source, and a new room that got our PATCH without one). Anything else is a person's.
- **The hold** (`ScreenPick`, while `director.screens.hold_hand_picks` is true, and (S5.14) only while a vibe plays
  and `send_palette` is on, i.e. while the director is sending the screens' palette at all; otherwise the pick is
  only logged, with why):
  - the director stops sending the screens' palette, to every screen (`screens_palette.why`: "held: <label> was
    picked on a screen");
  - the card shows a chip `{"id": "screen_pick", "kind": "screen_pick", "label": "Colours: <label> (picked on a
    screen)", "value": ScreenPick, "expires_in_s": null}`; `<label>` is the catalog's label for a name, else the name;
    `palette: null` (the screen card's "The scene's own colours", LivePanel) reads "the scene's own" (S5.14, the
    Room page's own words; 2f to confirm); anything else "custom colours";
  - a newer pick replaces it (the chip follows);
  - it counts as a nudge for `engaged()` (Auto won't leave the mode under it);
  - the lamps are unchanged (they keep the director's palette).
- **It ends** at the next mode change (`on_mode_change` clears every nudge), Back to auto (`clear("all")`), or the
  chip's × (`clear("screen_pick")`, through the existing `DELETE /api/v1/nudge/{id}`). Then the record of what was sent
  is cleared, so the director's palette goes back to every live screen at once.
- **Persisted** in state.json as `director.nudges.screen_pick` (`ScreenPick | null`), with the usual rule: a restored
  hold ends at the first RoomView whose mode differs from the saved mode.
- `hold_hand_picks: false` (hot-reloaded): picks are only logged, and the director sends as in Stage 4.
- **Snapshot:** `screens_palette` gains `hold` (ScreenPick or null), `look_source`, `boot`. Logs: `screen_pick`
  (`picked`, with the event; `cleared`, with why).
- api.py needs no change: the card's chips come from `director.chips()`, and `DELETE /api/v1/nudge/{id}` calls
  `director.clear(id)`. The Room page's Music card shows chips as they come (2f).

**The director's lamps from the registry** (so a DMX fixture added to fixtures.yaml gets the director's colours):
with `ctx.rig()` giving a view with fixtures, the lamps are its `lamp` fixtures that have `colour` in their type's
attrs, and the screens its `screen` fixtures, each with its `screen`, `placement` and `role` (a fixture's own `role`
wins over its placement). Without a view (no rig, an error with no fixtures), patch.yaml as now. The ids are the same
for the six existing devices, so nothing changes for them. `palette_fn` and `LookData.lamps` are keyed by fixture id.

**`Director.look_fn() -> LookData | None`**: any thread, the latest look swapped by reference (the engine's
`look_fn`).

**Tests (B16)** (a fake room with the PATCH route and a WS that pushes `screen.look`; no real room):
- the probe: a new room (2xx, `yes`); an old room (400 `bad_request`, the PATCH again without `source`, `no`, the
  next PATCH without it); a `screen.look` with `source` → `yes`; a new boot → `unknown`;
- our echo (with `source`; without it within 3 s) is not a pick; a different palette, or ours after 3 s, is;
- the hold: no more PATCHes to any screen; the chip's label (a catalog name, an unknown name, an object); a newer pick;
  `engaged()`;
- ends: a mode change, Back to auto, the chip's × (each re-sends at once); persisted and restored; `hold_hand_picks`
  false;
- the lamps from a fake registry view (a lamp fixture with a role, one by placement; screens), and patch.yaml without
  one; `look_fn`;
- RoomLink: events fanned out by type, guarded; `room_boot` from status;
- the name scan adds `fixtures.py`, `outputs.py`, `looks.py`, `emitters.py`, `hue_out.py`, `artnet_lan.py`, and to
  `_names_from_config()` fixtures.yaml's fixture, output, node, type and group ids, zones and screens.

**As built (B16, part 1: the hold, the probe and RoomLink's events; 16 Oct).** Nothing is committed.
- **Built as above.** In `director.py`: `on_room_event` (provided), the `source` probe, the echo and the hold,
  `screen_pick` in `chips()`, `engaged`, `persist`/`restore`, and `clear(id, why=None)` (an optional `why`, for the
  log only; api.py calls it unchanged). In `autopilot.py`: the fan-out in `RoomLink._on_message`, and `room_boot()` in
  `provides()`.
- **Details the spec left open:**
  - The echo record is set **before** each PATCH, because room's WS event can arrive before the HTTP answer. It is
    kept per screen, so a screen leaving live doesn't lose it.
  - A 400 `bad_request` gets the same PATCH again at once without `source`. Our echo of that PATCH (no source,
    within `own_echo_s`) is not a pick.
  - A probe answer for a boot that has since changed is dropped. A new boot resets to `unknown` (logged, via
    `room_boot`).
  - Turning `hold_hand_picks` off (hot-reloaded) also ends an active hold, at the next tick (why: "…hold_hand_picks
    is off"), and the director's palette goes back out. So does turning `send_palette` off (S5.14).
  - A pick's `palette` object reads "custom colours", null "the scene's own" (S5.14). A name the catalog doesn't know
    reads as the name.
  - An in-flight PATCH run stops before its next screen once a hold starts.
- **Snapshot.** `screens_palette` gains `hold`, `look_source`, `boot`, `sent_at` (screen → epoch s), `picks` and
  `own_echoes`. RoomLink's snapshot gains `boot` and `stats.room_events` ({type: count}, at most 64 types counted;
  every type is still fanned out).
- **Logs** (director.jsonl):
  - `look_source` {state, boot, via: patch | event | room_boot};
  - `screen_pick` {what: picked, event, held} or {what: cleared, why: "the chip's ×" | "back to auto" | "the mode
    changed" | the settings line, pick};
  - `screens_palette` gains `source` (bool).
- **Tests** (niced): test_director gains the probe (new room, old room), the echo, the hold (chip labels, a newer
  pick, `engaged`, the lamps carrying on), its three ends (each re-sends at once), persist/restore, `hold_hand_picks`
  false and the settings. It also gains one wired test: the real RoomLink against an aiohttp fake room on an ephemeral
  port, whose PATCH emits `screen.look` on its events WS, including a "restart" into the old room. test_autopilot
  gains the fan-out (by type, guarded, junk dropped, status and mode.transition not fanned out) and `room_boot`. Two
  Stage-4 assertions were updated for the new `source` key and the two new `director.screens` keys.
  - test_director, test_autopilot, test_api, test_brain, test_main, test_contract, test_config and test_situations:
    241 passed, with DeprecationWarnings as errors.
  - test_e2e: 3 passed.
- **Cost:** one `ctx.room_boot()` read per director tick, and O(1) work per room event. test_tick_budget still
  passes.
- **Not built here** (B16's other items): the director's lamps from the registry (`ctx.rig()`), `look_fn`, and the
  name scan's Stage-5 additions.
- **Open:** if a person's PATCH and one of ours that is already in flight reach room at the same moment, room
  decides which lands last. The hold still starts, but that screen may show our palette until the hold ends. The
  window is one PATCH round trip.

**As built (c8, part 2: B16's other items; 16 Oct).**
- **The lamps from the registry.** `director.layout_from_rig(view)` gives `(lamps, screens)`: the `lamp` fixtures
  whose type has `colour` in its attrs and the `screen` fixtures, each `{kind, screen, placement, label, role,
  house}` (S5.14: `house` is fixtures.yaml's; House lights and an entry fade's start use it, hex × intensity, for a
  lamp room has no stored state for, before show.yaml's `director.house`: so a DMX pinspot whose house is
  `{intensity: 0}` stays dark under House lights instead of going to 80 % warm white). The
  director reads `ctx.rig()` each tick (a reference; recomputed only when the view object changes) and uses it while it
  has fixtures; without one (no rig, the rig off, an error with no last good registry) it uses patch.yaml's layout, as
  before. A fixture's own `role` (when it is one of show.yaml's `lamps.roles`) wins over its placement. The snapshot's
  `patch.from` says which (`fixtures.yaml` | `patch.yaml`). The six existing devices have the same ids, screens and
  placements in both, so nothing changes for them; a DMX fixture added to fixtures.yaml gets the director's colours.
- **`Director.look_fn()`**: the latest LookData (`self.look`, swapped each compose); the engine's `look_fn`.
- **The name scan** now covers `fixtures.py`, `ofl.py`, `outputs.py`, `looks.py`, `colour_mix.py`, `hue_out.py` and
  `artnet_lan.py`, and its names include fixtures.yaml's fixture, output, node, type and group ids, zones, screens,
  placements, roles and tags, and show.yaml's layer ids, minus `fixtures.BUILTIN_TYPES` (fixed vocabulary). All clean.
- Tests: `test_the_lamps_and_screens_come_from_the_registry`, `test_look_fn_is_none_until_the_first_compose`.

### S5.9 How the virtual Maestro coexists

| What | Until | Stage 5 | Switch, and rollback |
|---|---|---|---|
| **Screens** (RGB, the DIMMER pulse, ACTIVITY, PAN/TILT/ZOOM, GOBO, PRISM) | P4 | **Unchanged.** artnet_out's virtual Maestro → room_service's receiver (`127.0.0.1:6454`, universe 0, patch.yaml's layout); the renderer's BeatFollower and music_direction read it. The `screens` output (`showbus`) stays off and isn't built. Screens' palette PATCHes as in Stage 4, plus the hold (S5.8) | P4: the renderer reads our bus directly (§9.5) |
| **Hue lamps** | the hue cut-over | Room streams them, from the virtual Maestro's hue channels, while patch.yaml says `hue.streamer: room` (today). With `outputs.hue.enabled: true` **and** `hue.streamer: show`, hue_out streams them from the look engine at 50 Hz with the lamps' own leads; room's HueOutput is idle (`show`). artnet_out keeps writing the hue channels; room ignores them under `show` | 68's `hue.streamer` in patch.yaml decides who streams (both sides read it; one streamer per area). Rollback: `streamer: room` (hue_out stops within one reload), and/or `enabled: false` |
| **DMX fixtures** | a node is bought | Only artnet_lan, unicast to the node at Port-Address ≥ 1. Never to room's receiver: a loopback destination on 6454 is refused | `outputs.<id>.enabled`; rollback `false` |

- The two paths never write the same thing: the screens are only on the virtual Maestro, the lamps only on one
  streamer (patch.yaml's switch), and DMX only on artnet_lan.
- artnet_out needs no change in Stage 5. It goes on yielding to a real Maestro on universe 0, as before; artnet_lan has
  no yield (it never shares room's universe).
- **Order at the Hue cut-over** (no gap and no fight): `outputs.hue.enabled: true` first (hot-reloaded; hue_out sits in
  `waiting_room`), then 68 sets `hue.streamer: show`: room's HueOutput stops its area; room's adapter sends `POST
  /api/v1/hue/stream` for the lamps that follow; hue_out waits up to `start_wait_s` for room's area to go inactive and
  starts. Back: `streamer: room` → hue_out stops at its next reload (≤ 2 s); room starts. If both overlap for a moment,
  the bridge keeps one DTLS session: whichever loses sees its area taken and goes `taken_over` (no fight).

### S5.10 show.yaml's Stage-5 keys (c8's file; in place since 16 Oct)

| Section | Read by | Keys |
|---|---|---|
| `rig` | outputs (Rig) | `enabled` (the rollback; hot-reloaded), `artnet {poll_s, keepalive_s, idle_eval_hz, silent_after_s, reply_bind}` → artnet_lan, `hue {takeover_check_s, start_wait_s, release_settle_s, release_timeout_s, error_backoff_s}` → hue_out |
| `looks` | looks (LookEngine.configure); fixtures (`align_pct`, `min_visible_ms`) | `align_pct`, `min_visible_ms`, `middle_depth`, `master`, `caps {flash_hz_max}`, `layers [ {id, enabled?, attr, fx, phase, subdivide?, period_bars? \| period_s?, depth, depth_scale?, spread?, needs, without_beat?, attack_ms?, decay_ms?, select} ]`, `hits {select, styles, bands?, min_strength, depth, decay_ms}` |
| `director.screens` gains `hold_hand_picks`, `own_echo_s` | director | S5.8 |
| `director.build` gains `hit_swell_ms` (300, §5.4; 300..10000) | director (LookData.hit, maestro_palette_fn), looks | S5.14: a drop nobody predicted swells this slowly on an output without hits (Hue) |

- **Layer rules** (looks.py's compile): `id` unique; `attr` intensity (colour reserved); `fx` in `FX_SHAPES`; `phase` in
  `FX_PHASES`; `needs` in `FX_NEEDS`; `depth` and `spread` a number or a `LOOK_INPUTS` word; `select` a selector. A
  layer with a bad key is left out with a problem line; the others run. Layers run in file order.
- The two layers shipped: `pulse` (on): artnet_out's beat swell, so a lamp moved to hue_out looks as it does through
  room today; `motion` (**off**): a bar-phase wave round `group:around`, for tuning by ear before it goes on.
- Every number is a starting point with its §, except `own_echo_s` (agreed with 2f). The running Stage-4 readers ignore
  every new key: checked on 16 Oct, the running show_service reloaded revision `06ea9643fdc7` with no settings error
  and no director problem line.

### S5.11 Rules for every builder

S3.13 and S4.10 hold. Also:
- **Never, in a test or an experiment:**
  - talk to the real Hue bridge (192.168.1.72), read the real pairing files, or start an Entertainment stream: fake
    clients and streamers only, secrets in `tmp_path`;
  - send Art-Net to the LAN, or to `127.0.0.1:6454`: ephemeral local sockets only;
  - bind 8703, 6980, 6981, 6982 or 6454 (or 8700-8702);
  - PUT, POST or PATCH the real room API, or open its WS: a fake room;
  - read or write the real state dir: `nodes.json` and state.json in `tmp_path`;
  - touch Flask, the renderer, or a production service; kill a process you didn't start; commit.
- **No new process, no GPU.** Every install and test run goes under `flock /tmp/claude-1000/sm-build.lock nice -n 19`
  with `~/.venvs/sm-show`. Only B14 installs (its requirements block).
- **Costs** are printed with `-s` and go in each builder's "As built", measured niced with the render running.
- Every module carries the name scan's rule: config names live in config.

### S5.12 Going live, and who to tell

The steps, in order (c8's; integration done 16 Oct, S5.13). Nothing visible changes until step 5.
1. **The suite**, niced: `flock /tmp/claude-1000/sm-build.lock nice -n 19 ~/.venvs/sm-show/bin/python -m pytest
   show_service/tests -q` (S5.13: green). It includes `test_stage5_end_to_end`.
2. **Commit** (c8; own paths only, no `add -A`): `show_service/{fixtures,ofl,outputs,looks,colour_mix,hue_out,
   artnet_lan}.py`, the edited `show_service/{__main__,api,director,autopilot,contract,artnet_out}.py` (artnet_out:
   S5.14's grid tolerance), the new and edited tests (`test_{fixtures,ofl,outputs,looks,colour_mix,hue_out,
   artnet_lan}.py`; `test_{main,api,director,autopilot,e2e,artnet_out}.py`),
   `show_service/README.md`, `show_service/requirements.txt` (B14's block), `tools/show/add_fixture.py`,
   `config/room/fixtures.yaml`, `config/room/show.yaml`. The hue-entertainment block is already installed in
   `~/.venvs/sm-show`.
3. **Restart show_service:** `scripts/ctl restart show`. **No output changes**: every output is off. Two director
   fixes (S5.14) do show on today's lamps, through the virtual Maestro: the key lamps (`opposite`) go warm white at
   the feel's key_cct (Home, Dinner, Chill, Notes), and an unforeseen drop with a token swells on the lamps instead of
   a one-beat step (Party, Disco). Say so when announcing the restart.
   - Check (from WSL): `curl -s http://127.0.0.1:8700/show/api/v1/rig | jq '{enabled, registry, o: (.outputs |
     map_values(.state))}'` reads 6 fixtures, no problems, every output `off`; `/show/api/v1/hue/status` reads `idle`
     ("outputs.hue.enabled is false in config/room/fixtures.yaml"; the four route words, S5.6b); `/show/api/v1/state`
     `.brain.components.director.patch.from` is `fixtures.yaml`; no `show-out-*` thread in `.threads`.
   - The director's next screens-palette PATCH carries `source`. Until step 4 the running room code refuses it once
     per room boot (400) and the director sends it again without (`screens_palette.look_source: "no"`); a hand pick
     can't be told apart until step 4 (the old room emits no `screen.look`).
   - CPU (S5.13): with every output off, Stage 5 adds nothing measurable.
4. **An announced Flask restart** (room_service lives in Flask, CLAUDE.md §3): tell 2f, 68 and the sessions editing
   room_service first; 2f owns the room side. The running Flask dates from 07:03 on 16 Oct, before room's Stage-5 code
   (2f's `source` on `PATCH /api/v1/screens/{id}/look` and the `screen.look` event; 68's hue adapter and HueOutput
   reading `hue.streamer`). After it: `look_source` reads `yes`, and a palette picked on a screen's card holds (the
   chip "Colours: <label> (picked on a screen)" on the Music card) until ×, Back to auto or a mode change.
5. **The Hue cut-over** (the owner's call, with 68; S5.9's order, no gap and no fight):
   1. c8 sets `outputs.hue.enabled: true` in `config/room/fixtures.yaml` (hot-reloaded within 2 s). `GET
      /show/api/v1/rig` `.hue.state` reads `waiting_room` (patch.yaml still says room); `/show/api/v1/hue/status`
      reads `idle`.
   2. 68 sets `hue.streamer: show` in `config/room/patch.yaml` (room and show_service both hot-reload it): room's
      HueOutput stops its area; room's hue adapter sends `POST /show/api/v1/hue/stream` for the lamps that follow;
      hue_out waits up to `start_wait_s` (3 s) for the area to go inactive, then starts.
   3. Check: `/show/api/v1/hue/status` `streaming` ("Following the music"; `armed`, "Ready: waiting for the music",
      until sound comes); `/show/api/v1/rig` `.hue`: `dtls: true`, 4 channels; `.outputs.hue` `rate_hz` about 50,
      `extra.runner.late_ms.p95` under 1.5 ms; the Room page's lamp rows read "Following the music"; a mode exit
      releases (room's adapter: `POST /show/api/v1/hue/stream` with the new set, `POST /show/api/v1/hue/release`,
      answered once the area reads inactive + 1 s, then the lamps' put-back).
   4. Measure (S5.14: S5.13's +0.020 is a floor, not the answer): over 60 s before and after, with ML on and the 4K
      render running, take **per-thread** CPU (`/proc/<pid>/task/*/stat`), and state the Hue figure as
      `show-out-hue` plus `hue-dtls-sender` (hue-entertainment's sender: 50 wake-ups a second, a queue hand-over,
      AES-GCM), not the process delta (run-to-run noise about ±0.01). Also compare the virtual Maestro's rate
      (`/show/api/v1/state` `.artnet`) and show-rx's `rx_audio_ms` p95 (hit latency, §5.1) before and after. Budget:
      at most +0.03 of a core (S5.0 rule 7); expect about 0.028-0.03, at the line.
   5. **Rollback:** 68 sets `hue.streamer: room` (room sends no release: hue_out stops the frames at once and the
      area within about 0.5 s, and forgets the set; room's HueOutput starts), and/or `outputs.hue.enabled: false` (the
      frames and the area stop at the reload). Both hot-reloaded (S5.6b point 6).
      - **The ordering is a race we win, not a handshake.** Room and show_service pair with the same application
        key, so the bridge names both as the same `active_streamer`, and neither side's takeover check can see the
        other. The rollback works only because show_service's 0.5 s patch.yaml poll (and its re-read right after a
        DTLS handshake, S5.6c) stops our stream before room's 2 s patch watch starts room's own. If room's HueOutput
        ever started first, our PUT stop would end room's stream and park it in `taken_over` until the set changes.
6. **DMX**, when a node is bought. **Two owner calls first** (S5.14):
   - **Hit rate.** With `looks.hits.bands` unset and `looks.caps.flash_hz_max` off (caps default off by ruling), every
     band's onset at strength ≥ 0.3 flashes DMX: about 13 a second on techno, so Disco's DMX would flicker at up to
     ~13 Hz on hats. Options: `bands: [sub, bass]` (the commented example), a `flash_hz_max`, or accept it.
   - **Onsets at low confidence.** §5.4's "Low: DMX follows onsets" isn't built: hits need the clock's gate above
     `none`, so at low confidence DMX holds steady. Build it (hits at gate `none` too), or keep it and record the
     ruling here.
   Then: its OFL file (`tools/show/add_fixture.py <maker>/<model>` into
   `config/room/fixture-types/ofl/`), a `nodes` entry (`match` by MAC or short name, `last_ip`), a `types` entry, the
   fixtures, and an output with `enabled: true`. Check `GET /show/api/v1/rig/frame?output=<id>`, `/show/api/v1/rig`
   `.nodes` (replies, or "no replies yet" until ca's relay forwards them), and the node's own lights. A `virtual: true`
   node first (previz: `GET /show/api/v1/rig/dmx`) costs nothing on the LAN.
7. **Tell:**
   - **68:** done to your hand-over contract (S5.6b): room's `stream`, `client` and `colour` imported (so a change
     there reaches show_service at its next restart: tell c8 if a signature changes); the pairing via
     `BridgeSecrets.load(<hue.bridge>, <hue.host>)` (S5.6c: patch.yaml's host, as room's); the routes answer exactly
     `idle | armed | streaming | taken_over`, with `lights` (what room asked, unfiltered) and `boot_id` (S5.6c, your
     addendum) (the driver's
     own word stays in `/show/api/v1/rig` `.hue`); release answers `released: true` only once the area reads inactive
     + 1 s, else `shared_area` (other lamps still follow), `taken_over`, `bridge_unreachable` or `timeout`, within
     19 s; takeover captured right after our `action: start`; streaming stops at once on `outputs.hue.enabled: false`
     and on the flip back (which also forgets the set); the cut-over order in step 5. fixtures.yaml reads patch.yaml's
     hue block (`bridge`, `area`, `gamma`, `stop_after_s`) and `streamer` until P5. **Re-sync (S5.14):** show_service now persists what room asked and takes
     it back after a restart (only while patch.yaml says `show`). Room's side is done (68's addendum, `_sync_show`):
     each read under `show` GETs `/show/api/v1/hue/status`, which carries `lights` (what room asked, unfiltered, never
     `wanted`) and `boot_id` (S5.6c); first contact adopts `lights`; a new `boot_id`, a set unlike room's, or `idle`
     while room wants lamps re-sends room's set. Tested against your real `HueAdapter` (S5.6c). One note: while our hue
     output is off, status reads `idle`, so your rule 3 re-POSTs the set on every read (harmless).
   - **2f:** `source: "show_service"` on our look PATCHes (probed per room boot); the `screen.look` event is read; a
     hand pick holds with the chip id/kind `screen_pick` ("Colours: <label> (picked on a screen)"), cleared by ×
     (`DELETE /api/v1/nudge/screen_pick`), Back to auto or a mode change. Room's hue row could say "show_service's Hue
     output is off" when patch.yaml says `show` and HueStatus reads `off`: their call. S5.14: a hold starts only while
     a feel plays with `send_palette` on (else the pick is only logged), and a pick of `palette: null` (the card's
     "The scene's own colours") reads "Colours: the scene's own (picked on a screen)": 2f to confirm the words.
   - **ca:** ArtPollReplies reach PC22's udp 6454, which the relay sends to room_service: please also forward OpCode
     0x2100 packets to a show_service port (registered in PORTS.md; its `host:port` goes in show.yaml
     `rig.artnet.reply_bind`). Art-Net out to LAN nodes is unicast from WSL (no new listening port). The show card's
     state gains `rig`; the Spectre card could show `rig.outputs.*.state`.
   - **10:** latency.json keys we read: `outputs.<fixture id>.<attr>` per lamp (`hue.ne` …) and `outputs.<output
     id>.<attr>` per output (`hue`, `dmx-a` …), attrs `intensity` (or `brightness`), `colour`, `position`; zero is
     "frame sent" for Hue and DMX.
   - **04:** nothing changes on the screens (the virtual Maestro still carries them until P4); the director no longer
     overwrites a palette a person picked on a screen (after step 4).
   - **aa:** nothing.

### S5.13 Integration and measurements (c8, 16 Oct)

**Built by c8** (the builders' unlanded items): `outputs.py` and `tests/test_outputs.py` (S5.4b), the `__main__` and
api.py seams (S5.4b), the director's lamps from the registry, `look_fn` and the name scan's Stage-5 additions (S5.8).

**Reconciled** (the builders' open points):
- **B12:** `lamps` is passed to `Registry.poll` and `LookEngine.configure`; the Rig keys on `poll()`'s True and the
  view's identity, never the revision; the hits/`min_attack_ms` caps checks live in the drivers' `configure` (both
  enforce "only off" and "only raise"). The test OFL types stay embedded in test_ofl.py.
- **B13:** the module is `colour_mix.py` (S5.5's emitters.py); the name scan lists it. Its six S5.5a decisions stand.
- **B14:** `Rig.stop` gathers the task `HueOut.stop()` returns on show-brain, and brain.py awaits it; the copied (not
  imported) Hue code stands, pinned by its parity tests; `fixtures.hue_settings` is the one rule (hue_out calls it).
- **B15:** the runner calls `plan_hit` for a hit wake (S5.7a), and `set_settings(rig.artnet)` reaches the drivers on a
  show.yaml change; `/api/v1/rig/frame` serves `universe_view()`, and the new `GET /api/v1/rig/dmx` serves
  `Nodes.dmx_view()` (previz). The grid's latency gap (an output whose fixtures all have their own measurement)
  stays as B15 noted.
- **B16:** chip id/kind `screen_pick` and `DELETE /api/v1/nudge/screen_pick` unchanged (api.py needed nothing).

**Three integration rulings**, each found by the Stage-5 e2e on real test music and pinned by a unit test:
1. **No hits under House lights or Lights low** (looks.py; S5.5a). DMX flashed up to level + 0.5 over Lights low.
2. **Only the hits a look would flash wake a runner** (`LookEngine.takes_hit`, `Rig.on_hit`; S5.4b, S5.5a). Every
   band's onsets reach show-rx (13 a second on techno); each re-planned a DMX frame.
3. **A hit never pushes the beat frame** (artnet_lan.py; S5.7a). With dense hits the frame at `t_beat − L` was
   being replaced by pulled frames, so the beat itself was never carried.

**The Stage-5 end to end** (`tests/test_e2e.py::test_stage5_end_to_end`, about 30 s; skipped without ffmpeg). The real
wiring in-process on ephemeral ports; a fake room on its own thread (both screens live; it emits `screen.look` on
every look PATCH, as 2f's room code does); a FAKE Hue bridge (CLIP and DTLS streamer faked at the factory edge; the
module's default factories raise; the pairing file in tmp names a TEST-NET address); Art-Net to a local sink node and
a virtual node; tmp copies of fixtures.yaml (the six devices, Hue on, three RGB pars on two universes), patch.yaml
(`hue.streamer: show`) and show.yaml (the real `lamps`, `director`, `rig`, `looks`). Test audio: a fetched techno
track through the real test-audio child. It checks, and with `-s` prints (one run, niced, 16 Oct):
- room's hue adapter's `POST /api/v1/hue/stream` (the four lamps and a screen: the screen comes back `unknown`), then
  `streaming` with a DTLS session to the fake bridge's area;
- **Hue at 50 Hz:** 300 frames in 6.0 s, 50.00 Hz, gap p50 20.0 ms, p99 20.7 ms; every lamp every frame; each lamp's
  xy equals the director's colour for it at that moment (p95 error 0.0000 xy); colour parity with the virtual
  Maestro's hue channels p50 0.0007, p95 0.0013 xy; the lamps' roles (above: extend, opposite: key) reach Hue;
- **DMX on the beat grid:** every beat in the window had its beat frame (13 of ~13 at 128.1 bpm), leaving +1.03 ms
  after the beat (p95 2.28 ms; niced, under the test's own load), and each was the clock's beat at that moment (|err|
  p50 0.00 ms); 180/190 frames on the 16th grid at the sink; the dimmer follows the engine; hits wake the runner
  (`extra.runner.hits` > 0); no rig or engine errors;
- **the virtual node:** `GET /api/v1/rig/dmx` decodes its universe; `/rig/frame?output=dmx-v` reads `virtual: true`,
  `to: null`;
- **the screens rule:** no screen in any output's frame; at Lights low the Hue brightness is exactly 0.1^0.6 of full
  (16467), the DMX dimmer 26, and the virtual Maestro's screen RGB stays 255 (never scaled);
- **the hold:** the director's palette PATCHes carry `source` (`look_source: yes`); a fake hand pick (`screen.look`,
  source web, palette `ice`) shows the chip and stops every screen PATCH through a Next look (4 s, none sent); Back to
  auto (`DELETE /api/v1/nudge/all`) clears it and the director's palette goes back to both screens at once;
- **release:** `POST /api/v1/hue/release` with the four lamps answers `released: true, via: show` after the fake
  area reads inactive; the brain's ticks have no errors (rig tick CPU p95 1.9 ms, director 1.4 ms).

**The suite** (16 Oct, `flock … nice -n 19 … pytest show_service/tests -q`): **1211 passed, 1 skipped** (the network
OFL test) in 9 min 34 s.

**CPU of the real process** (scratch harness, not in the repo: the real `main()` as a niced subprocess, HOME and every
file in tmp, the Hue bridge faked at the factory edge with each frame encoded as HueStream and sent to a local UDP
sink (no DTLS), Art-Net to local sinks, a fake room with live screens, the techno track through the real test-audio
child, the e2e's lively vibe, one `renderer` WS client, **ML off in every run**; 60 s of `/proc/<pid>/stat` after a
30 s warm-up; load average about 2 on 20 threads; the 4K render was not running):

| Rig | show_service, core | Δ vs shipped | runner threads | Art-Net sinks |
|---|---|---|---|---|
| **shipped** (fixtures.yaml as is: 6 devices, every output off) | 0.410, 0.395 | | none | the virtual Maestro 40 Hz |
| **Hue on** (the cut-over, S5.12 step 5) | 0.428, 0.418 | **+0.020** | show-out-hue 0.024-0.025 | Hue 50.0 Hz; the virtual Maestro 40 Hz |
| Hue + 2 RGB pinspots on one universe (§9.3's first DMX) | 0.461, 0.454 | +0.055 | hue 0.025, dmx 0.022 | DMX 38.5 Hz; the virtual Maestro 37.5-38 Hz |
| 24 RGB pars registered, every output off | 0.413 | +0.011 (the director colours 24 more lamps: its tick p95 2.4 ms) | none | |
| Hue + 16 pars (sink) + 8 pars (virtual node) | 0.493, 0.520 | +0.10 | dmx-a 0.030-0.032, hue 0.027-0.030, dmx-v 0.024-0.026 | DMX 32-33 Hz; the virtual Maestro **32 Hz** |

- show-rx 0.32-0.34 in every run (Stage 4's 0.34-0.36 with the worker); show-bus 0.04; show-brain 0.022 shipped,
  0.032 with 24 more lamps; show-artnet 0.015. Rig tick CPU p95 0.4 ms (off) to 1.9 ms (three outputs). Every run
  exited 0 on SIGTERM with no ERROR line, and no rig, engine or guard error.
- **Per runner:** a bare runner loop (fake engine and driver, 50 Hz) costs 0.007 of a core; the rest is
  `evaluate` (p50 145 µs for 4 Hue lamps, 233-250 µs for 2 pars, 400-540 µs for 8-16 pars; wall time, GIL waits
  included) and GIL hand-overs with show-rx. Runner lateness p95 1.0-1.1 ms (Hue), 1.1-1.6 ms (2 pinspots), 2.2 ms
  (16 pars).
- **Against S5.0 rule 7 (≤ +0.03 with every output on):** **met for the Hue cut-over** (+0.020), **as a floor**
  (S5.14's review): the harness's fake streamer sent each frame inline on the runner thread, so the real
  `hue-dtls-sender` thread (50 wake-ups a second, a queue hand-over, AES-GCM, one more GIL contender) wasn't measured;
  ML was off and the 4K render wasn't running; and the process delta (0.020) is smaller than `show-out-hue`'s own
  CPU (0.024-0.025), so the run-to-run noise is about ±0.01. With the sender, Hue is more likely about 0.028-0.03:
  at the budget line, not under it. How long show-rx takes per packet with the runners on (hit latency, §5.1) wasn't
  measured. S5.12 step 5.4 measures all of it. DMX costs about
  0.022 of a core per universe even for two fixtures, so Hue plus the first pinspots is +0.055, and the synthetic
  24-par rig +0.10, where the extra GIL traffic also slows the virtual Maestro (the screens' feed) from 40 to 32 Hz.
  Both are for c8 and the owner before a DMX output goes on (S5.12 step 6); nothing is bought yet.
- **DMX frame rate:** 31-38 Hz at the sink, not the grid's 43, because a frame that leaves more than the grid's 0.5 ms
  of slack late pushed the next 16th a whole tick on (the spacing measured from the actual send). Beat frames were
  unaffected (the e2e above). At nice 0 the virtual Maestro reads 38.8-39.5 Hz in production (S4.12). **Fixed in
  S5.14** (both artnet_lan and artnet_out): a grid point up to 1 ms inside the gap goes at the gap's end.

### S5.14 Review fixes (16 Oct; c8's fixer)

The Stage-5 review raised 20 issues (7 major; two of the minor ones are the same fix). 19 are fixed here, three of them
with something left for someone else: room's side of the Hue re-sync (68), the low-confidence onsets on DMX (the
owner), and a coloured key light within ±200 K (the owner, vibes.yaml). One, hits on every band, is the owner's call
and unchanged (recorded in S5.12 step 6). Nothing is committed; every output is still off.
SHOW_ENGINE §5.4, §8.6 and §8.8 are the rules each fix follows. Nothing branches on a name: the new behaviour comes
from driver caps, LookData fields and show.yaml keys.

**The drop's unison hit, per output** (major; §5.4: Hue never gets a reactive hit).
- It used to be laid into `palette_fn`, so every lamp, Hue included, got a one-beat full step 60-170 ms late, and a
  Hue output's `caps.hits: false` couldn't stop it.
- Now the director puts it in `LookData.hit` (`contract.LookHit`: `t0_ms`, `t1_ms`, `predicted`, `swell_ms`,
  `lamps`), never in `palette_fn`. The engine lays it over the director's colours per output, from caps alone
  (`contract.hit_weight`, the one rule):
  - an output that takes hits (DMX) shows it at once for [t0, t1);
  - one that doesn't (Hue) shows it at once only for a predicted (armed) drop line;
  - an unforeseen drop on such an output swells: a smoothstep up over `swell_ms`, then back down after t1, mixed in
    OKLab so the colour glides with the level.
- `swell_ms` is the new show.yaml `director.build.hit_swell_ms` (300, §5.4's ≥ 300 ms; allowed 300..10000).
- **The virtual Maestro** (room streams its hue channels to the lamps until the Hue cut-over) now reads
  `Director.maestro_palette_fn`: `palette_fn` plus the hit by the same rule, as Hue takes it. So the lamps change
  today too: an unforeseen drop swells on them instead of stepping.
- The director keeps an unforeseen drop's hit until its swell has fallen back (t1 + swell).
- Tests: `test_the_drop_hit_follows_each_outputs_caps` (Hue's level takes ≥ 300 ms to rise, its xy never steps; DMX
  at once; a predicted line on time on both), `test_an_unforeseen_drop_is_lookdatas_and_swells_on_the_virtual_maestros_lamps`.
  The armed-drop test now checks `LookData.hit` and `maestro_palette_fn`.

**The key light's white** (major; §8.8: warm white, 2400-3000 K, in the quiet feels).
- A role with `white_k` capped the mixed colour's **absolute** OKLCh chroma at its `chroma_max`. A 2700 K white alone
  is C ≈ 0.138, so `lamp_chroma_max: 0.04` dragged the key to about 4900-5000 K, a blue base to lavender and teal to
  lime.
- `director.white_tint` now caps the tint's OKLab (a, b) **distance from white(key_cct)**. A colour's neutral axis is
  re-anchored on the white in proportion to how achromatic it is. So a grey or white palette leaves the white
  exactly as it is (2706 K for 2700). A coloured one moves toward the colour at most `chroma_max` away. With no cap,
  it is the old mix (Party and Disco, 0.3, keep their full colour). A role without a white keeps the absolute cap.
- Test: `test_the_key_light_stays_at_key_cct_in_the_warm_white_feels`, on the real vibes.yaml (every feel whose
  `lamp_chroma_max` ≤ 0.06) and show.yaml roles, at three saturations and ten palettes:
  - a neutral palette lands within 200 K of key_cct;
  - a coloured one is within `chroma_max` of the white, outside `avoid_hues`, and under 4000 K.
- **Not as asked** (the reviewer's "within about 200 K for a coloured palette"): a tint of 0.04 in OKLab, the cap
  vibes.yaml sets, moves a 2700 K white by up to about −560 / +750 K along the locus (red art → ~2140 K, teal →
  ~3450 K). The test bounds it at 20000 K × the cap. If the owner wants ±200 K whatever the art, the quiet feels'
  `lamp_chroma_max` would be about 0.012 (c8's vibes.yaml call).

**No vibe on DMX is the house look** (major; §8.6: on exit DMX falls to its house or failsafe scene).
- With no vibe, the engine marked frames active whenever the features were fresh and the room wasn't quiet. Every
  fixture then got artnet_out's neutral amber with the 0.7 swell, so pinspots would pulse to TV audio or talk.
- New driver cap `idle_without_vibe` (`contract.DriverCaps`; artnet `True`, hue `False`): on such an output no vibe
  is inactive (`why: "no_vibe"`), i.e. each fixture's `house`. Hue keeps the neutral look, because room's hue adapter
  decides which lamps follow.
- Test: `test_no_vibe_is_the_house_look_where_nothing_room_side_gates_the_output`.

**The colour wheel** (major; S5.5 "the nearest colour slot in OKLab").
- Above `WHEEL_WHITE_C`, Open was never compared, so 4000 K took yellow and #ffd0e0 took orange.
- Now the nearest slot of all wins, whites included. The two thresholds still force Open.
- Test: `test_wheel_the_white_slot_competes_on_distance`.

**House lights for DMX fixtures** (major).
- A lamp room has no stored state for (every DMX fixture) got show.yaml's 2700 K × 0.8 under House lights and as the
  start of every entry fade. fixtures.yaml's own `house` was ignored, so the ball pinspots would have lit at 80 %.
- `layout_from_rig` now carries `house`, and `Director._house_rgb` uses it (hex × intensity, as the engine reads it)
  before `director.house`. Room's stored state still wins.
- Test: `test_house_lights_use_the_fixtures_own_house_when_room_has_none`.

**Hue's rise limit** (two minor issues, one fix).
- hue_out capped a rise at 1/attack per second; the engine allows `SLEW_PEAK`/attack (1.5/attack). So the driver
  clipped the engine's own swell whenever depth × level > 2/3: Disco's 0.9 lost up to 0.11 mid-attack.
- `contract.SLEW_PEAK` is now the one constant (looks.py and hue_out.py).
- Test: `test_sent_brightness_matches_the_virtual_maestros_hue_channels` checks what `set_frame` actually sends
  against artnet_out's hue channels, at depths 0.7 and 0.9, within 1/255.

**The DMX confidence policy** (minor; §5.4 Middle: beat-locked on DMX only, at reduced depth).
- The director sent `beat_lock = (gate == beat)` and `hue_depth` 0 otherwise, so the engine's gate-`dmx` branch never
  ran (`looks.middle_depth` was dead) and DMX went steady at middle confidence.
- Now `set_look_out` carries:
  - `beat_lock`: the vibe's own;
  - `hue_depth`: unchanged, 0 off gate `beat`, so the virtual Maestro's Hue and the engine's Hue parity hold;
  - **`beat_depth`**: the same accent depth before the gate. It goes to the rig only; `__main__` filters it out for
    artnet_out, whose clock is gated at `beat` by itself, so nothing changes there.
- The engine's `swell` input reads `beat_depth` while a layer runs beat-locked, so DMX runs × `middle_depth` at gate
  `dmx`.
- §8.6's "one phrase passed" now counts at gate `dmx` too (DMX is locked from there).
- Tests: `test_beat_depth_keeps_dmx_beat_locked_at_middle_confidence`; the director's look tests are updated.
- **Open, the owner's call:** §5.4's "Low: DMX follows onsets" is not built (hits need the gate above `none`). It is
  recorded in S5.12 step 6.

**House lights, Lights low and the dip are steady** (minor).
- They stopped hits and zeroed the swell depth, but any other layer kept moving the lamps (the `motion` layer would
  wave the house light), and hits flashed DMX over the pre-drop dip.
- Now the engine treats `house`/`low` like a held frame (no layer, no hit). The new `LookData.dip` stops hits (and
  `takes_hit`) while the dip is on.
- Test: `test_house_low_and_the_dip_are_steady`.

**The screens' hold** (minor).
- A hold started on any palette pick, even with no feel playing or `send_palette` off. The Music card then said
  "picked on a screen" with no show running, and `engaged()` blocked Auto.
- Now a hold starts only while a vibe plays and `send_palette` is on; otherwise the pick is only logged, with why.
  Turning `send_palette` off ends a hold, as `hold_hand_picks` off does.
- `palette: null` (the card's "The scene's own colours") now reads "Colours: the scene's own (picked on a screen)",
  the Room page's own words. 2f to confirm them.

**Measured emitters, and the curve** (minor).
- The RGBW path (white = min(r, g, b)) ignored a type's `emitters` overrides. A warm-white W turned 6500 K into
  ~3000 K.
- A type that declares any usable `xy` or `Y` now goes through the least-squares solve, RGB and RGBW included.
- fixtures.yaml's `types` example now says to measure each type's `curve` (`linear` or `gamma:<g>`). Under `srgb` a
  linear-dimming RGBA misses #ff8040 by ~0.1 xy.
- Tests: `test_declared_emitters_send_rgb_and_rgbw_through_the_solve`, `test_chromaticity_through_the_curve` (bytes
  decoded through the matching response land within 0.01 xy; `srgb` on a linear fixture misses by > 0.05).

**Hits on every band** (minor): **not changed: the owner's call** (caps default off by ruling). With `bands` unset
and no `flash_hz_max`, Disco's DMX would flicker at up to ~13 Hz on hats. It is recorded in S5.12 step 6, with the
options.

**Hue never strobes** (minor).
- Only the rise slew enforced it: falls are instant, and the 2 × attack rule covered only subdivided beat layers. A
  `square`, `saw` or `phase: time` layer selecting Hue lamps could flicker them at 6-10 Hz.
- New driver cap `min_period_ms` (hue: 2 × `min_attack_ms`, 160 ms; artnet 0). The engine runs any oscillator whose
  cycle is shorter at double its period, again until it isn't, so it stays on its own grid. The layer is listed in
  `stats().per_output.<id>.coarsened`.
- Test: `test_a_hue_output_never_runs_an_oscillator_faster_than_its_min_period`.

**What room asked Hue to follow, across a restart** (major).
- `_asked` lived only in memory. A restart, a crash or the output switched off and on came back `idle` and stopped the
  area, while room still read "Following the music" until the next mode change.
- show_service's side:
  - `HueOut.persist_state()` / `restore_state()`; `Rig.persist()` keeps each driver's state by output id in
    state.json's `rig`, also for an output switched off since, and hands it back when the output is built again;
  - restored only while patch.yaml still says `hue.streamer: show`;
  - `request` and `release` mark state.json dirty.
  - S5.6b: forgotten (and persisted so) on the flip from `show` to `room`; kept and updated while the output is off.
- Room's side (in `read()`, POST `/hue/stream` again when our set differs from `show_wanted` or our boot changed) is
  68's, now built (`_sync_show`). It reads `lights` (`HueStatus.asked`, unfiltered) and `boot_id` from `GET
  /api/v1/hue/status` (S5.6c).
- Tests: `test_what_room_asked_comes_back_after_a_restart` (a new HueOut takes the set back and streams with no
  request; not under `streamer: room`), `test_driver_state_survives_a_restart_and_off_and_on` (the Rig's plumbing,
  through `persist` → `restore`).

**The DMX grid's tolerance, and the rate cap's slack** (major and minor): S5.7a.
- Tests: `test_a_little_lateness_doesnt_skip_grid_ticks`, `test_a_rate_cap_keeps_the_grids_slack`, and
  `test_a_little_lateness_keeps_the_screens_feed_on_its_grid` (artnet_out).
- With the old `_grid`, the lateness test reads 21 Hz at 0.8 ms of lateness (checked).

**fixtures.yaml half-written** (minor).
- A poll mid-way through an in-place save could load a truncated file that is still valid YAML (an output on, its
  fixtures cut off). The Hue lamps would then blip for a few seconds.
- `Registry.poll` now applies a file change only when the next poll sees the same files (about a second later). The
  first load, `force`, and show.yaml's own inputs still apply at once.
- Test: `test_a_half_written_file_is_never_loaded`; the registry tests poll twice after an edit.

**A hit wakes only its own outputs** (minor).
- `LookEngine.takes_hit(d, output_id)` also asks whether that output has a fixture in `looks.hits.select` (compiled
  per output). `Rig.on_hit` wakes only those runners, so a universe whose fixtures never flash stays on its beat grid.
- Tests: `test_takes_hit_per_output_follows_the_hits_selection`,
  `test_a_hit_wakes_only_the_outputs_whose_fixtures_it_would_flash`.

**The build-ahead** (minor): S5.4b (`max(1 ms, the runner's own evaluate p95 + the GIL switch interval)`, at most
4 ms). Test: `test_a_runner_builds_ahead_by_its_own_evaluate_cost`.

**The CPU figure** (minor): S5.13 now calls +0.020 a floor and says why; S5.12 step 5.4 measures per thread, with the
DTLS sender, ML and the render on.

**The suite** (16 Oct, `flock … nice -n 19 … pytest show_service/tests -q`): **1242 passed, 1 skipped** (the network
OFL test) in 9 min 40 s. The Stage-5 e2e (`-s`, niced): Hue 50.00 Hz, xy against the director's colour p95 0.0000,
against the virtual Maestro's hue channels p95 0.0012; DMX beat frames 13 of ~13, +1.29 ms after the beat (p95 3.48);
**DMX at the sink 39.3 Hz** (32.5 Hz before the grid's tolerance), 223/236 frames on the 16th grid; runner lateness p50
0.58, p95 1.89 ms; rig tick p95 1.93 ms. Isolated, `evaluate` for 4 Hue lamps is p50 37-47 µs, p95 72-80 µs (the
full suite's one reading of 164 µs was under the host's other load).

**Interfaces changed** (all additive; old callers keep working):
- `contract.LookData` gains `dip`, `hit` (`LookHit`); `DriverCaps` gains `min_period_ms`, `idle_without_vibe`
  (NotRequired); `contract.SLEW_PEAK` and `contract.hit_weight`; `Frame.why` gains `no_vibe`.
- `LookEngine.set_look(..., beat_depth=)`, `LookEngine.takes_hit(d, output_id=None)`, `Rig.set_look(...,
  beat_depth=)`.
- `Director.maestro_palette_fn` (`__main__` gives it to the virtual Maestro), `director.white_tint`.
- `HueOut.persist_state` / `restore_state`; `Rig.persist` returns `{"outputs": {...}}`.
- show.yaml `director.build.hit_swell_ms`. In `__main__`, `ctx.set_look_out` sends artnet_out only `hue_depth`,
  `beat_lock` and `reset`.

### S5.15 The Mic choice (16 Oct; the owner's ask via d4)
- `inputs.py`: `Inputs` lists the room's sound inputs from receiver health and show.yaml: the permanent senders'
  streams, less test, MIDI and `room_sound: false`. Each has a `label` (show.yaml `senders.<name>.label`, else the
  sender's name), its state, its `device` (the heartbeat's `dev`) and its `devices` (the heartbeat's new optional
  `devices`). `GET /api/v1/inputs`, `PUT /api/v1/input {stream|null, device?}`; persisted in `STATE_DIR/input.json`.
- `Receiver.set_choice(stream|None)`: a priority layer after `set_priority` (test streams) and before
  `cfg.stream_priority`, so sources.py resetting its priority never clears the owner's pick.
  `Receiver.set_device_hints({stream: device})` adds `devices` to that sender's SUBSCRIBEs, sent at once on a change.
- `vban`: heartbeat `devices` (≤32 × ≤128 chars, trimmed to fit 1436 B); SUBSCRIBE `devices`;
  `vban.subscribed_devices(pkt)` for senders. Receivers and older senders ignore keys they don't know.
- The card's `hearing.mic` = `Inputs.mic()`: {stream, device, chosen} for the active stream.

### S5.16 Mic on demand (16 Oct; the owner's rule)
The room's mic runs only while something needs it: Auto is on, or the mode follows the music. Otherwise it is off,
with the senders' capture devices actually closed. The Room page's Mute always wins.
- **`mic.py`** (`Mic`): `wanted` = Auto on (`Autopilot.is_on()`, read live) or the mode claim (room_service's
  device `show.mic` PUTs `{"mode": true}`, `{"mode": false}` when it puts it back); `on` = wanted and not muted.
  - Which streams capture while on: the room-sound inputs (`inputs.Inputs`). With a Mic choice, only the chosen
    stream captures; if it hasn't been live for 10 s (since the mic came on, the choice was made, or it was last
    live), every room-sound stream captures until it is. With no choice, all of them. Off or muted: none. MIDI and
    test streams are never the mic.
  - Re-decided at 2 Hz on thread `show-mic`, at once on a PUT, and on `wake()` (the autopilot calls it on a
    `set_on` and on a room status that changed Auto). The receiver hears only changes:
    `rx.set_off_streams(off, covered)`. The ring hears `Rings.set_off(off)`.
  - Persisted in `STATE_DIR/mic.json` (`{"v": 1, "muted", "mode"}`, tmp + fsync + replace). A bad file starts
    unmuted with no mode claim and says so in `error`.
  - `GET /api/v1/mic` → `{on, muted, wanted, why, claims: [{by: auto|mode, label}], mode, streams: {capturing, off},
    error}`; `PUT /api/v1/mic {muted?, mode?}` (at least one bool, else 400 `{error, detail}`) → the GET body. `why`
    is page words: "Listening for Auto", "Listening: Party follows the music", "Listening for Auto and Party",
    "Off: nothing needs it", "Muted", "Muted: Auto can't hear the room", "Muted: Party can't follow the music",
    "Muted: Auto and Party can't hear the room". The mode's label comes from the room view, else "this mode".
- **`vban`**: SUBSCRIBE `off: [streams]` (`subscribe_obj`/`subscribe_packet(off=)`, key only when non-empty;
  `subscribed_off(pkt)` for senders); an empty `subscribe` is still a SUBSCRIBE. Heartbeat state `"off"`: device
  closed, `frame`/`n`/`t_cap_ns` null, `devices` still sent. A stream in neither list stops, as before.
- **`receiver.py`**: `set_off_streams(names, covered=None)` (any thread; applied at the next tick, SUBSCRIBEs at
  once). Every sender still gets a SUBSCRIBE each second: `subscribe` = its streams minus the off ones, `off` = the
  rest. Privacy at start: a configured stream that could be a mic (not MIDI, not a test stream, not an on-demand
  sender's) is held off until a decision covers it. `Mic.attach` decides before show-rx starts, and a sender added
  later waits for the next decision. Audio of an off stream is dropped on arrival: no meter, no `on_level`, no
  delivery (so no DSP, ring, ML feed or listener). Straggling packets are counted in `off_dropped`, those more than
  1 s after it went off also in `off_ignored` (logged). Dropping starts at once: there is no grace for the sound
  itself, only for the counting. State `"off"`: kept off here, or a fresh heartbeat says "off" with no audio after
  it. An "off" stream is never live; the active stream leaves it at once; MIDI detaches from its map; back on, its
  "quiet" needs 5 s of new silence (nothing from before the off counts). Health:
  `off`, `off_held`, `off_decided`, `off_dropped`, `off_ignored`, and per stream `off_dropped`/`off_ignored`.
- **`ring.py`**: `Rings.set_off(streams)`: an off stream is never written and reads as empty. Going off or coming
  back marks its ring `forget`: the writer floors it before its next block, so no clip ever holds sound from before
  the mic went off. `off_gen` steps each time a stream goes off.
- **`listener.py`**: an off stream gives no clip ("the mic is off"). A clip whose encoding saw `off_gen` move is
  dropped before the upload ("the mic went off").
- **`situations.py`**: features and CED readings of an off stream read as missing. So quiet, talking,
  music_audible and energy_above read unknown while the mic is off ("the mic is off"), a rule on "quiet" never
  fires because the mic was switched off, and piano_playing's heard_by check is skipped (as when that mic is down).
- **The card** (`api.card`): top-level `mic_state` (the GET body). `hearing.text` is "Mic muted" or "Mic off"
  when that is why no room sound comes in, ahead of the connection words, with `hearing.level` 0. "The microphone
  is starting" covers every room stream still "off" while the mic is on.
- `GET /api/v1/state` carries `mic`: the GET body plus `receiver` (its `off*` health keys).
- **Without the brain** (`--no-brain`) there is no Auto: the mode claim alone decides, before the first SUBSCRIBE.
  If mic.py fails to load, the receiver keeps every room mic held off (privacy first), logged.

### S5.17 Song ends (17 Oct; the owner's ask; the event shape agreed with 04)
"End of track / song would be detected by art director so it could naturally transition (and select an appropriate
transition) at those junctures." Plain words: docs/SHOW_ENGINE.md §8.5 "Song ends"; bus shapes: §11.1.
- **`structure.py`, the end ahead (Sonos).**
  - **The estimate.** While `now_playing.state` is `playing` with a usable `duration` and `position` ("H:MM:SS";
    a stream's 0 or empty gives nothing), the song's start on the monotonic clock is
    `read time - position`. The read time is `now - the member's age_s`.
    - Positions are whole seconds, so the start is an interval 1 s wide. Each later read that agrees narrows it
      (0.15 s slack for room's rounded ages and the read's own lag). One that doesn't (a seek, or a pause and play)
      starts it again.
    - The predicted end is the interval's middle + duration. There is no Sonos → mic delay, because structure models
      none.
  - **Arming.** Once the end is within `track.arm_s` (20), `track_end_armed` goes out. It is re-sent with the same
    id when the end moves more than `rearm_s` (0.5).
  - **Calling it off** (`eta_s: null`, `detail: "disarmed"`):
    - a pause or stop, or a muted or silent zone (sonos_playing false);
    - a seek back past `arm_s`, or the length being lost;
    - a track change more than `end_slack_s` (3) before the end: another Sonos track (a skip), or a dsp track change.
  - **The end.** `track_end` via `sonos` goes out when the armed end passes, or when the track id changes or Sonos
    stops within `end_slack_s` of it.
  - **The change itself.** The track change goes out at once if there is sound. If the change comes in silence, it
    waits for the new song's first sound, at most `first_sound_s` (10).
- **`structure.py`, the end in the sound.**
  - A silence (the track test's `gap_gate` / `gap_lufs_m`) that lasts `track.end_gap_s` (3) after music is an end.
    Music means situations' `music_audible`; when that is unknown, any sound counts. `track_end` via `dsp` goes out
    with `at_ms` = the gap's start.
  - There is no dsp end while Sonos vouches for the song (playing, its end further than `arm_s`). There is one
    `track_end` per song: the first of the two vias wins.
  - **The sound back.** After a `track_end`, if sound returns with no track change (at once via dsp; via sonos
    after `end_slack_s`, so a gapless next id can still arrive), `track_end` is re-sent with `detail: "sound_back"`
    and `at_ms` = the first sound. A later silence after music sends `track_end` again (the same id).
  - **`track_change` at_ms.** The new song's first beat while the clock's gate is beat|dmx, else its first sound:
    - after a dsp gap, the sound's return;
    - via sonos, the armed end, the end of a short gap, or the first sound it waited for.
  - It now also carries `prev_track_id`. A dsp track change is still detected exactly as before.
- **The hook.** The new `ctx.track_transition(event, TrackFacts)` slot is provided by the director. `_emit` calls it
  synchronously on show-brain while it builds a TRACK_EVENT_KINDS event; a call-off is not sent to it.
  - It returns `{"transition"}` or `{"held": True}`, which goes on that same room.event. There is no second event.
  - A failing hook leaves the event without either, and is counted in `ends.hook_errors`.
  - `TrackFacts`: `{silent, phase, gate, old, new, back}`. `old` and `new` are `TrackProfile`s `{genre, key, mode,
    bpm, energy, artist}`; a Sonos change at once knows only the new artist.
- **Event ids** (every room.event, both publishers): `contract.EventData.id`. A re-send keeps its id; a moved drop
  line's drop_armed and its call-off share one.
- **`director.py`, `track_transition`** (`choose_track_transition`, `change_size`; show.yaml
  `director.track_transitions`). A person's hold (Keep this look, or their change pending) → `{"held": True}`.
  Otherwise:
  - **Through dark (`fade-dark`)** when the gap is certain or expected:
    - a track_end via dsp;
    - at a Sonos end, or armed ahead of one: the room silent, or the phase `outro`;
    - a track_change while this junction's track_end went dark and no sound_back came.

    `out_s` / `in_s` (2.5 / 2.0) × the feel's scale, each within `min_s`..`max_s` (1.5..4); `dark_max_s` 8.
  - **Otherwise a segue**, by the feel's `styles.<transition_style>.cut` (no feel, or a word with no entry:
    `default`):
    - `never`: a crossfade;
    - `beat`: a cut `on: downbeat` while the clock is at `segue.cut_gate` (beat) or better, else a crossfade over
      `crossfade_s` (2) × scale;
    - `always`: a cut (`on: now` without the beat).

    Starting points: gentle never ×1.5, lively beat ×1, wild always ×0.6.
  - A sound_back repeats the end's choice.
  - **`change`.** On a track_change, each term known on both sides votes:
    - the genre words differ;
    - the keys are ≥ `key_fifths` (2) apart on the circle of fifths (the same, parallel or relative key is 0);
    - the tempos are off by more than `tempo_ratio` (0.08), folded by octaves;
    - the mean energy is ≥ `energy` (0.25) apart;
    - Sonos's artist differs.

    It is big at ≥ `big_share` (0.5), and `unknown` (big) when nothing is known yet (always on the two end events).
- **`director.py`, its own look.**
  - **A fade-dark track_end** dips the lamps toward the vibe's floor over `out_s` (never off; `LookData.dip` true).
    A new look, a sound_back, or `dark_max_s` releases the dip, and the lamps return over `in_s`.
  - **The track_change's look** lands on the first bar line at or after its at_ms that the screens' lead allows (no
    grid: then). It crosses over as the event says: a cut; a crossfade over `seconds`; or, for fade-dark, a crossfade
    over `in_s`.
    - It now applies to every `change_looks`, not only phrase vibes. A song change is the junction.
    - It also lands when the director is relaxed by the gap's own quiet, since the new song is the sound coming back.
      It does not land when the feel is relaxed because people talk.
  - **`change: small`** keeps the palette; anything else moves it on.
  - **Holds.** Keep this look, a pending human change, or the event's `held`: no look of ours.
- **Snapshots.**
  - structure: `ends` `{sonos (end_s, remaining_s, spread_s, reads, duration_s), armed, end, waiting_first_sound,
    heard, counts}`.
  - director: `sections.song_end` `{junction, dip, stats}`.
  - The director log gets `transition` and `song_end` records.
- **Tests.**
  - `test_structure` (Sonos arm, re-arm, call-off on pause / seek / skip, the end, a stream with no length, the wait
    for the first sound; a dsp end at the gap's start but not on a 1.5 s rest, after talk alone, or in a break Sonos
    vouches for; sound_back; the hook's answer, held or nothing; ids);
  - `test_director` (the transition table, change sizes, the lamps' dip and the junction's look, a small change, the
    holds, no feel, the real hook through structure, settings);
  - `test_contract`.
- **For 04 (the renderer).**
  - Play `transition` on that change, using the half that applies:
    - fade-dark `out_s` from the track_end (ending at an armed end's at_ms where it can), and `in_s` from the
      track_change (or from a `sound_back`);
    - dark for at most `dark_max_s`, then back by `in_s`.
  - `held: true`: ignore the cut for that change.
  - The `section_change` with `detail: "track"` that follows a track_change is the same junction.
  - Absent `transition`: the mode's default, as today.
