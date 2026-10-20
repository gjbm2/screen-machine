# Audio calibration: what the room + PA + mic do to music

An occasional measurement per room and mic, like the light-latency calibration in `tools/room/latency_cal/`. It answers two questions:

1. What does this room's chain (PA, room acoustics, the mic and its built-in processing) do to music before show_service hears it?
2. Does our beat stack (show_service's DSP and beat clock) still find the beats in what's left?

It also builds a **room model** (`roomsim.py`) that replays the measured degradation on any audio, so beat-annotated datasets can be tested "through the room" (`tools/room/beat_eval/`) without playing anything.

Run it again when the room, the mic, its position, or the PA changes. First runs:

- this room, PC22's Dell speakerphone (stream `pc22-mic`);
- the other room, the new boundary mic on the kiosk (stream `room-mic`).

## The flow

```
 owner plays a track over the PA ──▶ mic ──▶ calibrate.py records rec.wav (+ show_service's bus and state)
 owner names the track          ──▶ calibrate.py fetches the original (yt-dlp) ──▶ original/original-48k.wav
 analyse.py compares the two    ──▶ analysis/report.md (+ report.json, figs/)  and  model/model.json
```

### 1. Record

```bash
P=~/.venvs/sm-roundtrip/bin/python; C=tools/room/audio_cal/calibrate.py
$P $C record start --note "where the PA and the mic are"   # then the owner plays the track over the PA
$P $C record status                                       # seconds so far, the last second's level, bus lines
$P $C record stop
```

- `record start` records the mic that show_service's stream is using right now (its `sender.dev`; `--device` to
  pick another). On PC22 that is `pc22_record.py`, run on Windows Python (`py -3.12`) through powershell.exe. It
  opens the mic as `tools/show_sender/pc22_agent.py` does (WASAPI shared mode, 48 kHz, mono, float32), so it hears
  what the beat clock hears, and the agent carries on alongside it. It stops itself after `--max-s` (an hour).
- It also logs show_service's live beat clock (`bus.jsonl`, `state.jsonl`), so the analysis can score what the
  clock actually did. Ask the session that owns show_service to hold restarts while the track plays.
