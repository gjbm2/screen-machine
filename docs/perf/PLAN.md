# 4K60 on both screens: the plan

18 Oct 2026. Evidence: `DIAGNOSIS_2026-10-18.md` and `evidence/`. (`evidence/programme/superseded-long-programme.md` is the old long version, kept only for its file:line pointers.)

## What limits us (measured)

- **The GPUs are not the limit.** A 4K frame is about 3 ms of GPU work, and the GPU waits on Python.
- **Render thread:** 12.5-13 ms of its own CPU per frame, plus about 7 ms waiting for the one Python lock it shares with the other screen and the web loop.
- **The ffmpeg pipe:** 35-38 ms of CPU per frame per screen spent moving raw 12 MB frames. Two pipes top out at about 50 fps even with no rendering.
- **The renderer's own freezes:** its repeat rule and scene-rate divider act on "GPU" numbers that are really host time.
- **The box:** Windows uses a lot of the CPU, and the CPU runs at about 74% of its rated clock.

## Done: both TVs at 4K60 (20 Oct 23:17, owner's quiet window)

Both TVs ran live at 2160p60 through the real path: own worker, GPU encode, 40 Mbit/s. Measured over 90 s:

| | North TV | South TV |
|---|---|---|
| Frames presented on the TV | 60.0/s | 60.0/s |
| TV dropped frames | 18 (0.2/s) | 17 (0.2/s) |
| TV stalls | 3 | 0 |
| Repeats | 6 of 5,423 | 1 of 5,390 |
| New frames/s (renderer) | 60.0 | 58.9 |
| Render host ms p50 / p95 | 3.1 / 8.5 | 3.0 / 8.4 |
| Blocked (lock) ms | 0 | 0 |
| Slot to packet p50 / p95 | 60 / 66 ms | 63 / 72 ms |
| Encode CPU per frame | 2.3 ms | 2.4 ms |

- **Resyncs and degrade:** 0 resyncs; the degrade controller stayed at "full rate, 60 fps".
- **Kiosk:** 53% CPU idle. The TV drops without stalls (about 0.3%) are probably the N95's decode or compositor: the one thing to watch.
- **Switch:** both TVs are at `fps: 60` in `config/live/stream_switches.json` (ff, 6026d1de).
- **Data:** `evidence/after60/`.

## Measured on the TVs, 20 Oct 22:50 (both live, live-score-paper, 30 fps)

This is the same 90 s capture as the baseline. The like-for-like comparison is the 18 Oct live-score window (W3, old code).

| | North, W3 | North, now (still ffmpeg, in-process) | South, W3 | South, now (GPU encode, own process) |
|---|---|---|---|---|
| New frames/s (of 30) | 7.3-7.6 | **29.7** | 13.4-13.8 | **29.8** |
| Repeated slots | 72-73% | **0%** | 51-53% | **0%** |
| Freezes of 10+ slots | 51-54, longest 173 | **0** | 37-43, longest 129 | **0** |
| Render host ms p50 / p95 | 24-26 / 115-124 | **5.9 / 22** | 19.4 / 88-96 | **5.0 / 13** |
| Lateness (slip) ms p95 | 222-224 | **2.5** | 182-192 | **0.6** |
| TV stalls in 90 s | 120 (W1) | **8** | 120 (W1) | **0** |
| TV dropped frames in 90 s | 108 (W1) | **8** | 113 (W1) | **0** |
| Resyncs | 4-6 per 90 s (W1) | **0** | 4-6 (W1) | **0** |

- **CPU:** stream_service plus encoders went from 3.1 cores (W1, at 19-24 fps) to 1.6 cores (at 30 fps): control 0.72, south worker 0.45, north ffmpeg 0.41.
- **Degrade controllers:** both at level 0 (full rate). North's host load is 0.82 of budget on the old path; south's is 0.57.
- **Data:** `evidence/after1/`.

## Status, 19 Oct 00:45: all five changes are built and live (ff)

