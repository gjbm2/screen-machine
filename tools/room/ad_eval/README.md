# ad_eval: score the art director against the songs themselves

A fixed set of 20 karaoke songs (`songs.yaml`), plus any song an owner note names, runs through the real show code on simulated time, in two modes. `karaoke` knows the song, as the karaoke player gives it. `live` hears the music cold. Each run is scored against an answer key made from the whole file with hindsight, and the same code always gives the same scores.

    ~/.venvs/sm-show/bin/python tools/room/ad_eval/run.py            # everything, nice 19, 4 jobs
    ~/.venvs/sm-show/bin/python tools/room/ad_eval/run.py --songs k94db54a10b --modes karaoke

- `reference.py <id>` writes the answer key to `~/.cache/sm-ad-eval/ref/<id>.json`: beats, downbeats, sections, energy, climax, last hit and end. It uses its own models, never the show's.
- `replay.py <id> --mode M` runs one song through dsp, clock, ML lines (`ml_prepass.py`, cached), structure, cues, director and the look engine, and writes a timeline.
- `score.py` holds the metrics: beat, snap (how far the lights lag), contour, sections, climax, ending and coherence. `overall` is their weighted mean. Weights and tolerances are in `weights.yaml`; they are starting points.
- Runs go to `~/.cache/sm-ad-eval/runs/<UTC stamp>-<sha>/`, holding `scorecard.json` and `<id>.<mode>.json`. `runs/history.jsonl` lists every run. Each run prints its diff against the previous run and the best one, with regressions marked `!`.
- Owner notes (`~/.local/state/screen-machine/show/ad_feedback.jsonl`) become `cases` in the scorecard. A `good` case must not fall; a `bad` case should rise.

What's stubbed:
- the LLM listener;
- the room (no screens reported live);
- the renderer (its input is scored, not its own lag);
- AI calls;
- ML costs, which are fixed per task.

The mic hears the clean mix. There is no room model yet, so `--gain-db` is the only degradation.
