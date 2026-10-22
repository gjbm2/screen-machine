#!/usr/bin/env bash
# Install/update wifi-prefer-ap on the kiosk box (keeps its Wi-Fi on the fast access point).
# Run from WSL:  bash media-server/local-scripts/install-wifi-prefer-ap.sh [--prefer-current]
#
# The preferred AP is recorded in /etc/default/wifi-prefer-ap on the box. On first install, or with
# --prefer-current, it is taken from the box's CURRENT association, so make sure the box is on the
# fast AP first (the script prints the frequency it recorded). Undo on the box:
#   sudo systemctl disable --now wifi-prefer-ap      (clears the pin; stock roaming again)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
REMOTE_DIR="$PROJECT_ROOT/media-server/remote-scripts"
PREFER_CURRENT=0
[[ "${1:-}" == "--prefer-current" ]] && PREFER_CURRENT=1

# SSH credentials (same source as install-deadman.sh)
set -a; source "$SCRIPT_DIR/.env"; set +a
HOST="${MEDIA_SERVER_HOST:-192.168.1.92}"
USER="${MEDIA_SERVER_USER:-gjbm2}"
PORT="${MEDIA_SERVER_PORT:-22}"
PASS="${MEDIA_SERVER_PASSWORD:?MEDIA_SERVER_PASSWORD missing from media-server/local-scripts/.env}"

SSH=(sshpass -p "$PASS" ssh -q -o StrictHostKeyChecking=no -o LogLevel=ERROR -p "$PORT" "$USER@$HOST")
SCP=(sshpass -p "$PASS" scp -q -o StrictHostKeyChecking=no -o LogLevel=ERROR -P "$PORT")

TMPSCRIPT="$(mktemp)"
trap 'rm -f "$TMPSCRIPT"' EXIT
cat > "$TMPSCRIPT" <<'REMOTE'
set -euo pipefail
D=/tmp/wifi-prefer-ap-install
W=(wpa_cli -p /run/wpa_supplicant -i wlp2s0)
install -m 755 "$D/wifi-prefer-ap.sh" /usr/local/sbin/wifi-prefer-ap
install -m 644 "$D/wifi-prefer-ap.service" /etc/systemd/system/wifi-prefer-ap.service
if [[ ! -s /etc/default/wifi-prefer-ap || "$(cat "$D/prefer-current")" == 1 ]]; then
  ST=$("${W[@]}" status)
  grep -q '^wpa_state=COMPLETED' <<<"$ST" || { echo "box is not connected to Wi-Fi; not recording a preferred AP" >&2; exit 2; }
  BSSID=$(sed -n 's/^bssid=//p' <<<"$ST"); FREQ=$(sed -n 's/^freq=//p' <<<"$ST")
  printf '# written by install-wifi-prefer-ap.sh %s (AP on %s MHz)\nPREFERRED=%s\n' "$(date -u +%FT%TZ)" "$FREQ" "$BSSID" > /etc/default/wifi-prefer-ap
  echo "preferred AP recorded: ..${BSSID: -5} on $FREQ MHz"
else
  echo "keeping existing preferred AP: ..$(sed -n 's/^PREFERRED=//p' /etc/default/wifi-prefer-ap | tail -c 6)"
fi
systemctl daemon-reload
systemctl enable wifi-prefer-ap.service
systemctl restart wifi-prefer-ap.service
rm -rf "$D"
sleep 12
echo "--- service ---"
systemctl is-enabled wifi-prefer-ap && systemctl is-active wifi-prefer-ap
journalctl -u wifi-prefer-ap -n 5 --no-pager -o cat
REMOTE

echo "Copying files to $USER@$HOST..."
"${SSH[@]}" "rm -rf /tmp/wifi-prefer-ap-install && mkdir -p /tmp/wifi-prefer-ap-install"
echo "$PREFER_CURRENT" > "$TMPSCRIPT.flag"
trap 'rm -f "$TMPSCRIPT" "$TMPSCRIPT.flag"' EXIT
"${SCP[@]}" "$REMOTE_DIR/wifi-prefer-ap.sh" "$REMOTE_DIR/wifi-prefer-ap.service" "$USER@$HOST:/tmp/wifi-prefer-ap-install/"
"${SCP[@]}" "$TMPSCRIPT.flag" "$USER@$HOST:/tmp/wifi-prefer-ap-install/prefer-current"
"${SCP[@]}" "$TMPSCRIPT" "$USER@$HOST:/tmp/wifi-prefer-ap-install/install-remote.sh"

echo "Installing (sudo on the box)..."
# password goes to sudo on stdin, never on a command line
printf '%s\n' "$PASS" | "${SSH[@]}" "sudo -S -p '' bash /tmp/wifi-prefer-ap-install/install-remote.sh"
