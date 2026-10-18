# Design 2: protecting real-time processing cycles on PC22

The screens were not live while I checked: `GET :8702/api/sessions` returned `[]` at 16:1x. So this design takes no new renderer numbers. It uses the verified hypotheses and the 14:27 baseline (piano-waterfall / note_fluid, 2160p30), plus the read-only lever checks I made from 16:05 to 16:21. Tags: **M** = measured (source given), **I** = inferred from code or mechanism, **U** = unverified.

## 0. Which levers exist on this box (checked today, read-only)

| Lever | State today | Evidence |
|---|---|---|
| Raise priority (nice below 0, SCHED_FIFO/RR) | **Not available.** `RLIMIT_NICE=0`, `RLIMIT_RTPRIO=0`, sudo asks for a password | `/proc/self/limits`, `sudo -n true` |
| RT budget | Root has 950000/1000000 µs, but `init.scope cpu.rt_runtime_us = 0` and the kernel has `CONFIG_RT_GROUP_SCHED=y`. So even root cannot run SCHED_FIFO in the cgroup the AV stack lives in without first giving that cgroup an RT budget. | `/sys/fs/cgroup/cpu/init.scope/*`, `/proc/config.gz` |
| Kernel | 5.15.167.4-microsoft, `PREEMPT_NONE`, `HZ=100`, no `NO_HZ_FULL`, no autogroup | `/proc/config.gz` |
| cgroup layout | **Hybrid.** The v1 `cpu` and `cpuset` controllers are owned by root. The unified v2 tree is mounted but has **no controllers**. `user@1000` has `Delegate=yes (cpu memory pids)`, but in hybrid mode there is nothing to delegate. So `systemd-run --user -p CPUWeight=…` does **nothing** today. There is no `.wslconfig`. | `mount`, `cgroup.controllers` empty, `systemctl show user@1000` |
| Where the processes sit | All in v1 `cpu:/init.scope` at nice 0: stream_service 960569, show_service 959806, Flask 863239, every Claude/VS Code process, Pylance and vite :5189. In `cpu:/user.slice/user-1000.slice`: dev-server.py 2515, the dagnet vite 3017 and Playwright Chromium 863802. Both groups have `cpu.shares` 1024. | `/proc/<pid>/cgroup`, `ps -o ni,cls` |
| Consequence for nice | nice only ranks tasks **inside** one group. If dev-server is the only runnable task of user.slice on a vCPU, it gets that group's full 1024 share there whatever its nice value (I, from how CFS group scheduling works). Niceing agent jobs does work against AV, because both sit in init.scope. | as above |
| Affinity | Allowed unprivileged on processes with the same UID. Everything currently runs on 0-19. There is precedent: `Analyser` uses `pin_last=4` plus nice 19 (stream_service/score/client.py:40-62, :175-176), and the gallery decode worker nices itself (stream_service/plugins/sources/gallery.py:100-103). `Engraver` uses the defaults (nice 0, unpinned; client.py:155). vCPU siblings are paired (0-1 … 18-19). | `taskset -p`, sysfs topology |
| Thread names | OS `comm` is "python" for every thread (Python 3.12 does not set pthread names). Per-thread tools therefore have to work from `threading.get_native_id()` inside the process. | `/proc/<pid>/task/*/comm` |
| Windows | Power plan Balanced; maximum processor state is 100% on AC, so the plan does **not** cause the 62-67% clock. vmmemWSL PriorityClass cannot be read from a non-elevated shell (protected process). The Hyper-V scheduler-type event was not found, so the scheduler type is **U**. | `powercfg /q`, `Get-Process` at Idle priority |

**show_service right now** (M, 16:20, screens off, PID 959806, `SHOW_BEAT_THREAD=on` via deploy/sm-show.env:23):
- Over a 3 s /proc delta, the threads used: show-rx 47% of a core, show-beat 41%, show-bus 20%, show-brain 9%. That is about 1.17 cores in one process and one GIL.
- The :6980 socket's Recv-Q sat at 327-394 KB (about 416 KB cap) for about 4 s. Kernel drops rose about 95/s, then the queue drained to 2.5 KB. Drops total 125,985 since the 15:43 start. beat_thread lag p95 was 204 ms.
- Box at the time: load 13.6. tsc at 173% (nice 19), Pylance at 100% (nice 0, init.scope, 40 min after the VS Code restart), dev-server at 100%, an analysis python at 100%, pytest at nice 15.
- So the beat thread has **not** removed the backlog, which matches H6 verdict 2: it adds no GIL capacity.
- Raw thread deltas: /tmp/claude-1000/-home-gjbm2-dev-screen-machine/49a9e605-47c6-4ba4-98e3-fd28d61d6fec/scratchpad/perf/d2_show_threads.txt

