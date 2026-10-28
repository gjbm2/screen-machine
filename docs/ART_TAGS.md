# Art tags

Owner, 27 Oct 2026: every picture saved to a bucket gets a few tags from an AI pass, so the library can be browsed by
tag and Ambient can pick art that fits the music. Tagging never holds up generation or display.

## What

- **Lexicon:** `config/art_tags.yaml` (hot-reloaded). It holds the facets, the 10 music groups, the rubric (the system
  prompt) and the tagger settings.
- **Shown as lozenges:** subject (1-2), medium (1) and moods (1-3).
- **Kept but not shown:** palette, `look` (a short description), energy, valence, organic, density, quality,
  confidence, a 0..1 fit for each music group, and `auto_ok`.
- **Favourites** stay in `bucket.json` `favorites`. The UI shows them as a ★ tag and calls the favourite endpoint. The
  tag routes ignore the words "favourite" and "favorite".

## Where it is stored

Tags go in the picture's sidecar, `output/<bucket>/<file>.<ext>.json`, as two keys: `tags` and `tagging` (`status`,
`ai`, `added`, `removed`, `model`, `cost_usd`, ...). Every other key is left as it was.

- **`tags`** is the effective list: the AI's subject, medium and moods, then the words a person added, minus the
  words they removed.
- **Writes** are atomic (a temp file, then `os.replace`) and use a per-file lock.
- **Copies:** a copy to another bucket carries its tags.
- **Rebuilds:** `extractjson` and `reindex?rebuild_all_sidecars=true` keep `tags` and `tagging`.

## The queue

The queue is code in `routes/art_tags/`: one daemon thread inside Flask at nice 10, one picture at a time, at least
`min_interval_s` apart.

