# The 2x4K60 programme for PC22

18 Oct 2026, revised about 20:15 after two reviews. **Tags:** [M] measured (source and window given), [I] inferred, [U] unverified. Code lines are as of `254e73f2` (19:57). I re-checked the room, profile, GLSL-cap, receiver and score-client lines at about 20:0x. Re-grep before editing. Appendix A lists the review findings I handled differently from what the reviewer suggested.

**Names used here:**
- **W0-W11** are this programme's workstreams.
- The diagnosis report's measurement windows are written **win0-win4**:
  - win1: the 14:27 baseline;
  - win2: 14:44-14:52, perturbed by our own benches;
  - win3: 14:58-15:04, live-score;
  - win4: 15:06-16:30, with no screen live.
- **Check 0-3** are early measurements.
- **STR-Rn, FLK-n, SHW-n and FE-n** are restart batches.
- **A1 and A2** are the acceptance shows (§1).

The evidence is under `docs/perf/evidence/`: the diagnosis is `docs/perf/DIAGNOSIS_2026-10-18.md`, and the fact findings and the architecture decision are in `docs/perf/evidence/programme/`. Only the small evidence files were kept in git; raw captures stayed in the investigating session's scratch.

---

## 0. Owner summary (one screen)

**Goal.** Both TVs at 4K with 60 new pictures a second. The show engine, room, lights, Hue, Art-Net, live-score and the mics all run at the same time, and your desktop is in normal use.

**Why this PC can do it:**
1. **The graphics cards are mostly idle.** A 4K frame is about 3 ms of GPU work out of a 16.7 ms frame. The GPU caught up within 2.7 ms on all 4,661 frames measured [M win1]. What holds us back is Python and the copying around the GPUs.
2. **Encoding on the GPU is far cheaper.** It costs about 2-3 ms of CPU per frame on the encode thread (3-5 ms for the whole process) instead of 35-38 ms. One P4000 averaged 60.07 fps at 4K with its encoder 61% busy. In those 10-second runs, though, a few frames came out up to 0.13 s late [M spike, 18:55]. A 10-minute run on each card settles it (Check 1).
3. **Batching GPU calls is far cheaper.** Bundling a frame's 50 GPU calls into one "graph" costs 0.12 ms instead of 1.2-2.1 ms. The slowest 1% cost 1.2 ms instead of 10-13 ms [M 18:59-19:03].
4. **The AV stack fits only if Windows uses less and the CPU clock fault is fixed.**
   - After the work, the AV services need about 3-5.5 cores at today's clock, or 1.8-3.7 once the clock is fixed [I from M, `evidence/programme/box-budget-v2/`].
   - With today's desktop the spare is −5.8 to +2.8 CPUs, so it may not fit.
   - With show mode, fewer desktop apps and the clock fixed, the spare is +2.0 to +11.3.
5. **The catch is the PC itself.** Windows uses 12-14.5 of its 20 CPUs, and the CPU runs at about 66% of its rated speed [M 14:51 and 18:51; the 18:51 sample overlapped our own benches]. Your decisions below settle that.

**The architecture in 6 lines:**
1. The renderer stays in WSL. Each live screen gets its own render-and-encode process. It starts when the screen goes live, takes a few seconds to start, and exits 30 s after the screen stops.
2. Each screen encodes on its own GPU (north on the 4060, south on the P4000). Raw video no longer goes through ffmpeg.
3. Today's stream_service becomes a light "control" process. It keeps the API, rooms, directors and media sending on :8702 unchanged. URLs and TV pages stay as they are.
4. **Priority:** in WSL the renderer can't push itself up, because it may not raise its own priority. It gets the PC by turning other things down: show mode on Windows, agents' jobs squeezed onto 2 CPUs, and previews and helpers at low priority. Its fair share per screen is one GPU, a fixed slice of video memory and about 1-1.5 CPU cores. That share is reported, not enforced.
5. One controller per screen steps quality down smoothly when anything is short: effects first, then scene rate, then fps, then bitrate. It never stops a stream, and it says why on the HUD and the Room card.
6. Windows-native is a measured exit ramp for south only. C++ is a last resort for one stage. Neither is the plan.

| Phase | What | Weeks | What you will see |
|---|---|---|---|
| 1 | True numbers, quick 30p fixes, show audio fix | 1-2 | The HUD tells the truth; no more 1-3 s freezes at 30p; music reacts faster |
| 2 | GPU encode | 2-3 | 30p holds on every scene, live-score included; about 20-65 ms less delay |
| 3 | One renderer per screen, on demand | 3-5 | Screens stop stuttering together; the renderer is gone 30 s after the last screen; a few seconds of cold start |
| 4 | Fewer GPU calls, smart step-down | 4-7 | More new frames per second; the Room card says when and why it stepped down |
| 5 | 60 fps | 7-8 | South at 60 first, then north; the acceptance shows |

That is 7-8 weeks with two builders: the renderer's owner, plus one more for the benches and the GPU-call work. With one builder it is 9-10 weeks [I: the items add up to about 46-51 builder-days].

**Your decisions.** Blocking ones come first.
1. **BLOCKING:** OK short GPU test runs (≤10 min each, only while no screen is live) on both cards, the desktop 4060 included.
2. **BLOCKING:** OK running agents' background jobs through `scripts/bg`: lowest priority, 2 CPUs while a screen is live, 4 otherwise.
3. Week 1: OK a read-only admin Spectre card that names what uses about 9.5 of Windows' CPUs.
4. Week 1: turn off dagnet's forced file polling and limit Pylance indexing (about 1-3 cores together).
5. Before week 7: the CPU runs at 66% speed. Read HWiNFO (sensors only) under load and send the package power, limit reasons and temperatures.
6. Before week 7: clean the P4000's cooler (82 °C while live).
7. Week 3: OK a Spectre "show mode": while a screen is live, desktop apps drop to low priority. Nothing closes.
8. Week 3: OK a 2-minute 4060 clock-hold test. If it helps, the clock is held only while a screen is live.
9. Week 6: look at the step-down thresholds before they harden.
10. Week 6: 60p bitrate of 40 Mbit/s (my pick) or 50 (needs a higher HEVC level flag).
11. Week 6: 60p player buffer of 150 ms (my pick), 200 (fewer glitches, slower reactions) or keep 120 (see W8.5).
12. Week 6: keep or close PC22's Brave tabs that show the live screens during shows.
13. Week 6: OK holding the kiosk's Chrome at 136, the only version 4K60 is proven on.
14. Week 6: OK one 10-minute test on one TV in a quiet time (the 60↔30 step check).
15. Week 7: OK setting devtest to 1080p30, because it shares the P4000 with south.
16. Player pause-to-rebuffer: not recommended (fewer but longer freezes). Say if you want it.

You get one more line only if nobody claims a role within a day (Check 0): "Nobody owns <role> now: OK for the renderer builder to take it?" It blocks only if the renderer role itself is the one left unclaimed.

**Later, and only if measurements call for them:**
- Renderer nice −5 through a one-time limits.conf. It buys little: render threads wait for a CPU only 0.17-0.30% of the time [M win1].
- A newer WSL kernel. It needs one WSL restart, which kills every WSL process.
- Windows-native south with TCC on the P4000 (one reboot).
- A resident pre-started renderer for a faster cold start.

---

## 1. Definition of done

**The acceptance shows** are ordinary shows with the recorder on. Nothing extra runs. No feature waits for them: each phase ships when its own items are done.

- **Setup (both shows):**
  - Both TVs live at `hevc-2160p60`.
  - Mic ON, and real music through the room speakers.
  - The **real kiosk sender** carries the Steinway's MIDI (`room-piano`) and the room mic (`room-mic`) if it has arrived, or else the PC22 mic (`pc22-mic`) (`config/room/show.yaml:38-48`). The piano is played for at least 3 min.
  - FX at the normal budget.
  - Hue, Art-Net, show_service with its beat process, the live-score children and the PC22 mic agent all running.
  - The desktop in normal use, with show mode on. Agents' jobs are allowed under `scripts/bg`.
- **A1, 12 min, with the scenes cued by hand** from the Room page or the library. Auto's other parts stay on; under Auto the Art Director would pick the scenes.
  - Scenes: note-fluid 3 min, live-score 3, piano-waterfall 2, live-art 2, and one GLSL scene (an `st-*` scene) 2.
  - The first four are the most-started scenes by TV starts (`evidence/programme/tv_scene_tally.txt` [M]). GLSL scenes are about 17% of TV starts.
- **A2, 60 min of pure Auto,** with some library browsing on a phone or the PC during it.
- **Pass:**
  - A1 and A2 each meet §1.1-§1.3 once.
  - No run of 10 or more repeated slots, no failed session, no leaked worker.
  - 0 room timing fallbacks and 0 parked screens.
  - The test piano (udp 6981) is used only for the repeatable sound-to-glass probe (W0.9d), never as A1's or A2's piano.
- **Tools:**
  - From W0: the frame trace and flight recorder (W0.3, W0.5); the Spectre host card (W0.5, W7.3); `udpq` at 20 Hz per socket; a passive bus probe on :8701/:8703 (the `evidence/inputs-realtime/bus_listen.py` pattern); a Flask prober; the sender-link reader; the sound-to-glass probe (W0.9).
  - From W8: page stats (W8.1, W8.2).

### 1.1 Per screen (each TV; the glass client is the TV)

| Measure | Done when | Today | Tool |
|---|---|---|---|
| Output frames/s | 60.0; p1 of 1 s windows ≥59.5 | 30p only | trace `t_pkt` |
| New (content) frames/s | ≥57 mean per scene; p5 of 1 s windows ≥50; ladder at level 0 in ≥95% of seconds | note-fluid 19.3 / 24.2 of 30 [M win1]; live-score 7.3-13.8 [M win3] | trace (rendered and not held) |
| Repeats (re-sent surfaces) | ≤3% of slots; no run of ≥10 slots; at most one ≥6-slot run per 10 min | 18-34% [M win1], 51-73% [M win3]; freezes of 0.3-2.8 s | trace |
| Resyncs | 0 | 8-17 s lost per 150 s [M win3] | trace |
| Slot to packet | p50 ≤60 ms; p99 set from the Check 1 soaks (provisionally ≤100 ms) | 30p: p50 129-175 ms [M win1]. Spike at 60p: p50 54 ms; p99 99-134 ms in 10 s runs, 56 ms in a 4 s run [M 18:55-19:05] | trace |
| Fan-out egress (packet to socket) | p99 ≤16.7 ms | p99 74-101 ms, max 277 ms [M win2, perturbed] | W0.4 |
| Glass stalls | ≤1 per minute per TV; frozen ≤0.1% of time | floor 0.2-0.5/s; 5.99/s north [M win2, perturbed] | page stats (W8.1, W8.2) |
| Glass dropped frames | ≤0.1/s | 5.95/s north [M win2, perturbed] | page stats |

GPU clocks, temperatures and NVENC load are in §1.4. They explain a miss; they don't decide one.

