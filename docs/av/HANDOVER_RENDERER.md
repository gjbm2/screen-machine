# Handover: the real-time renderer (stream_service)

**For:** the new agent session taking over the renderer, and the owner.
**From:** screen-machine-2f, which owns `docs/AV_PROGRAMME.md`, the contracts in `docs/av/contracts/` and room_service.
**Date:** Tue 13 Oct 2026, late evening.

## 1. Your job

You own the GPU renderer and stream server: `stream_service/`, `config/live/` (scene and transition presets) and, later, `schema/stream*`.

**What it does:**
- renders generative scenes on the workstation's GPUs (CUDA via CuPy, inside WSL);
- composites the transitions between scenes;
- encodes 4K with NVENC (HEVC);
- streams it as fMP4 over a WebSocket to the display page on each TV, which plays it with MSE.

**Your interface to everything else is the contract `docs/av/contracts/LIVE_V1.md` §1:** the session API, events, the media WebSocket, the catalog and plug-in schemas. Within v1, changes are additive only. If you need a breaking change, agree it with screen-machine-2f first.
- room_service is the only caller of the session API.
- The display page consumes the media WebSocket (§1.4).

## 2. Read first

1. `docs/AV_PROGRAMME.md` (v2): §0–§1 (owner rulings), §3.3 (the art↔live cut: your part is holding the anchor, `go`, `exit` and `hold_pts`), §4 (the renderer).
2. `docs/av/contracts/LIVE_V1.md` §1 (your API), and §2.2 rules 4–8 (how the page plays your stream).
3. `docs/DISPLAY_CONTROL_LAYER_DESIGN_2026-10.md` §2.5 "Player" (playbackRate never above 1.0; edge jumps).
4. `experiments/stream-latency/README.md` (the lab; bd's, read-only). It has the measured facts on NVENC, the low-delay MSE mux, the H.264 SPS trap and WSL clock skew.
5. `docs/KIOSK_INFRASTRUCTURE_NOTES.md` (kiosk: sway, 4K60 HEVC proven on both TVs, powerline).
6. Project memory: `kiosk-video-hardware`, `live-mode-full-intensity`, `game-mode-rejected`, `latency-calibration`.

## 3. Where it stands (13 Oct, ~23:45)

stream_service was built tonight by a builder agent in screen-machine-2f's workflow. **That builder is still finishing** (see §6).

| Area | State |
|---|---|
| Plug-ins | Registry with JSON-Schema params and `x-ui` hints; live parameter changes ease in (`plugins/base.py`, `plugins/__init__.py`). |
| Sources | `image` (internal: anchors and exit targets), `solid`, and **`fractal_zoom`** (`plugins/sources/fractal_zoom.py`). |
| `fractal_zoom` | An **endless zoom in float32**, using the Poincaré function of a Julia map at a repelling fixed point. The pixel iteration runs in offset form, δ ← λδ + δ², so the image is exactly self-similar and the loop wraps with no visible seam. The frames at the start and end of a loop are identical (previews in screen-machine-2f's scratchpad `renderer/previews/`). Presets: nebula, dendrite, rabbit, seahorse and custom c, with palettes. |
| Transitions | `cut`, `crossfade` (eased, blended in sRGB like CSS opacity). |
| Pipeline | Layer tree with nestable transitions (`layers.py`); a render loop paced on deadlines (`renderer.py`); one final RGBA→NV12 kernel; ffmpeg NVENC through `encode/` (copied from the lab, plus a VUI colour-tag hook). |
| Sessions | starting → holding → entering → live → exiting → held, plus failed and ended. TTL and idempotency keys (`sessions.py`). |
| API | `/healthz`, `/dev`, `/api/catalog`, `/api/sessions` (CRUD, go, exit, renew, frames), `/api/state`, `/api/events` (WS), `/media/{sid}` (WS) (`server.py`, `media.py`, `events.py`). |
| Profiles | `hevc-2160p60`, `hevc-2160p30`, `h264-720p30`, `av1-720p30` (`profiles.py`). Default VUI tags: primaries/matrix BT.709, transfer **iec61966-2-1 (sRGB)**, range tv. A session can override them with `color_trc`. |
| Dev UI | `/dev` (`static/dev.html` plus `static/live-player.js`): the reference MSE player and a test console. |
| Presets | `config/live/scenes.yaml` (fractal-zoom, fractal-dendrite, fractal-rabbit, fractal-seahorse, black), `config/live/transitions.yaml` (crossfade-1s/3s/6s, cut). |
| Tests | `stream_service/tests/` (api, fmp4, layers, plugins, presets, and a GPU integration test). |
| Tools | `tools/fractal_preview.py` (renders still frames and seam checks). |

The builder's final report adds the performance numbers (4K60 on each GPU: late-frame %, render and write ms). screen-machine-2f will forward it to you.

## 4. How to run it

- **venv:** `~/.venvs/sm-stream` (CuPy 14.2, aiohttp, numpy, pyyaml, pillow, jsonschema, pytest).
- **Run:** `~/.venvs/sm-stream/bin/python -m stream_service --port <port>`
  - **Port:** dev on 8082 and up, inside WSL. **Production: 8702** (the AV block: room 8700, show bus 8701, stream 8702, agreed with screen-machine-ca on 14 Oct).
    - 8081 is exlib's Metro bundler and bd's lab default. Don't use it.
    - Production runs in the GNU screen session `sm-stream` through screen-machine-ca's `scripts/ctl` (same pattern as Flask and ngrok; `screen -r sm-stream` attaches; logs in `~/.local/state/screen-machine/logs/stream_service.log`). Your arguments go in `deploy/sm-stream.env` (STREAM_PORT, STREAM_ARGS, STREAM_HOLD, plus pass-through env vars), which you own.
  - **Token:** `~/.config/screen-machine/stream_service.token` (0600, created on first start). Never print it.
- **Test console:** from Windows Chrome open `http://localhost:<port>/dev`; WSL forwards localhost.
  - Windows Chrome can decode HEVC on the RTX 4060, so try `hevc-2160p60`.
  - Headless Chrome for Testing in WSL can't decode HEVC, so use `h264-720p30` there.
- **Tests:** `~/.venvs/sm-stream/bin/python -m pytest stream_service/tests`

**First thing to do:** the owner wants to try the fractal locally.
1. Bring up a dev instance on a spare port, e.g. 8092 (8082 and 8083 may be in use by screen-machine-2f's agents).
2. Check `/dev` plays.
3. Send the owner the link and a 3-line how-to: create a session, Go, change the parameters, Exit.

## 5. Open work, in priority order

| # | Item | Notes |
|---|---|---|
| 1 | **Even 4K60 pacing** | The lab dropped 6–7 % of frames live, because of the 12 MB raw pipe write per frame and a player that ran above 1.0×. Measure tonight's build.<br>• **Fixes:** overlapping writes, then GPU-resident encode. PyNvVideoCodec works today but adds 3 frames of delay; PyAV/FFmpeg 8 needs driver ≥570 (it's 566.14 now).<br>• **Target:** ≥99 % of frames presented over 30 min at 4K60. 4K30 is the fallback rung. |
| 2 | **The P4000** (south screen) | No lab run has used it. Pascal: NVENC can do HEVC 4K60 but not AV1, and CUDA is slower. Measure the fractal at 4K60 there. If it's short, trade quality (iterations, aa) or move south to 4K30. |
| 3 | **Colour parity at the cut** | Tonight the integration agent compares the sRGB and BT.709 transfer tags with H.264 in headless Chrome. **HEVC on the real TVs** still has to be checked on glass, in an owner window. Kiosk access goes through screen-machine-2f and the owner. |
| 4 | **Visual quality at 4K** | Fractal shimmer and aliasing (`aa=2` costs 4×). How the encoder copes with fine fractal detail at 30 Mbit/s HEVC. Palettes. |
| 5 | **More scenes and transitions** | The owner wants this extensible: one plug-in file plus a preset each. Transition ideas: a "screen melt", a noise dissolve, a zoom-through. Each scene is a plug-in with a schema; the web Room page builds its forms from the catalog automatically. |
| 6 | **Production service** | A startup unit; `--public-base ws://192.168.1.205:8702` (via `scripts/ctl` and `deploy/sm-stream.env`, in screen session `sm-stream`); two sessions at once (north on GPU 0, the 4060, which has only ~1.5 GB of VRAM free because Windows uses the rest; south on GPU 1, the P4000); the GeForce NVENC session cap; logs. |
| 7 | **Later** | Beat-phased visuals: render about 0.3 s ahead using `config/room/latency.json` (screen-machine-10's calibration). Parameter bindings to show-state once Art-Net/Maestro exist. An optional flash-limiter output filter, off by default (owner: safety mechanisms are optional). |

## 6. Coordination and the cut-over

- **Don't edit `stream_service/` or `config/live/` yet.** screen-machine-2f's build workflow still has agents working there: the renderer builder (finishing), then a reviewer that fixes what it finds, then an integration agent that fixes problems between components.
- **Until then,** you can read everything, run the service on a spare port, and do performance, pacing, P4000 and PyNvVideoCodec work in your own scratch directory.
- **When the workflow is done, screen-machine-2f will message you "cut-over"** and forward the final reports. From then on the directories are yours.
- **Peers:**

  | Session | Role |
  |---|---|
  | screen-machine-2f | room_service, the contracts, the AV plan, the display page and web Room page builds |
  | screen-machine-10 | latency calibration and the kiosk light relay |
  | screen-machine-aa | RF, blinds and the room state model |

  The lab in `experiments/stream-latency/` is bd's: read-only.

## 7. Rules for this shared production checkout

1. **Git:** never run `stash`, `checkout`, `reset`, `pull`, `clean` or `add -A`. Commit only with the owner's OK, only your own paths, in pathspec form.
2. **Never run `npm run build`:** `dist/` is served to the production TVs.
3. **Flask is production:** :5000 and :8765 are read-only. Never send a frame on :8765, and never restart Flask or run `start.sh`.
4. **Processes:** kill only processes you started, by PID. No `pkill -f`.
5. **GPUs are shared** with the Windows desktop and other sessions. Keep test sessions short and delete what you create.
6. **No access to the kiosk or TVs** without the owner's OK.
7. **Secrets:** none in git or in logs.

## 8. Owner preferences that bear on the renderer

- Picture quality matters more than latency.
- **Target:** 4K60, with 4K30 as the fallback.
- **TVs:** Game Mode is never used. The TVs run FilmmakerMode, and software never changes their picture settings.
- **Live mode is full intensity:** a lighting rig, not a simulated artwork. The page shows only your video, so **every effect, transition and piece of text is drawn by the renderer.**
- **Art↔live is a hard cut between matching frames.** The stream opens on the exact on-screen still and converges back to one, and the player never runs above 1.0×.
- Build in days, keep it config-driven and extensible, and keep safety mechanisms optional.