- **New saves:** `_append_to_bucket` and a copy between buckets put the new file at the front.
- **`_recent`** (the generator's scratch area) is never tagged.
- **Sweep:** 60 s after Flask starts, then every `sweep_every_s` (15 min). It queues every file named in a bucket's
  sequence that has no result yet, so it also does the one-time historic backfill.
  - Order: north-screen, south-screen, devtest, then the other buckets A to Z.
  - Within a bucket: favourites first, then newest first.
- **What it sends:** a 768 px JPEG (the middle frame for a video), plus the sidecar's `original_prompt`, `prompt` and
  `refined_prompt` (each once, cut to 700 characters) and the bucket name.
- **Model:** `config/llm.yaml` task `tag`, which is Gemini 3.1 Flash-Lite only. It never falls back to OpenAI.

**Failures:**

| What happened | What the queue does |
|---|---|
| Timeout, 429, 5xx or a dropped connection | Backs off 30 s, doubling up to 30 min. The picture stays queued. |
| Out of credit (402, billing), or no key or model | Pauses 1 h and sends one alert (`art_tags.quota` / `art_tags.error`). |
| A refusal, an unreadable picture or an unusable answer | Counts one attempt. After 3, it sets `tagging.status: failed` with `error` and leaves the picture alone. |
| Over the day's budget | Waits for local midnight. |

## Costs

About $0.00125 a picture (measured on 27 Oct: ~3,900 prompt tokens and ~175 answer tokens).

- The backfill of ~2,000 pictures is about $2.50.
- The budget is `llm.yaml` `budgets.tag` = $3.00 a day.
- Each call is a row in the utils.llm ledger with the task `tag`.
- Gemini prices double on 1 Jan 2027 (see `llm.yaml`).

## API (under `/api`, open like the other bucket routes)

| Route | Does |
|---|---|
| `GET /art-tags/lexicon` | `{version, facets: {subject, medium, moods, palette: [{word, desc}]}}` |
| `GET /art-tags/status` | `{running, queued, current, done_session, failed_session, paused_until, pause_reason, waiting_until, last_error, spend_today_usd, model, coverage: {bucket: {total, tagged, failed}}}`. Coverage is cached 60 s. |
| `POST /art-tags/sweep` | Sweeps now. Returns `{queued: n}`. |
| `POST /buckets/<b>/tags/<file>` | Takes `{"add": [...], "remove": [...]}` and returns `{tags, tagging}`. |
| `POST /buckets/<b>/tags/<file>/retag` | Puts the file at the front for a fresh AI pass (a person's edits are kept). Returns `{queued}`. |

How edits work:

- Adding a removed word brings it back. Any other new word goes into `added`.
- Removing an AI word puts it in `removed`. Removing an added word drops it.
- Words are lower-case, with spaces turned into hyphens, at most 32 characters.

`GET /buckets/<b>/complete` already returns each file's `metadata.tags` and `metadata.tagging`.

## CLI (calibration; run from the repo root with Flask's venv)

```
.venv/bin/python -m routes.art_tags.cli tag north-screen <file>                 # prints the cleaned answer, writes nothing
.venv/bin/python -m routes.art_tags.cli tag north-screen <file> --model gemini-3.8-flash --prompt
.venv/bin/python -m routes.art_tags.cli tag north-screen <file> --write         # also writes the sidecar
.venv/bin/python -m routes.art_tags.cli sweep --dry                             # counts per bucket
```

Each `tag` is one paid call. `--model` calls only that model (`utils.llm.call_json(model=)`), still billed to `tag`.

## How the renderer uses it

The gallery decode worker reads the sidecar's `tags` and `tagging.ai`, plus the `bucket.json` favourites. It never
reads the prompt.

- **Matching facets:**
  - subject and moods are the AI's words minus `removed`, plus any `added` word that is a lexicon word of that facet;
  - the numbers and music fits are used as given.
- **`auto_ok: false`** is never picked automatically unless the picture is a favourite.
- **Untagged pictures** are still eligible, at a neutral score.

## The art director makes pictures when the library is thin

Owner, 28 Oct: "connect listening AI with artistic director with AI image generator". Code:
`routes/art_tags/director.py`, a daemon thread inside Flask started next to the tag queue. Settings: the `director:`
block in `config/art_tags.yaml` (read each pass).

Every `interval_s` (60 s), one decision:

1. **Is the drawing room in Ambient?** It reads room_service `GET :8700/api/v1/status` `mode.current`. Any other
   mode: nothing.
2. **Is there an art wish?** It reads show_service `GET :8703/api/v1/look` `direction.art`. If there is none, or it
   is `via: none`: nothing.
3. **Is a screen thin?** For each of the room's screens (`routes/data/rooms.json`) it counts the **strong matches**
   among the gallery's pictures in the screen's bucket (DATE-HEX stills whose sidecar has a prompt and a workflow).
   A strong match is tagged (`status: done`, `auto_ok: true`), has a wished subject and no avoided one, and has a
   wished mood **or** a music fit of 0.7 or more for the genre heard. Fewer than `thin` (24) means thin.
4. **One picture** for one thin screen, if the caps allow:
   - caps: `enabled`, `per_day` (12 tries a local day; a restart reads them back from the log), and
     `per_screen_min_gap_s` (10 min); two thin screens take turns;
   - one `utils.llm` call (task `prompt`) writes the image prompt from the wish's theme, subjects, moods and media, plus
     the listener's `character` (`GET :8703/api/v1/listening`). House style: 16:9 wall art, no text or logos;
   - the generation goes through the scheduler's `publish: false` path: the generation service with no target (a
     tracker job with source `art-director` and no screen, so the TV shows no job indicator), then
     `publish_to_destination(..., update_published=False)`. The refiner and workflow are the ones the screen's own
     schedule uses (`routes/scheduler/<screen>.json`, today `enrich`);
   - the picture lands in `output/<screen>/` only. It is **never shown on the wall**. The save queues its tagging,
     and the renderer's gallery re-indexes within a minute, so it joins Ambient's pool;
   - a failure backs off 10 min, doubling to an hour.

Cost: each picture is one generation (about £0.01) plus one prompt call (about $0.0006).

- **Status:** `GET /api/art-tags/director` returns `{enabled, running, mode, wish, genre, action, per_screen: {<screen>:
  {strong, pool, tagged, thin, last_at}}, making, today_count, per_day, backoff_until, last_prompt, last_error}`.
- **Log:** one JSON line per decision in `~/.local/state/screen-machine/logs/art_director.jsonl`. `action` is one of
  `off`, `not-ambient`, `no-wish`, `enough`, `waiting`, `generated` or `failed`.
- **Dry run** (the live GETs only; no prompt call, no generation):
  `.venv/bin/python -m routes.art_tags.cli director --dry`.

## Tests

```
.venv/bin/python -m pytest routes/art_tags/tests -p no:cacheprovider
```
