#!/bin/bash
# Privileged unit control on the kiosk (media server) box, for Spectre's media-server / kiosk-light-relay modules.
#
# Usage: ./kiosk-ctl.sh {start|stop|restart} {kiosk|light-relay|wifi-prefer-ap}
#
#   kiosk           whichever video unit is in use: kiosk.service (sway, default since 2026-10-13) or the X11 fallback
#                   kiosk-x11.service (active one first, else the enabled one), so an action never switches modes
#                   (the two units Conflicts= each other). stop/restart also clear leftover kiosk Chromes.
#   light-relay     light-relay.service (lightsense.py)
#   wifi-prefer-ap  wifi-prefer-ap.service
#
# Output: '<target>: already running (<unit>)' / '<target>: started (<unit>, pid N)' / '<target>: stopped (<unit>)' /
#         '<target>: not running (<unit>)' / '<target>: restarted (<unit>, pid N)'.
# Exit:   0 ok, 1 the action failed on the box, 2 usage error, 3 the box could not be reached.
#
# Same mechanism as restart-kiosk.sh: the sudo password comes from local-scripts/.env and travels inside the script fed
# to the remote bash over SSH stdin, then is piped to 'sudo -S', so it never appears in any argv / ps listing (here or
# on the box). Unlike restart-kiosk.sh it is quoted with printf %q, so any character in it is safe. Nothing is written
# to maintenance.log.

set -euo pipefail

usage() {
    echo "usage: $0 {start|stop|restart} {kiosk|light-relay|wifi-prefer-ap}" >&2
    exit 2
}

[ $# -eq 2 ] || usage
ACTION="$1"
TARGET="$2"
case "$ACTION" in start|stop|restart) ;; *) usage ;; esac
case "$TARGET" in kiosk|light-relay|wifi-prefer-ap) ;; *) usage ;; esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/local-scripts/.env" ]; then
    # shellcheck disable=SC1091
    source "$SCRIPT_DIR/local-scripts/.env"
elif [ -f "$SCRIPT_DIR/.env" ]; then
    # shellcheck disable=SC1091
    source "$SCRIPT_DIR/.env"
fi

REMOTE_HOST="${MEDIA_SERVER_HOST:-192.168.1.92}"
REMOTE_USER="${MEDIA_SERVER_USER:-gjbm2}"
REMOTE_PORT="${MEDIA_SERVER_PORT:-22}"
case "$REMOTE_HOST$REMOTE_USER$REMOTE_PORT" in
    *[!A-Za-z0-9._-]*) echo "kiosk-ctl: invalid MEDIA_SERVER_HOST/USER/PORT in local-scripts/.env" >&2; exit 2 ;;
esac

# BatchMode: key auth only, never an interactive prompt. ControlMaster=no reuses an existing master connection but
# never spawns a persistent one (whose inherited stderr would hold a caller's pipe open for 10 minutes).
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=5 -o ControlMaster=no -o ServerAliveInterval=10 -o ServerAliveCountMax=3
          -o LogLevel=ERROR -p "$REMOTE_PORT")

