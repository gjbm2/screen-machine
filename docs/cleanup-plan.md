# Screen Machine — Cleanup & Re-engineering Plan

## Overview

This document identifies the major structural issues in the Screen Machine codebase and proposes concrete plans for addressing them. Issues are grouped into three tiers:

1. **Architectural** — fundamental design problems requiring re-engineering
2. **Structural** — code organisation, duplication, and robustness issues
3. **Hygiene** — dead code, missing types, documentation gaps

Each section describes the current state, the problems it causes, the target state, and a migration path.

---

## Table of Contents

- [A. Architectural Issues](#a-architectural-issues)
  - [A1. Generation Job Registry](#a1-generation-job-registry)
  - [A2. WebSocket Re-engineering](#a2-websocket-re-engineering)
  - [A3. Authentication & Authorisation](#a3-authentication--authorisation)
  - [A4. Display Screen: Polling → Push](#a4-display-screen-polling--push)
- [B. Structural Issues](#b-structural-issues)
  - [B1. Code Duplication](#b1-code-duplication)
  - [B2. Error Handling](#b2-error-handling)
  - [B3. Configuration Management](#b3-configuration-management)
  - [B4. Dependency Management](#b4-dependency-management)
- [C. Hygiene Issues](#c-hygiene-issues)
  - [C1. Dead Code](#c1-dead-code)
  - [C2. Logging Consistency](#c2-logging-consistency)
  - [C3. Type Hints & Documentation](#c3-type-hints--documentation)
- [D. Suggested Execution Order](#d-suggested-execution-order)

---

## A. Architectural Issues

### A1. Generation Job Registry

#### Current State

Generation jobs have no persistent lifecycle tracking. The system relies on a blocking HTTP request to hold frontend and backend together for the duration of a job (up to 20 minutes).

**Frontend tracking:**
- `batchId` (nanoid) in React state — lost on tab close/refresh
- `localStorage['activePlaceholders']` — survives refresh but has no reconnection logic
- `activeGenerations` array — pure React state
- No API exists to query running jobs

**Backend tracking:**
- RunPod `job_id` appended to `runpod_jobs.log` — append-only flat file with no status, no timestamps, no batch association
- `job_progress_listeners_latest` — in-memory dict, lost on restart, no cleanup, no API to query
- No mapping between frontend `batchId` and RunPod `job_id`

**The blocking pattern:**
- `POST /api/generate-image` blocks until the job completes (or times out at 20 min)
- Frontend `fetch()` has no timeout and no `AbortController`
- Backend spawns a thread that polls RunPod every 0.5s in a tight loop
- Progress overlays go to display screens via WS — not to the generation UI
- `handle_image_generation` joins all threads with `wait=True`

#### Problems

| Scenario | Consequence |
|---|---|
| Browser tab closed/refreshed | Backend thread continues. Result is published but user never sees confirmation. `fetch()` aborted silently. |
| Server restart | All polling threads killed. RunPod jobs continue running, orphaned. `runpod_jobs.log` is never re-read on startup. |
| Network blip | `fetch()` rejects. Backend doesn't know. Job completes, result discarded. |
| 20-minute timeout | Backend raises `TimeoutError`. RunPod job keeps running — no cancellation sent. |
| Cancel request | Only "cancel all" exists. No single-job cancel. No frontend abort. |
| Concurrent jobs | Multiple threads, each independently polling. No shared view of system load. |

#### Target State

A persistent, queryable job registry that decouples job submission from result delivery.

**Job model:**
```
Job {
  id:               server-generated UUID
  batch_id:         frontend-provided nanoid (for grouping)
  runpod_job_id:    from RunPod SDK (nullable until submitted)
  endpoint_id:      RunPod endpoint
  status:           queued | submitted | running | completed | failed | cancelled | timed_out
  progress:         0-100 (nullable)
  progress_stage:   string (nullable, e.g. "Rendering > Interpolating")
  created_at:       ISO timestamp
  updated_at:       ISO timestamp
  completed_at:     ISO timestamp (nullable)
  prompt:           string
  workflow:         string
  publish_dest:     string (nullable)
  result:           JSON (nullable — output URLs, metadata)
  error:            string (nullable)
}
```

**Storage:** SQLite (single file, no external dependency, supports concurrent reads, atomic writes). Alternatively, a JSON file per job in a `jobs/` directory if SQLite is too heavy.

**API:**
```
POST   /api/jobs                    → Submit job, returns immediately with {job_id}
GET    /api/jobs                    → List jobs (filterable by status, batch_id)
GET    /api/jobs/:id                → Get single job (status, progress, result)
POST   /api/jobs/:id/cancel        → Cancel a specific job
DELETE /api/jobs/:id                → Remove completed/failed job record
GET    /api/jobs/active             → Currently running jobs (for reconnection)
```

**Backend flow:**
1. `POST /api/jobs` validates input, creates a `queued` job record, returns `{job_id}` immediately
2. A background worker picks up queued jobs, submits to RunPod, updates status to `submitted` → `running`
3. Progress updates (from RunPod WS or polling) update the job record
4. On completion: status → `completed`, result populated, publish triggered if configured
5. On server restart: query RunPod for status of all `submitted`/`running` jobs and reconcile

**Frontend flow:**
1. `POST /api/jobs` returns a `job_id` immediately
2. Frontend polls `GET /api/jobs/:id` every 2-3s (or subscribes via SSE/WS) for progress
3. On page refresh: `GET /api/jobs/active` recovers all in-flight jobs and re-attaches UI
4. Cancel sends `POST /api/jobs/:id/cancel`

#### Migration Path

1. Create `jobs.py` module with SQLite-backed `JobRegistry` class
2. Add the `/api/jobs` REST endpoints
3. Refactor `generate.py` to use the registry instead of blocking threads
4. Add startup reconciliation (check RunPod for in-flight jobs)
5. Update frontend to use submit-then-poll pattern
6. Remove `runpod_jobs.log`, `job_progress_listeners_latest`
7. Add cleanup: purge completed jobs older than N days

---

### A2. WebSocket Re-engineering

#### Current State

A single WebSocket server on port 8765 multiplexes four unrelated concerns through implicit client classification:

- **Overlay viewers** — any connection that doesn't send a message within 500ms
- **Audio producers** — first message is `{"type": "audio", ...}`
- **Relay senders** — any other JSON message (RunPod progress, lux data, test messages)
- **Dead code** — `useGenerationWebSocket.ts` exists but is never imported

All messages from relay senders are broadcast to all overlay viewers. Display screens, the operator UI, light sensors, and RunPod workers share one undifferentiated channel. Viewers filter client-side.

#### Problems

| Problem | Details |
|---|---|
| Cross-thread event loop mismatch | `display.py:send_overlay()` runs in Flask's thread, must call async WS functions on the WS server's event loop. Uses three fallback strategies, none reliable. Overlays from Flask routes can silently fail. |
| No connection cleanup | Overlay viewers never removed from `registry.overlays` on disconnect. The `finally` block only handles audio clients. Dead connections accumulate. |
| Broadcast storm | Every viewer receives every message type. Active generation + transcription + lux = noise that must be filtered client-side. |
| No message delivery guarantees | Messages lost during reconnect gaps. No queue, no replay, no acknowledgement. |
| Implicit client typing | 500ms timeout heuristic for classification. No handshake, no auth, no explicit registration. |
| Unbounded memory growth | `job_progress_listeners_latest` and `last_stop_time` are never pruned. |
| Inconsistent reconnection | `useOverlayWebSocket` uses exponential backoff. `Index.tsx` uses fixed 5s. No shared abstraction. |

#### Target State

Replace the single multiplexed server with purpose-specific channels. Two viable approaches:

**Option A: Multiple SSE/WS endpoints (simpler)**
```
GET  /api/events/overlays/:screenId    → SSE stream, filtered by screen
GET  /api/events/jobs/:jobId           → SSE stream for job progress
WS   /ws/audio/:target                 → Dedicated audio streaming (needs bidirectional)
POST /api/sensors/lux                  → HTTP POST (lux data doesn't need WS)
```

SSE (Server-Sent Events) is appropriate for overlay and job progress because they're unidirectional server→client flows. Audio remains WS because it requires bidirectional binary streaming.

**Option B: Socket.IO with rooms (more capable)**
```
Rooms: overlay:{screenId}, job:{jobId}, audio:{target}, sensor:{sensorName}
```

Socket.IO provides: rooms (topic filtering), automatic reconnection, message acknowledgement, fallback transports. More dependency, but solves most of the current problems out of the box.

**Either way, the design principles are:**
1. Clients declare what they want to receive (explicit subscription, not implicit timing)
2. Messages are routed to relevant subscribers, not broadcast to everyone
3. Each channel has a clear protocol (message types, expected payloads)
4. Connection lifecycle is explicit (connect, authenticate, subscribe, disconnect, cleanup)
5. The cross-thread event loop problem is eliminated by using Flask-native mechanisms (SSE via streaming responses, or Socket.IO which integrates with Flask)

#### Migration Path

1. Decide between SSE+WS or Socket.IO (recommend SSE+WS for lower dependency)
2. Implement new overlay SSE endpoint; update `DisplayPage` to use it
3. Implement job progress SSE endpoint (pairs with A1 job registry)
4. Convert lux sensor to HTTP POST
5. Implement dedicated audio WS endpoint with explicit handshake
6. Migrate `Index.tsx` to use job SSE (from A1) and overlay SSE
7. Remove `overlay_ws_server.py`, `connection_registry.py`
8. Remove dead code: `useGenerationWebSocket.ts`, `OLDDisplayPage.tsx`

---

### A3. Authentication & Authorisation

#### Current State

There is no concept of identity anywhere in the system. No login, no sessions, no tokens, no API keys, no request validation. `CORS(app)` allows all origins. The ngrok tunnel exposes every endpoint — including server reboot and SSH command execution — to the public internet.

**Specific risks:**
- `POST /api/admin-k9x7m/reboot` — reboots the media server, accessible to anyone via ngrok
- `POST /api/admin-k9x7m/set-url` — navigates Chrome to any URL via CDP, accessible to anyone
- `POST /api/alexa` — no Amazon request signature verification, anyone can forge requests
- `POST /api/generate-image` — can be called by anyone, incurring OpenAI/RunPod costs
- WS port 8765 — accepts any connection, no authentication
- Admin panel at `/adminadmin` — hidden by URL obscurity only
- SSH password embedded in shell commands in `admin_api.py`
- `.env` contains live API keys and may have been committed to git history

#### Target State: Trust Rings

The system has distinct actor types with different trust levels. Auth should be designed around concentric trust rings:

```
Ring 0 — Internal (in-process)
  Actors:   Scheduler threads, background workers
  Auth:     None needed (same process, no network boundary)
  Access:   Full

Ring 1 — Authenticated operator (local or remote)
  Actors:   You, using the web UI or admin panel
  Auth:     Password login → session cookie (Flask-Login or similar)
  Access:   Full (generate, publish, schedule, admin, file ops)

Ring 2 — Authenticated device (local network)
  Actors:   Display screens, light sensors, audio devices
  Auth:     Long-lived device token (in URL query param or header)
  Access:   Scoped per device type:
            - Display: poll own content, receive own overlays, report mask
            - Sensor: report lux data only
            - Audio: stream audio for transcription only

Ring 3 — Verified external service
  Actors:   Alexa
  Auth:     Amazon request signature verification (ask-sdk)
  Access:   Scoped: generate with approved params, trigger approved events
            Cannot: admin, file ops, schedule mutation, reboot

Ring 4 — Untrusted external
  Actors:   Everyone else
  Auth:     N/A
  Access:   Nothing. Rejected.
```

#### Authorisation Matrix

| Endpoint Group | Operator | Display | Sensor | Alexa | Untrusted |
|---|---|---|---|---|---|
| `/api/admin-*` | YES | — | — | — | — |
| `/api/generate-image` | YES | — | — | Scoped | — |
| `/api/publish/*` | YES | — | — | — | — |
| `/api/buckets/*` (read) | YES | Own bucket | — | — | — |
| `/api/buckets/*` (write) | YES | — | — | — | — |
| `/api/schedulers/*` | YES | — | — | Limited | — |
| `/api/files/*` | YES | — | — | — | — |
| `/api/alexa` | N/A | N/A | N/A | Signed | — |
| `/api/<dest>/mask` | YES | Own screen | — | — | — |
| `/api/lightsensor/*` | YES | — | YES | — | — |
| `/api/jobs/*` | YES | — | — | — | — |
| WS/SSE overlays | YES | Own screen | — | — | — |
| WS audio | YES | — | — | — | — |
| Frontend routes | YES | `/display/:id` | — | — | — |

#### Implementation Design

**A. Operator auth (Ring 1):**
- Single hashed password stored in `.env` as `APP_PASSWORD`
- `POST /api/auth/login` — validates password, sets a signed session cookie (`flask-login` or `itsdangerous`)
- `@require_login` decorator on all operator-level routes
- Frontend: login page gate before rendering `App.tsx`; session cookie sent automatically with all `fetch()` calls
- Session expiry: configurable (e.g. 7 days)

**B. Device tokens (Ring 2):**
- Generated per device, stored in a config file or env var (e.g. `DEVICE_TOKEN_NORTH_SCREEN=<random>`)
- Passed as `?token=<value>` in display URLs and WS/SSE connections
- `@require_device_token(allowed_scopes)` decorator
- Scope checked against a mapping: `{token: {type: "display", screen_id: "north-screen"}}`

**C. Alexa verification (Ring 3):**
- Use `ask-sdk` or manual certificate chain validation per Amazon's specification
- Validate `SignatureCertChainUrl` and `Signature` headers on every request
- Reject any request that fails validation

**D. Middleware:**
- `@app.before_request` hook that checks auth for all `/api/*` routes
- Explicit allowlist for unauthenticated routes (e.g. `/api/auth/login`, `/api/health`)
- All other routes require a valid session, device token, or service signature

**E. ngrok hardening:**
- Admin endpoints (`/api/admin-*`) excluded from ngrok tunnel (reverse proxy rules)
- Or: ngrok configured with IP allowlisting / HTTP basic auth as an outer layer
- CORS restricted to known frontend origins

#### Migration Path

1. **Immediate:** Rotate all API keys (OpenAI, RunPod, AssemblyAI). Ensure `.env` is in `.gitignore` and scrub from git history if needed.
2. Add operator login (password → session cookie) and `@require_login` decorator
3. Gate the frontend with a login page
4. Add `@require_login` to admin, generate, publish, scheduler, file, bucket mutation endpoints
5. Add device token system for display screens and sensors
6. Add Alexa request signature verification
7. Restrict CORS to actual frontend origins
8. Add rate limiting (Flask-Limiter) — critical for generation endpoints
9. Harden ngrok: exclude admin endpoints, add IP allowlist if possible
10. Audit logging: record who did what (operator session, device token, Alexa) for admin and generation actions

---

### A4. Display Screen: Real-Time Updates

#### Current State

The display screen (`/display/:screenId`) runs on Samsung TVs and kiosks. It uses three mechanisms to stay current:

**Image detection — polling every 1 second:**

Publishing means copying a file to `/output/<screenId>.jpg` (or `.mp4`) via `shutil.copy2()` + `touch()`. The display discovers this by sending 3 HEAD requests every 1000ms to `/output/<base>.jpg`, `.JPG`, and `.mp4`, comparing `Last-Modified` headers, and triggering a crossfade on change. **259,200 HTTP requests per day per display** to detect 10–50 actual changes.

**Brightness/mask — polling every 3 seconds:**

`useMask.ts` sends GET to `/api/<screenId>/mask` every 3000ms. The server computes brightness from sensor lux (EMA-smoothed) or solar position (astral). **28,800 requests per day per display** for slowly-changing data.

**Overlays — WebSocket push (already efficient):**

`useOverlayWebSocket.ts` receives overlay messages in real-time via WS. Sub-100ms latency, no polling. This is the one part that works well.

**Other issues:**
- `mask_states` and `brightness_overrides` in `display.py` are in-memory dicts — lost on server restart
- Display guesses file extensions (`.jpg`, `.JPG`, `.mp4`) because it doesn't know what was published
- Race condition: display can `GET` a file while the publisher is still writing it
- Hard reload every ~4 hours as defence against memory leaks on kiosk devices

#### Design Decision: Keep Disk-Based Publication

The disk-based publication model has real strengths that shouldn't be discarded:

| Strength | Why it matters |
|---|---|
| **Self-evident source of truth** | What's on screen = whatever file is at `/output/screen.jpg`. You can `ls -la` it, open it, debug it. No state to query. |
| **Stateless recovery** | Display reboots? Reads the file. Server reboots? File's still there. No "what was the last command?" problem. |
| **No connection dependency** | Display works even if WS is completely down. Doesn't need to be online at the moment of publication. |
| **Multiple consumers** | Any process can read the file — other displays, monitoring, backups. No protocol needed. |
| **Simple debugging** | Something wrong on screen? Look at the file. |

A fully command-driven model (where the server tells the display "show this URL" via WS) would add complexity: the display must be connected at publish time or you need a fallback (which is... reading from disk). You'd need server-side command replay, reconnect sync, and state tracking. You'd be building two systems — the command path and the fallback — instead of one simple one.

**The real problems with the current implementation are narrow and fixable without changing the underlying model:**

1. **Polling waste** — The display doesn't know *when* the file changed, so it polls constantly. Fixable by adding a WS notification signal ("file changed") while keeping the file as the source of truth.

2. **Race condition on partial writes** — The display can fetch during a write. Fixable with atomic writes: `shutil.copy2()` to a temp file, then `os.rename()` (which is atomic on the same filesystem).

3. **Extension guessing** — 3 HEAD requests per cycle because the display doesn't know the file type. Fixable by either standardising on one extension, or having the notification include the file path.

4. **Brightness polling** — The server knows when brightness changes but has no way to push it. Fixable by sending mask updates over the existing WS connection.

#### Target State

Keep file-on-disk as the publication model and source of truth. Add a **notification signal** so the display knows when to read, instead of polling to discover changes. Push brightness updates instead of polling for them. Fall back to polling if the push channel is unavailable.

**Image notification:**

After `publish_to_destination()` completes an atomic file write, send a WS message to the relevant screen:

```
{
  type: "file_changed",
  screen: "north-screen",
  url: "/output/north-screen.jpg?t=1709420000",
  media_type: "image"
}
```

The display receives this and fetches the URL (the actual file on disk, served statically). Optionally preloads into an offscreen element before crossfading — this is a nice-to-have that guards against any residual partial-write risk.

If the WS connection is down, the display falls back to HEAD polling at a slower interval (5–10s). On reconnect, it does one immediate poll to catch anything missed.

**Atomic writes:**

Replace the current `shutil.copy2()` + `touch()` with:
```python
temp_path = display_path.with_suffix('.tmp')
shutil.copy2(source, temp_path)
os.rename(temp_path, display_path)   # atomic on same filesystem
```

This eliminates the partial-write race condition regardless of whether the display uses polling or push.

**Brightness push:**

Instead of the display polling `/api/<screenId>/mask` every 3 seconds, the server pushes brightness updates when they change:

- **Sensor-based:** When computed brightness changes by more than a threshold (e.g. > 0.02), push an update to the relevant screen. The sensor data already arrives via WS — the server just needs to compute the mask and push the result.
- **Solar-based:** Push every 30–60 seconds (solar position changes ~0.25°/minute). Or compute client-side since the astral calculation is deterministic given location and time.

Fallback: if WS is down, revert to GET polling at 30s interval.

```
{
  type: "mask_update",
  screen: "north-screen",
  brightness: 0.72,
  warm_hex: "#FFE4B5",
  warm_alpha: 0.15,
  source: "sensor"
}
```

**Persist mask state:**

Write `mask_states` and `brightness_overrides` to a JSON file (or fold into per-destination config). Restore on startup so screens don't flash full brightness after a server restart.

**Display protocol summary:**

One WS/SSE connection per screen carrying:

| Message Type | Payload | Trigger |
|---|---|---|
| `file_changed` | `{url, media_type}` | Publisher completes atomic write |
| `mask_update` | `{brightness, warm_hex, warm_alpha}` | Sensor/solar change exceeds threshold |
| `overlay` | `{html, duration, position, fadein, clear}` | `send_overlay()` call (already works) |
| `mask_toggle` | `{enabled}` | Admin toggles mask on/off |
| `brightness_override` | `{brightness}` or `{clear: true}` | Admin sets/clears manual override |

**Fallback modes:**

| Mode | When | Behaviour |
|---|---|---|
| Push (normal) | WS/SSE connected | Receives all messages in real-time (~50ms) |
| Polling fallback | WS/SSE disconnected | HEAD poll at 5-10s for images, GET poll at 30s for mask |
| Reconnect catch-up | WS/SSE reconnects | One immediate poll to sync, then back to push mode |

#### Future Consideration: URL-Agnostic Publication

The notification-based approach opens the door to a future enhancement: the `file_changed` message could carry *any* URL — a bucket path, S3 presigned URL, or CDN path — not just a local file path. The display doesn't care where the content lives; it just fetches whatever URL it's given.

This would allow serving from Wasabi/S3 directly without copying to `/output/`, but it's **not necessary now** and adds complexity around the "what's the source of truth?" question. The file-on-disk model works and is worth keeping until there's a concrete reason to change it.

#### Migration Path

**Phase 1 — Notification signal (quick win, existing infrastructure):**

1. Make file writes atomic in publisher (`copy2` to temp, `os.rename`)
2. After write, send `file_changed` message via existing overlay WS broadcast
3. In `DisplayPage`, handle `file_changed`: fetch the URL, crossfade on load
4. Keep `useFilePolling` as fallback but increase interval to 5–10s
5. This eliminates ~95% of polling traffic and cuts image latency to ~50ms

**Phase 2 — Brightness push (alongside A2):**

6. Add server-side push for mask updates on sensor change / solar timer
7. Replace `useMask` polling with push reception + 30s fallback
8. Persist `mask_states` and `brightness_overrides` to disk; restore on startup
9. Consolidate into per-screen channel as part of A2 redesign
10. Remove `useFilePolling.ts` once push + fallback is proven stable
11. Consider client-side solar calculation to eliminate server-side solar polling

---

## B. Structural Issues

### B1. Code Duplication

| Duplicated Logic | Locations | Action |
|---|---|---|
| `loosely_matches()` | `routes/generate.py`, `routes/scheduler_handlers.py` | Extract to `routes/utils.py` |
| Image compression in `encode_image_uploads` / `encode_reference_urls` | `routes/utils.py:238-256`, `routes/utils.py:288-308` | Extract shared `compress_image()` helper |
| Image loading/encoding for generation | `routes/scheduler_handlers.py:56-97`, `routes/scheduler_handlers.py:343-358` | Extract `format_images_for_generation()` |
| History append + size capping | `scheduler_handlers.py` in `handle_generate` and `handle_animate` | Extract `append_to_history()` and `cap_history()` |
| EXIF/metadata extraction | `routes/bucketer.py:559-600` and `routes/utils.py` | Remove from `bucketer.py`, import from `utils.py` |
| `sidecar_path` | `routes/bucketer.py`, `routes/utils.py`, `routes/publisher.py` | Single definition in `utils.py`, import everywhere |

### B2. Error Handling

| Issue | Location | Action |
|---|---|---|
| `findfile()` returns `None`, callers pass directly to `open()` | `routes/generate.py:381`, `routes/admin_api.py:56` | Check return value, raise descriptive error or return 404 |
| Broad `except Exception` catches | `routes/publisher.py:56-68`, `routes/openai.py:46-48` | Catch specific exceptions (`FileNotFoundError`, `PIL.UnidentifiedImageError`, etc.) |
| `batch_val` potentially uninitialized | `routes/scheduler_handlers.py:388` | Initialize `batch_val = None` before `try` |
| Missing import: `ExifTags` | `routes/utils.py:837` | Add `from PIL import ExifTags` |
| Missing import: `subprocess` | `routes/utils.py:852` | Add `import subprocess` |
| Circular import | `routes/utils.py:799` imports from `routes.publisher` | Use local `sidecar_path` (already defined at lines 883-885) |

### B3. Configuration Management

| Issue | Location | Action |
|---|---|---|
| `DEBUG = True` hardcoded | `config.py:16` | `os.getenv("FLASK_DEBUG", "false").lower() == "true"` |
| `HOST = "0.0.0.0"` hardcoded | `config.py:17` | `os.getenv("HOST", "127.0.0.1")` |
| `PORT`, `WS_PORT` hardcoded | `config.py:18-19` | Load from env with defaults |
| WS host/port hardcoded | `overlay_ws_server.py:294-295` | Use `config.WS_PORT` |
| Generation timeout `1200` hardcoded | `routes/generate.py:404,418` | `config.GENERATION_TIMEOUT` from env |
| ngrok domain hardcoded | `start.sh:36` | `NGROK_DOMAIN` env var |

### B4. Dependency Management

| Issue | File | Action |
|---|---|---|
| Duplicate `websockets>=11.0` and `websockets>=13` | `requirements.txt` | Keep only `websockets>=13` |
| Both `opencv-python-headless` and `opencv-python` | `requirements.txt` | Keep only `opencv-python-headless` |
| Loose version pins on FastAPI, uvicorn, pydantic | `requirements.txt` | Add upper bounds (e.g. `>=0.100,<1.0`) |

---

## C. Hygiene Issues

### C1. Dead Code

| Item | Location | Action |
|---|---|---|
| Local `debug`, `info`, `warning`, `error` functions (overwritten by import) | `routes/generate.py:40-65` | Remove |
| Duplicate `import requests` | `routes/generate.py:29` | Remove |
| Duplicate `import utils.logger` | `routes/alexa.py:28` | Remove |
| Duplicate `from PIL import Image` | `routes/utils.py:21-24` | Remove |
| Commented-out `run_request = endpoint.run_sync` | `routes/generate.py:531-534` | Remove |
| Stale comment about duplicate function | `routes/scheduler_handlers.py:1276` | Remove |
| "THESE NEED TESTING" comment | `routes/utils.py:824` | Add tests or remove |
| `useGenerationWebSocket.ts` | `src/hooks/use-generation-websocket.ts` | Delete (never imported) |
| `OLDDisplayPage.tsx` | `src/pages/OLDDisplayPage.tsx` | Delete |
| `WebSocketMessage` type alias | `src/hooks/image-generation/types.ts:46` | Delete |

### C2. Logging Consistency

| Issue | Locations | Action |
|---|---|---|
| Mixed `log_to_console`, `info`, `error`, `warning`, `debug` | `routes/display.py` and others | Standardise on `utils.logger` throughout |
| Debug log with `"!!! uses_two_images !!!"` | `routes/generate.py:364` | Replace with structured debug message |
| `traceback` imported inside functions | `routes/scheduler_handlers.py:391,518` | Move to top-level import |
| Full API response logged | `routes/openai.py:161` | Log summary only |
| Long separator in debug log | `routes/scheduler_handlers.py:524` | Remove or shorten |

### C3. Type Hints & Documentation

**Priority files for type hints** (public-facing functions):
- `routes/openai.py` — `openai_prompt`, `_image_to_base64_data_url`, `hash_schema`
- `routes/scheduler_handlers.py` — all handler functions
- `routes/generate.py` — `start()`, `update_workflow()`, `loosely_matches()`
- `routes/utils.py` — `findfile`, `dict_substitute`, `fuzzy_match`
- `overlay_ws_server.py` — `handler`, `send_overlay_to_clients`

**Priority files for docstrings:**
- `routes/scheduler_handlers.py` — all `handle_*` functions
- `routes/generate.py` — `start()`
- `routes/openai.py` — `openai_prompt()`

---

## D. Suggested Execution Order

The architectural issues (A1-A4) have dependencies. The job registry (A1) should come first — most self-contained, most day-to-day reliability impact. The display push quick win (A4 steps 1-3) is low-effort and high-impact, so it belongs in Phase 1. Full WS re-engineering (A2) depends on A1 (job progress delivery) and subsumes the full A4 implementation. Auth (A3) is orthogonal and can be done in parallel.

### Phase 1: Foundation (do first)

| Item | Effort | Rationale |
|---|---|---|
| **A1. Job registry** | Medium | Eliminates the most common real-world failure (orphaned jobs, lost progress). Self-contained. |
| **A4 quick win: notification signal + atomic writes** | Small | Make file writes atomic (`rename`). Send `file_changed` WS message after write. Display fetches on notification instead of polling. Eliminates ~95% of polling, cuts latency from 0–1s to ~50ms. |
| **A3 step 1: Rotate secrets** | Trivial | If keys are in git history, they're compromised. Do this immediately. |
| **A3 step 2: Operator login** | Small | Password gate + session cookie. Prevents the worst ngrok exposure. |
| **B2. Fix broken imports** | Trivial | `ExifTags`, `subprocess` missing — causes runtime crashes. |
| **B4. Fix requirements.txt** | Trivial | Duplicate/conflicting dependencies. |

### Phase 2: Core re-engineering

| Item | Effort | Rationale |
|---|---|---|
| **A2. WebSocket re-engineering** | Large | Depends on A1 for job progress design. Eliminates the flaky overlay delivery and broadcast storm. Subsumes the full A4 display protocol. |
| **A4 full: brightness push + per-screen channels** | Medium | Push mask updates on sensor/solar change. Persist mask state. Per-screen WS/SSE channel (part of A2). Polling becomes fallback only. File-on-disk stays as source of truth. |
| **A3 steps 3-6: Device tokens, Alexa verification, CORS, rate limiting** | Medium | Completes the auth layer. |
| **B1. Code duplication** | Small | Quick wins, reduces maintenance burden. |
| **B3. Configuration management** | Small | Moves hardcoded values to env vars. |

### Phase 3: Polish

| Item | Effort | Rationale |
|---|---|---|
| **C1. Dead code removal** | Trivial | Cleanup. |
| **C2. Logging consistency** | Small | Standardise logging approach. |
| **C3. Type hints & docs** | Medium | Ongoing — do as files are touched. |
| **A3 steps 7-10: ngrok hardening, audit logging** | Small-Medium | Final security hardening. |
