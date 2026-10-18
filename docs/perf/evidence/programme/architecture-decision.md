# Renderer architecture decision: 2x4K60 programme

**For the owner (6 lines):**
1. Decision: the renderer stays in WSL and is split into one render-and-encode process per live screen. It starts when a screen goes live and exits 30 s after the screen stops.
2. First step: encode on the GPU. That removes the raw-video copy that limits two screens to 50-52 fps. It ships in today's process, with ffmpeg kept as a fallback.
3. Then the per-screen split, then far fewer GPU driver calls per frame. After that, each screen is limited by its GPU, not by Python.
4. Windows-native is not worth it now. Measured, it is no cheaper per call and 2-14x worse when two Python threads compete. We try it for south only if the trigger numbers in section 2 are met.
5. No C++, unless after all that Python pauses still lose more than 1 frame in 1,000.
6. The PC also needs room. Your calls on the CPU clock (it runs at 66% of rated speed), on desktop apps during shows and on agents' background jobs decide whether both screens hold 60 with everything else running.

Tags: [M] measured (source and window), [I] inferred, [U] unverified. Code lines were read on 18 Oct at stream_service 8cb3ec90 (16:49). I re-checked `room_service/config.py:105-131`, `stream_service/sessions.py:2859-2901`, `deploy/sm-stream.env:11`, `room_service/renderer_proxy.py:1-20` and `src/pages/DisplayPage/secureUrls.ts:15-24` at 19:3x.

---

## 1. Decision

**A now. B for south only if trigger FB1 or FB2 is met. C only as a single native stage if trigger FC1 is met.**

- **A is the winner on all three lenses** (scores 8 / 7 / 8, against B 6 / 4 / 4 and C 3 / 5 / 2.5).
  - It is built from parts measured in the environment it will run in. GPU encode works in WSL2 at 2.0-2.5 ms of CPU per frame [M spike].
  - Per-call CUDA cost is the same as native Windows (p50 ratio 0.79-1.15) [M].
  - CUDA graphs cut launch cost 10-20x on both platforms [M].
  - CUDA stream priority cuts queueing behind a full-GPU grid from 8.9 to 0.64 ms [M].
  - A needs no clock bridge, no second code copy and no new Windows piece or port.
- **B's only distinctive gain is host scheduling**: HIGH priority class outside WSL's 5.5-8 LP share [M]. It rests on things nobody has measured:
  - the hypervisor scheduler type [U];
  - native wake-up at HIGH [U].
  - Every native number taken so far is the same or worse.
  - B is therefore an exit ramp, not the plan. A's split is built so the ramp is cheap: the same Cut B seam, a swappable transport, and platform shims written during the split.
- **C contradicts "C++ only if it is the only solution"** in every area the fact findings examined: call overhead, encode, GPU scheduling and glass.
  - Its one unique property is an output cadence that is immune to the GIL. That is grafted into A in Python (the output clock on the encode thread, section 3.6).
  - Native code is kept in reserve for that one stage.
- **What A alone cannot promise.** "Only the GPU caps it" holds per screen after the programme. For the whole box it holds only if the box items happen (P6: agent cap, stray load, desktop apps, clock cap).
  - After the programme, AV needs 2.5-4.2 cores in WSL [I], against 5.5-8.0 LPs granted [M].
  - Headroom on today's desktop is -4.0 to +3.4 LPs [I].
  - With the clock fixed it is +3.2 to +11.6 LPs [I].

## 2. Preconditions, and the measurements that flip the decision

### Preconditions for A (each measured early; if one fails, the listed fallback applies)

