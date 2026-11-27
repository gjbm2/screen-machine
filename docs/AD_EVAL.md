# The art director's eval: architecture, approach and workplan

The owner's question (27 Nov 2026): *how can we be confident that a high score means lighting that is artistically
successful, and that properly feels the music: its narrative structure as well as its beats?* This document is the
answer so far: how the eval is built, why it is built that way, where it stands, and who does what next.

The code is in `tools/room/ad_eval/`. Its README covers commands and files; this document covers the design. The
overnight loop that steers by the eval is `.claude/skills/ad-improve` (run by `scripts/ad-nightly`). Owner verdicts on
a song come in through `.claude/skills/ad-review`.

## 1. Architecture

```
karaoke library (~/.local/state/screen-machine/karaoke)
   │
   ├─ reference.py ── the answer key, per song (ref/<id>.v4.json)
   │     beats, downbeats, energy, climax, last hit, end      (its own models, never the show's)
   │     sections from keys.yaml                               (annotated by hand against lyrics and stems; key_rev)
   │     sim: which sections sound alike (audio + lyrics)
   │     character: bpm, punch, range, brightness, low end, liveliness 0..4
   │
   ├─ replay.py ───── the real show code on simulated time, one song
   │     dsp → clock → ML lines (cached) → structure → cues → director → look engine
   │     modes: karaoke (the song is known, as the player gives it; its brief.json is read)
   │            live    (heard cold: no brief, no plan)
   │     SM_AD_EVAL_SEED: another draw of the director's dice
   │     writes a timeline: decisions, lamp/screen outputs, and what the lights showed (20 frames a second)
   │
   ├─ score.py ────── one timeline against its key
   │     overall = goals × gate                                 (SCORE_VERSION 2; weights.yaml)
   │     goals: beat, contour, sections, ending, and form.py's form, contrast, proportion, arc, peak, release
   │     gate:  design.py's restraint limits: palette, kit, flourish, stillness (meeting a limit earns 1, no more)
   │     set:   character, across the whole song set (do songs that sound different look different?)
   │
   ├─ controls.py ─── does the score work at all? (every run)
   │     the real show must beat: the wrong song, 8 bars late, backwards, its looks dealt to the wrong sections,
   │     one static look; by ≥ 0.05 on average and in more songs than chance (sign test p ≤ 0.05) → controls.pass
   │     benchmarks, reported only: the loudness box (brightness = loudness), beats_box (a show built from the key)
   │
   └─ run.py ──────── the song set (songs.yaml + every song an owner note names) × modes × seeds (default 3)
         scorecard.json, history.jsonl, the diff against the previous and the best run
         owner notes (ad_feedback.jsonl) → cases: a good one must not fall, a bad one should rise

scripts/ad-nightly (cron 03:00) → scores a clean copy of HEAD → ad-improve: one fix per attempt, kept only if it
beats the seed spread and every owner verdict holds → morning report (~/.cache/sm-ad-eval/reports/<date>.md)
```

### What the goals measure

| goal | the question it asks | how |
|---|---|---|
| beat | do the pulses land on the beat? | pulses within 70 ms of a key beat |
| contour | does the brightness follow the song's loudness over time? | correlation of smoothed curves |
| sections | does the look change where the song changes, and not elsewhere? | hits on key boundaries, less strays |
| form | do sections that sound alike look alike? (the chorus comes back as the chorus) | Spearman: sound-alike vs look-alike over section pairs |
| contrast | do unlike sections look unlike, in more than one way? | the second-largest gap of five families: brightness, extent, movement, colour, texture |
| proportion | is the change at a boundary as big as the change in the music? | Spearman over boundaries |
| arc | does each section's size follow its energy across the song? | Spearman over sections |
| peak | is the climax the biggest look? | share of other sections smaller |
| release | does a lift land: size rising across the line where the music arrives? | key arrivals |
| ending | do the lights go dark when the song ends? | error in seconds |

The gate keeps restraint, the house style: one palette per song, a small kit of scenes per light group, few flourishes,
heads still for most of a quiet song (by its liveliness). It caps; it never rewards more restraint than the limit, or
a static look would win.

