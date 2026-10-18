# Live diagnostics on the TV: build spec

**Status:** approved by the owner on 17 Oct 2026 ("yes, let's get on with it"). Coordinator: screen-machine-e1.
**For:** the sessions building it: 04 (stream_service), aa (room adapters), 2f (room_service core, Room page, display page), c8 (show bus, v2).

## Decision

An optional, per-screen panel of diagnostics during live modes. It is **burned into that screen's own stream by
stream_service**, not drawn by the display page.

- D-B (DISPLAY_CONTROL_LAYER_DESIGN_2026-10.md:10) says live mode is one `<video>` with nothing over it, and names "HUD".
- LIVE_V1 rule 11 bars page messages during live.
- The :8765 overlay path is dropped while live.
- A DOM layer over 4K video on the N95 has never been measured, and the record so far is that the N95 can't afford 4K layer blending.

So the kiosk's render path stays exactly as it is.

**Trade-offs:**
- Everyone watching that screen's stream sees the panel, phones included.
- The panel is absent during the art↔live cut frames, which must match the still.
- The per-viewer copy is a text view in the Room page's Technical details.

The **devtest** screen does not turn it on by itself. It is switched by hand only.

## Contracts (build against these in parallel)

### stream_service (04)

- **Create:** the session create body gains `hud: bool` (default `false`). Add it to the known-field set.
- **PATCH:**
  - `PATCH /api/sessions/{sid}` accepts `{"hud": bool}` in any non-terminal state.
  - It is handled in its own branch, not `_apply_look`.
  - It is never spread to the session's peers.
- **Summary:** `summary.hud = {"on": bool, "failed": null, "rate_hz": number}` (`failed` stays null: the panel never turns itself off).
- **`GET /api/sessions/{sid}/hud`** returns `{"on", "failed", "at", "lines": [str, ...], "warn": [line index, ...]}`.
  - It works whether the panel is on or off; when off, lines are built on request, at most once a second.
  - Reached through room as `/renderer/api/sessions/{sid}/hud`.
- **No suppression:** switched on, it shows (owner, 18 Oct). Preview and fxtest sessions never send `hud`.
- **Media stats:** the page's 1 Hz report may carry `role` (`kiosk` / `viewer` / `lab`), so the glass line uses the TV's own player.

### room target (aa)

- **DiagAdapter** in `room_service/adapters/room.py`, cloned from PageAdapter and registered alongside it:
  - subsystem `diag`, label "Live details", category `setting`;
  - one member per screen, so the target is `diag.<screen>`;
  - value schema `boolean`, default `false`, x-ui label **"Show details on the TV"**.
- **Persistence:** saved to `state_dir/diag.json` and restored on start.
- **Apply:** it calls `controllers[sid].set_hud(value)`, which 2f provides.
- **Modes:** no mode sets it unless a mode lists `diag.<screen>` explicitly; it is not under `page.*`.
- **Writes:** through the existing `PUT /api/v1/room/targets`.

### room_service screens (2f)

- **`ScreenController`:**
  - gains `hud` (wanted) and `set_hud(v)`;
  - with a live session, `set_hud` PATCHes it `{"hud": v}`;
  - both create bodies (`_entry`, `_entry_headless`) carry `hud`.
- **Status:** `status.screens.<id>.hud = {"wanted": bool, "applied": bool, "failed": null | "<words>"}`, where `failed` comes from the session summary.

### Room page (2f)

The Room page part lives in the screen card, under More → Technical details:
- **Switch:** at the top, "Show details on the TV", from the target's x-ui.
  - It writes `PUT /api/v1/room/targets` and is never disabled.
  - The line under it reads "On the TV now" or "Shows when it goes live".
- **Chip:** while on, a small read-only "Details on TV" chip on the card header, so it isn't left on for a party.
- **Text view:** under the switch, a monospace copy of the same lines from `/renderer/api/sessions/{sid}/hud`.
  - It polls once a second, only while Technical details is open.
  - It has a stale marker, and works whether the TV panel is on or off: this is the per-viewer view.
- **Places it does not go:** the menu (no controls there), the guest look controls, and the TV URLs.
- **Display page:** `MseLivePlayer` adds `role` to its 1 Hz media stats. No other change.

### Show bus (c8, v2, after v1 lands)

- A `room.why` topic at most once a second, about 300 B: the Auto line and its last reason, the listening `why`, the mic state, and the last look-change reason in words.
- stream_service adds it to its want list and shows it on one panel line.

## What the panel shows (about 14 lines of up to 64 characters, text refreshed twice a second)

