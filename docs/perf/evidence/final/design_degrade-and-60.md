# Design 3: the path to smooth 2×4K60, then graceful degradation

**Evidence base.** Live numbers come from the 14:27 baseline unless marked otherwise: PID 818235, note-fluid and piano-waterfall scenes, both screens at 2160p30. The H2 figures also use the clean window from 15:01 to 15:04. No screen was live from 15:06 to 16:14, so nothing was re-measured live. The 60 fps encode figures come from bounded synthetic benches run at nice 19 between 15:49 and 15:51 with 0 sessions live. Each figure is tagged:
- **[M]** measured;
- **[I]** inferred from code or arithmetic;
- **[U]** depends on something not yet verified.

**Owner rules this design keeps:**
- Optimise the slow part before degrading.
- Degrade, never refuse: no gates, trips, self-off or hidden holds.
- Every GPU job is bounded, and the 3 s hang stop stays.
- The renderer never masks.
- The cut frames between art and live match the still exactly.
- Output resolution never changes mid-stream. A new init segment would mean a visible re-init, and the cut is matched at 4K.

---

## 1. Where 2×4K60 stands today: the walls in order

| # | Wall | Measured today | At 2×4K60 | What removes it |
|---|---|---|---|---|
| 1 | Raw NV12 pipe and rawvideo demux (12.4 MB per frame through a 1 MiB pipe) | Baseline: 26-28 ms wall per frame, of which writer plus demux kernel CPU is 24-26 ms **[M]**. Bench: one 4K60 chain holds 60.1 fps; **two chains with no rendering reach only 50.2-52.2 fps** (write p50 17-18 ms, 28.7-29.8 ms CPU per frame) **[M]** | Does not fit, even with zero render cost | GPU-resident encode (Phase C) |
| 2 | One GIL shared by both screens, the asyncio loop and workers | Render threads wait on the GIL futex 18.6-19.4% of wall time **[M]**:<br>- about 7 ms per frame on the lighter scenes;<br>- at least 10-17 ms per frame off-CPU on note_fluid.<br>80% of spikes over 100 ms hit both screens together; chance would give 24% **[M]**. Two CUDA threads in one process double the per-call cost, 91 to 200 µs; two processes mostly do not **[M]** | 2 × 60 × 6.6 ms of GIL-held time, plus the loop's 26-30%, comes to **105-109% of one GIL** | One process per screen (Phase D), plus fewer calls (Phase E) |
| 3 | Render thread's own CPU | 11-13 ms per frame (65% user, 35% sys); host p50 is over 16.7 ms on 57-61% of frames **[M]** | 70-80% of a 60 fps frame **even with zero GIL wait** | A per-frame host diet (Phase E) |
| 4 | 30p profile: upload then NVENC wait in series (p5, `-delay 0`, profiles.py:81) | Chain service p50 26.2-28.5 ms, a cap of about 33-35 fps **[M]** | Not relevant: the 60p profile is p4 with `-delay 2` (profiles.py:79) | `nvenc_delay≥1` now (B6); gone with in-process encode |
| 5 | NVENC engine | At 30p, p5: 13-15 ms per frame at 30 fps clocks. Bench at a paced 60 fps on the real 60p profile **[M]**:<br>- 4060: 9.3 ms (56% busy);<br>- P4000: 9.95 ms (60% busy), about 69% with production content and about 83% when sw_thermal throttles **[I]** | Fits, with 30-44% headroom when not throttled | The encoder steps on the ladder; P4000 cooling |
| 6 | SM compute | About 3 ms per frame on the P4000 after subtracting encode work; 5.1 ms per full frame from the two-variable fit **[M]**. On the 4060 it is 6-8 ms, but that residual includes an unattributed desktop and Brave share **[U]** | note_fluid takes about 31-34% of the P4000 at 60 fps. seahorse (12 ms) and nebula aa=2 (15.9 ms) on the P4000 **do not fit** **[M]** | Ladder steps: render_scale, rate_div |
| 7 | The box | WSL gets about 8 of the 20 logical processors, at about 2.45 GHz (62-67% of the 3.7 GHz nominal, and it stays there when idle). At baseline the guest's 7.8 busy vCPUs fill about 97% of that share **[M]** | The pipe path alone would need about 4.3-4.6 cores at 2×60 **[I]** | Phase C (the biggest saving), Phase F |
| 8 | Delivery | 57 of about 180 Mbit/s on the powerline today. 60-86% of TV stall counts are explained by upstream packet timing (H5) | 2 × 50 Mbit/s gives a **modelled** p99 of about 62 ms of queueing against the 120 ms buffer **[U]** | Bitrate of 30-40 Mbit/s; paced output |

