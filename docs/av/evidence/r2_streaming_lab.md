# State of bd's streaming / real-time graphics lab (experiments/stream-latency), read-only review as of 2026-10-13 ~19:35 BST

## 1. What is proven on the real kiosk today (latency, presented %, codecs, modes)?
[high] Two runs give valid numbers. Both used the HEVC 4K30 18 Mbit/s stream from the CUDA fractal source, one lab Chrome over one TV, over powerline (matrix rows 1–2, 18:27–18:28). None of the figures below include the TV's own processing.

(1) MSE, target=2
- Chrome's on-glass estimate: p50 157 ms, p95 190 ms.
- Framebuffer truth (fb_probe): capture→framebuffer p50 191 ms, p95 211 ms.
- 782/900 frames presented (87%); 6 stalls in 30 s.
- Barcode checks 26 ok, 0 bad.

(2) wctrack (WebCodecs→MediaStreamTrackGenerator)
- Chrome's estimate: p50 76 ms, p95 109 ms.
- Framebuffer: p50 112 ms, p95 132 ms.
- 776/900 frames presented (86%).

Chrome's estimate is optimistic against the framebuffer by 19 ms (MSE) and 35 ms (wctrack) at p50.

Server side at 4K (on-device runs):
- render p50 about 10.6–11 ms (p95 about 15)
- pipe about 7–8 ms
- HEVC encode about 12 ms
- net p50 about 6 ms

Codecs:
- **HEVC 4K:** decode and presentation are proven in both mse and wctrack. Linux Chrome has no software HEVC decoder, so this playing at all proves VA-API.
- **AV1 4K and H.264 1080p60 (WebCodecs, hw=prefer-hardware, no software fallback reported):** the decoder produced frames (client stage p50 3.6 and 2.1 ms). Presentation was not measured, because the window was hidden; see the next finding.
- **H.264 4K 25M over MSE:** appended but never measured on screen.
- **H.264 1080p30:** VA-API decode was confirmed in the 2026-10-11 smoke test. Its latency figures were invalid.

Modes: mse and wctrack are proven. The plain 'webcodecs' canvas mode has only been run headless or under Xvfb.

Results from after the display fix are in the 183422 finding.

Not proven:
- glass-to-glass including the TV (TV adds about 35–45 ms via One Connect, per reviews only)
- the ≥99% presented over 30 min pass bar
- a 24 h soak
- streaming over the Wi-Fi fallback
- two independent streams or encoders
- 4K60 streams
- VA-API playback in the production Chromes

The display mode has also changed today: both TVs now run 3840x2160@60 (the owner switched Game Mode ON). Since about 19:22 the kiosk runs one X screen per TV (ZaphodHeads).
-> IMPLICATION: The transport and codec choice (ws-fMP4/MSE or WebCodecs, HEVC 4K30, VA-API on the N95) is technically proven for one window. The screens trail capture by roughly 110–210 ms before the TV, plus about 35–45 ms in the TV. The 4K30 HEVC primary profile (D11) is plausible but not yet accepted against the 99%/30 min bar. All of the AV-spec items still to be measured (two independent compositions, TV-inclusive latency, long soak) remain programme work.
Sources: experiments/stream-latency/runs/20261013-182630/matrix.md:3-10; experiments/stream-latency/runs/20261013-182630/matrix.json (stages fb, expected_minus_fb, info.accel, caps); experiments/stream-latency/README.md:26-32,98-118; docs/KIOSK_INFRASTRUCTURE_NOTES.md:17-19 (updated 19:33); docs/REFACTOR_PLAN.md:311-312,317; media-server/remote-scripts/xorg/20-zaphod.conf; routes/admin_api.py (uncommitted diff: queries :0.0 and :0.1, 'one X screen per TV since 2026-10-13')

## 2. Best hypothesis for why runs 3–8 of the 2026-10-13 matrix presented ~0 frames (evidence only)
[high] The lab Chrome page became hidden (occluded) at about 18:28:30, a few seconds into run 3, and stayed hidden. So rVFC and rAF stopped firing even though networking and decoding kept working. The encoder, network, codecs and session change were not the cause.

