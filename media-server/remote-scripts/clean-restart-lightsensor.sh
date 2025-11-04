#!/bin/bash
# Clean restart of light sensor with proper lock cleanup

set -e

echo "=== Clean Light Sensor Restart ==="
echo ""

# Kill ALL lightsense processes
echo "[1] Killing all lightsense.py processes..."
pkill -9 -f "lightsense.py" 2>/dev/null || echo "    No processes to kill"
sleep 2

# Remove Yoctopuce lock file
echo ""
echo "[2] Removing Yoctopuce lock file..."
rm -f /tmp/.yoctolock
echo "    ✓ Lock file removed"

# Wait for USB to stabilize
echo ""
echo "[3] Waiting for USB to stabilize..."
sleep 3

# Start the service
echo ""
echo "[4] Starting lightsense.py..."
cd /home/gjbm2
source .venv/bin/activate
nohup python lightsense.py > lightsense.log 2>&1 &
NEW_PID=$!
echo "    ✓ Started with PID: $NEW_PID"

# Wait and verify
echo ""
echo "[5] Waiting for startup..."
sleep 5

echo ""
echo "[6] Checking process status..."
if ps -p $NEW_PID > /dev/null 2>&1; then
    echo "    ✓ Process $NEW_PID is running"
else
    echo "    ✗ Process $NEW_PID died - checking logs:"
    tail -20 /home/gjbm2/lightsense.log
    exit 1
fi

echo ""
echo "[7] Recent log output:"
tail -10 /home/gjbm2/lightsense.log

echo ""
echo "=== Restart Complete ==="

