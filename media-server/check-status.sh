#!/bin/bash
# Quick status check for media server
# Usage: ./check-status.sh [--verbose]

set -e

# Check for verbose flag
VERBOSE=false
if [[ "$1" == "--verbose" ]] || [[ "$1" == "-v" ]]; then
    VERBOSE=true
fi

# Get script directory and load config
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$SCRIPT_DIR/maintenance.log"

# Redirect ALL output to log file AND console
exec > >(tee -a "$LOG_FILE")
exec 2>&1

# Try to load from local-scripts/.env first, then current directory
if [ -f "$SCRIPT_DIR/local-scripts/.env" ]; then
    source "$SCRIPT_DIR/local-scripts/.env"
elif [ -f "$SCRIPT_DIR/.env" ]; then
    source "$SCRIPT_DIR/.env"
fi

# Configuration with fallbacks
REMOTE_HOST="${MEDIA_SERVER_HOST:-screen-machine-drawingroom}"
REMOTE_USER="${MEDIA_SERVER_USER:-gjbm2}"
REMOTE_PORT="${MEDIA_SERVER_PORT:-22}"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

if [ "$VERBOSE" = true ]; then
    echo "========================================="
    echo "Starting status check for $REMOTE_USER@$REMOTE_HOST"
    echo "========================================="
    echo -e "${BLUE}========================================${NC}"
    echo -e "${BLUE}  Media Server Status Check${NC}"
    echo -e "${BLUE}  Server: $REMOTE_USER@$REMOTE_HOST${NC}"
    echo -e "${BLUE}========================================${NC}"
    echo ""
else
    echo "==========================================="
    echo "Media Server Quick Status: $REMOTE_USER@$REMOTE_HOST"
    echo "==========================================="
fi

