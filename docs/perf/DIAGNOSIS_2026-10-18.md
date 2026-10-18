# What stops smooth 4K60 on PC22

_Diagnosis for the 4K60 programme (see docs/perf/PROGRAMME.md when it lands). Raw data was in the investigating session's scratch directory and is not kept in git._

Read-only investigation, 18 Oct 2026.

**Tags:** [M] measured (with its window: W0-W4, see section 1), [I] inferred from code or arithmetic, [U] not verified.

**Line numbers** refer to the code as of 14:19-15:24. Peers have deployed since then (stream_service last at 16:45, show_service at 15:24), so lines may have moved.

**Owners** are named by role. Session codes changed after the VS Code restart on 18 Oct; current names are in brackets where known:
- the stream owner = stream_service;
- the show owner = show_service (screen-machine-e9; the show-rx fix is being built by screen-machine-3a);
- the ops owner = scripts/ctl, Spectre and the Windows rules;
- the display-page owner = the display page (vacant).

**Terms:**
- GIL = Python's one-at-a-time lock, shared by every thread in a process.
- LP = a physical logical CPU, of which the box has 20.
- vCPU = WSL's view of a CPU.
- repeat = the server re-sends the last picture.
- resync = the schedule jumps forward after falling more than 250 ms behind.

## 0. Short answer

- **It is the CPU side, not the GPUs.** The GPUs mostly sit waiting for Python. [M]
- **Two walls:**
  - the Python render thread, which shares one GIL with the other screen and the web loop;
  - the raw-video path into ffmpeg: each 12.4 MB frame is copied three times by the CPU before NVENC sees it.
- **The scene matters a lot:**
  - note-fluid: north showed 19 new frames per second and south 24, out of 30 [M W1];
  - live-score: north showed 7-8 and south 13-14, and the raw-video path itself fell below 30 fps [M W3].
- **The PC is full.**
  - Windows and Hyper-V take about 12 of the 20 LPs at all times, and WSL gets about 6-9.
  - The cores run at about 2.3-2.5 GHz against 3.7 GHz nominal. [M]
- **2x4K60 needs:**
  - encode on the GPU, with no raw video through ffmpeg;
  - one render process per screen;
  - far fewer GPU driver calls per frame;
  - less background load on the box.
  - Then a degrade ladder (effects, then scene rate, then fps) that never stops a stream. [I]

## 1. Measurement windows (read first)

| Window | Time | Process / scene | Used for | Caveats |
|---|---|---|---|---|
| W0 scout | 14:15-14:23 | PID 783125, piano-waterfall-ember | session counters only (repeats 29% north / 22% south over 50k slots) | none |
| **W1 baseline (reference)** | 14:27:07-14:28:37 (90 s) | PID 818235 (a peer restarted it at 14:25:26), note-fluid-ice-silk then note-fluid-dusk-pool, 2160p30 | every frame record, per-thread CPU, GPU and Windows counters | probe cost: 6.9% of a core, plus 2.5% for nvidia-smi |
| W2 | 14:44-14:52 | PID 818235, piano-waterfall then live-score-night | py-spy GIL split, media tap, Hyper-V counters (14:51) | **perturbed:** analysts' pipe, ffmpeg and GPU benches pushed guest busy to 71-80% |
| discarded | 14:54-14:57 | none | none | Flask down, a vite build ran, stream restarted 14:55:37 (new PID 861326) |
| W3 | 14:58-15:04 | PID 861326, live-score (-paper/-night). The scene spawns score.analyse and score.engrave, plus an extra ffmpeg per screen | frame dumps, syscall sampler, py-spy | 3-9 min after a restart; light analyst load: CUDA micro-benches on both GPUs around 15:02, Windows counter batches 15:02-15:03, py-spy at 15:04 |
| W4 | 15:06-16:30 | no screen live | encode-chain, 60p NVENC, pipe and Hyper-V benches | none |

**Later code changes:** live-score changed after W3. It now runs one transcription per room (ace1bda5, 15:04:55), and its lock is no longer held across GPU waits (e252a370). New scope/FFT inputs landed 15:43-16:45. None of these was measured.

**Status at 16:42:**
- stream_service was running (PID 1056960, up since about 16:31) with no ffmpeg children, so **no screen was live**;
- show_service was using 99% of a core;
- stream_service.tools.lookstats had measured 0.2% of a core in W1 and was gone by 16:42.

