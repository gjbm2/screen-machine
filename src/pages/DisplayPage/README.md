# Display page (`/display/:screenId`)

What each TV (and the snapshotter) loads. Contracts: `docs/av/contracts/LIVE_V1.md` §2 (frozen v1;
additive changes only). Design: `docs/AV_PROGRAMME.md` §3.1–3.3, `docs/DISPLAY_CONTROL_LAYER_DESIGN_2026-10.md`
§2.2 (layer stack) and §2.5 "Player".

## Three modes, chosen by URL

| URL | Mode | What runs |
|---|---|---|
| `/display/<id>` | **controlled** (the default since 14 Oct; the TVs' kiosk URLs never change) | Connects by itself to room_service (`VITE_ROOM_URL`, http→ws, e.g. `ws://192.168.1.205:8700`) as `/v1/screen/<id>?role=kiosk`: mask push (no 3 s poll while pushed), freeze/unfreeze, the live layer, `kiosk.state` every 5 s (with `viewport`, which room uses for the live render's shape). Art until a live cutover, then the MP4 stream, and back. |
| `/display/<id>?ctl=off` | **legacy** | Today's old page: 1 s file poll, 3 s mask poll, legacy overlay socket, controls panel, 4-hourly reloads. `?ctl=ws://host:port` points the socket elsewhere. |
| `/display/<id>?artonly=1` | **art-only** (snapshotter) | Only the art stack (still/clip + brightness + warm) on black. No polling, sockets, overlays, controls or live layer. `window.__smArtReady`, `window.__smRenderArt(state)`. |

Other parameters (unchanged): `?nomask`, `?nocolour`, `?display=fill`, `?debug`. Role: `?kiosk=viewer` →
`viewer`, `?kiosk=lab` → `lab`, otherwise (including none) `kiosk`. No keys (no auth in the AV stack).

**Pub/sub (15 Oct, owner: "pub/sub, no limit to subs"):** one page per screen is *driven* (the TV's own
device; it does the handshakes). Every other page, a standby glass tab or any viewer, any number of them,
is a *subscriber* that shows whatever the screen shows. Its `welcome` carries `live.attach` while a
session runs; it gets `live.attach {rejoin: true}` at go, on a session replacement and after restart
adoption, `live.leave` on exit and `live.abort` on failure. A subscriber that lost the stream is re-offered
`rejoin` with backoff. `attachLive` holds a rejoin while the tab is hidden and swaps in a fresh
`LiveController` for it. Another device opening `/display/<id>` while the screen is in art stays a
subscriber; the own device (User-Agent without versions) can reclaim the driven role. The page limit per
screen is gone. A stray second glass tab on the kiosk now plays the 4K stream too (extra N95 decode).
Contract: LIVE_V1 §2.2a rule 3.

The only changes to the default mode are the approved hygiene fixes:
1. a failed mask fetch keeps the last mask (it used to reset to brightness 0 and black the screen);
2. `<html>`/`<body>` are black while the page is mounted;
3. one error boundary per layer (media, mask, overlays, controls panel; plus the page), so a crash
   unmounts one layer and never shows white. The controls panel and legacy mask layers moved into
   their own components (`ControlsPanel.tsx`, `LegacyMaskLayers.tsx`) so their boundaries really
   isolate them; their DOM is unchanged.

## Layout

```
index.tsx              route: picks the mode; the page itself (default + controlled branches)
MediaDisplay.tsx       today's still/clip layer (default mode only, untouched)
OverlayContainer.tsx   overlays (both modes)
ControlsPanel.tsx      hover/tap panel (both modes; moved out of index.tsx unchanged)
LegacyMaskLayers.tsx   today's brightness/warm layers (default mode)
hooks/                 useFilePolling, useBaseFileName, useMask (fail-hold fix), useOverlayWebSocket
                       (optional accept gate), useBlackBody
common/LayerBoundary   per-layer error boundary
control/               protocol types, ControlClient (socket), store (reducer), MaskPoller, caps,
                       DisplayController (the controlled page's brain, outside React render)
art/                   ArtMedia + ClipCycle (holdable port of MediaDisplay), MaskController (rAF/ref
                       mask writes), MaskLayers, ArtOnlyPage, shared styles
live/                  MseLivePlayer (media WS → MSE), LiveController (cuts, timeouts), LiveLayer
```

Layer stack (controlled mode, bottom to top): body (black) → `LiveLayer` (z 1, the MSE `<video>` is
added imperatively) → art stack `[data-sm-art]` (z 2, opaque black: media, brightness z 5, warm z 6,
overlays z 9999, controls panel).

## Controlled mode in short

