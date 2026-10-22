# Screen Machine: refactor and code-organisation plan

Final, 2026-10-11; display layer revised 2026-10-13. Written for the owner, who will carry it out with AI assistance. **Status: P0 (security and memory hotfixes) was deployed and verified on 2026-10-11 at 15:01; everything from P0.5 on is proposed.** **Owner ruling (2026-10-14, ~08:15): feature breadth over enterprise-grade QA. Build straight into the localhost deployment, give individual features a crash check, and keep real tests for the general harness and system only (§1). It overrides the labs, staging copies, soaks, shadow exits and per-feature QA below; the phases have not been rewritten to match yet.** **Display layer (2026-10-13):** the owner gave two directions for the display pages that day, both relayed by the streaming session and quoted in §1: first, one page with an active control layer on a dumb mini-PC; second, an art/live compositing split (art mode is composited in the page's DOM; live mode is composited by the live renderer on the workstation GPU, the page only plays a full-viewport 4K60 stream with nothing over it, and live is full intensity). `docs/DISPLAY_CONTROL_LAYER_DESIGN_2026-10.md` is now the canonical display-layer design (Approach A: evolve today's `DisplayPage` in place, with grafts from C and B), and this plan has been brought into line with it. **Where the two conflict on the display layer, the design doc wins.** bd's update writing the second direction into the design doc landed at 20:58 on 2026-10-13 (revised 21:19), and this plan follows it; its timings (stall, exit and delete) are taken as provisional until bd confirms the doc final. Its §5 decisions (22 items; D27) are open: the owner has not answered them yet, except item 9 (mask during live), which the second direction resolves, and item 22 (4K60 against Game Mode), which is moot. Item 20 (overlays and the info panel during live) has no default. **Kiosk (2026-10-13 19:31 UTC):** the mini-PC now runs Wayland (sway): a pre-made 4K60 HEVC file plays on both TVs (0.03% dropped), and live 4K60 still drops 6–7% from workstation pacing; the X11 setup is the disabled fallback (§3.5). **TVs and room AV (2026-10-13, late evening):** Samsung IP Control is paired and live on both TVs (§3.5), and Game Mode is never used (D24). `docs/AV_PROGRAMME.md` (screen-machine-10) asks for room scope inside this plan's existing phases; that is recorded as proposals pending the owner's AV-D1 and AV-D6 (§5.2, D28), except AV-D2 (a Windows UDP relay for Art-Net), which the owner has decided and which resolves D22. **Repo cleanup (2026-10-14):** screen-machine-3d tidied the repo root and split `src/utils/api.ts` into `src/shared/` with mock mode removed (staged, not committed or deployed); T7 and Appendix A mark what is done. The 2026-10-11 plan itself brings together three earlier proposals (an incremental strangler, a target-first design and the streaming critical path; not the design doc's Approaches A–C), three judges' reviews and an adversarial verification pass (C1–C15). Where a proposal's claim was refuted, the corrected fact is used, and Appendix C lists the corrections.

---

## 1. Executive summary

### What is wrong (verified)

1. **Prod runs from the dev checkout and writes into git.** `routes/scheduler/*.json` and `scheduler/_vars.json` are tracked and rewritten all the time. Any `git stash`, `checkout` or `pull` in `/home/gjbm2/dev/screen-machine` would wipe live state. bd's streaming lab (`experiments/stream-latency`, running on :8081) and the main session's hotfixes share that same tree.
2. **Memory was leaking (fixed and deployed in P0).** The single `python3 app.py` process had reached 5.7 GB RSS after about 78 days. The main cause was the unbounded `console_logs` / `_recent_logs` in `utils/logger.py` (C10). They are now bounded, and after the restart the process sits at about 160 MB. Watch the slope over the next days to confirm there is no second leak.
3. **Anyone who could reach the server could execute code on it (the worst holes are patched; the structure is not).** The scheduler API is unauthenticated, and its templates were rendered with unsandboxed Jinja, giving SSTI/RCE (C4). `/test-overlay`, `/api/publish` and bucket copy could read arbitrary files into public places (C3). The WS server on :8765 relayed any client's HTML to every display (C2). P0 closed these without changing app behaviour. Still open: the APIs remain unauthenticated, WS frames can still be spoofed, and ports 5000, 8000, 8080, 8765 and 2222 are forwarded from the WAN while the kiosks use the public IP. The owner's "network reach" control is therefore not actually in force until P3.
4. **WS sends cross event loops.** `send_overlay` writes to sockets owned by the WS loop, either from the scheduler's loop or from throwaway loops on waitress threads. Under backpressure websockets 15 aborts the connection (C1). It has not shown up in 44 h of logs, but a slow Wi-Fi kiosk is exactly what triggers it.
5. **"What's on screen" is written non-atomically while kiosks poll it 3×/s.** The pointer is written with `copy2` plus `touch` (C12), and `bucket.json` read-modify-write has neither locks nor atomic writes. A kiosk can fetch a torn file, and `load_meta` can hit a half-written JSON.
6. **The scheduler blocks and loses data.** Generation runs synchronously inside a destination loop for up to 20 min (C13). Initial actions run twice on load (C7). Every event is appended to history twice, so the live files hold 50 unique events in 100 slots (C8). During a `wait` the full state is rewritten 15–20×/s under a global lock (C9).
7. **The backend has no structure.** All of `routes/` is one 24-module import cycle held together by about 170 function-level imports. `routes/utils.py` is a 990-line grab-bag. Paths depend on the current working directory (C15), and importing `app` starts the live schedulers (C11).
8. **The frontend has no data layer.** `api.ts` is a 2.4k-line singleton with a mock-mode trap (split into `src/shared/` and mock mode removed on 2026-10-14; T7). About 15 uncoordinated pollers run, and window `CustomEvent`s carry cross-component state. One 3.36 MB bundle serves every route, including the kiosk. The kiosk masks its leaks with hard reloads at 2/6/10/14/18/22h.
9. **There is no safety net.** Running `pytest` from the repo root has live side effects (it clears north-screen overlays, can hang while holding :8765, and resumes schedulers if `app` is imported outside `tests/`). There is no CI, no linter (≈34 undefined names reach prod paths) and no frontend tests.
10. **Streaming prerequisites are partly in place.** Hardware decode is enabled on the N95: VA-API was applied on 2026-10-11 (vainfo lists H.264, HEVC Main/Main10, VP9 and AV1), and it is **proven in the production Chromes** (2026-10-13: 4K30 HEVC played in both under X11, then 4K60 under sway, and Linux Chrome has no software HEVC decoder). The mini-PC has been on **powerline Ethernet since 2026-10-13** (about 170–190 Mbit/s, loaded ping p99 about 41 ms; a 24 h soak is running), with its weak Wi-Fi (15–20 Mbit/s) kept as a fallback. Still missing: the kiosk page loads from the public-IP origin (hairpin; Private Network Access for `ws://` to a private IP unverified; no secure context for WebCodecs), and nothing provides a per-screen channel to attach a live player to. **Smooth motion and 4K60 solved on the kiosk, 2026-10-13 (bd):** the single X screen spanning both TVs presented only about 60 frames/s in total. One X screen per TV (ZaphodHeads, about 18:31 UTC) fixed 4K30 (about 0.25% dropped video frames in both production windows), but 4K60 video still dropped 74–82% under X11. At 19:31 UTC the kiosk moved to **Wayland (sway 1.9)**, where Chrome 136's zero-copy video path works: a pre-made HEVC 4K60 file at 30 Mbit/s played on both TVs with 0.03% dropped and rAF at 60.0 with 0 late frames, and the kiosk passed reboot, Chrome-kill, TV-missing-at-start and unplug tests. The X11 setup is kept as the disabled fallback service (§3.5, P3 step 5). Live 4K60 from the lab generator still drops 6–7%, a workstation-side pacing problem (P6A, P6B). Owner priority: **4K picture quality matters more than ultra-low latency** (large screens, viewers stand close), and live is to stream at 4K60 (second owner direction below).

### Recommended direction

**Owner ruling on scope and QA (2026-10-14, ~08:15, relayed to all agents by screen-machine-04).** It postdates everything else here, the display design doc included, and wins wherever they conflict:
- "you don't need layers within layers for a SITE SPECIFIC APPLICATION ... we should be pushing for FEATURE BREADTH not ENTERPRISE GRADE QA"
- "individual renderers etc. really do not [need testing] beyond basic 'do they crash' status. if they're shit, I'll just rip them out"
- Earlier that morning (08:05), stopping a peer's end-to-end test run: "wtf are you running e2e tests? stop it. fucking waste of effort."

What it changes in this plan (the phases below have not been rewritten yet, so read them through this list):
- **Build straight into the localhost deployment.** No labs, forks, staging copies or dev stack. Dropped: P2's release pipeline (`/srv/screen-machine`, `releases/<sha>`, generated `sm.env`, `deploy.sh`) and its 72 h soak, the dev stack on 5001/8766 (§3.8), the staging stream service and V4 staging kiosk (§3.9), and P6A's lab-to-copy graduation. Work lands in this tree and goes live on restart.
- **Individual features, renderers, overlays and transports get a "does it crash" check only:** it starts, shows or serves something, and logs no exception. Dropped: per-feature characterisation goldens and vitest suites, multi-round or adversarial reviews, end-to-end and integration test runs (unless the owner asks), GitHub Actions CI, the import-linter and cycle ratchets, tsc/eslint ratchets as gates (the counts stay as information), and the 24–48 h per-variable kiosk soaks and 7-day shadow exits (P3, P4, P4b). One kiosk change at a time, south first, stays as a habit.
- **The general harness and system keep real tests:** the display page's control core (reducer, ControlClient), the realtime hub protocol (snapshot, epoch, acks), the Flask core and scheduler, publishing's atomic pointer write and the HEAD-poll mtime contract, and Siren. P1's guard that stops tests reaching prod sockets and SSH stays; it matters more now that work lands in prod.
- **Kept because they are not QA:** security work (P3 network reach, the admin token, the template sandbox), P2's correctness fixes (such as the atomic pointer write, C12), and rollback where it is already cheap (a flag, the previous launch command). Rollback is no longer a precondition for every phase.
- **One part of P2 stays, made cheap:** prod keeps running from this checkout, so untrack and git-ignore the live state (`routes/scheduler/*.json`, `scheduler/_vars.json`) in place, so that `git stash`, `checkout` or `pull` can no longer wipe it (item 1 above).
- **Approvals are unchanged:** kiosk, TV and Windows changes still need the owner's OK.
- The ≈30–41 owner-day estimate (§5.1) predates the ruling and is too high; re-estimate once the phases are rewritten.

**Owner direction for the display layer (2026-10-13, relayed by the streaming session):**
1. "re-architect the current display html pages so that they adapt their display logic to show a live mp4 when triggered … an active control layer e.g. WS-powered … not reliant on the 3s poll for light sensor updates"
2. "safest to have all the control machinery in one layer rather than having to have a separate control layer that re-points to different web pages. The mini-pc is meant to be dumb … the light sensor … only an accident".

**Second owner direction for the display layer (2026-10-13, later the same day, relayed by the streaming session):**
3. "art mode: all the compositing happens in chromium / DOM. live mode: all the compositing happens in live renderer (local GPU) and then \_all\_ the display browser should do is stream mp4 4K at 60fps. our new display page architecture needs to reflect that."
4. "live mode is full intensity — it's a lighting rig at that point, not a simulated artwork".

Here *art mode* means the page showing stills and clips (`mode: still|clip`), not the TVs' Samsung Art Mode. What follows from it:
- **Art mode is unchanged** and composited in the DOM: stills, clips, the brightness mask and warm tint, overlays, and the through-black still-to-still transition.
- **Live mode:** the page shows one full-viewport `<video>` (MSE, 4K60) and nothing over it: no DOM overlays, controls/info panel, mask, warm tint, HUD, CSS effects (opacity, filter, transform, blend) or page-side crossfades. Every effect and transition in live mode is rendered on the workstation GPU into the stream. Live is full intensity: the mask and warm tint are art-mode only.
- **The art ↔ live switch is a hard cut between matching frames:** the renderer's frame matches the art still at the cut.
- The WS control layer stays in the page in live mode but draws nothing.
- **Settled by bd's design update (20:58, 2026-10-13; design §2.5):** a stall hard-cuts to the still (an unmatched failure cut, never a page fade), and recovery re-runs the entry handshake (re-anchor). **Still open** (no answers assumed here): overlays and the info panel sent to a screen that is live, an owner decision with no default (D27 / design §5 item 20; until it is made the page draws none in live mode, drops them and counts them in `overlays_suppressed`). Ambient light and the live renderer is settled in design §4/§2.6: no ambient influence on the live renderer between handshakes; the renderer gets the mask only at each matched cut.