At 30 fps today the limits are, in order: render-thread host cost (it starts 80-88% of the slip), then the encode chain (it sets how fast lateness is caught up). The GPU's share is about 0%: GPU lag stayed at or under 2.7 ms on 100% of 4,661 frames **[M]**.

---

## 2. Optimisation path, in order

Each phase is prototyped, measured and then kept. There are no stop gates, only "done when it measures X".

### Phase A: honest numbers first (hours; owner 04)

Every later step is judged on these, so they come first.

| Fix | Where | Why |
|---|---|---|
| Split host_ms into `cpu_ms` (time.thread_time) and `blocked_ms` in `_Rec`, around `_render_frame`, `frame_hook` and the writer submit | renderer.py:657/:700 | Separates our own Python and driver cost from the GIL convoy, on every frame |
| Record `div`/`held` in `_Rec`; set host_ms to None on repeats; record slip honestly at a resync | renderer.py:136-150, :440-446 | Content fps is unmeasured today; window_stats is biased low |
| HUD guard key: read `frame_gpu_ms` | hud.py:269 | That HUD field is always blank today |
| Add the session id to the rate log; emit a `session.rate` event; relabel render_ms as "GPU stream span" | layers.py:155-157, diag.py:91-92 | Today it says "on the GPU" when the cost is host time (12-67× the calibrated cost) |
| Per-plug-in guard table: host on-CPU time against the event span, plus calibrated GPU cost (F4) | guard.py:163-193 | Shows the 20-300× inflation per plug-in |
| 1 Hz flight recorder (F5), including NVML clocks and enc% | new | Before/after comparison for every step below |

### Phase B: cheap fixes at today's 30p (hours each)

| # | Fix | Expected gain | Basis |
|---|---|---|---|
| B1 | Session summary: sort each ring once and cache for 1 s | stats.py:9-30, renderer.py:917-930. 18.3 ms of GIL per call drops to under 1 ms. Main-thread GIL drops from 26-30% to about 15-18% of wall | [M] cost per call, [I] result |
| B2 | Make the 20 s plug-in rescan incremental (directory mtimes), and run the library index and preset signature in a subprocess | plugins/__init__.py:116-170, presets.py:227-236, library.py:266. The rescan bursts hold **26-29% of lateness in 10% of the time**; south fell from 29 to 20 fps and from 13.5 to 4.4 fps in the measured bursts | [M] |
| B3 | Replace the `sleep(0)` spin with one sleep to the deadline | renderer.py:438. Wake slip p95 from 3.5-4.8 ms to under 0.5 ms, and fewer GIL re-takes | [I] |
| B4 | Guard: keep the frame-level events and the 3 s hang stop; time individual plug-ins round-robin | guard.py:163-193. About 3.0 ms down to about 0.5 ms of host time per frame | [M] split, [I] gain |
| B5 | Batch per-frame uploads into one pinned staging copy, from a per-slot ring | sound.py:69/:72, note_fluid `U.set`. Uploads cost 2.4 ms per frame live against 0.06-0.3 ms in isolation; the `.set` share drops from about 5.3 ms to under 1 ms | [M] cost, [I] gain |
| B6 | `nvenc_delay=1` (or 2) on hevc-2160p30 | profiles.py:81. Stage B (upload plus NVENC) is the tighter link at 30p; with a delay, the bench reaches 49-55 fps at delay 0 and 67-103 fps at delay 2. Costs 33 ms per frame of delay | [M] bench, one batched restart |
| B7 | Repeat lock-in: after 3 repeats in a row, render one probe frame | renderer.py:463-467. Counterfactual replay: **removes every run of 10 slots or more** (longest 0.1 s instead of 2.7-2.9 s); north +1.5 to +2.6 content fps, south −0.3 to +1.4 | [M] replay |
| B8 | rate_div and `over_budget` driven by the plug-in's own CPU plus true GPU lag | layers.py:135-160, guard.py:280-290. Removes about 30 false demotions per 90 s, and frozen exits like the one at 15:06:40 | [M] mislabel, [U] content gain |
| B9 | A/B test `sys.setswitchinterval(0.001)` | Unknown until measured with Phase A | [U] |

