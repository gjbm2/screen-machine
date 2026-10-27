# Jobs: the generation job tracker

Every generation job (image or video, from the generator page, the scheduler, Alexa or the API) is a
**job** in one tracker, from the moment it's asked for until it finishes. The tracker is the only
record of job state. Pages subscribe to it; nothing scrapes overlay HTML any more.

- Backend: `routes/jobs/` (tracker, store, RunPod progress, HTTP + SSE API). It runs inside Flask.
- History: SQLite at `~/.local/state/screen-machine/jobs.db` (override with `JOBS_DB`); kept 90 days.
- Frontend: `src/shared/api/jobs.ts` (types + REST + stream) and `src/shared/jobs/store.ts` (one shared
  stream per page, read with `useJobs()`).
- Shown on: the generator page (bottom-left jobs dock), each screen's `/display/<screen>` page (a small
  pill top-left while a job renders to it), and the Jobs page `/jobs` (running now + history).

## Job

One job = one output image or video for one target. A request for 2 images on 2 screens is 4 jobs
sharing a `batch_id`.

| field | type | meaning |
|---|---|---|
| `id` | string | `job_<12 hex>`, made when the job is asked for (before prompt refining) |
| `batch_id` | string \| null | the request it belongs to (the generator page's batch id, or a new one) |
| `batch_index` | number \| null | index within the request |
| `placeholder_id` | string \| null | the generator page's placeholder for this image, if it sent one |
| `source` | `generator` \| `scheduler` \| `alexa` \| `api` | who asked |
| `kind` | `image` \| `video` | from the workflow |
| `target` | string \| null | publish destination id (`north-screen`…); null = not published |
| `target_name` | string \| null | its display name |
| `prompt` | string | the prompt as asked |
| `refined_prompt` | string \| null | the prompt actually sent, after refining |
| `workflow` | string \| null | workflow id, once known |
| `refiner` | string \| null | refiner used |
| `status` | see below | |
| `message` | string \| null | one short plain-text line: "Refining the prompt", "Waiting for a GPU", "Sampling 12/28"… |
| `stages` | string[] | the workflow's processing stages (may be empty) |
| `stage_index` | number \| null | current stage (0-based index into `stages`) |
| `percent` | number \| null | 0–100 overall, null when unknown |
| `error` | string \| null | plain-text reason when failed |
| `runpod_job_id`, `endpoint_id` | string \| null | RunPod identifiers once submitted |
| `created_at`, `submitted_at`, `started_at`, `finished_at`, `updated_at` | number \| null | epoch seconds |
| `generating_at` | number \| null | first ComfyUI step frame from the worker relay (models loaded, sampling begun); null if the worker never sent one |
| `publishing_at` | number \| null | when the status became `publishing` |
| `estimate` | object \| null | expected phase lengths, see "Phases and the time estimate" |
| `duration_s` | number \| null | created → finished |
| `cost_gbp` | number \| null | GPU cost |
| `seed` | number \| null | |
| `result_url` | string \| null | the output (image or video URL) |
| `published_path` | string \| null | where it was published |
| `cancel_requested` | boolean | |

**Status:** `preparing` (refining, loading the workflow, uploading) → `queued` (at RunPod, waiting for a
worker) → `running` → `publishing` (output back; publishing, costing) → one of `completed`, `failed`,
`cancelled`. Active = the first four. A RunPod timeout or a 20-minute local timeout is `failed` (and the
RunPod job is cancelled). Jobs still active when Flask stops are marked `failed` ("Server restarted")
at the next start, and their RunPod jobs are cancelled.

## Phases and the time estimate

| phase | from → to | learned? |
|---|---|---|
| preparing ("Imagining") | `created_at` → `submitted_at` | yes: `prepare_s` |
| getting ready | `submitted_at` → `generating_at` (GPU queue, cold boot, model load) | **never** |
| generating | `generating_at` → `publishing_at` | yes: `generate_s` |
| publishing | `publishing_at` → `finished_at` | yes: `publish_s` |

`started_at` is RunPod's IN_PROGRESS (or the first relay frame). `generating_at` is stamped by the tracker on
the first ComfyUI `progress` frame for the job (`on_worker_frame`), and published and saved at once.

Cold starts vary from seconds to minutes, so the "getting ready" phase is deliberately left out: it is not
learned, not exposed, and fills only a small fixed slice of the ring. Generation on a warm worker is steady
per workflow, and that is what the estimate and the ring measure.

`estimate` = `{prepare_s, generate_s, publish_s, samples, basis}` (routes/jobs/estimate.py). Each phase is the
median over the last 30 completed jobs of the same workflow that have `created_at`, `submitted_at`,
`generating_at`, `publishing_at` and `finished_at` (`basis: "workflow"`, needs ≥ 3), else over the last 30
of any workflow (`"all"`, ≥ 3), else defaults 4 / 30 / 3 s (`"default"`). A job without `generating_at`
(a worker without the relay) is not a sample. A phase that is negative or over 30 min is ignored. Results
are cached per workflow for 60 s. The estimate is set when the job is made and redone once when its workflow
becomes known; it doesn't change after that.

Clients turn it into the ring and the words: `src/shared/jobs/estimate.ts` (`jobProgress`, `useJobProgress`:
fraction, remaining seconds, phase; generating is corrected upward by `percent`; never backwards, never full
before done) and `src/shared/jobs/words.ts` (the stage labels and "About 30 seconds"-style text, in one place).
If `generating_at` is null the client takes generation to start at `started_at`.

## HTTP API (Flask, `/api`)

- `GET /api/jobs?state=active|finished|all&limit=50&before=<created_at>&target=&source=&q=` →
  `{jobs: Job[], next_before: number|null}`, newest first. `q` matches the prompt.
- `GET /api/jobs/<id>` → `Job` (404 if unknown).
- `POST /api/jobs/<id>/cancel` → `Job`.
- `POST /api/jobs/cancel-all` → `{cancelled: number}`. `/api/cancel_all_jobs` is kept as an alias.
- `GET /api/jobs/stream?target=<id>` → Server-Sent Events. With `target`, only jobs for that target.
  - `event: snapshot`, data `{jobs: Job[], server_time}`: active jobs plus those finished in the last 60 s.
    Sent first on every (re)connect.
  - `event: job`, data `Job`: the whole job after any change (progress at most ~4 per second per job).
  - a `: ping` comment every 15 s. The server ends a stream after ~5 minutes; EventSource reconnects
    (`retry: 3000`) and gets a fresh snapshot.

No auth on any of these (they sit beside the existing open generation API).

## Progress from RunPod

The tracker polls RunPod `/status` for every submitted job (queued / running / done). Step progress comes
from the worker's ComfyUI relay: the worker opens a websocket to Flask's :8765 and sends
`{job_id: <runpod id>, comfy: {type, data}}`. `overlay_ws_server.py` hands those frames to the tracker
(and no longer broadcasts them to screens). `progress` frames give `value/max`; the stage advances when
`value` drops or `max` changes; overall percent is weighted by the workflow's `processing_stages`.