| # | Precondition | Number | If it fails |
|---|---|---|---|
| PC0 | The stream_service owner role (formerly 0b/04) is re-identified | someone named | Nothing below ships. This blocks everything. |
| PC1 | PyNvVideoCodec soak on GPU 1, 4K60, P4, AQ 12, CBR, default 3-frame hold, no screen live | ≥10 min; 0 decode errors; encode-thread CPU p50 ≤3 ms; submit-to-packet p99 ≤70 ms | ctypes binding to `libnvidia-encode.so.1`, headers from nv-codec-headers 12.1 (MIT). Still A, still no C++. |
| PC2 | Graph prototype with a per-frame uniform arena, from CuPy 14.2: 50 kernels read params through a device pointer, filled by one captured H2D from a fixed pinned buffer | ≤0.3 ms CPU per frame (a fixed graph measured 0.16-0.22 ms [M]) | `pip install cuda-bindings` for `cuGraphExecKernelNodeSetParams`. If even that costs >1 ms per frame, it feeds FC1. |
| PC3 | After the split (step 4), on devtest and then a TV | render-thread GIL futex <3% of wall; cross-screen spike coincidence <30% (today 80%, chance about 23% [M]); control egress p99 ≤16.7 ms (T3) | Egress over 16.7 ms: pass the client fds to a per-screen egress child (SCM_RIGHTS). If still over: FB3. |
| PC4 | Box supply during a 2x4K60 show, with the P6 levers on | guest busy ≤ granted LPs - 1; hidden LP wait ≤10% (today 9-12% live, 31% at 18:51 [M]) | Evaluate FB1 and FB2. |

### Flip to B for south (a design decision taken from data, never a runtime gate)

- **FB1: all of these hold.**
  - After steps 1-4 plus P6, over a 10-minute 2x4K60 show:
    - worker host_ms p50 ≤9 ms;
    - late output slots ≥1%;
    - per-second correlation of late slots with Windows root LP use r ≥0.5 (from the Spectre host card);
    - guest busy ≤ granted LPs.
  - And every native bench passes (bounded, no screen live, existing `%LOCALAPPDATA%\sm-perf\venv`):
    - M6: an elevated Spectre read shows the **root** hypervisor scheduler;
    - M2: 2,000 high-resolution waits of 16.7 ms at HIGH, with the EcoQoS and timer opt-outs, under desktop load, lateness **p99 <2 ms**;
    - M4: a single-thread render-shaped loop with inline Encode() at HIGH on GPU 1 holds **60.0 fps with ≤1% late slots**, where the same loop in WSL misses ≥1%;
    - M3: native PyNvVideoCodec gives 60 fps at ≤3 ms CPU with 0 decode errors;
    - M1: native cold start **p90 ≤8 s** over 10 fresh starts;
    - M0: wslrelay at 12.5 MB/s costs ≤0.1 LP with RTT p99 ≤2 ms.
- **FB2, the alternative trigger.** An elevated read shows vmmemWSL's priority cannot be changed, and hidden LP wait during a live show stays **>15% with guest demand capped at ≤6 vCPUs**. That would contradict the diagnosis's r = 0.86 with guest demand.
- **FB3.** Root-side CPU per WSL CUDA call (vmwp/vmmem, elevated read) adds **≥0.5 LP at 2x4K60 after P5**, so A's budget is understated. Or the egress over 16.7 ms survives the fd handoff (PC3).
- **B is dropped for good if any of these holds:**
  - M6 shows the core scheduler;
  - M2 p99 ≥2 ms;
  - M1 p90 >10 s;
  - the CPU clock is fixed to ≥95% of nominal (≥3.5 GHz all-core) and FB1's correlation then falls below 0.5.
- **Why south first:**
  - The P4000 is not the desktop GPU, so a native fault or a TDR does not reset the owner's screen.
  - It has about 8 GB of VRAM free [M].
  - Only the Windows System process uses it [M].
  - North follows by placement config only if south proves out.

### Flip to C (one native stage only)

- **FC1.** After steps 1-7 at 2x4K60:
  - more than **0.1% of output slots** are missed with blocked_ms (GIL, GC or prepare) as the measured cause;
  - and content fps is **below 57** on the four top-airtime scenes while own CPU is ≤7 ms;
  - and this survives `sys.setswitchinterval(0.001)`, `gc.freeze()` and the output clock on the encode thread.
  - Then build only a native output clock, encode and egress (about 1-1.5 kLOC, a flat C ABI through ctypes). No native plan executor and no second render path.
- **FC2 (native encoder only).** The PC1 soak fails, the ctypes NVENC binding proves impractical, and the owner rejects the 54 ms 3-frame hold.

### Flips inside A (optimise the slow thing; no gates)