**Do not:**
- **Render late instead of repeating.** The replay gives 5-34× more resyncs. This refutes the lens fix "repeat only when the render stage is binding".
- **Raise `fs.pipe-max-size`.** It needs root and the bench showed little gain.
- **Use SCHED_FIFO.** The losses are on the host, it needs root, and a spinning real-time thread conflicts with the bounded-job rule.
- **Step output resolution.** It forces a new init segment and breaks the cut.

Target after A and B at 2×30, to be measured: content fps of 28 or more on both screens, repeats under 5%, no freezes of 10 slots or more **[I]**. The pipe stays at 26-28 ms per frame, so this is not a path to 60.

### Phase C: GPU-resident encode (the one structural must; about 2-3 days after a 0.5-1 day prototype; owner 04)

**Prototype.**
- Install PyNvVideoCodec 2.2.3 in its own venv (not sm-stream).
- Run on GPU1 with no screen live, at most 240 frames, under a timeout.
- Measure:
  - CPU per frame;
  - NVENC ms at p1, p4 and p5;
  - whether `Encode` releases the GIL (a spinner thread counting while it runs);
  - its built-in delay of 3 frames, which is 50 ms at 60 fps.

**Build.**
- An in-process NVENC with 3 registered CuPy NV12 surfaces.
- `nvEncSetIOCudaStreams`, so each encode is ordered after the render stream with no host sync.
- An encoder thread that locks bitstreams with the GIL released and a bounded `doNotWait` poll budget. This keeps the GPU job bounded.
- IDR on demand, for cut frames and client joins.
- Keep fmp4.py. Keep the ffmpeg pipe as a fallback profile, chosen at session start if the native encoder will not open. That is a degrade, not a refusal.

**Removes:**
- the D2H copy (1.3-2.0 ms);
- the 149 MB pinned ring;
- the writer, pipe, demux and ffmpeg upload, worth **35-38 ms of CPU per frame per screen** at baseline load (about 1.1 cores per screen at 30 fps, about 2.2 at 60) **[M]**;
- the 12 wake handoffs per frame;
- server_ms p50 drops from 129-175 ms to about 35-55 ms **[I]**.

**Enables:**
- free repeats: re-submit the same surface (on the P4000 an unchanged surface already encodes cheaper, 15.4 against 19.4 Mcycles **[M]**), or extend the previous fragment's duration;
- paced output, with one frame per period from an output clock;
- a step from 60 to 30 fps through fragment duration;
- a forced IDR at `hold_pts`.

### Phase D: one render process per screen (1-2 weeks; owner 04; tell 2f, aa and c8)

- The child owns the render thread, the encoder thread and a small control thread.
- The parent keeps aiohttp, the buses, the directors and the fan-out.
- Commands go over a queue. The music and sound snapshot is a latest-value shared-memory block. Fragments reach the parent over a pipe or shm, or the child serves its media WS itself, which also takes egress off the parent GIL.
- Kernel caches are pre-warmed by a short-lived helper process, so the child's prime is a cache hit **[U]**.
- Removes the cross-screen convoy and the MainThread's 29.9% GIL share from the render path **[M]**.

### Phase E: per-frame host diet (days; one plug-in at a time)

- Capture the static launch sequences in CUDA graphs. Per-frame scalars go through the existing device param buffers.
- Fuse note_fluid's 20 Jacobi launches.
- Build one SoundView per room, evaluated at each session's photon time. hook_ms drops from about 4-6 to 2-3 ms **[I]**.
- Replace the sync at live_score.py:873 with double-buffered staging and an `Event.done` check.
- Target: 5-15 CUDA calls per frame instead of about 50-80, and render on-CPU time of 7 ms or less **[I]**.

### Phase F: the box (owner decisions; Windows only through Spectre)

- Turn off the dagnet dev-server's forced polling (0.83 core).
- Stop the leftover vites on :5188 and :5189 (0.4-0.6 core and 11-17k wakeups/s).
- Run agent batch jobs under `nice 19` or `chrt -i 0`.
- Watch the desktop Brave tabs (the 127.0.0.1 "kiosk" client on each screen). They decode both streams and take 17.7% of NVDEC on the 4060; Brave falls from about 2.7 to 1.3 cores with the screens off.
- Do a read-only check of the clock cap (62-67% of nominal, no limit flags, persists when idle; cause unknown).
- GPU clock hold: an optional A/B test through a Spectre card. The 4060 boosted by itself under a sustained 60 fps encode (video clock from 1522 to 1985-2125 MHz, p5 from 14.6 to 10.7 ms per frame **[M]**), so this may not be needed.
- Clean the P4000's cooler (82 C at 44 W).