## 1. What protection can and cannot buy

1. **Guest priority has almost nothing to recover.** Render threads wait in the in-guest run queue only 0.17-0.30% of wall (M, baseline). nice, FIFO or cgroup weight can win back at most that.
2. **The losses are elsewhere:**
   - GIL convoy: about 40-50% of today's lateness (H1 verdicts).
   - The render thread's own on-CPU work: 11-13 ms per frame (M).
   - Pipe and encode copies: 35.5-38.1 ms of CPU per frame per screen (M).
   - Host time-slicing of vCPUs once guest demand passes WSL's share. The root partition takes a near-constant 12 LPs, leaving WSL about 8 (M).
3. **The guest's own demand drives that stress, not Windows apps.** Per second, the hidden share correlates with guest busy at r=0.86 and with root time at r=-0.03. Host wake tails correlate with guest busy at r=0.70 (M, H4 verdict 1). At baseline the guest was busy 7.8 vCPUs, about 97% of its budget, while it looked "61% idle".
4. When guest demand exceeds the budget, Hyper-V slices vCPUs without knowing what runs on them. A vCPU running a render thread is as likely to be held off as one running tsc (I). **Protection therefore means three things, in this order:**
   - (a) get the hot path off the shared GIL;
   - (b) take CPU work out of the hot path;
   - (c) keep total guest demand under about 8 LPs by capping background work, and enlarge that budget from Windows.
   Priority comes last, and most of it is unavailable anyway.

### CPU budget, two screens (cores)

| Component | 2×4K30 today (M) | 2×4K60, today's path (I from M) | 2×4K60, target (I) |
|---|---|---|---|
| Pipe and encode chain | 2.1-2.2 | 4.3-4.6 (H4 verdict 1) | about 0.1-0.2 (GPU-resident; **U**) |
| Render threads, on-CPU | 0.5-0.6 | 1.3-1.6 (11-13 ms × 60 × 2) | 0.7-1.0 (6-8 ms, fewer CUDA calls; **U**) |
| stream_service main loop | about 0.3 | about 0.3 | about 0.3 (cheap summaries) |
| show_service | 1.0-1.2 | 1.0-1.2 | 1.0-1.2, split across processes |
| Flask / room | about 0.2 | about 0.2 | about 0.2 |
| **AV total** | **about 4.1-4.5** | **about 7.1-7.9, the whole WSL budget** | **about 2.3-2.9** |

GIL demand at 2×60 in **one** process: 2 × 60 × 6.6 ms + the loop's 0.26-0.30 = 1.05-1.09 GIL-seconds per second, which is more than 100% (H1). So per-screen processes are required, not optional.

## 2. Process and thread architecture (the main lever)

### 2.1 One render process per screen (stream_service; owner 04)
- **The child owns:** the render thread, writer, nvenc-read, the ffmpeg child or native encoder, the GPU guard and its own CUDA context (one GPU each).
- **The parent keeps:** aiohttp, the buses, room following and the director. It also keeps media WS fan-out, or the child serves the media WS itself behind `:8700/renderer/*`.
- **IPC:**
  - commands go over a pipe;
  - telemetry goes into a shared-memory ring;
  - fMP4 fragments go over a Unix socket;
  - each child subscribes to `:8701` and `:8703` directly, or reads latest-value shared memory.
- **Fits the owner's 18 Oct ruling:** isolated renderer processes, cold start acceptable, roughly 30 s idle linger.
- **Gain:**
  - Removes most of the convoy. With two CUDA threads in one process each call costs 2.1x (91 to 200 µs, M); across processes it is 1.0-1.4x.
  - Removes the cross-screen coupling: 80% of over-100 ms spikes coincide across screens, against a 24% null (M).
  - Takes the worker-burst share (26-29% of lateness in 10% of the time, M) off the render thread's GIL.
  - Expected host_ms p50 falls from 19-22 ms toward the 11-13 ms on-CPU floor (**I**). That is still 70-80% of a 60 fps frame, hence 2.2.
