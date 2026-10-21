# room_service

The room control plane: modes and the transitions between them, the screens' art ↔ live
handshakes, the near-real-time mask push and the headless-Chrome snapshotter.
Contracts: `docs/av/contracts/LIVE_V1.md` §2–§3. Design: `docs/AV_PROGRAMME.md` §2–§3.

```
web Room page ──HTTP+WS (open on the LAN)──▶ room_service :8700 ──REST+events (stream token)──▶ stream_service
display page ◀──control WS /v1/screen/<id>──┘   │  ├─ GET /api/<id>/mask ─▶ Flask :5000   (read-only)
                                                 │  └─ WS :8765 (receive only) ◀─ lux frames
light relay (calib-3) ──WS /v1/ingest/lux (key)──┘ lightmask → mask.set (Flask /mask fetch stops for that screen)
                                                 └─ headless Chrome: /display/<id>?artonly=1 → anchor/target PNGs
```

## Run

```bash
python3.12 -m venv ~/.venvs/sm-room
~/.venvs/sm-room/bin/pip install aiohttp pyyaml playwright==1.58.0 pytest pytest-asyncio pillow astral==3.2 websockets pycryptodomex
cd ~/dev/screen-machine
~/.venvs/sm-room/bin/python -m room_service --port 8700            # binds 0.0.0.0 (kiosks reach it via the LAN)
#   --host 127.0.0.1  --config config/room/room.prod.yaml  --state-dir …  --no-snapshots  --log-level DEBUG
# A dev/smoke instance: a temp copy of room.prod.yaml with its own service.state_dir (and its own --port).
```

* `astral==3.2` is `lightmask`'s one dependency (sensor ingest; without it room_service runs Flask-only);
  `websockets` only runs calib-3's relay code in the tests.
* `playwright==1.58.0` matches the cached Chrome for Testing 145 (`~/.cache/ms-playwright/chromium-1208`);
  the browser is launched by `executable_path`, so nothing is downloaded. `pillow` is for the tests only.
* Token: `~/.config/screen-machine/room_service.token`, created on first start (64 hex chars, 0600, `O_EXCL`).
  It is the **admin** token (LIVE_V1 §3: "operate open, edit gated"; see Auth below). Tokens and page keys
  never reach a log line or the UI: the access log drops query strings, and a log filter redacts them.
* State: `~/.local/state/screen-machine/room/` (atomic writes, done in a worker thread so the event loop
  never blocks on fsync or a 6 MB PNG, LIVE_V1 §2.2a.6): `screens.json` (desired state, believed
  layer, session + attach, open freeze, fallbacks), `state.json` (the room state model, docs/av/ROOM_STATE_MODEL.md
  §3: mode, transition, beliefs, desired values, stored slots, drift), `sensor_mask.json` and `page.json` (what those
  adapters applied), `config-history/` (the last 50 modes.json), `snapshots/<screen>-{anchor,target}.png`.
* Stopping the service leaves live sessions running on purpose; the next start adopts them.
* The display pages connect by themselves to `ws://192.168.1.205:8700` (`src/pages/DisplayPage`); `?ctl=` only
  overrides that address.

## Runs inside the app

Owner ruling, 14 Oct 22:25: **one back end.** room_service runs inside the Screen Machine app process.
`app.py` calls `room_in_app.start_in_background()` (repo root) right after it starts the legacy WS server.
The GPU renderer (stream_service, :8702) stays a separate worker.

* **Thread and port:** a daemon thread `room-service` runs room_service on its **own asyncio loop** and its own
  aiohttp server and ClientSession. It serves every HTTP and WebSocket route on `0.0.0.0:8700`, the port the
  separate service used, so the display pages, the Room page and the Windows LAN port-forward and firewall rule
  need no change. Flask's public :5000 door (WAN, ngrok) has **no room routes**. room_service never imports Flask,
  and Flask only starts and stops it.
* **Env:** `ROOM_IN_APP` (default `1`; `0` turns it off), `ROOM_PORT` (8700), `ROOM_HOST` (0.0.0.0),
  `ROOM_CONFIG` (`config/room/room.prod.yaml`), `ROOM_LOG` (`logs/room_service.log`, INFO; warnings also go to the
  Flask console).
* **Never blocks Flask:** the port is bound before anything of room_service starts. A busy port, or the state
  dir's `room.lock` held by the separate service, gives **one** warning and then a quiet retry every 10 s, so
  `scripts/ctl stop room` alone hands over within 10 s. Any other failure (a missing package, the config, the loop
  dying) is logged, sent to Siren as `room.service_died`, and retried with backoff 2/5/15/30/60 s.
* **Stop:** at exit (atexit) it shuts down cleanly. A SIGTERM or kill skips that, which is harmless: state is written
  atomically and live sessions are adopted by the next start.
