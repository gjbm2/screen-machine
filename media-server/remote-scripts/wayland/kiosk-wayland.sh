#!/usr/bin/env bash
# Kiosk launcher for sway (default since 2026-10-13, started by kiosk.service via sway-kiosk.conf): north Chrome on workspace 1 (HDMI-A-1), south on workspace 2
# (HDMI-A-2), restarted if one exits. Started by sway (exec in sway-kiosk.conf), so WAYLAND_DISPLAY and SWAYSOCK
# are set. Video path from /etc/default/kiosk-wayland: KIOSK_VIDEO=zerocopy (default) or copy.
LOG=/var/log/kiosk.log
exec >>"$LOG" 2>&1
echo "$(date '+%F %T')  --- kiosk-wayland start (sway $(sway --version | awk '{print $3}'))"
# One launcher at a time: sway runs in a logind session, so a launcher from an earlier sway can outlive it
# (stopping kiosk.service ends sway but not every process in the session). Replace any older one.
PIDF=/tmp/kiosk-wayland.pid
if [[ -f $PIDF ]] && old=$(<"$PIDF") && [[ $old != "$$" ]] && kill -0 "$old" 2>/dev/null; then
    echo "$(date '+%F %T')  stopping older launcher $old"; kill "$old"
fi
echo $$ > "$PIDF"
[[ -r /etc/default/kiosk-wayland ]] && . /etc/default/kiosk-wayland
KIOSK_VIDEO=${KIOSK_VIDEO:-zerocopy}

URL_NORTH="http://95.141.21.170:8000/display/north-screen"
URL_SOUTH="http://95.141.21.170:8000/display/south-screen"
DIR_NORTH=/tmp/chrome-north DIR_SOUTH=/tmp/chrome-south
DBG_NORTH=9222 DBG_SOUTH=9223
mkdir -p "$DIR_NORTH" "$DIR_SOUTH"; chmod 700 "$DIR_NORTH" "$DIR_SOUTH"

# Keep ALL features in ONE --enable-features switch (a second one replaces the first).
# Same for --disable-features. Chrome 155 turns zero-copy on by default, so copy mode disables it explicitly.
case "$KIOSK_VIDEO" in
  copy)     VIDEO="--enable-features=AcceleratedVideoDecodeLinuxGL"
            DISABLE="--disable-features=DBusDaemon,AcceleratedVideoDecodeLinuxZeroCopyGL" ;;
  *)        VIDEO="--use-gl=angle --use-angle=gl-egl --enable-features=AcceleratedVideoDecodeLinuxGL,AcceleratedVideoDecodeLinuxZeroCopyGL"
            DISABLE="--disable-features=DBusDaemon" ;;
esac
CHROME="google-chrome-stable --ozone-platform=wayland \
        --no-first-run --no-default-browser-check --noerrdialogs \
        --kiosk $DISABLE $VIDEO \
        --kiosk-mode-no-dialogs --kiosk-mode-no-prompts \
        --disable-session-crashed-bubble --disable-infobars ${CHROME_EXTRA:-}"
echo "$(date '+%F %T')  video path: $KIOSK_VIDEO"

declare -A PID
launch() {  # launch <name> <workspace> <port> <dir> <url>
    local name=$1 ws=$2 port=$3 dir=$4 url=$5
    swaymsg -q "workspace $ws"
    $CHROME --remote-debugging-port=$port --user-data-dir="$dir" "$url" > "$dir/chrome_console.log" 2>&1 &
    PID[$name]=$!
    # wait for its window, then pin it to its TV's workspace (new windows open on the focused workspace, but a
    # slow start could race the next launch)
    for _ in $(seq 1 60); do
        swaymsg -t get_tree | grep -q "\"pid\": ${PID[$name]}," && break
        sleep 0.5
    done
    swaymsg -q "[pid=${PID[$name]}] move container to workspace $ws, fullscreen enable"
    echo "$(date '+%F %T')  spawn $name (pid ${PID[$name]}) on workspace $ws"
}
# North TV shows workspace 1. With the south TV missing, sway keeps workspace 2 on the north TV too; focusing 1
# last (and re-checking in the loop) keeps north on north. Focusing 2 then 1 shows each on its own TV when both exist.
north_on_north() {
    [[ $(swaymsg -p -t get_outputs | awk '/^Output HDMI-A-1/{f=1} f && /^  Workspace:/{print $2; exit}') == 1 ]] && return
    swaymsg -q "workspace 2"; swaymsg -q "workspace 1"
    echo "$(date '+%F %T')  re-showed workspace 1 (north) on HDMI-A-1"
}
launch north 1 $DBG_NORTH "$DIR_NORTH" "$URL_NORTH"
launch south 2 $DBG_SOUTH "$DIR_SOUTH" "$URL_SOUTH"
north_on_north
while sleep 5; do
    # our sway is gone (service stopped or restarted): exit instead of relaunching into nothing
    [[ -S $SWAYSOCK ]] || { echo "$(date '+%F %T')  sway gone, launcher exiting"; exit 0; }
    kill -0 "${PID[north]}" 2>/dev/null || launch north 1 $DBG_NORTH "$DIR_NORTH" "$URL_NORTH"
    kill -0 "${PID[south]}" 2>/dev/null || launch south 2 $DBG_SOUTH "$DIR_SOUTH" "$URL_SOUTH"
    north_on_north
done
