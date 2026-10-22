# Kiosk display page and screen-side control: what exists today for a "show mode" (live generative video per screen, driven by a room controller) and for freezing the light-sensor mask during shows

## 1. How does the display page decide what to show?
[high] Route /display/:screenId (src/App.tsx:30) mounts src/pages/DisplayPage/index.tsx.

1. useBaseFileName resolves the base name. The mapped destinations from api.ts:1704-1713 carry no `type` or `file`, so the match at useBaseFileName.ts:14 always fails and the base name falls back to screenId (lines 18-24). It works by accident.
2. useFilePolling HEAD-polls `/output/<id>.jpg`, `.JPG` and `.mp4` (useFilePolling.ts:17-28, 54-62). It picks the newest by Last-Modified (74-76). It swaps only when that time is greater than the last one seen (78-86), so two publishes inside the same 1 s Last-Modified tick can be missed.
3. A failed HEAD counts as 0, so the current item is kept.
4. Until the first result arrives, the page renders a black div with the text 'Loading…' (index.tsx:212-214).
5. On a change it fades through black (index.tsx:159-173, 318-331; MediaDisplay.tsx:111-127, fade overlay z 9998).
6. An .mp4 does not play as a normal video. MediaDisplay runs a slow-motion loop: a primer at 0.065x, ease to 0.6x, ease out, hold the last frame, crossfade to the first frame, then loop (MediaDisplay.tsx:6-15, 186-259).
-> IMPLICATION: Content today is a file pointer that is polled, not a channel. Publishing an mp4 cannot serve as show video, because it would play in slow-motion easing. A live layer has to be a new layer on the page; it cannot reuse MediaDisplay.
Sources: src/App.tsx:30; src/pages/DisplayPage/hooks/useBaseFileName.ts:11-28; src/utils/api.ts:1694-1713; src/pages/DisplayPage/hooks/useFilePolling.ts:4-91; src/pages/DisplayPage/index.tsx:159-173,212-214,310-332; src/pages/DisplayPage/MediaDisplay.tsx:6-15,111-127,186-259

## 2. What are the poll intervals?
[high] Each screen polls:
- Content: 3 HEAD requests every 1 s (useFilePolling.ts:4,90), with no timeout and no in-flight guard.
- Mask: GET /api/<id>/mask about every 3 s, chained with setTimeout after each fetch settles (useMask.ts:4,53).

Polled only while the controls panel is open (index.tsx:63-126):
- Scheduler status: every 15 s.
- Event triggers: every 30 s.
- Next event: every 15 s.

Overlay WS reconnect backoff is min(10 s, 2^n s) (useOverlayWebSocket.ts:48-53).

Server side:
- The scheduler tick is 2 s (config.py:29), with a 0.1 s inner loop (scheduler.py:1314-1315).
- A paused scheduler sleeps 2 s per loop and processes nothing (scheduler.py:1041-1049).

That comes to about 200 requests/min per screen, matching REFACTOR_PLAN.md:956.
-> IMPLICATION: A show-state poll on the same pattern would cost little. But control latency through the existing paths is about 1-3 s (content and mask) or about 2 s plus device time (scheduler events). That is fine for mode changes, not for anything beat-level.
Sources: src/pages/DisplayPage/hooks/useFilePolling.ts:4,90; src/pages/DisplayPage/hooks/useMask.ts:4,53; src/pages/DisplayPage/index.tsx:63-126; src/pages/DisplayPage/hooks/useOverlayWebSocket.ts:48-53; config.py:29; routes/scheduler.py:1041-1049,1314-1315

## 3. How is the mask fetched and applied, and what happens when a fetch fails?
[high] Fetch: useMask(screenId, noMask) (index.tsx:44) calls api.getMask, which is GET `${VITE_API_URL}/<id>/mask` (api.ts:2344-2355).