* **Flask venv packages** room_service needs beyond what the app already has:
  `~/dev/screen-machine/.venv/bin/pip install playwright==1.58.0 pycryptodomex==3.24.1 hue-entertainment==0.1.2`
  (it adds only new packages and upgrades nothing; aiohttp 3.11.18 and astral 3.2 are already there). Without
  `pycryptodomex` room_service cannot start (the blinds adapter imports it). Without playwright there are no snapshots,
  so no screen can go live.
* **Standalone**, the same code path (e.g. a smoke check on a spare port with its own state dir):
  `python room_in_app.py --port 8799 --config <temp copy of room.prod.yaml> [--state-dir DIR] [--no-snapshots]`.
  Tests: `tests/test_in_app.py`.

**Cutover** (once the packages above are in the Flask venv):
1. `scripts/ctl stop room`
2. Restart Flask in its `flask` screen session. Don't use `./start.sh restart`: its `pkill -f "python.*app\.py"`
   also kills any other matching command line, including your own shell.
   ```bash
   cd ~/dev/screen-machine
   FPID=$(ss -Hltnp 'sport = :5000' | sed -n 's/.*pid=\([0-9]*\).*/\1/p' | head -1)   # Flask's own pid only
   kill -TERM "$FPID"; while kill -0 "$FPID" 2>/dev/null; do sleep 0.5; done
   screen -S flask -X quit 2>/dev/null; screen -wipe >/dev/null
   ./start.sh start flask      # = screen -dmS flask bash -c "… source .venv/bin/activate && python3 app.py"
   ```
3. Check that the Flask process serves :8700:
   ```bash
   ss -Hltnp 'sport = :8700'; ss -Hltnp 'sport = :5000'            # the same python3 pid on both
   curl -s -m 3 http://127.0.0.1:8700/api/v1/status | python3 -c 'import json,sys; print(json.load(sys.stdin)["service"])'
   tail -n 20 logs/room_service.log
   ```
   Doing step 2 before step 1 also works: the in-app room_service waits, then takes :8700 within 10 s of the stop.
   Rollback: `ROOM_IN_APP=0` in `.env`, restart Flask as above, then `scripts/ctl start room`.

After the cutover the `room` entry in `scripts/ctl` and Spectre's sm-room card are unnecessary. They belong to
screen-machine-ca. `scripts/ctl start room` would then refuse anyway, because :8700 is taken.

## Auth (LIVE_V1 §3, owner ruling 13 Oct late)

* **Operating is open on the LAN, no token:** status, modes, catalog, snapshots, `PUT /api/v1/mode`,
  `PUT /api/v1/screens/{id}`, `PATCH /api/v1/screens/{id}/live`, the events WS, `/healthz`.
* **Admin token** (`Authorization: Bearer <room_service.token>`) for config edits; v1 has none yet
  (`api.ADMIN_ROUTES`). Clients that send the token keep working; a token that is sent but **wrong** is a
  401 (so a stale saved token is noticed). `?token=` is read only on the events WS and the snapshot PNGs.
* **Host check** on every request (`allowed_hosts`, default `192.168.1.205`, `pc22.local`, `localhost`,
  `127.0.0.1`, `::1`; port ignored): 403 `host_not_allowed` otherwise (DNS rebinding).
* **Origin check** on `/api/*`: a request carrying `Origin` must come from `web_origins` or room_service's own
  origin (403 `origin_not_allowed`); no Origin (curl, scripts) is fine. The page control socket is
  origin-checked too (DCL v3 §7): its Origin must be in `web_origins`, `kiosk_origins` or be
  `art_page_base`'s. **The TVs load the page from their kiosk URL's origin (today the WAN address), so
  that origin must be added to `kiosk_origins` before a TV gets `?ctl=`.**
* **Kiosk control keys:** put `{"<screen>": "<key ≥16 chars>"}` in `~/.config/screen-machine/room_page_keys.json`
  (0600; reloaded on change). A screen listed there needs its key for `role=kiosk|lab` (`&ctlkey=` on the WS
  URL or `hello.key`; wrong/missing → close 4401). Screens not listed accept unauthenticated glass clients
  (warned once; status shows `policy.page_key_required` and `observed.page.authenticated`). Viewers never
  need a key. **No key file ships**, so nothing changes until the owner creates one and the kiosk URL
  carries `&ctlkey=`.
* Caps: 64 events clients, 16 control sockets per screen.

## Config

One site config (owner, 14 Oct): each thing is defined once.

