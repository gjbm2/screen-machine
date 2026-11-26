#!/usr/bin/env bash
# The virtual Art-Net node on the Windows side (docs/GIGBAR_RIG.md §7): copy it there and start, stop or check it.
#
#   tools/virtual_node/run-windows.sh start [-- <virtual_node.py flags>]   # copy, then start (stops our old one first)
#   tools/virtual_node/run-windows.sh stop
#   tools/virtual_node/run-windows.sh status
#   tools/virtual_node/run-windows.sh log
#
# It runs as Windows Python 3.12 (py -3.12's python.exe, in a hidden window: Windows Firewall allows python.exe
# inbound on this laptop and blocks pythonw.exe, and rules go by program), from %LOCALAPPDATA%\screen-machine\virtual-node,
# logging to node.log there; its PID is kept in node.pid, and stop only ever ends that PID. The view is
# http://127.0.0.1:8705/ in a Windows browser. On PC22 it is a Spectre card instead (Windows' udp 6454 is the Art-Net
# relay's there: run it with --port 6455).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
winuser="$(cmd.exe /c 'echo %USERNAME%' 2>/dev/null | tr -d '\r')"
dest="/mnt/c/Users/$winuser/AppData/Local/screen-machine/virtual-node"
windest="C:\\Users\\$winuser\\AppData\\Local\\screen-machine\\virtual-node"
cmd="${1:-status}"; shift || true
[[ "${1:-}" == "--" ]] && shift

ps() { (cd /mnt/c && powershell.exe -NoProfile -Command "$1") | tr -d '\r'; }
pid() { [[ -f "$dest/node.pid" ]] && tr -d '\r\n ' < "$dest/node.pid" || true; }
alive() { local p; p="$(pid)"; [[ -n "$p" ]] && [[ -n "$(ps "Get-Process -Id $p -ErrorAction SilentlyContinue | Where-Object { \$_.ProcessName -like 'python*' } | Select-Object -ExpandProperty Id")" ]]; }

stop() {
  if alive; then ps "Stop-Process -Id $(pid)"; echo "stopped the virtual node (pid $(pid))"; else echo "not running"; fi
  rm -f "$dest/node.pid"
}

case "$cmd" in
  start)
    mkdir -p "$dest"
    stop >/dev/null
    cp "$here/virtual_node.py" "$here/gigbar.html" "$dest/"
    extra=""; for a in "$@"; do extra+=", '$a'"; done
    p="$(ps "\$py = (py -3.12 -c 'import sys; print(sys.executable)'); \$p = Start-Process -FilePath \$py -WorkingDirectory '$windest' -PassThru -WindowStyle Hidden -ArgumentList @('$windest\\virtual_node.py', '--log', '$windest\\node.log'$extra); \$p.Id" | tail -1)"
    echo "$p" > "$dest/node.pid"
    sleep 1.5
    if alive; then echo "virtual node running (pid $p): view http://127.0.0.1:8705/ ; log $dest/node.log"
    else echo "it didn't stay up:"; tail -20 "$dest/node.log" 2>/dev/null; exit 1; fi
    ;;
  stop) stop ;;
  status)
    if alive; then echo "running (pid $(pid))"; curl -s -m 3 http://127.0.0.1:8705/status 2>/dev/null \
                     || ps "(Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8705/status).Content"; echo
    else echo "not running"; fi
    ;;
  log) tail -40 "$dest/node.log" ;;
  *) echo "usage: $0 start|stop|status|log" >&2; exit 2 ;;
esac