Server (display.py:106-183) checks in this order:
1. Manual brightness override (display.py:125-136): returns that brightness, warm_alpha 0.
2. maskoff state (139-146): returns brightness 1.0.
3. intensity_cfg.adjust false (148-155): returns 1.0.
4. Otherwise compute_mask (display_utils.py:116-313). It exec()s config.py on every call (129-131). For a destination in the sensor's target group (`living-room`, routes/data/intensity.json) it uses the latest lux sample, with no staleness check (display_utils.py:174-177), mapped through a lux→intensity table. Lux 0 gives brightness 0.01; 100 lux gives 1.0. Smoothing is a 5-sample moving average plus an EMA with alpha 0.2 that advances on every read (lightsensor.py:20-30, 93-100). Without a sensor match it falls back to a solar curve.

The live destinations file is src/data/publish-destinations.json, which is first in SEARCH_PATHS (routes/utils.py:32-35). It has intensity_cfg adjust:true for both screens (lines 29-34, 52-57).

Apply: a black div with rgba(0,0,0,1-brightness) at z 5, with a 1000 ms CSS transition (index.tsx:334-344; useMask.ts:64). On top sits a warm multiply div (z 6) with warm_hex/warm_alpha (index.tsx:347-360).

On fetch error: state is reset to brightness 0 (useMask.ts:46-50). That is an overlay of rgba(0,0,0,1), so the screen goes fully black until a later poll succeeds. The comment says 'neutral', but the effect is black.

A hung fetch has no timeout. It freezes the last mask, because the next poll is only scheduled in `finally`.

The lux frames broadcast over WS reach the page but are not used for the mask.
-> IMPLICATION: During a show in a dark room the sensor drives the screens toward 1% brightness. Hue or moving-head flashes would pump it. Any mask fetch error (server restart, network blip) blacks both screens mid-show. Show mode needs both a mask freeze and the P0.5 hold-last fix (REFACTOR_PLAN.md:680).
Sources: src/pages/DisplayPage/hooks/useMask.ts:4,21-66; src/pages/DisplayPage/index.tsx:40-44,334-360; src/utils/api.ts:2344-2386; routes/display.py:18-23,79-183; routes/display_utils.py:116-313; routes/lightsensor.py:20-136,167-199; routes/data/intensity.json; routes/utils.py:32-35,85-108; src/data/publish-destinations.json:29-34,52-57; docs/REFACTOR_PLAN.md:680,331

## 4. How do overlays arrive?
[high] Connection: the page opens a WebSocket to VITE_WS_HOST, baked at build time (useOverlayWebSocket.ts:4,41). That is ws://95.141.21.170:8765, a public-IP hairpin (docs/VITE_PROD_PROPOSAL.md:40). The page sends nothing, so after 0.5 s the server registers it as a passive overlay client (overlay_ws_server.py:138-205).

What gets broadcast to every client:
- Server-originated send_overlay: {screens, html, duration, position, clear (default True), fadein, job_id} (display.py:185-274).
- Every frame from any sender, relayed (overlay_ws_server.py:171-177, 245-257). Only `html` and `substitutions` are stripped and only type generation_update is blocked (53-69).
- Each lux frame twice: raw, plus the enriched broadcast_lux_level (lightsensor.py:189-198).

Page handling:
- It filters on msg.screens (useOverlayWebSocket.ts:64).
- It adds an overlay for every frame, even one with no html, so lux, progress and transcription frames make phantom overlays (69-83).
- Default duration is 5 s, then a 5 s fade before removal (71-101).
- `clear` wipes all existing overlays (75).
- Overlays render at z 9999, above the mask, so they are never dimmed (OverlayContainer.tsx:61,82).
- Any <script> in overlay HTML is re-appended to body on every overlays change (index.tsx:144-153), so it re-executes and accumulates.

Token-gated paths that can push arbitrary HTML or script to screens: /test-overlay (app.py:135-153) and the scheduler `overlay` action (scheduler_handlers.py:1760+).
-> IMPLICATION: The legacy WS has no typed control messages. Any new 'show' message type sent on `/` would be relayed from any sender to all pages, and so could be spoofed, unless it is added to _RELAY_BLOCKED_TYPES (overlay_ws_server.py:58) or sent on the planned v2 path (/v2/screen/<id>). The page handler must branch on `type` before treating a frame as an overlay; this overlaps with P0.5 step 1.
Sources: src/pages/DisplayPage/hooks/useOverlayWebSocket.ts:4,29-115; src/pages/DisplayPage/OverlayContainer.tsx:52-98; src/pages/DisplayPage/index.tsx:37,144-153,362; overlay_ws_server.py:48-69,125-205,245-257,284-317; routes/display.py:185-274; routes/lightsensor.py:167-199; app.py:135-153; docs/VITE_PROD_PROPOSAL.md:40

