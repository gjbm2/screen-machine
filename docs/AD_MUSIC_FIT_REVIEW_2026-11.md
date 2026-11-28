# Does the show feel the music? A top-down review of the art director, 27 Nov 2026

**For:** the owner, and the sessions that own the pieces (c8: show_service; 04: renderer).
**Asked (owner, 27 Nov):** "How well do our lighting and rendering scenes, effects and transitions fit the live audio
and programmed audio (karaoke songs): shape, contours, tone and cadence? We want this to feel the music, snappy and
intelligent. The a.d. in theory has the information it needs, but I worry we haven't built the right eval machinery
around it to have confidence in its competence."
**Evidence:** code and config read on 27 Nov (file:line below); the laptop's runtime logs
(`~/.local/state/screen-machine/show/director.jsonl`, about 4.6 h, mostly singalong and karaoke; `cues.jsonl` since
10 Nov); 15 karaoke songs' measured shapes; `tools/room/beat_eval` results. PC22's logs weren't read (this is the
laptop). Nothing was changed.

---

## 0. The answer in short

1. **The timing plumbing is strong.**
   - The beat clock is precise when it is locked (precision 0.95).
   - Every output is evaluated at its own latency, and DMX frames are phase-locked to the beat.
   - Transitions and gestures are measured in beats and land on lines.
   - Section recall lands exactly from the second time through.
   - director.jsonl records why it did everything.
2. **The musical judgement on top of that plumbing has gaps, and they explain most of the missing "feel":**
   - **It pulses to the clock, not to the sound.** Accent depth is a fixed number per feel, scaled by energy smoothed
     over 2 s. Fills fire on every phrase's last bar whether or not the music has one.
   - **The song's shape is computed but doesn't reach the outputs.** The song-relative intensity feeds only the band
     words (20 s dwell). For karaoke, the song's whole energy curve is on disk and is never read while it plays.
   - **It's late.**
     - The screens show energy and bands about 1 s after the sound.
     - The beat clock takes about 14 s to lock, and is locked for only about a third of a song on the room mic.
     - A section heard live is confirmed about 7–9 s after it starts, and is then dropped as too late.
   - **Tone barely steers anything.** The palette is round-robin. Key, swing, brightness and syncopation are unused.
     Scene choice is dominated by unchecked AI genre/mood tags, cubed.
   - **On screen, the scenes speed up and flash on the beat, and that's all.** 464 of 468 mappings use one rule,
     `pulse = beat × punch`. None binds bar, phrase, downbeat, section or build.
3. **The eval machinery matches what you feared.**
   - Only the beat clock has ever been scored against ground truth. That tool has been dormant since 21 Oct, and it
     fails its own bars (lock ≤ 8 s, coverage ≥ 0.7).
   - Everything above the clock is graded against itself (shape_eval scores the director against the same loudness
     measure it is built from) or by eye.
   - No ground truth exists for sections, drops, climaxes or transitions. No harness replays a song through the whole
     show. Nothing reads the decision logs.
   - Since 22 Oct there have been 54 commits to director, structure, cues and looks, none checked against the music.
4. **Karaoke is where the biggest, cheapest wins are,** and it has a date (5 Dec). The song is a file before it plays,
   so beats, sections, energy, drops and the ending can all be known exactly and early. Today almost none of that is
   used: the lights still find the beat from the mic.

---

## 1. Fit to the music, by dimension

### 1.1 Cadence (beat, bar, phrase)

| | Live mic | Karaoke |
|---|---|---|
| Beat source | DBN tracker on the mic. Room-mic full songs: F 0.76, precision 0.95, coverage 0.35, lock 13.6 s, 6 of 24 at the wrong octave (beat_eval, commit 257c50ae). | **The same mic tracker.** `clock.py` has no notion of the player, so every song start re-locks for several seconds, and a singing crowd wobbles it. |
| Mid-song stability | bpm wobbles 98–103 on a steady ~100. The slew allows 10% (`clock.py:78`), not the doc's 4%. Octave flips mid-song (logged: 74 → 145 → 127 → 148 → 74 in 7 min), and each one doubles every beat-locked rate. 3/4 needs ~40 s of votes. Phrases can only be 4/8/16 bars. | Same |
| Accents | Swell depth = `accent_depth` per feel × `swell(predicted beat)`; gesture strength = director energy (ensemble.py:1574-1579). The sound's own hit strength at a beat is not used. Fills fire on every phrase's last bar at energy ≥ 0.6 (show.yaml:1240). | Same |
| Real hits | Only in `[beat, full]` accent styles (show.yaml:1030), in 15 of 65 light scenes. None at all when the gate is `none` (13% of audible music: ballads, intros), because §5.4's "DMX follows onsets" was never built. Hats aren't filtered (`looks.hits.bands` unset, `flash_hz_max: null`), so techno can reach ~13 flashes/s. | Same |
| Screens | The beat is read at photon time (`sessions.py:438`). Good. | Same |

