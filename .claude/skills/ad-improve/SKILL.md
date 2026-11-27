---
name: ad-improve
description: The overnight improvement loop for the art director. Score the show on the fixed song set, find the worst-scoring dimension, review the songs behind it, make one fix, re-score, and keep the fix only if the scores and the owner's recorded verdicts say it's better. Run by scripts/ad-nightly (a systemd user timer), or by hand when asked to "improve the a.d." or "run the overnight loop".
---

# The overnight loop

The goal: the numbers in `tools/room/ad_eval` go up night after night, and the owner's recorded verdicts
(`~/.local/state/screen-machine/show/ad_feedback.jsonl`) keep holding. Short, careful steps. One idea per attempt.

## Each attempt

1. **Score.** `nice -n 19 ~/.venvs/sm-show/bin/python tools/room/ad_eval/run.py` (the fixed song set, both modes).
   - If HEAD already has a run today, use it.
   - The diff against the best run is the starting point.
2. **Pick.**
   - First, any owner verdict (`timeline.py notes`) that still fails.
   - Else, the dimension with the lowest aggregate score.
   - Then the 2–3 songs worst on it.
3. **Review.** Use the ad-review skill on those songs' replay timelines (`timeline.py sheet <run>/<song>.<mode>.json
   --lyrics`) and find the one cause they share. Check it against `docs/AD_MUSIC_FIT_REVIEW_2026-11.md`.
   **The answer key is a machine's, and it can be wrong.** The section boundaries come from novelty and are
   labelled by the lyrics. Before chasing a miss, check that the reference boundary is real: is there an energy or
   timbre change there, or a lyric section start? Is a show section that the reference lacks actually right (a chorus
   the novelty missed)? Never change the show to match a reference error. List suspect references in the morning
   report under "eval issues", so the eval gets fixed by hand.
4. **Fix one thing.** Make the smallest change that removes the cause: a config value, a rule, a missing signal.
   - **Never** a change that only games a metric (e.g. changing looks at every possible line to raise section
     hits). Ask whether a show designer would make the change.
   - **May edit:** `show_service/`, `stream_service/`, `config/live/`, `config/room/show.yaml`, `config/room/vibes.yaml`,
     `utils/song_shape.py`, and tests.
   - **Never edit:** the eval itself (`tools/room/ad_eval/`), the feedback file, CLAUDE.md, auth, ports, deployments' site
     facts.
   - A fix must agree with `docs/LIGHT_DESIGN_NOTES.md`. For example: one palette of 2–3 colours per song; palette, positions and gobo held through a song while level, groups and speed escalate; the full rig kept for the high point; derby, laser and ball as a rare layer.
   - Keep to a file's own house rules: its header, and the tests that hold it. For example, `light_scenes.yaml`
     keeps gobos to a few slow scenes (test_the_real_scenes_use_gobos_with_restraint). Light effects stay sparse
     across the room (director FX_PER_MOMENT and FX_ROOM_MAX): restraint is the owner's, not a metric to beat.
   - Run the touched module's tests (`~/.venvs/sm-show/bin/python -m pytest -q show_service/tests/test_<x>.py`).
5. **Re-score** on the full set.
   - **Keep the fix** if the overall score rises, no dimension falls by more than 0.02, every `good` verdict still
     holds, and no `bad` verdict that passed before now fails.
   - **Else undo exactly your own change** (`git diff -- <your files> > /tmp/attempt.patch; git apply -R
     /tmp/attempt.patch`). Never stash, checkout, reset or clean.
6. **Kept:**
   - Commit only your own paths, with a message saying the metric change: "ad-improve: <what> (overall 0.61 → 0.64;
     sections 0.42 → 0.55)".
   - The show picks it up at its next restart.
   - Restart it yourself only between 02:00 and 06:00, and only when nothing plays: GET
     `http://127.0.0.1:5000/api/karaoke/now` is not playing and GET `http://127.0.0.1:8700/api/v1/status` has
     `mode.transition` null. Then run `scripts/ctl restart show`.
   - Restart stream the same way, only when no screen is live.
   - Never restart Flask.

Stop when the time budget (`AD_IMPROVE_UNTIL`, default 06:30) is reached, after 6 attempts, or after 3 attempts in a
row are undone.

## The morning report

Write `~/.cache/sm-ad-eval/reports/<date>.md`, at most 15 lines:
- the score card (yesterday's best → today's), per dimension;
- each attempt in one line (kept or undone, why);
- what the owner could check by eye in one song, if anything (one line, with the song and time);
- eval issues: references that looked wrong (song, time, why).

Plain words. No essays.