- Start recording before the music, and let the room be as it normally is: talk and other sound are part of the
  measurement (the model's room sound comes from the parts without music).
- **Any other stream, e.g. `room-mic` (the kiosk's boundary mic):** `record start --stream room-mic` records it
  *through show_service* (`--via show`, the default for every stream but pc22-mic). The kiosk's sender holds its
  mic exclusively (`arecord` on `plughw`), so nothing else can open it there; instead `_showtap` reads show_service's
  levels feed (`WS /v1/levels?wave=2048`: the active stream's newest 2048 INT16 samples, up to 60 frames a second)
  and stitches the overlapping frames on their samples (exact match, the packet counter as a guide), so rec.wav is
  what the receiver decoded, gaps and all. The stream must be show_service's active stream. `tapmap.jsonl` maps
  recording samples to show_service's clock (the bus log's). Measured 20 Oct: 41 s, 0 gaps, within one packet of
  the counter; about 1 % of a core.

### 2. Fetch the original

```bash
$P $C fetch "https://www.youtube.com/watch?v=..."         # or search words: the first YouTube hit
```

It saves the source and a 48 kHz copy in `original/` and records it in `run.json`. The fetched version may differ
from the one played (another master, edit or speed: the first run's copy played 0.63 % faster than YouTube's). The
analysis finds the track in the recording, fits the speed, and keeps a source difference out of the room's numbers.
Then `$P $C analyse` (below; it takes the CPU lock itself).

### 3. Analyse

```bash
flock ~/.cache/screen-machine/cpu.lock nice -n 19 ~/.venvs/sm-roundtrip/bin/python \
    tools/room/audio_cal/analyse.py tools/room/audio_cal/runs/<run> [--no-config] [--skip-beats] [--no-stack] \
    [--stack-gates -30,-47] [--seeds 0,1,2]
```

- It takes 10-15 minutes for a minute of music (more while other jobs hold the CPU lock). The model fit replays the original through candidate models
  (4-6 minutes); then the beat trackers (madmom offline and online, Beat This!, dsp's hit detector), each in its own
  env; then our beat stack (`tools/room/beat_eval/run_stack.py`) on the real recording and on the model's replays, at
  dsp's default gate (-30 LUFS) and at the music's measured loudness (or `--stack-gates`).
- `--skip-beats` leaves out the trackers, the live-clock score and the stack: no ground truth, about 5 minutes.
- `--no-stack` leaves out only the stack replays.
- `--no-config` leaves `config/room/audio_cal.json` alone. Synthetic runs never write it.
- From Python (calibrate.py): `analyse(run_dir, write_config=True, cpu_lock=True)`. It takes the CPU lock in-process. Never combine `cpu_lock=True` (or `--lock`) with an outer `flock` on the same file, because it would wait for itself.

## A run directory

`tools/room/audio_cal/runs/<YYYYmmddTHHMMSSZ>-<stream>/` (never in git):

| file | written by | what |
|---|---|---|
| `rec.wav`, `rec.json` | recorder | 48 kHz mono float32: what the mic heard, starting with room tone before the music |
| `bus.jsonl`, `state.jsonl` | calibrate.py | show_service's `music.*` messages and 1 Hz state, each with WSL monotonic `rx_ms` |
| `run.json` | calibrate.py | run, stream, device, times, `original` once fetched |
| `original/` | calibrate.py | the fetched file and `original-48k.wav` |
| `analysis/report.md` | analyse.py | the plain-English summary |
| `analysis/report.json` | analyse.py | every number |
| `analysis/figs/*.png` | analyse.py | frequency response + coherence, IR + decay, noise spectrum, gain over time, level in/out, onset envelopes, alignment, live beats vs ground truth |
| `analysis/beats.json` | analyse.py | pseudo ground truth: Beat This! beats + downbeats and madmom beats, in original and recording time |
| `model/model.json`, `ir.wav`, `band_limit.wav`, `noise.wav` | analyse.py | the room model |
| `reference/` | screen-machine-26, by hand | the first run's reference beats and stack results (README.txt there) |

For a real run, analyse.py also updates `config/room/audio_cal.json`. That file holds `streams.<stream>`: the run, when it was measured, the device, a summary, and the model's path. `summary.music_lufs` is the music's loudness at the mic as dsp's gate reads it (the median 3 s loudness at party volume): the gate's reference (a vibe's `loudness_ref_lufs`) belongs near it.

## What the analysis does

The module docstring in `analyse.py` has the details. In short:

- **Alignment.** When calibrate.py has already found and speed-matched the clean section (run.json `music`: `rec-music.wav` and `original/original-matched.wav`), that is used, and the acoustic lag is measured on it. Otherwise the original is found window by window: 6 s windows of 24 log band energies (80 Hz-8 kHz, 200 fps) against the whole original, a consensus line that allows the played copy to run up to 2 % faster or slower (another master or transfer: on the first run it was 0.63 %), then GCC-PHAT on the waveform where the mic leaves enough of it. Windows where the track played off the line (the owner seeking) are found too, and kept out of the room sound.
- **Room sound.** Everything the mic delivered outside the music and the seeks, events included (typing, talk): it becomes the model's `noise.wav`. The report gives its level and spectrum, mains hum, the quiet floor, and the mic's gate (how often it shuts to digital silence).
- **Levels and events.** The music's loudness at the mic (the 3 s loudness dsp's gate reads; `music_lufs`), the chain's gain, music over room sound. 1 s loudness blocks against the original find other sound over the music and dropouts (the mic shutting the music out); both stay out of every fit.
- **Response.** H1 with coherence, and the power response (the room sound's spectrum taken off), which the model uses when the coherence is low. The band limits (the steep cliffs at both ends: a speakerphone's high-pass and the cut at its internal Nyquist) are split from the passband between them.
- **IR and dynamics.** The impulse response and decay times, the level curve and the level-dependent octave gate are estimated as before. When the coherence is low they are reported with a caveat (they are the estimator's numbers, not the room's) and kept out of the model.
- **Processing.** What a linear chain can't explain is fitted by replaying the original through candidate models and matching the real recording's coherence (per octave) and onset survival (dsp's bands; onsets count double): a diffuse room tail (RT60 and DRR from a grid, the room's echo when no impulse response can be read) and a random spectral gain per frequency region (roomsim's `scramble`, standing in for the mic's noise suppression). The gate comes from the room sound; the dropout rate from the events.
- **Beats.** Onset-envelope survival (dsp.py's SuperFlux, overall and for sub, bass, mid and high); madmom offline, madmom online with 2 nets (the live clock's) and Beat This! on the recording and the replays, against the original. The ground truth is madmom's offline beats of the whole original, as heard at the mic.
- **Live clock.** This section needs `bus.jsonl` with `music.clock`. Host time is mapped to recording time by pairing dsp's `music.hit` on the bus with dsp's own hits on the recording (`beats_offline.py dsphits`: the same detector on the same audio, sub-millisecond while the stream is intact), else by the hit train against the onset envelope. The live beats are scored against the ground truth (F, CMLt, AMLt, within 25 ms, coverage, precision while gated, first lock, bpm), next to the live stream's packet loss during the music (`state.jsonl`).
- **Model fit.** The original is replayed through the finished model with several seeds and set next to the real recording: loudness, band levels and edges, coherence, onsets, madmom online, and our beat stack at both gates.

Caveats:

- There is one source position and one mic.
- The excitation is music, not a sweep, so the response is only known where the track has energy.
- The PA's own response is part of "the room".
- A different master of the original shows up as EQ.
- Through a speakerphone, the room's echo and the mic's processing are fitted stand-ins (they make the replay lose what the real recording loses), not measurements.

## The room model

```python
import roomsim   # tools/room/audio_cal/roomsim.py; numpy, scipy, soundfile, soxr only
y, fs = roomsim.apply(x, fs_in, "tools/room/audio_cal/runs/<run>/model/model.json",
                      seed=0, snr_db=None, dynamics=True)
```

```bash
~/.venvs/sm-roundtrip/bin/python tools/room/audio_cal/roomsim.py in.wav out.wav --model <model.json> [--snr-db 10] [--seed 3] [--no-dynamics]
```

The chain (model version 2; roomsim.py's docstring has every step):

1. Mono, 48 kHz.
2. Loudness-match to the calibration track as played, so the measured gain applies (the PA at the calibration's volume).
3. The room: the measured IR, or a minimum-phase FIR of the passband response (with the fitted diffuse tail, when one was fitted).
4. The fitted level curve and octave gate (a plain mic only).
5. The room sound (`noise.wav`), looped from a seeded offset, at its measured level or `snr_db`.
6. The mic's processing: the fitted spectral scramble, the gate, the dropouts (all seeded).
7. The mic's band limits (`band_limit.wav`).
8. INT16 steps.

`dynamics=False` (`--no-dynamics`) keeps the linear chain and the room sound only. The output keeps the input's timeline: nothing delays it beyond the filters' own (minimum-phase) delay, so beat annotations stay valid as they are; the measured acoustic lag is in `system_delay`.

### The first run (pc22-mic, 18 Oct)

`runs/20261018T145602Z-pc22-mic/model/model.json`. analysis/report.md has the side-by-side of the real recording and the model's replays: loudness, band edges, coherence, onset survival, madmom, and our beat stack at both gates.

## Testing without a real run

`synth_run.py` builds a fake run from a test-music track with known degradation. The planted degradation:

- the start trimmed by 2 s, and a 90 ppm drift;
- a 120 Hz high-pass and a +6 dB lift at 2 kHz;
- an RT60 0.5 s room;
- a 3:1 compressor;
- room tone at -55 dBFS with hum, and 10 s of "talk".

It writes `truth.json` next to the run. `--bus` also fabricates a `bus.jsonl` (a beat clock, and dsp's own hits on the recording), so the live-clock scoring can be tested.

```bash
flock ~/.cache/screen-machine/cpu.lock nice -n 19 ~/.venvs/sm-roundtrip/bin/python tools/room/audio_cal/synth_run.py --bus
flock ~/.cache/screen-machine/cpu.lock nice -n 19 ~/.venvs/sm-roundtrip/bin/python tools/room/audio_cal/analyse.py <printed dir> --no-config
```

Synthetic runs live in `~/.cache/screen-machine/audio_cal_synth/`.

## Sonos programme runs (room-mic)

For the kiosk's boundary mic the music is driven: `calibrate.py sonos-run --programme <dir>` (screen-machine-26) plays a
calibration programme (`programme.py build pass1|pass2`: silence, a 4-rung volume ladder at Sonos 20/30/40/50, songs
at volume 40, a rubato piano) through the Drawing Room Sonos, records room-mic through show_service and logs the live
clock. `programme_analyse.py` reads that run against the programme:

```bash
flock ~/.cache/screen-machine/cpu.lock nice -n 19 ~/.venvs/sm-roundtrip/bin/python tools/room/audio_cal/programme_analyse.py \
    <run dir> --programme ~/.cache/screen-machine/audio-cal/programmes/pass1-YYYYMMDD --gate-test-only [--fast]
# ... 26 sets the printed music_lufs_test as room-mic's music_lufs for pass 2, then:
flock ... programme_analyse.py <pass-2 run dir> --programme .../pass2-YYYYMMDD --write-config
```

- **Alignment:** analyse.py's `align` (offset and drift), the Sonos' position ticks as the cross-check.
- **Levels:** show_service's stack replayed on the recording (`run_stack.py`): dsp's lufs_s per segment (the live bus's
  and BS.1770 beside it); room tone from the silences.
- **Ladder:** lufs_s against volume, a line in volume and in log-volume, the better one predicting party volume (70)
  with its uncertainty; the rungs' gain (recording over programme) as a cross-check that the room's own sound can't
  bias. `music_lufs` (party) = the songs' level at 40 + the ladder's rise to 70. dsp's floor clamp (its 100 ms floor
  + 21 LU caps the gate's reference) is worked out from the recording too.
- **Live clock per song:** the bus's music.clock flashes, host time to recording by `tapmap.jsonl`'s lower envelope,
  scored with `beat_eval/score.py` (F, CMLt, AMLt, hit25, cover, precision, wrong lock, lock and gate times, bpm),
  the offline replay beside it (parity); pass 2 against pass 1 on the songs both played (before and after the gate).
- Writes `analysis/programme_report.md` (for the owner), `programme_report.json`, `gate.json`, `clock_u.json`,
  `figs/levels.png`. `--write-config` merges `streams.room-mic` into `config/room/audio_cal.json` (or
  `--audio-cal-file`), taking pass 1's ladder when pass 2 has none; never from a synthetic run into the real file.
  `--fast` skips the replay (levels from the live bus).

`programme_synth.py` tests it without playing anything: `programme` builds a small programme, `run --programme <dir>`
a fake run with known truth (offset, drift, a planted ladder, a host clock with skew and jitter, a bus made from
run_stack's replay), `check <run dir>` compares the report with `truth.json`. In
`~/.cache/screen-machine/audio_cal_synth/`.

## Files

- `analyse.py`: the analysis.
- `roomsim.py`: the room model.
- `beats_offline.py`: the offline beat trackers (madmom offline and online, Beat This!) and dsp's own hit detector, run as subprocesses in show_service's env and the ML worker's env.
- `synth_run.py`: the synthetic run maker.
- `calibrate.py`, `pc22_record.py` (screen-machine-26): record and fetch.
- `programme.py`: the Sonos calibration programmes.
- `programme_analyse.py`: the analysis of a Sonos programme run (levels, ladder, gate values, live clock).
- `programme_synth.py`: synthetic programmes and runs with known truth, to test it.
