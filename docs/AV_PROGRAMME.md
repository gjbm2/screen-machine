# Room AV build plan (v2)

**For:** the owner, and the agent sessions building the live screens, the room coordinator and the device adapters in this checkout.

**Maintainer:** screen-machine-2f, since late on 13 Oct, when the owner handed it over. screen-machine-10 wrote v1 and is now on latency calibration. **Send changes to screen-machine-2f rather than editing this file.**

**Date:** Tue 13 Oct 2026, late evening. **Status:** the build plan for this week. The decisions in §8 are open.

**Supersedes v1**, which is archived at `docs/av/AV_PROGRAMME_v1_2026-10-13.md`, including screen-machine-10's latency edits from 22:51.
- v1's research on Maestro, Hue, Art-Net, Sonos, ports and room authentication still holds. §6 relies on it for the later device work.
- v1's 12-week milestone plan, its cut design, its safety gates and its 60 s sensor hold are dropped (§1).

| Document | Relation to this plan |
|---|---|
| `docs/DISPLAY_CONTROL_LAYER_DESIGN_2026-10.md` (DCL, bd) | Still the reference for the page's layer stack, the live player rules and the failure table. This plan changes three things: how the anchor frame is made, where the page's control socket ends, and the build order (§9). |
| `docs/av/ROOM_STATE_MODEL.md` (v1.1, 14 Oct) | **The spec for room state and transitions** that §2 summarises. It adopts screen-machine-aa's reference model (`experiments/rf-spike/roomstate/`): targets, modes, the store, set and retrieve verbs, transition rules and one state file. It adds the fixes from the scenario traces S1–S14: interrupted chains, rollback, in-flight marks for blinds, hand moves and slot lifetime. §9.3 lists the changes needed in aa's code, and §10.2 the owner questions. room_service's coordinator is built to it. |
| `docs/av/DISPLAY_CONTROL_LAYER_V3.md` (v3.1, 14 Oct) | **The design for page signalling.** room_service owns all display-page signalling on `/v1/screen/<id>`: pictures, overlays, mask, policy and reloads. The TVs leave :8765. Its LIVE_V1 amendments (§8.6) go into LIVE_V1 rev 1.1, and its owner questions are in §10.2. It supersedes DCL's socket and transport sections; DCL's layer stack and player rules stand. |
| `docs/REFACTOR_PLAN.md` (RP, screen-machine-12) | Still the plan for the Flask codebase. This week's work changes no Flask code, so RP's gates (P1, P2, the P4 hub and its shadow week) are not on its path (§9). |
| `docs/KIOSK_INFRASTRUCTURE_NOTES.md` | Kiosk facts: sway, 4K60 on both TVs, powerline. |
| `docs/av/HANDOVER_RF_BLINDS_SPIKE.md` | The RF and blinds spike (mirror ball, Bloc Blinds), handed to a separate agent. |
| `docs/av/AV_PROGRAMME_v1_2026-10-13.md` | Device research for §6. |

---

## 0. Summary

1. **This week's goal: a seamless art → live → art switch on the TVs, controlled from the web app.**
   - It ships with one transition (crossfade) and one generative scene (an endless fractal zoom).
   - Both are plug-ins chosen by config, so more can be added without new plumbing.
   - Hardware (mirror ball, blinds, Hue, Maestro) joins late in the week, as the owner configures each item.
2. **Modes are whole-room configurations, and the coordinator's job is the transition between them.**
   - A mode declares the state it holds (screens live or in art, ball spinning, blinds down, sensor mask suspended) and any one-shot actions.
   - A transition is a plan: the difference between what the room is believed to be doing and what the target mode wants, put in order and timed (§2).
3. **The cut between art and live uses a screenshot from a headless Chromium on the workstation.**
   - That Chromium renders the same display page in an art-only mode. Its screenshot is the stream's first frame on entry and its last frame on exit.
   - Chromium composites both sides, so they match by construction, and the renderer knows nothing about masks or overlays.
   - Each cut briefly freezes the page's art state, so the screenshot and the screen agree (§3.3).
4. **The page gets a control socket:**
   - the mask is pushed in near real time (the sensor measures at up to ~10 Hz);
   - it carries live attach, cut, freeze and acks.
   - In live mode the page plays one full-screen 4K `<video>` and nothing else, as before.
5. **Three processes:**
   - Flask, unchanged;
   - `room_service` (new): the control plane, covering modes, screens, the mask push and snapshots, and later the device adapters;
   - `stream_service`: the real-time renderer, encoder and media server, graduated from the lab.

   No Flask code changes this week.
6. **Flash limiting is optional and off by default.** Nothing waits on it.
7. **Hue, Art-Net and Maestro wait** until the new Hue bridge and the MaestroDMX are configured later this week. The mirror ball and the blinds are a separate spike (`docs/av/HANDOVER_RF_BLINDS_SPIKE.md`).

---

## 1. Owner rulings, 13 Oct late evening

| Topic | Ruling | Where it lands |
|---|---|---|
| Art↔live anchor | A headless Chromium renders the same page with an art-only parameter (still plus mask, no overlays or live). Its screenshot is the starting and ending frame. It never involves the scheduler. | §3.3, §3.5 |
| Timeline | Days, not weeks. Start with the live renderer, the display page and the web control layer. One transition (crossfade) and one generative style (fractal zoom), both config-driven and extensible. | §4, §5, §7 |
| Safety | Every safety mechanism is optional. | §4.4 |
| After a show | No hold. Live switches off within seconds. The sensor keeps running and keeps driving the mask on the art side. | §3.4 |
| Hue and Art-Net | Later this week, once the new bridge and the MaestroDMX are configured. | §6 |
| Modes | Declarative whole-room configurations, but the key is coordinating transitions. Some things are held state (display mode, Sonos playing, ball spinning). Others are signals sent on a transition: entering disco starts the mirror ball through a radio unit; leaving disco, in any direction, stops it. | §2 |
| Blinds | If the RF unit, or another route, can drive the Bloc Blinds, lower them during the transition, so the neighbours don't see flashing lights. Handed to a separate spike. | §2.4, handover |
| Mask | The page moves from a 3 s poll to a near-real-time push over the socket. The freeze at each cut has to allow for that. | §3.2, §3.3 |
| Who builds | This session (screen-machine-2f) keeps this doc up to date and starts development on the web control layer and the real-time renderer. | §7, §8 D2 |

