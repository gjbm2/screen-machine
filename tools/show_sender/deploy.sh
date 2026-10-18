#!/usr/bin/env bash
# Deploy the show sender to the kiosk mini-PC (owner's OK and a quiet window first: docs/AV_PROGRAMME §11 rule 4).
#   tools/show_sender/deploy.sh [user@host]          default gjbm2@192.168.1.92
# Copies show_sender.py + show_service/vban.py to ~/show-sender/, makes its venv (numpy) if missing, then:
#   with passwordless sudo: installs alsa-utils if missing and the system unit show-sender.service;
#   without (the kiosk, 18 Oct): unpacks arecord from Ubuntu's alsa-utils .deb into ~/show-sender/bin and installs
#   the systemd --user unit show-sender.user.service as show-sender.service (gjbm2 lingers).
# Then (re)starts it.  Idempotent.
set -euo pipefail
HOST="${1:-gjbm2@192.168.1.92}"
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=8 "$HOST")
"${SSH[@]}" 'mkdir -p ~/show-sender'
scp -q -o BatchMode=yes "$HERE/show_sender.py" "$REPO/show_service/vban.py" "$HERE/show-sender.service" \
    "$HERE/show-sender.user.service" "$HOST:show-sender/"
"${SSH[@]}" bash -s <<'REMOTE'
set -euo pipefail
cd ~/show-sender
[ -x venv/bin/python ] || python3 -m venv venv
venv/bin/python -c 'import numpy' 2>/dev/null || venv/bin/pip install -q 'numpy>=1.26'
if sudo -n true 2>/dev/null; then
  command -v arecord >/dev/null || sudo apt-get install -y -q alsa-utils
  sudo install -m 0644 show-sender.service /etc/systemd/system/show-sender.service
  sudo systemctl daemon-reload
  sudo systemctl enable show-sender.service >/dev/null
  sudo systemctl restart show-sender.service
  sleep 2
  systemctl --no-pager --lines=8 status show-sender.service || true
else
  export XDG_RUNTIME_DIR="/run/user/$(id -u)"
  mkdir -p bin
  if ! command -v arecord >/dev/null && [ ! -x bin/arecord ]; then
    rm -rf deb && mkdir deb && (cd deb && apt-get download -q alsa-utils >/dev/null && dpkg -x alsa-utils_*.deb x)
    install -m 0755 deb/x/usr/bin/arecord bin/arecord
    rm -rf deb
  fi
  PATH="$HOME/show-sender/bin:$PATH" arecord --version | head -1
  mkdir -p ~/.config/systemd/user
  install -m 0644 show-sender.user.service ~/.config/systemd/user/show-sender.service
  systemctl --user daemon-reload
  systemctl --user enable show-sender.service >/dev/null
  systemctl --user restart show-sender.service
  sleep 2
  systemctl --user --no-pager --lines=8 status show-sender.service || true
fi
REMOTE
