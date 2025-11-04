#!/usr/bin/env bash
# Dual‑HDMI kiosk with explicit port‑URL mapping
# -------------------------------------------------------------
# QUICK TOGGLE
#   ONE_SCREEN=1     → turn **off** HDMI‑2 and run a single full‑screen
#                       Chrome window on HDMI‑1 only.  All dialogs must
#                       appear fully on‑screen.
#   ONE_SCREEN=mirror → (optional) clone HDMI‑2 to HDMI‑1 instead of off.
#                       Still a single Chrome window.
# Leave ONE_SCREEN unset for the normal dual‑window layout.
#
#    DIALOG_TAMER=0|1
#        0 / unset  → Don't touch Chrome dialogs
#        1          → Every second move any Chrome *dialog* window to 0,0 so
#                       it can't hide off‑screen (requires *wmctrl*)
#        2          → reposition stray Chrome dialogs every 0.2 s to (0,0)
#        3          → **maximize** and reposition every 0.2 s (bullet‑proof)
#                       Adds both _NET_WM_STATE maximized_vert & maximized_horz.
#                       Ideal when Chrome keeps sliding the window away.
# -------------------------------------------------------------

LOG=/var/log/kiosk.log
exec >>"$LOG" 2>&1
echo "$(date '+%F %T')  --- script start"

# single‑instance lock
exec 9>/tmp/kiosk.lock
flock -n 9 || { echo "$(date '+%F %T')  exit: another copy running"; exit 0; }

URL_NORTH="http://95.141.21.170:8000/display/north-screen"
URL_SOUTH="http://95.141.21.170:8000/display/south-screen"
W=3840 H=2160

DIR_NORTH=/tmp/chrome-north
DIR_SOUTH=/tmp/chrome-south
mkdir -p "$DIR_NORTH" "$DIR_SOUTH"
chmod 700 "$DIR_NORTH" "$DIR_SOUTH"

CHROME="google-chrome-stable \
        --no-first-run --no-default-browser-check --noerrdialogs \
        --kiosk --disable-features=DBusDaemon \
        --kiosk-mode-no-dialogs --kiosk-mode-no-prompts \
        --disable-session-crashed-bubble --disable-infobars \
        --enable-logging=stderr --v=1"

[[ -n "$CHROME_EXTRA" ]] && CHROME="$CHROME $CHROME_EXTRA"

export DISPLAY=:0

# DPMS / screensaver off
xset s off
xset -dpms
xset s noblank

# wait for X to be ready
for i in {1..15}; do
    if xrandr --query > /dev/null 2>&1; then
        echo "$(date '+%F %T')  xrandr OK after $i s"
        break
    fi
    sleep 1
done

# re‑assert DPMS off (some drivers flip it back on)
DISPLAY=:0 xset s off
DISPLAY=:0 xset -dpms
DISPLAY=:0 xset s noblank

export XDG_RUNTIME_DIR=/run/user/1000   # adjust if UID differs

openbox --startup /bin/true &
unclutter -idle 0 &
echo "$(date '+%F %T')  openbox + unclutter started"


# --- ENV toggles -------------------------------------------------------------
ONE_SCREEN=${ONE_SCREEN:-0}
DIALOG_TAMER=${DIALOG_TAMER:-0}
echo "$(date '+%F %T')  ONE_SCREEN=$ONE_SCREEN  DIALOG_TAMER=$DIALOG_TAMER"
# -------------------------------------------------

# optional dialog‑tamer helper (needs wmctrl)
command -v wmctrl >/dev/null && [[ $DIALOG_TAMER != 0 ]] && {
  interval=1
  [[ $DIALOG_TAMER == 2 || $DIALOG_TAMER == 3 ]] && interval=0.2
  (
    while sleep "$interval"; do
      # wmctrl -lGx: ID  X  Y  W  H  WM_CLASS  TITLE
      wmctrl -lGx | awk '/google-chrome/ {print $1,$3,$4,$5,$6}' | while read -r wid x y w h; do
        # skip the two kiosk windows (full‑screen 4K): width >= 3800 && height >= 2100
        if (( w >= 3800 && h >= 2100 )); then continue; fi
        if [[ $DIALOG_TAMER == 3 ]]; then
          # maximize + move top‑left
          wmctrl -ir "$wid" -b add,maximized_vert,maximized_horz
          wmctrl -ir "$wid" -e 0,0,0,-1,-1
        else
          # just move top‑left, keep size
          wmctrl -ir "$wid" -e 0,0,0,"$w","$h"
        fi
      done
    done
  ) &
  echo "$(date '+%F %T')  dialog‑tamer loop started (interval $interval s, mode $DIALOG_TAMER)"
}

