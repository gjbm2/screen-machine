#!/usr/bin/env bash
# The show sender on the kiosk, for its Spectre card: status | start | stop | restart | logs [n]
#   tools/show_sender/kiosk-ctl.sh status        -> one JSON line {"active", "since", "pid"}
set -uo pipefail
HOST="${SHOW_SENDER_HOST:-gjbm2@192.168.1.92}"
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=6 "$HOST")
case "${1:-status}" in
  status)
    "${SSH[@]}" 'a=$(systemctl is-active show-sender.service 2>/dev/null); s=$(systemctl show -p ActiveEnterTimestamp --value show-sender.service 2>/dev/null); p=$(systemctl show -p MainPID --value show-sender.service 2>/dev/null); printf "{\"active\":\"%s\",\"since\":\"%s\",\"pid\":%s}\n" "$a" "$s" "${p:-0}"' ;;
  start|stop|restart)
    "${SSH[@]}" "sudo systemctl $1 show-sender.service && systemctl is-active show-sender.service" ;;
  logs)
    "${SSH[@]}" "journalctl -u show-sender.service --no-pager -n ${2:-200}" ;;
  *) echo "usage: $0 status|start|stop|restart|logs [n]" >&2; exit 2 ;;
esac
