# piano_render

MIDI file + instrument id → a rendered, mastered audio file, offline and cached. It replaces the test piano's
Windows GS Wavetable synth with sampled pianos: sfizz plays SFZ libraries and FluidSynth plays SF2s. The first
output is PC22's speakers (`pc22.py`); Sonos comes later. File time 0 is MIDI time 0, so the MIDI sent to the
screens can be started against the same clock. Engines are pluggable, so a paid engine (Pianoteq, a VST3 host)
is one more module.

## Run

```bash
python3.12 -m venv ~/.venvs/sm-piano && ~/.venvs/sm-piano/bin/pip install -r piano_render/requirements.txt
PY=~/.venvs/sm-piano/bin/python                       # run from the repo root

$PY -m piano_render list                              # instruments (ok, or why not) and rooms
$PY -m piano_render fetch                             # install instruments, IRs and sfizz (fetch.py)
$PY -m piano_render render "config/live/test-piano/Classical - Chopin - Nocturne in E-flat, Op. 9 No. 2.mid" \
    --instrument grand-c5 [--room recital|none] [--no-realism] [--force] [--json]
$PY -m piano_render prerender --instrument grand-c5   # every .mid in config/live/test-piano
$PY -m piano_render play-pc22 FILE_OR_MID [--instrument ID]   # pc22.py: plays out loud on PC22
$PY -m piano_render fetch --status                    # what is installed (no network)
$PY -m piano_render audition [--out DIR] [--instruments ID ...] [--excerpts N ...]   # see Audition
```

- `--config PATH` (or `$PIANO_RENDER_CONFIG`) picks the registry. The default is
  `config/piano/instruments.json`.
- `--cache DIR` (or `$PIANO_RENDER_CACHE`) picks the render cache. The default is
  `~/.cache/screen-machine/piano/renders`.
- `render`, `prerender` and `audition` drop themselves to idle CPU priority (SCHED_IDLE, nice 19) unless you
  pass `--no-idle`.
- On this box, still wrap long jobs as the CLAUDE.md rule says:
  `chrt -i 0 flock -w 1800 ~/.cache/screen-machine/cpu.lock nice -n 19 ...`
- The package sets `OMP/OPENBLAS/MKL/NUMBA_NUM_THREADS=1` unless they are already set.
- `fetch`, `play-pc22` and `audition` are imported only when used. If one module is missing, the other
  commands still work.

From Python:

```python
from piano_render import render
res = render.render(midi_path, "grand-c5")      # RenderResult; res.path = FLAC 24-bit 48 kHz
res.cached, res.lufs, res.true_peak_dbtp, res.onset_ms, res.stats
render.cached(midi_path, "grand-c5")            # lookup only, never renders
render.read_progress(res.cache_key)             # {"stage", "done_s", "total_s", "pid", "updated"} or None
```

## Tests

```bash
OMP_NUM_THREADS=1 NUMBA_NUM_THREADS=1 chrt -i 0 flock -w 1800 ~/.cache/screen-machine/cpu.lock nice -n 19 \
  ~/.venvs/sm-piano/bin/python -m pytest -q piano_render/tests --ignore=piano_render/tests/test_pc22.py
```

The tests take about a minute with no network and no sound.

- sfizz tests build a one-sample SFZ in tmp. They need libsfizz under
  `~/.local/share/screen-machine/piano/engines/sfizz` (or `$PIANO_RENDER_SFIZZ_LIB`) and are skipped without it.
- FluidSynth tests use `/usr/share/sounds/sf2/TimGM6mb.sf2`.
- `test_pc22.py` and `test_fetch.py` belong to those modules. `test_pc22.py` may use PC22's audio device, so
  run it only when sound is allowed.

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
- `render.render(midi, instrument_id=None, *, room=None, realism=True, registry=None, progress=None, force=False, cache_dir=None, lock_timeout=None) -> RenderResult`
  - RenderResult fields: path, sample_rate, duration_s, onset_ms, lufs, true_peak_dbtp, cache_key, engine,
    instrument, room, realism, stats, cached.
  - `room=None` means the instrument's own room. `"none"` means no room, unless a room has that id.
  - `progress(state_dict)`.
  - `stats` holds the timings, the master stats, `first_onset_ms` (when the first note becomes audible,
    relative to its MIDI time) and `first_audible_s`.

## Cache, locking and progress

Files: `<cache>/<key>.flac` + `<key>.json` (the RenderResult). The key is a sha256 over:
- the MIDI bytes;
- the instrument id and entry (minus the cosmetic `label`, `order`, `credit` and `_comment`);
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
  they run (scipy.signal alone costs about 1.8 s). `piano_render.session`'s render child answers a cached piece
  in 0.21-0.25 s (1.55-1.84 s before, 22 Oct), and a whole session start to its "rendered" line takes 0.31-0.35 s.

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
  folder), renders them with `render.render()` into `<work>/renders` (never the live cache), and writes the
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