## 5. What is the reload schedule, and what else restarts the page?
[high] Timed reloads:
- On mount, scheduleNextHardReload() (index.tsx:155-157) sets a single setTimeout to the next of reloadHours [2, 6, 10, 14, 18, 22], in the kiosk's local time (index.tsx:369-399).
- At that time it fades a black div (z 999999) over 1 s, then calls location.reload() at +1.2 s (index.tsx:401-422).
- The timer id is not kept, and there is no flag, URL param or server control to cancel or defer it.
- The deployed bundle dist/assets/index--tLZUHxv.js contains `2,6,10,14,18,22`.
- After a reload the page shows 'Loading…' until the HEAD polls return.

Other restart sources:
- kiosk-loop.sh pkills and relaunches all kiosk Chromes at the fixed URLs on any change to the set of connected HDMI outputs. In the per-TV X-screen branch that is kiosk-loop.sh:92-116; in the legacy branch it is 154-220.
- Nothing respawns a Chrome that has died. The loop only reacts to connector changes, and the comment at admin_api.py:157 claiming a respawn is wrong (DISPLAY_CONTROL_LAYER_DESIGN_2026-10.md:337).
- kiosk.service restarts only if Xorg dies (kiosk.service: ExecStart=Xorg, Restart=always).
-> IMPLICATION: Reloads at 18:00 and 22:00 fall inside likely evening show windows. Each one blanks the screen for about 1.2 s plus the reload and warm-up, and drops any live layer. Show mode needs a reload hold, or P5 step 3's health-gated build_id/kiosk.reload, before evening shows. Any HDMI hot-plug (possibly when a TV wakes) restarts both Chromes, so the show layer must re-attach on page load from server state.
Sources: src/pages/DisplayPage/index.tsx:155-157,367-422; dist/assets/index--tLZUHxv.js (grep match); media-server/remote-scripts/kiosk-loop.sh:30-31,92-116,154-220; media-server/remote-scripts/kiosk.service; routes/admin_api.py:154-167; docs/DISPLAY_CONTROL_LAYER_DESIGN_2026-10.md:337; docs/REFACTOR_PLAN.md:948

## 6. What URL params exist today?
[high] - `?nomask`: skips the mask fetch entirely, making both brightness and warm layers transparent (index.tsx:40,44; useMask.ts:22-27,64).
- `?nocolour`: removes only the warm multiply layer (index.tsx:41,347).
- `?display=fill|fit`: sets object-fit (index.tsx:43).
- `?debug`: shows MediaDisplay's debug HUD (MediaDisplay.tsx:58).

The controls panel can toggle nomask and display through navigate(replace) (index.tsx:182-198).

Not implemented yet, though proposed in the plan: `?kiosk=`, `?ctl=legacy`, `?live=1` (grep of src/ found none).

The kiosk URLs are fixed, with no params: http://95.141.21.170:8000/display/{north,south}-screen (kiosk-loop.sh:30-31; webview-manager.sh:6-7).
-> IMPLICATION: `?nomask` would suppress the mask, but it can only be applied by navigating Chrome (admin set-url through CDP), and that is lost at the next Chrome relaunch. URL params are not a viable show switch; show state has to come from the server.
Sources: src/pages/DisplayPage/index.tsx:39-44,182-198; src/pages/DisplayPage/MediaDisplay.tsx:58; src/pages/DisplayPage/hooks/useMask.ts:22-27,64; media-server/remote-scripts/kiosk-loop.sh:30-31

