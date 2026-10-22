# Media Server Management

This directory provides a complete maintenance and deployment system for the Screen Machine media server (kiosk displays).

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                     Development Machine (WSL)                    │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │  Flask Server (port 5000)                                   │ │
│  │  - API endpoints for content generation                     │ │
│  │  - Light sensor data receiver (WebSocket port 8765)        │ │
│  │  - Overlay management                                       │ │
│  └────────────────────────────────────────────────────────────┘ │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │  Frontend: built dist/ via Flask :5000                                │ │
│  │  - React UI for content management                          │ │
│  │  - Proxies API requests to Flask                            │ │
│  └────────────────────────────────────────────────────────────┘ │
│                                                                   │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │  media-server/local-scripts/                                │ │
│  │  - remote-maintenance.sh  ← Main harness                   │ │
│  │  - diagnose-lightsensor.sh                                  │ │
│  │  - deploy-kiosk.sh                                          │ │
│  │  - .env (credentials)                                       │ │
│  └────────────────────────────────────────────────────────────┘ │
└───────────────────────────┬───────────────────────────────────────┘
                            │ SSH
                            │ (remote-maintenance.sh)
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│              Media Server (Ubuntu 24.04 Headless)                │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │  sway (Wayland display server) — since 2026-10-13 (§8)      │ │
│  │  - HDMI-A-1 north = workspace 1, HDMI-A-2 south = ws 2      │ │
│  │  - 3840x2160@60 on both TVs, zero-copy 4K60 video           │ │
│  │  (X11 fallback: kiosk-x11.service, Xorg + kiosk-loop.sh)    │ │
│  └────────────────────────────────────────────────────────────┘ │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │  kiosk.service (systemd)                                    │ │
│  │  - Starts sway on boot (logind session on tty1, gjbm2)      │ │
│  │  - sway runs kiosk-wayland.sh                               │ │
│  │  - Auto-restarts on failure                                 │ │
│  └────────────────────────────────────────────────────────────┘ │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │  kiosk-wayland.sh                                           │ │
│  │  - One full-screen Chrome per TV, restarted if it exits     │ │
│  │  - Keeps the north page on the north TV if south is off     │ │
│  │  - North: http://IP:8000/display/north-screen              │ │
│  │  - South: http://IP:8000/display/south-screen              │ │
│  │  - Logs: /tmp/chrome-{north,south}/chrome_console.log     │ │
│  └────────────────────────────────────────────────────────────┘ │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │  Light Sensor Client (optional)                             │ │
│  │  - Reads Yoctopuce USB sensor                               │ │
│  │  - Sends lux data via WebSocket to Flask                   │ │
│  │  - ws://IP:8765                                             │ │
│  └────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

## Directory Structure

```
media-server/
├── README.md               # This file - complete architecture & operations guide
├── .gitignore             # Excludes .env, results, logs
│
├── Admin Scripts (run from root)
│   ├── check-status.sh          # Quick status check
│   ├── restart-kiosk.sh         # Restart kiosk service
│   ├── reboot-server.sh         # Reboot media server
│   ├── view-logs.sh             # View maintenance logs
│   └── maintenance.log          # Shared log file (gitignored)
│
├── local-scripts/          # Advanced maintenance tools
│   ├── remote-maintenance.sh    # Main operations harness
│   ├── diagnose-lightsensor.sh  # Light sensor diagnostic
│   ├── deploy-kiosk.sh          # Automated deployment
│   ├── install-deadman.sh       # Install the Deadman watchdog
│   ├── install-wifi-prefer-ap.sh  # Install the Wi-Fi access-point lock
│   ├── install-ethernet-primary.sh  # Make powerline Ethernet the primary link (Wi-Fi fallback)
│   ├── .env.example             # Configuration template
│   └── .env                     # Your credentials (gitignored)
│
└── remote-scripts/         # Deployed to media server
    ├── kiosk.service            # The kiosk: sway (Wayland) — default since 2026-10-13
    ├── wayland/                 # sway-kiosk.conf (→ /usr/local/share/kiosk/), kiosk-wayland.sh (→ /usr/local/bin/)
    ├── kiosk-x11.service        # X11 fallback (disabled): Xorg + kiosk-wait-start.sh + kiosk-loop.sh
    ├── kiosk-wait-start.sh      # X11 fallback: startup wrapper
    ├── kiosk-loop.sh            # X11 fallback: kiosk manager
    ├── webview-manager.sh       # Alternative manager
    ├── wifi-prefer-ap.sh        # Keeps Wi-Fi on the fast access point (→ /usr/local/sbin/wifi-prefer-ap)
    ├── wifi-prefer-ap.service   # Its systemd unit
    ├── network/                 # Wired-first config: netplan 60-ethernet.yaml, Wi-Fi source-route drop-in,
    │                            #   ARP sysctl, eth-primary-rollback.sh
    └── xorg/20-zaphod.conf      # X11 fallback: one X screen per TV (→ /etc/X11/xorg.conf.d/)
```

Measured hardware, network and clock facts, and the reasons behind the changes below, are in
[docs/KIOSK_INFRASTRUCTURE_NOTES.md](../docs/KIOSK_INFRASTRUCTURE_NOTES.md).

---

## Critical System Configuration

These settings are **essential** for a reliable kiosk. Without them, the system will
appear to work initially but break unpredictably (e.g. after a reboot or kernel update).

### 1. Mask getty on tty1

Xorg runs on virtual terminal 1 (`vt1`). Ubuntu's default `getty@tty1.service` also
claims tty1 to show a text login prompt. If both services run, they race for the
terminal -- and on some kernel versions getty wins after ~20 seconds, killing Xorg
and leaving a bare login prompt on the physical display.

**Symptoms if not done:** Kiosk shows a Linux login prompt instead of Chrome.
Kiosk service may show as running briefly then go `inactive (dead)`.

```bash
# On the media server:
sudo systemctl stop getty@tty1.service
sudo systemctl disable getty@tty1.service
sudo systemctl mask getty@tty1.service
# Verify:
systemctl is-enabled getty@tty1.service   # should print "masked"
```

The `kiosk.service` file also includes `Conflicts=getty@tty1.service` as a
second layer of protection, but **masking is the primary defence** -- it prevents
getty from starting at all, regardless of boot ordering or kernel behaviour.

To undo (e.g. if you need a local console for debugging):
```bash
sudo systemctl unmask getty@tty1.service
sudo systemctl start getty@tty1.service
```

