#!/bin/bash
# Restart the kiosk service on media server
# Usage: ./restart-kiosk.sh

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
BLUE='\033[0;34m'
NC='\033[0m'

echo "========================================="
echo "Starting kiosk restart for $REMOTE_USER@$REMOTE_HOST"
echo "========================================="

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Restart Kiosk Service${NC}"
echo -e "${BLUE}  Server: $REMOTE_USER@$REMOTE_HOST${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Execute restart on remote server and capture output
OUTPUT=$(ssh -p "$REMOTE_PORT" "$REMOTE_USER@$REMOTE_HOST" << EOF
# Colors for remote
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# restart whichever kiosk is running: kiosk.service (sway, default since 2026-10-13) or the X11 fallback
# kiosk-x11.service, so a restart never switches modes (starting kiosk.service would stop kiosk-x11: Conflicts=)
KIOSK_UNIT=kiosk.service
systemctl is-active --quiet kiosk-x11.service && KIOSK_UNIT=kiosk-x11.service

echo "=== Current Status ==="
systemctl status \$KIOSK_UNIT --no-pager | head -5
echo ""

echo "=== Stopping Kiosk Service (\$KIOSK_UNIT) ==="
echo "$MEDIA_SERVER_PASSWORD" | sudo -S systemctl stop \$KIOSK_UNIT
sleep 2
echo -e "\${GREEN}✓ Service stopped\${NC}"
echo ""

echo "=== Cleaning Up Processes ==="
# Kill any remaining Chrome/X processes ("chrome.*--kiosk": under sway the browser's command line starts
# /opt/google/chrome/chrome, under X11 google-chrome-stable)
pkill -f "chrome.*--kiosk" 2>/dev/null || true
pkill -f "chromium.*kiosk" 2>/dev/null || true
sleep 1
echo -e "\${GREEN}✓ Cleanup complete\${NC}"
echo ""

echo "=== Starting Kiosk Service (\$KIOSK_UNIT) ==="
echo "$MEDIA_SERVER_PASSWORD" | sudo -S systemctl start \$KIOSK_UNIT
sleep 3
echo -e "\${GREEN}✓ Service started\${NC}"
echo ""

echo "=== New Status ==="
if systemctl is-active --quiet \$KIOSK_UNIT; then
    echo -e "${GREEN}✓ Kiosk service is RUNNING (\$KIOSK_UNIT)${NC}"
    systemctl status \$KIOSK_UNIT --no-pager | head -15
else
    echo -e "${RED}✗ Kiosk service FAILED to start (\$KIOSK_UNIT)${NC}"
    systemctl status \$KIOSK_UNIT --no-pager | head -15
    echo ""
    echo "Check logs with: sudo journalctl -u \$KIOSK_UNIT -xe"
    exit 1
fi
echo ""

echo "=== Display Check (in 5 seconds) ==="
sleep 5
# display server: sway (kiosk.service, default since 2026-10-13) or Xorg (kiosk-x11.service fallback)
if ps aux | grep -E "(sway|Xorg|chrome)" | grep -v grep >/dev/null; then
    echo -e "${GREEN}✓ Display processes detected${NC}"
    ps aux | grep -E "(sway --config|Xorg.*:0|chrome.*--kiosk)" | grep -v grep | head -5
else
    echo -e "${YELLOW}⚠ Display processes not yet running (may take a moment)${NC}"
fi
echo ""

echo "=== Connected Displays ==="
# HDMI connectors from sysfs (the same under sway and X11, needs no display socket), then each output's mode from
# the display server: swaymsg under sway, xrandr under the X11 fallback (one X screen per TV, :0.0 north, :0.1 south)
for C in /sys/class/drm/card*-HDMI-A-*; do
    echo "\${C##*/}: \$(cat "\$C/status" 2>/dev/null), \$(cat "\$C/enabled" 2>/dev/null)"
done
SWAY_PID=\$(pgrep -xo sway)
if [ -n "\$SWAY_PID" ]; then
    # this sway's own socket (a crashed sway can leave a stale one behind)
    OUTPUTS=\$(SWAYSOCK=\$(ls /run/user/*/sway-ipc.*."\$SWAY_PID".sock 2>/dev/null | head -1) swaymsg -t get_outputs -r 2>/dev/null \
        | jq -r '.[] | "sway: " + .name + " " + (if .active then "\(.current_mode.width)x\(.current_mode.height)@\(.current_mode.refresh / 10 | round / 100)Hz" else "disabled" end)' 2>/dev/null)
    echo "\${OUTPUTS:-sway: could not query outputs yet}"
elif pgrep -x Xorg >/dev/null; then
    { DISPLAY=:0.0 xrandr --query; DISPLAY=:0.1 xrandr --query; } 2>/dev/null | grep " connected" | sed 's/^/X11: /'
else
    echo -e "\${YELLOW}⚠ No display server (sway or Xorg) running yet\${NC}"
fi
EOF
)

# Display output (automatically logged via exec redirect)
echo "$OUTPUT"

echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}Kiosk restart complete!${NC}"
echo -e "${BLUE}========================================${NC}"

echo "========================================="
echo "Kiosk restart completed successfully"
echo "========================================="
echo ""
echo -e "${YELLOW}Log file:${NC} $LOG_FILE"
echo ""
echo -e "${YELLOW}Tip:${NC} Check logs in real-time:"
echo "  ssh $REMOTE_USER@$REMOTE_HOST 'sudo journalctl -u kiosk.service -f'"

