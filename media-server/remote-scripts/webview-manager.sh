#!/usr/bin/env bash
exec 9>/tmp/webview-manager.lock
flock -n 9 || exit 0
# Screen-1 -> URL1  Screen-2 -> URL2 (if present).  No forced reloads.

URL1="http://185.254.136.253:8000/display/north-screen"
URL2="http://185.254.136.253:8000/display/south-screen"
WIDTH=3840
HEIGHT=2160

export DISPLAY=:0
export XDG_RUNTIME_DIR=/run/user/1000        # change if id -u gjbm2 ≠ 1000

openbox --startup /bin/true &
unclutter -idle 0 &          # hide mouse
xset s off -dpms s noblank

prev_state=""
while sleep 2; do
    mapfile -t CONN < <(xrandr --query | awk '/^HDMI-[0-9] connected/{print $1}' | sort)
    state=$(printf '%s\n' "${CONN[@]}")

    [[ "$state" == "$prev_state" ]] && continue     # nothing changed
    pkill -f "--kiosk" 2>/dev/null                  # kill old windows

    [[ ${CONN[0]} ]] && chromium-browser --noerrdialogs --kiosk \
        --window-position=0,0 --window-size=${WIDTH},${HEIGHT} "$URL1" &

    [[ ${CONN[1]} ]] && chromium-browser --noerrdialogs --kiosk \
        --window-position=${WIDTH},0 --window-size=${WIDTH},${HEIGHT} "$URL2" &

    prev_state="$state"
done