## 2. Approach: what makes the score worth trusting

1. **Test the score before steering by it.** Each run checks it on known answers: if the score can't tell the real
   show from a wrong one, nobody steers by it that night. The first scorer failed this: the real show and a shuffled
   one both scored 0.612, and the loudness box 0.904. It was rebuilt (v2) around form and the controls.
2. **The answer key is annotated, not guessed.** Machine novelty sections were wrong for most songs. keys.yaml holds
   sections and liveliness checked against lyrics and stems. A miss is checked against the key before the show is
   changed to match it.
3. **Goals and limits are different things.** Feeling the music is a goal (more is better); restraint is a limit (meet it
   and stop). Multiplying them stops a busy show and a dead one from both scoring well.
4. **Judge over several draws.** The director draws at random, from fixed seeds. A change that alters how many draws
   are made reshuffles every draw after it. One draw's arc and character moved ±0.09 between seeds of the same code,
   and one song's overall by 0.25. So every run averages 3 draws per song, reports each song's `overall_spread`, and a
   fix is kept only if its gain beats that spread.
5. **Benchmarks say where the headroom is.** The loudness box is the bar a show should clear. Where it wins, the show is
   missing something plain (it tracks loudness by construction). Where the show wins, the show has design the box
   can't have.
6. **Score HEAD, never a working tree.** Other sessions edit in place and the replay imports what is on disk, so the
   nightly scores a clean copy of HEAD.
7. **The owner's eye is final.** Owner verdicts become cases that must hold. The next step (§4, step 1) asks the owner to
   choose between blind pairs, to check that a higher score is a better show.
8. **Never game a metric.** A fix must be one a lighting designer would make, and must agree with
   `docs/LIGHT_DESIGN_NOTES.md`.

### The show side the eval judges

The karaoke song's **brief** (show_service/brief.py, `brief.json` beside each song, made by AI when the song is first
analysed) gives the song's idiom (hush, ballad, story, dark, anthem, groove, disco, dance: config/live/idioms.yaml),
palette, moods and a map of its sections, each with a size and how it arrives. The director performs it:
- the idiom limits what may be drawn (motion, pulse, groups, transitions);
- the map sets each section's level, and its lifts get a wind-up and landing (show_service/phrasing.py);
- each section kind has its own look (the look book), recalled when it returns, growing only in size;
- the last chorus is the chorus at its biggest.

With no brief (live music, a stream), the director falls back to what it hears: a lift is a predicted section during a
heard build (`_arrive_heard`). docs/LIGHTS.md §5.11 has the detail.

## 3. Where it stands (27 Nov 2026, karaoke mode, 21 songs, mean of 4 draws)

|  | overall | form | contrast | proportion | arc | peak | release | ending | contour | sections |
|---|---|---|---|---|---|---|---|---|---|---|
| the show | 0.455 | 0.28 | 0.60 | 0.22 | 0.39 | 0.65 | 0.52 | 0.40 | 0.51 | 0.42 |
| loudness box | 0.579 | 0.30 | 0.15 | 0.65 | 0.78 | 0.39 | 0.22 | 0.24 | 1.00 | 0.89 |

Character across the set is 0.35. The controls pass, and the real show beats every wrong show by about 0.2 to 0.35.

What it says:
- **The show wins on design.** Its contrast, peak, release and ending beat the box by a wide margin. The brief (+0.06
  overall when it arrived) and the look book (form +0.05, contrast +0.07) are why.
- **The show loses on shape.** Size doesn't follow energy across the song (arc 0.39 against 0.78), and the change at a
  boundary isn't proportional to the music's (0.22 against 0.65). Part of the box's lead is by construction (contour
  and sections reward following loudness), but arc and proportion are real misses.
- **Songs of different kinds still look too alike** (character 0.35). Quiet songs' heads still move about 8°/s.
- **Live mode** (no brief) has not been measured since the brief went in.

## 4. Workplan

One list for every session working on the a.d. and its eval. Owners: **bb** owns the eval's scorer and the
song brief; **f2** owns the overnight loop (ad-nightly, ad-improve) and the director fixes it makes. A finished item is
struck through with its commit.