### 1.2 Contour (energy, dynamics, builds)

- **Lamps:** level = `lerp(vs.level, u)` (director.py:5083), where `u` is the mean of three parts:
  - energy, normalised over 2 s;
  - onsets (0.6 × drive over 8 s + 0.4 × onset);
  - harmony (0.7 × tension + 0.3 × chord change). So dissonance brightens the lamps.
  - Party compresses the level to [0.6, 1.0].
- **The good parts aren't connected:**
  - The song-relative `_intensity()` (director.py:2599) feeds only the band words.
  - The composer's lamp plan goes to artnet_out only (`__main__.py:779`). Its `loud` is an absolute gate that reads
    about 1 through any normally loud song (compose.py:422).
- **Screens:** energy and bands are read on arrival (`sessions.py:442`, `energy_now(now)`) with a 0.3 s attack, so they
  show on the glass about 0.9–1.1 s after the sound. The beat on the same screen is on time.
- **Karaoke:**
  - `utils/song_shape.py` measures the song's loudness and flux curve (backing + lead summed, 8 s smoothing). Only
    the section means and a climax flag are used, and only to size the lights' line-ups. `song_shape.at()` has no
    callers.
  - The shape is measured lazily at first play: 85 of the 100 laptop songs have none yet.
  - Section levels bunch near the top (Don't Stop Me Now 0.82–0.97 throughout), and the peak flag was set on only 5
    of 15 songs.
- **Builds and drops (live):**
  - The build rule needs the bass held or falling, which rarely fits pop pre-choruses. Logged: 10 builds, 0 drops,
    3 breakdowns.
  - In Party and Disco, `peak_tokens_per_hour` is unset (your call, vibes.yaml:241/281), so `_on_drop` never cuts
    or hits there. Strobes have no budget.
  - The renderer's FX director has no build handling ("build raises amounts" in SCENE_FX_INTERPLAY wasn't built).

### 1.3 Shape (sections, recall, the big moments)

- **Live:**
  - A boundary is confirmed about 6 s after it, plus the lead and the snap to a bar: about 7–9 s in all (the doc
    says 3–5 s).
  - `max_late_bars: 1` (config/room/show.yaml:258) then drops it, so the first verse → chorus never changes the look.
  - Section *kinds* come only from karaoke lyric plans (director.py:2733). The specific moves (`open-out`,
    `lift-and-land`, `breathe`, `settle`, `last-chorus`) never fire on live music.
  - In the logs, all 238 section looks were lyric-plan predictions. None was confirmed by structure.
- **Karaoke:**
  - Announced 8 s ahead (structure.py:183), so the screens land on time. Good.
  - The sections are only as good as the lyrics:
    - Repeated lines become chorus, so a verse refrain counts as chorus.
    - Consecutive verses merge (Common People: a 100 s "verse").
    - No intro section; no pre-chorus.
    - Boundaries sit on vocal onsets, not riff or drum entries.
    - Songs with no chorus are all verse.
  - **Bug:** `songplan.py:55-59` splits a section at the climax before the peak test at `:64-69` decides whether
    there is one, so it makes false same-letter boundaries (Happy 186 s, The Elements 22 s, Common People 274 s).
- **Endings:**
  - Karaoke's `track_end` uses the position before the speaker delay, so it fires one speaker delay early on a
    screen output.
  - No last hit and no fade-out is modelled from the file.
  - Pause doesn't cancel an announced section, so it can fire during the pause and again on resume.

### 1.4 Tone (timbre, mood, key, genre)

- **Lights:**
  - The palette is the feel's next one, round-robin (treatment.py:470-497), unless the AI plans one.
  - Key and mode are used only to detect track changes.
  - Unused: spectral brightness, noisiness, swing, syncopation, harmonic change.
  - Subdivided patterns stay straight on swung music.
- **Screens:**
  - Scene weight = tags^`STYLE_POWER` (3.0, stream_service/director.py:92) × stars × taste × favour.
  - The tags are one LLM pass over renders (`tagpass-20261028`, ~27 genre fits per preset), which SCENE_FX_INTERPLAY
    §2.1 itself rates low-trust. Cubing makes them dominate.
  - The measured look_stats aren't used for scene choice.
  - The stats themselves are from 18 Oct. 260 of 660 scene presets are unmeasured, including every karaoke photo and
    video look, so those get no scene/effect interplay at all.

