#!/bin/bash
# Compact status check for media server
# Usage: ./check-status-compact.sh

set -e

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
NC='\033[0m'

echo "Media Server Quick Status - $(date '+%H:%M:%S')"
echo "================================================================"

# Execute compact status check (suppress all SSH noise)
OUTPUT=$(ssh -q -o LogLevel=ERROR -p "$REMOTE_PORT" "$REMOTE_USER@$REMOTE_HOST" << 'EOF' 2>/dev/null
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

# System
echo -n "[System] "
echo "$(hostname) | Uptime: $(uptime -p)"

# Kiosk Service: kiosk.service is the sway (Wayland) kiosk since 2026-10-13; the X11 fallback is kiosk-x11.service
echo -n "[Kiosk Service] "
if pgrep -x sway >/dev/null; then DS=sway; elif pgrep -x Xorg >/dev/null; then DS=X11; else DS=""; fi
if systemctl is-active --quiet kiosk.service; then
    echo -e "${GREEN}✓ Running${DS:+ ($DS)}${NC}"
elif systemctl is-active --quiet kiosk-x11.service; then
    echo -e "${GREEN}✓ Running (X11 fallback, kiosk-x11.service)${NC}"
else
    echo -e "${RED}✗ NOT RUNNING${NC}"
fi

# Light Sensor Service
echo -n "[Light Sensor] "
if systemctl is-active --quiet light-relay.service; then
    # Check WebSocket connection
    if lsof -i -n 2>/dev/null | grep python | grep 8765 >/dev/null; then
        echo -e "${GREEN}✓ Running + Connected to Flask${NC}"
    else
        echo -e "${YELLOW}⚠ Running but NOT connected${NC}"
    fi
else
    echo -e "${RED}✗ NOT RUNNING${NC}"
fi

# Chrome Instances with Page Load Detection: ask each Chrome's DevTools endpoint which pages it has open
# (north :9222, south :9223; the same under sway and X11). chrome_console.log is no proof: Chrome runs without
# --enable-logging since 2026-07-28, so it only holds Chrome's own stderr.
chrome_status() {  # chrome_status <north|south> <devtools port>
    local json pages n
    # match the page URL, not the binary: under sway the browser's command line starts /opt/google/chrome/chrome
    if ! pgrep -f "chrome.*display/$1-screen" >/dev/null; then
        echo -e "${RED}✗ NOT RUNNING${NC}"
        return
    fi
    if ! command -v jq >/dev/null; then
        echo -e "${YELLOW}⚠ Running (jq not installed, page not checked)${NC}"
        return
    fi
    if ! json=$(curl -s -m 2 "localhost:$2/json"); then
        echo -e "${YELLOW}⚠ Running, but DevTools port $2 not answering${NC}"
        return
    fi
    pages=$(echo "$json" | jq -r '.[] | select(.type == "page") | .url' 2>/dev/null)
    n=$(echo "$pages" | grep -c "display/$1-screen")
    if [ "$n" -eq 1 ]; then
        echo -e "${GREEN}✓ Page loaded (display/$1-screen)${NC}"
    elif [ "$n" -gt 1 ]; then
        echo -e "${YELLOW}⚠ Page loaded in $n tabs (expected 1)${NC}"
    else
        echo -e "${YELLOW}⚠ Not on display/$1-screen: $(echo "${pages:-no page open}" | head -1)${NC}"
    fi
}
echo -n "[Chrome North] "
chrome_status north 9222
echo -n "[Chrome South] "
chrome_status south 9223

# GPU Process
echo -n "[GPU Process] "
if pgrep -f "chrome.*type=gpu" >/dev/null; then
    echo -e "${GREEN}✓ Running${NC}"
else
    echo -e "${RED}✗ Missing (causes white screens)${NC}"
fi

# Displays: count connected+enabled HDMI connectors from sysfs (the same under sway and X11, needs no display
# socket), then add each output's mode from the display server: swaymsg under sway, xrandr under the X11 fallback
# (one X screen per TV, :0.0 north, :0.1 south)
echo -n "[Displays] "
DISPLAY_COUNT=0
for c in /sys/class/drm/card*-HDMI-A-*; do
    [ "$(cat "$c/status" 2>/dev/null)" = connected ] && [ "$(cat "$c/enabled" 2>/dev/null)" = enabled ] && DISPLAY_COUNT=$((DISPLAY_COUNT + 1))
done
MODES=""
if [ "$DS" = sway ]; then
    # this sway's own socket (a crashed sway can leave a stale one behind)
    MODES=$(SWAYSOCK=$(ls /run/user/*/sway-ipc.*."$(pgrep -xo sway)".sock 2>/dev/null | head -1) swaymsg -t get_outputs -r 2>/dev/null \
        | jq -r '.[] | .name + " " + (if .active then "\(.current_mode.width)x\(.current_mode.height)@\(.current_mode.refresh / 10 | round / 100)Hz" else "disabled" end)' 2>/dev/null)
elif [ "$DS" = X11 ]; then
    # mode lines of connected outputs only: a TV that is switched off can keep its last mode in X (kiosk-loop.sh
    # never turns the output off), which xrandr then lists, marked "*current", under a "disconnected" header
    MODES=$({ DISPLAY=:0.0 xrandr --current; DISPLAY=:0.1 xrandr --current; } 2>/dev/null \
        | awk '/^[^ ]+ (dis)?connected/ {o = ($2 == "connected") ? $1 : ""} o != "" && /^   [0-9]/ {for (i = 2; i <= NF; i++) if ($i ~ /\*/) {r = $i; gsub(/[*+]/, "", r); print o " " $1 "@" r "Hz"}}')
fi
DETAIL=" (${DS:-no display server}${MODES:+: ${MODES//$'\n'/, }})"
if [ "$DISPLAY_COUNT" -eq 2 ]; then
    echo -e "${GREEN}✓ 2 displays connected${NC}$DETAIL"
elif [ "$DISPLAY_COUNT" -eq 1 ]; then
    echo -e "${YELLOW}⚠ Only 1 display connected${NC}$DETAIL"
else
    echo -e "${RED}✗ No displays detected${NC}$DETAIL"
fi

# Resources
echo -n "[Resources] "
LOAD=$(uptime | awk -F'load average:' '{print $2}' | cut -d, -f1 | tr -d ' ')
MEM=$(free | grep Mem | awk '{printf "%.0f", ($3/$2)*100}')
DISK=$(df -h / | tail -1 | awk '{print $5}' | tr -d '%')
echo "Load: $LOAD | Mem: ${MEM}% | Disk: ${DISK}%"

EOF
)

# Filter out Ubuntu welcome banner - only show checklist lines
echo "$OUTPUT" | grep -E '^\[|^Load:'
echo "================================================================"
echo "For detailed view: ./check-status.sh --verbose"

