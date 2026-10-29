# Screen Machine – Comprehensive Documentation

## Table of Contents
1. Introduction & Purpose
2. High-Level Architecture
3. Technology Stack
4. Backend (Flask / Python)
5. Frontend (React / TypeScript)
6. Real-Time Overlay & WebSocket Layer
7. Scheduler & Automation Engine
8. AI Generation Pipeline
9. Storage, Buckets & File Management
10. Configuration & Environment Variables
11. Directory Structure Overview
12. Development Workflow
13. Running Locally
14. Deployment & Production Considerations
15. Testing Strategy
16. Logging & Monitoring
17. Security Considerations
18. Roadmap & Future Improvements
19. License

---

## 1. Introduction & Purpose
Screen Machine is an end-to-end platform that **generates, schedules, and displays AI-generated artwork** on network-connected screens (e.g. Samsung Frame TVs). It combines a Python/Flask back-end, a React/TypeScript front-end, and a WebSocket overlay layer to deliver real-time feedback, robust scheduling, and remote GPU execution via RunPod.  
Key goals:
* Democratise high-quality generative art by abstracting complex ML workflows behind a friendly UI.  
* Provide a reliable scheduling engine so that screens always have fresh, context-aware content.  
* Offer live status overlays and granular logging for both creators and maintainers.

## 2. High-Level Architecture
```
┌──────────────────────────────┐        WebSocket (8765)        ┌──────────────────────────────┐
│  React Front-End (Vite)      │  <───────────────────────────► │  Overlay Clients (TVs, etc.) │
│  • Generation UI             │                               │  • Lightweight HTML pages     │
│  • Scheduler UI              │          REST/HTTP            └──────────────────────────────┘
│  • Live Logs / Toasts        │                               ▲
└─────────────▲────────────────┘                               │ send_overlay()
              │                                               
              │                                               │
              │                                               ▼
┌─────────────┴────────────────┐  Flask Blueprints             ┌──────────────────────────────┐
│  Python API Server (Flask)   │  /api/generate   /api/publish │  RunPod Remote GPU           │
│  • Blueprint modules         │◄──────────────────────────────┤  • ComfyUI workflows         │
│  • Scheduler Engine          │   JSON payloads               │  • Async job progress        │
│  • Bucket / File APIs        │                               └──────────────────────────────┘
│  • Overlay WS relay          │                                               ▲
└──────────────────────────────┘        S3 / Local FS                       Webhook / WS
```
End-to-end flow:
1. User submits a generation request from the React UI.  
2. Flask `/api/generate` builds a ComfyUI workflow and triggers a **RunPod** job.  
3. RunPod streams progress; Flask relays updates via the **WebSocket relay** (`overlay_ws_server.py`).  
4. Overlay clients (browsers/TVs) receive live overlays (progress bars, alerts).  
5. On completion Flask saves assets to `output/`, updates buckets, and optionally publishes to the target screens via Samsung TV WebSockets.

