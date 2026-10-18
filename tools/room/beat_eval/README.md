# Beat stack evaluation

Does our live beat stack (show_service's dsp + clock, with the Beat This! slow lane) still find the beats once a
room, a PA and a mic have had their way with the music? The room audio calibration (`tools/room/audio_cal/`)
measures a room and fits a room model (`roomsim.py`); this folder replays beat-annotated music through the stack,
clean or through that model, and scores what the lights would have flashed.

## Harness

Files (screen-machine-26's builders; run_stack/score/run_batch are the harness):

| file | env | what |
|---|---|---|
| `run_stack.py` | `~/.venvs/sm-show` | one audio file through the stack exactly as `show_service/__main__.py` wires it live; JSON out |
| `slow_lane.py` | `~/.venvs/sm-show-ml` | run_stack's pre-pass: the ML worker's own Beat This! task on the windows the live worker would run |
| `score.py` | `~/.venvs/sm-roundtrip` | results vs annotations: mir_eval, gate coverage, gated precision, wrong locks, lock time, tempo |
| `run_batch.py` | any (stdlib) | a dataset or a stratified subset, one track at a time under the CPU lock, resumable, then score.py |
| `synth.py` | any (numpy) | synthetic loops with exact annotations (house 120, hip-hop 92 with 8th hats, a click at 100) |
| `pseudo_gt.py` | `~/.venvs/sm-show` | madmom's offline downbeat tracker as stand-in annotations for the test tracks |

Heavy steps share the host with the live 4K renderer: run them as
`flock ~/.cache/screen-machine/cpu.lock nice -n 19 <cmd>`, one at a time (run_batch.py does this per track).

### Quick start

```sh
# synthetic loops (exact annotations): the clock must lock on these, or the replay is wrong
~/.venvs/sm-roundtrip/bin/python tools/room/beat_eval/synth.py
~/.venvs/sm-roundtrip/bin/python tools/room/beat_eval/run_batch.py ~/.cache/screen-machine/beat-eval/synth/index.json

# a dataset through this room's model, 40 tracks spread over the genres, at the measured noise and at 10 dB SNR
M=tools/room/audio_cal/runs/<run>/model/model.json
~/.venvs/sm-roundtrip/bin/python tools/room/beat_eval/run_batch.py ~/.cache/screen-machine/beat-datasets/<name>/index.json \
    --subset 40 --stratify genre --degrade $M
~/.venvs/sm-roundtrip/bin/python tools/room/beat_eval/run_batch.py ... --degrade $M --snr-db 10

# several datasets from the combined index, dealt over dataset then genre, with the gate's reference at the room's level
~/.venvs/sm-roundtrip/bin/python tools/room/beat_eval/run_batch.py ~/.cache/screen-machine/beat-datasets/index.json \
    --datasets ballroom,gtzan --subset 40 --stratify dataset,genre --gate-ref-lufs -47
# anything else run_stack.py takes, for every track, after --
~/.venvs/sm-roundtrip/bin/python tools/room/beat_eval/run_batch.py ... -- --rx-latency-ms 40 --loss-pct 1
# with --degrade, give each track its own room-noise draw (--seed + a hash of its id), or every track hears the
# same 30 s of room sound and the result swings with that one stretch
~/.venvs/sm-roundtrip/bin/python tools/room/beat_eval/run_batch.py ... --degrade $M --track-seeds

# one file by hand
flock ~/.cache/screen-machine/cpu.lock nice -n 19 ~/.venvs/sm-show/bin/python tools/room/beat_eval/run_stack.py \
    song.wav --beats song.beats --out /tmp/song.json [--degrade $M] [--no-slow-lane] [--gate-ref-lufs -47]
~/.venvs/sm-roundtrip/bin/python tools/room/beat_eval/score.py /tmp/song.json [more.json | a results dir ...]
```

Results go to `~/.cache/screen-machine/beat-eval/results/<dataset>[-<room run>][-snrN][-refL][-noslow][-durS]/`
(`--out` names another): one JSON per track (`<dataset>__<id>.json`, the same name from a dataset's own index or the
combined one), `batch.jsonl` (per track: exit code, `lock_wait_s`, `run_s`, `cost` = run_s per second of audio,
the stack id), `scores.csv` (per track) and `summary.csv` (overall, per dataset, per dataset/genre). Delete a track's
JSON to redo it; a stopped batch resumes. The 13 ballroom excerpts marked `duplicate` are left out unless
`--with-duplicates`. Nothing here goes in git.

The stack under test is whatever show_service holds on disk when each track runs, and other sessions edit it in
place. Each result records `stack` (git HEAD, the files that differ from it, a short hash per file; `stack.id` hashes
the files only), and score.py prints a NOTE when a results dir mixes stacks: delete the stale results and rerun to
compare like with like.

### What the replay copies from live (run_stack.py's docstring has the detail)

- **Input**: 48 kHz; `--degrade` runs `roomsim.apply` (mono, the room's EQ/reverb/dynamics/noise); otherwise the
  channels are averaged. Then the VBAN INT16 round trip (vban's own codecs) and 128-frame blocks.
- **Time**: stream sample n sounds at T0 + n/48000; block j reaches show-rx at its last sample + `--rx-latency-ms`
  (30); `on_tick` every 10 ms of host time between blocks. A published body knows only audio already "arrived".
- **Per block**: `Features.process`, then `Clock.set_level(features gate)` and `Clock.process`; `music.hit` ->
  `Clock.observe_hit`; `Features.clock_fn = Clock.snapshot`; `Features.gate_ref_lufs` = `--gate-ref-lufs` (-30, dsp's
  default; a vibe sets -35 .. -18). **Per tick**: `Features.tick`, then `Clock.tick`.
- **Slow lane**: the worker's `beats` task on its 10 s grid (random phase from `--seed`), the newest 30 s window
  that has arrived, none before the segment holds 30 s, none on a silent window; each line goes to
  `MLLink._on_line` (beat_vote, agree runs, cooldown, then `Clock.apply_integer_correction`) 3.7 s after its turn
  (the live worker's measured p50, 18 Oct). `--no-slow-lane` turns it off; `--slow-cost-s measured` uses this run's
  own cost; `--slow-every-s 15` models the worker's CPU guard stretching the interval (15 s live at 16:35, 18 Oct).
- **Packet loss** (off by default): `--loss-pct P --loss-burst B` drops VBAN packets (B per hole on average, at most
  250 ms). Each hole reaches dsp and the clock as the receiver's zero-filled AUDIO_GAP block, with the next block
  flagged AUDIO_RESUME. The slow lane skips any window that holds a gap, as the worker does.
- **Pre-roll**: `--preroll-s` (default 30 s with `--degrade`, else 0) of room tone before the music, as an open mic
  hears it: dsp's gate learns the input's noise floor over its first 30 s, and the slow lane needs 30 s.
- madmom runs inline offline (the same frames as live's show-beat thread; identical flashes on house120, a quarter
  less wall time); `--beat-thread on` runs the thread.
- Bodies: only those whose line or labels changed are kept (`--all-bodies` keeps every 10 Hz heartbeat). The
  2 Hz `features` trace also carries the clock's AGC gain and quiet flag.

### Output and scores

`flashes` are the causal integer-beat crossings of the published clock line (each from the body in force at that
moment; artnet_out's re-phase rule), with the gate, conf and state they were flashed under. score.py reports, from 0
s and with the first 5 s left out: mir_eval F (70 ms), CMLt, AMLt, P-score of all flashes; F of gated flashes; hit
rate within 25 ms (any flash, and gated flashes) and the share of annotated beats with a gated flash within 70 ms
(`gHit70`, run_stack's "gated within 70 ms"); the median signed offset (flash - annotation: latency, not tracking);
gate coverage (beat, beat|dmx); precision of gated flashes (strict, and allowing a doubled tempo's off-beats);
wrong-lock share of gated time (a trailing 4-bar window under 50 % precision); time from the music's start to a 4-beat
correct lock (and the share of tracks that lock); tempo Acc1/Acc2 (track estimate and share of time). It prints a
table per track (up to 30 tracks; `--tracks on|off`) and per group, for both ranges. score.py's docstring defines
each column.

### Cost (measured 18 Oct, nice 19 under the 4K render, one track at a time)

Run time per second of audio (`cost` in batch.jsonl; the process's own time, imports included, lock wait not):

| what | audio | cost | of which |
|---|---|---|---|
| 30 s excerpts (ballroom, gtzan) | 29-32 s | 0.33-0.41 (10-13 s a track) | replay 0.24-0.32 s/s, 2-2.5 s of start-up; no slow-lane turn (below) |
| full tracks, slow lane on (asap, tapcorrect, harmonix) | 130-416 s | 0.41-0.62 (64-220 s a track) | replay 0.21-0.35 s/s, slow pre-pass 0.17-0.25 s/s (Beat This! loads in 2-5 s, then 2-4 s per 30 s window, one per 10 s) |
| the same, `--no-slow-lane` (tapcorrect 064, 197 s) | 197 s | 0.32 | replay 0.30 s/s |
| synth loops (64 s) | 64 s | 0.49-0.95 | the 0.95 ran while the host was busier |

madmom's two LSTMs dominate the replay (dsp is about a sixth); a quiet pre-roll is nearly free, since the clock
skips madmom while the room is quiet. For planning: every 30 s excerpt of ballroom + gtzan (1,683 tracks, 14 h of
audio) is about 5.3 h of locked CPU; the full-track sets (harmonix 5.9 h, tapcorrect 5.4 h, asap 1.8 h) about
6.5 h with the slow lane, 4.2 h without; a 40-track stratified subset per set, about 8 min (excerpts) or 80-90 min
(full tracks, about 4 minutes each). Each room-model condition (`--degrade`, each `--snr-db`, each
`--gate-ref-lufs`) is another pass. The batch's wall time can be mostly lock wait: while fetch_datasets.py held the
lock in its 5-minute slices, each track waited 2-10 minutes for it. score.py is light (2-4 s for a results dir,
mostly imports) and pins itself to one BLAS thread (unpinned, OpenBLAS's threads spun on 16 cores).

### Smoke checks (18 Oct; the stack on disk then: clock.py and dsp.py uncommitted and being edited)

- **Synthetic loops** (synth.py, exact annotations, slow lane on): all three lock (house120 3.5 s, click100 4.2 s,
  hiphop92 4.6 s, at 120/100/92 bpm; hiphop92's 184 activation never doubles it); after 5 s F 0.99-1.00, hit25 1.00,
  gated precision 1.00, no wrong locks, offsets +1..+5 ms. Beat This! agrees with the clock on every window (vote
  conf 1.0: its beats fall where the clock's are, so the slow lane's times line up) and moves the bar's one once on
  house120 and hiphop92 (score.py judges beats, not downbeats); on click100 it finds too few beats to vote.
- **The calibration recording** (`tools/room/audio_cal/runs/20261018T145602Z-pc22-mic/rec-music.wav`, 53.5 s,
  `--beats reference/beats-at-mic.beats`, no pre-roll) reproduces 26's numbers (reference/README.txt) to the
  percent: the default gate (-30 LUFS) never locks, no flash gated "beat", 12 % within 25 ms (F 0.42; 0.40 after
  5 s); `--gate-ref-lufs=-47` locks at 3.9 s, 79 % within 25 ms, gated within 70 ms 64 % (F 0.91; after 5 s F 0.94,
  gate coverage 0.69 beat / 0.89 beat|dmx, gated precision 0.99, no wrong locks); the clean original
  (`original/original-matched.wav` vs `beats-original-timeline.beats`) locks at 3.9 s, 94 % within 25 ms. With the
  slow lane on (2 turns, Beat This! 125 bpm on both) nothing changes: at -30 the clock has no grid to vote on; at -47
  the two votes disagree on the bar, so neither applies.
- **Datasets, clean** (results/smoke-excerpts, smoke-full; per-dataset rows after 5 s): ballroom 4 excerpts F 0.79
  (one Quickstep at half tempo, never gated "beat"), gtzan 4 F 0.46 (disco 0.99; country at double tempo, gated
  precision 0.51 strict / 1.00 double-half; blues locked half tempo on the off-beats, wrong-lock 0.91; classical
  never gated); asap 3 rubato piano pieces (tagged "must not falsely lock"): never a 4-beat correct lock, gate
  "beat" on 0-4 % of the beats, but Haydn's few gated flashes are mostly off the beat (wrong-lock 0.87) and Bach's
  are at double tempo; tapcorrect 3 pop tracks F 0.88-0.98, all lock (8-45 s), hit25 0.64-0.92; harmonix 3 full
  tracks: Paralyzer F 0.99 and Gangnam Style 0.98 (lock in 8 and 4 s), but Gunpowder & Lead (Country) 0.40: it locks
  only at 150 s, and half its gated time is a wrong lock (0.51). Flashes sit +2..+22 ms after the annotations on the
  full tracks (`offMs`), which costs hit25 more than F.
- **Slow lane on/off on a full track** (tapcorrect 064, 197 s, 108 bpm): 17 turns, Beat This! at 107 bpm (13) or
  111 (4, its 20 ms frames), but no vote applied (12 "low agreement": its integer corrections can't fix the
  clock's stretches at the wrong, non-octave tempo; 2 "no grid"), so the scores are identical with and without it;
  the pre-pass is 40 % of the run (44 of 109 s).
- **`--degrade` runs** with the PC22 room model (`runs/20261018T145602Z-pc22-mic/model/model.json`, as written at
  20:09): house120 through it with the 30 s pre-roll and `--gate-ref-lufs -47`, no slow lane, locks 12 s after the
  music starts, F 0.94 after 5 s, gated within 70 ms 72 % (results/smoke-synth-pc22room-ref-47-noslow). A check
  that the path works, not a result.
- **Excerpts never get a slow-lane turn**: the worker runs Beat This! only once its segment holds 30 s, and the
  grid's next turn comes after that, so on 30 s excerpts (ballroom, gtzan) the slow lane is idle unless a pre-roll
  (`-- --preroll-s N`, room tone; `--degrade` adds 30 s) puts the music's end past a turn.
- The stack changed under the smoke runs (results in one dir carry up to three `stack.id`s; score.py's NOTE): the
  numbers above are a harness check, not the baseline.

## Datasets

`fetch_datasets.py` (`~/.venvs/sm-roundtrip`) puts every beat-annotated set into one layout under
`~/.cache/screen-machine/beat-datasets/` (nothing in git): `<set>/audio/<id>.{wav,flac,opus}`, `<set>/beats/<id>.beats`
(`time<TAB>position in bar`, 1 = downbeat, 0 = unknown), `<set>/index.json` (run_batch.py's `{id: entry}`: genre,
duration_s, n_beats, bpm, beats_per_bar, kind `excerpt`/`full`, tags, notes, the onset check), `<set>/failures.json`
(what was dropped, and why) and `<set>/verify.json`; plus the merged `index.json` (ids `<set>/<id>`, paths relative
to the root) and `summary.json` (counts, hours, genres, failures, disk).

```sh
F=tools/room/beat_eval/fetch_datasets.py
~/.venvs/sm-roundtrip/bin/python $F                                   # fetch what is missing, then verify it
~/.venvs/sm-roundtrip/bin/python $F --list                            # the table below, from the indexes
~/.venvs/sm-roundtrip/bin/python $F --verify                          # check every item (about 20 s)
~/.venvs/sm-roundtrip/bin/python $F --only harmonix --harmonix-n 120  # grow a set (what is there is kept)
```

It renices itself and takes the CPU lock around its heavy steps (alignment, decoding), letting it go every 5
minutes, so do not wrap it in flock. It is idempotent: a fetched item is kept and a dropped one is not retried
(`--retry-failed` does), except YouTube 403s, timeouts and network errors, which every run retries.

What is there (18 Oct 2026, 19:53):

| set | items | hours | each | annotations | genres | for |
|---|---|---|---|---|---|---|
| ballroom | 698 | 6.1 | 30 s excerpt | beats + downbeats | 10 ballroom styles (ChaChaCha 111 .. Rumba-American 7) | steady dance music |
| gtzan | 998 | 8.3 | 30 s excerpt (22.05 kHz mono) | beats + downbeats | 10 genres x 100 (jazz, reggae 99) | the genre spread |
| harmonix | 90 | 5.9 | full track (102-422 s) | beats + downbeats | Pop 25, Dance/Electronic 22, Hip-Hop 15, R&B 6, Alternative, Metal, Country, Rock 4 each, Indie Rock 2, Reggaeton 2, Prog 1, Funk/Disco 1 | party music, whole songs; 7 not in 4/4 |
| tapcorrect | 74 | 5.4 | full track (126-545 s) | beats + downbeats | pop/various (international chart pop) | whole songs, annotated on this very audio |
| asap | 24 | 1.8 | full piece (81-420 s) | beats + downbeats | classical piano, 12 composers | rubato: must not falsely lock |
| smc | 0 | - | 40 s excerpt | beats | - | its host is offline |

1,884 items, 27.4 hours. Disk: 6.4 GB of audio, 2.8 GB of downloads kept (Harmonix's mel spectrograms 1.3 GB and
its YouTube audio 0.9 GB, for re-cutting without downloading again; TapCorrect's YouTube audio 0.3 GB), 9.2 GB in all.

- **ballroom**: MTG's ISMIR 2004 tempo-contest audio (the 1.45 GB tarball is deleted after extraction) with CPJKU's
  annotations (Krebs, Böck & Widmer 2013). The 13 exact duplicates (Sturm 2014; left out of Böck's ISMIR 2019 list)
  are marked `duplicate`, and run_batch.py leaves them out unless `--with-duplicates`.
- **gtzan**: the marsyas Hugging Face mirror's audio with Marchand & Peeters' 2015 annotations. jazz.00054 is
  unreadable in the archive, so 998. GTZAN's known faults (distorted, repeated and mislabelled clips; Sturm 2013)
  are in it.
- **harmonix**: the Harmonix Set (Nieto et al. 2019) annotates 912 full tracks but ships no audio; it publishes a
  YouTube link per track and mel spectrograms of the originals it annotated. The fetcher downloads a video's audio,
  finds where the original starts in it by correlating its mel (librosa 0.7's, rebuilt in numpy) with the published
  one, and keeps it only if it matches throughout: mean per-band r of at least 0.80, 85 % of 20 s windows at the
  global lag, and at most 15 ms of drift between the first and the last 40 % of the track (no other edit, no speed
  change). It cuts the audio onto the annotations' timeline (48 kHz stereo FLAC), one MP3 encoder + decoder delay
  later (1105 samples at 44.1 kHz, 25 ms: the published mels were made from MP3s decoded without gapless trimming;
  measured on 29 tracks), then runs the onset check (salience at least 2, lag within 60 ms). Where the video is
  shorter than the original (30 tracks, 0.1-6.6 s) the cut is padded with silence and the beats there are left out
  (`padded_s`, the notes). Candidates are the 784 tracks with Harmonix's own YouTube match score of 0.95 or more.
  The subset is stratified: genre shares weighted towards party music (`HARMONIX_SHARES`: Pop 23 %,
  Dance/Electronic 20 %, Hip-Hop 15 %, ...), up to 6 tracks not in 4/4 first, the tempo spread within each genre;
  a genre whose candidates run out hands its share to the biggest ones (Funk/Disco, Classic Rock and Reggaeton did).
  Yield on 18 Oct: 90 of 240 tried. 108 videos were another edit or version (music-video edits, radio edits,
  remixes; 15 of them drift by more than 15 ms), 41 could not be downloaded (31 gone, 3 private, 2 age-restricted,
  5 403s that the next run retries), 1 failed the onset check.
- **tapcorrect**: Chordify's TapCorrect (Driedger et al. 2019): 101 tracks whose beats were tapped and corrected on
  their YouTube audio, so the audio is the annotated audio itself (the Opus stream remuxed, not re-encoded; one AAC
  item is FLAC). A video must be the annotated length to within 1 s and pass the onset check in at least 80 % of its
  30 s segments. 74 of 101: 20 videos gone (removed, private, 1 age-restricted), 4 another version (1.3-3.7 s
  longer or shorter), 3 off their onsets (two by a constant -120 and -134 ms, one with no clear peak).
- **asap**: ASAP's beats and downbeats (Foscarin et al. 2020) on MAESTRO v3's Disklavier recordings (Hugging Face
  mirror), cut to ASAP's span: one performance per piece, round-robin over 12 composers (at most 3 each), 60-420 s.
- **smc**: SMC_MIREX (Holzapfel et al. 2012; 217 hard 40 s excerpts) lives only on smc.inesctec.pt, which timed out
  on all three URLs all of 18 Oct; no mirror exists (the Wayback Machine's copy is cut at 10 MB). Every run tries
  again (about a minute) and fetches it once the host is back.

**Onset check** (every item; it drops items only in harmonix and tapcorrect): the audio's spectral-flux onset
envelope (5 ms frames) is averaged at every annotated beat shifted by tau; `onset_lag_ms` is the best tau within
+-150 ms (positive: the onsets come after the beats), `onset_salience` that peak over the mean across tau (beats on
the onsets give well over 2, beats that are not, about 1). Medians: ballroom -12.0, gtzan -22.5, harmonix -14.3,
tapcorrect -13.6, asap -5.7 ms (the measure's own lead, about -13 ms where audio and annotation come from the same
file; GTZAN's annotations sit about 10 ms later); single tracks spread from -60 to +32 ms on the full-track sets.
It is a sanity check, not a correction. ASAP has four outliers (+44, +125 and +148 ms, -128 ms: rubato pieces whose
score beats fall on rests and ornaments), kept.

**Verify** (`--verify`, and after every fetch): each item's header and last second (a truncated file fails there)
and its beats (increasing, as many as the index says, inside the audio, a median tempo of 30-320 bpm, a length that
fits its kind). 18 Oct: 1,881 of 1,884 clean. The three flagged are annotation quirks, kept: gtzan/jazz.00031
(median 339 bpm), gtzan/reggae.00002 (its last beat 0.11 s after the clip ends), asap Beethoven op. 53 ii (56 beats
in 245 s, 14 bpm).

## Baseline (18 Oct; stack s9f302a82da = HEAD 13ff85cf, show_service committed, the same id on all 204 results)

`baseline.py` pins down "before" for the stack work that follows: one fixed subset, the conditions, one report.

```sh
~/.venvs/sm-roundtrip/bin/python tools/room/beat_eval/baseline.py subset     # ~/.cache/screen-machine/beat-eval/baseline-subset.json
~/.venvs/sm-roundtrip/bin/python tools/room/beat_eval/baseline.py run --conditions C,A,B           # all 51 tracks
~/.venvs/sm-roundtrip/bin/python tools/room/beat_eval/baseline.py run --conditions D --datasets harmonix,tapcorrect,asap
~/.venvs/sm-roundtrip/bin/python tools/room/beat_eval/baseline.py run --conditions E --datasets ballroom,gtzan
~/.venvs/sm-roundtrip/bin/python tools/room/beat_eval/baseline.py report     # baseline-report/report.md, report.json, tracks.json
# after a stack change: the same with --tag <name> (results in baseline-<cond>-<name>/), and compare the reports
```

- **Subset** (seed 0): ballroom 20 and gtzan 20 (30 s excerpts, 2 per genre), harmonix 6 (full tracks: Pop, Dance,
  Hip-Hop, R&B, Country, Alternative; the biggest genres first), tapcorrect 2, asap 3 (rubato piano: the no-false-lock
  check). SMC: none (offline). Every replay is capped at 260 s (each Harmonix pick plays whole). Each track has its
  own fixed seed (`--track-seeds`), the same in every condition.
- **Conditions**: A clean, gate -30; B room model (PC22 mic, model v2), gate -30; C room model, the calibrated gate
  -48.64 (audio_cal.json `music_lufs`, as GateOffsetWatch sets it live); D = C with the slow lane on (the 11
  full-length tracks: on a 30 s excerpt it gets three turns, mostly over the pre-roll); E = C without the model's room
  sound (`--snr-db 60`; the 40 excerpts), a control. A, B, C, E run without the slow lane. The room-model runs have
  run_stack's 30 s pre-roll of room sound before the music.
- **Cost**: A 20 min, B 37, C 31, D 29 (11 tracks), E 15; 0.27-0.62 s of run per s of audio (the host was busier
  than at the smoke runs).

Steady-beat music (everything but ASAP and GTZAN classical), whole replay from the music's start, means per track:

| cond | n | F | CMLt | AMLt | hit25 | cover | prec | wrong | tLock (locked) | locked | Acc1 | Acc2 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A clean, -30 | 46 | 0.75 | 0.53 | 0.78 | 0.57 | 0.61 | 0.88 | 0.07 | 7.8 s | 0.89 | 0.65 | 0.98 |
| B room, -30 | 46 | 0.28 | 0.13 | 0.21 | 0.12 | 0.07 | 0.78 | 0.17 | 18.2 s | 0.24 | 0.20 | 0.35 |
| C room, -48.64 | 46 | 0.36 | 0.20 | 0.33 | 0.17 | 0.07 | 0.94 | 0.01 | 15.3 s | 0.28 | 0.22 | 0.37 |
| D = C + slow lane (full tracks) | 8 | 0.34 | 0.19 | 0.27 | 0.15 | 0.05 | 0.97 | 0.01 | 19.0 s | 0.50 | 0.12 | 0.25 |
| E = C without room sound (excerpts) | 38 | 0.36 | 0.14 | 0.27 | 0.17 | 0.14 | 0.92 | 0.05 | 16.8 s | 0.39 | 0.24 | 0.53 |

The bars for this room (C, steady-beat music): precision while gated >= 0.9: 0.94 (pooled 0.96), pass; coverage >=
0.7: 0.07, fail; median time to lock <= 8 s: 15.3 s over the 28 % that lock, fail; false locks on the expressive
sets rare: ASAP and GTZAN classical never gated "beat" in B, C, D, pass. The current stack is not adequate in this
room: when it flashes on "beat" it is right, but it almost never does.

What the runs show (report.md has every table, the per-genre rows and the 10 worst tracks):
- **Conf never reaches the beat gate.** In C 38 of 51 tracks never gate "beat"; the clock sits locked or holding
  with conf at a median 0.11 (the gate opens at 0.70; clean: 0.75). 27 of the 46 steady tracks run at a wrong,
  non-octave tempo, 7 at half tempo.
- **Room sound before the music sets a wrong tempo that sticks.** In the 30 s pre-roll the room sound alone gets
  the clock to locked/holding on every track, and on 17 of 48 the tempo during the music stays within 4 % of it.
  The calibration track (`original-matched.wav`) through the same C settings: no pre-roll, 3 of 3 seeds lock (3.8,
  4.8, 21.9 s; F 0.70-0.88); with the 30 s of room sound first, 2 of 3 never lock and sit at 42-44 bpm (a third of
  125). The real recording replayed whole (`rec.wav`: 182 s of real room sound, then the track) does the same: it
  never gates "beat" at -48.64 or -30, and sits at 42-44 bpm through the music. Cut to the music alone it locks in
  3.9 s.
- **The mic chain alone is most of the loss.** Without the room sound (E) coverage is 0.14 and 39 % lock (clean
  0.56 / 88 % on those excerpts). The speakerphone's processing (the model's scramble) leaves 9 % of the mid-band and
  5 % of the high-band onsets on the real recording, against 51-65 % in sub and bass. That looks like it starves the
  clock on music whose beat isn't in the bass; Sweet Dreams' synth bass is why the calibration track still locks.
- **The calibrated gate** removes B's wrong locks (0.17 -> 0.01) and lifts precision (0.78 -> 0.94), but not
  coverage: dsp's floor-following already lowers the -30 reference after a room-sound pre-roll, so B and C gate
  about equally rarely.
- **The slow lane does nothing here**: D equals C on every number. 250 turns, 0 applied; 229 "no grid" (it votes
  only on a confident clock line), the rest "too few beats" or no vote.
- **Clean (A) misses the bars too**: coverage 0.61, precision 0.88. Waltz 0.41 strict (0.95 double/half), Rumba-
  International 0.56, metal 0.64 with a 0.47 wrong-lock share, Quickstep at half tempo, ASAP Haydn's 8 gated flashes
  a wrong lock (0.87).
