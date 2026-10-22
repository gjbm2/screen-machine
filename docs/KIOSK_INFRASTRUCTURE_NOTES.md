# Kiosk infrastructure notes

Measured facts, changes made and open items for the display chain. Started 2026-10-11 during the real-time
streaming investigation (`experiments/stream-latency/`). For day-to-day operation of the mini-PC see
[media-server/README.md](../media-server/README.md); for the streaming plan see [REFACTOR_PLAN.md](REFACTOR_PLAN.md).

**Times:** the mini-PC logs in UTC; workstation times are UK local (BST = UTC+1). Times below are marked.

**Owner priorities (2026-10-11):** 4K picture quality matters more than ultra-low latency, given the screen size
and how close people stand. Running a cable to the drawing room is impossible; "ethernet over powerline … is
really the only viable option in that location".

## Status at a glance (updated 2026-10-13)

| Area | State | Section |
|---|---|---|
| Hardware video decode on the mini-PC | **Enabled** (VA-API) and **working in the production Chromes**: both played live 4K30 HEVC on 2026-10-13, which Linux Chrome can only do in hardware | [2](#2-hardware-video-decode-va-api) |
| Display mode | **3840x2160 at 60 Hz** on both TVs since 2026-10-13 (the EDIDs now carry the HDMI Forum block; it appeared around when the owner tried Game Mode, but does not depend on it). Until then 4K30 only | [3](#3-display-mode-and-smooth-motion) |
| Kiosk display server | **Wayland (sway 1.9) since 2026-10-13 19:31 UTC** (`kiosk.service`); the X11 kiosk is the disabled fallback `kiosk-x11.service` | [3](#wayland-sway-kiosk-since-2026-10-13-4k60-on-both-tvs) |
| Smooth video / motion | **4K60 on both TVs works** under Wayland: pre-made 4K60 HEVC played with 0.03 % dropped, drawing 60 fps, 0 late; survives reboot, Chrome crash and TV unplug. (Under X11: one X screen per TV fixed 4K30 judder, but 4K60 dropped 74-82 %.) Live 4K60 from the lab generator still drops 6-7 %: workstation-side pacing | [3](#wayland-sway-kiosk-since-2026-10-13-4k60-on-both-tvs) |
| Wi-Fi | Now the **fallback** link. Weak, ~15-20 Mbit/s ceiling; access-point lock installed to stop moves to the slow AP | [4](#4-wi-fi) |
| Wired network | **Powerline Ethernet live since 2026-10-13, primary route**: ~170-190 Mbit/s, loaded ping p99 ~40 ms; Wi-Fi fallback tested; 24 h soak running | [5](#5-powerline-ethernet-primary-since-2026-10-13) |
| Workstation (WSL) clock | Ran fast for months (2 % when measured, +4.6 % average since the July boot, at times ±10 %); **fixed** | [6](#6-workstation-wsl-clock) |
| Samsung TV picture settings | Source-checked list for the QE75LS03FW in [FRAME_PRO_SETTINGS_2026-10.md](FRAME_PRO_SETTINGS_2026-10.md). Now: both TVs report Filmmaker Mode with Game Mode on Auto (Samsung IP Control, 19:16 and 19:37 UTC); Auto engages only on an ALLM request, which the kiosk never sends. 4K60 does not depend on Game Mode: all the sway 4K60 measurements were taken in that state, and toggling Game Mode On and back at 19:36 UTC changed neither EDID nor the outputs. Owner, 2026-10-13: no Game Mode, ever (Game Bar overlay and flicker) | [3](#3-display-mode-and-smooth-motion) |
| Git | **None of the 2026-10-11/13 work is committed yet** | [7](#7-open-items) |

## 1. Physical chain

```
Workstation (Windows + WSL2, Flask :5000)                    drawing room
  WSL eth0 (NAT) <- Windows netsh portproxy <- LAN -> router -> powerline -> mini-PC enp1s0 (primary, .10)
                                                        `-> Wi-Fi AP --------> mini-PC wlp2s0 (fallback, .92)
                                                                         | HDMI-1, HDMI-2
                                                     Wireless One Connect box (one per TV)
                                                                         | proprietary wireless link
                                                                Samsung The Frame Pro (2025)
```

- Mini-PC: AZW/Beelink "MINI S", Intel N95 (Alder Lake-N, i915), 7.5 GiB, Ubuntu 24.04, hostname
  `screen-machine-drawingroom`, LAN IPs 192.168.1.10 (Ethernet, primary since 2026-10-13) and 192.168.1.92
  (Wi-Fi; admin scripts still use this one, it stays reachable over Wi-Fi). Two kiosk Chromes, one per TV, side by side on one
  7680x2160 X screen (`kiosk-loop.sh`).
- Chrome is 136.0.7103.113. It is **not** apt-held: it stays at 136 only because the apt timers and
  unattended-upgrades are masked ([README §2](../media-server/README.md#2-disable-all-automatic-updates)). Since
  the 2026-10-11 `apt-get update`, a manual `apt upgrade` would install Chrome 155.x. The VA-API rules in §2 were
  verified for 136 only, so either `sudo apt-mark hold google-chrome-stable` or re-verify §2 after any upgrade.
- Workstation LAN IP 192.168.1.205. The Windows port proxy forwards 8080 (to WSL 5000), 5000, 8765 and 2222 (to 22);
  those are rebuilt by `fix-wsl-networking.ps1`. **8081 and 3001 are manual rules** (used by the streaming lab and
  the throughput tests) that the script does not rebuild; after a WSL IP change re-add them with
  `netsh interface portproxy add v4tov4 listenport=8081 listenaddress=0.0.0.0 connectport=8081 connectaddress=<WSL IP>`.
- Both TVs are Samsung The Frame Pro **QE75LS03FWUXXU**. The panel's only HDMI input is a Micro-HDMI port; the
  normal input is its Wireless One Connect box, and the mini-PC plugs into one HDMI port on each box.

## 2. Hardware video decode (VA-API)

**Before:** no VA-API driver (libva/iHD) installed, `gjbm2` not in the `render` group and no Chrome flag, so every
video was software-decoded on the N95's 4 cores. Linux Chrome has no software HEVC decoder at all, and
single-slice 4K live H.264 in software (low-delay mode) reached only ~6 fps even on the workstation's Xeon, so the
N95 cannot do it.

**Changed 2026-10-11 (owner approved):**
1. Packages (0 upgraded, 0 removed): `intel-media-va-driver-non-free` 24.1.0, `vainfo` 2.12.0, `intel-gpu-tools`
   1.28 (for `intel_gpu_top`), plus the dependencies `libva2`, `libva-drm2`, `libva-x11-2`, `libva-wayland2` and
   `libigdgmm12`. Script: `experiments/stream-latency/beelink/enable-hwdecode.sh` (runs `apt-get update`, refuses if
   apt would upgrade or remove anything, suspends needrestart so `kiosk.service` is not restarted mid-install).
   Side effect: the box's package lists now date from 2026-10-11 (about 380 packages upgradable, none upgraded).
2. `gjbm2` added to the `render` and `video` groups (it was in neither; Chrome opens `/dev/dri/renderD128` itself).
3. Chrome flag `--enable-features=AcceleratedVideoDecodeLinuxGL` added to `CHROME=` in
   `media-server/remote-scripts/kiosk-loop.sh`, deployed to `/usr/local/bin/kiosk-loop.sh` (byte-identical to the
   repo copy; the previous copy is kept as `/usr/local/bin/kiosk-loop.sh.bak-20261011-140753`, a 14:07 UTC
   timestamp), `kiosk.service` restarted.
4. Also installed 14:17 UTC, for Wi-Fi diagnostics only: `iw` 6.7 (0 upgrades). `wifi-prefer-ap` does not need it.

`deploy-kiosk.sh` does none of parts 1-2 or 4: on a fresh box run `enable-hwdecode.sh` and deploy the repo's
`kiosk-loop.sh`; hardware decode takes effect at the next `kiosk.service` restart or reboot.

Rules that matter (verified against Chrome 136 sources):
- Only `AcceleratedVideoDecodeLinuxGL` is needed; the old `VaapiVideoDecoder`/`VaapiVideoDecodeLinuxGL` names
  are ignored since M131.
- Keep **all** features in **one** `--enable-features` switch. A second one (e.g. via `CHROME_EXTRA`)
  replaces the first.

What was verified: in a lab Chrome on the box (13:59 UTC) Chrome's log showed `VaapiVideoDecoder` and the page
reported hardware WebCodecs decode for H.264. For the production Chromes (14:08 UTC): both carry the flag and their
GPU processes run in the `render` group with `/dev/dri/renderD128` open. On 2026-10-13 (18:25-18:28 UTC) both
production Chromes played the lab's live 4K30 HEVC stream through MSE with 0.2-0.3 % dropped frames; Linux Chrome
has no software HEVC decoder, so that playback was hardware decode.

Verify:
```bash
# on the mini-PC: the driver
LIBVA_DRIVER_NAME=iHD vainfo --display drm --device /dev/dri/renderD128 | grep -E "Driver version|VAProfile(H264|HEVC|AV1)"
# from WSL, while a <video>/MSE clip plays on a kiosk (WebCodecs players are not listed):
ssh gjbm2@192.168.1.92 'mkdir -p /tmp/lab' && scp experiments/stream-latency/beelink/cdp_probe.py gjbm2@192.168.1.92:/tmp/lab/
ssh gjbm2@192.168.1.92 '~/.venv/bin/python /tmp/lab/cdp_probe.py 9222 media 8'   # VaapiVideoDecoder, kIsPlatformVideoDecoder: true
```
`cdp_probe.py` needs `~/.venv/bin/python` (system python3 lacks `websockets`). Don't use the DevTools
`SystemInfo` decoder list (`cdp_probe.py … gpu`) as the test: it stays empty until `chrome://gpu` has been opened.

Undo: remove the flag from `kiosk-loop.sh` and redeploy;
`sudo apt-get remove intel-media-va-driver-non-free vainfo intel-gpu-tools`, then `sudo apt-get autoremove --dry-run`
and run it for real only if it lists just `libva2 libva-drm2 libva-x11-2 libva-wayland2 libigdgmm12`;
`sudo gpasswd -d gjbm2 render; sudo gpasswd -d gjbm2 video`.

## 3. Display mode and smooth motion

- **Now (2026-10-13):** both TVs run 3840x2160 at 60 Hz; both EDIDs contain the HDMI Forum block (OUI C4-5D-D8) and
  the outputs list 4K60. Game Mode: both TVs report Filmmaker Mode with Game Mode on Auto (Samsung IP Control, 19:16 and 19:37 UTC); Auto engages only on an ALLM request, which the kiosk never sends. 4K60 does not depend on Game Mode: all the sway 4K60 measurements were taken in that state, and toggling Game Mode On and back at 19:36 UTC changed neither EDID nor the outputs. Owner, 2026-10-13: no Game Mode, ever (Game Bar overlay and flicker). Whether Input Signal Plus is on has not been read from the TVs;
  the EDID behaves as if it is. The bullets below describe the state until then.
- Until 2026-10-13 the EDID each Wireless One Connect box gives the mini-PC (identical on both outputs) has only an HDMI 1.4 vendor
  block (max TMDS 340 MHz) and **no HDMI Forum block**. So 3840x2160 RGB is possible only at 30 Hz; 4K50/60
  (VIC 96/97) are offered only as YCbCr 4:2:0, which the kernel lists but **Xorg 21.1.12 prunes** (9 4K modes in
  `/sys/class/drm/card1-HDMI-A-1/modes`, 5 in `xrandr`).
- Effect: 33 ms per frame; 1080p at 60/100/120 Hz is available today.
- Samsung's 2025 e-Manual for this model: with **Input Signal Plus** Off an input takes 4K50/60 only as 8-bit 4:2:0
  and the PC-native mode is 4K30; with it On, 4K60 RGB/4:4:4 at 8 or 10 bit and PC-native 4K60. Path: Settings >
  All Settings > **General & Privacy > External Device Manager > Input Signal Plus**, then pick the kiosk's
  connector (it is per connector). Our EDID matches the Off state.
- Ways to 4K60: (a) Input Signal Plus On for the kiosk input (preferred; change one TV first, see below), or
  (b) xorg.conf `Option "MaxClock" "600MHz"` to keep the 4:2:0 modes (4:2:0 halves colour resolution in each
  direction, a quarter of the colour detail, so it is a poor fit for art).
- Stills already arrive as full 4K RGB 8-bit at 30 Hz today, so Input Signal Plus mainly buys 60 Hz (smoother
  slow video) and HDR acceptance, not sharper stills. The N95's HDMI tops out at 594 MHz TMDS without FRL, so
  4K60 RGB 8-bit is the best this PC can send.
- TV input lag: on the panel's own Micro-HDMI port about 9 ms in Game Mode and 69 ms outside it at 4K60 (rtings);
  through the Wireless One Connect box about 35-45 ms at 4K60 (TechRadar, id.nl, Tom's Guide; picture mode not
  stated; rtings only says the box significantly increases lag).
- **TV settings:** the source-checked list for this model (connection, picture, Game Mode, input label, power and
  AI features), what must be checked on the TV itself, and the corrections to an earlier withdrawn list are in
  [FRAME_PRO_SETTINGS_2026-10.md](FRAME_PRO_SETTINGS_2026-10.md). Headline: Input Signal Plus On for the kiosk
  connector; Movie mode, Gamma 2.2, Colour Tone Warm2, Colour Space Auto, other processing off; Game Mode Off
  (rtings: true 4:4:4 needs the PC label *and* Game Mode, a trade-off to test); ambient/AI brightness, Motion
  Lighting, Auto Power Saving and Auto Power Off off. Software updates reset picture settings. Game Mode Off and
  4K first follow from the owner's priority above. **Nothing has been changed on the TVs yet.**
- Verifying a change from the mini-PC. Baseline: [kiosk-captures/](kiosk-captures/) — `edid-2026-10-11.txt` (two
  status lines, then one base64 EDID line per output) and `xrandr-verbose-2026-10-11.txt`. New capture and compare
  (install `edid-decode` in WSL first):
  ```bash
  ssh gjbm2@192.168.1.92 'for c in /sys/class/drm/card1-HDMI-A-1 /sys/class/drm/card1-HDMI-A-2; do base64 -w0 $c/edid; echo; done' > edid-new.txt
  ssh gjbm2@192.168.1.92 'for s in 0 1; do DISPLAY=:0.$s xrandr --current --verbose; done' > xrandr-new.txt   # one X screen per TV
  sed -n 3p docs/kiosk-captures/edid-2026-10-11.txt | base64 -d | edid-decode    # baseline, HDMI-A-1
  sed -n 1p edid-new.txt | base64 -d | edid-decode                                # new, HDMI-A-1
  ssh gjbm2@192.168.1.92 'grep -c ^3840x2160 /sys/class/drm/card1-HDMI-A-1/modes'   # quick check; 9 today
  ```
  Working Input Signal Plus should add an HDMI Forum VSDB (OUI C4-5D-D8, max TMDS 600 MHz, SCDC), move VIC
  96/97 into the normal video block, and make `xrandr --verbose` list 3840x2160 at 60 Hz (594 MHz). `--current`
  reads X's cached state and the TV may only re-send its EDID on a hot-plug. Risk: 4K60 RGB is an 18 Gbps signal;
  on a different Samsung + Intel pair the equivalent setting gave "no signal" at 594 MHz, so change one TV first
  and keep a way back (`xrandr` to 4K30, or switch the setting Off).

### Smooth motion: one X screen per TV (since 2026-10-13)

**Symptom.** In the live 4K demo on both TVs the white sweep lines broke up vertically and juddered. The same
stream was smooth in a browser on the workstation (owner, 2026-10-13).

**Which stage.** Each stage was tested on its own:
- Encoding: `experiments/stream-latency/tools/stream_smoothness.py` recorded the live stream, decoded it on the
  workstation and tracked the sweep bar: a constant 64 px per frame, no frame off-step, none missing. Smooth.
- Decoding: VA-API was doing the decode on the mini-PC (video engine 9-16 % busy during playback). It kept up.
- Drawing to the screen: `experiments/stream-latency/static/motion.html` draws a moving bar in the browser with no
  video at all. In a window on each TV it ran at about 30 fps instead of 60 (frame interval 33.3 ms, ~210 missed
  refreshes in ~760 frames per TV) and tore. So the fault was in putting frames on the screen, not in the video.

**Cause.** Both TVs were one 7680x2160 X screen. Xorg can hand a window's frame straight to the display (a page
flip, synced to that TV's refresh) only when the window covers the whole X screen. A 3840-wide kiosk window never
does, so every frame of both windows was copied into one shared screen buffer instead: tearing, and about 30 fps per
TV on the N95. Things that did not help: Chrome GPU/vsync switches; the picom compositor (worse; still installed,
not running); the older `intel` X driver does not support this GPU (Alder Lake-N); Xorg 21.1's `modesetting` driver
has no TearFree option.

**Fix.** `ZaphodHeads` in `/etc/X11/xorg.conf.d/20-zaphod.conf` (repo: `media-server/remote-scripts/xorg/`) makes
each output its own X screen: `:0.0` = HDMI-1 (north), `:0.1` = HDMI-2 (south). Each kiosk window then covers its
whole screen and is page-flipped on its own TV's refresh. `kiosk-loop.sh` detects the two screens and starts
openbox and one Chrome on each, plus a single unclutter on north to hide the mouse pointer (one per screen kept
quitting: "someone created a sub-window to my sub-window"). A window manager is needed on both screens: without
one, Chrome's kiosk window came up 3839x2159 (one pixel short), could not flip, and that TV fell back to about
50 fps with tearing.

| Measured on the TVs | One shared screen (before) | One screen per TV (after) |
|---|---|---|
| `motion.html`, both TVs at once | ~30 fps, ~210 missed refreshes per ~760 frames, tearing | **60.0 fps, 0 missed** (22 s each, 18:30 UTC, after a plain kiosk restart) |
| Live 4K30 HEVC 18 Mbit/s, Chrome `droppedVideoFrames` | 34.4 % and 31.1 % (~6 min, lab Chromes) | **0.28 % north, 0.24 % south** (16/5788, 14/5796; ~3 min, production Chromes, 18:25-18:28 UTC) |
| Lab latency, capture to on screen (p50, excludes the TV's own ~35-45 ms) | - | ~126 ms: encode 12, pipe 9, network 9, player buffer and display ~85 |

**Living with it.**
- Anything that talks to X must name the screen: `DISPLAY=:0.1` for the south TV; plain `:0` reaches north only.
  The status scripts (`check-status*.sh`, `restart-kiosk.sh`, `remote-maintenance.sh`, the admin API status in
  `routes/admin_api.py`) now read both screens; the admin API change takes effect at the next Flask restart.
- A window cannot span or move between TVs. `ONE_SCREEN` and `DIALOG_TAMER` apply only to the old layout.
- Undo: `sudo rm /etc/X11/xorg.conf.d/20-zaphod.conf && sudo systemctl restart kiosk`; `kiosk-loop.sh` falls
  back to the old single-screen layout by itself. Install steps: media-server/README.md, Critical System
  Configuration §7.
- **On X11, 4K60 video does not fit** (2026-10-13 18:52-19:02 UTC; solved by the Wayland kiosk below): HEVC 4K60 on both TVs dropped 82 % / 74 % of frames,
  with the GPU's VideoEnhance engine at 97-100 % converting every decoded frame to RGB (decode, network and the
  workstation had headroom). Chrome's zero-copy path, which skips that conversion, fails on this X11 setup in
  Chrome 136 and 155 (GPU process exits: no SharedImage backing for NV12). WebCodecs was worse. Details and
  untested routes: `experiments/stream-latency/README.md`, "4K60 video on both TVs".
- A TV missing when X starts (e.g. after a power cut) is handled (tested 18:41-18:46 UTC). With the south connector
  forced off (`echo off > /sys/class/drm/card1-HDMI-A-2/status`) the kiosk restart brought up north normally.
  On `echo detect`, kiosk-loop spawned south within a second at 3840x2160. Separately, a south screen shrunk to
  1024x768 grew back to 3840x2160 with the `xrandr --output HDMI-2 --auto` that kiosk-loop runs on reconnect,
  and the Chrome window followed. Afterwards both production pages drew at 60.0 fps with no late frames.
- Known risk, not seen: an open Xorg bug (xserver #1924) can freeze a screen when a new client presents into a
  window another, now-gone client was page-flipping, e.g. after a Chrome GPU-process restart. Turning page
  flips off would avoid it but undo this fix; a frozen-screen watchdog is listed in §7.
- Installed 18:22 UTC with a 15-minute auto-rollback timer, which was cancelled at 18:31 UTC once both TVs had come
  back by themselves after a kiosk restart at 3840x2160, on their normal pages, at 60 fps.

### Wayland (sway) kiosk since 2026-10-13: 4K60 on both TVs

**Why.** On X11, Chrome's working VA-API path converts every decoded frame to RGB on the GPU's VideoEnhance engine,
which two 4K60 streams saturate, and its zero-copy path (which skips that step) crashes. Under Wayland the zero-copy
path works: decoded frames go to the GPU as they are and VideoEnhance stays at 0 %.

**What runs.** `kiosk.service` (repo `media-server/remote-scripts/kiosk.service`) starts sway 1.9 (Ubuntu package,
10 new packages, nothing upgraded) in a logind session on tty1 for `gjbm2`, with
`/usr/local/share/kiosk/sway-kiosk.conf` (repo `remote-scripts/wayland/`): HDMI-A-1 (north) and HDMI-A-2 (south) at
3840x2160@60, workspace 1 pinned to north and 2 to south, black background, no borders, cursor hidden, no idle
blanking. sway runs `/usr/local/bin/kiosk-wayland.sh`, which starts one `--ozone-platform=wayland` Chrome per TV
(same URLs, profiles and CDP ports 9222/9223 as before), restarts a Chrome that exits, and keeps the north page on the
north TV when the south TV is missing. Video path in `/etc/default/kiosk-wayland`: `KIOSK_VIDEO=zerocopy` (default:
`--use-angle=gl-egl` + `AcceleratedVideoDecodeLinuxZeroCopyGL`) or `copy`. Log: `/var/log/kiosk.log`.

| Measured on the TVs, 2026-10-13 | X11 (one X screen per TV) | Wayland (sway, zero-copy) |
|---|---|---|
| Pre-made HEVC 4K60 30 Mbit/s file, both TVs, ~3 min | - | **3 dropped of ~8,600 per TV (0.03 %)**, drawing 60.0 fps / 0 late |
| Live HEVC 4K60 from the lab, both TVs, ~3 min | 82 % / 74 % dropped; drawing 31-42 fps; VideoEnhance 97-100 % | 6.0 % / 7.3 % dropped; drawing 60.0 fps / 0 late; VideoEnhance 0 % |
| Live HEVC 4K30, both TVs | 0.28 % / 0.24 % dropped | not re-run |

The remaining live 4K60 drops come from the workstation, not the box: the lab generator misses its 16.7 ms frame
deadline about 2 % of the time (`late_source_frames`; render plus a 12 MB pipe write per frame inside WSL), and the
lab player speeds playback above 1.0x to catch up, which at 60 fps on a 60 Hz panel forces Chrome to drop frames.
Fixes belong in the renderer (even pacing) and the player (never above 1.0x; resync by a hard jump).

**Tests passed (2026-10-13, 19:33-19:41 UTC).** Chrome killed: back in ~10 s on its own TV. South TV missing at
start-up (`echo off > /sys/class/drm/card1-HDMI-A-2/status`, kiosk restart): north TV shows north; on reconnect south
appears on its TV. South unplugged while running and replugged: same. **Reboot**: kiosk up ~40 s after the reboot
command with both pages, 4K60 replay 2 and 1 dropped of ~1,450, light relay, Wi-Fi helper and watchdog running.

**Notes.** wlroots tags the HDMI signal as content type "Graphics" (X sent "No Data"); the TVs' picture settings
read over IP Control are unchanged (Filmmaker Mode, Warm2, same values). sway logs "Failed to parse EDID" (the TVs'
512-byte EDID); modes and 4K60 are unaffected, outputs show as "Unknown". sway's children run in the login session,
not the unit's cgroup, so `kiosk.service` kills the launcher and Chromes in `ExecStopPost`. X11 tools (`xrandr`,
`wmctrl`, `DISPLAY=`) no longer apply; use `SWAYSOCK=$(ls /run/user/1000/sway-ipc.*.sock) swaymsg -t get_outputs`.
Back to X11: `sudo systemctl disable --now kiosk && sudo systemctl enable --now kiosk-x11`.

## 4. Wi-Fi

### Measurements (2026-10-11)

| | Fast AP (5 GHz ch128, 5640 MHz) | Slow AP (5 GHz ch48, 5240 MHz) |
|---|---|---|
| Signal at the mini-PC | -68 to -70 dBm | -69 to -71 dBm |
| Download from the workstation (single TCP stream via the port proxy) | **20.4 Mbit/s** (other runs 13.6-21.1, 12-20 MB transfers) | **5.0 Mbit/s** (earlier run 4.5) |
| Ping to router under load | | 74 ms average, 219 ms max (earlier run) |

Method: `scripts/tput.py` (single TCP stream). Workstation `python3 scripts/tput.py serve 3001 12 3`; box
`python3 /tmp/tput.py get 192.168.1.205 3001` (scp the script over first). Without the proxy: box
`tput.py recv 5201`, WSL `tput.py push 192.168.1.92 5201 20`.

- The card is a Realtek RTL8821CE (rtw88 driver), 1x1, internal antenna. 2.4 GHz is no better and the APs'
  band steering pulls it back to 5 GHz.
- Ruled out: the Windows port proxy (17.4 direct vs 16.2 Mbit/s proxied), Wi-Fi power saving (no change), RF
  interference (nothing strong nearby), Bluetooth coexistence (only affects 2.4 GHz).
- rtw88 misreports the RX rate/width and marks every frame as ACKed, so `iw` RX MCS and retry counters are
  meaningless on this card. Trust measured throughput.
- The fast AP is on channel 128 (5640 MHz), a **DFS (radar) channel** (the box's regulatory data marks it "Radar
  detection", GB/DFS-ETSI). A radar detection, or the radar listen period after an AP restart, can take it off air,
  so occasional fallbacks to the slow AP (ch48, not DFS) are expected. If `journalctl -u wifi-prefer-ap` shows
  frequent fallbacks, move the fast AP to a non-DFS channel in UniFi; its BSSID, and so the lock, should stay the same.
- Since 2026-10-13 the Ethernet port `enp1s0` (Realtek RTL8111, r8169) is the primary link, over powerline (§5).
- Options considered: a USB 2x2 adapter with an external antenna, an AP or mesh node near the TVs, and interim
  tweaks (Bluetooth off, less background scanning; not applied, owner decision pending, because a mistake could
  cut remote access). The owner chose powerline (§5).

### Why it ended up on the slow AP

Both APs reach the box at almost the same signal. The kernel/supplicant logs (UTC) show:
- 802.11v steering by the APs (`WNM: Preferred List Available` immediately before the moves at 13:47 and 14:05);
- an AP-initiated disassociation by the fast AP (reason 8) at 10:21 that dropped it onto the slow AP for 5 min;
- with nothing pinned, the box itself also flips between the two within seconds after a rescan, and a Wi-Fi
  restart picks either AP more or less at random.

### Fix: `wifi-prefer-ap` service (installed 2026-10-11)

Files: `media-server/remote-scripts/wifi-prefer-ap.sh` (installed as `/usr/local/sbin/wifi-prefer-ap`),
`media-server/remote-scripts/wifi-prefer-ap.service`, installer `media-server/local-scripts/install-wifi-prefer-ap.sh`.
Config on the box: `/etc/default/wifi-prefer-ap` (`PREFERRED=<BSSID of the fast AP>`, written by the installer
from the box's association at install time; optional `IFACE` (wlp2s0), `NET_ID` (0), `GRACE`, `RETRY`,
`RETRY_MAX`, `MIN_SIGNAL`).

Behaviour:
1. While connected to the fast AP, it pins wpa_supplicant's network to that BSSID (`set_network 0 bssid`). The
   supplicant should then turn down 802.11v steering to other APs (not yet observed with a real steering request;
   a forced roam while pinned bounced back within about 4 s) and will not choose the slow AP on reconnect.
2. If the box is disconnected for 40 s while pinned (fast AP rebooting, radar, gone), it releases the pin so the
   box can join any AP.
3. While on another AP it checks every 120 s; if the fast AP is visible at -78 dBm or better it re-pins and
   reassociates. If that leaves the box disconnected for 40 s, the pin is released again and the interval doubles
   (up to 30 min), so a marginal AP cannot cause flapping. If the reassociate simply leaves it on the other AP
   (seen once in testing), the pin stays set to the fast AP and it retries every 120 s.
4. The pin lives only in wpa_supplicant's memory: boot always starts unpinned, and stopping the service runs
   `--release`. It never edits netplan or the Wi-Fi password config.

Tested on the box 2026-10-11 (UTC):

| Test | Result |
|---|---|
| Forced onto the slow AP (`wpa_cli roam`) | Back on the fast AP after ~4 s, without the service acting |
| Fast AP made unavailable (`wpa_cli set disallow_aps`) | On the slow AP 45 s later (about 45 s offline) |
| Switch-back once the fast AP is available again | First attempt (15:50) left the box on the slow AP. After a manual supplicant restart, the 15:52 check did not see the fast AP; the attempt at 15:54 succeeded |
| Supplicant restart | Box re-pinned within ~6-10 s when it came back on the fast AP |

Known limits: `wpa_cli roam` proved unreliable on this card, so switch-back uses re-pin plus reassociate, which
can need more than one attempt. The lock stops the moves; it does not raise the ~15-20 Mbit/s ceiling.

Operate (on the mini-PC):
```bash
systemctl status wifi-prefer-ap                     # running?
journalctl -u wifi-prefer-ap -n 20                  # pins, fallbacks, switch-backs
sudo wpa_cli -p /run/wpa_supplicant -i wlp2s0 status | grep -E '^(bssid|freq|wpa_state)='   # freq=5640 = fast AP
sudo wpa_cli -p /run/wpa_supplicant -i wlp2s0 get_network 0 bssid   # pinned BSSID, FAIL = unpinned
sudo systemctl disable --now wifi-prefer-ap         # undo: releases the pin, stock roaming again
```
Re-install or update from the repo root in WSL: `bash media-server/local-scripts/install-wifi-prefer-ap.sh`
(keeps the recorded AP; needs `sshpass` and `MEDIA_SERVER_PASSWORD` in `media-server/local-scripts/.env`, like
`install-deadman.sh`). Add `--prefer-current` to re-record the AP the box is on now (make sure that is the fast
one first); do this too if the fast AP is ever replaced (new BSSID). After any netplan change, check the Wi-Fi
network is still id 0 (`sudo wpa_cli -p /run/wpa_supplicant -i wlp2s0 list_networks`), that
`netplan-wpa-wlp2s0.service` still exists, and that the journal shows "on preferred AP: pinned".

Alternative at the source: the UniFi controller can lock this client to one AP, which would make the service
unnecessary.

## 5. Powerline Ethernet (primary since 2026-10-13)

The owner connected the mini-PC by powerline Ethernet on 2026-10-13. Background and buying/installing guidance:
[POWERLINE_RESEARCH_2026-10.md](POWERLINE_RESEARCH_2026-10.md) (it also assesses the TP-Link TL-PA717 KIT).

### Measured 2026-10-13 (afternoon; workstation <-> box)

| | Ethernet (powerline) | Wi-Fi (for comparison, §4) |
|---|---|---|
| Link to the adapter | 1000 Mb/s full duplex | |
| Download to the box, 100 MB x 3 | 167-176 Mbit/s | 15-20 Mbit/s |
| Download, 1200 MB sustained (51 s) | **188 Mbit/s** | |
| Upload from the box, 60 MB | ~80-160 Mbit/s (depends which end times it) | |
| Ping to router, idle | p50 3.1 ms (a plain cable would be < 1 ms) | ~2.6 ms avg |
| Ping to router during the sustained download | p50 27 ms, **p99 41 ms**, max 49 ms, **0 % loss** (800 pings) | 74 ms avg, 219 ms max on the slow AP |

Against the acceptance targets (worst hour over 24 h: TCP >= ~120 Mbit/s, loaded ping p99 < ~50 ms, no drops):
passes so far on single runs; the 24-hour soak (below) decides. UDP loss has not been measured (no iperf3).

### How the box is configured (installed 2026-10-13)

Installer: `media-server/local-scripts/install-ethernet-primary.sh` (run from the repo root in WSL; needs `sshpass`
and `MEDIA_SERVER_PASSWORD`). Files in `media-server/remote-scripts/network/`:
- `60-ethernet.yaml` -> `/etc/netplan/60-ethernet.yaml`: `enp1s0` DHCP (got 192.168.1.10), `optional: true` (boot
  does not wait for it), default-route metric **100**. Wi-Fi (`50-cloud-init.yaml`, untouched) keeps metric 600.
- `50-wifi-source-route.conf` -> `/etc/systemd/network/10-netplan-wlp2s0.network.d/`: policy rule `from
  192.168.1.92 lookup 102` with a Wi-Fi-only table, so anything addressed to the Wi-Fi IP answers over Wi-Fi.
  Remote access on .92 therefore survives a powerline failure. If the Wi-Fi DHCP address changes, update `From=`.
- `60-multihome-arp.conf` -> `/etc/sysctl.d/`: `arp_ignore=1`, `arp_announce=2`, so with two interfaces on one
  subnet each answers ARP only for its own address.
- `eth-primary-rollback.sh` -> `/usr/local/sbin/eth-primary-rollback`: removes the above and downs `enp1s0` (Wi-Fi only).
The installer arms the rollback as a 5-minute dead-man, applies the change detached on the box, checks SSH on both
addresses and the default route, and only then cancels the dead-man.

Verified after install: default route `via 192.168.1.1 dev enp1s0 metric 100`; the kiosk URL
(`http://95.141.21.170:8000/display/north-screen`) loads over Ethernet in 17 ms; `kiosk`, `wifi-prefer-ap` and
`deadman.timer` still active. **Failover test:** with `enp1s0` link down the default route moved to Wi-Fi within
seconds and the kiosk URL kept loading (via .92); with the link back it returned to Ethernet.

**Known gap:** failover only happens when the cable *link* drops (adapter unplugged or powered off). If the
powerline segment fails while the adapter keeps its Ethernet link up, the default route stays on `enp1s0` and the
kiosk pages stall (remote access on .92 still works). A small watchdog that pings the router via `enp1s0` and
takes it out of the default route on repeated failure would close this; not built yet.

Operate (on the box):
```bash
ip -br -4 addr; ip route show default            # enp1s0 .10 metric 100 = wired primary
ip rule | grep 102; ip route show table 102      # Wi-Fi source route
sudo eth-primary-rollback                        # undo: back to Wi-Fi only
```

### 24-hour soak (started 2026-10-13 15:44 UTC)

`scripts/powerline_soak.sh` runs in WSL: every 15 min it pushes 200 MB to the box's Ethernet address and records
throughput, idle ping (10 s) and ping during the push (12 s), loss, link carrier and the default-route device, to
`logs/powerline-soak-<start>.csv` (gitignored). First sample: 176 Mbit/s, idle p50 3.9 / p99 21 ms, loaded p50 24 /
p99 38 ms, 0 % loss. Stop it with `kill $(cat logs/powerline-soak.pid)`; it also needs the receiver it starts on the
box (`/tmp/tput.py recv 5201`, exits by itself after 24 h + 10 min).

## 6. Workstation (WSL) clock

**Symptom (until 2026-10-11):** WSL's `CLOCK_REALTIME` and `CLOCK_MONOTONIC` ran fast (+20,000 ppm, 2 %, when
measured) and the wall clock was stepped back ~0.65 s every 32 s (a sawtooth). `CLOCK_MONOTONIC_RAW` (the raw TSC)
was accurate (within 0.2 ppm of the Windows clock). Server-side timers and timestamps were affected. It had run for
months: uptime implied +4.6 % on average since the July boot, and the kernel log shows it at the ±10 % limit in
June and July.

**Cause:** two time services steering the one WSL2 kernel clock towards different references:
- WSL's own `chronyd`, hidden in WSL's root namespace (not visible to `ps` in the distro; answers on
  `127.0.0.1:323`), steers to the Windows host clock (`PHC0`, Hyper-V `/dev/ptp0`) and updates every 8 s.
- Ubuntu 24.04's `systemd-timesyncd` (enabled under WSL by the `wsl-setup` drop-in) steers to `ntp.ubuntu.com`.
- The Windows clock was 0.38 s ahead of UTC because the Windows Time service was stopped (demand-start).
  So timesyncd stepped the clock back every poll; chrony read each step as the clock running slow, learned a
  -21,000 ppm correction and applied it through the kernel `tick` (10200-10270 instead of 10000).
- Setting `tick` by hand is futile while chrony runs: it rewrites it every 8 s.

**Fix applied 2026-10-11 16:19 BST (15:19 UTC) by the owner, in an Administrator PowerShell:**
```powershell
# stop the second time service inside WSL
wsl -d Ubuntu -u root -- sh -c "systemctl disable --now systemd-timesyncd && systemctl mask systemd-timesyncd"
# keep the Windows clock (which WSL follows) on time
sc.exe config w32time start= auto
net start w32time
w32tm /config /manualpeerlist:"time.cloudflare.com,0x9 time.windows.com,0x9" /syncfromflags:manual /update
reg add HKLM\SYSTEM\CurrentControlSet\Services\W32Time\TimeProviders\NtpClient /v SpecialPollInterval /t REG_DWORD /d 1024 /f
w32tm /config /update
w32tm /resync /force
```
Result: within about a minute chrony unwound (tick 9999, freq about -20 ppm) and `CLOCK_MONOTONIC` matched
`MONOTONIC_RAW` to about 130 ppm (about 60 ppm an hour later). The Windows clock slews its own offset out
gradually rather than stepping: 0.44 s ahead at the fix, 0.21 s an hour later, 0.18 s at 17:30 BST, with the
correction rate falling from about 70 ppm. WSL's wall clock follows Windows, so it converges on UTC over a few hours.

**Check it:** `python3 scripts/wsl_clock_check.py` (read-only; checks the **rate** only; exit 1 if it is off by
> 1000 ppm). For the absolute offset, on Windows: `w32tm /stripchart /computer:time.cloudflare.com /samples:3
/dataonly` (should be close to 0 s) and `w32tm /query /status`.

**If it comes back:** check `systemctl is-enabled systemd-timesyncd` is still `masked` (a `wsl-setup` package
update could re-enable it) and that the Windows Time service is running. Do not install chrony or ntp in the
distro (a third time service). `wsl --shutdown` resets chrony and the kernel tick but stops every WSL process,
including the production Flask app; afterwards the WSL IP may change, so run `fix-wsl-networking.ps1`
(Administrator) and re-add the manual 8081/3001 rules (§1).

Undo: `wsl -d Ubuntu -u root -- sh -c "systemctl unmask systemd-timesyncd && systemctl enable --now systemd-timesyncd"`
(this would restart the fight unless the Windows clock is accurate).

## 7. Open items

- Live 4K60: make the renderer pace frames evenly at 60 fps (the lab generator misses 16.7 ms ~2 % of the time) and
  never play above 1.0x in the page's player; then re-run live 4K60 on both TVs (§3, Wayland).
- Wayland kiosk follow-ups: an anchor-frame check (Wayland screenshot tool `grim`, not installed) comparing the art
  still and the stream's first frame on glass; the lab tools `lab-chrome.sh` and `fb_probe.py` are X11-only.

- **Commit the 2026-10-11/13 work.** What is deployed to the box is byte-identical to the working tree but not in git:
  `media-server/remote-scripts/{kiosk-loop.sh,wifi-prefer-ap.sh,wifi-prefer-ap.service}`,
  `media-server/local-scripts/{install-wifi-prefer-ap.sh,install-ethernet-primary.sh}`, `media-server/remote-scripts/network/`,
  `media-server/README.md`, `scripts/powerline_soak.sh`,
  `scripts/{wsl_clock_check.py,tput.py}`, `docs/{KIOSK_INFRASTRUCTURE_NOTES,FRAME_PRO_SETTINGS_2026-10,POWERLINE_RESEARCH_2026-10}.md`,
  `docs/kiosk-captures/` and `experiments/stream-latency/`. The `kiosk-loop.sh` diff also contains the uncommitted
  2026-07-28 `xrandr --current` change. Added 2026-10-13: `media-server/remote-scripts/xorg/20-zaphod.conf`, the
  two-screen display checks in `media-server/{check-status.sh,check-status-compact.sh,restart-kiosk.sh}`,
  `media-server/local-scripts/remote-maintenance.sh` and `routes/admin_api.py`, and
  `docs/DISPLAY_CONTROL_LAYER_DESIGN_2026-10.md`.
- Frame Pro TV settings: apply [FRAME_PRO_SETTINGS_2026-10.md](FRAME_PRO_SETTINGS_2026-10.md) (one TV first for
  Input Signal Plus), verify from the mini-PC (§3), and update `kiosk-loop.sh` if 4K60 should be forced.
- Decide whether to `apt-mark hold google-chrome-stable` (§1).
- Powerline: read the 24 h soak (`logs/powerline-soak-*.csv`) against the acceptance targets (§5); consider the
  Ethernet path watchdog for the link-up-but-no-path case; optionally a DHCP reservation for 192.168.1.10 and
  switching the admin scripts' `MEDIA_SERVER_HOST` to it.
- Interim Wi-Fi tweaks (Bluetooth off, less background scanning): owner decision pending (§4).
- Re-check the workstation's absolute clock offset a few hours after the fix (§6).
- ~~Game Mode test~~ closed 2026-10-13: Game Mode is not used (Filmmaker Mode, Auto) and 4K60 holds without it (§3).
- Run the full lab matrix (`matrix-device.json`, `matrix-generative.json`) on the TVs now that they run 4K60 with
  one X screen each; the 2026-10-13 demo was one 3-minute 4K30 HEVC run.
- Per-TV X screens follow-ups (§3; X11 fallback only since the Wayland switch): a frozen-screen watchdog (the page heartbeat or CDP sees no new frames → restart
  that TV's Chrome, then `kiosk.service`); optionally let kiosk-loop restart only the TV whose connector changed
  (today any change restarts both Chromes). The `Monitor "HDMI-n"` lines in `20-zaphod.conf` name Monitor
  sections that do not exist; Xorg logs no warning and uses them as the outputs' monitor names, so they are
  harmless; drop them at the next X restart that is needed anyway.
- ~~`check-status-compact.sh` false "No page load detected"~~ fixed 2026-10-13: the status scripts now read the
  open pages from Chrome's DevTools ports and work under sway and the X11 fallback.
- `routes/samsung_utils.py` bugs found during TV research (owner decision, tracked in REFACTOR_PLAN T2/D17):
  `device_sleep` sends KEY_POWER to a TV already in Art Mode (switches it to HDMI); `get_status` maps any
  art-channel error to `art_mode='off'`; the 04:10 `device-media-sync` crashes with `NameError: log_debug`.
- Test leftovers on the mini-PC were removed on 2026-10-11 (the old `/tmp/lab` copies predated the lab fixes;
  always copy the scripts from the repo). `/usr/local/bin/kiosk-loop.sh.bak-20261011-140753` is kept on purpose
  until the VA-API flag has run for a while; the 2026-10-13 backups are listed in media-server/README.md
  (kiosk-loop.sh). After the 2026-10-13 tests the lab Chrome profiles were removed; `/tmp/lab` (probe scripts)
  is still there and goes at the next reboot. `picom` is installed but not running.
