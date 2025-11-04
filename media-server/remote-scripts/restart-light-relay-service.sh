#!/bin/bash
# Restart the light-relay systemd service

echo "=== Restarting light-relay.service ==="
echo ""

# Kill any manual processes
echo "[1] Killing manual lightsense.py processes..."
pkill -9 -f lightsense.py 2>/dev/null || echo "    No manual processes"
sleep 2

# Restart the service
echo ""
echo "[2] Restarting systemd service..."
echo "${MEDIA_SERVER_PASSWORD}" | sudo -S systemctl restart light-relay.service
sleep 3

# Check status
echo ""
echo "[3] Service status:"
systemctl --no-pager status light-relay.service

echo ""
echo "[4] Recent service logs:"
journalctl -u light-relay.service -n 15 --no-pager

echo ""
echo "=== Restart Complete ==="

