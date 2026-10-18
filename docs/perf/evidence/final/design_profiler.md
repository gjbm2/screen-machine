# Design 1: the frame recorder, a tool to show where each frame's time goes

Legend: **[M]** measured (by the lenses or verifiers, or by me today, with the source given). **[I]** inferred from code or arithmetic. **[U]** unverified: it must be prototyped on devtest before anything relies on it.

The tool has three parts:
- **The recorder** keeps a timeline for every frame slot, on one clock, from the pacing wake to the glass.
- **The box probe** is a child process with its own interpreter. It samples the host, the GPUs, Windows and the other processes, so none of that work runs under stream_service's GIL.
- **The attribution rule** turns those records into answers: "this frame was late because of X", "this second was bound by Y", and "this scene really costs Z on this GPU".

Nothing in it stops, trips or suppresses anything. When its own cost goes over budget, it measures less often.

---

## 1. Questions the tool must answer, and which output answers each

| Owner's question | Output that answers it | Today |
|---|---|---|
| Is it the CPU or the GPU? | A per-second **duty table** for each serial stage (render thread, writer, encoder chain, NVENC, SM, loop). It also gives the share of frames where the GPU was still busy with the previous frame when the next one started (`gpu_pending ≥ 1`). | `render_ms` is a host-inflated span labelled as GPU time. diag.py:91-92 says "render over budget" while GPU1 is 4% busy [M]. |
| Why does a frame stall or jitter? | A **dossier** for each late frame, which splits the lateness into: own CPU by stage, blocked (mostly GIL), run queue, ring wait (pipe or encoder), GPU, and wake. It also lists the suspects from the same 100 ms window. | `host_ms` is one number. Repeats record 0.0 (renderer.py:141), so `window_stats` shows a host p50 of 0.0 in 15 of 45 2 s windows [M] (instrumentation/gaps_baseline.json). |
| What protects real-time cycles? | Continuous evidence for the structural fixes in H1, H3 and H4: GIL-blocked ms per frame, worker and loop bursts as markers, Windows LP idle and clock. | Visible only to lenses run by hand, with py-spy and Get-Counter. |
| Where do we degrade, and by how much? | An honest per-stage cost input (own CPU, true GPU, write, encode) and a live **cost table** per scene and effect. The future single controller reads these. | rate_div reacts to the guard spans, which ran 12-67x the calibrated GPU cost [M] (H2). |
| What did the TV actually see? | The glass's frame-id-tagged gaps, joined to server frame n. Each stall is classed as upstream, network, player seek, or player/decode. | Cumulative page counters only, overwritten (media.py:178). The HUD glass fps alternates between 0 and 42-60 on a 30 fps stream [M] (hud_alias.json). |

---

## 2. Ground rules

1. **One clock.** All new stamps use `pace_clock()`, which is CLOCK_MONOTONIC_RAW (renderer.py:77-79). The render, writer, reader and guard threads already use it (renderer.py:341, :423).
   - The loop side currently uses `time.monotonic()` (media.py:72, :134, :140; sessions.py:2926). renderer.py:78 records that this clock once ran about 2% fast on this host. New loop-side stamps (send times, loop lag) therefore use `pace_clock()` too.
   - Stamps are stored as int32 µs offsets from the slot's `t_wake`. Only `t_wake` is stored as a float64 in seconds.
2. **One join key: slot n.** The slot index n is also the encoded `frame_id` and the media timestamp index [I].
   - Each slot submits exactly one frame, rendered or repeated.
   - ffmpeg runs with `-r fps -fps_mode passthrough` (encoder.py:54, :76).
   - `frame_id` is derived from the FLV timestamp (encoder.py:146), and `_on_frame` already looks records up by `frame_id` (renderer.py:845).
   - Fragments carry `frame_id * frame_dur` (renderer.py:853), so the glass recovers n as `round(mediaTime * fps)`.
   - Network jitter is computed from deltas local to each side: arrival gap minus send gap. No clock sync between devices is needed.
3. **Under the GIL, add no work unless it is in-process by necessity.** H1 measured that waiting for the GIL takes about 40-60% of host_ms, and that summary GETs, the 20 s rescan and the analysts' own polls trigger spikes [M]. So:
   - Host and GPU sampling, file I/O and process scans run in the probe child.
   - Summaries are computed once a second and served as cached bytes.
   - Per-frame export is binary.
4. **New CUDA calls go only where they replace old ones.** Event.record costs p50 11-17 µs, but p99 6.2-12.7 ms under one CPU-bound Python thread [M] (gil-profiler/cudacall_bench.txt). Every new call is another GIL hand-off. The design adds one event per frame and saves more by trimming the guard (Stage E).
5. **Honest labels.**
   - `render_ms` becomes `gpu_span_ms` (it includes host gaps).
   - "blocked" means wall time minus own CPU minus run queue. Calling it GIL is **[I]**: FL1 showed the only non-ring futex was the GIL, but only on piano-waterfall and live-score at 14:58, never on note_fluid.
   - "True GPU time" comes only from probe frames or calibration.
