#!/usr/bin/env bash
# The show sender on the kiosk, for its Spectre card: status | start | stop | restart | logs [n]
#   tools/show_sender/kiosk-ctl.sh status        -> one JSON line {"active", "since", "pid"}
set -uo pipefail
HOST="${SHOW_SENDER_HOST:-gjbm2@192.168.1.92}"
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=6 "$HOST")
# the unit is a systemd --user one when the kiosk has no passwordless sudo (deploy.sh, 18 Oct): pick whichever exists
PICK='export XDG_RUNTIME_DIR=/run/user/$(id -u); if [ -f ~/.config/systemd/user/show-sender.service ]; then SC="systemctl --user"; JC="journalctl --user"; else SC="sudo systemctl"; JC="journalctl"; fi'
case "${1:-status}" in
  status)
    "${SSH[@]}" "$PICK"'; Q=${SC#sudo }; a=$($Q is-active show-sender.service 2>/dev/null); s=$($Q show -p ActiveEnterTimestamp --value show-sender.service 2>/dev/null); p=$($Q show -p MainPID --value show-sender.service 2>/dev/null); printf "{\"active\":\"%s\",\"since\":\"%s\",\"pid\":%s}\n" "$a" "$s" "${p:-0}"' ;;
  start|stop|restart)
    "${SSH[@]}" "$PICK"'; $SC '"$1"' show-sender.service && ${SC#sudo } is-active show-sender.service' ;;
  logs)
    "${SSH[@]}" "$PICK"'; $JC -u show-sender.service --no-pager -n '"${2:-200}" ;;
  *) echo "usage: $0 status|start|stop|restart|logs [n]" >&2; exit 2 ;;
esac