## 7. How could the page host a live layer with minimal change?
[medium] Smallest change that fits the plan:
1. Add a LiveLayer between <MediaDisplay> (index.tsx:310-332) and the brightness div (index.tsx:334): position absolute, inset 0, z-index 2, opacity-faded. This is the z-order in REFACTOR_PLAN.md:269 and DISPLAY_CONTROL_LAYER_DESIGN_2026-10.md:95-105.
2. Inside it, a muted autoplay <video> fed by a port of the lab MsePlayer (experiments/stream-latency/static/client.js:249). WebCodecsTrackPlayer (client.js:769) is the alternative; it gave a lower p50 in today's run.
3. Drive it from a server-sourced show state. The quickest form is a useShowState hook polling a new GET /api/screens/<id>/show every 1-2 s, modelled on useMask, or a typed WS message as in the previous answer.
4. Keep MediaDisplay mounted underneath as the fallback still. Render LiveLayer outside the `if (!currentSrc) return Loading…` gate (index.tsx:212-214).
5. With the layer under the mask, the mask dims live video. Show mode must therefore set a fixed mask or drop the warm multiply, because the multiply cost over 4K on the N95 is unmeasured (REFACTOR_PLAN.md:302, D27 item 9).

An iframe would also work with no code today: overlay HTML containing <iframe src=lab player> with a long duration, sent through the token-gated /test-overlay. But it sits at z 9999, above the mask. It is wiped by any later send_overlay, whose `clear` defaults to True (display.py:190; useOverlayWebSocket.ts:75). It dies on the timed reloads. And owner direction D15 rules out iframe presentation: live plays inline only (REFACTOR_PLAN.md:266; design doc lines 64,76).
-> IMPLICATION: A show-mode MVP is roughly: a LiveLayer, a show-state source, mask freeze and reload hold. It can be built before the full P4/P5 v2 hub, provided it is written as the first slice of the planned PlayerHost/LiveLayer and not a parallel hack. Confidence is medium because this is a design inference from the code, not something tested.
Sources: src/pages/DisplayPage/index.tsx:212-214,310-344; experiments/stream-latency/static/client.js:249,769; docs/REFACTOR_PLAN.md:266,269-281,302; docs/DISPLAY_CONTROL_LAYER_DESIGN_2026-10.md:64,76,95-109,236-278; routes/display.py:185-194; src/pages/DisplayPage/hooks/useOverlayWebSocket.ts:74-76

## 8. How could a room controller tell a screen to enter or leave show mode today?
[high] There is no show-mode concept, endpoint or WS message today.

Unauthenticated levers:
- Mask: POST/DELETE /api/<id>/brightness-override and POST /api/<id>/maskoff|maskon (display.py:42-104).
- Scheduler events: POST /api/schedulers/events/throw {event, scope, ttl}, for example wake, sleep, Standby, Blank, Random, Generate, Animate on north and south (scheduler_api.py:663-707; north-screen.json:67-223). It returns at once and gives no device outcome.
- POST /api/schedulers/<id>/pause and /unpause (scheduler_api.py:210,282). A paused scheduler does not process events (scheduler.py:1041-1049).
- Content: POST /api/publish (publish_api.py:32).
- WS :8765: any client can send JSON. It is relayed to all pages, but the page has no handler for it.

Token-gated levers (ADMIN_API_TOKEN):
- /api/admin-355729b8/set-url: CDP Page.navigate on port 9222 or 9223 (admin_api.py:170-264). Lost at the next Chrome relaunch.
- restart-screen and restart-kiosk (admin_api.py:126-167).
- /test-overlay: arbitrary HTML and script (app.py:135-153).

There is no HTTP endpoint for TV status or direct wake. Those exist only through scheduler events, Alexa (routes/alexa.py:337) and the CLI utils/samsung_control.py. Nothing reports what is actually on glass: there are no acks.
-> IMPLICATION: A room-supervisor MVP could already freeze the mask (brightness-override) and wake the TVs (throw 'wake'), then pause the schedulers. Actually starting live video needs new page code plus a new show/channel endpoint. All these state-changing endpoints are open to anyone who can reach port 5000/8000, which conflicts with the spec's §12 call for authenticated control.
Sources: routes/display.py:42-104; routes/scheduler_api.py:210,282,663-707; routes/scheduler.py:1041-1049; routes/scheduler/north-screen.json:67-223; routes/publish_api.py:32; routes/admin_api.py:30-36,126-264; app.py:135-153; overlay_ws_server.py:171-177