- **Socket** (`ControlClient`): envelope v1, `hello` with `caps` from `MediaSource.isTypeSupported`,
  dead after 6 s without an inbound frame, full-jitter reconnect `random(0, min(10 s, 0.5 s·2^n))`
  (n resets after 30 s up; immediate on `online`, and on close code 4008 only if that socket had been
  up for 5 s, so a server closing right after `hello` never causes a tight loop), `hb` after 2 s
  without sending. `&ctlkey=` on the page URL is sent as `hello.key` (LIVE_V1 §3; never logged).
  Deployment: room_service refuses an upgrade from an Origin outside `web_origins`, `kiosk_origins` or
  `art_page_base`'s origin with HTTP 403, which the browser reports as close 1006. The page then backs off
  as if the network had failed and keeps polling the mask, so a TV's kiosk origin must be in
  `kiosk_origins` before that TV gets `?ctl=`. An unknown screen is closed with 4404 (backs off).
- **Mask**: `mask.set` is applied through refs in `requestAnimationFrame` with `transition_ms`
  (linear); a burst coalesces to one commit per frame. Compositor-only (LIVE_V1 §2.2 rule 11): the
  brightness layer is static black at `opacity` 1 − brightness (pixel-identical to today's
  `rgba(0,0,0,1−b)`, 0 differing pixels), the warm layer animates only `opacity`, its colour changes
  with no transition and at most once per 5 s (a newer colour waits; a deferred one never lands during
  a freeze), both layers `will-change: opacity`, and unchanged values are not rewritten. The 3 s poll
  is paused while the socket is up, `policy.mask_push` is true and a pushed value (or the welcome's
  mask) has arrived on this connection; it resumes 5 s after the socket goes down. A failed poll keeps
  the last mask. While the art stack is hidden (live), masks apply with `transition: none`, and a
  commit still pending when a cut reveals the art is made first, with no transition.
- **Freeze**: new bases/masks are queued (latest only); a running mask transition and any through-black
  base change finish; overlays fade out over 600 ms (not their normal 5 s) and the ack waits for that,
  so nothing the ArtState lacks is on glass (new ones are dropped and counted); the controls panel
  closes; a clip is held on one frame (`ClipCycle.hold()`; mid cross-fade it snaps to frame 0). Then
  `ack frozen` with the exact `ArtState` (mask as drawn after nomask/nocolour; `base_url` as the page
  has it, i.e. relative `/output/<file>?t=…`). 30 s safety unfreeze. Unfreeze applies the queue as
  normal art changes.
- **Live** (`LiveController` + `MseLivePlayer`): `live.attach` mounts a muted `<video>` under the art
  stack and opens the media socket (LIVE_V1 §1.4). Each fragment is appended as a view of its socket
  message (no copy); queued fragments are joined only when appends fall behind. `play()` at rate
  1.0 once `target_buffer_ms` is buffered. **Entry cut**: inside the rVFC callback of the first
  presented frame after `play()`, one attribute flip (`data-hidden` on the art stack) hides the art,
  then `ack live_visible`. **Exit cut**: on `live.leave{hold_pts}`, the base is decoded, then the first
  presented frame with `mediaTime ≥ hold_pts − 0.5/fps` flips the art back, `ack art_visible{exit}`,
  teardown. Timeouts: warm-up 8 s → `live_failed`; leave 20 s → `exit-timeout`; no presented frame for
  2 s → `stall`; `live.abort` → `abort`; player/MSE error → `live_failed` before the cut, `error` after.
  `playbackRate` is never changed; latency is shed by a hard jump to `buffered.end − target` after 2 s
  more than 2× target ahead (`edge_jumps`); holes are skipped (`gap_skips`). Teardown: close socket,
  `SourceBuffer.abort()`, `removeAttribute('src')`, `load()`, remove the element.
  While a session is mounted, `html[data-sm-live]` hides everything else in the app (e.g. toasts).
- **While live or frozen**: overlays are dropped and counted (`overlays_dropped`), the overlay layer and
  controls panel are unmounted while live; new bases swap into the hidden art stack after `img.decode()`
  with no through-black.
- **Timed reloads** (02/06/10/14/18/22 h, `control/ReloadScheduler.ts`): `policy.timed_reload` (welcome,
  or a `policy` message, which changes only the fields it carries) false cancels them and reports
  `next_reload_at: null`; true (also before the first welcome, and once the socket has been down for
  `POLICY_LAPSE_AFTER_DOWN_MS`) runs today's schedule. Whatever the policy, a due reload waits while live,
  frozen or attaching, and until 60 s after returning to art. The default mode keeps today's
  `scheduleNextHardReload()` unchanged.
- **`kiosk.state`** every 5 s: `heap_mb`, `long_tasks`, `layer`, `frozen`, `base_url`, `mask` (as drawn),
  `policy` (as applied), `next_reload_at`, `live`, plus additive counters.
- `window.__smCtl` exposes the controller (state, event log) for diagnostics and tests.

## Art-only mode

