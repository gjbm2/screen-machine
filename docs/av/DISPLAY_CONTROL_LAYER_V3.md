# Display control layer v3 — live control & signalling for display pages

**For:** the owner (§0–§1) and the agents who build it (all sections).
**Status:** design proposal **v3.1**. Drafted Tue 13 Oct 2026, late; revised Wed 14 Oct ~00:40 after a 45-item review (§11 logs each item as fixed or rejected, with the reason). **Additive to `docs/av/contracts/LIVE_V1.md` §2, except for the amendments listed in §8.6.** screen-machine-2f records those in LIVE_V1 before anyone builds against them. A v1.0 page and a v1.0 server keep working with a v1.x peer (§3.9).
**File owner:** screen-machine-2f, which owns `docs/av/` (AV_PROGRAMME §11). Send changes there.
**Builds on:**
- **DCL v2** (`docs/DISPLAY_CONTROL_LAYER_DESIGN_2026-10.md`, bd). Its protocol ideas, per-screen state model, degraded mode, security rules, failure table and N95 measurement plan (§6) carry over. Its host does not: DCL v2 assumed a hub inside Flask on :8765 (AV_PROGRAMME D1).
- **LIVE_V1** (the frozen v1 contract that the in-flight build implements), **AV_PROGRAMME v2** §3 and §7, **REFACTOR_PLAN** P0.5/P3/P4/P5 and D7/D13, **KIOSK_INFRASTRUCTURE_NOTES**.
- **`lightmask/`** (repo root), the shared light→mask package. It is already built and golden-tested against Flask (5049/5049).

**Method:** read-only. Three maps of today's signalling (page, server, in-flight build) were taken 13 Oct 22:37–23:45. For this revision they were re-checked against the in-flight tree at 14 Oct 00:20–00:35. File:line references are git HEAD (what is on glass) unless marked *in-flight*. **The in-flight files change by the minute, so §8.1 is re-taken at build start.**

---

## 0. Summary

### 0.1 What is wrong today

Both TVs run the page built from git HEAD on 11 Oct. Its signalling:

| # | Problem | What it does to the TVs |
|---|---|---|
| W1 | **The page sends nothing back.** No hello, ack, heartbeat, error or health report. | A black, white, frozen or leaking TV is invisible until someone looks at it. |
| W2 | **Pictures are found by polling:** 3 HEAD requests a second per page (about 260k a day per TV), with no timeout or in-flight guard, on 1 s `Last-Modified` resolution, against files written in place (`useFilePolling.ts`, `publisher.py:651-665`). | Up to 1 s late. A second publish in the same second is missed. A poll during the copy can show a truncated picture. On a stalled link, requests queue without bound. |
| W3 | **Brightness is polled every 3 s.** At HEAD, a failed poll and every page load mean brightness 0 (`useMask.ts:13-17,46-50`). The smoothing filter advances on every *read*, from every reader. Flask also uses the last lux reading however old it is (`display_utils.py:173-176`). | A Flask restart or a blip fades both TVs to black. Light response is 0.5–3.5 s, and it changes when someone opens the Light Sense page. If the relay stops, the mask freezes at its last value. |
| W4 | **Overlays ride an unauthenticated broadcast socket (:8765)** with no ids, snapshot, acks, per-screen routing or client liveness. Sends cross threads and event loops; the fan-out is serial (`display.py:253-275`, `overlay_ws_server.py:302-317`). | Overlays are lost across blips. One slow client stalls all clients for 20–40 s. A failed send leaves a silent "zombie" kiosk socket until the next reload. |
| W5 | **Every :8765 frame reaches every page.** Lux (3.8 frames/s, measured), progress and transcription frames become empty "phantom" overlays. Overlay scripts are re-appended on every overlay change. Clip loops leave orphan timers and decoders. | About 38 phantom full-screen divs and 15 React updates a second per TV, all day, plus growing scripts and timers. The likely cause of the browser instability behind the 4-hourly reloads. |
| W6 | **The only recovery and the only way a new build arrives** is an unconditional hard reload at 02/06/10/14/18/22h, both TVs at once (`index.tsx:369-422`). | Six visible dips a day. A reload during an outage lands on Chrome's error page. Ungated, it would cut across live. A build that fails to boot leaves a TV black until someone restarts it over SSH. |
| W7 | **Anyone who reaches :8765** (forwarded from the WAN) can send `{lux}` and steer both TVs' brightness. | Spoofable, including to black. |
| W8 | **The in-flight build** (LIVE_V1, behind `?ctl=`) is being fixed tonight. As of 00:35 its controlled page still runs the legacy file poll, the :8765 socket and the phantom-overlay path, and it falls short of LIVE_V1 in the places listed in §8.1. | Three sockets during live; phantom overlays remain. |

### 0.2 The new layer in one picture

```
 Flask :5000 (no change needed)         room_service :8700 (one process, one asyncio loop)     kiosk mini-PC (dumb)
 ──────────────────────────────         ─────────────────────────────────────────────        ────────────────────
 publisher ─writes─▶ output/<id>.* ──stat 4 Hz──▶ ContentWatcher ──┐
 send_overlay ─▶ :8765 broadcast ──listen only──▶ OverlayBridge ───┤
 /api/<id>/mask ◀── fallback only, ≤1 per 3 s ─── FlaskMaskSource  │
                       lightmask ◀── SensorService ◀───────────────┼─── /v1/ingest/lux ◀── light relay (key)
                                                  modes, Room UI ──┤
                                                                   ▼
                                    ScreenStore: desired │ applied (per topic revs, persisted)
                                                   │                   ▲
                                    ConnectionManager: per-client       GlassMonitor ─▶ /glass, alerts
                                    outbox; ScreenController picks      ▲
                                    the driven glass                    │ acks, kiosk.state
                                                   │ /v1/screen/<id>    │
                                                   ▼  one socket/page   │
 GET /display, /output, /api ◀── HTTP, same origin ── Chrome /display/<id>?ctl=… ──┘
                                          CtlDisplayPage (own component tree)
                                          ├ art stack (DOM): picture, mask, overlays (shadow roots)
                                          └ live layer: one 4K60 <video> ◀── stream_service :8702 media socket
```

### 0.3 Your questions, answered

- **"How does the new display page handle signals over the ws channel? Is our ws server robust enough?"** The TVs stop using the :8765 server. Each page has one socket to room_service. It sends a hello, receives a full snapshot, and from then on gets small, typed, per-screen messages. Each client has a bounded outbox. The newest brightness replaces an unsent older one. Commands are never dropped and keep their order. A slow client is disconnected rather than stalling the others. Both ends detect a dead socket within seconds. A page that loses its socket says on reconnect what it is actually playing. A message lost on a dead link therefore cannot restart live over a link that cannot carry it (§3.5).
- **"Light sensor processing not reliant on a 3 s poll."** The relay sends readings straight to room_service, which computes the mask with the shared `lightmask` package and pushes it. Real changes, such as lights going on or off or a cloud, are sent at once and appear in about a quarter of a second with the relay at 10 Hz (0.5–3.5 s today). Slow drift is sent at most every 3 s as a 1 s fade, so the GPU is idle in steady light. The page polls Flask only after its socket has been down for 30 s.
- **"Don't overload Chromium on the mini-PC."**
  - Brightness is drawn with compositor-only opacity, at most once per frame, with no React work per message.
  - At most one control socket and one media socket, and at most one decoding video while live.
  - Overlay updates are coalesced to one per 5 s.
  - The fraction of time the compositor is animating is measured and budgeted.
  - The limits are in §5.5 and are measured on the N95 before rollout (§9).
- **"The 4-hourly reloads exist because of leaks, and must not cut across live."**
  - The known leaks are fixed at their source (§5.6).
  - Reloads become a policy: never during live or a cut, gated on server health, one TV at a time, and preferably hidden in the black of a picture change (§5.7).
  - The six-a-day timetable becomes one health-gated 04:00 reload a day for 30 days (RP D13). It goes only if renderer and GPU memory stay flat (§9.4). The JS heap alone is not enough, because Chrome reports it coarsely unless told otherwise.

### 0.4 What changes for you

- New pictures are pushed. The change starts within about 0.5 s and the picture is fully in after the 3 s through-black fade, decoded first. No missed or half-loaded pictures, and a purged file never blanks a TV.
- Brightness follows the room within a fraction of a second. Failures hold the last picture and brightness. Nothing goes black or white unless told to.
- Reloads stop being a timetable. They happen when needed: a new build (smoke-tested first, one TV at a time), a crash, or the daily safety reload. Never during live.
- A glass view says what each TV is showing, and alerts on a stale, duplicate, drifting or offline page.
- **Mini-PC:** one owner window for the launcher changes in §8.4. The URL per TV moves into `/etc/default/kiosk-wayland`, the Chrome profiles move to persistent storage, Chrome starts only once the server answers, and Chrome gets two flags. Flask needs no change.
- Every step is switched per screen, at runtime, from room_service, and switches back in seconds. A rollback also works when a page cannot reach room_service or cannot boot (§8.2).
- **Build:** a minimum viable layer in about 4–5 builder-days with two builders. It starts after the AV Day-2 integration (§8.2), and soaks follow.

---

## 1. Principles

| # | Principle | In practice |
|---|---|---|
| P1 | **Server decides, page reports** | room_service holds each screen's desired state. The page applies it and reports what it actually drew. The page acts alone only to degrade toward the still (P8) or to roll itself back (§8.2). (DCL I5) |
| P2 | **State and commands are different** | *State* (policy, mask, picture, overlay set) is **latest-value**: a newer value replaces an unsent older one. *Commands* (freeze, live.\*, reload, maintenance) are **ordered**, never dropped or merged, and **acked**. *Telemetry* (`kiosk.state`, `log`, `hb`) is **fire-and-forget**. The four LIVE_V1 handshake acks are **critical**: they are re-sent after a reconnect (§3.3). |
| P3 | **Snapshot on connect** | `welcome` carries everything needed to draw the screen. A reconnect is a resync. The page compares by content key, which for a picture is the file's identity, not its URL (§3.3). A reconnect or a switch between push and poll therefore never remounts a picture, overlay or stream that has not changed. |
| P4 | **Never black, never white, unless told** | Failures hold the last picture and mask. Boot starts from the last known picture and mask. With nothing known, the page stays black until the first mask arrives, as today's page does for its first second, rather than flashing a guessed brightness (§5.7). The body is black. (DCL I4, LIVE_V1 rule 12) |
| P5 | **The N95 budget is part of the contract** | Compositor-only mask, no React render per message, compositor duty budgeted, everything bounded, at most one control and one media socket (LIVE_V1 rule 11, §5.5). |
| P6 | **The mini-PC stays dumb** | Config and the launcher only: no agent, no page logic, no probes (DCL I2). The one-time launcher changes are in §8.4. |
| P7 | **No dependence on :8765 or 3 s polls** | While its socket is up, a page polls nothing and never opens :8765. Polls exist only in degraded mode. room_service may *listen* on :8765 as a bridge until Flask notifies it directly. |
| P8 | **Degrade toward the still** | On any doubt (socket down, stall, failed decode, error), keep the last good picture and mask. Upgrades and retries are server decisions. |
| P9 | **Additive to LIVE_V1, amendments named** | New types go only to pages that declared the minor version. Where v3 changes a v1 rule, §8.6 says so, so a builder coding to LIVE_V1 does not diverge. |
| P10 | **Clock-free** | Nothing is compared across hosts' clocks. Order is epoch plus rev. Durations travel as relative values (`remaining_ms`) and are measured on one clock. Round-trip time is measured by echo. |
| P11 | **Every switch is runtime and per screen** | Each step has a rollout setting that flips back in seconds with no box access. The page kill switch also reaches a page that cannot connect, and a build that cannot boot can be rolled back without the box (§8.2). |
| P12 | **One owner per decision** | Reloads: one page module (ReloadPolicy) and one server module (ReloadManager). Pictures: ContentController. Overlays: OverlayHost. Driven glass: ScreenController. Nothing else reloads, swaps a base, mounts an overlay or picks the glass. |
| P13 | **Minimum first** | The build ships the minimum viable layer. Everything else is listed as deferred in §8.2 and waits for evidence. |

### 1.1 Terms (each defined once)

| Term | Meaning |
|---|---|
| **room_service** | The room control plane process: aiohttp, :3001 in dev, **:8700 in production** (AV_PROGRAMME D4, `deploy/sm-room.env`). |
| **instance** | One running room_service, with its own state dir and lock (§4.7). |
| **ctl page / legacy page** | A display page that runs `CtlDisplayPage`, the new component tree that opens the control socket / one that runs today's page (§5.1). |
| **glass client** | A page whose pixels are on a TV: role `kiosk`, or `lab` on devtest, authenticated by key (§3.2). |
| **driven glass / standby** | The one glass client per screen that room_service drives and believes / any other authenticated glass client on that screen, which gets viewer traffic only. |
| **successor** | A new page load whose `hello.prev.page_load_id` names the driven glass's page load: the same TV after a reload or a Chrome respawn. |
| **topic** | One piece of retained per-screen state: `policy`, `mask`, `state` (the picture), `overlays` (the whole set). |
| **rollout setting** | A per-screen server setting that switches a v3 step on or off (`mask_push`, `content_source`, `overlay_source`, …). Modes never change it (§4.4). |
| **desired / applied** | What room_service wants on a screen / what the driven glass reports it drew. |
| **drift** | Applied differs from desired for longer than a grace period (§4.5). |
| **rev** | Per-screen, per-topic counter, +1 on every change. It orders messages only; it never appears in a URL. |
| **epoch** | Random id chosen at each room_service start. |
| **content key** | The value that decides whether glass must change. Picture: `base.key`, the in-place file's identity `<ext>:<mtime_s>:<size>`. Overlay: id plus version. Live: the session. Mask: the values, within the deadband. |
| **command** | An ordered, acked instruction with an id: `fid` (freeze), `session` (live), `cmd_id` (reload, maintenance). |
| **critical ack** | `ack frozen`, `live_visible`, `art_visible`, `live_failed`: re-sent after a reconnect and deduplicated (§3.3). |
| **base** | The still or clip on the art stack. |
| **art stack / live layer** | The DOM layers that draw art mode / the MSE `<video>` under them (LIVE_V1 rule 4; DCL §2.2). |
| **reachable** | The driven glass has sent an inbound frame within the last 3 s (it sends `hb` after 2 s idle). |
| **degraded mode** | The page's behaviour once its control socket has been down for 30 s (§5.4). |
| **backstop** | LIVE_V1 rule 9: no reload while live, frozen, or from `live.attach` to the end of teardown, nor for 60 s after returning to art. |
| **art idle** | Layer art, not frozen, no live attach, no base transition running, no overlay visible. |
| **boot cache** | The page's `localStorage` record `sm.display.<screen>` (§5.7). |
| **owner window** | A time agreed with the owner for work on the kiosk or TVs. |

---

## 2. Signal inventory

Every signal that touches a display page today, and where it lives in v3. "S" numbers are the page map's.

| # | Signal today | New home | Transport | Delivery | Rate limit | Owner |
|---|---|---|---|---|---|---|
| 1 | Page load and new builds (S1): HTTP from Flask at Chrome start and on timed reloads | Load path unchanged. `hello.build` is reported. room_service compares it with `dist/index.html`, smoke-tests the new build, then may send `kiosk.reload{reason:"build"}` to one TV at a time | HTTP (load); control socket (command) | Ordered command, acked | ≤1 reload per 10 min per screen; one per (screen, build); halts on a failed TV | Flask serves; ReloadManager decides; ReloadPolicy executes |
| 2 | Base-name lookup (S2): `GET /publish-destinations`, which can never match | **Removed.** The URL comes in `screen.state.base.url` | — | — | — | page |
| 3 | Picture detection (S3): 3 HEAD/s | **`screen.state`** from ContentWatcher, keyed by file identity; one `ack{kind:"state"}` per change | control socket | Latest-value, plus a per-change ack | Coalesced; ≤1/s per screen; ≤1 per 10 s while live; none from freeze to unfreeze | ContentWatcher; ContentController |
| 3b | (fallback) | HEAD poll in degraded mode only, seeded with the drawn picture's key, so its first sweep is never a "change" | HTTP to Flask, same origin | Poll | 3 sequential HEADs every 2 s, in-flight guard, 4 s timeout, 2 matching reads | page (FilePoller) |
| 4 | Picture change rendering (S4) | `screen.state.transition`: `through_black` (default) or `cut`. Decode before the fade. No transition on a hidden art stack | page-internal | — | — | ContentController |
| 5 | Clip loop and first frame (S5) | Page-internal. No first-frame extraction and no new clip decoder while a live session is mounted (§5.6 L4) | — | — | — | ClipCycle |
| 6 | Mask poll (S6) | **`mask.set`** from SensorService (`lightmask`). Snaps go at once; drift is shaped (§4.4) | control socket | Latest-value | Snap ≤10/s per screen; drift ≤1 per 3 s; ≤2/s while live | SensorService; MaskController |
| 6b | (fallback) | Flask `/mask` poll after 30 s with the socket down; first value ramped over 10 s; hold-last | HTTP to Flask, same origin | Poll | 1 per 3 s, 4 s timeout | page (MaskPoller) |
| 7 | Lux frames broadcast to pages (S7) | **Removed from pages.** Relay → `/v1/ingest/lux` (LIVE_V1 §2.2b) | WS to room_service | Ordered stream, `lux.ack` | Relay ≤10 Hz plus 1 Hz heartbeat; server cap 20/s | relay; SensorService |
| 8 | Progress and transcription frames to pages (S8) | **Never reach pages.** Progress appears only as an overlay (id `job-<job_id>`, updated in place, coalesced) | — | — | — | OverlayBridge |
| 9 | Overlays (S9) | **`overlays.set`**: the whole set (≤3) with ids, versions and `remaining_ms` | control socket | Latest-value (one slot); reported in `kiosk.state`, not acked | ≤0.5 msgs/s per screen; each overlay ≤1 update per 5 s; HTML ≤32 KB | OverlayBridge (later a Flask hook); OverlayHost |
| 10 | The :8765 socket lifecycle (S10) | **Replaced** by the one control socket, with `hb` and liveness on both ends | WS `/v1/screen/<id>` | — | `hb` after 2 s idle | ControlClient; ConnectionManager |
| 11 | Timed hard reload (S11) | `policy.timed_reload` (LIVE_V1 rule 9); the daily `safety` reload while it is off; `kiosk.reload`; all health-gated (§5.7) | control socket; page-local | Policy latest-value; command acked | ≤1 per 10 min | ReloadPolicy; ReloadManager |
| 12 | On-screen controls panel and its polls (S12) | Stays local and operator-only. Polls get timeouts; the panel closes on freeze and live; no toasts on display routes | HTTP to Flask | Poll while open | As today, 4 s timeouts | page |
| 13 | Page → server telemetry (S13, absent) | **`kiosk.state`** every 5 s and on a layer, freeze or transition change; **`log`** for errors; acks | control socket | Fire-and-forget; commands acked; critical acks re-sent | 1 per 5 s (≤1/s on change); `log` ≤10/min | DisplayController; GlassMonitor |
| 14 | In-flight control socket (S14) | This layer: v1.0 plus the v1.x additions | WS | per §3 | per §3.6 | — |
| 15 | Live media socket (S15) | Unchanged (LIVE_V1 §1.4) | WS to stream_service | Stream | — | stream_service; LiveController |
| 16 | Freeze and live handshake (LIVE_V1 §2.1) | Unchanged, plus three guards: the page must be reachable, the latest picture must be acked, and nothing is pushed between freeze and unfreeze (§4.3) | control socket | Ordered commands, acked | Per cut | ScreenController |
| 17 | Mask overrides: `maskon`/`maskoff`/`brightness-override` (Flask HTTP) | Read from Flask's GET endpoints every 2 s (LIVE_V1 §2.2b); later owned by room_service, token-gated | HTTP (server side) | Poll | 0.5/s | SensorService |
| 18 | Light-mapping config (`intensity.json`, `config.py`, destination `intensity_cfg`) | `lightmask.ConfigWatcher` re-reads the files on change | files | — | refresh every 2 s, in the executor | SensorService |
| 19 | Interim lux trigger (room_service listens on :8765) | Kept until each sensor is on ingest. The same listener feeds OverlayBridge | WS, receive only | — | — | FlaskWsListener |
| 20 | Interim mask fetch `GET /api/<id>/mask` | **Fallback source only**, per screen, when that screen's `mask.source` is `flask` | HTTP | — | ≤1 per 3 s per screen while any screen runs the legacy page, else ≤1/s | FlaskMaskSource |
| 21 | Publishes and overlays from the scheduler, Alexa, the API and generation | Unchanged in Flask. Seen by ContentWatcher (files) and OverlayBridge (:8765) | files; :8765 | — | — | Flask |
| 22 | Admin SSH `restart-screen`, `set-url`; Samsung device control | Not this layer. The admin API gains a soft `POST /api/v1/screens/<id>/reload`. SSH restarts stay as the escalation (RP D13 KioskMonitor) | HTTP | Command | — | room_service admin; RP |
| 23 | Server health and page presence (absent) | `GET /api/v1/screens/<id>/glass`; `/healthz` with presence and loop lag; alerts | HTTP; events WS | — | — | GlassMonitor |
| 24 | Room UI status and events (LIVE_V1 §3) | Extended with a glass summary per screen | HTTP; events WS | Latest-value | ≤4/s | EventHub |
| 25 | Art-only snapshot page (LIVE_V1 §2.3) | Unchanged. It opens no socket (asserted by a test) | CDP (Playwright) | — | Per cut | snapshotter |
| 26 | Audio and transcription | Not a page signal. Stays on :8765 for the operator UI | — | — | — | Flask |
| 27 | (new) Static kill flag `/output/_ctl/<screen>.json` | Read by the ctl page at boot, and every 10 min while it has never received a welcome since load (§5.4) | HTTP to Flask, same origin | Poll | 1 per 10 min at most | page |
| 28 | (new) Boot watchdog | Inline script in `index.html`: black at once; reloads with backoff if the bundle never boots (§5.7) | page-local | — | backoff 1–30 min | page |
| 29 | (new) Page status in `document.title` | Control-plane state, readable through CDP `/json`, which `check-status.sh` already prints | CDP (read-only) | — | — | page |

