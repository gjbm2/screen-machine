# piano_render

MIDI file + instrument id → sampled piano, rendered on demand as it plays, on PC22's speakers (`pc22.py`) or a Sonos
(`sonos.py`). Nothing is rendered ahead and nothing is stored (owner, 22 Oct: "it should be rendered on demand").
It replaces the test piano's Windows GS Wavetable synth with sampled pianos: sfizz plays SFZ libraries and
FluidSynth plays SF2s. Stream frame 0 is MIDI time 0, and each output reports when it is heard, so the MIDI sent to
the screens is timed against the same clock (see Latency and compute). Engines are pluggable, so a paid engine
(Pianoteq, a VST3 host) is one more module.

- The live path: `session.py` (started by show_sender) loads the sound once and renders each play through
  `stream.py` into the sink while it plays; `calibrate.py` hears it through the room's mic and moves the MIDI clock.
- `render.py` still renders a whole piece offline to a FLAC, for the audition set and comparisons only. It keeps
  nothing unless asked (`out=` a file, or a cache you name). There is no prerender any more.

## Run

```bash
python3.12 -m venv ~/.venvs/sm-piano && ~/.venvs/sm-piano/bin/pip install -r piano_render/requirements.txt
PY=~/.venvs/sm-piano/bin/python                       # run from the repo root

$PY -m piano_render list                              # instruments (ok, or why not) and rooms
$PY -m piano_render fetch                             # install instruments, IRs and sfizz (fetch.py)
$PY -m piano_render render "config/live/test-piano/Classical - Chopin - Nocturne in E-flat, Op. 9 No. 2.mid" \
    --instrument grand-c5 [--out FILE.flac] [--room recital|none] [--no-realism] [--force] [--json]   # offline
$PY -m piano_render play-pc22 FILE_OR_MID [--instrument ID]   # pc22.py: out loud on PC22 (a .mid renders as it plays)
$PY -m piano_render.session --midi MID --instrument ID [--pad-s S] [--output pc22|sonos:ZONE]   # show_sender's helper
$PY -m piano_render fetch --status                    # what is installed (no network)
$PY -m piano_render audition [--out DIR] [--instruments ID ...] [--excerpts N ...]   # see Audition
$PY -m piano_render.stream MIDI --instrument ID [--out FILE.wav | --out -] [--ahead 0.5] [--unpaced]  # render as it plays
```

- `--config PATH` (or `$PIANO_RENDER_CONFIG`) picks the registry. The default is
  `config/piano/instruments.json`.
- `render` writes `--out FILE` (default `<piece> - <instrument>.flac` in the current folder). `--cache DIR` (or
  `$PIANO_RENDER_CACHE`) keeps renders in a cache of your own instead; there is no default cache.
- `render` and `audition` drop themselves to idle CPU priority (SCHED_IDLE, nice 19) unless you pass `--no-idle`.
- On this box, still wrap long jobs as the CLAUDE.md rule says:
  `chrt -i 0 flock -w 1800 ~/.cache/screen-machine/cpu.lock nice -n 19 ...`
- The package sets `OMP/OPENBLAS/MKL/NUMBA_NUM_THREADS=1` unless they are already set.
- `fetch`, `play-pc22` and `audition` are imported only when used. If one module is missing, the other
  commands still work.

From Python:

```python
from piano_render import stream, render
st = stream.Streamer(midi_path, "grand-c5", ahead_s=1.0); st.open()   # as it plays (see the contract)
for blk in st.blocks(position=sink_position): sink.write(blk.data)
st.rewind()                                      # back to frame 0 for another play, samples still loaded

res = render.render(midi_path, "grand-c5", out="piece.flac")         # offline; RenderResult, FLAC 24-bit 48 kHz
res = render.render(midi_path, "grand-c5", cache_dir="work/renders") # or in a cache you name (audition does)
res.cached, res.lufs, res.true_peak_dbtp, res.onset_ms, res.stats
render.cached(midi_path, "grand-c5", cache_dir=...)  # lookup only, never renders
```

## Tests

```bash
OMP_NUM_THREADS=1 NUMBA_NUM_THREADS=1 chrt -i 0 flock -w 1800 ~/.cache/screen-machine/cpu.lock nice -n 19 \
  ~/.venvs/sm-piano/bin/python -m pytest -q piano_render/tests
```

The tests take about four minutes with no network and no sound (`test_stream.py` alone about 35 s).

- sfizz tests build a one-sample SFZ in tmp. They need libsfizz under
  `~/.local/share/screen-machine/piano/engines/sfizz` (or `$PIANO_RENDER_SFIZZ_LIB`) and are skipped without it.
- FluidSynth tests use `/usr/share/sounds/sf2/TimGM6mb.sf2`.
- `test_pc22.py` and `test_fetch.py` belong to those modules. `test_pc22.py` runs files and streams through the
  real player and `pc22.py` on a fake device (no Windows, no sound). Its four `test_windows_*` tests use PC22's
  audio device (digital silence) and run only with `PIANO_PC22_DEVICE_TESTS=1`.
- `test_sonos.py`: fakes only (room_service, the Sonos's UPnP and its HTTP fetch of a radio stream, show_service's
  stream route); streams use the real ffmpeg, into tmp. Its last test is the whole live chain on Sonos: a real
  `Session` on a real Streamer through `SonosOutput` to the fake Sonos.
- `test_session.py`: the session's protocol on real Streamers (TimGM6mb) into fake sinks that pace and read them as
  the real ones do; the arm; the calibrator's wiring; the kept copy (real ffmpeg, into tmp); the load estimate;
  `main`'s refusals; the gain through the real `PC22Output` with a fake player.
- `test_stream.py`: streamed engines equal `render()` sample for sample, the realism chain against
  `realism.apply` + `master.process`, block-size independence, pacing (a fake clock), the limiter and the
  memory cap.

## Latency and compute

Measured 23 Oct on this box (20 vCPU WSL, no screen live, load average 3-13), for `stream.py`: the piece is
rendered while it plays and nothing is stored. `session.py` plays every press this way (since 23 Oct; before, it
rendered whole pieces into a FLAC cache, up to 12 sounds x 25 pieces, about 35 MB each).

**The rule.** The MIDI for an event at MIDI time t is sent at `t_heard(t) - L`.
- `t_heard(t) = H0 + t`. H0 is when sample 0 is heard: the sink's own report plus that output's calibrated delay.
  - PC22: the player's DAC time of sample 0 (`t_audio0`).
  - Sonos: the start estimate, corrected by the RelTime edges.
  - The calibrated delay is measured with the mic in the same room. PC22 has its own mic; the drawing room has the
    room mic: cross-correlating what we sent with what it hears gives the delay. Our own numbers go in
    `~/.local/state/screen-machine/piano/latency.json`.
- `L` is show.yaml `tests.piano.lead_ms` (650 ms). show_service moves the stream's notes `+L`, so the screens draw
  each note when it is heard.
- The render-ahead buffer (`ahead_s`, 0.5 s) is the only latency we choose. It delays only the start, because H0 is
  measured at the sink, after the buffer.

**The MIDI path to the screens.**
1. show_sender sends each event at `t_heard - L`.
2. show_service receives it over UDP on this box (well under 1 ms) and applies the `+L` runtime acoustic offset.
3. stream_service's sound_view evaluates notes at photon time. Its render→photon lead was 536-541 ms on tonight's
   sessions; notes stamped ahead wait, up to 2 s.