### bb: the scorer, the brief, the a.d.'s narrative
1. **Blind pairs for the owner's eye.** A page showing two versions of the same moment side by side (the 3D room,
   from two replays: two commits, or the show against a control). The owner picks the better one, or "same". Each pick
   is saved, and the eval reports how often the score agrees with the owner. This is what answers the owner's question.
   Until it agrees on most pairs, the score is a guide, not a judge.
   **Built (27 Nov):** `run.py --dmx` records each replay's DMX. `pairs.py make <run> [--vs <run>]` cuts 18 s around
   a lift where the two sides look most different, puts them on random sides, and keeps the key hidden.
   show_service/ad_pairs.py serves the batch and keeps the picks. The page is `/lights/pairs`: ← Left, ↓ Same,
   → Right, Space plays the moment again. `pairs.py report` gives, for each measure, how often the side the owner
   picked scores higher. First batch `draws-1`: 12 pairs of two draws of the same code, overall gaps of 0.03 to 0.27.
   Still to do: the owner picks; then read the report, re-weight or drop the measures the eye disagrees with
   (item 5), and make the next batches (`--vs` across commits, briefs on and off).
2. **Arc and proportion.** Find why a section's size doesn't follow its energy when the brief sets its level: line-ups,
   level, movement, or the measure itself (the box wins arc with brightness alone). Then fix the show or the measure.
3. **Character.** Make songs of different kinds look different: why quiet songs' heads move (motion_cap, drifts, moves
   across changes), and whether the measure is fair to the idioms.
4. **Live mode.** Score `--modes live` with briefs off. Check that the fallback to heard cues lands lifts, and that a
   song with no brief isn't worse than before the brief went in.
5. **Look again at contour and sections** once the blind pairs have data: do they reward artistry, or only following
   loudness? A change bumps SCORE_VERSION.
6. **Eval issues from the nightly.** Fix the keys and references the morning report flags.

### f2: the overnight loop
- Run nightly at 03:00: score HEAD (3 draws a song), no show changes while `controls.pass` is false, one fix per
  attempt, kept only past the seed spread with every owner verdict holding, then the morning report.
- Flag suspect keys and references to bb under "eval issues" (it never edits `tools/room/ad_eval/` itself).
- Its picks follow the weakest dimension. Today that is form, proportion and arc, which overlap bb's items 2 and 3. So
  before working on one, check this list and tell bb, so two sessions don't fix the same thing two ways.

- **How long it takes now:**
  - A full run (21 songs × 2 modes × 3 draws, scored in a clean copy of HEAD at `~/.cache/sm-ad-eval/tree`) takes
    about 30 min.
  - 03:00–06:30 then holds the first score plus about 5 attempts.
  - When time is tight, an attempt is checked on its own 2–3 songs first.
- **Not run for real yet.** The first night is 28 Nov. It is a headless Claude, `acceptEdits` + Bash, run from cron
  (WSL here has no user systemd bus). The log is `~/.cache/sm-ad-eval/nightly.log`, and reports are in
  `~/.cache/sm-ad-eval/reports/`.
- **A kept fix** goes into the real tree as a patch. If another session has changed the file meanwhile, it is left
  out and reported, not forced.
- **f2's own director items, not started** (docs/AD_MUSIC_FIT_REVIEW_2026-11.md §4):
  - live-mic accents from the measured groove, not the metronome (review item 6);
  - clock steadiness: hold the octave once chosen, cap the slew (item 10);
  - an octave check on the karaoke file grid (k2b55b24fd2 reads 176 bpm in 3/4).
- **The owner's verdict cases** (Common People's dark verse, Don't Stop Me Now's late new-song look) are checked in
  every morning report.

### The owner
- Blind pairs once they're up (a few minutes at a time).
- Verdicts on songs through ad-review; tuning by eye in the room.

### The rules between us
- A scorer change that alters what `overall` means bumps `SCORE_VERSION`, and bb tells f2 in the same hour.
- A change to the eval's defaults (songs, seeds, modes) is told to f2, who adjusts the loop's time budget.
- Each session reports only its own work to the owner.
