# Stream latency lab

A test rig to answer two questions: **can the workstation stream real-time GPU-generated video that the
Chrome kiosks show, and what is the latency from "frame generated" to "frame on the TV"?**

It is self-contained (nothing in the Flask app, the scheduler or the kiosk pages is touched) and
measures every stage of the pipeline separately, so a slow number can be attributed to the encoder,
the network, Chrome or the display.

```
 workstation (WSL2)                                                  kiosk mini-PC (N95)            TV
 ┌───────────────┐  NV12  ┌────────────┐ FLV  ┌──────────────┐  WS   ┌──────────────────────┐ HDMI ┌────┐
 │ lab/source.py │──pipe─▶│ ffmpeg     │─────▶│ server.py    │──────▶│ Chrome client.js     │─────▶│    │
 │ frame+barcode │ 1 MiB  │ *_nvenc    │      │ fMP4/frame,  │ fMP4  │ MSE | WebCodecs |    │      │    │
 │ +UTC clock    │        │ -delay 0   │      │ fan-out,stats│◀──────│ WebCodecs→MSTG<video>│      └────┘
 └───────────────┘        └────────────┘      └──────────────┘reports└──────────────────────┘
        t_capture   t_rendered   t_written   t_encoded   t_send   t_recv   t_ready   t_present/expected
                                                                   (+ fb_probe.py: frame seen in X framebuffer)
```

## What was measured on the real hardware (2026-10-11; read-only except the changes marked Enabled / Since / Fixed)

| Item | Finding | Consequence |
|---|---|---|
| Kiosk box | AZW/Beelink MINI S, Intel N95 (Alder Lake-N, i915), 4 cores, 7.5 GiB, Ubuntu 24.04, **Chrome 136.0.7103.113** | ~94 % idle, 5 GiB free: room for a test Chrome |
| Hardware decode | Was off (no libva / iHD driver, `gjbm2` not in `render`, no VA-API flag). **Enabled 2026-10-11** in the production kiosks: `beelink/enable-hwdecode.sh` + `--enable-features=AcceleratedVideoDecodeLinuxGL` in `kiosk-loop.sh` | The VA-API driver exposes H.264, HEVC, VP9 and AV1 decode (`vainfo`); Chrome hardware decode is verified for H.264 so far (lab Chrome on the box). Before, everything was software-decoded on 4 Atom cores |
| Display mode | **Since 2026-10-13: 3840×2160 @ 60 Hz on both TVs** (owner switched Game Mode on; the EDID gained the HDMI Forum block), and **one X screen per TV** (north `:0.0`, south `:0.1`; `media-server/remote-scripts/xorg/20-zaphod.conf`). Until then: both TVs ran **3840×2160 @ 30 Hz**. EDID: 2025 Samsung, HDMI 1.4-class (340 MHz), no HDMI-Forum block, 4K60 only as 4:2:0. The kernel offers 4K50/60 4:2:0; **Xorg prunes them** | 33 ms per frame sets the floor. 4K60 needs Samsung's *Input Signal Plus* On for the kiosk connector (documented for this model, QE75LS03FW; see `docs/FRAME_PRO_SETTINGS_2026-10.md`) or Xorg `MaxClock` (4:2:0 only). 1080p @ 60/100/120 is available today |
| Network | **Since 2026-10-13: powerline Ethernet is primary** (`enp1s0`, 192.168.1.10): 167–188 Mbit/s to the box, loaded ping p99 ~40 ms, 0 % loss; Wi-Fi (~15–20 Mbit/s, `wifi-prefer-ap` pinned to the fast AP) is the fallback | The full `matrix-device.json` (up to 25 Mbit/s) now fits, with headroom for the production pages. Check `ip route show default` is on `enp1s0` before a run; on Wi-Fi fallback keep bitrates under ~10 Mbit/s (`docs/KIOSK_INFRASTRUCTURE_NOTES.md` §4–5) |
| Path to WSL | Workstation LAN IP 192.168.1.205; Windows `netsh portproxy` forwards **8081** to WSL (a manual rule) | Lab server listens on 8081. `fix-wsl-networking.ps1` does not rebuild 8081/3001: after a WSL IP change re-add them (`docs/KIOSK_INFRASTRUCTURE_NOTES.md` §1) |
| Encoder | RTX 4060 + Quadro P4000, driver 566.14. distro ffmpeg 6.1.1 `h264/hevc/av1_nvenc` work inside WSL (AV1 on the 4060 only) | **Frame in → packet out: ~7 ms at 1080p60, ~21–27 ms at 4K30** (1 MiB pipes), zero frame delay |
| Workstation clock | Until 2026-10-11 16:19 WSL's `CLOCK_MONOTONIC`/`REALTIME` ran **~2 % fast** and the wall clock stepped back ~0.65 s every 32 s (WSL's chrony vs Ubuntu's timesyncd). Fixed; check with `python3 ../../scripts/wsl_clock_check.py` (from this directory) | Lab runs before the fix have server-side stage values stretched by ~2 %. The retained pre-fix runs (`runs/20261011-14*`) also predate the skew-aware sync, so their cross-host stages (net/client/present/total/fb) carry up to ~20 ms/s of offset drift. Re-run anything that matters |
| H.264 trap | NVENC's SPS has no VUI `bitstream_restriction`, so Chrome's VA-API H.264 decoder must assume reorder depth = DPB size (≈5 frames at 4K) | `lab/bitstream.py` rewrites the SPS (`max_num_reorder_frames=0`); validated bit-exact. HEVC/AV1 from NVENC need nothing |