Evidence:
(a) **Report cadence.** The page's report setInterval (250 ms) ran at exactly 250 ms until 18:28:20. From 18:28:30 it ran at exactly 1000 ms, and by about 18:32–18:33 at about 15–60 s. That is Chrome's hidden-page timer throttling followed by intensive wake-up throttling (computed from clock.jsonl viewer rows). Viewer c6 in run 183422 shows the same signature when it stopped presenting at 18:43–18:45.
(b) **mse-jump events.** In rows 4–8 these fire at exact 1-second spacing with 'ahead' about 31–33 frames. They come from the 250 ms watchdog (client.js:455-457), now throttled: data kept arriving and was not being consumed.
(c) **Framebuffer probe.** fb_probe saw about 1000 barcoded frames in each of rows 1–2, only 110 early in row 3, then 0 in rows 4–8. The frames really were not on screen.
(d) **Pipeline still working.** Rows 3–7 still have client-stage values (MSE append 0.5–0.7 ms; WebCodecs decoder output 2.1–3.6 ms), net 5–8.5 ms, 0 server drops and MSE rate 1.00.
(e) **Brief visibility in row 4.** Row 4's 38 presented frames all fall at 18:29:30–35, a moment when the report cadence briefly rose. So its '4% at target=1' is not a valid result about target=1.
(f) **Row 8.** Row 8's 'no viewer data in the dwell window' is an artefact of throttled reporting: its rows do appear in frames.jsonl later, after the 3 s REPORT_GRACE_S had passed.

Why the window was hidden: bd's own note (REFACTOR_PLAN.md §3.5, written about 19:27) says kiosk-loop.sh's hot-plug respawn put the kiosk windows on top of the lab window, and that Chrome on X11 stops drawing a window that is fully obscured. I could not confirm the respawn time independently. maintenance.log has 1-minute status granularity and shows nothing. The kiosk-loop log is on the box.

Two speculations, both low confidence:
- The trigger may have been a TV hot-plug when the owner switched Game Mode on. That switch changed the EDID and moved the TVs to 4K60.
- That it coincided with the run-3 session change may be chance.

The design doc §6 (written at 19:07) still calls the cause undiagnosed and frames it as 'after a session change'.
-> IMPLICATION: Rows 3–8 are untested, not failed. H.264 4K over MSE, AV1 4K, H.264 1080p60, MSE target=1 and the pattern-source comparison all need valid re-runs. The design §6 item 5 premise ('session change on a running page failed') is probably wrong and should not drive end-then-start decisions. Any lab-over-TV run needs a stacking or visibility check before and after (plan §3.5 V1 constraints). The page could also log document.visibilityState so such runs void themselves automatically.
Sources: experiments/stream-latency/runs/20261013-182630/clock.jsonl (viewer report intervals: 250 ms until 18:28:20, 1000 ms from 18:28:30, ~60 s from ~18:33); experiments/stream-latency/runs/20261013-182630/matrix.json (fb_frames 978/1017/110/0..., recent_events mse-jump at 1 s spacing, stages.client); experiments/stream-latency/runs/20261013-182630/frames.jsonl (5 s bins: presented 0 from 18:28:35; 38 at 18:29:30-35); experiments/stream-latency/static/client.js:158,435-458,460-494; experiments/stream-latency/server.py:69,982-994; docs/REFACTOR_PLAN.md:313-316,1195; docs/DISPLAY_CONTROL_LAYER_DESIGN_2026-10.md:576-579,599

## 3. What does run 20261013-183422 appear to be testing?
[medium] It is a single long server instance, not a matrix. It started at 18:34:22 and was still writing at 19:28. It holds one encoder session the whole time (session 1, about 97k frames). The stream averages about 75 KB/frame ≈ 18 Mbit/s at 30 fps, with one IDR per second and encode p50 about 12 ms. That matches HEVC 4K30 18M, though the codec is not logged.

Several viewers came and went:
- **18:34–18:35:** c2 alone, 86–88% presented (rVFC), p50 about 133 ms.
- **18:36–18:40:** c2 and c4 concurrently (RTT about 2.6–3.5 ms, so on the kiosk). Both fell to 64–67% presented, with p50 159–173 ms and net p99 about 96 ms. This matches bd's documented blocker: 'each Chrome caps at about 30 fps, with tearing and 31–34% of video frames dropped' with two windows on one X screen.
- **c5/c6 (18:37–18:51):** RTT about 1 ms, so probably not the kiosk. They presented 96–98% until 18:43, then hit the hidden-page signature.
- **Two framebuffer probes** (c1, c3) ran from 18:34 until both died together at 19:22:18. This coincides with check-status at 19:22:21 showing both production Chromes NOT RUNNING, and 'Only 1 display connected' afterwards. That is consistent with the X restart into per-TV screens (zaphod conf at 19:21; admin_api.py now queries :0.0 and :0.1).
- **19:24:57–19:28+:** c8 and c9 connected in the same second (RTT about 3 ms) and play concurrently. Each shows 95–98% presented by rVFC, Chrome-estimated total p50 121–138 ms and p95 137–141 ms, net p99 about 42–44 ms.