The room side runs beside this (ports in `docs/PORTS.md`): room_service inside the Flask process (`room_in_app.py`, LAN door :8700, show bus 127.0.0.1:8701), stream_service (the live GPU renderer, :8702), show_service (the room's ears and art director, 127.0.0.1:8703) and dot_service (the e-ink tags, 127.0.0.1:8704), the last three run by `scripts/ctl`. HTTPS is Caddy on Windows: `https://screen.finsburybankforsavings.com`.

## 3. Technology Stack
### Back-End
* **Python 3.12**  
* **Flask 3.x** – REST API & static file hosting
* **Flask-CORS** – cross-origin resource sharing
* **websockets** / **websocket-server** – overlay relay layer
* **RunPod SDK** – remote GPU execution
* **OpenAI SDK** – prompt enrichment, natural-language features
* **Pillow / OpenCV / ffmpeg-python** – image & video manipulation
* **Astral** – solar position calculations for brightness/temperature curves
* **PyTest** – extensive unit & integration tests

### Front-End
* **React 18** with **TypeScript** – component-driven UI
* **Vite 5** – lightning-fast build tool
* **shadcn/ui + Radix UI** – accessible UI primitives
* **MUI 6** – complex widgets (data-grids, dialogs)
* **Tailwind CSS** – utility-first styling (`tailwind-merge`, `tailwindcss-animate`)
* **TanStack React-Query** – data-fetching and cache management
* **Dnd-kit** – drag-and-drop interactions
* **Sonner / shadcn Toaster** – real-time toast notifications

### Native / Vendor Components
* **Samsung TV WS** – vendored library for encrypted WebSocket control
* **Quart / Hypercorn** – lightweight async web-server used by vendor library

## 4. Backend (Flask / Python)
### Entry Point `app.py`
* Sets up Flask, loads environment, and registers all **Blueprints** under a common `/api` prefix.
* Serves the pre-built React bundle from `dist/` (`STATIC_FOLDER` in `config.py`) under waitress on :5000.
* Spawns the **overlay WebSocket server** on port `8765` (threaded).

### Blueprint Modules
* `routes/generate_api.py` – REST wrapper around the generation pipeline.
* `routes/publish_api.py` – pushing assets to screens/buckets.
* `routes/bucket_api.py` – CRUD for S3-like buckets, listing, purge etc.
* `routes/scheduler_api.py` – high-level endpoints to create/edit/start/stop schedules.
* `routes/file_api.py` – general file upload/download helpers.
* `routes/test_*_ui.py` – lightweight HTML playgrounds for manual QA.

### Core Services
1. **Scheduler Engine** (`routes/scheduler.py` + utils)
   * Cron-like but millisecond-precision tick (
`SCHEDULER_TICK_INTERVAL = 2.0s`).
   * Persists state to disk so jobs survive restarts (exported variables in `scheduler/_vars.json`).
   * Supports complex triggers, conditions, and variable exports.
2. **Generation Pipeline** (`routes/generate.py`)
   * Loads & mutates ComfyUI workflow JSONs with runtime parameters.
   * Uploads reference images, negative prompts, LoRA settings etc.
   * Polls RunPod job status, synthesises weighted progress, and emits **overlay messages**.
   * On success saves images/videos with full EXIF/XMP metadata (`save_*_with_metadata`).
3. **Publisher** (`routes/publisher.py`)
   * Transcodes / resizes for target displays (`maxwidth`, `maxheight`).
   * Uses Samsung TV WebSockets or filesystem copy to push artwork.
4. **Overlay Relay** (`overlay_ws_server.py`)
   * Accepts messages from back-end _and_ external producers (e.g. RunPod containers).  
   * Broadcasts to any connected clients (front-end dashboards, TVs) and maintains job-specific progress queues.

### Utility Packages
* `utils/logger.py` – centralised coloured logging with in-memory buffer exposed at `/api/logs`.
* `routes/utils.py` – common helpers for file detection, JSON caching, etc.
* `samsungtvws/` – vendored fork of `samsungtvws` library with async & encryption extras; enables secure Frame TV control.

## 5. Frontend (React / TypeScript)
### Key Pages (Router)
* `/` (`Index.tsx`) – main prompt-driven generation UI (drag-and-drop, reference images, advanced options, live overlays).
* `/room`, `/music`, `/lights` (control only; setup at `/settings/lights`), `/settings`, `/jobs` – the app's other places. Each page is lazy-loaded as its own chunk (`src/App.tsx`).
* `/scheduler` – visual schedule builder & monitor.
* `/display` & `/display/:screenId` – lightweight, full-screen HTML pages that show the latest artwork for a given screen (the TVs load `/display/<screen>`).
* `/schema-edit` & `/schema-dynamic-form` – JSON-schema based editors for workflows and global settings.

### Context Providers
* `ReferenceImagesContext` – globally caches uploaded reference images.
* `LoopeViewContext` – manages display-mode toggles & full-screen refresh triggers.

### State-Management & Data
* **React-Query** for API calls (`src/shared/api/`, base URL in `src/shared/http/client.ts`; `src/services/`).
* WebSocket hook to `ws://<host>:8765` (same-origin `/ws/` over HTTPS) for live overlay & progress events.
* Drag-and-drop orchestration via `@dnd-kit/core` & sortable modifiers.

### UI Libraries
* **shadcn/ui + Radix** – menus, dialogs, sliders, accordions.
* **MUI** – data-heavy elements not yet ported to shadcn.
* **Tailwind** – rapid layout and responsive design; theming via `next-themes`.

## 6. Real-Time Overlay & WebSocket Layer
* All overlay messages share a common schema:  
  `{ screens?: string[], html: string, duration?: number, position?: "top-left" | "bottom", fadein?: number, job_id?: string }`
* `send_overlay()` in `routes/display.py` renders a Jinja2 template (e.g. `overlay_generating.html.j2`) with substitutions and pushes it through the relay.
* Front-end listens and displays overlays using a portal; TVs render overlays on-screen for progress or alerts.

## 7. Scheduler & Automation Engine
* **JSON-defined schedules** stored in `routes/scheduler/` (scripts in `routes/scheduler/scripts/`).  
  `{ cron: "0 * * * *", workflow: "latent-imagine.json", publish: "north-screen" }`
* Supports variable exports/imports so later jobs can reference earlier outputs.
* API endpoints allow creating one-off, recurring, or conditional tasks.
* UI offers manual trigger, pause/resume, and simulation modes.

## 8. AI Generation Pipeline
1. **Prompt & Parameters** – user chooses workflow, width/height, CFG, LoRA, refiner, etc.
2. **JSON Graph Mutation** – placeholders in the ComfyUI graph (`{{SAMPLER}}`, `{{LOAD-IMAGE}}`) are replaced at runtime.
3. **Remote Execution** – payload sent to designated RunPod endpoint (per-workflow GPU type).
4. **Progress Extraction** – RunPod returns `comfy` events; weighted progress is computed and emitted.
5. **Post-Processing** – upscaling, interpolation, video assembly.
6. **Publishing** – result copied to `output/` and optionally pushed to buckets or TVs.

## 9. Storage, Buckets & File Management
* Local assets live in `output/`: one bucket folder per destination (`output/<destination>/`, plus `_recent`), with the published file beside it (`output/<destination>.jpg|mp4` and its `.json` sidecar).
* Destinations are configured in `routes/data/publish-destinations.json` and buckets managed through `routes/bucket_api.py`.
* EXIF/XMP metadata written so downstream apps retain prompt history.

## 10. Configuration & Environment Variables
Important `.env` keys:
* `OPENAI_API_KEY` – GPT prompt enrichment.
* `RUNPOD_API_KEY` & `RUNPOD_ID` – default POD for generation.
* `GEMINI_API_KEY` – the AI listener and art tags.
* `ADMIN_API_TOKEN` – bearer token for the WAN admin API (`routes/admin_api.py`).
* `VITE_*`, `SM_LAN_HOST` – the addresses the app uses over plain http.

Runtime constants (`PORT` 5000, `WS_PORT` 8765, `DEBUG`, `STATIC_FOLDER`) reside in `config.py`.

## 11. Directory Structure Overview
```
├── app.py                 # Flask entry-point
├── config.py              # Global constants
├── overlay_ws_server.py   # WS relay
├── routes/                # All Flask blueprints & core engines
│   ├── scheduler.py
│   ├── generate.py
│   └── ...
├── scheduler/             # Persisted variables & helpers
├── output/                # Generated assets (git-ignored)
├── src/                   # React front-end (Vite)
│   ├── pages/
│   ├── components/
│   └── ...
├── dist/                  # Production React bundle (served by Flask; dist_prev/ is the rollback)
├── tests/                 # PyTest suites
└── requirements.txt / package.json
```

## 12. Development Workflow
### 12.1 Back-End Live Reload
Use the provided helper:
```bash
./start.sh start flask   # production Flask in the GNU screen session `flask`
```
In production restart Flask through its Spectre card (it restarts room_service too). `restart-flask.sh` is an old helper that `pkill -f`s `app.py`; don't use it.
### 12.2 Front-End HMR
```bash
npx vite --port 5174 --strictPort   # dev server (proxy /api to :5000); dev ports 5174-5199, see docs/PORTS.md
```
There is no Vite server in production: Flask serves `dist/`. Deploy with the build-and-swap in `CLAUDE.md`.
### 12.3 Linting & Formatting
* **ESLint** config in `eslint.config.js` (TypeScript rules).  
* Python linting via `flake8` (not enforced by CI yet).

## 13. Running Locally
```bash
# 1. Clone & install Python deps
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Install front-end deps & build
npm install
npm run build   # or `npm run dev` for HMR

# 3. Export env vars
export OPENAI_API_KEY=sk-...
export RUNPOD_API_KEY=rp_...
export RUNPOD_ID=<default-endpoint>

# 4. Launch
python app.py   # Flask on :5000, WS relay on :8765
```
Visit `http://localhost:5000` for the UI.

## 14. Deployment & Production Considerations
* **Docker** image not yet included – recommend multi-stage build (Python-slim + node:alpine).  
* SSL ends at Caddy on Windows (`https://screen.finsburybankforsavings.com`): `/` → :8080, `/rs/*` → room_service :8700, `/ws/*` → overlay socket :8765.
* Flask runs under **waitress** (64 threads) unless `DEBUG`.
* Off-load long-running tasks to **RunPod** or internal GPU cluster.

## 15. Testing Strategy
* `pytest` covers scheduler, generation utilities, and bucket interactions.
* Behavioural tests under `tests/behavioral/` cover the scheduler's stop behaviour.
* **Coverage** target ≥ 80 % (run `pytest --cov`).
* Front-end: TODO – integrate **Vitest** + **React-Testing-Library**.

## 16. Logging & Monitoring
* Unified logger in `utils/logger.py` streams to console, in-memory buffer (served at `/api/logs`), and a rotating file (`logs/screen_machine.log`, INFO; `SM_LOG_LEVEL=DEBUG` to dig).
* Front-end Console tab polls logs via React-Query.
* Production: ship logs to ELK/Loki via a standard STDOUT sink.

## 17. Security Considerations
* Secrets loaded via **dotenv**; never commit `.env`.
* Overlay WS is unauthenticated. There is no auth anywhere in the AV stack (room_service, stream_service, the display pages, `/room`) by owner ruling; only `/room` from off the premises asks for a password, at Caddy. The WAN-exposed Flask admin API keeps its token.

## 18. Roadmap & Future Improvements
* Dockerised production image & CI pipeline.
* Replace Flask static serving with S3 + CloudFront for UI assets.
* Role-based access control for the scheduler and logs.
* Vitest & Playwright coverage for front-end.
* GPU auto-scaling based on schedule density.

## 19. License
© 2024 Screen Machine. All rights reserved.  
See individual third-party library licenses within their respective folders.