Encoder alternatives that did **not** work at this driver: PyAV wheels (bundled FFmpeg 8 needs NVENC API 13 /
driver ≥ 570) and PyNvVideoCodec (works, but holds output back by 3 frames).

### Chrome internals that decide the design (verified against the 136.0.7103.113 sources)
- VA-API on X11 needs only `--enable-features=AcceleratedVideoDecodeLinuxGL` (default off in 136). The
  often-quoted `VaapiVideoDecoder` / `VaapiVideoDecodeLinuxGL` names are ignored since M131. Zero-copy also
  needs `--use-angle=gl-egl` + `AcceleratedVideoDecodeLinuxZeroCopyGL`. Chrome opens `/dev/dri/renderD128`
  itself, hence the `render` group.
- MSE is treated as **live** (low-delay rendering: 1 buffered frame instead of 4; decoder low-delay) only if
  the init segment has `mvhd` duration 0 and no `mehd`. Our muxer does this; Chrome logs
  "Video rendering in low delay mode." `MediaSource.duration = Infinity` does **not** do it.
- WebCodecs `optimizeForLatency` does not change VA-API H.264 reordering, so the SPS fix is needed there too.
- WebCodecs needs a secure context: a plain-http LAN origin is not one. `lab-chrome.sh` adds
  `--unsafely-treat-insecure-origin-as-secure=<origin>` (alternatively use an `ssh -R` tunnel to localhost).
- Two kiosk windows on one 7680×2160 X screen cannot page-flip: Chrome's frames are copied at vblank
  (≈ +1 frame, tearing near the top). Modelled floor at 30 Hz ≈ 50–67 ms **plus TV processing**. Measured on
  2026-10-13 it was worse than modelled: ~30 fps per TV and 31–34 % dropped video frames. **Fixed the same day
  with one X screen per TV** (see "First device results" below).
- Frame Pro input lag (rtings and other reviews): ≈ 9 ms in Game Mode and ≈ 69 ms outside it at 4K60 on the
  panel's own Micro-HDMI port; through the Wireless One Connect box (our path) ≈ 35–45 ms even in Game Mode.
  The owner prioritises picture quality over latency. The source-checked TV settings for this model are in
  `docs/FRAME_PRO_SETTINGS_2026-10.md` (Game Mode Off); don't change TV settings just for a test.