- **Effort and risk:** 1-2 weeks; medium-high (session lifecycle, room following).
- **Verify** with perf/verify-h1/capture_h1.py (20 s, nice 19):
  - render-thread GIL-futex share goes from 18-19% of wall to under 3%;
  - cross-screen spike coincidence falls to the null;
  - host_ms p50 is 13 ms or less on note_fluid.
- **Approval:** the direction is already ruled by the owner. 04 owns the code.

### 2.2 Interim fixes inside today's process (days; owner 04)

| Change | Measured basis | Expected gain |
|---|---|---|
| Engine.summary: one sort per ring, a 1 s cache, a 10 s window (stats.py:9-30, renderer.py:917-930) | 18.3 ms of GIL per call; 38% of MainThread's GIL time | MainThread GIL 26-30% down to about 15-18% |
| Incremental 20 s rescan (mtime check) and library index / preset signature in a subprocess (plugins/__init__.py:116-170, presets.py:227-236, library.py:266) | Burst seconds: host 54-60 ms against 22-25 ms calm | Removes about 27% of lateness |
| Replace the `sleep(0)` spin (renderer.py:433-438) with a sleep to the deadline | 2-4% of wall in GIL re-takes outside host_ms | Fewer handoffs; wake slip p95 under 0.5 ms (**I**) |
| Guard: frame-level events every frame, per-plug-in events round-robin (guard.py:163-193) | 3.0 ms per frame in guard records | About 2.5 ms per frame of host time back; hang stop unchanged |
| Batched pinned upload for .set() (sound.py:69/:72, note_fluid U) | 5.3 ms per frame of .set() | Under 1 ms |
| `sys.setswitchinterval(0.001)` as an A/B | none | Unknown; measure with the cpu/blocked split (F3) |

**Do not nice individual threads inside a process that shares one GIL** (for example the executor workers). If a niced thread holding the GIL is descheduled, every render thread's wait gets longer: priority inversion (I). Adjust priority only per process, after the split.

### 2.3 Take the encode CPU out of the hot path
This is the largest single item in the budget: it removes about 2.2 of the 4.1-4.5 AV cores today, and 4.3-4.6 at 2×60.
- **Prototype:** PyNvVideoCodec in its own venv, on GPU1, only while no screen is live.
- **Interim options:**
  - `nvenc_delay=1` on hevc-2160p30 (profiles.py:81). Chain time could fall from 26-28 ms to about 15 ms (**I**).
  - vmsplice: +35-50% pipe throughput (M, bench).
- Detail belongs to Design 1. It is listed here because it is the biggest cut in guest demand, and so the biggest cut in hidden vCPU time.

### 2.4 show_service (owner c8)
- **madmom activation into a child process** at nice 0. Do not use nice 19, unlike `ml.worker` 960032, which is not real-time. This takes 27-37% of show-rx's load out of the shared GIL (M, py-spy).
- **Bound the queue instead of letting it grow:** SO_RCVBUF of 48-64 KB keeps the queue at about 0.25 s or less (receiver.py:90). Report the loss on the Music card. This is a degrade, not a gate.
- **Skip soxr and madmom on zero-filled gaps** (clock.py:945-951).
- **Then the planned show_rt / show_brain split** (SHOW_ENGINE §2). It removes about 30% more GIL holders (bus 20% and brain 9%, M now).
- **Verify:** udpq.py at 20 Hz for 30 s under load. Recv-Q p50 under 64 KB, drops near 0/s, show-rx in state R under 70%.

## 3. CPU partitioning inside the guest

