#!/usr/bin/env bash
# Deploy the show sender to the kiosk mini-PC (owner's OK and a quiet window first: docs/AV_PROGRAMME §11 rule 4).
#   tools/show_sender/deploy.sh [user@host]          default gjbm2@192.168.1.92
# Copies show_sender.py + show_service/vban.py to ~/show-sender/, makes its venv (numpy) if missing, installs
# alsa-utils (arecord) if missing, installs and (re)starts show-sender.service.  Idempotent.
set -euo pipefail
HOST="${1:-gjbm2@192.168.1.92}"
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=8 "$HOST")
"${SSH[@]}" 'mkdir -p ~/show-sender'
scp -q -o BatchMode=yes "$HERE/show_sender.py" "$REPO/show_service/vban.py" "$HERE/show-sender.service" "$HOST:show-sender/"
"${SSH[@]}" bash -s <<'REMOTE'
set -euo pipefail
cd ~/show-sender
[ -x venv/bin/python ] || python3 -m venv venv
venv/bin/python -c 'import numpy' 2>/dev/null || venv/bin/pip install -q 'numpy>=1.26'
command -v arecord >/dev/null || sudo apt-get install -y -q alsa-utils
sudo install -m 0644 show-sender.service /etc/systemd/system/show-sender.service
sudo systemctl daemon-reload
sudo systemctl enable show-sender.service >/dev/null
sudo systemctl restart show-sender.service
sleep 2
systemctl --no-pager --lines=8 status show-sender.service || true
REMOTE
