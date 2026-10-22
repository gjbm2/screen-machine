# Live v1 contracts: stream_service, room_service, display page, web Room page

**For:** the agents building this week's live-screen components in parallel, and screen-machine-2f, which owns this file.
**Status:** v1, frozen for the parallel build on 13 Oct 2026, late. Changes are additive only. A builder that needs a breaking change says so in its report and does not make it.
**Rev 1.1 (14 Oct, 08:30), by owner ruling ("feature breadth, not enterprise QA").** The contract is now light-touch. For leaf features the code is the spec, and there are no more review rounds.
- **Cue, auto, direction, palette, inputs, show bus:** the spec is `proposals/LIVE_V1_cue_auto_inputs.md` plus `proposals/LIVE_V1_cue_auto_inputs_addendum.md`, as built in `stream_service/` by screen-machine-04. Where code and proposal differ, the code wins.
- **Display-page signalling beyond v1:** the design is `docs/av/DISPLAY_CONTROL_LAYER_V3.md` v3.1, and its §8.6 lists the amendments. They are adopted as they get built, and room_service and the page code are the reference.
- **Room state model:** `docs/av/ROOM_STATE_MODEL.md` v1.1. It extends §3 additively.
**Design context:** `docs/AV_PROGRAMME.md` (v2), especially §2, §3, §4 and §5.

```
 web Room page (src/pages/Room, Vite dev :5174/5175)
        │  HTTP + WS (Bearer room token)                        Flask :5000 (prod, READ-ONLY: GET /api/<id>/mask)
        ▼                                                       Flask WS :8765 (prod; room_service listens only)
 room_service :8700 ───────────────── HTTP + events WS (Bearer stream token) ──▶ stream_service :8082 dev / :8702 prod
   coordinator, screens, mask push, snapshotter (headless Chrome)        renderer + NVENC + media WS
        ▲  control WS /v1/screen/<id>                                       │ media WS /media/<session>
        │                                                                   ▼
 display page /display/<id>?ctl=ws://…:8700 ◀──────────────── fMP4 (MSE) ───┘
```

---

## 0. Common conventions