| Lever | Available? | Gain | Recommendation |
|---|---|---|---|
| **Affinity cap on background**: agent batch jobs (tsc, pytest, analysis, py-spy, builds) and Playwright on vCPUs 16-19 (2 virtual cores) with `nice 19 chrt -i 0` | **Yes**, unprivileged; the code already does this | Caps background's draw on host LPs at 4 vCPUs. Guest busy reached 16.8 vCPUs in heavy windows (IR7). Measured costs at higher load: synthetic pipe write 9 / 17 / 40 ms at 31 / 61 / 76% guest busy; show-rx 1.44 / 1.88 / 2.63 ms per packet; wake p99 0.3 ms (no screens) against 4.5-6 ms (heavy). Holding guest demand at about 9-10 vCPUs should keep the stream in the 17-25 ms write regime (**I**) | **Do first.** Add a `scripts/bg` wrapper (ca owns scripts/) and a CLAUDE.md agent rule. It slows the owner's agents, not AV, so **ask the owner**. Do not re-pin other sessions' processes |
| Keep AV threads off 16-19 (AV affinity 0-15) | Yes | Small: stops AV waking on vCPUs that background saturates (**I**) | Optional, as an A/B. **Never pin a single AV thread to a single vCPU**: Hyper-V moves vCPUs around, and a pinned thread cannot escape a vCPU the host has descheduled (I) |
| nice / SCHED_IDLE on background | Yes, but only within the same cgroup | ≤0.4% of wall for render threads. Helps against guest run-queue bursts (procs_running up to 27) | Do it as part of the wrapper. It does little for dev-server, the dagnet vite or Playwright, which sit in user.slice |
| cgroup v2 CPUWeight via `systemd-run --user` | **No** (hybrid hierarchy) | In-guest only, ≤0.4% | Defer. It needs `.wslconfig kernelCommandLine=cgroup_no_v1=all`, then `wsl --shutdown` (restarts every service and session). init.scope's weight also needs root, because shell-launched processes land there. It also conflicts with the owner's rule that services run as screen sessions under scripts/ctl. **Owner and ca** |
| SCHED_FIFO/RR | **No**: rtprio 0, no sudo, rt budget 0 in init.scope | ≤0.5% in-guest, 0 on the host side | **Don't.** A Python RT thread still waits on the GIL. With PREEMPT_NONE and HZ=100 a spinning RT thread can starve a vCPU, which breaks the bounded-job rule |
| isolcpus / nohz_full | nohz_full not in this kernel; isolcpus would only give guest-side isolation | about 0, because the host moves vCPUs anyway | Don't |

## 4. Background noise: inventory and fixes (no gates on AV)

| Process | Cost | Where it runs | Fix | Who decides |
|---|---|---|---|---|
| dagnet `dev-server.py` 2515 (99 days) | 0.83 guest core (forced polling; host lens); 100% in top now | user.slice | `WATCHFILES_FORCE_POLLING=false`, or run without `--reload` | Owner (other project) |
| Pylance node 951204 | 100% for 40 min after the VS Code restart (M now) | init.scope, nice 0 | Exclude `node_modules`, `dist*`, `.venvs` from analysis; limit indexing | Owner (VS Code settings) |
| vite :5188/:5189 (nice 10) and the :8080 vite card | 0.4-0.6 core, 11-17k wakeups/s | init.scope | Stop the strays. Production serves Flask `dist/` | The owning sessions; the owner for the :8080 card |
| Claude batch jobs (tsc at 173% now, pytest, py-spy, bfs `/`) | Bursts of 1-3 cores. A bfs walk lined up with 1057 show drops in 2 min (H6 verdict 1; the link is from timing only) | init.scope | The `scripts/bg` wrapper from §3; no whole-filesystem walks | Agents; the rule is the owner's |
| Playwright headless Chromium (swiftshader) | Unmeasured | user.slice | Close it when not testing; run it under `bg` | Its session |
| `score.engrave` | Unmeasured | nice 0, unpinned (client.py:155) | Give it the Analyser's `nice 19, pin_last=4` unless it is on the frame path (**U**) | 04 |

**Verify:** during a show, guest busy stays at about 9 vCPUs or less (/proc/stat at 1 Hz), and per-second write_ms p50 and wake p99 fall in the high-load thirds. Compare like with like: the same scene and both screens live.

## 5. Windows side (through Spectre only; every item needs owner approval)