### 2. Disable all automatic updates

A kiosk must **never** update itself. Automatic updates can install new kernels,
change driver behaviour, or trigger reboots -- any of which can break the display.
Ubuntu 24.04 ships with `unattended-upgrades` enabled by default.

**Symptoms if not done:** System reboots unexpectedly into a new kernel version.
Kiosk may fail to start after reboot due to driver/timing changes.

```bash
# On the media server -- stop, disable, and mask all five update services:
sudo systemctl stop unattended-upgrades.service
sudo systemctl disable unattended-upgrades.service
sudo systemctl mask unattended-upgrades.service

sudo systemctl stop apt-daily.timer
sudo systemctl disable apt-daily.timer
sudo systemctl mask apt-daily.timer

sudo systemctl stop apt-daily-upgrade.timer
sudo systemctl disable apt-daily-upgrade.timer
sudo systemctl mask apt-daily-upgrade.timer

sudo systemctl stop apt-daily.service
sudo systemctl mask apt-daily.service

sudo systemctl stop apt-daily-upgrade.service
sudo systemctl mask apt-daily-upgrade.service

# Verify all masked:
systemctl is-enabled unattended-upgrades.service  # masked
systemctl is-enabled apt-daily.timer               # masked
systemctl is-enabled apt-daily-upgrade.timer       # masked
```

**Updates should only ever be done manually**, during a planned maintenance window,
with physical or SSH access to the box to recover if something breaks.

### 3. Disable automatic reboot (if unattended-upgrades is ever re-enabled)

As an extra precaution, ensure the unattended-upgrades config does not allow
automatic reboots:

```bash
# /etc/apt/apt.conf.d/50unattended-upgrades
# Ensure this line is set to "false":
Unattended-Upgrade::Automatic-Reboot "false";
```

### 4. Hardware video decode (VA-API) — enabled 2026-10-11

Without it every video the kiosk plays is decoded in software on the N95's four cores (Linux Chrome cannot
play HEVC at all without it). Three parts, all required:

- Packages (installed 2026-10-11, nothing upgraded): `intel-media-va-driver-non-free` 24.1.0, `vainfo` 2.12.0,
  `intel-gpu-tools` 1.28 (for `intel_gpu_top`), plus the automatic `libva2`, `libva-drm2`, `libva-x11-2`,
  `libva-wayland2` and `libigdgmm12`. Installed with `experiments/stream-latency/beelink/enable-hwdecode.sh`,
  which runs `apt-get update`, refuses to upgrade or remove anything and stops needrestart from restarting
  `kiosk.service` mid-install. To undo, see `docs/KIOSK_INFRASTRUCTURE_NOTES.md` §2.
- `gjbm2` in the `render` and `video` groups (Chrome opens `/dev/dri/renderD128` itself).
- `--enable-features=AcceleratedVideoDecodeLinuxGL` in `kiosk-loop.sh`. **Keep every feature in that one
  `--enable-features` switch**; a second `--enable-features` (e.g. via `CHROME_EXTRA`) silently replaces it.

```bash
# Verify on the media server:
LIBVA_DRIVER_NAME=iHD vainfo --display drm --device /dev/dri/renderD128 | grep -E "Driver version|VAProfile(H264|HEVC|AV1)"
id gjbm2 | grep -o render
grep -c -- '--enable-features=AcceleratedVideoDecodeLinuxGL' /usr/local/bin/kiosk-loop.sh   # 1
```

`deploy-kiosk.sh` does not install the packages or set the groups (and does not install `wifi-prefer-ap`, §5):
on a fresh box run `enable-hwdecode.sh` and `install-wifi-prefer-ap.sh` afterwards. Chrome is not apt-held, so a
manual `apt upgrade` would move it from 136 to 155.x; the flag above was verified for 136 only, so re-check it
after any Chrome upgrade (or `sudo apt-mark hold google-chrome-stable`).

### 5. Wi-Fi access-point lock (`wifi-prefer-ap`) — installed 2026-10-11

