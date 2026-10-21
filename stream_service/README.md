# stream_service

The real-time renderer, NVENC encoder and media server for live mode (contracts: `docs/av/contracts/LIVE_V1.md`
§1; design: `docs/AV_PROGRAMME.md` §4). Graduated from `experiments/stream-latency/` (bd's lab, read-only):
its pacing on `CLOCK_MONOTONIC_RAW`, the ffmpeg/NVENC pipe with FLV framing, the low-delay fMP4 muxer and the
WebSocket fan-out with drop-to-keyframe are reused here.

```
 asyncio loop (aiohttp)                      render thread (per session)             writer thread       ffmpeg (NVENC)
 REST /api/*, events WS  ──commands──▶  apply at frame boundary                       wait copy event     h264/hevc/av1_nvenc
 media WS fan-out  ◀──fragments──┐      layer tree -> RGBA (CuPy) -> NV12 kernel ─▶  write 12 MB NV12 ─▶ -delay 0|2, FLV out
                                 └──────────── fMP4 moof+mdat ◀── reader thread ◀───────────────────────────┘
```

* One session per screen, pinned to a GPU (`gpu` is used for both the CUDA render and NVENC `-gpu`).
  `CUDA_DEVICE_ORDER=PCI_BUS_ID` is set at start-up, so 0 = RTX 4060 and 1 = Quadro P4000 everywhere.
* Frames are CuPy `uint8` (H, W, 4) sRGB-encoded. One final kernel converts RGBA -> NV12 (BT.709 limited
  range, chroma averaged per 2x2 block). A ring of 6 pinned host slots and a writer thread overlap the pipe
  write with rendering.
* Timeline: frame `n` is at `n / fps` stream seconds (= the video's `mediaTime`). A frame slot that starts more
  than one period late counts in `late_frames`. If rendering is the cause (GPU render > 85 % of a period) the
  previous frame is repeated in that slot (`repeats`), so the timeline never shifts; if the pipe/encoder is
  behind, the right content is rendered late and the stream catches up. More than 0.25 s behind re-bases the
  schedule (`resyncs`): the stream then runs that much later than real time, which the player's buffer absorbs.
* **Encoder per screen** (`config/live/stream_switches.json`, 4K60 plan change 2): `nvenc` = GPU-resident
  (`encode/nvenc.py`: PyNvVideoCodec encodes the slot's NV12 surface on the render stream, no host copy, no
  ffmpeg; HEVC only), `ffmpeg` = the pipe above. Read when a session is created (no restart to flip a screen):
  `{"default": {"encoder": "ffmpeg"}, "screens": {"south-screen": {"encoder": "nvenc"}}}`. The GPU encoder forces
  IDRs on the GOP grid as before, on the frame at `hold_pts` and when a player joins. If it cannot be created the
  session starts on ffmpeg; if it fails mid-session the session switches to ffmpeg and keeps playing (players get
  the new init, then its IDR; a freeze of about 1.2 s while ffmpeg's NVENC starts). Either way `encoder_fallback`
  says why in `/api/sessions` and on the details panel. `/api/sessions` also reports `encoder` and, in `stats`,
  `encode_cpu_ms` (the encode /
  writer thread's own CPU per slot), `slot_to_packet_ms` and `forced_idrs`. Bounded P4000 run, 18 Oct (real Engine,
  synthetic scene): 2160p30 29.8 fps, encode thread 2.2 ms CPU per frame (p95 3.2), slot to packet p50 105 ms;
  2160p60 59.5 fps, 2.2 ms, 55 ms; 0 decode errors; whole process 4.2-4.4 ms CPU per frame.
* **60 fps and the degradation controller** (4K60 plan change 5). `"fps": 30 | 60` per screen in the switches file
  (default 30): a session's profile becomes its sibling at that rate (`hevc-2160p30` asked -> `hevc-2160p60` live;
  `/api/sessions` keeps `profile` as asked and adds `profile_live`). `hevc-2160p60` is 40M CBR with a 1 s buffer at
  HEVC Main tier level 5.1 (`hev1.1.2.L153.90`; a 2 s buffer would be High tier). `degrade.py`, one per session where
  its Engine runs (`"degrade": "on" | "off"`), decides once a second from the frame records (render-thread host time,
  real GPU span, encode time, repeats, late slots) and steps down at most once a second (never fewer effects: owner,
  21 Oct): the scene every 2nd slot (a floor under the scene's own `rate_div`), then 60 -> 30 fps (GPU
  encoder only: NVENC Reconfigure, frame ids stay on the 60 Hz grid, fMP4 samples of 2 ticks, the in-band SPS pinned
  to the init's). Up after 10 s (fps 30 s) when the load without the step projects under 0.7 of the period; a failed
  step up doubles its wait. Frozen across entry and exit. Reported as `degrade{level, step, why, since, text, ...}` in
  `/api/sessions`, a `session.degrade` event and one line on the details panel ("60 -> 30 fps: computer busy").

## Run

```bash
cd ~/dev/screen-machine
~/.venvs/sm-stream/bin/python -m stream_service --port 8082            # dev
# production (LIVE_V1 §0): --port 8702 --public-base ws://192.168.1.205:8702 (systemd unit, screen-machine-ca)
#   --host 0.0.0.0  --config-dir config/live
#   --token-file ~/.config/screen-machine/stream_service.token  --max-sessions 4  --log-level INFO
```

* The token file is created on first start (64 hex chars, mode 0600, `O_EXCL`). It is never logged; the access
  log is off because query strings carry `?token=` / `?k=`.
* Dev page: `http://127.0.0.1:8082/dev` (paste the token once; it is kept in `localStorage`). It builds forms
  from the catalog schemas, creates sessions (solid colour or PNG anchor), goes live, edits live params,
  changes scene, exits (colour or PNG target), deletes, and plays the stream with the reference player
  (`static/live-player.js`), optionally simulating the art cuts (anchor/target shown over the video, hidden on
  the first presented frame, shown again on the first frame at `hold_pts`). `/dev?attach=<ws url>` just plays.
* Simulated music (dev): run `python -m stream_service.tools.show_synth --port 8711 --song pop --bpm 124` and
  start the service with `--show-bus ws://127.0.0.1:8711/v1/show`. The dev page's "Music (simulated Maestro)"
  panel sets the tempo (a step or a ramp; the beat phase never jumps), switches song style (at the next bar),
  stops the beats (a breakdown) and jumps to the next section, and shows the synth's tempo next to the beat
  follower's, timing how long the follower takes to re-lock. It goes through `/dev/synth`, which proxies
  show-synth's `GET/POST /v1/control` (see the docstring in `tools/show_synth.py`).
* Venv: `~/.venvs/sm-stream` (cupy-cuda12x, aiohttp, numpy, pyyaml, pillow, jsonschema, PyNvVideoCodec==2.2.3
  for the GPU encoder; tests add pytest, pytest-asyncio, playwright). Needs the distro `ffmpeg` (6.1, NVENC API 12)
  on PATH (the ffmpeg encoder, and the GPU encoder's fallback).

## API (LIVE_V1 §1) and what is additive

All of §1.1-1.4 as specified. Additive extras (nothing in the contract changed):

| Where | Extra |
|---|---|
| `GET /healthz` | `{"ok": true, "version"}`, no auth |
| catalog `profiles.*` | `color` (`primaries`, `trc`, `matrix`, `range`) and `target_buffer_ms` |
| `POST /api/sessions` | optional `"color_trc": "bt709"` (default per profile: `iec61966-2-1`) for the colour check |
| `POST .../exit` | also accepted from `held` (a new target re-runs the out transition) |
| session summary | `gpu, codec, w, h, fps, color_trc, scene_params, transitions, hold_pts, error, end_reason, expires_in_s, stats{rendered, resyncs, frames_encoded, write_ms, slip_ms, host_ms, encode_ms, bitrate_mbps}, client_stats` |
| events | `session.transition` carries `"superseded": true` when a transition was discarded before it finished (every `started` gets one `done`); on connect the events socket first sends a `session.state` (and `session.held`) per live session, so a restarted room_service can adopt them; `session.ended` may carry `detail` |
| media config message | `session` |
| `GET /api/sessions/{id}/frames?since=N` | per-frame timing records (debug; used by `tools/perf.py`) |
| `GET/POST /dev/synth` | dev only, bearer token: proxies show-synth's control API (`http://H:P/v1/control` for `--show-bus ws://H:P/v1/show`); 404 without `--show-bus`, 502 when nothing answers |

Errors are `{"error": code, "detail"}` (aiohttp's own 404/405/413 under `/api/` too): `unauthorized` 401,
`bad_request`/`bad_json`/`bad_spec`/`unknown_param`/`bad_param`/`unknown_preset`/`unknown_source`/
`unknown_transition`/`unknown_profile`/`unsupported_profile`/`bad_still`/`bad_image` 400, `not_found` 404,
`method_not_allowed` 405, `bad_state` 409, `too_large` 413 (body over 64 MB), `too_many_sessions` 503,
`start_failed` 500.

Session flow: `starting` -> (first IDR muxed: `session.ready`) `holding` -> `go` -> `entering` -> (in done) `live`
-> `exit` -> `exiting` -> (out done: `session.held{hold_pts}`) `held` -> `DELETE`/TTL -> `ended` (+ `eos` on the
media sockets). `hold_pts` is the pts of the first frame rendered with the out transition at progress 1.
A `PATCH` with `scene`, `params` or `transition` is allowed in `holding`, `entering` and `live`; one carrying only
direction/palette/auto settings (`direction`, `palette`, `palette_transition_s`, `auto`, `beat_follow`,
`direction_ease_s`) is accepted in any state except `ended`/`failed`. In `holding` the scene is not on screen yet,
so a `scene` or `params` change rebuilds the pending scene under the session lock (it starts exactly as asked on
`go`; overlapping PATCHes apply one on another); in `entering`/`live`, `params` ease the visible scene (~0.8 s)
and `scene` runs an in-stream transition (default: the session's `in`). A PATCH that waited for the lock while
`go` ran is applied as an `entering` PATCH.

The events socket subscribes before its handshake completes and first sends the current state of every
active session, so nothing emitted while room_service (re)connects is lost.

## Profiles

| Profile | Codec | Size @ fps | Bitrate | GOP |
|---|---|---|---|---|
| `hevc-2160p60` | HEVC (hev1) | 3840x2160 @ 60 | 30M | 1 s |
| `hevc-2160p30` | HEVC | 3840x2160 @ 30 | 20M | 1 s |
| `h264-720p30` | H.264 (SPS rewritten to `max_num_reorder_frames=0`) | 1280x720 @ 30 | 4M | 1 s |
| `av1-720p30` | AV1 (RTX 4060 only; 400 on the P4000) | 1280x720 @ 30 | 3M | 1 s |

NVENC preset p1, `-tune ull -zerolatency 1`, CBR, no B-frames; `-delay 0` except `hevc-2160p60` (`-delay 2`,
see Performance). Colour tags (VUI and an MP4
`colr`/nclx box): primaries bt709, matrix bt709, range tv, transfer `iec61966-2-1` by default (per profile;
per session with `color_trc`). Defined in `profiles.py`.

## Plug-ins

`plugins/sources/*.py` and `plugins/transitions/*.py` are discovered by module: a `Source` or `Transition`
subclass with a `name` registers itself. Adding a scene = one file + (optionally) a preset in
`config/live/scenes.yaml`; it then appears in the catalog and the web forms with no UI change.

* `params` is a dict of JSON-Schema properties, each with a `default` and optional `x-ui`
  (`label, step, unit, widget: slider|number|select|checkbox|color`). Unknown params are rejected (400), wrong
  types are 400, out-of-range numbers are clamped. `"x-ease": false` stops a numeric param from easing.
* `Source.setup(ctx, params)` runs in a worker thread (compile kernels, upload); `render(t_local, dt, out)`
  draws into a CuPy (H, W, 4) buffer on the session's render stream; `update(params)` receives live edits,
  which `step_params(dt)` eases with a 0.8 s time constant; `close()` releases buffers (deferred by the engine
  until the GPU has finished with them). `dt` is the stream time since the source last rendered.
* `Transition.render(progress_eased, a, b, out)`; `duration_s` and `easing` (CSS curves) are common params.
  The layer tree (`layers.py`) nests transitions (a new one takes the running one as its `a`) and collapses
  finished ones to their `b`.

v1 plug-ins: `image` (internal: anchors and targets; PNGs of any size are letterboxed on black),
`solid`, `fractal_zoom`, `cut`, `crossfade` (sRGB-space blend, like CSS opacity).

### Effects, live discovery, the library (14 Oct)

* **Effects** (`plugins/effects/*.py`): a `plugins.base.Effect` or `plugins._lib.effect_base.Effect` subclass
  (GLSL imports: `GlslEffect`), `render(t_local, dt, src, out)`, class attr `stage` (render order: warp, feedback,
  glitch, stylise, colour, light, projection, motion, overlay). `amount` 0 is identity; the engine fades effects in
  and out through it, and binds `pulse` to the beat by default. Effects run scene-attached (a scene spec's
  `effects: [...]`, max 4; they transition with the scene) or on the session's **master FX bus** after the
  transitions: `GET / PUT /api/sessions/{id}/fx` `{stack, fade_s, master, if_rev}` (whole stack, diffed by slot
  `id`), event `session.fx`. The bus opens when the art->live transition is done and is sealed when the exit
  completes (held frame stays exact). `direction.fx {density, rate, stages, moods}` and
  `auto.fx {enabled, pool, max}` drive the FX auto-director (off by default). Code: `effects.py`, `fx_state.py`,
  `fx_director.py`; design: docs/EFFECTS_LAYER_DESIGN.md (fx-layer branch).
* **Failure isolation**: every plug-in is set up and primed (one throwaway render) on a worker thread, so a
  shader that does not compile fails the prepare (500 `prepare_failed`, the session keeps running; an auto pick
  that fails is blacklisted for the session and the director picks again). A render exception substitutes:
  scene -> black + recovery cue, transition -> crossfade, effect -> passthrough / dropped from the bus;
  `session.plugin_error`, counted in `/api/diag`.
* **Live discovery**: the server rescans the three plug-in folders every 20 s (`Registry.rescan`): it imports
  only new or changed modules, unregisters removed ones, lists modules that fail to import (or whose classes fail
  registration) in the catalog's `broken`, and emits `catalog.changed`. `<id>.presets.yaml` next to a plug-in
  gives its presets; a plug-in without any gets an automatic preset (weight 0.5). Only the transitions config/live
  presets use are warmed at a GPU's first session; everything else compiles when a cue prepares it.
* **/dev library**: `GET /dev/library` (one item per plug-in, presets, tags as facets, origin core / imported by
  the `glt_` / `isf_` / `st_` prefix), `GET /dev/thumb/<name>.png` (the fxtest sheet's run tile, as JPEG),
  `GET /dev/preview/<name>.mp4`, `GET /dev/plugins` (status), `POST /dev/rescan`. The console at `/dev`
  (static/dev.html, a shell for the ES modules in static/console/: Browse, On screen, Under the hood) shows the
  Library live through `/dev/preview`, rates and tags through `/dev/rate`, and drives room screens only through
  room's per-screen API (cue, look, fx, live, inputs); see static/console/app.js for the module map.

### fractal_zoom

An endless zoom in float32 into a Julia set `f(z) = z^2 + c` at a repelling fixed point `z*`
(multiplier `lam = 2 z*`), rendered through the Poincare function `Psi` (`Psi(lam z) = f(Psi(z))`), so the
picture is exactly self-similar under scaling by `lam` with a +1 shift in escape count. The view
`zeta = view_radius * lam^-u * e^(i theta) * w` loops over `u = frac(phase)`, and the colour index
`(mu - N - u) / palette_period` cancels the shift, so the wrap is seamless. `Psi` near 0 comes from its Taylor
series (12 terms, coefficients from `Psi(lam z) = Psi(z)^2 + c`), which needs 4-8x fewer warm-up iterations than
the first-order start; then `delta <- lam delta + delta^2` around `z*`. Interior pixels stop early by periodicity
detection. `natural_spin` keeps a fraction of the intrinsic spiral rotation (`arg lam` per period: 88 deg/s
for dendrite, ~290 deg/s for rabbit at 0.25 e-folds/s), and `rotation` adds deg/s. Presets `c`: `nebula`
(default), `dendrite`, `rabbit`, `seahorse` (heavy), or `custom` (`c_re`, `c_im`); palettes ember/ice/neon/mono
(cosine palettes). See the module docstring for the maths.

`python -m stream_service.tools.fractal_preview --gpu 1 --params '{"preset": "rabbit"}' --seam --bench 20`
renders stills, checks the seam (u = 0 vs u -> 1) and times the kernel.

## Tests

```bash
cd ~/dev/screen-machine
~/.venvs/sm-stream/bin/python -m pytest stream_service/tests -q                       # unit (no GPU): 191 tests
SM_GPU_TESTS=1 ~/.venvs/sm-stream/bin/python -m pytest stream_service/tests -q -s     # + integration on :8082
SM_GPU_TESTS=1 SM_BROWSER_TESTS=1 SM_TEST_OUT=/tmp/sm-out ~/.venvs/sm-stream/bin/python -m pytest stream_service/tests -q -s
```

* Sessions (`test_sessions.py`, a fake engine, no GPU): the state machine (409s, go/exit/re-exit from held,
  superseded outs), events order, PATCH in holding rebuilds the pending scene (also when racing go or another
  PATCH), failure -> eos, TTL reaper, concurrent idempotent creates and the session limit, no event lost during
  the events handshake, JSON 404/405/413, client stats sanitised, late commands disposed of after stop,
  concurrent token creation.
  `test_bodies.py`: the body parser matches `json.loads` and keeps GIL stalls short.
* Unit: registry and schemas (validated as JSON Schema), validation/clamping, easing, presets and spec forms,
  hot reload, layer tree (nesting, collapse, event pts, superseded), fMP4 boxes and media framing, API auth
  (every route, events token, media key 4403/4404), request validation, token file.
* Integration (`SM_GPU_TESTS=1`, GPU `SM_TEST_GPU`, default 1): runs the service on :8082, creates an
  h264-720p30 session, captures config/init/frames from the media socket, drives go/PATCH/exit/delete and
  checks the events (ready, in/out started/done with pts, held with `hold_pts`, ended, eos), contiguous frames
  and an IDR every second, the VUI colour tags (ffprobe), and decodes PNGs with ffmpeg: anchor hold,
  mid-crossfade, live zoom, the frame before `hold_pts`, the `hold_pts` frame (exactly the target) and the
  held target.
* Browser (`SM_BROWSER_TESTS=1`): Chrome for Testing 145 headless plays `/dev?attach=` at 720p30 H.264: presented
  frames vs expected over 10 s, dropped frames, `playbackRate` 1.0, no edge jumps or stalls, and the exit cut
  fires on the first frame at/after `hold_pts`.

## Performance

`python -m stream_service.tools.perf --gpu 0 --profile hevc-2160p60 --seconds 60 [--dump raw.json]` (against a
running service) measures one live session from its per-frame records, with a draining media client.

hevc-2160p60, `fractal-zoom` (nebula) live, 60 s window, 13-14 Oct 2026 (GPU 1 was also carrying another
agent's 720p test encode during part of this):

| | Quadro P4000 (GPU 1) | RTX 4060 (GPU 0, shared with the desktop) |
|---|---|---|
| Late frame slots (> 1 period) | 48 / 3600 = 1.3 % (one ~0.8 s burst, max slip 84 ms) | 125 / 3600 = 3.5 % (two bursts, max slip 117 ms) |
| Repeats (duplicate frames shown) | 0 | 0 |
| GPU render (tree + RGBA->NV12) p50 / p95 | 5.2 / 5.7 ms | 3.2 / 4.5 ms |
| Device->host NV12 copy p50 | 1.1 ms | 2.1 ms |
| NV12 pipe write (12.4 MB) p50 / p95 / max | 8.2 / 12.0 / 50 ms | 9.1 / 16.0 / 53 ms |
| Write done -> packet out p50 / p95 | 22.1 / 26.1 ms (incl. 2 frames NVENC delay) | 22.4 / 27.1 ms |
| Encoder throughput / bitrate | 59.95 fps / 30.0 Mbit/s | 59.97 fps / 30.0 Mbit/s |
| Media client | 60.0 fps, 0 gaps, 0 drops | 60.0 fps, 0 gaps, 0 drops |

hevc-2160p30 on the P4000 (10 s): 0 late, 29.9 fps, 20 Mbit/s. fractal_zoom kernel alone at 4K: 4060
0.8-1.8 ms (seahorse 4.2), P4000 2.2-5.6 ms (seahorse 12.0; nebula with aa=2 15.9).

**The ffmpeg pipe is the bottleneck, not rendering.** Each 4K frame is 12.4 MB of NV12 through a 1 MiB pipe:
the bare pipe sustains ~118 fps (1.47 GB/s, 8.2 ms per frame), ffmpeg reading rawvideo ~108 fps, and with
`hevc_nvenc -delay 0` (the lab's setting: ffmpeg waits for every packet before reading the next frame) only
~66 fps on the P4000 (~74 on the 4060). `-delay 2` raises the P4000 to ~94 fps for +2 frames (33 ms) of latency,
so `hevc-2160p60` uses `nvenc_delay=2` (per profile in `profiles.py`); the 4060 stays at 70-80 fps whatever the
delay. At 60 fps the pipe path runs at 55-65 % of its capacity, and stretches of slow writes (system load,
WDDM scheduling on the shared GPU) leave frames late in bursts. A 6-slot pinned ring absorbs ~80 ms
hiccups. Late frames are rendered late with the right content and pts, not repeated (a repeat costs the same
write), and the worst delivery delay seen (~150 ms) is inside the player's 300 ms buffer. Repeats are kept
for render-bound lateness (GPU render > 85 % of a frame). The fix is GPU-resident encode (no 12 MB host copy
per frame), the later step in AV_PROGRAMME §4.4.

Before these changes (3 slots, `-delay 0`, repeat on any lateness) the same runs gave 1.8 % (P4000) and 5.0 %
(4060) repeated frames.

## Findings worth knowing

* **Encoder settling at `hold_pts` (exit cut).** The rendered frame at `hold_pts` is exactly the target; the
  *encoded* one is softer than a still that has been on screen for a while, because NVENC's low-delay CBR
  refines a just-settled picture only slowly over P-frames. Measured against the PNG in Y'CbCr
  (review, 14 Oct), with `crossfade-1s` (ease-in-out) and a textured, grainy target (a hard case):

  | | entry anchor (held ~1.5 s) | `hold_pts` | +1, +2 frames | ~1 s later (after the next IDR) |
  |---|---|---|---|---|
  | hevc-2160p30, P4000: Y mean / p99 / max | 1.6 / 5 / 10 | 3.2 / 10 / 27 | 3.1 / 10 / 21 | 1.8 / 6 / 11 |
  | h264-720p30: Y mean / p99 / max | 1.1 / 4 / 7 | 3.2 / 11 / 22 | 3.2 / 11 / 22 | 2.2 / 7 / 14 |

  With a flat colour target the error is small (mean 1.3, then 0.05), so `hold_pts + 2/fps` only helps flat
  targets. For textured stills the cut frame is visibly softer only if the eye catches a ~3-level grain
  difference. If it shows on the TVs, room_service can cut on the first keyframe at or after `hold_pts`
  (IDRs are at frame ids that are multiples of `gop_s * fps`, i.e. on whole stream seconds, so the exit takes
  up to 1 s longer: within the programme's "transition plus about a second"). The real fix is a forced IDR
  at `hold_pts`, which needs the GPU-resident encoder (the ffmpeg pipe cannot force one per frame): the `nvenc`
  encoder now sends one there.
* **Big request bodies.** A 4K PNG is ~10-30 MB of base64 in the JSON. Plain `json.loads` + `b64decode` held
  the GIL for ~125 ms on a 21 MB body (other threads stalled up to 81 ms), which made every live session's
  render thread late (~7 frames at 60 fps) at the start of each exit. `bodies.parse_body` (worker thread;
  base64 cut out of the text and decoded in 1 MiB chunks) holds it for at most ~9 ms. PNG decode, letterbox
  and upload already release the GIL (worst stall 7 ms).
* **Late-frame accounting.** NVENC initialisation blocks the first pipe writes for 0.3-0.8 s; the catch-up
  after it (while the anchor is held) is not counted in `late_frames` (counting starts at the first on-time
  slot after the first encoded frame, or 2 s after it at the latest).
* **Colour in headless Chrome.** The tags are in the bitstream itself (H.264 SPS VUI: bt709 / sRGB transfer /
  bt709 matrix / tv range; checked with ffprobe on the raw Annex B stream) and in the `colr` box, and ffmpeg
  decodes the stream within +-2. Headless Chrome for Testing 145 (software video path) shows greys exactly but
  blues low: (30, 90, 200) as (29, 90, 193), pure blue B 243. That matches the BT.709 matrix with the Cb->B
  coefficient clamped to 2.0 instead of 2.1124, as libyuv's 8-bit fixed-point BT.709 constants do (it does not
  fit their int8 range). It is **not** a BT.601 decode (that would give (36, 89, 195)). So the stream is
  right and this is the CPU conversion path. The kiosk (VA-API, GPU compositing) needs its own check
  (AV_PROGRAMME §3.3); `color_trc` made no difference in headless Chrome.
* **Start-up.** NVENC initialisation blocks the first pipe writes for ~0.3-0.8 s; `ready` arrives 1.1-2.5 s
  after `POST /api/sessions` (the first session on a GPU also creates its CUDA context).
* `codec_string` for HEVC is `hev1.…` (the lab's sample entry, which allows in-band parameter sets; it plays on
  the kiosk). LIVE_V1's example shows `hvc1.…`; clients should use the string they are sent.
