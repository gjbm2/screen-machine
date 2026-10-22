#!/usr/bin/env bash
# Make the kiosk box wired-first (powerline Ethernet on enp1s0) with Wi-Fi as fallback.
# Run from the repo root in WSL:  bash media-server/local-scripts/install-ethernet-primary.sh
#
# Installs (from media-server/remote-scripts/network/):
#   /etc/netplan/60-ethernet.yaml                       enp1s0 DHCP, default-route metric 100 (Wi-Fi stays 600)
#   /etc/systemd/network/10-netplan-wlp2s0.network.d/50-wifi-source-route.conf
#                                                       replies from the Wi-Fi address always use Wi-Fi
#   /etc/sysctl.d/60-multihome-arp.conf                 arp_ignore=1 / arp_announce=2 (two NICs, one subnet)
#   /usr/local/sbin/eth-primary-rollback                undo script (also the dead-man below)
# Safety: a 5-minute dead-man timer runs the rollback unless this script verifies that the box answers on both
# its Wi-Fi and Ethernet addresses and then cancels it. The apply step runs detached on the box (systemd-run),
# so a brief network reconfigure cannot cut it off half-way. Never touches 50-cloud-init.yaml (Wi-Fi + password).
# Undo later on the box: sudo eth-primary-rollback
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
NET_DIR="$PROJECT_ROOT/media-server/remote-scripts/network"

set -a; source "$SCRIPT_DIR/.env"; set +a
HOST="${MEDIA_SERVER_HOST:-192.168.1.92}"     # the Wi-Fi address (stays reachable via the source route)
USER="${MEDIA_SERVER_USER:-gjbm2}"
PORT="${MEDIA_SERVER_PORT:-22}"
PASS="${MEDIA_SERVER_PASSWORD:?MEDIA_SERVER_PASSWORD missing from media-server/local-scripts/.env}"
SSH_OPTS=(-q -o StrictHostKeyChecking=no -o LogLevel=ERROR -o ConnectTimeout=8 -p "$PORT")
SSH=(sshpass -p "$PASS" ssh "${SSH_OPTS[@]}" "$USER@$HOST")
SCP=(sshpass -p "$PASS" scp -q -o StrictHostKeyChecking=no -o LogLevel=ERROR -P "$PORT")
sudo_box() { printf '%s\n' "$PASS" | "${SSH[@]}" "sudo -S -p '' $*"; }

STAGE=/tmp/eth-primary-install
TMPSCRIPT="$(mktemp)"; trap 'rm -f "$TMPSCRIPT"' EXIT
cat > "$TMPSCRIPT" <<'REMOTE'
set -euo pipefail
S=/tmp/eth-primary-install
[ "$(cat /sys/class/net/enp1s0/carrier 2>/dev/null || true)" = 1 ] || ip link set enp1s0 up
install -m 755 "$S/eth-primary-rollback.sh" /usr/local/sbin/eth-primary-rollback
systemctl stop eth-primary-deadman.timer 2>/dev/null || true
systemd-run --quiet --unit=eth-primary-deadman --on-active=300 /usr/local/sbin/eth-primary-rollback
echo "dead-man armed: rollback in 5 min unless cancelled"
cat > "$S/apply.sh" <<'APPLY'
exec >>/var/log/eth-primary-apply.log 2>&1
echo "== $(date -u +%FT%TZ) apply"
S=/tmp/eth-primary-install
# remove the 2026-10-13 speed-test scaffolding if present
rm -f /run/systemd/network/05-enp1s0-test.network; ip rule del priority 105 2>/dev/null; ip route flush table 105 2>/dev/null
install -m 600 "$S/60-ethernet.yaml" /etc/netplan/60-ethernet.yaml
install -d -m 755 /etc/systemd/network/10-netplan-wlp2s0.network.d
install -m 644 "$S/50-wifi-source-route.conf" /etc/systemd/network/10-netplan-wlp2s0.network.d/50-wifi-source-route.conf
install -m 644 "$S/60-multihome-arp.conf" /etc/sysctl.d/60-multihome-arp.conf
sysctl -q -p /etc/sysctl.d/60-multihome-arp.conf
netplan generate
networkctl reload
networkctl reconfigure enp1s0
networkctl reconfigure wlp2s0
sleep 8
ip -br -4 addr; ip route; ip rule
echo "== applied"
APPLY
systemd-run --quiet --unit=eth-primary-apply --collect /bin/bash "$S/apply.sh"
echo "apply launched (log: /var/log/eth-primary-apply.log)"
REMOTE

echo "Copying files to $USER@$HOST..."
"${SSH[@]}" "rm -rf $STAGE && mkdir -p $STAGE"
"${SCP[@]}" "$NET_DIR/60-ethernet.yaml" "$NET_DIR/50-wifi-source-route.conf" "$NET_DIR/60-multihome-arp.conf" \
            "$NET_DIR/eth-primary-rollback.sh" "$TMPSCRIPT" "$USER@$HOST:$STAGE/"
"${SSH[@]}" "mv $STAGE/$(basename "$TMPSCRIPT") $STAGE/install-remote.sh"
echo "Installing (sudo on the box)..."
sudo_box bash "$STAGE/install-remote.sh"

echo "Waiting for the network to settle..."
sleep 25
ok=1
"${SSH[@]}" true || { echo "FAIL: no SSH on the Wi-Fi address $HOST"; ok=0; }
ETH_IP=$("${SSH[@]}" "ip -4 -o addr show enp1s0 | awk '{print \$4}' | cut -d/ -f1" || true)
DEF=$("${SSH[@]}" "ip route show default | head -1" || true)
echo "Ethernet address: ${ETH_IP:-none}"; echo "default route: ${DEF:-none}"
[[ -n "$ETH_IP" ]] || { echo "FAIL: enp1s0 has no address"; ok=0; }
[[ "$DEF" == *"dev enp1s0"* ]] || { echo "FAIL: default route is not via enp1s0"; ok=0; }
if [[ -n "$ETH_IP" ]]; then
  sshpass -p "$PASS" ssh "${SSH_OPTS[@]}" "$USER@$ETH_IP" true || { echo "FAIL: no SSH on the Ethernet address $ETH_IP"; ok=0; }
fi
"${SSH[@]}" "ping -c 3 -W 2 95.141.21.170 >/dev/null" || echo "WARN: public address (kiosk URL host) not answering ping (may be normal)"

if (( ok )); then
  sudo_box systemctl stop eth-primary-deadman.timer
  echo "OK: wired-first active; dead-man cancelled. Undo on the box: sudo eth-primary-rollback"
else
  echo "Checks failed: NOT cancelling the dead-man; the box rolls back to Wi-Fi only within 5 minutes."
  echo "Apply log: ssh $USER@$HOST cat /var/log/eth-primary-apply.log"
  exit 1
fi