| Group | Lines | Source |
|---|---|---|
| Stream | W×H @ fps, codec/profile, GPU; render ms p50/p95 (2 s), late %, Mbit/s; guard frame ms vs budget, trips; the HUD's own ms | `Engine.window_stats(2.0)`, `guard_stats` |
| Glass | the TV's presented fps (change in `presented` over time), dropped/s, buffer ms, stalls | media stats (role = kiosk) |
| Scene | now: preset p X/N, why picked, time on; next 2 with transition and ETA; scene's reduced rate | `current_spec`, `director.last`, `upnext_view`; `SourceLayer.rate_div` (expose) |
| Effects | bus stack (effect, level, tempo lock); next effect; FX director's next move and why | fx summary, upnext; `FxDirector.status()` (expose) |
| Tempo / drive | bpm and source, clock conf, bar.beat; top 3 `param ← signal = value` | `MusicFeed.clock`, `Modulation.last` |
| Tempo / drive (drawn every frame) | a beat lamp at photon time and the frame number, as kernel scalars | |
| Director | renderer direction (energy, pace, moods); room vibe and section; heard genre/key; last 3 room events with reasons | `direction_effective`, music.context/insight (keep the latest), an 8-entry room.event ring |

Not on the panel: GPU utilisation and VRAM (console only), the kiosk's heap, the song title.

## Drawing it (04)

1. **Model.** Built on the existing ticker at 2 Hz, only for sessions with the panel on, from in-memory fields only.
   - Never call `Session.inputs()`, `summary()` or nvidia-smi from it.
   - It writes a 64×16 uint8 code grid plus per-line colour codes (about 1 KB), swapped in by reference.
   - The build times itself; over 5 ms drops it to 1 Hz.
2. **Atlas.** Pillow bakes DejaVuSansMono into an alpha atlas once per cell size, in an executor at warm. Cell height is H/60 (36 px at 2160).
3. **Buffers.** The device atlas, 2 device grids and 2 pinned 2 KB blocks (from the live_art free list) are allocated in `Engine._start`. Toggling allocates nothing.
4. **Draw.**
   - Where: in `_render_frame`, **after `rgba_to_nv12`** (renderer.py ~673) and before `slot.ev_rendered.record`.
   - Upload: when a new grid is pending and the spare block's event is done, `memcpyAsync` it.
   - Gate: launch one `hud_nv12` kernel over a fixed rectangle only if `not bus.sealed and smoothstep(bus.level) * toggle_fade > 0`.
   - The kernel blends luma, pulls chroma to neutral (amber only for warning lines), and takes the beat lamp and frame number as scalars.
   - `self.frame` is never touched, so the frozen-exit still stays clean.
5. **Envelope.** `bus.level` is 0 until art→live 'in' is done, fades with the exit, and is 0 once sealed. Anchor, cut and hold frames therefore carry no panel. Use `bus.level`, never `gain_now`, because a mode's master amount can zero `gain_now`.
6. **Layout.**
   - Top-left, inset 64 px, aligned to 32 px (HEVC CTU).
   - Opaque near-black panel with greyscale text, about 7% of a 4K frame, clamped for portrait.
   - Text changes at most twice a second, so the encoder skips the block between updates.

## Cost and degradation (owner, 18 Oct: no gates; graceful degradation)

The panel exists to understand performance, so nothing turns it off but its switch.

- **Cost is reported, never acted on.** The panel times its own kernel with a CUDA event pair read via
  `Event.done` (never synchronize) and shows the figure as `details N ms`.
- **Errors skip a frame.** An exception drawing it skips that frame's panel and is logged (at most once a minute);
  the next frame tries again.  The stream never depends on it.
- **Slow builds degrade.** A text build over 5 ms updates the panel at 1 Hz instead of 2.
- **The cut frames stay clean.** It's drawn only while the master bus is open (`bus.level`, not `gain_now`), so the
  anchor, cut and hold frames match the art (D-C), and calibration frames show it only if it's switched on.
- **The kiosk is untouched:** no DOM layer, no new page message, no new socket.

## Order and checks

- **04** builds the stream_service part, then does one batched `scripts/ctl restart stream` with no screen live (see batch-stream-restarts).
  - Crash-check on devtest by PATCHing `hud` on and off: render_ms, late_pct, Mbit/s and guard ms, panel on vs off.
  - Check the panel is legible.
  - Check the first live frame and the hold frame carry no panel.
- **aa and 2f** build the room side. 2f tells the sessions, restarts Flask via Spectre, and verifies :5000 and :8700.
- **2f** builds the Room page part, then does the frontend deploy per CLAUDE.md.
- **c8** adds `room.why` after v1.
- Reply to **screen-machine-e1** when your part is live; e1 runs the end-to-end crash-check on devtest.
