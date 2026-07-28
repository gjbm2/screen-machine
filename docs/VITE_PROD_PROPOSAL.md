# Proposal: Moving the Vite Frontend to Production

**Date:** 2026-07-28
**Status:** Proposed
**Companion doc:** [SECURITY_PROPOSAL.md](SECURITY_PROPOSAL.md) (this proposal implements its "serve a built frontend" step and sets up its end-state)

---

## 1. Executive summary

The fear that a production Vite build "breaks file access because FE & BE share live access to the same filespace" is **half right, and the half that's right is fixable with a one-line config change plus a small route**.

- **Right:** whatever origin serves the frontend page must also serve the live, backend-written `output/` tree over HTTP. Media URLs are origin-relative (`/output/...`), the display kiosks detect new art by HEAD-polling those URLs every second, and `output/` is 3.6 GB of continuously rewritten files — it cannot be copied into `dist/` at build time.
- **Wrong:** nothing in the browser touches the filesystem. The only true filesystem coupling is *Flask ↔ `output/`*. Any HTTP origin that serves `output/` with accurate `Last-Modified` headers and real 404s satisfies the contract — **and Flask already does exactly that** in its catch-all route ([app.py:146-148](../app.py#L146-L148)). The only reason Flask can't be the frontend origin today is that `STATIC_FOLDER = 'build'` ([config.py:11](../config.py#L11)) points at a directory that is not a React build, while Vite builds to `dist/`.

**Recommendation (Option A):** make Flask `:5000` the single production origin — SPA + `/output` + `/api` — and repoint the Windows `netsh` forward (`:8000 → :5000` instead of `:8080`). Kiosks keep their URLs unchanged. The existing `npx serve -s dist -l 5173` prod branch in `start.sh` should be deleted: it is broken four independent ways (§4) and its failure mode is *silently frozen screens*, not errors.

Two mandatory riders ship with this change: `DEBUG=False` and a production WSGI server (waitress), because the Werkzeug dev server + debugger must not be the internet-reachable origin for everything (§7).

---

## 2. How file access actually works today

### The clients

| Client | URL it loads | What answers it |
|---|---|---|
| Kiosk Chrome ×2 (TV displays) | `http://95.141.21.170:8000/display/{north,south}-screen` ([kiosk-loop.sh:30-31](../media-server/remote-scripts/kiosk-loop.sh#L30-L31)) | Windows `netsh` portproxy `:8000` → WSL `:8080` = **Vite dev server** |
| Dev browser | `http://<host>:8080` | Vite dev server |
| Alexa webhook, remote | ngrok `adapted-vervet-eternal.ngrok-free.app` | Flask `:5000` |
| Overlay/generation websockets | `ws://95.141.21.170:8765` (baked into bundle) | `overlay_ws_server.py` |

So **production traffic is already flowing through the dev server** — the TVs work only because Vite serves the entire project root off disk, which is what makes relative `/output/...` URLs resolve.

### The four URL surfaces

1. **Media (`/output/...`) — origin-relative.** The backend emits root-relative URLs ([bucket_api.py:287,627-628](../routes/bucket_api.py#L287)) and the FE fabricates more of them ([useFilePolling.ts:17](../src/pages/DisplayPage/hooks/useFilePolling.ts#L17), [RecentView.tsx:867-868](../src/components/recent/RecentView.tsx#L867-L868), [ImageCard.tsx:347](../src/components/common/ImageCard.tsx#L347)). The browser resolves them against the **page origin** — never against `VITE_API_URL`.
2. **API — absolute, baked at build time.** `VITE_API_URL=http://95.141.21.170:5000/api` from `.env` is inlined into the bundle ([api.ts:104](../src/utils/api.ts#L104)). **Trap:** if `VITE_API_URL` doesn't start with `http`, the entire `Api` class silently enters mock mode ([api.ts:108-109](../src/utils/api.ts#L108-L109)) — so it must *stay* absolute; do not "fix" it to `/api`.
3. **Stragglers — hardcoded relative `/api/...`** that today only work because of the Vite dev proxy: the display flow's output-file listing ([imageUtils.ts:33](../src/components/display/utils/imageUtils.ts#L33)) and AdminPanel ([AdminPanel.tsx:90-94,315,335](../src/pages/AdminPanel.tsx#L90-L94)). (`apiService.ts:16` also does this but is dead code — nothing imports it.)
4. **WebSockets — absolute, baked:** `ws://95.141.21.170:8765` (`VITE_WS_HOST`). Unaffected by where the SPA is served from.

### The load-bearing contract: mtime polling

The whole display-refresh mechanism is HTTP metadata:

- [useFilePolling.ts:24-28](../src/pages/DisplayPage/hooks/useFilePolling.ts#L24-L28) HEAD-requests `/output/<screen>.jpg`, `.JPG` and `.mp4` **every 1000 ms**, compares `Last-Modified` values numerically (also to pick jpg vs mp4 — a missing variant must 404), and renders `/output/<screen>.<ext>?t=<lastModifiedMs>`.
- The publisher rewrites `/output/<screen>.jpg` *in place* and touches it ([publisher.py:617-638](../routes/publisher.py#L617-L638)).

Therefore the FE origin must serve `output/` with: **accurate `Last-Modified`**, **real 404s for missing files**, **no stale caching**, and Range support for mp4. Flask/Werkzeug's `send_from_directory` provides all four (verified: `Cache-Control: no-cache`, mtime-based ETag, conditional requests, 404s — Werkzeug 3.1.3 in `.venv`).

### The backend fetch-back loop

Two FE paths absolutize `/output/` and `/api/` reference URLs with `window.location.origin` and send them to the backend, which then **fetches them back over HTTP** ([api.ts:172-177](../src/utils/api.ts#L172-L177), [image-utils.ts:61-65](../src/utils/image-utils.ts#L61-L65)). Whatever origin serves the page must therefore return *real file bytes* to the backend too. Under a misconfigured static host this failure is ugly: the backend ingests `index.html` as image bytes.

---

## 3. Why a naive static prod build breaks

Build `dist/` and serve it anywhere that isn't also serving live `output/` and `/api`, and:

1. Every image and video 404s (no `/output` on the origin).
2. The display pages freeze silently (HEAD polls get SPA-fallback `200 index.html` instead of media or 404s).
3. Display output-file listing and AdminPanel break (relative `/api` with no proxy).
4. APK freshness/download breaks: HEAD `/build/sdk/app-release.apk` ([BucketGridView.tsx:2122,2225](../src/components/image-display/BucketGridView.tsx#L2122)) *and* download link `<base>/sdk/app-release.apk` ([BucketGridView.tsx:2217](../src/components/image-display/BucketGridView.tsx#L2217)) — both live outside `dist/`.
5. `/app` deep-links throw a `TypeError` (`VITE_APP_BASE_URL` undefined at build — [AndroidApp.tsx:32](../src/pages/AndroidApp.tsx#L32)).

## 4. Why the existing `start.sh` prod branch is broken

The `SCREEN_MACHINE_MODE=prod` branch (`npm run build; npx serve -s dist -l 5173`, [start.sh:69-73](../start.sh#L69-L73)) fails four independent ways:

1. **Port collision:** `:5173` is bound by the dagnet graph-editor dev server (live-verified; dagnet defaults to 5173, so this recurs).
2. **The symlink dies on every build:** `vite build` empties `dist/`, destroying the hand-made `dist/output → output` symlink; nothing recreates it.
3. **`serve` refuses symlinks by default** (serve-handler `symlinks:false`; no `-S` flag is passed, no `dist/serve.json` exists — the repo-root `serve.json` is empty *and* ignored, since serve resolves config against the served directory).
4. **`serve -s` corrupts the polling contract:** missing `/output` files get SPA-rewritten to `200 index.html` with no useful `Last-Modified` — already documented in [SECURITY_PROPOSAL.md:52-56](SECURITY_PROPOSAL.md#L52-L56). Screens freeze with zero errors in any log.

---

## 5. Options

### Option A — Flask single origin (recommended)

Flask `:5000` serves everything: built SPA from `dist/`, live `/output` (as it already does), `/api`, and the APK. WS `:8765` unchanged. The Windows portproxy repoints `:8000 → :5000`; kiosk URLs unchanged; ngrok unchanged (and gains full SPA + media access remotely, which it lacks today).

```
Kiosks/browsers ── :8000 ──netsh──▶ WSL :5000 Flask ──▶ dist/ (SPA)
ngrok ───────────────────────────▶            ├──────▶ output/ (live, off disk)
                                              ├──────▶ /api
Kiosks/FE ── ws://…:8765 ────────▶ overlay_ws_server   └──────▶ build/sdk (APK)
```

**Pros**
- The mtime/404/no-cache contract is preserved byte-for-byte — Flask reads `output/` directly; no symlink, no copy, no second server.
- One origin fixes the hardcoded relative `/api` fetches **and** the backend fetch-back loop simultaneously (the backend fetches from itself).
- Smallest possible diff; no `:5173`/serve/symlink failure modes; no new daemon.
- ngrok clients get the SPA + media for free; `/app` deep-links stop 404ing.

**Cons**
- One process serves SPA + 3.6 GB media + API + scheduler + ~6 HEAD/s poll load → the Werkzeug dev server and `DEBUG=True` are disqualified; waitress + `DEBUG=False` are mandatory riders, not nice-to-haves.
- One elevated change on the Windows host (netsh repoint) — outside the repo; rollback needs the same access.
- Doesn't fix the baked-public-IP hairpin for API/WS (unchanged from today; see §8).
- mp4 scrubbing under concurrency is Flask-grade, not nginx-grade — acceptable for this deployment; if it stutters, that's the trigger for Option C.

### Option B — Repair the `serve` + symlink design (not recommended)

Keep two origins: `serve` for SPA+media (moved to `:8080` to reuse the existing forward and dodge `:5173`), Flask for `/api`. Requires: recreating the symlink after every build, `--symlinks` + a `dist/serve.json` with etag/cache overrides, *and* FE code changes for the relative-`/api` callsites. Even then, `serve -s`'s SPA-200-for-missing-files behavior must be re-verified per release — its failure mode (frozen screens, backend ingesting HTML as image bytes) is silent. **Disqualified** unless a HEAD of an absent `/output/x.mp4` demonstrably returns 404 — and the repo's own analysis says it doesn't.

### Option C — Real reverse proxy (Caddy/nginx) on `:8080` (the graduation path)

Caddy serves `dist/` with SPA fallback, serves `/output/*` straight off disk (kernel-grade static serving, native Last-Modified/404/Range), and reverse-proxies `/api` + `/build` to Flask. No Windows change (reuses `:8000 → :8080`). This is the best long-term server and the natural home for TLS + auth ([SECURITY_PROPOSAL.md](SECURITY_PROPOSAL.md)'s end-state), but it adds a daemon to install and supervise under WSL2 for benefits the deployment doesn't need yet. **Adopt if/when** Flask-under-waitress proves too slow for media (mp4 stutter) or when TLS/auth work begins. Everything in Option A (STATIC_FOLDER=dist, absolute `VITE_API_URL`, env fixes) carries forward unchanged.

---

## 6. Migration plan (Option A)

Each step is independently verifiable; the kiosk-visible cutover is a single netsh change with a one-line rollback.

1. **Flask serves the real build:** [config.py:11](../config.py#L11) `STATIC_FOLDER = 'build'` → `'dist'`. (Flask anchors this to the repo root — the directory containing `app.py` — regardless of cwd, so no start-script coupling.)
2. **Keep the APK reachable** (the implicit `/build/*` static route disappears with step 1):
   ```python
   @app.route('/build/<path:p>')          # FE freshness HEAD: /build/sdk/app-release.apk
   def serve_build(p):
       return send_from_directory(str(ROOT_DIR / 'build'), p)

   @app.route('/sdk/<path:p>')            # FE download link: <base>/sdk/app-release.apk
   def serve_sdk(p):
       return send_from_directory(str(ROOT_DIR / 'build' / 'sdk'), p)
   ```
3. **Env for the build** (in `.env`, which Vite reads at build time): add `VITE_APP_BASE_URL=http://95.141.21.170:8000`; leave `VITE_API_URL` and `VITE_WS_HOST` absolute exactly as they are (mock-mode trap, [api.ts:104-109](../src/utils/api.ts#L104-L109)).
4. **Strip the Lovable dev script** from [index.html:23](../index.html#L23) (`cdn.gpteng.co/gptengineer.js` — third-party JS on every kiosk page load, not mode-gated).
5. **Fresh build:** `npm run build`. The current `dist/` is from 2025-07-18 — stale. Delete the `dist/output` symlink and the empty repo-root `serve.json` (both are now dead weight that invites misdiagnosis).
6. **`start.sh` prod branch:** delete the `npx serve -s dist -l 5173` line; prod = Flask (+ ngrok + WS thread it already starts). Optionally have prod mode run `npm run build` before starting Flask.
7. **Smoke-test on `:5000` before touching the forward:**
   - `GET /display/north-screen` → SPA HTML (index fallback works)
   - `HEAD /output/north-screen.jpg` → 200 with accurate `Last-Modified`
   - `HEAD /output/north-screen.mp4` (absent) → **404, not index.html** ← the critical one
   - `GET /build/sdk/app-release.apk` and `GET /sdk/app-release.apk` → APK bytes
   - `GET /api/publish/north-screen/display` → JSON
8. **Cutover (Windows host, elevated):** `netsh interface portproxy set v4tov4 listenport=8000 ... connectport=5000` (was 8080). Kiosks pick it up at the next scheduled hard reload (02/06/10/14/18/22h) or force via the `media-server/` SSH scripts. **Rollback = repoint to 8080 and restart Vite dev.**
9. **Verify end-to-end:** publish a new image; both screens swap within ~1 s; overlay appears via `ws://…:8765`.
10. **Hardening riders (same change window):** see §7.

Dev workflow is untouched: `npm run dev` on `:8080` still works, and its `/api` proxy to `:5000` still works.

## 7. Mandatory hardening riders

- **`DEBUG=False`** ([config.py:17](../config.py#L17)): the Werkzeug interactive debugger on a port that is both publicly forwarded and ngrok-tunneled is an RCE vector. `use_reloader=False` is already set, so nothing else depends on debug mode.
- **Production WSGI server:** swap `app.run(...)` for waitress (`waitress.serve(app, listen='0.0.0.0:5000')` in `app.py`'s `__main__`, WS thread started as today). This process now fronts all media traffic.
- **Rotate `.env` secrets:** it holds live OpenAI/RunPod/AssemblyAI keys *and* is the same file Vite reads at build time. Split FE-safe `VITE_*` values into `.env.production` so secrets and build inputs stop cohabiting.
- **CORS + auth:** `CORS(app)` is wide open and no `/api` route has auth while ngrok exposes all of it. After single-origin consolidation, cross-origin access is only needed from the dev Vite origin — tighten to that, and gate mutating routes reachable via ngrok.
- **Path traversal check:** [file_api.py:10-27](../routes/file_api.py#L10-L27) validates FE-supplied paths by *string-prefix* against cwd — verify `..` and sibling-prefix (`configs_evil`) bypasses are rejected before this stays internet-reachable.

## 8. Known debts this proposal does not fix (tracked, deliberate)

- **Baked public IP:** API/WS URLs hairpin via `95.141.21.170` inlined at build time — an ISP IP change forces a rebuild. Fix belongs to the Option C/TLS phase (relative URLs require first removing the mock-mode trap).
- **Stored URL rot:** mp4 publishes persist absolute `http://…:5000/api/...` thumbnails in `bucket.json`/sidecars ([publisher.py:447-462,493-508](../routes/publisher.py#L447-L462)); the shipped APK hardcodes a previous IP. Sweep stored JSON after migration; prefer emitting relative URLs backend-side going forward.
- **Doc drift:** [README.md:206](../README.md#L206) claims dev on `:5173`; [DEPLOYMENT_IP_CONFIGURATION.md:28](../DEPLOYMENT_IP_CONFIGURATION.md#L28) mislabels `:8000` as ngrok; [check-status.sh:198](../media-server/check-status.sh#L198) calls `:8000` "Flask". Fix alongside the migration so the next debugging session isn't misled.
- **Dead fallback:** `'/static/placeholder.jpg'` ([publisher.py:462,816](../routes/publisher.py#L462)) has no serving route in any topology.