The run therefore looks like:
(1) the before/after measurement of two-window presentation on one X screen versus one X screen per TV;
(2) with motion.html, Chrome-flag (LAB_EXTRA_FLAGS) and picom experiments in between.

bd's 19:33 note claims 0.2–0.3% dropped after the fix. The rVFC-based logs show about 2–5% never-presented. That gap is probably a difference in method, perhaps getVideoPlaybackQuality versus rVFC, but it is unverified.
-> IMPLICATION: There is first evidence that per-TV X screens remove the main presentation blocker for two windows: about 65% rises to about 96–98% per window. But the logs hold only about 3.5 minutes of it. The P3 step 5 / P6B gate (≥99% over 30 min, ≤1 stall per 10 min, both windows) is not yet demonstrated in the logs. The measurement method needs reconciling (rVFC versus playback-quality counters) before quoting a drop rate.
Sources: experiments/stream-latency/runs/20261013-183422/frames.jsonl (per-cid and per-minute presented %, sizes, stage medians); experiments/stream-latency/runs/20261013-183422/clock.jsonl (roles: two probes cid 1/3 until 19:22:18; viewer RTTs); docs/REFACTOR_PLAN.md:312; docs/KIOSK_INFRASTRUCTURE_NOTES.md:19; media-server/maintenance.log (status 19:21:21 vs 19:22:21); media-server/remote-scripts/xorg/20-zaphod.conf (mtime 19:21); media-server/remote-scripts/kiosk-loop.sh (uncommitted NSCREENS>=2 branch)

## 4. Have two simultaneous streams (one per TV) been tried?
[high] Two independent streams (different content per TV, two encoder sessions) have not been tried, and the lab server cannot do it. Hub holds exactly one Session. start_session stops the old ffmpeg before starting a new one. on_frame fans that single session out to every viewer. Only two run directories exist for today, and they ran one after the other (182630 ended about 18:34:10; 183422 began 18:34:22). So no second server instance ran concurrently.

Two concurrent decodes of the same stream, one window per TV, have been tried in 183422:
- On the shared X screen: about 65% presented each.
- After per-TV X screens: about 96–98% each by rVFC.

The server sends a separate copy per WebSocket. So about 36 Mbit/s (2×18) did cross the powerline, with net p99 about 42–96 ms.

Still untested:
- concurrent NVENC sessions (design §6 item 8)
- two concurrent 4K CUDA scenes
- two different streams decoding at once on the N95

lab-chrome.sh gained LAB_INSTANCE=2 ('a second lab Chrome, both TVs at once') at 18:45. It still assumes a single DISPLAY=:0 with x=0 and x=3840, which does not match the per-TV X screens now live (:0.0/:0.1). So how c8 and c9 were launched is not recorded.
-> IMPLICATION: The AV spec's core visual requirement, two independent compositions (FrameA and FrameB), needs per-screen sessions in the stream service (P6A §4.6 'one live session per screen'). The lab cannot test it without code changes, or two server processes on two ports as a stopgap. Add an explicit test: two different 4K HEVC streams from two CUDA scenes and two NVENC sessions, decoded on both TVs for 30 min or more. Measure GPU, NVENC and N95 load.
Sources: experiments/stream-latency/server.py:351-399,441-470; experiments/stream-latency/beelink/lab-chrome.sh:8,25-32,55-72; experiments/stream-latency/runs/ (only 20261013-182630 and 20261013-183422 today); experiments/stream-latency/runs/20261013-183422/frames.jsonl (c2+c4, c8+c9 overlapping); docs/DISPLAY_CONTROL_LAYER_DESIGN_2026-10.md:608

## 5. How would a renderer driven by external show-state (per-frame colour/dimmer/activity/pan/tilt) plug into the source abstraction?
[high] **The current contract.** A source is any object with render(frame_id:int, capture_ms:float) -> memoryview of NV12 (w*h*3/2 bytes).
- It is constructed with (w, h, fps, complexity, device).
- It is chosen by a hard-coded if/else on cfg['source'] (pattern|cuda) in Session.__init__, also enforced in validate_cfg and in the argparse choices.
- It is called synchronously on a dedicated 'source' thread, paced on CLOCK_MONOTONIC_RAW, and immediately written to ffmpeg stdin, which blocks.
- It receives no parameters after construction.
- POST /api/session rebuilds source and ffmpeg (new init, keyframe rejoin), so it is unusable for per-frame changes.
- Both sources always burn in the barcode, UTC clock and sweep bar (Stamper).

