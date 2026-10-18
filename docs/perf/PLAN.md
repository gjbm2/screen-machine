# 4K60 on both screens: the plan

18 Oct 2026. Evidence: `DIAGNOSIS_2026-10-18.md` and `evidence/`. (`evidence/programme/superseded-long-programme.md` is the old long version, kept only for its file:line pointers.)

## What limits us (measured)

- **The GPUs are not the limit.** A 4K frame is about 3 ms of GPU work, and the GPU waits on Python.
- **Render thread:** 12.5-13 ms of its own CPU per frame, plus about 7 ms waiting for the one Python lock it shares with the other screen and the web loop.
- **The ffmpeg pipe:** 35-38 ms of CPU per frame per screen spent moving raw 12 MB frames. Two pipes top out at about 50 fps even with no rendering.
- **The renderer's own freezes:** its repeat rule and scene-rate divider act on "GPU" numbers that are really host time.
- **The box:** Windows uses a lot of the CPU, and the CPU runs at about 74% of its rated clock.

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
- Still to check: piano MIDI timing, and the numbers with both screens live.

## Diagnostics still open, and how to get each

1. **The 4060's encoder at 4K60 beside your desktop.** It's a 10-second test. My permission rules blocked it, because GPU 0 is the desktop card. With no screen live, run this in a WSL terminal:
   `D=/home/gjbm2/dev/screen-machine/docs/perf/evidence/programme/encode-spike; CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES=0 timeout 70 nice -n 19 <venv>/bin/python $D/nvenc_spike.py --name g0 --json /tmp/g0.json --extra aq=12 --mode paced --frames 600 --fps 60 --bitrate 40000000`
   The venv needs `pip install PyNvVideoCodec==2.2.3 cupy-cuda12x numpy`. Read `fps_achieved` and `submit_to_packet_ms_steady` in /tmp/g0.json. Alternatively, allow the run and I'll do it.
2. **Why the CPU runs at 74% of its clock.** It isn't the power plan (Balanced, 100% max, aggressive boost). HWiNFO64 in sensors-only mode, under load, shows the cause: CPU package power against PL1, the "power limit" and "thermal throttling" flags, and core temperatures. If it's a power limit, the fix is in the BIOS; if it's thermal, it's cooling.
3. **What else Windows is spending CPU on.** Measured at 20:22: Carbonite backup 1.6 CPUs, a `node.exe` 1.7 (its command line is hidden, so it's probably Spectre's elevated backend), Slack 1.0, Defender 0.2, Brave about 0.9. Pausing Carbonite during shows is the quick win.
4. **The kiosk at 2x4K60.** Play two recorded 4K60 files on the kiosk in a quiet window, and read the dropped frames.