| Item | Value |
|---|---|
| Screen ids | `north-screen`, `south-screen`, `devtest` (Flask destination ids; page route `/display/:screenId`) |
| GPU per screen (default config) | north-screen → 0 (RTX 4060), south-screen → 1 (Quadro P4000), devtest → 0 |
| stream_service | `~/.venvs/sm-stream/bin/python -m stream_service --port 8082` (dev). **Production: 8702**, with `--public-base ws://192.168.1.205:8702`. 8081 belongs to exlib's Metro bundler and is bd's lab default. |
| AV port block | **room_service 8700, show bus 8701 (reserved), stream_service 8702.** `docs/PORTS.md` is the register, kept by screen-machine-ca and mirrored in Spectre. Production instances run like Flask and ngrok: screen-machine-ca's `scripts/ctl` starts each in a GNU screen session (`sm-room`, `sm-stream`), with arguments in `deploy/sm-{room,stream}.env`. There are no systemd units. |
| room_service | `python -m room_service` defaults to **:8700** (production, via `scripts/ctl`; the LAN portproxy and firewall are live). Dev instances use other ports with `--port`, but never 3001 (exlib-server's). The Room page defaults to `<its own host>:8700`. |
| Vite dev server (never `npm run build`) | page work: `VITE_API_URL=http://127.0.0.1:5000 npx vite --port 5174 --strictPort`; Room page work: the same on `--port 5175` |
| Tokens | `~/.config/screen-machine/stream_service.token` and `~/.config/screen-machine/room_service.token`. Each service creates its own on first start: 64 hex chars, mode 0600. Never print a token value in logs or reports. |
| State (outside the repo) | `~/.local/state/screen-machine/room/` (room_service) |
| Presets | `config/live/scenes.yaml`, `config/live/transitions.yaml` (hot-reloaded) |
| Modes | `config/room/modes.yaml` |
| Time | All `*_pts` values are stream seconds: `frame_id / fps`, equal to the video's `mediaTime`. Wall-clock times never cross hosts. |
| Errors | JSON `{"error": "<code>", "detail": "…"}` with a 4xx/5xx status |

---

## 1. stream_service API (consumer: room_service; the dev page in `stream_service/static/` too)

All `/api/*` calls need `Authorization: Bearer <stream token>`. WebSockets can't send headers, so `/api/events` takes `?token=`. The media socket uses a per-session key instead.

### 1.1 Catalog and presets

`GET /api/catalog` →
```json
{
  "sources": [{"name": "fractal_zoom", "label": "Fractal zoom", "description": "…", "internal": false, "schema": {…}}],
  "transitions": [{"name": "crossfade", "label": "Crossfade", "description": "…", "schema": {…}}],
  "filters": [],
  "profiles": {"hevc-2160p60": {"codec": "hevc", "w": 3840, "h": 2160, "fps": 60, "bitrate": "30M", "gop_s": 1.0}, "…": {}},
  "presets": {
    "scenes": {"fractal-zoom": {"label": "Fractal zoom", "source": "fractal_zoom", "params": {"zoom_rate": 0.25}}},
    "transitions": {"crossfade-3s": {"label": "Crossfade 3 s", "transition": "crossfade", "params": {"duration_s": 3.0, "easing": "ease-in-out"}}}
  }
}
```
- `internal: true` marks sources a person never picks, such as `image`.
- **Profiles:**

  | Profile | Use |
  |---|---|
  | `hevc-2160p60` | default for the TVs |
  | `hevc-2160p30` | fallback rung |
  | `h264-720p30` | headless tests: Chrome for Testing can decode H.264 in software but not HEVC |
  | `av1-720p30` | the same |

  Each profile has codec, size, fps, bitrate and GOP length; the GOP is 1 s unless stated.

**Parameter schema** (JSON Schema subset plus UI hints). Every property has a `default`. The web app builds forms from this:
```json
{"type": "object", "properties": {
  "zoom_rate": {"type": "number", "minimum": 0, "maximum": 2, "default": 0.25,
                "x-ui": {"label": "Zoom speed", "step": 0.01, "unit": "e-folds/s", "widget": "slider"}},
  "palette":   {"type": "string", "enum": ["ember", "ice", "neon", "mono"], "default": "ember",
                "x-ui": {"label": "Palette", "widget": "select"}},
  "mirror":    {"type": "boolean", "default": false, "x-ui": {"label": "Mirror"}}
}}
```
Widgets: `slider`, `number`, `select`, `checkbox`, `color` (a string `#RRGGBB`).
`x-ui.basic: true` (rev 1.1) marks the 1–3 headline parameters of a scene or effect. The Room page shows those on the main screen and the rest under "More". Realtime-bound macros are never basic.

**Spec forms.** Anywhere a scene or a transition is named, these forms are accepted:
- a preset name: `"fractal-zoom"`;
- `{"preset": "fractal-zoom", "params": {…overrides}}`;
- `{"source": "fractal_zoom", "params": {…}}` for a scene, or `{"transition": "crossfade", "params": {…}}` for a transition.

Unknown parameters are rejected with 400. Out-of-range numbers are clamped.

### 1.2 Sessions

`POST /api/sessions`
```json
{"screen": "devtest", "gpu": 0, "profile": "hevc-2160p60",
 "anchor": {"png_b64": "<base64 PNG>"},
 "scene": "fractal-zoom",
 "transitions": {"in": "crossfade-3s", "out": "crossfade-3s"},
 "ttl_s": 120, "idempotency_key": "devtest-1697234567-1"}
```
- `anchor` is either `{"png_b64": …}` (any size; letterboxed to the profile size if it doesn't match) or `{"rgb": [r, g, b]}` (a solid colour, for tests).
- The request body can be up to 64 MB.

→ `201`
```json
{"session": "s-3f9a12c4", "state": "starting",
 "attach": {"url": "ws://192.168.1.205:8702/media/s-3f9a12c4?k=<random>", "codec": "hevc",
            "w": 3840, "h": 2160, "fps": 60, "target_buffer_ms": 300}}
```
- `attach.url` uses `--public-base` (e.g. `ws://192.168.1.205:8702`). Without it, the URL uses the request's Host header.
- The same `idempotency_key` returns the same session.

**Session states:**
```
starting ──first IDR muxed──▶ holding ──go──▶ entering ──in transition done──▶ live
live ──PATCH scene──▶ live            (an in-stream scene transition; state stays live)
live|entering ──exit──▶ exiting ──out transition done──▶ held (the target is held until DELETE or TTL)
any ──error──▶ failed ; any ──DELETE/TTL──▶ ended
```

| Call | Effect |
|---|---|
| `POST /api/sessions/{id}/go` `{}` | Holding → entering. 409 if not holding. |
| `PATCH /api/sessions/{id}` `{"scene"?: spec, "params"?: {…}, "transition"?: spec}` | `params` merge into the current scene's params, and the renderer eases numeric values (≈0.8 s). `scene` changes scene inside the stream using `transition`, defaulting to the session's `in`. |
| `POST /api/sessions/{id}/exit` `{"target": {"png_b64"}\|{"rgb"}, "transition"?: spec}` | → `202`. Runs the transition (default: the session's `out`) from whatever is showing into the target, then holds the target and emits `session.held`. Allowed from entering, live or exiting (a re-exit replaces the target). |
| `POST /api/sessions/{id}/renew` `{}` | Extends the TTL. Every authenticated call on a session also renews it. |
| `DELETE /api/sessions/{id}` | → `204`. |
| `GET /api/sessions`, `GET /api/sessions/{id}` | Session summary: `{session, screen, state, profile, created_at, scene, clients, frames, late_frames, render_ms:{p50,p95}, encode_queue}` |
| `GET /api/state` | `{sessions:[…summary], gpus:[{index, name, mem_used_mb}]}` |

### 1.3 Events

`WS /api/events?token=…`. The server only sends; any client message is ignored. Each event carries the session id and its `screen`.
```json
{"type": "session.state", "session": "s-…", "screen": "devtest", "state": "holding"}
{"type": "session.ready", "session": "s-…", "screen": "devtest"}
{"type": "session.transition", "session": "s-…", "screen": "devtest", "kind": "in|scene|out", "phase": "started|done", "pts": 12.35}
{"type": "session.held", "session": "s-…", "screen": "devtest", "hold_pts": 15.3}
{"type": "session.ended", "session": "s-…", "screen": "devtest", "reason": "deleted|ttl|error"}
{"type": "session.error", "session": "s-…", "screen": "devtest", "detail": "encoder exited rc=…"}
```
- `session.ready` is sent when the first IDR of the held anchor has been muxed. It accompanies the move to the holding state.
- `hold_pts` is the pts of the first frame that shows the exit target fully (transition progress 1.0).

### 1.4 Media WebSocket (consumer: the display page's live layer)

`WS /media/{session}?k=<key>`. A wrong key closes the socket with code 4403.

**Server → client:**
1. One text message once the encoder config is known:
   ```json
   {"type": "config", "codec": "hevc", "codec_string": "hev1.1.6.L153.B0", "width": 3840, "height": 2160, "fps": 60, "timescale": 90000, "frame_duration": 1500}
   ```
2. A binary message `[0x01][init segment]`: ftyp + moov. It is low-delay: `mvhd` duration 0 and no `mehd`.
3. Binary messages `[0x02][key:u8][frame_id:u32 BE][moof+mdat]`, starting at the next keyframe after the client joins.
   - pts = `frame_id / fps`.
   - When a client falls behind (8 frames queued), the server drops to the next keyframe.
4. Text messages `{"type": "eos"}` when the session ends.

**Client → server (optional):** about once a second, `{"type": "stats", "presented": n, "dropped": n, "buffered_ms": x, "edge_jumps": n}`.

### 1.5 Renderer plug-ins (stream_service internal; listed so the catalog is understood)

- **Source:** `setup(ctx, params)`, `update(params)`, `render(t_local, dt, out_rgba)`, `close()`.
- **Transition:** `render(progress_eased, a_rgba, b_rgba, out_rgba)`. `duration_s` and `easing` (`linear`, `ease-in-out`, `ease-in`, `ease-out`) are common parameters.
- **Frames** are CuPy `uint8` arrays of shape (H, W, 4) holding sRGB-encoded values. They are converted once to BT.709 limited-range NV12 for NVENC.
- **v1 plug-ins:** `image` (internal), `solid`, `fractal_zoom`, `cut`, `crossfade`.

---

## 2. Display page ↔ room_service control protocol

`WS ws://<ctl-host>:8700/v1/screen/<screen_id>?role=kiosk|viewer|lab`

**Where the page gets the address:** only from an explicit `?ctl=ws://host:port` URL parameter.
- **If it is absent, or `?ctl=off`, the page opens no control socket and behaves exactly as it does today.** So a frontend build that ships early can't change the production kiosks: their URLs carry no `ctl`.
- Turning it on for a TV later means adding `?ctl=` to that kiosk's URL, an owner-approved launcher change.
- While no socket is open, the page also behaves exactly as today.

**Envelope:** `{"v": 1, "type": "<type>", "data": {…}}`.

*(17 Oct)* Live diagnostics are burned into the stream by the renderer (`docs/LIVE_DIAGNOSTICS.md`): the session create body and PATCH take `hud: bool`; the page draws nothing over the video (D-B unchanged) and only adds `role` to its media stats.

### 2.1 Messages

**room_service → page**

| Type | Data |
|---|---|
| `welcome` | `{epoch, screen, policy: Policy, mask: MaskSet\|null, live: null\|{session, attach, phase}}` where `Policy = {live_enabled:bool, mask_push:bool, timed_reload:bool}` |
| `policy` | `Policy`: sent whenever a policy value changes (e.g. a mode sets `timed_reload` off). Fields absent from a message keep their current value. *(Added 13 Oct late, additive.)* |
| `mask.set` | `MaskSet = {rev, brightness, warm_hex, warm_alpha, transition_ms}` |
| `freeze` | `{fid, reason: "enter"\|"exit"\|"reanchor"}` |
| `unfreeze` | `{fid}` |
| `live.attach` | `{session, url, codec, w, h, fps, target_buffer_ms}` (the `attach` from §1.2) |
| `live.leave` | `{session, hold_pts}` |
| `live.abort` | `{session, reason}`: drop live now. If live is visible, hard-cut to art; then tear down. |
| `hb` | `{}` (whenever the socket has been idle for 2 s) |

**page → room_service**

| Type | Data |
|---|---|
| `hello` | `{role, screen, page_load_id, build, caps:{mse_hevc, mse_h264, mse_av1}, flags:{nomask, nocolour, fit}}` |
| `ack` | `{kind:"frozen", fid, state: ArtState}` |
| `ack` | `{kind:"live_visible", session, media_time}` |
| `ack` | `{kind:"art_visible", session, media_time\|null, reason:"exit"\|"exit-timeout"\|"stall"\|"abort"\|"error"}` |
| `ack` | `{kind:"live_failed", session, reason}` (e.g. a warm-up timeout or an MSE error before the cut) |
| `kiosk.state` | every 5 s: `{heap_mb, long_tasks, layer:"art"\|"live", frozen, base_url, mask:{brightness, warm_hex, warm_alpha}, policy: Policy (as applied), next_reload_at: string\|null, live:{session, fps, presented, dropped, stalls, buffered_ms, edge_jumps}\|null}` |
| `hb` | `{}` |

```
ArtState = {base_url, kind:"image"|"video", clip_t: number|null,
            mask:{brightness, warm_hex, warm_alpha},          // as drawn, after nomask/nocolour
            flags:{nomask, nocolour, fit:"fit"|"fill"},
            viewport:{w, h}}
```

### 2.2 Page rules

1. **Liveness:** a socket with no inbound frame for 6 s is dead. Reconnect with full-jitter backoff `random(0, min(10 s, 0.5 s·2^n))`. One socket per page.
   - **One controller per document load** *(added 14 Oct, from the preview smoke test)*. `page_load_id` is generated once per document load, at module scope, and never changes until the document reloads. The control client and controller are created once, as a module singleton or a ref initialised once, never inside `useMemo` or a render path.
   - **Things that must not create a new controller or `page_load_id`:** re-renders, StrictMode's double invocation, error-boundary resets, remounts on layer changes, and state changes.
   - **Observed in the preview:** a mid-entry socket close and reopen with a new `page_load_id` made room_service re-run the entry handshake.
   - **room_service side:** a `hello` with the same `page_load_id` while live is a *reconnect*. It keeps the session and never re-enters.
2. **Mask:**
   - While the socket is up and `policy.mask_push` is true, the page applies `mask.set` and pauses its 3 s mask poll.
   - If the socket has been down for 5 s, the poll resumes.
   - A failed poll keeps the last mask. It never resets to brightness 0.
3. **Freeze:**
   - Stop applying new bases and masks; keep only the latest of each.
   - Let any running mask transition finish (≤ its `transition_ms`, or 1000 ms for the legacy fade).
   - Dismiss overlays and close the controls panel. Pause a clip base.
   - Then send `ack frozen` with the ArtState.
   - Stay frozen until `unfreeze`. After a 30 s safety timeout, unfreeze on its own and log it.
   - On unfreeze, apply the queued base and mask as normal art changes.
4. **`live.attach`:**
   - Mount the live layer *under* the art stack: an MSE `<video>`, muted, no CSS effects, created imperatively rather than through React state.
   - Append the init segment, then fragments. Call `play()` at `playbackRate` 1.0 once at least `target_buffer_ms` is buffered.
   - **Entry cut:** in the `requestVideoFrameCallback` for the first presented frame, hide the art stack in one property change, then `ack live_visible`.
   - If that takes more than 8 s, send `ack live_failed` and tear down.
5. **While live:**
   - New bases and masks still apply to the hidden art stack, with no CSS transition, unless frozen.
   - Overlays are dropped and counted.
   - `playbackRate` is never changed. If the buffer has run more than 2× `target_buffer_ms` ahead for 2 s, jump to `buffered.end − target_buffer_ms` and count an edge jump.
6. **`live.leave{hold_pts}`:**
   - **Exit cut:** on the first presented frame with `mediaTime ≥ hold_pts − 0.5/fps`, show the art stack in one property change, `ack art_visible` with reason `exit`, then tear down.
   - If no such frame arrives within 20 s, cut anyway with reason `exit-timeout`.
7. **Stall:** no new presented frame for 2 s while live → show the art stack, `ack art_visible` with reason `stall`, tear down.
8. **Teardown:** close the media socket; abort the SourceBuffer if it is open; `video.removeAttribute('src')`; `video.load()`; remove the element.
9. **Timed reloads** (the page's own hard reloads at 02/06/10/14/18/22h with a fade to black, `src/pages/DisplayPage/index.tsx` ~371-420; they exist because of past browser instability and memory leaks):
   - **`policy.timed_reload`** is set by the room's mode, as ordinary room state.
     - `true`: today's schedule.
     - `false`: no timed reloads at all. Cancel any that are scheduled, and report `next_reload_at: null`.
     - With no control socket, or before the first `welcome`: today's schedule, unchanged.
   - **Backstop, whatever the policy:** a timed reload never runs, or starts its fade, while live, frozen, or between `live.attach` and the end of teardown. If one falls due then, it waits until 60 s after the page has returned to art, and only if `timed_reload` is still true.
   - *(Added 13 Oct late, additive.)*
11. **Resource budget on the kiosk** (Intel N95, two 4K Chromes; owner, 13 Oct late: "make sure not to overload chromium on the mini-PC"). *(Added, additive.)*
    - **Mask animation must be compositor-only:**
      - Brightness is a static black full-screen layer whose `opacity` is `1 − brightness`. It replaces today's transition of `background-color` to `rgba(0,0,0,·)`, which repaints the full 4K layer on every frame of every fade. The result is pixel-identical: same alpha over black.
      - Warmth animates only the `opacity` of the multiply layer.
      - A `warm_hex` change applies without a transition, at most once per 5 s.
      - Both layers get `will-change: opacity`.
    - **At most one mask commit per animation frame:** a burst of `mask.set` coalesces to the latest. No React re-render per mask message; refs only.
    - **No per-frame work beyond the minimum:**
      - The media socket's binary frames go to `appendBuffer` as views (`subarray`) with no copies or JSON.
      - One append queue; no per-frame React state, console logging or allocation-heavy objects.
      - rVFC callbacks only count and compare.
      - Stats are aggregated and sent at most 1/s on the media socket. `kiosk.state` goes at most every 5 s.
    - **Control-socket traffic is small and quiet:** JSON text, with no per-frame or per-mask acks (acks only for freeze, cuts and failures). `hb` goes only when idle. Reconnect uses jittered backoff, never a tight loop. One control socket and one media socket at most.
    - **Server side:** at most 10 `mask.set` per second per screen, deadbanded and coalesced (§2.2a). There are no messages to the page during live except `policy`, `live.*`, `freeze`/`unfreeze` and `hb`; `mask.set` goes to the hidden art stack at most 2/s.
    - **Later, only if needed:** if main-thread jank shows on the kiosk, move the media socket and appends into a dedicated Worker (MSE in Workers, Chrome ≥108).
    - **The page reports** `heap_mb` (`performance.memory.usedJSHeapSize`) and a long-task count in `kiosk.state`, so load on the kiosk is visible.

12. **Black, never white:** the page's own root background is black, and `document.body` is black while the display page is mounted. Each layer has its own error boundary. An error in the live layer is treated as a stall.

### 2.2a Server rules for the control socket (room_service side) *(added 13 Oct late, additive)*

The page's control channel must never depend on Flask's legacy :8765 server. The rules:
1. **Per-client outbound queue, never unbounded:**
   - `mask.set` and `policy` are latest-value only: a newer one replaces an unsent older one.
   - Control messages (`freeze`, `unfreeze`, `live.*`) are never dropped and keep their order.
   - A client with more than 256 KB unsent, or more than 5 s behind, is disconnected. The page reconnects and gets a fresh `welcome`.
2. **`welcome` is authoritative:** it carries the full current state (policy, mask, live). Revs increase per message type within an epoch, and the page drops stale ones.
3. **One driven page per screen, any number of subscribers** *(pub/sub, owner 15 Oct; was "one glass client")*. One glass page (kiosk or lab) is *driven*: it does the freeze/ack/cut handshakes. Every other page on the screen (a standby glass page, any `viewer`) is a *subscriber* and shows whatever the screen shows: its `welcome` carries `live.attach` when a session is running; it gets `live.attach {rejoin: true}` at go, when a session is replaced and at the end of restart adoption; `live.leave` on exit; `live.abort` on abort, fallback or failure. A subscriber whose ack shows it lost the stream is re-offered `rejoin` (backoff 2, 4, 8, 15, 30 s; at most 10; never after unsupported codec, live disabled or session ended). No limit on subscribers (rate limits stay). The driven page is the TV's own device (its User-Agent, versions removed, kept across restarts): another device opening `/display/<id>` stays a subscriber, and the own device can reclaim. `screen.duplicate_glass` is still raised for two glass pages. Note: a stray second glass tab on the kiosk now plays the stream too (extra decode on the N95).
4. **Inbound limits:** frames up to 64 KiB; 50 messages/s; only the types in §2.1 are accepted for the client's role.
5. **Liveness:** `hb` after 2 s idle. A client silent for 15 s is dropped.
6. **Process isolation:** room_service runs alone and does no blocking work on its event loop. HTTP calls, snapshots and file writes are async or run in executors.

### 2.2b Light-sensor ingest *(added 13 Oct late; next increment, not required in the current build)*

The sensor must not depend on Flask's legacy :8765 server or on Flask computing the mask.

`WS ws://<room host>:8700/v1/ingest/lux`:
- The relay sends `{"v":1,"type":"hello","data":{"role":"sensor","sensor":"living-room","key":"…"}}`.
  - **The key is required:** guests share the Wi-Fi, so an unauthenticated ingest would let anyone set the screens' brightness.
  - room_service checks it against a 0600 file of per-sensor keys and closes the socket with code 4401 on a mismatch.
  - The relay reads its URL and key from `~/.config/light-relay/room.json` (mode 0600) on the kiosk, at start and on SIGHUP. With no file, the sink is off.
- Then, on each new measurement: `{"v":1,"type":"ingest.lux","data":{"sensor":"living-room","lux":312.5,"seq":118233,"mono_ms":9912345,"held":false}}`.
  - Readings are event-driven (Yocto `registerValueCallback`), at up to ~10 Hz, the Yocto-Light-V5 limit; the sensor is now on firmware 70007.
  - A 1 Hz heartbeat reading is sent as well.
  - The rate can drop to about 1.4 Hz (measureType 5) during a calibration capture. That is not a fault.
- **`held: true`** means a latency-calibration capture is running and the relay is sending a held value.
  - room_service must not change any mask from held readings. It keeps the last mask until `held` is false again, so the calibration flashes are never re-masked and the run doesn't measure its own mask changes.
- The server replies with `lux.ack{seq}` to every `ingest.lux`, or at least once a second while readings arrive. The relay (calib-3) drops a link that has gone 5 s after a send without any ack.
- **Validation:**
  - Readings must be finite numbers: NaN and ±inf are rejected. Values are clamped to 0–200 klx.
  - A sequence number that goes backwards is rejected. The check **resets on each `hello`**, in case the kiosk's clock steps (calib-3 seeds `seq` from the wall clock at relay start).
  - `mono_ms` is the kiosk's monotonic clock and is used only to judge a reading's age within one connection, never compared with server time.
- **Keys:** room_service stores per-sensor keys in `~/.config/screen-machine/room_sensors.json` (0600). A helper writes the matching `room.json` for the kiosk, so the key never passes through chat or logs. Deploying it is the owner's step.
- A relay that can't connect falls back to its legacy send to :8765.

**room_service computes the mask itself.** The page never sees lux: it receives only final mask values, so no mapping logic moves into the browser.
- **One implementation, not a fork:** the mapping becomes a pure, shared Python module (no Flask imports, no globals) that room_service uses now and Flask's `compute_mask` can switch to later. Today's Flask code is the reference. The module covers:
  - **sensor path:** sensor → `target_group` → destinations; the piecewise-linear lux→intensity curve from `routes/data/intensity.json`;
  - **colour:** mired interpolation, kelvin→sRGB, the gain against the reference white, `warm_alpha`;
  - **solar fallback** (`_solar_index` with astral, `east_west_bias` power, `day_stretch` gamma, `night_floor`);
  - **per-destination `intensity_cfg`** (`adjust`, lat/lon, per-screen keys);
  - **overrides.**
- **The same config files stay in use,** reloaded when they change, so the LightSense editor keeps working: `routes/data/intensity.json`, INTENSITY_CFG from `config.py` (read as `compute_mask` does today, or moved to `config/light.json` later), and the `publish-destinations.json` `intensity_cfg` and groups.
- **Golden tests:** the module must reproduce today's `compute_mask` output over a grid of lux values, times of day, destinations and override states, except for smoothing.
- **Smoothing changes on purpose.** Today's intensity EMA (α = 0.2, `routes/lightsensor.py`) advances on every *read*, from every caller, and its state is shared across screens. Its time constant therefore depends on how many clients poll it. The port replaces it with the time-based filter below;
- with the DCL §2.6 filter: log-lux; a 0.15 s snap when the reading is more than 0.75 EV away for 2 consecutive samples; otherwise a 2.5 s drift; a deadband; constants in a config file reloaded on change.
- **Stale sensor:** no reading for 10 s → the solar curve, ramped over 30 s.
- **Overrides** (`maskon`/`maskoff`/`brightness-override`) are read from Flask's GET endpoints every 2 s until they move to room_service.
- **Interim** (until the relay is switched over and the port lands): room_service *connects to* Flask's :8765 as a client that never sends, and fetches `/api/<id>/mask`, **at most once per second per screen.** Each fetch advances Flask's shared, read-driven EMA, so polling faster would also speed up the production TVs' masks.
- **Lux for the legacy pages:** TVs without `?ctl` still get their mask from Flask, so the relay keeps its legacy send to :8765 in parallel until every screen is on the new path.

### 2.3 Art-only mode (consumer: room_service's snapshotter)

`/display/<id>?artonly=1`:
- Renders the art stack only: still or clip, brightness layer, warm layer. No overlays, controls panel, live layer, polling or sockets. Black background.
- Exposes `window.__smRenderArt(state: ArtState): Promise<void>`.
  - It applies the state with no CSS transitions: base, mask, flags.
  - It waits for `img.decode()`, or for a clip to be seeked to `clip_t` and its `seeked` event.
  - It then waits two `requestAnimationFrame`s and resolves.
- Sets `window.__smArtReady = true` once mounted, before the first `__smRenderArt` call.
- The composited pixels must be identical to the normal page's art stack for the same ArtState and viewport.

---

## 3. room_service HTTP API (consumer: the web Room page)

- **Auth (owner ruling, 13 Oct late): operate open, edit gated.** Anyone on the Wi-Fi may operate the room; the owner's example is a visiting pianist switching from "live set" to "Sonos tracks". So there are no guest logins or pairing.
  - **Open on the LAN, no token:**
    - status, modes, catalog, snapshots;
    - `PUT /api/v1/mode`, `PUT /api/v1/screens/{id}`, `PATCH /api/v1/screens/{id}/live`;
    - setting a device target by hand;
    - the events WS;
    - `GET /healthz`.
  - **Gated by the admin token** (`Authorization: Bearer <room token>`; the events WS takes `?token=` only for admin streams):
    - editing config (modes, transitions, groups, labels, devices, room.yaml);
    - editing or forgetting stored values;
    - safety settings;
    - anything that writes `config/room/*`.
  - **Machine channels always need keys:**
    - *(superseded 14 Oct: no auth anywhere in the AV stack, owner ruling)* the kiosk control socket needs no key; `role=viewer` pages are subscribers (rule 3 of §2.2a) and need none either;
    - sensor ingest needs a per-sensor key (§2.2b).
  - **Browser protection:**
    - **Host:** the `Host` header must be one of the allowed hosts (`192.168.1.205:<port>`, `pc22.local:<port>`, `localhost` and `127.0.0.1` for dev), which defends against DNS rebinding.
    - **Origin:** a request carrying an `Origin` header must match the allowed origins, which is room_service's own origin plus the dev origins. That stops a random web page open on a phone from driving the room.
    - **No Origin:** non-browser LAN clients without one are allowed, since they could operate anyway.
  - **Never** on WAN, ngrok or Flask.
  - **Compatibility:** clients that already send the token keep working.
  - *(Changed 13 Oct late; the relaxation is backward-compatible.)*
- **CORS:** allow the configured web origins (default: `http://localhost:5174`, `http://localhost:5175`, `http://127.0.0.1:5174`, `http://127.0.0.1:5175`, `http://192.168.1.205:8080`, `http://192.168.1.205:5000`). Handle `OPTIONS` preflight for the `Authorization` and `Content-Type` headers.

| Call | Result |
|---|---|
| `GET /healthz` | `{"ok": true, "version": "…"}` |
| `GET /api/v1/status` | below |
| `GET /api/v1/modes` | `{modes: {<name>: {label, state, on_enter, on_exit, sequence}}}` |
| `PUT /api/v1/mode` `{mode}` | `202 {transition_id}` |
| `PUT /api/v1/screens/{id}` `{desired: "art" \| {live: {scene, transitions:{in, out}, params?, profile?}}}` | `202`. A change made by hand under the `respect` policy. |
| `PATCH /api/v1/screens/{id}/live` `{scene?, params?, transition?}` | Passed to the session (live parameter edits). 409 if the screen isn't live. |
| `GET /api/v1/catalog` | stream_service's catalog (§1.1), passed through |
| `GET /api/v1/screens/{id}/snapshots/{anchor\|target}.png` | The last anchor or exit-target PNG (404 if none) |
| `WS /api/v1/events?token=…` | below |

`GET /api/v1/status`:
```json
{"mode": {"current": "gallery", "target": null,
          "transition": null | {"id": "t-…", "from": "gallery", "to": "live-test",
                                "steps": [{"subsystem": "screens.devtest", "action": "live:fractal-zoom", "status": "pending|running|ok|failed|unconfirmed|cancelled", "detail": "…"}]}},
 "screens": {"devtest": {"desired": "art" | {"live": {…}},
                         "observed": {"layer": "art|live", "phase": "idle|freezing|snapshot|starting|attaching|entering|live|exiting|leaving|failed",
                                      "session": "s-…"|null, "page_connected": true, "page_role": "kiosk|lab|viewer",
                                      "kiosk_state": {…}|null, "last_ack_at": "…"},
                         "mask": {"brightness": …, "warm_hex": …, "warm_alpha": …, "suspended": false},
                         "last_error": null | "…"}},
 "stream_service": {"reachable": true, "url": "http://127.0.0.1:8082"},
 "subsystems": {"mirror_ball": {"kind": "edge_discrete", "available": false, "believed": null}}}
```

**Events:**
```json
{"type": "status", "data": {…}}
{"type": "mode.transition", "data": {"id", "phase": "started|step|done|cancelled", "step": {…}}}
{"type": "screen.phase", "data": {"screen", "phase", "detail"}}
{"type": "screen.mask", "data": {"screen", "brightness", "warm_hex", "warm_alpha"}}
{"type": "stream", "data": {…}}
{"type": "log", "data": {"level", "msg"}}
```
`status` is the full status, sent on connect and on every change, at most 4 per second. `stream` events are stream_service events passed through.

### 3.1 Service config

```yaml
# config/room/room.yaml (not secret)
stream_service: {url: "http://127.0.0.1:8082", token_file: "~/.config/screen-machine/stream_service.token"}
flask: {url: "http://127.0.0.1:5000", ws: "ws://127.0.0.1:8765/"}   # read-only; never send a frame on :8765
art_page_base: "http://127.0.0.1:5174"   # Vite dev until a page build with ?artonly is deployed; then http://127.0.0.1:5000
mask: {deadband_brightness: 0.005, deadband_warm_alpha: 0.002, transition_ms: 200, backstop_s: 1.0}
screens:
  devtest:      {gpu: 0, live_enabled: true,  mask_push: true}
  north-screen: {gpu: 0, live_enabled: false, mask_push: false}   # off until the owner turns them on
  south-screen: {gpu: 1, live_enabled: false, mask_push: false}
web_origins: ["http://localhost:5174", "http://localhost:5175", "http://127.0.0.1:5174", "http://127.0.0.1:5175",
              "http://192.168.1.205:8080", "http://192.168.1.205:5000"]
```
- A request to go live on a screen with `live_enabled: false` is refused with 409 `live_disabled`.
- `welcome.policy` mirrors these flags. `timed_reload` defaults to `true`.
- Modes can set `timed_reload` per screen as ordinary room state (target `page.<screen>.timed_reload`; see `docs/av/ROOM_STATE_MODEL.md`). room_service then sends a `policy` message and reads the applied value back from `kiosk.state`.

### 3.2 Modes file (v1: screens and sensor mask only)

```yaml
# config/room/modes.yaml
default: gallery
modes:
  gallery:
    label: Gallery
    state:
      screens: {north-screen: art, south-screen: art, devtest: art}
      sensor_mask: follow
  live-test:
    label: Live test (devtest)
    state:
      screens:
        devtest: {live: {scene: fractal-zoom, transitions: {in: crossfade-3s, out: crossfade-3s}, profile: h264-720p30}}
      sensor_mask: follow
```
State keys for subsystems that have no adapter yet (e.g. `mirror_ball`, `blinds`) are accepted, reported as `available: false`, and skipped with a warning.

---

## 4. Out of scope for v1

Hue, Maestro/Art-Net, Sonos, the RF devices (a separate spike), phone pairing, flash limiting, input bindings, content push, overlay push, and moving the kiosk to the LAN origin.
