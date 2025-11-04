#!/bin/bash
# Reboot the media server
# Usage: ./reboot-server.sh [--force]

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
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo "Reboot requested for $REMOTE_USER@$REMOTE_HOST (force=${1:---no})"

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Media Server Reboot${NC}"
echo -e "${BLUE}  Server: $REMOTE_USER@$REMOTE_HOST${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Check for --force flag
FORCE=false
if [[ "$1" == "--force" ]]; then
    FORCE=true
fi

if [ "$FORCE" = false ]; then
    echo -e "${YELLOW}⚠️  Warning: This will reboot the media server!${NC}"
    echo ""
    echo "This will:"
    echo "  1. Stop all display processes"
    echo "  2. Reboot the server"
    echo "  3. Displays will be offline for ~60 seconds"
    echo ""
    read -p "Are you sure? (type 'yes' to confirm): " -r
    echo
    if [[ ! $REPLY == "yes" ]]; then
        echo "Reboot aborted by user"
        echo -e "${GREEN}Aborted.${NC}"
        exit 0
    fi
fi

echo "Reboot confirmed, executing..."

echo -e "${YELLOW}Rebooting $REMOTE_HOST...${NC}"
echo ""

# Execute reboot and capture output
OUTPUT=$(ssh -p "$REMOTE_PORT" "$REMOTE_USER@$REMOTE_HOST" << EOF
echo "=== Pre-Reboot Status ==="
echo "Uptime: \$(uptime -p)"
echo "Kiosk service: \$(systemctl is-active kiosk.service)"
echo ""
echo "=== Initiating Reboot ==="
echo "$MEDIA_SERVER_PASSWORD" | sudo -S reboot
EOF
)

# Display output (automatically logged via exec redirect)
echo "$OUTPUT"

echo "========================================="
echo "Reboot command sent successfully"
echo "========================================="

echo ""
echo -e "${GREEN}Reboot command sent successfully!${NC}"
echo ""
echo -e "${YELLOW}Log file:${NC} $LOG_FILE"
echo ""
echo -e "${YELLOW}Next steps:${NC}"
echo "  1. Wait 60-90 seconds for server to reboot"
echo "  2. Check status: ./check-status.sh"
echo "  3. If needed, restart kiosk: ./restart-kiosk.sh"
echo ""
echo "Monitoring tip: Watch for server to come back online:"
echo "  watch -n 2 ping -c 1 $REMOTE_HOST"

