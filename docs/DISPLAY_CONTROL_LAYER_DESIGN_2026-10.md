<!-- 2026-10-13. Produced from: a read-only map of today's display layer and of REFACTOR_PLAN.md; three independent
designs (A evolve the display page, B rebuild it as one long-lived app, C server-authoritative desired state), all
bound by the owner's constraints (one layer in the display page; dumb mini-PC; light sensor is just a remote input);
two judges (24/7 robustness, delivery/risk); synthesis. Owner quotes: see the D-A to D-E directives block below. -->

# Display control layer: final recommendation (2026-10-13)

> **Revised 2026-10-13 (owner directives).** These owner directives are binding on everything below:
> - **D-A** "art mode: all the compositing happens in chromium / DOM." The page composites the still or clip, the ambient brightness mask, overlays and any art-mode transitions.
> - **D-B** "live mode: all the compositing happens in live renderer (local GPU) and then _all_ the display browser should do is stream mp4 4K at 60fps. our new display page architecture needs to reflect that." The page shows one full-viewport `<video>` (MSE, HEVC 3840×2160@60) and nothing over it. There is no mask ("live mode is full intensity — it's a lighting rig at that point, not a simulated artwork"), no overlays, HUD or controls, no CSS opacity, filter, transform or blend on the video, and no page-side fades. The workstation GPU renderer draws every effect, transition (crossfade, "screen melt"), text and fixture-level effect into the stream.
>   - *(17 Oct)* Live diagnostics (`docs/LIVE_DIAGNOSTICS.md`) are **burned into the stream** by the renderer, never drawn over the `<video>`: D-B is unchanged.
> - **D-C** No TV Game Mode, ever. No Chrome crossfades (the N95 is too weak). Every art↔live change is an instantaneous **hard cut** between frames that are indistinguishable (minor MP4 artefacts accepted). On entry, the stream opens on the exact on-glass still, mask included, and holds it until the page has cut and acked. On exit, the renderer converges to the target still with the then-current mask and holds it until the page has cut back.
> - **D-D** `playbackRate` never exceeds 1.0. Latency is controlled by a hard jump to the live edge, never by speeding up. The renderer paces frames evenly at exactly 60 fps.
> - **D-E** The WS v2 control layer (commands, acks, health, `kiosk.state`) stays in the page in both modes but draws nothing in live mode.
>
> **Facts behind the revision:** measured on 2026-10-13, see `docs/KIOSK_INFRASTRUCTURE_NOTES.md` §3. The kiosk now runs Wayland (sway + `kiosk-wayland.sh`), and the X11 kiosk is the disabled fallback. Under sway, pre-made 4K60 HEVC plays with 0.03 % dropped frames, so the earlier "biggest open risk" (4K30 presentation) is resolved and 4K60 is the target rung. TV Game Mode is not in use: over Samsung IP Control both TVs reported pictureMode=FilmmakerMode with gameMode=Auto at 19:16 UTC (before the sway switch) and at 19:37 UTC; Auto engages only on an ALLM request, which the kiosk never sends. All the sway 4K60 measurements (19:18-19:41 UTC, including the post-reboot run) were taken in that state, and toggling Game Mode On and back to Auto at 19:36 UTC changed neither EDID (hash 993716ac) nor the sway outputs. So 4K60 does not depend on Game Mode (§6 item 0, satisfied).
>
> **What changed:**
> - §1: modes, host, risk.
> - §2.1: I1 and I2, plus a new invariant I6.
> - §2.2: a live-renderer row and the sway host row; the page stack is split into art and live, and live warms up *under* the art stack; overlays and the controls panel are unmounted while live.
> - §2.3: 4K60 examples, the anchor ack (with the mask as drawn), the cut, fallback and recovery acks, `live:"cut"`, `caps.host` and the `kiosk=lab` role.
> - §2.4: anchor, re-anchor and exit state; liveMachine; the reducer keeps live mounted through `leaving`.
> - §2.5: entry and exit handshakes replace the crossfades; stalls hard-cut and re-anchor; the rung change.
> - §2.6: the mask is art-only, is held during every handshake and is committed with no transition at a cut.
> - §2.7: rows for fades, the renderer and Chrome respawn; reloads never fall during live.
> - §3: V1 now uses a production Chrome over CDP; steps 3, 7, 8 (Game Mode Off: already satisfied), 9 and 10; effort for steps 9 and 10.
> - §4: rows, including plan text that already carries the superseded live design; section names instead of plan line numbers, and a Status column.
> - §5 and the summary list: item 9 is settled, item 15 is reworded, item 20 is new (22 is new but moot), and item 21 (sequencing) is now numbered.
> - §6: the evidence, a new item 0 (4K60 without Game Mode: satisfied) and items 1–4, 7, 8, 11, 12 and 13, plus the run constraints.
>
> Art-mode behaviour (stills, clips, mask, overlays) is unchanged.

**For:** the owner, and the session that maintains `docs/REFACTOR_PLAN.md`.

**Method:** read-only analysis at HEAD `2dad861b` plus the working tree. Nothing was run, edited or restarted.

## 0. Basis for the choice

