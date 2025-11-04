#!/bin/bash
# Fix the TypeError bug in lightsense.py

set -e

SCRIPT_PATH="/home/gjbm2/lightsense.py"
BACKUP_PATH="/home/gjbm2/lightsense.py.backup-bug-fix-$(date +%Y%m%d-%H%M%S)"

echo "=== Light Sensor Bug Fix ==="
echo ""

# Create backup
echo "[1] Creating backup..."
cp "$SCRIPT_PATH" "$BACKUP_PATH"
echo "    Backup saved: $BACKUP_PATH"

# Fix the bug: change errmsg.value() to errmsg.value
echo ""
echo "[2] Fixing TypeError bug..."
sed -i 's/errmsg\.value()/errmsg.value/g' "$SCRIPT_PATH"

# Show the fixed line
echo ""
echo "[3] Fixed line:"
grep -n "errmsg.value" "$SCRIPT_PATH" || echo "    (Pattern not found)"

echo ""
echo "=== Bug Fix Complete ==="
echo ""
echo "Summary:"
echo "  ✓ Backup created: $BACKUP_PATH"
echo "  ✓ Fixed: errmsg.value() → errmsg.value"
echo "  ✓ File: $SCRIPT_PATH"
echo ""