---

## 3. Protocol v1.x on `/v1/screen/<id>`

### 3.1 Endpoint, envelope and versioning

- **Endpoint:** `ws://<ctl-host>:<ctl-port>/v1/screen/<screen_id>?role=kiosk|viewer|lab`, unchanged from LIVE_V1 §2. The address comes only from `?ctl=`.
- **Envelope:** `{"v":1,"type":"<type>","data":{…}}`, unchanged. Every new field lives inside `data`.
- **Version:** `v` is the major version and stays 1. Within it, changes are additive. Both sides ignore unknown types and unknown fields.
- **Negotiation:** by number only. The page sends `hello.proto.minor`. A 1.x message type goes only to a page whose minor is at least x. A page without `proto` is 1.0 and gets LIVE_V1 only. New fields on existing messages go to everyone; v1.0 readers ignore them (true of `protocol.ts` and `pages.py` as built). There are no feature tags.
- **Absent means default:** a field missing from a message takes the default in this document. In particular, a v1.4 page talking to a v1.0 server reads the missing `policy` fields as their defaults (§3.5), which is exactly the in-flight behaviour.

| Minor | Adds |
|---|---|
| 1.0 | LIVE_V1 §2 as frozen |
| 1.1 | Full `Policy` with `rev` and new fields; `role`; `log`; hello `proto`/`kiosk`/`have`/`prev`/`boot`; `hb.t` and its echo; `kiosk.state` `applied`/`art`/`budget`/`net`/`reload`; critical-ack re-send; the close-code table (§3.7) |
| 1.2 | `screen.state`, `ack{kind:"state"}`, `welcome.state` |
| 1.3 | `overlays.set`, `welcome.overlays` |
| 1.4 | `kiosk.reload`, `kiosk.maintenance`, `ack{kind:"cmd"}`, `welcome.maintenance`, `welcome.ctl_off` |

### 3.2 Roles

| Role | Who | Granted when | Receives | May send | Live |
|---|---|---|---|---|---|
| `kiosk` | The TV's Chrome | `?role=kiosk` **and** the screen's key in `hello.key` (§7). Without a valid key the socket is closed with 4401 (as built), unless the screen has no key configured (dev only) | Everything if driven; viewer traffic if standby | `hello`, `ack`, `kiosk.state`, `log`, `hb` | Yes, if driven glass |
| `lab` | The V1 test vehicle | `?role=lab` on **devtest only**, with the lab key | Same as kiosk | Same as kiosk | devtest only |
| `viewer` | Any other browser: phones, an operator laptop, the build smoke test | Default; no key | `policy`, `mask.set`, `screen.state`, `overlays.set`, `hb`. Never `live.*`, `freeze` or commands | `hello`, `hb`; anything else is ignored | No |
| artonly | The snapshotter's headless page | **Not a socket role.** It opens no socket and is driven over CDP by `window.__smRenderArt` (LIVE_V1 §2.3) | — | — | — |
| `sensor` | The light relay | On `/v1/ingest/lux`, with a sensor key (LIVE_V1 §2.2b) | `lux.ack` | `hello`, `ingest.lux` | — |
| bridge | The optional Flask hook | HTTP `POST /v1/ingest/*` with the bridge token, from localhost (§3.8) | — | Publish and overlay notices | — |
| operator | The Room page, admin tools | **Not on the screen socket.** HTTP API plus events WS (LIVE_V1 §3) | Events | HTTP commands | — |

**Driven glass: identity before recency.** These rules (LIVE_V1 §2.2a rule 3, made precise) live in **ScreenController**, not only in PageHub. Only the driven client ever reaches `on_page_hello` and `on_page_closed`, so a standby can never orphan a live session. In-flight `ScreensManager.welcome()` calls `on_page_hello` for every glass hello, and `on_page_hello` orphans live for any other page load (`screens.py` ~499-533); that is the bug these rules close.

| # | Event | Rule |
|---|---|---|
| G1 | A glass hello, and no driven glass | It becomes driven at once. |
| G2 | A hello with the **same `page_load_id`** as the driven glass (a reconnect, for example over a half-open old socket) | It replaces the old socket at once. The old socket is closed with 4009. There is no `role` message and no duplicate alert. LIVE_V1's same-page live rules apply (§3.5 `have.live`). |
| G3 | A hello whose **`prev.page_load_id`** names the driven glass, or the glass that left within the last 20 s (a reload or a Chrome respawn of the same TV) | It is the successor and becomes driven at once. A still-open old socket is closed with 4009. Live state: the new load starts in art, as LIVE_V1 says. |
| G4 | Any other glass hello (a copied kiosk URL on a laptop, a second tab) | Standby: `role{role:"standby", reason:"superseded"}` and viewer traffic only. Alert `screen.duplicate_glass`, except lab on devtest. |
| G5 | The driven glass leaves (socket closed, or not reachable for 15 s) | Hold for 20 s (`adopt_wait_s`) for G2 or G3. Then promote the newest *reachable* standby: `role{role:"kiosk", reason:"promoted"}` and a fresh `welcome`. |
| G6 | The operator forces it | `POST /api/v1/screens/{id}/glass {page_load_id}` makes that client driven; the others become standby. |
| G7 | A page that is demoted while live (only by G6) | It tears nothing down itself. Standby pages get no commands, so room_service ends the session (`live.abort` then `DELETE`) through its normal path. |

- **Why not "newest wins"** (in-flight `PageHub.glass()`): a laptop that opens a copied kiosk URL mid-live becomes driven, the TV's live session is orphaned and deleted, and the TV stall-cuts.
- **Why not plain "incumbent wins"** (v3.0): the laptop is promoted when the TV's socket closes on its routine reload, and the TV comes back as standby. G3 hands the glass straight to the TV's own next page load.
- `prev` is written to the boot cache at boot (§5.7), so a Chrome crash still leaves the crashed load's id for G3. On a reboot without a persistent profile, `prev` is lost. The TV then wins by G1 if no standby exists, or after G5's 20 s hold otherwise, and `duplicate_glass` says why.
- Source IPs cannot help: every peer reaches WSL as the Windows portproxy address (172.28.240.1).

### 3.3 Delivery classes, epochs, revs and content keys

| Class | Messages | Rules |
|---|---|---|
| **Latest-value state** | `policy`, `mask.set`, `screen.state`, `overlays.set`, `role` | Carries a `rev`. A newer unsent value replaces an older one in the outbox (§4.3). The page drops any rev at or below the one it has applied in this epoch. Retained in the snapshot. |
| **Ordered commands** | `freeze`, `unfreeze`, `live.attach`, `live.leave`, `live.abort` (LIVE_V1), `kiosk.reload`, `kiosk.maintenance` | FIFO per connection, never coalesced or dropped. Acked. Identified by `fid`, `session` or `cmd_id`. A pending `policy` is always sent before the next command (§4.3). |
| **Critical acks** | `ack frozen`, `live_visible`, `art_visible`, `live_failed` | If the socket is not open when one is due, the page keeps it in an unconfirmed queue (≤8, dropped after 60 s) and re-sends it after the next `welcome`. The server deduplicates by `(kind, fid|session)`. A `frozen` ack for a fid the server no longer holds is answered with `unfreeze{fid}`, so the page does not wait for its 30 s safety timeout. |
| **Fire-and-forget telemetry** | `kiosk.state`, `log`, `hb`, media `stats` | No ack. Each `kiosk.state` is a full report, so a lost one costs nothing. |

- **Epoch:** random per room_service start, sent in `welcome` only. A new epoch always arrives on a new socket, because sockets do not survive a restart, so per-message epochs are not needed.
- **Revs:** per screen and topic; +1 per change. On a new epoch the page resets its rev baselines and still compares content keys. Revs never appear in URLs, so a rev reused after a crash cannot alias a cached file.
- **Content keys decide, revs only order.** A message with a newer rev and the same content key changes nothing on glass.
- **Picture key = file identity.** `base.key` is `<ext>:<mtime_s>:<size>` of the in-place file `output/<dest>.<ext>` when the publish settled. The degraded HEAD poll computes the same key from `Last-Modified` (1 s resolution) and `Content-Length`. One picture can appear under three URLs: the immutable bucket file, the `?v=` in-place URL, or the poll's `?t=` URL. All three carry one key.
  - **Same key, different URL:** the page records the new URL for warm boot and ArtState, and nothing on glass changes.
  - **Verify once:** Flask's `send_from_directory` serves `Last-Modified` = floor(mtime). If it does not, the key uses `ETag` instead.
- **Policy is always sent whole.** LIVE_V1 lets `policy` be partial; room_service always sends the full object with `rev`, so coalescing two policy messages can never lose a field.

### 3.4 Message catalogue

| Type | Direction | Class | Since | Roles | Rate | Ack |
|---|---|---|---|---|---|---|
| `hello` | page → server | — | 1.0 (+1.1 fields) | all | once per socket | `welcome` |
| `welcome` | server → page | snapshot | 1.0 (+1.1–1.4 fields) | all | once per hello | — |
| `policy` | server → page | state | 1.0 (+1.1 fields) | all | on change | applied rev in `kiosk.state` |
| `mask.set` | server → page | state | 1.0 | all | snap ≤10/s, drift ≤1 per 3 s, ≤2/s live | none (rule 11); sampled lag in `kiosk.state` |
| `screen.state` | server → page | state | 1.2 | all | ≤1/s; ≤1 per 10 s live; none frozen | `ack{kind:"state"}` from driven glass |
| `overlays.set` | server → page | state | 1.3 | glass; viewers per policy | ≤0.5/s; none while live or frozen | ids in `kiosk.state` |
| `freeze`, `unfreeze`, `live.attach`, `live.leave`, `live.abort` | server → page | command | 1.0 | driven glass | per cut | LIVE_V1 acks (critical) |
| `kiosk.reload`, `kiosk.maintenance` | server → page | command | 1.4 | driven glass | ≤1 reload per 10 min | `ack{kind:"cmd"}` |
| `role` | server → page | state | 1.1 | glass | on change | — |
| `hb` | both | telemetry | 1.0 (+`t` 1.1) | all | after 2 s idle | echoed in `kiosk.state` |
| `ack` | page → server | — | 1.0 (+`state`, `cmd`) | glass | per event | — |
| `kiosk.state` | page → server | telemetry | 1.0 (+1.1 fields) | glass | every 5 s, and on change ≤1/s; ≤2 KB | — |
| `log` | page → server | telemetry | 1.1 | glass | ≤10/min, burst 5 | — |

### 3.5 Messages in detail