---

## 3. Per-frame budget at 2×4K60 after Phases A-F

Per screen, at a 16.7 ms period. The stages are pipelined, so throughput is set by the slowest stage, not by their sum.

| Stage | Today, 30p baseline | After the path | Confidence |
|---|---|---|---|
| Render thread, own CPU | 11-13 ms | 5-7 ms | [I]: 50-80 down to 5-15 calls, guard 3 to 0.5 ms, uploads 5.3 to under 1 ms, sound view −2 ms |
| Render thread, blocked (GIL and handoffs) | at least 7 ms (lighter scenes) up to 10-17 ms (note_fluid) | 1-2 ms | [I]: per-process bench at 1.0-1.4× per call |
| **host_ms** | p50 19-22, p95 78-85, max 386 | **p50 6-9, p95 14 or less (target)** | [I] |
| GPU kernels, light scenes | about 3-5 ms | same | [M] P4000, [U] 4060 |
| GPU kernels, heavy presets (P4000) | 12-16 ms | same, so the ladder is needed | [M] |
| D2H copy | 1.3-2.0 ms | 0 | [M] |
| Writer, pipe and demux | 26-28 ms wall, 24-26 ms CPU | 0 | [M] |
| ffmpeg upload and mux CPU | 9-10 ms | 0 | [M] |
| NVENC engine (p4, delay 2, AQ) | — | 9.3 ms (4060); 10-11.5 ms (P4000); about 13.8 ms throttled | [M] bench, content-corrected |
| Encode CPU (submit, lock, fMP4) | — | 1-1.5 ms or less | **[U]**: the Phase C prototype measures it |
| Slot to packet | 129-175 ms | about 35-55 ms | [I] |

**Headroom per stage at 60 fps:**
- NVENC: 31-44% (P4000 throttled: about 17%).
- Render host: about 45-65%.
- SM: about 70-80% on light scenes; negative for seahorse and nebula on the P4000.

**Cores against WSL's roughly 8-LP share:**

| | Today's design projected to 2×60 | After the path |
|---|---|---|
| Render threads | 1.3-1.6 cores, and GIL demand over 100%, so it cannot run | 0.6-0.85 |
| Encode chain | 4.3-4.6 | about 0.1-0.2 |
| Loop, fan-out and buses | about 0.3 | about 0.2-0.4, spread across processes |
| show_service | about 1.0 | about 1.0 (moving madmom redistributes the load; it does not cut it) |
| **AV total** | **6.7 or more, and infeasible** | **about 2-2.5** [I] |

**Verdict.** 2×4K60 looks feasible for typical scenes on both GPUs after Phases C, D and E **[I]**. Heavy presets on the P4000, a throttled P4000 NVENC and host bursts still need the ladder. Phase C is required whatever else is done: the pipe caps two chains at 50-52 fps **[M]**.

---

## 4. The degradation ladder

### 4.1 One controller per session instead of three mechanisms

Today three independent signals, each prone to lock-in, act on host-inflated numbers:
- the repeat EMA, which freezes 0.3-2.8 s at a time;
- the scene rate_div, which flaps 20-30 times a minute across both screens;
- the guard's `over_budget` frozen exit.

On top of those, each resync shifts the timeline 278-297 ms.

The new design:
- **The controller is seconds-scale.** It decides at 1 Hz on a ticker, using arithmetic only. It applies each change through the render thread's existing command queue at a frame boundary, so it adds no GPU work.
- **Slot-scale shock absorber.** Before Phase C this is the repeat rule plus the probe after 3 repeats (B7). After Phase C an output clock emits exactly one frame per period, and a late render shows the previous surface again as a cheap P-frame or an extended fragment. Lateness never builds up, so the resync timeline shift goes away.
- **Kept as bounded safety, not ladder steps:** the guard backlog (MAX_GPU_BACKLOG 2, guard.py:39) and the 3 s hang stop (guard.py:38).

### 4.2 Signals (all from Phase A, no syncs)