4. The photons appear at `t_heard`. That leaves 0.1 s of slack, so L must stay above about 0.6 s and below
   1.9 s (`LEAD_MS_MAX`).

**From press to sound, per output.**

| Step | PC22 (WASAPI) | Sonos (live stream) |
|---|---|---|
| Process start and `import piano_render.stream` | 0.36-0.38 s | the same |
| `open()`: the engine (partial preload) alongside the chain build | concert-d 2.9 s (engine 1.0 s, chain 2.9 s), fazioli-sf2 1.9 s (engine 0.07 s); see the engine loads below | the same |
| ...of which `import scipy.signal` (the chain's) | 2.5 s quiet, 4.2 s busy, in parallel with the engine | the same |
| Fill: `ahead_s` plus the pipeline's own lookahead (4,250 frames, 89 ms), at sfizz's `max_speed` 2.28x | 0.26 s (FluidSynth: as fast as it renders) | Sonos's own buffer (below) |
| Sink start | player spawn→ready 2.55-2.85 s (py.exe, sounddevice, WASAPI open; 4 silent runs), can overlap `open()`; then prebuffer 100-300 ms; WASAPI buffer 30 ms (20 asked) | the PUT, then the Sonos fetches over HTTP and buffers before it plays: unknown for a live stream, expected 1-3 s (a file took 0.50 s, PUT to heard, 21 Oct) |
| After `t_audio0` | the Windows engine (one 10 ms period) and the DisplayPort monitor's audio: unmeasured, expected 20-80 ms; calibrate with PC22's mic | the Drawing Room Amp: a few ms after its buffer; calibrate with the room mic |
| **Press → heard** | **measured end to end (The live session, below): 3.6-4.2 s for fazioli-sf2 and concert-d, 5.6-6.6 s for grand-c5, with the player started during the load** | about 3.5 s plus Sonos's buffer at up to 2.3x |

- Most of a cold start is process work that repeats on every press: the imports, the engine load and the PC22
  player spawn. `session.py` overlaps the player's spawn with the load (the arm, below), so on PC22 a press costs the
  load, about 0.5 s of fill and start, and the pad.
- A persistent stream process that kept the current sound and its player open would remove the load and the spawn
  too: about 0.4 s plus the pad on PC22 (an estimate from the parts). Not built: show_service starts show_sender, and
  so the session, per press.
- For Sonos the render is paced by the wall clock from its first block, `ahead_s` ahead (use
  `sonos.STREAM_AHEAD_S`, 4 s). The Sonos reads the growing MP3 at its own pace (TCP) and starts later than that
  clock, so its buffer holds at least `ahead_s`.

**The sinks** (`pc22.py`, `sonos.py`; measured 23 Oct, no screen live, nothing audible).
- `PC22Output.start(streamer)`: the player's prebuffer is the stream's `ahead_s`, and `position()` is the frame
  the WASAPI callback takes now (within 1 ms of the wall clock from `t_audio0`). 20 s silent runs (the PCM at
  -120 dB) on the real device: fazioli-sf2 and concert-d, 0 underruns, 0 late blocks, 39 pipe writes a second
  (blocks are coalesced to 50 ms), stop to silence 30-37 ms, `t_audio0` 1.8-2.3 s after `start()` with a cold
  player, peak RSS 0.24 / 0.93 GB.
- `SonosOutput.start(streamer, url=...)`: `Mp3Stream` encodes the stream with ffmpeg (libmp3lame 320 kb/s CBR)
  into `~/.local/state/screen-machine/piano/streams/<id>.mp3` as it plays; show_service serves it at
  `/api/v1/piano/stream/<id>.mp3`; the Sonos plays `x-rincon-mp3radio://<host>:8700/show/api/v1/piano/stream/<id>.mp3`.
  Files go 10 minutes after the stream ends (2.4 MB a minute meanwhile).
  - ffmpeg: 7-10% of one core while it streams (piano audio, paced), plus about 0.3 s and 1.5 CPU-s to start.
    It adds 64-80 ms before the bytes are in the file, and LAME's delay (1,105 frames, 23 ms) to the sound.
  - The PUT waits for 2 s of audio in the file (`STREAM_PUT_AFTER_S`): 0.8-1.1 s after the stream starts
    (concert-d, fazioli-sf2, with a 0.75 s pad).
  - Through room_service's real `/show` pass-through and the route, a reader got the stream chunked and byte for
    byte, live (first bytes 0.07 s after its request).
  - The Sonos's own start delay for a live stream is `STREAM_START_MS_DEFAULT` (1.5 s, a guess) until
    `calibrate.py` measures what follows it with the room mic.

**The live session** (`session.py`, the live path since 23 Oct).
- A press: show_service starts show_sender, which starts the session. The session loads the sound once
  (`Streamer.open()`); its "preparing" bar is the time so far over this sound's last loads on this box
  (`~/.local/state/screen-machine/piano/loads.json`). On PC22 it starts the player at the same time, timed to be ready
  about when the load ends (the arm: `ARM_LEAD_S` 3 s before the expected end; the player waits silent, its device
  open). On "play" the stream fills the player's prebuffer and starts.
- Render-ahead: PC22 1.0 s (`PC22_AHEAD_S`: a heavy sound gets 1 s against bursts; filling it at 2.28x takes 0.44 s
  and happens while the player starts); Sonos 4 s (`sonos.STREAM_AHEAD_S`).
- The pad: show_sender asks for `L - first event + 0.1` s (0.75 s for a piece that starts at once, 0 for a recording
  with a second of silence first) so the first MIDI event is never late. PC22 plays it as silence. On Sonos it is
  reduced by the Sonos's expected start delay (its 1.5 s guess plus the measured delay), so usually 0: the MIDI just
  goes out later.
- Loops and re-presses within a session reuse the loaded sound: `Streamer.rewind()` runs in the background as soon as
  a play ends (fazioli-sf2 0.02-0.10 s, concert-d 0.06-0.09 s), and the next pass's player is armed 0.5 s after it
  (`REARM_S`; a once-only play's stop drops it before it starts). A silent two-pass loop (fazioli-sf2, the recording
  below, show_sender's loop): 0 underruns both passes; the second pass's start 2.3 s without the re-arm, 1.7 s with
  it even when its "play" came 0.05 s after the first ended (with show_sender's own 3 s gap the player is ready first).
- Calibration: every block also goes to `calibrate.py`'s `StreamReference`; `begin()` at each start (its prior delay is
  in "started"'s `t_audio0` and `delay_ms`), `end()` at each end. Off for a silent check (`PIANO_SESSION_GAIN_DB`)
  and with `PIANO_CALIBRATE=0`.
- Kept copies: none by default. `PIANO_SESSION_KEEP_DIR=<dir>` keeps an MP3 (LAME VBR q2) of each play that played to
  its end, the newest 50 (`PIANO_SESSION_KEEP_MAX`).
- Until show_service serves `GET /api/v1/piano/stream/<id>.mp3` (it needs a restart), `--output sonos:<zone>` execs
  `session_files.py`, the old cached-file path, unchanged (it probes the route at each start). Delete that file and
  `~/.cache/screen-machine/piano/renders` once the route is live.

Measured end to end, 23 Oct, silent (show_sender's `run_test_piano` in process with its MIDI recorded, the real session
and the real PC22 player and device, the PCM at -120 dB; lead 650 ms; the arm on; nice 5 as live; no screen live). The
test piece is the Grieg Waltz Op. 12/2 (111 s, first note at 0); the recording is 46 s of the Chopin Nocturne Op. 9/2
written by `show_service/recorder.encode_smf`, with 1.3 s of silence first. The grand-c5 rows and the last ran beside
another session's score stress test (8 CPUs at nice 19, load average 4.6-6).

| Sound | Piece | Load | Press → `t_audio0` (after the load) | CPU (one core) | Peak RSS | Underruns / late blocks | MIDI sent vs due: median / p99 / max |
|---|---|---|---|---|---|---|---|
| fazioli-sf2 | Grieg | 2.4 s | 4.2 s (1.5 s: 0.7 start + 0.75 pad) | 7% | 241 MB | 0 / 0 | 0.30 / 0.83 / 2.3 ms |
| fazioli-sf2 | recording | 2.5 s | 3.7 s (0.7 s) | 6% | 237 MB | 0 / 0 | 0.30 / 0.79 / 1.3 ms |
| concert-d | Grieg | 2.5 s | 4.1 s (1.35 s: 0.6 + 0.75 pad) | 24% | 943 MB | 0 / 0 | 0.32 / 0.87 / 10.9 ms |
| concert-d | recording | 2.9 s | 3.6 s (0.5 s) | 24% | 980 MB | 0 / 0 | 0.30 / 0.82 / 1.2 ms |
| grand-c5 | Grieg | 4.2 s | 6.6 s (1.9 s: 1.1 + 0.75 pad) | 38% | 847 MB | 0 / 0 | 0.37 / 0.97 / 13.4 ms |
| grand-c5 | recording | 4.0 s | 5.6 s (1.1 s) | 103% | 913 MB | 295 / 2,638 (up to 2.4 s late) | 0.40 / 1.13 / 4.5 ms |
| concert-d, arm off | recording | 5.2 s (busy) | 8.1 s (2.4 s) | 33% | 986 MB | 0 / 0 | 0.36 / 0.89 / 3.8 ms |

- Every event was sent and stamped on time against `t_audio0` (stamps within 0.26 ms). The arm takes about 1.9 s off
  each press.
- **grand-c5 can't keep real time on dense pedalled music when the box is busy.** Its wrapper turns on sampled
  release, hammer noise, pedal noise and pedal resonance (15,336 regions, up to 193 voices). On the recording, in a
  fresh process beside the stress test: the render thread 91% of a core, 112% with sfizz's loader. Sample quality 1:
  84% (the output -60 dB from quality 2); 128 voices 87% (-42 dB); 96 voices 63% (-34 dB). The underruns put the
  sound late against the MIDI (the calibrator, when its mic is on, would see it). `"stream_voices"` in
  instruments.json caps an instrument's voices while streaming (sfizz can't change it mid-render: that silences every
  voice); it isn't set for any instrument yet.

**Engine loads with partial preload** (`STREAM_PRELOAD` 32768 frames). Each figure is the median (range) over 40
fresh processes, with the resident memory right after the load.

| Sound | Load | RSS after load |
|---|---|---|
| concert-d | 1.9 s (1.2-4.2) | 211 MB |
| grand-c5 | 4.3 s (3.0-5.8) | 218 MB |
| steinway-b-162 | 5.0 s (2.4-10.0; 2.5 s with the page cache warm) | 281 MB |
| fazioli-sf2 | 0.13 s (0.10-0.19) | 60 MB |

The full preload of the offline path took 10-60 s and 2.5-7 GB per load.

**Compute at 1x** (realism on, paced like a sink, one stream alone, the whole process's CPU):

| Sound | Piece | CPU (% of one core) | Late blocks | Peak RSS / VmData |
|---|---|---|---|---|
| fazioli-sf2 | Rachmaninoff Op. 32/8 (112 s) | 9 | 0 | 0.30 / 0.26 GB |
| steinway-b-162 | Chopin Op. 9/2 (first 60 s) | 28 | 0 | 0.92 / 1.10 GB |
| steinway-b-162 | Rachmaninoff (first 60 s) | 45 | 0 | 1.40 / 1.55 GB |
| concert-d | Chopin Op. 9/2 (257 s) | 45 | 0 | 1.42 / 1.73 GB |
| concert-d | Rachmaninoff (112 s) | 61 | 0 | 1.84 / 1.90 GB |
| grand-c5 | Rachmaninoff (first 60 s) | 74 | 0 | 1.32 / 1.50 GB |
| grand-c5 | Chopin Op. 9/2 (first 60 s) | 80 | 10 (11 ms at most) | 1.04 / 1.22 GB |

- **Where it goes.** sfizz itself takes most of it, in two parts.
  1. The voice rendering, on the stream's own thread: about 0.75 core for grand-c5's 160 voices, 0.2-0.45 for
     20-75 voices.
  2. sfizz's background loader: it reads each sample whole once a note needs more than its preload. That is
     10-30 MB of float32 per note, with its page faults. It costs a third (grand-c5) to about as much (concert-d)
     as the voice rendering, and twice as much for steinway-b-162, whose samples are FLAC.
- **The chain** (thread CPU, concert-d): resonance 4.4%, room 4.6%, master 1.1-2.1%, una corda/EQ/width 1.1%,
  trim <0.1%. That is 11-12% of a core.
- **Memory** is bounded by sfizz's working set. sfizz 1.2.3 keeps a whole sample in memory while a voice plays it
  and frees it about 5 s after its last voice. So peaks follow the playing density: 0.3 GB for the SF2, 0.9-1.9 GB
  for the big SFZ sets, against 2.5-7 GB with full preload. sfizz 1.2.3 cannot stream a sample from disk.
- **grand-c5** is the heaviest sound: 160 voices at 0.75-0.8 core.
  - Alone it had 10 late blocks in one of two minutes.
  - With the other tests running beside it (20 s excerpts, 7 streams at once), 32 of 40 runs fell behind, up to
    5 s.
  - It needs `ahead_s` 1.0 and the box to itself, or fewer voices.
- concert-d fell behind in 1 of 40 runs under the same contention. steinway-b-162 and fazioli-sf2 never did.

**No cut notes with partial preload.**
- sfizz in live mode plays a fresh note from its preloaded start while the background loader reads the rest.
- The test, on 20 s excerpts (Rachmaninoff dense, Chopin sparse) for concert-d, grand-c5 and steinway-b-162, with
  a fresh process per load, paced at 1x:
  - 20 loads per excerpt and sound: all 120 renders bit-identical per excerpt;
  - identical to a 0.25x-paced render (4x the loader's time);
  - for concert-d, identical to the freewheeling offline mode.
- With sfizz's own 8192-frame preload, 20 of 52 loads cut notes: mostly in the first 0.6 s, while the buffer
  filled at full speed, but also at 1.3 s and 15.7 s.
- So streams use 32768 frames (0.68 s). They never render faster than 0.68 / `LOADER_BUDGET_S` (0.3 s) = 2.28x real
  time, so a fresh note always leaves its load at least 0.3 s of wall time (`Engine.max_speed`).
- fazioli-sf2 (FluidSynth, whole SF2 in memory): 40 of 40 identical.

**Against today's offline renders.**
- On the same 60 s, against an offline render made in a fresh process:
  - concert-d -78.7 dB: the pedal-lift noise follows the dry's running RMS, not the whole piece's;
  - fazioli-sf2 -139 dB: rounding.
- Against the cached files (whole pieces):
  - concert-d -42 to -45 dB. A fresh offline render differs from the cached one by -43.5 dB too: sfizz's random
    pedal-noise round robins depend on the process's history.
  - fazioli-sf2 -87 dB: FluidSynth's first render after a load differs slightly from later ones.
- Where the offline master turned a whole piece down, the stream limits only the peaks instead:
  - the Chopin: 1.3-1.9 dB at most, for 5.6-10.6 s of 257 s;
  - the Rachmaninoff up to 4.7 dB, for 34-50 s of 112 s, where the offline turned the whole piece down 4.6 dB.
  - Those windows differ by -18 to -55 dB.
- Synthetic SFZ (tests): engines, una corda, EQ, width and the master (unlimited) are bit-exact; the room is
  -140 dB; the resonance bank is exact without the pedal noise.

**The streaming process** (`stream.prepare_process`).
- Normal scheduling at nice 5, sfizz's loader threads included (checked).
- Single-threaded DSP.
- A hard cap: `RLIMIT_DATA` 3 GB (`DATA_CAP_MB`, `$PIANO_STREAM_DATA_CAP_MB`), 1.6x the largest measured.
  - At the cap, sfizz's loader throws `std::bad_alloc` and the process aborts (exit 134). That kills that stream,
    never the box.
  - Core dumps are off (`RLIMIT_CORE` 0, not dumpable). With them on, WSL wrote a 0.9 GB `.dmp` to
    `%TEMP%\wsl-crashes`.

## The contract

**config/piano/instruments.json** (written by the fetch step).

Top level: `root`, `engines_root`, `default`, `rooms{}`, `instruments{}`.

- `root` and `engines_root` expand `~`. A relative path is taken against the config file's directory.
- An instrument's `file` and a room's `ir` are under `root` unless absolute.
- The `windows-gs` engine stands for the live PC22 GS synth. It is always "available" and never rendered.

Instrument fields read here:

| Field | Effect |
|---|---|
| `engine` | which engine renders it |
| `file` | the SFZ or SF2 |
| `preset` | `{bank, program}`, FluidSynth only |
| `velocity_gamma` | every strike becomes `v' = (v/127)^γ` |
| `gain_db` | gain in master |
| `onset_ms` | copied into `RenderResult.onset_ms` (the samples' own lead-in, for sync) |
| `room` | the default room |
| `eq` | the instrument's EQ (applied with or without realism) |
| `width` | mid/side width of the dry instrument (realism) |
| `resonance_db` | the sympathetic / sustain-pedal resonance bank; null = off (realism) |
| `detune_cents` | extra engine renders at these pitch offsets, mixed in (honky-tonk; realism) |
| `half_pedal` | optional, default false; see Engines |
| `una_corda` | optional, default true; false turns off the soft-pedal shaping (an engine that models CC67) |
| `resonance_pan` | optional, default 0.3; the bank's key-tracked pan width (bass left), negative = audience view |
| `stream_voices` | optional: sfizz's voice cap while streaming (default 256); see The live session |
| `layers` | optional, a blend: `[{file, gain_db, offset_ms}]`, each file rendered by the instrument's engine with the same events and summed (see Engines, Layers). `file` stays the main layer, the one show_service checks; the instrument is available only when every layer file exists |

Rooms: `ir` (a 48 kHz float WAV, 4 channels true stereo L→L, L→R, R→L, R→R; 2 = mono in, stereo out),
`synth` `{rt60_s, predelay_ms, width}` (the fallback when `ir` is null or not installed), `wet_db` (null = no
wet). IRs are normalised to unit energy, so `wet_db` is the wet level relative to the dry for broadband sound.

**Modules**

- `instruments.load(path=None) -> Registry`
  - `get(id)`: the entry with defaults filled in and `file` absolute.
  - `raw(id)`, `ids()`, `available()` (windows-gs always), `why_unavailable(id)`, `default`.
  - `rooms` (with `ir` absolute) and `room(id)`.
- `score.load_midi(path, channels=None) -> Performance`
  - Events `(t_s, kind 'on'|'off'|'cc', num, value)`, sorted, with the file's order kept at equal times.
  - Keys outside 21..108 are dropped (counted in `dropped`).
  - Only CC64, CC66 and CC67 are kept, raw.
  - A note-on with velocity 0, or a note-off with velocity 0, counts as release velocity 64.
  - `Performance.controls(rate_hz, duration_s=None) -> Controls`. Row i is the state after every event at
    t <= i/rate:

    | Array | Shape | Meaning |
    |---|---|---|
    | `key_down` | (n, 88) | key is down |
    | `damper_lift` | (n,) | `clip((cc/127 - 0.25)/0.5, 0, 1)` |
    | `sostenuto` | (n, 88) | latched when CC66 rises through 64 |
    | `una_corda` | (n,) | CC67/127 |
    | `undamped` | (88,) | MIDI ≥ 89 |
    | `struck` | (n, 88) float32 | seconds since the last note-on, inf before the first |

    It also has `free()` and `damping_tau()`, the show_service/midi.py decay rule. Its constants are copied
    into score.py, and a test checks them against show_service.
- `engines.create(inst, engines_root) -> Engine`
  - `render(perf, sr=48000, progress=None, pitch_cents=0.0) -> (N, 2) float32`.
  - Sample 0 is MIDI 0, with no trimming.
  - After the last event the tail lasts until no voice is active, or until the output has stayed below the
    tail floor for 0.5 s, capped at the last event + 30 s. The floor is -70 dBFS after the instrument's
    `gain_db` (-70 - gain_db before it, kept within -120..-60), as the block peak of both channels. When the
    floor or the cap ends it, the last 50 ms are faded out. `stats` has `tail_s`, `tail_end`
    (voices | floor | cap) and `tail_floor_dbfs`.
  - The voice count alone was not enough: sfizz 1.2.3 left voices hanging forever (constant level, -84 to
    -101 dBFS before gain) when a polyphony limit applied to release samples in pedalled passages (K18,
    Maestro CF3; both also fixed at the source, see Instruments), and every such render ran to the 30 s cap.
  - Every render starts clean.
  - `progress(done_s, total_s)` is called at least every 0.25 s.
  - Engines can be registered with `engines.register(name, cls)`.
- `realism.apply(audio, sr, perf, inst, room, progress=None, engine=None, stats=None)` returns (M, 2) float32,
  M ≥ len(audio): sample 0 stays MIDI time 0 and the end grows by the resonance and room tails (trimmed below
  -120 dBFS). Stages, each documented in its `piano_render/dsp/` module:
  1. detune (`tone.py`): the engine renders again at each `detune_cents` (pitch only), all voices at 1/√voices;
  2. una corda (`tone.py`): CC67, smoothed, drives a high shelf (-4 dB above 1.8 kHz) and -2.5 dB at full pedal;
  3. EQ (`filters.py`), 4. width (`tone.py`);
  5. resonance (`resonance.py`): ~1,370 numba resonators at 12 kHz (partials up to 5 kHz, 20 per key, beating
     pairs for k ≤ 4), tuned to the instrument's own partials (`analysis.py`: measured once per instrument from
     a probe render of every key, cached in `~/.cache/screen-machine/piano/analysis`), decaying with the
     damper / half-pedal / sostenuto / undamped-treble state, a key's own resonators gated off while its own
     sound is up, pedal-lift noise into the bank, key-panned, high-passed at 60 Hz;
     `resonance_db` -35 puts the bank about 20 dB under the dry with the pedal down and 45+ dB under it
     0.5 s after the pedal comes up (concert-d, 22 Oct); each dB is a dB;
  6. room (`room.py`): FFT convolution with the room's IR or a synthetic one, wet high-passed at 100 Hz and
     low-passed at 9 kHz, at `wet_db`.
  - `realism.apply_dry(audio, sr, inst)`: what a render with realism off gets, the instrument's EQ only.
  - `realism.VERSION` is part of the cache key (with or without realism). Change it whenever the output changes.
  - numba kernels are compiled once and cached beside `dsp/_kernels.py` (first compile about 5 s).
- `master.process(audio, sr, gain_db)`
  - Measures BS.1770-4 integrated LUFS (K-weighting and both gates), the sample peak, and the true peak (4x
    resample_poly).
  - Applies `gain_db`, then cuts the gain only as far as needed for true peak ≤ -1 dBTP.
  - Plain gain: no compression, no limiter, no delay.
  - `master.write_flac` writes FLAC 24-bit 48 kHz.
- `stream.Streamer(perf_or_midi, instrument_id, *, registry=None, sr=48000, block=512, ahead_s=0.5, room=None, realism=True, gain_db=None, limiter=True, max_speed="auto")`:
  render as it plays, nothing stored (see Latency and compute).
  - `open()` loads (engine with partial preload, the IR, the cached tuning); `blocks(position=None)` yields
    `Block(pos, data)` paced to the sink (`position()` = its playback frame; default the wall clock);
    `next_block()` / `render_all()` unpaced (still within the engine's `max_speed`); `rewind()` back to frame 0
    for another play (engines restart clean with their samples loaded, the chain is built again); `close()`;
    `stats`.
  - `stream.prepare_process(nice=5, data_cap_mb=None)`: the stream process's scheduling and memory cap.
  - Engines stream through `engine.realtime = True`, `start(perf)`, `render_blocks(n)`, `finished`; `render()` is
    built on the same step, so it is unchanged sample for sample. `engines.base.StreamMixer` sums layers and
    detuned copies block by block.
  - `dsp/streaming.py`: the realism stages and the master, block by block with their state carried over.
- `session.py`: show_sender's helper, the live path (its docstring has the protocol; Latency and compute, The live
  session, has what a press does).
- `render.render(midi, instrument_id=None, *, room=None, realism=True, registry=None, progress=None, force=False, cache_dir=None, lock_timeout=None, out=None) -> RenderResult`
  - Offline only (the audition set, comparisons). `out`: the FLAC goes there, no cache is read or written. Else the
    cache named by `cache_dir` or `$PIANO_RENDER_CACHE`; with neither it raises ValueError (nothing is kept unless
    asked).
  - RenderResult fields: path, sample_rate, duration_s, onset_ms, lufs, true_peak_dbtp, cache_key, engine,
    instrument, room, realism, stats, cached.
  - `room=None` means the instrument's own room. `"none"` means no room, unless a room has that id.
  - `progress(state_dict)`.
  - `stats` holds the timings, the master stats, `first_onset_ms` (when the first note becomes audible,
    relative to its MIDI time) and `first_audible_s`.

## Cache, locking and progress (offline renders into a cache you name)

There is no default cache: the live test piano renders as it plays and keeps nothing. These rules hold for a cache
named by `cache_dir` / `$PIANO_RENDER_CACHE` (audition uses `<work>/renders`).

Files: `<cache>/<key>.flac` + `<key>.json` (the RenderResult). The key is a sha256 over:
- the MIDI bytes;
- the instrument id and entry (minus the cosmetic `label`, `order`, `credit` and `_comment`);
- the instrument's files: an SFZ's text and its #includes, an SF2's size and mtime; for a blend, every layer's too
  (a single-file instrument's key is unchanged by this);
- the room entry;
- the realism flag;
- `RENDER_VERSION` and `realism.VERSION`;
- the engine version (for example `sfizz:libsfizz.so.1.2.3`);
- the sample rate.

How a render is written and shared:
- Files are written to temp names and renamed, the `.json` last. A `.json` therefore always means a complete
  render.
- While rendering, `<key>.progress.json` holds `{"stage": load|engine|realism|master|write, "done_s", "total_s", "pid", "updated"}`.
  A heartbeat thread rewrites it every 0.5 s, and it is removed at the end.
- A second render of the same key, from a thread or another process, waits on `<key>.lock` (flock) and then
  returns the first one's result.
- The lock holder removes the lock file, and a waiter re-locks the live one, so no lock files are left
  behind.
- In-process, the most recently used engine stays loaded (`render.release_engines()` frees it). Renders in
  one process run one at a time.
- A cache hit imports no scipy and no numba: `realism.py`, `master.py` and the `dsp` modules import them only when
  they run (scipy.signal alone costs about 1.8 s).

## Engines: behaviour worth knowing (measured 22 Oct)

**Pedals**
- CC64 (damper) and CC66 (sostenuto) reach every engine as on/off: 127 at ≥ 64, 0 below. That is the MIDI
  convention and FluidSynth's own rule.
- Left alone, sfizz 1.2.3 holds notes at any CC64 or CC66 ≥ 1. That would turn the MAESTRO files' resting-foot
  values (11–63) into a pedal held down.
- An instrument with `"half_pedal": true` gets CC64 raw. Use it for SFZs that model half-pedalling themselves.
  Set on `grand-c5` (Accurate-Salamander sfz_live: CC64 sets the release time through its Disklavier-calibrated
  curve, and pedal resonance from CC64 22) and `vintage-grand` (Splendid Grand's own graded release). Measured
  on a note released with the pedal held at a value (dB under the note, 3 s after the key comes up):
  `vintage-grand` CC64 30: cut, 40: gone by 3 s (-59 at 1 s), 50: -94, 60: -73, 127: -40; `grand-c5` CC64 0:
  cut, 30: -34, 40: -30, 60: -27, 127: -26. Without the flag every value under 64 was cut and 64+ held fully.
- CC67 is always passed raw. The continuous values stay in the Performance for the realism stage.

**sfizz** (libsfizz 1.2.3 by ctypes)
- Settings: freewheeling, sample quality 2 in both modes, 256 voices, and 512-frame blocks with
  sample-accurate event offsets. Release velocity is passed.
- **Whole samples are preloaded.** With sfizz's default 8192-frame preload, freewheeling still cut notes off
  after 0.17 s when the background load hadn't been picked up: about 1 fresh load in 20 on a tiny test SFZ.
  - Cost for Salamander V3: load 0.8 s → 17–19 s, and RSS 93 MB → 2.5 GB.
  - `$PIANO_RENDER_SFIZZ_PRELOAD` (frames; 0 = sfizz's default) overrides it.
- `pitch_cents` uses `sfizz_set_tuning_frequency(440·2^(c/1200))`, because sfizz ignores RPN 0. Measured
  +49.93 cents for +50, with timing untouched.
- libsfizz's load chatter goes to stderr, so stdout stays clean for `--json`.

**FluidSynth** (2.3.4 through pyfluidsynth, `fluid_synth_write_float`)
- Settings: reverb and chorus off, 7th-order interpolation, 512 voices, one core, no mlock, gain 1.0.
- Events land on FluidSynth's 64-frame grid, so up to 1.3 ms late and never early.
- No release velocity.
- `pitch_cents` uses RPN 0 plus a pitch bend.
- TimGM6mb's key 69 sample has about 6 ms of silence at its start (key 60 doesn't).

**Layers (blends)** (`engines.base.LayeredEngine`; `engines.create` makes one for an instrument with `layers`)
- Each layer's file is rendered by the instrument's engine with identical events and `pitch_cents`. It is scaled by
  its `gain_db`, moved by `offset_ms` rounded to whole samples (positive = later; a negative value drops the layer's
  first samples), and summed. Sample 0 stays MIDI time 0.
- Each layer keeps the tail rules, with its floor taken after the instrument's `gain_db` plus its own.
- Only one layer is loaded at a time, since a bitKlavier mic set takes about 4.8 GB in sfizz. The layer loaded last
  stays loaded and renders first next time. The sum is always made in the configured order, so it is the same
  whichever ran first.
- `stats.engine.layers` holds each layer's engine stats. The realism stage (detune, partial analysis) renders
  through the blend.
- A blend costs one engine render and one sample load per layer. Chopin excerpt: 55 s wall against 37 s for one mic
  set; the second load falls in the `engine` stage.

**Measured: Chopin Nocturne Op. 9/2 on Salamander V3 SFZ (sfizz)**

| Measure | Value |
|---|---|
| Length | 224.6 s of MIDI, 236.7 s of audio |
| Whole render | 177 s wall, 174 s CPU (1.33x realtime) |
| Engine render | 148 s CPU (1.6x realtime per core) |
| Voices | 256 (the cap) reached |
| Load | 17 s |
| Master (LUFS + true peak) | 7 s at render time; true peak takes about 1.2 s since then (it skips chunks that can't hold the peak) |
| Peak RSS | 3.4 GB |
| Raw level | -33.4 LUFS, -12.9 dBTP |
| With gain_db +16 | cut to +11.9 (-1.0 dBTP), giving -21.5 LUFS |
| First note audible | +12.1 ms after its MIDI time (the samples' own lead-in) |

## Instruments

Installed by `fetch` (sources, sha256s and credits are in `config/piano/instruments.json`). Calibrated 22 Oct
by measurement only; nothing has been judged by ear yet.

| id | Room page label | Library | Room | Resonance | γ | gain_db | onset_ms |
|---|---|---|---|---|---|---|---|
| `concert-d` (default) | Concert grand (Steinway D) | bitKlavier Grand, stage ORTF, 16 layers | recital | -35 | 1.0 | 11.8 | 1.8 |
| `concert-d-hall` | Concert grand (Steinway D, audience seat) | bitKlavier Grand, house ORTF | recital | -35 | 1.0 | 12.5 | 1.8 |
| `concert-d-blend` | Concert grand (Steinway D, stage + hall) | layers: stage ORTF 0 dB + house ORTF -4 dB | recital | -35 | 1.0 | 10.3 | 1.5 |
| `concert-d-tail` | Concert grand (Steinway D, darker, from the tail) | bitKlavier Grand, tail omni (Decca tail) | recital | -35 | 1.0 | 14.3 | 2.4 |
| `steinway-b-162` | Steinway B (Piano in 162) | Ivy Audio Piano in 162, close pair, 5 layers, pedal-down samples | room | off (own samples) | 0.75 | 24.9 | 1.7 |
| `grand-c5` | Grand piano (Yamaha C5) | Accurate-Salamander 6.2a, sfz_live, 16 layers | recital | off (own samples) | 1.0 | 10.3 | 1.5 |
| `bright-cf3` | Bright concert grand (Yamaha CF3) | Maestro Concert Grand, 5 layers | recital | -35 | 1.0 | 12.9 | 5.5 |
| `steinway-b` | Steinway B (close, dry) | VCSL, 3 layers | recital | off | 1.35 | 9.0 | 0.8 |
| `vintage-grand` | Vintage grand (old Akai samples) | Splendid Grand, 4 layers | room | off | 1.0 | 9.0 | 0.5 |
| `fazioli-sf2` | Fazioli (owner's SoundFont) | owner's SF2 (FluidSynth) | room | off | 0.85 | 5.6 | 1.4 |
| `upright` | Upright piano | K18, 6 layers | room | -35 | 1.0 | 14.5 | 7.4 |
| `upright-vcsl` | Upright piano, close (Yamaha) | VCSL, 3 layers | room | -35 | 1.3 | 12.7 | 2.6 |
| `honky-tonk` | Honky-tonk (old player piano) | Old Piano FB, 1 layer | room | off | 1.0 | 10.7 | 1.0 |
| `rhodes` | Electric piano (Rhodes) | jRhodes3d | room | off | 1.0 | 2.5 | 0.1 |
| `harpsichord` | Harpsichord (French) | VCSL (C1-C6 only) | recital | off | 1.0 | 10.0 | 0.6 |
| `gm-basic` | Basic (General MIDI) | FluidR3_GM (FluidSynth) | room | off | 1.0 | 8.1 | 2.2 |
| `windows-gs` | Basic (Windows synth) | the live PC22 GS synth, never rendered | - | - | - | - | - |

How the numbers were set:
- `gain_db`: the Chopin Nocturne opening (excerpt 1 below, realism on, the instrument's own room) measures
  -20.0 LUFS on every instrument. Loud passages are not compressed: where a render would peak above -1 dBTP,
  master turns the whole render down (26 of the 70 audition files, all in excerpts 4 and 5, by 0.3-5.4 dB).
- `velocity_gamma`: changed only where the soft-to-loud spread was out of line. The spread is the LUFS of
  excerpt 5 (Rachmaninoff, loud) minus excerpt 1 (Chopin, soft), before the ceiling. The 16-layer grands
  measure 6.2-7.2 dB.
  - `fazioli-sf2` measured 8.6 dB, and a single note rose 27.5 dB from velocity 40 to 115 (the others
    13-22 dB). With γ 0.85 it measures 7.6 dB and 17.9 dB.
  - `steinway-b` measured 5.1 dB; with γ 1.35 it measures 5.8 dB.
  - `upright-vcsl` measured 5.5 dB; with γ 1.3 it measures 5.9 dB.
  - The two VCSL libraries respond weakly to γ (3 layers). Pushing γ further would mostly move notes into
    the soft layer.
- `onset_ms`: the configured definition (first sample above -50 dBFS after the note-on, before gain), as the
  median over 21 isolated notes (7 keys from C1 to C7, velocities 40/80/115). The FluidSynth entries add
  0.7 ms, the mean lateness of its 64-frame event grid. The time to reach -20 dB under each note's own peak
  (the audible attack, independent of gain) is within 1.5 ms of these, except `bright-cf3` (5.5 ms, the
  same), `honky-tonk` (2.2), `vintage-grand` (1.7) and `fazioli-sf2` (4.2 with γ 0.85).

Fixed 22 Oct, in the fetch wrap (`"replace"` in the instrument's `wrap` block, so a fresh fetch makes the same
wrapper; the installed copies were rewritten in place by `fetch`):
1. `upright` (K18):
   - Its two pedal-noise groups set `lokey=-1` but not `hikey=-1`, so sfizz also played `pedal.on.wav`
     (17.5 s, about 45 dB under the note) on every key press. They now get `hikey=-1`.
   - Its 30 key-release groups had `polyphony=1`; with it, sfizz 1.2.3 left release voices hanging at about
     -100 dBFS after pedalled passages. That limit is removed.
   - Engine only, before → after: two notes 8 → 4 peak voices, tail 14.7 → 0.6 s; Chopin excerpt 256 → 50
     voices, tail 30 → 0.6 s, render 37 → 2.7 s; Bach 256 → 19 voices, tail 30 → 0.6 s.
2. `bright-cf3` (Maestro CF3):
   - `note_polyphony=1` sat in its `<global>`, so it also applied to the release samples; with it, sfizz 1.2.3
     left about 100 release voices hanging at -94 dBFS after pedalled passages, and renders ran to the 30 s
     tail cap. It now sits on the main notes' `<master>` only (a re-struck key still chokes its old note).
   - Engine only, Chopin excerpt: 117 → 41 peak voices, tail 30 → 1.0 s, render 15 → 3.7 s. LUFS unchanged.
3. `grand-c5` and `vintage-grand` now set `"half_pedal": true` (see Engines). `grand-c5`'s Chopin rose 0.5 dB
   with it, so its `gain_db` went from 10.8 to 10.3.

**The bitKlavier Grand's other mic sets (`concert-d-hall`, `-blend`, `-tail`, added 22 Oct).**
- What they are: the same Steinway D recordings as `concert-d`, made at the same time through other mic pairs, from
  archive.org (48 kHz/24-bit, 2.7-2.8 GB each). Every set has the same 664 files. The map is the same generator
  (`post: bitklavier-sfz`).
  - House ORTF: Schoeps cardioids 9 ft up at the stage edge, the audience's sound.
  - Tail Omni: Schoeps omnis about 8 ft behind the tail.
  - Stage Lid Omni (`bitklavier-stage-omni`) is installed too. The makers suggest blending it with the stage
    ORTF; it is not offered yet.
  - Not fetched: House Wide Cardioid, Lip Cardioid, Harp Omni, Piano Bar, Binaural (KU100 at the player; meant for
    headphones). Sizes are 2.7-2.9 GB at 48k/24, listed by `archive.org/advancedsearch.php?q=bitklavier`.
- Time alignment: every file in every set was trimmed to its own onset. There is no acoustic delay between the
  sets: attack times (-20 dB under the peak) differ from the stage set's by a median of +0.06 ms (house), -0.10
  ms (tail) and +0.06 ms (stage omni), and by under 1 ms on 92-97% of the 480 notes. That spread is per file, so
  no `offset_ms` can improve it, and the blend uses 0. Summed, the blend's third-octave bands are -1.2 to +1.6 dB
  off the power sum of its layers: partly coherent, with no cancellation.
- Noise levels differ per set. The block's `levels` holds each set's own, from its raw files against its own C4
  note, the same way as the stage set's:
  - pedal takes: each take at the stage set's level against the C4 velocity-64 note;
  - key-release noise: the median over the 88 keys;
  - release resonance: against the notes' sustain (1.5-2.5 s), not their attack.
  - Through sfizz (the noise probe), every pedal take of house and tail measures -38.0 dB (±0.1) under the C4 mf
    note. Key-release noise at velocity 64, as the mean over 6 keys, is -35.6 (house), -36.4 (tail) and -36.7
    (stage omni) against -36.6 for the stage set. Before, the tail set's pedal-down takes were 4-5 dB louder and
    its release noise 3.4 dB louder; the house set's resonance was 4.2 dB louder.
- Room: `recital`, like `concert-d`, so an A/B differs only in the mics.
  - The house set holds little more hall than the stage set: +0.3 dB (30-300 ms) and +1.0 dB (0.3-1 s) more late
    energy against each note's first 30 ms.
  - Any recorded hall stops at a key release (`ampeg_release` 0.5 s). After a staccato note, the house set with
    no room is -34.6 dB at 0.25-0.75 s, the same as the stage set with no room (-35.7). With `room` it is -32.0,
    and `concert-d` (recital) is -19.7.
  - So `dry` or `room` would leave the audience seat drier than the default.
- What changes, on the Chopin excerpt:

  | | `concert-d` | `-hall` | `-blend` | `-tail` |
  |---|---|---|---|---|
  | Bass (< 120 Hz), energy share | -19.1 dB | -24.8 dB | -20.2 dB | -25.9 dB |
  | 2-6 kHz, energy share | -23.9 dB | -27.6 dB | -25.9 dB | -29.3 dB |
  | L/R correlation | +0.45 | +0.85 | +0.65 | +0.45 |

  The house pair is narrower and duller, and the tail pair is the darkest.
- Excerpts 1-5, LUFS: hall -20.0, -22.8, -20.9, -16.6, -15.1; blend -20.0, -23.0, -20.9, -16.8, -16.7; tail
  -20.0, -23.2, -21.9, -15.6, -16.8.
  - All are within 1.5 dB of the medians except hall's Rachmaninoff (+2.5 dB). Its soft-to-loud spread before the
    ceiling is 7.0 dB, within the grands' range. The ceiling cut its Rachmaninoff only 2.1 dB (concert-d 4.2)
    because its true peaks are lower: the distant pair's transients are softer.
- Cost: a 40 s excerpt took 11-18 s (one set) and 36-55 s (blend). Each set's sample load is about 20 s.

**`steinway-b-162` (Ivy Audio Piano in 162, added 22 Oct).**
- Where it comes from: BitTorrent only. ivyaudio.com/freebies says "Direct downloads will be back soon!" and
  gives one magnet per edition. The SFZ edition is btih `4c1609b3…d9c9`; the page's 2022 and 2025 Wayback
  copies give the same magnet. No official mirror was found (archive.org, GitHub, sfzinstruments).
  - The fetch block's `url` is that magnet. `fetch` never downloads a magnet: the zip must already be at
    `root/_downloads/57ebdd690cf6-Ivy_Audio_-_Piano_in_162_sfz.zip` (4,919,223,206 bytes, sha256 in the
    block), else the entry fails at once and says so.
  - 22 Oct: fetched with aria2c 1.37 (Ubuntu's deb, unpacked into `engines_root/tools/aria2`), in 2 min from 7
    seeders. The magnet's trackers are dead and aria2's DHT found nobody, so the seeders came from a DHT crawl
    and were probed by hand. All 587 pieces were checked, nothing was uploaded, and it did not seed.
- The library is two SFZs (close pair, ambient pair) over 1,760 FLACs each (24-bit/44.1 kHz): every key, 5
  layers, 2 round robins, with pedal-up and pedal-down samples, chosen at the note-on by `hicc64=63` /
  `locc64=64`. There are no pedal-noise or key-release samples. Only the close pair is used, with the `room` IR.
- The wrap (`IvyAudio-PianoIn162-Close.screen-machine.sfz`) changes three things:
  1. One take per note: round robin 2 (`2Close.flac`) is mapped to round robin 1. sfizz keeps whole samples
     in memory as float32, so all 1,760 would be 10.0 GB per render process, with up to 3 at once. Now 880
     samples, 5.2 GB.
  2. `sustain_lo=64`, as for the other SFZs.
  3. `offset=` on 136 samples whose attack came late (before: up to 101 ms of silence first, and 62 samples
     over 10 ms). Each now starts at most 1 ms before its first sound, with the main attack (-20 dB re
     peak) at most 10 ms in. About 15 ms of key noise before some forte strikes is kept, but cut to 10 ms.
- Measured 22 Oct (engine and realism, own room):
  - Pedal: CC64 picks the pedal-down sample (correlation with the file 1.00). Held 3-5 s, pedal-down notes
    are 8-13 dB louder than pedal-up, with 3-9 dB more energy between the note's partials. That is real
    resonance, so `resonance_db` is null.
  - Noise probe (`pedalprobe/probe.py`): pedal noise none (no samples); after a velocity-20 C4, the note's own
    release tail is -35.9 dB under the velocity-64 C4 peak.
  - The samples are about 21 dB quieter than the other grands' (C4 at velocity 64 peaks at -46.8 dBFS before
    gain; the others at -25.9), hence `gain_db` 24.9. In the quietest 10% of each excerpt, the hiss in the
    8-16 kHz band is -98 to -104 dBFS at -20 LUFS (`concert-d`: -93 to -95).
  - γ 0.75 brings the spread from 8.4 dB (γ 1) to 6.8 dB.
  - Excerpts 1-5: -20.0, -24.0, -21.9, -17.0 (limited 2.4 dB) and -17.7 LUFS (limited 4.6 dB). All are within
    1.2 dB of the medians.
  - `onset_ms` 1.7 is the configured -50 dBFS threshold, taken at the others' level (-64.9 dBFS before this
    gain). Taken literally (-50 dBFS before gain) it is 9.0 ms. To -20 dB under the peak it is 5.1 ms, up to
    14 ms on fortes.
  - Cost: the samples take about 60 s to load (FLAC decoding) for each render process. After that a 40 s
    excerpt renders in 6-20 s, with 21-45 peak voices and tails of 0.9-1.0 s.

Known defects, measured 22 Oct:
1. `bright-cf3` measures 7-19 dB less bass than the other grands below 250 Hz on the Chopin.
2. Calibrating on a pedalled piece leaves three instruments quieter on the unpedalled Bach than the median:
   `grand-c5` by 2.2 dB (1.7 before half pedalling), `bright-cf3` by 2.1 dB and `steinway-b` by 2.4 dB.
3. Stereo: `upright`, `grand-c5`, `vintage-grand` and `upright-vcsl` are wide spaced pairs, with L/R
   correlation about 0 over a whole piece. None has anti-phase bass: summed to mono, below 200 Hz they lose
   up to 3 dB (worst case `upright`, -3.1 dB). `fazioli-sf2` is nearly mono (S/M -14 dB).

## Audition

`C:\Users\Greg\Music\Piano audition\` on PC22, a local folder that is not redirected to OneDrive. It holds 70
FLACs (16-bit, 48 kHz, TPDF-dithered, tagged with album "Piano audition (22 Oct 2026)") and `Read me.txt`, which
lists the instruments and explains the A/B files. Nothing was played. The `upright`, `bright-cf3`, `grand-c5`
and `vintage-grand` files were made again later on 22 Oct, after the fixes above (`Read me.txt` says so).

**Excerpts.** These were cut from `config/live/test-piano/`.
- Excerpts 1-3 start at the piece's own time 0.
- Later starts snap to the note with the largest gap before it, within ±3 s. They begin 0.3 s early, with
  CC64/66/67 set to their values at that moment, and no notes from before the start.
- Ends snap to the largest gap within ±4 s. Keys still down are released, and all pedals are lifted, 2 s
  after the cut.

| # | Piece | Source span (s) |
|---|---|---|
| 1 | Chopin Nocturne Op. 9/2 (cantabile, pedal) | 0-41.8 |
| 2 | Debussy Reflets dans l'eau (half pedal, una corda) | 0-41.1 |
| 3 | Bach Prelude in F minor, WTC I (no pedal) | 0-42.6 |
| 4 | Bill Evans, Here's That Rainy Day (PiJAMA) | 166.1-206.1 |
| 5 | Rachmaninoff Prelude Op. 32/8 (fast, loud) | 28.6-68.6 |

**Renders.**
- Every instrument except `windows-gs` plays each excerpt with realism on, in its own room.
- The default (`concert-d`) also has two A/B files:
  - `- dry samples`: realism off;
  - `- no room`: realism on with `room="none"` (resonance and una corda, no room).
- Every file uses the instrument's `gain_db`, with no extra level matching.
- Each file is trimmed 0.5 s after the last 50 ms window above -70 dBFS, with a 0.5 s fade (the room's
  near-silent tail; before the fixes above, also the upright's and CF3's 30 s hanging voices).

**Results.** Median LUFS per excerpt across the instruments:

| Excerpt | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|
| Median LUFS | -20.0 | -22.8 | -21.1 | -17.0 | -17.6 |

- Every file is within ±1.5 dB of its excerpt's median, with six exceptions:
  - Bach: `grand-c5`, `bright-cf3` and `steinway-b` (defect 2);
  - Rachmaninoff: `vintage-grand` (+1.5 dB; +1.48 before half pedalling);
  - Rachmaninoff: `steinway-b` (-1.8 dB, held down by the true-peak ceiling) and `harpsichord` (-2.6 dB,
    which has no dynamics).
- True peak is at most -1.01 dBTP, with no clipped samples. There are no dropouts: the quietest 100 ms
  block inside the music is -60.3 dBFS (Debussy, `vintage-grand`).
- Rendering all 70 files took 690 s of wall time for 3,017 s of music (4.4x realtime, at idle priority),
  plus one engine load per instrument: concert-d 13 s warm (51 s cold), grand-c5 27 s, the rest under 11 s.

**To make it again.** `piano_render/audition.py` (the 22 Oct scripts, ported; its excerpt MIDIs are
byte-identical to the 22 Oct ones):

```bash
OMP_NUM_THREADS=1 NUMBA_NUM_THREADS=1 chrt -i 0 flock -w 3600 ~/.cache/screen-machine/cpu.lock nice -n 19 \
  ~/.venvs/sm-piano/bin/python -m piano_render audition [--instruments ID ...] [--excerpts N ...] [--no-ab]
```

- It cuts the excerpts into `~/.cache/screen-machine/piano/audition/excerpts/` (`--work DIR` moves that
  folder), renders them with `render.render()` into its own cache, `<work>/renders`, and writes the
  16-bit FLACs to `--out` (default PC22's folder above), replacing files of the same name (temp name, then
  rename).
- Each file's measurements (LUFS, true and sample peak, clipped samples, the quietest 100 ms, render
  timings, the engine's tail and peak voices) are merged by file name into `<work>/table.json`.
- The dither is seeded by the file name, so a file comes out the same every time.
- The four instruments' 20 files took 4 min 25 s (two engine loads cold), and the five `grand-c5` files
  1 min 50 s again after its gain change.

## Adding an engine

1. Subclass `engines.base.Engine` in `engines/<name>.py`. Implement `available`, `version`, `_load`,
   `_reset`, `_set_pitch`, `_render_block` and `_active_voices`. Or override `render()` for an engine that
   renders whole files, such as Pianoteq's CLI.
2. Add it to `_BUILTIN` in `engines/__init__.py`.
3. Set `"engine": "<name>"` in instruments.json.