**`hello`** (LIVE_V1 fields first, including LIVE_V1 §3's `key`; the rest are 1.1):
```json
{"v":1,"type":"hello","data":{
  "role":"kiosk","screen":"north-screen","page_load_id":"p-3c9e1a2b","build":"index-Bq3f9x2K",
  "caps":{"mse_hevc":true,"mse_h264":true,"mse_av1":false},
  "flags":{"nomask":false,"nocolour":false,"fit":"fit"},
  "key":"…",
  "proto":{"minor":4},
  "kiosk":"north",
  "have":{"epoch":"e7f3a1c2","policy_rev":5,"mask_rev":90311,"state_rev":118,"overlays_rev":54,
          "base_key":"jpg:1760391845:812334","live":null,"ended":["s-3f9a12c4"]},
  "prev":{"page_load_id":"p-77aa0c11","reason":"cmd","cmd_id":"e7f3a1c2-41"},
  "boot":{"mode":"fade_in"}}}
```
- `role` in the URL query is authoritative; `hello.role` is informational (as built).
- `key` is LIVE_V1 §3's `hello.key`. The page takes it from the URL fragment `#ctlkey=`, which is never sent to Flask. As built, it also accepts the LIVE_V1 query form `&ctlkey=`, but the kiosk URL uses the fragment.
  - At boot the page holds the key in memory and `sessionStorage`, then removes the fragment with `history.replaceState`. The URL that CDP and `check-status.sh` print therefore carries no key, and the panel's `navigate({search})` calls cannot drop it (react-router 6.30 resets the hash).
  - The key is never put in the WebSocket URL.
- `build`: `unknown` (the bundle-name selector missed) and `dev` are never compared with `dist` (§4.4).
- `kiosk` comes from `?kiosk=north|south` (DCL, RP P3) and names the physical output for alerts.
- `have.live` is `{session, phase:"warming"|"playing"|"leaving"}` or null. `have.ended` lists the sessions this page load has torn down (≤8). The server uses them for live only:
  - If room_service believes the screen is live on session S and `have.live.session == S`: `welcome.live = S`, and the page carries on.
  - If room_service believes the screen is live on S but the page reports null, or lists S in `ended`: the page has already cut to art, and its `art_visible` was lost on a dead socket. The server treats the hello as that ack (reason `stall`): it records a fallback, sends no `welcome.live`, and re-enters through the normal handshake and backoff.
  - A hello without `have` (a v1.0 page) gets LIVE_V1's behaviour.
  - The page also ignores any `welcome.live` or `live.attach` for a session in its ended set, and re-sends `art_visible` for it. This protects it even against an older server.
- `prev` comes from the boot cache (§5.7). It feeds G3, closes the loop on `kiosk.reload` (`done`) and feeds `screen.reload_storm`.
- `boot.mode` is `fade_in`, `instant`, `cold` or `legacy_return` (§5.7).

**`welcome`** (LIVE_V1 fields: `epoch`, `screen`, `policy`, `mask`, `live`):
```json
{"v":1,"type":"welcome","data":{
  "epoch":"e7f3a1c2","screen":"north-screen",
  "server":{"version":"0.4.0","proto_minor":4,"hb_ms":2000},
  "role":"kiosk",
  "policy":{"rev":5,"live_enabled":false,"timed_reload":false,"safety_reload":"04:00","overlays_in_live":"drop",
            "mask_push":true,"content_source":"push","overlay_source":"push","telemetry":"full","reload_offset_s":0},
  "mask":{"rev":90312,"brightness":0.44,"warm_hex":"#F6D2B8","warm_alpha":0.029,"transition_ms":1000,"source":"sensor"},
  "live":null,
  "state":{"rev":119,"transition":"through_black",
           "base":{"key":"jpg:1760391845:812334","kind":"image",
                   "url":"/output/north-screen/20261013-221405-a1b2.jpg",
                   "fallback_url":"/output/north-screen.jpg?v=1760391845123456789-812334","poster_url":null}},
  "overlays":{"rev":55,"items":[{"overlay_id":"job-8f2c","version":"c41d","html":"…","position":"top-right",
                                 "remaining_ms":3200,"fadein":false,"after_state_rev":null,"kind":"progress"}]},
  "maintenance":null,"ctl_off":null}}
```
- `server.hb_ms` is informational; the timings are fixed in §3.6.
- `mask` is null unless room_service's value comes from a sensor reading or a Flask fetch at most 10 s old. With `mask: null` the page holds what it draws and does **not** start its Flask poll. A restart therefore never pushes a persisted value that may be up to 30 s old (as built, `welcome_mask` already returns null before the first push).
- `ctl_off` is `{"until_s":86400,"reason":"…"}` (a relative TTL) while a server kill switch is active for this screen (§8.2). The page sets `ctl_off_until` and reloads into the legacy page, subject to the backstop and health gates. The server persists the switch and re-issues it to any ctl page that connects while it is active, so it survives a kiosk reboot that loses `localStorage`.
- The welcome is at most 128 KB. Its overlay items are capped at 32 KB each and there are at most 3 of them (§3.6), and the outbox limit does not apply to it (§4.3).

**`policy`** fields. The full object is sent every time. A field absent from a message takes the default below.

| Field | Type | Default | Set by | Meaning |
|---|---|---|---|---|
| `rev` | int | — | server | Topic rev (1.1) |
| `live_enabled` | bool | `room.yaml` | `room.yaml` (LIVE_V1) | Turned off only after the exit handshake (DCL §2.4). |
| `timed_reload` | bool | `true` | mode (`page.<screen>.timed_reload`, ROOM_STATE_MODEL) | LIVE_V1 rule 9: today's 4-hourly schedule. |
| `safety_reload` | `"HH:MM"` \| null | `"04:00"` | rollout setting | While `timed_reload` is false: one health-gated reload a day at art idle (RP D13; kept for 30 days, §9.4). |
| `overlays_in_live` | `"drop"` \| `"queue"` | **`"drop"`** | mode, once the owner answers Q3 | LIVE_V1 rule 5 (drop and count) until then. |
| `mask_push` | bool | `room.yaml` | rollout setting | LIVE_V1. False → the page polls Flask's `/mask`. |
| `content_source` | `"poll"` \| `"push"` | `"poll"` | rollout setting | `push`: apply `screen.state`; the HEAD poll runs only in degraded mode. |
| `overlay_source` | `"legacy"` \| `"push"` \| `"off"` | `"legacy"` | rollout setting | `push`: use `overlays.set` and **close the :8765 socket**. `off`: no overlays. |
| `telemetry` | `"basic"` \| `"full"` | `"full"` | rollout setting | Whether `kiosk.state` includes `budget`. |
| `reload_offset_s` | int | `0` (south: 90) | rollout setting | Shifts timed and safety reloads, so the two TVs never reload together. |

**`mask.set`** is LIVE_V1's `{rev, brightness, warm_hex, warm_alpha, transition_ms}` plus an optional `source`. Its values are `lightmask`'s own strings: `sensor`, `astral` (the solar curve), `manual_override`, `maskoff`, `fixed`, `blend` (a stale-sensor ramp), plus `flask` for the fallback source. The page clamps every value and never applies a NaN (§7). `transition_ms` comes from the shaper (§4.4): 200 ms for a snap and 1000 ms for drift.

**`screen.state`** and its ack (1.2):
```json
{"v":1,"type":"screen.state","data":{"rev":120,"transition":"through_black",
  "base":{"key":"mp4:1760393210:41877721","kind":"video",
          "url":"/output/north-screen/20261013-224010-c3d4.mp4",
          "fallback_url":"/output/north-screen.mp4?v=1760393210482113300-41877721","poster_url":null}}}
{"v":1,"type":"ack","data":{"kind":"state","rev":120,"key":"mp4:1760393210:41877721","status":"applied",
  "url_used":"/output/north-screen/20261013-224010-c3d4.mp4","visible":true,"decode_ms":410,
  "recv_to_start_ms":420,"recv_to_visible_ms":3350}}
```
- `status`:
  - `applied`: on the art stack (`visible: false` while live).
  - `queued`: frozen; applied on `unfreeze`.
  - `deferred`: a clip arrived while a live session is mounted and no `poster_url` was given; it is applied through black once art is visible again (§5.6 L4).
  - `failed`, with `error`, once both `url` and `fallback_url` have failed.
- `url` is the immutable per-publish file when one can be identified, otherwise `fallback_url`. `fallback_url` is always the in-place file with `?v=<mtime_ns>-<size>`, and the page tries it at once if `url` fails to load (§5.8).
- room_service measures publish-to-glass on its own clock, from detection to the ack: `recv_to_start_ms` is when the change began on glass, `recv_to_visible_ms` when it was fully in.

**`overlays.set`** (1.3) carries the whole set. It replaces v3.0's `overlay.show`/`overlay.clear`, so there are no ordering questions between overlays:
```json
{"v":1,"type":"overlays.set","data":{"rev":56,"items":[
  {"overlay_id":"job-8f2c","version":"c41e","html":"<div …>…</div>","position":"top-right",
   "remaining_ms":118000,"fadein":false,"after_state_rev":null,"kind":"progress"}]}}
```
- The page diffs by `(overlay_id, version)`. An unchanged item is left alone. A new version of the same id replaces the content inside that overlay's shadow root without remounting its wrapper, and runs its scripts once (§5.6 L1). An id missing from the set is removed; `"items": []` clears all.
- `remaining_ms` is computed by the server on its own clock when the message is sent. The page starts its own timer from it, so an overlay expires even if no further message arrives.
- `after_state_rev`: show only once that picture rev is visible, capped at 5 s. This fixes today's publish prompt landing on the old picture.
- At freeze, OverlayHost fades visible overlays out (≤300 ms) but keeps the items. On an `unfreeze` that is not followed by live, it shows the unexpired ones again. An aborted entry therefore does not lose an overlay that was sent before the freeze.

**Commands** (1.4):
```json
{"v":1,"type":"kiosk.reload","data":{"cmd_id":"e7f3a1c2-41","reason":"build","when":"art_idle","deadline_s":3600,"flags":null}}
{"v":1,"type":"kiosk.maintenance","data":{"cmd_id":"e7f3a1c2-42","on":true,"ttl_s":3600,"reason":"owner window: N95 measurements"}}
{"v":1,"type":"ack","data":{"kind":"cmd","cmd_id":"e7f3a1c2-41","status":"deferred","detail":"live"}}
```
- `kiosk.reload.reason` is `build`, `operator`, `recover` or `rollback`. (Heap-driven reloads are deferred: §8.2.)
- `when`:
  - `now`: still subject to the backstop and the health gate.
  - `art_idle`: the next art-idle moment, preferably the black phase of a picture change. At `deadline_s` it runs at the next art-idle moment regardless.
- `flags` carries the kill switch: `{"ctl_off_ttl_h": 24}` makes the page reload as a legacy page and ignore `?ctl` until the TTL lapses (§8.2).
- `kiosk.maintenance` suspends the page's own reloads (timed, safety, boundary) and tells room_service to suppress alerts and automatic actions for that screen. It lapses at `ttl_s`.
- Ack `status`: `accepted`, `deferred` (with `detail`: `live`, `frozen`, `health`, `maintenance`, `rate`), `done`, `refused`, `duplicate`. A reload's `done` is inferred from the next `hello.prev.cmd_id`.

**`role`** (1.1): `{"role":"standby","reason":"superseded"|"forced"}`, or `{"role":"kiosk","reason":"promoted"|"forced"}` followed by a fresh `welcome`. A same-page reconnect (G2) and a successor (G3) never get a `role` message.

**`kiosk.state`** (LIVE_V1 fields first, then the in-flight additions, then 1.1). It is sent every 5 s, and also at once (at most 1/s) when `layer`, `frozen` or `art.transitioning` changes:
```json
{"v":1,"type":"kiosk.state","data":{
  "heap_mb":71.4,"long_tasks":0,"layer":"art","frozen":false,
  "base_url":"/output/north-screen/20261013-224010-c3d4.mp4",
  "mask":{"brightness":0.44,"warm_hex":"#F6D2B8","warm_alpha":0.029},
  "policy":{"rev":5,"timed_reload":false,"content_source":"push","overlay_source":"push"},
  "next_reload_at":"2026-10-14T04:00:00+01:00","live":null,
  "overlays_dropped":0,"freeze_timeouts":0,"reconnects":2,"mask_source":"push",
  "applied":{"policy_rev":5,"mask_rev":90312,"state_rev":120,"overlays_rev":57},
  "art":{"base_key":"mp4:1760393210:41877721","transitioning":false,"deferred_rev":null},
  "overlays":{"ids":[],"mounted":0},
  "budget":{"heap_precise":true,"heap_limit_mb":4096,"long_task_ms_max":0,"dom_nodes":412,"scripts":9,
            "videos":1,"decoders_active":1,"renders_1m":3,"ctl_in_1m":24,"mask_commits_1m":20,
            "anim_ms_1m":1200,"recv_to_commit_ms_p95":6.9},
  "net":{"hb_echo":{"t":81236000.1,"held_ms":1730},"online":true,"degraded":false},
  "reload":{"next_in_s":8133,"pending":null,"last_reason":"cmd","uptime_s":40211},
  "errors":{"count":0,"last":null},
  "maintenance":false}}
```
- `next_reload_at` is page-local wall time, for display only. `reload.next_in_s` is the clock-free value the server uses.
- `budget` is sampled cheaply (§5.5).
  - `heap_precise` says whether Chrome runs with `--enable-precise-memory-info`. Without it, `heap_mb` is cached for 20 min and bucketed (§5.5), and GlassMonitor ignores it for trends.
  - `anim_ms_1m` is the time in the last minute during which a mask or overlay CSS transition was running (summed from `transitionrun`/`transitionend`).
  - `recv_to_commit_ms_p95` is page-local. With `net.hb_echo` it lets the server estimate sensor-report-to-commit latency with no per-mask ack: server queue time + RTT/2 + page receive-to-commit. This reconciles DCL's ≤50 ms target with LIVE_V1 rule 11's ban on per-mask acks.

**`log`** (1.1): `{"level":"error","msg":"TypeError: …","where":"overlay:job-8f2c","count":3}`. It carries `window.onerror`, `unhandledrejection`, error-boundary trips and decode failures. Messages are truncated to 2 KB and repeats are counted, not resent.

**`hb`:** server → page `{"t":81236000.1}` (server monotonic ms, opaque to the page; 1.1); page → server `{}`. RTT = server now − `hb_echo.t` − `hb_echo.held_ms`.

**LIVE_V1 handshake messages** (`freeze`, `unfreeze`, `live.*` and their acks) keep their meaning. Three v1.x additions:
- The critical acks are re-sent and deduplicated (§3.3).
- **Freeze settle:** the page waits for a running base transition, a mask transition and the overlay fade-out to finish, up to **4.5 s** (in-flight: 2.8 s), and then for two frames before `ack frozen`. room_service's `freeze_ack_s` becomes **5 s** (AV_PROGRAMME §3.3: 3 s). The server also waits for the latest picture's ack before it freezes (§4.3), so in practice the wait is ≤1 s.
- **A freeze cancels an uncommitted reload:** its fade reverses, and the reload is retried after the backstop. Right before it commits, the page closes its control socket (1001 `reloading`), so no command can land on a committed reload (§5.7).

### 3.6 Timing and limit constants (the one place they are defined)

| Constant | Value | Source |
|---|---|---|
| `hb` when idle | 2 s, both directions | LIVE_V1 rule 1, §2.2a rule 5 |
| Page treats socket as dead | 6 s with no inbound frame | LIVE_V1 rule 1 |
| Server drops a silent client | 15 s (as built since 00:30) | LIVE_V1 §2.2a rule 5 |
| **Page reachable** (before a freeze, for G5 promotion, for "waiting for the page") | an inbound frame within 3 s | v3.1 |
| `hello` deadline | 10 s | as built |
| Reconnect backoff | full jitter `random(0, min(10 s, 0.5 s·2^n))`; n resets after 30 s connected | LIVE_V1 rule 1 |
| Inbound per client | 64 KiB per frame; 50 msgs/s | LIVE_V1 §2.2a rule 4 |
| Outbox per client | commands ≤64; ≤256 KB queued *behind* the frame being written; oldest queued ≤5 s; one send ≤5 s; `welcome` exempt | LIVE_V1 §2.2a rule 1 |
| `welcome` | ≤128 KB | v3.1 |
| `mask.set`, snap | ≤10/s per screen in art; `transition_ms` 200 | LIVE_V1 rule 11 |
| `mask.set`, drift | ≤1 per 3 s; `transition_ms` 1000; deadband 0.015 brightness, 0.005 warm alpha | v3.1 |
| `mask.set` while live | ≤2/s, applied without transition to the hidden stack | LIVE_V1 rule 11 |
| Flask `/mask` fetch (fallback source) | ≤1 per 3 s per screen while any screen runs the legacy page; otherwise ≤1/s | LIVE_V1 §2.2b, tightened |
| `screen.state` | ≤1/s per screen; ≤1 per 10 s while live; none from freeze to unfreeze | v3 |
| Wait for the state ack before a freeze | ≤5 s | v3.1 |
| Freeze settle (page) / `freeze_ack_s` (server) | 4.5 s / 5 s | v3.1 (AV_PROGRAMME §3.3: 3 s) |
| `overlays.set` | ≤0.5 msgs/s per screen; each overlay ≤1 update per 5 s (first show and clears at once); ≤3 items; HTML ≤32 KB each | v3.1 |
| `kiosk.state` | every 5 s, ≤2 KB; on change ≤1/s | LIVE_V1 §2.1 |
| Critical-ack queue | ≤8; re-sent after the next `welcome`; dropped after 60 s | v3.1 |
| Command retry | 2 s, up to 3 times per socket | v3 |
| Degraded switch | after **30 s** down: mask poll (every 3 s, 4 s timeout, first value ramped over 10 s) and picture poll (HEAD every 2 s, 4 s timeout, 2 matching reads) | v3.1 (LIVE_V1 rule 2: 5 s) |
| Timed reloads with no socket | after 30 min down, or no `welcome` since load | v3.1 (as built: 5 s) |
| Glass hold before promoting a standby | 20 s (`adopt_wait_s`) | v3.1 |
| Reload rate | ≥10 min apart per screen; one screen at a time | DCL §2.7 |
| Health gate | same-origin `GET /api/health` answers within 2 s | RP P0.5 step 7 |
| Build rollout | smoke test ≤60 s; the next TV only after a hello on the target build within 15 min | v3.1 |
| Boot watchdog | 30 s; then reloads at 1, 2, 5, 10, 30 min | v3.1 |
| Boundary self-rollback | 3 page-boundary trips within 1 h → legacy for 6 h | v3.1 |
| Static kill flag | at boot; every 10 min while there has been no `welcome` since load | v3.1 |

### 3.7 Close codes

| Code | Sent by | Meaning | Page reaction |
|---|---|---|---|
| 1000 / 1001 | either | Normal / going away (the page sends 1001 `reloading` before a committed reload) | Jittered reconnect |
| 4001 | server | Dead: silent for 15 s (as built) | Normal backoff |
| 4002 | server | No `hello` within 10 s (as built) | Normal backoff |
| 4008 | server | Behind: outbox over its limits (as built since 00:30) | Reconnect at once (as built). After 3 × 4008 within 1 min, normal backoff and `screen.slow_client` |
| 4009 | server | This socket was replaced by a newer socket of the same page load (G2) or its successor (G3) | Do not reconnect *that* socket. If no other socket of this page is open after 30–60 s (a race or a bug), reconnect normally |
| **4012** | server | Restart or drain | Reconnect after 0.5–3 s jitter |
| 4029 | server | Rate limited (as built since 00:30) | Backoff, at least 10 s |
| 4400 | server | Unsupported major version (as built) | Degraded mode; retry in 5 min |
| 4401 | server | Missing or bad key: a kiosk or lab page (as built), or an ingest path | Page: degraded mode, retry in 5 min, `document.title` says so. Relay: fall back to the legacy send |
| **4404** | server | Unknown screen. The upgrade is **accepted** and then closed with this code, because a browser reports a refused upgrade as 1006, which the page cannot tell from a network failure (in-flight: HTTP 404 before the upgrade; the constant already exists in `pages.py`) | Degraded mode; retry in 5 min |

v3.0's 4013 (slow consumer) is dropped in favour of the as-built 4008.

### 3.8 Ingest paths

**Sensor:** `WS /v1/ingest/lux`, exactly LIVE_V1 §2.2b: `hello` with a key, then `ingest.lux{sensor, lux, seq, mono_ms, held}`, answered by `lux.ack{seq}`. The relay side is already in the repo (`tools/room/latency_cal/lightsense.py`, the calib-3 relay); deploying its `room.json` is the owner's step. A server → relay `cfg` message (DCL) can be added later as a 1.x type.

**Bridge** (optional; used only if the owner approves the Flask hook, §8.3). HTTP, `Authorization: Bearer <bridge token>`, accepted only from 127.0.0.1. It is outside `/api/`, so the room-token middleware does not apply.
```json
POST /v1/ingest/publish   {"dest":"north-screen","immutable_url":"/output/north-screen/20261013-224010-c3d4.mp4",
                           "kind":"video","published_at":"2026-10-13T22:40:10Z","silent":false,"history":false,"source":"scheduler"}
POST /v1/ingest/overlay   {"overlay_id":"prompt-7d1e","screens":["north-screen"],"html":"…","duration_ms":30000,
                           "position":"bottom-left","clear":true,"fadein":true,"job_id":null,"kind":"prompt",
                           "after_publish":true}
```
- Both answer `202`.
- A publish notice is a *hint*: ContentWatcher stats and settles the file at once instead of waiting for its next sweep. Detection drops from ≤0.5 s to about 0.
- HTML over 32 KB is refused with 413.

### 3.9 Schema file and contract tests

- **`schema/room_screen.v1.json`** (JSON Schema 2020-12; AV_PROGRAMME §11 gives `schema/room*` to screen-machine-2f): one `$defs` entry per message type, with a discriminator on `type`. Each type carries one `x-since` minor; there are no per-field notes.
- **`schema/examples/room_screen/<type>[-variant].json`:** one example per message, including every example in §3.5, plus a **poll-path ArtState** (`base_url: "/output/devtest.jpg?t=1760391845000"`) that must pass the server's validation (§7).
- **Server tests** (`room_service/tests/test_contract.py`; add `jsonschema` to the sm-room venv):
  - every example validates;
  - in the fake-page tests, every message room_service emits is validated through a test hook on the outbox;
  - a drift test asserts that every schema type is handled in `pages.py` and that every handled type is in the schema.
- **Page tests:** every example parses with `parseEnvelope`; everything `envelope()` produces validates (`ajv` is already a dependency); a drift test against the `protocol.ts` unions.
  - **Vitest is not in `package.json`.** Adding it means an `npm install` in the shared checkout while other agents run Vite. Do it only in an agreed install window. Until then, run the page contract tests in a scratch package with its own `node_modules`, outside the checkout's dependency tree.
- **Compatibility, both ways:**
  - a v1.0 page fixture (hello without `proto`) against a v1.4 server receives no 1.x types and still works;
  - a v1.4 page against a v1.0 server (a rollback of room_service) reads the missing `policy` fields as their defaults, keeps timed reloads on, and keeps polling for pictures and overlays.

---

## 4. Server architecture inside room_service

### 4.1 Components

Everything runs on room_service's one asyncio loop. Blocking work (stat, file reads, JSON writes, `lightmask` config refresh and its astral maths) goes to an executor (LIVE_V1 §2.2a rule 6). A loop-lag probe reports p99 lag in `/healthz`.

| Component | Job | Builds on (in-flight) |
|---|---|---|
| **InstanceLock** | One instance per state dir; owner-tagged stream sessions (§4.7) | new, small |
| **ConnectionManager** | Sockets, roles, keys, outbox, liveness, 4404 | `pages.py` PageHub/PageClient (outbox, keys and 4008/4029/4401 since 00:30), extended |
| **ScreenStore** | Desired and applied per screen; revs; rollout settings; persistence; change log | new (`display_store.py`) |
| **ScreenController** | LIVE_V1 handshake, plus: the driven-glass rules G1–G7 (§3.2); the reachability gate; waiting for the picture's ack before a freeze; `hello.have.live`; the critical-ack dedup; releasing a stale fid only after adoption | `screens.py`, changed where listed |
| **ContentWatcher** | Detects publishes; file identity keys; re-checks the immutable file | new (`content.py`) |
| **FlaskWsListener + OverlayBridge** | One receive-only :8765 socket; classifies frames; maintains each screen's overlay set; coalesces updates | the listener in `mask.py`, moved to `flask_ws.py`; new `overlays.py` |
| **SensorService** | Ingest; `lightmask` (filter, mapping, staleness, overrides); MaskShaper; MaskArbiter | new (`sensor.py`), wiring only |
| **FlaskMaskSource** | Fallback mask from Flask's `/mask` | `mask.py` MaskPusher, reused as a source |
| **PolicyService** | Page policy from `room.yaml` and modes, merged with the rollout settings | `config.py`, `coordinator.py`, extended |
| **ReloadManager** | Build tracking; smoke test; reload, maintenance and kill-switch commands; staggering | new (`reloads.py`) |
| **GlassMonitor + AlertSink** | Drift, staleness, budget, alerts | new (`glass.py`, `alerts.py`) |
| **Admin API** | Glass, settings, reload, maintenance, kill switch, forced glass | `api.py`, extended |
| **EventHub** | Room UI status and events | `events.py`, unchanged |

File names are suggestions for the room_service builder.

### 4.2 ScreenStore

One record per configured screen:
```
ScreenRecord
  settings      rollout settings (mask_push, content_source, overlay_source, telemetry,
                safety_reload, reload_offset_s, mask.source) + rev     # persisted; never changed by modes
  desired
    policy      page policy (room.yaml ⊕ mode) ⊕ settings + rev
    mask        MaskSet + rev + source + fresh_at                      # last value persisted ≤1 per 30 s
    state       ScreenState{base{key,kind,url,fallback_url,poster_url}, transition} + rev   # persisted
    overlays    [Overlay(id, version, html, expires_mono, after_state_rev, kind)] (≤3) + rev   # wall-clock expiry persisted
    maintenance {on, until, reason} | null                             # persisted
    ctl_off     {until, reason} | null                                 # persisted; re-issued in welcome
    commands    {cmd_id: Command(kind, data, deadline_mono, status, tries)}
    live        owned by ScreenController (LIVE_V1)
  applied  (from the driven glass only)
    page        {cid, role, kiosk, page_load_id, prev_page_load_id, build, connected_at, last_inbound_mono, minor}
    revs        {policy, mask, state, overlays}
    glass       {layer, frozen, base_key, base_url, transitioning, mask_drawn, overlay_ids, live stats}
    budget      latest kiosk.state budget + a 6 h series (one point per minute)
    standby     [{cid, page_load_id, connected_at}]
    errors, reconnects, slow_closes, page_loads (last 50, with reasons)
```
- **Producers write intents, never sockets:** `set_state(screen, base, cause)`, `set_overlays(...)`, `set_mask(screen, mask, source)`, `patch_policy(screen, patch, cause)`, `patch_settings(screen, patch, cause)`, `command(screen, kind, data)`. Each bumps the topic's rev and calls `ConnectionManager.publish(screen, topic)`.
- **Every desired change** is appended with its `cause{actor, corr}` to `logs/<screen>.jsonl` in the state dir (DCL §2.4), with observed changes beside it. "Why was south black at 21:14?" becomes a grep.
- **Overlay expiry** runs on the server's monotonic clock. Expired overlays are dropped from the set and never re-sent.
- **During live and freezes** the store keeps accepting changes. ConnectionManager decides what goes out (§4.3).

### 4.3 ConnectionManager and the outbox

**Per-client outbox** (the in-flight one, since 00:30, already has latest-value `mask.set`/`policy`, a byte limit that excludes the frame in flight, and an age limit):
```
Outbox
  commands  deque[msg]               # ordered lane: never merged or dropped; >64 → close 4008
  slots     OrderedDict[key → msg]   # latest-value lane; key = "policy" | "mask" | "state" | "overlays" | "role"
  held      set[key]                 # slots the writer skips for now (see gating)
  put_state(key, msg): replace the pending value in place, else append
  put_cmd(msg):        append
writer task
  if "policy" pending and not held and commands:  send policy        # a command never overtakes the policy it needs
  elif commands:                                  send commands.popleft()
  elif a slot is pending and not held:            send the first one
  else:                                           hb after 2 s idle
  await wait_for(ws.send_str(next), 5 s)                             # timeout → close 4008
  close 4008 if bytes queued behind the frame in flight > 256 KB, or the oldest queued is > 5 s old
  `welcome` is never counted against either limit
```
- **Commands go first**, except behind a pending `policy`. Commands are rare, and freeze or abort must never wait behind a picture. `live.attach` must not arrive before the `policy{live_enabled:true}` it depends on: the page refuses an attach when `live_enabled` is false (`LiveController.ts:75-77`), and the refusal would count as an entry failure.
- **Cross-topic couplings are explicit.** Only three exist:
  - policy before a command (above);
  - an overlay that belongs to a new picture (`after_state_rev`);
  - a freeze waits for the picture's ack (below).

  Overlays travel as one set, so there is no ordering between overlays at all.
- **What each role receives:** viewers and standbys get the state topics. Only the driven glass gets commands.
- **Gating** (LIVE_V1 rule 11, plus v3.1):

  | Phase | `mask` | `state` | `overlays` |
  |---|---|---|---|
  | Art, idle | snap ≤10/s, drift ≤1 per 3 s | ≤1/s | ≤0.5/s |
  | From `freeze` to `unfreeze` | ≤2/s (the page queues the latest) | **held** | **held** |
  | Live | ≤2/s | ≤1 per 10 s | **held** |

  - With `overlays_in_live: "drop"`, OverlayBridge drops new overlays for a screen while it is live and counts them. With `"queue"`, they stay in the set and the current unexpired set is sent after the return to art.
  - **Before sending `freeze`**, ScreenController waits up to 5 s for `ack{kind:"state"}` of the latest state rev it sent. A freeze therefore never lands in the middle of a through-black change that it caused itself.
- **Publish fan-out** is `put_*` on each client's outbox: no awaits and no loops over slow sockets. One slow client therefore never delays another, which is the root fix for W4.
- **Inbound** (as built, plus): `log` is added to the page types; per-role allowlists; invalid fields drop the message and increment `bad_msgs`; JSON errors are counted, never fatal.

### 4.4 Producers and bridges

**ContentWatcher** (zero Flask change). It catches every publish path, because they all go through `publisher._publish_to_destination`.
- **Sweep:** every 250 ms, one executor call stats `output/<dest>.{jpg,JPG,mp4}` for each configured screen, plus the current immutable bucket file of each screen. That is 12 stats per sweep.
- **Candidate:** the newest by `st_mtime_ns`. The extension filter stays `.jpg/.JPG/.mp4`, as today (DCL item 7).
- **Settle:** a candidate is accepted once `(mtime_ns, size)` is unchanged across two sweeps. That covers `copy2`'s in-progress and copied-mtime states and the following `touch()`. Latency is 250–500 ms after the write.
- **Key:** `base.key = "<ext>:<floor(mtime_ns/1e9)>:<size>"` of the in-place file.
- **URLs:**
  - `fallback_url` is always `/output/<dest>.<ext>?v=<mtime_ns>-<size>`.
  - `url`: read `output/<dest>/bucket.json` `published_meta` (in the executor; a partial read is retried at the next sweep). If it names a bucket file that exists with the same size, push the **immutable** `/output/<dest>/<file>`, which never changes after publish, so truncated reads cannot happen. Otherwise `url = fallback_url`. `_record_publish` runs after `copy2` and `touch` (`publisher.py:656-697`), so a sweep can see the file before `bucket.json`. It then pushes the fallback form first, and the immutable form follows with the same key, which costs nothing on glass.
- **Published file deleted.** The immutable file can disappear while it is on glass:
  - the scheduled 30-day purge (`routes/scheduler/{north,south}-screen.json:264`; `bucketer.purge_bucket` goes by mtime, skips only favourites, and never skips the published file);
  - a manual delete (`bucket_api.py:334`).

  A same-bucket republish keeps the bucket file's old mtime, so a 40-day-old picture republished today is purged tonight. ContentWatcher re-stats the immutable file on every sweep. If it is gone or its size has changed, it pushes a new `screen.state` with the **same key** and `url = fallback_url` (rev+1). Nothing changes on glass, and the page's boot cache and ArtState follow the new URL. The in-place copy survives purges. Ask RP to make purge and delete skip each destination's published file (§8.3).
- `kind` comes from the extension. A Blank publish (the 651-byte `_blank.jpg`) is an ordinary picture, as today.
- A bridge publish notice (§3.8) triggers an immediate stat and settle. The two paths deduplicate on the key.
- `screen.state.rev` is persisted. On start, the first sweep compares the files with the persisted state, so a publish made while room_service was down is pushed at once.
- `poster_url` is null in the MVP. Later, room_service can extract frame 0 with ffmpeg at detection (as the snapshotter does) and serve it from its own HTTP (§5.6 L4).

**FlaskWsListener and OverlayBridge** (zero Flask change):
- One receive-only socket to `ws://127.0.0.1:8765/`, which never sends a data frame (the rule `mask.py` already follows). It also carries the interim lux trigger until ingest replaces it.
- **Classification:** a frame with non-empty `html` is a server-originated overlay. P0's `_sanitize_relayed` strips `html` from relayed frames, so no WAN client can inject an overlay through the bridge. `lux` frames are the interim trigger. Everything else (progress, transcription, `job_id`-only frames) is ignored.
- **Mapping to a screen:** the `screens` list → those screens; null or empty → every configured screen; `index` is the operator UI and is skipped.
- **Mapping to an overlay:**
  - `job_id` → `overlay_id = "job-<job_id>"`, updated in place; otherwise `ov-<8 hex of the html hash>-<n>`.
  - `version` is a hash of the HTML. `duration` (ms) → expiry. `fadein: 0` → `fadein: false`.
  - `clear: true` → the new overlay replaces the whole set.
  - HTML over 32 KB is dropped with an alert (the largest template today is 8.4 KB).
- **The set:** the bridge keeps each screen's set (≤3 items; the oldest is evicted) in ScreenStore. Every change produces one `overlays.set`.
- **Coalescing:** a job's progress arrives up to twice a second (Flask polls RunPod every 0.5 s, `generate.py:541`). Per `overlay_id`, TV-bound updates go out at most once per 5 s and the latest content wins. A first show and a clear go out at once. Each overlay therefore costs about 12 updates a minute instead of about 120.
- **Prompt on the right picture:** if the screen's `state.rev` changed in the 2 s before an overlay arrived, or changes in the 600 ms after it, the overlay gets `after_state_rev`. The bridge therefore holds each new overlay for up to 600 ms (overlays are not latency-critical). With the Flask hook this becomes exact (`after_publish: true`).
- **Known gaps** until the hook: no overlay ids from Flask; overlays sent during the listener's own reconnect, or its first 0.5 s on :8765, are lost.
- **Native overlays (step 5b, optional):** with the hook, the bridge prefers `POST /v1/ingest/overlay` and ignores :8765 HTML frames that match by id or HTML hash within 2 s. The :8765 listener then carries only legacy traffic.

**SensorService** (LIVE_V1 §2.2b, adopted in full). This is **wiring of the existing `lightmask` package**, following its README's room_service block: no new filter or mapping code, and no new goldens.
- `/v1/ingest/lux` with per-sensor keys from `~/.config/screen-machine/room_sensors.json`.
- **Filter:** one `lightmask.LuxFilter` per sensor (log-lux snap and drift, the spike guard, `held`, `seq`, staleness, warm start). Use `f.is_held(t)`, not the latched flag. Persist `export_state()` with each push.
- **Config:** `lightmask.ConfigWatcher.refresh()` every 2 s in the executor.
- **Overrides:** read from Flask's GET endpoints every 2 s (`lightmask.Overrides.from_flask`).
- **Per screen:** `mask_for_dest` runs on each sample and on a 1 s timer (the solar curve and the stale ramp move without samples), then goes through `lightmask.MaskGate` with **`max_rate_hz = 10`** (the package default is 20; LIVE_V1 says 10/s per screen).
- **MaskShaper** (new, about 30 lines) turns the gate's output into messages, so the compositor is idle in steady light (§5.5):
  - **Snap:** |Δbrightness| ≥ 0.05, a change of `source`, or an override. Sent at once with `transition_ms` 200.
  - **Drift:** everything else, sent at most once per 3 s with `transition_ms` 1000, and only beyond the deadband (0.015 brightness or 0.005 warm alpha).
  - The constants are in `room.yaml` `mask:`.
- **MaskArbiter:** one writer per screen. The setting `mask.source` is `sensor` or `flask`: `flask` until the relay's `room.json` is deployed (step 3), then `sensor`. Only the active source calls `ScreenStore.set_mask`. `lightmask` itself blends to the solar curve when the sensor goes stale (10 s, ramped over 30 s), so there is no third writer and nothing alternates. A change of source is sent once with `transition_ms` 2000.
- **FlaskMaskSource** fetches only for screens whose `mask.source` is `flask`. Each fetch advances Flask's shared, read-driven EMA, which the legacy TV also depends on. While any screen still runs the legacy page, the cap is therefore **≤1 fetch per 3 s per screen**: the ctl screen's own page no longer polls, so the total read rate stays where today's two TVs put it. Once every screen is on ctl, the cap is ≤1/s.

**PolicyService and rollout settings.** These are two owners, merged into one `policy` message.
- **Page policy:** `room.yaml` defaults, plus modes, which set `page.<screen>.timed_reload` and later `page.<screen>.overlays_in_live` as ordinary room state (LIVE_V1 §3.1, ROOM_STATE_MODEL). `live_enabled` is `room.yaml` (LIVE_V1).
- **Rollout settings** (`mask_push`, `content_source`, `overlay_source`, `telemetry`, `safety_reload`, `reload_offset_s`, `mask.source`) are per-screen server settings.
  - `room.yaml` gives their first-start defaults.
  - They are persisted in `display/<screen>.json` and changed only by `PUT /api/v1/screens/{id}/settings` (admin token).
  - Modes never touch them, so a gallery→live→gallery change cannot silently revert a rollout in the middle of an evidence window.
  - A change takes effect at once, with no restart.
- Any change produces a full `policy` message. The applied value is read back from `kiosk.state` (LIVE_V1 §3.1).
- Turning `live_enabled` off runs the exit handshake first.

**ReloadManager** (the MVP covers build, operator and rollback reloads; heap-driven reloads are deferred, §8.2):
- **Build tracking:** it watches `dist/index.html` (mtime) and extracts the asset name, e.g. `index--tLZUHxv`. A glass page whose `hello.build` differs is stale. `unknown` (the page's bundle-name selector missed, `DisplayController.ts` `buildId()`), `dev` and a missing value are **never compared**, so a selector mismatch cannot cause a reload loop.
- **Smoke test, before any build reload.** The snapshotter's headless Chrome, in its own browser context, opens `http://127.0.0.1:5000/display/devtest?ctl=ws://127.0.0.1:<port>`. That is a viewer: no key, and it never becomes glass. The page must say hello with `build == target` within 60 s. If it does not, nothing is reloaded and `room.build_smoke_failed` fires. This catches a bundle that throws at module evaluation, which would otherwise never mount React, never open a socket and never be reachable by a kill switch.
- **Stagger,** with `reloads.auto_on_build: true` (owner question Q5):
  - South first: `kiosk.reload{reason:"build", when:"art_idle", deadline_s:3600}`.
  - North goes only after south has said hello **on the target build** and acked its picture (or reported art idle) within 15 min of its reload.
  - Otherwise ReloadManager **halts**: `screen.build_rollout_halted`, and north is not touched.
- At most **one build reload per (screen, target build)**. If the hello after it does not show the target build, ReloadManager stops and alerts.
- **Kill switch:** `POST /api/v1/screens/{id}/ctl_off {ttl_h, reason}` is persisted. A connected ctl page gets `kiosk.reload{reason:"rollback", flags:{ctl_off_ttl_h}}`. Any ctl page that connects while the switch is active gets `welcome.ctl_off`.
- It retries commands as in §6.3, and never sends a reload to a screen in maintenance, except an operator `now`.

**Admin API** (LIVE_V1 §3 conventions; writes need the admin token):

| Call | Effect |
|---|---|
| `GET /api/v1/screens/{id}/glass` | Desired vs applied, drift, budget, page and standby info, active alerts (§4.5) |
| `PUT /api/v1/screens/{id}/settings` `{…patch}` | Rollout settings → `202 {rev}` |
| `POST /api/v1/screens/{id}/reload` `{when, reason, deadline_s?}` | `202 {cmd_id}` |
| `POST /api/v1/screens/{id}/ctl_off` `{ttl_h, reason}` / `DELETE` | Kill switch on / off |
| `POST /api/v1/screens/{id}/maintenance` `{on, ttl_s, reason}` | `202 {cmd_id}` |
| `POST /api/v1/screens/{id}/glass` `{page_load_id}` | Force the driven glass (G6) |
| `DELETE /api/v1/screens/{id}/overlays` | Clears the screen's overlay set |
| `GET /api/v1/screens/{id}/log?n=200` | Tail of the change and cause log |

### 4.5 GlassMonitor, health and alerts

GlassMonitor runs once a second over each ScreenStore record.

`GET /api/v1/screens/{id}/glass`:
```json
{"screen":"north-screen",
 "page":{"connected":true,"reachable":true,"role":"kiosk","kiosk":"north","page_load_id":"p-3c9e1a2b","build":"index-Bq3f9x2K",
         "build_current":true,"uptime_s":40211,"minor":4,"standby":0},
 "desired":{"policy_rev":5,"state":{"rev":120,"key":"mp4:1760393210:41877721"},"mask":{"rev":90312,"brightness":0.44},"overlays":[]},
 "applied":{"policy_rev":5,"state_rev":120,"mask_rev":90312,"base_key":"mp4:1760393210:41877721","mask_drawn":{"brightness":0.44},
            "layer":"art","last_report_s":2.1},
 "drift":[],
 "latency":{"publish_to_start_ms_p95":480,"publish_to_visible_ms_p95":3400,"mask_report_to_commit_ms_p95":41,"rtt_ms":7},
 "budget":{"heap_mb":71,"heap_precise":true,"long_tasks_per_min":0.1,"dom_nodes":412,"scripts":9,"anim_fraction_10m":0.04},
 "alerts":[]}
```

| Alert key | Fires when | Not when |
|---|---|---|
| `screen.page_offline` | No driven glass for 60 s | maintenance on |
| `screen.kiosk_stale` | Connected, no `kiosk.state` for 20 s | — |
| `screen.duplicate_glass` | A standby exists (G4) | lab on devtest; the build smoke test (a viewer) |
| `screen.state_drift` | Applied key differs from desired for 30 s, or `ack state failed` twice | live, frozen, `deferred` |
| `screen.mask_drift` | Drawn mask outside the deadband of desired for 10 s | live, frozen, `mask_push` off, page flags `nomask`/`nocolour` |
| `screen.long_tasks` | > 6 long tasks/min for 10 min | — |
| `screen.reload_storm` | > 3 page loads in 1 h | — |
| `screen.build_stale` | Page build ≠ `dist` build for 24 h | build not comparable |
| `screen.build_rollout_halted` / `room.build_smoke_failed` | §4.4 | — |
| `screen.slow_client` | 3 closes with 4008 in 1 h | — |
| `sensor.stale` | No reading for 60 s | `held` |
| `bridge.flask_ws_down` | :8765 listener down 120 s | — |
| `room.loop_lag` | Loop lag p99 > 100 ms over 1 min | — |
| `room.instance_conflict` | Another instance holds the state dir lock, or foreign-owned stream sessions exist for our screens | — |

- The JS heap is reported, not alerted on. Leak evidence comes from process memory in soaks (§9.4), because `usedJSHeapSize` excludes MSE buffers, decoders, decoded images and the GPU process. A heap alert can be added once `heap_precise` is true on the kiosk and a baseline exists.
- **AlertSink:** Siren (`utils/alerts`) runs inside Flask today. Its dispatcher imports the repo's `config.py`. Day 1 checks whether the sm-room venv can import it with the Gmail channel's dependencies.
  - If it can, room_service calls `alert()` in-process.
  - If not, alerts appear in `/healthz`, `/api/v1/status` and the Room page, and deadman gets a second probe of room_service's `/healthz` (a workstation change, owner OK).
- `/healthz` grows `{"ok", "version", "epoch", "instance", "loop_lag_ms_p99", "screens": {id: {"glass": bool, "alerts": n}}}`.
- **"On glass" means composited by Chrome.** Samsung IP Control readings (picture mode, power) can sit beside the glass view later; that is out of this layer's scope.

### 4.6 Persistence

All files live in the state dir (`~/.local/state/screen-machine/room/` by default) and are written atomically (temp file plus `os.replace`, as `util.atomic_write_json` does) from the executor.

| File | Contents | Writes |
|---|---|---|
| `room.lock` | `flock` held for the life of the instance (§4.7) | at start |
| `screens.json` (exists) | Live desired state, as built | as built |
| `display/<screen>.json` | Rollout settings, `state` (rev, base), overlay set with wall-clock expiry, maintenance, kill switch, last mask, lightmask filter state, rev counters | ≤1 per 2 s when dirty; mask ≤1 per 30 s |
| `logs/<screen>.jsonl` | Change log with causes | append; rotate at 10 MB × 3 |
| `~/.config/screen-machine/room_sensors.json` | Sensor keys (LIVE_V1) | owner/helper |
| `~/.config/screen-machine/room_page_keys.json` | Per-screen page keys and the lab key (as built: `page_keys_file`) | owner/helper |
| `~/.config/screen-machine/room_bridge.token` | Bridge token (only with the Flask hook) | created on first start |

Key files are mode 0600 and never printed (LIVE_V1 §0; as built, keys are added to the log redaction set).

### 4.7 Restart behaviour and single instance

1. **Lock:** take an exclusive `flock` on `<state_dir>/room.lock`. If another process holds it, exit non-zero with a clear message. A dev run while production runs must therefore pass its own `--state-dir` (the flag exists).
2. Load persisted records; choose a new epoch.
3. Start the bridges. ContentWatcher's first sweep runs before the listener opens, so the first `welcome` carries the current picture.
4. SensorService warm-starts its `lightmask` filters from persisted state. Its first push happens only after a fresh reading or fetch, and `welcome.mask` is null until then, so pages hold what they draw.
5. Accept page sockets. `welcome` is sent at once, built from persisted and warm state; it is never held.
6. **Adoption** (LIVE_V1, with two changes):
   - **Owner tags:** every session room_service creates carries `owner: <instance>` (`service.instance`, default `room-<port>`, so production is `room-8700`). Adoption, orphan deletion and the "unknown screen" deletion touch **only sessions with our owner**. Today, any instance deletes every session it does not keep, including those of screens it does not know (`screens.py` `_reconcile_sessions`, `_finish_adoption`), so a dev run with default config would delete production's live session. Foreign sessions are listed in `/healthz` and raise `room.instance_conflict`. This needs the LIVE_V1 §1.2 addition in §8.6.
   - **Stale fid order:** a persisted freeze is released only **after** adoption has resolved. If the adopted session is exiting or held, `live.abort` goes first and `unfreeze` after it, so the abort cut lands on the frozen art stack and not on queued changes. (In-flight `on_page_hello` releases it at the first hello.)
7. **Graceful stop:** close every page socket with 4012. Pages reconnect after 0.5–3 s of jitter.
8. **Production supervision:** `scripts/ctl` runs room_service in the GNU screen session `sm-room` (port 8700, arguments in `deploy/sm-room.env`; stop is SIGTERM, then a process-group kill after 15 s). It's started as `python -m room_service`, so `start.sh`'s `pkill` can't match it (AV_PROGRAMME §9). A restart takes a few seconds. That is well inside the page's 30 s hold before degraded mode, so a restart is invisible on glass.

---

## 5. Page architecture

### 5.1 Modules and the split

**Two component trees, chosen at the route** (`DisplayPageRoute`, `index.tsx`):
- `?artonly` → `ArtOnlyPage` (as built).
- A valid `?ctl=` (not `off`), with no active `ctl_off_until` in the boot cache → **`CtlDisplayPage`**, a new component tree.
- Otherwise → **`LegacyDisplayPage`**: git HEAD's page plus only the owner-approved P0.5 fixes (Q8). This file is frozen.

In the in-flight tree, both paths are one 486-line component with `ctl ?` branches. React hooks cannot be called conditionally, so the ctl page always runs the legacy hooks (`index.tsx:71-73` `useBaseFileName`/`useFilePolling`, `:128` `useOverlayWebSocket`, `:130` `useMask`, `:230-239` the script re-append effect). A leak or crash in them survives the kill switch, which would "roll back" to the same code. **`CtlDisplayPage` imports none of the legacy hooks.** Its degraded fallbacks are controller classes. The kill switch then really swaps code.

| Module | Job | Status |
|---|---|---|
| `CtlDisplayPage.tsx` | The ctl tree: art stack, live layer, controls panel | New (split out of `index.tsx`) |
| `control/ControlClient.ts` | One socket: connect, hello, liveness, jittered reconnect, `online`, close-code table (§3.7) | Built; v1.1: proto/have/prev/boot, 4009/4401/4404 handling, `hb.t` echo |
| `control/DisplayController.ts` | The brain outside React: topic reducers, commands and acks, the critical-ack queue and ended-session set, `kiosk.state`, `log`, `document.title` status | Built; v1.1–1.4 handlers |
| `control/store.ts` | Coarse state for React (layer, frozen, overlay list, base) | Built |
| `control/Budget.ts` | Long tasks, heap (with `heap_precise`), DOM/script/video/decoder counts, render and message counters, compositor animation time | New |
| `control/ReloadPolicy.ts`, `control/BootCache.ts` | The only code that reloads (§5.7); the boot cache | New (absorbs `canReloadNow` and in-flight `ReloadScheduler.ts`) |
| `control/MaskPoller.ts`, `art/FilePoller.ts` | Degraded fallbacks (§5.4) | MaskPoller built; FilePoller new (seeded, 2 matching reads) |
| `art/ContentController.ts` | Picture swaps: decode before fade, through-black, `fallback_url`, last good base, clip rules under live, acks | New |
| `art/OverlayHost.ts` | Overlays by id in shadow roots; scripts once per version with a scoped `document` and scoped timers; cap 3; freeze and live behaviour | New (replaces `index.tsx:144-153` and `useOverlayWebSocket` on the ctl page) |
| `art/MaskController.ts` | Compositor-only mask through refs in rAF; idle-layer rule | Built; must move to opacity-only (§8.1) |
| `art/ArtMedia.tsx`, `art/ClipCycle.ts` | Still and clip rendering; generation-scoped timers | Built; first-frame extraction to change (§5.6 L4) |
| `live/*` | LiveController, MseLivePlayer, LiveLayer (LIVE_V1 rules 4–8) | Built; two drifts (§8.1) |
| `common/LayerBoundary.tsx`, `hooks/useBlackBody.ts` | Error boundaries; black body | Built |
| `index.html` (repo root) inline script | Black at once on `/display/*`; boot watchdog (§5.7) | New (also RP P0.5 step 4) |
| `hooks/useFilePolling.ts`, `useMask.ts`, `useOverlayWebSocket.ts` | **Legacy page only** | Need P0.5 fixes (guards, timeouts, html filter) |

- **Same-origin requests only.** Every Flask request from the ctl tree (`/api/health`, `/api/<id>/mask`, `/output/…`) is a relative URL against the page's own origin. The ctl tree never uses `VITE_API_URL` or `VITE_WS_HOST`: the production build bakes `http://95.141.21.170:5000/api` into the bundle, which would keep the TVs on the WAN hairpin and on a different origin from the document they reload.
- `window.__smBooted = true` is set by both trees after their first commit, for the boot watchdog.

### 5.2 State outside React render

- DisplayController owns the state. Socket messages never call `setState` directly.
- Hot paths write through refs: the mask in rAF (at most one commit per frame), live cuts in rVFC callbacks, picture swaps through ContentController.
- React subscribes through `useSyncExternalStore` (as built) to coarse state only: layer, frozen, overlay list, the current base element. These change a few times an hour, overlays a few times a minute.
- Budget: **0 React renders per `mask.set`, `hb`, `policy` (unchanged fields) or `kiosk.state`**; about 1 render per picture change or overlay change.

### 5.3 Layer stack

As LIVE_V1 rule 4 and DCL §2.2, bottom to top:
- `html`/`body`: black.
- **LiveLayer** (z 1): the MSE `<video>`, created imperatively.
- **ArtStack** (z 2, opaque black), holding:
  - the base (still or clip);
  - brightness (z 5): static black, `opacity = 1 − brightness`;
  - warm (z 6): multiply, `opacity = warm_alpha`;
  - overlays (z 9999), each in its own shadow root;
  - controls panel.

Additions:
- **Guard CSS** (built in the in-flight `index.tsx`): `[data-sm-art][data-hidden], [data-sm-art][data-hidden] * { visibility: hidden !important; }`, so one attribute flip hides the stack even if a descendant sets `visibility: visible`. Keep it, and keep the contract test that everything outside the live layer computes to hidden while live (DCL §2.2).
- **Idle mask layers cost nothing, judged perceptually:**
  - The warm layer gets `display: none` once `warm_alpha × (1 − min(r,g,b)/255) < 1/255` has held for 2 s, or when `nocolour` is set. That means it would change no pixel by a full code value. `warm_alpha = 0.07·(1−intensity)^β` is above 0 except at full brightness (`display_utils.py:189-190`), so an "alpha = 0" rule would almost never fire.
  - The brightness layer goes the same way at brightness ≥ 0.998.
  - A hysteresis of 2 s prevents toggling.
  - With no full-screen multiply over it, a clip can use a hardware overlay plane.
  - The art-only page runs the same code, so anchors still match glass. The renderer applies no mask (live is full intensity), so nothing else needs the rule.
- **While live**, overlays and the panel are unmounted and the art stack is hidden in one property change (LIVE_V1 rules 4–5). A clip base stays held and paused, and no new clip decoder starts (§5.6 L4).

### 5.4 Degraded mode

One switch, at 30 s, instead of v3.0's four tiers:

| Condition | Mask | Picture | Overlays | Reloads |
|---|---|---|---|---|
| Socket up, sources `push` | Push | Push | Push | Policy and commands |
| Socket down < 30 s | Hold | Hold | Current ones run their own timers | Backstop as always |
| **Down ≥ 30 s** | Flask `/mask` every 3 s, 4 s timeout, in-flight guard, hold-last. The first value is ramped in over 10 s, so a stale Flask value (§8.3) cannot cause a step | HEAD poll every 2 s, seeded with the drawn picture's key. A change needs 2 matching reads and goes through ContentController (decode first, through black) | No new ones. **:8765 is not reopened** (overlays are not critical; P7) | — |
| Down ≥ 30 min, or no `welcome` since load | Same | Same | Same | Timed reloads with the policy defaults, health-gated |
| Flask down, room_service up | Push continues (SensorService needs no Flask) | New pictures retry loading; the current one stays | Bridge waits | Health gate defers |
| `?ctl` absent or `off`, `ctl_off_until` active, or the static kill flag set | **The legacy page** | | | |

- **Why 30 s:** a supervised room_service restart (graceful 4012, `RestartSec=2`, imports, state load, first sweep, the page's own backoff) can exceed 5 s. At LIVE_V1's 5 s the page would fetch Flask's `/mask` mid-restart, and that value comes from the last lux Flask received, possibly days old (`display_utils.py:173-176`). The TV would fade toward it and then back.
- **The seeded HEAD poll** removes the two through-black dips that v3.0 caused on every outage over 10 s: one when the poll first adopted a `?t=` URL, and one when the welcome brought back the immutable URL (`useFilePolling.ts:48,83` starts from `lastModifiedRef = 0`).
- **Static kill flag:** at boot, and every 10 min while there has been no `welcome` since load, the ctl page fetches `/output/_ctl/<screen>.json` (a 404 is normal). `{"ctl_off_h": N}` sets `ctl_off_until` and reloads into the legacy page (gated). It needs no Flask code: an operator writes the file into `output/_ctl/` on the workstation. It is the rollback for a page that cannot reach room_service at all (§8.2).
- **`document.title`** shows the control-plane state, for example `ctl up`, `ctl down 12m (1006×9)`, `ctl rejected 4401` or `ctl unknown screen`. `check-status.sh` already prints page titles from CDP, so this is the one way to see a Local Network Access or Origin block from outside without new code on the box.
- **Removed from v3.0:** the offline solar schedule (it helped only when both servers are down for over 10 min, where hold-last suffices) and the page-side heap ceiling (the metric cannot support it; §5.5).
- DCL's HTTP twin (`GET /state`, `POST /kiosks/state`) is dropped on purpose. It made sense when the hub was a thread inside Flask. room_service serves HTTP and WebSocket from one process, so when one is down the other usually is too.

### 5.5 Resource budget on the N95

Each Chrome is one tab on a 4K panel; two share an Intel N95 with 7.5 GiB and single-channel memory. These limits are part of the contract and are checked in §9.

| Item | Limit |
|---|---|
| Sockets | ≤1 control + ≤1 media. 0 to :8765 once `overlay_source=push` |
| Inbound control traffic | ≤11 msgs/s peak in art (snaps), typically ≤1/s in steady light; ≤3/s while live; average ≤20 KB/s |
| Mask commits | ≤1 per animation frame; 0 style writes when the drawn value is unchanged |
| **Compositor animating** | `anim_ms_1m` ≤20 % of wall time averaged over 10 min in steady daylight, and no more than today's page (a 1 s fade per 3 s poll, about 33 %) during a cloud sweep. Both TVs follow one sensor group and animate together, so this is the cost that matters, more than message counts |
| Mask CSS | `opacity` only, with `will-change: opacity`; `warm_hex` changes without a transition, at most every 5 s (LIVE_V1 rule 11) |
| React renders | 0 per message type listed in §5.2; ≤60 per minute in total |
| Timers | Intervals only for ControlClient's 500 ms tick, `kiosk.state` (5 s) and Budget (30 s), and, only while a live session is mounted, MseLivePlayer's 250 ms watch (the stall check folds into it) and its 1 s stats. Everything else is a generation-scoped timeout |
| Long tasks (> 50 ms) | Target 0 in steady state; alert above 6/min |
| Memory | Chrome runs with **`--enable-precise-memory-info`** (§8.4). Without it, `usedJSHeapSize` is refreshed at most every 20 min and rounded into 100 buckets between 10 MB and 4 GB (about 6 % apart; Chromium `memory_info.cc`), so it cannot show a trend. JS heap flat within ±10 % over 6 h after warm-up. **The leak evidence is renderer and GPU process RSS** (§9.4), because the JS heap excludes MSE buffers, decoders, decoded 4K images and GPU memory. No page-side heap ceiling |
| DOM | ≤3 mounted overlays; overlay scripts and timers removed with them |
| Decoders | **≤1 *decoding* `<video>` while a live session is mounted.** A clip base held paused under live may remain mounted; §9.5 3(d) decides whether it must go. No first-frame extraction and no new clip decoder from `live.attach` to the end of teardown. In art, a clip→clip change releases the old decoder at the black moment before loading the new one (as today) |
| Media | No main-thread `toDataURL` (L4); decoders freed on every swap (built) |
| Live appends | A batch of one chunk is appended as its `subarray` with no copy; copy only when coalescing a backlog (LIVE_V1 rule 11) |
| Console and event ring | No per-message logging on kiosk and lab roles. The event ring (400 entries, readable over CDP as `window.__smCtl.events`) holds commands, acks, cuts, socket changes and errors only. `mask.set` and `hb` are counted, not ringed |
| `kiosk.state` | ≤2 KB every 5 s; sampling ≤2 ms per minute (DOM counts every 60 s, heap every 5 s) |
| Bundle | Today one 3.36 MB chunk parsed at every load. Not this layer's fix (RP T7); reloads become rare instead |

### 5.6 Memory-leak defences (why the 4-hourly reloads exist)

| # | Leak today | Fix | Where | Status |
|---|---|---|---|---|
| L1 | Overlay scripts re-appended to `body` on every overlay change, never removed (`index.tsx:144-153`); the spinner's `setInterval` multiplies | OverlayHost mounts each overlay in its **own shadow root**, so a template's `<style>`, its global `body` font rule and its Google Fonts `<link>` stay inside it. Scripts run once per (id, version), **after** the HTML is attached, through `new Function` with a scoped `document` shim (`getElementById`, `querySelector(All)` on the shadow root) and scoped `setTimeout`/`setInterval`/`requestAnimationFrame`. Two concurrent job overlays can then never update each other's `progress_ring`, which today's `document.getElementById` calls would do (`overlay_generating.html.j2:106-108`, `overlay_spinner.html.j2:27`). On removal or a new version, it clears the timers and the shadow content | `art/OverlayHost.ts` | New. RP P0.5 step 2; pre-authorised as a leak fix |
| L2 | Phantom overlays from lux, progress and transcription frames (`useOverlayWebSocket.ts:64-101`) | The ctl tree has no :8765 socket once `overlay_source=push`. The legacy hook ignores frames without `html` | hooks | New. RP P0.5 step 1; pre-authorised |
| L3 | Orphan clip-loop timers on detached `<video>` elements (`MediaDisplay.tsx:213-240`) | Generation-scoped timers | `art/ClipCycle.ts` | Built (ctl path) |
| L4 | A second, never-freed decoder and a main-thread 4K `toDataURL` per clip; in-flight, a clip published while live also gets a held decoder plus a transient third one (`index.tsx:293-305`, `ArtMedia.tsx:144-179,301-309`) | Decoder freed (built). The first frame for the end-of-loop cross-fade is taken only while art is idle and no live session is mounted, by `createImageBitmap(video, {resizeWidth:1920, resizeHeight:1080})` drawn into a `bitmaprenderer` canvas: no data URL, no main-thread JPEG encode. From `live.attach` to the end of teardown, a clip published to the screen is applied as its `poster_url` if the server gives one (later); otherwise it is **deferred** (`ack state deferred`) and applied through black once art is visible again. The exit target then shows the base that was on the art stack, which is what the exit snapshot captures anyway | `art/ArtMedia.tsx`, `ContentController.ts` | Partial |
| L5 | Zombie reconnect with stale closures (`useOverlayWebSocket.ts:48-53,110-113`) | One ControlClient whose `drop()` detaches handlers | `control/ControlClient.ts` | Built |
| L6 | Console retains every frame, including overlay HTML | No per-message logging on glass roles | all | Partial |
| L7 | MSE and VA-API sessions | LIVE_V1 rule 8 teardown; proven by 50 attach/detach cycles on the N95 (§9.4) | `live/*` | Built; unproven |
| L8 | (in-flight) an event-ring object and an array splice per `mask.set` (`DisplayController.ts`, `event()`), which also evicts the cut events the ring exists for within a minute at 10/s | High-rate kinds are counted, not ringed (§5.5) | `control/DisplayController.ts` | New |

**Structural defences:**
- Every timer and listener a module creates is registered with a disposer scope (per layer, per picture generation, per overlay version) and released with it.
- All collections are bounded (event ring, handled `cmd_id`s, overlay map, critical-ack queue, ended-session set).
- No React state per message.
- Leak canaries in `kiosk.state.budget`: heap, DOM nodes, scripts, videos, decoders.
- The reload policy is the last resort, not the plan.

### 5.7 Reload policy (health-gated)

**ReloadPolicy is the only code that calls `location.reload()`.**

| Reason | Who decides | When |
|---|---|---|
| `timed` | The page, while `policy.timed_reload` is true, or after 30 min with the socket down, or with no `welcome` since load (LIVE_V1 rule 9) | Today's schedule, shifted by `reload_offset_s`; waits up to 10 min for art idle |
| `safety` | The page, while `timed_reload` is false and `safety_reload` is set | Daily at that time (default 04:00) plus `reload_offset_s`; waits up to 2 h for art idle, else skips that day |
| `build`, `operator`, `recover` | room_service (`kiosk.reload`) | `art_idle` or `now` |
| `rollback` | room_service (`kiosk.reload` flags or `welcome.ctl_off`), the static kill flag, or the page itself (boundary trips) | `now`, subject to the backstop and health gates |
| `boundary` | The page: the page-level error boundary tripped | After 30 s, then 2, 5 and 10 min. The counter `{count, first_at}` is in the boot cache, so the backoff survives the reload. After **3 trips within 1 h**, the page sets `ctl_off_until` = now + 6 h and boots the legacy page with no warm boot |

**Gates.** One list, and every exception is named:
- **Backstop:** LIVE_V1 rule 9, for every reason.
- **Health:** the same-origin `GET /api/health` must answer within 2 s, for every reason, because the document comes from Flask. If it does not, retry every minute.
- **Rate:** ≥10 min since the last reload (boot cache), for every reason except `rollback` and the first `boundary` reload.
- **Maintenance:** no reloads except an operator `now` and `rollback`.

**Commit.** A reload that is waiting (for art idle, or in its fade-out) is cancelled by an arriving freeze: the fade reverses, and the command is acked `deferred{frozen}`. At commit the page:
1. writes the boot cache synchronously;
2. closes the control socket with 1001 `reloading`;
3. calls `location.reload()`.

Nothing can then freeze a page that is already unloading. room_service sees the close as the page going away, which it never counts as an entry failure (`PageGone`).

**Making a reload close to invisible:**
1. **Hide it in a picture change.** With `art_idle`, ReloadPolicy waits for ContentController's black moment (fade-out finished, the incoming picture decoded) and commits there, writing the **incoming** base and `boot_mode: "fade_in"` to the cache. The new page starts black, decodes the cached incoming picture and fades it in over 1 s under the cached mask. On glass this is one through-black change with a longer black hold (bundle parse, about 1–3 s on the N95). Past `deadline_s` it does a standalone fade-out, reload and fade-in.
2. **Boot modes:**

   | Mode | When | What the page draws |
   |---|---|---|
   | `fade_in` | after a reload committed in the black | black, then a 1 s fade-in of the cached incoming base |
   | `instant` | crash, Chrome respawn, operator `now`, and a reboot with a persistent profile | the cached base and mask at once, before any network |
   | `cold` | no cache: a first boot, or a profile that `/tmp` lost at reboot | black until the first mask (`welcome` or poll), then a 1 s fade-in; if none comes within 10 s, 0.3 neutral (Q7). Today's page also boots black for its first second, so there is no bright flash at night |
   | `legacy_return` | the first legacy boot after `ctl_off` | as today's page |

   - When the `welcome` or the first poll names a base whose key differs from the drawn one, the change runs **through black**, never as a cut. A warm boot that missed a publish therefore never jump-cuts between two artworks.
   - If the cached URL fails to load, the page tries the cached `fallback_url`, and otherwise holds black until the welcome.
3. **The boot cache** (`localStorage` `sm.display.<screen>`, wrapped in try/catch) holds `{page_load_id, base{key,kind,url,fallback_url}, mask, policy, boot_mode, reload{last_at, reason, cmd_id}, boundary{count, first_at}, ctl_off_until}`.
   - **Writes:** at boot (the page load id, so a crash still leaves it for G3); on every applied base or policy change (at most 1 per 10 s); the mask at most every 30 s; always on `pagehide` and synchronously at commit.
   - **It is persistent only with the persistent profile in §8.4.** Today both Chromes run with `--user-data-dir=/tmp/chrome-north|south`, which Ubuntu clears at boot. Without the move, a reboot (for example a power cut) loses warm boot, `prev`, the rate gate, the boundary counter and `ctl_off_until`. The page then cold-boots black, G1/G5 decide the glass, and the server re-issues an active kill switch in `welcome.ctl_off`.
4. **Boot watchdog.** An inline, non-module script in `index.html`, on `/display/*` paths without `artonly`:
   - it sets the html background to black at once (RP P0.5 step 4);
   - if `window.__smBooted` is not set within 30 s, it reloads with backoff 1, 2, 5, 10 and 30 min (counter in `sessionStorage`), checking `/api/health` first.

   A bundle that throws at module evaluation never mounts React, so none of the machinery above runs. The watchdog is what lets such a page recover once `dist` is reverted to `dist_prev` on the workstation, with no SSH. It also protects the legacy TV, which would otherwise pick up a broken build at its next 4-hourly reload and stay black. The smoke test in §4.4 should stop a broken build first.
5. **Stagger:** ReloadManager sends build commands to one screen at a time and halts on a failed TV (§4.4).

### 5.8 Error boundaries

| Boundary | Fallback | Then |
|---|---|---|
| Page | Black full-screen div (as built) | `boundary` reload (§5.7), counted; self-rollback after 3 trips/h |
| Art media | The last good `<img>`, kept in a module variable, plus the current mask. A base that fails to load is retried once at `fallback_url` at once | `log`; ContentController retries at 2, 5, 15 s |
| One overlay | That overlay is removed | `log`; others unaffected |
| Live layer | Treated as a stall (LIVE_V1 rule 12): hard cut to art | `ack art_visible{reason:"error"}` (critical) |
| Controls panel | Panel closed | `log` |

Global `window.onerror` and `unhandledrejection` handlers forward to `log` (rate-limited) and never reload by themselves.

---

## 6. Reliability semantics

### 6.1 Ordering
- Per connection, commands are strictly FIFO and go before state, except that a pending `policy` goes before the next command (§4.3).
- Per topic, revs are monotonic. The overlay set is one topic, so overlays have no ordering among themselves.
- Cross-topic couplings are explicit: policy before command, `after_state_rev`, and the state ack before a freeze. There is no other cross-topic guarantee, and the page reports glass truth.
- WebSocket over TCP does not reorder. Duplicates can come only from server retries and critical-ack re-sends, and those are idempotent (§6.2).

### 6.2 Idempotency
- **State:** applying the same content key twice is a no-op.
- **Commands:**
  - The page keeps the last 64 handled `cmd_id`s; a repeat is acked `duplicate` and not re-run.
  - A reload's `cmd_id` survives the reload in the boot cache and comes back in `hello.prev`.
  - `fid` and `session` are idempotent per LIVE_V1 (as built).
- **Critical acks:** the server keeps the last 16 `(kind, fid|session)` per screen and ignores repeats.
- **Overlays:** keyed by id and version.

### 6.3 Commands: at least once
- **On one socket:** if there is no ack within 2 s, resend; at most 3 tries.
- **On reconnect:** pending commands are *re-evaluated*, not replayed blindly.
  - A reload is resent only if it is still wanted and `hello.prev.cmd_id` shows it has not run.
  - A command past its deadline is dropped and logged.
- **Freeze and live** keep LIVE_V1's handshake semantics: a timeout aborts, there is no retry, and the screen stays in art. room_service's backoff decides the next attempt, with two changes:
  - ScreenController freezes only a **reachable** page. An unreachable one is "waiting for the page", as if absent.
  - A freeze-ack timeout on a page that went silent during the wait counts as **transient**, not as an entry failure.

  Without these, a dead powerline path left the dead socket in place for the 15 s drop, and three freeze timeouts at about t = 0, 5.5 and 13.5 s put the screen in `failed` (`backoff_s [2,5,15,30]`, `max_entry_failures 3`; `screens.py` `_entry`). The mode's live request was then lost when the link healed.

### 6.4 Latest-value state
- A slow client gets fewer intermediate masks, never a stale final value.
- The snapshot on every connect is authoritative (LIVE_V1 §2.2a rule 2).

### 6.5 Reconnects and storms
- **Page:** full-jitter backoff; reacts per close code (§3.7); reconnects at once on `online`.
- **After a room_service restart:** at most six clients (two TVs, devtest, a few viewers) reconnect within 0.5–3 s of each other. Each costs one `welcome` parse (≤128 KB, typically under 30 KB) and changes nothing on glass, because content keys match.
- **Server:** accepts bursts; `welcome` is built from memory and never awaits I/O.

### 6.6 Clock-free design

| Timing | Whose clock | Crosses hosts? |
|---|---|---|
| Rev, epoch | Counters | No clock |
| `remaining_ms`, overlay expiry, rate limits, staleness, command deadlines | Server monotonic | No: sent as a duration |
| Page liveness, backoff, reload rate, boundary backoff | Page monotonic and page wall | No |
| Timed and safety reload hours | Page wall (as today) | No |
| Solar curve | Server wall | No: the page gets values |
| Picture keys | Server file mtime / Flask `Last-Modified` of the same file | Same host's filesystem; compared as identity, not as time |
| RTT | Server monotonic via `hb.t` echo minus `held_ms` | Echoed opaque, never compared |
| `ingest.lux.mono_ms` | Relay monotonic, age within one connection only | No (LIVE_V1 §2.2b) |
| Live pts | Stream time (LIVE_V1 §0) | No wall clock |

### 6.7 Failure table

| Failure | Detected by, within | What the TV shows | Recovery | Alert |
|---|---|---|---|---|
| **Network blip** (< 6 s) | Usually nothing; else the page watchdog at 6 s | Unchanged | Reconnect (G2); snapshot; nothing remounts | — |
| **Powerline path dead, link up** (KIOSK notes §5 "Known gap") | Page 6 s; server: unreachable at 3 s, dropped at 15 s | Picture and mask held. Live: stall cut to art after 2 s; `art_visible` queued | No freeze is sent to an unreachable page; nothing counts as an entry failure. Reconnect attempts back off. Polls time out instead of queueing. The box's Ethernet path watchdog (infra, recommended) fixes the cause | `screen.page_offline` at 60 s |
| **Failover to Wi-Fi** | Source IP changes, so all TCP connections reset; page detects at once or within 6 s | Art unchanged. **Live: exactly one stall cut to art** | Reconnect over Wi-Fi with the same `page_load_id` (G2). `have.live: null` with the session in `ended` → the server records the fallback and re-enters by backoff. No re-attach mid-scene. 4K60 needs about 30 Mbit/s, and Wi-Fi gives 15–20, so each re-entry fails cleanly at warm-up and the screen stays in art after 3 fallbacks in 10 min | `screen.slow_client` if it thrashes |
| **room_service restart** | Close 4012, or reset | Unchanged; live keeps playing (its media socket goes to stream_service) | Jittered reconnect; new epoch; welcome with `mask: null` until fresh; adoption (stale fid released after it, §4.7). Under 30 s, so no degraded switch | supervisor restart count |
| **room_service down for long** | Page 6 s | Held; after 30 s, mask from Flask (ramped over 10 s) and the seeded HEAD poll; timed reloads after 30 min | Automatic on return; nothing remounts (keys) | `screen.page_offline` (deadman's second probe if Siren is not in room_service) |
| **Second room_service on the same state dir or stream_service** | Lock; owner tags | Unchanged | The second instance refuses to start; foreign sessions are never adopted or deleted | `room.instance_conflict` |
| **Flask restart or deploy** | Page: polls and loads fail; bridge: listener drops | Unchanged: mask push continues, picture held, no black (fixes W3) | New pictures retry; the listener reconnects 1–30 s; reloads deferred by the health gate | `bridge.flask_ws_down` only if longer than 120 s |
| **:8765 thread dead** | Listener | Unchanged on ctl pages | Siren `ws.server_died` (exists); bridge reconnects | `bridge.flask_ws_down` |
| **Chrome crash and respawn** (`kiosk-wayland.sh`, about 10 s) | Old socket closes at once | sway's black background, then the cached picture and mask (`instant`) | The launcher starts Chrome only once the origin answers (§8.4). The new load's `prev` names the crashed one → successor (G3), driven at once; live desired → entry handshake | `screen.reload_storm` above 3/h |
| **Kiosk reboot** (power cut; up in about 40 s while WSL and Flask may take minutes) | — | sway black until the origin answers; then `instant` (persistent profile) or `cold` (black until the first mask) | Health-gated launch; `welcome.ctl_off` re-issued if active | `screen.page_offline` until it connects |
| **Origin unreachable at load** (a race past the launcher gate) | — | Chrome's net-error page, dark with `--force-dark-mode` | Chrome's own auto-reload of error pages is **unverified**; check once in an owner window. If it does not reload, the launcher's health gate covers respawns only | `screen.page_offline` |
| **Bundle fails at module evaluation** | Build smoke test (before rollout); boot watchdog (on glass) | Black (inline background) | Smoke test blocks the rollout. On glass: revert `dist` to `dist_prev`; the watchdog's next reload picks it up. Without the watchdog: SSH `restart-screen` | `room.build_smoke_failed`, `screen.page_offline` |
| **Published bucket file deleted** (30-day purge, manual delete) | ContentWatcher re-stat within 250 ms | Unchanged | `url` switches to `fallback_url` with the same key; a page that fails the immutable URL tries `fallback_url` at once | — |
| **Renderer hung, process alive** | No `kiosk.state` for 20 s; socket dropped at 15 s | Frozen frame | RP D13's KioskMonitor ladder (`restart-screen` ≤3/h over SSH keys, server side) when built (Q14). Until then: alert only | `screen.kiosk_stale`, then `screen.page_offline` |
| **Duplicate page** (laptop with a copied kiosk URL) | Second authenticated hello | Unchanged: the TV stays driven | The newcomer is standby (G4); the TV's own reloads hand over by G3; a standby is promoted only after 20 s with no successor | `screen.duplicate_glass` |
| **Stale page build** | `hello.build` ≠ `dist` build | Unchanged | Smoke test, then a `build` reload at art idle (if Q5 is yes), one TV at a time; a page without `kiosk.reload` support updates at its next timed reload | `screen.build_stale` at 24 h |
| **Chrome upgrade (136 → 155) or WAN IP change** | Socket opens fail | Unchanged (degraded) | LAN origin and Chrome hold before step 8 (§8.4). With the LAN origin, no request goes from a public to a private address, so Local Network Access does not apply. The origin allowlist is config, not code | `screen.page_offline`; `document.title` names the close code |
| **Slow client** | Outbox limits (256 KB behind, 5 s) | Unchanged, or a brief hold | Close 4008; reconnect; fresh snapshot. Others are never delayed | `screen.slow_client` |
| **Malformed message** | Validators on both sides | Unchanged | Dropped and counted (`bad_msgs`); wrong `v` → 4400; frame > 64 KiB → aiohttp closes 1009; clamps stop NaN or out-of-range values reaching CSS | In `/glass` counters |
| **Truncated or missing picture** | ContentController decode failure | Previous picture stays | `fallback_url`; retry 2, 5, 15 s; a newer rev supersedes; immutable URLs remove the cause | `screen.state_drift` |
| **Sensor dead** | SensorService (`lightmask` staleness) | Mask held, then the solar curve over 30 s (`blend`) | the kiosk's service manager restarts the relay | `sensor.stale` at 60 s |
| **Overlay storm** (generation progress) | Bridge coalescing | ≤3 overlays, ≤1 update per 5 s each | — | — |
| **Clock step** on any host | — | No effect (§6.6) | — | — |

---

## 7. Security

**Threat model:**
- LAN guests share the Wi-Fi.
- Several legacy ports are forwarded from the WAN today (:8765, :5000, :8000), but room_service's port must never be (AV_PROGRAMME D4; owner confirms on the router).
- All peers reach WSL as one portproxy IP, so source IPs prove nothing.

| Control | Rule |
|---|---|
| Reach | room_service is LAN-only: a LAN-scoped portproxy and firewall rule for 8700, never forwarded. The Room HTTP API is not exposed through ngrok (AV_PROGRAMME §5). |
| Origins | `/v1/screen/<id>` accepts a browser `Origin` only from config: `web_origins` plus `kiosk_origins` in `room.yaml`, not code. The kiosk origin is `http://192.168.1.205:8080` once step 8b moves the URL (§8.4). The WAN origin `http://95.141.21.170:8000` is listed only until then, so a WAN IP change cannot lock the TVs out. A missing `Origin` (a non-browser client) is allowed as `viewer`, or as glass only with a valid key (test clients). |
| Page keys | One key per screen plus one lab key, in `room_page_keys.json` (0600; as built). Compared in constant time; the server never keeps it in `hello` (as built). A kiosk or lab page without a valid key is closed with 4401 (as built). Key handling: |
| | **In the page:** taken from the URL fragment `#ctlkey=` (also `&ctlkey=`, LIVE_V1 §3), sent as `hello.key`, held in memory and `sessionStorage`, and removed from the visible URL by `history.replaceState` at boot. Never in the WebSocket URL. |
| | **On the box:** the key lives only in `/etc/default/kiosk-wayland` (0640 root:gjbm2), which `deploy-kiosk.sh` already keeps. The launcher builds the URL at launch (§8.4), so the key is never in a tracked file. A helper on the box writes it, so it never passes through chat. |
| | **In logs:** `check-status*.sh` redact `#…` from the page URLs they print. `remote-maintenance.sh` tees everything (including `ps` command lines) into `media-server/maintenance.log`, which is git-tracked and pushed to GitHub; it already holds about 500 lines with full Chrome command lines. That file is untracked or scrubbed, and the redaction is in place, **before any key is issued** (owner/RP; §8.4). |
| Role scoping | Kiosk/lab may send only `hello`, `ack`, `kiosk.state`, `log`, `hb`. Viewers only `hello` and `hb`; their other messages are ignored. Sensors only `hello` and `ingest.lux`. No client can send anything to another client. |
| Acks affect observability only… | …with one exception: LIVE_V1's `ack frozen` ArtState, which feeds the snapshotter. Therefore `base_url` is validated **by path only**: strip the query and fragment, require `^/output/[\w.-]+(/[\w.-]+)?$`, and join it only to the configured Flask origin. Absolute URLs are refused. This accepts both the push form and the in-flight poll form (`/output/<dest>.<ext>?t=<ms>`, `useFilePolling.ts:49,83`), so it can ship on day 1 without breaking devtest live, and a contract test proves it (§3.9). ffmpeg runs with `-protocol_whitelist file,http,tcp` and only against that origin. This closes the SSRF and local-file read in `snapshot.py` (in-flight: `urljoin` accepts any absolute URL). It is a security fix that changes no behaviour, so it falls under the owner's standing pre-authorisation. |
| Sensor | Per-sensor keys (LIVE_V1 §2.2b); 4401 on a mismatch; finite values only, clamped to 0–200 klx; `seq` must not go backwards (reset on each hello); ≤20 readings/s. |
| Bridge | Bearer token, 127.0.0.1 only, ≤20 requests/s, HTML ≤32 KB. |
| Overlay HTML | Accepted only from Flask: :8765 frames with `html`, which P0 strips from relayed frames, or the token-gated hook. Runs in a shadow root with a scoped `document` and scoped timers (§5.6 L1). It is trusted code from Flask's own templates and is never accepted from a page. |
| Rate limits | Inbound 50 msgs/s → 4029; `kiosk.state` ≤1/s (extras ignored); `log` ≤10/min; HTTP admin as LIVE_V1 §3. |
| Input clamps | Brightness and warm alpha 0–1; `warm_hex` `^#[0-9A-Fa-f]{6}$`; strings ≤256 chars except overlay HTML and `log.msg` (2 KB); ids `^[\w.:-]{1,64}$`; `page_load_id` and `cmd_id` formats checked. **The page clamps too:** it never applies NaN, and it rejects a base URL that is not a same-origin `/output/` path. |
| Legacy surface (not this layer; RP) | :8765 stays WAN-reachable and spoofable for legacy pages until RP P3 step 8 closes it. Once both TVs are on ctl with `overlay_source=push` and the relay is on ingest, nothing on the TVs depends on it. `maskon`/`maskoff`/`brightness-override` on :5000 are unauthenticated; RP recommends a token before fast push. |

---

## 8. Migration

### 8.1 What the in-flight build already implements

Re-checked 14 Oct 00:20–00:35, while the LIVE_V1 review-and-fix pass was editing `pages.py`, `screens.py`, `mask.py`, `config.py`, `DisplayController.ts`, `ControlClient.ts`, `ReloadScheduler.ts` and `protocol.ts`. **Re-take this table at build start.** "Drift" means built but different from LIVE_V1 or from this design.

| v3 element | LIVE_V1 | In-flight status |
|---|---|---|
| Socket `/v1/screen/<id>`, envelope v1, roles `kiosk`/`viewer`/`lab` | §2 | **Built** |
| `hello` → `welcome` snapshot (policy, mask, live) | §2.1 | **Built**; `welcome.policy` now from `c.policy()` |
| `hb` at 2 s idle; page 6 s watchdog; full-jitter reconnect; `online` | rule 1 | **Built**; server drop now 15 s |
| Outbox: latest-value `mask.set`/`policy`, never-dropped commands, 256 KB / 5 s, 4008 | §2.2a rule 1 | **Built (00:30).** **Drift:** `welcome` counts against the byte limit; no policy-before-command rule; no slots for state or overlays yet |
| Page keys (`room_page_keys.json`, `&ctlkey=` → `hello.key`, 4401) | §3 | **Built.** The fragment form `#ctlkey=` and `replaceState` are v3.1 |
| `mask.set` through refs in rAF; poll fallback; hold-last | rule 2 | **Built.** **Drift:** MaskController transitions `background-color` (rule 11); the initial mask is brightness 0; MaskPoller maps a missing `brightness` to 0 |
| Freeze, unfreeze, `ack frozen` | rule 3 | **Built.** **Drift:** settle cap 2.8 s against the server's 3 s, so a through-black change (≥3.1 s, `index.tsx` `onFadeOutComplete`) can be acked mid-fade; overlays are not dismissed before the ack |
| `live.attach/leave/abort`, cuts, stall, teardown | rules 4–8 | **Built** (page, room_service). **Drift:** `MseLivePlayer.pump()` copies every batch into a new `Uint8Array` (rule 11: views only); a separate 100 ms stall interval; no ended-session set, so a `welcome.live` after a stall cut re-attaches mid-scene |
| `policy` message; `policy.timed_reload`; `ReloadScheduler` | §2.1, rule 9 | **Built (00:30).** **Drift:** the policy lapses to timed reloads after only 5 s with the socket down (v3.1: 30 min) |
| `kiosk.state` every 5 s | §2.1, rule 11 | **Built** |
| Black body; per-layer error boundaries | rule 12 | **Built** (on both paths; see step 8a) |
| One glass per screen | §2.2a rule 3 | **Drift:** newest wins in `PageHub.glass()`, and `on_page_hello` runs for every glass hello, so a second glass hello mid-live orphans the session (`screens.py` ~499-533) |
| Restart adoption | §2.2a | **Drift:** deletes every unkept session, including other screens' (no owner check); releases a stale fid at the first hello, before adoption resolves |
| Inbound limits, role scoping, 4029 | §2.2a rule 4 | **Built** |
| Unknown screen | — | HTTP 404 before the upgrade (the page sees 1006); `CLOSE_UNKNOWN_SCREEN = 4404` is defined but unused |
| Interim mask fetch | §2.2b | **Drift:** a lux frame on :8765 triggers a fetch, with a budget of 10/s total (`config.py` `max_requests_per_s`) |
| Sensor ingest, server side | §2.2b | **Not built.** `lightmask/` is built and golden-tested; the relay side exists (calib-3) |
| Origin check on the page socket; `base_url` validation | (DCL §2.3) | **Not built** |
| Controlled page still runs the legacy hooks (file poll, :8765, legacy mask hook, script re-append) | (rule 11) | **Yes**: one component with `ctl ?` branches; removed by the split (§5.1) |
| Ctl page Flask requests | — | Through `Api` (`VITE_API_URL`, baked as the WAN `:5000`), not same origin |
| `buildId()` | — | Returns `unknown` when its selector misses |
| Event ring | — | One entry per `mask.set` |
| P0.5 phantom filter and once-per-id scripts | — | **Not done** on either path |
| Hold-last in the *default* `useMask` | — | **Done, uncommitted**: it changes production behaviour when deployed, so it needs the owner's yes (Q8) |
| Content push, overlay push, reload commands, glass, schema files | out of scope (§4) | v3 adds them |

### 8.2 Steps

**Sequencing with the in-flight build.**
- **V3 work starts after AV_PROGRAMME §7 Day 2's devtest integration (Thu 15).** Until then, the four LIVE_V1 builders own `room_service/pages.py`, `screens.py`, `mask.py`, `config.py`, `coordinator.py`, `snapshot.py` and `src/pages/DisplayPage/{index.tsx, control/*, art/*, live/*}`. V3 builders touch none of them before that hand-over, and each step below names its file owners.
- **AV Day 2's "then one TV"** runs the in-flight v1.0 ctl page through a **CDP `Page.navigate` trial** on that TV's port (9223 for south; `--allow-production`, owner OK). It is not persistent: a Chrome respawn returns the TV to its legacy URL, which is the trial's rollback. That trial does not wait for v3's kill switch or keys. Everything persistent waits for step 8b.
- Two builders (server and page), each followed by a review-and-fix pass, as AV_PROGRAMME §7 does.

| Step | Day | Owner (files) | What | Rollback |
|---|---|---|---|---|
| **0. Guard rails** | before 1 | server builder (`service.py`, `screens.py` adoption, `snapshot.py`) | InstanceLock and owner-tagged sessions (§4.7; needs stream_service's `owner` field, §8.6). `base_url` path-only validation and the ffmpeg whitelist (§7; pre-authorised). The vitest decision (§3.9). | Dev only: revert. |
| **1. Conform and fix** | 1 | server: `pages.py`, `screens.py`, `config.py`; page: `index.tsx` → `CtlDisplayPage.tsx`, `control/*`, `live/*`, `art/MaskController.ts` | **Server:** settings split and full `policy`; outbox rules (policy before command, `welcome` exempt); the 3 s reachability gate and the transient freeze timeout; 4404 after upgrade; the Flask fetch rule; mask ≤2/s live; G1–G7 in ScreenController; `have.live` and the critical-ack dedup; stale fid after adoption. **Page:** the split into `CtlDisplayPage` (same-origin URLs, no legacy hooks); `policy` with absent-field defaults; `kiosk.state` fields; opacity-only MaskController and the idle-layer rule; the boot cache and boot modes; overlay dismissal at freeze; the critical-ack queue and ended set; the 4.5 s settle; the live append and timer drifts; the event ring. | Dev only: revert. |
| **2. Scaffolding and security** | 2 | 2f (`schema/`); both builders | Schema, examples and contract tests (minor only, both directions); `#ctlkey=` and `replaceState`; Origin allowlist from config; lab on devtest only; `role` (standby) messages; `log`; `hb.t` echo; Budget (with `anim_ms_1m`); `document.title` status. | Screens without a configured key accept unauthenticated glass (dev only). |
| **3. Sensor ingest** | 2 | server (`sensor.py`; `mask.py` becomes FlaskMaskSource) | Wire `lightmask` (§4.4): `/v1/ingest/lux`, `LuxFilter`, `mask_for_dest`, `MaskGate(max_rate_hz=10)`, MaskShaper, MaskArbiter, the overrides poll, persistence. No new filter, mapping or golden work. The owner deploys the relay's `room.json` (Q11). | Setting `mask.source: flask` per screen; the relay keeps its legacy send. |
| **4. Picture push** | 3 | server (`content.py`); page (`art/ContentController.ts`, `art/FilePoller.ts`) | ContentWatcher (keys, immutable re-stat, `fallback_url`); `screen.state`; ContentController (decode first, through black, `fallback_url`, clip rules under live, `deferred`); the seeded FilePoller; the state-ack wait before a freeze; a 60 s safety HEAD poll counting `push_miss` during the evidence period. | Setting `content_source: poll`. |
| **5. Overlays** | 3 | server (`flask_ws.py`, `overlays.py`); page (`art/OverlayHost.ts`) | OverlayBridge (set, coalescing, 32 KB); `overlays.set` with snapshot; OverlayHost (shadow roots, scoped `document` and timers, cap 3, `after_state_rev`, restore after an aborted freeze); `overlay_source: push` closes the page's :8765 socket. **5b, optional:** native overlays through the Flask hook (§8.3). | Setting `overlay_source: legacy`. |
| **6. Glass, health, alerts** | 4 | server (`glass.py`, `alerts.py`, `api.py`); Room page builder | ScreenStore `applied`; GlassMonitor; `/glass`; AlertSink (Siren if importable); a Room page card. | Observability only; alerts can be muted per key. |
| **7. Reload policy** | 4 | page (`control/ReloadPolicy.ts`, `BootCache.ts`, root `index.html`); server (`reloads.py`) | ReloadPolicy (gates, commit, boundary self-rollback, safety reload); the boot watchdog; the static kill flag; ReloadManager (smoke test, halt, one reload per target, `unknown` not comparable); `kiosk.reload`, `kiosk.maintenance` and the persisted kill switch with `welcome.ctl_off`. | `timed_reload: true` (today's schedule); `reloads.auto_on_build: false`. |
| **8a. Page deploy** | 4–5 | owner, with screen-machine-12's review (AV_PROGRAMME D2) | See below. | `dist_prev`. |
| **8b. Turn on per screen** | then calendar | owner window | devtest through V1 with the N95 measurements (§9.5). South: first a CDP `Page.navigate` trial, then the persistent launcher change (§8.4) after the Chrome hold and with the LAN origin; 48 h. Then north. Per topic, in order: `mask_push` → `content_source` → `overlay_source` → `timed_reload: false` (the 04:00 safety reload stays for 30 days). | Per-topic settings; the kill-switch paths below; last resort the launcher URL back (box access). |
| **9. Decommission** | +7 days of evidence per screen | server | Remove the 60 s safety poll after 7 days of zero `push_miss`. Legacy polls remain only as degraded fallbacks. The relay's legacy send stops only once Flask's `compute_mask` treats old lux as stale (§8.3), because the degraded mask poll still reads Flask. Then RP can close WAN :8765. | Re-enable by setting. |

**Step 8a, the page deploy, is its own procedure.** One `dist/` serves both TVs and the operator SPA, so a build from this shared, dirty checkout would also ship to the still-legacy north TV at its next 4-hourly reload. That would include the in-flight legacy-path changes (black body on every display page, the page boundary, hold-last in `useMask`, the `/room` route in `App.tsx`) and any other agent's unfinished work.
1. Build from a **clean worktree at a reviewed commit**, never from the shared checkout (LIVE_V1 §0 already forbids `npm run build` here).
2. Get screen-machine-12's review of the page diff (D2).
3. Keep `dist_prev` (`start.sh` does).
4. **Gate on Q8.** The legacy page may carry only the owner-approved P0.5 fixes. If Q8 is no, strip them from the legacy tree before building.
5. **Legacy parity check** with no `?ctl`, in headless Chrome against the new build over 30 min: the same picture transitions, overlays and mask values as HEAD, a flat script count, and the watchdog never firing.
6. The guarantee is restated as **"no `?ctl` ⇒ today's behaviour plus only the owner-approved P0.5 fixes and the boot watchdog"**.

**Kill-switch paths** (acceptance #10). From fastest to last resort:
1. **Per-topic settings** (seconds).
2. **Server kill switch:** `kiosk.reload{flags}` to a connected page, plus `welcome.ctl_off` re-issued on every connect while active (persisted).
3. **Static kill flag** `/output/_ctl/<screen>.json`. It needs no room_service; it takes effect within 10 min on a page that has had no welcome since load, and at the next boot otherwise.
4. **Self-rollback** after 3 page-boundary trips in an hour.
5. **Bundle that cannot boot:** revert `dist` to `dist_prev`, and the boot watchdog reloads onto it.
6. **Last resort:** the launcher URL back (box access).

After a TTL lapses, the page tries ctl again. room_service re-issues an active switch on connect, so a renewal costs at most one extra reload, not two.

**Deferred, not in the MVP:**
- heap-driven reloads (evidence first, §9.4);
- `poster_url` extraction (clips published during live are deferred instead);
- the Flask hook (5b);
- the 50-client swarm (a 6-client test instead, §9.2);
- a chaos proxy beyond the faults in §9.3;
- per-field schema `x-since` and feature tags (one `proto.minor` instead);
- in-place DOM morphing of overlay updates (coalescing meets the budget);
- the operator UI for settings (the HTTP API is enough).

**Estimate:** the MVP is about 4–5 builder-days with two builders after Day 2, then 48 h on south and the soaks. RP puts the same ground (P4b, P5, supervision) at weeks, largely because of its Flask-side gates; this layer avoids Flask changes. The 4–5 days are for the MVP only.

### 8.3 Flask touchpoints

- **Required: none.** ContentWatcher reads files, OverlayBridge listens on :8765, and SensorService reads Flask's existing GET endpoints and files.
- **Optional, one tiny hook** (step 5b, owner approval, one Flask restart in a quiet window):
  - A new `routes/room_notify.py` (about 40 lines): a bounded queue (100), one daemon thread, POSTs with a 1 s timeout, drops when full, never raises or blocks the caller.
  - Two one-line calls: in `publisher._publish_to_destination` after `_record_publish` (inside `if update_published`), and in `display.send_overlay` after `data` is built (adding an `overlay_id`).
  - It must not go on the legacy :8765 broadcast. room_service works the same without it.
- **Asks for RP** (not needed by v3; each makes a v3 path cleaner):
  - `bucketer.purge_bucket` and the bucket delete (`bucket_api.py:334`) skip each destination's currently published file. ContentWatcher's fallback already covers it.
  - `compute_mask` treats lux older than about 30 s as stale and uses the solar curve. That is `lightmask`'s Flask switch (its README's "Flask switch" block) plus a staleness rule. Until it lands, the relay keeps its legacy send to :8765.
  - TV variants of `overlay_generating` and `overlay_panel_common_head`: no infinite non-compositor animations (`shimmerSweep` animates `background-position`, a main-thread paint every frame) and no `backdrop-filter`. Required before `overlay_source: push` on a TV if §9.5 fails its threshold (Q9).
  - Atomic publish writes (temp file plus `os.replace`); the Blank `NameError` (`publisher.py:152`); a token on the mask-override writes; the cross-loop `send_overlay` fix (RP H2a); closing WAN :8765 once nothing depends on it.

### 8.4 Kiosk and Windows touchpoints

The complete list. Every item is owner-approved and done in one owner window, except the trial in item 1.
1. **Trial (step 8b, first):** CDP `Page.navigate` on 9223 to the ctl URL. It is non-persistent: a respawn reverts it.
2. **Launcher, `kiosk-wayland.sh`** (repo copy, deployed by `deploy-kiosk.sh`). Today it hardcodes both URLs after sourcing `/etc/default/kiosk-wayland` (lines 15–19) and reads them once, so killing one Chrome respawns it with the old URL. It also puts the profiles in `/tmp`. The changes:
   - `URL_NORTH=${URL_NORTH:-…}` and `URL_SOUTH=${URL_SOUTH:-…}`, and `/etc/default/kiosk-wayland` is re-sourced inside `launch()`. A per-TV change then applies at that TV's next respawn: kill only its Chrome, at a quiet hour.
   - **Persistent profiles** `~/.local/share/kiosk/chrome-{north,south}`. The `pkill` pattern in `kiosk.service` `ExecStopPost` (`--user-data-dir=/tmp/chrome-(north|south)`) changes with them.
   - **Health-gated start:** `launch()` runs Chrome from a backgrounded subshell that first waits for `curl -sf -m 2 <origin>/api/health`. sway's background stays black meanwhile, so a kiosk that boots before WSL and Flask, or a respawn during a Flask deploy, never shows Chrome's error page.
   - The same edits in `kiosk-loop.sh` for the X11 fallback, or note that the X11 fallback stays legacy.
3. **`/etc/default/kiosk-wayland`** (0640 root:gjbm2; `deploy-kiosk.sh` keeps an existing file):
   - `URL_SOUTH="http://192.168.1.205:8080/display/south-screen?ctl=ws://192.168.1.205:8700&kiosk=south#ctlkey=<key>"`, and likewise north. A helper on the box writes the keys.
   - `CHROME_EXTRA="--enable-precise-memory-info --force-dark-mode"`. Confirm both on Chrome 136, the version on the box.
4. **Chrome hold:** `sudo apt-mark hold google-chrome-stable` (RP D7) **before** step 8b. The apt candidate is 155, and newer Chrome adds Local Network Access prompts that `--kiosk-mode-no-prompts` cannot answer. With the LAN origin (item 3) the page makes no public-to-private request at all.
5. **The relay's `~/.config/light-relay/room.json`** (LIVE_V1 §2.2b), deployed by the owner with the calib-3 relay (Q11).
6. **Windows:** a portproxy and a LAN-scoped firewall rule for 8700 (room_service) and 8702 (stream_service media). Both are added to `fix-wsl-networking.ps1`, because manual rules are not rebuilt after a WSL IP change (KIOSK notes §1).
7. **Deadman:** a second probe of room_service's `/healthz`, if Siren cannot be imported (§4.5).
8. **Repo hygiene, before any key exists:** `check-status*.sh` redact `#…` in the page URLs they print, and `media-server/maintenance.log` is untracked (or scrubbed and redacted) by its owner.
9. **Not in this layer, recommended:** the Ethernet path watchdog (KIOSK notes §5) for the "link up, path dead" case (Q12).

### 8.5 Changes to send to the DCL and RP owners

- **DCL (bd):** the page control plane is room_service's `/v1/screen/<id>`, not the Flask `/v2` hub (D1). DCL §2.3's protocol lands as v1.x here.
  - DCL's HTTP twin is dropped (§5.4).
  - DCL's per-mask acks become sampled latency in `kiosk.state` (§3.5).
  - The newest-wins question is settled as identity before recency (§3.2).
  - DCL §6 items 3(c), 3(d) and 6 are kept as v3 gates (§9.5).
- **RP (screen-machine-12):** P4's page socket, P4 step 6 (shadow acks) and P5 steps 1–3 move to room_service (this doc).
  - P4's Flask-internal fixes (H2a cross-loop sends, AssemblyAI off the loop) stay. They still matter for the legacy socket and the operator UI.
  - P0.5 is delivered by v3 steps 1 and 7 for the ctl page. The legacy page still needs the same fixes, gated by Q8, plus the boot watchdog.
  - D13 is adopted as written: the 04:00 health-gated safety reload stays for 30 days. The KioskMonitor ladder is the hung-renderer answer (Q14).
  - D7 (the Chrome hold) is a precondition of step 8b.
  - The asks in §8.3.
  - The P0.5 test plan's ":3001 throwaway server" collides with room_service's dev port; use another port.

### 8.6 LIVE_V1 amendments this design needs (for screen-machine-2f to record)

Each is additive for a v1.0 peer, but a builder coding to the current text would diverge, so they are listed here rather than claimed as "no change":

1. **§3 keys:** the page may take its key from the URL fragment `#ctlkey=` (preferred; never sent to Flask) as well as `&ctlkey=`. The wire field stays `hello.key`.
2. **§2.1 `hello`:** optional `proto{minor}`, `kiosk`, `have{epoch, *_rev, base_key, live:{session, phase}|null, ended[]}`, `prev{page_load_id, reason, cmd_id}`, `boot{mode}`.
3. **§2.1 acks:** the critical acks may be re-sent after a reconnect, and the server deduplicates them by `(kind, fid|session)`. A `frozen` ack for a fid the server no longer holds is answered with `unfreeze{fid}`.
4. **§2.2a live after a reconnect:** `welcome.live` is sent only when `hello.have.live` names that session (pages without `have` keep today's behaviour). A glass hello that reports no live while the server believes live counts as a lost `art_visible{stall}`. The page ignores `live.attach` and `welcome.live` for a session it has ended.
5. **§2.2a rule 3, one glass per screen:** the identity rules G1–G7 (same `page_load_id` replaces at once with 4009; a `prev.page_load_id` successor takes over at once; others are standby with a `role` message; promotion after 20 s), implemented in the screen controller.
6. **§2.2a rule 5, liveness:** a page is *reachable* with an inbound frame in the last 3 s. A freeze is sent only to a reachable page, and a freeze-ack timeout on a page that went silent is not an entry failure.
7. **§2.2 rule 3, freeze:** the page's settle may take up to 4.5 s; the server's `freeze_ack_s` becomes 5 s (AV_PROGRAMME §3.3: 3 s). The server waits up to 5 s for the latest `screen.state` ack before freezing, and sends no `screen.state` or `overlays.set` between freeze and unfreeze.
8. **§2.2 rule 2, mask:** the page holds for **30 s** (not 5 s) before polling Flask, and ramps the first polled value in over 10 s. `welcome.mask` is null unless the value is at most 10 s old, and a page with a drawn mask then holds rather than polls.
9. **§2.2 rule 9, timed reloads:** "no control socket" means down ≥30 min or no `welcome` since load (as built: 5 s). Every page reload is gated on same-origin `/api/health`. A daily `safety_reload` (default 04:00) applies while `timed_reload` is false (RP D13).
10. **§2.2 rule 11, messages during live:** add `screen.state` (≤1 per 10 s, applied to the hidden stack; rule 5 already expects bases to change while live), `ack{kind:"state"}` and `role`. The mask shaping (snap ≤10/s; drift ≤1 per 3 s, deadband 0.015) sits within the existing 10/s cap.
11. **§2.2 rule 5:** overlays during live stay **drop and count** by default (`overlays_in_live: "drop"`) until the owner answers Q3.
12. **§2.2a rule 1, outbox:** a pending `policy` is sent before the next command. The byte limit counts bytes queued behind the frame in flight. `welcome` is exempt, and is at most 128 KB.
13. **§2.2a, unknown screen:** accept the upgrade and close with 4404, instead of HTTP 404.
14. **Close codes:** 4009 (replaced by the same page load or its successor; do not reconnect that socket), 4012 (restart; jittered reconnect), 4404, plus the as-built 4008/4029/4401.
15. **§1.2 stream_service sessions:** an optional `owner` string in `POST /api/sessions`, echoed in the session summaries. room_service adopts and deletes only sessions with its own owner.
16. **§2.2b:** `mask.set.source` takes `lightmask`'s strings (`sensor`, `astral`, `manual_override`, `maskoff`, `fixed`, `blend`) plus `flask`. `MaskGate` runs at 10 Hz. The interim Flask fetch is ≤1 per 3 s per screen while any screen runs the legacy page, and only for screens whose source is `flask`.
17. **§2.1 Policy:** a field absent from `welcome.policy` takes its documented default, with `timed_reload` defaulting to true. New fields: `safety_reload`, `overlays_in_live`, `content_source`, `overlay_source`, `telemetry`, `reload_offset_s`, `rev`.
18. **§2.1 ArtState:** `base_url` is validated by path only (query stripped, same-origin `/output/` path), so the poll form passes. Optional `base_key`.
19. **§2.1 new 1.x types** (only to pages with `proto.minor` at least the stated minor): `screen.state`, `overlays.set`, `kiosk.reload`, `kiosk.maintenance`, `ack{kind:"state"|"cmd"}`, `log`, `role`. New `welcome` fields: `server`, `role`, `state`, `overlays`, `maintenance`, `ctl_off`.
20. **§4 out of scope:** content push and overlay push move into scope as the 1.2 and 1.3 additions above.

---

## 9. Test strategy

### 9.1 Contract tests
§3.9: schema plus examples, validated on both sides; a server emission hook; drift tests; compatibility in both directions; the poll-path ArtState passing validation.

### 9.2 Fake-page tests (`room_service/tests/`, building on `tests/fakes.py`)
- **Six-client run** (the 50-client swarm is deferred): two kiosks, devtest lab, a standby duplicate, a slow reader (8 KB/s), a malformed fuzzer. **Pass:** loop lag p99 < 20 ms; the slow reader closed with 4008 within 6 s while healthy clients' `mask.set` p99 stays < 50 ms; the fuzzer never raises past the handler.
- **Glass identity:**
  - a standby hello mid-live does not orphan the session;
  - a TV reload with a standby present hands over to the successor (G3), not the standby;
  - a same-page reconnect over a half-open socket gets no `role` message and no alert;
  - promotion happens only after 20 s with no successor.
- **Liveness:** an entry against a silent but still-registered socket counts no failures and ends in "waiting for the page".
- **Live after a reconnect:** a stall cut whose `art_visible` was lost, then a hello with `have.live: null, ended:[S]` → the server records a fallback and sends no `welcome.live`.
- **Outbox:**
  - a pending policy goes before `live.attach`;
  - the overlay set is held from freeze to unfreeze and then re-sent;
  - a 120 KB welcome is not closed with 4008.
- **Adoption:** a stale fid with an exiting session → `live.abort` before `unfreeze`; a second instance cannot take the lock; foreign-owned sessions are never deleted.
- **ContentWatcher:** the immutable file deleted → same key, `url = fallback_url`; two publishes in one second are settled correctly; `bucket.json` late → fallback first, immutable second, no glass change.

### 9.3 Chaos
A small asyncio TCP fault proxy between a headless Chrome page (Vite dev, `?ctl=` through the proxy) and room_service.

| Fault | Pass |
|---|---|
| Drop (close) | Reconnect; a DOM MutationObserver shows no base or overlay remount |
| Blackhole (half-open, no FIN) | Page detects ≤7 s; server unreachable at 3 s, dropped ≤16 s; resync on heal; an entry attempted meanwhile counts no failures |
| Slow (64 kbit/s) | 4008, reconnect; masks coalesced; no stale final value |
| Duplicate every server frame | No double effects: one reload, one overlay change, one picture transition |
| Reset during a live session (Wi-Fi failover) | Exactly one cut to art; no re-attach; one fallback recorded |
| Kill room_service for 12 s | No dip, no Flask mask fetch from the page, no remount |
| Kill room_service, then Flask, then both | §6.7 rows hold: no black, no white, warm boot on reload |
| Delete the published bucket file, then reload the page | The picture stays (fallback URL); no black |
| Serve a bundle that throws at import | The smoke test fails and no reload is sent; a page loaded on it stays black, and recovers through the watchdog once `dist` is reverted |

### 9.4 Soak
- **Workstation, 24 h:** headless Chromium (CPU raster), ctl page. Synthetic publishes every 30 s, the real mask shaper driven by a recorded lux trace, an overlay every 60 s, a forced reconnect every 10 min, and **no reload commands** (the reload path is tested separately, so the soak does not repeat the regime it is meant to judge). **Pass:** precise heap slope ≤1 MB/h after the first hour; DOM nodes, scripts, `<video>` count and decoders flat; 0 `push_miss`; 0 errors.
- **N95 live cycles (devtest, owner window):** 50 live attach/detach cycles. **Pass:** renderer and GPU process RSS and GPU memory flat (DCL item 9; the RP risk table relies on it).
- **South TV, 72 h + 72 h** (after step 8b): first with `timed_reload` on, then off with the 04:00 safety reload. Renderer and GPU process RSS are sampled every 5 min by read-only SSH (`/proc/<pid>/status` VmRSS for each Chrome's renderer and GPU processes, `intel_gpu_top -J`). Precise heap is taken from `kiosk.state`. **Pass:** RSS and heap within ±10 % over each 72 h, and no long-task trend. This is the evidence for Q6. The 04:00 safety reload is removed only after 30 days of it (RP D13).

### 9.5 N95 measurements (owner window)
Run each scenario for 30 min, first with today's production page (baseline) and then with the ctl page, on the same content. Use read-only CDP (`--allow-production` under the V1 rules) and the box's own tools.

| Measure | Tool | Pass |
|---|---|---|
| Renderer + GPU process CPU per Chrome, art steady (still) | `top -b` | ≤ baseline, and ≤50 % of one core averaged over 5 min |
| Mask sweep 0.3↔0.9 every 2 s (snaps) | `top`, `intel_gpu_top` Render/3D | CPU ≤ baseline + 5 pp; Render/3D busy ≤ +10 pp over static |
| Recorded daylight lux trace through the shaper | `kiosk.state.budget.anim_ms_1m` | Compositor animating ≤20 % over 10 min; ≤ today's page |
| Clip base under the mask sweep | `getVideoPlaybackQuality` | Dropped ≤0.1 % over 30 min |
| Main thread | `kiosk.state` long tasks; rAF intervals | Long tasks ≤1/min; rAF p99 ≤20 ms |
| Heap, DOM, scripts | `kiosk.state.budget` (precise) | Flat (±10 %) over the window |
| Both TVs in art with the mask sweep | the same | Each Chrome meets its single-TV limits |
| devtest live through V1 (one TV) | `kiosk.state.live`, `intel_gpu_top` Video | Control traffic ≤3 msgs/s; presented ≥99.9 % (DCL bar); VideoEnhance 0 % (zero-copy) |
| **DCL §6 3(c): warm-up configuration** (art stack with still, black layer and full-screen multiply over a covered warming `<video>` on one TV, the other TV live) | `intel_gpu_top`, dropped frames on the live TV | Live TV ≥99.9 % presented; no visible change on the warming TV |
| **DCL §6 3(d): a paused clip base under live**, against a still base | the same | No measurable difference. If there is one, the clip is swapped for a captured frame at `live.attach` |
| **DCL §6 item 6: two decoders in one page** at 4K and at 1080p during a 1–2 s overlap | dropped frames in both windows | No drops. Required before any A/B session switching |
| **Production worst case:** one TV live, the other in art with a clip base, the mask sweep and `overlay_generating` with real stage spans updating every 5 s | all of the above | Live TV ≥99.9 % presented; art TV dropped ≤0.1 %, long tasks ≤1/min |
| `overlay_generating` over a still | Render/3D, long tasks, `anim_ms_1m` | Render/3D ≤ +10 pp over the still; long tasks 0. If it fails, RP's TV variant is required before `overlay_source: push` on that TV (Q9) |

---

## 10. Acceptance criteria and open owner questions

### 10.1 Acceptance criteria

The layer is done when, on both TVs in ctl mode:

1. **One socket.** Exactly one control socket, plus one media socket while live. 0 sockets to :8765. 0 polls while connected, apart from the evidence-period safety poll (≤1/min).
2. **Pictures.** Publish to transition start p95 ≤1 s, and to fully visible p95 ≤3.5 s with `through_black`, both measured from acks. Zero `push_miss` over 7 days. No truncated picture shown. A purged or deleted bucket file never blanks a TV, including after a reload.
3. **Light.**
   - Sensor report to style commit p95 ≤50 ms on idle powerline.
   - A real light step to a visible change p95 ≤250 ms with the relay at 10 Hz (calib-3). At today's ~2 Hz it is about 0.6–1.1 s, because `lightmask`'s spike guard needs a second sample.
   - The compositor animates ≤20 % of the time in steady daylight.
   - The mask never goes black on any failure in §6.7.
4. **Overlays.** They survive a reconnect (snapshot) and an aborted entry. Script and timer counts stay flat. ≤3 mounted. None over live. ≤1 update per 5 s each.
5. **Robustness.**
   - Half-open is detected within 7 s by the page.
   - A room_service restart and a Flask restart are invisible on glass in art mode.
   - A Wi-Fi failover during live gives exactly one cut back to art and no re-attach.
   - A dead path never puts a screen in `failed`.
6. **Visibility.** `/glass` shows desired against applied for every topic. Each §4.5 alert fires in a fault drill within its stated time.
7. **Reloads.**
   - None during live or a handshake, ever (logged).
   - With `timed_reload` off and the 04:00 safety reload on, renderer and GPU RSS are within ±10 % over 72 h.
   - A build change is smoke-tested, then reaches each TV by `kiosk.reload` within its deadline, one TV at a time, in the black of a picture change or a single dip. The rollout halts on a TV that does not come back.
8. **Budget.** The §9.5 thresholds are met.
9. **Security.** A client without a key cannot become glass. Lux can only come from a keyed sensor. The `base_url` SSRF is closed. No key appears in a tracked file, `maintenance.log` or `check-status` output.
10. **Rollback.**
    - Each step's setting flips back within 10 s with no box access.
    - The kill switch also reaches a page that cannot connect (static flag, self-rollback) and a build that cannot boot (`dist_prev` plus the watchdog), all without box access.
    - The launcher URL is the last resort.

### 10.2 Owner questions

| # | Question | Recommended default |
|---|---|---|
| Q1 | Adopt v3: room_service owns all display-page signalling (pictures, overlays, mask, policy, reloads), and RP's Flask `/v2` page socket is dropped. | **Yes.** |
| Q2 | Move the page origin to the LAN (`http://192.168.1.205:8080`) in the same launcher edit as `?ctl` (§8.4), with Chrome held at 136 (D7) first? | **Yes to both.** A WAN origin opening `ws://` to a private IP depends on Chrome's Private/Local Network Access rules, which Chrome 155 tightens with a prompt kiosk mode cannot answer. RP P3 and DCL both put the LAN origin before production live. |
| Q3 | Overlays sent while a screen is live (DCL item 20). | **Undecided:** drop and count (LIVE_V1 rule 5) until you choose. "Queue" (show them after the return to art if still unexpired) is ready behind `overlays_in_live`. Nothing is ever drawn over live. |
| Q4 | Allow the optional Flask hook (one new module, two one-line calls) for exact overlay ids and zero-latency publish notices? | **Not now.** Ship with the watcher and bridge; decide after a week of evidence. |
| Q5 | Reload a TV automatically when a new page build is deployed: smoke-tested first, at its next picture change, deadline 1 h, health-gated, one TV at a time, halting if a TV does not come back? | **Yes.** |
| Q6 | Replace the 4-hourly reloads per TV with one health-gated 04:00 reload a day for 30 days (RP D13), then drop it if renderer and GPU memory stay flat? The 4-hourly schedule stays as the fallback whenever room_service is unreachable for 30 min. | **Yes.** |
| Q7 | Starting brightness when a page boots with no cached value. | **Black until the first mask arrives** (as today's page boots), then a 1 s fade-in; 0.3 neutral if nothing arrives within 10 s. No bright flash at night. |
| Q8 | Deploy hold-last, the black background, the page boundary and the boot watchdog to the *legacy* page too (RP P0.5)? It gates step 8a. | **Yes:** a server restart then never blacks a TV, ctl or not, and a broken build can be rolled back remotely. |
| Q9 | If the N95 measurement fails, replace `overlay_generating`'s blur and its shimmer animation with a plain panel for the TVs (RP template change)? | **Yes, only if §9.5 fails its threshold.** |
| Q10 | ~~Production port.~~ **Settled:** 8700 (D4, `docs/PORTS.md`), LAN-only portproxy. | — |
| Q11 | Deploy the light relay's room ingest config on the kiosk (`room.json`, LIVE_V1 §2.2b). | **Yes**, at step 3. |
| Q12 | Install the Ethernet path watchdog on the box for the "link up, path dead" case. | **Yes** (infrastructure, independent of this layer). |
| Q13 | The launcher changes in §8.4: URLs and keys in `/etc/default/kiosk-wayland` re-read per respawn, persistent Chrome profiles, a health-gated Chrome start, and two flags. | **Yes**, in one owner window before step 8b. |
| Q14 | Let RP D13's KioskMonitor restart a TV's Chrome automatically (≤3/h) when its page is stale or offline, which is the only answer to a hung renderer? | **Yes, when built.** Until then, alert only. |
| Q15 | Untrack (or scrub) `media-server/maintenance.log` and redact URLs in `check-status*.sh` before any page key is issued. | **Yes.** |

---

## 11. Review log (14 Oct, v3.0 → v3.1)

45 review items. Each was checked against the code before deciding. "Fixed" names where the change lives. Duplicates point to the first item.

| # | Area | Verdict | Change, or reason |
|---|---|---|---|
| 1 | Immutable picture URL purged or deleted while on glass | **Fixed** | Confirmed: `purge_bucket` (bucketer.py ~300-330) goes by mtime and never skips the published file, and a same-bucket republish keeps the old mtime. ContentWatcher re-stats the immutable file every sweep and switches to `fallback_url` with the same key (§4.4). The page tries `fallback_url` on any base failure, including warm boot (§5.7, §5.8). RP ask (§8.3); chaos test (§9.3). |
| 2 | Lost `art_visible` on a dead socket → re-attach mid-scene after a Wi-Fi failover | **Fixed** | Confirmed (`on_page_hello` gives `live_info()` to the same page; `LiveController.attach` re-attaches once `cur` is null). Added `hello.have.live`/`ended`, the server's "hello as lost ack" rule, the page's ended-session set and the critical-ack queue with dedup (§3.3, §3.5). The failure-table row is rewritten (§6.7). LIVE_V1 amendments 3–4. |
| 3 | Build stagger proceeds after 5 min; the kill switch cannot reach a page that fails at import; `unknown` build loop | **Fixed** | The smoke test in headless Chrome before any build reload; halt on a TV that does not return; one reload per (screen, build); `unknown`/`dev` never compared (§4.4). An inline boot watchdog in `index.html` makes a `dist_prev` revert recover without SSH (§5.7); named in §8.2's kill-switch paths. |
| 4 | Incumbent-wins hands the TV to a copied-URL laptop at the TV's next reload; half-open same-page reconnect; demoted-while-live | **Fixed** | Replaced with identity before recency, G1–G7 (§3.2): same `page_load_id` replaces silently (4009); a `prev` successor wins at once; a standby is promoted only after a 20 s hold; a page demoted while live tears nothing down. |
| 5 | The 15 s server drop lets three freeze timeouts reach `failed` against a dead socket | **Fixed** | Confirmed (`_entry`; backoff [2,5,15,30]; max_entry_failures 3). The 3 s reachability gate before freeze, promotion and "waiting for the page"; a timeout on a page that went silent is transient (§6.3, §3.6). LIVE_V1 amendment 6. |
| 6 | Degraded Flask mask after 5 s uses days-old lux; a restart is not invisible; the warm-start mask bumps | **Fixed** | Confirmed (`display_utils.py:173-176` has no age check). Hold 30 s before the Flask fallback, and ramp its first value over 10 s (§5.4). `welcome.mask` is null unless fresh, and the page then holds (§3.5). The relay's legacy send stays until Flask has a staleness rule (§8.2 step 9, §8.3). The staleness rule itself is an RP ask, not this layer's change. |
| 7 | A reload in the black warm-boots the *outgoing* picture, then cuts | **Fixed** | The cache records the incoming base with `boot_mode: fade_in`, is written synchronously at commit, and a differing picture always goes through black (§5.7). |
| 8 | Freeze during a base transition, clip cross-fade or reload fade | **Fixed** | The server waits ≤5 s for the state ack before freezing and holds state and overlays until unfreeze (§4.3). The page settle is 4.5 s and `freeze_ack_s` 5 s (§3.5). A freeze cancels an uncommitted reload, and the page closes its socket before a committed one (§5.7). The clip cross-fade already snaps on `hold()` (ClipCycle). Rejected "snap the transition to its end state before the ack": that is a visible jump right before a cut that must be invisible. |
| 9 | Everything in `localStorage` is lost on reboot (`/tmp` profiles) | **Fixed** | Persistent profiles in the launcher change (§8.4; the `kiosk.service` pkill pattern changes with them). The server persists the kill switch and re-issues it in `welcome.ctl_off`. The no-cache boot is black until the first mask, not 0.5 (§5.7, Q7). What does not survive without the move is stated (§5.7). |
| 10 | One picture under three URLs; `?v=<rev>` reused after a crash | **Fixed** | Content key = file identity `base.key` (§3.3); the seeded HEAD poll (§5.4); `fallback_url` uses `?v=<mtime_ns>-<size>`, never the rev (§4.4). |
| 11 | Nothing enforces one room_service per state dir or stream_service | **Fixed** | Confirmed, and worse than stated: `_reconcile_sessions` also deletes sessions of screens a dev config does not list. `flock` on the state dir; owner-tagged sessions, adopting and deleting only our own (§4.7). LIVE_V1 amendment 15. Not adopted: the mandatory dev screens allowlist, because the lock and owner tags remove the hazard, and dev configs keep `mask_push: false` for north and south. |
| 12 | Chrome respawn or boot while the origin is down → white error page | **Fixed** | A health-gated Chrome start in the launcher (sway stays black meanwhile) and `--force-dark-mode` (§8.4); Chrome's error-page auto-reload marked unverified, to check in an owner window (§6.7). Rejected: the `file://` bootstrap page (the launcher gate gives the same result with no extra file on the box) and `--default-background-color` (not a verified desktop Chrome switch). |
| 13 | Private/Local Network Access and the hardcoded WAN origin | **Fixed** | The LAN origin is in the same launcher edit and the Chrome hold is a precondition of step 8b (§8.4, Q2). The origin allowlist moves to config (§7). Instead of an HTTP beacon to Flask (which would need Flask code), the page reports its control-plane state in `document.title`, which `check-status.sh` already reads (§5.4). Also found: the ctl page's Flask calls used the baked WAN `VITE_API_URL`; they are now same origin (§5.1). |
| 14 | Outbox: commands overtake policy; an overlay pending at freeze is lost; `clear_others` reorders | **Fixed** | Policy before command (§4.3). Overlays become one `overlays.set` slot, so no ordering between overlays remains, and the set is held, not dropped, over a freeze. OverlayHost restores overlays after an aborted entry (§3.5). |
| 15 | A stale fid is released before adoption decides | **Fixed** | Confirmed (`on_page_hello`, `_stale_fid`). Release only after adoption; `live.abort` before `unfreeze` for an exiting or held session (§4.7). |
| 16 | SensorService and FlaskMaskSource both write the mask | **Fixed** | MaskArbiter: one writer per screen chosen by setting; `lightmask` handles the stale→solar ramp internally; Flask fetches only for `flask` screens (§4.4). |
| 17 | Limits and versioning: overlay 256 KB = outbox 256 KB; key location differs from LIVE_V1; no reverse-compat test; 4009 strands a page | **Fixed** | Overlay HTML ≤32 KB, `welcome` ≤128 KB and exempt from the limit (§3.6, §4.3). The key stays LIVE_V1's `hello.key`, with the fragment as its source (amendment 1). Absent-field defaults and the reverse test (§3.1, §3.9). Reconnect 30–60 s after 4009 if no other socket is open (§3.7). |
| 18 | Hung renderer has no remedy; overlay scripts are not DOM-scoped | **Fixed (overlays) / Rejected (launcher CDP probe)** | Shadow roots, scripts run after attach, scoped `document` shim (§5.6 L1). The hung renderer goes to RP D13's server-side KioskMonitor ladder (Q14). A CDP `Runtime.evaluate` probe in the launcher needs a WebSocket client and decision logic on the box, against P6 and the owner's "no kiosk agent", and RP already plans the server-side ladder. |
| 19 | Leak evidence rests on a coarse JS heap; the soak repeats the reload regime; no live cycles | **Fixed** | `--enable-precise-memory-info` (§8.4). Renderer and GPU RSS by read-only SSH as the evidence (§5.5, §9.4). No reload commands in the leak soak. 50 live cycles on the N95. The 04:00 safety reload kept for 30 days (Q6). The heap-based reload rule and page heap ceiling are dropped. Correction: the box runs Chrome **136**, not 155 (KIOSK notes §1), so confirm the flag there. |
| 20 | Mask push keeps the compositor animating | **Fixed** | MaskShaper: snaps at once, drift ≤1 per 3 s at 1000 ms with a 0.015 deadband (§4.4). A compositor-busy budget row (§5.5) and test (§9.5). Perceptual idle threshold for the warm layer (§5.3). Not adopted: putting the threshold "in the shared mask formula for the renderer's anchors", because anchors are art-only page snapshots running the same page code and the renderer applies no mask. |
| 21 | Extra decoders and a main-thread `toDataURL` while live | **Fixed** | Confirmed (`index.tsx:293-305`, `ArtMedia.tsx:144-179,301-309`). No new clip decoder or extraction while live; clips published during live are deferred (or shown as `poster_url` later); extraction via `createImageBitmap` at 1080p with no data URL; ≤1 *decoding* video while live (§5.5, §5.6 L4). DCL §6 3(c), 3(d) and item 6 restored, and the live-plus-art worst case added (§9.5). The rule says "decoding" because whether a held, paused clip may stay under live is exactly 3(d)'s open question. |
| 22 | Content keys across push and degraded paths | **Fixed** (same as 10) | — |
| 23 | Boundary reloads: contradictory gates, in-memory backoff, no automatic stop | **Fixed** | The counter persisted in the boot cache; the rate gate from the second reload; self-rollback after 3 trips/h with no warm boot; one gates list with named exceptions (§5.7). |
| 24 | The generating overlay keeps the page busy; about 120 replacements a minute | **Fixed** | Bridge coalescing ≤1 per 5 s per overlay, ≤0.5 msgs/s per screen (§3.6, §4.4); shadow roots contain the template's global styles (§5.6); RP TV variants (§8.3); a pass threshold in §9.5. Deferred: in-place text and attribute updates, because coalescing alone brings the cost within the ≤60 renders/min budget. |
| 25 | One component with `ctl ?` branches; the kill switch does not swap code; ladder sprawl | **Fixed** | A separate `CtlDisplayPage` tree, with the legacy file frozen; degraded fallbacks as classes; one degraded switch at 30 s; the offline schedule and heap ceiling dropped (§5.1, §5.4). |
| 26 | Overlay cap equals outbox cap; HTTP 404 for an unknown screen looks like 1006 | **Fixed** | Caps as in 17. Accept the upgrade and close 4404 (the constant already exists in `pages.py`) (§3.7). |
| 27 | The diagnostics ring fills with `mask.set` | **Fixed** | Confirmed (`DisplayController.ts` `event()` per mask). High-rate kinds counted, not ringed (§5.5, L8). |
| 28 | `live/*` copies every batch; extra intervals | **Fixed** | Recorded as drift (§8.1). Append the `subarray` for single chunks; fold the stall check into the 250 ms watch; the timer rule lists the live intervals (§5.5). |
| 29 | Reload hidden in a picture change vs warm boot | **Fixed** (same as 7) | — |
| 30 | `base_url` allowlist rejects the in-flight poll form and would break devtest live from day 1 | **Fixed** | Confirmed (`useFilePolling.ts:49,83` builds `?t=`). Path-only validation that accepts both forms, with a contract test; it can therefore stay on day 0–1 as a security fix (§7, §3.9). |
| 31 | V3's build plan collides with the in-flight builders; AV Day 2 puts a TV on ctl before V3's safeguards | **Fixed** | V3 starts after AV Day 2; named file owners per step; AV Day 2's TV trial runs through a non-persistent CDP navigate, whose rollback is a respawn; §8.1 re-taken at 00:35 and marked to re-take at build start (§8.2). |
| 32 | Step 8 would ship the shared checkout's legacy-path changes to north | **Fixed** | Step 8a: clean worktree at a reviewed commit, screen-machine-12's review (D2), `dist_prev`, Q8 as a gate, a no-`?ctl` parity check, and the restated guarantee (§8.2). |
| 33 | Incumbent-wins vs ScreenController (`on_page_hello` for every glass hello) | **Fixed / Rejected (revert to newest-wins)** | The coupling is real, so the rules live in ScreenController and only the driven client reaches `on_page_hello`/`on_page_closed` (§3.2), with tests (§9.2). Rejected keeping newest-wins: any second glass hello mid-live orphans and deletes the TV's session (`screens.py` ~523-530), a visible stall on the TV. G3 also gives the TV back its glass after its own reload, which plain incumbent-wins could not. |
| 34 | The page key in the URL fragment leaks into `maintenance.log`, check-status and react-router's `navigate` | **Fixed** | Confirmed (remote-maintenance tees into a git-tracked log with about 500 command lines; `resolvePath` resets the hash). The key lives in `/etc/default/kiosk-wayland`; the page strips the fragment with `replaceState` and holds the key in memory and `sessionStorage`; redaction and untracking happen before any key exists (§7, §8.4, Q15). |
| 35 | The kill switch only reaches a connected, driven page | **Fixed** | Five paths beyond the socket: `welcome.ctl_off` re-issue, the static flag through `/output`, boundary self-rollback, the boot watchdog with `dist_prev`, and the launcher as last resort; acceptance #10 restated (§8.2, §10.1). The static flag is read only at boot and every 10 min while there has been no welcome since load, not polled continuously. |
| 36 | Content keys: push URLs vs degraded-poll URLs | **Fixed** (same as 10) | — |
| 37 | `lightmask` already exists | **Fixed** | Step 3 is wiring (`ConfigWatcher`, `LuxFilter`, `mask_for_dest`, `MaskGate(max_rate_hz=10)`); no new goldens or filter; `mask.set.source` uses `lightmask`'s strings plus `flask` (§3.5, §4.4, §8.2). The v3.0 "10 Hz tick" is replaced by the package's sample-driven filter plus a 1 s evaluation timer, as its README says. |
| 38 | The launcher reads URLs once; "one URL edit, one relaunch" is not possible; touchpoints incomplete | **Fixed** | Confirmed (`kiosk-wayland.sh:15-19,61-68`). The CDP trial first; URLs from `/etc/default` re-read per respawn; the ctl and media ports added to `fix-wsl-networking.ps1`; every box and Windows change listed (§8.4). |
| 39 | "≈4 days" for the full scope | **Fixed (mostly)** | The MVP is cut and the deferred list named (§8.2): heap reloads, the 50-client swarm, the extended chaos proxy, per-field `x-since`, feature tags (one `proto.minor`), the hook, `poster_url`, overlay DOM morphing. Vitest only in an agreed install window or a scratch package (§3.9). **Not deferred:** the glass identity rules, which are the correctness fix for 4 and 33 and are small; the 20 s promotion hold reuses `adopt_wait_s`. |
| 40 | Rollout switches in mode-driven policy revert on a mode change | **Fixed** | Rollout settings are a separate, persisted, admin-only server setting (`PUT …/settings`) that modes never touch; modes drive only `timed_reload` and later `overlays_in_live` (§3.5, §4.4). |
| 41 | "Additive to LIVE_V1" was overstated | **Fixed** | `overlays_in_live` defaults to `drop`; §8.6 lists 20 amendments for screen-machine-2f, including messages during live, the timed-reload interpretation and the key form; the header and P9 restated. |
| 42 | Acceptance targets conflict with the design | **Fixed** | Publish → start ≤1 s and → visible ≤3.5 s; the light target conditional on the 10 Hz relay, about 0.6–1.1 s at ~2 Hz (§10.1). |
| 43 | The interim Flask mask source speeds up the legacy TV's smoothing | **Fixed** | Sensor ingest via `lightmask` is the ctl source; Flask is a fallback only, capped at 1 per 3 s per screen while any legacy page reads Flask, so the shared EMA's read rate stays at today's level (§4.4, §3.6). |
| 44 | Q2: keeping the WAN page origin | **Fixed** (same as 13) | — |
| 45 | Outbox limit vs overlays in `welcome` | **Fixed** (same as 17) | — |

**Also changed while verifying** (found in the in-flight tree during this review, not raised by it):
- The production port is settled at 8700 (Q10).
- The in-flight outbox, keys and close codes 4008/4029/4401 have landed, so v3's 4013 is dropped in favour of 4008, and the key file is `room_page_keys.json`.
- In-flight, a kiosk without a key is closed with 4401, not demoted, and v3.1 adopts that.
- The in-flight `policy` lapses to timed reloads after 5 s with the socket down; v3.1 asks for 30 min (amendment 9).
- MaskPoller maps a missing `brightness` to 0, recorded as a drift (§8.1).