## 9. How can the mask be suppressed or frozen during a show?
[high] Today, without code:
- POST /api/<id>/brightness-override {"brightness": X} for each screen. The mask endpoint then returns X with warm_alpha 0 (display.py:79-91, 125-136). The page applies it within about 3 s plus a 1 s transition.
- DELETE the same path afterwards. maskoff gives brightness 1.0 the same way.
- Caveats: both are in-memory dicts (display.py:19,23), lost on a server restart. During a restart the page's fetch errors black the screen (useMask.ts:46-50). After the restart the sensor mask resumes. The EMA keeps tracking show lighting underneath, so clearing the override snaps to whatever the current lux gives.

Minimal code changes:
(a) Hold-last on fetch error (P0.5 step 6).
(b) A per-screen mask policy that the server honours, e.g. mask_source = sensor|frozen|show|off. It should be persisted (P4b already plans state/mask_overrides.json), with a show brightness that is fixed or taken from the Art-Net DIMMER of the screen's virtual fixture.
(c) While show state is active, the page ignores mask updates or holds the show value.
(d) Optionally make the sensor/EMA ingest mode-aware, so show light does not drive the solar/sensor state after the show.

Neither REFACTOR_PLAN.md nor the display-control design mentions show lighting interacting with the sensor (grep: no show, room, Hue or DMX hits). P4b's 50 ms response would make pumping worse.
-> IMPLICATION: Mask freeze is cheap to prototype through brightness-override. Production needs (1) the hold-last fix, (2) persisted, mode-aware mask policy and (3) P4b's design amended so show modes bypass or freeze the sensor loop. Without these, a server restart mid-show blacks the screens.
Sources: routes/display.py:18-23,79-136; src/pages/DisplayPage/hooks/useMask.ts:46-53; docs/REFACTOR_PLAN.md:329-357,680,906-933; docs/DISPLAY_CONTROL_LAYER_DESIGN_2026-10.md:226-229,280-317; routes/data/intensity.json

## 10. How can reloads be suppressed during a show?
[high] Timed reloads: not possible today without a code change. The reload is a module-level setTimeout with no handle or flag (index.tsx:155-157, 369-399). Minimal change: keep the timeout id, and on firing check a show-active flag (from the same show-state source), deferring by N minutes if a show is on. Better, as P5 step 3 plans: replace timed reloads with server-requested kiosk.reload at the next transition, health-gated (REFACTOR_PLAN.md:948; design doc lines 334-335).

Other reload sources:
- Hot-plug relaunch: kiosk-loop.sh pkills both Chromes on any connector change. It cannot be suppressed from the page, only mitigated, by waking TVs well before the show and having the page re-attach to live from server state on load.
- Admin restart-kiosk and restart-screen: operator discipline.

Workaround with no code: avoid shows spanning 18:00 and 22:00 kiosk-local time, which is not realistic.
-> IMPLICATION: A reload hold must be in the first show-mode slice. The refactor plan's reload replacement (P5 step 3) sits on the critical path for evening shows and could be pulled forward as a small standalone change.
Sources: src/pages/DisplayPage/index.tsx:155-157,369-422; media-server/remote-scripts/kiosk-loop.sh:92-116,154-165; docs/REFACTOR_PLAN.md:941-961; docs/DISPLAY_CONTROL_LAYER_DESIGN_2026-10.md:333-337

## 11. How can TVs be woken and put on the kiosk input before a show, and what bugs are known?
[high] Existing functions in routes/samsung_utils.py:
- device_wake (164-215): if the TV is unreachable, sends WoL to the destination MAC, sleeps 2 s and re-checks. If PowerState is standby, sends KEY_POWER and re-checks after 1.5 s. If art_mode is 'on', sends KEY_POWER to exit Art Mode and returns 'success' with no verification.
- device_sleep (135-162).
- device_standby (431-471): holds KEY_POWER for 8 s.
- get_status (498-583).
- get_power_state (106-133): Art-Mode aware but unused by these functions.

TVs come from src/data/publish-destinations.json at 192.168.1.150 and .151, port 8002. routes/data has stale 192.168.2.60 and .61 entries.