6. **Degrade, never refuse.** When over budget the tool samples less often and says so. Any switch it has (for example ffmpeg per-frame stats) is a per-session toggle like `hud`, never automatic.

---

## 3. What exists, and what each piece becomes

| Instrument (file:line) | Defect [M unless marked] | Becomes |
|---|---|---|
| `_Rec` plus `records` dict plus 6 `Ring`s (renderer.py:136-150, :185-206) | No stage split. host 0.0 on repeats. No div/held, queue depths or thread CPU. Kept for 4096 slots. | A `FrameTrace` numpy ring of 8192 slots. `frame_records()` keeps its keys and adds new ones. |
| `Engine.summary` (renderer.py:917-930, stats.py:9-31) | 18.3 ms of GIL per call (ring_cost.json). It is the top GIL-held line on the main thread. | Percentiles from numpy over the ring, computed once a second by the ticker and cached. |
| `window_stats` (renderer.py:938-965) | Counts repeats' 0.0 in host_ms (:949-958): north p50 9.5 ms against 22.5 ms rendered-only. | Reads the trace, rendered rows only. |
| GPU guard (guard.py:163-193, :292-296) | Its spans include host and GIL time. stats() gives only totals and the worst call. | Keeps every hang stop. Adds per-call host wall and CPU. Feeds the cost table (§6). |
| HUD (hud.py:269, :272-293) | Reads `ema_ms`/`frame_ms`, but the key is `frame_gpu_ms` (guard.py:294), so the guard field is always blank. Glass fps is computed between 2 Hz builds. | Three lines (§7), with glass rates timed by when each stats message arrived. |
| `/api/diag` (diag.py:20-125, server.py:704-725) | The verdict is based on guest loadavg and render p95. pynvml is not installed in sm-stream, so every poll forks nvidia-smi. | Keeps its shape and gains a `perf` block. The verdict comes from the duty table. |
| Rate log (layers.py:156-160) | Carries no session or screen. Says "on the GPU". | Gives session, host/GPU labels and a marker. |
| Media stats (media.py:176-180) | Last value only, no time, no frame id, no client identity. | A ring of 8 per client with receive time, plus frame-id gap lists (§4.5). |
| `tools/perf.py` | Synthetic single session, and reads a token file. | Unchanged. The recorder covers production. |
| calibrate.py (`cal_spin`, :140-160) | Gives true GPU cost offline only (844 plug-ins). Never joined to live data. | Joined in the cost table, and the source of the probe technique (§4.3). |

Two facts I measured today with nice 19, under 1 s each:
- **PSI is not available**: `/proc/pressure/cpu` does not exist on this kernel. Guest steal always reads 0 (H4). So the guest cannot see host contention, and that view has to come from Windows.
- **The schedstat on-CPU field is stale for the running thread.** Over a 3 ms busy loop, `thread_time_ns` gave 3.04 ms and the schedstat field-1 delta gave 6.70 ms. So spans inside a thread use `time.thread_time_ns()` (2.02 µs [M]). Schedstat is used only for `run_delay` (completed waits are accurate) and for reading *other* threads from the probe.

---

## 4. Architecture

### 4.1 The per-frame trace (`stream_service/trace.py`, new)

`FrameTrace` is a struct-of-arrays numpy ring with 8192 rows, about 128 B per row, so about 1 MB per session. That is 273 s at 30 fps and 136 s at 60 fps. Each thread writes only its own columns, so there are no locks. The pattern matches today's "plain counters under the GIL" comment (renderer.py:175).