| Trigger | Number | Response |
|---|---|---|
| Worker bus drain on the render thread | >0.5 ms per frame p95 | Latest-value seqlock blocks in /dev/shm for state, plus SPSC rings for streams |
| Cold start, spawn to `session.ready`, with the warm hint | p95 >8 s, or >5% of entries hit room's fallback | A resident forkserver: cupy imported, no CUDA context, about 0 CPU. **Needs the owner's word.** |
| host_ms p50 at 60p on one screen after the diet | >9 ms | Re-scope P5 to the Python side of the hand-written scenes |
| The 4060 at 60p duty | SM clock p50 <1,500 MHz while live | Spectre clock-hold card (`-lgc` while any worker is live) |

## 3. Target architecture (spec)

### 3.1 Processes

```
scripts/ctl "stream" (screen sm-stream; existing Spectre card; :8702 unchanged)
└─ CONTROL  python -m stream_service       always on; no CUDA context; about 8% of a core idle [M]
   ├─ RENDER WORKER  gpu0 / north          on demand; one session; 30 s linger, then exit
   ├─ RENDER WORKER  gpu1 / south          same
   ├─ PREVIEW WORKERS (720p30, nice 5)     one per browser tab; prefer GPU 1 (preview.py:28-31); same linger
   ├─ kernel-prep helper (nice 19)         short-lived: GLSL->CUDA translation + NVRTC into the disk caches
   ├─ library/rescan child (nice 10)       short-lived per rescan or index build (diagnosis B2)
   └─ room-score child (CPU only)          one per room while a live-score scene is prepared
         └─ PM2S Analyser (existing: ~/.venvs/sm-score-ml, nice 19, last 4 CPUs)
```

- **Control.** Holds every Session, the directors, rooms, Up next, FX state, catalog, library, presets, ratings, the events WS, the `/media/{sid}` fan-out, the bus clients (one subscription each to :8701 and :8703), the WorkerPool, the NVML reader (1 Hz) and the HUD text.
  - Room leader/follower calls stay in-process (`sessions.py:548-750`).
  - `gpu_names` come from NVML.
  - `--warm` moves into worker start.
- **Worker.** Holds the Engine, plug-in instances by handle, SessionRuntime and Modulation, Sampler, its MusicFeed/ScopeFeed/SoundView copies, the guard, in-process NVENC and the degrade controller.
  - Spawned with `CUDA_DEVICE_ORDER=PCI_BUS_ID` and `Device(n)`, so it creates exactly one context. Never `CUDA_VISIBLE_DEVICES`.
  - Uses `prctl(PR_SET_PDEATHSIG)` (the pattern at `plugins/_lib/glsl/smoke.py:189`) and stays in the control's process group, so `scripts/ctl stop` takes everything down.
  - Holds no TCP listener, so there is **no new port and no new card**. The stream card lists live workers from `/api/state` (ops owner).
- **Worker thread rule (hard): one hot Python thread.**
  - **render** (hot). At each frame start it drains the cmd and bus socket without blocking. It renders into the next free NV12 surface on stream priority -5 and records an event.
  - **encode**. Blocks inside Encode() and EndEncode() with the GIL released [M]. Packetises and sends on the media socket.
  - **prepare/closer**. Bursty and CPU-light. It only imports the plug-in module (p50 0.5 ms [M]), constructs, allocates, loads cached cubins and primes, on stream priority 0. `gc.freeze()` runs after each prepare.
  - Translation and NVRTC never run in the worker; the kernel-prep helper does them. No asyncio, aiohttp or registry discovery in the worker.
  - Evidence for the rule: one CPU-bound Python neighbour turned a 1.2 ms 50-launch frame into 24.7 ms in WSL [M], and a paced 60 fps encode into 36 fps [M].
- **Platform seams, written during step 3 (about 1 day; they keep the path to B cheap):**
  - a `clockmap` module (the identity in WSL);
  - a guarded `fcntl` import (`encode/encoder.py:14`, `plugins/sources/gallery.py:52`);
  - one `pace_clock()` function (today `renderer.py:77-79`);
  - one `cpu_ms()` source;
  - a transport interface (Unix socket now, TCP later).

