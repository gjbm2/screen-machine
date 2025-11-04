#!/bin/bash
# Fix the light sensor IP address
# Updates lightsense.py to use the new server IP

set -e

OLD_IP="185.254.136.244"
NEW_IP="95.141.21.170"
SCRIPT_PATH="/home/gjbm2/lightsense.py"
BACKUP_PATH="/home/gjbm2/lightsense.py.backup-$(date +%Y%m%d-%H%M%S)"

echo "=== Light Sensor IP Fix ==="
echo ""
echo "Old IP: $OLD_IP"
echo "New IP: $NEW_IP"
echo "Script: $SCRIPT_PATH"
echo ""

# Check if the script exists
if [ ! -f "$SCRIPT_PATH" ]; then
    echo "ERROR: $SCRIPT_PATH not found!"
    exit 1
fi

# Create backup
echo "[1] Creating backup..."
cp "$SCRIPT_PATH" "$BACKUP_PATH"
echo "    Backup saved: $BACKUP_PATH"

# Check current content
echo ""
echo "[2] Current WebSocket URL:"
grep "WS_URL" "$SCRIPT_PATH" || echo "    (No WS_URL found)"

# Update the IP
echo ""
echo "[3] Updating IP address..."
sed -i "s/${OLD_IP}/${NEW_IP}/g" "$SCRIPT_PATH"

# Verify the change
echo ""
echo "[4] New WebSocket URL:"
grep "WS_URL" "$SCRIPT_PATH" || echo "    (No WS_URL found)"

# Check if lightsense is running
echo ""
echo "[5] Checking if lightsense.py is running..."
if pgrep -f "lightsense.py" > /dev/null; then
    echo "    Light sensor process found - you should restart it"
    echo "    PIDs: $(pgrep -f 'lightsense.py' | tr '\n' ' ')"
    echo ""
    echo "    To restart:"
    echo "      pkill -f lightsense.py"
    echo "      cd /home/gjbm2 && source .venv/bin/activate && python lightsense.py &"
else
    echo "    No lightsense.py process running"
fi

echo ""
echo "=== Fix Complete ==="
echo ""
echo "Summary:"
echo "  ✓ Backup created: $BACKUP_PATH"
echo "  ✓ IP updated: $OLD_IP → $NEW_IP"
echo "  ✓ File: $SCRIPT_PATH"
echo ""

