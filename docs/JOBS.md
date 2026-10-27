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