### 3.2 Flows and who owns each

| Flow | From → to | Transport | Code owner (role, current name) |
|---|---|---|---|
| Session API, events WS (46 routes) | room_service ↔ control :8702 | HTTP/WS, **unchanged** | stream_service owner (not re-identified) |
| Commands and prepares (each returns a handle) | control → worker | cmd socket, length-prefixed JSON | stream owner |
| Bus inputs: show.state 40 Hz, music.features 50 Hz, clock 10 Hz, levels ≤60 Hz | room :8701 and show :8703 → control (one subscription each) → each worker | WS, then raw frames on the cmd socket | stream owner; sources: room owner (vacant), show_service owner screen-machine-e9 |
| Runtime 10 Hz, lead 1 Hz, HUD text ≤2 Hz, delivery and NVML 1 Hz | control → worker | cmd socket | stream owner |
| Score bars | room-score child → member workers | socket | live-score owner screen-machine-b0 |
| Encoded media `[0x02][key][fid][moof+mdat]`, init once | worker → control → clients | media socket (separate, so an IDR never delays events), then WS | stream owner |
| TV media path | control :8702 → Windows portproxy → kiosk | **unchanged** | ops owner (portproxy via Spectre) |
| HTTPS media path | Caddy `/rs/*` → :8700 `/renderer/*` (renderer_proxy) → :8702 | **unchanged** | room owner |
| Page stats, plus `glass` and `hidden` flags, arrival margin, ping/pong | page → control | media WS text (additive; old pages are safe) | display-page owner (vacant) |
| Stats snapshot and `degrade{}` (1-2 Hz), sound tap 10 Hz, `plugin_error` by handle | worker → control → `/api/sessions`, `session.degrade`, HUD, Room card | evt socket | stream owner; Room card words: room/page owner |
| FX budget | worker → control FX director (`fx_director.py:47`) | evt socket | FX interplay owner screen-machine-ec |
| Anchor and exit stills | control → worker | /dev/shm file of decoded pixels, once per create or exit | stream owner |
| Host attribution, show mode, GPU clock hold | Spectre ↔ Windows | Spectre API | ops owner (vacant), with owner OK |

- **Rates.** Commands are 0.5-10 KB, a few per second. Bus traffic is about 150-180 messages/s, 0.3-0.6 MB/s. Events are about 1/s [M: 532 per 10 min]. Media is 3.75-6.25 MB/s per screen [I].
- **Time.** One CLOCK_MONOTONIC for every `t_ms`, `anchor_ms`, `at_mono` and `photon_at` [M split-map], so A needs no clock bridge.
- **Sockets** live in a per-service runtime directory (for example `/dev/shm/sm-stream/`) with plain file permissions. No auth.

### 3.3 Lifecycle

- **Worker states:** spawning → idle → bound (one session) → lingering 30 s → exit (frees the context and all VRAM). No warm sleep.
- **Spawn triggers:**
  1. **Warm hint (new, additive).** `POST /api/workers/warm {screen}`, which room sends without waiting at the **start** of its entry handshake. The spawn then overlaps the freeze ack (≤5 s) and the snapshot (≤3 s). An unused warm worker exits after the same 30 s linger.
  2. `POST /api/sessions` takes an idle worker on that GPU or spawns one at the top of the handler, in parallel with the scene pick and the anchor decode (p50 0.43 s [M]).
- **Create returns 201 at `starting`.** It no longer awaits `s.start()` (today `sessions.py:2899-2901`). `session.ready` follows once the first IDR is out.
- **Room re-budget, not stacked timeouts (room owner):**
  - `create_session_s` 5 → 2 s and `session_ready_s` 5 → 8 s (`room_service/config.py:109,126`).
  - The sum stays 10 s, so the documented entry worst case stays **29 s, under the page's 30 s freeze safety** (`config.py:127-131`) [M].
  - B's 20 s and C's 15 s proposals break that budget.
- **Cold-start trims (step 5).** No aiohttp (−0.65 s [M]) and no full `Registry().discover()` (−1.1 to −3.0 s [M]) in the worker; lazy plug-in imports; a disk cache for GLSL translation (p50 24 ms, max 130 ms per program [M]); the shared `~/.cupy/kernel_cache` (3,807 cubins [M]), topped up by the kernel-prep helper after each rescan.
  - Target: spawn to ready typically ≤4 s. The tail is about 12 s today [I], and the warm hint hides up to 8 s of it.