| Stage | Signal | Window and smoothing | "Busy" when | "Has room" when |
|---|---|---|---|---|
| Host, own | `cpu_ms` per rendered frame | p90 over 1 s, EMA τ 2 s | p90 host_ms over 0.9 of the period and `cpu_ms` at least half of host_ms | projected cost at the next level up under 0.7 of the period for 10 s |
| Host, blocked | `blocked_ms` = host_ms − cpu_ms | same | p90 host_ms over 0.9 of the period and blocked over cpu | same |
| GPU | GPU lag (completion of ev_rendered minus the end of host enqueue) and backlog at frame start | p90 over 1 s | lag p90 over 3 ms, or backlog of 1 or more on over 10% of frames for 1 s. Today 0 of 4,661 frames went over 3 ms | the next level's calibrated cost (calibration.json fit) under 0.7 of the period, and lag p90 under 1 ms, for 10 s |
| Encoder | submit-to-bitstream time, pending surfaces, NVML enc% at 1 Hz | EMA τ 2 s | enc% over 85%, or 2 or more surfaces pending for 1 s | enc% under 65% for 20 s |
| Outcome | slot slip, held fraction | per second | confirms the stage. An overrun past 1.2 periods on 25% or more of slots within 0.25 s fast-tracks one step | — |
| Delivery | the driven glass client's minimum buffered_ms and p99 arrival gap (after the glass identity fix, diag.py:111-127) | 5 s | buffer under 40 ms, or gap p99 over 80 ms, for 5 s | clean for 60 s |

The busiest stage at 0.9 or more decides which ladder applies. The thresholds are starting values for the prototype, not tuned.

### 4.3 Timing and hysteresis

- **Down:** at most one step per second, after 1 s of sustained load, or 0.25 s for gross overrun.
- **Up:**
  - one step at a time, in the reverse of the order taken;
  - only when the projected cost at the level above fits under 0.7 of the period;
  - the projection uses that level's last measured cost for this scene, or the calibration;
  - the condition must hold 10 s, or 30 s for the fps step.
- **Backoff:** if a step up is undone within 30 s, that step's hold doubles, up to 2 min. The backoff resets after 10 min clean.
- **Scene change:** the incoming scene starts at the level its calibration predicts will fit (as `fit_scale` does today, sessions.py:37), then climbs. It does not start at full quality and fall.

### 4.4 The steps (degrade, never refuse)

The order depends on which stage is binding:

| Binding stage | Order of steps |
|---|---|
| GPU | G1 → G2 → G3 → G4 → F |
| Host, own CPU | G1 → G3 → H3 → G4 → F |
| Host, blocked (contention outside the session) | G3 → F, plus a "computer busy" report. The report is the action, because nothing inside the session fixes contention |
| Encoder | E1 → E2 → F |
| Delivery | the bitrate axis only |

| Step | Knob | Saves | What the viewer sees | Prerequisite |
|---|---|---|---|---|
| **G1 FX trim** | Lower the FX director's bus budget (FX_BUS_BUDGET_MS 3.0, fx_director.py:47/:252). The costliest effect leaves at its normal fade, and only lighter ones are added. Host-bound sessions are ranked by measured `cpu_ms` (F4) | that effect's bench or host cost | one fewer or lighter overlay; effects come and go anyway | Phase A |
| **G2 render_scale down one step** (for example 0.5 to 0.42) | quality-tier param | about 30% fewer internal pixels, SM roughly ∝ scale² **[I]** | slightly softer scene detail; overlays and HUD stay at full 4K | **[U]** note_fluid sizes its buffers at construction (note_fluid.py:408), so the step is a prepared swap on a worker thread, applied at the next transition or with a short crossfade |
| **G3 scene rate 1/2** | `SourceLayer.rate_div` (layers.py:126-160), now set by the controller | a held frame is one copy (about 0.5 ms GPU, one call) against a full render (about 5 ms P4000, dozens of calls) **[M]** | scene motion at 30 while effects and transitions stay at 60 | B8 |
| **H3 sound view and HUD rates halve** | update rates | about 1-2 ms of host time **[I]** | sound-reactive detail at 30 Hz | Phase A |
| **G4 deeper** | render_scale −2, rate 1/3-1/4 | | softer, steppier scene | |
| **E1 preset p4 to p3** | NVENC reconfigure | about 10-15% of NVENC time **[U]** (only p5 against p4 measured: 10.7 to 9.3 ms) | barely visible | Phase C |
| **E2 spatial AQ off** | `-spatial-aq` | 1.5-3 ms per frame (profiles.py:63) | banding in smooth dark gradients (flat-region PSNR 57 to 49 dB) | Phase C; never while a still is on screen |
| **F output 60 to 30 fps, per screen** | pace period doubled; fragments of 2/60 s; timestamps stay in 60ths, so the timeline stays continuous; beats land within ±16.7 ms, inside the ±50 ms trim | halves every per-frame cost | judder-free 30 fps motion with no freeze and no jump | Phase C; **[U]** Chrome MSE on the kiosk must accept mixed sample durations |
| **Floor** | 30 fps output, rate 1/8 (RATE_MAX_DIV), no FX | — | still plays; slot-level holds absorb the rest; reported red | — |
| Bitrate axis (delivery only) | 50 → 40 → 30 Mbit/s, applied at the next IDR | about 40% of link use | a little more compression in busy scenes | — |