**Standing directives from earlier on 13 Oct, unchanged:**
- Art mode is composited in the DOM.
- Live mode is one full-screen 4K `<video>` at full intensity, with no mask and no overlays.
- Art↔live is a hard cut between matching frames, never a page fade.
- The player never runs above 1.0×.
- TV Game Mode is never used, and software never writes TV picture settings.

---

## 2. Modes and transitions

### 2.1 The model

A mode is a declaration in `config/room/modes.yaml`, with four parts:
- **`state`**: values held while the mode is active. Each value belongs to one subsystem.
- **`on_enter` / `on_exit`**: one-shot actions, fired on arrival or departure and not held. Example: entering piano pauses Sonos, after which Sonos is the user's.
- **`sequence`**: optional choreography, the order and timing of the transition's steps.
  - Items run in order. An item that is a list runs its members in parallel.
  - `after:` waits for an event from a subsystem.
- **`extends`**: inherit another mode and override parts of it.

```yaml
modes:
  gallery:                         # idle: art on both screens, house normal
    state:
      screens: {north: art, south: art}
      sensor_mask: follow
      mirror_ball: stopped
      blinds: unmanaged            # leave them wherever they are
  disco:
    state:
      screens:
        north: {live: fractal-zoom, in: crossfade-3s, out: crossfade-3s}
        south: {live: fractal-zoom, in: crossfade-3s, out: crossfade-3s}
      sensor_mask: suspended       # keep the pre-show mask; show light would skew the reading
      mirror_ball: spinning
      blinds: lowered
    sequence:
      - [blinds, screens]                       # start together
      - mirror_ball: {after: screens.live}      # ball starts as both screens go live
      # later: - lighting: {after: blinds.settled}   # nothing flashes until the blinds are down
```

### 2.2 How a transition runs

The coordinator keeps the current mode, the target mode and the **believed state** of every subsystem. All three are saved to disk.

1. **Compare** the believed state with the target mode's `state`. Only the differences become steps.
   - Leaving disco for any other mode stops the ball, because no other mode wants it spinning.
   - Moving between two modes that both want the ball spinning sends nothing to the ball.
2. **Add** the old mode's `on_exit` and the new mode's `on_enter`.
3. **Order and time** the steps, using `sequence` and each adapter's own dependencies (e.g. power to the wireless DMX before a Maestro show).
4. **Run them.**
   - Each step reports `ok`, `failed` or `unconfirmed`.
   - Progress streams to the web app.
5. **Interruption:** a new target arriving mid-transition cancels the steps that haven't started, and the coordinator plans again from the believed state.
   - A step already running (a screen crossfade) is finished or reversed by its own subsystem.
6. **Restart:** the coordinator reloads the mode, the believed state and the in-flight plan.
   - Readable subsystems are read again.
   - Signal-only subsystems are trusted as saved.
   - One-shot actions that already fired are not fired again.

### 2.3 Kinds of subsystem