### 1.2 Lifecycle

| Measure | Done when | Tool |
|---|---|---|
| Cold spawn to `session.ready`, timed from room's warm hint | p95 ≤8 s, typical ≤4 s, p99 ≤ room's window − 1 s (about 12 s, see W3.11), over 20 real starts | events log |
| Room timing fallbacks and parked screens | 0 over A1 and A2. Three fallbacks in 600 s park a screen (`room_service/config.py:116-117`). | room log |
| Entry worst case (room budget) | still ≤29 s, under the page's 30 s freeze safety (`room_service/config.py:129-131`) | config |
| Exit | room's exit POST p99 within its own 5 s timeout (W3.11); workers 0 and VRAM back to idle within 32 s of the last session; no orphan after `scripts/ctl stop stream` | `/api/state`, NVML, room log |

### 1.3 Per service

| Service | Done when | Today | Tool |
|---|---|---|---|
| show-rx (VBAN :6980; two sockets since 3a's split, one for audio and one for MIDI, `show_service/receiver.py:12-16`) | Per socket: 0 drops over the show; Recv-Q p50 ≤8 KB, p99 ≤64 KB. show-rx thread ≤70% of a core. | 153-168 drops/s, Recv-Q 297-376 KB [M 14:47-15:04]; about 95/s at 16:20 from a verifier [U] | `udpq`: each `:1B44` row of `/proc/net/udp`, 20 Hz |
| Sender links (kiosk `room-piano` and `room-mic`; `pc22-mic`) | VBAN frame-counter gaps 0 and MIDI `seq` gaps 0; sender-to-PC22 one-way delay p99 reported (target ≤20 ms [I]) | never measured | show_service health; sender heartbeat stamps (W0.9e) |
| MIDI | Kernel arrival stamp to bus publish p99 ≤20 ms; bus lag (arrival minus `t_ms`) p95 ≤50 ms | `music.note` lag p50 281 ms, p95 539 ms [M 14:51, perturbed] | the SO_TIMESTAMPNS stamps from the show-rx builder (screen-machine-3a); bus probe |
| show_service bus :8703 | Per topic, inter-arrival p99 ≤2x nominal: `music.features` 50 Hz (`show_service/dsp.py:221`), `music.clock` 10 Hz (`contract.py:115`), levels [U rate]. `music.clock` locked within 10 s of music starting [I target]. Beat process CPU reported. | not measured | bus probe (W0.9b); show health |
| Sound to glass | ≤500 ms p95 (the owner's target), from ≥20 onsets | unknown end to end; render-to-photon on south ≈309 ms [M 13 Oct] | W0.9d |
| Room show bus :8701 (40 Hz) | Inter-arrival p99 ≤50 ms, max ≤100 ms | p95 46 ms, max 101 ms [M 14:51] | bus probe |
| Art-Net out | Send interval p99 ≤2x nominal (the driver's continuous 50 Hz, `config/room/fixtures.yaml:36` [I]) | not measured | show_service's per-output `rate_hz` and `late_ms` (`show_service/outputs.py:169`, `:393-398`) |
| Hue out | Same rule; nominal 25 Hz (`room_service/hue/output.py:34`) | not measured | a send counter in room's Hue output; whether it exists is [U] (W6.6) |
| Flask and room | `GET :8700/healthz` and the app index on :5000 at 1 Hz: p99 ≤100 ms, max ≤500 ms | not measured | Flask prober |

### 1.4 The box

| Measure | Target (explains a miss) | Today | Tool |
|---|---|---|---|
| Guest demand | Busy vCPUs (1 s) ≤ LPs granted to WSL − 1, in ≥99% of seconds | 8.0 busy vs 5.5 granted [M 18:51, perturbed by our benches 18:50:54-18:52:24] | `/proc/stat` plus the WSL VP run time from the host card |
| Hidden LP wait | ≤10% | 9-12% [M 15:2x]; 31% [M 18:51, perturbed] | same |
| Wake-up | Probe wake p99 and p99.9 with screens live, reported; ≤2 ms if W7.13 is ever done | p99 6.0 ms with screens live [M, REPORT §5] | the box probe's 100 Hz wake probe (W0.5) |
| Windows | LP idle p50 ≥5%; total demand p95 ≤16 LPs | idle 0.04% [M 18:51, perturbed] | host card |
| CPU clock | ≥95% of nominal after the fix | 65.9% [M 18:51] | host card |
| GPU | NVENC p95 ≤85%. 4060 VRAM free ≥0.5 GB. P4000 ≤80 °C, with sw_thermal in ≤5% of seconds. 4060 SM clock p50 ≥1,500 MHz while live if the clock hold is approved; otherwise reported only. | P4000 82 °C; 4060 at 960 MHz under 60p duty [M synthetic] | NVML via the box probe |
| GPU resets | 0 TDRs, 0 hang stops | not measured | Display event 4101 on the host card; dxg lines in dmesg; guard log |

The per-screen and per-service numbers decide pass or fail. The box numbers explain a miss.

---

## 2. Workstreams

**Restart classes:**
- **none**
- **SW**: an edit to the per-screen switches file (W0.11). It is read by mtime and applies at the next session start (degrade and trace apply at once). No restart.
- **FE**: frontend deploy (`git status src/` first; everything in `src/` ships)
- **STR-Rn**: a batched stream restart, only when `GET :8702/api/sessions` is `[]`, devtest included
- **FLK**: Flask restart via Spectre (tell the sessions first; verify :5000 after)
- **SHW**: show restart
- **SPC**: Spectre module save (hot reload)
- **WSL**: WSL restart
- **WIN**: Windows reboot

**Roles.** No name is invented for a vacant role.

| Role | Current name | Workstreams |
|---|---|---|
| Programme lead (sends the role-claim note) | screen-machine-09, the session that wrote this programme [M ListAgents ~20:0x] | Check 0 |
| stream_service (renderer) owner | not re-identified. Likely screen-machine-ff [I: `8cb3ec90` credits "ff's c6f43e9b", a stream_service change at 16:29, after the rename]. | W0-W5, W8 server half, W9 |
| Second builder | unclaimed | W0.8, W0.9, W4 (except W4.6) |
| show_service owner | screen-machine-e9 | W6 |
| show-rx fix builder | screen-machine-3a | W6.1, W6.4 |
| FX interplay | screen-machine-ec | W5.2 (FX budget), W6.5 |
| live-score | screen-machine-b0 | W1.12, W3.12, W4.6 |
| room_service owner | vacant (was 2f) | W3.11, W8.1 room half, W6.6 Hue, W9 config |
| display-page owner | vacant (was 2f) | W8.1, W8.2, W8.4 page half; W5.6 card words |
| Ops / Spectre owner | vacant or unknown (was ca) | W7, W0.5 Windows half, cards |
| kiosk owner | was bd; re-check after the rename | W8.7-W8.9 quiet windows |
| `docs/AV_PROGRAMME.md` owner (§11 records owners) | vacant (was 2f) | records the roles |

**Check 0 (week 0).**
- The programme lead sends one role-claim note to the four peers with no known role: screen-machine-26, -4e, -ff and -7c [M ListAgents ~20:0x]. It covers the stream, second-builder, room, display-page, ops, kiosk and AV_PROGRAMME roles.
- Roles still unclaimed after a day go to the owner as one plain line (§0).
- Nothing waits for this. If a half stays empty, the renderer builder takes it with the owner's OK and tells the peers. Only the renderer role itself is blocking.

### 2.0 Every verified bottleneck mapped to work

| Bottleneck (source) | Items |
|---|---|
| H1: render-thread host cost (own CPU 12.5-13 ms, 35% kernel) plus one GIL shared by both screens, the loop and the workers | W1.1-W1.5, W1.10, W1.11, W3, W4 |
| Encode thread vs the GIL (one Python neighbour cut a paced 60 fps encode to 36 fps [M `gil_paced_p4_aq12`]) | W1.10, W2.6, W3.3 |
| H3: raw NV12 pipe (35-38 ms CPU per frame; two chains top out at 50-52 fps) | W1.6 (interim), W2 |
| H2: degrade logic on mislabelled "GPU" numbers (repeat EMA, rate_div flapping, over_budget, resync lag, HUD and diag bugs) | W0.1, W0.2, W1.7, W1.8, W2.6, W5 |
| H4: the box (Windows 12-14.5 LPs, hidden LP wait, clock at 62-67%) | W0.5, W7 |
| H5: the glass (egress jitter, Brave mis-picked as the glass client, uncounted drops, repeats invisible to the page) | W0.4, W3.5, W8 |
| The player's resume-on-one-frame cascade | W2 (removes the upstream cause), W8.5 |
| H6: show-rx backlog | W6 |
| Sender links over the powerline at 2x4K60 (never measured) | §1.3 rows, W0.9e, W8.8 |
| H7: P4000 thermal, heavy presets, 4060 shared with the desktop | W7.6, W7.8, W5 (ladder), W0.6 (cost table), Check 1 (4060 run) |
| WDDM / GPU-PV per-call cost (launch 45-49 µs p50, p99 up to 9 ms) | W4.2-W4.8, W1.9 |
| Demux page faults | W2 (removes the pipe) |
| Wake-up tails (HZ=100, PREEMPT_NONE; pacing p99 9.6-12.7 ms, likely GIL reacquisition [I]) | W0.5 wake probe, W1.3, W1.10, W3 (one hot thread), W7.13 (later) |
| live-score children, including the extra ffmpeg per screen (never measured) | W0.8d, W3.12, W4.6 |
| GC (never measured) | W0.3 marker, W1.11 |
| asyncio loop GIL holds (29.9% [M win2, perturbed]) | W1.1, W3 |
| Windows memory (commit 181/195 GB; WSL trimmed to 10.5 GB) | W7.3, W7.11, W3.8 (one shared preview worker) |
| Kiosk N95 decode at 2x4K60 | W8.8 |
| Network at 60p; HEVC level 5.1 vs 50M | W8.5, W8.6, W8.9, W5.5 |
| 4060 down-clock under 60p duty (2.6x kernel time) | W7.6, W5.1 (1 s sustain), W3.10 (warm-up burst) |
| 4060 VRAM (about 0.9 GB free while live) | W3.13, W3.8 placement |
| dxg pinned-memory failures (dmesg) | W2 (drops the 149 MB ring), W3.13; B9 not built |
| One P4000 encodes about 95 fps of 4K in total (two at once: 46.9 + 47.9 fps [M 18:59]) | placement rule; W9.0 (devtest); W3.8 (previews) |
| Room 5 s timeouts vs cold start, and the shared create/exit timeout | W3.10, W3.11 |
| Hard-coded IPs (`deploy/sm-stream.env:11`) | W8.4 |
| Every switch flip costs a restart (`deploy/sm-stream.env:7`) | W0.11 |
| Spectre's own polling (30-35 powershell/min, about 54 wsl.exe/min) | W7.9 |
| Router password on argv | W7.10 |

### W0. Measurement first (stream owner; second builder for W0.8-W0.9; ops owner for the Windows half)

Every later step needs a before and an after.

| ID | What changes (where) | Done when (measured how) | Effort | Needs | Restart |
|---|---|---|---|---|---|
| W0.1 | **Honest numbers (T1):**<br>- host_ms None on repeats (`renderer.py:141`)<br>- slip recorded before a resync (`:440-446`)<br>- `window_stats` counts rendered rows only (`:949-958`)<br>- HUD reads `frame_gpu_ms` (`hud.py:269` vs `guard.py:294`)<br>- "render" relabelled "GPU span" (`diag.py:91-92`)<br>- the rate log carries the session (`layers.py:155-157`)<br>- `prctl` thread names (`renderer.py:344-347`, `encoder.py:83-84`)<br>- `nvidia-ml-py` in sm-stream (`diag.py:22`) | The HUD guard line shows a number. On win1-like load, diag north host p50 reads about 22 ms (not 9.5). `top -H` shows thread names. No nvidia-smi forks. | 0.5 d | Check 0 | STR-R1 |
| W0.2 | **cpu/blocked split:** `_Rec` gains `cpu_ms` (`time.thread_time`), `runq` (kept-open schedstat fd), `blocked = host − cpu − runq`, div and held, so content fps exists | Fields in `/api/sessions`; cost ≤0.05 ms per frame (self-meter) | 0.25 d | W0.1 | STR-R1 |
| W0.3 | **FrameTrace (T2):** new `trace.py` with an 8,192-slot numpy ring; markers (rescan, prepare, GC, HTTP >5 ms, loop lag >10 ms); dossiers; a cached `GET /api/perf`; 3 HUD lines (`evidence/final/design_profiler.md` §4.1 and §5); a GIL-wait column per thread, encode thread included; a per-frame CUDA-call counter behind a flag. Unit tests (shared plumbing). | Cost ≤0.3 ms per frame; dossiers name the starter of ≥90% of late slots | 1 d | W0.2 | STR-R2 |
| W0.4 | **Fan-out stamps (T3, server half):** `t_posted` and `t_sent` per client, a ring of 512, stats kept with their receive time (`media.py:113-136`, `:176-180`, `:188-221`) | Per-client egress p50/p95/p99 in `/api/perf` | 0.3 d | W0.3 | STR-R2 |
| W0.5 | **Box probe and flight recorder (T4):** a new `boxprobe.py` child with its own GIL. It reads `/proc`, NVML and each `:1B44` queue, and runs the **100 Hz wake probe** (`evidence/final/design_profiler.md:154`). It writes 1 Hz flight files, kept 3 days. Ops builds a Spectre **host-perf card**, `scripts/windows/hostperf/hostperf.ps1`, holding PDH handles (not WMI): LP idle, root vs WSL VP run time, % performance, queue length, commit, and a count of Display event 4101. | One box line per second with guest busy vs granted LPs and wake p99/p99.9; probe ≤1% of a core; the card reports its own cost | stream 0.5-1 d; ops 1 d | W0.3 | STR-R2; SPC |
| W0.6 | **Live cost table and probe frames (T5):**<br>- guard `begin`/`end` add host wall and CPU (`guard.py:163-193`)<br>- a `%globaltimer` hold kernel (works under WSL [M]). **Its own bound:** it stops at the timer target or after 10 M SM cycles, whichever comes first. That is ≤48 ms even at 210 MHz, the lowest clock seen [M GPU-G4], and about 4 ms on the 4060 or 7 ms on the P4000 at full clock. A longer hold is 2-4 back-to-back capped launches, ≤1 period in total. A hold that the cap cuts short rejects that sample, which is counted.<br>- new `costs.py`; `GET /api/perf/costs` | True GPU ms per plug-in within ±15% of calibration on 3 plug-ins; rejected-sample count reported | 1 d | W0.3 | STR-R3 |
| W0.7 | On-request snapshot: py-spy `--gil --nonblocking` plus the trace, ≤20 s, nice 19 (`tools/snapshot.py`) | Names the GIL holder at a stall | 0.25 d | W0.3 | none |
| W0.8 | **Bounded benches** (second builder). No screen live, nice 19; each run waits for an idle GPU, as `evidence/programme/encode-spike/run_one.sh` does.<br>(a) **Check 1:** PyNvVideoCodec, paced 60p, P4, AQ 12. A 10-min soak on the P4000; a 10-min soak on the 4060 (the desktop card), logging NVENC%, SM% and the Windows 3D-engine busy; and one 10-min run on both cards at once. Each run has a render-shaped Python neighbour in the same process (about 6 ms of Python per 16.7 ms), and all are logged with box load.<br>(b) **Check 2:** graph prototype with a per-frame uniform arena.<br>(c) CUDA calls and Python ms per frame on the 5 A1 scenes (W0.3 counter, read in an ordinary show).<br>(d) live-score re-measure after `ace1bda5`, children included. Name the extra ffmpeg per screen seen in win3, and say whether W2 removes it.<br>(e) Native benches for the W10 decision (§2.11): Windows worker cold start, 4 ms sleep lateness at HIGH priority with `timeBeginPeriod(1)`, per-call CUDA costs alone and beside a busy Python thread, and 60 s of a real scene loop. They run in the existing `%LOCALAPPDATA%\sm-perf\venv`, windowless, before week 5. | (a) 0 decode errors in every run; encode-thread CPU p50 ≤3 ms; slot-to-packet and pace-lateness p50/p99 recorded per run. §1.1's slot-to-packet p99 is then set from them.<br>(b) ≤0.3 ms CPU per frame<br>(c)/(d) a table per scene<br>(e) numbers recorded | 3-3.5 d | owner ask 1 | none |
| W0.9 | **Acceptance kit** (second builder):<br>(a) `udpq` at 20 Hz, per socket (two `:1B44` rows since 3a's split)<br>(b) a passive bus probe on :8701/:8703 with per-topic rates<br>(c) a Flask prober<br>(d) **sound to glass:** test-piano onsets at known times; the MIDI-event overlay (`9b7e8110`) flashes on each onset; the `tools/room/latency_cal` light sensor reads it. At least 20 onsets. The sensor reads at 10 Hz, so the resolution is about 100 ms, and that is stated with the result.<br>(e) a sender-link reader: VBAN frame counters, MIDI `seq` gaps, heartbeat stamps<br>(f) a report script that fills the §1 tables from the flight files | Produces §1 from one show | 1-1.25 d | W0.5 | none |
| W0.10 | **Baseline show at 30p** with everything on (the "before") | §1 tables filled at 30p | 0.5 d | W0.9 | none |
| W0.11 | **Per-screen switches file**, for example `config/live/stream_switches.json` (the stream owner names it). It is read by mtime, as modes.json is, and holds: encoder `nvenc` or `ffmpeg`, workers `proc` or `inproc`, degrade `off`, `shadow` or `on`, the switch interval and the trace level. Values are keyed by screen, with a default. Encoder and workers apply at the next session start; degrade, interval and trace apply at once. Unit tests (shared plumbing). Plain file, no auth. | A flip on devtest takes effect at its next start with no restart | 0.5 d | — | STR-R2 |

### W1. Cheap 30p fixes inside today's process (stream owner)

| ID | What changes (where) | Done when (measured how) | Effort | Needs | Restart |
|---|---|---|---|---|---|
| W1.1 | **B1** summary: one sort per ring and a 1 s cache. `stats.py:9-30` sorts on every call (`renderer.py:917-930`). `_update_lead` (`sessions.py:2055`) uses the cache. | Summary GET ≤1 ms of GIL (W0.7); main-thread GIL share ≤19%, was 29.9% [M win2] (29.9% × (1 − 0.378) = 18.6%) | 0.25 d | — | STR-R1 |
| W1.2 | **B2** rescan by directory mtimes (`plugins/__init__.py:116-203`); the library index and preset signatures move to a nice-10 child (`library.py:258-271`, `presets.py:227-236`) | No rescan marker over 50 ms on the loop; host_ms in ±1 s burst windows ≤1.1x calm (was 1.4-1.5x [M win1]) | 0.75 d | W0.3 | STR-R2 |
| W1.3 | **B3** one sleep to the deadline; no `sleep(0)` spin (`renderer.py:434-438`) | Wake slip p95 ≤0.5 ms when alone [I] | 0.1 d | — | STR-R1 |
| W1.4 | **B4** guard: a frame-level pair every frame, per-plug-in events round-robin; the 3 s hang stop is unchanged (`guard.py:38`, `:163-193`) | Guard host ≤0.5 ms per frame (was 3.0 [M]) | 0.5 d | W0.3 | STR-R2 |
| W1.5 | **B5** batched per-slot uploads, staging allocated once (`sound.py:62-73`, note_fluid `U.set`) | `.set` ≤1 ms per frame (was 5.3 [M]) | 0.5 d | — | STR-R2 |
| W1.6 | **B6** `nvenc_delay=1` on hevc-2160p30 (`profiles.py:81`): +33 ms, interim until W2 | Chain capacity ≥40 fps on win1-like load | 0.05 d | — | STR-R1 |
| W1.7 | **B7** one probe frame after 3 repeats (`renderer.py:463-474`) | 0 runs of ≥10 slots in a 10-min 30p show; north +1.5 fps (replay predicts +1.5-2.6 [M]) | 0.1 d | — | STR-R1 |
| W1.8 | **B8** rate_div and `over_budget` driven by own CPU plus true GPU lag (`layers.py:126-160`, `guard.py:280-290`); W5.2 replaces it later | Rate changes ≤5/min per screen (was 20-30/min [M]); no false frozen exits | 0.5 d | W0.2, W0.6 | STR-R3 |
| W1.9 | **Stream priorities:** render stream −5 (`gpu.py:248`); prepare, prime and upload streams 0 (`renderer.py:242`, `:256`, `:270`, `:283`). A short kernel queued behind a full grid went from 8.9 to 0.64 ms [M]. | Render GPU span p99 rises ≤1 ms during a prepare | 0.1 d | — | STR-R1 |
| W1.10 | `sys.setswitchinterval(0.001)` in today's process as an A/B, flipped through the switches file (W0.11). In workers it becomes the default (W3.3). | blocked_ms and encode-thread GIL wait, with and without, recorded | 0.05 d | W0.2, W0.11 | STR-R2; then SW |
| W1.11 | `gc.freeze()` after each prepare; GC markers (W0.3) | GC pauses >2 ms counted per minute | 0.1 d | W0.3 | STR-R2 |
| W1.12 | `score.engrave` at nice 19. The Analyser and engrave are pinned to CPUs 16-17, so the agents' `scripts/bg` CPUs 18-19 stay separate. Today the Analyser runs on the last 4 CPUs, 16-19 (`score/client.py:40-64`, `:175` `pin_last = 4`). Unless engrave is on the frame path [U]; live-score (screen-machine-b0) decides. | Engrave and Analyser CPU off cores 0-15 and 18-19 | 0.1 d | b0 | STR-R2 |
| — | **B9 (vmsplice) is not built.** W2 removes the pipe, and growing pinned memory risks the dxg `create_existing_sysmem` failures seen in dmesg [M]. | — | — | — | — |

Phase 1 target at 2x30p: content fps ≥28 on both screens on note-fluid, repeats <5%, no ≥10-slot freezes [I].

### W2. GPU-resident encode (stream owner), the one structural must for 60

It ships in today's process at 30p first, **on devtest first** through the switches file, with ffmpeg as the fallback. In today's process the encode thread shares one GIL with 2 render threads and the asyncio loop. The trace's encode-thread GIL wait (W0.3) is read on devtest before any TV switches.

| ID | What changes (where) | Done when (measured how) | Effort | Needs | Restart |
|---|---|---|---|---|---|
| W2.1 | New `encode/nvenc.py`: PyNvVideoCodec 2.2.3 in `~/.venvs/sm-stream`. `CreateEncoder` is given CuPy's context and the render stream; 3 NV12 surfaces come from the pool; HEVC P4, `aq=12`, CBR, GOP 60, bf 0. The encode thread blocks in `Encode()` with the GIL released [M]. No host sync (md5-identical either way [M]). | Encode-thread CPU p50 ≤3 ms per frame in production; a 60 s capture passes `ffmpeg -v error` with 0 errors | 1.5-2 d | Check 1 | STR-R4 |
| W2.2 | hvcC from the VPS/SPS/PPS of `GetSequenceParams()`, next to `build_avcc` (`bitstream.py:391`). Today it comes from ffmpeg's FLV (`:548-553`). Annex-B to length-prefixed packets. | **Golden test** (shared plumbing): byte-identical fMP4 to the ffmpeg-derived output on the same packets | 0.75 d | W2.1 | STR-R4 |
| W2.3 | Colour through the `colr` nclx box in the init (`fmp4.py:39`); the source-context VUI route only if Chrome ignores `colr` | Devtest: entry and exit cut luma match today's or better | 0.5 d | W2.2 | STR-R4 |
| W2.4 | Forced IDR (`FORCEIDR\|OUTPUT_SPSPPS`) at `hold_pts` and on client join | Exit cut luma error ≤1.6 (was 3.2) | 0.25 d | W2.1 | STR-R4 |
| W2.5 | `Reconfigure()` for fps 60↔30 and bitrate 50/40/30: about 2 ms, no IDR [M]. Test whether a preset or AQ change needs a new encoder [U]. | Unit check on devtest; the result recorded | 0.25 d | W2.1 | STR-R4 |
| W2.6 | **Output clock on the encode thread.** At each deadline it takes the newest finished surface (`cudaEventQuery`, 1.2 µs [M]) or re-submits the previous one. The render thread renders for the next slot it can make. This replaces the repeat rule and resync (`renderer.py:440-446`, `:463-474`). The frame id stays on the profile's tick grid. Per-slot Python on this thread (pick, `Encode()`, Annex-B to fMP4, send hand-off) is kept to a minimum and timed in the trace. | 0 resyncs; output fps p1 ≥ (profile fps − 0.5); content fps reported separately; encode-thread GIL wait p99 reported | 1 d | W2.1, W0.2 | STR-R4 |
| W2.7 | Fallback chain: native NVENC, then today's ffmpeg profile (shown as "encoder fallback" in `degrade{}` and on the HUD), then a ctypes NVENC binding if Check 1 failed. Chosen per screen in the switches file (W0.11): devtest first, then south, then north. | A forced-failure check on devtest falls back and keeps playing | 0.25 d | W2.1, W0.11 | STR-R4; then SW |
| W2.8 | Bitstream-md5 test for every NVENC option, because unknown keys are silently ignored [M] | Tests pass | 0.25 d | W2.1 | none |
| W2.9 | Optional low-latency mode (EOS after every frame: 15 ms vs 54 ms at 60p [M]), off by default. It held only 57.56 fps paced [M 19:00]. | ≥30-min soak with a decode check, at ≥59.5 fps, before anyone turns it on | 0.5 d | W2.1 | none |
| W2.10 | `fmp4.py` per-sample trun durations: 3000 at 30p cadence on a 60 Hz grid, frame id +2, contiguous tfdt (`fmp4.py:92-107`) | Unit test; an fps step becomes possible later | 0.25 d | W2.2 | STR-R4 |

**Phase 2 done when, at 30p:**
- encode-thread CPU ≤3 ms per frame;
- slot to packet p50 ≤110 ms (the 3-frame hold is about 100 ms at 30p [M]; it is 54 ms at 60p);
- AV CPU down by about 2 cores [I];
- live-score holds 30 fps.

### W3. Process-per-screen workers with an on-demand lifecycle (stream owner)

This is Cut B: the control keeps every Session, and workers hold only what runs per frame (`evidence/programme/split-map/process_layout.txt`).

| ID | What changes (where) | Done when (measured how) | Effort | Needs | Restart |
|---|---|---|---|---|---|
| W3.1 | **The seam, in-process first:** an EngineProxy plus a worker handle table over a loopback transport. It covers the roughly 25 leaks in `evidence/programme/split-map/seam_leaks.tsv`:<br>- prepared instances become handles (`sessions.py:449-513`, `fx_state.py:314-355`)<br>- `StartTransition`, `UpdateParams`, `Retire` and `FxUpdate` (`sessions.py:806`, `:889`, `:921`, `:1040`)<br>- the listener callbacks (`:2177-2245`), the summary (`:2315-2359`) and the HUD reads (`hud.py:253-297`, `:335`)<br>Handle-table unit tests. | The frame trace on devtest and the TVs matches the post-W2 trace (content fps and host p50 within ±5%) | 3-4 d | W2 | STR-R5 |
| W3.2 | Platform seams: `clockmap` (the identity in WSL); guarded `fcntl` (`encode/encoder.py:14`, `plugins/sources/gallery.py:52`); one `pace_clock()` (`renderer.py:77-79`); one `cpu_ms()`; a transport interface (Unix socket now, TCP later) | Importing worker modules runs no POSIX-only calls at module level | 1 d | W3.1 | STR-R5 |
| W3.3 | **Worker process** `python -m stream_service.worker`:<br>- `CUDA_DEVICE_ORDER=PCI_BUS_ID` plus `Device(n)` (never `CUDA_VISIBLE_DEVICES`)<br>- `PR_SET_PDEATHSIG` (the pattern at `plugins/_lib/glsl/smoke.py:189`). Workers are spawned **only from the control's main loop thread**, because PDEATHSIG fires when the parent thread exits. They stay in the control's process group.<br>- one hot Python thread (render), plus the encode thread (GIL released in `Encode()`) and a prepare/closer thread<br>- `sys.setswitchinterval(0.001)` by default; the W1.10 A/B only confirms it<br>- prepare: translation and NVRTC run in the helper (W3.7). The remaining prepare Python is measured in Check 3; if it inflates rendering, it is sliced into the render thread's frame slack.<br>- no asyncio, aiohttp or registry discovery<br>- WorkerPool states: spawning → idle → bound → lingering 30 s → exit | Workers 0 and VRAM back to idle within 32 s; no orphan after `scripts/ctl stop stream`; `workers: proc` or `inproc` in the switches file | 2-3 d | W3.2 | STR-R6; then SW |
| W3.4 | Bus fan-in. The control keeps one subscription each to :8701, :8703 and levels, and forwards raw frames to the workers. Each levels client costs show_service about 25% of its bus loop [I from a code comment, `stream_service/scope_feed.py:9-11`]. Each worker keeps latest-value copies of MusicFeed, ShowInputs/Sampler, ScopeFeed and SoundView. | Worker bus drain ≤0.5 ms per frame p95 (if over: a `/dev/shm` seqlock plus SPSC rings); show_service health shows 1 renderer client per bus | 1 d | W3.3 | STR-R6 |
| W3.5 | A media socket from worker to control, separate from the cmd/evt sockets so an IDR never delays events. The `/media/{sid}` fan-out stays on :8702. | **Check 3 (egress):** p99 ≤16.7 ms. If over: an SCM_RIGHTS fd handoff to a per-screen egress child, which also reads the page's incoming stats text and forwards it to the control. Then FB3 (§2.11). | 1 d | W3.3, W0.4 | STR-R6 |
| W3.6 | Stats snapshot from the worker at 1-2 Hz; `/api/sessions` served from cache | A summary GET touches no worker | 0.5 d | W3.3 | STR-R6 |
| W3.7 | Kernel-prep helper (nice 19): GLSL-to-CUDA translation plus NVRTC into `~/.cupy/kernel_cache` (3,807 cubins [M]) and a new translation disk cache, topped up after each rescan. Workers never translate or compile. | A worker's prepare of a cached GLSL scene ≤200 ms p95 [I target] | 1 d | W3.3 | STR-R6 |
| W3.8 | **One shared preview worker per GPU** that holds all preview sessions (720p30, nice 5; `preview.py:28-31`), with the same pool and linger. It goes on the GPU with the most NVENC and VRAM headroom at that moment (NVML, 1 Hz). While south is at 60p that is usually not the P4000 [I]. Placement is reported, never refused, and previews step down first. | Library browsing during a live screen leaves that screen's §1.1 rows unchanged; RSS and VRAM per worker on the stream card | 0.5 d | W3.3 | STR-R6 |
| W3.9 | **Check 3** after STR-R6, on devtest and then a TV | Render-thread GIL futex <3% of wall; cross-screen spike coincidence <30% (was 80%; chance about 23% [M]); egress p99 ≤16.7 ms; render host p95 during a prepare ≤1.2x calm [I target] | 0.25 d | STR-R6 | none |
| W3.10 | **Lifecycle:**<br>- create returns 201 at `starting`: `sessions.py:2899-2901` stops awaiting `s.start()`<br>- warm hint `POST /api/workers/warm {screen}` (additive)<br>- cold-start trims: lazy plug-in imports (0.5 ms each [M]), no aiohttp (−0.65 s [M]), no full `discover()` (−1.1 to −3.0 s [M])<br>- a **warm-up burst** during the anchor hold, so the 4060 has clocked up by cutover (ramp 0.3-0.5 s [M/I]). It is exactly 30 frames of the session's own scene (0.5 s at 60p), rendered and discarded through the normal path. No new kernel: GLSL scenes keep translate.py's guards (`_G_LOOP_CAP` 65,536 iterations and `_G_CYCLE_CAP` 3 M cycles per thread, `glsl_compat.cuh:24-33`), and hand-written scenes keep their fixed loop counts [U: check the 5 A1 scenes]. The 3 s hang stop stays. | Spawn to ready, from the warm hint: p95 ≤8 s, typical ≤4 s, p99 within room's window − 1 s, over 20 real starts | 1.5 d | W3.3 | STR-R7 |
| W3.11 | **Room (room owner):**<br>- **A separate exit timeout,** `exit_session_s = 5 s`. Today the exit POST reuses `create_session_s` (`room_service/screens.py:2024`). An exit that times out does "exit failed; hard cut to art" (`screens.py:2059`), an unmatched cut on the TVs. Exit awaits a 4K PNG decode, `_prepare` and `_transition_ready` (`stream_service/sessions.py:905-911`), which become worker calls after the split. Exit HTTP time has never been measured.<br>- **Re-split the entry budget:** `create_session_s` 5 → 3 s and `session_ready_s` 5 → 7 s (`room_service/config.py:126`, `:109`). The sum is unchanged, so the worst case stays 29 s.<br>- Send the warm hint, without awaiting, at the start of entry, so the spawn overlaps the freeze and the snapshot.<br>- Why 3 s: today's create HTTP time is pick→starting (p90 0.79 s, max 1.80 s, n=28) plus starting→created (2160p p90 0.81 s, max 3.64 s) [M `evidence/programme/split-map/lifecycle_coldstart.txt`, `session_start_times.txt`]. With 201 at `starting`, create is about pick→starting. (The 3.07 s figure is 2160p starting→ready p90, not create.)<br>- After STR-R7, measure create and exit HTTP p99 over 20 real entries and exits, then set the final values (still summing to 10 s).<br>- **Ships after STR-R7.** | 20 entries and 20 exits with no timing fallback; hint→ready p99 ≤ (measured freeze-and-snapshot p5) + `create_session_s` + `session_ready_s` − 1 s (about 12 s) | 1-1.25 d | STR-R7 | FLK-1 |
| W3.12 | Room-score child (CPU only; live-score, screen-machine-b0): ScoreBuilder plus the PM2S Analyser, fed from bus notes instead of the leader's SoundView (`score/room.py:117-139`). It publishes bars to member workers. b0 confirms the design first. | live-score bars/s ≥ win3's | 1-2 d | W3.4 | STR-R7 |
| W3.13 | VRAM pools sized once at worker start, north conservative (about 0.9 GB free while live [M]); no per-frame pinned host memory | 0 WDDM eviction stalls seen in the trace | in W3.3 | — | — |
| W3.14 | `/api/state` lists the workers: pid, GPU, state, linger left, CPU, RSS, VRAM. Ops shows them on the existing Spectre stream card. | Visible on the card | 0.5 d + ops 0.5 d | W3.3 | STR-R6; SPC |

### W4. Host diet: fewer CUDA calls (second builder from week 3; live-score's own item is b0's)

Scenes are ranked by TV starts × calls per frame from W0.8c, or by airtime if it is computed from session durations.

| ID | What changes (where) | Done when (measured how) | Effort | Needs | Restart |
|---|---|---|---|---|---|
| W4.1 | Rank the scenes | Ranked table | 0.25 d | W0.8c | none |
| W4.2 | **GLSL runtime** (about 17% of TV starts plus most effects): a per-frame uniform arena (one captured H2D copy from a fixed pinned buffer) plus one CUDA graph per program. RawKernel instead of ElementwiseKernel (34 vs 51-60 µs [M]). The parameter step costs 51 µs p50 per plug-in today [M `evidence/programme/native-core/param_step_out.json`]. | The Check 2 path in production: GLSL scene host p50 ≤5 ms | 2-3 d | Check 2 | STR-R8 |
| W4.3 | piano-waterfall (63 TV starts): graph-capture the static launches | That scene's host p50 ≤9 ms at 60p | 1 d | W4.1 | STR-R8 |
| W4.4 | live-art (45): the same | same | 1 d | W4.1 | STR-R8 |
| W4.5 | note-fluid (43): its 20 Jacobi launches become static graph nodes or are fused; `U.set` batched | same | 1-1.5 d | W4.1 | STR-R9 |
| W4.6 | live-score (37; screen-machine-b0): the sync at `plugins/sources/live_score.py:873` becomes double-buffered staging plus `Event.done` | same | 1 d | b0 | STR-R9 |
| W4.7 | Sound analysis once per room, evaluated at each screen's photon time (hook 4-6 ms → ≤2 ms [I]). Under the split this becomes room-level frames sent to each worker. **The stream owner confirms the design** [I]. | hook_ms ≤2 ms | 1 d | W3.4 | STR-R9 |
| W4.8 | Poll events (1.2 µs) instead of blocking syncs (52-85 µs p50, ms tails [M]) on the render thread | 0 blocking syncs per frame in the trace | 0.5 d | W0.3 | STR-R8 |

**W4 done when**, on one screen at 60p:
- host_ms p50 ≤9 ms and p95 ≤14 ms;
- own CPU ≤7 ms;
- 5-15 CUDA calls per frame.

If host p50 stays above 9 ms, FC1 (§2.11) decides what comes next.

### W5. Degradation controller (stream owner; FX budget with screen-machine-ec; card words with the room and display-page owners)

It replaces the repeat EMA, rate_div's own control and the over_budget exit. It never stops a stream.

| ID | What changes (where) | Done when (measured how) | Effort | Needs | Restart |
|---|---|---|---|---|---|
| W5.1 | New `degrade.py`, one per session, inside the worker.<br>- **Signals:** cpu_ms, blocked_ms, GPU span and lag, encoder submit-to-packet, NVML enc% and clocks (from the control at 1 Hz), content fps, and delivery (the glass client's minimum buffered_ms and arrival margin).<br>- It decides at 1 Hz with arithmetic only and applies at a frame boundary.<br>- Down at most 1 step per second, after 1 s sustained (this covers the 4060's clock ramp), with a fast track for gross overruns.<br>- Up after 10 s (30 s for fps) if the projection is <0.7 of the period; the wait doubles after a failed step up.<br>- Frozen across entry and exit.<br>- If config ever puts two 4K60 encodes on one P4000, it degrades fps or bitrate and reports it. It never refuses.<br>- **Shadow mode first.** Unit tests. | In one shadow show on today-like load it names "host" in ≥95% of degraded seconds, and never "GPU" while lag ≤2.7 ms | 1.5 d | W0.2, W0.6, W2.6 | STR-R8 |
| W5.2 | **Act at 30p** with three steps: the FX budget (`fx_director.py:47`; FX interplay, screen-machine-ec), an even scene rate (new content every 2nd slot; `layers.py:126-160`), and halving the sound and HUD rates. Retire the old mechanisms. | Rate changes ≤2/min per screen; content fps ≥ the shadow baseline; never stops | 1 d + ec 0.5 d | W5.1 | STR-R9 |
| W5.3 | Encoder steps: preset P4 → P3, then AQ off. Never while a still is on screen; at a scene change if a new encoder is needed (0.5-1.7 s [M]). | Measured NVENC saving recorded | 0.5 d | W2.5 | STR-R9 |
| W5.4 | **fps step 60 → 30 per screen** (`Reconfigure`, trun 3000, frame id +2) | No stall or judder on devtest, then on one TV | 0.5 d | W2.10, W8.7 | STR-R9 |
| W5.5 | Bitrate step 40 → 30 at an IDR, plus 20 → 12 for the Wi-Fi fallback (15-20 Mbit/s [M]) [I], driven by the delivery signal | The picture survives a forced 20 Mbit/s cap on devtest | 0.5 d | W8.1, W8.2 | STR-R9 |
| W5.6 | Reporting: `degrade{}` in `/api/sessions`; a `session.degrade` event (replaces the session-less rate log); a HUD line; plain words on the Room card, such as "30 fps: computer busy" (display-page owner) | Visible in all four places | 0.5 d + page 0.25 d | W5.1 | STR-R8; FE-2 |
| W5.7 | render_scale as a prepared swap per plug-in; note_fluid sizes its buffers at construction (`note_fluid.py:408`) [U] | Works on the 3 heavy presets | 1 d | W5.2 | STR-R9 |
| W5.8 | A new scene starts at the level its cost table says fits, as `fit_scale` does (`sessions.py:37`) | seahorse and nebula on the P4000 start degraded, not frozen | 0.25 d | W0.6 | STR-R9 |
| W5.9 | Show the owner the thresholds before they harden (owner ask 9) | The owner has seen them | — | W5.2 | none |

### W6. show_service real time (show_service owner screen-machine-e9; show-rx builder screen-machine-3a)

The show-rx builder (screen-machine-3a) has already built the capacity fix. This workstream points at it and does not redo it.

| ID | What changes (where) | Done when (measured how) | Effort | Needs | Restart |
|---|---|---|---|---|---|
| W6.1 | **3a's capacity fix goes live:**<br>- `3ae21cf9`: coalesced on_audio, free hole skips, SO_TIMESTAMPNS, MIDI on its own socket<br>- `1933a8fd`/`d31aaee8`: madmom in `beatproc.py`<br>- `2695e78b`: wiring, `SHOW_BEAT_PROC=on`<br>- `3ecda73a`: per-mic loudness gate<br>Rollbacks: `SHOW_RX_OFF=...` and `SHOW_BEAT_PROC=off`. Whether a show restart has happened since 19:29 is [U]. | Live after the next show restart by e9 or 3a | done | — | SHW-1 |
| W6.2 | Measure it with W0.9a/b in a show with music and the real kiosk piano | Per socket: drops 0; Recv-Q p50 ≤8 KB, p99 ≤64 KB; show-rx ≤70% of a core | 0.25 d | W6.1 | none |
| W6.3 | Only if W6.2 misses: the show_rt / show_brain split owned by the show_service owner (screen-machine-e9) (SHOW_ENGINE §2). SO_RCVBUF is 4 MB (`receiver.py:130`), so a capacity shortfall would show as a hidden queue; bounding it is e9's call. | The W6.2 numbers | 3-5 d | W6.2 | SHW |
| W6.4 | MIDI timing from 3a's kernel stamps: an arrival-to-bus-publish histogram in show health | p99 ≤20 ms | 0.25 d | W6.1 | SHW-1 if ready, else SHW-2 |
| W6.5 | Sound to glass ≤500 ms: the FFT path's 3 s loudness gate, lead and guard (FX interplay screen-machine-ec, with e9) | W0.9d p95 ≤500 ms | 0.5-1 d | W0.9 | SHW / STR |
| W6.6 | Send-cadence counters. Art-Net already reports `rate_hz` and `late_ms` per output (`show_service/outputs.py:169`, `:393-398`). Room's Hue stream (25 Hz, `room_service/hue/output.py:34`) gets one if it is missing [U] (room owner). | §1.3 Art-Net and Hue rows measurable | 0.25 d | — | FLK-1 |

### W7. Box and Windows via Spectre (ops owner, vacant; owner decisions)

| ID | What changes (where) | Done when (measured how) | Effort | Needs | Restart |
|---|---|---|---|---|---|
| W7.1 | `scripts/bg`: nice 19 and `chrt -i 0`. Its jobs run on CPUs 18-19 while any screen is live and 16-19 otherwise; it checks the renderer's sessions every 10 s and re-pins only its own jobs. Plus a CLAUDE.md rule (owner ask 2). The live-score Analyser stays on 16-17 (W1.12). The budget assumed agents' jobs use ≤1.5 cores [I]. | Guest busy ≤ granted − 1 during a show, AV included | 0.25 d | owner | none |
| W7.2 | Stray load (owner ask 4):<br>- the vites on :5188/:5189 (their owning sessions stop them) and the :8080 vite card<br>- dagnet's forced polling (0.81 core [M])<br>- Pylance (2.07 cores in a burst [M]) | −1.5 to −3 cores of guest demand [M/I] | owners | owner | none |
| W7.3 | **Host attribution card** (elevated, read-only): `Process(*)` 2 x 2 s; vmmemWSL priority and whether it can be set; `bcdedit hypervisorschedulertype` (§2.11); Energy Meter / RAPL if present; GPU scheduling priority of vmwp, dwm and Brave; paging; Display event 4101 | The roughly 9.5 unattributed LPs are named [M 18:51] | 1 d | owner ask 3 | SPC |
| W7.4 | The host-perf card from W0.5 | — | (W0.5) | — | SPC |
| W7.5 | **Show-mode card:**<br>- while any worker is live, Brave, Slack, Code, Notion and Carbonite go to BelowNormal plus EcoQoS; nothing closes<br>- Spectre learns liveness by polling the renderer's state through the front door (`:8700/renderer/api/state` [U path]) at 1 Hz<br>- it restores Normal after about 60 s with no live worker, when Spectre starts and at boot, so a crash of the stream or of Spectre can't leave the desktop slowed | Before/after LP split on the host card | 0.5 d | owner ask 7, W3.14 | SPC |
| W7.6 | **GPU clock hold:** a 2-minute A/B (owner ask 8). Then a card that runs `nvidia-smi -lgc <floor>,<max>` on the host while any worker is live, and `-rgc` after the last linger. The same poll and the same restore rules as W7.5 (60 s with no live worker, Spectre start, boot). | 4060 SM clock p50 ≥1,500 MHz while live; kernel time back toward 2.4 ms from 6.2 ms [M synthetic] | 0.5 d | owner | SPC |
| W7.7 | CPU clock cap: the owner reads HWiNFO (owner ask 5), then a BIOS power-limit or cooling fix. Expected 1.5-1.65x on every CPU stage [U]. | % Processor Performance ≥95% under load | owner | — | WIN (BIOS) |
| W7.8 | Clean the P4000 cooler (owner ask 6) | ≤80 °C live; sw_thermal in ≤5% of seconds | owner | no screen live | none |
| W7.9 | Spectre's own polling (30-35 powershell/min, about 54 `wsl.exe bash -lc`/min [M/I]): one long-lived runspace, slower polls when the UI is closed, and the built UI instead of the dev server (0.23 LP [M]) | Spectre's share on the host card halves [I target] | 1 d | — | SPC |
| W7.10 | Security fix (pre-authorised): the router card's password comes off argv (`routerSsh.ts:21`) | Not in `/proc/*/cmdline` | 0.25 d | — | SPC |
| W7.11 | Windows memory (commit 181/195 GB; WSL trimmed from 18.2 to 10.5 GB [M]): track paging against stalls on the host card. If they correlate, the owner decides about Brave's memory. | Correlation recorded | — | W7.3 | none |
| W7.12 | vmmemWSL priority A/B, only if W7.3 shows the root scheduler and the priority can be set | Wake tails before/after | 0.5 d | W7.3, owner | SPC |
| W7.13 | **Later, optional:** a newer WSL kernel with `kernelCommandLine=preempt=full`.<br>- Today's 5.15.167.4 is HZ=100, PREEMPT_NONE, with no PREEMPT_DYNAMIC, so `preempt=full` does nothing on it [M `evidence/programme/gpu-sched-tcc/measured_summary.json`]. Whether the newer kernel has HZ=250 and PREEMPT_DYNAMIC is [U]; read its config first, and read the reported newer-WSL stalls.<br>- `wsl --update` and the `.wslconfig` edit are Windows changes, so they go through Spectre only.<br>- A WSL restart kills every WSL process: Flask (the app, room_service, the show bus), stream_service, show_service, the overlay socket, vites, dagnet, and every agent session's shells and jobs. Tell every session first; it is the owner's choice. | Wake p99 with screens live ≤2 ms (was 6.0 [M]) | 0.5 d | owner | WSL |

### W8. Glass and player at 60p (display-page and room owners, both vacant; stream owner; kiosk owner)

| ID | What changes (where) | Done when (measured how) | Effort | Needs | Restart |
|---|---|---|---|---|---|
| W8.1 | **Glass identity:**<br>- room adds `glass:true` to the driven page's attach and `false` to subscribers' (`room_service/screens.py:690`, `:727`, `:1192`, `:1737`)<br>- the page keeps it and echoes `glass` and `hidden` in its 1 Hz stats<br>- `media.py` stores the booleans<br>- a shared `glass_client()` ranks glass, then visible kiosk, then kiosk, then newest<br>- diag keeps baselines per client (`diag.py:111-125`, `hud.py:271-278`)<br>The diff is in `evidence/programme/glass-60p/proposed_glass_pick_and_hooks.diff`. | diag and the HUD read the TV, never Brave | room 0.25 d, page 0.25 d, stream 0.25 d | — | FE-1 (page, first; old rooms are safe); STR-R7; FLK-1 |
| W8.2 | Page hooks (T3, page half): per-`waiting` events with frame ids, received-fid holes, the minimum arrival margin per second, and a ping/pong clock offset. Old pages are safe, because unknown text is ignored. | Every TV stall classed as upstream, network, seek, decode or drop | page 0.5 d | W0.4 | FE-1 |
| W8.3 | Drop-to-key counted in media time, not frames. `max_queue` is `fps × 0.5 s` (`media.py:138-141`), so a 30p cadence step would mean 1 s. | Unit test at both cadences | 0.1 d | — | STR-R7 |
| W8.4 | **No hard-coded IPs:**<br>- the page takes the media host from `location.hostname` and keeps the path and query (`src/pages/DisplayPage/secureUrls.ts:15-24`)<br>- every consumer of `attach.url` changes first: `src/pages/DisplayPage/live/MseLivePlayer.ts` and `src/pages/DisplayPage/control/DisplayController.ts` [M grep ~20:0x], plus any preview or dev page the stream owner knows of<br>- room calls the stream at 127.0.0.1:8702 (`config/room/room.prod.yaml:3`), so without `--public-base` `attach.url` would read `ws://127.0.0.1:8702/...`. Dropping the flag is safe only once no consumer uses that host.<br>- `--app-url` drives the :8702/dev redirect to the app's console (`stream_service/server.py:87-97`); derive it from the request's Host with port 8700 instead<br>- then drop both flags from `deploy/sm-stream.env:11`. Page URLs don't change. | TVs, HTTPS pages and the :8702/dev redirect still work | page 0.25 d, stream 0.25 d | FE-1 before STR-R7 | FE-1; STR-R7 |
| W8.5 | **60p buffer target** `LIVE_BUFFER_MS` for hevc-2160p60 (`profiles.py:76`). The owner's 17 Oct ruling set 120 ms for "sound to glass under 0.5 s, push hard" (`profiles.py:73-75`).<br>- **Latency sum [I]:** today's south render-to-photon of about 309 ms [M 13 Oct; whether it already includes a 120 ms buffer is U], plus up to 70 ms on the sound side (MIDI ≤20 ms, bus ≤50 ms), gives about 380 ms at 120, 410 at 150 and 460 at 200, against 500.<br>- The glass model gives 0-1 glitches/min at 200 vs 1-6.5 at 120 on 150 Mbit/s links [I `evidence/programme/glass-60p/sim60_table.txt`]. 150 is not in the model [U].<br>- Owner ask 11: 150 (my pick), 200 or 120. | §1.1 glass stalls and §1.3 sound to glass both hold | 0.05 d | owner | STR-R9 |
| W8.6 | 60p bitrate 40M (owner ask 10). It sits inside HEVC level 5.1 Main tier's 40 Mbit/s, at 45% of the powerline; 50M needs a higher level flag. VBV checked by eye. | Picture A/B by the owner | 0.1 d | owner | STR-R9 |
| W8.7 | Chrome 136 plays a mixed 60/30-duration fMP4: devtest first, then one TV in a quiet window (kiosk owner; owner asks 13 and 14) | No stall, drop or judder at the step | 0.5 d | W2.10 | none |
| W8.8 | N95 decode at 2x4K60 at the chosen bitrate (DCL test 3b): recorded 2x4K60 real-content files played on the kiosk in a quiet window, **before W9.2**. Measure video-engine %, dropped frames, Chrome CPU, and the kiosk show-sender's CPU and link gaps (§1.3) while it decodes. Re-read during A1. | Dropped ≤0.1/s per TV; sender gaps 0 | 0.5 d | W2, W8.6 | none |
| W8.9 | Re-run the 24 h powerline soak with a working receiver (93 of 94 pushes failed on 13-14 Oct [M]). `scripts/powerline_soak.sh` pushes 200 MB at line rate every 15 min (about 10 s at 170 Mbit/s) over the link the TVs' video uses, and hard-codes 192.168.1.92 and .10 (`:9`). Change it first: before each push, check the renderer's sessions (as `evidence/programme/encode-spike/run_one.sh` does) and skip the push while any is live, keeping the pings; read the addresses from `.env`. Run it in a window agreed with the kiosk owner. | Capacity p1 over a day known; no push during a live session | 0.25 d + soak | kiosk owner | none |
| W8.10 | Stagger the two screens' IDRs by half a GOP | Burst overlap 0 | 0.1 d | W2.4 | STR-R9 |
| W8.11 | Brave tabs on PC22 (owner ask 12). If they stay, measure the Flask relay CPU at 60p (18.8% of a core at 30p [M]). | The Flask row in §1.3 holds | — | owner | none |

### W9. Switch to hevc-2160p60, then the acceptance shows

The per-screen profile lives in `routes/data/publish-destinations.json`. Room reads that file once at start (`room_service/config.py:270-272`), so each change is a Flask restart: tell the sessions first and verify :5000 after. The fast rollback from 60p is the controller's fps step (W5.4), which needs no restart.

| ID | What changes (where) | Done when (measured how) | Effort | Needs | Restart |
|---|---|---|---|---|---|
| W9.0 | Devtest shares GPU 1 with south. Set devtest to hevc-1080p30 (owner ask 15), because one P4000 encodes about 95 fps of 4K in total [M]. | Config changed | 0.05 d | owner | FLK-2 (with W9.1) |
| W9.1 | South `live.profile` set to hevc-2160p60 | South meets §1.1 in a 10-min show | 0.5 d | W2-W5, W8.7 | FLK-2 |
| W9.2 | North set to hevc-2160p60 | Both screens meet §1.1 | 0.5 d | W9.1, W8.8, W7.6 | FLK-3 |
| W9.3 | Acceptance shows A1 and A2 (§1) | All of §1 | 1 d | W9.2 | none |
| W9.4 | After acceptance, the stream owner decides whether to retire the ffmpeg path. Docs updated: the PORTS.md 8702 row, the CLAUDE.md cards, and REFACTOR_PLAN changes sent to its maintainer. | Done | 0.5 d | W9.3 | STR (if retired) |

### W10 (conditional). Windows-native south (architecture B)

This happens only if FB1 or FB2 in §2.11 is met and none of the W10 stop values apply. It is judged in week 5, after W3 and W7.

**Why not now.**
- **For native:** with one thread alone, which is the worker's design case, Windows' per-call GPU costs are better: p99 0.3-0.6x WSL's [M `evidence/programme/wsl-vs-windows-cuda/runs/analysis.txt`].
- **Against native:** its sleep tails are much worse. A 4 ms sleep is p99 18.1 ms late (14.0 ms with `timeBeginPeriod(1)`), against 2.1 ms in WSL. Beside a busy Python thread its p99s are 2.7-5.6x worse [M, same file].
- **Cost:** native adds 11-13 days and a second platform. It is a measured exit ramp, not the plan.

**The work:**
- a Windows render host (logon task, pythonw, windowless) with its own Spectre card plus `scripts/windows/render-host/*.ps1`;
- `docs/PORTS.md` 8704, registered first (WSL loopback via wslrelay, never LAN);
- workers at HIGH priority in a Job object (kill-on-close, about 6 GB, ≤30% of the box);
- EcoQoS and timer opt-outs, `timeBeginPeriod(1)`, and a single-thread inline-Encode loop;
- a clockmap fit, a Windows CuPy cache warm, a code-hash handshake, and an automatic, reported fallback to WSL workers;
- TCC on the P4000 is an owner choice: admin plus a reboot (WIN), and the P4000 leaves WSL.

Effort: +11-13 d.

### W11 (conditional). One native stage (architecture C)

This happens only if FC1 in §2.11 is met after W4. Then build only a native output clock, encode and egress: about 1-1.5 kLOC behind a flat C ABI called through ctypes. There is no native render path.

### 2.11 Decision points

Each row is a measured trigger, not a gate. Each is read from the flight files and the trace.

| Decision | When | Metric and threshold | Tool | If met |
|---|---|---|---|---|
| **FB1: box share** (Windows-native south?) | Week 5, after W3 and W7.1-W7.6; re-read after the clock fix | South content fps <57 at own CPU ≤7 ms, **and** its late slots track WSL's share of the PC: a per-second correlation r ≥0.5 between south's late slots and the guest's hidden LP wait (or WSL's LP grant) | flight files (W0.5), host card (W7.3) | Evaluate W10 against its stop values |
| **FB2: tails** | Week 5 | More than 0.1% of south's slots are late because of a single CUDA call >2 ms or a wake >2 ms (trace causes), **and** the native bench (W0.8e) shows those tails smaller on Windows | trace (W0.3), W0.8e | Evaluate W10 against its stop values |
| **FB3: egress** | Weeks 4-5, after the egress child (W3.5 fallback) | Egress p99 still >16.7 ms | W0.4 | Find where the time goes (WSL's network hop or our code). If it is the hop, W10's scope includes Windows-side egress [I]. |
| **FC1: one native stage** | Week 7, after W4 | More than 0.1% of slots missed for blocked-time causes (GIL, wake), **and** content fps <57 at own CPU ≤7 ms, with switch interval 1 ms, `gc.freeze` and the output clock all in place | trace, W0.2 split | W11 |
| **W10 stop values** | With W0.8e and after the clock fix | Any one of: Windows worker cold start p90 >10 s; a 4 ms sleep at HIGH priority with `timeBeginPeriod(1)` still p99 ≥2 ms late (14.0 ms today at normal priority [M]); FB1's r below 0.5 once the clock is fixed | W0.8e, host card | W10 is dropped |
| **Scheduler type** (`bcdedit hypervisorschedulertype`) | Week 1 (W7.3) | Root or core scheduler | W7.3 | Root: try the vmmemWSL priority A/B (W7.12) before any W10 work |

---

## 3. Sequencing

**Critical path:**
1. Check 0: roles claimed, the stream owner first.
2. W0.1-W0.3: true numbers.
3. W2: GPU encode.
4. W3.1-W3.6: workers.
5. W4: the diet, by TV starts.
6. W5.4: the fps step.
7. W9: 60p and acceptance.

**Ordering constraints.** These are dependencies, not gates:
- STR-R7 (201 at `starting`, warm hint) ships before FLK-1 (room re-splits its timeouts).
- FE-1 (the location-derived page and every `attach.url` consumer) ships before STR-R7 drops `--public-base`.
- W8.7 (the Chrome check) comes before W5.4.
- W8.8 (the N95 check) comes before W9.2.

| Week | Stream owner | Second builder | Others | Restarts | You see |
|---|---|---|---|---|---|
| 0 | — | — | Check 0 role-claim note (lead); owner asks 1-2 | — | — |
| 1 | W0.1, W0.2, W1.1/3/6/7/9 | W0.8a/b (Check 1, Check 2) | W6.1 show restart (e9/3a); W7.1, W7.2 (owner); W7.3 and the W0.5 card (ops) | STR-R1, SHW-1 | No more 1-3 s freezes; true HUD numbers; music reacts faster |
| 2 | W0.3-W0.6, W0.11, W1.2/4/5/8/10/11/12 | W0.9 kit (after W0.5), W0.8c/d, then the W0.10 baseline | W7.9, W7.10 (ops); W8.9 soak (kiosk owner) | STR-R2, STR-R3 | Fewer stutters every ~20 s (rescan) |
| 2-3 | W2 on devtest in today's process, then the TVs, at 30p | W0.8e native benches | FE-1 (W8.1/W8.2/W8.4 page half) | STR-R4, then SW flips | Every scene holds 30, live-score included; less delay |
| 3-4 | W3.1, W3.2 | W4.1, W4.2, W4.8 | W7.5, W7.6 cards (ops) | STR-R5 | — |
| 4-5 | W3.3-W3.9, W3.14; then W3.10, W8.1 stream half, W8.3, W8.4 stream half | W4.3, W4.4 | W3.12 (screen-machine-b0); W3.11, W8.1 and W6.6 room halves (room) | STR-R6, STR-R7, FLK-1 | Screens independent; the renderer exits when idle |
| 5 | **Decisions FB1-FB3 (§2.11)**; W5.1 shadow | W4 continues | — | STR-R8 | — |
| 5-7 | W5.2-W5.8 | W4.5, W4.7 | W4.6 (b0); W8.5-W8.7, W8.10; owner asks 9-16 | STR-R9, FE-2 | More new frames; the Room card explains step-downs |
| 7 | **Decision FC1**; W9.0 + W9.1, south at 60p | — | W8.8 N95 check with recorded files | FLK-2 | South at 60 |
| 8 | W9.2, north at 60p; A1 and A2; W9.4 | — | — | FLK-3; maybe one STR | Both at 60 |

**Phase exit measures.** These are measured, not gates:
- **Phase 1:** content fps ≥28 at 2x30 on note-fluid; repeats <5%; trace ≤0.3 ms per frame.
- **Phase 2:** encode-thread CPU ≤3 ms per frame; 30p held on live-score.
- **Phase 3:** Check 3 met; cold start p95 ≤8 s; 0 timing fallbacks in 20 entries and 20 exits.
- **Phase 4:** host p50 ≤9 ms at 60p on one screen; the controller names the binding stage correctly.
- **Phase 5:** §1 passes.

---

## 4. Risks, early measurements, and what to do if the answer is no

| # | Risk | Early measurement (when) | If it says no |
|---|---|---|---|
| 1 | PyNvVideoCodec unstable over minutes, or slow tails | Check 1 on each GPU and on both at once (week 1) | ctypes binding to `libnvidia-encode.so.1` (headers from nv-codec-headers 12.1, MIT), still in WSL, no compile |
| 2 | Graph arena too dear from CuPy | Check 2 (week 1) | `pip install cuda-bindings` for `cuGraphExecKernelNodeSetParams`; if still >1 ms per frame, it feeds FC1 |
| 3 | Slot to packet at 30p (about 100 ms with the 3-frame hold) | W2 on devtest (weeks 2-3) | Accept it at 30p (still better than 129-175 ms); EOS mode only after its soak, since it held 57.6 fps |
| 4 | Encode thread starved by the GIL in today's process (one neighbour cut paced 60 fps to 36 [M]) | W2 on devtest, with the trace's encode-thread GIL wait (weeks 2-3) | Switch interval 1 ms (W1.10); less Python per slot; bring W3 workers forward |
| 5 | The split leaves GIL waits, prepare inflation or egress p99 >16.7 ms | Check 3 at STR-R6 (weeks 4-5) | Slice prepares into frame slack; the SCM_RIGHTS egress child; then FB3 |
| 6 | Box supply stays the cap (9.5 unattributed LPs; clock at 66%) | W7.3 (week 1), host card during shows | Show mode, the clock fix, the agent cap; then FB1/FB2 |
| 7 | 4060 down-clocks at 60p duty (960 MHz [M synthetic]) | W7.6 A/B (week 3) | If the owner says no to the hold: warm-up bursts, and the controller degrades and reports "graphics card busy" |
| 8 | 4060 can't hold 4K60 NVENC beside the desktop | Check 1's 4060 run (week 1) | North steps down bitrate or fps through the controller, reported |
| 9 | 4060 VRAM <1 GB while live | W3.13 trace (weeks 4-5) | Smaller pools; previews and devtest on the P4000 while south isn't at 60 |
| 10 | Cold-start tail exceeds room's windows | W3.10, 20 starts (week 5) | Warm hint plus the re-split budget; a resident forkserver **only with your word** |
| 11 | Chrome rejects mixed sample durations | W8.7 (week 6) | Keep the 60 cadence with re-submitted surfaces (saves render, not encode); never a new init mid-stream |
| 12 | N95 can't decode 2x4K60 at 40M | W8.8 (week 7, recorded files) | Bitrate 30-40M via W5.5; report to the owner (a kiosk hardware question) |
| 13 | Powerline dips or Wi-Fi fallback; the sender links share the link | W8.9 soak; §1.3 sender-link rows | Bitrate step down to 12 Mbit/s [I] |
| 14 | live-score heavier than the others (never measured after `ace1bda5`) | W0.8d (weeks 1-2) | W4.6 first; the controller starts it at the level its cost table predicts |
| 15 | Heavy presets (seahorse 12 ms, nebula 15.9 ms on the P4000) | W0.6 cost table | W5.7 and W5.8 start them degraded; never refused |
| 16 | South plus devtest plus previews on one P4000 | W9.0, W3.8 | Devtest at 1080p30; previews placed by headroom |
| 17 | Ownership gaps (stream, room, page, ops) | Check 0 now | Role-claim note; an empty half goes to the renderer builder with your OK. Only the renderer role blocks. |
| 18 | Newer WSL kernels have reported stalls | Read the reports before W7.13 | Stay on 5.15; W1.10 and W3 cover most of the wake-tail cost |

---

## 5. What not to do

- **No gates, trips, self-off, bans or suppression.** Degrade and report. The 3 s hang stop and the in-kernel loop caps (`glsl_compat.cuh:24-33`) stay unchanged. WDDM's 2 s TDR still fires first for a runaway kernel.
- **Every new GPU job has its own in-kernel bound:** the W0.6 hold kernel's cycle cap, and the W3.10 burst through the existing guards.
- **No auth** anywhere in the AV stack, sockets and the switches file included. `/dev/shm/sm-stream/` uses plain file permissions.
- **No labs, side preview servers, staging, worktrees or release holds.** Never add `STREAM_HOLD` to `sm-stream.env`. Bounded benches only, with no screen live.
- **No SCHED_FIFO** and no in-guest priority games. Render threads wait in the guest run queue only 0.17-0.30% of the time [M win1], so in-guest priority buys almost nothing; priority comes from lowering everything else. Don't nice threads inside a process that shares one GIL (priority inversion). Don't pin one AV thread to one vCPU. No isolcpus. Don't raise `fs.pipe-max-size`.
- **Don't render late instead of repeating** before W2.6. Replay showed 5-34x more resyncs.
- **Never change output resolution or the init segment mid-stream.**
- **Never mask in the renderer.** The mask is Chrome-only on art, and live runs at full intensity.
- **Hard cuts only,** no Chrome crossfades. Never TV Game Mode. Never change kiosk or TV URLs.
- **Don't place two 4K60 encodes on one P4000 by config.** If it ever happens, the controller degrades and reports it; it never refuses. No P5 at 60p (it held only 59.4 fps [M]). Don't change HAGS.
- **Don't grow pinned host memory per frame** (no B9 ring growth). Allocate once.
- **No `CUDA_VISIBLE_DEVICES`.** Use `CUDA_DEVICE_ORDER=PCI_BUS_ID` plus `Device(n)`.
- **No warm-sleeping workers** (owner ruling). A forkserver only with the owner's word.
- **Don't start with C++.** No TCC unless W10 is triggered. No WSL restart or Windows reboot without the owner and without telling every session.
- **Windows admin only through Spectre's API.** No hand-rolled netsh or firewall rules. Nothing that opens a window on the desktop.
- **Restart Flask only via Spectre.** Tell the sessions first and verify :5000 after. Kill only your own PIDs, never with `pkill -f`. Never send frames on :8765.
- **No bulk pushes over the kiosk link while a screen is live** (W8.9).
- **Serialise and nice heavy tools** (tsc, eslint, py-spy). No full-filesystem walks.
- **No hard-coded IPs** in new code.

---

## 6. Ops

**New processes** (architecture A). All are children of the stream control: one process group, `PDEATHSIG`, spawned from the control's main thread, and stopped by `scripts/ctl stop stream`.

| Process | Lifetime | Priority | Listens | Card |
|---|---|---|---|---|
| control (`python -m stream_service`, screen `sm-stream`) | always | nice 0 | :8702, unchanged | existing stream card, plus a worker list (W3.14) |
| render worker, one per live screen | on demand, 30 s linger | nice 0; fair share about 1-1.5 cores, one GPU and a fixed VRAM pool, reported and not enforced | none (Unix sockets in `/dev/shm/sm-stream/`) | listed with CPU, RSS and VRAM |
| shared preview worker, one per GPU in use | on demand, 30 s linger | nice 5 | none | listed with RSS and VRAM |
| kernel-prep helper | short | nice 19 | none | — |
| library/rescan child | short | nice 10 | none | — |
| room-score child plus the PM2S Analyser | while live-score is prepared | Analyser nice 19 on CPUs 16-17 (W1.12) | none | — |
| box probe | always, supervised | nice 0 | none | — |
| show beat process (screen-machine-3a) | always | per `SHOW_BEAT_PROC` | none | show card (mllink counts it) |

**Ports.** None are new under A.
- Update the `docs/PORTS.md` 8702 row: workers are children with no listener, and media stays on 8702.
- 8704 is used only if W10 happens, and is registered first.

**Spectre cards** (ops owner; each calls only its own `scripts/windows/<thing>/*.ps1`):
- host-perf (W0.5);
- host attribution (W7.3, elevated, read-only);
- show mode (W7.5, with its automatic restore);
- GPU clock hold (W7.6, with its automatic restore);
- the stream card's worker list (W3.14).

Keep the CLAUDE.md "Screen Machine cards" list current as each card lands.

**Restart batches.** Stream restarts happen only when `GET :8702/api/sessions` is `[]`, devtest included.

| Batch | Contents |
|---|---|
| STR-R1 | W0.1, W0.2, W1.1, W1.3, W1.6, W1.7, W1.9 |
| STR-R2 | W0.3-W0.5, W0.11, W1.2, W1.4, W1.5, W1.10, W1.11, W1.12 |
| STR-R3 | W0.6, W1.8 |
| STR-R4 | W2, shipped with `encoder: ffmpeg` everywhere; then SW flips: devtest, south, north |
| STR-R5 | W3.1, W3.2 |
| STR-R6 | W3.3-W3.8, W3.14, shipped with `workers: inproc`; then SW flips |
| STR-R7 | W3.10, W3.12, W8.1 stream half, W8.3, W8.4 stream half and the flag drop |
| STR-R8 | W4.2-W4.4, W4.8, W5.1, W5.6 |
| STR-R9 | W4.5-W4.7, W5.2-W5.5, W5.7, W5.8, W8.5, W8.6, W8.10 |
| SW | Encoder, workers, degrade, switch-interval and trace flips and rollbacks: no restart |
| FLK-1 | W3.11 plus the W8.1 and W6.6 room halves, after STR-R7 |
| FLK-2 | W9.0 + W9.1 |
| FLK-3 | W9.2 |
| SHW-1 | W6.1 (plus W6.4 if ready); SHW-2 only if W6.4 misses SHW-1 |
| FE-1 | W8.1 page half, W8.2, W8.4 page half |
| FE-2 | Room card words (W5.6) |
| WSL, WIN | Only W7.13 and W10 (TCC), by owner choice |

That is 9 planned stream restarts (plus 1-2 for fixes [I]), 3 Flask restarts, 1-2 show restarts and 2 frontend deploys. Switch flips and their rollbacks need none.

**Rollback.** Switch names are proposals; the stream owner picks the final ones.

| Switch | Rollback |
|---|---|
| `encoder: ffmpeg` (switches file) | Today's encode chain, from the next session start. **Kept as the fallback until A1 and A2 pass.** |
| `workers: inproc` (switches file) | Today's single process (the seam stays in place), from the next session start |
| `degrade: shadow` or `off` (switches file) | At once: the controller computes but does not act (`off` = today's logic until W5.2 retires it) |
| switch interval unset (switches file) | Python default, at once |
| trace `L3` or `off` (switches file) | Recorder minimal, at once |
| `SHOW_RX_OFF=...`, `SHOW_BEAT_PROC=off` | 3a's rollbacks (a show restart) |
| Room timeouts back to 5 s | Revert, then FLK |
| 60p profile | The controller's fps step at once; or the publish-destinations file back plus FLK |
| `dist_prev/` swap | Frontend rollback |
| Spectre "restore" | Each card restores (`-rgc`, priorities back to Normal), and does so by itself after 60 s with no live worker, at Spectre start and at boot |

**Effort.**
- **Stream-side items in total:** about 46-51 days [I, summed from the W tables]:
  - W0 8.8-10.1, W1 3.1, W2 5.5-6, W3 12.25-14.25, W4 6.75-8.25, W5 5.75, W8 0.85, W9 2.55.
- **Split across two builders:**
  - the stream owner, about 35-38 days;
  - the second builder, about 11-13 days (W0.8, W0.9, and W4 except W4.6).
- **Others:**
  - room about 1.5 days, page about 1.25, ops about 5.5;
  - live-score (screen-machine-b0) 2-3;
  - FX (screen-machine-ec) about 1;
  - show_service (screen-machine-e9, with 3a) 0.5-5 (5 only if W6.3 is needed);
  - kiosk owner about 1.25.
- **Calendar:** 7-8 weeks with two builders, or 9-10 weeks with one, at 5 days a week [I].

**Evidence** (all under `docs/perf/evidence/`):
- The diagnosis and designs: `docs/perf/DIAGNOSIS_2026-10-18.md`, `evidence/final/design_profiler.md`, `evidence/final/design_rt-protection.md`, `evidence/final/design_degrade-and-60.md`.
- The split map: `evidence/programme/split-map/seam_leaks.tsv`, `evidence/programme/split-map/process_layout.txt`, `evidence/programme/split-map/lifecycle_coldstart.txt`, `evidence/programme/split-map/session_start_times.txt`.
- WSL vs Windows CUDA: `evidence/programme/wsl-vs-windows-cuda/runs/analysis.txt`.
- GPU encode spike: `evidence/programme/gpu-encode-spike/summary_v2.tsv`.
- Box budget: `evidence/programme/box-budget/budget_table.tsv`. The revised budget with the critics' rows (measured encode CPU, live-score children, preview worker, box probe) is `evidence/programme/box-budget-v2/budget_v2.py` and `evidence/programme/box-budget-v2/budget_v2_out.txt`.
- GPU scheduling: `evidence/programme/gpu-sched-tcc/measured_summary.json`.
- Glass at 60p: `evidence/programme/glass-60p/sim60_table.txt`, `evidence/programme/glass-60p/proposed_glass_pick_and_hooks.diff`.
- GLSL parameter step: `evidence/programme/native-core/param_step_out.json`.
- Scene starts: `evidence/programme/tv_scene_tally.txt`.
- Bus baseline: `evidence/inputs-realtime/bus_12s_analysis.txt`.

---

## Appendix A. Review findings handled differently from the reviewer's suggestion

1. **Name collision:** I renamed the report's windows (win0-win4), not the workstreams. The W ids that peers may already cite stay valid.
2. **W9 restart class:** I chose the publish-destinations file plus Flask restarts (FLK-2, FLK-3), not modes' live specs. The profile is per screen, while modes are flat and per room. The fast rollback is the controller's fps step.
3. **FB1-FB3, M0-M6 and FC1:** I found no definitions on disk under `perf/`, so §2.11 defines them afresh from the evidence. The old "core scheduler drops W10" rule had no evidence behind it, so the scheduler type now decides only W7.12.
4. **The architecture note:** It is now `docs/perf/evidence/programme/architecture-decision.md`. Where its numbers differ (restarts, effort, AV cores, slot to packet), this programme's numbers are the current ones, and the note's owner should copy them.
5. **The hold-kernel cap:** it is sized for 210 MHz, the lowest clock measured (GPU-G4), not 240 MHz. Long holds are split into capped launches, so the cap stays at 50 ms or less.
6. **Acceptance:** A1 and A2 each run once, in ordinary use. The earlier "two separate days" requirement is dropped, so acceptance doesn't turn into QA (CLAUDE.md rule 6).
7. **The 60p buffer pick:** it moved from 200 to 150 ms because of the latency sum in W8.5. 150 is marked [U] because the glass model has no 150 ms case.