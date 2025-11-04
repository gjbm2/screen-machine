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

# Kiosk Service
echo -n "[Kiosk Service] "
if systemctl is-active --quiet kiosk.service; then
    echo -e "${GREEN}✓ Running${NC}"
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

# Chrome Instances with Page Load Detection
echo -n "[Chrome North] "
NORTH_PID=$(pgrep -f "google-chrome.*north-screen" | head -1)
if [ -n "$NORTH_PID" ]; then
    # Check console logs for actual page rendering proof
    if [ -f "/tmp/chrome-north/chrome_console.log" ]; then
        # Look for React TypeScript files or console output (proof the app is running)
        if tail -50 /tmp/chrome-north/chrome_console.log 2>/dev/null | grep -qE "INFO:CONSOLE|\.tsx|\.ts\)"; then
            CONNS=$(lsof -i -n -p $NORTH_PID 2>/dev/null | grep ESTABLISHED | wc -l)
            echo -e "${GREEN}✓ Rendering page ($CONNS conns)${NC}"
        else
            # Fallback: check if initial load happened
            if grep -q "display/north-screen" /tmp/chrome-north/chrome_console.log 2>/dev/null; then
                echo -e "${YELLOW}⚠ Page loaded but no recent activity${NC}"
            else
                echo -e "${YELLOW}⚠ No page load detected${NC}"
            fi
        fi
    else
        # Fallback to connection count if no logs
        CONNS=$(lsof -i -n -p $NORTH_PID 2>/dev/null | grep ESTABLISHED | wc -l)
        if [ "$CONNS" -gt 2 ]; then
            echo -e "${GREEN}✓ Loaded ($CONNS conns, no log file)${NC}"
        else
            echo -e "${YELLOW}⚠ Running (no logs, $CONNS conns)${NC}"
        fi
    fi
else
    echo -e "${RED}✗ NOT RUNNING${NC}"
fi

echo -n "[Chrome South] "
SOUTH_PID=$(pgrep -f "google-chrome.*south-screen" | head -1)
if [ -n "$SOUTH_PID" ]; then
    # Check console logs for actual page rendering proof
    if [ -f "/tmp/chrome-south/chrome_console.log" ]; then
        # Look for React TypeScript files or console output (proof the app is running)
        if tail -50 /tmp/chrome-south/chrome_console.log 2>/dev/null | grep -qE "INFO:CONSOLE|\.tsx|\.ts\)"; then
            CONNS=$(lsof -i -n -p $SOUTH_PID 2>/dev/null | grep ESTABLISHED | wc -l)
            echo -e "${GREEN}✓ Rendering page ($CONNS conns)${NC}"
        else
            # Fallback: check if initial load happened
            if grep -q "display/south-screen" /tmp/chrome-south/chrome_console.log 2>/dev/null; then
                echo -e "${YELLOW}⚠ Page loaded but no recent activity${NC}"
            else
                echo -e "${YELLOW}⚠ No page load detected${NC}"
            fi
        fi
    else
        # Fallback to connection count if no logs
        CONNS=$(lsof -i -n -p $SOUTH_PID 2>/dev/null | grep ESTABLISHED | wc -l)
        if [ "$CONNS" -gt 2 ]; then
            echo -e "${GREEN}✓ Loaded ($CONNS conns, no log file)${NC}"
        else
            echo -e "${YELLOW}⚠ Running (no logs, $CONNS conns)${NC}"
        fi
    fi
else
    echo -e "${RED}✗ NOT RUNNING${NC}"
fi

# GPU Process
echo -n "[GPU Process] "
if pgrep -f "chrome.*type=gpu" >/dev/null; then
    echo -e "${GREEN}✓ Running${NC}"
else
    echo -e "${RED}✗ Missing (causes white screens)${NC}"
fi

# Displays
echo -n "[Displays] "
DISPLAY_COUNT=$(DISPLAY=:0 xrandr --query 2>/dev/null | grep " connected" | wc -l)
if [ "$DISPLAY_COUNT" -eq 2 ]; then
    echo -e "${GREEN}✓ 2 displays connected${NC}"
elif [ "$DISPLAY_COUNT" -eq 1 ]; then
    echo -e "${YELLOW}⚠ Only 1 display connected${NC}"
else
    echo -e "${RED}✗ No displays detected${NC}"
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