The floor never stops a stream. Nothing below it switches anything off.

Before Phase C, the ladder can use only G1, G2, G3, H3 and G4. The fps "step" is then the profile chosen at session start, as today with 2160p30.

### 4.5 Cut frames and transitions

- **Entry, art to live.** The stream opens on the anchor PNG at full 4K (sessions.py:2810, then renderer.py:310-337), with the master bus closed and so no FX. The controller is frozen from session start until the in transition completes. Ladder steps never apply to StillLayer or anchor frames.
- **Exit, live to art.** The controller is frozen from the start of the exit to the hold frame. No preset, AQ or bitrate step lands inside that window; any active encoder step is restored at the start of the exit.
- **IDR at the hold.** With Phase C the encoder forces an IDR at `hold_pts`. That fixes the known softness of the exit cut: luma mean 3.2 off at `hold_pts`, against 1.1-1.6 at entry (AV_PROGRAMME §4.4).
- **Frozen exits.** These follow the honest GPU signal only (B8).
- **Never touched by the ladder:** output resolution, the init segment and the mask (the mask is Chrome on art only).

### 4.6 Reporting (the cost is shown, never hidden)

- **HUD details line**, for example: `60 fps · scene 30 (GPU 14.2/16.7 ms) · host 6.1+0.8 · enc 61% · why: GPU`.
- **`/api/sessions/{sid}`:** `degrade: {level, steps:[{knob,value}], binding, loads:{host_cpu,host_blocked,gpu,enc,net}, since, recover_hold_s}`.
- **Event:** `session.degrade {session, screen, from, to, binding, cost}`, which also replaces the session-less rate log.
- **Room page screen card:** read-only plain words, no new switch. For example "Smooth, 60", "Smooth, lighter effects", "30 fps: graphics card busy", "30 fps: computer busy", each with its cost ("encoder 61%").
- **Flight recorder:** level and stage loads per second, for before/after comparisons.

### 4.7 Prototype, measure, then talk

1. **Shadow mode for one show.** The controller computes its level and binding stage, logs them, and acts on nothing. Check: on baseline-like load it must say "host" and never "GPU", since lag stayed at or under 2.7 ms on 100% of frames.
2. **Act at 30p with the existing knobs only** (G1, G3, H3). Measure content fps, repeat runs, and rate changes per minute (20-30 today).
3. **After Phase C,** add the encoder steps and the fps step, and move the screens to `hevc-2160p60` in the room config.
4. **Show the owner the thresholds** before any of them hardens into anything resembling a gate.

---

## 5. What still rests on unverified ground

- **All "after the path" budgets** (Phases C, D and E) are inferred from call counts and benches, not measured end to end.
- **PyNvVideoCodec and NVENC under WSL GPU-PV:** whether they release the GIL, their CPU per frame, and the 3-frame delay are all unmeasured here.
- **Two items need checks on the real system:** Chrome MSE accepting mixed sample durations (the 60 to 30 step), and how each plug-in can change render_scale live.
- **The NVENC saving from p4 to p3** is unmeasured. The P4000 at about 83% busy when throttled is derived, not measured.
- **The 4060's real SM share** is unknown, because about 20 points of SM time are unattributed desktop and Brave work.
- **Powerline queueing at 2 × 50 Mbit/s** is modelled only (D11).
- **The clock cap** has an unknown cause. GPU idle down-clocking was seen as a trough only once, in the contaminated window.
- **`setswitchinterval`** has an unknown effect.
- **No live re-measure was possible after 15:06.** Every live figure is from the 14:27 baseline or the 15:01-15:04 window.