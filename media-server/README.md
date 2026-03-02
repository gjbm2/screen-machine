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
│  │  Vite Frontend (port 8080)                                  │ │
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
│  │  Xorg :0 (Display Server)                                   │ │
│  │  - Manages dual 4K HDMI displays                            │ │
│  │  - Runs headless (no physical monitor needed)              │ │
│  └────────────────────────────────────────────────────────────┘ │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │  kiosk.service (systemd)                                    │ │
│  │  - Starts Xorg on boot                                      │ │
│  │  - Runs kiosk-loop.sh as user gjbm2                        │ │
│  │  - Auto-restarts on failure                                 │ │
│  └────────────────────────────────────────────────────────────┘ │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │  kiosk-loop.sh                                              │ │
│  │  - Detects HDMI displays                                    │ │
│  │  - Launches Chrome in kiosk mode with console logging      │ │
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
│   ├── .env.example             # Configuration template
│   └── .env                     # Your credentials (gitignored)
│
└── remote-scripts/         # Deployed to media server
    ├── kiosk-wait-start.sh      # Startup wrapper
    ├── kiosk-loop.sh            # Main kiosk manager
    ├── webview-manager.sh       # Alternative manager
    └── kiosk.service            # Systemd service definition
```

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

---

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
- Display processes (Xorg, Chrome)
- Connected displays (xrandr)
- **Chrome console logs** - proof that pages are rendering
  - `/tmp/chrome-north/chrome_console.log`
  - `/tmp/chrome-south/chrome_console.log`
  - Shows JavaScript errors, network requests, page load events
- Network connections to Flask server
- Recent errors from logs
- Disk and memory usage

**Chrome Console Logging:**
Chrome now logs all console output (errors, warnings, network activity) to dedicated log files. This lets you verify pages are actually loading and rendering without looking at the displays. Run `./check-status.sh --verbose` to see the console logs.

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
- Display processes (Xorg, Chrome)
- Connected displays
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

**What it does:**
- Starts on boot (`multi-user.target`)
- Creates user runtime directory (`/run/user/1000`)
- **Conflicts with `getty@tty1.service`** to prevent VT1 ownership race (see below)
- Launches Xorg :0 on vt1
- Executes kiosk-wait-start.sh as post-start hook
- Auto-restarts on failure (RestartSec=5)

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

### kiosk-loop.sh
**Location:** `/usr/local/bin/kiosk-loop.sh`

**What it does:**
- Polls HDMI connections every 2 seconds
- Configures displays with xrandr (3840x2160)
- Launches Chrome in kiosk mode per display
- Manages dialog windows (DIALOG_TAMER)
- Supports single-screen mode (ONE_SCREEN)

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

1. **System packages:** xorg, openbox, wmctrl, unclutter, xrandr, snapd
2. **Google Chrome:** From official repository
3. **User account:** gjbm2 (UID 1000)
4. **System lockdown:**
   - `getty@tty1.service` masked (prevents VT1 conflict)
   - `unattended-upgrades` + all `apt-daily*` services masked (prevents automatic updates)
5. **Scripts:** Deployed to `/usr/local/bin/` with correct ownership
6. **Service:** Installed, enabled, and started (with `Conflicts=getty@tty1.service`)
7. **Logs:** `/var/log/kiosk.log` created

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
sudo apt update && sudo apt upgrade -y
wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | sudo apt-key add -
echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" | \
  sudo tee /etc/apt/sources.list.d/google-chrome.list
sudo apt update
sudo apt install -y google-chrome-stable xorg xserver-xorg-core openbox \
  unclutter wmctrl xrandr xset snapd

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
DISPLAY=:0 xrandr --query 2>/dev/null | grep " connected"  # should show HDMI outputs
ps aux | grep "google-chrome-stable.*kiosk"       # should show Chrome processes
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
- X11 display issues: `sudo -u gjbm2 DISPLAY=:0 xrandr --query`

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
- **Chrome:** 136.0.7103.113 (updates automatically)
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
- **../DEPLOYMENT_IP_CONFIGURATION.md** - Project-wide IP configuration logic
- **../README.md** - Main project documentation
- **../protocol.md** - WebSocket and API protocols

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