No function selects an input. Nothing sends KEY_SOURCE or KEY_HDMIx, or sets the source. The vendored samsungtvws has KEY_SOURCE (samsungtvws/remote.py:128) but nothing uses it. Exiting Art Mode relies on the TV returning to its last source.

Today's trigger: throw event 'wake' to each screen's scheduler. It runs device_wake with timeout 30, blocking that scheduler loop. The result appears only in the schedule log (scheduler_handlers.py:716-731), and nothing happens if the scheduler is paused.

Known bugs (REFACTOR_PLAN.md:1056 T2; KIOSK_INFRASTRUCTURE_NOTES.md:357-359):
1. device_sleep checks power_state in ['on','standby'] (samsung_utils.py:149) before art_mode. A Frame in Art Mode reports 'on', so 'sleep' sends KEY_POWER and flips the TV to HDMI. The Art-Mode branch (157) is unreachable then. The standby branch also toggles power.
2. get_status maps any art-channel exception, timeouts included, to art_mode='off' (565-573). device_wake can then return 'no_action' while the TV stays in Art Mode (210-212), so the show is invisible.
3. KEY_POWER is a toggle, so any misread state flips the wrong way.
4. WoL is sent from WSL2 in NAT mode. Its broadcast likely does not reach the LAN (inference, matching the established fact that multicast does not cross the NAT).

TV-side risks (FRAME_PRO_SETTINGS_2026-10.md:66,137-149,163): Auto Power Off may default to 4 h and works in Art Mode; owners report random Art Mode switches during HDMI use and hot-plug loss after the TV is off; the box goes into power saving when the screen is off. The settings are not applied yet (KIOSK_INFRASTRUCTURE_NOTES.md:22).
-> IMPLICATION: Pre-show wake needs these pieces:
- the T2 fix (check Art Mode first; unknown means do not toggle);
- a verify-and-retry loop with an explicit observed state, as the spec requires;
- a direct supervisor call or endpoint instead of scheduler events;
- an input-select capability, to be tested on firmware 0090_1301;
- a lead time of minutes, because a wake-induced HDMI hot-plug would relaunch both Chromes;
- a TV-state watchdog during the show.

T2 should move from 'pausable track' to a show prerequisite. Confidence is high for the code facts; the WoL-through-NAT and input behaviour are low-confidence inferences (see open questions).
Sources: routes/samsung_utils.py:28-80,106-215,431-471,498-583; routes/scheduler_handlers.py:716-760; routes/scheduler/north-screen.json:185-223; samsungtvws/samsungtvws/remote.py:128; docs/REFACTOR_PLAN.md:1056; docs/KIOSK_INFRASTRUCTURE_NOTES.md:22,357-359; docs/FRAME_PRO_SETTINGS_2026-10.md:66,84,137-149,163