set +e
{
    printf 'PW=%q\nACTION=%q\nTARGET=%q\n' "${MEDIA_SERVER_PASSWORD:-}" "$ACTION" "$TARGET"
    cat <<'REMOTE'
as_root() {   # password (if any) via a pipe from the printf builtin: never in argv
    if [ -n "$PW" ]; then printf '%s\n' "$PW" | sudo -S -p '' "$@"; else sudo -n "$@"; fi
}

wait_state() {   # wait_state <unit> <active|inactive> <seconds>
    local i
    for ((i = 0; i < $3; i++)); do
        if [ "$2" = active ]; then systemctl is-active --quiet "$1" && return 0
        else systemctl is-active --quiet "$1" || return 0; fi
        sleep 1
    done
    return 1
}

main_pid() { systemctl show "$1" -p MainPID --value 2>/dev/null; }

clear_kiosk_chromes() {   # the kiosk.service ExecStopPost pattern: only this user's two kiosk Chromes
    pkill -u "$(id -u)" -f -- '--user-data-dir=/tmp/chrome-(north|south)' 2>/dev/null || true
    sleep 1
}

main() {
    local unit
    case "$TARGET" in
        kiosk)
            # same choice as restart-kiosk.sh: the X11 fallback only when it is the one running (or the only one enabled)
            unit=kiosk.service
            if systemctl is-active --quiet kiosk-x11.service; then
                unit=kiosk-x11.service
            elif ! systemctl is-active --quiet kiosk.service && ! systemctl is-enabled --quiet kiosk.service \
                    && systemctl is-enabled --quiet kiosk-x11.service; then
                unit=kiosk-x11.service
            fi ;;
        light-relay) unit=light-relay.service ;;
        wifi-prefer-ap) unit=wifi-prefer-ap.service ;;
        *) echo "$TARGET: unknown target" >&2; return 2 ;;
    esac

    case "$ACTION" in
        start)
            if systemctl is-active --quiet "$unit"; then
                echo "$TARGET: already running ($unit)"; return 0
            fi
            as_root systemctl start "$unit" || { echo "$TARGET: start failed ($unit)" >&2; systemctl status "$unit" --no-pager -n 10 >&2; return 1; }
            if wait_state "$unit" active 20; then
                echo "$TARGET: started ($unit, pid $(main_pid "$unit"))"
            else
                echo "$TARGET: start failed ($unit is $(systemctl is-active "$unit"))" >&2
                systemctl status "$unit" --no-pager -n 10 >&2
                return 1
            fi ;;
        stop)
            if ! systemctl is-active --quiet "$unit"; then
                [ "$TARGET" = kiosk ] && clear_kiosk_chromes
                echo "$TARGET: not running ($unit)"; return 0
            fi
            as_root systemctl stop "$unit" || { echo "$TARGET: stop failed ($unit)" >&2; return 1; }
            [ "$TARGET" = kiosk ] && clear_kiosk_chromes
            if wait_state "$unit" inactive 20; then
                echo "$TARGET: stopped ($unit)"
            else
                echo "$TARGET: stop failed ($unit is $(systemctl is-active "$unit"))" >&2
                return 1
            fi ;;
        restart)
            if [ "$TARGET" = kiosk ]; then
                # as restart-kiosk.sh: stop, clear leftover Chromes, start
                as_root systemctl stop "$unit" || { echo "$TARGET: stop failed ($unit)" >&2; return 1; }
                clear_kiosk_chromes
                as_root systemctl start "$unit" || { echo "$TARGET: start failed ($unit)" >&2; systemctl status "$unit" --no-pager -n 10 >&2; return 1; }
            else
                as_root systemctl restart "$unit" || { echo "$TARGET: restart failed ($unit)" >&2; systemctl status "$unit" --no-pager -n 10 >&2; return 1; }
            fi
            if wait_state "$unit" active 20; then
                echo "$TARGET: restarted ($unit, pid $(main_pid "$unit"))"
            else
                echo "$TARGET: restart failed ($unit is $(systemctl is-active "$unit"))" >&2
                systemctl status "$unit" --no-pager -n 10 >&2
                return 1
            fi ;;
        *) echo "$ACTION: unknown action" >&2; return 2 ;;
    esac
}

# main's stdin is /dev/null so nothing it runs can swallow the rest of this script from the SSH stdin
main </dev/null
REMOTE
} | timeout 150 ssh "${SSH_OPTS[@]}" "$REMOTE_USER@$REMOTE_HOST" bash -s
RC=${PIPESTATUS[1]}
set -e

case $RC in
    0|1|2) exit $RC ;;
    124) echo "$TARGET: timed out talking to $REMOTE_USER@$REMOTE_HOST" >&2; exit 3 ;;
    255) echo "$TARGET: could not reach $REMOTE_USER@$REMOTE_HOST over SSH" >&2; exit 3 ;;
    *) echo "$TARGET: $ACTION failed (exit $RC)" >&2; exit 1 ;;
esac