- **First frame.** A bounded warm-up burst runs during the anchor hold, so the 4060 is at boost by cutover. Its ramp from 240 MHz takes 0.3-0.5 s [M/I].
- **Reuse.** Any profile can reuse a lingering worker (RenderContext is per session, `gpu.py:236-250`). Previews re-cue one session per tab (`preview.py:1-15`), so library browsing stays inside the linger. A plug-in or code change retires idle workers, so the next session gets fresh code.
- **Failure.** Worker EOF becomes `session.error`, then `failed`, which is room's existing fallback (`sessions.py:2304-2313`). If no worker can spawn, the in-process engine is used and the fallback is reported. That is a degrade, never a refusal.
- **Hang.** The guard's 3 s hang stop stays in the worker, unchanged (`guard.py:38`). It ends the session exactly as today. If the render thread is still inside a driver call when the worker would start lingering, the control ends that process instead of lingering. There is no new timer or threshold. The in-kernel caps stay (`glsl_compat.cuh:24-32`), and WDDM's 2 s TDR still fires first for a runaway kernel [I].

### 3.4 Resource claiming "to a fair limit"

**Structural (built in, not a gate):**
- One hot thread per worker, so a screen uses at most about 1.2 cores.
- Render and encode stream at priority -5; prepare, prime and upload streams at 0 (`gpu.py:248`, `renderer.py:242,256,270,283`).
- A VRAM pool sized once at worker start. North is sized conservatively: about 0.9 GB is free on the 4060 while live [M].
- No per-frame pinned host memory. GPU encode drops the 149 MB pinned ring, which sidesteps the dxg `create_existing_sysmem` failures seen under guest fragmentation [M dmesg].
- Each 4K60 screen encodes on its own GPU: one P4000 tops out at about 95 fps total for two streams [M].
- Previews run at nice 5.

**A/B behind env flags:** `sys.setswitchinterval(0.001)` in workers. Each lost GIL handoff cost about one 5 ms interval [M].

**Box levers through Spectre (each lowers other work, never AV; owner OK marked):**
- `scripts/bg` for agents' batch jobs: taskset 16-19, nice 19, `chrt -i 0`. **Owner OK and a CLAUDE.md rule.**
- A show-mode card: Brave, Slack, Code, Notion and Carbonite go to BelowNormal plus EcoQoS while any screen is live; nothing is closed. **Owner OK.**
- A GPU clock-hold card: `nvidia-smi -lgc` while any worker is live, `-rgc` after the last linger, A/B first. **Owner OK.**
- A read-only host-attribution card: `Process(*)` 2x2 s, vmmemWSL priority, `bcdedit hypervisorschedulertype`, Energy Meter (RAPL) if present.
- The clock cap is a BIOS or cooling fix. **Owner.**

### 3.5 Encode path

1. The render thread converts RGBA to NV12 into one of three NV12 surfaces, allocated once, and records an event.
2. The encode thread calls `Encode()` with the CuPy context and the render stream. No host sync is needed: sync and no-sync runs give md5-identical bitstreams [M].
3. Packets come back under PyNvVideoCodec's default 3-frame hold (54 ms at 60p [M]). They are converted from Annex-B to length-prefixed packets, then framed by the existing `fmp4.py` into moof+mdat, then sent on the media socket.
   - EOS after every frame (15 ms p50 [M]) is used only after a ≥30-minute soak with a decode check.
4. **Settings.**
   - 60p: HEVC P4, spatial AQ 12, CBR, GOP 60, no B-frames. P5 cannot hold 60 on the P4000 [M].
   - The hvcC record is built from `GetSequenceParams()` VPS/SPS/PPS. Today it comes from ffmpeg's FLV (`bitstream.py:548-553`).
   - Colour: the `colr` nclx box in the init (`fmp4.py:39`). The source-context VUI route is used if the devtest check shows Chrome ignoring `colr` at the hard cut.
   - A forced IDR at `hold_pts`.
   - `Reconfigure()` for 60↔30 fps and 50/40/30 Mbit/s, 1.5-2.1 ms with no IDR [M].
