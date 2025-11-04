#!/bin/bash
# Restart the light sensor script
# Kills the old process and starts a new one

set -e

SCRIPT_PATH="/home/gjbm2/lightsense.py"
VENV_PATH="/home/gjbm2/.venv"
LOG_FILE="/home/gjbm2/lightsense.log"

echo "=== Light Sensor Restart ==="
echo ""

# Kill existing process
echo "[1] Stopping existing lightsense.py processes..."
if pgrep -f "lightsense.py" > /dev/null; then
    PIDS=$(pgrep -f "lightsense.py")
    echo "    Found PIDs: $PIDS"
    pkill -f "lightsense.py"
    sleep 2
    
    # Verify they're dead
    if pgrep -f "lightsense.py" > /dev/null; then
        echo "    WARNING: Process still running, forcing kill..."
        pkill -9 -f "lightsense.py"
        sleep 1
    fi
    echo "    ✓ Processes stopped"
else
    echo "    No existing process found"
fi

# Check if script exists
echo ""
echo "[2] Checking script..."
if [ ! -f "$SCRIPT_PATH" ]; then
    echo "    ERROR: $SCRIPT_PATH not found!"
    exit 1
fi
echo "    ✓ Script found: $SCRIPT_PATH"

# Check virtual environment
echo ""
echo "[3] Checking virtual environment..."
if [ ! -d "$VENV_PATH" ]; then
    echo "    ERROR: Virtual environment not found: $VENV_PATH"
    exit 1
fi
echo "    ✓ Virtual environment found"

# Start the service
echo ""
echo "[4] Starting lightsense.py..."
cd /home/gjbm2
source "$VENV_PATH/bin/activate"
nohup python "$SCRIPT_PATH" > "$LOG_FILE" 2>&1 &
NEW_PID=$!
echo "    ✓ Started with PID: $NEW_PID"

# Verify it's running
echo ""
echo "[5] Verifying process..."
sleep 2
if pgrep -f "lightsense.py" > /dev/null; then
    echo "    ✓ Process is running"
    ps aux | grep "[l]ightsense.py"
else
    echo "    ERROR: Process failed to start!"
    echo "    Check log file: $LOG_FILE"
    exit 1
fi

echo ""
echo "=== Restart Complete ==="
echo ""
echo "Log file: $LOG_FILE"
echo "To view logs: tail -f $LOG_FILE"
echo ""