**The minimal plug-in:**
(1) A new source class, e.g. ShowSource, modelled on CudaSource, given a state provider for its screen: a lock-free 'latest snapshot' reference swapped by a network thread. It reads that once at the top of render().
(2) Per-frame easing at render rate, because state arrives at about 40 Hz against 30/60 fps frames. Use a critically damped or one-pole filter per channel. Integrate animation phase from ACTIVITY (phase += f(activity)·dt) instead of t·f(activity), to avoid jumps.
(3) Pass the values as scalar kernel arguments, as the fractal kernel already takes t, iters and detail: rgb, dimmer as gain, activity, pan/tilt as offsets, zoom, effectIndex as a scene switch with a crossfade.
(4) Register the new kind in validate_cfg, Session and the argparse choices.
(5) Make Stamper optional. It is instrumentation, not content.
(6) Staleness and safety: hold the last state, then ease to a safe look past a timeout. Add a luminance-change or flash-rate limiter for photosensitivity.

**Constraints:**
- In WSL the renderer must be CUDA (CuPy) or CPU. There is no OpenGL–CUDA interop. A Windows-native engine such as TouchDesigner has no frame-handoff path in the lab; the planned 'pipe' source kind is the nearest hook.
- At 4K the serial render (about 10.6 ms on device) plus pipe write (about 8 ms) is about 19 ms. That fits 30 fps but not 4K60 (16.7 ms) without pipelining or zero-copy NVENC, which needs driver ≥570 or a native binding (README).
- Two screens means two sessions, which the lab Hub cannot hold.
-> IMPLICATION: The source seam is small and clean: one method, one thread, one frame cadence. A show-driven renderer is a new source class plus a state feed, not a rewrite. But the plan's stream-service API (§4.6 source kinds synthetic|file|still|pipe) has no generative or show-state kind and no parameter channel. Both must be added to P6A. The renderer-engine decision (in-WSL CUDA versus Windows-native) changes the frame-handoff work materially.
Sources: experiments/stream-latency/server.py:118-153,156-230,372-399,869-885,1094-1096; experiments/stream-latency/lab/source.py:49-85,125-132; experiments/stream-latency/lab/gpu_source.py:19-23,73-114; experiments/stream-latency/lab/encoder.py:51-71,87-94; experiments/stream-latency/README.md:120-128,230-236; experiments/stream-latency/runs/20261013-182630/matrix.json (render p50 10.6-11.0 ms at 4K)

## 6. What does a production stream service still need (per refactor plan P6A)?
[high] **P6A as written** (bd, 4–7 days, in parallel after P2):
1. Copy, don't move, the lab into stream_service/{mux,encoders,sources,transports}.
2. A sessions API (§4.6): POST /api/sessions {screen, source{kind,params}, encode, transports, latency_class, ttl_s, idempotency_key}, plus renew, source switch and DELETE. It includes admission control (one session per screen, an NVENC cap per GPU, GPU per profile), a supervisor and a stall watchdog via ffmpeg -progress.
3. ws-fmp4-mse first at 4K30 HEVC with a 2–3 frame client buffer. Stubs for webcodecs-ws, webcodecs-mstg and ll-hls. Plus the hardening already in the lab: transport-backlog drop-to-key, MSE gap jumps, empty_moov init, skew-aware clock sync. Still to do: MediaSource re-creation on fatal errors, and WebCodecs reconfigure at the next keyframe.
4. Production hooks: a 'ready' signal on the first IDR, per-client backlog and drop-to-key stats in /api/state, ttl_s ≥120, a clean new init on encoder restart.
5. Unit tests (fake encoder), contract tests, an SPS parse on every init segment.
6. sm-stream.service with Restart=always, LAN-bound on :8081, with the lab moving to :3001 (D16); deploy.sh support.
7. A 24 h staging soak and the D10/D11 decision table.

Exit criteria: it survives an encoder kill, TTL expiry ends a session, ready and backlog stats are exposed, stage stats match the lab, and RSS stays flat over 24 h.

