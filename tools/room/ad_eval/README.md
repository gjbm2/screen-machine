# ad_eval: score the art director against the songs themselves

The design, the reasons for it, where it stands and the shared workplan are in [docs/AD_EVAL.md](../../../docs/AD_EVAL.md).

A fixed set of 20 karaoke songs (`songs.yaml`), plus any song an owner note names, runs through the real show code on simulated time, in two modes. `karaoke` knows the song, as the karaoke player gives it. `live` hears the music cold. Each run is scored against an answer key made from the whole file with hindsight, and the same code always gives the same scores.

    ~/.venvs/sm-show/bin/python tools/room/ad_eval/run.py            # everything, nice 19, 4 jobs
    ~/.venvs/sm-show/bin/python tools/room/ad_eval/run.py --songs k94db54a10b --modes karaoke

- `reference.py <id>` writes the answer key to `~/.cache/sm-ad-eval/ref/<id>.json`: beats, downbeats, sections, energy, climax, last hit and end. It uses its own models, never the show's.
- `replay.py <id> --mode M` runs one song through dsp, clock, ML lines (`ml_prepass.py`, cached), structure, cues, director and the look engine, and writes a timeline.
- `score.py` holds the metrics. `overall` = `goals` x `gate`. The goals: beat, contour, sections, ending, and what the lights showed against the song's shape (`form.py`: form, contrast, proportion, arc, peak, release). The gate is restraint (`design.py`), a limit and not a goal. Weights and tolerances are in `weights.yaml`; they are starting points. `SCORE_VERSION` changes when `overall` means something else, and runs are compared only within one version.
- `form.py` reads what the lights showed, from the look engine's frames (`replay.py` records them 20 times a second): how many lights are on, how the heads move, flicker, colour. Of the whole song set it asks whether songs that sound different got shows that look different (`character`).
- `controls.py` tests the score itself on every run: the real show against deliberately wrong ones (the wrong song, late, backwards, its looks dealt to the wrong sections, one static look). `scorecard.json` `controls.pass` says whether the score can tell them apart; when it can't, don't steer by it. It also scores a loudness box (brightness = loudness, one scene, one colour): the mark a show should beat.
- `--seeds N` (default 3) plays each song N times with other draws of the director's dice (`SM_AD_EVAL_SEED` in `replay.py`) and scores the mean. The same code and seed give the same scores, but a change that alters how many draws are made reshuffles every draw after it. One draw's arc and character moved ±0.09 between seeds, and one song's overall by 0.25. So a one-seed difference smaller than that is a reshuffle, not a fix. Each song reports `overall_spread` across its draws. A song is flagged as regressed only past that spread, and runs with different seed counts are marked as not like for like. The controls use the first draw.
- `run.py --rescore <run dir>` scores a run's timelines again after a change to the scorer, without replaying.
- Runs go to `~/.cache/sm-ad-eval/runs/<UTC stamp>-<sha>/`, holding `scorecard.json` and `<id>.<mode>.json`. `runs/history.jsonl` lists every run. Each run prints its diff against the previous run and the best one, with regressions marked `!`.
- Owner notes (`~/.local/state/screen-machine/show/ad_feedback.jsonl`) become `cases` in the scorecard. A `good` case must not fall; a `bad` case should rise.

What's stubbed:
- the LLM listener;
- the room (no screens reported live);
- the renderer (its input is scored, not its own lag);
- AI calls;
- ML costs, which are fixed per task.

The mic hears the clean mix. There is no room model yet, so `--gain-db` is the only degradation.