| Column | Written by, at | Source in code |
|---|---|---|
| `t_wake` (f64 s), `slip_us` (the pre-resync value is kept), `flags.slept`, `flags.resync` | render, after the pacing loop | renderer.py:433-446. The slip is recorded before `slip = 0.0`, which fixes the "slip 0 on resync" bug. |
| `pre_us`: `_poll_timer`, `_drain_commands`, `_poll_retired` | render | renderer.py:448-452 (time not covered by today's `t_start`) |
| `wait_us` (ring), `flags.repeat`, `repeat_reason` (rb = render-bound EMA, gpu = backlog) | render | :464-472, :639-648, :713-726 |
| Stage spans `hook_us` (sub-spans `mod_us`, `sound_us`), `tree_us`, `nv12_us`, `hud_us`, `d2h_us`, `host_us` | render, `pace_clock` at each boundary | :657-700. The hook is sessions.py:770-774 (`runtime.begin_frame`, `sound.update`). |
| `cpu_us` (`thread_time_ns` delta over the host section), `runq_us` (`run_delay` delta via a kept-open `/proc/thread-self/schedstat` fd), `vcsw`/`ivcsw` (`getrusage(RUSAGE_THREAD)`) | render | new; `blocked_us = host − cpu − runq` |
| `gpu_pending` at frame start (`len(timer.fifo)`), `rate_div`, `flags.held` (the source copied its hold frame), `n_fx`, `flags.probe` | render, plus a flag set from layers.py:165-168 | guard.py:196-199 |
| `gpu_span_us` (ev_start to ev_rendered), `gpu_tree_us` (ev_start to new `ev_tree`), `copy_us` | writer, after `ev_copied.synchronize()`, which is already done | renderer.py:802-809. Events are read only after they complete, so no new sync. |
| `t_pick`, `write_us`, `wcpu_us` (writer thread CPU) | writer | :810-822 |
| `t_pkt`, `size`, `key` (and `t_enc_pre`/`t_enc_post` when ffstats is on, §4.4) | nvenc-read thread | :842-852 |
| `t_posted`, `t_sent_first`, `t_sent_last`, `n_sent`, `n_dropkey` | loop | media.py:113-136, :188-221 (§4.5) |

Derived columns are computed only when aggregating:
- chain service = `t_pkt(n) − max(t_written(n), t_pkt(n−1))`. This is the lenses' formula for the encoder stage, and it needs no ffmpeg changes.
- content frames = rendered and not held.

**Markers.** A second ring of 4096 entries holds `(t, dur, kind, detail)` for things that steal the GIL or change the work. `trace.mark()` writes them, and `trace.span(kind)` is a context manager. Sources:
- the rescan (server.py:458-470, :504-506);
- library index and preset signatures;
- `prepare`/`prime`/`warm` (renderer.py:233-309);
- executor jobs, by wrapping `Session._run` (sessions.py:454);
- HTTP routes over 5 ms, timed in the `auth_and_errors` middleware (server.py:82-83);
- asyncio loop lag over 10 ms (a 50 ms ticker task, the show_service brain.py pattern);
- `gc.callbacks` for gen-2 collections or any collection over 2 ms (H1 verdict 2 names untuned GC as an untested spike candidate);
- resync, rate change, cue, transition and fx change, server drop-to-key per client (media.py:116-131), and HUD builds.

**OS thread names.** Python 3.12 does not set the OS thread name, so every thread shows as `python` in `/proc`, `top -H` and py-spy. `trace.name_thread("north-render")` calls `prctl(PR_SET_NAME)` once per thread, at renderer.py:344-347 and encoder.py:83-84, and from the executor initializer. The probe and every lens tool can then find threads by name.

### 4.2 GPU timings without synchronising

- **Per frame, always:** ev_start, the new **ev_tree** (after `bus.render`, before `rgba_to_nv12` at renderer.py:683), ev_rendered and ev_copied. The writer reads all four after the sync it already does. Cost: one Event.record, p50 11-17 µs [M].
- **GPU lag, the real "GPU-bound" signal:** `gpu_pending ≥ 1` at frame start means the GPU had not finished the previous frame when the host began this one. In the baseline the GPU finished within 2.7 ms on 100% of 4661 frames [M] (H7), so this bit is about 0 today. It is the test that would show a change.
- **Per plug-in (guard):** `begin`/`end` (guard.py:163-184) also take `pace_clock` and `thread_time_ns` (about 2.85 µs each end [M]). Each sample therefore carries host wall, host CPU and event span. A sample is classed **host-paced** when span ≤ host wall + max(1 ms, 10%). For such a sample the span is an upper bound on GPU time, not the GPU cost. The baseline was 99.9-100% host-paced [M] (gaps_baseline.json).
- **True GPU cost: held-stream probe frames [U].** This is the production version of calibrate.py's `cal_spin` (:140-160).
  - About once a second per session, the probe picks one stage in round-robin: the scene, each effect, the active transition, or NV12 plus HUD.
  - Just before that stage's `begin`, it launches a single-thread **hold** kernel on the render stream. The kernel spins on `%globaltimer` for `hold_ms` = that stage's host-wall p75, capped at 1 period.
  - The GPU then runs the stage's launches back to back after the host has queued them, so the event pair times GPU work only.
  - The sample is kept only if the host finished queueing before `t_launch + hold_ms`, which can be checked from host clocks alone. Otherwise it is counted as rejected.
  - Cost per probe: that one frame finishes at most (hold − host) plus the stage's GPU time later, a few ms once a second [I]. Output pixels are unchanged.
  - It is bounded by the in-kernel time cap, with the guard's 3 s hang stop as backstop.
  - **Unverified:** `%globaltimer` behaviour under WSL2 GPU-PV. Do not fall back to `clock64`, because GPU0 down-clocks as far as 210 MHz (GPU-G4) and a cycle cap would then stretch 10x. If globaltimer fails on devtest, the probe stays off and the table shows calibrated values.
- **The split between Python and kernel launches** is not measured continuously. Instead, `RUSAGE_THREAD` gives a per-second user/sys split (sys is about driver and kernel work: 35% in FL10 [M]), and each plug-in's own CPU comes from the guard brackets. On request, `py-spy --native` gives the line-level split (Stage F).

### 4.3 The encode path

- **Always on, from existing fields:** queue (`t_pick − end_of_host`), `write_us`, chain service, and `t_pkt → t_posted`.
- **ffmpeg's own threads at 1 Hz from the probe child:** dmx0:rawvideo, main and mux0:flv. For each: CPU per frame, the share of time in D or S, and minor faults. These are the numbers E1 and H3 used.
- **Per-frame split on request:** `-stats_enc_pre`/`-stats_enc_post` with `'{n} {ptsi}'` write to FIFOs that `encode/ffstats.py` opens before spawning ffmpeg. A thread does only `os.read` and stamps `pace_clock`.
  - A bench showed one line per frame, arriving per frame (instrumentation/ffstats_flush_result.json, 20/20 lines) [M].
  - The stamps include our reader's GIL wait (p99 ms-level) [I]. Use them per second, not per frame.
  - It is off by default, behind a per-session switch, and crash-checked on devtest first. If the reader stalls for about 27 s of lines, ffmpeg blocks [I].
- **Later, with GPU-resident encode:** the native encoder fills `t_enc_in`/`t_enc_out` and its lock-wait in the same columns.

### 4.4 Fan-out and the glass

- **Server** (media.py, owner 04):
  - `on_frame` stamps `t_posted(n)`.
  - `_sender` stamps `t_sent(n)` when `send_bytes` returns. It reads n from the message bytes 2:6, already present (renderer.py:854), and also records `transport_backlog()`. This marks hand-off to the transport, not the wire, and is labelled so.
  - A per-client ring of 512 `(n, t_sent)`.
  - Stats messages are kept with their receive time instead of being overwritten.
- **Page** (MseLivePlayer.ts, owner 2f). Fields are added to the existing 1 Hz `stats` message (:481-499); no new socket or DOM:
  - `last_n` (the rVFC `mediaTime`, :419-424);
  - `pgaps` (up to 8 `[n, ms]` present gaps over 1.5 periods);
  - `agaps` (up to 8 `[n, ms]` WS inter-arrival gaps over 1.5 periods, measured with `performance.now()` in onmessage);
  - `waits` (n at each `waiting`, :501-504);
  - counts of `edge_jumps`, `gap_skips` (not sent today) and `nudges`;
  - identity: `glass` (the screen this page is driving, or null), a short UA, and an instance id.
  - This is under 1 KB/s per client [I].
- **diag.client_stats** (diag.py:111-127) uses the client that is driving the glass, not the one with the most frames presented. This matters because the 127.0.0.1 "kiosk" client is Brave on PC22 (H5) [M].

### 4.5 The box probe child (`stream_service/boxprobe.py`, new)

`python -m stream_service.boxprobe --parent <pid>` is spawned by stream_service at start. It is supervised with restart on exit and backoff. If it is absent, the recorder still works and shows "box: unavailable". It is a single-threaded process with its own GIL, at nice 0, and talks to stream_service over non-blocking pipes in both directions. On EAGAIN it drops the line and counts `dropped_lines`.

| Rate | What | Cost [M unit costs; totals I] |
|---|---|---|
| 100 Hz | Its own sleep-to-wake lateness. This is the host and scheduler wake tail with no GIL in it (HC2 measured p99 4.5 ms loaded and 0.3 ms idle). Samples taken after the probe's own work are excluded. | about 0.2% of a core |
| 10 Hz | `/proc/<parent>/task/*/schedstat` through kept-open fds, about 25 threads × 2.88 µs. That gives per-thread CPU and run queue for main, workers, renders, writers and readers in 100 ms bins: the "suspects" for blocked time. | about 0.7 ms/s |
| 1 Hz | ffmpeg and `score.*` children per thread; `/proc/stat` (guest busy vCPUs against the ~8 LP WSL budget H4 found); loadavg; procs_running; `/proc/net/udp` `:1B44` rx_queue/drops (show_service's VBAN socket, read-only, H6); NVML per GPU (util, enc and dec with sampling period, sm/video clocks, clock event reasons, temperature, power, VRAM); Spectre host card (§4.6) | about 2 ms/s |
| 0.2 Hz | Top 8 guest processes by CPU delta, scanned in chunks | about 400 × 57 µs, but in another process, so no GIL cost to the renderer |

Notes:
- GPU0's SM% includes the Windows desktop and Brave. Per-process NVML is probably unavailable on WSL [U], so GPU0's SM% stays a box-level figure.
- The probe needs `nvidia-ml-py` in `~/.venvs/sm-stream`. diag.py:22 already prefers pynvml, so installing it also stops the nvidia-smi fork on every poll.

### 4.6 The Windows host card (Spectre, owner ca)

This is a new Windows-side piece, so per CLAUDE.md it gets its own card.
- **Pieces:** a module in `src/server/modules/`, registered in `registry.ts`, that runs `scripts/windows/hostperf/hostperf.ps1`. The script holds .NET PerformanceCounter/PDH handles open rather than using WMI; WMI cost 1.6% of the box in the baseline [M].
- **Counters at 1 Hz:**
  - Hyper-V LP `% Idle Time` and `% Total Run Time`;
  - root VP total run time;
  - WSL VP `% Guest Run Time`;
  - `% Processor Performance`, `% Performance Limit` and its flags;
  - Processor Queue Length;
  - committed memory.
- **At 0.2 Hz:** GPU Engine utilisation by adapter, and the top 8 Windows processes. HC4 found Brave at about 2.7 cores and the Spectre UI dev server at about 1.
- `CPU Wait Time Per Dispatch` is included but labelled "probably blind": it read a flat 2.4-3.5 µs (H4).
- **Status and reading:** status comes from `GET /api/status/<id>`; the probe polls it, with the address from `.env`, not a hard-coded IP.
- **Housekeeping:** the card reports its own CPU, and the CLAUDE.md Spectre section gets the new card. It has no port.

### 4.7 Aggregation, endpoints and storage

- **Once a second per session, on the existing 10 Hz ticker** (sessions.py:2922-2930, every 10th tick):
  - aggregate the last second of trace rows with numpy (about 100 µs [I]): stage p50/p95, duty, blame shares, content fps, trace level;
  - cache the result as JSON bytes, keep 120 s of history;
  - hand one line to the probe, which writes the flight file.
- **Endpoints** (all additive GETs, reached via `:8700/renderer/*`, no auth):

| Endpoint | Returns | Notes |
|---|---|---|
| `GET /api/perf` | per-session cached aggregates for the last 120 s, current verdict, box sample, markers from the last 60 s | Served from cache, so a GET never recomputes. The per-GET summary cost today is 45-128 ms in-process [M]. |
| `GET /api/sessions/{sid}/trace?since=N` | binary columnar rows (`ndarray[i0:i1].tobytes()` with a dtype header); `&fmt=json&limit≤512` for tools | JSON for 4096 rows × 40 columns would be tens of ms of GIL [I], and the lenses' own polls caused 10 of 55 spikes [M]. |
| `GET /api/sessions/{sid}/late?since=N` | dossiers (§5) | |
| `GET /api/perf/costs` | the cost table (§6) | |
| `GET /api/perf/flight?date&screen&from&to` | gz JSONL, read in an executor in chunks | |
| `PATCH /api/sessions/{sid} {"ffstats": bool}` | the per-session switch | Handled like `hud`, never spread to peers. |

`/api/diag` and `/frames` stay; `/frames` becomes an alias of `trace&fmt=json`.

- **Flight recorder** (the probe writes it):
  - `~/.local/state/screen-machine/perf/YYYY-MM-DD/<screen>.jsonl.gz`, one aggregate line per second plus at most 5 dossiers per second;
  - box lines go to `box.jsonl.gz`;
  - kept 3 days, about 10 MB per day after gzip [I].
  - It survives stream restarts, which happened 3 times on 18 Oct and cost two verification rounds their live window.

---

## 5. The attribution rule: what made each frame late

**(a) What started the lateness** (the FL5 increment method, which matches the lenses' 80-88% host / 9-18% ring result [M]).
- Let `L(n) = max(0, slip(n))` with the resync slip recorded honestly, and let `ΔL(n+1) = L(n+1) − L(n)`.
- When ΔL > 0 and the render thread did not sleep, split ΔL across slot n's components in proportion to their share of the busy time `B(n) = pre + wait + host`:

| Component | Category | Further split |
|---|---|---|
| `cpu_us` | **own CPU** | by stage wall share (hook: modulation and sound; tree: each plug-in's own CPU from the guard, the remainder is "engine"; NV12; HUD; D2H) |
| `blocked_us` | **blocked (GIL/driver)** [I] | suspects: other threads' CPU in the same 100 ms bin from the probe, plus markers within ±0.25 s (the H1 coincidence window), top 3 |
| `runq_us` | **guest run queue** | top guest processes |
| `wait_us` | **pipe/encoder back-pressure** | writer in write (pipe) or chain service above the period (encoder) |
| `pre_us` | **commands/guard** | marker (transition start, cue) |
| repeat with reason `gpu`, or `gpu_pending ≥ 1` | **GPU** | the probed stage's GPU ms |
| `slip` on a slept slot | **wake** | probe wake tail at that second |

**(b) What kept it late.** In a run of late or repeated slots, the drain per slot is the period minus the writer's back-to-back gap. H2 and H3 confirmed this within 1-2 ms on four windows [M]. When the gap is at or above the period, the run is labelled **pipe-bound (cannot catch up)**, and the next resync is predicted.

**(c) Output lateness** (serial, so no proportional split): `t_sent(n) − D(n)` = slip + pre + wait + host + queue + write + chain + parse→post + send.

**(d) Glass, per stall** (by n):
- send gap at or above 1.5 periods and arrival gap about equal to it: **upstream**, linked to the dossier for n;
- arrival gap minus send gap at or above 1 period: **network/transport**;
- arrival on time but a present gap or `waiting`: **player/decode**;
- an edge jump, gap skip or nudge at n: **player seek** (10-24% of counted stalls, H5 [I]);
- server drop-to-key: **drop**, a marker with the client id.

**Verdict per second:**
- CPU or GPU is decided by the duty table.
- **GPU-bound** if any of these hold: `gpu_pending ≥ 1` on more than 10% of frames; probe GPU per frame above 0.7 period; NVENC duty above 85%; SM above 80%.
- Otherwise it names the CPU-side stage with the highest duty, and the top two blame categories.

Example dossier. This shows the format only; the numbers resemble the baseline but are not one real frame.
```
n=41213 north  late 61 ms (+28 this slot)  REPEAT rb (ema 34.1 > 28.3)
 started by slot 41212: host 58.2 = cpu 13.1 + blocked 44.6 + runq 0.4 | wait 2.1 | pre 0.3
   blocked suspects (100 ms): main 71 ms [GET /api/sessions 48 ms], south-render 38 ms, gc -
   stages: hook 4.1 (sound 2.6)  tree 47.9 (note_fluid host 41.2 cpu 9.8 span 43.0 gpu 1.1*)  nv12 0.6
 sustained by: writer gap 31.9 ms vs 33.3 period -> drains 1.4 ms/slot
 output: queue 31  write 27.0  chain 28.5  parse 0.4  loop 1.2  sent(TV) +2.0
 glass TV: arrival gap = send gap, presented on time
 * probe sample 3 s ago
```

---

## 6. The live GPU cost table (`stream_service/costs.py`, new)

- **Key:** (plug-in name, plug-in sha1 as in calibration.json, kind, GPU index/model, render_scale, profile fps).
- **Per key:** calls/s; host wall p50/p95; own CPU p50/p95; blocked share; span p50/p95; **probe GPU p50/p95 (n, rejected)**; calibrated p50/p95 from `~/.cache/sm-fxtest/calibration.json` for this GPU; inflation (span ÷ probe GPU); share of time at `rate_div > 1`; content fps. A scene row adds its effect stack's sums.
- **Storage:** log-bucket histograms (64 buckets) with an EWMA, held in memory and written to `~/.local/state/screen-machine/perf/costs.json` every 60 s from an executor. The table survives restarts and merges across sessions and GPUs.
- **Uses:**
  - It shows the inflation directly: live worst note_fluid 133-344 ms against calibrated 0.98-1.38 ms [M].
  - It flags plug-ins whose live GPU cost differs from calibration (the cost depends on content).
  - It will be the honest cost input for rate_div and the later controller.
  - Switching rate_div onto it is **an owner decision after a side-by-side log**. Stage E only logs both signals.

---

## 7. Where it shows

- **TV details panel** (hud.py `build_lines`). The `render`, `guard` and `glass` lines become three lines of at most 64 columns, so ROWS stays 16. The values below are illustrative, close to the baseline:
  - `host 22/85 c12 b10 q0 gpu3 lag0 | late34% GIL46 cpu38 pipe12`
  - `out w24 chain29 nv15 q31 | duty r88 w82 e90 nv43 sm24 %`
  - `glass 29.4fps drop1.1/s buf150 stall1.3/s net+2 seek.2`
  
  The panel also shows `trace L1` when the recorder has degraded. Glass rates come from the stats ring, timed by receipt.
- **Dev console, Under the hood → Diagnostics** (stream_service/static/console/hood/diag.js, HIST=120 already). This is the owner's main view:
  1. "Where the time goes": per-second stacked blame bars.
  2. Stage duty meters, including guest vCPU budget, Windows LP idle and % processor performance.
  3. The last 30 late-frame dossiers, expandable.
  4. The cost table for this GPU.
  5. The box: top guest and Windows processes, GPU clocks, temperatures and throttle reasons, show socket backlog.
  
  The console polls `/api/perf` (cached) at 1 Hz, only while the section is open.
- **The /room page is unchanged** (CLAUDE.md rule 7). Its Technical details text view already mirrors the HUD lines.
- **d4's /show/dev/dsp monitor** may add one "Renderer" tile reading `/renderer/api/perf`, so inputs and renderer sit on one page. That page is d4's, so tell d4; this design does not depend on it.

---

## 8. Overhead budget and the self-degradation ladder

Unit costs [M] (instrumentation/probe_overhead.json, event_cost_gpu1.json, cudacall_bench.txt):

| Call | µs |
|---|---|
| perf_counter_ns | 0.62 |
| CLOCK_MONOTONIC_RAW | 0.83 |
| thread_time_ns | 2.02 |
| getrusage(THREAD) | 1.6 |
| schedstat pread, kept-open fd | 2.88 |
| schedstat open-read-close | 56.8 |
| Event.record | 11-17 p50; p99 6,000-12,700 under convoy |
| Event.done | 0.8-5 |
| get_elapsed_time | 1.5 |

Render thread at L0 [I], to be checked against the self-meter:

| Item | Cost per frame |
|---|---|
| about 10 boundary stamps | about 8 µs |
| thread CPU, run queue and rusage at host start and end | about 13 µs |
| about 25 column writes | about 7 µs |
| guard host timing, 3-5 plug-ins | 15-30 µs |
| ev_tree | 11-17 µs p50 |
| **Total** | **about 55-75 µs**, with Python attribute overhead perhaps 2x, so about 0.15 ms |

Budget: **≤ 0.3 ms per frame**, which is 1.8% at 60 fps. The 6 `Ring.add` calls per frame go away, and Stage E trims guard records.

Other components:
- writer: about 8 µs per frame;
- loop: about 10 µs per frame per session, plus the 1 Hz aggregate of about 100 µs;
- probe child: under 1% of a core and none of the GIL.

**Self-meter:** `trace.py` times its own bookkeeping per frame, and the probe reports its own CPU. Over budget (p95 over 5 s) steps one level down; under half of budget for 30 s steps back up. The level shows on the HUD, the console and the flight log.

| Level | Stage spans and thread CPU | ev_tree | Guard host timing | Probe frames |
|---|---|---|---|---|
| L0 | every frame | every frame | every call | 1 per s |
| L1 | every 2nd frame | off | every 2nd frame | 1 per 2 s |
| L2 | every 4th frame | off | every 4th frame | 1 per 5 s |
| L3 | every 8th frame | off | round-robin, one plug-in | off |

At every level the frame-level `host`, `wait`, `write` and `t_pkt` are always recorded, as they are today.

---

## 9. Staged build list (smallest useful first)

Each stage gets a crash-check on devtest and one batched `scripts/ctl restart stream` when no screen is live (owner rule; batch-stream-restarts). `trace.py` and `costs.py` are shared plumbing and get real unit tests.

| Stage | Files / functions | Owner, effort | What you can answer after it |
|---|---|---|---|
| **A. Stop the misleading numbers** | renderer.py:141 (host_ms None on repeats), :440-446 (record slip before resync), `window_stats` :949-958 (rendered rows only); hud.py:269 (`frame_gpu_ms`); render→"span" labels in hud/diag/README; diag.py:76-108 verdict from host vs GPU-pending; media.py:176-180 stats ring by receipt time, hud.py:272-293 glass rates from it; layers.py:156-160 log and marker with session; stats.py `Ring.summary` with one sort and a 1 s cache, sessions.py:2055 `_update_lead` uses it; `pip install nvidia-ml-py` into sm-stream; `prctl` thread names at renderer.py:344-347 and encoder.py:83-84 | 04, 2-3 h | Correct host p50 (22.5 ms, not 0-9), a working guard line, true glass fps, rate changes per screen. Removes the top GIL-held line (18.3 ms/call). |
| **B. Frame trace and attribution** | new `trace.py` (`FrameTrace`, `span`, `mark`, `ThreadClock`, `gc` hook, `blame`, `aggregate`, ladder); renderer.py `_render_loop` :432-476, `_render_frame` :650-711 (+ev_tree), `_submit_repeat` :713-726, `_writer_loop` :790-826, `_on_frame` :842-855, `summary`/`window_stats`/`frame_records` read the trace; layers.py:165-168 held flag; sessions.py:770-774 hook sub-spans, :454 worker markers; server.py middleware :82-83, rescan :458-506, loop-lag task, `/trace`, `/late`, `/api/perf`; hud.py 3 lines; tests/test_trace.py | 04, 0.5-1 day | For every frame: own CPU against blocked against run queue by stage, starter against sustainer, content fps, duty per stage. Answers "CPU or GPU" continuously. |
| **C. Fan-out and glass join** | media.py `on_frame` :113-136, `_sender` :188-221, client stats ring; MseLivePlayer.ts `sendStats` :481-499, `onVideoFrame` :419-430, onmessage arrival gaps, `onWaiting` :501-504, gap_skips and identity; diag.py:111-127 driving-glass client; frontend deploy per CLAUDE.md | 04 + 2f, 0.5 day | Each TV stall classed as upstream, network, seek, decode or drop, with frame id. Brave and the TV separated. |
| **D. Box probe, host card, flight recorder** | new `boxprobe.py` (child plus parent client); Spectre module plus `scripts/windows/hostperf/hostperf.ps1`, CLAUDE.md cards list (ca); flight files | 04 + ca, 1 day | Windows saturation and clock live, which other process is eating CPU, GPU clocks and throttle reasons, show input socket backlog, replay after a restart. |
| **E. Live cost table and probe frames** | guard.py `begin`/`end`/`_sample` host wall and CPU; round-robin per-plug-in end events (A/B; hang stop and localisation unchanged; GIL-G3 puts guard records at about 3.0 ms per frame); hold kernel in `gpu.py` kernels (globaltimer-bounded); probe scheduler in renderer; new `costs.py`; `/api/perf/costs`; console table; rate_div's current signal logged beside the honest one | 04, 1 day | True GPU cost per scene and effect per GPU, live, against calibration; a measured inflation factor; the input the degradation controller needs. |
| **F. On request** | `encode/ffstats.py` plus args at encoder.py:50-73 and the `ffstats` PATCH; `tools/snapshot.py` (nice 19, at most 20 s: `py-spy record --gil --nonblocking` plus trace plus `/api/perf` into one folder); an optional external GIL meter (`/proc/<pid>/mem` reader with CPython 3.12.3 offsets, as in verify-h1, fragile across upgrades) | 04, 0.5 day | The encoder chain split into demux/queue, upload plus NVENC, and mux. Who holds the GIL at a given stall. |
| **G. With GPU-resident encode** | the native encoder fills `t_enc_in`/`t_enc_out`/lock-wait | 04 | The same views after the pipe goes away. |

---

## 10. Unverified parts

| Point | Status |
|---|---|
| "blocked ≈ GIL" | [I]. Measured only on lighter scenes (FL1, 14:58). Stage F's py-spy snapshot is the check. |
| `%globaltimer` hold kernel on WSL2 GPU-PV | [U]. The probe stays off if it fails. |
| Per-process NVML on WSL | [U], probably absent. GPU0's SM% stays box-level. |
| ffstats reader blocking ffmpeg | Possible only with a GIL stall of tens of seconds [I]. It stays off by default. |
| Spectre PDH cost | Unmeasured. WMI's 1.6% is the ceiling to beat. |
| frame_id = n | [I] from code. The Stage B unit test plus a live check that `size` fills for every n confirms it. |
| Overhead figures | Estimates built from measured unit costs. The self-meter is the arbiter. |

## 11. What it would have shown on the 14:27 baseline (sanity check from verified lens numbers, north / south)

| Reading | Value [M] |
|---|---|
| host_ms p50/p95, rendered frames only | 22.5/84.7 / 19.4/78.3 ms |
| own CPU per frame, calm seconds | about 12.5 / 12.7 ms |
| blocked per frame (quiet / burst seconds) | ≥11.5 / ≥10.0 ms quiet; 36-42 ms in bursts |
| run queue | 0.17-0.30% of wall |
| Render-thread duty / writer / encoder chain | 83-91% / 82-76% / 90-84% |
| NVENC | 14.6 / 13.3 ms per frame (39-43% busy) |
| SM | 23.4 / 13.0% |
| GPU lag | ≤ 2.7 ms on 100% of frames |
| Slip started by host / ring | 80 / 88% host, 9-18% ring |
| Repeats / content fps | 34 / 18%; 19.3 / 24.2 fps |
| Windows LP idle / % processor performance | 0.03% / 59.8-62.5 (about 2.3-2.5 GHz) |
| Show socket (loaded) | 268-285 KB standing, 23-37% loss |
| Verdict line | "CPU-bound: render thread (about half GIL-blocked), encoder chain 84-90%; GPUs idle; Windows host saturated" |

All of these numbers come from the 14:27 baseline (note_fluid). The live re-measures could not run, because no screen was live from 15:06 on 18 Oct. The probe-overhead and event-cost files used for §8 are in `/tmp/claude-1000/-home-gjbm2-dev-screen-machine/49a9e605-47c6-4ba4-98e3-fd28d61d6fec/scratchpad/perf/instrumentation/`.