## Stages reported (all on the server's clock)

| stage | from → to | notes |
|---|---|---|
| `render` | capture → frame rendered | burned-in clock shows the capture time |
| `pipe` | rendered → written to ffmpeg stdin | WSL pipe copy |
| `encode` | written → FLV tag out of ffmpeg | NVENC + mux |
| `queue` | encoded → handed to the WebSocket | server fan-out |
| `net` | sent → WebSocket message handled in the page | includes portproxy, Wi-Fi, main-thread delay |
| `client` | received → ready | MSE: `appendBuffer` done; WebCodecs: decoder output |
| `present` | ready → Chrome's expected on-glass time | MSE: rVFC `expectedDisplayTime`; `wctrack`: first BeginFrame at/after rVFC `presentationTime` + 1 refresh (estimate, flag 8); `webcodecs`: rAF + 1 refresh (estimate, flag 8) |
| `total` | capture → Chrome's expected on-glass time | the headline number (excludes TV processing) |
| `fb` | capture → frame seen in the X framebuffer | `beelink/fb_probe.py`; independent of Chrome's own timing |
| `expected_minus_fb` | Chrome's estimate − framebuffer truth | negative = Chrome is optimistic |

Clock sync runs over the same WebSocket and is skew-aware: the page (`static/sync.js`) and `fb_probe.py` keep
20 s of ping samples, take the low-RTT envelope and least-squares fit offset **and rate** (`a + b·t`), and convert
their timestamps to the server clock before reporting. Until 5 envelope samples spanning 2 s exist, they use the
single lowest-RTT offset. Every server start has a random **epoch** id (in every `config` and `pong`); reports
made against another epoch are dropped and counted as `items_stale`. Each report's fit quality (rtt, ppm, residual,
n) is logged to `runs/<ts>/clock.jsonl`, and the server warns when skew exceeds 100 ppm.
Frame identity comes from the media timestamp (`mediaTime`/`VideoFrame.timestamp` = frame id / fps) and is
cross-checked by reading the barcode back from the screen every N frames (`barcode ok/bad`).

Server behaviour worth knowing:
- `/api/state` and the matrix only count frames captured more than 3 s ago (`REPORT_GRACE_S`), so frames that
  are never presented are reported before a window is closed. `matrix.md` has per-run columns for bitrate,
  client mode and params, presented/produced, not presented (overdrawn/never), drops, MSE ahead/rate/stalls,
  connections, clock sync (rtt/ppm) and notes; failed runs get a row with the error.
- `POST /api/session` merges into the last requested config under a lock; invalid configs get HTTP 400.
- Intra refresh was removed (it cannot coexist with drop-to-keyframe); configs with `intra_refresh` are rejected.
- Optional flow control: a page that sends `{"type": "ack", "fid": N}` gets at most a few frames in flight. The
  shipped `client.js` does not send acks yet.
- Known limit: whenever the low-RTT envelope has fewer than 5 samples or spans under 2 s, `sync.js` and
  `fb_probe.py` fall back to offset-only (rate 0), anchored on the lowest-RTT sample, which can be up to 20 s old.
  That happens after a (re)connect and can happen mid-run on a jittery link. In a simulated jittery Wi-Fi model at
  the old +2 % skew the error reached ~400 ms; with the workstation clock fixed (tens of ppm) the same case is about
  1 ms. The page holds report rows for up to 8 s after (re)connecting until the fit is valid (`SYNC_HOLD_MS`);
  `fb_probe.py` does not hold them, so discard its first seconds.
- Before the 2026-10-11 clock fix the server clock (`MonotonicEpochClock`, on `CLOCK_MONOTONIC`) was not smooth,
  and cross-host stage times were off by up to ~50 ms during those episodes. On the first device run, check
  `runs/<ts>/clock.jsonl` (ppm near 0, small residuals) before trusting the per-stage numbers.