prev=""
while sleep 2; do
    mapfile -t HDMI < <(xrandr --query | awk '/^HDMI-[0-9] connected/{print $1}')
    combo=$(printf '%s ' "${HDMI[@]}")        # e.g. "HDMI-1 " or "HDMI-1 HDMI-2 "
    [[ "$combo" == "$prev" ]] && continue

    echo "$(date '+%F %T')  connector set: $combo"
    pkill -f "google-chrome-stable.*--kiosk"

    case "$combo" in
      "HDMI-1 " )
          xrandr --output HDMI-1 --auto --primary --pos 0x0
          echo "$(date '+%F %T')  spawn NORTH on HDMI-1"
          $CHROME --user-data-dir="$DIR_NORTH" \
                  --window-position=0,0  --window-size=${W},${H}  "$URL_NORTH" \
                  > "$DIR_NORTH/chrome_console.log" 2>&1 &
          ;;
      "HDMI-2 " )
          xrandr --output HDMI-2 --auto --primary --pos 0x0
          echo "$(date '+%F %T')  spawn SOUTH on HDMI-2"
          $CHROME --user-data-dir="$DIR_SOUTH" \
                  --window-position=0,0  --window-size=${W},${H}  "$URL_SOUTH" \
                  > "$DIR_SOUTH/chrome_console.log" 2>&1 &
          ;;
      "HDMI-1 HDMI-2 " | "HDMI-2 HDMI-1 " )
          if [[ "$ONE_SCREEN" == "1" || "$ONE_SCREEN" == "off" ]]; then
              # --- ONE‑SCREEN: disable HDMI‑2 completely
              xrandr --output HDMI-1 --auto --primary --pos 0x0
              xrandr --output HDMI-2 --off
              xrandr --fb ${W}x${H}               # shrink the root FB
              echo "$(date '+%F %T')  ONE_SCREEN (off) – single kiosk window on HDMI-1"
              $CHROME --user-data-dir="$DIR_NORTH" \
                      --window-position=0,0 --window-size=${W},${H}  "$URL_NORTH" \
                      > "$DIR_NORTH/chrome_console.log" 2>&1 &
          elif [[ "$ONE_SCREEN" == "mirror" ]]; then
              # --- ONE‑SCREEN: mirror HDMI‑2 onto HDMI‑1
              xrandr --output HDMI-1 --auto --primary --pos 0x0
              xrandr --output HDMI-2 --auto --same-as HDMI-1
              echo "$(date '+%F %T')  ONE_SCREEN (mirror) – single kiosk window on HDMI-1"
              $CHROME --user-data-dir="$DIR_NORTH" \
                      --window-position=0,0 --window-size=${W},${H}  "$URL_NORTH" \
                      > "$DIR_NORTH/chrome_console.log" 2>&1 &
          else
              # --- normal dual-window layout
              xrandr --output HDMI-1 --auto --primary --pos 0x0
              xrandr --output HDMI-2 --auto --right-of HDMI-1
              echo "$(date '+%F %T')  spawn NORTH on HDMI-1  and  SOUTH on HDMI-2"

              $CHROME --user-data-dir="$DIR_NORTH" \
                      --window-position=0,0      --window-size=${W},${H}  "$URL_NORTH" \
                      > "$DIR_NORTH/chrome_console.log" 2>&1 &

              $CHROME --user-data-dir="$DIR_SOUTH" \
                      --window-position=${W},0   --window-size=${W},${H}  "$URL_SOUTH" \
                      > "$DIR_SOUTH/chrome_console.log" 2>&1 &
          fi
          ;;
      * )
          echo "$(date '+%F %T')  no HDMI connected – nothing to show"
          ;;
    esac

    prev="$combo"
done



