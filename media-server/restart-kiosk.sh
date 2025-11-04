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

echo "=== Current Status ==="
systemctl status kiosk.service --no-pager | head -5
echo ""

echo "=== Stopping Kiosk Service ==="
echo "$MEDIA_SERVER_PASSWORD" | sudo -S systemctl stop kiosk.service
sleep 2
echo -e "\${GREEN}✓ Service stopped\${NC}"
echo ""

echo "=== Cleaning Up Processes ==="
# Kill any remaining Chrome/X processes
pkill -f "google-chrome-stable.*kiosk" 2>/dev/null || true
pkill -f "chromium.*kiosk" 2>/dev/null || true
sleep 1
echo -e "\${GREEN}✓ Cleanup complete\${NC}"
echo ""

echo "=== Starting Kiosk Service ==="
echo "$MEDIA_SERVER_PASSWORD" | sudo -S systemctl start kiosk.service
sleep 3
echo -e "\${GREEN}✓ Service started\${NC}"
echo ""

echo "=== New Status ==="
if systemctl is-active --quiet kiosk.service; then
    echo -e "${GREEN}✓ Kiosk service is RUNNING${NC}"
    systemctl status kiosk.service --no-pager | head -15
else
    echo -e "${RED}✗ Kiosk service FAILED to start${NC}"
    systemctl status kiosk.service --no-pager | head -15
    echo ""
    echo "Check logs with: sudo journalctl -u kiosk.service -xe"
    exit 1
fi
echo ""

echo "=== Display Check (in 5 seconds) ==="
sleep 5
if ps aux | grep -E "(Xorg|chrome)" | grep -v grep >/dev/null; then
    echo -e "${GREEN}✓ Display processes detected${NC}"
    ps aux | grep -E "(Xorg.*:0|chrome.*kiosk)" | grep -v grep | head -5
else
    echo -e "${YELLOW}⚠ Display processes not yet running (may take a moment)${NC}"
fi
echo ""

echo "=== Connected Displays ==="
if DISPLAY=:0 xrandr --query 2>/dev/null | grep " connected" >/dev/null; then
    DISPLAY=:0 xrandr --query 2>/dev/null | grep " connected"
else
    echo -e "\${YELLOW}⚠ Could not query displays yet\${NC}"
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