**Gaps against the AV spec (my reading):**
- Concurrency: the lab's Hub is single-session, so two concurrent per-screen sessions are real work, not a port.
- A generative or show-state source kind and a per-frame parameter input.
- Optional stamping.
- Measuring two NVENC sessions plus two 4K renders.
- A 4K60 budget, if wanted.
- A formal decision on WebRTC/MediaMTX (spec) versus ws-fMP4 (plan; WHEP needs UDP into WSL).
- The P6B LiveLayer in the display page.
- The P3 blockers: LAN origin, production VA-API check, display fix exit, Chrome hold.
-> IMPLICATION: P6A as planned productionises a single-purpose 'put one synthetic stream on a screen' service. For the room AV programme it needs three additions: multi-session per screen, a show-state-driven renderer source, and a parameter feed. These should be scoped in now, before the §4.6 API is frozen with bd.
Sources: docs/REFACTOR_PLAN.md:283-292,555-558,967-996,1000-1038,1126-1127,1132; docs/REFACTOR_PLAN.md:139 (sources/{synthetic,file,still,pipe}.py); experiments/stream-latency/server.py:56-66,337-349,441-470 (lab already has backlog drop-to-key)

## 7. What interface/contract would the lab most naturally consume for show-state?
[medium] **Recommended: a local WebSocket carrying JSON, latest value wins, at about 40 Hz.**

Why it fits the lab:
- The lab server is aiohttp and already speaks typed JSON over WebSocket, with per-connection roles (viewer/probe; 'hello', 'report', 'ping' messages).
- It already has an epoch and ping/pong clock-sync scheme.
- A show-state producer can simply say hello with a new role (e.g. 'show') and send {type:'show-state', ...}, or use a dedicated /ws/show path. The Hub stores the latest snapshot per screen and the source thread reads it per frame.
- TCP gives connection liveness for free, which makes staleness detection easy.
- TCP also crosses the Windows→WSL boundary (portproxy or localhost forwarding) if the Art-Net receiver has to live on the Windows side. Inbound LAN UDP cannot reach WSL under NAT plus TCP-only portproxy, and to my knowledge WSL NAT localhost forwarding is TCP-only too.

The alternative: UDP JSON on localhost is equally simple when producer and renderer both run inside WSL. It has no head-of-line blocking and naturally gives latest-wins semantics. But the lab has no UDP listener today.

Suggested payload: the AV spec's canonical schema per screen:
- screenA / screenB: {rgb:[0..1]x3, dimmer, activity, motion:[pan,tilt], zoom, effectIndex}
- plus mode, seq, t_src in ms, and maestro.artnetAgeMs

Semantics:
- no queueing; the renderer interpolates
- hold, then ease to a safe look after a staleness timeout (about 0.5–2 s)
- expose per-screen state age in /api/state

Avoid /api/session (it restarts the encoder) and /api/client (it goes to the browsers) for this.

Latency budget for the AV plan: a state change waits 0–33 ms for the next 30 fps tick. Then render, pipe and encode take about 30 ms, the network about 6–9 ms p50, and the client buffer and presentation follow. That gives framebuffer p50 about 190 ms (MSE target 2) or about 110 ms (wctrack), plus about 35–45 ms in the TV. The screens will therefore trail Maestro's physical DMX by roughly 150–270 ms.
-> IMPLICATION: Define the show-state contract as a versioned JSON message (schema plus latest-wins, staleness and safe-look rules) owned by the room/show-state service. The renderer subscribes; it never parses Art-Net. Choose WebSocket if the Art-Net receiver may run on Windows; either works if everything stays in WSL. Plan visual design for 'smooth correlation, not sync': the visuals lag the lights by about 0.15–0.27 s.
Sources: experiments/stream-latency/server.py:812-860,869-900; experiments/stream-latency/static/sync.js; /tmp/claude-1000/-home-gjbm2-dev-screen-machine/954809c3-b227-4c7a-9aec-b8a50dbbfd3f/scratchpad/AV_SPEC.md:244-267; /tmp/claude-1000/-home-gjbm2-dev-screen-machine/954809c3-b227-4c7a-9aec-b8a50dbbfd3f/scratchpad/CONTEXT_NOTES.md:16-17; experiments/stream-latency/runs/20261013-182630/matrix.json (fb stage p50 191 / 112 ms)