## SURPRISES
- CONTEXT_NOTES says kiosk-loop.sh relaunches Chrome on 'HDMI hotplug or Chrome death'. The code only reacts to connector-set changes (kiosk-loop.sh:93-97, 155-161), and nothing respawns a dead Chrome. The comment at admin_api.py:157 claiming 'webview manager will auto-relaunch' is wrong, as DISPLAY_CONTROL_LAYER_DESIGN_2026-10.md:337 also notes.
- CONTEXT_NOTES says 'two Chrome windows on one 7680x2160 X screen, TVs 4K@30, Game Mode OFF (D24)'. Evidence against it:
- The working-tree kiosk-loop.sh now has a ZaphodHeads per-TV X-screen branch (kiosk-loop.sh:77-117), and media-server/remote-scripts/xorg/20-zaphod.conf was written 2026-10-13 19:21:59.
- From 19:22:21 the minutely status in media-server/maintenance.log flips from '2 displays connected' to 'Only 1 display connected'. That fits per-screen X, because check-status-compact.sh:139 queries only DISPLAY=:0. So per-TV X screens are probably live on the box since about 19:22 (inferred, not verified).
- REFACTOR_PLAN.md:311,317 says both TVs run 3840x2160@60 since 2026-10-13 after the owner switched Game Mode ON.
- KIOSK_INFRASTRUCTURE_NOTES.md:18,40 still say 4K30 on one X screen.
- In the per-TV branch any connector change pkills ALL kiosk Chromes (kiosk-loop.sh:99). One TV's HDMI hot-plug, for example on wake from Art Mode or One Connect power save, therefore restarts both screens.
- A failed mask fetch does not go 'neutral' as the comment claims. brightness=0 gives a fully black overlay (useMask.ts:46-50, 64).
- The sensor curve maps 0 lux to 0.01 brightness (routes/data/intensity.json). In a dark show room the screens would be dimmed to about 1%, and Hue or moving-head light would pump them. Neither REFACTOR_PLAN.md nor DISPLAY_CONTROL_LAYER_DESIGN_2026-10.md considers show lighting (no show, room, Hue or DMX mentions). P4b's ms-level response would make pumping worse.
- brightness-override and maskoff are in-memory only (display.py:19,23), so a server restart mid-show drops the freeze and blacks the screens during the restart. They are also unauthenticated and reachable through the WAN port forwards: brightness-override 0 is a remote blackout. The same holds for events/throw (wake/sleep TVs), scheduler pause and publish. Only admin_api and /test-overlay are token-gated (app.py:141; admin_api.py:30-36). This conflicts with AV spec §12, which wants authenticated state-changing endpoints.
- The legacy WS relays any sender's JSON to every display page, stripping only html/substitutions (overlay_ws_server.py:53-69,171-177). A new show-control message type on `/` would be spoofable unless it is added to _RELAY_BLOCKED_TYPES.
- Lux frames are broadcast twice per reading (raw relay plus enriched, lightsensor.py:189-198). Each one creates a phantom overlay on every page and re-runs every overlay <script> (useOverlayWebSocket.ts:69-83; index.tsx:144-153).
- Two divergent publish-destinations.json files exist. src/data wins (routes/utils.py:32-35), with TV IPs 192.168.1.150/151 and intensity_cfg. routes/data has 192.168.2.60/61 and no intensity_cfg.
- useBaseFileName only works by accident: the frontend's mapped destinations drop `type` and `file`, so it always falls back to screenId (useBaseFileName.ts:14-24; api.ts:1704-1713).
- Pausing a screen's scheduler (one obvious way to stop generation during a show) also stops it processing the 'wake' and 'sleep' events (scheduler.py:1041-1049). Wake must happen before the pause, or by a direct call.
- An overlay-iframe hack for live video would be wiped by any later send_overlay, because clear defaults to True (display.py:190; useOverlayWebSocket.ts:75). D15 forbids iframe presentation in any case (REFACTOR_PLAN.md:266).

## OPEN QUESTIONS
- Is the ZaphodHeads per-TV X-screen layout actually deployed on the box since about 19:22 today, and did bd update KIOSK_INFRASTRUCTURE_NOTES and the status scripts to match? This was inferred from file mtimes and maintenance.log, not checked on the box.
- Does a Frame Pro's wireless One Connect box drop HDMI hot-plug when the TV goes to Art Mode, standby or power save? If so, every wake or sleep relaunches both kiosk Chromes.
- After KEY_POWER exits Art Mode, does the TV always land on the One Connect HDMI port the mini-PC uses? Does firmware 0090_1301 accept KEY_HDMI1/2 or a source-select through samsungtvws? Not tested; no code does it today.
- Do Wake-on-LAN packets sent from WSL2 (NAT mode) reach the TVs, and do the TVs keep network wake enabled in standby?
- What should screen brightness be during a show: fixed (1.0?), per-mode, or driven by the screen virtual fixture's Art-Net DIMMER? Should the warm tint be dropped while live (D27 item 9)?
- Where is the Yoctopuce sensor relative to the Hue lamps, moving heads and the TVs? Can show light or screen light reach it (loop gain)?
- Should show mode be a claim in the refactor plan's ChannelState (a new 'room' owner ranked against operator/alexa/scheduler)? Or a separate policy bundle, e.g. mask_source=show, reload_hold, live_enabled?
- Will the owner approve P0.5 (hold-last mask, phantom-overlay and script fixes) now? It is a prerequisite for any show that must survive a server restart.
- How should a LAN room controller authenticate, given that the refactor plan says 'no app auth, reach is the control' and the AV spec wants authenticated room control with guests on the LAN?