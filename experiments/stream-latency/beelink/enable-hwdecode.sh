#!/usr/bin/env bash
# Enable VA-API hardware video decode for Chrome on the kiosk mini-PC (Intel N95 / ADL-N).
# RUN ON THE MINI-PC WITH SUDO, ONLY WITH THE OWNER'S OK:   sudo bash enable-hwdecode.sh [--with-gpu-top]
#
# What it changes (all reversible):
#   1. apt installs intel-media-va-driver-non-free + vainfo (+ libva2 libva-drm2 libva-x11-2 libva-wayland2
#      libigdgmm12 as dependencies). Dry-run on 2026-10-11: 7 new packages, 0 upgrades, 0 removals.
#      --with-gpu-top also installs intel-gpu-tools (intel_gpu_top shows the "Video" engine busy %).
#   2. adds gjbm2 to the 'render' and 'video' groups so Chrome's GPU process can open /dev/dri/renderD128
#      (today it only has the card1 fds that Xorg hands out via DRI3).
# It does NOT touch kiosk.service / kiosk-loop.sh and does NOT restart anything: Ubuntu 24.04's needrestart
# apt hook would otherwise restart any service mapping a replaced library (kiosk.service included = both
# TVs blank), so it is suspended for this apt run (NEEDRESTART_SUSPEND=1, NEEDRESTART_MODE=l = list only).
# The running kiosk Chromes keep their old groups; the NEXT kiosk.service restart or reboot starts the
# production Chromes with render+video membership and the iHD driver present (they still need the
# AcceleratedVideoDecodeLinuxGL flag to use it; check with cdp_probe.py 9222 media during <video>/MSE playback).
#
# Undo: sudo apt-get remove intel-media-va-driver-non-free vainfo && sudo gpasswd -d gjbm2 render && sudo gpasswd -d gjbm2 video
set -euo pipefail
KIOSK_USER=${KIOSK_USER:-gjbm2}
PKGS=(intel-media-va-driver-non-free vainfo)
[[ "${1:-}" == "--with-gpu-top" ]] && PKGS+=(intel-gpu-tools)

if [[ $EUID -ne 0 ]]; then echo "run with sudo" >&2; exit 1; fi
# inherited by apt-get and its DPkg::Post-Invoke hooks: needrestart only lists, never restarts services
export NEEDRESTART_SUSPEND=1 NEEDRESTART_MODE=l DEBIAN_FRONTEND=noninteractive

echo "== refresh package lists (no installs) =="
# the box's lists can be months old; superseded .debs 404 on the mirror otherwise
apt-get update -qq

echo "== simulate first =="
SIM=$(apt-get -s install --no-install-recommends "${PKGS[@]}")
grep -E "^(Inst|Remv|Purg)|upgraded" <<< "$SIM" || true
# here-strings, not `echo | grep -q`: with pipefail, grep -q exiting on the first match can SIGPIPE a
# long echo and turn the match into a failure, i.e. skip the refusal
if grep -qE "^(Remv|Purg) " <<< "$SIM"; then
  echo "refusing: install would remove packages" >&2; exit 2
fi
# "Inst pkg [oldver] (newver ...)" means an already-installed package would be upgraded
if grep -qE "^Inst \S+ \[" <<< "$SIM"; then
  echo "refusing: install would upgrade existing packages (kiosk policy: no incidental upgrades)" >&2; exit 3
fi

if command -v needrestart >/dev/null; then
  echo "== already stale before we start (needrestart, list only; nothing will be restarted) =="
  needrestart -b -r l 2>/dev/null | grep -E "^NEEDRESTART-SVC" || echo "(no services)"
fi

echo "== install =="
apt-get install -y --no-install-recommends "${PKGS[@]}"

echo "== groups =="
usermod -aG render,video "$KIOSK_USER"
id "$KIOSK_USER"

echo "== verify (as $KIOSK_USER with the render group) =="
sudo -u "$KIOSK_USER" sg render -c "LIBVA_DRIVER_NAME=iHD vainfo --display drm --device /dev/dri/renderD128" 2>&1 \
  | grep -E "vainfo|Driver version|VAProfile(H264|HEVC|VP9|AV1)" | head -40
echo "done. Nothing restarted."