Two access points reach the kiosk at the same weak signal, but one gives ~20 Mbit/s and the other ~5 Mbit/s.
The APs' steering kept moving the box onto the slow one. `wifi-prefer-ap.service` pins the box to the fast AP,
falls back to any AP if the fast one disappears for 40 s, and switches back when it returns. Details, test
results and limits: [docs/KIOSK_INFRASTRUCTURE_NOTES.md §4](../docs/KIOSK_INFRASTRUCTURE_NOTES.md#4-wi-fi).

```bash
# Install / update (from the repo root in WSL; keeps the recorded AP). Needs sshpass and
# MEDIA_SERVER_PASSWORD in media-server/local-scripts/.env (used for sudo on the box), like install-deadman.sh:
bash media-server/local-scripts/install-wifi-prefer-ap.sh
# Re-record the preferred AP as the one the box is on now (make sure it is the fast one, freq=5640):
bash media-server/local-scripts/install-wifi-prefer-ap.sh --prefer-current
# On the media server:
journalctl -u wifi-prefer-ap -n 20                       # pins, fallbacks, switch-backs
sudo systemctl disable --now wifi-prefer-ap              # undo: releases the pin, stock roaming again
```

### 6. Wired-first networking (powerline Ethernet) — installed 2026-10-13

`enp1s0` (powerline Ethernet, DHCP 192.168.1.10) carries the default route at metric 100; Wi-Fi (`wlp2s0`,
192.168.1.92) stays configured at metric 600 and takes over if the cable link drops. A source route keeps the Wi-Fi
address answering over Wi-Fi, so SSH to 192.168.1.92 (what the admin scripts use) works even if the powerline path
fails. Measured ~170-190 Mbit/s and loaded ping p99 ~40 ms, against 15-20 Mbit/s on Wi-Fi. Details, failover test and
the known gap (powerline path dead while the adapter link stays up):
[docs/KIOSK_INFRASTRUCTURE_NOTES.md §5](../docs/KIOSK_INFRASTRUCTURE_NOTES.md#5-powerline-ethernet-primary-since-2026-10-13).

```bash
# Install / re-apply (from the repo root in WSL; 5-minute auto-rollback unless both addresses answer):
bash media-server/local-scripts/install-ethernet-primary.sh
# On the media server:
ip -br -4 addr; ip route show default       # enp1s0 192.168.1.10 metric 100 = wired primary
sudo eth-primary-rollback                   # undo: back to Wi-Fi only
```
The netplan Wi-Fi file (`50-cloud-init.yaml`, holds the Wi-Fi password) is never edited; the Ethernet config is a
separate `/etc/netplan/60-ethernet.yaml`. Plug the powerline adapters straight into wall sockets.

### 7. One X screen per TV (ZaphodHeads) — installed 2026-10-13; X11 fallback only since the Wayland switch (§8)

`/etc/X11/xorg.conf.d/20-zaphod.conf` (repo: `remote-scripts/xorg/20-zaphod.conf`) makes each HDMI output its own X
screen: `:0.0` = HDMI-1 (north), `:0.1` = HDMI-2 (south). `kiosk-loop.sh` sees two screens and starts openbox
and one Chrome on each, plus one unclutter on north (the pointer's screen). Without it, both TVs share one 7680x2160 screen, no Chrome window can be
page-flipped, and video tears and runs at about 30 fps per TV (4K video dropped 31-34 % of frames). With it: 60 fps
and 0 missed refreshes on both TVs, 0.2-0.3 % dropped frames on 4K30 live video. Details:
[docs/KIOSK_INFRASTRUCTURE_NOTES.md §3](../docs/KIOSK_INFRASTRUCTURE_NOTES.md#smooth-motion-one-x-screen-per-tv-since-2026-10-13).

```bash
# Install (from the repo root in WSL), then restart the kiosk (TVs go black ~10-20 s):
scp media-server/remote-scripts/xorg/20-zaphod.conf media-server/remote-scripts/kiosk-loop.sh gjbm2@192.168.1.92:/tmp/
ssh -t gjbm2@192.168.1.92 'sudo install -m 644 /tmp/20-zaphod.conf /etc/X11/xorg.conf.d/ && sudo install -m 755 /tmp/kiosk-loop.sh /usr/local/bin/ && sudo systemctl restart kiosk'
# On the media server:
DISPLAY=:0 xdpyinfo | grep 'number of screens'     # 2
grep 'per-TV X screens' /var/log/kiosk.log | tail -1
for s in 0 1; do DISPLAY=:0.$s wmctrl -lG; done      # each Chrome window 0 0 3840 2160
# Undo: sudo rm /etc/X11/xorg.conf.d/20-zaphod.conf && sudo systemctl restart kiosk   (kiosk-loop falls back by itself)
```
Anything that talks to X on the box must now name the screen: `DISPLAY=:0.1` for the south TV (plain `:0` is north
only). The screen count comes from `xdpyinfo` (package `x11-utils`, pulled in by `xorg`).

---

### 8. Wayland (sway) kiosk — default since 2026-10-13

`kiosk.service` now starts **sway** (Wayland) instead of Xorg. sway runs in a logind session on tty1 for `gjbm2` with
`/usr/local/share/kiosk/sway-kiosk.conf` (repo `remote-scripts/wayland/`) and starts
`/usr/local/bin/kiosk-wayland.sh`, which runs one `--ozone-platform=wayland` Chrome per TV: north on HDMI-A-1
(workspace 1), south on HDMI-A-2 (workspace 2), same URLs, profiles and CDP ports (9222/9223) as before. Why: on X11
two 4K60 video streams dropped 74-82 % of frames and Chrome's zero-copy video path crashes; under sway zero-copy works
and both TVs play 4K60 with 0.03 % dropped. Details and tests (reboot, Chrome crash, TV missing or unplugged):
[docs/KIOSK_INFRASTRUCTURE_NOTES.md §3](../docs/KIOSK_INFRASTRUCTURE_NOTES.md#wayland-sway-kiosk-since-2026-10-13-4k60-on-both-tvs).

```bash
# On the media server:
systemctl is-active kiosk; systemctl is-enabled kiosk kiosk-x11      # active / enabled disabled
export SWAYSOCK=$(ls /run/user/1000/sway-ipc.*.sock)
swaymsg -p -t get_outputs | grep -E '^Output|Current mode|Workspace'   # both 3840x2160 @ 60 Hz, ws 1 and 2
cat /etc/default/kiosk-wayland                                       # KIOSK_VIDEO=zerocopy (or copy)
grep -E 'kiosk-wayland start|spawn|re-showed' /var/log/kiosk.log | tail
# Back to the X11 kiosk (kept installed, disabled):
sudo systemctl disable --now kiosk && sudo systemctl enable --now kiosk-x11
```
Installed with Ubuntu's `sway` package (`apt-get install --no-install-recommends --no-upgrade sway`: 10 new packages,
nothing upgraded). Things that differ from X11: no `xrandr`, `wmctrl` or `DISPLAY=` (use `swaymsg` with `SWAYSOCK` as
above); sway's child processes live in the login session, so `kiosk.service` kills the launcher and Chromes in
`ExecStopPost`; `KIOSK_VIDEO=copy` turns zero-copy off (and keeps it off on Chrome 155, which enables it by default).
The previous X11 unit file is kept on the box as `/etc/systemd/system/kiosk.service.x11-backup-*`.

## Quick Admin Commands

For daily operations, use these simple scripts from the `media-server/` directory:

```bash
# Check if everything is running (kiosk + light sensor)
./check-status.sh

# Restart the kiosk (displays will reload)
./restart-kiosk.sh

# Reboot the entire server (requires confirmation)
./reboot-server.sh

# View maintenance logs
./view-logs.sh
```

**What `check-status.sh` shows:**
- System uptime and hostname
- **Kiosk service** status (display management)
- **Light sensor service** status (light-relay.service)
  - USB sensor detection (Yoctopuce Yocto-Light-V5)
  - WebSocket connection to Flask server (:8765)
- Display server (sway, or Xorg on the X11 fallback) and Chrome processes
- Connected displays (kernel connector status, plus modes from `swaymsg` or `xrandr`)
- **Open pages** per screen, read from Chrome's DevTools port (9222 north, 9223 south) - proof the right page is loaded
- Network connections to Flask server
- Recent errors from logs
- Disk and memory usage

**Chrome output:** Chrome has run without `--enable-logging` since 2026-07-28, so `/tmp/chrome-{north,south}/chrome_console.log`
holds only Chrome's own stderr, not page console messages. The page check therefore uses the DevTools port instead.
`check-status*.sh` use `jq` on the box.

These scripts automatically load credentials from `local-scripts/.env` and log all operations to `maintenance.log`.

---

## Quick Start

### 1. Set Up Credentials

```bash
cd media-server/local-scripts/
cp .env.example .env
micro .env
```

**Minimal .env configuration:**
```bash
MEDIA_SERVER_HOST=screen-machine-drawingroom
MEDIA_SERVER_USER=gjbm2
MEDIA_SERVER_PORT=22
```

### 2. Set Up SSH Keys (Recommended)

```bash
# Generate key if needed
ssh-keygen -t ed25519

# Copy to server
ssh-copy-id gjbm2@screen-machine-drawingroom

# Test
ssh gjbm2@screen-machine-drawingroom "echo 'Success!'"
```

### 3. Test Connection

```bash
# Simple status check
./check-status.sh

# Or use the full maintenance harness
cd local-scripts/
./remote-maintenance.sh system-status
```

---

## Common Admin Tasks

### Daily Operations

**Check if displays are working:**
```bash
./check-status.sh
```

**Restart displays (if frozen or showing wrong content):**
```bash
./restart-kiosk.sh
```

**Reboot server (for system updates or major issues):**
```bash
./reboot-server.sh
# Prompts for confirmation unless you use --force
```

### Admin Script Details

#### check-status.sh
Quick health check showing:
- System uptime
- Kiosk service status
- Display server (sway or Xorg) and Chrome processes
- Connected displays and modes
- Network connections
- Recent errors
- Resource usage

**Usage:**
```bash
./check-status.sh
```

**No confirmation required** - safe to run anytime.

#### restart-kiosk.sh
Cleanly restarts the kiosk service:
1. Stops kiosk service
2. Kills remaining Chrome/X processes
3. Starts kiosk service
4. Verifies displays are running
5. Shows connected displays

**Usage:**
```bash
./restart-kiosk.sh
```

**Downtime:** ~10 seconds  
**No confirmation required**

**Use when:**
- Displays are frozen
- Content not updating
- After updating kiosk scripts
- Chrome is showing errors

#### reboot-server.sh
Reboots the entire media server:
1. Shows pre-reboot status
2. Prompts for confirmation (unless `--force`)
3. Issues reboot command
4. Shows next steps

**Usage:**
```bash
# With confirmation prompt
./reboot-server.sh

# Skip confirmation (careful!)
./reboot-server.sh --force
```

**Downtime:** ~60-90 seconds  
**Requires confirmation**

**Use when:**
- System updates installed
- Kernel updates
- Major system issues
- Hardware changes

#### view-logs.sh
View the maintenance log file showing all operations.

**Usage:**
```bash
# Last 50 lines (default)
./view-logs.sh

# Last 100 lines
./view-logs.sh 100

# Follow in real-time
tail -f maintenance.log
```

**Shows:**
- Timestamp of each operation
- Which script ran (check-status, restart-kiosk, etc.)
- Success/failure status
- Error messages

**Log location:** `media-server/maintenance.log` (gitignored)

---

## Remote Maintenance Harness

The `remote-maintenance.sh` script is your primary interface for all operations.

### Core Commands

```bash
# System status and health check
./remote-maintenance.sh system-status

# Collect all logs
./remote-maintenance.sh collect-logs

# Deploy updated kiosk scripts (after editing)
./remote-maintenance.sh deploy-scripts              # Deploy all scripts
./remote-maintenance.sh deploy-scripts kiosk-loop.sh  # Deploy specific script

# Update IP addresses after ISP change
./remote-maintenance.sh update-ip 185.254.136.244 95.141.21.170

# Diagnose light sensor setup
./remote-maintenance.sh diagnose-lightsensor

# Fix light sensor IP configuration
./remote-maintenance.sh fix-lightsensor 95.141.21.170

# Run custom script
./remote-maintenance.sh run-script my-check.sh
```

### How It Works

1. **Loads credentials** from `.env`
2. **Tests SSH connection** to media server
3. **Deploys script** to remote `/tmp` directory
4. **Executes remotely** and streams output
5. **Saves results** locally with timestamp
6. **Cleans up** temporary files

All output is saved to `../maintenance-results/` with timestamps.

### Configuration Options (.env)

```bash
# === Required ===
MEDIA_SERVER_HOST=screen-machine-drawingroom  # Hostname or IP
MEDIA_SERVER_USER=gjbm2                       # SSH username
MEDIA_SERVER_PORT=22                          # SSH port

# === Optional ===
MEDIA_SERVER_SSH_KEY=/path/to/key.pem         # Custom SSH key path
MEDIA_SERVER_PASSWORD=your-password           # Not recommended - use SSH keys

# === Informational (not used by scripts) ===
FLASK_API_URL=http://95.141.21.170:5000
WEBSOCKET_URL=ws://95.141.21.170:8765
VITE_FRONTEND_URL=http://95.141.21.170:8000
```

---

## Remote Scripts Architecture

### kiosk.service (systemd)
**Location:** `/etc/systemd/system/kiosk.service`

**What it does (since 2026-10-13, Critical System Configuration §8):**
- Starts on boot (`multi-user.target`)
- Runs sway as `gjbm2` in a logind session on tty1 (`PAMName=login`); sway starts `kiosk-wayland.sh`
- **Conflicts with `getty@tty1.service`** (VT1 race, see below) and with `kiosk-x11.service`
- On stop, kills the launcher and the kiosk Chromes (`ExecStopPost`), which live in the login session
- Auto-restarts on failure (RestartSec=5)

The X11 version described below (Xorg :0 on vt1 + `kiosk-wait-start.sh` + `kiosk-loop.sh`) is now
`kiosk-x11.service`, installed but disabled.

**Critical: VT1 / getty conflict**

Xorg runs on virtual terminal 1 (`vt1`). Ubuntu's default `getty@tty1.service` also
uses tty1 to show a login prompt. If both run simultaneously, they race for VT1
ownership. On some kernel versions getty wins after ~20 seconds, killing Xorg and
leaving a bare Linux login prompt on the display instead of the kiosk.

The service file includes `Conflicts=getty@tty1.service` so systemd stops getty
when the kiosk starts. For belt-and-suspenders reliability, getty should also be
**masked** on the server (see [Critical System Configuration](#critical-system-configuration)).

**Management:**
```bash
sudo systemctl status kiosk.service
sudo systemctl restart kiosk.service
sudo journalctl -u kiosk.service -f
```

### kiosk-wait-start.sh
**Location:** `/usr/local/bin/kiosk-wait-start.sh`

**What it does:**
- Waits up to 10 seconds for Xorg :0 to start
- Verifies Xorg remains stable for 3 seconds
- Launches kiosk-loop.sh as user gjbm2
- Exits with error if Xorg doesn't stabilize

### kiosk-wayland.sh
**Location:** `/usr/local/bin/kiosk-wayland.sh` (repo `remote-scripts/wayland/`), options in `/etc/default/kiosk-wayland`

**What it does:**
- Started by sway (`exec` in `sway-kiosk.conf`); replaces any older launcher (`/tmp/kiosk-wayland.pid`) and exits
  when its sway is gone
- Starts the north Chrome on workspace 1 (HDMI-A-1) and the south one on workspace 2 (HDMI-A-2), full screen,
  `--ozone-platform=wayland`, CDP 9222/9223, profiles `/tmp/chrome-{north,south}`
- Video path: `KIOSK_VIDEO=zerocopy` (default; `--use-angle=gl-egl` + `AcceleratedVideoDecodeLinuxZeroCopyGL`) or
  `copy` (zero-copy explicitly disabled)
- Every 5 s: restarts a Chrome that exited; if the south TV is missing, keeps the north page showing on the north TV
- **Logs:** `/var/log/kiosk.log`; Chrome output in `/tmp/chrome-{north,south}/chrome_console.log`

### kiosk-loop.sh (X11 fallback)
**Location:** `/usr/local/bin/kiosk-loop.sh` — used only by `kiosk-x11.service`

**What it does:**
- Polls HDMI connections every 2 seconds
- Configures displays with xrandr (3840x2160)
- Launches Chrome in kiosk mode per display, with VA-API hardware decode
  (`--enable-features=AcceleratedVideoDecodeLinuxGL`; keep all features in that one switch)
- With two X screens (Critical System Configuration §7, the normal setup since 2026-10-13): one openbox
  and Chrome per screen, north on `:0.0`, south on `:0.1`, and a single unclutter on north. `ONE_SCREEN` and `DIALOG_TAMER` apply only
  to the old single-screen layout, which it falls back to when X has one screen
- Manages dialog windows (DIALOG_TAMER)
- Supports single-screen mode (ONE_SCREEN)

**Deployed copy:** `/usr/local/bin/kiosk-loop.sh`. Backups: `.bak-20261011-140753` (before the VA-API flag),
`.bak-20261013-182218` (before per-TV screens); `.bak-20261013-1828*`/`-1836*`/`-1838*` are the intermediate per-TV
versions from the same evening (the last differences: openbox on the south screen, one unclutter instead of two).

**Environment Variables:**
- `ONE_SCREEN=1` - Disable HDMI-2, single display
- `ONE_SCREEN=mirror` - Mirror HDMI-2 to HDMI-1
- `DIALOG_TAMER=1` - Move dialogs every 1s
- `DIALOG_TAMER=2` - Move dialogs every 0.2s
- `DIALOG_TAMER=3` - Maximize & move dialogs every 0.2s

**URLs:**
- North screen: `http://IP:8000/display/north-screen`
- South screen: `http://IP:8000/display/south-screen`

**Logs:** `/var/log/kiosk.log`

### wifi-prefer-ap.service (systemd)
**Location:** `/etc/systemd/system/wifi-prefer-ap.service`, script `/usr/local/sbin/wifi-prefer-ap`,
config `/etc/default/wifi-prefer-ap` (`PREFERRED=<BSSID>`; optional `IFACE` (wlp2s0), `NET_ID` (0), `GRACE`, `RETRY`, `RETRY_MAX`, `MIN_SIGNAL`)

**What it does:**
- Every 5 s reads `wpa_cli status` for `wlp2s0` (netplan's wpa_supplicant, control socket `/run/wpa_supplicant`)
- On the preferred AP: pins the network to its BSSID, so steering requests to the slow AP are refused
- Disconnected 40 s while pinned: releases the pin so any AP can be used
- On another AP: every 120 s re-pins and reassociates if the preferred AP is visible at -78 dBm or better; if
  that attempt leaves the box disconnected for 40 s, the pin is released and the interval doubles (up to 30 min)
- The pin is held only in wpa_supplicant's memory; stopping the service clears it (`ExecStopPost=... --release`)
- Never edits netplan or the stored Wi-Fi credentials

**Logs:** `journalctl -u wifi-prefer-ap` (only the last two octets of BSSIDs are logged)

### webview-manager.sh
**Location:** `/usr/local/bin/webview-manager.sh`

**What it does:**
- Simpler alternative to kiosk-loop.sh
- Uses Chromium instead of Chrome
- Basic dual-screen support
- No dialog management

---

## Light Sensor System

### Architecture

**Server Side (Flask):**
- WebSocket server: `overlay_ws_server.py` (port 8765)
- Handler: `routes/lightsensor.py`
- Stores lux history and broadcasts to clients
- API: `GET /api/lightsensor/lightsense`

**Client Side (Media Server):**
- Python script reads Yoctopuce USB light sensor
- WebSocket client sends: `{"lux": value, "sensor_name": "name"}`
- Connects to: `ws://IP:8765`
- Runs as systemd service or cron job

### Troubleshooting

**After IP address change:**

```bash
# 1. Diagnose setup
./remote-maintenance.sh diagnose-lightsensor

# Review output for:
# - Script location
# - Service name
# - Hardcoded IPs

# 2. Fix IP addresses
./remote-maintenance.sh fix-lightsensor 95.141.21.170

# 3. SSH and restart service
ssh gjbm2@screen-machine-drawingroom
sudo systemctl restart <sensor-service-name>

# 4. Verify data flowing
curl http://95.141.21.170:5000/api/lightsensor/lightsense
```

---

## IP Address Management

### When ISP Changes Your IP

**What needs updating:**
1. Main server `.env` (Flask/Vite config)
2. Kiosk scripts (display URLs)
3. Light sensor client (WebSocket URL)

### Automated Update Process

```bash
# 1. Update main server
cd /home/gjbm2/dev/screen-machine
sed -i 's/OLD_IP/NEW_IP/g' .env

# 2. Update kiosk scripts
cd media-server/local-scripts/
./remote-maintenance.sh update-ip OLD_IP NEW_IP

# 3. Fix light sensor
./remote-maintenance.sh fix-lightsensor NEW_IP

# 4. Verify
./remote-maintenance.sh system-status
```

### Manual Update (if needed)

```bash
# SSH to media server
ssh gjbm2@screen-machine-drawingroom

# Update kiosk scripts
sudo sed -i 's/185\.254\.136\.244/95.141.21.170/g' /usr/local/bin/kiosk-loop.sh
sudo sed -i 's/185\.254\.136\.253/95.141.21.170/g' /usr/local/bin/webview-manager.sh

# Restart kiosk
sudo systemctl restart kiosk.service

# Verify
grep -n "95.141.21.170" /usr/local/bin/kiosk-*.sh
```

---

## Deployment to New Ubuntu Instance

### Automated Deployment

**Remote deployment (recommended):**
```bash
cd media-server/local-scripts/
./deploy-kiosk.sh 192.168.1.100 ubuntu
```

**Local deployment:**
```bash
# Copy files to new server
scp -r media-server/ user@new-server:/home/user/

# SSH and deploy
ssh user@new-server
cd media-server/local-scripts/
./deploy-kiosk.sh
```

### What Gets Installed

1. **System packages:** sway (no recommends; the default kiosk), jq, and for the X11 fallback xorg, openbox, wmctrl,
   unclutter, x11-xserver-utils, snapd. Nothing is upgraded (`--no-upgrade`, no `apt upgrade`)
2. **Google Chrome:** From official repository
3. **User account:** gjbm2 (UID 1000)
4. **System lockdown:**
   - `getty@tty1.service` masked (prevents VT1 conflict)
   - `unattended-upgrades` + all `apt-daily*` services masked (prevents automatic updates)
5. **Scripts:** Deployed to `/usr/local/bin/` with correct ownership
6. **Services:** `kiosk.service` (sway) installed, enabled and (re)started; `kiosk-x11.service` installed disabled;
   `sway-kiosk.conf`, `kiosk-wayland.sh`, `/etc/default/kiosk-wayland` and the X11 fallback files including
   `20-zaphod.conf`
7. **Logs:** `/var/log/kiosk.log` created

Not done by `deploy-kiosk.sh` (run separately afterwards): VA-API hardware decode packages and groups
(`experiments/stream-latency/beelink/enable-hwdecode.sh`, see Critical System Configuration §4), the Wi-Fi
access-point lock (`local-scripts/install-wifi-prefer-ap.sh`, §5), wired-first networking
(`local-scripts/install-ethernet-primary.sh`, §6) and the Deadman watchdog (`install-deadman.sh`). Without the VA-API
step, Chrome falls back to software video decode (no 4K live video).

### Post-Deployment

```bash
# Update IP addresses
./remote-maintenance.sh update-ip 185.254.136.244 YOUR_IP

# Verify
./remote-maintenance.sh system-status
```

### Light Sensor Setup (Optional)

The media server can optionally include a **Yoctopuce light sensor** that sends ambient light data to the Flask server for automatic brightness adjustments.

**Hardware:**
- Device: Yoctopuce Yocto-Light-V5 (USB)
- Connected to media server via USB

**Software Setup:**

1. **Install Python dependencies:**
```bash
ssh gjbm2@media-server
python3 -m venv ~/.venv
source ~/.venv/bin/activate
pip install yoctopuce websockets
```

2. **Deploy light sensor script:**
   - Place `lightsense.py` in `/home/gjbm2/lightsense.py`
   - Configure WebSocket URL to point to your Flask server:
```python
WS_URL = "ws://YOUR_FLASK_IP:8765/"
```

3. **Create systemd service:**
```bash
sudo tee /etc/systemd/system/light-relay.service << 'EOF'
[Unit]
Description=Yoctopuce Light Sensor WebSocket Relay
After=network.target

[Service]
WorkingDirectory=/home/gjbm2
ExecStart=/home/gjbm2/.venv/bin/python /home/gjbm2/lightsense.py
Restart=always
User=gjbm2

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable light-relay.service
sudo systemctl start light-relay.service
```

4. **Verify:**
```bash
# Check service status
systemctl status light-relay.service

# Check USB device
lsusb | grep -i yocto

# Check WebSocket connection
lsof -i -n | grep python | grep 8765
```

**Troubleshooting Light Sensor:**

```bash
# Diagnose issues
cd media-server/local-scripts
./remote-maintenance.sh diagnose-lightsensor

# Restart service
ssh gjbm2@media-server
pkill -9 -f lightsense.py
rm -f /tmp/.yoctolock
sudo systemctl restart light-relay.service

# Check logs
journalctl -u light-relay.service -f
```

**IP Address Changes:**
When your Flask server IP changes, update the light sensor configuration:
```bash
ssh gjbm2@media-server
nano /home/gjbm2/lightsense.py
# Update WS_URL to new IP
sudo systemctl restart light-relay.service
```

### Manual Deployment (step-by-step)

Complete walkthrough for configuring a fresh Ubuntu 24.04 box as a kiosk from scratch.

```bash
# ============================================================
# STEP 1: Install dependencies
# ============================================================
sudo apt update            # no 'apt upgrade': the box is deliberately kept on its tested versions
wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | sudo apt-key add -
echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" | \
  sudo tee /etc/apt/sources.list.d/google-chrome.list
sudo apt update
sudo apt install -y --no-upgrade --no-install-recommends sway          # the default (Wayland) kiosk
sudo apt install -y --no-upgrade google-chrome-stable jq xorg xserver-xorg-core openbox \
  unclutter wmctrl x11-xserver-utils snapd                             # jq: status scripts; the rest: X11 fallback

# ============================================================
# STEP 2: Create kiosk user
# ============================================================
sudo useradd -m -s /bin/bash gjbm2
sudo usermod -aG audio,video gjbm2

# ============================================================
# STEP 3: Lock down the system (CRITICAL -- do not skip)
# ============================================================

# 3a. Mask getty on tty1 (prevents login prompt stealing the display)
sudo systemctl stop getty@tty1.service
sudo systemctl disable getty@tty1.service
sudo systemctl mask getty@tty1.service

# 3b. Disable ALL automatic updates (prevents surprise reboots/breakage)
sudo systemctl stop unattended-upgrades.service
sudo systemctl disable unattended-upgrades.service
sudo systemctl mask unattended-upgrades.service

sudo systemctl stop apt-daily.timer
sudo systemctl disable apt-daily.timer
sudo systemctl mask apt-daily.timer

sudo systemctl stop apt-daily-upgrade.timer
sudo systemctl disable apt-daily-upgrade.timer
sudo systemctl mask apt-daily-upgrade.timer

sudo systemctl stop apt-daily.service
sudo systemctl mask apt-daily.service

sudo systemctl stop apt-daily-upgrade.service
sudo systemctl mask apt-daily-upgrade.service

# 3c. Verify lockdown
systemctl is-enabled getty@tty1.service           # masked
systemctl is-enabled unattended-upgrades.service  # masked
systemctl is-enabled apt-daily.timer              # masked
systemctl is-enabled apt-daily-upgrade.timer      # masked

# ============================================================
# STEP 4: Deploy kiosk scripts
# ============================================================
sudo cp remote-scripts/kiosk-*.sh /usr/local/bin/
sudo cp remote-scripts/webview-manager.sh /usr/local/bin/
sudo chown root:root /usr/local/bin/kiosk-*.sh
sudo chown gjbm2:gjbm2 /usr/local/bin/webview-manager.sh
sudo chmod +x /usr/local/bin/kiosk-*.sh /usr/local/bin/webview-manager.sh

# ============================================================
# STEP 5: Install and start systemd service
# ============================================================
sudo cp remote-scripts/kiosk.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable kiosk.service
sudo systemctl start kiosk.service

# ============================================================
# STEP 6: Create log file
# ============================================================
sudo touch /var/log/kiosk.log
sudo chown gjbm2:gjbm2 /var/log/kiosk.log

# ============================================================
# STEP 7: Verify everything is working
# ============================================================
systemctl status kiosk.service                    # should be active (running)
systemctl is-enabled getty@tty1.service           # should be masked
cat /sys/class/drm/card*-HDMI-A-*/status             # should say connected for each TV
SWAYSOCK=$(ls /run/user/1000/sway-ipc.*.sock) swaymsg -p -t get_outputs | grep -E '^Output|Current mode'
curl -s localhost:9222/json | grep -o 'display/[a-z]*-screen'   # north page (9223: south)
```

---

## Troubleshooting

### SSH Connection Issues

**Problem:** Cannot connect to media server

**Diagnosis:**
```bash
# Test basic connectivity
ping screen-machine-drawingroom

# Test SSH manually
ssh -p 22 gjbm2@screen-machine-drawingroom

# Check SSH keys
ls -la ~/.ssh/

# Check .env file
cat local-scripts/.env
```

**Solutions:**
```bash
# Set up SSH keys
ssh-keygen -t ed25519
ssh-copy-id gjbm2@screen-machine-drawingroom

# Or use IP address instead of hostname
# In .env:
MEDIA_SERVER_HOST=192.168.1.100

# Check SSH key permissions
chmod 700 ~/.ssh
chmod 600 ~/.ssh/id_ed25519
```

### Kiosk Shows Linux Login Prompt Instead of Chrome

**Problem:** Physical display shows a text login prompt (`login:`) instead of Chrome kiosk.

**Cause:** `getty@tty1.service` is running and has taken over VT1 from Xorg. This is
the most common failure mode after a fresh install or kernel update.

**Fix:**
```bash
ssh gjbm2@screen-machine-drawingroom

# Mask getty (permanent fix)
sudo systemctl stop getty@tty1.service
sudo systemctl mask getty@tty1.service

# Restart kiosk
sudo systemctl restart kiosk.service

# Verify
systemctl is-enabled getty@tty1.service   # should be "masked"
systemctl is-active kiosk.service          # should be "active"
```

See [Critical System Configuration](#critical-system-configuration) for full details.

### Kiosk Service Issues

**Problem:** Service won't start or crashes

**Diagnosis:**
```bash
# Get status
./remote-maintenance.sh system-status

# Collect logs
./remote-maintenance.sh collect-logs

# Or manually:
ssh gjbm2@screen-machine-drawingroom
sudo journalctl -u kiosk.service -xe
cat /var/log/kiosk.log
```

**Common Causes:**
- **getty@tty1 conflict** (most common): see section above
- Chrome not installed: `which google-chrome-stable`
- User doesn't exist: `id gjbm2`
- Runtime dir missing: `ls -la /run/user/1000/`
- Display issues (sway, the default): `journalctl -u kiosk.service -n 50`, `tail /var/log/kiosk.log`,
  `SWAYSOCK=$(ls /run/user/1000/sway-ipc.*.sock) swaymsg -t get_outputs`
- X11 fallback only: `sudo -u gjbm2 DISPLAY=:0.0 xrandr --query` (and `:0.1` for the south TV)

### Display Issues

**Problem:** Displays show black screen or wrong resolution

**Diagnosis:**
```bash
ssh gjbm2@screen-machine-drawingroom

# Check displays
sudo -u gjbm2 DISPLAY=:0 xrandr --query

# Check Chrome processes
ps aux | grep chrome

# Check kiosk logs
tail -f /var/log/kiosk.log
```

**Solutions:**
```bash
# Restart service
sudo systemctl restart kiosk.service

# Force display detection
sudo -u gjbm2 DISPLAY=:0 xrandr --output HDMI-1 --auto
sudo -u gjbm2 DISPLAY=:0 xrandr --output HDMI-2 --auto

# Test manual Chrome launch
sudo -u gjbm2 DISPLAY=:0 google-chrome --kiosk http://example.com
```

### Light Sensor Issues

**Problem:** Sensor data not flowing after IP change

**Diagnosis:**
```bash
# Run diagnostic
./remote-maintenance.sh diagnose-lightsensor

# Check Flask endpoint
curl http://95.141.21.170:5000/api/lightsensor/lightsense

# Check USB devices
ssh gjbm2@screen-machine-drawingroom
lsusb | grep -i yocto
```

**Solution:**
```bash
# Fix IP addresses
./remote-maintenance.sh fix-lightsensor 95.141.21.170

# Find and restart sensor service
ssh gjbm2@screen-machine-drawingroom
systemctl list-unit-files | grep -i sensor
sudo systemctl restart <sensor-service>
```

---

## Maintenance Procedures

### Daily Checks
```bash
# Quick check (30 seconds)
./check-status.sh

# Or detailed report (2 minutes)
cd local-scripts/
./remote-maintenance.sh system-status
```

### Weekly Maintenance
```bash
# Collect logs
./remote-maintenance.sh collect-logs

# Review for errors
grep -i "error\|fail\|crash" maintenance-results/logs-*/system.log
```

### After ISP IP Change
```bash
# 1. Update main server
cd /home/gjbm2/dev/screen-machine
sed -i 's/OLD_IP/NEW_IP/g' .env

# 2. Update kiosk scripts
cd media-server/local-scripts/
./remote-maintenance.sh update-ip OLD_IP NEW_IP

# 3. Fix light sensor
./remote-maintenance.sh fix-lightsensor NEW_IP

# 4. Verify all systems
./remote-maintenance.sh system-status
curl http://NEW_IP:5000/api/health
curl http://NEW_IP:5000/api/lightsensor/lightsense
```

### After System Update (manual, planned only)

**Automatic updates are disabled on the kiosk.** Only perform manual updates during
a planned maintenance window when you have SSH or physical access to recover from
breakage. Kernel updates in particular can change driver/timing behaviour and break
the kiosk.

```bash
# SSH to media server
ssh gjbm2@screen-machine-drawingroom

# Check what would be updated BEFORE applying
sudo apt update
apt list --upgradable

# Only proceed if you understand the changes and have recovery access
sudo apt upgrade -y

# Reboot if kernel was updated
sudo reboot

# Verify after reboot (from dev machine)
cd media-server/
./check-status-compact.sh

# If kiosk shows login prompt instead of Chrome after reboot:
ssh gjbm2@screen-machine-drawingroom
systemctl is-enabled getty@tty1.service   # check if still masked
sudo systemctl restart kiosk.service       # try restarting
```

---

## File Locations Reference

### Development Machine
```
/home/gjbm2/dev/screen-machine/
├── .env                                    # Main Flask/Vite config
└── media-server/
    ├── README.md                           # This file
    ├── check-status.sh                     # Quick status check
    ├── restart-kiosk.sh                    # Restart kiosk service
    ├── reboot-server.sh                    # Reboot server
    ├── local-scripts/
    │   ├── .env                            # SSH credentials
    │   ├── .env.example                    # Template
    │   ├── remote-maintenance.sh           # Main harness
    │   ├── diagnose-lightsensor.sh         # Diagnostic script
    │   └── deploy-kiosk.sh                 # Deployment automation
    ├── remote-scripts/                     # Version-controlled copies
    │   ├── kiosk-wait-start.sh
    │   ├── kiosk-loop.sh
    │   ├── webview-manager.sh
    │   └── kiosk.service
    └── maintenance-results/                # Command outputs (gitignored)
```

### Media Server
```
/usr/local/bin/
├── kiosk-wait-start.sh
├── kiosk-loop.sh
└── webview-manager.sh

/etc/systemd/system/
└── kiosk.service

/var/log/
└── kiosk.log

/run/user/1000/
└── (runtime directory for gjbm2)
```

---

## Technical Specifications

### Media Server Requirements
- **OS:** Ubuntu 24.04 LTS (headless)
- **RAM:** 4GB minimum, 8GB recommended
- **CPU:** 2+ cores recommended
- **GPU:** Any with dual HDMI outputs
- **Displays:** 2× 4K (3840×2160) HDMI displays
- **Network:** Static IP or dynamic DNS recommended
- **USB:** For optional light sensor

### Software Versions
- **Xorg:** Any recent version
- **Chrome:** 136.0.7103.113 (does not update by itself because the apt timers are masked, but is not apt-held: a manual `apt upgrade` would install 155.x; re-check the VA-API flag after any upgrade)
- **Python:** 3.10+ (for light sensor client)
- **Node.js:** Not required on media server

### Network Ports
- **5000:** Flask API (TCP)
- **8000:** Vite frontend (TCP)
- **8765:** WebSocket for light sensor (TCP)
- **22:** SSH (TCP)

### Current Configuration
- **ISP IP:** 95.141.21.170
- **Previous IPs:** 185.254.136.244, 185.254.136.253
- **Media Server:** screen-machine-drawingroom
- **User:** gjbm2 (UID 1000)
- **Last Updated:** February 8, 2026

---

## Best Practices

### Security
- ✅ Use SSH keys (not passwords)
- ✅ Keep `.env` in `.gitignore`
- ✅ Use strong passphrases on SSH keys
- ✅ Limit SSH access to specific IPs (optional)

### System Stability (Critical)
- ✅ **Disable automatic updates** -- mask `unattended-upgrades`, `apt-daily*` (see [Critical System Configuration](#critical-system-configuration))
- ✅ **Mask `getty@tty1`** -- prevents login prompt from stealing Xorg's display
- ✅ **Never run `apt upgrade`** on the kiosk without a planned maintenance window and physical/SSH recovery access
- ✅ **Pin the kernel** -- if the system is working, avoid kernel changes
- ❌ Do NOT enable unattended upgrades, auto-reboot, or any automatic package management

### Operations
- ✅ Always test with `system-status` before changes
- ✅ Use `update-ip` command (don't edit scripts manually)
- ✅ Keep `remote-scripts/` in sync with deployed scripts
- ✅ Save `maintenance-results/` outputs for debugging
- ✅ Document any custom modifications

### Deployment
- ✅ Use automated `deploy-kiosk.sh` script (includes system lockdown)
- ✅ Verify getty is masked and updates are disabled after every fresh install
- ✅ Update IP addresses immediately after deployment
- ✅ Verify displays before considering deployment complete
- ✅ Test light sensor if present
- ✅ Document any deployment-specific notes

---

## Support & Documentation

### Related Documentation
- **../docs/DEPLOYMENT_IP_CONFIGURATION.md** - Project-wide IP configuration logic
- **../README.md** - Main project documentation
- **../docs/AUDIO_WS_PROTOCOL.md** - WebSocket and API protocols

### Getting Help

1. **Quick status check:**
   ```bash
   ./check-status.sh
   ```

2. **Try restarting kiosk:**
   ```bash
   ./restart-kiosk.sh
   ```

3. **Collect detailed logs:**
   ```bash
   cd local-scripts/
   ./remote-maintenance.sh collect-logs
   ```

3. **Review this README** - Most common issues are covered

4. **Check git history** - See what changed recently

5. **SSH directly** if needed:
   ```bash
   ssh gjbm2@screen-machine-drawingroom
   ```

---

**Maintained by:** Screen Machine Project  
**Last Updated:** February 8, 2026  
**Version:** 2.1