**Winner: Approach A, evolve today's `DisplayPage` in place, used as the delivery spine.**
- It scored best on delivery (8) and second on robustness (7, versus C's 8).
- Its steps are the smallest and each is reversible.
- There is no parity port.
- It already matches the plan's P4 step 6 (shadow acks) and P4b step 3 (the page applies `mask.set`), which retrofit the current page.

**Ideas grafted from C:**
- Per-screen `policy` switches delivered by the server, so rollback never touches the mini-PC.
- Shadow acks before anything is push-driven.
- Push order: mask, then overlays, then content.
- The mask is applied outside React.
- A server-precomputed offline mask schedule (D14).
- An `adopting` barrier after a core restart, and a timeout on every live state.
- A sensor heartbeat, server-sent config and input clamps.
- A change log that records the cause of each change.
- Extension parity as an explicit owner decision.

**Ideas grafted from B:**
- Black `html`/`body` background.
- Origin allowlist, role-scoped inbound messages and a rate limit.
- A `viewer` role and a duplicate-kiosk alert.
- MSE and `<video>` nodes owned imperatively, not by React.
- Light constants in a file that is reloaded when it changes.
- A server-driven recovery ladder.
- A spare N95 as a staging kiosk.

**A's must-fixes, both fixed:**
1. Every reload the page starts itself is gated on a healthy server, and the page background is black (§2.7, step 0).
2. P2 is a hard gate before any server change ships. Every step's primary rollback is a runtime switch. The timeline is re-baselined (§3).

**Other judges' must-fixes, honoured where their parts were grafted:**
- v2 is routed by WebSocket path (B's must-fix).
- Live self-heals when a page reconnects, and no screen waits on another (C's).
- No `base_gen` chain; ordering uses revs per message type (C's).
- Profile changes end the old session before starting the new one until the N95 is measured (C and B).

---

## 1. Recommendation in one paragraph

Keep one web page per TV: the existing `/display/north-screen` and `/display/south-screen`.

- **What the page does:** it does everything itself.
  - It holds a live connection to the server and learns from it what to show, how bright to be and which overlays to draw.
  - **Art mode** (D-A): it composites everything in the DOM: the still or slow-motion clip, the brightness mask, overlays and art-mode transitions.
  - **Live mode** (D-B): it plays one full-viewport 4K60 HEVC stream with nothing over it. The live renderer on the workstation GPU composites every effect and transition into that stream.
  - It switches between the two modes inside the same page with an instantaneous hard cut between identical frames, never a fade (D-C). It never navigates to another page.
- **Who decides:** the server decides what each screen should show. The page reports back exactly what it is showing, so any mismatch can be seen and alerted on.
- **The mini-PC stays dumb:** sway with one Wayland Chrome per TV on a fixed URL, the launcher `kiosk-wayland.sh`, which relaunches a Chrome that has died, and the existing network and deadman scripts. No app logic. The X11 kiosk stays installed as a disabled fallback.
- **The light sensor becomes a small independent service.** It sends readings to the server from whichever machine it is plugged into. The server smooths them and pushes the brightness to both screens within tens of milliseconds, instead of today's 3-second poll. The mask applies in art mode only; live runs at full intensity.
- **When something fails,** a screen keeps its last good picture and brightness instead of going black.
- **How we get there:** by evolving today's page in small steps: light first, then overlays, then content, then live. Each step is switched on per screen from the server and can be switched back in seconds.
- **What it removes from the current plan:** the iframe and separate player page, the local light loop on the mini-PC, the kiosk agent, and the rebuild of the display page.
- **Cost:** about the same as the plan to reach live video (roughly 30–41 owner-days). The light fix reaches the screens after roughly 18–25 owner-days, before any page rebuild or live work, and the leak fixes ship in week one.
- **Biggest open risk:** live 4K60 from the workstation. The N95 is no longer the risk: under sway, a pre-made 4K60 HEVC file dropped 0.03 % of frames on both TVs (§6). Live 4K60 from the lab generator still dropped 6–7 %, because the generator missed its 16.7 ms deadline about 2 % of the time and the lab player ran above 1.0×. Three things must be proven before 4K60 live is promised (§6):
  - the renderer paces frames evenly at 60 fps;
  - it reproduces the page's still and mask closely enough for the hard cuts;
  - ~~4K60 still reaches the panel with TV Game Mode Off (D-C).~~ Satisfied 2026-10-13: Game Mode is not in use and 4K60 holds (§6 item 0).

---

## 2. Architecture

### 2.1 Invariants (written into the plan as §2 principles 11–13)

| # | Invariant |
|---|---|
| **I1 One page** | In normal operation each kiosk Chrome loads exactly one page, `/display/<id>`. That page owns the control socket, screen state, stills, clips, live video, the brightness mask and warm tint, overlays and health reporting. In art mode it composites all of these. In live mode it shows only the stream (I6). Nothing navigates it, re-points it, or iframes another app page into it. Admin `set-url` (`routes/admin_api.py:170-264`) and same-URL reloads are maintenance or rollback tools only. The plan's D23 sandboxed overlay iframe sits *inside* the page as a containment box, so it is compatible (art mode only; unmounted in live, I6). |
| **I2 Dumb host** | The mini-PC runs only these:<br>• `kiosk.service`: sway 1.9 plus `/usr/local/bin/kiosk-wayland.sh`. The launcher starts one `--ozone-platform=wayland` Chrome per TV, respawns a dead Chrome with the same URL and keeps north on north.<br>• Deadman and network scripts.<br>• `light-relay.service` while the sensor is plugged in here: a host-agnostic publisher (I3), not display logic.<br>It holds no display, mask or content logic and runs no CDP agent. The X11 kiosk (Xorg plus `kiosk-loop.sh`, one X screen per TV) is the disabled fallback `kiosk-x11.service`. It cannot play 4K60 (`KIOSK_INFRASTRUCTURE_NOTES.md` §3). |
| **I3 Sensor is remote** | The light relay is a host-agnostic publisher configured only by server URL, sensor name and an optional key. Nothing on the kiosk reads it locally. |
| **I4 Never black, never white, unless told** | Black appears only for `mode: blank` or before the first content after a cold boot. Today a failed mask fetch blacks the screen (`useMask.ts:46-50`). A crash or reload shows white today, because the body uses the light theme (`src/index.css:6-7`), `dist/index.html` sets no background, and there is no ErrorBoundary. |
| **I5 Server decides, page reports** | The page may degrade *toward the still* on its own, immediately, and must report it. Every upgrade or retry is a server decision. |
| **I6 Art in the DOM, live on the GPU** (D-A to D-E) | **Art mode:** the page composites the still or clip, the brightness mask and warm tint, overlays and art transitions.<br>**Live mode:** the page shows one full-viewport `<video>` (MSE, HEVC 3840×2160@60).<br>• Nothing is drawn over it, and it has no CSS opacity, filter, transform or blend.<br>• Live runs at full intensity, with no mask.<br>• The live renderer draws every effect, transition and piece of text into the stream.<br>**Switching:** art↔live changes are hard cuts between indistinguishable frames that the renderer holds, never page fades.<br>**Other rules:** `playbackRate` ≤ 1.0. The TVs never use Game Mode (Game Mode is switched Off once the §3 step 8 test passes; it is On today). The control layer runs in both modes and draws nothing in live mode. |

### 2.2 Components

| Component | Where it runs | Job |
|---|---|---|
| Display page: today's `src/pages/DisplayPage`, evolved | Each kiosk Chrome | New strict modules: `control/` (ControlClient, reducer, protocol types) and `live/` (MseLivePlayer, liveMachine). The mask is written through refs inside rAF. Overlay scripts run once per overlay id. There is one ErrorBoundary per layer. `health/` sends `kiosk.state` and owns the health-gated reload policy. In live mode only the stream is drawn. `control/` and `health/` keep running there but draw nothing (D-E). |
| Realtime hub: plan P4's `sm/realtime` | sm-web, on its own thread and asyncio loop, port :8765 | Serves the paths `/v2/screen/<id>`, `/v2/ingest/<kind>` and `/v2/operator`. The legacy `/` handler stays byte-for-byte unchanged. Only the path router goes into `overlay_ws_server.ws_main`; the hub code lives where the plan puts it, so nothing has to be reworked later. |
| ScreenStateStore + ChannelService | sm-web | Desired state per screen: base, mode, live intent, policy. Claims and leases (D12), the live state machine, and persistence in `state/screens/<id>.json`. |
| SensorService + pure `compute_mask` | sm-web | Lux filter, staleness, warm start; publishes `mask.set`. |
| KioskRegistry + KioskMonitor | sm-web | Acks and `kiosk.state`; `GET /api/screens/<id>/glass` (desired vs applied); Siren alerts; the restart ladder. |
| Live renderer (bd, P6A) | WSL, workstation GPU (CUDA) | Composites everything shown in live mode (D-B): content, every effect and transition (crossfade, "screen melt"), text and fixture-level effects.<br>• Draws the entry **anchor** (and each re-anchor) and the exit **target** exactly as the page composites the still and mask, then holds them until told to move on (§2.5).<br>• Paces frames evenly at the rung's rate: exactly 60 fps on the 4K60 target rung (D-D), and exactly 30 fps on the 30 fps fallback rungs, each frame held for two refreshes. A late render repeats the previous frame in its slot rather than shifting the timeline.<br>• Feeds sm-stream. |
| sm-stream (bd, P6A) | WSL, port :8081 | One NVENC session per stream, fMP4 over WebSocket straight to the page, readiness signal, per-client backlog stats. The session API also carries the anchor, diverge and converge calls, and reports when a hold has started. |
| light-relay | Sensor host (today the mini-PC, over USB) | Typed `ingest.lux` to `/v2/ingest/lux`. |
| `kiosk.service`: sway 1.9 plus `kiosk-wayland.sh` | mini-PC | One Wayland Chrome per TV on a fixed URL with `?kiosk=north\|south`. A dead Chrome is respawned (this exists; the rate limit and health gate are to be added), and north is kept on north. The disabled fallback is `kiosk-x11.service` (Xorg plus `kiosk-loop.sh`: hotplug layout, no respawn today). |

**Z-order inside the page, bottom to top (art mode, D-A):**

| Layer | z-index | Notes |
|---|---|---|
| `html`/`body` background | — | black |
| **LiveLayer** | 1 | New; `position:absolute; inset:0`. Mounted from `connecting` until the detach after the exit cut (connecting, warming, ready, playing, stalled, recovering, leaving). Covered by ArtStack until the cut. |
| **ArtStack** | 2 | New container with an opaque black background; it holds the layers below. |
| ├ `MediaDisplay` (still or clip) | — | as today |
| ├ Brightness | 5 | `index.tsx:334-344` |
| ├ Warm (multiply) | 6 | `index.tsx:347-360` |
| ├ Overlays | 9999 | |
| └ Controls panel | above overlays | |

**Live mode (D-B):**
- ArtStack is `visibility:hidden`. While live, the Overlays layer and the controls panel (and the D23 overlay iframe) are **unmounted**, not merely hidden; their scripts are stopped and removed with them.
  - Only `MediaDisplay` and the brightness and warm layers stay mounted, hidden, so the stall and exit cuts can show them in one frame.
  - A guard rule `.art-stack[data-hidden] * { visibility: hidden !important; }` backs this up, because a descendant that sets `visibility:visible` would otherwise still be painted.
  - A contract test asserts that while live every element outside LiveLayer and its ancestors computes to `visibility:hidden` or `display:none`.
- LiveLayer's `<video>` is the only thing drawn:
  - 3840×2160 at 1:1, full viewport;
  - no CSS opacity, filter, transform, blend or transition on it or its ancestors;
  - nothing over it, so no mask, overlays, HUD or controls panel.

**How the layers are used:**
- This reverses the plan's order (live above the base). Live warms up *underneath* the art stack, so nothing is ever drawn over it once it is visible.
- Each cut is a single change made in one callback:
  - entry: ArtStack is hidden;
  - stall or exit: ArtStack is shown over the video.
- From C we take the invariant that **the base layer is never unmounted and stays decoded while live is shown**. A stall or exit cut is then one property flip onto a picture that is already decoded. The page runs `img.decode()` before an exit cut, and a clip base stays paused while live.
- If the lab shows that a covered video does not present frames (§6, item 4), LiveLayer warms up *above* ArtStack at `opacity:0.001` instead.
  - The entry cut, in the same rVFC callback, removes the opacity **and** sets ArtStack `visibility:hidden`, so live composites exactly as in the default order and nothing remains on the video.
  - The stall cut, in one callback, shows ArtStack and puts LiveLayer back to `opacity:0.001`, so recovery warms up the same way as entry. The exit cut, in one callback, shows ArtStack and sets LiveLayer `visibility:hidden` before the detach. Neither ever animates opacity.
  - The reducer does not change. §6 item 3(a) is re-measured in this variant to confirm VideoEnhance stays at 0 %.

### 2.3 Control protocol (v2)

**Endpoint**
- Address: `ws://${location.hostname}:8765/v2/screen/<id>`. The host is derived when the page runs, so nothing is baked in (today `useOverlayWebSocket.ts:4` and `api.ts:104` are baked at build time).
- **Why routing by path is required:** today's handler waits 0.5 s for a first frame. Any first JSON frame makes the client a *sender*, and that frame is relayed to every viewer (`overlay_ws_server.py:138-177`). A v2 `hello` sent on `/` would therefore be broadcast to all screens, and the page would never be registered as a receiver.
- websockets 15.0.1 is installed; it exposes `request.path` and `serve(origins=…)`.

**Roles**
- `kiosk`: the page carries `?kiosk=north|south`, set in the launcher's URLs (`kiosk-wayland.sh`; `kiosk-loop.sh` on the X11 fallback), or `?kiosk=lab` for the V1 vehicle (§3). Only kiosk clients count as what is on glass.
  - `kiosk=lab` is accepted only on `devtest`. It counts as devtest's kiosk for acks, `/glass` and live attach, and is excluded from production alerts and from `kiosk.duplicate`.
- `viewer`: any other browser on `/display/<id>`. It receives pushes, does not attach live unless `?live=1` is set, and never raises alerts.
- `sensor`: the relay.
- `operator`: read-only summaries. Commands go over HTTP.

**Envelope:** `{"v":2,"type":…,"epoch":"e7f3","rev":N,"data":{…}}`.
- `epoch` is random for each hub start.
- `rev` counts per retained type per screen; for `screen.state` it is persisted and monotonic.
- No field is ever compared across hosts' clocks.

**Server → page**
```jsonc
{"v":2,"type":"welcome","epoch":"e7f3","data":{"build_id":"index--tLZUHxv","hb_ms":2000,
  "snapshot":{"policy":{…},"screen.state":{…},"mask.set":{…},
              "overlays":[{"overlay_id":"ov-81","html":"…","position":"bottom-left","remaining_ms":3200}]}}}
{"v":2,"type":"policy","rev":3,"data":{"ctl":"push","mask_source":"push","overlay_source":"push",
  "content_source":"poll","live_enabled":false,
  "offline":{"mask_hold_s":600,"schedule":[[0,0.42,"#F6D0B5",0.031],[3600,0.30,"#F2C29E",0.045]]}}}
{"v":2,"type":"screen.state","rev":120,"data":{"mode":"live",
  "base":{"url":"/output/north-screen.jpg?v=118","kind":"image","rev":118},
  "live":{"session_id":"s-4c2e","phase":"on_air","visible":true,
          "anchor":{"base_rev":118,"mask_rev":90312,"clip_t":null},
          "attach":{"transport":"ws-fmp4-mse","url":"ws://sm.lan:8081/ws?session=s-4c2e&client=north",
                    "codec":"hvc1.1.6.L153.B0","w":3840,"h":2160,"fps":60,"target_buffer_ms":300}},
  "transition":{"base":"through_black","live":"cut"}}}
{"v":2,"type":"screen.state","rev":125,"data":{"mode":"live",…,"live":{"session_id":"s-4c2e","phase":"on_air","visible":true,
          "anchor":{"base_rev":118,"mask_rev":90420,"clip_t":null,"anchor_pts":812.250}}}}   // re-anchor after a stall (§2.5)
{"v":2,"type":"screen.state","rev":131,"data":{"mode":"still",
  "base":{"url":"/output/north-screen.jpg?v=130","kind":"image","rev":130},
  "live":{"session_id":"s-4c2e","phase":"leaving","exit":{"base_rev":130,"mask_rev":90977,"hold_pts":5123.383}}}}
{"v":2,"type":"mask.set","rev":90312,"data":{"brightness":0.44,"warm_hex":"#F6D2B8","warm_alpha":0.029,
  "transition_ms":150,"source":"sensor","srv_ms":81234567.8}}
{"v":2,"type":"overlay.show","data":{"overlay_id":"ov-82","html":"…","duration_ms":5000,"position":"top-right","clear":true}}
{"v":2,"type":"overlay.clear","data":{"overlay_id":null}}
{"v":2,"type":"kiosk.reload","data":{"reason":"build","when":"next_art_transition"}}
{"v":2,"type":"hb","data":{"srv_ms":81236000.1}}
```

**Page → server**
```jsonc
{"v":2,"type":"hello","data":{"role":"kiosk","screen":"north-screen","kiosk":"north","client_id":"k-9f1",
  "page_load_id":"p-77a","build":"index--tLZUHxv","have":{"epoch":"e7f3","state_rev":117,"mask_rev":90311},
  "caps":{"mse":{"hevc":true,"h264":true},"power_efficient":{"hevc":true},"max":{"w":3840,"h":2160,"fps":60},
          "host":"sway"},   // "sway"|"x11", from a launcher URL parameter (kiosk-loop.sh adds &host=x11)
  "local_flags":{"nomask":false,"nocolour":false,"fill":false}}}
{"v":2,"type":"ack","data":{"kind":"mask","rev":90312,"phase":"applied","echo_srv_ms":81234567.8,"recv_to_commit_ms":6.9}}
{"v":2,"type":"ack","data":{"kind":"anchor","rev":119,"base_rev":118,"mask_rev":90312,"clip_t":null,"fit":"contain",
  "drawn":{"brightness":0.44,"warm_hex":"#F6D2B8","warm_alpha":0.029}}}   // exactly what is on glass at `starting`, mask as drawn after nomask/nocolour
{"v":2,"type":"ack","data":{"kind":"state","rev":120,"phase":"visible","layer":"live","cut_media_time":0.0}}
{"v":2,"type":"ack","data":{"kind":"state","rev":120,"phase":"failed","reason":"live-warmup-timeout"}}
{"v":2,"type":"ack","data":{"kind":"state","rev":120,"phase":"fallback","reason":"stall"}}
{"v":2,"type":"ack","data":{"kind":"state","rev":120,"phase":"recovered"}}
{"v":2,"type":"ack","data":{"kind":"state","rev":125,"phase":"visible","layer":"live","cut_media_time":812.250}}   // re-anchor cut
{"v":2,"type":"ack","data":{"kind":"state","rev":131,"phase":"visible","layer":"base","cut_media_time":5123.383,
  "drawn":{"brightness":0.41,"warm_hex":"#F6D0B5","warm_alpha":0.031}}}   // exit cut; `drawn` must match the exit target
{"v":2,"type":"ack","data":{"kind":"state","rev":131,"phase":"visible","layer":"base","reason":"exit-timeout"}}   // unmatched exit cut
{"v":2,"type":"kiosk.state","data":{"applied":{"state_rev":120,"mask_rev":90312},"shadow":false,
  "glass":{"layer":"live","base_url":"/output/north-screen.jpg?v=118","live_session":"s-4c2e"},
  "live":{"state":"playing","fps":60.0,"dropped":3,"stalls":0,"rate":1.0,"edge_jumps":0,"ahead_ms":310,"rx_kbps":29800,"decoder":"hw"},
  "mask":{"brightness":0.44,"drawn":false},"overlays_suppressed":0,
  "ctl":{"mode":"push","reconnects":2},"heap_mb":71,"uptime_s":40211,"last_error":null}}
{"v":2,"type":"hb","data":{"echo_srv_ms":81236000.1}}
```

**Sensor ↔ server** (on `/v2/ingest/lux`)
```jsonc
{"v":2,"type":"hello","data":{"role":"sensor","sensor":"living-room","client_id":"light-relay@media-server","key":"…"}}
{"v":2,"type":"ingest.lux","data":{"sensor":"living-room","lux":312.5,"seq":118233,"mono_ms":9912345}}
{"v":2,"type":"cfg","data":{"report_hz":20,"heartbeat_s":1,"min_change_pct":1.0}}          // server → relay
{"v":2,"type":"lux.ack","data":{"seq":118233}}                                            // ≥1/s, so the relay can detect a dead link
```

**Ordering and resync**
- `welcome` always carries the full snapshot, and the snapshot is authoritative. On a new epoch the page resets its rev baselines.
- Within an epoch the page drops any retained message whose `rev` is at or below the one it has applied.
- The page compares by **content key**, never by rev: base URL and rev, live `session_id`, overlay ids. So a reconnect never remounts a still or a live stream that has not changed.
- Overlays come back in the snapshot with `remaining_ms`, so none are lost across a blip.

**Liveness**
- The server sends `hb` whenever it has been idle for 2 s.
- The page treats **6 s with no inbound frame** as a dead socket, closes it and reconnects. This is what catches the case where the powerline link is up but the path is dead (`KIOSK_INFRASTRUCTURE_NOTES.md` §5).
- The server treats a kiosk that has sent no `kiosk.state` or `hb` for 60 s as stale.

**Reconnect**
- Full-jitter backoff: `random(0, min(10 s, 0.5 s·2^n))`. `n` resets after 30 s connected. Reconnect at once on the `online` event or close code 4008.
- One socket per page; this fixes the zombie reconnect at `useOverlayWebSocket.ts:47-58`.

**Degraded mode**
- It starts 5 s after the socket is lost.
- The page then polls `GET /api/screens/<id>/state` every 15 s (same origin, same reducer). Until content push ships, the legacy pollers remain the content path anyway.
- All polls get an in-flight guard and a 4 s `AbortController` timeout.
- While the WS is down but HTTP works, `kiosk.state` is POSTed to `/api/kiosks/state` every 30 s.

**Security** (plan principle 7, "reach is the control", is kept)
- `origins=` allowlist for the browser roles.
- Inbound messages are scoped by role: a kiosk may send only `hello`, `ack`, `kiosk.state` and `hb`; a sensor only `ingest.lux`.
- Inbound frame cap of 64 KiB; 50 msgs/s, otherwise close with 4008.
- `screen_id` is validated against the destinations registry.
- Lux is clamped to 0–200 klx and a sequence number that goes backwards is rejected.
- **Recommended:** an optional per-sensor key, because the v2 paths are reachable from the WAN until 8765 closes.
- No client can publish to other clients. Acks affect only observability.

**Versioning**
- `v` is the major version. Changes within a major version are additive; unknown types and fields are ignored. An unsupported major is closed with 4400 and the page falls back to degraded polling.
- The JSON Schema in `schema/realtime.v2.json` is the source of truth, with contract tests on both sides.

### 2.4 State model per screen

**Server (`state/screens/<id>.json`, atomic writes)**
```
ScreenState {screen, rev,
  base:   {url, kind: image|video, rev, published_at},
  intent: {mode: auto|live|blank, owner, lease_expires_at (server UTC), live_spec:{source, ladder}},
  live:   {session_id, phase: starting|on_air|leaving|adopting, visible, attach, rung,
           anchor:{base_rev, mask_rev, clip_t, anchor_pts (re-anchor only)}, exit:{base_rev, mask_rev, hold_pts}} | null,
  hold:   {mask, overlays} while an entry, re-anchor or exit handshake is open (§2.5, §2.6),
  policy: {ctl, mask_source, overlay_source, content_source, live_enabled, offline}}
effective mode = blank if intent=blank; live if intent=live and live.phase in {on_air, adopting};
                 otherwise still/clip from base.kind
```

**Claims and arbitration** (D12, unchanged)
- The order is `base < scheduler:<dest> < api/alexa < operator`.
- A publish during live updates `base` underneath (rev+1). The live view is not interrupted.
- While live, and while any entry, exit or re-anchor handshake is open, the page swaps a new base into the hidden ArtStack with no transition: `img.decode()` (or load and pause a clip), then replace it in one commit. Through-black runs only while ArtStack is visible in art mode. A stall or exit cut therefore always reveals a fully decoded still with no transition in progress.

**Other server-side state**
- Mask overrides and `maskoff` are persisted in `state/mask_overrides.json`. Today they are lost on restart (`routes/display.py:19,23`).
- `last_mask` is persisted for a warm start.
- Every desired change is appended to `logs/screens/<id>.jsonl` with `cause{actor, corr}`, together with every observed transition. "Why was south black at 21:14?" becomes a grep.

**Page**
- A pure reducer computes `view = {base, live (only if live.phase in {on_air, adopting, leaving} and (role=kiosk or ?live=1) and policy.live_enabled), blank}`.
  - During `leaving` the effective mode is already still/clip, but the live view stays mounted until liveMachine's exit cut and detach (§2.5).
  - The server runs the exit handshake before it turns `policy.live_enabled` off. If the flag drops with no `leaving`, the page makes an unmatched failure cut and reports `reason:"policy-off"`.
- `liveMachine` runs this cycle, or ends in `failed`. Every state has a timeout.
  - `idle → anchoring` (clip paused, anchor acked)
  - `→ connecting → warming` (under the art stack, with the renderer holding the anchor)
  - `→ ready` (warm, `play()` called, still under the art stack, waiting for `visible:true`; its timeout is the 30 s anchor hold limit)
  - `→ playing` (entry cut done)
  - `→ stalled` (hard cut to the base) `→ recovering` (under the art stack) `→ playing` (re-anchor cut)
  - `→ leaving` (waiting for `hold_pts`) `→ idle` (exit cut, then detach)
- **"Is it on glass?"** `PUT /api/screens/<id>/channel` returns the new `rev`. `GET /api/screens/<id>/glass` shows whether the kiosk's acked rev has reached it.

### 2.5 Live-mode switch sequence

Every art↔live change is a **hard cut between indistinguishable frames** (D-C). The renderer holds the frame on each side of a cut until the page has cut and acked, so the cut never races the stream. The page never fades, ramps opacity or crossfades. Every visible transition into, out of or during live is drawn by the renderer into the stream (D-B).

**Going live**
1. **Trigger.** A scheduler `{"action":"channel","mode":"live","source":{…},"lease":"10m"}`, an operator `PUT /api/screens/<id>/channel`, or Alexa/the API. ChannelService records the claim; this is idempotent on (screen, owner, spec hash).
2. **Anchor (`starting`, timeout 10 s).**
   - The server waits until no overlay is showing on that screen. Overlays last seconds; an operator start clears them instead.
   - It then publishes `screen.state` rev+1 with `phase:"starting"`. From here until the entry ack it holds `mask.set` and `overlay.show` for that screen (§2.6). It also holds base changes: a publish in the window updates only the server's `base`, and the page starts no still-to-still transition until the ack, so no through-black can land inside the anchor window (proposed by screen-machine-10, AV programme).
   - The page lets any running mask transition finish, closes the controls panel and pauses a clip base on its current frame.
   - It acks `{kind:"anchor", base_rev, mask_rev, clip_t, fit, drawn}`, which is exactly what is on glass: `drawn` holds the mask values as drawn, after `nomask`/`nocolour` (§2.3).
3. **Start the session (timeout 5 s).**
   - The rung is chosen from the link-group budget (north and south share one mini-PC link) and the kiosk's `hello.caps`, including `caps.host` (x11 → start at 4K30).
   - `POST sm-stream /api/sessions` with an idempotency key, `ttl_s=120` and the anchor:
     - base URL;
     - `clip_t`;
     - the fit and mask values from the anchor ack, exactly as drawn (after `nomask`/`nocolour`).
   - The renderer draws the anchor exactly as the page composites it. It uses the same fit and the same black-alpha and multiply arithmetic, from one shared formula with golden tests against page screenshots.
   - It holds the anchor, evenly paced at the rung's frame rate, until told to diverge (hold limit 30 s). Then wait for `ready` (first IDR muxed).
   - On timeout: Siren `stream.start_failed`. The server releases its holds, the screen stays on the still, and a paused clip resumes.
4. **Publish.** `screen.state` rev+1 with `phase:"on_air"` and the attach.
   - For scheduled events the server may publish `visible:false` up to 10 s early. The page warms up and, after `visible:true` arrives, cuts in the next rVFC callback, provided a frame was presented in the last 100 ms. Otherwise it waits up to 2 s and then reports `failed`. The held anchor makes that cut match.
5. **Warm up under the art stack.**
   - The page mounts LiveLayer beneath ArtStack (§2.2). LiveLayer owns its `<video>` and MediaSource imperatively; React only renders the container.
   - The page opens the media WebSocket and appends until `readyState ≥ HAVE_ENOUGH_DATA` and the buffer holds at least `target_buffer_ms`. Then it calls `play()` at `playbackRate` 1.0.
   - If that takes more than 8 s, the page sends `ack failed`, unmounts the player and stays on the still.
6. **Cut.** In the rVFC callback for the first frame presented after `visible:true` (the first presented frame when `visible` was never false), which is the held anchor, the page hides ArtStack: still and mask. The overlay layer is empty (step 2) and the controls panel is closed; both are then unmounted for the rest of live (§2.2).
   - This is one property change, so the next composited frame is the video alone.
   - The frames on either side of the cut are the same picture, apart from MP4 artefacts.
   - The page sends `ack visible`, with `layer: live`.
   - This replaces the earlier 10-frame warm-up gate and the 1 s crossfade.
7. **Diverge.** Only after that ack does the server release its holds and tell the renderer to diverge (`POST /api/sessions/<id>/diverge`). For example, the renderer ramps from the masked anchor to full intensity and then starts its effects.
   - If no `ack visible` arrives within 10 s of `visible:true`, and in any case within the 30 s anchor hold limit, the session ends and the retry policy in step 8 runs.
8. **Hold.** Reconcile runs every 5 s and renews the TTL.
   - If the page acked `failed`, or the entry ack timed out (step 7), the server bumps the rev and retries: after 10 s, 30 s, 2 min and 5 min, at most 5 attempts per 30 min. After that the screen stays on the still and Siren raises `live.client_failed`.

**Player** (D-D)
- `playbackRate` stays at 1.0 and never exceeds it. With 60 fps content on a 60 Hz panel, any speed-up forces Chrome to drop frames.
- Latency is controlled by a **hard jump to the live edge**: `currentTime = buffered.end − target_buffer_ms`. The jump happens when the buffer has run more than 2× `target_buffer_ms` ahead for 2 s; these thresholds are provisional, and §6 item 7 sets them.
- Jumps are counted in `kiosk.state` (`edge_jumps`).

**Leaving live**
1. Stop, lease expiry or a higher claim.
   - The server fixes the exit target: the current base, which may have changed during live, and the then-current mask with the page's `local_flags` applied (the same flags as at the anchor ack; the controls panel that changes them is unmounted while live).
   - It holds `mask.set`, `overlay.show` and base changes for that screen until the exit ack (the exit target is the base and mask named in `exit`); overlays and any newer base are delivered only after it, as an ordinary art-mode change.
   - It publishes `mode: still|clip` with `phase:"leaving"` and `exit{base_rev, mask_rev}`.
2. The page prepares the target behind the video:
   - it decodes the target (`img.decode()`; a clip target is loaded and paused on its first frame);
   - it applies the target mask values to the hidden ArtStack, with no CSS transition (§2.6);
   - its overlay layer stays empty until after the exit cut. Overlays received during live are dropped and counted in `overlays_suppressed` (§5 item 20 interim).
3. The server calls `POST /api/sessions/<id>/converge` with the target. The renderer converges to it with its own transition, drawn into the stream (for example a ramp down from full intensity, or a screen melt).
   - It then holds the target still with that mask.
   - sm-stream reports the hold's first `pts`, and the server republishes `exit` with `hold_pts`.
4. **Cut.** In the rVFC callback for the first presented frame with `mediaTime ≥ hold_pts` after step 2 has finished (target decoded, mask applied), the page shows ArtStack (`<img>` + mask) over the video, in one frame.
   - A clip target starts playing after the cut.
   - The page sends `ack visible`, with `layer: base` and the `drawn` mask values. A mismatch with the exit target raises `kiosk.state_drift`.
   - On that ack the server releases its holds (§2.6).
5. Only then does the page detach the stream: abort and remove the SourceBuffer, revoke the object URL, call `removeAttribute('src')` then `load()` to free VA-API, and close the media WS.
6. The server calls `DELETE` on the session **after that ack**.
   - If `hold_pts` has not been presented within 15 s of `leaving`, the page hard-cuts to the target anyway. This cut is unmatched; it is a failure path, reported with `reason:"exit-timeout"`.
   - Without an ack, the server deletes the session after 20 s.

**Profile or rung change**
- **End-then-start** (when live is on glass): leave live through the exit handshake (the renderer converges to the still), then go live at the new rung through the entry handshake. Both cuts match, and the still shows in between.
- An A/B overlap (two decoders) is used only once §6 shows the N95 can decode both without drops. Even then it is a hard cut on a frame both streams hold, never a crossfade.
- In-place `changeType` is not used.

**Stalls** (no fades)
- **No new presented frame for 2 s:** the page hard-cuts to the still in one frame, showing ArtStack with the latest stored mask, applied with no CSS transition.
  - This cut is unmatched; it is a failure path. The page acks with `phase:"fallback"`.
  - The player stays attached under ArtStack. When data returns, it hard-jumps to the live edge.
- **Recovery:** after 3 s of healthy frames, counted under ArtStack, the page reports `recovered`. Recovery **re-runs the entry handshake** so the renderer re-anchors:
  1. The server holds the mask, overlays and base changes. No overlay is showing: the Overlays layer stays unmounted while live, including during the fallback (§2.2).
  2. The page acks the anchor.
  3. The server asks the renderer to re-anchor (`POST /api/sessions/<id>/anchor`, which returns `anchor_pts`) and republishes `screen.state` with `live.anchor.anchor_pts`.
  4. The page cuts on the first presented frame with `mediaTime ≥ anchor_pts` and acks.
  5. Only then does the renderer diverge.
- **If the stall triggers a rung step-down** (§3, step 10), there is no recovery on this session. The still is already on glass, so the server deletes the session without an exit handshake and starts the lower rung through the entry handshake. Same-session recovery runs only when no step-down is due.
- **3 fallbacks within 10 min:** `failed`. The screen stays on the still, the page reports it, and the server decides what to do.

**Self-heal** (C's must-fix)
- **Control socket reconnects while playing:** the stream continues, because content is compared by `session_id` and nothing remounts.
- **Page loads while a valid claim exists:** it receives `live` in its snapshot, starts on the still and runs the entry handshake. The server re-anchors the session on what that page shows, with `anchor_pts` as in Stalls → Recovery. The entry handshake on a fresh page follows the same rules: no overlay showing, mask and overlays held.
- The retry budget resets on a new `page_load_id`.
- No screen ever waits for another screen. Group flips are deferred.

**Core restart while live**
- `welcome` is held for at most 3 s, until one reconcile pass has run.
- A session not yet confirmed is sent as `phase:"adopting"`. The page keeps playing if the `session_id` matches.
- The 120 s TTL is longer than a restart, so the stream survives.

### 2.6 Light-sensor path

**Path:** relay (any host) → `/v2/ingest/lux` → SensorService → pure `compute_mask` → retained `mask.set` for every screen in the sensor's target group → page applies it through refs inside rAF → page acks.

**The mask applies in art mode only** (D-B: "live mode is full intensity — it's a lighting rig at that point, not a simulated artwork").
- **While live:** the page draws no mask. It still stores each `mask.set` and acks it with `phase:"stored"`, so a stall or exit cut can apply the current value at once. Mask values written to a hidden ArtStack, or at a cut, are committed with `transition: none` before the cut frame. `transition_ms` applies only to `mask.set` updates while ArtStack is visible in art mode.
- **The renderer gets the mask at every matched cut** (§2.5): the page's drawn values go into the entry anchor and each re-anchor, and the then-current values go into the exit target.
- **During a handshake** the server holds `mask.set` (and `overlay.show` and base changes, §2.5) for that screen, so the page and the renderer agree on the frame either side of the cut. The holds run from `starting` to the entry ack, from a stall recovery's `recovered` report to its re-anchor cut ack, and from `leaving` to the exit ack. SensorService keeps filtering during a hold, and the latest value is sent when the hold is released.

**1. Relay**
- Location: `deploy/sensors/light-relay/`, with the source brought into the repo. Today it exists only on the box (`media-server/README.md:738-820`).
- Reporting: Yocto value callbacks on any change of at least 1%, up to 20 Hz (to verify on the device), plus a 1 Hz heartbeat.
- Configuration: `SM_INGEST_URL`, `SENSOR_NAME`, optional `SM_SENSOR_KEY`. The server can retune it with `cfg`.
- Legacy `{"sensor_name","lux"}` frames on `/` are still accepted until the relay is upgraded.

**2. Filter** (advances only on ingest, unlike today's EMA, which advances on every read: `lightsensor.py:93-100, 201-226`)
- Works in log-lux.
- **Snap:** τ 0.15 s when the reading is more than 0.75 EV from the current state for 2 consecutive samples. This also rejects single-sample spikes.
- **Drift:** otherwise τ 2.5 s, optionally faster when brightening.
- **Deadband:** |Δbrightness| ≥ 0.005 or |Δwarm_alpha| ≥ 0.002.
- At most 20 Hz per screen. `transition_ms` of about 150 ms is sent with each update.
- All constants live in `config/light.json`, reloaded when its mtime changes. This keeps today's "edit the curve, see it in seconds" loop after `exec(config.py)` is removed (`display_utils.py:129-131`).

**3. Staleness and warm start**
- Today a dead relay freezes the mask forever (`display_utils.py:174-177`).
- New rule: no frame for 10 s (the heartbeat makes this meaningful) → `source:"solar"` with a 30 s ramp. Siren `sensor.stale` after 60 s. A 5 s ramp back when readings return.
- After a core restart, `last_mask` is used until the first fresh sample.

**4. Offline (the page cannot reach the server)**, D14
- Hold the last mask for 10 min.
- Then follow `policy.offline.schedule`: about 12 h of solar-curve points that the server precomputes and refreshes every 10 min. The page interpolates them on its own monotonic clock.
- This is data, not ported logic.

**5. Legacy** `GET /api/<id>/mask` is served from the same SensorService, so even screens still in poll mode get the better filter.

**Latency targets**

| Measure | Target | Today |
|---|---|---|
| Sensor report → style commit (server-measured from ack plus heartbeat RTT; no cross-host clocks) | **p95 ≤ 50 ms** idle on powerline, ≤ 100 ms with the link saturated | — |
| Real light step → first visible change, filmed at 240 fps with the TV's ambient features off | **p95 ≤ 250 ms** (includes report interval, snap confirmation and the TV's own lag with Game Mode Off through the One Connect box: unmeasured, probably ≥ 70 ms; measured in the filmed test, and the target is re-set from it) | 0.5–3.5 s, then several seconds to settle |
| Flicker under steady light | none (filmed check) | — |
| Optical loop gain (if the sensor can see the TVs) | checked once by stepping the mask 0.2 → 1.0 | — |

### 2.7 Failure handling

| Failure | What the screen shows | How it recovers |
|---|---|---|
| Core restart or deploy | Unchanged. Still or clip stays; live keeps playing (its media socket goes to sm-stream); mask held. | Jittered reconnect. New epoch, so the snapshot is authoritative; compared by key, so nothing remounts. The `adopting` barrier re-adopts the session. |
| Core down for longer than the live TTL (120 s) | Live stalls, then hard-cuts to the still (with the latest stored mask) after 2 s. Mask held 10 min, then the offline schedule. | Degraded HTTP polling. When core returns, ChannelService restarts live if the lease is still valid. |
| Only the control socket down (port :8765 forward or hub thread) | Unchanged. | 6 s watchdog, degraded polling, `kiosk.state` over HTTP POST. The hub is supervised and restarted, with Siren `realtime.hub_restart`. |
| Short network blip | Unchanged. | Reconnect. Overlays missed during the blip arrive in the snapshot. |
| Powerline link up, path dead | Still and mask held. Live hard-cuts to the still after 2 s. | 6 s watchdog; `kiosk.heartbeat_stale` at 60 s. The fix is the planned Ethernet path watchdog (infrastructure, allowed on the box). |
| Powerline fails over to Wi-Fi | Stills and mask unaffected. Live hard-cuts to the still, shows it for a few seconds, then re-enters at a lower rung through the entry handshake, or stays on the still. | All sockets reset because the source IP changes. The rung ladder runs per link group (§3, step 10). |
| sm-stream crash or encoder failure | Last frame for up to 2 s, then a hard cut to the still. | Encoder restart → a new session through the entry handshake. The stall cut has already put the still on glass, so there is no exit handshake. Reconcile sends the screen back to base within 5 s, raises `stream.session_failed` and applies the retry policy. |
| Live renderer late or stuck | A late render repeats the previous frame in its slot, so the cadence stays at 60 fps (D-D). If no anchor or exit hold arrives in time, the page stays on the still (entry) or hard-cuts to the target (exit, after 15 s). | `stream.late_frames` from the renderer's late-frame count; `stream.anchor_timeout` when a re-anchor (`anchor_pts`) or exit hold (`hold_pts`) is not reported in time (a first-anchor failure stays `stream.start_failed`, §2.5 step 3); retry policy. |
| NVENC session limit reached | Still. | Retry policy; `stream.admission_denied`. |
| Live flapping | Still. | `failed` reported; the server decides. |
| Relay or sensor dead | Mask held, then ramps to the solar curve after 10 s. | systemd restarts the relay; `sensor.stale` at 60 s. |
| JS error in one layer | That layer's ErrorBoundary unmounts it. The last base `<img>` (kept in a module variable) and the mask stay; an error in LiveLayer hard-cuts to the still. Never white. | **Health-gated** reload after 30 s, with backoff. `kiosk.reload_storm` alert. |
| Heap growth | — | `kiosk.reload` at the next still-to-still transition in art mode, health-gated; never while live or while an entry, exit or re-anchor handshake is open. A reload that falls due during live is deferred until the exit ack. The flagged 04:00 reload, also health-gated, is skipped while live and is kept for 30 days. |
| Build changed after a deploy | Unchanged. | `kiosk.reload` or a `build_id` mismatch → reload at the next still-to-still transition in art mode (never while live or while a handshake is open; deferred until the exit ack), at most once per 10 min, **only if `/api/health` answers within 2 s**. |
| Chrome renderer crash or hung page | Sad tab or a frozen frame. | KioskMonitor: stale for 60 s → `restart-screen` over SSH keys (at most 3/h) → `restart-kiosk` → alert only. Owner decides whether this runs automatically. Skipped while the screen is in `maintenance` (V1). |
| Chrome process exits | Black sway background for about 10 s. | `kiosk-wayland.sh` already respawns it on its own TV with the same URL (measured ~10 s, `KIOSK_INFRASTRUCTURE_NOTES.md` §3). Still to add: at most 5 respawns per 10 min, and a launch that waits up to 5 min for `/api/health`, then launches anyway. On the X11 fallback nothing respawns it (`kiosk-loop.sh` only reacts to HDMI changes), and that is the case the comment at `admin_api.py:157` gets wrong. |
| Chrome starts while the server is down | Black until the server is healthy. | Health-gated launch. Optional later: a Service Worker shell plus the last still. |
| Clock skew | No effect. | Ordering uses only epoch and rev; all page timers are host-local; leases run on the server clock. |
| Spoofed lux or acks | Clamped and rate-limited; key rejected; acks only affect observability. | Close WAN 8765 (P4 step 7, after D5). |
| An operator laptop opens `/display/<id>` | Counted as `viewer`; no live; no alerts. | `kiosk.duplicate` alert if two clients claim the same `?kiosk=`. |
| TV off, in the TV's own (Samsung Frame) Art Mode, or on the wrong input | The browser is fine; the panel is not. | Show samsungtvws state next to the glass state. "On glass" means "composited by Chrome". |

---

## 3. Migration path

**How each step is tested on the real kiosk without disturbing production**

| | Vehicle | What it involves |
|---|---|---|
| **V1** | Production Chrome over CDP | Under sway, the lab Chrome launcher (`experiments/stream-latency/beelink/lab-chrome.sh`, CDP 9224) and `fb_probe.py` do not work, because both are X11-only. The 2026-10-13 lab tests therefore ran in the production Chromes (`KIOSK_INFRASTRUCTURE_NOTES.md` §3, §7). The procedure:<br>• Put that screen into `maintenance` in KioskMonitor (no restart ladder and no `kiosk.heartbeat_stale` for the window), and clear it after the restore.<br>• `cdp_probe.py --allow-production` navigates one production Chrome (9222 north or 9223 south) to `/display/devtest?kiosk=lab`, with devtest mirroring that screen's content and mask.<br>• Afterwards it restores the fixed URL.<br>• If the restore fails, killing that Chrome brings it back on its fixed URL (`kiosk-wayland.sh` respawn, about 10 s).<br>It takes one TV, so it needs an owner window. `lab-chrome.sh` works only on the X11 fallback. |
| **V2** | Shadow | Production pages report what they show; rendering does not change. |
| **V3** | Server policy flip | Per screen: devtest, then south for 48 h, then north. No box access, no Chrome restart. |
| **V4** | Spare N95 staging kiosk | If bought. Zero production risk. |

**Two further rules**
- `cdp_probe.py` refuses eval or navigate on 9222/9223 unless given `--allow-production` (the lab tools already do this). V1 is the only sanctioned use of that flag: inside an owner window, and always ending with a restore.
- Before P2's dev stack exists: serve the candidate `dist/` from a throwaway static server on the lab's port 8081, which is already port-proxied, with `/output` and `/api` proxied to :5000. V1 loads it.

### Step 0. Page hygiene and D14, now (1–1.5 d, frontend only)

**Change:**
- Ignore WS frames that have no `html`. This stops the phantom overlays created by lux, progress and transcription frames (`useOverlayWebSocket.ts:60-101`).
- Overlay scripts run once per overlay id and are removed with it (replaces `index.tsx:144-153`).
- Poll timeouts and an in-flight guard.
- Black `html`/`body` in `index.html`.
- ErrorBoundary with a health-gated reload.
- **Hold-last mask in the legacy `useMask`** (D14). Every later server restart, including the P2 cutover (plan P2 step 7, "Cutover window"), then becomes invisible.

**Approval:** the two leak fixes fall under the owner's standing pre-authorisation for leak fixes. The black background, ErrorBoundary and hold-last are small visible changes and need the owner's yes.

**Test:** V1 against the candidate bundle; on production, CDP counts `<script>` elements over 24 h.

**Rollback:** previous `dist/` (keep a `dist_prev`).

### Step 1. P1 and P2, unchanged and now a hard gate (8–11 d)

**Change:** safety net; release directory with symlink rollback; dev stack on 5001/8766; `state/` directory; **H2a** cross-loop bridge; **H2f** atomic pointer replace. Nothing below ships to production before this.

**Rollback:** per the plan.

### Step 2. Hub v2 core, server only (4–6 d)

**Change:**
- `sm/realtime` hub mounted by path on :8765.
- Retained `policy` (every source starts as `poll`/`legacy`, `ctl` set to `shadow`), `screen.state` (base derived exactly as today: newest of `.jpg/.JPG/.mp4`, `useFilePolling.ts:58-86`), `mask.set` (existing computation), and overlays mirrored from `send_overlay` through the bridge.
- Per-client `Queue(256)` with latest-value coalescing for retained types; 5 s send timeout → 4008; `hb`.
- Origin allowlist, role scopes, rate limit.
- `GET /api/screens/<id>/state|glass`, `POST /api/kiosks/state`.

**Test:** contract tests and a CLI client on the dev stack. Then production with no page connected yet, then V1.

**Rollback:** `SM_V2_WS=0` or a release-symlink flip. The legacy `/` path is untouched throughout.

### Step 3. Shadow acks (1.5–2 d + 7 days' running time)

**Change:**
- A ControlClient in the page, mode `shadow`. It sends `hello`, then acks and `kiosk.state` describing what the *legacy* logic is showing. It ignores pushes when rendering.
- The server computes publish-to-glass time and push-vs-poll divergence.
- `?kiosk=north|south` goes into the `kiosk-wayland.sh` URLs (and `kiosk-loop.sh` for the X11 fallback) as part of the P3 LAN-origin URL edit, so there is one Chrome relaunch at a quiet hour. Until then shadow reports are tagged by viewport.

**Test:** V2 on both screens; no visual change.

**Exit:** 7 days with every divergence explained.

**Rollback:** `policy.ctl=legacy`, or `?ctl=legacy` as a static override read before any v2 code runs.

### Step 4. Light push, P4b Tier 1 (3–4 d)

**Change:**
- Relay v2 in `deploy/sensors/light-relay/`.
- SensorService and its filter, pure `compute_mask` with characterisation goldens, `config/light.json`, staleness and warm start, persisted overrides.
- The page applies `mask.set` through refs and acks it when `policy.mask_source=push`. The 3 s poll becomes the fallback when the WS is down.

**Test:** V3 (devtest via V1, then south, then north), torch test filmed at 240 fps, loop-gain check.

**Rollback:** `policy.mask_source=poll` per screen, live. Point the relay back at `/`.

**Exit:** p95 report→commit ≤ 50 ms; no flicker.

### Step 5. Overlays through v2 (1–1.5 d)

**Change:** `overlay.show/clear` per screen, with `remaining_ms` in the snapshot. Kiosk pages drop the legacy `/` socket when `policy.overlay_source=push`.

**Test:** V3.

**Rollback:** `policy.overlay_source=legacy`.

### Step 6. Content push (2 d)

**Change:**
- An `on_published` hook after the atomic replace bumps `screen.state.base` with `?v=<rev>`. This also fixes the problem that two publishes within one second are missed today because of the 1 s `Last-Modified` resolution.
- `img.decode()` before the through-black fade.
- The HEAD polls run only in degraded mode, plus a 30 s safety poll that counts `push_miss`.
- The owner decides **extension parity** first: today only `.jpg/.JPG/.mp4` show, and `north-screen.png` and `south-screen.png` already exist.

**Test:** V3.

**Rollback:** `policy.content_source=poll`.

**Exit:** publish-to-glass p95 < 1 s from acks; at most 15 requests/min per screen.

### Step 7. Supervision and reload replacement (1.5–2 d)

**Change:**
- A rate limit and a health-gated launch for the respawn that `kiosk-wayland.sh` already does. The same respawn, rate limit and health gate go into `kiosk-loop.sh` for the X11 fallback.
- KioskMonitor restart ladder over SSH keys instead of `sshpass`.
- Fix the comment at `admin_api.py:157`.
- `build_id` / `kiosk.reload` replace the 4-hourly reloads (`index.tsx:369-422`). The flagged 04:00 health-gated reload stays for 30 days.

**Test:** V4 if available; otherwise kill one Chrome on south at a quiet hour.

**Rollback:** previous `kiosk-wayland.sh` (or `kiosk-loop.sh`); `policy` flag to turn the old reload hours back on.

### Step 8. P3 network, in parallel, mostly ops (2–3 d + owner time)

**Change:** LAN origin `sm.lan:5000`; `?kiosk=` in the URLs; LAN-scoped netsh rules; close WAN 8765 after D5; `apt-mark hold` Chrome 136 (D7). **This must finish before production live,** because Private Network Access for a public-origin page opening `ws://` to a private IP is unverified.

**TV Game Mode Off (D-C): satisfied 2026-10-13, no step needed.** TV Game Mode is not in use: over Samsung IP Control both TVs reported pictureMode=FilmmakerMode with gameMode=Auto at 19:16 UTC (before the sway switch) and at 19:37 UTC; Auto engages only on an ALLM request, which the kiosk never sends. All the sway 4K60 measurements (19:18-19:41 UTC, including the post-reboot run) were taken in that state, and toggling Game Mode On and back to Auto at 19:36 UTC changed neither EDID (hash 993716ac) nor the sway outputs. So 4K60 does not depend on Game Mode (`KIOSK_INFRASTRUCTURE_NOTES.md` §3).
- This closes the plan's open D24 test. No per-mode Game Mode switching is built. If a TV is ever found in Game Mode again, check with `swaymsg -t get_outputs` that 3840×2160@60 survives switching it back off.

**Rollback:** URL edit; reopen the forward.

### Step 9. LiveLayer on devtest (4–5 d; was 3–4 d, scope grew with the anchor handshake)

**Change:**
- MseLivePlayer, ported from the lab `MsePlayer` (`static/client.js`): low-delay init, gap jump, evict to a keyframe. Its live-edge rate control is replaced: `playbackRate` stays at 1.0 and never above, and the player resyncs by a hard jump to the live edge (D-D).
- The art/live layer split (§2.2) and the §2.5 handshakes:
  - anchor ack and the entry cut on the first presented frame;
  - the exit cut on `hold_pts`;
  - the stall hard-cut and re-anchor;
  - clean unmount.
- No crossfades or fades anywhere.
- LiveController MVP against the lab `server.py`, with a testPattern driver that can draw a given still and mask, hold it, diverge and converge, so the handshakes can be tested before the production renderer exists.
- An automated **anchor-match and no-black-frame check** on still → live → still, the stall cut and the re-anchor:
  - page side: CDP screenshots against the decoded anchor and hold frames;
  - on glass: `grim` under sway (§6, item 11).
- A fault-injection TCP proxy in front of devtest's attach URL: latency, loss, half-open, 15 Mbit/s cap.

**Test:** V1 or V4 only. Production screens keep `policy.live_enabled=false`.

**Rollback:** the flag.

### Step 10. Production live: P6A′ (bd, 6–9 d; was 4–7 d, the renderer's share grew) and P6B′ (owner, 4–6 d)

**Change:**
- sm-stream gets readiness, per-client backlog and drop-to-key stats in `/api/state`, `ttl_s ≥ 120`, and a clean new init on encoder restart.
- The live renderer gets:
  - the anchor, diverge, re-anchor and converge calls, with hold reporting (`hold_pts`);
  - mask parity with the page, from one shared formula with golden tests against page screenshots;
  - even 60 fps pacing (D-D): a late render repeats the previous frame in its slot, and the late-frame count is exported.
- Every live effect and transition is drawn here (D-B).
- Reconcile every 5 s with `adopting`; leases; the scheduler `channel` action; a ChannelPanel showing desired vs glass; ack-gated delete.
- A rung ladder per link group, end-then-start (exit and entry handshakes): 4K60 HEVC ~30 Mbit/s → 4K30 HEVC ~18 Mbit/s → 4K30 HEVC ~10 Mbit/s → 1080p30 ~5 Mbit/s → still.
  - Step down on ≥ 2 backlog drop-to-key events in 10 s or any page stall.
  - Step up only after 5 min clean, with backoff.
  - The X11 fallback kiosk cannot play 4K60, so a screen on it starts at 4K30 (`caps.host`, §2.3).
- Alerts.

**Test:** devtest via V1 or V4 → south in an owner window → north. Drill: pull the powerline adapter during live.

**Rollback:** `policy.live_enabled=false` or clear the claim.

### Step 11. Decommission (T8)

**Change:**
- Remove the legacy `/` viewer path and the raw lux rebroadcast (`overlay_ws_server.py:171-177`).
- Drop kiosk mask polling to a 60 s safety rate.
- Remove the legacy pollers 7 days after zero `push_miss`.
- Remove `light-relay.service` from the mini-PC deadman's default (`deadman.py:290`) once `sensor.stale` has run for a week.

**Rollback:** git revert.

### Effort (owner-days)

| Step | Owner-days |
|---|---|
| 0 | 1–1.5 |
| P1 + P2 | 8–11 |
| 2 | 4–6 |
| 3 | 1.5–2 |
| 4 | 3–4 |
| 5 | 1–1.5 |
| 6 | 2 |
| 7 | 1.5–2 |
| 9 | 4–5 (was 3–4) |
| 10 | 4–6, plus bd 6–9 (was 4–7) |
| **Total to production live** | **≈ 30–41** (was 29–40) |

- **Revision 2026-10-13:** steps 9 and 10 grew because of the anchor handshake, mask parity and even pacing (D-B to D-D). No other step's effort changed. Under sway, step 7's respawn already exists, but its rate limit and health gate, and the X11-fallback copy, remain.
- **Plan comparison:** the plan's critical path is 26–36 days ("plan on 30–40"), so this is **roughly level**. The P5 parity port, Tier 2 and the iframe/`/player` work are swapped for shadow acks, policy switches and a hardened live state machine.
- **Light push on glass:** about **18–25** owner-days (steps 0 + P1 + P2 + 2–4).
- **Leak fixes:** week one.

---

## 4. Deltas to REFACTOR_PLAN.md

Plan items are named by section and step, not by line, because the plan is revised in place. **Status** is against the plan as it stood on 2026-10-13: *applied* (already in the plan), *partly* (the rest is still to do) or *open*. The plan session acts only on what is not applied.

| Plan item | Action | Status | Detail |
|---|---|---|---|
| §2 principles | **Add** | applied (P13's text sits in P11's art/live bullet) | P11 "one page, dumb host, sensor is a remote input" (text of I1–I3, with I2's sway host), P12 "local degrade, server upgrade" (text of I4–I5), and P13 "art composites in the DOM, live on the GPU; hard cuts only" (text of I6). |
| §3.1 diagram | **Change** | applied (the renderer is in the text under the diagram) | Remove `kiosk-agent.service`. Draw light-relay as a "sensor node (currently on the mini-PC)". Page sockets: control → `:8765/v2/screen/<id>`, media → `:8081` direct. Draw the live renderer (workstation GPU) feeding sm-stream. Draw the kiosk as `kiosk.service` = sway + `kiosk-wayland.sh`, with `kiosk-x11.service` (Xorg + `kiosk-loop.sh`) as the disabled fallback. |
| §3.2 package tree, App. B | **Change** | applied | `deploy/sensors/light-relay/`. `deploy/kiosk/` keeps only these: the sway kiosk (`kiosk.service`, `sway-kiosk.conf`, `kiosk-wayland.sh` with a rate-limited, health-gated respawn), the X11 fallback (`kiosk-x11.service`, `kiosk-loop.sh`, `20-zaphod.conf`), deadman, network scripts and the installer. No `kiosk-agent.py`. |
| §3.3 frontend structure, P5, §8, T-track "Remove /display-legacy" | **Drop/Change** | applied | No `kiosk.html` entry and no `/display-legacy`. Evolve `DisplayPage` in place with strict `control/` and `live/` modules. Live nodes are owned imperatively and the mask is written through refs. Rollback is `policy.*` flags, with `?ctl=legacy` as a static override. Drop `window.__smHealth`. Defer the ≤300 KB bundle budget to T7. Rename "kiosk shell" to "display page" throughout (§1, §5.1, P5 heading). |
| §3.4, §4.1–4.2, P4 | **Change** | applied | Route v2 by path (`/v2/screen/<id>`, `/v2/ingest/<kind>`) instead of a `hello` on the legacy socket. Add a retained `policy` type. Add `hb` (2 s idle, 6 s dead), `ack{kind, rev, phase}` (supersets `kiosk.state`), the `kiosk`/`viewer` roles, the Origin allowlist, role-scoped inbound, the 50 msg/s and 64 KiB limits, the HTTP twin `GET /api/screens/<id>/state`, and the `POST /api/kiosks/state` fallback. Keep the hub ownership, bridge, per-client queues, epoch/rev, authoritative snapshot and evidence week. |
| P4 step 6 (shadow acks), P4b step 3 (the page applies `mask.set`) | **Keep, now the main path** | applied | They *are* the migration (steps 3–6), ordered mask → overlays → content, each behind its own policy flag. Shadow acks come first. |
| §3.5 "Transport; presentation is inline only", §4.4–4.6, §6 D15 | **Drop** | applied | `presentation` (inline/iframe/page), the postMessage contract, page mode, `/player?return=`, and "kiosk-agent navigates back". **Inline only.** Experimental transports ship as lazy inline driver chunks, tried on devtest via V1 or V4. |
| §3.5 "Display page PlayerHost" | **Change** | partly (the 10-frame warm-up gate, the 5 s delete and the "pending the design update" bullets remain) | Live mode is one full-viewport `<video>` with nothing over it: no mask, overlays or controls, and no CSS opacity, filter, transform or blend (I6). Live warms up *under* the art stack. Entry and exit are hard cuts on frames the renderer holds (§2.5): an anchor ack, a cut on the first presented frame, and on exit a cut on `hold_pts`. These replace the 10-frame warm-up gate and every crossfade and fade. `playbackRate` ≤ 1.0, with a hard jump to the live edge. Base is never unmounted while live is shown. Stall of 2 s → hard cut to the still; recovery re-runs the entry handshake. Profile changes are end-then-start (exit then entry handshake) until §6 proves A/B is affordable. Delete is ack-gated. Every live state has a timeout. `adopting` barrier. Self-reload only when `/api/health` is OK (replaces "WS-dead self-reload after 10 min"). |
| §3.5b, P4b, §6 D25 | **Drop Tier 2; change** | applied | Reword "Sensor and kiosks share a box" to "currently hosted on the mini-PC". Specify the filter (log-lux, snap/drift, deadband, ≤20 Hz, `transition_ms`) in `config/light.json`. Add a relay heartbeat and `cfg`, staleness → solar plus `sensor.stale`, warm start, ack-measured lag, and an optical loop check. D25 becomes "Tier 1 only". |
| P4b step 4 | **Change** | applied | Relay goes in `deploy/sensors/`, not `deploy/kiosk/`. Host-agnostic, with an optional key. |
| §6 D14 | **Decide now, ship at step 0** | applied (P0.5) | Hold 10 min, then the server-precomputed solar schedule. Never black, never full brightness at night. |
| P5 "Supervision and reload replacement", §6 D13 | **Replace** | applied | No kiosk-agent. Instead: a rate limit and a health-gated launch for the respawn `kiosk-wayland.sh` already does (and the same in `kiosk-loop.sh` for the X11 fallback), the KioskMonitor ladder over SSH keys, and `build_id`-driven `kiosk.reload`. |
| P5 overall | **Shrink** | applied | About 4–6 d (channels, live state machine and supervision) instead of 6–8, because there is no parity port. |
| P6A steps 3–4 (§5 "P6A: Stream service productionisation") | **Drop and replace** | partly (the renderer's anchor, diverge, re-anchor and converge calls are not in the plan) | Instead of the `/player` iframe and mask: a session `ready` signal, per-client backlog and drop-to-key stats, `ttl_s ≥ 120`, and a clean init on encoder restart. The renderer also gets: anchor, diverge, re-anchor and converge with hold reporting; mask parity with the page; even pacing at the rung's rate; and every live effect and transition (D-B to D-D). `/player` (`static/client.html`) stays a lab page only. |
| P6B stage 2 step 2 | **Change** | partly (the ladder is still "to be confirmed") | Inline `wsFmp4Mse` only. Add the link-group ladder (end-then-start, starting at 4K60 HEVC with 4K30 as the fallback rung), ack-gated delete, and a powerline-pull drill as an exit criterion. |
| §6 D11 (codec and profile) | **Change** | partly (the ladder is still "to be confirmed") | Primary 4K60 HEVC (3840×2160@60, ~30 Mbit/s) under sway; 4K30 HEVC is the fallback rung, and the only 4K rung on the X11 fallback. Confirmed ladder: 4K60 HEVC ~30 Mbit/s → 4K30 HEVC ~18 → 4K30 HEVC ~10 → 1080p30 ~5 → still (§3 step 10). The X11 fallback starts at 4K30. |
| §6 D24 (TV Game Mode) | **Change** | open | Off, permanently (D-C: "No TV Game Mode, ever"). Already the case: TV Game Mode is not in use: over Samsung IP Control both TVs reported pictureMode=FilmmakerMode with gameMode=Auto at 19:16 UTC (before the sway switch) and at 19:37 UTC; Auto engages only on an ALLM request, which the kiosk never sends. All the sway 4K60 measurements (19:18-19:41 UTC, including the post-reboot run) were taken in that state, and toggling Game Mode On and back to Auto at 19:36 UTC changed neither EDID (hash 993716ac) nor the sway outputs. So 4K60 does not depend on Game Mode. No per-mode Game Mode switching is built. |
| §6 D25 | **Change** | open | Closed: no ambient influence on the live renderer between handshakes; live is full intensity (D-B, §2.6). |
| §6 D27 | **Change** | open | 22 items: item 9 settled, items 20 and 22 new and open (22 arises only if §6 item 0 fails), item 21 sequencing (§5). |
| Plan text "pending / to be confirmed in the design update" (§2 P12, §3.5 PlayerHost, P6B stage 1 step 2 and stage 2 step 2) | **Change** | open | Resolve to the §2.5 timings (2 s stall, 3 s recovery, 3 fallbacks in 10 min, 15 s exit timeout), the §3 step 10 ladder and §5 item 20. |
| Display-fix bar (P3 step 5) | **Change** | open | ≥ 99.9 % presented (§6 item 1), not ≥ 99 %. "Item 3c" now means the warm-up configuration, not the mask over live. |
| P3 step 4 | **Change** | applied | Remove `http://sm.lan:8081` from `--unsafely-treat-insecure-origin-as-secure`; MSE does not need it. Add `?kiosk=` to the URLs. P3 must finish **before production live** (PNA unverified). Do the D7 Chrome hold before step 9. |
| §3.6 | **Add** | applied | `state/screens/<id>.json`, `state/mask_overrides.json` (pulled forward from T3), `logs/screens/<id>.jsonl`. |
| §3.8 | **Add** | partly (the two `stream.*` keys are new) | KioskRegistry and `/glass`. Siren keys `kiosk.heartbeat_stale`, `kiosk.state_drift`, `kiosk.duplicate`, `kiosk.player_stalled`, `kiosk.reload_storm`, `live.client_failed`, `sensor.stale`, `mask.lag_high`, `realtime.hub_restart`, `stream.late_frames`, `stream.anchor_timeout` (the plan's §3.8 already has `stream.start_failed`, `stream.session_failed` and `stream.admission_denied`). State that "on glass" means composited by Chrome, and pair it with samsungtvws state. |
| §3.9 | **Add** | partly (V1 is still the lab Chrome) | Shadow acks; the fault-injection proxy; the anchor-match and no-black-frame check (CDP screenshots; `grim` on glass under sway); the V1–V4 vehicles, where V1 is now a production Chrome over CDP because `lab-chrome.sh` and `fb_probe.py` are X11-only; the `cdp_probe` production guard. |
| §5.1 sequencing | **Reorder** | applied (pending the owner's yes, §5 item 21) | Step 0 ahead of P1. P4b immediately after the P4 hub core, ahead of overlays, content and P5. P3 before production live. |
| §8 risks | **Add** | partly (the HEVC presentation risk is still open there) | Live 4K60 renderer pacing and anchor fidelity (§6); falling back to the X11 kiosk means 4K30 at best; PNA; sockets reset on NIC failover; MSE leaks once timed reloads are gone. Close the risk "HEVC frame presentation on the N95": pre-made 4K60 dropped 0.03 % under sway (§6). |
| Plan text that still carries the superseded live design (wherever it appears) | **Change** | open | This covers:<br>• the warm-up gate followed by a 1 s crossfade;<br>• the stall fade-out and fade-back;<br>• the fade on leaving live;<br>• live-edge rate control;<br>• a mount that resolves after 10 presented frames;<br>• a 4K30-first ladder or `"profile":"4k30-hevc"` default;<br>• Xorg or `kiosk-loop.sh` as the host stack;<br>• `lab-chrome.sh` as V1;<br>• "4K60 does not work on the N95";<br>• a mask, warm tint, overlay, HUD or controls drawn over the live video;<br>• per-mode or live-only TV Game Mode switching.<br>Each should match §2.2, §2.5 and §3 here. The plan's open item on overlays sent to a live screen is §5 item 20. |

---

## 5. Decisions the owner must make

Each has a recommended default except items 20 and 22, which are left open. Item 9 is settled by D-B. The summary list at the end repeats items 1–22 in the same order.

1. **One page:** confirm I1 and that `set-url` and same-URL reloads are maintenance and rollback only. *Default: yes.*
2. **"Just a web server":** confirm this means "a dumb browser host". *Default: yes.*
3. **Evolve in place:** evolve `DisplayPage` rather than build a new `kiosk.html` entry. *Default: in place.*
4. **D14:** hold the last mask 10 min, then follow the server-precomputed solar schedule, and ship hold-last into today's page at step 0. *Default: yes.*
5. **Step 0 visible bits:** approve the black background and ErrorBoundary (the leak fixes are already covered by the standing pre-authorisation). *Default: yes.*
6. **Recovery:** generic respawn on the box plus the server-driven restart ladder (3/h, then `restart-kiosk`, then alert only), run automatically, and no CDP agent on the box. *Default: automatic, rate-limited.*
7. **Extension parity on content push:** *default: keep today's `.jpg/.JPG/.mp4` filter.*
8. **Still-to-still transition:** *default: keep through-black, now with decode-before-fade.*
9. **Mask during live:** *settled by the owner (D-B): none. Live is full intensity, and the mask is art-mode only.*
10. **Wi-Fi degrade:** *default: 1080p rung, then the still.*
11. **Light tuning defaults** (snap τ 0.15 s at > 0.75 EV, drift τ 2.5 s, deadband 0.5%, ≤ 20 Hz, 150 ms transition, stale 10 s → 30 s solar ramp, alert at 60 s): *default: yes, tunable in `config/light.json`.*
12. **Optional per-sensor ingest key:** *default: yes, until WAN 8765 is closed at least.*
13. **`?kiosk=north|south` URL flag**, added with the P3 URL change: *default: yes.*
14. **Spare N95 staging kiosk (about £150):** *default: yes.*
15. **V1, a production Chrome over CDP on one TV** (navigate to devtest, then restore), in owner-agreed off-hours windows until a staging box exists. Under sway the lab Chrome is X11-only. *Default: yes.*
16. **Hold Chrome at 136** (`apt-mark hold`, D7) before live work: *default: yes.*
17. **Interim light push on the legacy WS:** *default: no.* Ship only the D14 hold-last now.
18. **Later, optional:** Service Worker shell plus last still for "Chrome starts while the server is down". *Default: defer.*
19. **Relay hosting:** *default: stays on the mini-PC as an independent unit.* Evaluate Yoctopuce VirtualHub later.
20. **Overlays and the info panel during live** (raised by the programme author, screen-machine-10). Nothing may be drawn over the video (D-B), so scheduler and system overlays and the info panel cannot appear during a live show unless the renderer draws them into the stream. The options:
    - **(a)** the server forwards overlay content to the renderer, which draws it;
    - **(b)** overlays are not shown during live; they are dropped, or queued for art mode;
    - **(c)** a mix by overlay type.

    Until the owner decides, the page draws none in live mode and counts them in `kiosk.state` (`overlays_suppressed`). *No default: owner to decide.*
21. **Sequencing:** pull P4b right after the P4 hub core, ahead of overlays, content and P5; run P3 before production live. *Default: yes.*
22. ~~4K60 against Game Mode.~~ **Moot (2026-10-13):** 4K60 holds without Game Mode (§6 item 0). Kept for numbering.

---

## 6. What the in-progress lab device test should measure

**Current evidence** (revised 2026-10-13; measured, `KIOSK_INFRASTRUCTURE_NOTES.md` §3)

**Earlier lab runs** (the original draft read these from raw files):
- `runs/20261013-182630/matrix.md`: HEVC 4K30 at 18 Mbit/s presented 87 %. Rows 3–8, all after the first in-page session or codec change, presented 0 frames.
- `runs/20261013-183422/frames.jsonl`: 31–60 % of frames were flagged `FLAG_NEVER`.
- These runs were on the shared 7680×2160 X screen, which could not page-flip either window.

**One X screen per TV (X11):**
- 4K30 HEVC dropped 0.24–0.28 %.
- 4K60 dropped 74–82 %, because the GPU's VideoEnhance engine was saturated converting every frame to RGB.
- Chrome's zero-copy path crashed (Mesa GBM lacks NV12).

**Wayland (sway, zero-copy VA-API):**
- A pre-made HEVC 4K60 30 Mbit/s file played on both TVs with **3 of ~8,600 frames dropped per TV (0.03 %)**, and rAF ran at 60.0 fps with 0 late.
- It survived a reboot, a Chrome kill, a TV missing at start and a runtime unplug.

**Live 4K60 from the lab generator under sway:**
- It still dropped **6–7 %**.
- The generator missed its 16.7 ms deadline about 2 % of the time, and the lab player ran above 1.0×. Hence D-D.

**What this settles:**
- The earlier "biggest open risk" (4K30 frames not all presented) is **resolved**.
- **4K60 is the target rung, and 4K30 is the fallback rung.**
- Still unproven:
  - live 4K60 with even renderer pacing and `playbackRate` ≤ 1.0;
  - the anchor handshake;
  - the 0-frame session-change rows (item 5);
  - ~~4K60 at the panel with Game Mode Off (item 0).~~ Satisfied (item 0).

**What to measure**

0. **4K60 without Game Mode (D-C): satisfied 2026-10-13.** TV Game Mode is not in use: over Samsung IP Control both TVs reported pictureMode=FilmmakerMode with gameMode=Auto at 19:16 UTC (before the sway switch) and at 19:37 UTC; Auto engages only on an ALLM request, which the kiosk never sends. All the sway 4K60 measurements (19:18-19:41 UTC, including the post-reboot run) were taken in that state, and toggling Game Mode On and back to Auto at 19:36 UTC changed neither EDID (hash 993716ac) nor the sway outputs. So 4K60 does not depend on Game Mode. Re-check only if a TV is found in Game Mode or after a TV firmware update: `swaymsg -t get_outputs` shows 3840×2160@60 and the pre-made HEVC 4K60 file drops ≤ 0.1 %.
1. **Presentation truth for live 4K60** (4K30 as the fallback rung).
   - Compare rVFC `presentedFrames`, `getVideoPlaybackQuality()` (total, dropped, corrupted) and rAF intervals. `fb_probe.py` is X11-only, so there is no framebuffer count under sway; `grim` can spot-check frames.
   - Run with the renderer pacing evenly at 60 fps and `playbackRate` never above 1.0 (D-D). Record the renderer's late-frame count and the player's `edge_jumps`.
   - Sweep HEVC bitrates at 4K60 and 4K30, generative CUDA against the pattern source.
   - *Pass:* ≥ 99.9 % of frames presented over 30 min (the pre-made file reached 99.97 %), 0 late renderer frames, and ≤ 1 stall per 10 min.
2. **Hardware decode is in use.**
   - Run `cdp_probe.py <9222|9223> media` (read-only) on the production Chrome under test for `kIsPlatformVideoDecoder` / VaapiVideoDecoder.
   - Check `mediaCapabilities.decodingInfo().powerEfficient` for both codecs.
   - Check `intel_gpu_top` VideoEnhance at 0 %, which confirms the zero-copy path.
3. **N95 budget for the real production case.** Use `intel_gpu_top` (Video, VideoEnhance, Render/3D) plus per-Chrome CPU and dropped frames, with:
   - **(a)** one 4K60 live window;
   - **(b)** live 4K60 in *both* windows at once under sway (pre-made file: 0.03 % dropped; live still to do);
   - **(c)** the warm-up configuration: the art stack (still, black alpha layer, full-screen `mix-blend-mode: multiply` warm layer; no overlays, which are held from `starting`) composited over a covered, warming `<video>` on one TV, with the other TV live;
   - **(d)** a paused clip base under live, compared with a still base.

   This checks that warm-up and a paused clip base cost nothing visible. §5 item 9, the mask during live, is settled by D-B: there is none.
4. **Covered warm-up and the cut.** This decides the warm-up z-order in §2.2.
   - Do decode and rVFC run for a `<video>` covered by the opaque art stack, and for one at `opacity:0.001` above it?
   - How long does it take from attach to the first presented frame?
   - When the art stack is hidden inside the rVFC callback, does the video appear on the very next composited frame, with no intermediate frame (black, or video under the mask)?
   - Once uncovered, does the video use the zero-copy path (VideoEnhance 0 %)?
   - In the `opacity:0.001` variant: does removing or re-adding `opacity:0.001` take effect on the next composited frame without leaving the zero-copy path?
5. **Session change on a running page.** Rows 3–8 failed here on X11; re-test under sway.
   - Compare re-init on the same element, a new MediaSource, and a new element.
   - Record recovery time and whether black frames appear.
   - This decides end-then-start against A/B, and how the post-stall re-anchor attaches.
6. **Two decoders in one page** at 4K and at 1080p during a 1–2 s overlap: are there drops in either window? A/B switching is used only if this is clean.
7. **Buffer-target sweep** at 2, 4, 8, 12, 18 and 24 frames (33–400 ms at 60 fps), with `playbackRate` fixed at 1.0 and hard jumps to the live edge.
   - Measure presented %, stalls per hour, live-edge jumps per hour and latency, with the powerline link idle and then loaded (`scripts/powerline_soak.sh`).
   - This sets the default `target_buffer_ms` (provisional value 300 ms) and the jump thresholds in §2.5.
8. **Encoder and renderer side:**
   - CUDA render plus NVENC time per 4K60 frame with complex content;
   - deadline misses at 16.7 ms (the lab generator misses about 2 %), and whether render-ahead keeps the cadence even;
   - the anchor's mask parity against page screenshots;
   - how many concurrent NVENC sessions the RTX 4060 allows on driver 566.14 (two screens, plus two more if A/B is ever used).
9. **24 h soak** at the chosen profile:
   - JS heap and GPU memory;
   - SourceBuffer size after eviction;
   - decoder errors;
   - VA-API context and memory after 50 attach/detach cycles.
10. **Failover drill** (owner window): pull the powerline adapter during live. Measure time until media-socket silence is detected, time to reconnect over Wi-Fi, presented % at 8 and 5 Mbit/s at 1080p, and whether two streams fit on Wi-Fi.
11. **Anchor-match and no-black-frame check** through still → live → still, through a forced stall hard-cut and through the re-anchor.
    - **Page side:** CDP screenshots against the decoded anchor and hold frames.
    - **On glass:** `grim` under sway (not installed yet; `KIOSK_INFRASTRUCTURE_NOTES.md` §7). `fb_probe.py` is X11-only.
    - Run every case twice: at full brightness and at a non-neutral mask (brightness ≈ 0.3, warm_alpha ≈ 0.05), so the renderer's reproduction of the mask is tested, not just the still (proposed by screen-machine-10).
    - *Pass:* no black frame, and the two sides of each planned cut differ only by MP4 artefacts (the threshold is set from the measured artefact level).
12. **Optional, art mode only:** the cost of a 64×36 `drawImage` luma probe of the still or clip, to decide whether a pixel-level "proof of glass" in `kiosk.state` is affordable. In live mode the page does nothing but play the stream (D-B); proof of glass there comes from rVFC `presentedFrames` and the renderer's frame ids.
13. **Informational:** filmed capture-to-glass with `/clock.html`. The TVs run without Game Mode (Filmmaker Mode, gameMode=Auto; D-C). Quality comes before latency, so this does not gate anything.

**Run constraints:**
- Under sway, the lab Chrome (`lab-chrome.sh`, CDP 9224) and `fb_probe.py` do not run, because both are X11-only.
- Single-TV items use one production Chrome over CDP (V1: navigate, test, then restore its URL) while the other keeps running untouched. Two-TV items (3b, 3c, 10 and the link-group ladder) navigate both production Chromes in an owner window and restore both afterwards. The 24 h soak (item 9) runs on V4, or on one TV in an owner-agreed 24 h window.
- Any run that covers a TV, and the powerline-pull drill, happen only in an owner-agreed window.


## Owner decisions (summary list)
- Confirm the one-page invariant: in normal operation each kiosk loads only /display/<id>. Admin set-url and same-URL reloads are for maintenance and rollback only. Recommended default: yes.
- Confirm that 'the mini-PC is really just a web server' means 'a dumb browser host': Chrome, a launcher with a generic respawn, deadman and network scripts, and no app logic. Recommended default: yes.
- Evolve today's DisplayPage in place, with new strict control/ and live/ modules, instead of building a new kiosk.html entry and keeping /display-legacy. Recommended default: evolve in place.
- D14, mask when the page loses the server. Recommended default: hold the last mask for 10 minutes, then follow a solar-curve schedule the server precomputes and sends in advance. Never go black. Ship the hold-last part into today's useMask now, so server restarts stop blacking the screens.
- Approve step 0 now. The leak fixes (phantom overlays, overlay scripts running once) are already covered by the standing leak-fix pre-authorisation. The small visible changes need approval: a black html/body background and an ErrorBoundary that shows the last image and reloads only when the server is healthy. Recommended default: yes.
- Kiosk recovery. Recommended default: no CDP agent on the box. Instead, a rate limit and a health-gated launch (waits for /api/health) for the respawn of a dead Chrome that kiosk-wayland.sh already does (and the same in kiosk-loop.sh for the X11 fallback), plus server-driven restarts on a stale heartbeat: restart-screen up to 3 times an hour, then restart-kiosk, then alert only. Runs automatically, with SSH keys replacing sshpass.
- Content push and file extensions. Recommended default: keep today's .jpg/.JPG/.mp4 filter, so the existing north-screen.png and south-screen.png do not suddenly start showing. Revisit later.
- Transition between stills. Recommended default: keep today's 1 s out, 1 s black, 1 s in, but decode the new image before the fade starts.
- Mask during live: settled by the owner (D-B). There is none: live is full intensity, and the mask is art-mode only.
- Live video on the Wi-Fi fallback. Recommended default: step down to a 1080p rung, then to the still. Step back up only after 5 minutes clean, with backoff.
- Light tuning defaults, all tunable in config/light.json. Recommended default: log-lux filter with a snap time constant of 0.15 s when the reading moves more than 0.75 EV for 2 samples; otherwise a drift time constant of 2.5 s; 0.5% brightness deadband; at most 20 Hz per screen; 150 ms CSS transition; sensor stale after 10 s, then a 30 s ramp to the solar curve; alert after 60 s.
- Optional per-sensor ingest key: one environment variable on the relay host. Recommended default: yes, at least until WAN port 8765 is closed.
- Add ?kiosk=north|south to the kiosk URLs, in the same kiosk-wayland.sh edit (and kiosk-loop.sh for the X11 fallback) as the P3 LAN-origin change, so there is a single Chrome relaunch at a quiet hour. Recommended default: yes.
- Buy a spare identical N95 (about £150) as a staging kiosk running the same sway kiosk (kiosk.service + kiosk-wayland.sh). Recommended default: yes.
- Allow V1 until a staging box exists: one production Chrome is driven over CDP (navigate to devtest, then restore) for live and parity tests in owner-agreed off-hours windows, plus the powerline-pull failover drill. Under sway the lab Chrome (lab-chrome.sh, CDP 9224) and fb_probe.py are X11-only. Recommended default: yes.
- Hold Chrome at 136 (apt-mark hold, plan D7) before live work starts. Recommended default: yes.
- No interim light push on the legacy WebSocket. It would build on the unsafe cross-loop sends. Recommended default: no interim push; ship only the D14 hold-last now.
- Optional later: a Service Worker shell plus a cached last still, so a Chrome start during a server outage shows the picture. Recommended default: defer.
- Light relay hosting. Recommended default: stays on the mini-PC as an independent unit in deploy/sensors/light-relay/. Evaluate Yoctopuce VirtualHub (relay process on the server) later.
- Overlays and the info panel during live (raised by the programme author, screen-machine-10). Nothing may be drawn over the live video (D-B), so scheduler and system overlays and the info panel cannot appear during a live show unless the renderer draws them into the stream. The options: (a) the server forwards overlay content to the renderer, which draws it; (b) overlays are not shown during live, either dropped or queued for art mode; (c) a mix by overlay type. Until decided, the page draws none in live mode and counts them in kiosk.state. No recommended default: owner to decide.
- Pull P4b (light push) ahead of overlays, content and P5, right after the P4 hub core. Run P3 (LAN origin) before production live. Recommended default: yes.
- ~~4K60 against TV Game Mode~~: moot, 4K60 holds without Game Mode (§6 item 0).