# Execute status check on remote server and capture output
OUTPUT=$(ssh -T -o LogLevel=ERROR -p "$REMOTE_PORT" "$REMOTE_USER@$REMOTE_HOST" << EOF
# Colors for remote
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'
VERBOSE=$VERBOSE

if [ "\$VERBOSE" = true ]; then
    echo "=== System Info ==="
    echo "Hostname: \$(hostname)"
    echo "Uptime: \$(uptime -p)"
    echo ""
fi

echo "=== Kiosk Service Status ==="
# kiosk.service is the sway (Wayland) kiosk since 2026-10-13; report the X11 fallback instead when that is the one running
KIOSK_UNIT=kiosk.service
if ! systemctl is-active --quiet kiosk.service && systemctl is-active --quiet kiosk-x11.service; then
    KIOSK_UNIT=kiosk-x11.service
fi
if systemctl is-active --quiet \$KIOSK_UNIT; then
    echo -e "${GREEN}✓ Service is RUNNING (\$KIOSK_UNIT)${NC}"
    echo ""
    systemctl status \$KIOSK_UNIT --no-pager | head -20
else
    echo -e "${RED}✗ Service is NOT RUNNING${NC}"
    echo ""
    systemctl status \$KIOSK_UNIT --no-pager | head -20
fi
echo ""

echo "=== Light Sensor Service Status ==="
if systemctl is-active --quiet light-relay.service; then
    echo -e "${GREEN}✓ Service is RUNNING${NC}"
    echo ""
    systemctl status light-relay.service --no-pager | head -15
    echo ""
    echo "WebSocket connection:"
    lsof -i -n | grep python | grep 8765 || echo "No active WebSocket connection found"
    echo ""
    echo "USB Sensor:"
    lsusb | grep -i yocto || echo "No Yoctopuce sensor detected"
else
    echo -e "${RED}✗ Service is NOT RUNNING${NC}"
    echo ""
    systemctl status light-relay.service --no-pager | head -15
fi
echo ""

echo "=== Display Processes ==="
# display server: sway (kiosk.service, default since 2026-10-13) or Xorg (kiosk-x11.service fallback)
if pgrep -x sway >/dev/null; then
    echo -e "\${GREEN}✓ Display processes running (sway)\${NC}"
elif pgrep -x Xorg >/dev/null; then
    echo -e "\${GREEN}✓ Display processes running (Xorg, X11 fallback)\${NC}"
else
    echo -e "\${RED}✗ No display server (sway or Xorg) running\${NC}"
fi
ps aux | grep -E "(sway|Xorg|kiosk-(wayland|loop)|chrome)" | grep -v grep | head -10
echo ""

echo "=== Displays Detected ==="
# HDMI connectors from sysfs (the same under sway and X11, needs no display socket), then the display server's
# view: swaymsg under sway, xrandr under the X11 fallback (one X screen per TV, :0.0 north, :0.1 south)
DISPLAY_COUNT=0
for C in /sys/class/drm/card*-HDMI-A-*; do
    [ "\$(cat "\$C/status" 2>/dev/null)" = connected ] && [ "\$(cat "\$C/enabled" 2>/dev/null)" = enabled ] && DISPLAY_COUNT=\$((DISPLAY_COUNT + 1))
done
if [ "\$DISPLAY_COUNT" -eq 2 ]; then
    echo -e "\${GREEN}✓ 2 displays connected\${NC}"
elif [ "\$DISPLAY_COUNT" -eq 1 ]; then
    echo -e "\${YELLOW}⚠ Only 1 display connected\${NC}"
else
    echo -e "\${RED}✗ No displays detected\${NC}"
fi
for C in /sys/class/drm/card*-HDMI-A-*; do
    echo "  \${C##*/}: \$(cat "\$C/status" 2>/dev/null), \$(cat "\$C/enabled" 2>/dev/null)"
done
SWAY_PID=\$(pgrep -xo sway)
if [ -n "\$SWAY_PID" ]; then
    # this sway's own socket (a crashed sway can leave a stale one behind)
    OUTPUTS=\$(SWAYSOCK=\$(ls /run/user/*/sway-ipc.*."\$SWAY_PID".sock 2>/dev/null | head -1) swaymsg -t get_outputs -r 2>/dev/null \
        | jq -r '.[] | "  sway: " + .name + " " + (if .active then "\(.current_mode.width)x\(.current_mode.height)@\(.current_mode.refresh / 10 | round / 100)Hz" else "disabled" end)' 2>/dev/null)
    if [ -n "\$OUTPUTS" ]; then
        echo "\$OUTPUTS"
    else
        echo -e "  \${YELLOW}⚠ Could not query sway\${NC}"
    fi
elif pgrep -x Xorg >/dev/null; then
    { DISPLAY=:0.0 xrandr --current; DISPLAY=:0.1 xrandr --current; } 2>/dev/null | grep " connected" | sed 's/^/  X11: /'
fi
echo ""

echo "=== Chrome URLs and Status ==="
# Get Chrome process details (match the page URL, not the binary: under sway the browser's command line starts
# /opt/google/chrome/chrome)
CHROME_PIDS=\$(pgrep -f "chrome.*display/(north|south)-screen" || echo "")
if [ -n "\$CHROME_PIDS" ]; then
    echo "Chrome kiosk processes found:"
    for PID in \$CHROME_PIDS; do
        # Get the command line to extract the URL
        URL=\$(ps -p \$PID -o args= | grep -oP 'http://[^ ]+' | head -1)
        DISPLAY_NAME=\$(echo "\$URL" | grep -oP '(north|south)-screen' || echo "unknown")
        
        echo ""
        echo "  Process \$PID (\$DISPLAY_NAME):"
        echo "    URL: \$URL"
        
        # Try to get Chrome debugging info (if remote debugging is enabled)
        # Check for white screen indicators via process stats
        MEM=\$(ps -p \$PID -o rss= 2>/dev/null | awk '{printf "%.1f MB", \$1/1024}')
        echo "    Memory: \$MEM"
        
        # Check Chrome's user data dir to see which one
        DATADIR=\$(ps -p \$PID -o args= | grep -oP 'user-data-dir=[^ ]+' | cut -d= -f2)
        echo "    Data dir: \$DATADIR"
        
        # Check for crash files
        if [ -n "\$DATADIR" ] && [ -d "\$DATADIR" ]; then
            CRASHES=\$(find "\$DATADIR" -name "*.dmp" 2>/dev/null | wc -l)
            if [ "\$CRASHES" -gt 0 ]; then
                echo -e "    \${YELLOW}⚠ \$CRASHES crash dump(s) found\${NC}"
            fi
        fi
        
        # Check if Chrome is responsive by looking at CPU usage
        CPU=\$(ps -p \$PID -o %cpu= 2>/dev/null | tr -d ' ')
        echo "    CPU: \${CPU}%"
        if (( \$(echo "\$CPU < 0.1" | bc -l 2>/dev/null || echo 0) )); then
            echo -e "    \${YELLOW}⚠ Low CPU - may be stuck/frozen\${NC}"
        fi
    done
    
    echo ""
    echo "=== Open Pages: Proof Pages Are Loaded ==="
    # each Chrome's DevTools endpoint lists its open pages (north :9222, south :9223; the same under sway and X11)
    for S in north:9222 south:9223; do
        NAME=\${S%:*}; PORT=\${S#*:}
        echo ""
        echo "--- \${NAME^^} Screen (DevTools localhost:\$PORT) ---"
        if ! command -v jq >/dev/null; then
            echo -e "\${YELLOW}⚠ jq not installed: open pages not checked\${NC}"
        elif JSON=\$(curl -s -m 2 "localhost:\$PORT/json"); then
            PAGES=\$(echo "\$JSON" | jq -r '.[] | select(.type == "page") | .url + (if (.title // "") != "" then "  (" + .title + ")" else "" end)' 2>/dev/null)
            N=\$(echo "\$PAGES" | grep -c "display/\$NAME-screen")
            if [ "\$N" -eq 1 ]; then
                echo -e "\${GREEN}✓ display/\$NAME-screen loaded\${NC}"
            elif [ "\$N" -gt 1 ]; then
                echo -e "\${YELLOW}⚠ display/\$NAME-screen open in \$N tabs (expected 1)\${NC}"
            else
                echo -e "\${YELLOW}⚠ display/\$NAME-screen not open\${NC}"
            fi
            echo "\${PAGES:-(no pages open)}"
        else
            echo -e "\${YELLOW}⚠ DevTools port \$PORT not answering\${NC}"
        fi
    done

    # Chrome runs without --enable-logging since 2026-07-28, so these logs only hold Chrome's own stderr
    echo ""
    echo "=== Chrome stderr (chrome_console.log) ==="
    echo ""
    echo "--- NORTH Screen (last 25 lines) ---"
    if [ -f "/tmp/chrome-north/chrome_console.log" ]; then
        tail -25 /tmp/chrome-north/chrome_console.log 2>/dev/null | grep -v "^$" || echo "(log is empty)"
    else
        echo -e "\${YELLOW}⚠ No log at /tmp/chrome-north/chrome_console.log\${NC}"
    fi

    echo ""
    echo "--- SOUTH Screen (last 25 lines) ---"
    if [ -f "/tmp/chrome-south/chrome_console.log" ]; then
        tail -25 /tmp/chrome-south/chrome_console.log 2>/dev/null | grep -v "^$" || echo "(log is empty)"
    else
        echo -e "\${YELLOW}⚠ No log at /tmp/chrome-south/chrome_console.log\${NC}"
    fi

    echo ""
    echo "=== What URLs Were Loaded at Startup ==="
    echo "(from kiosk startup log)"
    # kiosk-wayland.sh logs "spawn north ...", the X11 kiosk-loop.sh "spawn NORTH ..."
    grep -iE "spawn (north|south)" /var/log/kiosk.log 2>/dev/null | tail -3 || echo "(no spawn logs found)"
    
    # Check for GPU issues (common cause of white screens)
    echo ""
    echo "GPU Process status:"
    GPU_PIDS=\$(pgrep -f "chrome.*type=gpu" || echo "")
    if [ -n "\$GPU_PIDS" ]; then
        echo -e "  \${GREEN}✓ GPU process running\${NC}"
    else
        echo -e "  \${YELLOW}⚠ No GPU process found (possible rendering issue)\${NC}"
    fi
else
    echo -e "\${RED}✗ No Chrome kiosk processes found\${NC}"
fi
echo ""

echo "=== Network Verification: Are Pages Actually Loaded? ==="
echo "Active connections from Chrome to the frontend origin (port 8000 -> Flask :5000):"
# counted by port with ss: lsof cannot see Chrome's sockets (checked 2026-10-13: none listed while the pages were
# loaded), and on this box only the kiosk Chromes talk to port 8000
CHROME_CONNS=\$(ss -Htn state established '( dport = :8000 )' 2>/dev/null | wc -l)
if [ "\$CHROME_CONNS" -gt 0 ]; then
    echo -e "\${GREEN}✓ \$CHROME_CONNS active connections to port 8000\${NC}"
    echo "Connection details:"
    ss -tn state established '( dport = :8000 )' 2>/dev/null | head -10
else
    echo -e "\${RED}✗ No active connections to port 8000 - pages may not be loaded!\${NC}"
fi
echo ""
echo "WebSocket connections (port 8765):"
ss -tnp state established '( dport = :8765 )' 2>/dev/null | head -6
echo ""

echo "=== Recent Errors (last 10) ==="
# no sudo: the kiosk user is in the adm group, which can read the system journal. Filtered: every sway start logs two
# PAM lines (pam_lastlog.so is missing), and every stop more from the ExecStopPost pkills, which also open a login
# session (PAMName=login); unfiltered they push real errors out of the last 10
journalctl -u \$KIOSK_UNIT -p err --since "24 hours ago" --no-pager \
    | grep -vE 'pam_lastlog|PAM adding faulty module|^[^ ]+ +[0-9]+ [0-9:]+ [^ ]+ \(pkill\)' | tail -10 || echo "No errors in last 24 hours"
echo ""

echo "=== Disk Usage ==="
df -h / | tail -1
echo ""

echo "=== Memory Usage ==="
free -h | grep "Mem:"
echo ""
EOF
)

# Display output (automatically logged via exec redirect)
echo "$OUTPUT"

echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}Status check complete!${NC}"
echo -e "${BLUE}========================================${NC}"

echo "========================================="
echo "Status check completed successfully"
echo "========================================="
echo ""
echo -e "${YELLOW}Log file:${NC} $LOG_FILE"