`docs/DISPLAY_CONTROL_LAYER_DESIGN_2026-10.md` implements both directions (bd added the second at 20:58 on 2026-10-13) and is the canonical display-layer design. Where it conflicts with this plan on the display layer, the design doc wins. Its §5 decisions (22 items, including the reorder as item 21) are open (not yet answered by the owner; D27), except item 9, which the second direction resolves, and item 22, which is moot; item 20 has no default. In short: one page per TV (today's `/display/<id>`, evolved in place) owns the control socket, stills, clips, live video, the art-mode mask and art-mode overlays; in live mode it only plays the stream; the server decides and the page reports what is on glass; the mini-PC stays a dumb browser host; the light sensor is a remote input wherever it is plugged in. This drops the iframe/`/player` contract, the local light loop on the mini-PC (Tier 2), the kiosk agent and the page rebuild with its parity port (Appendix C).

Strangle, don't rewrite, and order the work backwards from the screen. The hotfixes are already live (P0, done 2026-10-11). Next, ship the display page's leak fixes and a hold-last mask, so later server restarts stop blacking the screens (P0.5). Then build a test harness that physically cannot touch prod (P1), and separate prod from the dev tree with a typed settings seam, a minimal composition root and a batch of behaviour-neutral correctness and performance fixes (P2). P1 and P2 are a hard gate: no server change below ships before them. After that, the path to a live stream is: an owned-loop realtime hub with v2 routed by path, then shadow acks from the display pages (P4) → light push, Tier 1 (P4b) → overlays, then content, by push, plus supervision that replaces the timed reloads (P5) → live on devtest, then production live with bd's stream service, productionised in parallel (P6B, P6A). LAN origin and network reach (P3, mostly ops) run alongside and must finish before production live. Each display step is switched on per screen by a server-sent `policy` flag and switched back in seconds. New code is born in `sm/` and `stream_service/`, fenced by import-linter. Legacy entry points keep their signatures and delegate inward. Everything that does not serve the screen becomes a pausable track after the feature: the scheduler engine, storage, jobs, the frontend data layer and dead code. The path to production live is roughly **30–41 owner-days** (about 32–47 if the Room-AV proposal in §5.2 is adopted and P3 reach gates the claim API), and light push is on glass after about **18–25** owner-days plus the 7-day shadow window (P4b can be built during it), with a coherent stopping point after every phase. The whole programme is about 75–100 person-days.

### Framework recommendation

- **Keep Flask 3 + waitress** for REST, the SPA and `/output`. The kiosk contract depends on its Last-Modified/HEAD/Range/404 semantics.
- **Restructure websockets 15 into an owned-loop realtime hub** inside the web process. Every other thread reaches it only through `loop.call_soon_threadsafe`. This fixes C1 on any host; ASGI is not needed for it.
- **Run media in a separate aiohttp process** (`stream_service/`, graduated from bd's lab, which is already aiohttp), LAN-bound, with ffmpeg/NVENC subprocesses. Kiosks attach to it directly. Media never travels on :8765 and is never proxied through waitress.

Why not ASGI (uvicorn + Starlette + a2wsgi) now:
- Its main benefit is a same-port WS, which nobody needs once the frontend derives URLs at runtime.
- Media has to be out of process anyway.
- The long-held waitress threads are fixed by `/api/jobs` (T5), not by swapping servers.
- Porting about 360 routes under a new server before any test net exists is pure risk.

Re-evaluate ASGI if any of these triggers appears:
- (a) remote browsers need one HTTPS origin for WebCodecs;
- (b) many long-lived HTTP streams (SSE) would pin waitress threads;
- (c) the T6 scheduler runner wants to share the hub loop.

When a trigger fires, the move is mechanical because producers see only the bridge interface. Also: **no broker** (NATS/Redis), **no microservices** beyond the stream service (Room-AV proposal pending AV-D1/AV-D6: stream service + room service; §5.2), **SQLite only for the job registry**, and the filesystem stays the source of truth for media.

---

## 2. Guiding principles

1. **Every phase ships and every phase rolls back** (2026-10-14 ruling: where rollback is cheap; it is no longer a precondition; §1) without restoring backups: a symlink flip, a server-sent per-screen `policy` flag (with `?ctl=legacy` as a static override) or another feature flag, a kiosk URL edit (maintenance only, such as P3's LAN origin; principle 11), or a previous launch command kept as a drop-in.
2. **Pin behaviour before moving code** (2026-10-14 ruling: only for the general harness and system; individual features get a crash check; §1). Characterisation tests come before any seam is strangled. New code gets unit and contract tests. No phase "fixes while moving" without a test and an owner sign-off.
3. **Behaviour-neutral by default.** Changes visible to the owner are listed per phase under "Behaviour changes" and need a decision (§6). That includes fixing a bug that live schedules might depend on, and reviving a dormant feature by fixing a NameError.
4. **Legacy calls new, never the reverse.** `routes/*` may import `sm/*`. `sm/*` may import `routes/*` only through named `sm/legacy/*` adapters, each with a deletion phase. `stream_service/` imports nothing from the repo except `schema/`.
5. **Three planes stay separate.** Control: small, ordered, snapshotted JSON. Media: binary, lossy, LAN-direct. Files: `/output` stays disk-as-truth.
6. **Fail to still, never to black.** Any live failure reverts the screen to the last published still or clip, by a hard cut (never a page fade), and raises a Siren alert.
7. **Security comes from network reach, not from IP checks.** netsh portproxy makes every client look like the Windows host, so source-IP trust inside WSL is meaningless. The controls are closed router forwards, listener separation, and machine tokens only on paths that must cross the WAN. No login, no kiosk or operator tokens. Proposed exception, pending the owner's yes: a token on the mask write endpoints (§3.7).
8. **Single owner for every mutable thing.** A socket, a state file, a cache: each has exactly one writer, and every in-memory container has a maxlen or TTL (review rule).
9. **Copy Siren.** `utils/alerts` is the house pattern: a never-raise API, a single-owner worker, bounded queues, atomic state, and tests with seams.
10. **Critical-path rule.** Before the feature, the only legacy touches are the ones listed in §5.1. Everything else waits in a track.
11. **One page, dumb host, sensor is a remote input** (owner directions 2026-10-13; design §2.1, I1–I3).
    - *One page:* in normal operation each kiosk Chrome loads exactly one page, `/display/<id>`. That page owns the control socket, screen state, stills, clips, live video, the brightness mask, warm tint and overlays (art mode only), and health reporting. Nothing navigates it, re-points it, or iframes another app page into it. Admin `set-url` and same-URL reloads are maintenance or rollback tools only. D23's sandboxed overlay iframe sits *inside* the page as a containment box, so it is compatible.
    - *Art/live compositing split* (second owner direction, §1 items 3–4): in art mode (stills and clips) all compositing happens in the page's DOM, unchanged: base, mask, warm tint, overlays and the through-black still-to-still transition. In live mode all compositing happens in the live renderer on the workstation GPU, into the stream; the page shows only one full-viewport `<video>` (MSE, 4K60) with nothing over it (no DOM overlays, controls/info panel, mask, warm tint, HUD, CSS effects (opacity, filter, transform, blend) or page-side crossfades), and live is full intensity. The art ↔ live switch is a hard cut between matching frames (the renderer's frame matches the art still at the cut). The control socket stays connected in live mode but draws nothing.
    - *Dumb host:* the mini-PC runs only sway 1.9 (Wayland, `kiosk.service`, since 2026-10-13 19:31 UTC), two Chromes (one per TV, `--ozone-platform=wayland`), their launcher `kiosk-wayland.sh` (fixed URLs, one Chrome per TV, keeps north on the north TV when south is missing, restarts a Chrome that exits), deadman, network scripts (including the Ethernet path watchdog) and the EDID/output-mode fallback watchdog. The X11 setup (one X screen per TV, openbox on each, a single unclutter on `:0.0`, `kiosk-loop.sh`) is the disabled fallback `kiosk-x11.service`. The host holds no display, mask or content logic and runs no CDP agent. The light relay also runs there today as an independent sensor node (D27 item 19); the kiosk does not depend on it.
    - *Sensor is remote:* the light relay is a host-agnostic publisher configured only by server URL, sensor name and an optional key. Nothing on the kiosk reads it locally.
12. **Local degrade, server upgrade** (design I4–I5). The page may degrade *toward the still* on its own, immediately, and must report it. Every upgrade or retry is a server decision, except the bounded in-session stall recovery in §3.5 (design §2.5: after 2 s with no new presented frame the page hard-cuts to the still; the player stays attached, and after 3 s of healthy frames the entry handshake re-runs (re-anchor); 3 fallbacks in 10 min → `failed`; never a page fade). Black appears only for `mode: blank` or before the first content after a cold boot: a lost server holds the last picture and, in art mode, the last mask (D14); a playing live stream does not depend on core once attached (§3.1); and a crash shows black, never white.

*Room-AV proposal pending AV-D1/AV-D6:* changes to principles 5–8 (a show plane and a device-command plane; stream failure versus commanded darkness; authenticated room control and service-token claims; single ownership of devices) and new principles on physical safety and TV control (see §5.2).

---

## 3. Target architecture

### 3.1 Process topology

```
                         Internet
   Alexa ──┐   RunPod worker ──┐ (progress: polling or per-job-HMAC ingest)   OpenAI/AssemblyAI (outbound)
           │                   │
   router: ALL forwards closed except tunnel → 127.0.0.1:5100 (ingress) ; VPN for remote operator/Android
           │
 ┌─────────▼──────────── Windows host 192.168.1.205 (netsh portproxy, LAN-only rules) ────────────────┐
 │ WSL2 Ubuntu: every server in a named GNU screen session, started by its project script (Spectre)   │
 │  sm-web  (screen `flask` via start.sh; /srv/screen-machine/current, one python process)            │
 │   ├─ waitress :5000   REST /api, SPA (index.html) incl. the display page /display/<id>, /output    │
 │   ├─ waitress 127.0.0.1:5100  ingress app: /api/alexa (+ /ingest/runpod if chosen) ONLY            │
 │   ├─ realtime hub thread (own asyncio loop) :8765  control plane only, max_size 1 MiB              │
 │   │    v2 by path: /v2/screen/<id>, /v2/ingest/<kind>, /v2/operator; legacy / handler unchanged    │
 │   ├─ ChannelService (screen state) · SensorService · KioskRegistry + KioskMonitor · Siren worker   │
 │   ├─ StreamClient reconcile (5 s)                                                                  │
 │   ├─ scheduler loops (per-destination threads until T6; then one engine loop)                      │
 │   └─ generation executor (bounded, T5)                                                             │
 │  live renderer (CUDA, workstation GPU): composites all live content → sm-stream                    │
 │  sm-stream (screen via scripts/ctl, LAN :8702*)  sessions API · per-client queues · NVENC          │
 │  state: /srv/screen-machine/{state,config,output,logs}   releases/<sha>, current → releases/<sha>  │
 └──────────────────────────────────────────────┬─────────────────────────────────────────────────────┘
                                                │ LAN (powerline Ethernet since 2026-10-13; Wi-Fi fallback)
 ┌──────────────────────────────────────────────▼──────── media server 192.168.1.10 (Ethernet) ───────┐
 │ addresses: .10 powerline Ethernet (primary) · .92 Wi-Fi fallback and admin/SSH                     │
 │ kiosk.service → sway 1.9 (Wayland, since 2026-10-13) → kiosk-wayland.sh → 2× Chrome 136            │
 │    (launch per TV, respawn; no app logic) · disabled fallback: kiosk-x11.service (X11, kiosk-loop) │
 │    http://sm.lan:5000/display/{north,south}-screen?kiosk={north,south}   (one page per TV)         │
 │    page: control WS → :8765/v2/screen/<id> ; media WS → :8702 (direct) ; CDP 9222/9223 (localhost) │
 │    art: page composites still/clip, mask, overlays · live: full-viewport <video> only              │
 │ deadman.timer · network scripts (incl. Ethernet path watchdog) · EDID/mode fallback watchdog       │
 │ sensor node (currently on the mini-PC): light-relay → ws://sm.lan:8765/v2/ingest/lux               │
 └────────────────────────────────────────────────────────────────────────────────────────────────────┘
 * ports per the register `docs/PORTS.md` (owned by screen-machine-ca; D16): stream_service :8702, room_service :8700, show bus :8701 (reserved,
   localhost only). :8081 and :3001 belong to exlib (Spectre); bd's lab must not use :8081 while exlib-mobile runs (prefer 8082–8099).
```

In live mode the workstation composites everything the TV shows (the live renderer and NVENC on its GPU), and the kiosk only decodes and presents the stream (principle 11). Live playback does not depend on core once attached. A core restart or a blocked scheduler thread must not interrupt a playing stream. Splitting the hub or the scheduler into their own processes later is possible, because producers see only the bridge. The light relay is a sensor node, not part of the kiosk: it runs wherever the sensor is plugged in (today the mini-PC, over USB) and talks only to the server (principle 11).

### 3.2 Backend package tree and dependency rules

```
app.py                     thin entry: create_app(load_settings()); __main__: start_services(); serve()
config.py                  shim re-exporting sm.settings (deleted in T8)
overlay_ws_server.py       LEGACY: v2 path router added to ws_main (P4); legacy / handler unchanged byte-for-byte; deleted T8
sm/
  settings.py              frozen dataclass from SM_* env; ALL paths/ports; defaults = today's paths; stdlib only
  app.py                   create_app(settings): Flask app, blueprints, error handlers; NO side effects
  runtime.py               start_services/stop_services: alerting, hub, schedulers, executors (composition root)
  selfcheck.py             deploy smoke: settings, config files present, schedule scripts vs schema, overlay templates
  core/                    atomic.py (tmp+fsync+os.replace, per-path locks), templating.py (SandboxedEnvironment),
                           ids.py, clock.py, casting.py, qr.py   ← split from routes/utils.py (T3)
  realtime/                protocol.py (generated), hub.py, server.py, bridge.py, ingest.py, metrics.py
  channels/                model.py, service.py (claims/leases), store.py
  streaming/               client.py (StreamClient), reconcile.py
  kiosks/                  registry.py (KioskRegistry: acks, kiosk.state, /glass), monitor.py (KioskMonitor: alerts, restart ladder)
  destinations/            registry.py (single loader, schema-validated, public_view())
  api/                     screens.py, health.py first; later buckets, publish, jobs, files, admin
  display/  (P4b, T3)      mask.py (pure compute_mask), sensor.py (SensorService; P4b), overlays.py (template render, routes/overlays only)
  storage/  (T4)           bucket_store.py
  publishing/ (T4)         service.py
  generation/ (T5)         jobs.py (SQLite), runpod.py, service.py, comfy_graph.py (pure), api.py
  llm/ (T5)  integrations/alexa/ (T5)  audio/ (P2/T8)  devices/ (samsung: DevicesPort (IP Control reads; samsungtvws actions), T2; mediaserver, T8)
  scheduler/ (T6)          model, store (single writer), lifecycle, events, triggers, runner, actions/, api
  ports.py                 typing.Protocol ports (JobsPort, PublishPort, ScreensPort, DevicesPort, Clock) for T2 (DevicesPort) and T4–T6
  legacy/                  anti-corruption adapters wrapping routes.*; each docstring names its deletion phase
routes/                    LEGACY; shrinks module by module; scheduler*.py deleted last (T6); empty at T8
stream_service/            SEPARATE deployable: own requirements.txt + unit; imports only schema/
  api.py sessions.py supervisor.py admission.py encoders/nvenc_ffmpeg.py sources/{synthetic,file,still,pipe}.py
  mux/{fmp4.py,h264_sps.py} transports/{base,ws_fmp4,webcodecs_ws,llhls_mediamtx}.py player/ health.py tests/
schema/                    realtime, channel, stream-api, destinations JSON Schemas → codegen (TS + Python), CI drift check
deploy/                    deploy.sh rollback.sh dev-stack.sh sm-{room,stream}.env windows/ backup job
                           kiosk/{kiosk.service (sway),wayland/ (kiosk-wayland.sh + generic respawn, sway-kiosk.conf),
                                  kiosk-x11.service + kiosk-loop.sh + xorg/ (X11 fallback),webview-manager.sh,deadman.py,
                                  network/,install.sh}  (no agent)
                           sensors/light-relay/ (host-agnostic relay; runs wherever the sensor is plugged in)
scripts/                   manual/ (old root test scripts, pair_tv, samsung_control), ops/, migrations/ (idempotent importers)
tests/                     unit/ contract/ characterization/ integration/ legacy/ fixtures/
experiments/               bd's lab; export-ignored; never collected by pytest; never deployed
utils/alerts/              unchanged except the health→scheduler inversion (T3)
```

**Dependency rules** (import-linter in CI from P1, applied to `sm/` as it appears):
- `settings` imports only the stdlib. `core` imports `settings`.
- `realtime` imports `core` and its own `protocol` only: no Flask, no features, no `routes`.
- Feature packages (`channels`, `streaming`, `kiosks`, `destinations`, `display`, `storage`, `publishing`, `generation`, `scheduler`) import `core`, `ports`, `realtime.bridge`, `utils.alerts` and other features' public modules only. They never import `realtime.hub`.
- `api/*` imports only its own feature service.
- `sm.app` and `sm.runtime` may import anything.
- `routes.*` may import `sm.*`. `sm.*` imports `routes.*` only from `sm/legacy/*`.
- `stream_service` imports only `schema/`.

**Enforcement beyond import-linter:**
- ruff `F` rules blocking. ruff `TID251` bans `os.getenv`/`os.environ` outside `settings.py` (in `sm/`), raw `jinja2.Environment` anywhere, and `subprocess(..., shell=True)`.
- `mypy --strict` on `sm/` and `stream_service/`.
- A CI ratchet on the legacy import cycle (24 modules, about 170 function-level imports) that may only go down.

### 3.3 Frontend structure

```
index.html → src/app/       operator SPA: main.tsx, App.tsx (ONE QueryClientProvider), React.lazy per route
src/pages/DisplayPage/      THE display page for /display/:screenId, evolved in place (no separate entry, no /display-legacy)
  index.tsx, MediaDisplay    art mode, composited in the DOM as today: still/clip, info panel, trigger buttons, fit/fill,
                             ?nomask/?nocolour/?display=fill; all inside ArtStack, hidden while live
  control/   ControlClient.ts (one v2 socket per page: hello, hb watchdog, backoff+jitter; ?ctl=legacy read first),
             reducer.ts (pure; epoch-scoped rev; snapshot authoritative; compares by content key), protocol types;
             stays connected in live mode but draws nothing
  live/      LiveLayer (one full-viewport <video>; in live mode nothing is drawn over it), MseLivePlayer.ts (owns <video> +
             MediaSource imperatively, not React; playbackRate never above 1.0, resync by a hard jump), liveMachine.ts
             (entry, exit and stall handshakes, design §2.5; planned cuts match, failure cuts are unmatched hard
             cuts); inline drivers only: wsFmp4Mse, testPattern;
             experimental transports as lazy inline chunks
  overlays   art mode only: legacy HTML rendered as today; scripts run ONCE per overlay id, removed with it (P0.5)
  mask       art mode only (live is full intensity): brightness and warm layers written through refs inside rAF
             (outside React); hold-last + offline schedule (D14)
  health/    kiosk.state + acks, reloadPolicy.ts (health-gated), pollFallback.ts (degraded mode); ErrorBoundary per layer
src/shared/  realtime/ (WS client used by control/ and later by the operator UI), protocol/*.gen.ts,
             http/client.ts (landed 2026-10-14 with NO mock mode; to come: relative /api for http pages in P3 (https pages already use it), typed errors, timeouts), config/runtime.ts, lib/urls.ts (mediaUrl)
src/features/ screens/ChannelPanel (new, P6B); later generation, buckets, recent, viewer, scheduler, admin, lightsense (T7)
src/components/ui/  shadcn primitives
```

Rules (eslint `boundaries` / `no-restricted-imports` / `no-restricted-syntax`):
- The display page's new `control/` and `live/` modules import only `src/shared` (and each other through typed interfaces).
- Features import `shared/*` and never each other's internals.
- New code may not use `CustomEvent`/`window.dispatchEvent`, `fetch(` outside `shared/http`, or `src/data/*.json` imports for runtime config.
- `DisplayPage/control`, `DisplayPage/live`, `src/shared` and new feature folders are strict TS from day one. tsc and eslint baselines are ratchets: 24 and 407 errors after the 2026-10-14 `api.ts` split (41 and 439 on 2026-10-11).
- The display page's background (`html`/`body` on `/display/*` routes only, P0.5 step 4) is black, so a reload or crash never flashes white (today it uses the light theme). The operator UI is unchanged.
- Rollback for the display page is a server-sent `policy.*` flag per screen, with `?ctl=legacy` as a static override read before any v2 code runs (design §3). Admin `set-url` stays a maintenance tool.
- The ≤300 KB gzip bundle budget for the display page (against today's 3.36 MB for everything) is deferred to T7, because the page now shares the SPA bundle.

### 3.4 Real-time control plane

**Ownership.** `sm/realtime/hub.py` runs on its own thread and loop, started by `start_services()`. Nothing else touches a websocket. Any thread calls `bridge.publish(...)`, which uses `loop.call_soon_threadsafe` and never blocks or raises. These go away (the last two with the legacy `/` handler in T8, which stays unchanged until then):
- the loop-guessing in `routes/display.py:254-273`;
- the leaking loop in `routes/audio_utils.py:139-151`;
- `connection_registry.py`;
- the custom `ping_client`/`passive_keepalive`.

On the v2 paths, keepalive is the library's built-in ping plus the app-level `hb` below.

**Delivery.**
- Each v2 client gets a bounded `asyncio.Queue(256)`, a writer task and a 5 s send timeout.
- Overflow policy per type: the retained types (`policy`, `screen.state`, `mask.set`) keep only the latest value; `overlay.*` drops the oldest; `job.progress` is coalesced per `job_id` and `stream.health` per session. A persistently slow client is closed (4008) and resyncs on reconnect.
- One slow kiosk can no longer stall the others. Today's fan-out is serial (`overlay_ws_server.py:246-279`).
- `max_size` is 1 MiB, enforced; the v2 paths also cap inbound frames at 64 KiB (below).

**Envelope and ordering.**
- Every frame carries `epoch` (random for each hub start) and, for retained types, a `rev` counted per type per screen (persisted and monotonic for `screen.state`). There is no `base_gen` chain. Ordering and freshness rely on `rev`, never on comparing `ts` across hosts (clocks on different machines are never exactly aligned; see P3 step 7). Latency measurements use a skew-fitted sync.
- State-like types are retained per screen.
- Every (re)connect receives a full snapshot of retained state for its path in `welcome`. There is no replay log.
- **The snapshot is authoritative**, even when its `rev` is lower than what the client holds (for example after `state/screens/` is restored, or after a core restart). The client resets its rev baseline on a new epoch. Within an epoch, a client ignores a retained message with `rev ≤` the one it has applied. The page compares by **content key** (base URL and rev, live `session_id`, overlay ids), never by rev, so a reconnect never remounts a still or a live stream that has not changed.
- After a core restart, `welcome` is held for at most 3 s until one reconcile pass has run, and a live session not yet confirmed is sent as `phase: "adopting"` (design §2.5).

**Routing, handshake and roles** (design §2.3 has the full message set).
- **v2 is routed by WebSocket path on :8765**, not by a `hello` on the legacy socket: `/v2/screen/<id>` (display pages), `/v2/ingest/<kind>` (`lux`, `worker`, `audio`) and `/v2/operator` (read-only summaries). Only a path router goes into `overlay_ws_server.ws_main`. *Why:* today's handler treats any first JSON frame as a *sender* and relays it to every viewer (`overlay_ws_server.py:138-177`), so a v2 `hello` on `/` would be broadcast to all screens. websockets 15.0.1 exposes `request.path` and `serve(origins=…)`.
- v2 clients send `hello{v:2, role, client_id, build, caps, have}` on their path. Roles: `kiosk` (the page carries `?kiosk=north|south`, set by the kiosk launcher: `kiosk-wayland.sh`, or `kiosk-loop.sh` on the X11 fallback; only kiosk clients count as what is on glass), `viewer` (any other browser on `/display/<id>`: receives pushes, does not attach live unless `?live=1`, never raises alerts), `operator`, `sensor`, `worker`, `audio`.
- Retained types per screen: `policy` (which sources are push, poll or legacy, `live_enabled`, the offline mask schedule), `screen.state` and `mask.set`. Overlays come back in the snapshot with `remaining_ms`.
- **Liveness:** the server sends `hb` after 2 s idle; the page treats 6 s with no inbound frame as a dead socket and reconnects (catches a powerline link that is up while the path is dead). A kiosk silent for 60 s is stale.
- **Acks:** `ack{kind, rev, phase}` (for example `mask` `applied`, or `stored` while live; `state` `visible`, `fallback`, `recovered` or `failed`; plus the `anchor` ack of the entry handshake, design §2.5) supersets `kiosk.state`; acks affect only observability.
- **v2 clients have no publish operation.** Operators command over HTTP.
- **Inbound is role-scoped:** a kiosk may send only `hello`, `ack`, `kiosk.state` and `hb`; a sensor only `hello` and `ingest.lux` (its 1 Hz heartbeat is an `ingest.lux` repeat with a new `seq`, so it needs no `hb`); ingest roles receive nothing but acks and `cfg`. Inbound frames are capped at 64 KiB and 50 msg/s (otherwise close 4008). `screen_id` is validated against the destinations registry; lux is clamped and a backwards `seq` rejected.
- **Origin allowlist** (`serve(origins=…)`) for the browser roles; an optional per-sensor key, because the v2 paths are reachable from the WAN until 8765 closes.
- **HTTP twin and fallback:** `GET /api/screens/<id>/state` (same reducer input, used in degraded mode every 15 s), `GET /api/screens/<id>/glass` (desired vs applied), and `POST /api/kiosks/state` (every 30 s while the WS is down but HTTP works).
- Typed ingest handlers translate `{job_id, comfy}` into `job.progress`, held in a bounded TTL ProgressStore (the legacy `job_progress_listeners_latest` goes with the legacy handler in T8). Lux on `/v2/ingest/lux` becomes one `ingest.lux` per report.

**Legacy compatibility.**
- **The legacy `/` handler stays unchanged byte-for-byte** until T8, including its classifier (0.5 s of silence → legacy viewer; first frame `{type:'audio'}` → Android producer; any other first JSON → legacy sender). This keeps the deployed kiosk bundles, the operator `Index.tsx`, the RunPod worker, the light relay and the Android app working unchanged.
- Legacy viewers keep receiving exactly what they receive today: every broadcast message, legacy-shaped. They never say which screen they are, so the server cannot filter for them; per-screen filtering applies only to v2 clients on `/v2/screen/<id>`. `send_overlay` keeps feeding the legacy path and also publishes to v2 through the bridge.
- Legacy senders are translated into typed ingest. The translation and the per-shape logging tap the helpers the handler already calls (`broadcast_lux_level`, `send_overlay_to_clients`, `_record_job_progress`); "byte-for-byte" covers the `/` handler function and its classifier, not those helpers (H2(a) already changes `send_overlay_to_clients`). **During an evidence week, the current sanitised relay (`_sanitize_relayed`, which strips `html`/`substitutions`) stays in place.** Per-role and per-message-shape traffic is logged at the same time, to identify the unknown client that fails the handshake every ~5 s and anything else that depends on the relay.
- The relay is removed only once that log shows no consumers (T8).

Until WAN :8765 is closed, typed ingest can still be spoofed (job progress, lux, `generation_update`). That is acceptable only as a transition, which is why P3/P4 close it.

**Producers.** Events are emitted at existing choke points, alongside legacy output:
- `send_overlay` → `overlay.show/clear` per screen, rendered exactly as today (mirrored from P4, consumed from P5). The page renders overlays in art mode only; overlays sent to a screen that is live are an owner decision with no default (D27 / design §5 item 20); until it is made, the page draws none in live mode, drops them and counts them in `overlays_suppressed` (§3.5).
- Publisher, after the atomic replace → `on_published`, which bumps `screen.state.base` with `?v=<rev>` (P5).
- The generate progress loop → `job.progress/completed/failed`, matching the frontend's existing `AsyncGenerationUpdate` shape.
- SensorService and the mask routes → `mask.set` (P4b), applied by the page in art mode only and stored while live (§3.5b).
- Later (T4/T6/T7): `bucket.changed` and `scheduler.state` (coalesced to 1/s).

**Health.** `/api/health` reports clients by role and screen, queue depths, drops, loop lag and ingest rates. Siren alerts fire on loop lag over 1 s (`realtime.loop_lag`) and on a screen with no subscriber for more than 2 min (`realtime.no_subscriber`). The hub thread is supervised and restarted (`realtime.hub_restart`); while it is down the pages hold their picture and fall back to HTTP polling.

### 3.5 Media/streaming plane, the per-screen channel, and the display page's player seam

**Channel model (the seam).** `ChannelState` (sent on the wire as the retained `screen.state`; design §2.4) is the single source of truth for what is on a screen:
- `mode` is `still | clip | live | blank`.
- `base` is a MediaRef to the last published item. It is **always the fallback**.
- `live`, when present, is `{session_id, phase: starting|on_air|leaving|adopting, visible, attach, rung, anchor{base_rev, mask_rev, clip_t}, exit{base_rev, mask_rev, hold_pts}}`: `anchor` is what was on glass at entry and `exit` the target the renderer converges to and holds (design §2.4–2.5). Every live phase has a timeout (design §2.5). For scheduled events the server may publish `visible:false` early so the page warms up and the cut is instant (design §2.5).
- `policy` holds the per-screen switches the server sends: `ctl` (`legacy|shadow|push`), `mask_source`, `overlay_source`, `content_source`, `live_enabled` and the offline mask schedule (D14). Every display step is switched on, and back, with these flags, so rollback never touches the mini-PC.
- `claims[]` is a list of `{owner, mode, expires_at}` with arbitration `base < scheduler:<dest> < api/alexa < operator`. The effective state is the highest unexpired claim, otherwise the base.
- A `rev` makes ordering explicit.
- The default rule is "live wins until its lease ends or someone stops it; a new publish becomes the base/fallback". This is decision D12.
- Leases mean a wedged legacy scheduler cannot leave a screen stuck on live. Scheduler stop or unload calls `release_owner('scheduler:<dest>')`.
- State is persisted atomically to `state/screens/<id>.json` and published retained on `/v2/screen/<id>` and `/v2/operator`. Every desired change and every observed transition is appended to `logs/screens/<id>.jsonl` with its cause (`actor`, `corr`), so "why was south black at 21:14?" becomes a grep.

**Base MediaRef is the pointer, not the bucket file.** The push path sends `/output/<dest>.<ext>?v=<rev>`. It does *not* switch stills to "immutable bucket file URLs", for two reasons:
- Today the screen survives deletion of the bucket file, because the pointer is a copy.
- Some publishes have no bucket file at all (`skip_bucket`, URL/temp sources).

`screen.state.base` is bumped only after `os.replace` has completed, so a push-triggered fetch never reads a torn file. If a later publish replaces the pointer before the kiosk fetches, the kiosk gets newer bytes and then the newer rev. That is harmless.

**Transport; presentation is inline only.**
- Transport is one of `ws-fmp4-mse | webcodecs-ws | webcodecs-mstg` (WebCodecs → MediaStreamTrackGenerator → `<video>`, a mode bd's lab measures) `| ll-hls | whep`. Production starts with `ws-fmp4-mse` only.
- **Live always plays inline in the display page** (owner direction 2026-10-13; D15). There is no iframe presentation, no page mode, no postMessage contract and no `/player?return=` navigation: one origin, and MSE-over-WS from the LAN origin needs no secure context. Nothing is drawn over the live video (second owner direction, §1): the mask, warm tint and overlays are art-mode only, and live effects are rendered into the stream. Experimental transports ship as lazy inline driver chunks and are tried on devtest via the V1 or V4 vehicles (§3.9). `/player` (`static/client.html`) stays a lab page only.
- The server picks one `attach` (transport, URL, codec, w/h/fps, `target_buffer_ms`) per screen from the session's `attach[]` (§4.6), the link-group budget and the kiosk's `hello.caps` (`mse{hevc,h264}`, `power_efficient`, max `{w,h,fps}`; §4.2), and sends it in `screen.state.live.attach`. If the page's driver cannot play it (`canPlay` false), the page acks `failed` and the server decides what to do next (design §2.3, §2.5).

**Display page PlayerHost** (design §2.2 and §2.5, updated for the art/live split at 20:58 on 2026-10-13; its timings are provisional until bd confirms the design doc final).
- **Art mode** (stills and clips), composited in the DOM and unchanged. Layers bottom to top: black `html`/`body` → ArtStack (z 2, opaque black), which holds base (`MediaDisplay`, still or clip, unchanged: easing, hold-final-frame and the 0.6× pace) → brightness (5) → warm multiply (6) → overlays (9999) → controls panel.
- **Live mode:** LiveLayer, one full-viewport `<video>` (MSE, 4K60), and nothing over it: no DOM overlays, controls/info panel, mask, warm tint, HUD, CSS effects (opacity, filter, transform, blend) or page-side crossfades. All effects and transitions are rendered on the workstation GPU into the stream, and live is full intensity. ArtStack is hidden; the overlay layer and the controls panel are unmounted while live, not merely hidden (design §2.2). The control socket stays connected (acks, `kiosk.state`, retained state) but draws nothing.
- **Planned switches are hard cuts between matching frames:** the renderer's frame matches the art still at the cut, so the cut shows no black or mismatched frame. Failure cuts (a stall, or the exit timeout) are unmatched hard cuts to the still, with no black frame either. There is no crossfade into or out of live.
- **Still open** (no answers assumed here): overlays and the info panel sent to a screen that is live are an owner decision with no default (D27 / design §5 item 20: the renderer draws them, they are dropped or queued for art mode, or a mix by type). Until it is made, the page draws none in live mode, drops them and counts them in `kiosk.state` (`overlays_suppressed`), and the server waits until no overlay is showing (an operator start clears them) before going live. Ambient light is settled in design §4/§2.6: no ambient influence on the live renderer between handshakes; the renderer gets the mask only at each matched cut.
- **Live warms up under ArtStack** (design §2.2): LiveLayer (z 1) sits under ArtStack. The entry cut hides ArtStack in one property change; a stall or exit cut shows it again over the video. **The base is never unmounted and stays decoded while live is shown,** and a clip base stays paused. If a covered video does not present frames (design §6 item 4), LiveLayer warms up above ArtStack at `opacity: 0.001`, removed at the cut, so nothing remains on the video while live; the reducer does not change.
- LiveLayer owns its `<video>` and MediaSource imperatively; React renders only the container.
- **Entry handshake** (design §2.5; replaces the 10-frame warm-up gate). At `starting` the server holds `mask.set`, overlays and base changes for that screen (a publish in the window updates only the server's `base`, and the page starts no still-to-still transition until the ack); the page lets any mask transition finish, closes the controls panel, pauses a clip base and acks the anchor `{kind: anchor, base_rev, mask_rev, clip_t, fit, drawn}`, which is exactly what is on glass (`drawn`: the mask as drawn, after `nomask`/`nocolour`). The renderer draws that still with the mask, using the page's formula, and holds it at 60 fps (hold limit 30 s). The page warms up under ArtStack until `readyState ≥ HAVE_ENOUGH_DATA` and at least `target_buffer_ms` is buffered, plays at 1.0, cuts in the rVFC callback for the first presented frame (the held anchor) and acks `visible` with `layer: live`. Only then is the renderer told to diverge (it ramps from the masked anchor to full intensity). Not ready within 8 s → `ack failed` and the screen stays on the still.
- **Playback rate:** the page never sets `playbackRate` above 1.0. It resyncs to the live edge by a hard jump (a seek), never by speeding up: at 60 fps on a 60 Hz panel any speed-up forces dropped frames (measured 2026-10-13, platform facts below).
- **Stall** (design §2.5; never a page fade): after 2 s with no new presented frame the page hard-cuts to ArtStack with the latest stored mask, in one frame (unmatched, a failure path; ack `phase: fallback`). The player stays attached under ArtStack and hard-jumps to the live edge when data returns. After 3 s of healthy frames it reports `recovered`, and the entry handshake re-runs (re-anchor: the server holds the mask, overlays and base changes, the page acks the anchor, the renderer re-anchors and returns `anchor_pts`, the page cuts on it, then the renderer diverges). If the stall triggers a rung step-down, there is no recovery on that session: the still is already on glass, so the server deletes it without an exit handshake and starts the lower rung through the entry handshake. 3 fallbacks within 10 min → `failed`: the screen stays on the still and the server decides.
- **Profile or rung changes are end-then-start** (the exit handshake to the still, then the entry handshake at the new rung; both cuts match) until the lab proves two decoders fit (design §6 items 5–6). No in-place `changeType`.
- **Leaving live** (design §2.5): the server fixes the exit target (the current base and the then-current mask) and holds `mask.set`, overlays and base changes until the exit ack; the page decodes the target (`img.decode()`) and applies its mask to the hidden ArtStack; the renderer converges to the target and holds it. The page cuts on the renderer's held exit frame (`hold_pts`); if that is not presented within 15 s of `leaving`, it hard-cuts anyway (unmatched, `reason: exit-timeout`). It acks `visible` with `layer: base`, then unmounts cleanly (abort and remove the SourceBuffer, revoke the object URL, `removeAttribute('src')` then `load()` to free VA-API, close the media WS). **The server deletes the session after the base ack, or after 20 s.**
- Every live state has a timeout, and an `adopting` barrier keeps a playing session across a core restart when the `session_id` matches.
- A page that connects while a valid claim exists starts on the still and runs the entry handshake, and the server re-anchors the session on what that page shows (self-heal); the retry budget resets on a new `page_load_id`, and no screen ever waits for another.
- A `testPattern` driver that can draw a given still and mask, hold it, diverge and converge proves the handshakes (still → live → still, the stall cut and the re-anchor) before the production renderer exists.
- The polling fallback and WS reconnects compare by content key and may update **only the base layer**. They never tear down or remount a playing live layer while the snapshot's state is unchanged.
- **Self-reload only when `/api/health` answers OK** (within 2 s), with backoff. This replaces the earlier "WS-dead self-reload after 10 min", so a server outage never turns into a reload loop.
- What each screen shows in every failure case (core restart, hub down, powerline up but path dead, failover to Wi-Fi, encoder crash, JS error, Chrome exit, TV off) is tabled in design §2.7.

**Stream service** (`stream_service/`; owned by screen-machine-04 (the renderer session, `docs/av/HANDOVER_RENDERER.md`) after screen-machine-2f's cut-over; 2f built it from bd's lab, and bd's lab (`experiments/stream-latency/`) stays bd's, on its own dev port):
- **All live compositing happens on the workstation** (second owner direction, §1): the live renderer and NVENC on its GPU produce every effect and transition the TV shows in live mode, including the held entry anchor and exit target that match the page at each planned cut (design §2.5).
- **Even frame pacing:** the renderer delivers frames evenly at the session rate (16.7 ms at 60 fps). In the 2026-10-13 live 4K60 run the lab generator missed that budget about 2% of the time (render plus a 12 MB pipe write per frame inside WSL), and the lab player then ran above 1.0× to catch up, which caused the 6–7% dropped frames on glass (P6A step 3; the page side is the playback-rate rule above).
- **Bandwidth:** two 4K60 HEVC streams at about 30 Mbit/s each (about 60 Mbit/s) fit the powerline link (170–190 Mbit/s measured) but not the Wi-Fi fallback (15–20 Mbit/s), so live degrades on Wi-Fi (D11 ladder).
- One supervised ffmpeg NVENC subprocess per session: `-delay 0 -zerolatency 1`, FLV-framed stdout, restart with backoff, a stall watchdog via `-progress`.
- 1 s GOP plus per-client drop-to-next-keyframe for joins. On-demand IDR waits for driver ≥570 (D21).
- **H.264 always passes through the SPS rewriter in the mux layer.** A contract test parses the SPS of every init segment.
- Native 4K is the target; at 4K60 the 16.7 ms budget is tight (the lab generator misses it ~2% of the time; see even pacing above). In the 4K30 work, 4K NV12 through a 1 MiB pipe cost about 8 ms per frame (measured), and capture to encoded is about 26 ms at 4K. Upscaling from 1080p is a bandwidth-degraded fallback, not the default.
- Admission control: one live session per screen, a configurable NVENC session cap per GPU (consumer GeForce drivers cap concurrent sessions), and GPU selection per profile.
- Sessions carry `ttl_s` (at least 120 s, longer than a core restart) and must be renewed by core's reconcile loop, so orphans die if core is gone.
- The service signals `ready` once the first IDR is muxed, reports per-client backlog and drop-to-key stats in `/api/state`, and sends a clean new init segment after an encoder restart (P6A step 4).
- `POST /api/sessions/{id}/source` switches the source inside one continuous encoder output, so there is no keyframe rejoin and no player remount.
- LAN-bound. **No app auth and no RFC1918 filter**: behind netsh portproxy the filter does nothing, so reach is controlled by not forwarding the port.

**Control-plane side.**
- `StreamClient` uses 2–3 s timeouts.
- `reconcile.py` runs every 5 s against `/api/state`. It renews TTLs, adopts sessions after a core restart (`adopting`), deletes orphans, and on a missing or stalled session falls back to base and raises an alert. On leaving, the page cuts on the renderer's held exit frame (`hold_pts`), or hard-cuts after 15 s (unmatched, `reason: exit-timeout`); a session is deleted only after the page acks the base, or after 20 s (design §2.5).
- The rung is chosen per **link group** (north and south share one mini-PC link) from its budget and the kiosks' `hello.caps`. If a page acks `failed`, or sends no ack within 10 s, the server retries after 10 s, 30 s, 2 min and 5 min (at most 5 per 30 min), then leaves the screen on the still and raises `live.client_failed` (design §2.5).
- `ChannelService.go_live` is **idempotent**, keyed by `(screen, owner, spec hash)`. This matters because legacy schedulers can run initial actions twice (C7), and because retries happen.
- Clips (mp4 files) stay on the static plane as `mode: clip`.

**Known streaming risks to measure, not assume:**
- Renderer frame pacing at 4K60 (workstation side; see the stream service above). Live 4K60 on glass is proven only once the renderer paces evenly and the page never plays above 1.0×.
- MSE SourceBuffer growth over 24/7 playback. Drivers trim buffers and release them on unmount.
- Presented frames on the kiosk: solved on 2026-10-13, first for 4K30 under X11 (about 0.25% dropped), then for 4K60 under Wayland (a pre-made HEVC 4K60 30 Mbit/s file on both TVs: 0.03% dropped, rAF 60.0 with 0 late frames; live 4K60 from the lab: 6–7% dropped, from workstation pacing). Still to confirm: a 30-minute live run at the primary profile, and why the lab's runs after an in-page session change presented 0 frames (design §6 item 5).
- *(Out since 2026-10-13: the cost of `mix-blend-mode: multiply` over 4K video and the opacity-only live fallback. Nothing is drawn over live video.)*
- NVENC session limits and GPU contention with other work on the box.

**Verified platform facts that constrain the media plane** (bd's lab, checked against the Chrome 136.0.7103.113 and FFmpeg n6.1.1 sources; README at `experiments/stream-latency/README.md`). **The canonical record of the kiosk's hardware, display mode, Wi-Fi, powerline plan and WSL clock, with verify and undo commands, is `docs/KIOSK_INFRASTRUCTURE_NOTES.md` (bd); where it differs from this plan, it wins.**
- **Hardware decode is a hard prerequisite for 4K live, not an optimisation.** In low-delay mode Chrome's software decoder uses slice threading only, so a single-slice 4K H.264 stream got about 6 fps and fell 1–2 s behind even on a Xeon. Linux Chrome has no software HEVC decoder at all.
- **VA-API on Chrome 136/X11:** only `--enable-features=AcceleratedVideoDecodeLinuxGL` is needed (`VaapiVideoDecoder` and `VaapiVideoDecodeLinuxGL` are ignored since M131). Chrome opens `/dev/dri/renderD128` itself, so the kiosk user must be in `render`. Put every feature in **one** `--enable-features` switch, because a repeated switch overwrites the first. **Under sway (Wayland, since 2026-10-13)** the kiosk Chromes run with `--ozone-platform=wayland` and, by default, the zero-copy path (`KIOSK_VIDEO=zerocopy` in `/etc/default/kiosk-wayland`: `--use-angle=gl-egl` plus `AcceleratedVideoDecodeLinuxZeroCopyGL` in the same single switch; `copy` is the alternative). Zero-copy works there: decoded frames go to the GPU as they are and the VideoEnhance engine stays at 0%. Under X11 the same path crashes (below).
- **MSE low-delay rendering comes from the init segment.** Chrome buffers 1 frame instead of 4 only if `mvhd` duration is 0 and there is no `mehd`. FFmpeg needs `-movflags empty_moov`; setting `MediaSource.duration=Infinity` does nothing. `frag_every_frame` adds a frame of latency, because it flushes on the next packet, so use a custom muxer or `frag_custom` plus an explicit flush. hls.js also uses MSE, so LL-HLS inherits both this rule and the H.264 SPS reorder issue. VA-API ignores WebCodecs `optimizeForLatency` for reordering.
- **Display path.** **Since 2026-10-13 both TVs run 3840x2160@60** (Game Mode bullet below). Under sway both outputs (HDMI-A-1 north, HDMI-A-2 south) are set to 3840x2160@60 in `sway-kiosk.conf`; check them with `swaymsg -t get_outputs` (with `SWAYSOCK` set; notes §3), because X11 tools (`xrandr`, `wmctrl`, `DISPLAY=`) no longer apply. sway logs "Failed to parse EDID" for the TVs' 512-byte EDID; modes and 4K60 are unaffected. *X11 fallback:* the kernel offers 4K50/60 at 4:2:0, but Xorg 21.1.12 prunes those modes (MaxClock comes from the HDMI 1.4 range limits); the fix is Samsung "Input Signal Plus" per port, or an xorg.conf Monitor section with `Option "MaxClock" "600MHz"`. With 4K60 present the override is not needed today; re-check xrandr before ever adding it.
- **X11 history (now the disabled fallback `kiosk-x11.service`): presented frame rate across both TVs, fixed for 4K30 on 2026-10-13 (bd).** Cause: both TVs were one 7680×2160 Xorg screen (modesetting, no compositor), so the Chrome windows could never page-flip: about 60 frames/s in total, each Chrome about 30 fps with tearing and 31–34% of video frames dropped. Chrome flags and picom did not help. **Fix:** one X screen per TV (ZaphodHeads, about 18:31 UTC): `/etc/X11/xorg.conf.d/20-zaphod.conf` (repo `media-server/remote-scripts/xorg/`), **north = `:0.0`, south = `:0.1`**; `kiosk-loop.sh` starts openbox and Chrome on each screen plus **one** unclutter on `:0.0`, logs `per-TV X screens: 2`, and falls back to the single-screen layout if the conf is removed. **Measured:** a canvas motion test at 60 fps with 0 missed frames on both TVs; live 4K30 HEVC at 18 Mbit/s in both production Chromes dropped 0.28% / 0.24% over about 3 min (lab capture-to-screen p50 about 126 ms, plus the TV's own lag, ≈95–100 ms estimated without Game Mode; spike §10); 4K60 still failed (next bullet). **Rule on the X11 fallback:** anything that talks to X on the box must use `DISPLAY=:0.1` for south (`check-status*.sh`, `restart-kiosk.sh`, `remote-maintenance.sh` and the `routes/admin_api.py` status query were updated for X11). bd is now porting the status scripts, the admin API display check (`routes/admin_api.py`) and `deploy-kiosk.sh` to Wayland (uncommitted). Any `routes/admin_api.py` change goes live only at the next Flask restart, which is the owner's call, and should be reviewed first. **Hot-plug under X11:** with south absent at X start, north came up normally and south spawned within 1 s of reconnecting; both pages then ran at 60.0 fps. On the X11 fallback `kiosk-loop.sh` restarts **both** Chromes on any connector change (making that per-TV is optional, P5 step 3); re-check the equivalent under sway. Write-up: `docs/KIOSK_INFRASTRUCTURE_NOTES.md` §3 and `media-server/README.md` Critical config §7.
- **4K60 on both TVs: solved under Wayland (sway 1.9), since 2026-10-13 19:31 UTC (bd).** *Under X11 it failed* (18:52–19:02 UTC, owner-approved and watched): HEVC 3840×2160@60, 30 Mbit/s, MSE, both production Chromes, about 3 min, dropped 82% / 74% of frames with the pages drawing at 31–42 fps, because Chrome's X11 VA-API path converts every decoded NV12 frame to RGB on the GPU's VideoEnhance engine (97–100%), and its zero-copy path, which skips that step, does not play on X11 (no SharedImage backing for NV12; the GPU process exits). Decode, network and workstation all had headroom. **Now:** `kiosk.service` starts sway (`/usr/local/share/kiosk/sway-kiosk.conf`, repo `media-server/remote-scripts/wayland/`), which runs `kiosk-wayland.sh` with one Wayland Chrome per TV (same URLs, profiles and CDP ports 9222/9223). Chrome 136's zero-copy path works there: VideoEnhance at 0%. **Measured:** a pre-made HEVC 4K60 30 Mbit/s file on both TVs dropped 0.03% (3 of about 8,600 frames per TV), with rAF at 60.0 and 0 late frames. **Passed:** reboot (kiosk up in about 40 s), Chrome-kill respawn, a TV missing at start, and runtime unplug and replug. **Live 4K60 from the lab generator still drops 6–7%** (with rAF at 60.0, 0 late and VideoEnhance at 0%): a workstation-side problem, because the generator misses its 16.7 ms frame budget about 2% of the time and the lab player then plays above 1.0× to catch up, which at 60 fps on a 60 Hz panel forces drops. **New requirements:** the renderer paces frames evenly (P6A); the page never sets `playbackRate` above 1.0 and resyncs by a hard jump (P6B; this replaces any live-edge rate control that speeds up). Live 4K30 has not been re-run under sway. **Fallback to X11:** `sudo systemctl disable --now kiosk && sudo systemctl enable --now kiosk-x11` (4K30 only there). Write-up: `docs/KIOSK_INFRASTRUCTURE_NOTES.md` §3 "Wayland (sway) kiosk since 2026-10-13"; the X11 4K60 test is in `experiments/stream-latency/README.md` ("4K60 video on both TVs"). TV picture settings and Game Mode (D24) are separate; 4K60 does not depend on them.
- **Window stacking on the kiosk (X11 fallback; bd, 2026-10-13).** `kiosk-loop.sh`'s hot-plug respawn put the kiosk windows on top of the lab Chrome's window, and Chrome on X11 stops drawing a window that is fully obscured. Constraints for a lab-Chrome run over a TV on the X11 fallback (§3.9, V1 row):
  - check that the lab window is on top and unobscured before the run, and again after any hot-plug or kiosk respawn; a run during which a respawn happened is void;
  - the production window under the lab window stops drawing, so a lab-over-TV run does not measure the two-window production load (design §6 item 3b needs both production windows visible);
  - the TV shows the lab window, so it runs only in an owner-agreed window.
  - Under sway these X11 facts do not apply. The lab tools `lab-chrome.sh` and `fb_probe.py` are X11-only (notes §7), so under sway V1 is a production Chrome over CDP (§3.9).
- **TV latency is part of the budget, and the box path is fixed.** Both TVs are 2025 Frame Pros (QE75LS03FWUXXU; firmware read over IP Control on 13 Oct as T-RSMFDEUC-0090-1296.8, while `docs/FRAME_PRO_SETTINGS_2026-10.md` cites 0090_1301; to reconcile), necessarily fed wirelessly from their One Connect boxes, which adds about 35–45 ms even in Game Mode (rtings, TechRadar, id.nl, Tom's Guide). **Game Mode (D24): never used, live or art** (owner, 2026-10-13, after the IP Control setter test showed Samsung's Game Bar overlay and flicker: "we don't use game mode"; spike §10; design D-C). No transition may change picture mode, input or any TV picture setting. D24 had recommended Game Mode OFF on 2026-10-11 too, estimating a saving of only 25–35 ms; spike §10 corrects that: with the box path Game Mode would save an estimated 55–60 ms (rtings: 9 ms on the panel's own Micro-HDMI port in Game Mode, 69 ms outside it; the box's 35–45 ms is probably a Game Mode figure, and none is published for the box with Game Mode off), which the owner judged not worth it. Samsung's manual says it forces the Game picture mode and disables some functions. **Facts (notes §3; spike §10):** both TVs read Filmmaker Mode with Game Mode on Auto (Samsung IP Control, 19:16 and 19:37 UTC). Auto is inactive: it engages on an ALLM request (perhaps also on a "Game" HDMI content type, never tested), and the kiosk sends neither (no ALLM support on the box; wlroots hard-codes the connector content type to Graphics). Setting both TVs to Off is the owner's call, timed with bd. The HF-VSDB at 600 MHz (and with it 3840x2160@60) appeared around when the owner tried Game Mode, but 4K60 does not depend on it: all the sway 4K60 measurements were taken with Game Mode on Auto (inactive), and toggling Game Mode On and back at 19:36 UTC changed neither EDID nor the outputs. This closes the earlier D24 test. **4:4:4:** rtings found true 4:4:4 needs both the "PC" input label and Game Mode, so that route is out. Until 2026-10-13 the link was full RGB 8-bit at 4K30, so stills already arrived at full 4K; re-check the colour format at 4K60.
- **TV settings are manual, one-off and per set. The verified list is `docs/FRAME_PRO_SETTINGS_2026-10.md`** (bd, 2026-10-11, from Samsung's 2025 EU e-Manual BN81-27185C-660 for our model and rtings' 2025 Frame Pro tests, each claim re-checked against its source; it replaces an earlier list taken from other models). Samsung IP Control reads the picture settings and Game Mode (the IP Control bullet below; `docs/GAME_MODE_AUTO_SPIKE_HANDOVER.md` §10); Input Signal Plus has not been read over it. Summary:
  - **Input Signal Plus ON for the kiosk connector only**, at Settings > All Settings > General & Privacy > External Device Manager > Input Signal Plus. Off gives 4K50/60 only as 4:2:0 and PC-native 4K30; On gives 4K60 RGB/4:4:4 at 8/10-bit and PC-native 4K60. It mainly buys 60 Hz and HDR acceptance, not sharper stills. Change one TV first (a 594 MHz 'no signal' failure has been seen on other Samsung + Intel pairs), then verify from the mini-PC that an HF-VSDB appears (OUI C4-5D-D8, 600 MHz, SCDC) and that 4K60 is listed (`swaymsg -t get_outputs` under sway; on the X11 fallback xrandr lists it at 594 MHz). Baseline EDID and xrandr captures are in `docs/kiosk-captures/`. **2026-10-13:** an HF-VSDB at 600 MHz appeared and both TVs went to 3840x2160@60; it does not depend on Game Mode (above). Input Signal Plus itself has not been read from the TVs; the EDID behaves as if it is on.
  - **Picture (the list's recommendation; the TVs' current values differ, see the measured baseline below):** Movie mode (most accurate out of the box per rtings; 960 Hz backlight flicker versus 120 Hz in most other modes), Gamma 2.2, Colour Tone Warm2, Colour Space Auto, Contrast Enhancer Off, Picture Clarity Off, Auto HDR Remastering / Colour Booster / EyeComfort Off, Local Dimming Low (there is no Off), and HDMI Black Level matched to the PC's range (the PC sends limited-range RGB today). **Measured baseline** (IP Control readback, 13 Oct, both TVs identical; bd has recorded it in `docs/FRAME_PRO_SETTINGS_2026-10.md`): picture mode Filmmaker Mode, input HDMI1, Game Mode Auto, colour tone Warm2, gamma BT.1886, colour space Auto, local dimming Standard, backlight 40, contrast 45, sharpness 10, brightness 0, colour 25, tint 0; Auto Motion Plus, Contrast Enhancer, Digital Clean View, Brightness Optimisation and Film Mode Off. It was unchanged by the move to sway. Where it differs from the list, it is the owner's current choice and stands unless the owner changes it. **Drift checks compare against this measured baseline** (for example `config/tv_baseline.json`), not against the recommendation list above; if the owner changes a setting, the baseline is re-read. The "Graphics" HDMI content type that wlroots sends (X sent "No Data") changed none of the readable values (spike §10); whether it changes unreported processing is unconfirmed.
  - **Input label:** not "PC" (the 4:4:4 route needs PC plus Game Mode, which is never used, so it is out; D24).
  - **Power and AI off:** Adaptive Picture (AI Mode), Brightness Optimisation, Energy Saving Solution, Motion Lighting, Auto Power Saving, and Screen Saver if present. Auto Power Off: off, or the longest period.
  - **Interaction with our light response (§3.5b):** Samsung documents that Adaptive Picture (AI Mode), Brightness Optimisation and EyeComfort change brightness or colour from ambient light or the time of day, and that Motion Lighting changes brightness with on-screen motion. *Our inference:* left on, they would double-correct on top of the server's light-reactive mask and warm tint in art mode, and would alter the full-intensity live picture, so all four must stay off (the doc already sets them Off) for the mask to be the only brightness controller.
  - **Software updates reset picture settings** to defaults (Samsung). Either turn auto-update off or re-apply the baseline after each update (by hand, or by owner-approved IP Control writes). A read-only IP Control check against the measured baseline catches drift (`tv.settings_drift`; IP Control bullet below); the fallback watchdog below catches the Input Signal Plus part.
  - Things to confirm on the TV itself (port naming, whether Input Signal Plus persists, option names, Art Mode interplay on a 24/7 input) are listed in the doc. Because persistence is unconfirmed, the mini-PC needs a watchdog (EDID and output modes: `swaymsg` under sway, xrandr on the X11 fallback) that alerts on a fallback to 4K30 or 4:2:0.
- **Samsung IP Control is live on both TVs and is the TV adapter to build on** (spike §10, screen-machine-85). JSON-RPC 2.0 over HTTPS POST to `https://<tv>:1516/`. Both TVs are paired (the owner accepted the prompts at 19:16 UTC); the tokens are in `~/.config/screen-machine/ipcontrol/<ip>.token` on the WSL host (mode 600, outside the repo).
  - **Readable:** power, Art Mode, picture mode, input, Game Mode and the expert picture settings. Picture levels and input are readable only outside Art Mode (`-32601` in Art Mode; tooling README). Game Mode is settable. Input Signal Plus has not been checked.
  - **Reliability over the TVs' Wi-Fi:** about 2,400 calls, 2 empty replies, 0 timeouts; p50 about 660 ms per poll, slowest 3.5 s. Each TV serves **one connection at a time**, so calls are serialised (one client per TV), empty replies are retried, and timeouts are at least 5 s.
  - **Tooling:** `experiments/ipcontrol/` (`ipctl.py`, stdlib only: pair, get, survey, watch, set; its README has the protocol, the soak figures and the 13 Oct per-TV baseline), uncommitted.
  - **Use:** inside DevicesPort (`sm/devices/samsung.py`; T2), IP Control is used **read-only**: observed power, Art Mode, input and picture mode next to `/glass` (§3.8), plus settings-drift checks against the measured baseline (above). Drift checks run only when Art Mode is off and treat `-32601` or an empty reply as unknown, never as drift; the baseline includes FWVersion, so a firmware change is flagged and the settings are re-read. DevicesPort's only writes are the existing samsungtvws wake and Art Mode actions with the T2 fixes; **any IP Control write needs the owner's approval.** It fits the dumb-host rule: the server talks to the TVs, not the kiosk.
- **The wireless link may be compressed.** Sources conflict: Samsung Korea describes the Wireless One Connect box as using a "high-efficiency, high-compression codec" (in an 8K context), Samsung Chile says it carries 4K "without significant compression", and choose.tv saw PC text degrade over it. The panel's Micro-HDMI is the only route reviewers found clean. Before committing to stream quality targets, run a one-off A/B against the Micro-HDMI port with a 1-px checkerboard, gradients and dark grain.
- **GPU content generation in WSL** works through CUDA/CuPy (WSL has no OpenGL–CUDA interop). A 4K fractal frame takes 2.8 ms of GPU time on the 4060. Capture to encoded is about 26 ms at 4K and about 7 ms at 1080p.

### 3.5b Ambient light → screen brightness in near-real-time

**Today.** The Yoctopuce sensor is USB-attached to the mini-PC, which currently hosts it alongside both kiosks. Per the owner that is "only an accident": the sensor is a remote input, and nothing on the kiosk should depend on it being there (principle 11). `light-relay.service` sends lux over `ws://95.141.21.170:8765`, hairpinning mini-PC → router → Windows netsh → WSL. The server enriches it and appends it to `lux_history`; the EMA is advanced on **read** paths, so its time constant depends on how often kiosks poll. Each kiosk then **polls** `GET /api/<dest>/mask` every 3 s, which re-executes `config.py` on every call. Measured today, the relay reports about twice a second (median interval 0.52 s; p10 35 ms suggests duplicates or bursts). So the sensor-to-glass delay is roughly 0.5–3.5 s plus EMA lag. The enriched lux broadcast already reaches every kiosk over the WS; nothing uses it for brightness, and the overlay hook turns each frame into a phantom overlay (fixed in P0.5 step 1).

**Goal (owner requirement).** The screens respond to light changes within a few ms, bounded mainly by network latency, and are "not reliant on the 3s poll for light sensor updates" (owner, 2026-10-13).

**Art mode only** (second owner direction, §1: "live mode is full intensity — it's a lighting rig at that point, not a simulated artwork"). The page applies `mask.set` (brightness and warm tint) only in art mode; in live mode it draws no mask and the picture is full intensity. While live the page stores the latest `mask.set`, acks it `stored` and applies it at the next cut to art. During the entry, re-anchor and exit handshakes the server holds `mask.set` (and overlays and base changes) for that screen, so the page and the renderer agree on the frames either side of the cut; mask values written to the hidden ArtStack or at a cut are committed with no CSS transition (design §2.5–2.6). Mask lag and `mask.lag_high` are measured in art mode only. Settled in design §4/§2.6: no ambient influence on the live renderer between handshakes; the renderer gets the mask only at each matched cut. Everything below describes art mode.

**The latency floor is set by four things:**
- **Sensor reporting rate.** About 500 ms today. Fix it in the relay: report on change, or at up to about 20 Hz (the Yocto-Light-V5's maximum report rate, to verify on the device), using the Yocto API's configurable report frequency or value callbacks. The relay's source is not in this repo (it exists only on the box, `media-server/README.md:738-820`) and should come into `deploy/sensors/light-relay/`.
- **Network path.** LAN instead of the public-IP hairpin. Wired Ethernet removes the Wi-Fi jitter (D8).
- **Transport.** Push instead of a 3 s poll.
- **Smoothing.** Read-driven EMA instead of an explicit time-based filter.

**Tier 1, server push (the only tier; D25).** Path: relay (any host) → `/v2/ingest/lux` → `SensorService` → pure `compute_mask` → retained `mask.set` for every screen in the sensor's target group → the page applies it through refs inside rAF (art mode) → the page acks. Protocol and constants: design §2.6.
1. **Relay** (`deploy/sensors/light-relay/`, host-agnostic): configured only by `SM_INGEST_URL`, `SENSOR_NAME` and an optional `SM_SENSOR_KEY`. Yocto value callbacks on any change of at least 1%, up to 20 Hz (to verify on the device), plus a 1 Hz heartbeat (an `ingest.lux` repeat; §3.4). The server can retune it with `cfg` and acks at least once a second, so the relay detects a dead link. Legacy `{"sensor_name","lux"}` frames on `/` are still accepted until the relay is upgraded.
2. **Filter**, advancing **only** on ingest (today's EMA advances on every read): works in log-lux; *snap* with τ 0.15 s when the reading is more than 0.75 EV from the current state for 2 consecutive samples (which also rejects single-sample spikes); otherwise *drift* with τ 2.5 s; deadband |Δbrightness| ≥ 0.005 or |Δwarm_alpha| ≥ 0.002; at most 20 Hz per screen, each update carrying `transition_ms` of about 150 ms. All constants live in `config/light.json`, reloaded when its mtime changes, which keeps today's "edit the curve, see it in seconds" loop once `exec(config.py)` is gone. Defaults are D27 item 11.
3. A pure `compute_mask(dest_cfg, now, sensor_state, overrides)` (T3's refactor, pulled forward) computes the mask for each screen in the target group and pushes `mask.set{brightness, warm_hex, warm_alpha, transition_ms, source, srv_ms}`, retained per screen. Legacy `GET /api/<id>/mask` is served from the same `SensorService`, so screens still in poll mode get the better filter.
4. **Staleness and warm start.** Today a dead relay freezes the mask forever. New rule: no frame for 10 s (the heartbeat makes this meaningful) → `source: "solar"` with a 30 s ramp; Siren `sensor.stale` after 60 s; a 5 s ramp back when readings return. After a core restart the persisted `last_mask` is used until the first fresh sample.
5. **Offline** (the page cannot reach the server; D14): hold the last mask for 10 min, then follow `policy.offline.schedule`, about 12 h of solar-curve points that the server precomputes and refreshes every 10 min and the page interpolates on its own monotonic clock. This is data, not ported logic. Hold-last ships first, into today's `useMask` (P0.5); until P4b it holds the last mask until the server answers again, because the 10-min limit needs the schedule.

While connected the page polls the mask only as a 30 s safety check (≤ 2/min, P4b exit; T8 drops it to 60 s); the 3 s poll is the fallback when the WS is down. Expected sensor-to-glass time is the sensor interval plus about 5–15 ms on wired LAN (two hops plus compute). On the kiosk's powerline link (since 2026-10-13) the hop is about 3 ms idle and p99 about 41 ms while the link is saturated by a bulk transfer (measured); on its Wi-Fi fallback it was 70–220 ms under load. It needs only the P4 hub core and shadow acks, with no channels or content push, so it is the first push topic and proves the hub.

**Tier 2 (a local loop on the mini-PC) is dropped** (owner direction 2026-10-13; D25). It would have put a localhost relay socket, a TS port of `compute_mask` and brightness policy on the dumb host. Resilience when the server is down now comes from hold-last plus the server-precomputed offline schedule (item 5).

**Acceptance** (design §2.6; art mode only):
- Sensor report → style commit, measured on the server from the ack plus the heartbeat RTT (no cross-host clocks): **p95 ≤ 50 ms** idle on powerline, ≤ 100 ms with the link saturated. Siren `mask.lag_high` watches it; `stored` acks while live are excluded.
- Real light step (torch) → first visible change, filmed at 240 fps with the TVs' ambient features off (§3.5): **p95 ≤ 250 ms**, including the report interval, snap confirmation and the TV's own lag without Game Mode through the One Connect box (unmeasured; estimated ≈95–100 ms, spike §10; design §2.6), measured in the filmed test, which re-sets this target. Today it is 0.5–3.5 s, then several seconds to settle.
- No visible flicker under steady light (filmed).
- Optical loop check, if the sensor can see the TVs: step the mask 0.2 → 1.0 once and confirm the loop does not pump.
- With the WS down, behaviour falls back to the current 3 s poll.

### 3.6 State and persistence

| What | Today | Target (P2 unless noted) |
|---|---|---|
| Scheduler state | `routes/scheduler/<dest>.json` (tracked, 57–108 KB rewrite after every instruction and 15–20×/s in wait) | `state/scheduler/<dest>.json`. P2 stops rewrites when a `wait` changes nothing. T6 adds a single writer with debounced persistence (≤1/s, flush on lifecycle change) |
| Vars | `scheduler/_vars.json` (tracked); stale `routes/scheduler/_vars.json` | `state/scheduler/_vars.json` (locked RMW in T6); stale copy deleted |
| Destinations | `src/data/publish-destinations.json` (frontend tree; wins via cwd search); stale shadow in `routes/data/` | `config/publish-destinations.json` (**same file name**, because callers look it up by name), found first via `CONFIG_DIR` in `SEARCH_PATHS`. `selfcheck` fails if it is missing, so the stale git copy can never be read. Only `routes/data/publish-destinations.json` is deleted; `routes/data/` stays in `SEARCH_PATHS` (alexa j2, refiners, reasoners, intensity.json) |
| workflows.json / refiners.json | `src/data`, bundled and read live | stay versioned in the repo; backend reads them by explicit settings path (T3) |
| Screen state (channels), mask overrides | in-memory dicts; overrides and `maskoff` are lost on restart (`routes/display.py:19,23`) | `state/screens/<id>.json`: base, intent, live, policy; atomic, `rev` persisted (P4). `state/mask_overrides.json` plus `last_mask` for the warm start (P4b, pulled forward from T3) |
| Screen change log | none | `logs/screens/<id>.jsonl`: every desired change with its cause (`actor`, `corr`) and every observed transition (P4) |
| Light constants | `config.py`, re-executed on every mask call | `config/light.json`, reloaded on mtime (P4b) |
| Job registry | `runpod_jobs.log` (21k-line CSV, cwd-relative) | `state/runpod_jobs.log` (P2) → `state/jobs.sqlite` WAL (T5, idempotent importer of non-terminal rows) |
| Media | `output/` in the repo | `/srv/screen-machine/output` (same-filesystem `mv`), with a compat symlink `repo/output →` new location during the soak, and `current/output →` it as a safety net |
| Pointer + sidecar | `copy2` + `touch`, in place | temp file in the same dir with an unpolled name, `copyfile` (never `copystat`), `os.utime(now)`, `os.replace`; other-extension siblings are **left in place** (the kiosk polls only `.jpg/.JPG/.mp4`, and `north-screen.png`/`south-screen.png` exist today, so removing siblings after a `.png` publish would leave a screen with nothing to show) (P2) |
| bucket.json | truncate-and-write, no lock | atomic write (P2); per-bucket lock + rev + `bucket.changed` (T4) |
| Secrets | `.env` in repo root, token `.txt` files tracked | `config/sm.env` (0600, loaded by the start script); `config/tokens/` |
| Logs, alerts | `logs/` in repo | `logs/` and `state/alerts_state.json` under `/srv/screen-machine` |

Rules:
- All JSON writes go through `sm/core/atomic.py`, reusing the pattern from `utils/alerts/state.py`.
- The stream service holds no durable state.
- Nightly backup job (scheduled the same way as the other services' jobs, per the owner's single-admin-pattern ruling (2026-10-14: "don't want wildly different admin patterns across servers on this device")): tar of `state/` and `config/` plus `sqlite3 .backup` (once T5 lands), 14 rotations.
- **Ordering gotcha:** `git rm --cached` of state files happens only *after* prod no longer runs from that tree.

### 3.7 Config, secrets and security posture

**Settings.** `sm/settings.py` is a frozen dataclass read from `SM_*` env. It is stdlib-only, and its defaults reproduce today's paths and ports exactly. Variables: `SM_ENV`, `SM_ROOT`, `SM_STATE_DIR`, `SM_CONFIG_DIR`, `SM_OUTPUT_DIR`, `SM_LOG_DIR`, `SM_DIST_DIR`, `SM_HTTP_HOST/PORT`, `SM_WS_HOST/PORT`, `SM_INGRESS_PORT`, `SM_STREAM_URL`, `SM_SCHEDULERS=all|none|csv`, `SM_FAKE_EXTERNALS`. Existing names are kept: `ADMIN_API_TOKEN`, `ALERTING_ENABLED`, `RUNPOD_*`, `OPENAI_*`, `ASSEMBLYAI_*`. `config.py` re-exports for compatibility. At startup the app logs every resolved path. The start script runs sm-web with the release dir as its working directory until T3 removes the remaining cwd lookups (`findfile`, `SEARCH_PATHS`); `compute_mask`'s `exec('config.py')` goes in P4b.

**Network reach, enforced in this order (P3/P4).** Closing ports changes behaviour off the LAN, so this is decision D4.
1. Deploy a bundle with relative/runtime URLs. Today `http://95.141.21.170:5000/api`, `:8000` and `ws://95.141.21.170:8765` are baked in.
2. Move kiosks to LAN URLs.
3. Set up a VPN (Tailscale or WireGuard) for remote operator and off-LAN Android use.
4. Stand up the ingress listener `127.0.0.1:5100` (only `/api/alexa`) and point the tunnel at it. Verify Alexa end to end.
5. Close WAN 5000/8000/8080/2222/8081/3001 (8081 and 3001 are exlib's ports, managed by Spectre; coordinate with screen-machine-ca and `docs/PORTS.md`). No AV port (8700–8702) is ever WAN-forwarded.
6. Choose the RunPod progress path and update the worker (its code is not in this repo).
7. Close 8765 last.

**Narrow, justified exceptions:**
- The existing `ADMIN_API_TOKEN` (the admin API SSHes into the media server).
- Alexa request-signature, timestamp and applicationId verification. This proves the webhook comes from Amazon; it is not user auth.
- RunPod progress. Preferred: the worker uses RunPod `progress_update` and the backend polls status, so no inbound port is needed. Fallback: a **per-job, expiring HMAC** minted at submit and posted to `/ingest/runpod` on the ingress listener.
- **Recommended, needs the owner's yes (a behaviour change and a narrow exception to principle 7):** a token on `maskon`, `maskoff` and `brightness-override` writes before P4b ships. Once P4b pushes overrides to the glass within milliseconds, open override endpoints are a LAN strobe vector (and while 5000 is WAN-forwarded, `brightness-override 0` is a remote blackout). The operator UI's mask toggles then send it. It is also a Room-AV proposal (§5.2).

There is no token on the :8765 control socket, nor on the stream service's LAN-only `/ws`. v2 clients cannot publish, the legacy relay stays sanitised until T8 removes it, and the control socket becomes LAN-only once WAN 8765 closes (P4 step 7). The one exception is an optional per-sensor ingest key on `/v2/ingest/lux`, at least until WAN 8765 closes (D27 item 12).

**Deployed 2026-10-11 (P0), to be pinned by regression tests in P1:**
- Plain `SandboxedEnvironment` in `scheduler_utils.py` and `dict_substitute`. Not `ImmutableSandboxedEnvironment`, which breaks `overlay_schedule.html.j2` (`dict.update` / `list.append` inside `{% set %}`).
- `find_template_file` restricted to `routes/` and `src/data`.
- `realpath` + `commonpath` in `file_api`.
- Relayed WS frames stripped of `html` and `substitutions`, plus a bounded job-progress map.
- `/test-overlay` behind the admin token.
- Sanitised `/api/publish-destinations`.
- `AdminPanel.tsx` fetches destinations instead of bundling them.
- admin `set_url` via `json.dumps`.
- Publisher refuses sources outside the media store or temp dir.
- Logger buffers bounded: the list is trimmed in place, because `app.py:101` slices it (a deque would break that).
- Added after the adversarial patch review, before deploy:
  - bucket-to-bucket copy validates that the source is a configured bucket and that the file resolves inside it (a pre-existing arbitrary-file-to-public hole);
  - relayed `type: generation_update` frames are neutralised;
  - temp publish sources must be direct children of the temp dir with a media extension;
  - `/api/files` and the admin token check no longer raise on NUL or non-ASCII input;
  - set-url uses a `repr()` literal plus base64 transport;
  - `build/test-display.html` sends the stored admin token;
  - the job-progress map updates in place, so generator threads never see the key missing.

**Still open after the patches:**
- The sandbox stops neither DoS (huge loops) nor **double rendering**: `set_var` and overlay text render twice, so values from the unauthenticated `/context`, `/exported-vars` and event payloads become template source on the second pass. Single-pass rendering is a behaviour change, scheduled in T6 behind goldens and owner sign-off.
- `/api/files` still has no auth (LAN reach is the control). Its realistic risk is planting schedule scripts, a persistence vector for the template issue. It is *not* arbitrary write: `abspath` normalises `..` (C5).
- WS spoofing and eavesdropping on :8765 remain until P4 and the port closure.

**Secrets hygiene.** The `samsung-token` values in the bundle and API are **token file names**, not tokens, so what they leak is LAN topology. The real exposure is the git-tracked token `.txt` files, `debug/*.unf` (a UniFi backup), the support bundle, and `' - Copy.json'` (its AKIA key ids sit inside S3 presigned URLs that expired in May 2025: key ids only, no secret). Untrack them in HEAD, make the repo private, optionally re-pair the TVs, and let the owner decide on rotation and a history rewrite (D2). The IP Control pairing tokens are already outside the repo (`~/.config/screen-machine/ipcontrol/`, 0600) and must stay outside any git tree; P2's settings seam gives DevicesPort their path (today's location, or `config/tokens/ipcontrol/` at 0600 after the cutover).

### 3.8 Deployment, ops and observability

**`deploy/deploy.sh <ref>`:**
1. `git archive` into `releases/<sha>`. `.gitattributes` export-ignores `experiments/`, `tests/`, `docs/`, `debug/` and `scripts/manual/`.
2. Build the venv from `requirements.lock` (a `pip freeze` of today's `.venv`).
3. `npm ci && vite build` **inside the release**, never in the dev checkout.
4. Run `python -m sm.selfcheck`.
5. Flip the `current` symlink.
6. `systemctl restart sm-web`.
7. Poll `/api/health` for 60 s and roll back automatically on failure.

`rollback.sh` flips the symlink back. For the first cutover, the old GNU-screen launch command is kept verbatim as a 2-minute rollback.

**Supervision.**
- **Supervision follows the owner's single-admin-pattern ruling (2026-10-14: "don't want wildly different admin patterns across servers on this device").** Every server on PC22 runs in a named **GNU screen session** started by its own project script: Flask/sm-web and ngrok via `start.sh` (owned by screen-machine-12), room_service and stream_service via `scripts/ctl` (screen sessions `sm-room`, `sm-stream`; settings and owner holds in `deploy/sm-room.env` and `deploy/sm-stream.env`). Spectre manages all of them through its standard `wslService` cards (status, start, stop, restart, logs, LAN and boot toggles). **No systemd units or linger for app services.** Boot is the pre-logon `scripts/wsl-boot.sh` plus Spectre logon tasks, all calling project scripts (`docs/OPS_CONTROL.md`).
- `sm-web` therefore gets, inside that pattern: a restart-on-crash loop with backoff in its start script (or Spectre's card), so a crash restarts it; a pidfile and the screen session name for stop/restart, replacing `pkill -f`; and, since there is no cgroup memory limit in this pattern, a memory backstop from the bounded buffers (P0) plus a Siren RSS-growth alert and an optional RSS watchdog that restarts it through the script above a threshold.
- `sm-stream` (P6A) and `sm-room` (room_service) **exist since 2026-10-14** as screen sessions driven by `scripts/ctl` and surfaced in Spectre, held by their owners through hold lines in `deploy/sm-{stream,room}.env` (`docs/OPS_CONTROL.md`, screen-machine-ca).
- Kiosk (P5): the kiosk launcher (`kiosk-wayland.sh` under sway, which already restarts a Chrome that exits, without a rate limit or health gate; `kiosk-loop.sh` on the X11 fallback) respawns a Chrome that has died, with the same URL (at most 5 per 10 min; the launch waits up to 5 min for `/api/health`, then launches anyway). KioskMonitor runs the restart ladder over SSH keys instead of `sshpass`: a kiosk stale for 60 s → `restart-screen` (at most 3/h) → `restart-kiosk` → alert only; automatic and rate-limited by default (D27 item 6). No agent runs on the mini-PC.
- **Windows boot (corrected 2026-10-14, screen-machine-ca).** Today two stale tasks start things wrongly: `\Start WSL Services` (pre-logon) runs `~/wsl-autostart.sh` (dev mode, a Vite dev server and broad pkills), and `LocalAutomation_BootStart_screen-machine` runs a bare `python app.py` plus a tmux Vite dev server. Replacements are ready (`scripts/wsl-boot.sh`, and Spectre's prod `start.sh start flask`/`ngrok`); installing them needs the owner's OK. `fix-wsl-networking.ps1` **cannot run on this PC**: Windows PowerShell 5.1 hits parse errors from the PowerShell-7-only ternary at line 134, and `pwsh` is not installed. Its job is done instead by **Spectre's portproxy reconciler** (every 60 s, it re-points WSL-targeted rules after a WSL IP change; deployed 2026-10-13/14). Port rules are owned by Spectre; the register is `docs/PORTS.md`.
- `start.sh` becomes dev-only: fix the `MODE=dev` default and drop the broad `pkill -f python.*app.py`.

**Observability.**
- `/api/health` grows: RSS, threads, log-buffer sizes, hub clients by role and screen, loop lag, kiosk heartbeat age, stream health, and (T5) job counts.
- Deadman: 5xx or non-JSON counts as **down**, and `ok:false` as **degraded**, a distinct alert. Today any HTTP response counts as up (`deadman.py:200-202`). `ok` is also false when a scheduler tick is stale, so it must not mean "host down". Set `HEALTHCHECK_PING_URL`.
- New Siren keys: `kiosk.heartbeat_stale`, `kiosk.state_drift`, `kiosk.duplicate`, `kiosk.player_stalled`, `kiosk.reload_storm`, `live.client_failed`, `sensor.stale`, `mask.lag_high`, `realtime.hub_restart`, `realtime.loop_lag`, `realtime.no_subscriber`, `stream.start_failed`, `stream.session_failed`, `stream.admission_denied`, `stream.encoder_restart_storm`, `stream.late_frames` (P6A pacing), `stream.anchor_timeout` (design §2.5), `process.rss_growth`, `tv.settings_drift` (DevicesPort, §3.5).
- **KioskRegistry and `/glass`** (P4). The registry holds each kiosk's acks and `kiosk.state`. `GET /api/screens/<id>/glass` shows desired vs applied per screen (`PUT .../channel` returns the new `rev`; `/glass` shows whether the kiosk's acked rev has reached it). "On glass" means **composited by Chrome**: it says nothing about the panel, so `/glass` is shown next to the TV's observed state from DevicesPort over IP Control (power, Art Mode, input, picture mode; §3.5) once that adapter lands. Only `kiosk`-role clients count; two clients claiming the same `?kiosk=` raise `kiosk.duplicate`.
- Logging (T3): stdlib `QueueHandler`, 14-day retention, per-tick scheduler lines moved to an off-by-default TRACE (they are 81–83% of log bytes), tracebacks kept. Today `error(f'{e}')` drops them.

**Dev stack.** `deploy/dev-stack.sh` runs the dev checkout on 5001/8766 with `SM_ENV=dev`, a sanitised state copy, `SM_SCHEDULERS=none|devtest`, `SM_FAKE_EXTERNALS=1` and alerting off. A laptop browser at `/display/devtest?kiosk=lab` (when V1 is not running; or `?live=1` for live only) acts as the test kiosk; without `?kiosk=` it is a `viewer` (no live unless `?live=1`, not counted on `/glass` or in shadow acks). bd's lab keeps running in `experiments/` and is never moved by this plan.

### 3.9 Testing strategy

**Harness that cannot touch prod (P1).**
- `pytest.ini` with `testpaths=tests` and `norecursedirs = routes utils experiments samsungtvws node_modules output* dist* build scripts`, plus `--import-mode=importlib`. Markers: `unit`, `contract`, `characterization`, `legacy`; the default run excludes `legacy`.
- A root `conftest.py` prelude that **merges with** the existing `tests/conftest.py` `pytest_configure` storage redirect. It:
  - refuses to run if `SM_ENV=prod` or the root is under `/srv/screen-machine`;
  - uses tmp state, output and log dirs;
  - sets `ALERTING_ENABLED=0`;
  - scrubs `*_KEY/*_TOKEN/*_SECRET/*PASSWORD*`;
  - installs a socket guard (loopback fixture ports only; 5000, 8765, 8081, 8700, 8702 and all non-loopback denied);
  - blocks `ssh`/`sshpass` subprocesses;
  - fails any module-scope `import app` until P2;
  - runs a **canary test** that must fail to connect to :8765.
- Quarantine: `utils/test_ws.py` (clears north-screen overlays at import) moves to `scripts/manual/`. The root part was done on 2026-10-14 (staged, uncommitted): the root `test*.py` scripts are now `scripts/manual/manual_*.py`, renamed so a bare pytest never collects them, and `test_ws_client.py` (binds :8765, can hang) and the empty `test_jpg_endpoint.py` were untracked and archived outside the repo. `routes/test_*_ui.py` stay in place, because `app.py` registers them, and are excluded by `norecursedirs`. A one-off pass/fail/skip baseline of the 239 legacy tests is recorded.

**Test layers:**
1. **Unit** tests for new pure modules, without Flask: hub fan-out, slow-client isolation, coalescing, snapshot/epoch, cross-thread publish; channel arbitration and leases; atomic writes; ProgressStore TTL.
2. **Contract** tests: every emitted event validates against `schema/realtime.v2.json` (on both the Python and TS sides); codegen drift check; stream API against a fake encoder; SPS parse on every H.264 init segment; all 13 schedule scripts against the schema (5 xfail until fixed); the kiosk `/output` Last-Modified/HEAD/404 contract through `create_app(test_settings)` from P2.
3. **Characterisation (goldens)** before each strangle: `send_overlay` payload per template; `publish_to_destination` file and `bucket.json` effects. These can run before the settings seam by `chdir`-ing into a tmp tree, since paths are cwd-relative. Also `resolve_schedule` traces for every script at fixed clocks with fake services, and the T6 shadow diff.
4. **Frontend**: vitest + Testing Library for ControlClient, the reducer (snapshot, epoch, content-key comparison), liveMachine and MseLivePlayer with fake drivers and a fake media socket, the hold-last mask, and overlay scripts running once. Optional Playwright smoke of the display page against the dev stack.
5. **Hardware-in-the-loop**: bd's tools (`validate_mux.py`, client stage stats, a production Chrome over CDP (V1) under sway; `lab-chrome.sh` only on the X11 fallback) against a staging stream service, plus 24 h soaks with heap and stall telemetry. Production kiosk windows are touched only with the owner's OK.
6. **Shadow acks before push** (P4). Before any display behaviour is push-driven, the production pages run the v2 ControlClient in `shadow` mode: they report what the *legacy* logic is showing, and the server measures publish-to-glass time and push-vs-poll divergence. 7 days with every divergence explained is the gate for the first push flag.
7. **Live-path checks** (P6B): an automated cut check (CDP screencast; `fb_probe.py` luma only on the X11 fallback, since it is X11-only today) through planned still → live → still cuts and the re-anchor (no black or mismatched frame) and through a forced stall fallback (a single-frame hard cut to the still, with no black frame and no fade), and nothing drawn over the video while live; the on-glass anchor-match check with `grim` (P6B stage 1 step 4); a fault-injection TCP proxy in front of devtest's attach URL (latency, loss, half-open, 15 Mbit/s cap).

**Test vehicles on the real kiosk** (design §3), so no step disturbs production:

| | Vehicle | What it involves |
|---|---|---|
| **V1** | Production Chrome over CDP | Under sway (design §3, §5 item 15): `cdp_probe.py --allow-production` navigates one production Chrome (9222 north or 9223 south) to `/display/devtest?kiosk=lab`, then restores its fixed URL; if the restore fails, killing that Chrome brings it back on its fixed URL (`kiosk-wayland.sh` respawn, about 10 s). The other production Chrome runs untouched. From P4, devtest's retained `screen.state` and `mask.set` can mirror that screen's (a server-side switch, P4 step 3); before P4 it shows devtest's own content and mask. It takes one TV, so it runs only in owner-agreed off-hours windows. *X11 fallback only:* the lab Chrome `experiments/stream-latency/beelink/lab-chrome.sh` (own profile, CDP 9224, nice +10, outside `kiosk-loop.sh`'s pkill) and `fb_probe.py`; there a hot-plug respawn restacks the kiosk windows above it and Chrome on X11 stops drawing a fully obscured window, so check the stacking before and after the run, void a run that saw a respawn, and do not treat a lab-over-TV run as the two-window production load (§3.5). |
| **V2** | Shadow | Production pages report what they show; rendering does not change. |
| **V3** | Server policy flip | Per screen: devtest, then south for 48 h, then north. No box access, no Chrome restart. |
| **V4** | Spare N95 staging kiosk | If bought (D27 item 14). Zero production risk. |

- `cdp_probe.py` (bd's lab tool) has a guard: it refuses eval or navigate on 9222/9223 (the production Chromes) unless given `--allow-production`. The flag is used only with the owner's OK: for V1 (inside an owner window, always ending with a restore; design §3) and for read-only production checks such as P0.5's script count.
- Before P2's dev stack exists, the candidate `dist/` is served from a throwaway static server, with `/output` and `/api` proxied to :5000, and V1 loads it. It runs on a free port registered in `docs/PORTS.md` (the 8082–8099 band), with a LAN rule created through Spectre; never on :3001 or :8081, which belong to exlib.

**CI**: GitHub Actions on push, on hosted runners with no secrets and no route to prod: ruff, mypy (new packages), import-linter, the cycle ratchet, pytest (unit, contract, characterisation), the tsc and eslint ratchets, vitest, `vite build`, and (from T7) the display page bundle budget. **Never run pytest or builds in a release dir**, and never `import app` outside pytest until P2 lands.

---

## 4. Key interfaces

**4.1 Envelope (wire, protocol v2).** The full message set, with examples, is in design §2.3; this is the summary.
`{v:2, type, epoch, rev?, data}`: `epoch` is random per hub start; `rev` counts per retained type per screen. No field is compared across hosts' clocks.
- The socket path carries the scope (`/v2/screen/<id>`, `/v2/ingest/<kind>`, `/v2/operator`; §3.4), so there is no per-frame topic.
- Server → page: `welcome{build_id, hb_ms, snapshot{policy, screen.state, mask.set, overlays[] with remaining_ms}}`, `policy` (retained), `screen.state` (retained; the wire form of ChannelState), `mask.set{brightness, warm_hex, warm_alpha, transition_ms, source, srv_ms}` (retained; applied in art mode only, stored and acked `stored` while live), `overlay.show{overlay_id, html, duration_ms, position, clear}` (rendered in art mode only; while live: D27 / design §5 item 20, interim dropped and counted in `overlays_suppressed`; §3.5), `overlay.clear`, `kiosk.reload{reason, when: now|next_transition}`, `hb`.
- Page → server: `hello`, `ack{kind: mask|state|anchor, rev, phase: applied|stored|visible|fallback|recovered|failed}` (`stored`: a `mask.set` received while live; the `anchor` ack carries `base_rev, mask_rev, clip_t, fit, drawn` instead of a phase), `kiosk.state`, `hb`.
- Other types: `job.progress|completed|failed{job_id, batch_id, dest, percent, stage, stages, message|outputs|error}` for the operator, `ingest.lux{sensor, lux, seq, mono_ms}` with `cfg` and `lux.ack` back, `stream.health`.
- Versioning: `v` is the major version; within it changes are additive and clients ignore unknown types and fields. An unsupported major is closed with 4400 and the page falls back to degraded polling. JSON Schema (`schema/realtime.v2.json`) is the source of truth; codegen produces `src/shared/protocol/*.gen.ts` and `sm/realtime/protocol.py`.

**4.2 Handshake.**
- Page → server, on `/v2/screen/<id>`: `hello{role: kiosk|viewer, screen, kiosk?, client_id, page_load_id, build, have{epoch, state_rev, mask_rev}, caps{mse{hevc,h264}, power_efficient, max{w,h,fps}}, local_flags{nomask, nocolour, fill}}`. Other roles say hello on their own path (`sensor` with an optional key, `worker`, `audio`, `operator`).
- Server → client: `welcome` with the full, authoritative snapshot (§4.1). It is held at most 3 s after a core restart until one reconcile pass has run.
- Page → server: `ack{kind: mask|state, rev, phase: applied|stored|visible|fallback|recovered|failed, …}` on every retained change (a `mask.set` received while live is acked `stored`, §3.5b), `ack{kind: anchor, rev, base_rev, mask_rev, clip_t, fit, drawn}` at `starting` and at each re-anchor (design §2.5), and `kiosk.state{applied{state_rev, mask_rev}, shadow, glass{layer, base_url, live_session}, live{state, fps, dropped, stalls, rate, edge_jumps, ahead_ms, rx_kbps, decoder}, mask{brightness, drawn}, overlays_suppressed, ctl{mode, reconnects}, heap_mb, uptime_s, last_error}` every 10 s and on change.
- Liveness: `hb` after 2 s idle; 6 s silence = dead socket. Degraded mode starts 5 s after the socket is lost: `GET /api/screens/<id>/state` every 15 s, and `POST /api/kiosks/state` every 30 s.
- Ingest: `ingest.progress{job_id, comfy}` (worker), `ingest.lux{sensor, lux, seq, mono_ms}` (sensor).
- No publish op. The legacy `/` handler and its classification are unchanged (§3.4).

**4.3 Bridge (Python, any thread).**
`bridge.publish(topic, type, data, *, retain=False, coalesce_key=None) -> None`: non-blocking, never raises, drops counted.
`bridge.on(type_prefix, handler) -> unsubscribe` for in-process consumers on the hub loop (must not block).
`hub.register_ingest(role, msg_type, handler)`.
Legacy shim: `routes.display.send_overlay(...)` keeps its signature, renders as today, and publishes per screen.

**4.4 ChannelState and ChannelService.**
```
MediaRef     {url, kind: image|video, rev, ext}
AttachOption {transport: ws-fmp4-mse|webcodecs-ws|webcodecs-mstg|ll-hls|whep, url, codec (RFC 6381),
              params{w,h,fps,target_buffer_ms,latency_hint_ms,...}}
ChannelState {screen, rev, mode: still|clip|live|blank, base: MediaRef|null,
              intent: {mode: auto|live|blank, owner, lease_expires_at (server UTC), live_spec{source, ladder}},
              live: {session_id, phase: starting|on_air|leaving|adopting, visible, attach: AttachOption, rung,
                     anchor: {base_rev, mask_rev, clip_t, anchor_pts?}, exit: {base_rev, mask_rev, hold_pts}}|null,
              policy: {ctl, mask_source, overlay_source, content_source, live_enabled, offline},
              claims: [{owner, mode, expires_at}], transition{base: through_black}}   # design §2.4
              # planned art ↔ live switches are hard cuts between matching frames; failure cuts (stall, exit
              # timeout) are unmatched hard cuts; so the former live_ms crossfade field goes
ChannelService:
  get(screen); snapshot()
  on_published(screen, MediaRef)                       # publisher hook after os.replace
  go_live(screen, *, source, profile, owner, lease_s=None, prefer=None)  # idempotent on (screen, owner, spec)
  set_override(screen, *, mode, source, owner, lease_s=None)
  clear(screen, *, owner, scope='live'|'override'|'all'); release_owner(owner)
  on_stream_health(report)                             # reconcile → fallback + Siren
```
HTTP: `GET /api/screens` (sanitised); `GET|PUT|DELETE /api/screens/<id>/channel` (PUT returns the new `rev`); `GET /api/screens/<id>/state` (the HTTP twin of the snapshot); `GET /api/screens/<id>/glass` (desired vs applied); `POST /api/kiosks/state` (fallback while the WS is down); `GET /api/screens/<id>/mask`; `GET /api/runtime-config → {api_base:'/api', ws_url (derived from request host), build_id}`.
Scheduler: `{"action":"channel","mode":"live","source":{...},"profile":"4k60-hevc","lease":"10m","targets":[...]}` / `{"action":"channel","mode":"clear"}`. Non-blocking, with a 3 s budget.

**4.5 Display page LiveDriver (TS).** Inline only (D15); there is no iframe or page presentation and no postMessage contract.
```ts
interface LiveDriver {
  transport: Transport;
  canPlay(caps: KioskCaps, a: AttachOption): boolean;
  mount(el: HTMLElement, a: AttachOption, cb: {onState(s, detail?): void; onStats(s: LiveStats): void}): Promise<void>; // resolves when warm under ArtStack (HAVE_ENOUGH_DATA, target buffer, playing at 1.0); the page then cuts on the first presented frame of the held anchor (design §2.5)
  unmount(): Promise<void>; // releases MediaSource/SourceBuffers (removeAttribute('src') + load()), decoders, sockets, timers; idempotent
}
```
The driver creates and owns its `<video>` and MediaSource imperatively inside `el`; React renders only the container. The `<video>` fills the viewport and nothing is drawn over it while live. The driver never sets `playbackRate` above 1.0; it resyncs to the live edge by a hard jump. Switching is a hard cut by the page; drivers have no fade.

**4.6 Stream service API (LAN).**
- `POST /api/sessions {screen, source{kind: synthetic|file|still|pipe, params}, encode{codec, size, fps, bitrate_kbps, gop_s}, transports[], latency_class: interactive|ambient, ttl_s, idempotency_key, anchor{base_url, fit, clip_t, brightness, warm_hex, warm_alpha}} → 201 {session_id, attach: AttachOption[], state}`: the renderer draws the anchor as the page composites it and holds it at 60 fps (hold limit 30 s; design §2.5).
- Handshake calls: `POST /api/sessions/{id}/diverge` (after the entry ack); `POST /api/sessions/{id}/anchor` (re-anchor after a stall; returns `anchor_pts`); `POST /api/sessions/{id}/converge {target}` (on leaving: converge to the target still and mask, then hold it). The session reports when a hold starts, with its first pts (`hold_pts`), which the server republishes in `screen.state.live.exit`.
- `POST /api/sessions/{id}/renew`; `POST /api/sessions/{id}/source`; `DELETE /api/sessions/{id}` (idempotent)
- `GET /api/sessions/{id}`; `GET /api/state → {sessions[] (with the renderer's late-source-frame count, P6A pacing), clients[{remote, transport, stages{p50,p95}, dropped}], gpu{sessions, cap}}`; `GET /api/health → {ok, version, encoders_alive}`
- `WS /ws?session=&client=&transport=` (data plane, framing owned by the transport). The session reports `ready` once the first IDR is muxed; `/api/state` carries per-client backlog and drop-to-key counts; `ttl_s ≥ 120`. There is no production `/player` page: `static/client.html` stays a lab page.
- Errors: `409` when the per-screen session limit is hit (returns the existing session when the idempotency key matches); `503` when the NVENC cap is reached.

**4.7 Post-feature service contracts (for T4–T6):**
- `BucketStore.snapshot(bucket)` and `mutate(bucket, fn, *, op, file) -> rev` (per-bucket lock, atomic write, `bucket.changed`). `load_meta`/`save_meta` delegate to it.
- `PublishService.publish(dest, source: BucketRef|LocalPath(in OUTPUT_DIR or temp)|RemoteUrl(allowlisted)) -> PublishedRecord`, plus `undo`/`redo` that validate *before* moving the pointer.
- `GenerationService.submit(req) -> job_id`, `get`, `cancel`, `wait(timeout)` (legacy `handle_image_generation(wait=True)` = submit + wait). `Job` lives in `state/jobs.sqlite`. The RunPod adapter handles `TIMED_OUT`, cancels on timeout or error, retries transient errors, reconciles at startup and **surfaces typed failures** (today a `TimeoutError` becomes a `ValueError` and then an empty list).
- Scheduler `@action(name, schema_ref) def h(instr, ctx) -> ActionResult = Continue | ExitBlock | Unload | Pending(job_id)`, with a per-destination `engine: v1|v2` flag.

---

## 5. Phased roadmap

### 5.1 Overview and critical path

The sequence follows the display-layer design (design §3), pending the owner's yes to its reordering (D27): leak fixes and hold-last first, then the P1/P2 gate, then the hub, shadow acks, light, overlays, content, supervision and live, each switched on per screen by a server `policy` flag.

```
P0 hotfixes ─► P0.5 display page ─► P1 safety net ─► P2 prod separation ─► P4 hub v2 core ─► P4 shadow acks ─►
  (done)        hygiene + hold-last   (3–4 d)          (5–7 d)               (4–6 d)            (1.5–2 d + 7 days)
                mask (1–1.5 d)       └──── hard gate, 8–11 d ─────┘
─► P4b light push ─► P5 overlays ─► P5 content ─► P5 supervision ─► P6B live on ─► P6B production live ─► T8
   Tier 1 (3–4 d)    via v2         push (2 d)    + reload           devtest        (4–6 d; with P6A,      decommission
                     (1–1.5 d)                    replacement        (4–5 d)        bd 6–9 d)
                                                  (1.5–2 d)
Alongside: P3 LAN origin, reach, Chrome hold (2–3 d + owner time; must finish before production live; the Chrome hold
           comes before the devtest live step; Ethernet and VA-API are done; the display fix is deployed (sway), and its
           display-fix bar at 4K60 is still to pass, P3 exit) · P6A stream service (bd, from P2)
Tracks (pausable, off the path): T1 hygiene/dead code · T2 confirmed-bug batch · T3 platform consolidation ·
T4 storage/publishing · T5 jobs/generation · T6 scheduler engine · T7 frontend data layer · T8 overlays + decommission
```

| Design step | Plan phase | Owner-days |
|---|---|---|
| 0. Page hygiene and D14 | P0.5 | 1–1.5 |
| 1. P1 and P2, hard gate | P1, P2 | 8–11 |
| 2. Hub v2 core | P4 steps 1–5 and 8 | 4–6 |
| 3. Shadow acks | P4 step 6 | 1.5–2, plus 7 days' running time |
| 4. Light push, Tier 1 | P4b | 3–4 |
| 5. Overlays through v2 | P5 step 1 | 1–1.5 |
| 6. Content push | P5 step 2 | 2 |
| 7. Supervision and reload replacement | P5 step 3 | 1.5–2 |
| 8. Network, in parallel | P3 | 2–3 plus owner time (off the path) |
| — RunPod progress (D5) and close WAN 8765 | P4 step 7, with P3 step 8 | about 1, plus the worker change; alongside, off the path |
| 9. LiveLayer on devtest | P6B stage 1 | 4–5 |
| 10. Production live | P6B stage 2 (owner), P6A (bd) | 4–6, plus bd 6–9 |
| 11. Decommission | T8 | after the feature |
| **Total to production live** | | **≈ 30–41** |

*(Pre-ruling. Under the 2026-10-14 ruling, P1 and P2 shrink to small items and stop being a gate, and the soaks and shadow exits go; re-estimate pending.)* The critical path is P0.5 → P1 → P2 → P4 → P4b → P5 → P6B, about **30–41 owner-days to production live** (29–40 before the anchor handshake, mask parity and even pacing grew steps 9 and 10; design §3 effort). That is roughly level with the earlier estimate (26–36, "plan on 30–40"): the P5 parity port, Tier 2 and the iframe/`/player` work are swapped for shadow acks, policy switches and a hardened live state machine. **Light push is on glass after about 18–25 owner-days plus the 7-day shadow window** (P0.5 + P1 + P2 + P4 + P4b; P4b can be built during the window, but production flips wait for its end), and the leak fixes ship in week one (P0.5). P3 and P6A run alongside; P3 must finish before production live. The display fix that blocked live 4K was deployed on 2026-10-13 (P3 step 5): per-TV X screens fixed 4K30, then the move to Wayland (sway) made 4K60 work (a pre-made 4K60 HEVC file on both TVs: 0.03% dropped, rAF 60.0). Before production live remain even renderer pacing (P6A), a player that never runs above 1.0× (P6B), and the 30-minute presented-frame run at 4K60 (≥ 99.9% presented, 0 late renderer frames, ≤ 1 stall per 10 min; design §6 item 1; §3.5). **With the Room-AV proposal** (§5.2, pending AV-D1/AV-D6): if P3 *reach* must gate the claim API and the room's K5 and DevicesPort work (≈2–3.5 d) is added, the range becomes ≈32–47 owner-days; screen-machine-10 computes ≈34.5–47 to its milestone M6 (screens as fixtures in production), counting P3 in full.

**Coherent stopping points:**
- after P0: leak reset, holes patched;
- after P0.5: no phantom overlays or script build-up; a server restart holds the last mask instead of blacking the screens; a timed reload waits until the server answers; a crash shows black, never white;
- after P2: prod and dev separated, reversible deploys;
- after P3: network reach actually enforced;
- after P4: the v2 path closed to injection (legacy relay still sanitised until T8), thread-safe pushes, the v2 hub running, and both screens reporting what is on glass (shadow);
- after P4b: screens in art mode follow ambient light within tens of milliseconds;
- after P5: overlays and content pushed, no 4-hourly blanking, dead Chromes respawned;
- after P6B: live streaming.

**Pre-feature legacy touches (the complete list):**
1. The hotfixes.
2. The P0.5 display page fixes.
3. The settings seam and composition root.
4. The H2 batch: cross-loop bridge, audio loop, wait-write skip, wait-log key, logger hot path, atomic `bucket.json`, atomic pointer, reloading `publish_api`, log clamps.
5. `send_overlay` also publishing through the bridge, `audio_utils` re-pointed at it, and the v2 path router in `overlay_ws_server.ws_main` (the legacy `/` handler itself is untouched).
6. The display page evolved in place (`control/`, `live/`, mask through refs), each part behind a `policy` flag.
7. The `on_published` hook in the publisher.
8. The scheduler `channel` action.
9. The kiosk URL and flags (`?kiosk=`, LAN origin) and the generic respawn in the kiosk launcher (`kiosk-wayland.sh` under sway; `kiosk-loop.sh` on the X11 fallback).
10. P4: the `job.*` emit in the `routes/generate.py` progress loop; the per-shape logging taps in the helpers the legacy handler calls (§3.4).
11. P4b: the `routes/display.py` mask routes and `display_utils.compute_mask` delegating to `SensorService` and the pure `compute_mask` (overrides persisted; legacy `GET /api/<id>/mask` keeps its URL and shape); `routes/lightsensor.py` (`lux_history` sized by time; legacy lux frames fed to `SensorService` through `broadcast_lux_level`).
12. P5 step 3: the `admin_api.py:157` comment and the restart commands the KioskMonitor ladder reuses over SSH keys.

Everything else waits for a track.

**Prerequisites for streaming:** P0.5, P1, P2, P3 (LAN origin, the Chrome hold, and the display-fix bar at 4K60, P3 exit; VA-API in production and Ethernet are done, and the display fix is deployed, sway), P4, P5 and P6A (including even renderer pacing at 4K60). P4b sits on the path but live does not depend on it. **Can follow streaming:** T1–T8. T1–T3 may be picked up in gaps after P1 or P2, but nothing on the path waits for them.

---

### P0: Activate the hotfixes (DONE 2026-10-11, 15:01)

**What was done.**
1. **Offline verification** in an isolated, network-namespaced scratch copy:
   - 72 targeted checks covering legitimate vs hostile inputs for every fix;
   - all 68 scheduler template strings and every overlay/prompt template render identically under the sandbox;
   - the legacy test suite gave an identical pass/fail set vs baseline (119/78; one pre-existing flaky test).
2. **Frontend:** the original source rebuilt byte-identically to the live `dist/`, proving the rebuild would ship only the AdminPanel change. The patched bundle contains 0 of 6 device-identifier values.
3. **Three-lens adversarial review** (behaviour, bypass, runtime): no blockers. Its minor findings were fixed before deploy, including closing the pre-existing bucket-copy hole.
4. **Deploy:** the frontend went in via the atomic `dist_next` swap (`dist_prev/` is the rollback), then Flask restarted in its screen session. Before restarting, the scheduler state was backed up and the in-memory mask and override state recorded (all defaults, so nothing was lost).

**Post-deploy evidence.**
- Health was OK about 17 s after restart; all five schedulers resumed.
- RSS went from 5.74 GB to about 160 MB; threads from 110 to 91.
- `/api/publish-destinations` returns no device keys.
- `/test-overlay` returns 401 without the token.
- An `/api/files` sibling-prefix path returns 403, while the legitimate path returns 200.
- Six WS clients reconnected within 30 s, and light-sensor readings resumed at about 2 Hz.

**Still to do.**
- **Committed 2026-10-11** with the owner's OK: `2dad861b` on `main`, tag `prod-hotfix-2026-10-11` (14 files by explicit path; not pushed). Never use `git add -A`, `stash`, `checkout` or `reset` in this tree.
- Watch the RSS slope and the `Stripped HTML/blocked fields` and `Refusing source` warnings for 24–72 h.
- **Rollback:** `git checkout HEAD -- <the 13 files>`, restart, and swap `dist_prev` back.

**Behaviour changes.**
- `/test-overlay` needs the admin token; `build/test-display.html` now sends it and reports failures.
- Publish refuses local sources outside the media store and the temp dir (none seen in two days of logs).
- The admin panel's screen list loads asynchronously.

**Lesson recorded for P2.** `start.sh`'s `pkill -f "python.*app\.py"` matches *any* process whose command line contains that text. During this deploy it killed the operator's own shell (the restart itself completed). Replace it in P2 with a pidfile and the screen session name (per the single-admin-pattern ruling, §3.8).

---

### P0.5: Display page hygiene and hold-last mask (design step 0)

**Goal.** Stop the display page's leaks and make every later server restart invisible on the screens, before any server work.

**Why now.** It is frontend-only, small and reversible, and it protects everything after it: with hold-last in place, the P2 cutover and every later deploy hold the last picture and mask instead of blacking the screens (D14). The leak fixes ship in week one.

**Steps:**
1. Ignore WS frames that have no `html`. This stops the phantom overlays created by lux, progress and transcription frames (`useOverlayWebSocket.ts:60-101`).
2. Overlay scripts run once per overlay id and are removed with it (replaces `index.tsx:144-153`).
3. Poll timeouts (4 s `AbortController`) and an in-flight guard on every poll.
4. **Black background for display routes only** (today a crash or reload shows white: the body uses the light theme and `dist/index.html` sets no background). `index.html` is shared with the operator SPA, and once the bundle's CSS loads `src/index.css:104-105` (`body { @apply bg-background }`) would override a plain `html,body` rule. So an inline head script in `index.html` adds class `display` to `<html>` when `location.pathname` starts with `/display/`, with `html.display, html.display body {background:#000}` (higher specificity than index.css's `body` rule). The operator UI is unchanged.
5. An ErrorBoundary that keeps the last base image and mask and reloads only when `/api/health` answers, with backoff.
6. **Hold-last mask in the legacy `useMask`** (D14). Today a failed mask fetch blacks the screen (`useMask.ts:46-50`). Hold the last mask until the server answers again. The 10-min limit and the offline schedule start at P4b, when `policy.offline.schedule` exists.
7. **Gate the existing timed reloads** (2/6/10/14/18/22h, `index.tsx:369-422`, which today call `location.reload()` unconditionally) on `/api/health` answering within 2 s; if it does not, retry every minute and skip the reload until it does. Otherwise a reload during an outage lands on Chrome's own network-error page, which is neither black nor the last picture. Small visible change (D27 item 5).

**Approval.** Steps 1–2 are leak fixes under the owner's standing pre-authorisation for security and leak fixes that do not change app behaviour. Step 3 has no visible effect; it ships with them if the owner agrees it falls under the same pre-authorisation, otherwise with steps 4–7. Steps 4–7 are small visible changes and need the owner's yes (D27 items 4–5).

**Test.** V1 against the candidate bundle (V1 rules, §3.9), served from a throwaway static server on a registered free port (`docs/PORTS.md`; not :3001 or :8081, which are exlib's) until P2's dev stack exists (§3.9). On production, count `<script>` elements over 24 h through CDP, with `--allow-production` (§3.9) and the owner's OK.

**Rollback.** The previous `dist/` (keep a `dist_prev`, as in P0).

**Behaviour changes.**
- No phantom overlays; overlay scripts no longer accumulate.
- When the server is unreachable, the screens keep the last mask instead of going black (D14, recommended default pending the owner's yes).
- A crash or reload shows black instead of white.
- A timed reload is skipped, and retried every minute, while `/api/health` does not answer.

**Exit criteria.**
- `<script>` count flat over 24 h on both screens.
- With V1 on the candidate bundle, stopping the throwaway server's `/api` proxy for 15 min (or blocking `/api/devtest/mask`) leaves the mask unchanged.
- No white frame on a forced reload.
- A timed reload with `/api/health` unreachable is skipped and retried, not run.

**Effort / risk / downtime.** 1–1.5 days / low (frontend only, `dist_prev` rollback) / none: the kiosks pick up the bundle at their next timed reload, or at a same-URL reload in a quiet hour.

**Parallel with.** P1 groundwork.

---

### P1: Safety net (additive only)

*2026-10-14 ruling (§1): cut to the prod-isolation guard (step 1) and real tests for the system core; no CI, per-feature goldens or ratchet gates.*

**Goal.** Tests, gates and CI that physically cannot touch prod, and characterisation coverage of the seams P2 to P5 will touch.

**Why now.** P2 edits paths across about 12 untested modules. The F821 gate and the goldens must exist *before* that. Until P2, every file in this tree goes live on the next restart, so P1 only adds files.

**Steps:**
1. `pytest.ini` and the root `conftest.py` prelude, merged with the existing `tests/conftest.py` (§3.9). Include the socket guard, the ssh block, the `import app` guard and the canary.
2. `git mv` `utils/test_ws.py` to `scripts/manual/` (the root test scripts were moved or archived on 2026-10-14; §3.9). Do not move `routes/test_*_ui.py`, which `app.py` imports.
3. Record the baseline report of the 239 legacy tests and mark the failures `legacy`.
4. `pyproject.toml` with ruff (F rules blocking). **Every existing F821 gets a `noqa` with a reference.** Pure bug fixes go to T2. The dormant-feature ones (`samsung_utils` `log_debug`, i.e. Frame photo sync; `publisher` `_update_published_info`, i.e. Blank; the `utils` EXIF imports) reference D17 and are never auto-fixed, because fixing them would start uploading to the TVs or change metadata.
5. Frontend ratchet scripts (tsc 24, eslint 407 as of 2026-10-14; rules-of-hooks blocking on touched files), vitest scaffolding, `tsconfig.strict.json` for the display page's `control/` and `live/` modules and `src/shared` (it already covers `src/shared`, strict-clean: `npm run typecheck:shared`).
6. import-linter config (contracts activate as `sm/` appears) and a cycle-size ratchet script (24 / about 170).
7. Characterisation tests:
   - `send_overlay` payload per template (generating, prompt, guidance, schedule, explain), by monkeypatching the sender;
   - `publish_to_destination` file and `bucket.json` effects in a tmp tree via `chdir`;
   - the 13 schedule scripts against `schedule.schema.json.j2` (5 xfail);
   - the first `resolve_schedule` goldens at fixed clocks.
8. GitHub Actions workflow.

*Room-AV proposal pending AV-D1/AV-D6:* the guard also blocks UDP `sendto`; device fakes; optionally move the `resolve_schedule` goldens and schedule-schema checks to T6, keeping the `send_overlay` and `publish_to_destination` goldens (see §5.2).

**Exit criteria.**
- `pytest` from the root collects only `tests/`.
- The canary passes, and the guard log shows zero prod or non-loopback attempts.
- The repo tree checksum is unchanged after a run.
- CI is green, and the ratchets fail on regression.
- Every P0 hotfix has a regression test.

**Effort / risk / downtime.** 3–4 days / low / none.

**Parallel with.** P6A design work with bd (API freeze).

---

### P2: Production separation, composition root, behaviour-neutral fix batch (H2)

*2026-10-14 ruling (§1): no release pipeline, `/srv` move, rehearsal or soak. Keep untracking the live state in place and the correctness fix batch; build the settings seam and composition root only as far as later features need them.*

**Goal.** Prod runs from an immutable release, supervised the same way as every other server on PC22 (a named GNU screen session started by its project script, managed by Spectre; §3.8), with all runtime state outside git. Importing `app` has no side effects. The cheap correctness and performance fixes have landed.

**Why now.** Every later phase needs reversible deploys, a dev stack and a hub started explicitly. The H2 fixes are cheap, neutral and remove live hazards (C1, C9, C12) that P4 and P5 would otherwise build on. **P1 and P2 are a hard gate:** no server change from P4 on ships to production before P2's exit.

**Steps:**
1. **Settings seam.** Add `sm/settings.py`; `config.py` re-exports it. Route these through it:
   - `OUTPUT_DIR` and `STATIC_FOLDER`;
   - `SEARCH_PATHS` (prepend `CONFIG_DIR`, keep everything else including `routes/data`; remove the empty `sysprompts`);
   - `bucketer.BASE_OUTPUT`;
   - `publisher.OUTPUT_ROOT` and `PUBLISH_DESTINATIONS_FILE`;
   - `manage_jobs` `runpod_jobs.log`;
   - `file_api` roots;
   - `get_scheduler_storage_path` and the hard-coded dir in `initialize_schedulers_from_disk`;
   - `VARS_REGISTRY_PATH`, logger `logs/` and `ALERT_STATE_PATH`;
   - the alert email's log-tail reader (`utils/alerts/dispatcher.py` `_log_tail`, which hard-codes `logs/` next to the code), so alert emails keep their log tail after logs move;
   - two **operator-written** paths that live in the code tree today: UI-saved schedule scripts (`routes/scheduler/scripts`, the `file_api` write root) and Light Sense calibration (`routes/data/intensity.json`, `lightsensor.py` `save_mapping`). Both move to `state/`; otherwise saves land in the release folder and vanish at the next deploy;
   - the token paths;
   - the WS host and port (hard-coded `0.0.0.0:8765`).
   Log the resolved-path report at startup. Add `SM_SCHEDULERS`.
2. **Composition root.** `sm/runtime.start_services()` takes over the scheduler resume (`app.py:84-86`) and the WS start. `app.py` `__main__` calls `start_services(); serve()`. Prod behaviour is identical; importing `app` starts nothing. Lift the P1 import guard and add the `/output` contract test via `create_app`.
3. **H2 batch.** Each item gets a test:
   - (a) **Cross-loop bridge (C1).** Record the WS loop **every time** the supervised WS server (re)starts (`app.py:196-212` restarts it on a fresh loop, so a reference taken once would break every overlay after the first WS restart). `send_overlay` uses `asyncio.run_coroutine_threadsafe(send_overlay_to_clients(data), ws_loop)` with a done-callback that logs errors. Remove the `get_event_loop` / `new_event_loop` branches. *Benign behaviour change:* overlays sent mid-instruction appear immediately instead of after the instruction yields, and waitress threads no longer fan out synchronously. Send to clients concurrently with a short per-client timeout (about 2 s), so one backed-up kiosk cannot delay the others, which a plain proper-loop send otherwise would (P4's per-client queues supersede this).
   - (b) `audio_utils` uses the same bridge; close the event loop it leaks per AssemblyAI session thread. **Pulled forward from P4** (its step 5 now only confirms it): move AssemblyAI connect/disconnect off the WS loop (executor, one session per recording). Without this, routing every overlay through the WS loop would make all overlays wait behind the up-to-15 s transcription freezes that today only stall the WS loop.
   - (c) **C9.** The `wait` path skips `update_scheduler_state` only when the **whole serialised state** is byte-identical to the last write. A context-only comparison is not neutral: thrown and consumed events, trigger fire records and history are persisted by this same write, so they would reach disk up to ~60 s later, and a crash in that window could lose events or re-fire triggers. Stop deleting the wait-log rate-limit key every iteration (`scheduler.py:1323-1328`).
   - (d) Logger: move `inspect.stack()` after the throttle check. Clamp `/api/logs?limit` (≤0 → default; cap 1000). Cap the size of `/api/log` entries (keep the endpoint; it has had no frontend client since `use-console.ts` was removed on 2026-10-14, and nothing else in the repo calls it).
   - (e) Atomic `bucket.json` writes in `save_meta` (tmp + `os.replace`). The RMW lock waits for T4.
   - (f) **Atomic pointer and sidecar (C12).** `copyfile` to a **unique** unpolled temp name in `output/` (`tempfile.mkstemp(dir=..., prefix='.<dest>.')`, so two simultaneous publishes cannot collide), `shutil.copymode` from the source (keeps today's permissions and copies no timestamps), `os.utime(tmp, None)`, `os.replace`. **Do not remove other-extension siblings** (§3.6). Never `copy2`/`copystat` (they roll mtime back). Keep `/output` Last-Modified/404 semantics, and test against the kiosk's newest-of-`.jpg/.JPG/.mp4` logic.
   - (g) `publish_api` uses a reloading loader (mtime) for its sanitised view.
4. **Dev stack** (`deploy/dev-stack.sh`, §3.8), including a minimal fake RunPod job that publishes a fixture image with progress ticks.
5. **Release pipeline.**
   - `deploy.sh`, `rollback.sh`, `requirements.lock` and `.gitattributes` export-ignore.
   - `start.sh` (screen-machine-12's; agree the change with it) starts sm-web in the `flask` screen session from `/srv/screen-machine/current` with `config/sm.env`, a restart-on-crash loop with backoff, and a pidfile; Spectre's card shows and controls it (§3.8). No systemd unit.
   - **Generate `sm.env`; don't copy `.env`.** Loaders disagree on inline `# comments` and quoting (python-dotenv strips them; other loaders may not), and four `.env` lines (`VITE_API_URL`, `VITE_WS_HOST`, `VITE_URL`, `VITE_APP_BASE_URL`) carry unquoted inline comments that python-dotenv strips today. Parse `.env` with python-dotenv and write clean `KEY=value` lines (0600). The startup path report lists which keys loaded, never their values.
   - **Frontend build inputs.** `deploy.sh` builds from a git export, which has no `.env`, so every baked `VITE_*` value would be empty: no overlays, the operator UI pointing at localhost, QR and app links reading 'undefined'. Ship P3 step 1 (API and WS addresses derived at runtime) **before or with** the first pipeline deploy, and feed the remaining build-time values (`VITE_URL`, `VITE_APP_BASE_URL`) from `config/frontend.env`. `deploy.sh` refuses to build if a required value is empty.
   - `deploy.sh` creates the `current/output` symlink as a cwd safety net.
   - Windows boot: install the ready replacement boot path (`scripts/wsl-boot.sh` plus Spectre's prod start; owner's OK) and retire the two stale tasks (§3.8), then point it at the `sm-web` unit.
   - The nightly backup job (scheduled like the other services' jobs, §3.6).
   - Deadman: 5xx or non-JSON = down, `ok:false` = degraded.
   - Check that `/srv` and the repo are on the same filesystem (`stat -c %d`).
6. **Rehearse** on :5050/:8775 from a release dir with copied state and `SM_SCHEDULERS=none`. Use a laptop browser at `/display/devtest`, run the path report and check the `/output` contract.
7. **Cutover window** (15–30 min). Do not let the window span a reload hour (2/6/10/14/18/22h) unless P0.5's gated reload (step 7) has shipped.
   - stop the screen session;
   - `mv output` → `/srv/screen-machine/output` and leave the symlink `repo/output` → it;
   - **copy** (not move) the scheduler state, vars, `runpod_jobs.log`, `alerts_state.json`, `src/data/publish-destinations.json` → `config/publish-destinations.json`, `.env` → `config/sm.env` (generated, not copied; step 5), the token files → `config/tokens/`, the UI-saved schedule scripts → `state/scheduler/scripts/`, and `routes/data/intensity.json` → `state/`;
   - in the **copied** scheduler state and vars, rewrite absolute dev-tree paths (`/home/gjbm2/dev/screen-machine/output/` → `/srv/screen-machine/output/`; north-screen and the-beast hold such paths today), after reviewing a dry-run diff;
   - start the unit and run the P0 smoke checklist.
   **Rollback (≤2 min):** stop the unit, copy `state/` back into the tree, and run the documented old launch command. The output symlink keeps the old tree working.
8. **After a 72 h soak**, in the dev checkout (no longer prod):
   - commit `git rm --cached` of the live state, the stale `routes/scheduler/_vars.json`, `media-server/maintenance.log` (and drop its un-ignore), the token files, `debug/*.unf`/`*.tgz`, `wav/` and the junk (Appendix A);
   - delete `routes/data/publish-destinations.json` only;
   - replace `src/data/publish-destinations.json` with `publish-destinations.example.json`;
   - remove the `repo/output` symlink, but only once `grep -r /home/gjbm2/dev/screen-machine /srv/screen-machine/state` comes back empty.
   Never pull that commit into any tree that still holds live state.

*Room-AV proposal pending AV-D1/AV-D6:* the Siren subject prefix and ping URL from env; Maestro exports in the nightly backup; the Windows boot task also starts `room_service` (see §5.2). *Since 2026-10-14 `sm-room` runs as a screen session via `scripts/ctl`, and boot is `scripts/wsl-boot.sh` plus Spectre logon tasks (`docs/OPS_CONTROL.md`).*

**Behaviour changes.**
- Overlays appear immediately instead of after the current instruction yields, a backed-up kiosk is no longer silently cut off, and send failures are logged (H2a).
- Voice transcripts may reach the operator UI just after the schedule has reacted to them (H2b), and 'broadcast sent' log lines mean queued, not delivered.
- One 15–30 min outage at cutover. With P0.5's hold-last mask shipped first, the screens hold their last picture and mask through it instead of going dark (D14); still pick a quiet hour. Every later deploy restarts the app for about a minute.
- Edits in the dev checkout (code, overlay templates, `config.py`, `routes/data`, `workflows.json`) no longer reach prod until deployed.
- Live state, logs and media move to `/srv/screen-machine`. `/output` URLs don't change, but anything reading the old repo paths sees stale data.
- Fewer debug lines: scheduler 'waiting' lines drop to one per 30 s, and `/api/logs?limit` is clamped.
- Everything else is neutral **only if** the guards in steps 1, 3, 5 and 7 are in place: WS loop re-recorded on restart, per-client send timeout, transcription off the loop, byte-identical state comparison, no sibling deletion, generated `sm.env`, frontend build inputs, operator-written paths in `state/`, and the path rewrite. The 2026-10-11 behaviour review found that missing any one of them causes a visible regression.

**Exit criteria.**
- Prod `/proc/<pid>/cwd` is `/srv/screen-machine/current`, running in the `flask` screen session via its start script and visible in Spectre, and it restarts by itself after a kill.
- The dev checkout's `git status` is unchanged over 24 h of prod running (bd's `experiments/` excepted).
- One deploy and one rollback each complete in under 2 min.
- Importing `app` in a test starts no threads.
- RSS is under 1 GB at 72 h, growing under 50 MB/day.
- No writes land in any git tree.
- The backup has been restored once on the dev stack.

**Effort / risk / downtime.** 5–7 days / medium (path edits; mitigated by identical defaults, the path report, the rehearsal and copy-not-move) / one 15–30 min window plus a few 1-minute restarts.

**Parallel with.** P6A (bd builds `stream_service/` against the frozen API); T1 backend junk removal.

---

### P3: LAN origin and network reach (mostly ops, alongside P4 and P5; finishes before production live)

**Goal.** Kiosks load from a stable LAN origin that is a secure context, with hardware decode verified. WAN exposure is reduced to the tunnel/ingress.

**Why now.** It closes the biggest live risk (the unauthenticated APIs reachable from the internet) weeks earlier. It is required for streaming: no hairpin, no Private Network Access block, and WebCodecs becomes possible. **P3 must finish before production live**, because Private Network Access for a public-origin page opening `ws://` to a private IP is unverified. Each step is reversible with a one-line edit.

**Steps.** Each kiosk step is one variable, per screen, south first, with a 24–48 h soak.
1. **Frontend:**
   - ~~delete the `api.ts:108` mock-mode trap~~ done 2026-10-14 (staged, not deployed; T7). on http pages `src/shared/http/client.ts` still resolves `VITE_API_URL || 'http://localhost:8000'`, which the next bullet replaces;
   - derive the API base (`/api`) and WS URL from `window.location` or `/api/runtime-config`. *Partly done 2026-10-14 (screen-machine-68, owner's instruction, deployed):* pages loaded over https (the friendly name `https://screen.finsburybankforsavings.com`, Caddy on PC22 → Flask) use `${origin}/api` (`src/shared/http/client.ts`) and `wss://<host>/ws/` for the overlay socket (`Index.tsx`), which avoids mixed content. http pages (the TVs and the public IP) still use the baked `VITE_API_URL`/`VITE_WS_HOST`;
   - drop the baked `VITE_API_URL` and `VITE_WS_HOST`;
   - **QR codes and app links:** overlays build QR codes from `VITE_URL` (the public IP today). Phones in the room reach that only while the router forwards and hairpin exist, so before step 8 point `VITE_URL`/`VITE_APP_BASE_URL` at an address phones can still open: `sm.lan` (needs router DNS for phones, not just `/etc/hosts` on the mini-PC), a VPN name, or a tunnel route for just the pages QR codes open (owner decision);
   - deploy through the pipeline;
   - verify `grep -E '95\.141|samsung-token' dist/` is empty.
2. Give 192.168.1.205 a DHCP reservation and a LAN name (`sm.lan` via router DNS or `/etc/hosts` on the mini-PC).
3. **Kiosk A, URL only:** `URL_SOUTH=http://sm.lan:5000/display/south-screen?kiosk=south`, then north (`?kiosk=north`), in `kiosk-wayland.sh` (and in `kiosk-loop.sh`, so the X11 fallback matches). The `?kiosk=` flag marks the page as the kiosk role for shadow acks and `/glass` (D27 item 13); adding it in the same edit means one Chrome relaunch per screen at a quiet hour. Point deadman `PROBE_URL` at the LAN URL.
4. **Kiosk B, flags:** a **single merged** `--enable-features=` and a single merged `--disable-features=DBusDaemon,...` (a repeated switch overwrites the earlier one; `lab-chrome.sh:24`), plus `--unsafely-treat-insecure-origin-as-secure=http://sm.lan:5000` (not the stream service's `http://sm.lan:8702`: live plays inline over MSE, which does not need the stream origin to be secure), `--autoplay-policy=no-user-gesture-required`, `--disable-renderer-backgrounding` and `--disable-background-timer-throttling`, in the active launcher (`kiosk-wayland.sh` under sway, keeping its zero-copy features in the same single switch; `kiosk-loop.sh` on the X11 fallback). Verify `isSecureContext`.
5. **Kiosk C, VA-API (D7): packages APPLIED 2026-10-11 by bd with the owner's authorisation.** `apt-get update` first, because the lists were stale since February. 8 packages including intel-gpu-tools, 0 upgrades. gjbm2 added to render+video. vainfo shows VLD for H.264 CB/Main/High, HEVC Main/Main10/12/422/444, VP9 0–3 and AV1 P0. **Chrome flag also APPLIED 2026-10-11 (bd, owner OK):** `AcceleratedVideoDecodeLinuxGL` is in the single merged `--enable-features` switch in the production `kiosk-loop.sh` (backup `/usr/local/bin/kiosk-loop.sh.bak-20261011-140753`; repo copy `media-server/remote-scripts/kiosk-loop.sh`, not yet committed). Since the move to sway, `kiosk-wayland.sh` carries the same flag plus, by default, the zero-copy path (§3.5); `kiosk-loop.sh` is the X11 fallback. **Status: proven in production 2026-10-13.** 4K30 HEVC played in both production Chromes after the X display fix, then 4K60 HEVC under sway, and Linux Chrome has no software HEVC decoder, so decode is hardware. (Earlier it was proven only in a lab Chrome, H.264 via VaapiVideoDecoder.) To re-check later: `beelink/cdp_probe.py 9222 media` during `<video>` playback, using `~/.venv/bin/python` (system python lacks `websockets`); the production guard (§3.9) means eval or navigate on 9222/9223 needs `--allow-production` and the owner's OK. **Chrome is not apt-held:** the apt candidate is now 155.0.8059.39 (the VA-API step ran `apt-get update`), so a manual `apt upgrade` would jump from 136 to 155, and the flag rules here were verified for 136 only. **Owner decision pending:** `apt-mark hold google-chrome-stable` (D7; also design decision 16 in D27). Do it before the devtest live step (P6B stage 1). Use the checks below after any kiosk change. **Verify with:** WebCodecs `isConfigSupported({hardwareAcceleration:'prefer-hardware'})` → true; `mediaCapabilities.decodingInfo` → `powerEfficient:true`; CDP Media domain `kIsPlatformVideoDecoder:true` or `VaapiVideoDecoder` in a `--vmodule` log. **Do not** rely on CDP `SystemInfo.getInfo().videoDecoding`, which stays empty until chrome://gpu has been opened. Also confirm GPU compositing is intact and mp4 clip playback is unchanged.
   **Display mode (2026-10-13):** both TVs now run **3840x2160@60**. The EDID gained an HF-VSDB at 600 MHz around when the owner tried Game Mode, but 4K60 does not depend on Game Mode (notes §3). Game Mode is never used (owner, 2026-10-13; D24): both TVs read Filmmaker Mode with Game Mode on Auto, which is inactive (no ALLM, and wlroots sends content type Graphics); setting it to Off is the owner's call, timed with bd. No transition changes any TV setting. The One Connect wireless path is fixed (about 35–45 ms). Apply the remaining verified settings in `docs/FRAME_PRO_SETTINGS_2026-10.md` by hand (owner time; a device change, so the owner's OK), one TV first, then re-read the IP Control baseline (§3.5); where the TVs differ from that list (Filmmaker Mode, BT.1886, Local Dimming Standard), their values are the owner's current choice and stand unless the owner changes them. With 4K60 present, the MaxClock override (X11 fallback only) is not needed; re-check the modes (`swaymsg -t get_outputs` under sway, xrandr on the fallback) before ever adding it. Re-check the colour format at 4K60.
   **Display presentation: DONE 2026-10-13** (bd; §3.5). *First, X11:* one X screen per TV (ZaphodHeads), deployed at about 18:31 UTC, replaced the single X screen that presented only about 60 frames/s across both TVs; it fixed 4K30 (0.28% / 0.24% dropped over about 3 min) but not 4K60 (74–82% dropped, VideoEnhance saturated). *Then, Wayland:* at 19:31 UTC bd moved the kiosk to sway 1.9. `kiosk.service` is now sway with `kiosk-wayland.sh` (one Wayland Chrome per TV); the X11 setup is the disabled `kiosk-x11.service`. **Fallback:** `sudo systemctl disable --now kiosk && sudo systemctl enable --now kiosk-x11` (4K30 only there). Chrome 136's zero-copy path works under sway (VideoEnhance at 0%); a pre-made HEVC 4K60 30 Mbit/s file on both TVs dropped 0.03% with rAF at 60.0 and 0 late frames; reboot (kiosk up in about 40 s), Chrome-kill respawn, a TV missing at start and runtime unplug all passed. The TVs' picture settings read over IP Control were unchanged by the move. bd is porting the status scripts, the admin API display check (`routes/admin_api.py`) and `deploy-kiosk.sh` to Wayland (uncommitted); any `routes/admin_api.py` change goes live only at the next Flask restart, which is the owner's call, and should be reviewed first. **The display-fix bar** (referred to elsewhere in this plan): each window's rAF/compositor rate ≥ ~59 fps while both windows play live at the primary profile (4K60 HEVC, D11), and ≥ 99.9% of video frames presented (rVFC / `getVideoPlaybackQuality()`) over 30 min, with 0 late renderer frames and ≤ 1 stall per 10 min (design §6 item 1's pass bar; the pre-made file reached 99.97%). **So far:** the 4K60 file run meets the rate and presented-frame parts over about 3 min (rAF 60.0 with 0 late rAF frames, 0.03% dropped; the renderer part applies only to live); live 4K60 from the lab drops 6–7% until the renderer paces evenly (P6A) and the player stops playing above 1.0× (P6B); live 4K30 has not been re-run under sway. The 30-minute live run, recording the rAF rate during playback, is still to do, then re-run the lab's presented-frame measurements (design §6 items 1 and 3; item 3c, the mask over live, is out). bd records outcomes, with verify and undo commands, in `docs/KIOSK_INFRASTRUCTURE_NOTES.md` (§3 "Wayland (sway) kiosk since 2026-10-13"), which remains the canonical kiosk infrastructure record.
   Add the mini-PC watchdog that alerts if a screen falls back to 4K30 or 4:2:0 (reading the output modes through `swaymsg` under sway, xrandr on the fallback), and run the one-off wireless-link A/B (§3.5). On the X11 fallback, the lab Chrome (`experiments/stream-latency/beelink/lab-chrome.sh`) is launched as `/opt/google/chrome/google-chrome` with its own profile (`/tmp/chrome-lab`) and CDP port 9224, so `kiosk-loop.sh`'s hotplug `pkill -f "google-chrome-stable.*--kiosk"` does not touch it; only an explicit admin "restart kiosk" (pattern `google-chrome.*kiosk`) closes it. A hot-plug respawn does, however, restack the kiosk windows above it, and Chrome on X11 stops drawing a fully obscured window (§3.5 window stacking; V1 rules in §3.9). Under sway, `lab-chrome.sh` is X11-only, so V1 uses a production Chrome over CDP (§3.9).
6. **Kiosk D: wired Ethernet (D8): DONE 2026-10-13 (powerline primary; 24 h soak pending).** The owner connected the mini-PC by powerline. Canonical details: `docs/KIOSK_INFRASTRUCTURE_NOTES.md` §5 and `media-server/README.md` Critical Config §6.
   - **Setup:** `enp1s0` gets DHCP **192.168.1.10** and is the default route (metric 100, `/etc/netplan/60-ethernet.yaml`, separate from the Wi-Fi file). Wi-Fi **.92** stays up as the fallback at metric 600. A policy rule (from .92 → Wi-Fi-only table 102) keeps SSH/admin on .92 working over Wi-Fi even if powerline fails, so `MEDIA_SERVER_HOST` stays .92. ARP sysctls are set for two NICs on one subnet. Installer `media-server/local-scripts/install-ethernet-primary.sh` (5-min dead-man); undo with `sudo eth-primary-rollback` on the box.
   - **Measured (2026-10-13):** 167–176 Mbit/s (100 MB ×3) and 188 Mbit/s sustained (1.2 GB in 51 s) towards the box; upload about 80–160 Mbit/s; idle ping p50 3 ms; during a saturating download p50 27 / p99 41 / max 49 ms with 0% loss (800 pings). That passes the acceptance targets (≥~120 Mbit/s TCP worst hour, p99 loaded ping <50 ms) on single runs. **The 24 h soak decides** (`scripts/powerline_soak.sh` → `logs/powerline-soak-*.csv`, started 15:44 UTC). UDP loss at 80 Mbit/s has not been measured yet (no iperf3 on the box).
   - **Failover:** when the cable link drops, the default route moves to Wi-Fi within seconds and the kiosk URL keeps loading; it returns to Ethernet afterwards. **Known gap:** if the powerline path dies while the adapter keeps its Ethernet link up, there is no failover and the kiosk pages stall (remote access on .92 still works). **To do:** a small Ethernet path watchdog on the box (ping the router via `enp1s0`; drop it from the default route after repeated failures).
   - **Wi-Fi fallback** (unchanged, see the notes §4): 15–20 Mbit/s ceiling on the fast AP, pinned there by `wifi-prefer-ap`, which is too little for live 4K. Two 4K60 streams at about 30 Mbit/s each (about 60 Mbit/s) fit the powerline link but not Wi-Fi. Anything live should degrade (lower profile or fall back to still; D11 ladder) while the box is on Wi-Fi.
7. **WSL clock (ops): FIXED 2026-10-11 16:19 BST (15:19 UTC)** (owner applied, bd verified). Before: the WSL clock had run fast for months: **+2.0%** (+20,286 ppm) when measured, +4.6% on average since the July boot, and at times ±10%. `CLOCK_MONOTONIC` ran fast and `CLOCK_REALTIME` was stepped back about 0.6 s every ~32 s, because WSL's hidden chronyd (syncing to the Windows host clock) fought Ubuntu's systemd-timesyncd. Fix: timesyncd disabled and masked in WSL; Windows W32Time set to automatic start with time.cloudflare.com / time.windows.com peers, a 1024 s poll interval and a forced resync. After: chrony unwound within about 30 s (frequency about −20 ppm, was −21,000), the rate error is gone and the steps have stopped. **Remaining:** the Windows host was about 0.43 s ahead of UTC at the fix and W32Time slews it out slowly (0.21 s after one hour, 0.18 s at 17:30 BST), so WSL's wall clock (which follows the host) takes a few hours to converge; re-check the absolute offset then. Read-only check: `scripts/wsl_clock_check.py` (it checks the **rate** only, not the absolute offset); verify/undo commands are in `docs/KIOSK_INFRASTRUCTURE_NOTES.md` §6. Good practice regardless: never compare raw timestamps across hosts (envelope `ts`, lux timestamps, kiosk stall judgements); use host-local intervals or a skew-aware sync.
8. **Network reach** (D4/D6), in the order of §3.7: VPN → ingress listener + tunnel for `/api/alexa` (signature verification can follow in T5; the ingress alone already shrinks exposure) → close 5000/8000/8080/2222/8081/3001 on the router and LAN-scope the portproxy and firewall rules. **Port rules are owned by Spectre** (screen-machine-ca; register `docs/PORTS.md`), so LAN-scoping goes through Spectre's lanWan modules, not by hand and not through `fix-wsl-networking.ps1`, which cannot run on this PC (§3.8). 8081 and 3001 are exlib's rules (Spectre `exlib-mobile`, `exlib-server`, created 2026-02-07), not lab rules; leave them to Spectre. Any rule scoped to the kiosk must admit **both** its addresses: 192.168.1.10 (Ethernet, primary) and 192.168.1.92 (Wi-Fi fallback and admin). Give both DHCP reservations. → 8765 closes after P4 step 7.

*Room-AV proposal pending AV-D1/AV-D6:* split P3 into *front* (LAN origin, `?kiosk=`, the Chrome hold) and *reach* (close the WAN forwards and narrow ngrok before any claim API ships, including 8765 after D5); DHCP reservations at each device's current address; a port register (D16); guest isolation; a UDP-loss test on the powerline; a LAN-scoped TCP portproxy rule for the room UI on :8700 (see §5.2). *Per `docs/PORTS.md`, Spectre creates the :8700 and :8702 rules (LAN only, never WAN) when room_service and stream_service go to production.*

**Behaviour changes.**
- Off-LAN access only via VPN; Alexa only via the tunnel.
- The kiosk origin changes, so its cookies and localStorage start fresh.
- Operators opening the UI at the new address re-enter the admin token and lose saved UI state (tabs, placeholders), because browser storage is per address.
- QR codes on screens must be repointed (step 1), or they stop working when the forwards close.
- Deadman probes the LAN address, so it no longer tells you whether the public path works (which is being closed on purpose).
- Kiosk Chrome gets the secure-origin, autoplay and no-throttling flags. Check whether Chrome shows an 'unsupported command-line flag' bar on the TVs.
- The display fix (step 5, done 2026-10-13) drives each TV from its own sway (Wayland) output; pictures look the same (the TVs' picture settings are unchanged), with smooth video up to 4K60. X11 tools (`xrandr`, `wmctrl`, `DISPLAY=`) no longer apply on the box; use `swaymsg`. On the X11 fallback each TV has its own X screen, and tools must use `DISPLAY=:0.1` for south.

**Exit criteria.**
- Both kiosks run on the LAN origin with `isSecureContext === true`, with `?kiosk=north|south` in their URLs.
- Hardware decode is confirmed for the shortlisted codecs in the production Chromes (HEVC done 2026-10-13; H.264 and AV1 if shortlisted).
- Chrome is held at 136 (D7), before the devtest live step.
- The display fix passes the display-fix bar (step 5), before the devtest live step; or it is explicitly deferred and live stays blocked.
- 8765 is closed on the WAN (after D5; P4 step 7, done with step 8).
- An external scan shows only the tunnel.
- Alexa and VPN operator access both work.

**Effort / risk / downtime.** 2–3 days plus owner hardware and Windows time / medium (a wrong URL means a kiosk outage, reverted in one edit) / about 1 min per kiosk restart.

**Parallel with.** P4, P4b, P5, P6A. Must finish before production live (P6B stage 2).

---

### P4: Realtime hub v2 and shadow acks (design steps 2–3)

*2026-10-14 ruling (§1): build the hub and the acks, but drop the dev-stack stage and the 7-day shadow exit; switch to push once both screens ack correctly, with the polls kept as the fallback.*

**Goal.** Thread-safe, non-injectable, ordered delivery with an authoritative snapshot on connect, v2 routed by path beside an unchanged legacy socket, typed ingest, and every display page reporting what is on glass before anything is push-driven.

**Why now.** Light push (P4b), overlays and content (P5) and live control (P6B) ride on it. It gives v2 clients a non-relaying, role-scoped path. The legacy relay on `/` stays sanitised (P0) until T8 removes it, and WAN reach to it ends when 8765 closes (P4 step 7). Shadow acks prove the push path against what the legacy logic actually shows, with no visual change.

**Steps** (protocol detail: design §2.3–2.4):
1. Write `schema/realtime.v2.json` and codegen, with contract tests on both sides and a drift check.
2. Build `sm/realtime/*` per §3.4: owned loop started by `start_services`; **mounted by path** on :8765 (`/v2/screen/<id>`, `/v2/ingest/<kind>`, `/v2/operator`), with only the path router added to `overlay_ws_server.ws_main` and the legacy `/` handler unchanged byte-for-byte. Per-client `Queue(256)` with latest-value coalescing for retained types; 5 s send timeout → 4008; `hb` (2 s idle, 6 s dead); Origin allowlist, role-scoped inbound, 64 KiB and 50 msg/s limits; epoch and rev; per-shape logging of legacy traffic (evidence week, passive); metrics.
3. Retained types: `policy` (every source starts as `poll`/`legacy`, `ctl: shadow`), `screen.state` (base derived exactly as today: the newest of `.jpg/.JPG/.mp4`, `useFilePolling.ts:58-86`), `mask.set` (the existing computation, until P4b), and overlays mirrored from `send_overlay` through the bridge (render unchanged). Legacy clients keep receiving what they receive today. For V1, devtest's retained `screen.state` and `mask.set` can mirror those of the screen under test (a server-side switch).
4. ScreenStateStore (`state/screens/<id>.json`, atomic), the change log (`logs/screens/<id>.jsonl`), KioskRegistry, and the HTTP twin: `GET /api/screens/<id>/state|glass`, `POST /api/kiosks/state`.
5. Typed ingest for the worker (`ingest.progress` → `job.progress` in a bounded ProgressStore) and `job.*` from the generate loop. Confirm that AssemblyAI connect/disconnect no longer run on the hub loop (moved to P2 H2(b)). `connection_registry.py`, the custom keepalives, `job_progress_listeners_latest` and the raw lux rebroadcast stay with the legacy handler until T8.
6. **Shadow acks:** a ControlClient in the display page, mode `shadow`. It sends `hello`, then acks and `kiosk.state` describing what the *legacy* logic is showing, and ignores pushes when rendering. The server computes publish-to-glass time and push-vs-poll divergence. `?ctl=legacy` is a static override read before any v2 code runs. `?kiosk=` arrives with P3's URL edit; until then shadow reports are tagged by viewport. It ships with Vitest for ControlClient and the reducer (snapshot, epoch, content key).
7. **RunPod progress (D5)**, alongside, with P3 step 8; not a gate for P4b or P5; must finish before production live. Either the worker moves to `progress_update` and the backend polls, or `/ingest/runpod` gets a per-job HMAC on the ingress listener. Update the worker, confirm progress, **then close WAN 8765** (P3 exit). Effort: about 1 day for the backend side, plus the worker change (its code is not in this repo).
8. Health: hub metrics, loop lag, per-role counters; the hub thread supervised and restarted; Siren `realtime.loop_lag`, `realtime.hub_restart` and `realtime.no_subscriber`.

**Test.** Contract tests and a CLI client on the dev stack; then production with no page connected; then V1 (V1 rules, §3.9); then V2 (shadow) on both screens.

**Rollback.** `SM_V2_WS=0` or a release-symlink flip; `policy.ctl=legacy` or `?ctl=legacy` for the pages. The legacy `/` path is untouched throughout.

**Behaviour changes.** None visible: the pages render exactly as before while they report in shadow.

**Exit criteria.**
- No websocket send happens outside the hub loop (grep plus a test).
- A raw-sender test client cannot make anything appear on a display, and a kiosk-role client cannot send anything but `hello`, `ack`, `kiosk.state` and `hb`.
- Old deployed bundles still show overlays; legacy `/` traffic is unchanged.
- Hub tests pass without importing Flask.
- 7 days of shadow with every push-vs-poll divergence explained; `/glass` shows desired vs applied for both screens.
- The per-shape traffic log is running.
- (Closing WAN 8765 after D5 is in P3's exit; step 7 runs alongside and does not gate P4b or P5.)

**Effort / risk / downtime.** Hub core 4–6 days, shadow acks 1.5–2 days plus 7 days' running time; step 7 about 1 day plus the worker change, alongside / medium (every screen depends on :8765; the unchanged legacy path, `SM_V2_WS` and the symlink rollback mitigate) / one restart.

**Parallel with.** P3, P6A, T1, T2.

---

### P4b: Light-reactive mask, Tier 1 (owner requirement; first push topic; design step 4)

**Goal.** The screens react to ambient light within about 50 ms of a sensor report (§3.5b), "not reliant on the 3s poll". Tier 1 only (D25).

**Why here.** Immediately after the P4 hub core and shadow acks, ahead of overlays, content and P5. It is the owner's first ask, it needs only the hub core and shadow acks, and it is a low-risk first push topic with a natural fallback (the existing poll).

**Steps** (filter, staleness and offline detail: §3.5b and design §2.6):
1. Pull forward T3's pure `compute_mask` (no `exec('config.py')`), with characterisation goldens comparing old and new outputs at fixed times and lux values. Move the curve parameters and light constants into `config/light.json`, reloaded on mtime, so curve edits still reach the screens within seconds.
2. `SensorService` and its filter (log-lux snap/drift, deadband, ≤ 20 Hz per screen, `transition_ms`), advancing only on ingest; staleness → solar plus `sensor.stale`; warm start from the persisted `last_mask`; overrides and `maskoff` persisted in `state/mask_overrides.json` and published immediately. Legacy `GET /api/<id>/mask` is served from the same service.
3. The display page applies `mask.set` through refs inside rAF and acks it when `policy.mask_source=push`, in art mode only (live is full intensity, §3.5b); the 3 s poll becomes the fallback when the WS is down. Offline, the page holds the last mask (P0.5) for 10 min, then follows `policy.offline.schedule` (D14); before this step (P0.5 alone) it holds the last mask until the server answers again, because there is no schedule yet.
4. Relay v2 in `deploy/sensors/light-relay/` (not `deploy/kiosk/`), source brought into the repo: host-agnostic, configured by server URL, sensor name and an optional key; on-change reports up to about 20 Hz plus a 1 Hz heartbeat; `cfg` from the server; typed `ingest.lux` to `/v2/ingest/lux`. Investigate the duplicate or burst readings (p10 interval 35 ms). Size `lux_history` by time (about the last 10 min) rather than by count (1,000 entries), so the Light Sense page keeps today's window when the report rate rises. It stays hosted on the mini-PC as an independent unit (D27 item 19).
5. Measure: ack-measured lag (server-side, from the ack plus the heartbeat RTT); a torch test filmed at 240 fps; a steady-light flicker check; the optical loop check. First confirm the TVs' Adaptive Picture, Brightness Optimisation, EyeComfort and Motion Lighting are off (§3.5), or the TV will also react and confound the measurement.
6. Rollout V3: devtest (via V1; V1 rules, §3.9), then south, then north; production flips only after P4's shadow exit (7 days, every divergence explained).

**Recommended by this plan, needs the owner's yes:** a token on `maskon`, `maskoff` and `brightness-override` writes before P4b ships (fast push plus open overrides is a LAN strobe vector; §3.7). *Room-AV proposal pending AV-D1/AV-D6:* the same token, plus a room hold (brightness and warm values, token-gated, with a TTL the room renews), an output slew limit and a loop-gain check with Hue in view (see §5.2).

**Rollback.** `policy.mask_source=poll` per screen, live; point the relay back at `/`.

**Behaviour changes.**
- In art mode, brightness and warm tint follow the light within tens of milliseconds instead of 0.5–3.5 s, with a different (tunable) smoothing curve.
- A dead relay ramps to the solar curve after 10 s instead of freezing the mask forever.
- Mask overrides and `maskoff` survive a server restart.
- If the owner approves the mask-endpoint token: `maskon`, `maskoff` and `brightness-override` writes need it, and the operator UI sends it.

**Exit criteria.**
- p95 report → style commit ≤ 50 ms idle on powerline (≤ 100 ms with the link saturated), measured in art mode; real light step → first visible change p95 ≤ 250 ms (filmed; target re-set from the filmed TV lag; design §2.6).
- No flicker under steady light; loop gain checked.
- Kiosk mask polling at most 2/min while connected.
- Poll fallback verified with the WS down.

**Effort / risk / downtime.** 3–4 days / low-medium (mask parity is visible; the per-screen flag and the poll fallback mitigate) / one restart.

**Parallel with.** P3, P6A.

---

### P5: Channels and the display page (design steps 5–7)

**Goal.** Per-screen screen state drives overlays and content by push, behind per-screen `policy` flags, and supervision replaces the 4-hourly reloads. There is **no parity port**: the same page keeps rendering, so the info panel, the event-trigger buttons, the fit/fill and mask toggles, the URL options and the phone layout carry over automatically.

**Why now.** It turns the proven push path (P4, P4b) into the content path the live work plugs into, and removes the timed blanking before decode load arrives.

**Steps:**
1. **Overlays through v2** (1–1.5 d): `overlay.show/clear` per screen, with `remaining_ms` in the snapshot so none are lost across a blip. Kiosk pages drop the legacy `/` socket when `policy.overlay_source=push`; one socket per page fixes the zombie reconnect (`useOverlayWebSocket.ts:47-58`). Overlays render in art mode only. Overlays and the info panel sent to a screen that is live are an owner decision with no default (D27 / design §5 item 20: the renderer draws them, they are dropped or queued for art mode, or a mix by type). Until it is made, the page draws none in live mode, drops them and counts them in `overlays_suppressed`, and the server waits until no overlay is showing (an operator start clears them) before going live; the owner decides before P6B stage 2. Rollback: `policy.overlay_source=legacy`.
2. **Content push** (2 d): `sm/channels` (model, claims and leases, on the P4 store), `sm/destinations/registry.py` (thin, `public_view()`) and `sm/api/screens.py` (§4.4). The publisher's `on_published` hook, after the atomic replace, bumps `screen.state.base` with `?v=<rev>`; this is the only P5 change to legacy publishing, and it also fixes two publishes within one second being missed today (1 s `Last-Modified` resolution). `img.decode()` before the through-black fade. The HEAD polls run only in degraded mode, plus a 30 s safety poll that counts `push_miss`. **The owner decides extension parity first** (D27 item 7; default: keep today's `.jpg/.JPG/.mp4` filter, so the existing `north-screen.png` and `south-screen.png` do not suddenly show). Rollback: `policy.content_source=poll`.
3. **Supervision and reload replacement** (1.5–2 d; D13): a generic, rate-limited, health-gated respawn in the kiosk launcher (`kiosk-wayland.sh` under sway already restarts a Chrome that exits, so this adds the rate limit and the health-gated launch; the same in `kiosk-loop.sh` for the X11 fallback); the KioskMonitor ladder over SSH keys instead of `sshpass` (§3.8); fix the comment at `admin_api.py:157`, which wrongly claims a respawn exists; `build_id` / `kiosk.reload` replace the 4-hourly reloads (`index.tsx:369-422`), applied at the next transition, at most once per 10 min and only if `/api/health` answers within 2 s; a health-gated reload on a heap threshold; the flagged 04:00 health-gated reload stays for 30 days. Remaining leak fixes: untracked `MediaDisplay` easing timers and the offscreen `<video>` per src. **Frozen-screen watchdog** (a general check under sway; on the X11 fallback it also covers the xserver #1924 risk, §8): when a TV's `kiosk.state` shows no new frames, restart that TV's Chrome, then `kiosk.service`. Optional, X11 fallback only: make `kiosk-loop.sh` restart only the affected TV's Chrome on a connector change (today it restarts both); re-check the equivalent under sway (the runtime unplug test passed). No kiosk agent. Rollback: the previous launcher script; a `policy` flag turns the old reload hours back on.
4. Vitest for the overlay and content paths of the reducer (ControlClient and the reducer core are tested from P4 step 6; the bundle budget is deferred to T7).
5. Rollout per step: V3, devtest → south for 48 h → north. Rollback is the step's `policy` flag, or `?ctl=legacy`, with no deploy and no box access; only step 3's launcher change rolls back on the box (previous script, one Chrome relaunch).

*Room-AV proposal pending AV-D1/AV-D6:* in step 2 (+1–2 d, contract K5), a `room` claim rank, room-claim policy, service-token writes and ChannelService running the design §2.5 handshake (see §5.2).

**Behaviour changes.** No more blanking 6×/day (D13). New publishes reach the screens in under a second. Overlays come back after a reload or a network blip (new, positive). A dead Chrome is respawned. Screens otherwise look identical, because the same page renders them.

**Exit criteria.**
- Publish-to-glass p95 under 1 s, measured from acks.
- Kiosk requests are ≤ 15/min per screen, down from about 200.
- Overlays survive a reconnect; screen state survives a core restart.
- JS heap stays flat for 72 h without the timed reloads.
- Killing one Chrome on south at a quiet hour (or on V4) gives a respawn; a hung renderer is recovered by the KioskMonitor ladder in under 2 min.

**Effort / risk / downtime.** 4–6 days (was 6–8; no parity port) / medium (the visible surface; per-screen flags roll back in seconds) / about 30 s per Chrome relaunch.

**Parallel with.** P3, P6A, T1, T2, T7 groundwork (`shared/http`).

---

### P6A: Stream service productionisation (stream_service: screen-machine-04)

*2026-10-14 ruling (§1): built directly in `stream_service/`, with no lab copy; its release gate shrinks to a crash check (04's call; 04 relayed the ruling).*

*Ownership (2026-10-14):* `stream_service/` is owned by screen-machine-04 (the renderer session, `docs/av/HANDOVER_RENDERER.md`) after screen-machine-2f's cut-over; 2f built it from bd's lab, and bd's lab (`experiments/stream-latency/`) stays bd's, on its own dev port. Production release of `sm-stream` is gated by 04 (cut-over, a reviewer, and a 30-minute 4K60 pacing run per GPU) through `STREAM_HOLD` in `deploy/sm-stream.env`.

**Goal.** Turn `experiments/stream-latency` into `stream_service/`, a supervised, LAN-bound deployable.

**Why now.** It depends only on P2 (a repo home and supervised releases) and P3 (the LAN path, and hardware decode for on-device tests), so it shortens the critical path by running in parallel.

**Steps:**
1. Copy, rather than move, the lab modules into `stream_service/{mux,encoders,sources,transports}`. The lab stays where it is. When using the lab's device measurements, note that the full `matrix-device.json` (up to 25 Mbit/s) fits on the powerline link; the 8 and 12 Mbit/s rows are the only valid ones when the box is on its Wi-Fi fallback. The CUDA rows need `.venv-lab`. Presented-frame results taken before the display fix (P3 step 5: per-TV X screens, then sway) understate what the N95 can show; re-measure under sway. Two 4K60 streams at the primary bitrate (about 60 Mbit/s) also fit on powerline.
2. Build the sessions API (§4.6) with idempotency keys, TTL and renew, source switch, admission (one per screen, NVENC cap, GPU per profile), supervisor and stall watchdog.
3. Ship `ws-fmp4-mse` first, primary profile **4K60 HEVC at about 30 Mbit/s per TV** (D11; second owner direction 2026-10-13), with a 2–3 frame client buffer rather than chasing <100 ms; stubs for `webcodecs-ws`, `webcodecs-mstg` and `ll-hls`. Requirements from the owner direction and the 2026-10-13 4K60 runs:
   - **all live compositing happens in the renderer:** every effect and transition the TV shows while live is rendered on the workstation GPU into the stream (the page adds nothing over the video);
   - **matching cuts** (design §2.5, step 10): the anchor, diverge, re-anchor and converge calls with hold reporting (`hold_pts`, §4.6); the renderer draws the entry anchor and the exit target exactly as the page composites them (native 3840×2160 with no scaling, the same crop and matte, colour primaries, transfer and matrix tags and range matching how Chrome draws the still, and a high-quality anchor GOP; spike §10) and holds them until told to move on; mask parity with the page (one shared formula, golden tests against page screenshots);
   - **even frame pacing:** the renderer delivers frames evenly at 60 fps (16.7 ms each), and a late render repeats the previous frame in its slot rather than shifting the timeline. The lab generator misses that budget about 2% of the time (render plus a 12 MB pipe write per frame inside WSL), which, with the lab player's catch-up above 1.0×, gave 6–7% dropped frames on glass. Count late source frames per session and expose them in `/api/state` (§4.6).

   Requirements confirmed by bd's adversarial review:
   - drop-to-keyframe driven by the transport's **real** backlog (aiohttp `transport.get_write_buffer_size()`, small `SO_SNDBUF`), not an app-level deque, or megabytes queue in kernel and aiohttp buffers;
   - the MSE driver jumps timeline gaps after drops (Chrome otherwise stalls forever at a gap), never evicts the GOP being played, and recovers from fatal media errors by re-creating the MediaSource;
   - WebCodecs drivers reconfigure only at the next keyframe after an error and apply `decodeQueueSize` backpressure;
   - the init segment uses `empty_moov` (mvhd duration 0, no mehd), with per-frame fragments flushed explicitly;
   - client/server clock sync fits offset and skew (do not assume the hosts agree). The lab already does this (epochs, `clock.jsonl`; see its README); known limit: the offset-only fallback drifts during the first ~2 s.
4. **Production hooks for the display page** (replaces the earlier `/player` page and iframe contract): a session `ready` signal (first IDR muxed), per-client backlog and drop-to-key stats in `/api/state`, `ttl_s ≥ 120` (longer than a core restart), a clean new init segment on encoder restart, and the handshake calls of step 3. `/player` (`static/client.html`) stays a lab page only.
5. Tests: unit (fake encoder), contract (stream-api schema), SPS parse on every init segment.
6. `sm-stream`: LAN-bound on :8702 (`docs/PORTS.md`), already a screen session via `scripts/ctl` (`docs/OPS_CONTROL.md`); give it the same restart-on-crash behaviour and its own requirements, and extend `deploy.sh` to cover it.
7. A 24 h staging soak (on V4 if bought, otherwise V1 in an owner-agreed window, §3.9; the third Chrome via `lab-chrome.sh` only on the X11 fallback, where `kiosk-loop.sh`'s chrome `pkill` on HDMI changes and its window restacking apply, §3.5), then the latency/codec/transport table for D10 and D11.

*Room-AV proposal pending AV-D1/AV-D6:* re-scope to contract K6 (bd's; v0 due Thu 22 Oct): two sessions pinned to GPUs, `source.kind=show`, renderer-owned transitions and output limiter, a service token, no stamping in production; LL-HLS and WebRTC dropped for v1 (see §5.2).

**Exit criteria.**
- The service survives an encoder kill (`failed` → `running`, with a clean new init), and a TTL expiry ends the session.
- `ready` and the per-client backlog stats are exposed.
- **Even pacing at 4K60:** live 4K60 HEVC from the production renderer, on both TVs with a player that never runs above 1.0×, meets the display-fix bar (P3 step 5); late source frames are reported per session.
- Stage stats match the lab.
- RSS stays flat over 24 h, with SourceBuffer trimming verified.
- The owner has the decision table.

**Effort / risk / downtime.** 6–9 days (bd; the design's estimate for P6A′: was 4–7, and 5–8 with the `/player` page; it grew with the anchor handshake, mask parity and even pacing) / medium-high (hardware, codec, 4K60 pacing, Wi-Fi) / none.

**Parallel with.** P3, P4, P4b, P5.

---

### P6B: Live integration (the feature; design steps 9–10)

**Goal.** The operator and the scheduler can put any kiosk screen live with an owner-chosen profile (primary 4K60 HEVC, D11), played inline in the display page as one full-viewport `<video>` with nothing over it, falling back to the still by a hard cut with an alert.

**Former BLOCKER, cleared on the kiosk (2026-10-13).** The X setup presented only about 60 frames/s in total across both TVs; per-TV X screens fixed 4K30 (0.28% / 0.24% dropped), and the move to Wayland (sway) made 4K60 work: a pre-made HEVC 4K60 30 Mbit/s file on both TVs dropped 0.03% with rAF at 60.0 and 0 late frames. **Remaining before stage 2:** live 4K60 still drops 6–7% from workstation-side pacing, so even renderer pacing (P6A step 3) and a player that never runs above 1.0× (stage 1 step 1); then the 30-minute run against **the display-fix bar in P3 step 5** (rAF ≥ ~59 fps per window during live playback in both windows; ≥ 99.9% presented with 0 late renderer frames; ≤ 1 stall per 10 min; design §6 item 1); and the owner's decision on overlays during live (D27 / design §5 item 20; §3.5). Also: the Chrome hold (D7) before stage 1. Production-Chrome VA-API playback is proven.

**Stage 1, LiveLayer on devtest** (design step 9; 4–5 d):
1. MseLivePlayer, ported from the lab `MsePlayer` (`static/client.js`): low-delay init, gap jump, evict to a keyframe, and live-edge resync by a hard jump. **`playbackRate` is never set above 1.0** (this replaces the lab's catch-up rate control, which speeds up and so forces dropped frames at 60 fps on a 60 Hz panel).
2. The art/live layer split (live warms up under ArtStack) and the design §2.5 handshakes: the anchor ack and the entry cut on the first presented frame, the exit cut on `hold_pts` (or the 15 s exit timeout), the stall hard cut and the re-anchor, with no crossfade into or out of live; clean unmount (§3.5 PlayerHost); liveMachine in the page. In live mode the page draws nothing over the `<video>`.
3. A LiveController MVP against the lab `server.py`, with a `testPattern` driver that can draw a given still and mask, hold it, diverge and converge. It holds `mask.set`, overlays and base changes during the entry, re-anchor and exit windows, as in stage 2 step 1.
4. An automated cut check (CDP screencast and screenshots against the decoded anchor and hold frames) on planned still → live → still cuts and the re-anchor: no black or mismatched frame; and on a forced stall fallback: a single-frame hard cut to the still, with no black frame and no fade. **On-glass anchor-match check** (the acceptance test for "indistinguishable", spike §10): on the kiosk itself, capture the art still on glass and the stream's held anchor frame with `grim` under sway and compare them: identical geometry (native 3840×2160, no scaling, the same crop and matte) and matching colour primaries, transfer and matrix tags and range; 4:2:0 chroma softening is accepted. Compare, the same way, the exit hold frame against the target still and the re-anchor frame against the still on glass; pass when the two sides of each planned cut differ only by MP4 artefacts, with the threshold set from the measured artefact level. Run every case at full brightness and at a dimmed mask (brightness ≈ 0.3, warm_alpha ≈ 0.05), so the renderer's reproduction of the mask is tested too (design §6 item 11). `grim` is not installed (notes §7); installing it needs the owner's OK.
5. A fault-injection TCP proxy in front of devtest's attach URL: latency, loss, half-open, 15 Mbit/s cap.
6. Check that live mode draws nothing over the video (no overlay, controls/info panel, mask, warm, HUD or CSS-effect element over the `<video>`) and plays at full intensity (after the renderer's ramp from the masked anchor). This replaces the former `mix-blend-mode` cost test (design §6 item 3c) and the mask-during-live decision: both are out under the second owner direction, which resolves D27 item 9.

Test on V1 (a production Chrome over CDP, §3.9) or V4 only; production screens keep `policy.live_enabled=false`. Rollback: the flag.

**Stage 2, production live** (design step 10; owner 4–6 d, with P6A from bd):
1. `sm/streaming/client.py` and `reconcile.py` (§3.5): reconcile every 5 s with `adopting`; `ChannelService.go_live` (idempotent), leases, `clear` and `release_owner`; **handshake holds**: the server holds `mask.set`, overlays and base changes for the screen from `starting` to the entry ack, from `recovered` to the re-anchor cut ack, and from `leaving` to the exit ack, and a publish in a hold updates only the server's base (design §2.5–2.6); **ack-gated delete**: the page cuts on the renderer's held exit frame (`hold_pts`); if that is not presented within 15 s of `leaving`, it hard-cuts anyway (unmatched, `reason: exit-timeout`); the server deletes after the base ack, or after 20 s (design §2.5).
2. Display page live driver: **inline `wsFmp4Mse` only**, selected by caps. A **rung ladder per link group**, end-then-start through the exit and entry handshakes (design step 10): 4K60 HEVC ~30 Mbit/s → 4K30 HEVC ~18 Mbit/s (proven under X11) → 4K30 HEVC ~10 Mbit/s → 1080p30 ~5 Mbit/s → still (D11; on Wi-Fi, D27 item 10's default: 1080p, then the still). A screen on the X11 fallback starts at 4K30. Two 4K60 streams (~60 Mbit/s) fit the powerline link but not the Wi-Fi fallback (15–20 Mbit/s). Step down on ≥ 2 backlog drop-to-key events in 10 s or any page stall; step up only after 5 min clean, with backoff.
3. Scheduler `channel` action: a handler in `scheduler_handlers.py`, a branch in the `routes/scheduler.py` dispatch, an entry in `schedule.schema.json.j2`, a 3 s budget, and `release_owner` on stop/unload. This knowingly grows the if/elif chain; T6 moves it into the action registry.
4. Operator `src/features/screens/ChannelPanel` (mode, owner, rev, desired vs glass, player and stream stats, go-live/stop) on `shared/http` and `/v2/operator`.
5. Siren `stream.*`, `kiosk.*` and `live.client_failed`; `/api/health` aggregation; a runbook.

Rollout: devtest via V1 or V4 → south in an owner window → north. Rollback: `policy.live_enabled=false`, or clear the claim.

*Room-AV proposal pending AV-D1/AV-D6:* stage 2's first consumer is the room claim, at a minimum rung of 4K30 (AV-D12); the T2 subset (DevicesPort over IP Control, and fixed wake and Art Mode actions) lands before stage 2 (see §5.2).

**Behaviour changes.** A new feature, opt-in per screen. While a screen is live it shows only the stream, at full intensity: no mask, warm tint, page overlays or info panel (second owner direction).

**Exit criteria.**
- The display-fix bar (P3 step 5) holds with the chosen profile live in both windows.
- The chosen profile runs 24 h on both TVs with a flat heap.
- Glass-to-glass latency meets D10, measured with bd's harness.
- Planned still ↔ live switches are hard cuts between matching frames: no black or mismatched frame at the cut (automated screencast check), and the on-glass `grim` anchor-match check (stage 1 step 4) passes at full brightness and at a dimmed mask; a stall fallback is a single-frame hard cut to the still, with no black frame.
- In live mode nothing is drawn over the video, and the picture is full intensity.
- The player never runs above 1.0× (`kiosk.state` `live.rate` ≤ 1.0 throughout); live-edge resyncs are hard jumps, counted in `live.edge_jumps`.
- Killing `sm-stream` gives a fallback to the still within 5 s plus an alert.
- **A core restart does not interrupt live.**
- A duplicate `go_live` opens no second session.
- **Powerline-pull drill** (owner window): pulling the powerline adapter during live drops to a lower rung or the still by hard cuts, without a black frame, and recovers.
- No media bytes travel on :8765 or through waitress.

**Effort / risk / downtime.** 8–11 days (stage 1 4–5, stage 2 4–6) / high (the first end-to-end run, 4K60 pacing, the anchor handshake and the open overlay decision) / none for existing features.

**Parallel with.** T-tracks.

---

### Tracks (pausable; after the feature or in gaps)

**T1: Hygiene and dead code (2–4 d; after P1, frontend items coordinated with P5).**
- Steps: the deletions in Appendix A, each re-verified with the reachability script. Then the dependency diet; fixing or deleting `build/test-display.html` (send the token and check `res.ok`); the `start.sh` default; identifying who runs `check-status-compact.sh` every minute and the client that fails the WS handshake every ~5 s.
- Behaviour changes: legacy `/display` and the test/sim UIs go only under D18. For image-display, the `FullscreenDialog` JSX and the `useFullscreenDialog`/`useFullscreen` calls are removed from `ImageDisplay.tsx` before any files.
- Exit: the tsc and eslint baselines go down, and nothing that renders today disappears without a decision.

**T2: Confirmed-bug batch (3–5 d; owner-approved behaviour changes; each with a characterisation test and a one-line ADR, D19).**
- **C7:** initial actions run once, not twice. Never run them in the HTTP or Alexa thread. Loading onto a paused destination with a live loop must not mutate state and then return 500.
- **C8:** single history append. Fix the `:953` filter to match on `unique_id`, and run an idempotent importer that dedupes the already-persisted state files (dry-run report first). Loading must also tolerate duplicates.
- Paused state restoring as stopped. GET endpoints creating state files. The exported-vars NameError. `extract_json` recursion. `reindex` dropping dict entries. `list_items` crashing on dict sequences. Thumbnail on `bucketer.copy`.
- RunPod `TIMED_OUT` handling and cancel-on-timeout/error (stops orphaned billing). `cancel_all` cancels only non-terminal jobs.
- **Samsung TV control (`routes/samsung_utils.py`), flagged by bd and confirmed in the code** (also tracked in `docs/KIOSK_INFRASTRUCTURE_NOTES.md` §7). `device_sleep` (`:149`) checks `power_state in ["on","standby"]` before `art_mode`. A Frame in Art Mode reports power "on", so a `sleep` event (wired into both screens' schedules) sends KEY_POWER and flips an already-sleeping TV to the HDMI input; the `art_mode == "on"` branch is unreachable in that case. Check Art Mode first. Also `get_status` (`:565`) maps *any* art-channel exception, timeouts included, to `art_mode="off"`, which makes the bug above more likely; return `None`/unknown on error and have callers treat it as "don't toggle". IP Control (§3.5) reads power and Art Mode reliably, so the state check can come from DevicesPort (read-only) instead of the art channel; the wake and Art Mode actions keep the fixed samsungtvws code (check Art Mode first; if unknown, don't toggle; verify, then retry), or use IP Control setters only if the owner approves testing them. *Room-AV proposal pending AV-D1/AV-D6:* pull this subset (1–1.5 d) forward to before P6B stage 2 (see §5.2).
- Dormant features per D17.

**T3: Platform consolidation (4–6 d).**
- stdlib logging platform (`sm/logging.py`, with `utils/logger.py` as a wrapper for its 27 importers).
- The full `DestinationRegistry` as the single loader of the destinations JSON.
- Split `routes/utils.py`.
- **Invert alerts → scheduler:** `utils/alerts/health.py:55,94` stops importing `routes.scheduler`; the scheduler pushes heartbeats into a registry in `alerts.health`.
- Explicit settings paths replace `findfile` for known files.
- Pure `compute_mask`: no `exec('config.py')`, the filter advances only on sensor ingest, overrides are persisted. *Pulled forward into P4b; T3 only tidies what remains.*
- Exit: the cycle ratchet has dropped and the alerts package sits outside the cycle.

**T4: Storage and publishing (5–7 d).**
- `BucketStore` behind `load_meta`/`save_meta` at all 51 call sites.
- `PublishService` with explicit source types (replacing the substring heuristics at `publisher.py:204/212`), one published record, and one validate-then-move undo/redo replacing the 4 copies.
- `bucket.changed` events.
- A hygiene importer with a dry-run report (6.5k orphan `*.resized.jpg`, 308 orphan `.ref` files at 445 MB, test pollution), plus a rewrite of `purge_recent.py` per the D20 retention decision.
- Exit: 50 parallel mutations lose nothing and always leave valid JSON.

**T5: Jobs and generation (6–8 d).**
- SQLite `JobRegistry` with a one-time import of non-terminal jobs.
- The RunPod adapter (§4.7).
- A bounded executor with admission and spend caps.
- `/api/jobs` returning 202, with the frontend jobs store.
- Alexa: submit plus an enforced 7 s triage timeout plus signature verification.
- A shared OpenAI client with timeouts.
- **Keep or replace the per-destination `alerts_health` tick** that `generate.py:727-733` sends during polling, ideally driven by job progress, or Deadman and sched-wedged alerting break.
- Exit: no HTTP request is held longer than 30 s, and no orphaned RunPod jobs.

**T6: Scheduler engine v2 (12–18 d).**
- Goldens: the 13 scripts plus the sanitised live schedules (north-screen has 11 event triggers).
- `sm/scheduler`: models, action registry and `ActionResult`, a single-writer actor per destination with debounced persistence, a lifecycle FSM (keeping the legacy `/pause` toggle), event semantics v2, a single asyncio runner woken by timers and events, `Pending(job_id)` via T5, and **single-pass sandboxed templating** as an owner-approved change.
- **Shadow mode for 3–7 days:** v2 runs with no-op ports against mirrored schedules, events and the clock, and its resolved blocks and timings are diffed against the legacy log.
- A per-destination `engine: v1|v2` flag; `export-legacy` writes v1 JSON back for rollback.
- Cutover order: devtest → cloud/the-beast → north/south.
- `HEARTBEAT_STALE_S` goes from 600 to 60.
- Delete `routes/scheduler*.py` after 14 days on v2.

**T7: Frontend data layer and decomposition (12–18 d, continuous).**
- `shared/http` plus domain API modules; `api.ts` becomes a facade and is then deleted. **First part done 2026-10-14** (screen-machine-3d; staged, not deployed): `src/shared/http/client.ts` plus `src/shared/api/<domain>.ts` (admin, audio, buckets, events, files, generation, light, logs, publish, scheduler, vars; import via `@/shared/api`), with mock mode and 22 dead methods removed. `src/utils/api.ts` is now a ~95-line deprecated facade over the old `Api` singleton, and its only importers are four `DisplayPage` files. A differential harness matched the facade to the old `Api` on 1,381 prod-config cases. Behaviour changes: real requests instead of a faked backend on a 'lovable' hostname or a non-`http` `VITE_API_URL` (prod unaffected), and some `console.error` lines are gone (the throws and toasts are unchanged). Still to do: move the four `DisplayPage` importers to `@/shared/api` with the P5 display work, then delete the facade.
- Known bugs found during the split and left as they were: the `jpg_from_mp4` path in `image-utils.ts` 404s; BucketGridView's upload-by-URL is always rejected by the backend; BucketGridView reads `VITE_API_URL` raw, with no fallback.
- One QueryClient, React.lazy routes, a `queryBridge` from realtime events.
- `Index.tsx` moves to RealtimeClient and the jobs store; `parseJobMessage` and the HTML scraping are deleted.
- `CustomEvent`s and window globals are replaced.
- Decompose BucketGridView (fixing the DnD `drop-section-` vs `group-` id mismatch), LoopeModal (rules-of-hooks), `scheduler.tsx` (snapshot plus push), SchemaEditModal and RecentView.
- Split the display page into its own lazy chunk and enforce the ≤ 300 KB gzip budget for it (deferred from §3.3).
- Fix "use as prompt".

**T8: Overlay modernisation and decommission (4–6 d).**
- Native overlay components per kind, plus legacy HTML in a sandboxed iframe, **only after owner visual sign-off** (D23).
- Remove the WS relay once the per-shape log shows no consumers. Remove the legacy `/` viewer path, the raw lux rebroadcast (`overlay_ws_server.py:171-177`), `connection_registry.py`, the custom keepalives and the legacy job-progress maps once the RunPod worker, light relay and Android app are on typed ingest (design step 11).
- Drop kiosk mask polling to a 60 s safety rate, and remove the legacy content pollers 7 days after zero `push_miss`.
- Remove `light-relay.service` from the mini-PC deadman's default (`deadman.py:290`) once `sensor.stale` has run for a week.
- `routes/` and `sm/legacy/` are empty, import-linter has no exceptions, and the `config.py` shim and `overlay_ws_server.py` are deleted.
- Re-evaluate the ASGI triggers.

### 5.2 Room-AV programme: proposed scope inside existing phases (pending AV-D1/AV-D6)

`docs/AV_PROGRAMME.md` (screen-machine-10, 13 Oct late evening; §2.1–2.2) makes the room (Art-Net virtual fixtures, Hue, Sonos, MaestroDMX, a mode supervisor and a phone UI) the platform, with screen-machine as its screens subsystem. It asks this plan to keep its order (AV-D6 recommends (a)) and to add scope inside existing phases. **Everything in this subsection is a Room-AV proposal pending the owner's AV-D1 and AV-D6 (D28); none of it is adopted.** The one exception is **AV-D2, which the owner has decided:** `room_service` runs in WSL on the workstation; Art-Net reaches it through a Windows-side UDP relay that the owner installs as a separate Windows scheduled task; WSL stays on NAT; no room box and no hardware purchase. That resolves D22.

| Phase | Room-AV proposal (pending AV-D1/AV-D6) |
|---|---|
| P1 | The test guard also blocks UDP `sendto`, so no test can emit Art-Net, OSC or Hue to real lights; device fakes. Optional trim: the `resolve_schedule` goldens and the schedule-schema checks move to T6 (the `send_overlay` and `publish_to_destination` goldens stay, because P2's path edits touch those modules) |
| P2 | The Siren subject prefix and ping URL from env; Maestro exports in the nightly backup; the Windows boot task also starts `room_service`. *`sm-room` now runs as a screen session via `scripts/ctl` (`docs/OPS_CONTROL.md`)* |
| P3 | Split into *front* (LAN origin, `?kiosk=`, the Chrome hold) and *reach* (close the WAN forwards and narrow ngrok **before any claim API ships**, including 8765 after D5). Add DHCP reservations at each device's current address, the port register (D16), guest isolation and a UDP-loss test on the powerline. The room UI on :8700 needs a LAN-scoped TCP portproxy rule, created by Spectre (`docs/PORTS.md`) |
| P4b | A **room hold**: while the room sets `show_active`, SensorService holds brightness *and* warm values under a token-gated hold with a TTL the room renews, and the room clears it at startup. An **output slew limit**: no brightness reversal of ≥ 0.1 within 1 s; overrides ramp over ≥ 1 s, at most one per 5 s per screen. A **token on `maskon`, `maskoff` and `brightness-override`** before P4b ships (also recommended by this plan, §3.7: fast push plus open overrides is a LAN strobe vector; a behaviour change, so it needs the owner's yes). A loop-gain check with Hue in view. FYI: today `brightness-override` forces `warm_alpha` 0 (`routes/display.py:124-136`) |
| P5 step 2 (+1–2 d) | Contract K5: a `room` claim rank (base < scheduler < api/alexa < room < operator, a change to D12); room-claim policy `{reloads: hold, device_power: lock, overlays: drop, min_rung: hevc-2160p30}`; service-token writes; ChannelService runs the design §2.5 handshake. screen-machine-10 asked for a K5 draft by Fri 23 Oct; that needs the owner's approval of AV-D1 and AV-D6 first |
| P6A (bd) | Re-scope per contract K6 (bd's; v0 due Thu 22 Oct): two sessions pinned to GPUs, `source.kind=show`, anchor/diverge/re-anchor/converge, renderer-owned transitions and output limiter, even 60 fps pacing (GPU-resident to NVENC), a service token, no stamping in production; LL-HLS and WebRTC dropped for v1 |
| P6B | Stage 1 = design step 9 (4–5 d, as now). Stage 2: the room claim is its first consumer, at a minimum rung of 4K30 (AV-D12) |
| T2 subset (1–1.5 d), before P6B stage 2 | DevicesPort over IP Control (§3.5). Wake and Art Mode actions use the fixed samsungtvws code (check Art Mode first; if unknown, don't toggle; verify, then retry), or IP Control setters if the owner approves testing them |

**Principles and decisions (proposals, pending AV-D1/AV-D6):**
- §1/§7 "no microservices beyond the stream service" becomes "stream service + room service" (still no broker, no Docker).
- Principle 5 gains a **show plane** (latest value, lossy, fixed rate, freshness-gated) and a **device-command plane** (rate-limited; outcomes ok, failed or unconfirmed; desired kept separate from observed).
- Principle 6 becomes: a stream failure is a hard cut to the still; stale show input is handled inside the stream; commanded darkness is content, not failure.
- Principle 7: room control is authenticated and screen claims carry a service token; the operator UI is unchanged until P3.
- Principle 8 extends single ownership to devices (Hue handed over between house and room; TVs only through DevicesPort; stream sessions only through reconcile; Maestro only through the room).
- New principles: **physical safety** (safe by default, no unauthenticated effects, a limiter at every light output, shows lapse to Off); the lighting heads work without the workstation (Maestro is autonomous); **TV picture settings are never written by software** (drift is only reported, `tv.settings_drift`; AV §2.2 treats a one-off Game Mode change made by the owner, such as setting it to Off, as configuration, not a runtime switch, and re-applying the baseline after a firmware update is the same); **power and Art Mode↔HDMI are changed only by DevicesPort**, only outside a cut (the wake completes ≥ 60 s before the anchor handshake) and never while a claim is live.
- D10/D11: MSE + HEVC confirmed; 4K60 once the renderer paces evenly, 4K30 the fallback rung. D12: the room rank. D13: timed reloads never fire during a claim. D16 becomes the full port register (AV programme §3.5). D21 (NVIDIA ≥ 570) becomes relevant for zero-copy CUDA→NVENC (try PyNvVideoCodec first; AV-D9). D22: resolved by AV-D2 (decided).

**Effort.** This plan's path to production live is ≈30–41 owner-days with P3 alongside. If P3 *reach* must gate the claim API and the room's K5 and DevicesPort work (≈2–3.5 d) is added, it becomes ≈32–47; screen-machine-10 computes ≈34.5–47 to its milestone M6 (screens as fixtures in production), counting P3 in full.

**Ownership.** `docs/AV_PROGRAMME.md` and `docs/av/` belong to screen-machine-10; this plan cross-references them and does not edit them. In this checkout, commit only in pathspec form (`git commit -m … -- <paths>`): a `kiosk.service` → `kiosk-x11.service` rename is already staged in the index, and a plain `git add` plus `git commit` would sweep it in.

---

## 6. Owner decisions

| # | Decision | Options | Recommendation | Needed by |
|---|---|---|---|---|
| D1 | Prod layout and supervision | `/srv/screen-machine/{releases,current,state,config,output,logs}` release dir · keep running from the dev tree; supervision: **settled by the owner 2026-10-14** — every server on PC22 runs in a named GNU screen session via its project script, managed by Spectre (no systemd units or linger for app services) | Release dir plus the owner's screen-session pattern: `start.sh` runs sm-web from `/srv/screen-machine/current` with a restart loop, pidfile and RSS alert (§3.8). `sm-room` and `sm-stream` already follow it via `scripts/ctl` (`docs/OPS_CONTROL.md`). Replace the two stale Windows boot tasks first (§3.8; owner's OK) | P2 |
| D2 | Repo privacy and leaked material | untrack token files / UniFi backup / support bundle / `' - Copy.json'` in HEAD; make the repo private; re-pair TVs; history rewrite | Untrack plus private now; re-pair is cheap; reconsider the rewrite because of the UniFi backup | P2 |
| D3 | Hotfix side effects | accept the `/test-overlay` 401 (fix or delete `build/test-display.html`; the root copy was archived on 2026-10-14); confirm the publisher source restriction against every caller | Accept; fix the page in T1 | P0 |
| D4 | Close WAN forwards; remote access | close 5000/8000/8080/2222/8081/3001 now (8081/3001 are exlib's, via Spectre) and 8765 after D5; VPN for remote operator and Android · keep forwards | Close in the §3.7 order; Tailscale | P3 |
| D5 | RunPod progress ingress | (a) worker uses `progress_update`, backend polls (no inbound port) · (b) per-job HMAC → `/ingest/runpod` via ingress · (c) keep 8765 as a token-only worker role | (a) if the worker can be edited, otherwise (b) | P4 |
| D6 | Alexa | keep, with signature verification, exposed only via the ingress listener · retire | Keep if used | P3 (exposure) / T5 (verification) |
| D7 | Kiosk VA-API | apply bd's package fix plus flags · stay on software decode | **Enabled 2026-10-11** (owner-authorised): packages, render group and the Chrome flag in production `kiosk-loop.sh` (since 2026-10-13 also in `kiosk-wayland.sh` under sway, with the zero-copy path); **HEVC hardware playback proven in the production Chromes 2026-10-13** (4K30 under X11, 4K60 under sway). **Pending:** hold Chrome at 136 (`apt-mark hold google-chrome-stable`; apt would otherwise jump to 155). Also listed in the display-layer design's decisions (D27, item 16); do it before the devtest live step (P6B stage 1) | P3 |
| D8 | Wired Ethernet for the mini-PC | Ethernet (powerline) · stay on Wi-Fi | **Done 2026-10-13:** powerline primary (192.168.1.10; ~170–190 Mbit/s, loaded p99 ~41 ms), Wi-Fi .92 as fallback; 24 h soak decides final acceptance; Ethernet path watchdog still to build | P3 |
| D9 | Secure context | `--unsafely-treat-insecure-origin-as-secure=http://sm.lan:5000` (the page origin only; inline MSE does not need the stream service's :8702 to be secure) · LAN TLS (Caddy internal CA) · ssh -R | Chrome flag now; LAN TLS only if non-kiosk browsers need WebCodecs (that is also an ASGI trigger) | P3 |
| D10 | Latency class and first transport | interactive <100 ms (ws-fmp4-mse / webcodecs) · ambient 1–3 s (LL-HLS via MediaMTX) · WHEP (needs UDP) | Owner priority is quality over latency: ws-fmp4-mse first with a 2–3 frame buffer; WebCodecs only if bd's numbers justify it; LL-HLS only for purely ambient use | P6A start |
| D11 | Codec and profile | 4K60 HEVC · 4K30 HEVC/AV1 · H.264 + SPS rewrite · 1080p | **Primary: 4K60 HEVC at about 30 Mbit/s per TV**, per the second owner direction (2026-10-13: "stream mp4 4K at 60fps"). It works on the kiosk since the move to Wayland (sway) on 2026-10-13: a pre-made 4K60 HEVC 30 Mbit/s file on both TVs dropped 0.03% with rAF 60.0 and 0 late frames (§3.5). Live 4K60 waits on even renderer pacing (P6A) and a player that never runs above 1.0× (P6B); the 30-minute run (design §6 item 1) confirms it. Two streams (~60 Mbit/s) fit the powerline link, not the Wi-Fi fallback. **Degrade ladder** (design step 10): 4K30 HEVC ~18 Mbit/s → 4K30 HEVC ~10 Mbit/s → 1080p30 ~5 Mbit/s (H.264 + SPS rewrite, or HEVC) → still, on trouble; on the Wi-Fi fallback the default is 1080p, then the still (D27 item 10). A screen on the X11 fallback starts at 4K30. AV1 when the encoder path is ready (4060 only). Under X11 (now the disabled fallback) 4K60 dropped 74–82% and only 4K30 works (about 0.25% dropped). Room-AV proposal pending AV-D1/AV-D6: MSE + HEVC confirmed (with D10); 4K60 once the renderer paces evenly, 4K30 the fallback rung; room claims at a minimum rung of 4K30 (see §5.2) | P6A |
| D12 | Channel arbitration | live wins and a new publish becomes the fallback · publish preempts live; priority operator > api/alexa > scheduler > base | Live wins until its lease or a stop; that priority order. Room-AV proposal pending AV-D1/AV-D6: base < scheduler < api/alexa < room < operator (see §5.2) | P5 |
| D13 | Reload policy and kiosk recovery | keep the 4-hourly reloads · generic respawn in the kiosk launcher (`kiosk-wayland.sh` under sway, `kiosk-loop.sh` on the X11 fallback; rate-limited, health-gated launch) + KioskMonitor ladder over SSH keys (`restart-screen` ≤ 3/h → `restart-kiosk` → alert only) + `build_id`-driven `kiosk.reload` + 04:00 health-gated safety reload for 30 days; **no kiosk agent** on the mini-PC (owner direction 2026-10-13) | The latter, run automatically and rate-limited (D27 item 6), then drop the safety reload if heap stays flat. Room-AV proposal pending AV-D1/AV-D6: timed reloads never fire during a claim (see §5.2) | P5 |
| D14 | Mask when the page loses the server (art mode; live has no mask) | today black (accidental) · hold last, then full brightness · hold last 10 min, then a solar schedule the server precomputes and sends in advance | **Recommended default, pending the owner's yes (D27 item 4):** hold the last mask 10 min, then follow `policy.offline.schedule`. Never black, never full brightness at night. Ship hold-last into today's `useMask` at P0.5, holding until the server answers again; the 10-min limit and the offline schedule start at P4b, when `policy.offline.schedule` exists | P0.5 |
| D15 | Live presentation | inline · iframe · page | **Inline only** (owner direction 2026-10-13: all control machinery in one layer, no re-pointing to other pages). The iframe, page mode, postMessage contract and `/player?return=` are dropped; experimental transports ship as lazy inline driver chunks, tried on devtest via V1 or V4. Per the second owner direction, in live mode the page shows only the full-viewport `<video>`, with nothing over it, and switches by a hard cut between matching frames | P6B |
| D16 | Stream-service port | (superseded) prod :8081 / lab :3001 | **Superseded 2026-10-14 by the port register `docs/PORTS.md`** (owned by screen-machine-ca): stream_service **:8702**, room_service **:8700**, show bus :8701 (reserved, localhost only); all LAN only, never WAN; Spectre creates their rules at production. :8081 and :3001 are **exlib's** (Spectre `exlib-mobile` Metro and `exlib-server`, created 2026-02-07; firewall rules `la-exlib-mobile-8081`, `la-exlib-server-3001`), not lab rules. bd's lab must not run on :8081 while exlib-mobile runs (prefer 8082–8099). Room-AV's "full port register" proposal is met by `docs/PORTS.md` | P6A |
| D17 | Dormant features revived by NameError fixes | Samsung Frame photo sync (would start uploading to the TVs), Blank display, EXIF extraction: fix / remove / park | Park Samsung sync (remove `device_sync` unless wanted); fix Blank and EXIF only if you want them. Note that both screens' schedules still run `device-media-sync` at 04:10 every night: it connects to both TVs and then crashes on the `log_debug` NameError, so parking it should also remove that trigger | T2 |
| D18 | Legacy `/display` (renders today), test/sim UIs, `/schema-dynamic-form` | delete · redirect `/display` → `/` · keep | Redirect `/display`; delete the test/sim UIs (the simulator would run real handlers) | T1 |
| D19 | Scheduler semantic fixes | C7 double initial actions, C8 history, paused restore, single-pass templating, event admission | Approve each with goldens and an ADR | T2 / T6 |
| D20 | SQLite for jobs; `_recent` retention | SQLite (WAL) · JSONL; retention N days | SQLite; 30 days | T4 / T5 |
| D21 | NVIDIA driver ≥570 | upgrade (PyAV, on-demand IDR) · stay on 566 | Defer; 1 s GOP is enough. Room-AV proposal pending AV-D1/AV-D6: relevant for zero-copy CUDA→NVENC and even 4K60 pacing; try PyNvVideoCodec first, upgrade only if needed (AV-D9) (see §5.2) | after P6B |
| D22 | WSL networking | netsh portproxy · mirrored networking | **Resolved 2026-10-13 by AV-D2 (owner):** WSL stays on NAT with netsh portproxy; Art-Net (inbound UDP) reaches `room_service` in WSL through a Windows-side UDP relay that the owner installs as a separate Windows scheduled task; no room box, no hardware purchase. Revisit only if WHEP/UDP media is chosen (D10) (see §5.2) | resolved |
| D23 | Overlay modernisation | keep legacy HTML · sandboxed iframe · native components | Native for system overlays, sandboxed iframe for owner HTML, after parity sign-off | T8 |
| D24 | TV picture mode for live content | Game Mode on/off (the One Connect wireless path is fixed: about +35–45 ms even in Game Mode) | **Settled 2026-10-13: Game Mode is never used, live or art** (owner, after the IP Control setter test showed Samsung's Game Bar overlay and flicker: "we don't use game mode"; spike §10; design D-C). No transition may change picture mode, input or any TV picture setting, and no per-mode switching is built. Both TVs read Game Mode Auto, which is inactive (it never engages under sway: no ALLM, and wlroots hard-codes the connector content type to Graphics); setting them to Off is the owner's call, timed with bd. All the sway 4K60 results were measured in this state, so 4K60 does not depend on Game Mode (toggling it On and back changed neither EDID nor outputs; notes §3), and the earlier open test is closed (design §5 item 22, moot). This matches the 2026-10-11 recommendation (Game Mode OFF, non-PC input label and the rest of `docs/FRAME_PRO_SETTINGS_2026-10.md`). With the box path Game Mode would save an estimated ~55–60 ms (not the ~25–35 ms first written here; spike §10) and forces the Game picture mode; the owner judged it not worth it. The 4:4:4 route (PC label plus Game Mode) is out. Picture mode: the TVs run Filmmaker Mode, the owner's current choice (the list recommends Movie) | settled |
| D25 | Light response | Tier 1 server push only · add a Tier 2 local loop on the mini-PC | **Tier 1 only** (owner direction 2026-10-13: the mini-PC stays dumb and the sensor is a remote input). Filter, staleness and offline behaviour per §3.5b; tuning defaults are D27 item 11, tunable in `config/light.json`. Art mode only: live is full intensity (second owner direction). Settled in design §4/§2.6: no ambient influence on the live renderer between handshakes; the renderer gets the mask only at each matched cut | P4b |
| D26 | Commit the P0 hotfix | commit by explicit path and tag · keep uncommitted | **Done 2026-10-11:** `2dad861b`, tag `prod-hotfix-2026-10-11` | done |
| D27 | Display control layer decisions | the 22 items in `docs/DISPLAY_CONTROL_LAYER_DESIGN_2026-10.md` §5, each with a recommended default except items 20 and 22 (one page; dumb host; evolve in place; D14; P0.5 visible bits; recovery; extension parity; still-to-still transition; mask during live (item 9, **resolved 2026-10-13 by the second owner direction:** no mask over live, live is full intensity; the mix-blend cost test and opacity-only fallback are out); Wi-Fi degrade (1080p, then the still, on the D11 ladder); light tuning; sensor key; `?kiosk=`; spare N95; V1 as a production Chrome over CDP; Chrome hold; no interim light push on the legacy WS; Service Worker shell; relay hosting; overlays and the info panel during live (item 20: no default; owner to decide; until then dropped in live mode and counted in `overlays_suppressed`); sequencing (item 21: P4b straight after the P4 hub core, ahead of overlays, content and P5, and P3 before production live; default: yes); 4K60 against Game Mode (item 22: moot, 4K60 holds without Game Mode)) | **Not yet answered by the owner**, except item 9, which the second owner direction (2026-10-13) resolves, and item 22, which is moot. This plan already follows the reorder, pending the owner's yes. Most urgent: approve P0.5 (items 4–5, including the D14 hold-last and the gated timed reloads). bd's design update for the art/live split landed at 20:58 on 2026-10-13 (stall behaviour is now in design §2.5); still open: item 20 (overlays and the info panel during live); ambient light and the live renderer is settled in design §4/§2.6 (no ambient influence between handshakes) | P0.5 (items 4–5); the rest by the phase that uses them |
| D28 | Room-AV programme adoption (AV-D1, AV-D6 in `docs/AV_PROGRAMME.md`) | AV-D1: keep this plan as the programme · the room as the platform, with a separate `room_service` and screen-machine as its screens subsystem. AV-D6: (a) this plan's order unchanged, room scope inside existing phases · (b′) P6B stage 1 on devtest during the 7-day shadow window · (c) a fourth session takes the P4 hub now | **Not yet answered by the owner.** AV-D6 (a) is this plan's own order. If adopted, §5.2 is folded into the phases: ≈2–3.5 d of K5 and DevicesPort work, and ≈32–47 owner-days to production live if P3 *reach* must also gate the claim API (§5.2 Effort). Independently of D28, this plan recommends the mask-endpoint token (§3.7, P4b). AV-D2 (the Windows UDP relay) is already decided (D22) | AV programme: Fri 16 Oct; before P1 (UDP guard) and before the K5 draft (asked for Fri 23 Oct) |

---

## 7. Explicitly not doing

- No big-bang rewrite and no parallel "v2 app". Everything ships into the running system behind existing signatures.
- No second display page, iframe player or navigation for live, and no app logic, CDP agent or local light loop on the mini-PC (principle 11; owner direction 2026-10-13). The display page is evolved in place, not rebuilt.
- No ASGI/FastAPI migration and no SSE on waitress (re-evaluation triggers in §1). No NATS or Redis, no microservice split beyond the stream service (Room-AV proposal pending AV-D1/AV-D6: stream service + room service; §5.2).
- No display hardware upgrades (for example a stronger mini-PC): the owner handles display processing power elsewhere, and it is out of scope for this plan; the plan works with the N95 as it is (the spare N95, V4, is a staging copy, not an upgrade).
- No app auth, login, RBAC, kiosk or operator tokens (the one proposed exception is the mask-endpoint token, §3.7/P4b, recommended by this plan and pending the owner's yes), and **no source-IP allowlisting inside WSL**.
- No DOM compositing over live video: in live mode no overlays, controls/info panel, mask, warm tint, HUD, CSS effects (opacity, filter, transform, blend) or page-side crossfades (second owner direction 2026-10-13). Live effects and transitions are rendered on the workstation GPU into the stream. No page-side speed-up to catch up (`playbackRate` stays ≤ 1.0).
- No media on :8765 and no media through waitress. No server-side GStreamer playout; switching between art and live is a client-side hard cut.
- No database for media or buckets. The filesystem stays the source of truth, and SQLite is used only for jobs.
- No schedule-language redesign. The scripts are only made to validate.
- No `ImmutableSandboxedEnvironment`.
- No visible overlay change (React overlays or sandboxed iframe) without owner parity sign-off.
- No removal of polling fallbacks until push has been proven for 7 days per surface. No removal of the WS relay without a week of traffic evidence.
- No reviving dormant features through lint or bug fixes without a decision.
- No switching stills to bucket-file URLs. The pointer plus rev stays the base.
- No git history rewrite or force-push without explicit owner approval. No `git stash/checkout/pull` in a tree holding live state. Commits in this checkout use the pathspec form (`git commit -m … -- <paths>`), because other sessions' changes are already staged in the index (§5.2).
- No pytest, builds or edits in a release dir. No `import app` outside pytest before P2.
- No moving bd's lab. No Chrome or NVIDIA driver upgrades inside the refactor (Chrome is not apt-held today, so hold it; see D7). No Docker or Kubernetes. No de-vendoring `samsungtvws`.
- No porting the broken test/sim UIs. A dry-run engine can come later on the T6 seams.

---

## 8. Risks and mitigations

| Risk | Mitigation |
|---|---|
| First restart (new Python build plus a dirty tree) breaks something unseen | P0 diff review, code-only commit, `py_compile` pre-flight, smoke checklist, `git revert` rollback, quiet window |
| Publisher source restriction refuses a legitimate caller | Enumerate callers in P0; watch `Refusing source` logs for 24 h |
| Path or state cutover orphans state | P1 goldens first; identical defaults; startup path report; rehearsal on :5050/:8775; copy-not-move; `output` symlink; documented 2-minute rollback |
| Untracking commit pulled into a live tree deletes state | Untrack only after prod runs from release dirs; documented rule |
| Unknown WS clients depend on relay or legacy formats (RunPod worker, light relay, Android, the client failing every ~5 s) | Legacy classifier kept; sanitised relay kept; per-shape logging for a week; T8 removes it only on evidence |
| Kiosk regressions are highly visible | Evolve the page in place (no parity port); shadow acks before any push; one variable per step, south first; per-screen `policy.*` flags flipped from the server in seconds, with `?ctl=legacy` as a static override; `dist_prev`; generic respawn and the KioskMonitor ladder; polling fallback |
| VA-API flags break GPU compositing on Chrome 136 (zero-copy under sway; the X11 path on the fallback), or an unplanned Chrome upgrade (apt candidate 155) changes the flag rules | Flags isolated in their own step; merged `--enable-features`; `KIOSK_VIDEO=copy` as the non-zero-copy alternative under sway; CDP checks; one-edit revert; hold Chrome with `apt-mark hold` (owner decision) |
| Powerline path degrades or dies (fallback Wi-Fi is only 15–20 Mbit/s; two 4K60 streams need about 60 Mbit/s) | 24 h soak before relying on it; failover to Wi-Fi on link loss (tested); Ethernet path watchdog for the link-up-but-dead case (to build); live degrades to a lower profile or still on Wi-Fi; transport-backlog-driven drop-to-keyframe; `kiosk.player_stalled` alerts |
| Host clocks drift apart again (the WSL clock ran 2% fast until fixed on 2026-10-11) | Ordering by seq/rev only; skew-fitted sync for latency; health check or alert on chrony/W32Time drift, starting from `scripts/wsl_clock_check.py` (P3 step 7); host-local stall timers |
| H.264 reorder trap reappears through a new encoder path | SPS rewrite in the mux layer; init-segment contract test |
| The renderer misses its 4K60 frame budget (the lab generator is late on about 2% of frames, giving 6–7% dropped on glass with a catch-up player) | Even pacing in the renderer (P6A step 3) with late source frames counted per session in `/api/state`; the page never plays above 1.0× and resyncs by a hard jump (P6B); the degrade ladder to 4K30 (D11); P6A exit needs live 4K60 to meet the display-fix bar |
| A visible jump at the art ↔ live cut (a black or mismatched frame, or a brightness step, since the art still may be dimmed by the mask and live is full intensity) | Planned cuts only between matching frames (design §2.5); anchor and exit frames drawn with the page's mask (shared formula, golden tests); ramp to full intensity drawn by the renderer after the cut; automated screencast cut check (P6B stage 1 step 4); the on-glass `grim` anchor-match check (geometry and colour tags) at full brightness and at a dimmed mask (P6B stage 1 step 4; installing `grim` needs the owner's OK) |
| Overlays and the info panel sent to a screen in live mode: an owner decision with no default (D27 / design §5 item 20: the renderer draws them, they are dropped or queued for art mode, or a mix by type) | Interim rule: the page draws none in live mode, drops them and counts them in `overlays_suppressed`, and the server waits until no overlay is showing (an operator start clears them) before going live; the owner decides before P6B stage 2; until then live runs only on devtest (`policy.live_enabled=false` on production screens) |
| Orphaned NVENC sessions or GPU contention on a shared box | TTL plus renew; reconcile deletes orphans; NVENC cap and admission; restart-storm alert |
| Core restart or a `state/screens/` restore freezes kiosks on stale state | Epoch-scoped ordering; snapshot authoritative; comparison by content key, so nothing remounts on unchanged state; `adopting` barrier for live; hold-last mask (D14) |
| Closing WAN ports breaks Alexa or remote use | Ingress and VPN verified first; ports closed in a window; external scan |
| The logger is not the only leak | RSS in `/api/health` with a growth alert; an optional RSS watchdog that restarts sm-web through its start script (no cgroup limit under the screen-session pattern) |
| Scheduler v2 drifts on event-heavy schedules | Goldens; 3–7 day shadow diff; per-destination flag; export-legacy rollback |
| Generation offload breaks Deadman semantics | T5 keeps or replaces the per-destination `alerts_health` tick |
| WSL IP churn after reboot breaks portproxy | Spectre's portproxy reconciler re-points WSL-targeted rules every 60 s (`fix-wsl-networking.ps1` cannot run on this PC); kiosk heartbeat alerts |
| A Windows reboot brings prod up wrongly: the stale `\Start WSL Services` task runs `~/wsl-autostart.sh` (dev mode, Vite dev, broad pkills) and `LocalAutomation_BootStart_screen-machine` runs a bare `python app.py` plus Vite dev | Install the ready replacements (`scripts/wsl-boot.sh`, Spectre's prod start) with the owner's OK; until then, after any Windows reboot check that prod is running in prod mode (`/api/health`, the `flask` screen session) |
| Solo bandwidth and scope creep | Critical-path rule; stopping points after every phase; tracks pausable |
| bd and the refactor collide in one tree | Lab never moved; export-ignored; port agreed (D16); `stream_service/` copies rather than moves |
| `start.sh`'s `pkill -f "python.*app\.py"` kills unrelated processes, including the operator's shell (happened during P0) | Pidfiles and screen session names in P2 (the owner's screen-session pattern); never use broad `pkill -f` patterns in ops scripts |
| Faster light response causes visible flicker or pumping | Log-lux snap/drift filter, deadband and CSS transition in `config/light.json` (D25, D27 item 11); torch, steady-light and optical loop checks in P4b |
| *X11 fallback only* (`kiosk-x11.service`): the single X screen presented only ~60 frames/s in total across both TVs (each Chrome ~30 fps with tearing, 31–34% dropped video frames) | **Fixed 2026-10-13** for 4K30: one X screen per TV (ZaphodHeads); 60 fps per window in a canvas test, ~0.25% dropped video frames at 4K30 HEVC live. Residual risk if the fallback is used: an X/conf change or `kiosk-loop.sh` fallback silently reverting to the single-screen layout. Mitigation: the kiosk heartbeat's fps/dropped fields (`kiosk.state`), a status-script check for kiosk-loop's `per-TV X screens: 2` log line, and `DISPLAY=:0.1` for south in every X-touching tool |
| *X11 fallback only:* screen freeze after a client change on a page-flipping window (xserver issue #1924: a new client presenting into a window that a now-gone client was page-flipping, e.g. after a Chrome GPU-process restart). Not seen here | Frozen-screen watchdog: no new frames from a TV (fps fields in `kiosk.state`) → restart that TV's Chrome, then `kiosk.service` (P5 step 3 ladder; kept under sway as a general check). Disabling PageFlip is not an option, because it undoes the X11 display fix |
| The Wayland (sway) kiosk regresses or has to be rolled back (new on the box since 2026-10-13 19:31 UTC; X11 tools and several ops scripts do not apply to it yet) | Passed reboot (up in ~40 s), Chrome-kill respawn, TV-missing-at-start and runtime-unplug tests; one-command fallback to `kiosk-x11.service` (4K30 only there, so live steps down to 4K30); bd's Wayland ports of the status scripts, the admin API display check and `deploy-kiosk.sh` (the `routes/admin_api.py` change is reviewed first and goes live only at an owner-chosen Flask restart); the kiosk heartbeat's fps/dropped fields; the 30-min presented-frame run before production live |
| HEVC frames not all presented on the N95: **largely closed under sway** (the earlier lab rows, 87% at best at 4K30 and 31–60% flagged never-presented, were X11 limits; a pre-made 4K60 file now drops 0.03%; design §6) | Remaining: the 0-frames-after-session-change rows and the 30-min live run at the primary profile with rVFC and `getVideoPlaybackQuality()` (`fb_probe.py` is X11-only), pass bar ≥ 99.9% presented over 30 min with 0 late renderer frames and ≤ 1 stall per 10 min (design §6 item 1); end-then-start profile changes until session changes on a running page are proven (design §6 items 1 and 5) |
| Private Network Access blocks a public-origin page opening `ws://` to a private IP | P3 (LAN origin) must finish before production live |
| A TV firmware update resets the picture settings (Samsung documents it), or a setting changes unnoticed | Read-only IP Control drift check against the measured 13 Oct baseline (`tv.settings_drift`; §3.5), run only outside Art Mode, with `-32601` or an empty reply treated as unknown, never as drift; re-applying needs the owner (by hand, or approved IP Control writes); IP Control calls serialised per TV, empty replies retried, timeouts ≥ 5 s |
| All sockets reset when the kiosk fails over from powerline to Wi-Fi (the source IP changes) | Jittered reconnect; 6 s heartbeat watchdog; the snapshot restores state without remounts; rung ladder per link group, then the still |
| MSE or VA-API leaks once the timed reloads are gone | Clean unmount (`removeAttribute('src')` + `load()`); 24 h soak and 50 attach/detach cycles (design §6 item 9); heap in `kiosk.state`; health-gated `kiosk.reload` on a heap threshold; the 04:00 reload kept for 30 days |

---

## 9. Appendices

### A. Deletions inventory

Re-verify each item with `scratchpad/v3/reach_id.py` or a grep before deleting.

**Backend:**
- `routes/rp_handler.py`, `routes/server.py`, the unregistered `routes_blueprint` in `routes/__init__.py`.
- `routes/test_scheduler_ui.py`, `routes/simulate_scheduler_ui.py`, `routes/test_buckets_ui.py`, plus their registrations (`app.py:74-75`) and `/simulate-scheduler` (D18).
- `routes/samsung_utils.py.save`.
- `routes/arc/` (unreferenced prompt text; confirm).
- The empty `routes/sysprompts/`, and its `SEARCH_PATHS` entry.
- `templates/` (done 2026-10-14), `routes/overlays/!!overlay_alert.html`, `routes/overlays/!!overlay_percentage.html`.
- `update_buckets.py`.
- `scripts/purge_recent.py` (wrong schema; rewritten in T4).
- `utils/hello.py`, `utils/clean-openai-files.py`, `utils/test_ws.py`.
- `utils/pair_tv.py` and `utils/samsung_control.py` → `scripts/manual/`.
- Root `test.py`, `test_handlers.py`, `test_jpg_endpoint.py` (empty), `test_overlay_instruction.py`, `test_reference_image_persistence.py`, `test_ws.py`, `test_ws_client.py` → `scripts/manual/` or delete. **Done 2026-10-14 (staged):** moved as `scripts/manual/manual_*.py`; `test_jpg_endpoint.py` and `test_ws_client.py` untracked and archived.
- `connection_registry.py` (T8, with the legacy `/` handler).
- `routes/data/publish-destinations.json` only (P2).
- Stale `routes/scheduler/_vars.json`.
- `routes/workflows/kreav2.json:Zone.Identifier` and every `:Zone.Identifier`.
- Done 2026-10-14 (staged; untracked and archived to `~/archive/screen-machine-root-2026-10-14/`): `HeaderSection.tsx.new`, the file named `how -p --diff-filter=D HEAD`, `all-sh.tmp`, plus `bun.lockb`, `changes.plog`, `recent_route_changes.diff`/`.txt`, the root `test-display.html`, `test_overlay_schedule.json`, `test_purge_scheduler.json` and `temp/`. Still to do: `*.save`, `*.bak`, `' - Copy.json'` (AKIA key ids inside presigned URLs that expired in May 2025; no secret), `wav/`, `debug/*.unf`, `*.tgz` (`wav/` and `debug/` wait on the owner).
- Tracked token `.txt` files: untrack and move to `config/tokens/`.
- `routes/mock/` and `service_factory.py` survive until T5/T6 replace them with ports and fakes.

**Python deps:**
- Remove fastapi, uvicorn, quart, quart-flask-patch, hypercorn, bootstrap-flask, redis, aiofiles, python-multipart, websocket-server, and the duplicate websockets and opencv lines.
- Declare python-dotenv, numpy, python-dateutil, and pydantic explicitly if it is used for codegen.

**Frontend:**
- The legacy `/display` tree: `src/pages/Display.tsx` plus `src/components/display/`. 84 files, 52 of them import-reachable, **routed at `/display` and rendering today**. D18: redirect or delete.
- The image-display dead set: **53 files** (42 import-reachable but never rendered, plus 11 never imported). Includes ViewModeContent/Selector, batch-components/*, view-modes/*, ImageBatch*, detail-view/* (8), fullscreen/FullscreenContent+Header, ImageDetailView, ImageInfoDialog, ReferenceImageDialog. First remove the `FullscreenDialog` mount and the `useFullscreenDialog`/`useFullscreen` calls, the never-selected `'generated'` tab branch and the unused `useImageDisplayState` import from `ImageDisplay.tsx`.
- Done 2026-10-14 (staged; archived to `~/archive/screen-machine-root-2026-10-14/src-orphans/`): `src/api.ts`, `config.ts`, `use-console`, `use-generation-websocket`, `services/apiService` (and `.OLD`), `api/*.OLD`, `ImageDisplay.fixed`, `pages/Lightsense` (duplicate of `LightSense/`), `view-modes/BucketView/*`; the uncalled `api.ts` methods and the mock-mode branches.
- `OLDDisplayPage.tsx`, `SchedulerEventsTest.tsx`, `SchedulerPage/`, `SchemaDynamicForm.tsx` (route; D18).
- The other dead hooks, `App.css`.
- About 91 other unreachable files (≈9.4k lines).
- npm deps: uniforms-antd, react-helmet, cors, recharts plus the unused chart wrapper.

### B. Backend module → target home

| Current module | Target | Phase |
|---|---|---|
| `app.py` | thin entry → `sm/app.py` (`create_app`) + `sm/runtime.py` (`start_services`) | P2 |
| `config.py` | shim over `sm/settings.py`; deleted | P2 / T8 |
| `overlay_ws_server.py` | v2 path router added in `ws_main` (P4), handing v2 paths to `sm/realtime/server.py`; legacy `/` handler unchanged until T8, then deleted with the module | P4 / T8 |
| `connection_registry.py` | deleted with the legacy handler (hub owns v2 clients) | T8 |
| `routes/__init__.py` | blueprint removed | T1 |
| `routes/admin_api.py` | `sm/api/admin.py` + `sm/devices/mediaserver.py` (SSH key, URL as data) | P5 (restart ladder over SSH keys) / T8 |
| `routes/alexa.py` | `sm/integrations/alexa/` (intents, verification, per-device session) | T5 |
| `routes/audio_api.py`, `routes/audio_utils.py` | `sm/audio/transcription.py` (session manager) | P2 bridge + executor (H2b), T8 |
| `routes/bucket_api.py` | `sm/api/buckets.py` over `BucketStore` | T4 |
| `routes/bucket_utils.py`, `routes/bucketer.py` | `sm/storage/bucket_store.py`, `sm/media/*` | P2 atomic write, T4 |
| `routes/display.py` | `send_overlay` → `sm/display/overlays.py` + bridge; mask routes → `sm/display/mask.py` (overrides persisted); channel API → `sm/api/screens.py` | P2 / P4 / P4b / P5 / T3 |
| `routes/display_utils.py` | `sm/display/mask.py` (pure) | P4b / T3 |
| `routes/file_api.py` | `sm/api/files.py` (schedule scripts only) | T3 |
| `routes/generate.py` | `sm/generation/{runpod,comfy_graph}.py` | T5 (P4 adds `job.*` emit) |
| `routes/generate_api.py` | `sm/generation/api.py` (`/api/jobs`) | T5 |
| `routes/generate_handler.py`, `routes/generate_utils.py` | `sm/generation/service.py` | T5 |
| `routes/lightsensor.py` | `sm/display/sensor.py` (SensorService) + `ingest.lux` on `/v2/ingest/lux` | P4b / T3 |
| `routes/manage_jobs.py` | `sm/generation/jobs.py` (SQLite) | P2 path, T5 |
| `routes/openai.py` | `sm/llm/client.py` | T5 |
| `routes/publish_api.py` | `sm/api/publish.py` | P2 reload, T4 |
| `routes/publish_utils.py`, `routes/publisher.py` | `sm/publishing/service.py` | P2 atomic, P5 hook, T4 |
| `routes/rp_handler.py`, `routes/server.py` | deleted | T1 |
| `routes/samsung_utils.py` | `sm/devices/samsung.py`: DevicesPort over IP Control (read-only observed state and settings drift; writes only with the owner's approval), plus the samsungtvws wake and Art Mode actions with the T2 fixes; photo sync per D17 | T2 (Room-AV proposal: before P6B stage 2, §5.2) / T8 (D17) |
| `routes/scheduler.py`, `scheduler_utils.py`, `scheduler_handlers.py`, `scheduler_queue.py` | `sm/scheduler/{runner,store,lifecycle,events,triggers,actions/}` | P2 H2, P6B action, T2, T6 |
| `routes/scheduler_api.py` | `sm/scheduler/api.py` (same URLs + snapshot) | T6 |
| `routes/service_factory.py`, `routes/mock/` | replaced by `sm/ports.py` fakes | T5 / T6 |
| `routes/simulate_scheduler_ui.py`, `test_scheduler_ui.py`, `test_buckets_ui.py` | deleted (D18) | T1 |
| `routes/utils.py` | `sm/core/*` (casting, ids, qr, templating), `sm/media/*` (images, metadata, thumbnails, urls), `sm/destinations/registry.py`, `sm/generation` (runtime-value resolver), `sm/integrations/alexa` | P2 settings, T3 |
| `routes/data/`, `routes/overlays/`, `routes/workflows/`, `routes/scheduler/` (schema + scripts) | stay as data dirs, read via explicit settings paths; optional move to `data/` | T3 / T8 |
| `routes/scheduler/<dest>.json`, `scheduler/_vars.json` | `state/scheduler/` | P2 |
| `utils/logger.py` | `sm/logging.py` (wrapper kept) | P0 bound, P2 hot path, T3 |
| `utils/alerts/*` | unchanged; `health.py` inverted to pushed heartbeats | T3 |
| `utils/samsung_control.py`, `utils/pair_tv.py` | `scripts/manual/` | T1 |
| `utils/hello.py`, `utils/clean-openai-files.py`, `utils/test_ws.py` | deleted / `scripts/manual/` | P1 / T1 |
| `update_buckets.py`, `scripts/purge_recent.py` | deleted / rewritten on `BucketStore` | T1 / T4 |
| `samsungtvws/` (vendored) | stays | none |
| `experiments/stream-latency/` | stays as the lab; graduated **copy** → `stream_service/` | P6A |
| `experiments/ipcontrol/` (`ipctl.py`, README; uncommitted) | stays as tooling; its protocol handling is the basis for `sm/devices/samsung.py` | T2 |
| `media-server/remote-scripts/*` | `deploy/kiosk/`: `kiosk.service` (sway) and `wayland/` (`kiosk-wayland.sh` + generic respawn and `?kiosk=` URLs, `sway-kiosk.conf`); the X11 fallback (`kiosk-x11.service`, `kiosk-loop.sh`, `xorg/`); `webview-manager.sh`, deadman, network scripts, installer; no agent | P3 / P5 |
| light-relay (source only on the media server today, `media-server/README.md:738-820`) | `deploy/sensors/light-relay/`: host-agnostic v2 relay | P4b |
| `start.sh`, `restart-flask.sh`, `fix-wsl-networking.ps1` | dev-only / deleted / retired (cannot run under PowerShell 5.1; superseded by Spectre's portproxy reconciler) | P2 / P3 |

### C. Corrections applied (claims in earlier drafts that verification refuted or qualified)

- **C5:** `/api/files` is not "arbitrary write". `abspath` blocks `..`. The prefix bug allows sibling paths that start with an allowed directory name; the real risk is planting schedule scripts.
- **C6:** the `samsung-token` values in the bundle and API are token *file names*. The real exposure is the git-tracked `.txt` files. Removing the fields from what is served needs a rebuilt and redeployed `dist`.
- **C10:** do not use a deque for `console_logs`, because `app.py` slices it. Trim the list in place, as the patch does.
- **C4:** use `SandboxedEnvironment`, not `ImmutableSandboxedEnvironment`. The sandbox fixes neither DoS nor double rendering.
- **C14:** legacy `/display` is routed and renders; deleting it is a decision. The image-display dead set is 53 files (not 45), and three of its modules are mounted on every Index render.
- **C15:** only `routes/data/publish-destinations.json` is a shadow. `routes/data/` itself is live.
- Source-IP trust (RFC1918 filters, "LAN ingest trusted") does nothing behind netsh portproxy and is removed from every interface.
- **C1:** the cross-loop fix needs no ASGI; one owner loop with `call_soon_threadsafe` / `run_coroutine_threadsafe(ws_loop)` works on today's host.

- **Behaviour review (2026-10-11):** a code-checked pass over every change in this plan found that several steps described as neutral were not. The fixes are now in the steps: removing other-extension pointer siblings would blank a screen after a `.png` publish; a context-only wait-write skip delays event persistence; a WS loop recorded once breaks overlays after a WS restart; routing overlays through the WS loop exposes them to transcription freezes (fix pulled forward from P4) and to one slow kiosk; systemd keeps `.env` inline comments; a git-export frontend build has no `VITE_*` values; UI-saved schedule scripts and Light Sense calibration would land in the release folder; saved state holds absolute dev-tree paths; alert emails read `logs/` next to the code; QR codes encode the public IP; legacy viewers cannot be filtered per screen; `compute_mask` would stop picking up live curve edits; a count-sized lux history shrinks at 20 Hz; and the P5 parity list missed the info panel, the trigger buttons, URL options and phone layout. *(Partly superseded on 2026-10-13: with the page evolved in place there is no parity port, so those features carry over automatically; with P0.5's hold-last shipped first, the P2 cutover no longer "probably goes dark"; and the legacy `/` handler now stays unchanged byte-for-byte.)*

- **Owner direction 2026-10-13** (relayed by the streaming session; design in `docs/DISPLAY_CONTROL_LAYER_DESIGN_2026-10.md`, which wins on the display layer). The owner asked for the display pages to switch to live video themselves under an active WS control layer, with all control machinery in one layer and a dumb mini-PC. **Dropped:** the iframe `/player` contract, page mode and `/player?return=` (live is inline only, D15); the kiosk-local light loop, Tier 2 (D25); the kiosk agent and `window.__smHealth` (D13: generic respawn plus a server-driven ladder instead); the `kiosk.html` rebuild, `/display-legacy` and the P5 parity port (the page is evolved in place, rolled back by `policy` flags). **Added:** P0.5 (leak fixes and hold-last first), v2 routed by path, shadow acks, per-screen `policy` flags, a hardened live state machine (10-frame warm-up, base never unmounted, end-then-start, ack-gated delete, timeouts, `adopting`), and the sensor relay as a host-agnostic node in `deploy/sensors/`. **Reordered:** P0.5 ahead of P1; P1+P2 a hard gate; P4b straight after the P4 hub core and shadow acks, ahead of overlays, content and P5; P3 before production live. The path to production live stays at about 29–40 owner-days (30–41 after the second direction), with light push on glass after about 18–25. The owner has not yet answered the design's §5 decisions (D27).

- **Second owner direction 2026-10-13** (relayed by the streaming session; bd is writing it into the design doc). Art mode is composited in the DOM, unchanged; live mode is composited by the live renderer on the workstation GPU, and the page shows only a full-viewport 4K60 `<video>` with nothing over it, at full intensity. **Dropped:** the mask, warm tint and overlays over live video; page crossfades into and out of live (now a hard cut between matching frames); the `mix-blend-mode` cost question and test; the opacity-only live fallback; D27 item 9 (resolved). **Changed:** the primary live profile goes from 4K30 to 4K60 HEVC at about 30 Mbit/s per TV (D11), with a degrade ladder to be confirmed; the page never plays above 1.0× and resyncs by a hard jump (replacing live-edge rate control that speeds up); the renderer must pace frames evenly. **Open** (design update): overlays sent to a live screen, stall and fallback detail without page fades, and ambient light and the live renderer. *(bd's update landed at 20:58 the same day: the anchor, exit and stall handshakes, ack-gated delete after 20 s and the confirmed ladder with a 4K30 ~10 Mbit/s rung are in design §2.5 and step 10, which grew the path to production live to 30–41 owner-days; overlays and the info panel during live became design §5 item 20, with no default; ambient light and the live renderer was later settled in design §4/§2.6: no ambient influence between handshakes.)*
- **"4K60 live video on both TVs does not work on the N95 as set up" (2026-10-13) was true only under X11.** The kiosk moved to Wayland (sway 1.9) at 19:31 UTC, where Chrome's zero-copy path works and a pre-made 4K60 HEVC file plays on both TVs with 0.03% dropped. X11-only facts (`DISPLAY=:0.1`, the Zaphod silent-revert risk, xserver #1924, window stacking, kiosk-loop's restart of both Chromes) now apply only to the disabled `kiosk-x11.service` fallback.
- **"The owner switched Game Mode ON; the owner's setting stands until the D24 test" (2026-10-13) was superseded the same evening.** Both TVs report Game Mode on Auto (inactive without ALLM), 4K60 does not depend on Game Mode, and the owner directed "no Game Mode, ever" (notes §3; D24).
- **"Game Mode would save only about 25–35 ms in our path" (D24, 2026-10-11) was an underestimate.** With the One Connect box path it would save an estimated 55–60 ms (spike §10: rtings measured 9 ms on Micro-HDMI in Game Mode and 69 ms outside it, and the box's 35–45 ms is probably a Game Mode figure). The decision is unchanged: the owner judged it not worth it.
- **The display-fix bar's "≥ 99% presented" was aligned with design §6 item 1 on 2026-10-13:** ≥ 99.9% presented over 30 min, with 0 late renderer frames and ≤ 1 stall per 10 min.