```js
await page.waitForFunction(() => window.__smArtReady === true);
await page.evaluate((s) => window.__smRenderArt(s), artState);   // then screenshot
```
`__smRenderArt` applies base, mask and flags with no transitions, waits for `img.decode()` (or the clip's
`seeked` at `clip_t`), then two rAFs. Calls are serialised. It renders with the same components and
styles as the controlled page (`ArtMedia`, `MaskLayers`), so the pixels match. A relative `base_url`
resolves against the art-only page's own origin (Flask :5000 and Vite dev both serve `/output/`).

## Running it

Never `npm run build` here (dist/ is what the TVs load). Dev server:

```sh
VITE_API_URL=http://127.0.0.1:5000/api npx vite --port 5174 --strictPort
# default:    http://127.0.0.1:5174/display/devtest
# controlled: http://127.0.0.1:5174/display/devtest?ctl=ws://127.0.0.1:3001&kiosk=lab
# art-only:   http://127.0.0.1:5174/display/devtest?artonly=1
```
Note `/api` on `VITE_API_URL`: the API client appends paths such as `/<id>/mask` to it directly, as the
production build does with its baked `…:5000/api`. Without `/api` the mask fetch hits the SPA and fails.

## Tests (13–14 Oct 2026)

Type check and lint: `npx tsc --noEmit -p tsconfig.app.json` (no errors in this directory) and
`npx eslint src/pages/DisplayPage` (only pre-existing findings in the untouched `MediaDisplay.tsx` and
one pre-existing deps warning on today's media effect).

The browser tests ran in headless Chrome for Testing 145 (Playwright) against a fake room_service and a
fixture media server (libx264 → the repo's `stream_service/encode` FLV parser and fMP4 muxer, LIVE_V1 §1.4
framing); the harness lives outside the repo (see the build report). Results:

- Default page vs the production build (`:5000`) at 3840×2160 after 6 s: north-screen, devtest,
  north-screen?nomask: 0 differing pixels; `#root` DOM identical; body black.
- Art-only vs the frozen controlled page at 3840×2160: default, nomask, nocolour, fill, and a clip frozen
  at five points of its slow-motion cycle (primer, ease-in, cruise): 0 differing pixels. Art-only vs the
  production page for the same state (north-screen with its real sensor mask, devtest, ±nomask): 0
  differing pixels. A render takes 90–250 ms for a still, 0.5–1.3 s for a clip.
- The ported clip loop (`ClipCycle`) tracks today's MediaDisplay loop within ~40 ms over a full cycle; a
  frozen clip stays on the acked `clip_t` and resumes at pace after unfreeze.
- A failing mask poll: the production build goes fully black, this page holds the mask (0 diff).
- A crash in the controls panel unmounts only that layer (art unchanged, 0 diff).
- Mask push: send → style commit 1–9 ms, at the target value after `transition_ms`.
- Freeze: ack in ~170 ms (including a running 200 ms transition), exact ArtState; queued mask/base applied
  after unfreeze; 30 s safety unfreeze.
- Live (fixture stream h264 1280×720@30, 300 ms target buffer): attach → `live_visible` ≈ 0.8 s; entry cut in the rVFC of the first presented frame (frames_since_start = 1; an independent rVFC
  observer sees the art already hidden on that same frame); exit cut on the frame with
  `mediaTime = hold_pts`; stall cut 2.0 s after the last frame; abort 4 ms; warm-up timeout 8.0 s; wrong
  media key → `live_failed` in 28 ms; no `<video>` left after teardown; nothing but the video visible
  while live (no opacity/filter/transform on it or its ancestors).
- Liveness: dead socket detected 6 s after the last inbound frame; reconnect in < 0.5 s; poll resumes 5 s
  after the socket goes down.

Review pass (14 Oct, adversarial reviewer; harness in the scratch dir, `rv_tests.py`, `rv_ss_e2e.py`):

- **Real stream_service end to end** (a private instance, GPU 1, `h264-720p30`, anchor and exit target =
  a screenshot of the frozen page): freeze → session ready 1 s → attach → `live_visible` 0.97 s; the
  entry cut is in the first rVFC after `play()` and the anchor frame on glass differs from the art by
  mean 1.1/255 (encoding only); go → crossfade → live (30 fps, 0 dropped, 296 ms buffered); freeze →
  exit → `session.held` → `live.leave{hold_pts}` → exit cut on a held frame (`media_time ≥ hold_pts`),
  held frame vs art mean 1.6/255; 0 `<video>` after teardown.
- Opacity mask vs today's rgba mask (production page, art-only, frozen controlled page; default, nomask,
  nocolour, fill; north-screen with its real mask): 0 differing pixels at 3840×2160.
- Policy/timed reloads, `hello.key`, warm-colour limit (also across a freeze), reveal flush, overlay
  dismissal before the frozen ack (opacity 0, ack 0.7 s), acks for `live.abort`/`live.leave` arriving
  during the two-frame teardown window, appends as views (100 %), 4008 back-off (5 connects in 10 s),
  poll stops after a same-epoch welcome: all pass.