### 1.5 Transitions

- **Screens:**
  - Durations are tempo- and energy-scaled (137 of 140 mappings) but quantised to beats, not bars.
  - `cut` is manual-only, used only by the biggest-section and drop moves.
  - Logged: 139 of 144 look changes were crossfades.
  - Choosing a transition from the two scenes (SCENE_FX_INTERPLAY §2.4) wasn't built.
- **Late announcements miss the line.** `look_change` goes out "≥ 350 ms ahead where it can be" (show_service/director.py:39),
  but the renderer needs its lead + 0.25 s, i.e. 0.8–1.1 s. A later one falls to the next downbeat, up to a bar late.
- **FX layer:**
  - The FX director never sees `look_change` in director mode (sessions.py:2122-2131).
  - A scheduled FX move can land a bar after a scene cut.
  - That breaks the "one layer at a time" rule.
- **Calibration under all of it:**
  - latency.json measures only Hue and the south TV. North borrows south's figure.
  - DMX (the GigBARs) is unmeasured.
  - `sources: {}` means no acoustic offset has been measured.
  - So "~10 ms early" is a target nobody has checked.

---

## 2. The eval machinery today

| Competence | Status | What exists |
|---|---|---|
| Beat / downbeat / lock time | **Ground truth, dormant** | `tools/room/beat_eval` (mir_eval vs Ballroom/GTZAN/Harmonix/TapCorrect). Last run Oct; fails its own lock and coverage bars. 0 commits to the clock since 22 Oct. |
| Light vs sound latency | **Partial** | `latency_cal`: TV south 179 ms presented→photon, Hue t50 130 ms. No DMX, no north, no acoustic leg. The §5.5 photodiode/click method is doc-only. |
| Energy contour | **Circular** | `tools/room/shape_eval` (26 Nov, 20 songs, score 0.686 → 0.857). It scores `room.direction.energy` against the same loudness measure the director uses, with the clock, structure and ML stubbed out. |
| Section boundaries, chorus recall | **None** | No labels anywhere; test_structure uses synthetic input. |
| Builds / drops | **None** | cues.jsonl's `structure_saw` checks the forecast against structure.py, not against the music. |
| Look-change placement, cross-layer coherence | **Live-only** | `tools/room/sync_eval` scores landing against the director's *own* line, live, not deterministic, results to stdout. |
| Transition fit, scene/mood fit | **None** | SCENE_FX_INTERPLAY's step-4 judging page was never built. |
| Karaoke plan vs the song | **None** | Unit tests only. |
| Restraint, tokens | **Logged, unread** | director.jsonl: look-change gap p50 24 s; 119 of 144 changes reason `repeat`; 27 tokens: 17 on `transition`, 5 final chorus, 4 peak, 0 drops. Rotates at 1 MB (~4.6 h kept). |
| Regression between versions | **None** | Deterministic fragments (beat_eval's run_stack, shape_eval's director.jsonl), no diff. |

---

## 3. What to build: one bench, one corpus, one scorecard

Tuning by ear on real music stays (§13.3). The bench is what tells us whether a change made the show better or worse
before it reaches the room. It's a calibration tool run on demand, like beat_eval, not a test gate.

### 3.1 Corpus with ground truth (the piece that's missing)

- **20 karaoke songs**, chosen across genres, tempi, a 3/4, a ballad, a no-chorus song, and a long coda.
- **Machine first pass, owner checks it.**
  - Beats and downbeats: Beat This! on the instrumental stem.
  - Sections, with intro, pre-chorus and outro: novelty on the backing, labelled with the lyrics.
  - Drops, stops and the last hit: from the loudness curve.
  - **A small labelling page** shows the waveform, the proposed marks and the audio, and the owner drags to fix them.
    About 2 minutes a song. These labels become the truth.
- **Room recordings:** the mic's feed while those songs play in the room, saved with the player's position, so live
  runs can be replayed as the room heard them. The 120 s ring exists; add a session capture.
- Later: the same for a few live (non-karaoke) sessions.

### 3.2 Bench: a whole-show replay on simulated time

The pieces exist:
- `run_stack`: features, clock, slow lane;
- `shape_eval`: features → director;
- `looks.py` offline.

Join them, with structure, cues, ensemble, cached ML outputs, a stubbed listener and the looks engine run to DMX frames.
Add the renderer's music signals and its scene and transition picks.

