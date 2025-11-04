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
OUTPUT=$(ssh -p "$REMOTE_PORT" "$REMOTE_USER@$REMOTE_HOST" << EOF
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
if systemctl is-active --quiet kiosk.service; then
    echo -e "${GREEN}✓ Service is RUNNING${NC}"
    echo ""
    systemctl status kiosk.service --no-pager | head -20
else
    echo -e "${RED}✗ Service is NOT RUNNING${NC}"
    echo ""
    systemctl status kiosk.service --no-pager | head -20
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
if ps aux | grep -E "(Xorg|chrome)" | grep -v grep >/dev/null; then
    echo -e "${GREEN}✓ Display processes running${NC}"
    ps aux | grep -E "(Xorg|chrome)" | grep -v grep | head -10
else
    echo -e "${RED}✗ No display processes found${NC}"
fi
echo ""

echo "=== Displays Detected ==="
if DISPLAY=:0 xrandr --query 2>/dev/null | grep " connected" >/dev/null; then
    echo -e "\${GREEN}✓ Displays connected\${NC}"
    DISPLAY=:0 xrandr --query 2>/dev/null | grep " connected"
else
    echo -e "\${YELLOW}⚠ Could not query displays\${NC}"
fi
echo ""

echo "=== Chrome URLs and Status ==="
# Get Chrome process details
CHROME_PIDS=\$(pgrep -f "google-chrome-stable.*kiosk" || echo "")
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
    echo "=== Chrome Console Logs: Proof Pages Are Rendering ==="
    echo ""
    echo "--- NORTH Screen Console (last 25 lines) ---"
    if [ -f "/tmp/chrome-north/chrome_console.log" ]; then
        tail -25 /tmp/chrome-north/chrome_console.log 2>/dev/null | grep -v "^$" || echo "(log is empty)"
    else
        echo -e "\${YELLOW}⚠ No console log at /tmp/chrome-north/chrome_console.log\${NC}"
        echo "   (Chrome needs to be restarted after kiosk-loop.sh update)"
    fi
    
    echo ""
    echo "--- SOUTH Screen Console (last 25 lines) ---"
    if [ -f "/tmp/chrome-south/chrome_console.log" ]; then
        tail -25 /tmp/chrome-south/chrome_console.log 2>/dev/null | grep -v "^$" || echo "(log is empty)"
    else
        echo -e "\${YELLOW}⚠ No console log at /tmp/chrome-south/chrome_console.log\${NC}"
        echo "   (Chrome needs to be restarted after kiosk-loop.sh update)"
    fi
    
    echo ""
    echo "=== What URLs Were Loaded at Startup ==="
    echo "(from kiosk startup log)"
    grep -E "spawn (NORTH|SOUTH)" /var/log/kiosk.log 2>/dev/null | tail -3 || echo "(no spawn logs found)"
    
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
echo "Active connections from Chrome to Flask server (port 8000):"
CHROME_CONNS=\$(lsof -i -n -P 2>/dev/null | grep chrome | grep ":8000" | grep ESTABLISHED | wc -l)
if [ "\$CHROME_CONNS" -gt 0 ]; then
    echo -e "\${GREEN}✓ \$CHROME_CONNS active connections to port 8000\${NC}"
    echo "Connection details:"
    lsof -i -n -P 2>/dev/null | grep chrome | grep ":8000" | grep ESTABLISHED | head -10
else
    echo -e "\${RED}✗ No active connections to port 8000 - pages may not be loaded!\${NC}"
fi
echo ""
echo "WebSocket connections (port 8765):"
lsof -i -n -P 2>/dev/null | grep ":8765" | grep ESTABLISHED | head -5 || echo "No WebSocket connections"
echo ""

echo "=== Recent Errors (last 10) ==="
echo "$MEDIA_SERVER_PASSWORD" | sudo -S journalctl -u kiosk.service -p err --since "24 hours ago" --no-pager | tail -10 || echo "No errors in last 24 hours"
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