5. **Fallback chain:** in-process NVENC, then today's ffmpeg profile (reported as an "encoder fallback" degrade), then ctypes NVENC if PC1 fails.
6. **Real tests (shared plumbing):**
   - a byte-identity golden test of the new muxer and hvcC against today's `fmp4.py` and the ffmpeg-derived output on the same packets;
   - a bitstream-md5 test for every NVENC option, because unknown keys are silently ignored [M].

### 3.6 Output clock and degradation controller (inside the worker)

- **Output clock (grafted from C, in Python).** The encode thread owns the cadence.
  - At each slot deadline it takes the newest completed surface (`cudaEventQuery`, 1.2 µs [M]). If the render is not done, it re-submits the previous surface.
  - The render thread renders for the next slot it can make. A late render lowers the content rate, never the output cadence.
  - This replaces today's repeat rule and resync (`renderer.py:463-474`).
  - Frame id stays on the session profile's tick grid (60 Hz for hevc-2160p60). A half-rate step advances it by 2 with trun duration 3000, in the same init and the same SourceBuffer.
- **Controller.** One per session, in the worker, where the frame-level signals are. It decides at 1 Hz with arithmetic only and applies changes at a frame boundary.
  - **Signals:** `cpu_ms` and `blocked_ms`; GPU span from events; encoder submit-to-packet time; NVML enc% and clocks (from the control, 1 Hz); **stage age** (content fps, reported so that 60p carrying repeats never reads as 60); delivery (the glass client's minimum `buffered_ms` and arrival margin, after the glass-identity fix).
  - **Order:** effects (FX budget to the control's FX director) → scene rate (`layers.py:126-160`, now set by the controller, an even 1/2 rather than a ragged one) → render scale (a prepared swap) → encoder (P4→P3, AQ off; whether these need a new encoder is [U]) → fps 60→30. The binding stage picks the sub-steps.
  - **Delivery axis:** bitrate 50 → 40 → 30 Mbit/s at an IDR.
  - **Floor:** 30 fps, scene rate 1/8, no effects. It never stops.
  - **Hysteresis:** down at most one step per second after 1 s of sustained load; a gross-overrun fast track (>1.2 periods on 25% of slots in 0.25 s); up after 10 s (30 s for fps) only if the projected cost is under 0.7 of the period; the wait doubles after a failed step up. The 1 s sustain covers the 4060's clock ramp.
  - **Frozen** across the art-to-live entry and the exit hold.
  - **Never touched:** resolution, the init segment and the mask.
  - **Reported** as `degrade{}` in `/api/sessions`, the `session.degrade` event, a HUD line, and plain words on the Room card ("30 fps: computer busy").
  - **Rollout:** shadow mode for one show (it must say "host" on today's load), then act at 30p, then add the fps and bitrate steps after P3.
- **Fan-out fixes:** drop-to-key counted in time (0.5 s), not frames (`media.py:25-32`). The glass client is picked by room's `glass:true` on the driven page's attach, echoed in the page's stats, replacing the largest-`presented` pick (`diag.py:111-125`, `hud.py:271-278`).

### 3.7 What stays exactly as it is

- **The room_service contract:**
  - all 46 routes and their JSON;
  - the events WS and its snapshot-on-connect;
  - the session states and fields room acts on: `starting`, `ready`, `holding`, `ended`, `failed`, plus `transition`, `held` and `error`;
  - additions only: `degrade{}`, `session.degrade` and the warm hint;
  - `201 at starting` uses an existing contract state.
- **Media URLs:**
  - `attach.url = {public_base}/media/{sid}?k=` on :8702 (`sessions.py:2859-2862`);
  - TVs over http through the portproxy;
  - HTTPS pages through `/rs/renderer` + path + query, i.e. Caddy → :8700 `/renderer/*` → :8702 (`secureUrls.ts:16-24`, `renderer_proxy.py`).
- **Media WS bytes:**
  - a config text message, `[0x01][ftyp+moov]`, `[0x02][key u8][fid u32 BE][moof+mdat]`, then `eos`;
  - close codes 4403/4404;
  - the client stats fields (new fields are additive);
  - one init per session.
- **Displays:**
  - kiosk and TV URLs `/display/<screen>`, unchanged;
  - the light mask only in Chrome on art; live at full intensity;
  - hard cuts between matching frames;
  - never TV Game Mode;
  - the diagnostics HUD burned into the stream.
- **Guards:** the in-kernel loop caps, the clock budget and the single 3 s hang stop.
- **Operations:**
  - the `scripts/ctl` stream card and the sm-stream screen session;
  - `deploy/sm-stream.env` flags (the stream owner edits them);
  - no auth anywhere.
- **Hygiene, batched with the page change.** `--public-base ws://192.168.1.205:8702` and `--app-url http://192.168.1.205:8700/renderer` (`sm-stream.env:11`) are hard-coded IPs.
  - Fix: the page takes the host from `location.hostname` and keeps the path and query. The control then drops `--public-base` (`attach_for` already falls back to the request host).
  - Page URLs do not change.

## 4. Migration (production, flags in `sm-stream.env`, one batched restart per row, only when `GET :8702/api/sessions` is `[]` with devtest included)

| # | Ships | Done when (measured) | Days (stream owner unless noted) |
|---|---|---|---|
| 0 | Bounded measurements, no service started: PC1 soak (GPU 1); PC2 graph-arena prototype; a CUDA-call and Python-time count per frame on note-fluid and piano-waterfall at 4K; native M0-M4; Spectre elevated reads M6, `Process(*)`, vmmemWSL | Numbers in hand; FB benches recorded | 2.5, plus ops owner 1 |
| 1 | T1 honest numbers and the cpu/blocked split; B1 summary cache; B2 rescan and index in a child; B3 sleep to deadline; B7 probe frame; stream priorities -5/0; `setswitchinterval` flag | Fields present; probe cost <0.25%; no repeat run ≥10 slots | 2-2.5 |
| 2 | **P3** in today's process: PyNvVideoCodec, hvcC, Annex-B, `colr`, IDR at `hold_pts`, ffmpeg fallback, golden and md5 tests, output clock on the encode thread | Encode CPU ≤3 ms per frame; slot to packet ≤60 ms; about −2.2 cores at 30p [I] | 4-5 |
| 3 | Seam in-process: EngineProxy, handle table, loopback transport; platform seams | Same frame trace as step 2 | 3-4 |
| 4 | **Out-of-process workers** (`STREAM_WORKERS=proc`): pool, linger, PDEATHSIG, bus drain, media socket, stats snapshot, kernel-prep helper, preview pool at nice 5. Devtest first, then the TVs | PC3 met | 4-6 |
| 5 | 201 at `starting`; warm hint; cold-start trims. Room: re-budget plus warm-hint call plus `glass:true` (one Flask restart through Spectre, sessions told first, app port verified after) | Spawn to ready p95 ≤8 s over 20 real starts; entry budget still 29 s | 1.5, plus room owner 1 |
| 6 | Room-score child fed from bus notes (needs b0 to accept it instead of the leader's SoundView, `score/room.py:117-139`) | live-score frames/s ≥ W3 | 1-2 |
| 7 | **P5 by airtime:** piano-waterfall (63 TV starts), live-art (45), note-fluid (43; its 20 Jacobi launches become static graph nodes), live-score (37), then the GLSL runtime with the uniform arena (about 17% of starts [M tally]); B4 guard round-robin; B5 batched uploads; one SoundView per room | host_ms p50 ≤9 ms at 60p on one screen | 6-9 |
| 8 | Degrade controller: shadow, then act at 30p. Page: glass echo, arrival margin, ping/pong, location-derived media host (one vite deploy) | Names the binding stage correctly; never stops | 3-4, plus page owner 1 |
| 9 | `hevc-2160p60`, one screen at a time. Chrome 136 mixed-duration check (devtest, then one TV in a quiet window); N95 2x4K60 check (DCL test 3b) | TV stalls/min; N95 video-engine % | 1-2, spread out |
| S | Spectre cards: stream card lists workers; host attribution; show mode; GPU clock hold | Before/after LP split | ops owner 2-3 |

- **Effort:** about 28-40 stream-owner days, plus about 6-8 for the room, page and ops owners. That is about 6-8 weeks of calendar time.
- **Restarts:** about 7 stream restarts and 1 Flask restart.
- **What steps 1-2 alone deliver:** most of the 30p gain, and Wall 2 removed.
- **Decision points:** FB1/FB2 are judged after steps 4 and S; FC1 after step 7.
- **If B is triggered (south only, +11-13 days):**
  - a Windows render-host (logon task, pythonw, no window, ABOVE_NORMAL, holds no CUDA) with its own Spectre card and ps1;
  - `docs/PORTS.md` 8704 (WSL loopback through wslrelay, not on the LAN) and the CLAUDE.md cards line;
  - workers at HIGH in a Job object (kill-on-close, about 6 GB memory, all workers together ≤30% of the box);
  - EcoQoS and timer-ignore opt-outs, `timeBeginPeriod(1)`, high-resolution waitable timers;
  - an inline-Encode single-thread loop;
  - a clockmap fit;
  - a Windows CuPy cache warm;
  - a code hash handshake, with automatic fallback to WSL workers (reported).

## 5. Top risks, and the measurement that answers each

1. **Box supply** stays the cap: Windows root took 14.5 LPs at 18:51, 9.5 of them unattributed, with the clock at 66% [M]. Measured by the step-0 attribution and PC4.
2. **4060 down-clocking** (960 MHz at 60p duty, 2.6x kernel time [M synthetic]) and its VRAM. Measured with T5 `%globaltimer` probes [M works], then the clock-hold A/B.
3. **Control egress jitter**, the biggest glass lever: 24 vs about 1 glitch/min in the model [I]. Measured by PC3 on the day step 4 ships.
4. **Cut B handles:** about 25 seam leaks; prepared instances are the hard one. Step 3 ships them in-process first.
5. **Cold-start tail against room's windows.** Measured by step 5's 20 starts, with the forkserver flip if needed.
6. **Two contexts on one GPU** (north plus devtest on the 4060; NVENC capacity at 4K60 next to the desktop is [U]). Watch the frame trace and VRAM the first time it happens; the ladder absorbs it.
7. **Ownership:** the stream, room, page and ops roles are unconfirmed or vacant (PC0).

## 6. Owner asks (one line each)

1. Cap agents' background jobs to vCPUs 16-19 at nice 19 (a CLAUDE.md rule).
2. Read CPU sensors (HWiNFO, sensors only) to find the 66% clock cap, and clean the P4000 cooler.
3. Allow the Spectre show-mode card (desktop apps go to low priority while a screen is live; nothing closes).
4. Allow a 2-minute GPU clock-hold A/B through Spectre.
5. 60p bitrate: 40 Mbit/s (fits the signalled HEVC level 5.1, 45% of the powerline) or 50 Mbit/s (56%). My pick: 40.
6. 60p player buffer: 200 ms instead of 120 (about 0 glitches/min instead of 1-4 in the model; the lead already compensates for the extra delay [I]).
7. Brave's live viewer tabs on PC22 during shows: keep them or close them.

Evidence base: everything is in `/tmp/claude-1000/-home-gjbm2-dev-screen-machine/49a9e605-47c6-4ba4-98e3-fd28d61d6fec/scratchpad/perf/`.
- The diagnosis: `/tmp/claude-1000/-home-gjbm2-dev-screen-machine/49a9e605-47c6-4ba4-98e3-fd28d61d6fec/scratchpad/perf/final/REPORT.final.md`.
- The fact findings: `/tmp/claude-1000/-home-gjbm2-dev-screen-machine/49a9e605-47c6-4ba4-98e3-fd28d61d6fec/scratchpad/perf/programme/` (`split-map/`, `wsl-vs-windows-cuda/runs/analysis.txt`, `gpu-encode-spike/summary_v2.tsv`, `box-budget/budget_table.tsv`, `gpu-sched-tcc/measured_summary.json`, `glass-60p/sim60_table.txt`, `tv_scene_tally.txt`).

I wrote no files.