#!/usr/bin/env bash
# Launch / stop a SEPARATE test Chrome on the kiosk mini-PC, on top of one TV, without touching
# kiosk.service or the north/south kiosk Chromes (they use /tmp/chrome-{north,south}, CDP 9222/9223).
#
#   lab-chrome.sh start <url> [north|south] [profile]     profile: vaapi (default) | vaapi-zerocopy | none
#   lab-chrome.sh stop
#   lab-chrome.sh status
#   LAB_INSTANCE=2 lab-chrome.sh start <url> north   # a second lab Chrome (both TVs at once)
#   LAB_EXTRA_FLAGS="--disable-gpu-vsync" lab-chrome.sh start ...   # extra switches for experiments
#
# Runs as the kiosk user (gjbm2) over ssh. Uses `sg render` so a group added by enable-hwdecode.sh
# takes effect even inside an older (ControlMaster-multiplexed) ssh session.
# Stopping it simply reveals the kiosk window underneath again.
#
# Kept apart from production on purpose:
#  * started as /opt/google/chrome/google-chrome, not `google-chrome-stable`, so kiosk-loop.sh's hotplug
#    `pkill -f "google-chrome-stable.*--kiosk"` and check-status's kiosk count do not see it. (An explicit
#    admin "restart kiosk" — pattern 'google-chrome.*kiosk' — still closes it too, which is fine.)
#  * nice +10 (inherited by every Chrome process) and oom_score_adj >= 500, so the production screens win
#    any CPU contention and the kernel's OOM killer picks the lab Chrome first.
#  * stop/status find it by its profile dir only; there is no pidfile that could go stale.
#  * the argv is written to a launcher script that bash runs (sg's -c string is parsed by dash, which
#    cannot re-parse bash's %q quoting).
set -uo pipefail
# LAB_INSTANCE=2 runs a second, independent lab Chrome (profile /tmp/chrome-lab2, CDP 9226), e.g. one per TV.
INST=${LAB_INSTANCE:-}
[[ -z $INST || $INST =~ ^[2-9]$ ]] || { echo "LAB_INSTANCE must be empty or 2-9" >&2; exit 2; }
DIR=/tmp/chrome-lab$INST
PORT=$(( 9224 + ${INST:-0} ))
# LAB_CHROME: another Chrome build to test, e.g. an unpacked newer .deb (its chrome-sandbox must be setuid root:
# Ubuntu 24.04's AppArmor only lets /opt/google/chrome use the namespace sandbox)
CHROME=${LAB_CHROME:-/opt/google/chrome/google-chrome}
MATCH="--user-data-dir=$DIR( |\$)"   # pgrep/pkill -f pattern: our profile dir, nothing else
export DISPLAY=:0 XDG_RUNTIME_DIR=/run/user/$(id -u)

flags_for() {
  # Chrome 136 / Linux / X11 (verified against the 136.0.7103.113 sources, see README):
  #  * AcceleratedVideoDecodeLinuxGL is the ONLY switch needed for VA-API on the default ANGLE-GL
  #    backend (default off in 136). Old names (VaapiVideoDecoder, VaapiVideoDecodeLinuxGL) are ignored.
  #  * ANGLE's default 'gl' backend uses GLX on X11 -> one-copy path (VPP NV12->ARGB, X pixmap import).
  #    Zero-copy NV12 dma-buf import needs ANGLE on EGL plus AcceleratedVideoDecodeLinuxZeroCopyGL.
  #  * All features must be in ONE --enable-features switch (a repeated switch overwrites the first).
  # Verify the result with: cdp_probe.py 9224 gpu  and  cdp_probe.py 9224 media
  case "$1" in
    vaapi)          echo "--use-gl=angle --use-angle=gl --enable-features=AcceleratedVideoDecodeLinuxGL" ;;
    vaapi-zerocopy) echo "--use-gl=angle --use-angle=gl-egl --enable-features=AcceleratedVideoDecodeLinuxGL,AcceleratedVideoDecodeLinuxZeroCopyGL" ;;
    none)           echo "" ;;
    *) return 2 ;;
  esac
}

