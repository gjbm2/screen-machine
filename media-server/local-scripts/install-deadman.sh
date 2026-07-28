#!/usr/bin/env bash
# Install/update the Deadman watchdog on the media-server box.
# Run from WSL:  bash media-server/local-scripts/install-deadman.sh
#
# Copies deadman.py + systemd units to the box, builds /etc/deadman/deadman.env
# (root:root 600) from screen-machine/.env values, enables the 5-minute timer,
# and runs one check immediately. Secrets are never echoed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
REMOTE_DIR="$PROJECT_ROOT/media-server/remote-scripts"

# SSH credentials (same source as routes/admin_api.py)
set -a; source "$SCRIPT_DIR/.env"; set +a
HOST="${MEDIA_SERVER_HOST:-192.168.1.92}"
USER="${MEDIA_SERVER_USER:-gjbm2}"
PORT="${MEDIA_SERVER_PORT:-22}"
PASS="${MEDIA_SERVER_PASSWORD:?MEDIA_SERVER_PASSWORD missing from media-server/local-scripts/.env}"

SSH=(sshpass -p "$PASS" ssh -q -o StrictHostKeyChecking=no -o LogLevel=ERROR -p "$PORT" "$USER@$HOST")
SCP=(sshpass -p "$PASS" scp -q -o StrictHostKeyChecking=no -o LogLevel=ERROR -P "$PORT")

# Build the watchdog env file locally (never committed, never printed)
TMPENV="$(mktemp)"
chmod 600 "$TMPENV"
trap 'rm -f "$TMPENV"' EXIT
{
  grep '^GOOGLE_CLIENT_ID=' "$PROJECT_ROOT/.env"
  grep '^GOOGLE_CLIENT_SECRET=' "$PROJECT_ROOT/.env"
  grep '^GOOGLE_REFRESH_TOKEN=' "$PROJECT_ROOT/.env"
  grep '^ALERT_EMAIL_TO=' "$PROJECT_ROOT/.env"
  echo "PROBE_URL=${PROBE_URL:-http://95.141.21.170:8000/api/health?probe=deadman}"
  echo "MISS_THRESHOLD=2"
  echo "DISK_THRESHOLD_PCT=90"
  echo "SERVICES=kiosk.service light-relay.service"
  echo "SELF_MAILBOX=1"
} > "$TMPENV"

# The privileged steps travel as a FILE and run via `sudo -S bash <file>` —
# feeding a script over ssh stdin conflicts with sudo -S reading the
# password from the same pipe
TMPSCRIPT="$(mktemp)"
trap 'rm -f "$TMPENV" "$TMPSCRIPT"' EXIT
cat > "$TMPSCRIPT" <<'REMOTE'
set -euo pipefail
mkdir -p /opt/deadman /etc/deadman /var/lib/deadman
mv /tmp/deadman-install/deadman.py /opt/deadman/deadman.py
chmod 755 /opt/deadman/deadman.py
mv /tmp/deadman-install/deadman.env /etc/deadman/deadman.env
chown root:root /etc/deadman/deadman.env
chmod 600 /etc/deadman/deadman.env
mv /tmp/deadman-install/deadman.service /etc/systemd/system/deadman.service
mv /tmp/deadman-install/deadman.timer /etc/systemd/system/deadman.timer
touch /var/log/deadman.log && chmod 644 /var/log/deadman.log
systemctl daemon-reload
systemctl enable --now deadman.timer
systemctl start deadman.service
rm -rf /tmp/deadman-install
echo "--- timer status ---"
systemctl is-enabled deadman.timer && systemctl is-active deadman.timer
echo "--- last run ---"
tail -n 5 /var/log/deadman.log
REMOTE

echo "Copying files to $USER@$HOST..."
"${SSH[@]}" "mkdir -p /tmp/deadman-install"
"${SCP[@]}" "$REMOTE_DIR/deadman.py" "$REMOTE_DIR/deadman.service" \
            "$REMOTE_DIR/deadman.timer" "$USER@$HOST:/tmp/deadman-install/"
"${SCP[@]}" "$TMPENV" "$USER@$HOST:/tmp/deadman-install/deadman.env"
"${SCP[@]}" "$TMPSCRIPT" "$USER@$HOST:/tmp/deadman-install/install-remote.sh"

echo "Installing (sudo on the box)..."
"${SSH[@]}" "echo '$PASS' | sudo -S bash /tmp/deadman-install/install-remote.sh"

echo "Deadman installed. Timer runs every 5 minutes (Persistent=true)."