| Kind | Examples | How the coordinator drives it | Changes made by hand |
|---|---|---|---|
| **Level, readable** | screens (the page's acks), the sensor mask; blinds through the Bloc hub, if that route works; later Hue, Sonos playback, TV power | Sets the target, checks it by reading it back, corrects drift | A policy per field: `respect` (the default: accept the change and report the drift until the next transition), `enforce` (set it again) or `ignore` |
| **Edge, separate codes** | the ball on a latched RF relay (separate ON and OFF codes); blinds through learned UP/DOWN/STOP codes; Maestro cues over OSC, which never replies | Sends a signal only when the desired value changes. Keeps a believed state, marked `unconfirmed`. Can safely re-send the same code. | Invisible without a readback |
| **Edge, toggle** | an RF relay with one code that flips it | Sends only on a change, and never re-sends | Invisible, and the drift can't be corrected. Prefer separate codes, or a power-metering plug for a readback. |
| **One-shot action** | pause Sonos on entering piano; a hazer burst | `on_enter` / `on_exit` | — |

**Each adapter declares:**
- its kind and its states;
- how long a change takes (blinds travel, TV wake);
- its dependencies.

Adding a device means one adapter plus some YAML.

### 2.4 Devices in view this week

| Device | Kind | Notes |
|---|---|---|
| **Screens** | level | §3. The first subsystem, built now. |
| **Sensor mask** | level | `follow` or `suspended`, set per mode (§3.4). |
| **Mirror ball** | edge (separate codes, preferred) | The radio unit (Amazon UK B083LLLVPN) is a **BroadLink RM4 Pro**: a Wi-Fi IR + RF (433/315 MHz) transmitter that `python-broadlink` controls locally. It only transmits, so the motor needs a 433 MHz receiver; a latched relay with separate ON and OFF codes is preferred. **On the LAN since 15 Oct** at 192.168.1.178 (mac 34:8e:89:b1:5d:b2, devtype 0x520b); unicast from WSL works. Adapter `room_service/adapters/broadlink_rf.py` (screen-machine-d4), device file `config/room/rf/glitterball.json`; it reads the codes live and stays `dry_run` until they are learned, then runs `edge_discrete` (ON + OFF) or `edge_toggle`. Labelled "Glitterball" on the Room page (Drawing room › Effects); spins in Party and Disco. **Blocked on the owner** unlocking the RM4 in the BroadLink app so the codes can be learned. |
| **Blinds** | **level**, with a position for each blind | Bloc iQ hub at 192.168.1.136 (Dooya/Motion protocol, UDP :32100), driving 13 two-way battery roller blinds. Proven by screen-machine-aa (the spike) on 13 Oct:<br>• unicast request/reply from WSL works (~70 ms);<br>• multicast pushes don't reach WSL, so the adapter polls the hub's cache (no key needed, no RF sent);<br>• no Windows relay is needed.<br>Code: `experiments/rf-spike/rfspike/`. Addresses: `config/room/rf/blinds.json`. Behaviour: §2.5 (D5). |
| **Display page policy** | level, read back from the page's `kiosk.state` | Target `page.<screen>.timed_reload`. The page reloads itself every 4 h (02/06/10/14/18/22h) because of past browser instability and memory leaks.<br>**Owner, 13 Oct late:** hard reloads must not cut across live, so this is mode config, e.g. `false` in live modes and `true` in normal. room_service sends it over the page's control socket (LIVE_V1 §2.1 `policy`).<br>Backstop: the page never reloads mid-live or mid-cut, whatever the config. |
| Hue, Maestro, Sonos | — | Later this week (§6). |

The spike (screen-machine-aa, `docs/av/HANDOVER_RF_BLINDS_SPIKE.md`) has proved the blinds route. It is proposing an adapter contract v0.2 and a state-model spec, with a reference planner and scenario tests, in `experiments/rf-spike/`. The proposal covers snapshot and restore, edge-only subsystems, the TV's display mode as a slow level subsystem with a dependency, interruption and restart. screen-machine-2f adopts or adapts it into room_service (§2.5).

### 2.5 Snapshot and restore (D5, owner, 13 Oct ~23:10)

**The ruling:**
- Every live mode needs the blinds **down**.
- Returning to art, or to any other non-live mode, restores **each blind to the position it had before the live episode**. That means a restore per blind, not "raise all".

**What the coordinator needs to support it** (the next increment after the v1 build, based on the spike's proposal):
- **Values per member:** a subsystem's state can hold one value per member, e.g. a 0–100 position for each blind.
- **A `restore` target:** a mode can say `blinds: restore` instead of naming a value.
- **The snapshot:**
  - It is taken just before the first command of an episode that changes a subsystem away from its house state.
  - It is saved to disk and survives restarts.
  - A live → live hop does **not** take a new snapshot.
  - `restore` goes back to that snapshot and then clears it.
- **Interruption:** an interrupted transition still restores to the *original* snapshot, never to an intermediate position.
- **The same mechanism** serves Hue (restoring the house lighting after a show) and, later, the TVs' display mode.

The v1 coordinator being built now has the diff planner, the saved believed state and replanning after an interruption, but not snapshot and restore.

### 2.6 Driving modes

- **Web app:** a mode switcher (§5), which calls `PUT /api/v1/mode {mode}`.
- **Test overrides:** the live test panel writes to the screens subsystem directly (`PUT /api/v1/screens/<id>`). The coordinator treats that as a change made by hand, under `respect`.
- **Later:** a scheduler action, Alexa, and a phone UI, using the pairing model in v1 §3.6.

---

## 3. Screens: art, live and the cut

### 3.1 The page

The page evolves `src/pages/DisplayPage`, following the DCL's layer stack (DCL §2.2).

**Layers:**
- The art stack (still or clip, the brightness and warm layers, overlays) sits above a live layer (`<video>` over MSE). The video warms up underneath the art stack.
- The page background is black, and each layer has its own error boundary (DCL I4).

**Live mode:**
- The art stack is hidden, and overlays and controls are unmounted. Only the video is drawn, with no CSS effects on it.
- `playbackRate` stays at 1.0. Latency is shed by a hard jump to the live edge (DCL §2.5, Player).

**How the page is loaded:**
- **Normal**, `/display/<id>?kiosk=north|south`: what the TVs load.
- **`?artonly=1`**: what the snapshotter loads.
  - It shows the art stack only (still or clip, plus the mask): no overlays, controls, live layer, polling or sockets.
  - It renders whatever state it is handed through `window.__smRenderArt(state)`, and reports ready after `img.decode()` and two frames.
- The existing `?nomask`, `?nocolour` and `?display=fill` flags keep working ([index.tsx:39-43](../src/pages/DisplayPage/index.tsx#L39-L43)). The freeze ack reports them so the screenshot honours them.

**New behaviour stays inert until room_service turns it on for a screen.** A page with no socket behaves exactly as it does today.

### 3.2 The control socket and the near-real-time mask

**The socket:** one WebSocket per page, to `room_service`.
- **Address:** it comes only from an explicit `?ctl=ws://…:3001` URL parameter, never from the page's origin.
  - Without it, the page opens no socket and behaves exactly as today. So a frontend build that ships early can't change the production kiosks.
  - Origin isn't usable anyway: today the kiosks load the page from the WAN address (`media-server/remote-scripts/wayland/kiosk-wayland.sh:18-19`), and the new ports must stay LAN-only.
  - Turning it on for a TV means adding `?ctl=` to that kiosk's URL, an owner-approved change.
- **Contracts:** the exact messages, APIs and config live in `docs/av/contracts/LIVE_V1.md`.
- **Protocol:** the envelope, hello and welcome snapshot, revs, heartbeat, 6 s dead-socket watchdog and jittered reconnect follow DCL §2.3. The message set is below.

**Mask push replaces the 3 s poll** ([useMask.ts:4](../src/pages/DisplayPage/hooks/useMask.ts#L4)).

Today:
- The light relay sends `{sensor_name, lux}` to Flask's WebSocket on :8765.
- Flask records it and relays it to every connected client ([overlay_ws_server.py:172-177](../overlay_ws_server.py#L172-L177)).
- The page polls `/api/<id>/mask` every 3 s and fades over 1 s.

**The target** (LIVE_V1 §2.2b, the next increment): the light relay sends lux straight to room_service (`/v1/ingest/lux`), and room_service computes the mask itself.
- That's a pure port of Flask's curve with the DCL's faster filter, so the sensor path no longer depends on Flask's legacy :8765 server or on Flask computing the mask.
- It needs a small change to the relay on the kiosk, with the owner's OK. The relay's repo copy is screen-machine-10's calibration relay, `tools/room/latency_cal/lightsense.py`.

**Interim, the current build**, with no change to Flask:
1. room_service connects to :8765 as a plain receiver.
2. On each lux frame, with a 1 s backstop, it fetches `/api/<id>/mask` from Flask on localhost. That keeps all the existing curve, override and per-destination logic.
3. It pushes `mask.set` to the page when the value moves beyond a deadband.
4. The page applies it through refs inside rAF, with a short transition (`transition_ms`, about 150–250 ms, set in config).

Notes:
- **Rate:** the Yocto-Light-V5 on firmware 62070 measures at most about 10 times a second. The relay's normal send rate still needs checking.
- **Fallback:** the 3 s poll stays for when the socket is down, with one fix. Today a failed fetch resets the mask to brightness 0, which blacks the screen ([useMask.ts:46-50](../src/pages/DisplayPage/hooks/useMask.ts#L46-L50)); instead it will hold the last mask.
- **Why the target matters** (owner, 13 Oct late): the legacy :8765 server isn't trusted to be robust, and the mask must not rely on a 3 s poll anywhere in the chain. The page's own control socket already avoids :8765 entirely (LIVE_V1 §2.2a).

The still (base content) keeps coming from today's polling of Flask. Pushing content is not on this week's path.

**Messages (v1; additive changes only):**

| Direction | Messages |
|---|---|
| room_service → page | `welcome{snapshot}`; `mask.set{rev, brightness, warm_hex, warm_alpha, transition_ms}`; `freeze{fid}`; `unfreeze{fid}`; `live.attach{session, url, codec, w, h, fps, target_buffer_ms}`; `live.leave{session, hold_pts}`; `hb` |
| page → room_service | `hello{role: kiosk\|viewer, screen, kiosk, build, caps, flags}`; `ack{kind: frozen, fid, state}`; `ack{kind: live_visible, session, media_time}`; `ack{kind: art_visible, session, media_time, reason?}`; `ack{kind: fallback, reason}`; `kiosk.state{…}` every 5 s; `hb` |

### 3.3 The cut

This replaces the anchor arithmetic in DCL §2.5.

**Principle:** the stream's first and last frames are screenshots of the same page, in the same art state, rendered by Chromium. The renderer only holds an image and runs transitions.

**The freeze.** The mask is pushed up to ~10 times a second, and the scheduler can publish at any moment, so the screen can change between a screenshot and the cut. Each cut therefore starts with a short freeze:
- room_service stops pushing `mask.set` to that screen and sends `freeze{fid}`.
- The page stops applying new stills and masks; it queues them instead. It also:
  - lets any running mask transition finish (at most `transition_ms`);
  - dismisses any overlay with its normal exit;
  - pauses a clip base.
- It then acks `frozen{fid, state}` with exactly what is on its art stack: `{base_url, kind, clip_t, mask{brightness, warm_hex, warm_alpha}, flags{nomask, nocolour, fit}}`.
- The scheduler is not involved. Publishes carry on, and the page applies them after the cut.

**Art → live.** The timeout for each step is in brackets. Any timeout aborts the switch, unfreezes the page and leaves the screen in art.
1. Freeze. The page acks its state [3 s].
2. The snapshotter renders that state in the `?artonly` page at 3840×2160 and takes a screenshot [3 s].
3. room_service creates a stream session with that screenshot as the anchor, plus the scene and transition presets [5 s to `ready`, the first IDR of the held anchor; the hold limit is 30 s].
4. room_service sends `live.attach`, and the page warms the video up under the art stack [8 s].
5. On the first presented video frame (rVFC), the page hides the art stack in one property change and acks `live_visible` [10 s].
6. room_service sends `go`, and the renderer runs the `in` transition (crossfade) from the anchor into the scene.
7. room_service unfreezes the page. Mask and still updates resume on the hidden art stack, subject to the mode's `sensor_mask`.

**Live → art:**
1. Freeze. The page acks the state of its hidden art stack, which kept up with the current still during live, and with the mask too when `follow` is set [3 s].
2. The snapshotter takes a screenshot of that state [3 s].
3. `exit{target_png, transition}`: the renderer runs the `out` transition (crossfade) from the scene into the target, holds it, and reports the hold's first pts [transition length + 5 s].
4. `live.leave{hold_pts}`: the page cuts on the first presented frame with `mediaTime ≥ hold_pts`.
   - It shows the art stack, acks `art_visible`, detaches the stream and frees the decoder.
   - Limit: transition length + 10 s. Past that it cuts anyway and reports `exit-timeout`, an unmatched cut.
5. room_service deletes the session and unfreezes the page. Queued stills and masks apply as ordinary art changes.

Leaving live therefore takes the `out` transition's length plus about a second. There is no hold after a show.

**Failures** (in the spirit of DCL §2.5 "Stalls" and §2.7):

| Failure | What happens |
|---|---|
| No new video frame for 2 s | The page hard-cuts to its art stack (an unmatched cut, so a failure path) and reports `fallback`. room_service goes live again through the entry handshake (a new freeze and screenshot), backing off 2/5/15/30/60 s. The last step repeats, so the screen keeps trying and never parks in art. A renderer restart isn't counted, and the screen goes back live as soon as the renderer is up. |
| stream_service or the encoder crashes | The same. |
| The page reloads, or Chrome respawns, while the desired state is live | The page loads in art and connects, and room_service runs the entry handshake. |
| room_service restarts | The page keeps playing. room_service re-reads the desired state and the sessions, and adopts them. |

**Day-1 checks.** These are measurements, not gates.
- **Headless versus kiosk rendering:** compare the snapshotter's PNG with a CDP screenshot of the kiosk Chrome showing the same still (CDP 9222 north / 9223 south; read-only, in an agreed quiet window).
  - The risk is image scaling when the still isn't 3840×2160: headless rasterises on the CPU, the kiosk on the GPU.
  - If they differ visibly, pre-scale the stills to 3840×2160 or run headless with GPU raster.
- **Colour:** the PNG is sRGB. The encoder's matrix, range and transfer tags must make Chrome show the video frame with the same values as the `<img>`. Compare a paused video frame with the still, both by CDP screenshot.
- **Clip bases:** the page pauses at `clip_t`. Playwright's Chromium may not decode H.264 or HEVC, so the snapshotter extracts that frame with ffmpeg and renders it as an image.
- **Accepted:** the slight softening from 8-bit 4:2:0 encoding at the cut.

### 3.4 The light sensor around live

- The sensor and the mask never stop.
- **`follow`:** while live, `mask.set` goes to the hidden art stack, so the exit screenshot uses the room's light at that moment.
- **`suspended`** (set per mode) holds the last pre-show mask instead.
  - Use it for modes whose own light (screens at full intensity, Hue, the heads) or lowered blinds would skew the reading.
  - When the mode ends, the art mask moves from the held value to the current one with its normal transition.
- v1's 60 s hold was for Hue being restored after a show. It is dropped.

### 3.5 The snapshotter

- It runs inside room_service, using Playwright for Python in room_service's own venv.
  - A Chromium build is already cached in `~/.cache/ms-playwright`. Pin the package to the version that matches it.
- **Pages:** one persistent page per screen, with a 3840×2160 viewport at DPR 1, loading `http://127.0.0.1:5000/display/<id>?artonly=1`.
- **`snapshot(screen, state) → PNG`:** call `__smRenderArt(state)`, wait for ready, then `Page.captureScreenshot` as PNG. Target: 1 s or less.
- **Hand-off:** the PNG goes to stream_service as bytes inside the session call. There's no URL to fetch, so nothing needs an allowlist.

---

## 4. Real-time renderer and stream service

### 4.1 Shape

**The service:** `stream_service/`, graduated from `experiments/stream-latency/`. It keeps the lab's aiohttp server, HEVC 3840×2160 NVENC encode, low-delay fMP4 muxer and ws-fMP4 delivery to MSE.

**Sessions:** one per screen, each pinned to a GPU. North uses the RTX 4060. South uses the Quadro P4000, pending a pacing test on that card.

**Pipeline per session**, all on the GPU:

> sources → transition compositor → optional output filters → RGB→NV12 → NVENC → fMP4 → WebSocket

The renderer works in RGB, which it needs in order to blend stills and scenes, and converts to NV12 once at the end. The lab kernel rendered straight to NV12 ([gpu_source.py](../experiments/stream-latency/lab/gpu_source.py)).

### 4.2 Plug-ins and config

| Plug-in type | v1 members | Interface |
|---|---|---|
| **Sources** | `image` (holds a PNG: the anchor and the exit target), `fractal_zoom`; later scenes | `setup(ctx, params)`; `render(t, dt, params)` → an RGB frame on the GPU |
| **Transitions** | `cut`, `crossfade` | `render(progress, frame_a, frame_b, params)` → a frame. `duration_s` and `easing` are common to all. |
| **Filters** | none on by default; later the flash limiter (§4.4) | — |

**Each plug-in declares:**
- a `name`;
- a JSON Schema for its parameters, with UI hints (range, step, unit, label);
- its defaults.

The registry finds plug-ins by module, so a new scene is one file plus an optional preset.

**Presets** live in `config/live/scenes.yaml` and `config/live/transitions.yaml`, and reload when the files change. Modes and the web app refer to presets by name and can override their parameters.

```yaml
# config/live/scenes.yaml
fractal-zoom:
  source: fractal_zoom
  params: {speed: 0.35, palette: ember, detail: 2}

# config/live/transitions.yaml
crossfade-3s:
  transition: crossfade
  params: {duration_s: 3.0, easing: ease-in-out}
```

- **Live parameter changes:** a `PATCH` on the session. The renderer eases to the new values rather than jumping.
- **Input bindings** (later; not built now): once the show bus exists, a parameter can be bound to a show-state input, e.g. `speed: {bind: show.activity, range: [0.2, 1.5]}`.

### 4.3 The fractal zoom

- It starts from the lab's CUDA Julia kernel ([gpu_source.py](../experiments/stream-latency/lab/gpu_source.py)).
- **Making it endless in float32:** zoom into a self-similar point, and loop the zoom over one period of that self-similarity, so precision never runs out.
  - float64 is too slow at 4K60 on these cards. It runs at 1/64 of the fp32 rate on the 4060 and 1/32 on the P4000.
- It must fit the 16.7 ms frame budget at 4K60, with headroom for the transition and the colour conversion.

### 4.4 Pacing, latency, profiles and safety

**Pacing:** 4K60 needs evenly paced frames.
- Today the lab drops 6–7 % of live frames, for two reasons:
  - the generator misses its 16.7 ms deadline about 2 % of the time, because of a 12 MB raw pipe write per frame;
  - the lab player ran above 1.0×.
- The fix is GPU-resident render → NVENC. PyNvVideoCodec works today, at the cost of 3 frames of delay.
- Until that lands, the demo runs at 4K30. The transitions are the same.
  - **Measured 14 Oct (renderer review):** under host load (load average 5–19), `hevc-2160p60` on the P4000 ran 31–87 % late with resyncs, while `hevc-2160p30` ran 0 % late. `config/room/room.yaml` therefore sets `profile: hevc-2160p30` for both TVs.
  - The exit cut is also softer than the entry cut on textured stills: luma mean 3.2 off at `hold_pts`, against 1.1–1.6 at entry, until the next keyframe. screen-machine-04 decides the fix: report `hold_pts` at the first keyframe after the still is exact, or force a keyframe once encoding runs from GPU memory.
- A late render repeats the previous frame in its slot, so the timeline never shifts.

**Latency, measured 13 Oct** by screen-machine-10 (`tools/room/latency_cal`, south TV; the result is in `config/room/latency.json`):

| Leg | Value |
|---|---|
| Chrome "presented" → light at the room sensor, brightening | 179 ms |
| The same, darkening | 104 ms |
| Render → light (the lab's 130 ms capture → presented at 4K60, plus 179 ms) | ≈ 309 ms |

- The leg from "presented" to light covers the TV, the One Connect box and the panel. The panel brightens more slowly than it darkens, probably because of local dimming.
- For beat-phased visuals later, the renderer draws about 0.28–0.30 s ahead, reading its render-ahead from `config/room/latency.json`, with a ±50 ms trim. Not this week.
- Re-run the calibration (about 35 s, no sudo) after any change in the display chain.

**Safety:**
- An optional output filter (a flash limiter on downsampled luma and red) and a `photosafe` flag that a mode can set.
- Off by default and not built for the first demo. Nothing waits on it.

### 4.4a Live-mode cueing, auto-mix, inputs and palette (owner direction 14 Oct ~00:10, via screen-machine-04)

**Shape:**
- **Art → live happens once, by hand.** After that, every scene change gets its own transition.
- **What triggers a change:** a manual mode or scene change, a Maestro section change, or the renderer "mixing it up".
- **Inside a scene,** live DMX drives the visuals in real time: beat pulses, pacing.
- **Several transition types:** crossfade by default, plus screen melt and others.
- **Demo target:** art → T1 → effect 1 → T2 → effect 2 → … → art, with simulated DMX. Start and stop are by hand; the middle is random or music-led.

**Approved shape** (contract additions pending in `docs/av/contracts/proposals/LIVE_V1_cue_auto_inputs.md`, to be merged into LIVE_V1 by screen-machine-2f):
- **`cue`** is the general verb: from whatever is on screen to a scene or a still, via a transition, at `now`, `beat` or `bar`.
  - `go` and `exit` are aliases.
  - A cue to a still keeps exit's semantics (`held` + `hold_pts`), because the page's exit cut depends on it.
- **`auto` policy, owned by the renderer:**
  - a scene pool and weighted transitions from presets;
  - a dwell window;
  - follow-music cueing, snapped to the beat;
  - `paused`.
  - Every auto cue emits an event. A mode sets the policy through room_service.
- **Inputs: one Art-Net receiver.**
  - room_service owns the Art-Net ingest and the fixture patch, and publishes a localhost **show bus**: per-frame, latest-value, with `t_ms` on the shared host monotonic clock.
  - The renderer subscribes. It does no audio or DSP analysis of its own. Beat, onset and section events come from the show bus, and the renderer's only addition is a beat follower that extrapolates the phase forward to compensate for latency (ruling of 14 Oct 00:40).
  - Presets bind parameters to show channels (`bind`).
  - A show-bus simulator stands in until the Maestro and the relay exist.
  - *(15 Oct, owner: our own show engine, `docs/SHOW_ENGINE.md`, screen-machine-c8)* The beat, onset, energy and section inputs come from **show_service's own audio DSP** (P0 feeds them as a virtual Maestro, ArtDmx to 127.0.0.1:6454 in patch.yaml's layout; later its own bus v2, LIVE_V1 1.2). The renderer still does no audio analysis of its own.
- **Palette is its own lasting axis** (owner), separate from effect and modulation.
  - It's a room-level state target (`look.palette`), set by modes or by hand, and propagated by room_service to every renderer session and later to Hue.
  - Its value is a preset, `follow: maestro` (RGB from the show bus) or a drift.
  - The renderer applies one shared palette pass over an indexed field; non-indexable sources (photos, video) get a tint only.

### 4.5 Session API

room_service is the only caller, and every call carries a bearer token.

| Call | Purpose |
|---|---|
| `POST /api/sessions {screen, gpu, profile: hevc-2160p60\|hevc-2160p30, anchor_png, scene, params, transitions{in, out}}` | Returns `{session, attach}`. The session holds the anchor until `go`. |
| `POST /api/sessions/<id>/go` | Run `in` from the anchor into the scene. |
| `PATCH /api/sessions/<id> {scene?, params?, transition?}` | Change scene inside the stream (with a transition), or change parameters. |
| `POST /api/sessions/<id>/exit {target_png, transition}` | Run `out` into the target and hold it. `hold_pts` is reported as an event. |
| `DELETE /api/sessions/<id>` | End the session. |
| `GET /api/sessions`, `GET /api/state` | Sessions and health: fps, late frames, encoder queue, clients. |
| `GET /api/catalog` | Sources, transitions and filters with their schemas, plus the presets. |

---

## 5. Web app control layer

A new **Room** page in the web app (React, `src/pages/`), with three parts:

- **Mode switcher:**
  - shows the current mode, the target, and transition progress;
  - shows each step's outcome, from room_service's event stream.
- **Screens panel**, one per screen:
  - art or live state;
  - scene and transition pickers, filled from `GET /api/catalog`;
  - parameter forms generated from the JSON Schemas, so a new scene needs no UI code;
  - live parameter edits;
  - Go live and Back to art;
  - health: fps, dropped frames, stalls, latency.
- **Snapshots:** the last anchor and exit-target PNGs for each screen, for checking the cuts.

**Access:**
- It talks to room_service's HTTP API and event socket over the LAN.
- It is not exposed through ngrok or any WAN port.
- **Auth (owner, 13 Oct late): operate open, edit gated.** Anyone on the Wi-Fi can operate the room (e.g. a visiting pianist switching modes): modes, scenes, live parameters, setting blinds by hand.
  - Editing the setup needs the admin token: modes, transitions, stored values, devices.
  - The machine channels always need keys: the kiosk control socket and sensor ingest.
  - Origin and Host checks stop a random web page on a phone from driving the room.
  - Details: LIVE_V1 §3.

**How phones reach it** (screen-machine-10's measured notes, 13 Oct; next increment):
- **Separate build:** the panel gets its own Vite entry and output directory, **not `dist/`**. room_service serves it at `/` on its own port, 8700. A panel deploy is then never a kiosk deploy, and never a publish through Flask, the WAN ports or ngrok.
- **Same origin:** the page's default API base is `location.origin`, with no hardcoded port.
- **Caching:** `no-cache` on the HTML and immutable caching on the hashed assets, so phones pick up deploys.
- **Discovery:** a printed QR card for `http://192.168.1.205:8700/`, or `http://pc22.local:8700/` over mDNS, then Add to Home Screen.
  - Phones get public DNS resolvers from DHCP, so local DNS names don't resolve on them.
  - Chrome treats a public name on plain HTTP as an insecure public site, so **no friendly public name on plain HTTP.**
- **Later (v1):** a friendly name such as `room.gregmarsh.co.uk`, via Caddy on Windows owning 80/443 and Cloudflare DNS-01 certificates. It needs coordinating with screen-machine-ca (Spectre), which owns the Windows rules.
- **To test:** one iPhone (Safari, with Private Relay on) and one Android phone (Chrome): scan, load, Add to Home Screen, reopen.

**One back end (owner ruling, 14 Oct 22:25).** room_service runs inside the Screen Machine app process: `app.py` starts it through `room_in_app.py` on a daemon thread with its own asyncio loop. It still serves all its HTTP and WebSocket routes on LAN :8700, so the display pages, the Room page (`/room` in the app) and the Windows port-forward don't change, and Flask's public :5000 door has no room routes. stream_service (:8702) stays a separate worker. While the separate `scripts/ctl room` holds :8700, the in-app copy waits and retries every 10 s. **Cutover:** add the missing packages to the Flask venv (`.venv/bin/pip install playwright==1.58.0 pycryptodomex==3.24.1 hue-entertainment==0.1.2`), then (a) `scripts/ctl stop room`; (b) restart Flask in its `flask` screen session by its own pid, not with `start.sh restart`, whose `pkill -f` is a hazard; (c) check that `ss -Hltnp 'sport = :8700'` shows Flask's pid and `/api/v1/status` answers. Exact commands are in `room_service/README.md` ("Runs inside the app"). The ctl `room` entry (screen-machine-ca) is then unnecessary. The TVs' QR codes now open `http://192.168.1.205:8080/room?screen=<id>` (`ROOM_QR_BASE`). That page opens and highlights the screen's card and links to its art (`/?publish=<id>`) and its scheduler (`/scheduler?destination=<id>`).

---

## 6. Later this week: devices

These come in the order the owner gets them configured. Each one is an adapter plus some YAML.

1. **Mirror ball and blinds:** they come out of the spike (`docs/av/HANDOVER_RF_BLINDS_SPIKE.md`) as prototype adapters. room_service then adopts them.
2. **Hue** (the new bridge):
   - REST first;
   - snapshot the house's lighting before a show and restore it afterwards;
   - Entertainment streaming later.

   v1 §3.7 and its research file r7 have the details and pitfalls.
3. **Maestro** *(optional since 15 Oct: show_service is the room's own DSP and art director, `docs/SHOW_ENGINE.md`; nothing depends on a Maestro)*:
   - OSC to trigger show cues. Never send `/live/*`, which drops Show mode; black out with `/global/brightness 0`.
   - Art-Net in through a Windows UDP relay, so show-state can drive the renderer's parameters and Hue (v1 §3.1, §3.4 K2/K3, §3.5).
4. **Sonos:** SoCo, as one-shot actions (v1 §3.7).

Room authentication for phones (v1 §3.6) arrives with the phone UI, not before.

**WSL networking applies to every UDP device** (the RM4 Pro, the Bloc hub, OSC, Hue Entertainment, Art-Net). No inbound LAN UDP or multicast reaches WSL, and outbound UDP to LAN devices is still untested (v1 §1). Each adapter proves its path early.

---

## 7. Build order

**Who:**
- **screen-machine-2f** (owner, 13 Oct): this doc and the contracts (`docs/av/contracts/LIVE_V1.md`). It runs the build as a multi-agent workflow, at the owner's request, late on 13 Oct:
  - four builders in parallel (stream_service, room_service, the web Room page, the display page), each followed by an adversarial review-and-fix pass;
  - then one integration agent that proves art → live → art end to end in headless Chrome, runs the failure drills, and takes the colour and 4K measurements.
- **The RF and blinds spike:** a separate agent (`docs/av/HANDOVER_RF_BLINDS_SPIKE.md`).

**Day 1 (Wed 14):**
- **First:** freeze the page ↔ room_service messages (§3.2) and the session API (§4.5) as `schema/` files, each with one example. Everything else proceeds in parallel against them.
- **Renderer:**
  - the `stream_service` skeleton from the lab, and the plug-in registry;
  - the `image` source, `crossfade`, and `fractal_zoom` v0;
  - hold, go and exit, and the catalog;
  - viewed in a workstation Chrome first.
- **room_service:**
  - the skeleton: its own venv, `python -m room_service` on :3001, a state file, an event log, the token;
  - the screens subsystem with the handshake, the mask push and the snapshotter;
  - the coordinator with two modes, `gallery` and `live-test`.
- **Web app:** Room page v0, screens panel first.
- **Page:**
  - black background, error boundaries, and holding the last mask when a fetch fails;
  - the control client and mask push;
  - `?artonly`, freeze and ack;
  - the live layer with both cuts.
- **Checks:** headless versus kiosk rendering, and the colour tags (§3.3).

**Day 2 (Thu 15):**
- End to end on `devtest`, in a workstation Chrome at 3840×2160, driven from the web app: art → crossfade → fractal zoom → crossfade → art.
  - The cuts are invisible in a screen capture.
  - The mask follows a light change within about 0.5 s.
- Then one TV, in an owner window agreed for the kiosk. The page build is deployed and that kiosk's page reloaded.

**Day 3 (Fri 16):**
- Both TVs, at 4K60 if pacing has landed, otherwise 4K30.
- Failure drills: kill stream_service, reload the page, cut the stream.
- Modes from the web app, with screens only.
- The first device adapter from the spike, if it's ready.

**Then:** the devices, as each is configured (§6).

**The demo is done when:**
- **Live on a TV, from the web app:** no visible change at the cut, then a smooth 3 s crossfade into the zoom.
- **Off:** a smooth crossfade back into the current still with the current mask, and no visible change at the cut.
  - Both are filmed on a phone at 60 or 240 fps.
- At least 99 % of frames are presented over 30 min of live, with no stalls in steady state.
- Adding a second transition or scene takes one plug-in file plus a preset, and it appears in the web app with no UI change.
- In art mode the mask follows a light change on screen within about 0.5 s.
- Killing stream_service mid-live puts the still back within 2 s, and live returns by itself through the entry handshake.

---

## 8. Decisions for the owner

| # | Decision | Recommendation |
|---|---|---|
| D1 | Where the page's control socket and the live and mask logic live | **room_service, a new process**, not the Flask hub planned in RP P4. Iteration needs no Flask restarts, no Flask code changes this week, and RP's P1/P2/hub/shadow gates leave the critical path. RP can later fold the art-mode pushes (overlays, content) into the same socket, or move it. screen-machine-2f is building on this assumption unless the owner says otherwise. |
| D2 | Who builds what | **Settled (owner, 13 Oct):** screen-machine-2f's multi-agent build covers all four components, including the display page re-architecture in `src/pages/DisplayPage`. RP assigns `src/` to screen-machine-12, so 12 should review the page diff before any frontend build ships. The page's new behaviour is opt-in (`?ctl=`), so an early build can't change the production kiosks. |
| D4 | Ports | **AV port block, agreed with screen-machine-ca on 14 Oct: room_service 8700, show bus 8701 (reserved), stream_service 8702.**
- 8081 is *not* free. It belongs to exlib's Metro bundler (its portproxy and firewall rule come from Spectre's exlib-mobile toggle) and is bd's lab default.
- `docs/PORTS.md` is the register, kept by screen-machine-ca.
- Production instances run like Flask and ngrok: `scripts/ctl start|stop|restart|status|logs|health room|stream` (screen-machine-ca) starts each in a GNU screen session (`sm-room`, `sm-stream`), with arguments in `deploy/sm-{room,stream}.env`. There are no systemd units. Boot runs `scripts/ctl start all` (wsl-boot.sh and Spectre's logon task), which skips a held service (`ROOM_HOLD`). LAN rules for 8700 and 8702 are live, scoped to 192.168.1–4.0/24. Nothing starts at boot until screen-machine-2f says room_service is production-ready.
- During development, services run on WSL-only ports.<br>**room_service: 3001 can't be its production port.** It's exlib-server's default, and exlib's phone app hardcodes `192.168.1.205:3001` (found by screen-machine-10). Tonight's dev build uses 3001 inside WSL only, and only while exlib isn't running.<br>**Production port: the owner's choice.** 8700 was proposed in v1; screen-machine-10 has sent friendly-URL options. It needs a Windows portproxy rule plus a LAN-scoped firewall rule.<br>The owner confirms on the router that none of these is forwarded from the WAN. |
| D5 | Blinds when leaving a mode that lowered them | **Settled (owner, 13 Oct ~23:10):** live modes lower them; any non-live mode restores each blind to its position before the live episode (§2.5). |
| D6 | RF route for the ball and the blinds | **Blinds settled:** the Bloc iQ hub, locally, as a level subsystem (§2.4). **Ball:** the RM4 Pro plus a 433 MHz receiver, once the RM4 Pro is on the network. |

There is no D3. Moving the kiosk URL to the LAN address (RP P3, "front") is still worth doing, but it no longer blocks anything, because the socket address comes from runtime config (§3.2).

---

## 9. Changes to the DCL and RP

screen-machine-2f will send these to the docs' owners once the owner has settled D1 and D2.

**DCL** (bd):
- **§2.5:** the anchor and the exit target become headless-Chromium screenshots.
  - The renderer no longer reproduces the page's mask arithmetic, and the shared formula with golden tests is dropped.
  - The anchor ack becomes the freeze ack.
  - `diverge` and `converge` become `go` and `exit`, which carry PNGs.
- **§2.2 (hub row) and §2.3 (endpoint):** the page's control socket ends at room_service, `:3001/v1/screen/<id>`, not the sm-web hub's `/v2/screen/<id>` on :8765. The messages are those in §3.2: a subset of DCL v2, plus the freeze.
- **§2.6:** this week the mask is pushed by room_service, using the mask Flask already computes. The DCL's SensorService and filter come later.
- **§3 (migration order):** step 9 (the live layer) and the rest of the live path move ahead of steps 2–7. Step 0 (page hygiene) stays first.

**RP** (screen-machine-12):
- The P4 hub, the shadow week, P4b, P5 step 2 and P6B are no longer prerequisites for live.
- P2's release-directory deploys don't block room_service or stream_service. Both are new processes with their own venvs, started as `python -m …`, so `start.sh`'s `pkill` can't match them.
- Screen claims and arbitration (D12) move into room_service's coordinator, when they're needed.
- No Flask code changes this week.

---

## 10. Risks

| Risk | Mitigation |
|---|---|
| 4K60 pacing isn't solved this week | Demo at 4K30. The design doesn't change. |
| Headless and kiosk Chromium render the still differently | A day-1 diff; then pre-scale the stills, or run headless with GPU raster |
| A colour step at the cut | A day-1 check of the encoder's tags against the `<img>` |
| A new page build changes production screens | New paths stay inert unless room_service turns them on for a screen. Test on devtest first, then one TV in an owner window. |
| The mask socket drops | Fall back to the 3 s poll; hold the last mask; never black |
| UDP from WSL to LAN devices fails | Proven for the Bloc hub (unicast request/reply; multicast doesn't arrive, so the adapter polls). Each other adapter proves its own path early. A Windows-side helper is the owner's install. |
| RF devices can't be read back | A believed state marked `unconfirmed`; prefer separate codes, or a readback route (Bloc hub, metering plug) |
| Several sessions share the production checkout | §11 |

---

## 11. Working rules

1. **Git:** never run `git stash`, `checkout`, `reset`, `pull`, `clean` or `add -A`. Commit only your own paths, in pathspec form (`git commit -m … -- <paths>`), and only with the owner's OK. Other sessions' changes are already staged in the index.
2. **Processes:** no broad `pkill`; don't stop other sessions' processes. New services start as `python -m …`.
3. **Devices:** no device writes from tests or ad-hoc shells. Real output (RF, Hue, Maestro, Art-Net) goes only through room_service or show_service, or the spike's manual CLI, with the owner's OK and the owner present.
4. **Kiosk and TVs:** changes to the kiosk, its Chromes or the TVs need the owner's OK, in an agreed window.
5. **Secrets:** none in git. Tokens and keys live in 0600 files outside the repo, under `~/.config/screen-machine/`.
6. **Ownership:**

   | Owner | Paths |
   |---|---|
   | screen-machine-2f | `room_service/` (core, ingest, `live_api.py`), `config/room/` (except below), `src/pages/Room/`, `src/pages/DisplayPage/`, `docs/AV_PROGRAMME.md`, `docs/av/` |
   | screen-machine-04 | `stream_service/`, `config/live/` (since the cut-over on 14 Oct) |
   | screen-machine-aa | `room_service/roomstate/`, `room_service/adapters/` (blinds and devices), `room_service/room_api.py`, `room_service/room_config.py`, `config/room/rf/`, `experiments/rf-spike/` (frozen since 14 Oct) |
   | screen-machine-68 | `room_service/hue/`, `room_service/dmx/`, `config/room/patch.yaml`, and their wiring: the Art-Net receiver, the show bus on :8701, and the Hue adapter |
   | screen-machine-10 | `config/room/latency.json`, `tools/room/latency_cal/` (the light relay) |
   | screen-machine-ca | `scripts/ctl`, `deploy/` wiring, `docs/PORTS.md`, `docs/OPS_CONTROL.md`, the Windows and Spectre rules |
   | screen-machine-c8 | `show_service/`, `utils/llm/`, `config/room/vibes.yaml`, `config/room/fixtures.yaml`, `config/room/fixture-types/`, `config/llm.yaml`, `docs/SHOW_ENGINE.md` (the show engine: audio in, DSP, the room art director, Art-Net out; since 15 Oct) |
   | bd (read-only for everyone else) | `experiments/stream-latency/`; stream_service copies from it and never edits it |