**Compare within a window, never across:** W1 and W3 differ in scene and in process age.

## 2. The answer

**The GPUs are not the limit.** [M]
- In W1 plus a 14:59 sample (4,661 rendered frames), the GPU finished within 2.7 ms of the host's last enqueue on every frame.
- In W3, 6 of 3,775 frames exceeded 2.7 ms; the worst was 5.9 ms.
- In W1, SM was 23.6% busy on the 4060 and 12.8% on the P4000; NVENC was 43% and 39%. The 4060 reported `gpu_idle` in 44 of 44 samples.
- A 2 KB upload with GPU work queued returned in 0.12 ms, so host time is not hiding GPU waits [M bench, W3].

**First wall: host time in the render thread.**
- In W1 it was p50 19.4 ms (south) and 22.5 ms (north), p95 78 and 85 ms, against a 33.3 ms slot.
- Frames dominated by host time added 80% (north) and 88% (south) of all lateness in W1, but only 49-61% in W3.
- Where the time goes:
  - Own CPU is about 12.5-13 ms per rendered frame, and about 35% of that is kernel time [M W1].
  - Most of the rest is blocked: the render threads sat on the GIL futex 18.6-19.4% of wall time [M, 14:58, W3].
  - py-spy (W2): of 21.3 ms of wall time per frame, the thread held the GIL for 6.6 ms. About 11 ms went in CUDA driver calls, uploads and guard events, mostly without the GIL: either driver time or waiting to retake the GIL [M].

**Second wall: the raw-video encode chain.**
- It costs 35.5-38.1 ms of CPU per frame per screen [M W1].
- Its sustained capacity was 31.8-34.0 fps in W1, and only 27.3-29.1 fps in W3, below 30.
- So lateness drains at only 1-3 ms per slot in W1, and not at all on average in W3:
  - in W1 a host spike becomes a solid 0.3-2.8 s freeze;
  - in W3, 8-17 s of every ~150 s were lost to resyncs.

**What the TVs got** [M]:

| Window | North: frames/s rendered (repeats) | South: frames/s rendered (repeats) |
|---|---|---|
| W1 | 19.3 (34%) | 24.2 (18%) |
| W3 | 7.3-7.6 (72-73%) | 13.4-13.8 (51-53%) |

"Rendered" is an upper bound on new pictures: frames held by the scene rate divider still count as rendered.