| Change | Commit | Measured (ff's bounded proofs) |
|---|---|---|
| 1. True numbers, no self-made freezes | 977b608d | probe frame after 3 repeats; rate divider on real cost; rescan 72 → 0.005 ms |
| 2. GPU encode | c26c81de, plus the gpu_id fix in 47d1e683 | encode 2.2 ms CPU per frame (was 35-38); slot to packet 105 ms at 30p, 55-58 at 60p |
| 3. Worker per screen | 47d1e683 | render host p50 2.1 ms (was 8.8-9.8); blocked 0 (was 5-5.6); cold start 1.9-2.3 s |
| 4. Fewer GPU calls | bb78c069, plus b0's 6e28ab2b | GLSL 15 → 5 calls; note-fluid 30 → 2; live-score worst 78 → 14 ms |
| 5. 60 fps plus a degrade controller | 237ddd83 | 60.0 content fps, encode 2.1 ms, slot to packet 58 ms; 60 → 30 → 60 decodes |

**Per-screen rollout** (`config/live/stream_switches.json`, read at session start, no restart):
- devtest: nvenc + proc at 60;
- south: nvenc + proc at 30;
- north: nvenc + proc at 30 (since fc81da71).

**Still to check:** Chrome playing mixed 30/60 samples, for the degrade controller's fps step (on devtest).

**The first real test of changes 2-5 together** is south's next live show. A 24 h watcher captures it.

## The changes, in order

Each is one build plus one stream restart with no screen live.

1. **Stop the self-made freezes; make the numbers true.** In progress (ff).
   - repeat rule: a fresh frame after 3 repeats;
   - the rate divider fed by real GPU time;
   - session summaries cached;
   - the plug-in rescan off the lock;
   - the pacing sleep fixed;
   - GPU stream priorities;
   - true GPU vs host numbers on the details panel.
2. **Encode on the GPU.** PyNvVideoCodec takes the CuPy NV12 frame straight to NVENC, with no host copy and no ffmpeg.
   - Proven: 4K at 60 fps on the P4000, 2-3 ms of CPU per frame, valid HEVC, forced IDRs, 60↔30 reconfigure.
   - Keep ffmpeg as a switchable fallback.
3. **One render+encode process per screen, started on demand.**
   - Each worker exits 30 s after its screen stops. Cold start is 3-4.5 s.
   - The existing process stays as the control: the API, sessions, directors and media fan-out on :8702, unchanged.
   - Room's create/ready timeouts are re-split, because 5 s is too tight for a cold start.
   - The live-score room transcription becomes a small child that publishes state to both workers (b0's conditions in the old doc, W3.12).
4. **Fewer GPU calls in the hot scenes.**
   - CUDA graphs and one upload per frame, on the GLSL runtime, note-fluid, piano-waterfall, live-art and live-score.
   - 50 launches as a graph cost 0.12 ms instead of 1.2-2.4 ms.
5. **60 fps per screen** (40 Mbit/s, inside the HEVC level).
   - One degradation controller per screen: effects, then scene rate, then fps.
   - It never stops a stream and says why on the panel.

**Show: done.** 3a's show-rx fix (madmom in its own process, MIDI on its own socket, kernel timestamps) has been live since 20:21:51.
- With sound playing and madmom running: Recv-Q max 5 KB, 0 drops, show-rx at 33% of a core, 0% stream loss.
- Before: 320-420 KB queued, about 76 drops/s, show-rx at 45-52% plus 34% waiting for the lock.
- **Piano MIDI**, measured by 3a on 20 Oct: the kiosk's room-piano MIDI arrives on its own :6980 socket with kernel timestamps. Arrival to dispatch is p50 0.44 ms, p95 1.13 ms, max 2.23 ms (58 packets, no notes played yet). 0 drops and 0 misrouted on both sockets.
- **Real playing**, 20 Oct 19:49-20:09: 1,306 note-ons. Bus receipt minus the note's t_ms: p50 −4.8 ms, p95 5.5 ms, max 30.6 ms; 0.6% over 20 ms; 0 bus seq gaps.
  - The negative median is the time map: t_ms dates a key on the room-mic audio path.
  - Key to bus is well under the 20 ms target. Before the fix it was about 1.1-1.4 s.
- **Audio**: socket queue p95 2.4 ms. room-mic (the kiosk boundary mic) is active with 0 loss.
- Still to check: show with both screens live.

## Diagnostics still open, and how to get each

1. **Done, 20 Oct 23:08: the 4060's encoder at 4K60 beside the desktop** (owner OK).
   - 60.07 fps at P4 + AQ 12 and 40 Mbit/s; NVENC busy p50 66%, max 78%; video clock steady at 2,130 MHz.
   - Encode CPU 2.2 ms per frame; slot to packet 54 ms.
   - North is now nvenc + proc (ff, fc81da71).
2. **Why the CPU runs at 74% of its clock.** It isn't the power plan (Balanced, 100% max, aggressive boost). HWiNFO64 in sensors-only mode, under load, shows the cause: CPU package power against PL1, the "power limit" and "thermal throttling" flags, and core temperatures. If it's a power limit, the fix is in the BIOS; if it's thermal, it's cooling.
3. **What else Windows is spending CPU on.** Measured at 20:22: Carbonite backup 1.6 CPUs, a `node.exe` 1.7 (its command line is hidden, so it's probably Spectre's elevated backend), Slack 1.0, Defender 0.2, Brave about 0.9. **Done:** Spectre's `screen-machine-carbonite` card stops CarboniteService while a TV is live, and starts it 60 s after (owner, 20 Oct).
4. **Done 20 Oct 23:17: the kiosk at 2x4K60.** Both TVs presented 60.0 fps, with about 0.2 dropped frames/s each.