## SURPRISES
- The TVs now run 3840x2160@60, not 4K30, because the owner switched Game Mode ON on 2026-10-13. D24 (Game Mode OFF) is reopened. CONTEXT_NOTES' 'TVs run 4K@30' and 'Game Mode OFF decided' are out of date (docs/KIOSK_INFRASTRUCTURE_NOTES.md:18,23; REFACTOR_PLAN.md:317).
- The kiosk's X layout changed today at about 19:22: ZaphodHeads, one X screen per TV (:0.0 north, :0.1 south). It came with uncommitted edits to kiosk-loop.sh and routes/admin_api.py (production code, modified at 19:32) and to media-server/restart-kiosk.sh. check-status (cron) now reports 'Only 1 display connected' and showed both production Chromes NOT RUNNING at 19:22:21. Expect monitoring noise. lab-chrome.sh still assumes a single :0 screen with x=0 and x=3840.
- Matrix rows 3–8 were not failures of codec, transport or session change. The lab page was hidden/occluded, which produces the throttled-timer signature in clock.jsonl. The design doc §6 (19:07) still says 'undiagnosed' and frames it as 'after a session change' (item 5). REFACTOR_PLAN §3.5 (19:27) attributes it to kiosk-loop respawn restacking. Row 4's '4% at target=1', which the lead notes cite, is contaminated and not a result.
- Run 20261013-183422 is not a stub: it is a 54-minute-plus live session. It holds the two-window-on-one-X-screen measurement (about 65% presented each) and the first post-ZaphodHeads two-window measurement (about 96–98% each by rVFC). bd's notes claim 0.2–0.3% drops after the fix, which does not match the rVFC never-presented rate in the logs (about 2–5%); the measurement methods differ.
- The lab server holds exactly one encoder session for all viewers. The AV spec's two independent compositions (FrameA and FrameB) cannot be tested without code changes. The refactor plan's stream-service source kinds (synthetic|file|still|pipe, §4.6) include no generative or show-state-driven source and no per-frame parameter channel.
- Both lab sources always burn the barcode, UTC clock and sweep bar into every frame. A production renderer must make this optional.
- On-device 4K render p50 is about 10.6–11 ms (p95 about 15), not the README's '~4 ms incl. copy+stamp'. The serial render plus pipe write (about 19 ms) would not fit a 4K60 frame budget of 16.7 ms.
- The barcode readback costs about 68 ms p95 on the main thread per check (every 30 frames) on the N95. That may cause rVFC under-reporting, inflating 'never presented' counts.
- The AV spec's transport (H.264 over WebRTC via MediaMTX, 1080p60 first) is at odds with what is proven and planned: ws-fMP4/MSE or WebCodecs, HEVC 4K30 primary (D10/D11). WHEP would also need UDP into WSL.

## OPEN QUESTIONS
- What hid or occluded the lab window at about 18:28:30: a kiosk-loop respawn after a hot-plug, perhaps from the TV Game Mode or EDID change, or something else? The kiosk-loop log on the box would settle it; it was not read (no ssh).
- Were matrix rows 1–2 (18:27) shown at a 4K30 or 4K60 panel refresh? The time of the owner's Game Mode switch is not recorded.
- Which method backs bd's '0.2–0.3% dropped' after the display fix (getVideoPlaybackQuality? fb_probe?), and how does it reconcile with the rVFC data (about 2–5% FLAG_NEVER for c8/c9)?
- Were c8/c9 (19:24:57 onward) two lab Chromes, one per X screen, or production windows navigated to the lab client? lab-chrome.sh as it stands targets DISPLAY=:0 only.
- Has the post-fix two-window result been held for 30 minutes or more (pass bar ≥99% presented, ≤1 stall per 10 min)? Only about 3.5 minutes appear in frames.jsonl so far.
- Concurrent NVENC capacity on the RTX 4060 with driver 566.14 for two 4K HEVC sessions plus two CUDA scenes. Could the Quadro P4000 (HEVC, no AV1) host the second screen?
- Will the show renderer be in-WSL CUDA/CuPy (fits the lab's source seam) or a Windows-native engine such as TouchDesigner (needs a new frame-handoff path)?
- Where will the Art-Net receiver run, given that inbound LAN UDP cannot reach WSL? That decides whether the show-state feed into the renderer must cross Windows→WSL, which favours a TCP WebSocket over UDP.
- Is 30 fps content on the now-60 Hz panels the target, or is 4K60 generation wanted? 4K60 would need render/pipe pipelining or zero-copy NVENC (driver ≥570).
- Will the owner formally drop the AV spec's WebRTC/MediaMTX transport in favour of the lab's ws-fMP4/MSE (D10)?