The output is two timelines per song:
- every decision;
- every output's value at its photon time.

It is deterministic, so two commits can be diffed.

### 3.3 Scorecard (each with a bar; numbers are starting points)

| Dimension | Metric | Bar |
|---|---|---|
| Cadence | beat F, coverage, lock time, octave errors (karaoke: vs the file grid) | karaoke: lock at the first beat; live: lock ≤ 8 s |
| Snappiness | lag at the peak of the cross-correlation between the labelled energy curve and each output's level, at photon time | ≤ 150 ms lamps, ≤ 250 ms screens (karaoke) |
| Contour | Spearman correlation, output level vs labelled energy, per song | ≥ 0.7 |
| Accents | accent strength vs onset strength at each beat | positive and clearly above a shuffled baseline |
| Sections | look change within ±1 beat of a labelled boundary; median lateness; changes not at a boundary per minute | karaoke ≥ 90% on the beat |
| Moments | the big move spent on the labelled climax/drop/last chorus; not elsewhere | ≥ 80% |
| Ending | last hit and fade matched; screens dark on the last sound, not before | |
| Restraint | changes per minute, variation vs new look, tokens on labelled peaks | within the feel's dwell |
| Coherence | screens and lights changing on the same line; FX never a bar after a cut | |
| Taste | owner A/B page: two versions' 30 s clips (picture + lights, from the bench), good/better | weekly, 10 minutes |

Taste can't be measured automatically. A multimodal model (Gemini, already used in the karaoke sync eval) can triage
clips, but only after it agrees with the owner's A/B marks.

---

## 4. Fixes, ranked

Karaoke first, because of 5 Dec and because these are the cheapest and most certain.

**Karaoke (the file is known ahead):**
1. **The beat grid from the file.** Run Beat This! on the instrumental in the worker and save `beats.json`. Phase-lock
   `music.clock` to the player's position + speaker delay while a song plays; the mic only checks it. This ends the
   lock-up time and the octave flips.
2. **Energy and bands from the file, sampled at each output's photon time.** This takes the screens' ~1 s lag to
   zero, and lets lamps rise into a chorus instead of after it.
3. **Sections from the audio, labelled by the lyrics.** Find them by novelty and self-similarity on the backing,
   snapped to file downbeats. Add intro and pre-chorus. Fix the split-without-peak bug. Rank-normalise section levels
   within the song.
4. **Pre-scheduled moments.** Drops, stops, risers, the last hit and the fade-out, from the file, emitted as armed
   events at their times.
5. **Sync hygiene.**
   - The display page and the show use one delay value.
   - `track_end` uses the delayed position.
   - Pause cancels a pending section.
   - The worker measures the shape, beats and key at "ready", not at first play.

**Live (mic):**
6. **Accents from the sound.** Scale each beat's swell and gesture by the hit or onset strength within ±40 ms of it.
   Fire fills only on an onset-density rise in the last bar.
7. **Wire the shape.** Drive lamp level, accent density and motion from `_intensity()` + build_progress, take tension
   out of the level, and give the look engine the composer's plan.
8. **Late sections land, not vanish.** Land a late confirmed section on the next 4-bar or phrase line. Map structure's
   chorus letter to `section_kind` so the specific moves wake up.
9. **Reactive without a beat.** Bass hits at gate `none`, rate-capped. Restrict hit bands to sub/bass/low-mid.
10. **Clock steadiness.** Hold the octave once chosen. Cap the slew near 4%. Use a meter prior from the first votes.

**Screens:**
11. **Bind the structure.** Add mapgen rules that bind downbeat kicks, bar- and phrase-phase steps (camera, palette,
    symmetry) and build_progress, not only `pulse = beat × punch`.
12. **Announce in time.** Publish the renderer's lead on the bus, and have show_service announce at least lead + 0.3 s
    ahead.
13. **Trust measured stats over guessed tags.** `STYLE_POWER` to ~1.5 until the tags are checked. Re-measure
    look_stats for the 260 unmeasured presets.
14. **One layer at a time.** The FX director treats `look_change` as a scene change, holds about 2 bars after a cut,
    and ramps with builds. Longer transitions are quantised to bars, and drops and big sections may draw a cut.

**Calibration:**
15. Measure DMX latency, the north TV and the acoustic leg (`sources`) once, so "early by 10 ms" is a measurement.

**Order:** the bench and corpus (§3), then 1, 2, 5 and 3 against it before 5 Dec, then 6–8, then the rest. Each fix
is judged by the scorecard moving, and by the owner's A/B.