| Lever | Basis | Expected gain | Verification |
|---|---|---|---|
| **Shrink the root partition's ~12 LPs** | The 127.0.0.1 Brave tabs use about 2.7 cores while streaming against 1.3 when off, plus 17.7% NVDEC on the 4060. The Spectre UI dev server uses 1.0-1.6 cores; serve a built bundle. Slack 0.6-1.0, Carbonite 0.3-0.9 (schedule it outside show hours), Code 0.35-0.9 (M, H4 verdicts) | Up to about 3-5 LPs that WSL could use. Root time was constant during the measured windows, so its r≈0 does not show that reducing it has no effect (I) | An F9 card: LP idle, WSL VP run time, Processor Queue Length, before and after |
| A "show mode" button on a Spectre card that **only lowers** desktop apps (BelowNormal / EcoQoS) | Same as above | Same direction, cheaper than closing apps (**U**) | Same |
| vmmemWSL priority | Cannot be read from WSL (protected). Scheduler type unknown | Possibly the only lever on host-side wake delays (**U**) | First, read only from Spectre's elevated backend: `bcdedit /enum {current}` hypervisorschedulertype and vmmemWSL PriorityClass. Only with the root scheduler and a settable priority, run an A/B with wakelat.c |
| **Why the CPU runs at 2.3-2.6 GHz** | Max processor state 100% and no limit flags (M), but the in-guest imul chain shows p50 2.28 GHz | If it can be fixed, roughly 1.4-1.6x on every CPU stage just to reach the 3.7 GHz base (**U**: the cause could be a BIOS PL1/Tau setting, the cooler, or a hidden EPP setting) | HWiNFO or ThrottleStop read by the owner |
| `.wslconfig` (one owner-approved restart window) | Demux page faults are 108-192 per frame in service against 0-40 standalone; host commit is 180.6 of 195 GB (M) | `pageReporting=false` as a candidate fix for fresh-page faults (**U**). Put `cgroup_no_v1=all` in the same restart, and only if §3 ever needs it | A pipe bench and frames capture before and after |

## 6. GPU side

- **Keep desktop and browser load off the render GPU.** GPU0 (the 4060) drives the Windows desktop and Brave's decode. About 20 points of its SM% do not track north's frames (M). Close or minimise the 127.0.0.1 tabs, or open them as `?kiosk=viewer` or on direct http. Do not move Brave to the P4000: it runs at 82 C.
- **Placement stays one screen per GPU.** Unpaced NVENC capacity is 88 / 103 fps (p5 / p4) on the 4060 and 67-69 / 76-82 fps on the P4000 (M). Two 4K60 streams do not fit on either GPU. Encoding on the other GPU would add a 1.3-2 ms PCIe copy.
- **NVENC at 60:** keep the shipped p4 with `-delay 2`. Paced 60 fps NVENC was 56% busy on the 4060 and 60-69% on the P4000 (M), which leaves 30-44% headroom. Use preset steps as a degrade, never a refusal.
- **Clock hold** (`nvidia-smi -lgc` on the 4060, application clocks on the P4000) through a Spectre script. Idle down-clocking costs the P4000 +7% NVENC time per frame (M). The 4060's 210-240 MHz troughs were seen only in the contaminated window (**U**). Run a 2-minute A/B; owner approval needed.
- **P4000 cooling:** sw_thermal in 13% of seconds and +25% NVENC time in those seconds, at 82 C on 42-44 W (M). Physical cleaning by the owner, in a no-screen window.
- **Unchanged:** the guard's 3 s hang stop and every bounded GPU job.

## 7. Order of work and approvals

| # | Step | Owner | Effort | Owner OK needed? | Pass criterion |
|---|---|---|---|---|---|
| 0 | Instruments: F3 per-frame cpu/blocked split, F6 loop lag, F9 Spectre Hyper-V card | 04, ca | 0.5-1 day | F9 only (Windows) | Fields present; recorder cost under 0.25% |
| 1 | `scripts/bg` affinity cap, stray vites, Pylance, dev-server polling, Brave tabs | ca, sessions, owner | hours | Yes (agents and desktop) | Guest busy about 9 vCPUs or less during a show |
| 2 | §2.2 interim fixes, `nvenc_delay=1`, the show_service queue bound and madmom child | 04, c8 | days | No (inside their areas) | host_ms p95 under 50 ms; Recv-Q p50 under 64 KB |
| 3 | Per-screen render processes, GPU-resident encode | 04 | 2-3 weeks | Already ruled | GIL futex under 3%; AV total about 3 cores or less at 2×60 |
| 4 | Windows: vmmem read, clock-cause read, GPU clock hold, `.wslconfig` window | ca and the owner | hours each | **Yes** | A/B against the step 0 baselines |

None of these steps refuses, trips or suppresses anything. Each one either removes cost or lowers the priority of non-AV work. The degradation controller in Design 3 then runs on the honest per-stage signals from step 0.