## First device results (2026-10-13, both TVs, powerline Ethernet)
Live CUDA fractal, HEVC 3840×2160 @ 30 fps, 18 Mbit/s, MSE player with target 2 frames, shown in both production
kiosk Chromes at once (navigated over CDP with `--allow-production`, then put back).

| | One shared 7680×2160 X screen | One X screen per TV |
|---|---|---|
| Chrome `droppedVideoFrames` | 34.4 % / 31.1 % (~6 min, two lab Chromes) | **0.28 % / 0.24 %** (16/5788, 14/5796; ~3 min) |
| `static/motion.html` (no video; browser draws a moving bar) | ~30 fps, ~210 missed refreshes per ~760 frames, tearing | **60.0 fps, 0 missed** |
| Server `total` p50 (capture → on screen, before the TV's own ~35–45 ms) | – | **~126 ms** (p95 127–143): enc 12, pipe 9, net 9, client 1, present ~85 |

How the cause was isolated, stage by stage: `tools/stream_smoothness.py` showed the **encoded** stream is smooth
(bar moves a constant 64 px per frame, nothing missing); VA-API was **decoding** (video engine 9–16 % busy);
`static/motion.html` showed the loss with no video at all, so it was the **display path**: with one shared X
screen no window can be page-flipped. Full write-up: `docs/KIOSK_INFRASTRUCTURE_NOTES.md` §3.
`present` (~85 ms) is mostly the MSE buffer target (2 frames = 67 ms at 30 fps); a lower target trades latency for
stalls (see the local results below). The matrix in `matrix-generative.json` has not been run on the TVs yet.

### 4K60 video on both TVs: fails on X11, works on Wayland (tested 2026-10-13)

**Under X11** (18:52-19:02 UTC; the kiosk's display server until 19:31 UTC):
Same live CUDA source as above at HEVC 3840x2160 @ 60 fps, 30 Mbit/s, MSE with a 4-frame (67 ms) buffer, both TVs.
The encoded stream was smooth (`tools/stream_smoothness.py`: 0 missing, constant 32 px/frame).

| Chrome video path | Result |
|---|---|
| Default (136, production Chromes, ~3 min) | **82 % / 74 % frames dropped**; page drawing 31-42 fps; the GPU's VideoEnhance (video-processing) engine at **97-100 %** at ~1000 MHz; decode engine not saturated; network into the box 59-63 Mbit/s; workstation NVENC 30 %, GPU 36 % |
| Zero-copy (`--use-angle=gl-egl` + `AcceleratedVideoDecodeLinuxZeroCopyGL`), Chrome 136 and an unpacked 155.0.8059.39, also with `--enable-native-gpu-memory-buffers` | **Does not play**: "Could not find SharedImageBackingFactory ... (Y_UV, 420 ...)", the GPU process exits (8704) three times, then HEVC is reported unsupported |
| WebCodecs to canvas (136) | Worse: page 14-16 fps, 34-42 % of frames presented, latency > 1 s |

Cause: on X11 Chrome's working VA-API path converts every decoded NV12 frame to RGB on the VideoEnhance engine
(one copy per frame); two 4K60 streams are 120 4K conversions per second, more than the N95's engine does. At 4K30
(60 conversions per second) it fits, hence the 0.2-0.3 % result above. The box is not power-limited (package PL1
20 W, PL2 25 W). Untested routes to 4K60: a Wayland kiosk compositor (Chrome's dma-buf/zero-copy import and video
overlays are a different code path there); 4K60 on one TV with 4K30 on the other; a mini-PC with a stronger GPU.

Why zero-copy crashes on X11 (source-traced and confirmed on the box): Chrome decides NV12 is usable because EGL can
import NV12 dma-bufs, but on X11 the only SharedImage factory that takes a native-pixmap frame also asks Mesa's GBM,
whose format list has no NV12 (`gbm_device_is_format_supported(NV12) = 0` on the box). The in-process SharedImage
interface then marks the GPU context lost and ANGLE forces a GPU-process exit (code 34, logged as 8704). Wayland's
surface factory does not make that GBM check.

**Under Wayland** (sway 1.9, the kiosk since 19:31 UTC; `KIOSK_VIDEO=zerocopy`), same streams and harness:

| Test, both TVs | Result |
|---|---|
| Pre-made HEVC 4K60 file (`static/test-4k60.mp4` via `static/play.html`, ~3 min) | **3 dropped of ~8,600 per TV (0.03 %)**; page 60.0 fps / 0 late; VideoEnhance 0 % |
| Same file, right after a reboot (25 s) | 2 and 1 dropped of ~1,450 |
| Live HEVC 4K60 30 Mbit/s, `target=4` (~3 min) | 6.0 % / 7.3 % dropped; page 60.0 fps / 0 late; total p50 ~130 ms |
| Live, `target=4&rc=0` (no speed-up) | 7.7 % / 7.4 %: slow drift behind live, then 1 s jumps |

The live drops are the workstation side: the generator misses its 16.7 ms deadline ~2 % of the time
(`/api/state` `late_source_frames`; render + a 12 MB pipe write per 4K frame in WSL), and the player's live-edge
controller (`rate = 1 + 0.06 x error`, up to 1.5x) forces Chrome to drop frames whenever it runs above 1.0x with
60 fps content on a 60 Hz panel. A production player must never exceed 1.0x and must resync by a hard jump; the
renderer must pace frames evenly. Test file: `ffmpeg -f lavfi -i "testsrc2=size=3840x2160:rate=60,format=nv12" -t 40
-c:v hevc_nvenc -preset p4 -b:v 30M -maxrate 30M -bufsize 30M -g 60 -tag:v hvc1 -movflags +faststart static/test-4k60.mp4`
(git-ignored). Under sway, `beelink/lab-chrome.sh` and `beelink/fb_probe.py` do not work (X11-only); these runs
navigated the production Chromes over CDP (`cdp_probe.py <port> navigate <url> --allow-production`) and put the
display pages back afterwards.

## Results so far (local only — NOT a latency result on the kiosk)
One smoke run on the real kiosk (2026-10-11 13:59 UTC, about 1 min, lab Chrome over the south TV, `wctrack`,
H.264 1080p30 at 8 Mbit/s) confirmed VA-API decode (`VaapiVideoDecoder` in the Chrome log, HUD `wc-hw true`). Its
HUD figures (glass p50 ~1.27 s, 14 frames per 5 s) are **not** valid latency results: the box was on the slow AP
(~4.5 Mbit/s, below the stream rate), the WSL clock still ran 2 % fast, and the harness predates the review fixes.

Headless / Xvfb Chrome-for-Testing 136 on the workstation (software decode, no real display) was used to
validate the plumbing: all three client modes play, barcode checks match (an off-by-one under MSE is a
draw-time race and is counted separately), stage attribution works, and the framebuffer probe works.
Numbers there are not meaningful for the TV. What they do show:

- **Server side is fast.** Capture → encoded packet: ~7 ms at 1080p, **~26 ms at 4K** (GPU fractal 6 ms +
  pipe 8 ms + HEVC/H.264 encode 11–14 ms). Network hop on the workstation itself < 1 ms.
- **MSE** behaves as expected in low-delay mode: buffer target 2 frames ≈ 110 ms with few stalls;
  0.3 frames ≈ 40 ms with constant stalls. The live-edge target is a latency/stall trade-off.
- **Chrome's own on-glass estimate is optimistic** on Xvfb by 15–37 ms versus the framebuffer probe;
  the device run will show how far off it is on real hardware.
- **4K live video needs hardware decode.** In live/low-delay mode Chrome's software H.264 decoder uses
  slice threading only, so a single-slice 4K stream decodes on one core: ~6 fps and 1–2 s behind even on
  the Xeon. On the N95 it would be worse. (`--slices 4` helps only the software fallback.) Linux Chrome
  has no software HEVC decoder at all, so HEVC playing at all proves VA-API works.

## The GPU-generated source
`--source cuda` renders an animated, domain-warped Julia fractal with fine moving detail (hard to
encode) in one CUDA kernel straight to NV12, via CuPy in WSL (CUDA works in WSL; OpenGL there has no CUDA
interop). Measured: **4K frame in 2.8 ms of GPU time on the RTX 4060 (3.2 ms on the P4000)**, ~4 ms
including the copy to pinned memory and the barcode/clock stamp. It needs its own venv
(`python -m venv .venv-lab && .venv-lab/bin/pip install -r requirements-lab.txt`) so the production
`.venv` is not touched. Tested 2026-10-11 (in a scratch venv; `.venv-lab` itself has not been built and run yet)
with Python 3.12.3, driver 566.14, ffmpeg 6.1.1, cupy-cuda12x 14.2.0, nvidia-cuda-nvrtc-cu12 12.9.86,
nvidia-cuda-runtime-cu12 12.9.79, aiohttp 3.14.5, websockets 17.2, numpy 2.5.4, opencv-python-headless 5.0.0.93.

## Running it

### 1. Offline checks (no browser, no kiosk)
```bash
cd experiments/stream-latency
../../.venv/bin/python tools/validate_mux.py            # NVENC -> FLV -> fMP4, bit-exact decode, SPS fix
```

### 2. Local browser check (Chrome for Testing 136, the kiosk's build)
Get the same build as the kiosk: `curl -o chrome-linux64.zip
https://storage.googleapis.com/chrome-for-testing-public/136.0.7103.113/linux64/chrome-linux64.zip`. WSL has no
`unzip`, so extract it with Python's `zipfile` and restore each entry's mode (`(info.external_attr >> 16) & 0o777`),
then pass `--chrome <dir>/chrome-linux64/chrome`.
```bash
../../.venv/bin/python server.py --host 127.0.0.1 --port 8091 --codec h264 --size 1280x720 --fps 30
../../.venv/bin/python tools/local_chrome_test.py --chrome /path/to/chrome --server http://127.0.0.1:8091 \
    [--port 9333] [--profile-root /scratch/dir]
```
A local Chrome shares the server's clock, so the fitted clock skew it prints should be ~0 ppm (the test
warns above 200 ppm); the real cross-host skew only shows on the kiosk.

### 3. On the kiosk — needs the owner's go-ahead; each step is reversible
1. Check the network first: `ssh gjbm2@192.168.1.92 'ip route show default'` should say `dev enp1s0`
   (powerline, ~170 Mbit/s). If it says `wlp2s0` the box is on its Wi-Fi fallback: keep bitrates under ~10 Mbit/s. Leave the TV settings
   as they are (see "Chrome internals" above).
2. Hardware decode is **already enabled** on the kiosk (2026-10-11). On a fresh box: (a) run
   `enable-hwdecode.sh` as below (it runs `apt-get update`, then installs 7 packages, 8 with `--with-gpu-top`, and
   adds `gjbm2` to `render`/`video`; restarts nothing); (b) deploy the repo's
   `media-server/remote-scripts/kiosk-loop.sh`, which carries the flag; (c) decode takes effect at the next
   `kiosk.service` restart or reboot:
   ```bash
   scp beelink/enable-hwdecode.sh gjbm2@192.168.1.92:/tmp/ && ssh -t gjbm2@192.168.1.92 sudo bash /tmp/enable-hwdecode.sh --with-gpu-top
   ```
   It refuses if apt would remove or upgrade anything, and runs apt with `NEEDRESTART_SUSPEND=1
   NEEDRESTART_MODE=l`: Ubuntu 24.04's needrestart hook would otherwise restart services that map a
   replaced library, `kiosk.service` included (both TVs blank). It lists what was already stale first.
   Keep the ssh session up until it prints `done` (an interrupted dpkg needs `sudo dpkg --configure -a`).
   The running kiosk Chromes are untouched; the next kiosk restart or reboot starts them with the new
   groups and the iHD driver present.
3. Start the server on the workstation (8081 is already port-proxied):
   `../../.venv/bin/python server.py --codec h264 --size 1920x1080 --fps 30`
4. Put a test Chrome over **one** TV (the kiosk keeps running underneath; `stop` reveals it again):
   ```bash
   ssh gjbm2@192.168.1.92 'mkdir -p /tmp/lab' && scp beelink/*.sh beelink/*.py gjbm2@192.168.1.92:/tmp/lab/
   ssh gjbm2@192.168.1.92 'bash /tmp/lab/lab-chrome.sh start "http://192.168.1.205:8081/client.html?mode=wctrack" south vaapi'
   ssh gjbm2@192.168.1.92 '~/.venv/bin/python /tmp/lab/cdp_probe.py 9224 gpu; ~/.venv/bin/python /tmp/lab/cdp_probe.py 9224 media 6'
   ```
   Don't rely on `gpu`: its decode-profile list stays empty until `chrome://gpu` has been opened once in that
   Chrome. `media` lists only `<video>`/MSE players, so `kIsPlatformVideoDecoder: true` applies to `mode=mse`.
   In `wctrack`/`webcodecs` modes, hardware decode shows as the HUD's `wc-hw true`, the client's capability report
   in the server console (`webcodecs_prefer-hardware: true`, `powerEfficient: true`), or
   `ssh gjbm2@192.168.1.92 'grep -m3 VaapiVideoDecoder /tmp/chrome-lab/chrome.log'`.
   `lab-chrome.sh` only accepts `north|south` and `vaapi|vaapi-zerocopy|none`, and refuses to start unless
   `xrandr` shows a connected output at the chosen screen's position (with one TV, "south" would land on
   the production screen). With one X screen per TV (since 2026-10-13) it puts "north" on `:0.0` and "south"
   on `:0.1`. If kiosk-loop respawns the production window after a hotplug, it lands on top and the hidden lab
   Chrome stops drawing; restart the lab Chrome. It starts `/opt/google/chrome/google-chrome`, not `google-chrome-stable`, so
   kiosk-loop's hotplug `pkill` and check-status's kiosk count ignore it (an explicit admin "restart
   kiosk" still closes it). It runs at nice +10 and raises its processes' oom_score_adj to 500 after
   start, so production wins under CPU or memory pressure. Chrome sets its own values, so renderers
   it creates later are back at 300, the same as production; `status` shows both values. `stop` finds
   it by its profile dir only.
   `cdp_probe.py` refuses `eval`/`navigate` on the production ports 9222/9223 unless `--allow-production`.
5. Optional framebuffer probe (start it after the lab Chrome; Ctrl-C or closing the ssh session stops it):
   `ssh gjbm2@192.168.1.92 '~/.venv/bin/python /tmp/lab/fb_probe.py ws://192.168.1.205:8081/ws --screen-x 3840'`
   It shares Xorg with the production Chromes, so it keeps ONE X connection, reuses its images, polls
   at 200 Hz by default (`--hz`, keep >= 4x the panel refresh) at nice +10, and backs off when the
   barcode is not visible. It follows session changes and reconnects after a server restart. Every 10 s
   it prints its clock sync (rtt, skew in ppm, fit residual). Its first-seen times go to the server
   already on the server clock, using the same skew-aware sync as the page. Before a full matrix, do a
   dry run and watch `top -p $(pgrep -x Xorg)` plus the probe's `grab_ms_max` (in `/api/state`) at
   200 Hz against `--hz 120`. If Xorg CPU rises noticeably or `grab_ms_max` is above a few ms, use the
   lower rate or leave the probe out.
6. Watch the server console (stats every 5 s) or `curl http://localhost:8081/api/state`; switch modes live with
   `curl -XPOST localhost:8081/api/client -d '{"mode":"mse"}'` and encoders with
   `curl -XPOST localhost:8081/api/session -d '{"codec":"hevc","size":"3840x2160","bitrate":"10M"}'`,
   or run a sweep: `server.py --matrix matrix-device.json` (→ `runs/<ts>/matrix.md`). **`matrix-device.json`
   assumes a wired (powerline) link** (live since 2026-10-13): 8 of its 16 runs are at 15–25 Mbit/s, above the
   Wi-Fi ceiling. If the box has fallen back to Wi-Fi, copy only the 8M/12M rows into a `matrix-wifi.json`. Its three `source: cuda` rows need the server started from `.venv-lab/bin/python` (see
   "The GPU-generated source"); from `../../.venv` they fail and are recorded as error rows.
7. Clean up: `ssh gjbm2@192.168.1.92 'bash /tmp/lab/lab-chrome.sh stop && rm -rf /tmp/chrome-lab /tmp/lab'`
   (copy `chrome.log` off first if you need it).

### 4. True glass-to-glass (includes the TV)
The software stages stop at the framebuffer. For the TV's own processing, open `/clock.html` on a phone
(server-synced UTC clock), hold it next to the TV and photograph both (or film in slow-motion): the
difference between the clock in the photographed frame and the phone's clock is capture → glass
(± the phone screen's own lag). A photodiode on a microcontroller (≥ 2 kHz) is the precise alternative;
the Yocto-Light-V5 on the kiosk tops out at 20 Hz and cannot do it.

## Files
- `lab/source.py` CPU test pattern + the shared `Stamper` (barcode, UTC clock, sweep bar; complexity 0–3)
- `lab/gpu_source.py` CUDA fractal renderer (`--source cuda --render-gpu 0|1`)
- `lab/encoder.py` ffmpeg NVENC subprocess, FLV reader thread
- `lab/bitstream.py` FLV parsing, H.264 SPS rewrite, avcC/hvcC/av1C, codec strings
- `lab/fmp4.py` init segment + per-frame `moof`/`mdat`
- `server.py` aiohttp server: WebSocket fan-out, clock sync, per-stage stats, test matrix
- `static/client.html|js`, `static/sync.js`, `static/clock.html` browser side
- `beelink/` scripts for the kiosk: `enable-hwdecode.sh`, `lab-chrome.sh`, `cdp_probe.py`, `fb_probe.py`
- `tools/validate_mux.py`, `tools/local_chrome_test.py` local validation
- `tools/stream_smoothness.py` records the live stream, decodes it and checks the sweep bar moves evenly (is the
  *encoded* stream smooth?)
- `static/motion.html` display-path test with no video (`?speed=px/s`, `?fps=30`); `window.motionStats` over CDP
- `matrix-device.json` the planned device sweep; `matrix-generative.json` CUDA-source sweep (HEVC/H.264/AV1, 4K30
  and 1080p60); `runs/` logs (git-ignored)

## Next steps after the first device run
- If the numbers are good: replace the test fractal (`--source cuda`) with the real content renderer (CUDA via
  CuPy/PyTorch in WSL — OpenGL in WSL has no CUDA interop). Zero-copy CUDA→NVENC without added frame delay needs
  driver ≥ 570 (PyAV/FFmpeg 8) or a small native NVENC binding (PyNvVideoCodec works today but adds 3 frames).
  The current pipe costs ~8 ms per 4K frame.
- 4K60 (Input Signal Plus or Xorg `MaxClock`), a wired link (powerline), and a single full-screen window per
  output (page-flip instead of copy) are the three biggest levers outside the encoder. **All three are in place
  since 2026-10-13** (4K60 with Game Mode On; powerline primary; one X screen per TV).
