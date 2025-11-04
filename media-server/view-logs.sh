#!/bin/bash
# View maintenance logs
# Usage: ./view-logs.sh [lines]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$SCRIPT_DIR/maintenance.log"
LINES="${1:-50}"

if [ ! -f "$LOG_FILE" ]; then
    echo "No log file found at: $LOG_FILE"
    echo "Run some commands first (check-status.sh, restart-kiosk.sh, etc.)"
    exit 1
fi

echo "==================================="
echo "  Maintenance Log (last $LINES lines)"
echo "==================================="
echo ""

tail -n "$LINES" "$LOG_FILE"

echo ""
echo "==================================="
echo "Log file: $LOG_FILE"
echo "Total lines: $(wc -l < "$LOG_FILE")"
echo ""
echo "Usage:"
echo "  ./view-logs.sh           # Last 50 lines (default)"
echo "  ./view-logs.sh 100       # Last 100 lines"
echo "  tail -f $LOG_FILE  # Follow in real-time"