**The box** [M]:
- The Windows side (Hyper-V's root partition) ran on about 12 LPs in every sample, screens on or off.
- WSL got 8.0 LPs at 14:51 with the screens live, and 6.1 (range 2.9-8.9) with the screens off.
- LP idle was 0.03% at 14:51-14:58, and 9% with the screens off.
- Measured at the same time (screens off), 9-12% of the guest's "busy" time is hidden waiting for a physical CPU. The guest never sees this as steal.
- Clock speed:
  - Windows shows "% processor performance" at 62-67%, which is about 2.3-2.5 GHz.
  - A timed multiply loop in the guest gave a p50 of 2.0-2.3 GHz.
  - No limit flags were set.
  - It was slightly higher with less load: 68.9% vs 65.3%.
  - The cause is unknown.

**2x4K60:**
- Two encode chains with no rendering at all reach only 50-52 fps; one chain alone reaches 60.1 [M W4].
- NVENC at 60p (preset p4, `-delay 2`) is 56% busy on the 4060 and 60% on the P4000, or about 83% if the P4000 drops to its throttled video clock [M W4 + I].
- What it takes: GPU-resident encode, one render process per screen, and a CUDA-call diet. Then 60p is feasible for typical scenes [I]. Live-score is not assessed until it is re-measured after ace1bda5.

## 3. Where each frame's time goes (ms per frame, per screen)

The stages are pipelined, so the slowest stage sets throughput, not the sum.

| Stage | North (4060), W1 | South (P4000), W1 | W3 live-score (north / south) | 2x4K60 on today's code | 2x4K60 after the path [I] |
|---|---|---|---|---|---|
| Slot budget | 33.3 | 33.3 | 33.3 | 16.7 | 16.7 |
| Render host_ms p50 / p95 | 22.5 / 85 | 19.4 / 78 | 24-26 / 115-124 and 19.4 / 88-96 | over 16.7 on 57-61% of frames [M W1] | 6-9 / 14 or less |
| - own CPU | about 13 | about 13 | n/m | same | 5-7 |
| - blocked (mostly GIL, some driver) | about 10 typical, more in bursts | about 7 typical | n/m | GIL demand about 105-109% of one GIL, so it **cannot fit** [I] | 1-2 |
| GPU kernels | about 3.3 (weak fit, r2 = 0.39) [I] | about 2.8-5 [I] | n/m | heavy presets 12-16 on the P4000 | same |
| GPU behind host, max | 2.7 | 1.9 | 4.0 / 5.9 | n/a | n/a |
| D2H copy p50 | 2.3 | 1.3 | n/m | same | 0 |
| Writer and pipe (write_ms p50 / p95) | 24.5 / 56 | 22.5 / 51 | 31-32 / 62 and 29 / 59 | 17-18 p50 per chain (bench), **cap 50-52 fps** | 0 |
| ffmpeg upload, then NVENC wait (chain service p50) | 28.5 | 26.2 | n/m | n/a (60p uses p4, `-delay 2`) | 0 |
| NVENC engine | 14.6 (p5) | 13.3 (p5) | n/m | 9.3 (56% busy) / 10-11 (60%; about 83% throttled) | same |
| Encode-chain CPU | 38.1 | 35.5 | n/m | 28.7-29.8 per chain (bench, no render) | about 1-1.5 [U] |
| Sustained chain capacity | 31.8 fps | 34.0 fps | 27.3 / 29.1 fps | n/a | n/a |
| Slot to packet out, p50 | 175 | 129 | n/m | n/a | 35-55 |
| Repeats / rendered fps | 34% / 19.3 | 18% / 24.2 | 72-73% / 7.3-7.6 and 51-53% / 13.4-13.8 | n/a | n/a |
| AV CPU, both screens (stream, ffmpeg, show, Flask) | about 4.1-4.5 cores | shared | n/m (live-score children not counted) | 6.7 cores or more against about 6-9 LPs: **does not fit** [I] | about 2-2.5 cores |

**Notes on the table:**
- n/m = not measured.
- Encode-chain CPU depends on load: 16-17 ms per frame for one chain alone, 26 ms with two chains together, and 35.5-38.1 ms under W1's production load.
- W1 values come from baseline/numbers.json and frame-loop/frameloop2_out.txt.
- W3 values come from gil-profiler/cs_* and r2_* (the attribution was re-run in critic/lateness_any.py).
- Bench values come from h3-remeasure/ and h7-remeasure/.

## 4. Ranked bottlenecks

Two adversarial verifiers checked each hypothesis. All seven came back partly verified; the corrections are folded in below.

### 1. Render-thread host cost and the shared GIL (H1)

**What holds:**
- Host-dominated slots add 80-88% of lateness in W1, and 49-61% in W3. [M]
- 45 of 56 spikes over 100 ms on north (80%) fell within 0.25 s of a south spike; chance would give about 23%. [M W1]
- Python Main thread (W2): it holds the GIL 29.9% of the time. 37.8% of that is session-summary GETs, which re-sort 4,096-sample rings and cost 18.3 ms of GIL per call (`stats.py:9-30`). They are polled about once a second per screen from 172.28.240.1, the Room or console pages in PC22's Brave. [M]

**Corrected:**
- The GIL is about half of host time (39-59%). The remaining 12.5-13 ms of own CPU is 75-80% of a 60 fps frame by itself.
- Worker bursts (W1, verify-h1/burst_split_out.txt):
  - Burst seconds cover 10% of the time and add 17-20% of lateness. Windows of +-1 s around them cover 22% of the time and add 32-35%.
  - host_ms is 1.4-1.5x higher at p50 and 1.6-1.7x at p95 inside those windows.
  - The ~21 s periodic bursts (8, 29, 50 and 72 s) are the plug-in rescan (`plugins/__init__.py:116-203`) plus the library index rebuild (`library.py:258-271`). Others (53, 66 and 73 s) coincide with scene and effect prepares.
- Steady 33-100 ms frames still add more lateness than the big spikes.

**Fixes:** see section 8, items B1-B5 and B8, then phases P3 and P4.

### 2. Encode chain (H3)

**What holds:**
- Per-slot catch-up equals the period minus the writer gap. [M W1]
- A repeat costs a full 12.4 MB write.
- The chain costs 35.5-38.1 ms of CPU per frame at W1 load.

**Corrected:**
- It is two pipelined stages:
  - A: writer, then pipe, then demux;
  - B: ffmpeg's main thread, which uploads and then waits on NVENC.
  - They run in series because hevc-2160p30 uses `-delay 0` (`stream_service/profiles.py:81`). Their p50 service times are about equal at 30p (writer 26-29 ms, ffmpeg 26-28.5 ms).
- In W3 the chain ran below real time (27.3-29.1 fps mean). That makes it a co-equal first wall on live-score. [M]
- One 4K60 chain fits (60.1 fps); two reach 50-52 fps even with no rendering [M W4].

**Fixes:**
- B6: `nvenc_delay=1` on 30p (+33 ms of latency).
- B9: vmsplice from the pinned ring. In the bench (76-80% guest busy, W2), write p50 fell from 25-45 ms to 12.5-18.5 ms and throughput rose from 21-31 fps to 50-60 fps. [M bench]
- P3: GPU-resident encode, required for 60.

### 3. The renderer's own degradation logic (H2): it amplifies the problem but does not cause it

**What holds:**
- The repeat rule (`renderer.py:463-474`) predicts 99.96-100% of repeats.
- Every "GPU ms" figure (API, HUD, guard, rate log) is really host time: 8-107 ms logged against a calibrated 0.7-3.2 ms.
- The scene rate divider changed 513 times in 25 min, and 44 times in W1's 90 s.

**Reporting bugs:**
- The HUD guard line reads `ema_ms`/`frame_ms`, but the guard exposes `frame_gpu_ms` (`hud.py:269` vs `guard.py:294`), so the line is always "-".
- host_ms is stored as 0.0 on repeats, so diag's p50 reads 9.5 ms on north instead of 22.5.
- diag blames "render over budget" (`diag.py:91-92`).

**Refuted:** "render late instead of repeating". In replay that gives 5-34x more resyncs.

**The real flaws:**
- A repeat costs a full write, so a run of repeats shows as a solid freeze. In replay, one probe frame after 3 repeats removes every run of 10 or more slots, worth +1.5-2.6 fps on north.
- The rate divider demotes scenes for host and GIL stalls it cannot see.
- Each resync adds about 290 ms of wall-clock lag; the media timestamps themselves stay contiguous.

### 4. Host box (H4): a large, mostly constant tax

**What holds:**
- Windows takes about 12 LPs in every sample.
- LP idle was about 0 with the screens live (14:51-14:58), and 9% with them off.
- 9-12% of guest busy time is hidden wait for a physical CPU.
- The clock sits at 62-67% of nominal with no limit flags.

**Refuted:** "1.5-2x on every CPU stage".
- Render host time shows no sensitivity to box load (r = 0.13 / -0.04, W1). Only the pipe copies scale.
- A synthetic pipe write took 9.1 ms at 31% guest busy, 17-17.6 ms at 54-61%, and 40 ms at 76% (W2, perturbed).
- The hidden wait tracks the guest's own demand (r = 0.86), not Windows load (r = -0.03).
- The README benchmark was not like for like: it used preset p1 with `-delay 2`, against p5 with spatial AQ and `-delay 0` now.

**Fixes:** keep guest demand under the LPs WSL gets (GPU encode is the big cut; then cap background work), and find the clock cap.

### 5. Glass stalls (H5)

**What holds:**
- Upstream timing sets stall time.
- The two screens add up to 57.0 Mbit/s on a ~180 Mbit/s powerline.
- The 127.0.0.1 "kiosk" client is Brave on PC22.

**Corrected:**
- The upstream model predicts 0.4-1.3x the observed stall count, depending on an unmeasured player parameter, so stall attribution is unresolved.
- 10-24% of counted stalls are the player's own seeks.
- Server drop-to-key holes are not counted at all.
- Brave's path runs through Caddy, then portproxy, then the Flask proxy, so it is not a clean control.
- Fan-out egress delay after packet parse (W2): p50 2.5-6 ms, p95 34-41 ms, p99 74-101 ms, max 135-277 ms. [M]

**Bigger point:** server repeats freeze 18-73% of slots, and the client counters never see them. Client stalls freeze only 1.5-2.6% of the time.

### 6. show_service input backlog (H6): separate from the frame rate, and present with the screens off

**What holds:**
- 14:47-15:04: VBAN socket Recv-Q 297-376 KB and 153-168 drops/s [M].
- Reported by the H6 verifier at 16:20 (not re-checked): 327-394 KB, about 95 drops/s, and 125,985 drops since 15:43.
- My read at 16:46: Recv-Q 0 then 256 KB, no new drops in 5 s, 201,446 drops cumulative [M].
- Also reported by the lens: 23-37% of mic audio lost, about 0.7-1.4 s of hidden queue, and show-rx capped at about 80-84% of a core whenever madmom runs.
- The beat thread did not fix it, because it adds no GIL capacity.

**Fixes (the show owner):**
- Bound the socket buffer to about 0.25 s of audio (now at `receiver.py:127`). This bounds latency; it does not gate anything.
- Re-check gap handling against current `clock.py`: its header (lines 58-59) says long zero-filled gaps already compute nothing.
- Move madmom into a child process.
- Then split show_rt from show_brain.

### 7. GPUs (H7): not today's limit

**What holds:** the GPUs account for about 0% of today's shortfall.

**Corrected:**
- At 60p, NVENC is 56% busy on the 4060 and 60% on the P4000, or about 83% if the P4000 throttles.
- The P4000 runs at 82 C. `sw_thermal` showed in 6 of 44 samples, with SM clock dropping to 1,407 from 1,553 MHz, but encode time did not get worse in those seconds.
- Heavy presets (seahorse 12 ms, nebula aa=2 15.9 ms on the P4000) do not fit at 60.
- Windows reports the 4060's 3D engine 53% busy, against nvidia-smi's SM 24%. The desktop and Brave share that card, so its real headroom is unknown.

## 5. Other bottleneck types checked

| Type | Finding | Verdict |
|---|---|---|
| WDDM / GPU paravirtualisation | Each CUDA call goes to Windows. Per-call p50: event record 17-18 us, launch 45-49 us, small op 68-73 us, 2 KB upload 55-59 us. p99 0.1-9 ms, and 6-21 ms with one competing Python thread. Render-thread CPU is 34-35% kernel time; Windows privileged time is 65-72% [M W1/W3 bench] | **A real part of host cost** [I]. Cut call count (CUDA graphs, batching) |
| Memory bandwidth | 99.5 MB of DRAM traffic per frame: about 6 GB/s today, about 12 GB/s at 2x60, against about 94 GB/s theoretical [I] | Not a limit |
| Page faults / zero-fill | ffmpeg's demuxer takes 108-539 minor faults per frame. A 12.4 MB copy costs 4.9-19 ms into fresh pages, 1.9-2.6 ms into reused ones [M] | Part of the demux cost; fixed by GPU encode |
| PCIe | D2H runs at 5.5 GB/s on the 4060 (gen3 x8) and 9.4 GB/s on the P4000 (x16). Load is 0.75 GB/s per GPU today, about 1.5 GB/s at 2x60 [M/I] | Not a limit; copy outliers follow host spikes |
| Pipe | see section 4.2 | Wall 2 |
| GIL | see section 4.1 | Wall 1 |
| asyncio loop | Main thread holds the GIL 29.9% of the time; egress p99 74-101 ms, max 277 ms [M W2] | Adds glass jitter; fix the summaries first |
| show_service | see section 4.6 | Input latency, not fps |
| Kiosk decode | Kiosk 74% idle at 14:47 (sway 18%, each Chrome 9-18%) [M W2]. 4K60 to both TVs was proven in the 13 Oct lab (`profiles.py:73-75`) | Not a limit at 30p; check at 60p |
| Network | Simulation: queue p95 20 ms at 30p on 180 Mbit/s. At 2x60 with 50 Mbit/s paced: p95 25 ms on 180 Mbit/s, 160 ms on 120 Mbit/s [I] | Fine now; a powerline dip becomes a risk at 60p |
| GPU thermal | see section 4.7 | Clean the P4000 cooler |
| CPU clock / thermal | 62-67% of nominal; CPU temperatures never read | Open: biggest unknown lever |
| Windows memory | Commit 180.6 of 195 GB (93%); 7.6-7.9 GB free of 63.7; 750-2,400 pages/s; Brave 53.6 GB private; vmmemWSL working set 18.2 GB [M 14:51-15:02] | Risk of host paging stalls [U impact] |
| Wake-up latency | 1 kHz probe, screens live: p50 67 us, p99 6.0 ms, p99.9 15 ms, max 46 ms. Screens off: p99 0.3 ms, max 45 ms. Pacing sleep woke p99 9.6-12.7 ms late (W1). Kernel HZ=100, PREEMPT_NONE [M] | Real-time jitter floor: up to 1/3 of a 60p slot |
| live-score child processes | score.analyse, score.engrave and an extra ffmpeg per screen, never measured | Measure after ace1bda5 |
| Python GC, lookstats | GC not measured; lookstats 0.2% of a core | lookstats is not a factor |

## 6. The measurement tool to build

The tool must answer two things:
- per frame: "why was this frame late";
- per second: "CPU or GPU, and which stage".

It never gates anything; when it goes over its own budget it samples less often. Budget: 0.3 ms per frame or less (per-thread CPU reads cost 2-3 us).

It has three parts:
- **Frame trace, in-process:**
  - a ring per session on `pace_clock`, keyed by the slot number, which is also the frame id and media timestamp;
  - it splits each stage into own CPU, blocked and run-queue time;
  - it records rate_div and held frames;
  - it adds markers for rescans, prepares, GC, HTTP routes and loop lag.
- **Box probe, a child of stream_service** with its own GIL (it needs its own Spectre card if it becomes a separate process). It records:
  - per-thread CPU, including ffmpeg's threads;
  - NVML clocks, enc% and throttle reasons;
  - the show socket backlog;
  - wake latency at low duty cycle;
  - live-score's child processes.
- **Windows host card in Spectre (the ops owner):** LP idle, root vs WSL run time, % performance, queue length, available memory and commit. It must sample cheaply: the lenses' WMI batches cost wmiprvse up to 65% of a core.

| Step | What | Owner, effort | What you can then answer |
|---|---|---|---|
| T1 | Honest numbers: host_ms None on repeats; slip recorded before a resync; HUD key `frame_gpu_ms`; "render" becomes "GPU span"; the rate log names the session; nvidia-ml-py instead of forking nvidia-smi; thread names | the stream owner, 2-3 h | Correct p50s, a working guard line, true glass fps |
| T2 | Frame trace, attribution, `/api/perf` (cached), late-frame dossiers, 3 HUD lines | the stream owner, 0.5-1 day | "CPU or GPU", continuously |
| T3 | Fan-out send times, plus frame-id gaps from the page | the stream owner + the display-page owner, 0.5 day | Each TV stall classed as upstream, network, seek, decode or drop |
| T4 | Box probe, Spectre host card, 1 Hz flight recorder kept 3 days | the stream owner + the ops owner, 1 day | Live box saturation and clock; replay after a restart |
| T5 | Guard host/CPU split, a live cost table, and periodic probe frames that time pure GPU cost (the `%globaltimer` kernel clock is [U] under WSL) | the stream owner, 1 day | True GPU cost per scene and effect |
| T6 | On request only: ffmpeg per-frame stats; py-spy snapshot | the stream owner, 0.5 day | Encoder split; who holds the GIL at a stall |
| T7 | Same views once GPU-resident encode lands | the stream owner | Same answers without the pipe |

## 7. Protecting real-time cycles

**Guest priority cannot buy much:**
- Render threads wait in the guest run queue only 0.17-0.30% of the time [M W1].
- The real losses are the GIL, the threads' own CPU, the copy chain, driver calls, and the hidden LP wait (9-12% of guest busy).
- Priority is not available anyway: rtprio is 0, there is no sudo, and init.scope's RT budget is 0.
- Systemd CPU weights would need the AV processes moved into units, which needs root [U].

So protection comes from structure, and from keeping total demand under the LPs WSL actually gets.

| Lever | Available? | Expected gain | Owner OK needed? |
|---|---|---|---|
| One render process per screen | Code (the stream owner) | Removes the GIL convoy and the coupling between screens | Already ruled |
| GPU-resident encode | Code (the stream owner) | -2.2 cores today; -4.3 to -4.6 cores at 2x60 | No |
| Cheap summaries, rescan off the GIL, guard trim, batched uploads, fewer CUDA calls | Code (the stream owner) | See B1-B5 | No |
| show_service: madmom child process, socket bound, process split | Code (the show owner) | Input delay from about 1 s to 0.25 s or less | No |
| Cap agents' background jobs to vCPUs 16-19 with `nice 19` / `chrt -i 0` | Yes, unprivileged | Holds guest demand at about 9-10 vCPUs or less | **Yes** (slows agents, not AV); CLAUDE.md change is the owner's |
| Stop stray load: vites on :5188/:5189 (other sessions'; their owners stop them); dagnet dev-server's forced polling (0.8 core; inotify works on ext4); Pylance indexing | Owners | About 1.5-2 cores | **Yes** for dagnet and VS Code |
| Windows: close Brave's live tabs (about 2.7 cores vs 0.9-1.1, and 17.7% of the 4060's video decoder); built Spectre UI instead of its dev server (0.6-1.0 core); Carbonite outside show hours | Owner / Spectre | Up to 3-5 LPs | **Yes** |
| Find the clock cap: owner reads sensors (e.g. HWiNFO, read-only), or the ops owner adds a read-only Spectre script. No tuning tools; BIOS power limits are the owner's | Read-only first | Up to 1.4-1.6x on every CPU stage, if fixable [U] | **Yes** |
| vmmemWSL priority | Read via Spectre first | Possibly the host-side wake tails [U] | **Yes** |
| `.wslconfig pageReporting=false` | One WSL restart, which takes down Flask/room, stream and show together; via Spectre, after telling every session | Candidate fix for demux page faults [U] | **Yes** |

**Don't:**
- use SCHED_FIFO (a spinning Python RT thread breaks the bounded-job rule);
- nice individual threads inside a GIL-shared process (priority inversion);
- pin one AV thread to one vCPU;
- use isolcpus;
- raise `fs.pipe-max-size`.

## 8. Path to 4K60 and the degradation ladder

**The path.** Each phase is prototyped, measured and then kept. No phase adds a gate.

| Phase | What | Done when |
|---|---|---|
| P1 | Honest numbers (T1, plus the CPU/blocked split) | Fields present; cost under 0.25% |
| P2 | Cheap 30p fixes, B1-B9 below | Rendered fps 28 or more on both screens on note-fluid; repeats under 5%; no freezes of 10 slots or more; live-score re-measured [I] |
| P3 | **GPU-resident encode.** First a bounded PyNvVideoCodec bench, run while no screen is live on that GPU (not a side service); then in-process NVENC on CuPy NV12 surfaces as a new encode profile, with the ffmpeg profile kept as fallback | Encode CPU about 1 ms per frame; repeats nearly free; slot to packet 35-55 ms |
| P4 | One render process per screen | GIL futex under 3%; spikes no longer coincide across screens |
| P5 | Host diet: CUDA graphs, fused Jacobi solver, one SoundView per room | Own CPU 7 ms or less; 5-15 CUDA calls per frame |
| P6 | Box: background cap, stray load, Brave tabs, clock cap, P4000 cooling | Guest busy about 9 vCPUs or less during a show |

**The B items in P2:**
- B1: cache the summary: sort once, cache 1 s (`stats.py:9-30`, `renderer.py:917-930`).
- B2: rescan and library index off the GIL: incremental rescan, index in a child process (`plugins/__init__.py:116-203`, `library.py:258-271`).
- B3: sleep to the deadline instead of spinning on `sleep(0)` (`renderer.py:434-438`).
- B4: time guard plug-ins round-robin (`guard.py:163-193`), keeping a per-frame begin/end pair so the 3 s hang stop still covers every frame.
- B5: batch the per-frame uploads (`sound.py:62-73`, effect params).
- B6: `nvenc_delay=1` for hevc-2160p30 (`profiles.py:81`; +33 ms).
- B7: a probe frame after 3 repeats (`renderer.py:463-474`).
- B8: drive rate_div from own CPU plus true GPU lag (`layers.py:136-160`).
- B9: vmsplice from the pinned ring (`encoder.py:89-101`).

**Then move to hevc-2160p60.** Verdict: feasible for typical scenes after P3, P4 and P5 [I]. Live-score is not assessed. Heavy presets on the P4000, a throttled P4000 encoder, and host bursts still need the ladder.

**The ladder.** One controller per session replaces today's three lock-in-prone signals (the repeat EMA, rate_div and over_budget).
- **Signals:** it decides at 1 Hz from own CPU, blocked time, GPU lag, encoder busy and delivery.
- **Step order** depends on which stage is binding:
  - GPU: trim effects, then render scale down one step, then scene rate 1/2, then deeper.
  - Host, own CPU: trim effects, then scene rate 1/2, then halve the sound and HUD rates.
  - Host, blocked: scene rate 1/2, then fps, plus a "computer busy" report.
  - Encoder (after P3): preset p4 to p3, then spatial AQ off, then fps.
  - fps step (after P3): 60 to 30 per screen, with fragments twice as long; no freeze and no timestamp jump.
- **Floor:** 30 fps, scene rate 1/8, no effects. It never stops a stream.
- **Hysteresis:**
  - down: at most one step per second, after 1 s of sustained load;
  - up: one step at a time after 10 s (30 s for the fps step), only if the projected cost fits under 0.7 of the period;
  - the wait before stepping up doubles after a failed step up.
- **Never touched:** output resolution, the init segment and the mask. The controller is frozen across the art-to-live entry and the exit hold, and an IDR is forced at `hold_pts`.
- **Reported** on the HUD, in `/api/sessions` (`degrade{}`), as a `session.degrade` event, and as plain words on the Room screen card ("30 fps: computer busy").
- **Rollout:**
  1. Shadow mode for one show. It must say "host", not "GPU", on today's load.
  2. Act at 30p with the existing knobs only.
  3. After P3, add the encoder and fps steps.
  4. Review thresholds with the owner before they harden.

## 9. Next steps

1. **The stream owner, about 3 h:** T1 plus B1. Fixes the misleading HUD and diag numbers and removes the main thread's biggest GIL hold (18.3 ms per summary call).
2. **The stream owner, hours:** B2, B3 and B7. B7 is worth +1.5-2.6 fps on north in replay.
3. **The stream owner, one line plus one batched restart (only with no screen live):** B6.
4. **04:** re-measure live-score after ace1bda5. Count its child processes; test B9 vmsplice.
5. **The show owner, hours:** bound the VBAN buffer; check gap handling; madmom in a child process.
6. **The ops owner plus the owner:** a `scripts/bg` affinity and nice wrapper for agent jobs, plus a CLAUDE.md rule. The owning sessions stop the stray vites; the owner decides on dagnet's polling and Pylance.
7. **The stream owner, 0.5-1 day:** T2 frame trace and attribution.
8. **The stream owner, 1-2 days:** B4, B5 and B8.
9. **The stream owner, 0.5-1 day then 2-3 days:** P3 GPU-resident encode, the one structural must for 60.
10. **The ops owner + the stream owner, 1 day:** T4 box probe, Spectre host card and flight recorder.
11. **The stream owner + a new display-page owner, 0.5 day:** T3 glass join. A player rebuffer only if the owner wants one.
12. **The stream owner, 1-2 weeks:** P4, one render process per screen.
13. **The stream owner, days:** P5 host diet.
14. **04:** the degradation controller: shadow mode, then act at 30p, then the 60p steps.
15. **Owner:** clock-cap sensor read; clean the P4000 cooler; Brave tabs during shows; Windows memory (commit at 93%); an optional GPU clock-hold A/B through Spectre.

## 10. Open questions

- Why do the cores run at 62-67% of nominal? What are the CPU temperatures?
- How much host cost goes to GPU paravirtualisation per CUDA call, and how much do CUDA graphs save?
- Does NVENC with CUDA input (PyNvVideoCodec) work under WSL2's GPU paravirtualisation? Does it release the GIL, and what is its delay?
- What does live-score cost after ace1bda5 and the scope/FFT inputs? Is its encode chain still below 30 fps?
- On note-fluid, is "blocked" really the GIL? That was measured only on piano-waterfall and live-score.
- What is the 4060's real SM headroom, with the desktop and Brave sharing it?
- Does Windows' 93% memory commit cause stalls?
- Does the kiosk's Chrome MSE accept mixed sample durations (needed for the 60-to-30 step)?
- Does `%globaltimer` work under WSL2?
- Can vmmemWSL's priority be raised, and which Hyper-V scheduler is in use?
- What causes the uncounted drop-to-key holes, and the 0.2-0.5 stalls/s floor on the TVs?
- Does the 4060's 210-240 MHz clock trough happen outside the contaminated 14:54 window?
- **Owner:**
  - OK to cap agents' background jobs to vCPUs 16-19?
  - OK to close Brave's live tabs on PC22 during shows?
  - Do you want the player rebuffer (fewer but longer holds)?

Raw data and scripts are in `/tmp/claude-1000/-home-gjbm2-dev-screen-machine/49a9e605-47c6-4ba4-98e3-fd28d61d6fec/scratchpad/perf/`. The live-score lateness re-analysis is `/tmp/claude-1000/-home-gjbm2-dev-screen-machine/49a9e605-47c6-4ba4-98e3-fd28d61d6fec/scratchpad/perf/critic/lateness_any.py`.