* **Screens** are the destinations in `routes/data/publish-destinations.json` (Flask's display config) that carry a
  `live` block: `{"enabled": true, "gpu": 0, "profile": "hevc-2160p30", "mask_push": true}` (optional
  `timed_reload`; other keys such as `auto` are ignored here). The destination's `name` is `status.screens.<id>.name`,
  and its `maxwidth` x `maxheight` is the render size (`POST /api/sessions {width, height}`, scaled down to the
  profile's pixel count when bigger, even) and the snapshot page's viewport, so a portrait screen (devtest,
  2160x3840) renders and anchors portrait. Read at start; a Flask restart (room_service runs inside it) picks up edits.
* **Rooms**: `routes/data/rooms.json` says which room holds which screens, lights, blinds and sensors. It gives the
  `<room>:<kind>` mode groups (`room_config.py`) and `status.rooms` (`[{id, name, screens, lights, blinds, sensors}]`,
  file order, re-read on change), from which the Room page builds one section per room.
* **Service settings**: `config/room/room.prod.yaml` (LIVE_V1 §3.1, no `screens:`). A `screens:` block there still
  overrides per screen and per field, and may add screens (tests, dev copies). `service.destinations_file` and
  `service.rooms_file` point elsewhere.

Optional sections (additive; defaults in `config.py`):

```yaml
# screens.<id>.profile: hevc-2160p60        # used when a live spec names no profile
# screens.<id>.timed_reload: true           # the page policy's default (modes can change it)
# mask: {min_fetch_interval_s: 1.0, max_requests_per_s: 10, fetch_timeout_s: 2.0}
#   # min_fetch_interval_s: LIVE_V1 §2.2b, at most 1 GET /api/<id>/mask per second per screen: each GET
#   # advances Flask's shared intensity EMA, so faster polling also speeds up the TVs' masks. Don't lower it.
# allowed_hosts: ["192.168.1.205", "pc22.local", "localhost", "127.0.0.1", "::1"]   # Host check (port ignored)
# web_origins: [...]                        # default: from .env (SM_LAN_HOST, VITE_URL)
# timeouts:                                 # AV_PROGRAMME §3.3
#   freeze_ack_s: 5        snapshot_s: 3       session_ready_s: 7     live_visible_s: 10
#   # production (room.prod.yaml): create_session_s 20, exit_session_s 20, snapshot_s 10, session_ready_s 15
#   create_session_s: 5                       # POST session/exit HTTP timeout (inside the freeze)
#   settle_before_go_s: 1.0                   # after ack live_visible, before go (decoder warm-up)
#   held_extra_s: 5        art_visible_extra_s: 10                   # + the out transition's length
#   backoff_s: [2, 5, 15, 30, 60]  fallback_window_s: 600   # retries; the last step repeats: never parked
#   adopt_wait_s: 20       page_hb_idle_s: 2   page_dead_s: 15       step_timeout_s: 120
#   page_behind_s: 5       page_behind_bytes: 262144                 # LIVE_V1 §2.2a.1
# snapshot:
#   executable_path: ~/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome
#   viewport_w: 3840       viewport_h: 2160    timeout_s: 3          warm: true   # viewport: screens without a size
# service:
#   state_dir: ~/.local/state/screen-machine/room
#   destinations_file: routes/data/publish-destinations.json   rooms_file: routes/data/rooms.json
#   session_ttl_s: 120     renew_every_s: 30  sweep_orphans: true
# ingest:                                   # light-sensor ingest, LIVE_V1 §2.2b (room_service/ingest_config.py)
#   enabled: true          mask_source: auto  # auto: sensor while fresh, else Flask /mask, else hold | sensor | flask
#   screens: {north-screen: flask}          # per-screen mask_source
#   filter: {stale_after_s: 10, stale_ramp_s: 30}   # lightmask FilterParams (also config/light.json "filter")
#   overrides_poll_s: 2  eval_interval_s: 1  config_refresh_s: 2  ack_min_interval_s: 0.2
#   max_readings_per_s: 20  max_msgs_per_s: 50  hello_timeout_s: 10  idle_timeout_s: 60  latency_window: 512
# mask:                                     # shaper (DCL v3.1 §4.4), additive to the keys above
#   snap_transition_ms: 200  drift_transition_ms: 1000  drift_interval_s: 3  drift_deadband_brightness: 0.015
#   snap_delta_brightness: 0.05  max_sets_per_s: 10  live_max_sets_per_s: 2  source_change_transition_ms: 2000
# blinds: {enabled: false}                  # the hub blinds become a placeholder (tests, no LAN)
```

`config/room/modes.json` (docs/av/ROOM_STATE_MODEL.md §2; reloaded on change, and saved from the Room page through
`PUT /api/v1/room/config`): `gallery` (label Normal, the default), `live-test` (devtest, `h264-720p30` so a headless
Chrome can play it), `piano`, `off`, and the live modes `ambient` and `disco`, which store and lower the privacy blinds
(ch02, ch03, ch14), store and set the TVs, and put everything back on the way out. Devices without a working adapter
(`tv`, `mirror_ball`, `plug`, `sonos`) are placeholders: accepted, rows skipped, `available: false`. The blinds read the
Bloc hub for real; their writes stay off (`dry_run`) until `config/room/rf/blinds.json` says `"writes_enabled": true`.
`blinds: {enabled: false}` in the room config makes them a placeholder (tests).

## How it behaves

**Screens** (`screens.py`): one state machine per screen, one task each.

| Handshake | Steps [timeout] |
|---|---|
| entry | *(snapshotter page loaded)* → `freeze` → ack frozen [3 s] → snapshot [3 s] → `POST /api/sessions` (anchor PNG, scene, transitions, profile, gpu, width/height) → `session.ready` [7 s; 15 s in production] → `live.attach` → ack `live_visible` [10 s; the page gives up at 8 s with `live_failed`] → settle [`settle_before_go_s`, 1 s: the page holds the anchor while its decoder warms up] → `POST go` → `unfreeze` |
| exit | *(snapshotter page loaded)* → `freeze` → ack frozen [3 s] → snapshot [3 s] → `POST exit` (target PNG) → `session.held` [out + 5 s] → `live.leave{hold_pts}` → ack `art_visible` [out + 10 s] → `DELETE` → `unfreeze` |

* Any failure aborts cleanly: `live.abort` if attached, `DELETE`, `unfreeze`; the screen ends in art.
  An entry failure is retried with backoff 2/5/15/30/60 s; the last step repeats, so a screen never parks
  (21 Oct: no `failed` after failed go-lives). An entry that fails only because stream_service is unreachable
  doesn't count; the screen waits for it.
* Fallback (ack `art_visible` with reason stall/error, `session.error`/`ended`, a 404 on renew, or a
  `kiosk.state` showing art while live): `live.abort` if the page is still on the stream, `DELETE`, then
  the full entry handshake again (freeze reason `reanchor`) with the same backoff, 2/5/15/30/60 s, never parking.
  The fallbacks in the last 10 min are counted for the page's words only. A renderer restart isn't counted and
  goes back as soon as it's up. Phase `failed` now only means live is off for the screen in its config.
* **Live renders whether or not a page shows it** (owner, 14 Oct). With no glass page connected, the entry is
  headless: anchor = a snapshot of the current art (the newest `/output/<id>.jpg|.JPG|.mp4` on Flask, current
  mask) if the art-only page renders it, else black; `POST session` → `session.ready` → `go`, no freeze/attach/
  live_visible. The session carries `headless` (status `observed.headless`). A page that connects later joins it:
  `welcome.live` with the full `attach` (or a `live.attach` if it was promoted from standby or connected
  mid-adoption); its `ack live_visible` (or a `kiosk.state` live on that session) makes it an ordinary page-backed
  session, and its `live_failed` is a fallback (full entry with the page). Viewers get the attach in their welcome
  whenever a session is running. Leaving live with no page (or a page still joining) just ends the session.
  A headless session is adopted after a restart without waiting for a page. With a page connected, the
  entry/exit handshakes are unchanged.
* `PUT /api/v1/screens/{id} {desired, for_s}` (0 < for_s ≤ 86400): after for_s seconds, unless desired was changed
  meanwhile (any other value, or a later PUT without for_s, cancels the timer), the value from before the request
  goes back through the room state model (a hand set, so going back to the mode's value clears the drift). A timed
  PUT over a timed PUT keeps the first one's value to go back to. The deadline is persisted in `screens.json`
  (`expiry`), so a restart keeps it; status shows `desired_expires_in_s` and `desired_expires_to`.
* Desired changes mid-handshake are queued; switching to art during an entry aborts it promptly (before
  `go`, so the abort is invisible). Live → live on the same profile is a `PATCH` (scene/params) in-stream;
  a profile change is exit + entry.
* Pages: `kiosk`/`lab` are glass, `viewer` gets the mask only. The newest glass client is driven; a second
  one raises `screen.duplicate_glass` (log + `observed.duplicate_glass`). `hb` after 2 s idle; a client
  silent for 15 s is dropped (§2.2a.5). Outbox per §2.2a.1: `mask.set`/`policy` latest-value only, control
  messages never dropped and in order, a client > 256 KB or > 5 s behind is closed with 4008 (the page
  reconnects at once for a fresh welcome). Inbound > 50 msg/s → 4029 (the page backs off); no hello in
  10 s → 4002; dead → 4001.
* Reconnects: a page that reconnects with the same `page_load_id` keeps playing. `welcome.live` says which
  session room_service believes it plays but carries **`attach: null`**: a page that is still playing
  doesn't need it, and one that fell back to art while its socket was down (its stall ack lost) must not
  attach and cut straight into the running scene. It stays in art, its `kiosk.state` says so, and re-entry
  runs the full freeze/anchor handshake. A fresh load while live means the session is orphaned: it is
  deleted and the entry runs. A page gone for 20 s while live leaves the session running headless (below). An `unfreeze` that couldn't
  be delivered (socket down mid-handshake) is sent right after the next welcome.
* Page policy: `welcome.policy` and `policy` messages always carry the full `{live_enabled, mask_push,
  timed_reload}`. Modes set `page.<screen>.timed_reload` (a level subsystem): `policy` is sent, then the
  value is read back from `kiosk.state.policy.timed_reload` (`ok`), else `unconfirmed`.
* Snapshots: an ack whose ArtState is identical to the last one snapshotted (within 10 min) reuses that
  PNG (pure function of the state; common on exit with a held mask). A failed exit snapshot is retried once
  (after `ensure_ready`) before the hard cut.
* `POST /api/sessions` and `/exit` are bounded by `timeouts.create_session_s` (8 s), so the worst-case entry
  freeze (3+3+8+5+10 s) stays under the page's 30 s freeze safety. A create that timed out while
  stream_service's events socket is up counts as an entry failure (not "stream_service down").
* Ownership (DCL v3 §4.7.6): room_service records the sessions it created (persisted in `screens.json`;
  stream_service has no owner field yet). Adoption, orphan deletion and the sweep touch only those; other
  instances' sessions are left alone and listed as `stream_service.foreign_sessions` in status. Orphan
  sweep (every renew cycle, and soon after a create that failed client-side): sessions we created that no
  settled controller uses are deleted, so they don't hold GPU memory and an NVENC session until their TTL.
  A create that timed out is reclaimed via its idempotency key. One instance per state dir (`room.lock`).
* Restart: the persisted desired state is re-read and stream_service's sessions listed; nothing touches a
  persisted session before that. The page decides: a page that reconnects with the persisted
  `page_load_id` is still playing it (adopted at once), a new `page_load_id` shows art, and `kiosk.state`
  overrides either. The session the page plays is adopted and the rest deleted; with no page within 20 s
  they are all deleted (the entry runs when the page returns). A freeze left open is released with
  `unfreeze`; a session adopted while still `holding` gets `go`; one adopted mid-exit is aborted.
* Sessions are renewed every 30 s (TTL 120 s).

**Mask pusher** (`mask.py`): listens on Flask's `:8765` as a pure receiver (heartbeat off; it only ever
answers Flask's pings with pongs, as Flask's 30 s watchdog requires, and never sends a data frame). Any JSON
frame with a `lux` key is a trigger (coalesced), with a 1 s backstop; it GETs `/api/<id>/mask` for screens
with `mask_push` and a connected page **at most once per second per screen** (`mask.min_fetch_interval_s`,
LIVE_V1 §2.2b "Interim": each GET advances Flask's shared, read-driven intensity EMA, so polling faster would
also speed up the production TVs' masks). A trigger inside the interval is served when it allows, so a light
change is at most ~1 s late. A value beyond the deadbands
goes out as `mask.set`, except while the screen is frozen or the mode says `sensor_mask: suspended` (the last
value is held). A failed fetch keeps the last mask.

**Light-sensor ingest** (LIVE_V1 §2.2b; DCL v3.1 §4.4). The relay sends readings straight to room_service,
which computes the mask itself with the shared `lightmask` package and pushes only final mask values. **Off
until a sensor keys file exists**: with none, every screen's mask comes from Flask exactly as above.

| Module | Job |
|---|---|
| `ingest.py` | `WS /v1/ingest/lux`: `hello{role:"sensor", sensor, key}` checked in constant time against `~/.config/screen-machine/room_sensors.json` (`{sensor: key}`, 0600, re-read on change) → else close **4401** (also, logged once, when there is no keys file). Then `ingest.lux{sensor, lux, seq, mono_ms, held}`: finite numbers only, clamped 0–200 klx, `seq` strictly increasing (reset on each hello), `held` a bool. `lux.ack{seq}` with the newest seq, coalesced to ≤ 1 per 200 ms (every reading acked within 200 ms; calib-3 drops a link after 5 s without one). > 50 frames/s → **4029**; > 20 readings/s → dropped; no hello in 10 s → 4002; silent 60 s → 4001; a newer socket for the sensor → 4009 for the old one. Host check as everywhere; an `Origin`, if sent, must be one the page sockets accept (the relay sends none: allowed, the key is always required). |
| `sensors.py` | SensorService, wiring only: one `lightmask.LuxFilter` per sensor (snap 0.15 s / drift 2.5 s, spike guard, held, staleness, warm start from `<state_dir>/sensors.json`), `lightmask.ConfigWatcher` over Flask's own files (`config.py` INTENSITY_CFG, `routes/data/intensity.json`, `publish-destinations.json`; refreshed every 2 s in the executor, so the LightSense editor keeps working), `mask_for_dest` per screen, and the overrides read from Flask's `GET /api/<id>/maskstate` + `GET /api/<id>/brightness-override` every 2 s (only for screens on the sensor path). |
| `mask_arbiter.py` | MaskArbiter, a `MaskPusher` subclass and the room's one mask writer per screen. Tiers: **sensor** (fresh sensor; the Flask `/mask` fetch for that screen **stops**, since every fetch advances Flask's shared EMA), **flask** (today's fetch), **sensor-stale** (auto only: the sensor is stale *and* Flask has failed for 10 s → lightmask's ramp to the solar curve over 30 s), **hold**. `ingest.mask_source` per screen: `auto` (default), `sensor` (lightmask only, DCL), `flask` (today). `mask.set` gains `source`: lightmask's strings (`sensor`, `astral`, `manual_override`, `maskoff`, `fixed`, `blend`) or `flask`. A relay `held: true` (calibration capture) pushes nothing until it ends; frozen/suspended screens hold and catch up after. |
| `mask_shaper.py` | Per screen, after the arbiter: a filter snap or re-init, \|Δb\| ≥ 0.05, a source or override change → **at once, 200 ms**; a sensor↔Flask handover → 2000 ms; slow drift → **≤ 1 per 3 s, 1000 ms**, only beyond 0.015 brightness or the warm-alpha deadband; never > 10 mask.set/s per screen (2/s while live). |
| `mask_source.py` | Picks MaskArbiter or MaskPusher at start (MaskPusher if `ingest.enabled: false` or `lightmask`/`astral` is missing), fronts `/v1/ingest/lux`. |

Status: `GET /api/v1/status` has a top-level `mask` section (`ingest` with connections/closes/rejects/relay
lag, `latency_ms` = reading arrival → `mask.set` enqueue **p50/p95** (`immediate`; `deferred` = shaper-delayed
sends), `tiers`, `sensors` (filtered lux, stale, held, solar weight, rejects), `config`, `overrides`). Once a
keys file exists, each screen's `mask` also carries `source`, `tier`, `mask_source`, `sensor`, `held` and its
own `latency_ms`. Config: the `ingest:` section and the shaper keys under `mask:` (listed under Config above;
all optional). `lightmask` needs `astral==3.2` in the venv (`~/.venvs/sm-room/bin/pip install astral==3.2`).

**Owner steps to turn it on** (deploying to the kiosk is the owner's, LIVE_V1 Q11):
1. `~/.venvs/sm-room/bin/python tools/room/ingest_keys/make_sensor_key.py living-room --url ws://192.168.1.205:8700/v1/ingest/lux`
   (writes the key into `room_sensors.json` and the relay's `room.json` locally; prints only paths).
2. Copy that `room.json` to the kiosk and SIGHUP the calib-3 relay: the one-liner in
   `tools/room/ingest_keys/README.md`. The relay keeps its legacy send to `:8765` in parallel.
3. Screens follow the sensor once their page is on `?ctl=` with `mask_push: true`; legacy pages keep Flask's mask.

**Snapshotter** (`snapshot.py`): Playwright with the cached Chrome for Testing, one persistent 3840×2160
DPR 1 page per screen at `{art_page_base}/display/<id>?artonly=1`, warmed at start-up for live-enabled
screens. `__smArtReady` → `__smRenderArt(state)` → CDP `Page.captureScreenshot` with `optimizeForSpeed`
(lossless and pixel-identical to the default PNG encoder, but about 3× faster at 4K). Clip bases: ffmpeg
extracts the frame at `clip_t`, which is served to the page through a Playwright route on its own origin
and rendered as an image. A crashed, closed or timed-out page is rebuilt in the background. `kiosk.state`
showing a new still triggers a prefetch render, so the 4K decode is cached before a cut.

**Coordinator:** the room state model in `roomstate/` (docs/av/ROOM_STATE_MODEL.md v1.1; adapters in `adapters/`,
the modes file in `room_config.py`, the editor routes in `room_api.py`). The paragraph below describes v1, which it
replaced on 14 Oct: diff-based plans (only differences become steps),
`on_exit` before and `on_enter` after, `sequence` groups (a list runs in parallel), `after:` events
(`screens.live` = every screen step in the plan went live; `<key>.<value>`, `<key>.done`, adapter-emitted
events), and step statuses pending → running → ok/failed/unconfirmed/cancelled. A new target cancels pending
steps and replans from the believed state (readable subsystems are read back). Restart resumes an in-flight
transition; one-shot actions fire at most once per departure (a running one at the crash is marked
`unconfirmed`, never re-fired). Kinds: `level` (screens.<id>, sensor_mask), `edge_discrete` / `edge_toggle`
(base class `EdgeSubsystem` for the RF adapters), `action` (`log` built in). Manual changes (`PUT
/api/v1/screens/<id>`) follow `respect`: accepted, reported as `mode.drift` until the next transition.

## API extras (additive to LIVE_V1)

* `status`: `mode.{last_transition, drift, default, modes_error}`; per screen `observed.{detail, frozen,
  glass_clients, duplicate_glass, viewers, page (incl. authenticated), fallbacks_in_window, retry_in_s}`,
  `policy` (+ `timed_reload_applied`, `page_key_required`), `snapshots`; `stream_service.last_error`;
  top-level `flask`, `mask` (sensor ingest, below), `snapshotter`, `service`; `subsystems` also lists
  `page.<screen>.timed_reload`.
* Transition steps also carry `id, kind, group, after, started_at, finished_at`; transitions carry `warnings`.
* `GET /api/v1/modes` also returns `default` and `error`. `PUT /api/v1/screens/<id>` returns `{screen, desired}` (+ `for_s, desired_expires_in_s, desired_expires_to` with for_s).
* Snapshot PNGs also accept `?token=` (an `<img>` can't send a header; optional now that they're open).
* Optional config: `allowed_hosts`, `screens.<id>.timed_reload`, `mask.min_fetch_interval_s`,
  `service.page_keys_file`, `service.sweep_orphans`, `timeouts.{create_session_s, page_behind_s,
  page_behind_bytes, policy_ack_s, sweep_delay_s}`.
* `desired` also accepts the programme's short form `{live: "fractal-zoom", in: …, out: …}`.
* `/renderer/<path>` (`renderer_proxy.py`) passes everything through to stream_service's `/<path>` (any method; HTTP streamed both ways, and WebSocket relayed), so `/renderer/dev`, `/renderer/api/*` and `/renderer/api/events` work on room_service's origin. No auth; the Host check and CORS apply; the upstream's status is relayed; 502 `stream_unreachable` / 504 `stream_timeout`.

## Live controls (`live_api.py`)

The Room page's way to the renderer's breadth (cue, auto director, direction, palette, FX bus; LIVE_V1 1.1
proposal §1.6–§1.11 and the as-built `stream_service/server.py`, which wins where they differ). The page never
talks to stream_service: these routes act on the screen's **current** live session, found as `patch_live` finds
it (`session` set, `layer` live, desired not art; mutations also refuse while a cut is under way). Open on the LAN
like the other operate routes; the Host / Origin / CORS middlewares apply as everywhere.

| room_service | stream_service | Notes |
|---|---|---|
| `GET /api/v1/library` | `GET /api/catalog` + `GET /dev/library` | UI-ready summary: scene presets (source, tags, `x-ui.basic` values, `in_auto`), transition presets (cut first, `duration_s`), effect presets (stage, basic param names), palettes (swatch, family), `sources` with their basic schemas, facets for tag chips, `vocab`, the direction and auto defaults (as built in `director.py`), and a `thumb` URL per item. Gzipped (~70 KB for ~1000 presets). Cached 30 s; dropped on `catalog.changed` and on a stream reconnect. |
| `GET /api/v1/thumb/{name}.png[?v=]` | `GET /dev/thumb/{name}.png` | `name` is a plug-in name (`^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$`, else 400 `bad_name`). JPEG body as stream_service sends it, `Cache-Control: max-age=300`; LRU of 300, 4 upstream fetches at a time, a miss is not re-asked for 60 s (404 `no_thumb`). |
| `POST /api/v1/screens/{id}/cue` `{to, transition?, at?, idempotency_key?}` | `POST /api/sessions/{sid}/cue` `{to, via, at, source: "panel"}` | `to`: a scene preset, a scene spec or `"auto"` (next pick). `transition` → `via`; absent or `"default"` = the renderer's precedence (director's pick when auto is on, else the session `in`). 202 with the stream body plus `recorded`. At most 1 per second per screen (429 `too_fast`, §C.2). |
| `GET /api/v1/screens/{id}/look` | `GET /api/sessions/{sid}` | `{screen, session, state, scene, current_scene, scene_params, transitions, auto, direction, palette, palette_transition_s, beat_follow, masters, fx, cue, current_scene_effects, lead_ms}`. |
| `PATCH /api/v1/screens/{id}/look` `{auto?, direction?, palette?, palette_transition_s?, direction_ease_s?, beat_follow?}` | `PATCH /api/sessions/{sid}` (look fields only) | One route for the session-level axes, mirroring the as-built PATCH. `auto` merges over the current block (as built); `auto: true\|false` is short for `{enabled: …}`; `auto: null` removes it. `direction` is **merged over the session's current direction** here (the renderer replaces it whole; one GET first), `direction.fx` likewise. `palette: null` goes back to each scene's own colours (as built; the proposal says 400). Answers with the look. |
| `GET` / `PUT /api/v1/screens/{id}/fx` `{stack, fade_s?, master?, if_rev?}` | `GET` / `PUT /api/sessions/{sid}/fx` | Pass-through. PUT replaces the whole stack (echo GET entries back to keep them); `if_rev` → 409 `fx_conflict` on a race. |
| `GET /api/v1/screens/{id}/inputs` | `GET /api/sessions/{sid}/inputs` | The show-bus monitor (bpm, beat lock), read-only. |

Errors are `{error, detail}`: 404 `unknown_screen`; 409 `not_live` (no current session, or stream_service says
it is gone); stream_service's 4xx relayed with their status and code (e.g. 400 `unknown_preset`, 409 `bad_state`);
502 `stream_unreachable` (a stream 401 becomes 502 `stream_unauthorized`, so the page never asks for a key).

**Desired follows the glass.** A scene change that starts on the screen's session (a cue from here, or the
renderer's auto director: `session.transition{kind: scene, phase: started}`) is written into the screen's desired
live spec, as `PATCH /live {scene}` already does, so status, a re-entry after a fallback, parameter edits and the
coordinator's readback all see the scene on glass. It is left alone while a newer desired is still on its way.
The look itself (auto, direction, palette, FX) lives in the session only: a fallback re-entry starts a fresh
session without it (proposal §C.1's room-level `look.*` targets are the place for it later).

Tests: `tests/test_live_api.py` (a subclass of the fake stream_service with cue / fx / inputs / `/dev/library` /
`/dev/thumb`): route mapping, 409 `not_live`, the cue rate limit, desired following an auto scene change,
thumbnail names and the LRU, and both caches dropping on `catalog.changed`.

## Tests

```bash
cd ~/dev/screen-machine/room_service
~/.venvs/sm-room/bin/python -m pytest -q                 # everything (~90 s on a busy host)
~/.venvs/sm-room/bin/python -m pytest -q -m "not browser" # skip the headless-Chrome snapshotter tests
```

Everything runs against fakes (`tests/fakes.py`): a fake stream_service (REST + events), fake display pages
that follow the LIVE_V1 §2.2 page rules, a fake Flask (`/api/<id>/mask` and a `:8765` that records every
frame a client sends; it must stay empty), and a fake snapshotter. `test_snapshot.py` drives the real cached
Chrome against `tests/fixtures/artonly.html`, a stand-in implementing `__smRenderArt` until the real page
lands. Covered: full entry/exit; every entry and exit timeout; stall → re-entry with backoff; repeated fallbacks
and entry failures keep retrying, never parked; stream errors; switching to art mid-entry; queued changes mid-exit; in-stream PATCH; page reload,
socket blip and duplicates; restart adoption (live, orphan, mid-entry, no page); coordinator diff, sequence,
`after:`, interruption and restart; mask trigger/deadbands/backstop/rate/suspend/freeze; API auth/CORS/
rate-limited status; token files and redaction. Review additions (`test_hardening.py` and the "Review
fixes" sections): open operate + wrong tokens refused, Host/Origin checks, page keys, outbox latest-value/
order/256 KB/5 s, inbound flood code, hb only when idle, contract defaults, ffmpeg only http(s); the
1 req/s/screen mask rate; welcome without attach + lost-stall-ack re-entry; lost unfreeze re-sent; PATCH
race; orphan sweep and slow create; page timed_reload policy via a mode (and the shipped modes.yaml);
snapshot reuse and exit-snapshot retry; one-shot actions on disk before they fire.

Mask defaults in the harness are faster than production (`min_fetch_interval_s: 0.05`); one test runs the
production 1 s interval.

Sensor ingest: `test_ingest_unit.py` (fake time: config, shaper timing and rate caps, keys, lightmask wiring,
validation, held, stale → solar ramp, **golden parity** with Flask's `/api/<id>/mask` over lightmask's
fixtures, arbiter tiers), `test_ingest.py` (in-process: auth and close codes, Host/Origin, validation and seq
reset, ack cadence, 4029 flood, reading drop, Flask fetch stops/resumes, snap < 50 ms server-side and drift
≤ 1/3 s, held, overrides, config reload, warm start, and **calib-3's own `RoomSink` code** loaded from
`tools/room/latency_cal/lightsense.py` against the server), `test_ingest_e2e.py` (`python -m room_service`
as a process on a free port in 3031–3039 with temp config and keys, a fake relay replaying 50 ↔ 2000 lux
steps and a held window, a viewer page; set `INGEST_E2E_RESULTS=<file>` to keep the measurements) and
`test_make_sensor_key.py` (the helper).
