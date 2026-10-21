#!/usr/bin/env bash
# Install the kiosk output watchdog (media-server/remote-scripts/wayland/output_watchdog.py) on the kiosk mini-PC as the
# systemd --user unit kiosk-output-watchdog.service, then (re)start it. No sudo; touches nothing else (not sway,
# kiosk.service or the show sender).  Idempotent.
#   media-server/local-scripts/deploy-output-watchdog.sh [user@host]     default gjbm2@192.168.1.92
# Watch it: grep watchdog /var/log/kiosk.log  (or journalctl --user -u kiosk-output-watchdog on the kiosk)
set -euo pipefail
HOST="${1:-gjbm2@192.168.1.92}"
SRC="$(cd "$(dirname "$0")/../remote-scripts/wayland" && pwd)"
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=8 "$HOST")
"${SSH[@]}" 'mkdir -p ~/kiosk-watchdog ~/.config/systemd/user'
scp -q -o BatchMode=yes "$SRC/output_watchdog.py" "$HOST:kiosk-watchdog/"
scp -q -o BatchMode=yes "$SRC/kiosk-output-watchdog.service" "$HOST:.config/systemd/user/"
"${SSH[@]}" bash -s <<'EOF'
set -e
python3 ~/kiosk-watchdog/output_watchdog.py --once
systemctl --user daemon-reload
systemctl --user enable kiosk-output-watchdog.service >/dev/null
systemctl --user restart kiosk-output-watchdog.service
sleep 2
systemctl --user --no-pager --lines=5 status kiosk-output-watchdog.service || true
EOF
