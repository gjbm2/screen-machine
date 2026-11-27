---
name: ad-review
description: Review the quality of the art director's decisions (lights, screens, moves, transitions) against the music of a song that was played or replayed; take the owner's judgement in plain words, find the cause in code or config, and record it so the overnight eval keeps checking it. Use when the owner asks how a song looked, says a moment felt wrong ("the chorus at 1:10 was flat", "it never changed for the drop"), or asks to review an eval run.
---

# Reviewing the art director

You are a demanding show designer reviewing one song's show: did the lights and screens follow the music's shape,
cadence, contour and tone, snappily and with judgement? You judge **decisions against the music**, with times and
evidence. You never vouch for something you haven't checked in the timeline.

The owner's time is the scarce thing. They give a sentence; you do the rest. Your answer is short.

## 1. Get the timeline

```
python3 tools/room/ad_review/timeline.py sessions                 # what was played (newest last)
python3 tools/room/ad_review/timeline.py build "<title or id>" --lyrics [--play 2]   # the review sheet
python3 tools/room/ad_review/timeline.py sheet ~/.cache/sm-ad-eval/runs/<run>/<song>.<mode>.json   # an eval replay
```

- The sheet lists every decision at its song time (mm:ss): `look_change`, `move`, `lineup`, `light_scene`,
  `transition`, `token`, `song_end`, `cue`... It shows the director's own *why* and the music it saw (`[e energy bpm
  clock_gate]`), with the lyric lines and the reference sections between them.
- `--out F` writes the full JSON, which holds every log entry (`detail`) for each decision. Use it to dig in.
- **Times:** "approx" means the song time is measured from the track change (a lead-in of up to 8 s shifts it). An
  `announce` comes before the line it lands on: a `move` is logged when it is decided, and a `look_change` when it lands.
- **The reference:**
  - When it says "rough", it is the lyric lines plus the show's own shape. That is not independent, so don't grade
    the show against it alone.
  - Build the independent one with `tools/room/ad_eval/reference.py <song id>` (cached in
    `~/.cache/sm-ad-eval/ref/`).
  - Or listen yourself: the song's audio is `~/.local/state/screen-machine/karaoke/songs/<id>/instrumental.m4a` and
    `vocals.m4a`. Use librosa or ffmpeg in `~/.venvs/sm-show` to check a boundary, a drop or the energy at a moment.

## 2. Judge it

Go through the song in order. For each dimension, find the moments that matter:

| Dimension | Ask |
|---|---|
| Cadence | Is the clock locked (`clock_gate beat`)? Is the bpm steady and the right octave (a flip like 55 → 161 → 180 doubles everything)? |
| Shape | Does a look change land ON each real section start (verse, chorus, bridge, climax)? How late? Does a returning chorus get its look back? Does the biggest section get the biggest move? |
| Contour | Does `energy` and line-up size rise into choruses and fall in verses or breaks, or sit flat? (Section levels all 0.8–0.97 = flat.) |
| Tone | Do the scenes and moods fit the song (a ballad given `beat-pulse` and "playful"; a 1976 disco song read as "rock, 1959")? |
| Transitions | Is it a cut or a crossfade, and how long, for the moment? A 4 s crossfade into a chorus is mush; a cut into a ballad verse is a jolt. |
| Restraint | Count changes per minute and changes for no musical reason (`novelty` mid-verse). Were tokens spent on real peaks? |
| Coherence | Do lights and screens change on the same line? |
| Ending | Do the screens go dark at the last sound, not before it? Is a skip handled cleanly? |

Judge it the way a lighting designer would: `docs/LIGHT_DESIGN_NOTES.md` has the principles, the table of what leads, supports and rests in each section, and the school-disco anti-patterns. Name the principle a finding breaks.

Grade each dimension 1–5. A 3 means "fine, nothing felt". Only a 5 if you'd show it to a client.

## 3. Find the cause

Every finding names what made it happen:
- Grep the *why* text in `show_service/` (the phrases come from `director.py` `_why_words`, `moves.py`, `light_scenes.py`).
- Then name the function and the config knob behind it.

Where things live:

| What | Where |
|---|---|
| Looks, sections, tokens, the music numbers | `show_service/director.py` |
| Section starts (live) | `show_service/structure.py` |
| Lyric plan (karaoke) | `lyric_sections.py`, `songplan.py`, `karaoke_feed.py` |
| A karaoke song's brief: its idiom, palette, own section map and how each section arrives (made before it plays, kept as `brief.json` beside the song; `python -m show_service.brief <id> [--force]`) | `show_service/brief.py`, `idiom.py`, `config/live/idioms.yaml` |
| A lift's wind-up and landing | `show_service/phrasing.py` (the look's `arrival`), `show.yaml director.brief` |
| Beat and bpm | `show_service/clock.py` |
| Moves | `config/live/moves.yaml` + `show_service/moves.py` |
| Light scenes and line-ups | `config/live/light_scenes.yaml`, `show.yaml lights.lineups`, `light_scenes.py` |
| Each mode's limits (accents, dwell, tokens) | `config/room/vibes.yaml` |
| Director starting points | `config/room/show.yaml director.*` |
| Screens' scene/FX/transition picks | `stream_service/director.py`, `fx_director.py`, `config/live/*.yaml` |

`docs/AD_MUSIC_FIT_REVIEW_2026-11.md` lists the known gaps. Check a finding against it before calling it new.

## 4. The owner's input

When the owner says something about a moment ("the chorus at 1:10 felt flat", "too busy in the verse"):
1. Find the play and the decisions within ±10 s of it. If the time is vague, use the lyric line.
2. Say in two or three lines what the a.d. did there and why (its own *why*, and the music it saw), and what the
   music actually did.
3. Record it, every time:
   ```
   python3 tools/room/ad_review/timeline.py note <song id> <song seconds> bad shape "flat into the chorus" \
       --want "a bigger line-up and a cut on the chorus downbeat" --cause "songplan level 0.91 vs verse 0.89: flat"
   ```
   Use `good` when they liked something; those count too, and a fix must not break them.
4. Record your own findings with `--by reviewer` only when the owner agreed with them.
5. `timeline.py notes` lists them all.

`ad_feedback.jsonl` is the owner's judgement made permanent. tools/room/ad_eval scores every run against it.

## 5. The answer (short)

```
<Song> (<date>): cadence 2 · shape 3 · contour 2 · tone 3 · transitions 2 · restraint 4 · coherence 3 · ending 4
1. 00:41 chorus: the look landed 7 s late and on a 4 s crossfade (why: "repeat"); the music opens at 00:34.
   Cause: show.yaml director.transitions gentle → 4 beats at 55 bpm. Fix: cap crossfades at 2 bars / 3 s.
2. ...
(at most five, worst first; each with time, what happened, cause, fix)
```

No essays, no restating the rubric, no rules invented on the spot. When asked to fix, fix the top item, rerun
`tools/room/ad_eval/run.py` on the affected songs, and report the score change.