start() {
  local url=${1:-} screen=${2:-south} profile=${3:-vaapi}
  # validate everything BEFORE stopping a running lab Chrome or launching anything
  [[ -n $url ]] || { echo "usage: $0 start <url> [north|south] [vaapi|vaapi-zerocopy|none]" >&2; exit 2; }
  local x
  case "$screen" in
    north) x=0 ;;
    south) x=3840 ;;
    *) echo "bad screen '$screen' (north|south)" >&2; exit 2 ;;
  esac
  # Since 2026-10-13 each TV is its own X screen (media-server/remote-scripts/xorg/20-zaphod.conf):
  # north = :0.0, south = :0.1, both at +0+0. With one shared screen (the old layout) south sits at x=3840.
  if [[ $(xdpyinfo 2>/dev/null | awk '/number of screens:/{print $4}') -ge 2 ]]; then
    [[ $screen == south ]] && DISPLAY=:0.1 || DISPLAY=:0.0
    x=0
  fi
  local flags
  flags=$(flags_for "$profile") || { echo "bad profile '$profile' (vaapi|vaapi-zerocopy|none)" >&2; exit 2; }
  # LAB_EXTRA_FLAGS: extra Chrome switches for experiments (never a second --enable-features; use profile
  # 'none' and pass the full feature list instead)
  flags="$flags ${LAB_EXTRA_FLAGS:-}"
  [[ -x $CHROME ]] || { echo "$CHROME not found" >&2; exit 2; }
  # The target TV must really be at +x+0; otherwise (one HDMI connected, ONE_SCREEN) the window would
  # land on the only screen there is, the production one.
  # (captured first: `xrandr | grep -q` under pipefail fails whenever grep exits before a long listing ends)
  local layout; layout=$(xrandr --current 2>/dev/null)
  if ! grep -qE "^[^ ]+ connected( primary)? [0-9]+x[0-9]+\+$x\+0 " <<< "$layout"; then
    echo "refusing: no connected output at +$x+0 on $DISPLAY for '$screen' (xrandr --current)" >&2; exit 2
  fi
  # a survivor would receive our URL via Chrome's profile singleton and the new flags would be lost
  stop >/dev/null || { echo "refusing: the previous lab Chrome did not stop" >&2; exit 1; }
  mkdir -p "$DIR"; chmod 700 "$DIR"
  local origin; origin=$(python3 -c 'import sys,urllib.parse as u; p=u.urlsplit(sys.argv[1]); print(f"{p.scheme}://{p.netloc}")' "$url")
  local args=(
    --user-data-dir="$DIR" --remote-debugging-port=$PORT
    --no-first-run --no-default-browser-check --noerrdialogs --disable-session-crashed-bubble --disable-infobars
    --kiosk --window-position=$x,0 --window-size=3840,2160
    --autoplay-policy=no-user-gesture-required --disable-features=DBusDaemon
    # WebCodecs needs a secure context; a plain-http LAN origin is not one (localhost would be).
    --unsafely-treat-insecure-origin-as-secure="$origin"
    --enable-logging=stderr --vmodule='*/media/gpu/vaapi/*=2,*/media/gpu/chromeos/*=1,video_decoder_pipeline=2'
  )
  local extra; read -r -a extra <<< "$flags"
  # bash (not dash) parses this file, so %q quoting round-trips for any URL
  {
    echo '#!/usr/bin/env bash'
    echo '# generated by lab-chrome.sh start; run via sg render -c "bash <this file>"'
    printf 'exec nice -n 10'; printf ' %q' "$CHROME" "${args[@]}" "${extra[@]}" "$url"; echo
  } > "$DIR/launch.sh"
  if [[ " $(id -nG) " == *" render "* ]] || grep -qw "$(id -un)" <<< "$(getent group render)"; then
    nohup sg render -c "bash $DIR/launch.sh" > "$DIR/chrome.log" 2>&1 &
  else
    echo "warning: $(id -un) is not in the render group; VA-API will not be available" >&2
    nohup bash "$DIR/launch.sh" > "$DIR/chrome.log" 2>&1 &
  fi
  for _ in $(seq 40); do curl -s --max-time 1 "http://127.0.0.1:$PORT/json/version" >/dev/null && break; sleep 0.25; done
  sleep 1  # let the page's renderer start
  raise_oom
  status
}

raise_oom() {
  # Chrome sets absolute oom_score_adj values on its own processes (browser 0, GPU/utility 200, renderers
  # 300 -- the same as the production kiosks), so an inherited value does not survive: raise ours after
  # start. Renderers Chrome creates later get 300 again; `status` shows the current values.
  local p
  for p in $(pgrep -f -- "$MATCH"); do
    if [[ $(cat "/proc/$p/oom_score_adj" 2>/dev/null || echo 1000) -lt 500 ]]; then
      { echo 500 > "/proc/$p/oom_score_adj"; } 2>/dev/null  # (a failed redirection reports outside a plain 2>)
    fi
  done
}

stop() {
  # the browser is a child of sg/bash; kill everything using our profile dir only
  pkill -TERM -f -- "$MATCH" 2>/dev/null
  for _ in $(seq 20); do pgrep -f -- "$MATCH" >/dev/null || break; sleep 0.25; done
  pkill -KILL -f -- "$MATCH" 2>/dev/null
  sleep 0.2
  local left; left=$(pgrep -fc -- "$MATCH")
  if [[ $left -eq 0 ]]; then echo "lab chrome stopped"; else echo "lab chrome: $left process(es) still running" >&2; return 1; fi
}

status() {
  echo "lab chrome processes: $(pgrep -fc -- "$MATCH")"
  local pids; pids=$(pgrep -d, -f -- "$MATCH")
  if [[ -n $pids ]]; then
    echo "  nice: $(ps -o ni= -p "$pids" | sort -nu | xargs)   oom_score_adj: $(for p in ${pids//,/ }; do cat "/proc/$p/oom_score_adj" 2>/dev/null; done | sort -nu | xargs)"
  fi
  curl -s --max-time 2 "http://127.0.0.1:$PORT/json/version" | grep -E '"Browser"' || echo "CDP $PORT not answering"
  curl -s --max-time 2 "http://127.0.0.1:$PORT/json/list" | grep -E '"url"' | head -3
  echo "kiosk chromes still running: $(pgrep -fc -- '--user-data-dir=/tmp/chrome-(north|south)')"
}

case "${1:-status}" in
  start) shift; start "$@" ;;
  stop) stop ;;
  status) status ;;
  *) echo "usage: $0 start <url> [north|south] [vaapi|vaapi-zerocopy|none] | stop | status" >&2; exit 2 ;;
esac
