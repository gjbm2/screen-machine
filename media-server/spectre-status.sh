#!/bin/bash
# Read-only status probe of the kiosk (media server) box, for Spectre's media-server and kiosk-light-relay modules.
#
# Usage:
#   ./spectre-status.sh                               one line of JSON on stdout, always exit 0 (see below)
#   ./spectre-status.sh logs [N] [all|kiosk|light-relay]
#                                                     last N (default 200) lines of the kiosk logs, as sections headed
#                                                     "==> <source> <==" (exit 0; 1 if the box cannot be reached)
#
# Status JSON (one line):
#   {"ok":true,"host":"192.168.1.92","hostname":"…","ssh_ms":180,"eth_host":"192.168.1.10","eth_reachable":true,
#    "default_route_dev":"enp1s0","eth_dev":"enp1s0","wifi_dev":"wlp2s0",
#    "units":{"kiosk":{"active","enabled","nrestarts","since"},"kiosk_x11":{…},
#             "light_relay":{…,"version":"calib-2","room_sink":false},"wifi_prefer_ap":{…},
#             "deadman_timer":{"active","enabled","last_line","last_age_s"}},
#    "chrome":{"north":true,"south":true},"chrome_cdp":{"north":true,"south":true},
#    "mem_pct":24.4,"disk_pct":26,"load1":0.4,"uptime_s":13148}
#   On SSH failure: {"ok":false,"host":…,"ssh_ms":…,"eth_reachable":…,"error":"…"} within ~8 s.
#
# One SSH round-trip with key auth only (BatchMode=yes: never prompts, never uses MEDIA_SERVER_PASSWORD), no sudo, no
# changes on the box. An existing ControlMaster connection (~/.ssh/config) is reused but never spawned from here: a
# ControlPersist master spawned under a captured stderr keeps the caller's pipe open for 10 minutes.
# Nothing is written locally; in particular nothing goes to maintenance.log (the old poll path grew it to 79.5 MB).

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/local-scripts/.env"

# Host/user/port from local-scripts/.env (sourced in a subshell, so nothing else from it, the password included, enters
# this shell); the Ethernet address can be overridden there with MEDIA_SERVER_ETH_HOST.
HOST="" USER_="" PORT="" ETH_HOST=""
if [ -f "$ENV_FILE" ]; then
    { IFS= read -r HOST; IFS= read -r USER_; IFS= read -r PORT; IFS= read -r ETH_HOST; } < <(
        set +u
        # shellcheck disable=SC1090
        . "$ENV_FILE" >/dev/null 2>&1
        printf '%s\n' "${MEDIA_SERVER_HOST:-}" "${MEDIA_SERVER_USER:-}" "${MEDIA_SERVER_PORT:-}" "${MEDIA_SERVER_ETH_HOST:-}"
    )
fi
HOST="${HOST:-192.168.1.92}"
USER_="${USER_:-gjbm2}"
PORT="${PORT:-22}"
ETH_HOST="${ETH_HOST:-192.168.1.10}"

SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=5 -o ControlMaster=no -o ServerAliveInterval=2 -o ServerAliveCountMax=2
          -o LogLevel=ERROR -p "$PORT")
SSH_TIMEOUT=7   # whole round-trip, so a failed probe returns within ~8 s

json_error() {   # json_error <message>: minimal error object without python3 (message is reduced to safe characters)
    printf '{"ok":false,"host":"%s","error":"%s"}\n' "$(printf '%s' "$HOST" | tr -cd 'A-Za-z0-9._-')" \
        "$(printf '%s' "$1" | tr -cd 'A-Za-z0-9 ._:/()=-')"
}

case "$HOST$USER_$PORT" in
    *[!A-Za-z0-9._-]*) json_error "invalid MEDIA_SERVER_HOST/USER/PORT in local-scripts/.env"; exit 0 ;;
esac

# ── logs mode ────────────────────────────────────────────────────────────────────────────────────────────────────
if [ "${1:-}" = "logs" ]; then
    N="${2:-200}"
    WHICH="${3:-all}"
    case "$N" in ''|*[!0-9]*) echo "usage: $0 logs [N] [all|kiosk|light-relay]" >&2; exit 2 ;; esac
    [ "$N" -ge 1 ] && [ "$N" -le 5000 ] || { echo "N must be 1..5000" >&2; exit 2; }
    case "$WHICH" in all|kiosk|light-relay) ;; *) echo "usage: $0 logs [N] [all|kiosk|light-relay]" >&2; exit 2 ;; esac

    timeout 15 ssh "${SSH_OPTS[@]}" "$USER_@$HOST" bash -s -- "$N" "$WHICH" <<'REMOTE'
N=$1; WHICH=$2
main() {
    if [ "$WHICH" != light-relay ]; then
        echo "==> /var/log/kiosk.log <=="
        tail -n "$N" /var/log/kiosk.log 2>&1
        echo "==> journalctl -u kiosk -u kiosk-x11 -u light-relay <=="
        journalctl --no-pager -q -o short-iso -n "$N" -u kiosk.service -u kiosk-x11.service -u light-relay.service 2>&1
    else
        echo "==> journalctl -u light-relay <=="
        journalctl --no-pager -q -o short-iso -n "$N" -u light-relay.service 2>&1
    fi
}
main </dev/null
REMOTE
    rc=$?
    if [ $rc -ne 0 ]; then
        echo "spectre-status.sh: could not read logs from $USER_@$HOST (ssh exit $rc)" >&2
        exit 1
    fi
    exit 0
fi

[ $# -eq 0 ] || { echo "usage: $0 [logs [N] [all|kiosk|light-relay]]" >&2; exit 2; }

# ── status mode ──────────────────────────────────────────────────────────────────────────────────────────────────
command -v python3 >/dev/null 2>&1 || { json_error "python3 not found on the WSL side"; exit 0; }

# Ethernet-path check from this side, in parallel with the SSH round-trip
if command -v nc >/dev/null 2>&1; then
    exec 3< <(if nc -z -w2 "$ETH_HOST" 22 >/dev/null 2>&1; then echo true; else echo false; fi)
else
    exec 3< <(echo null)
fi

ERR_FILE="$(mktemp)"
trap 'rm -f "$ERR_FILE"' EXIT

T0=$(date +%s%N)
OUT=$(timeout "$SSH_TIMEOUT" ssh "${SSH_OPTS[@]}" "$USER_@$HOST" bash -s 2>"$ERR_FILE" <<'REMOTE'
emit() { printf '%s\t%s\n' "$1" "$2"; }
main() {
    emit hostname "$(hostname)"
    emit now "$(date +%s)"
    for pair in kiosk:kiosk.service kiosk_x11:kiosk-x11.service light_relay:light-relay.service \
                wifi_prefer_ap:wifi-prefer-ap.service deadman_timer:deadman.timer; do
        key=${pair%%:*}; unit=${pair#*:}
        while IFS='=' read -r k v; do
            [ -n "$k" ] && emit "unit.$key.$k" "$v"
        done < <(systemctl show "$unit" -p LoadState -p ActiveState -p SubState -p UnitFileState -p NRestarts \
                     -p ActiveEnterTimestamp 2>/dev/null)
    done

    # light relay: VERSION of the deployed script (the .py on the unit's ExecStart) and whether the room sink is configured
    relay_py=$(systemctl show light-relay.service -p ExecStart 2>/dev/null | grep -oE '/[^ ;]+\.py' | head -n 1)
    relay_py=${relay_py:-$HOME/lightsense.py}
    emit relay.version "$(sed -n -E "s/^VERSION[[:space:]]*=[[:space:]]*['\"]([^'\"]*)['\"].*/\1/p" "$relay_py" 2>/dev/null | head -n 1)"
    if [ -f "$HOME/.config/light-relay/room.json" ]; then emit relay.room_sink true; else emit relay.room_sink false; fi

    # deadman: last completed run (timer fires every 5 min)
    last=$(tail -n 400 /var/log/deadman.log 2>/dev/null | grep -a 'run complete' | tail -n 1)
    emit deadman.last_line "$last"
    [ -n "$last" ] && emit deadman.last_epoch "$(date -d "${last:0:19}" +%s 2>/dev/null)"

    # one Chrome per TV, identified by its DevTools port (north 9222, south 9223; same under sway and the X11 fallback)
    for tv in north:9222 south:9223; do
        name=${tv%%:*}; port=${tv#*:}
        if pgrep -f -- "--remote-debugging-port=$port( |\$)" >/dev/null 2>&1; then emit "chrome.$name" true; else emit "chrome.$name" false; fi
        if [ -n "$(ss -ltnH "sport = :$port" 2>/dev/null)" ]; then emit "cdp.$name" true; else emit "cdp.$name" false; fi
    done

    # network: the device the default route actually uses, and the wired / wireless NIC names
    emit net.route_dev "$(ip -o route get 1.1.1.1 2>/dev/null | sed -n 's/.* dev \([^ ]*\).*/\1/p')"
    for d in /sys/class/net/*; do
        n=${d##*/}
        [ -e "$d/device" ] || continue
        if [ -e "$d/wireless" ]; then [ -z "${wifi:-}" ] && wifi=$n; else [ -z "${eth:-}" ] && eth=$n; fi
    done
    emit net.eth_dev "${eth:-}"
    emit net.wifi_dev "${wifi:-}"

    emit mem_pct "$(awk '/^MemTotal:/{t=$2} /^MemAvailable:/{a=$2} END{if (t) printf "%.1f", (t-a)*100/t}' /proc/meminfo)"
    emit disk_pct "$(df -P / 2>/dev/null | awk 'NR==2{gsub("%","",$5); print $5}')"
    emit load1 "$(cut -d' ' -f1 /proc/loadavg)"
    emit uptime_s "$(cut -d' ' -f1 /proc/uptime)"
    emit end ok
}
main </dev/null
REMOTE
)
RC=$?
T1=$(date +%s%N)
SSH_MS=$(( (T1 - T0) / 1000000 ))
read -r ETH_OK <&3
exec 3<&-
ERR_TAIL="$(grep -v '^Pseudo-terminal' "$ERR_FILE" 2>/dev/null | tail -n 2 | tr '\n' ' ')"

printf '%s\n' "$OUT" | python3 -I -c '
import json, sys

host, user, ssh_ms, rc, eth_host, eth_ok, err_tail = sys.argv[1:8]
rc = int(rc)
kv = {}
for line in sys.stdin.read().splitlines():
    if "\t" in line:
        k, v = line.split("\t", 1)
        kv[k] = v

def num(v, cast=float):
    try:
        return cast(v)
    except (TypeError, ValueError):
        return None

out = {
    "ok": False,
    "host": host,
    "ssh_ms": int(ssh_ms),
    "eth_host": eth_host,
    "eth_reachable": {"true": True, "false": False}.get(eth_ok),
}

if rc != 0 or kv.get("end") != "ok":
    if rc == 124:
        msg = "ssh to %s@%s timed out" % (user, host)
    elif err_tail.strip():
        msg = err_tail.strip()
    else:
        msg = "ssh to %s@%s failed (exit %d)" % (user, host, rc)
    out["error"] = msg[:300]
    print(json.dumps(out, separators=(",", ":")))
    sys.exit(0)

def unit(key):
    p = "unit.%s." % key
    load = kv.get(p + "LoadState")
    return {
        "active": kv.get(p + "ActiveState") or "unknown",
        "sub": kv.get(p + "SubState") or None,
        "enabled": "not-found" if load == "not-found" else (kv.get(p + "UnitFileState") or "unknown"),
        "nrestarts": num(kv.get(p + "NRestarts"), int),
        "since": kv.get(p + "ActiveEnterTimestamp") or None,
    }

now = num(kv.get("now"), int)
last_epoch = num(kv.get("deadman.last_epoch"), int)
relay = unit("light_relay")
relay["version"] = kv.get("relay.version") or None
relay["room_sink"] = kv.get("relay.room_sink") == "true"
deadman = unit("deadman_timer")
deadman = {
    "active": deadman["active"],
    "enabled": deadman["enabled"],
    "last_line": kv.get("deadman.last_line") or None,
    "last_age_s": (now - last_epoch) if (now is not None and last_epoch is not None) else None,
}
uptime = num(kv.get("uptime_s"))

out.update({
    "ok": True,
    "hostname": kv.get("hostname") or None,
    "default_route_dev": kv.get("net.route_dev") or None,
    "eth_dev": kv.get("net.eth_dev") or None,
    "wifi_dev": kv.get("net.wifi_dev") or None,
    "units": {
        "kiosk": unit("kiosk"),
        "kiosk_x11": unit("kiosk_x11"),
        "light_relay": relay,
        "wifi_prefer_ap": unit("wifi_prefer_ap"),
        "deadman_timer": deadman,
    },
    "chrome": {"north": kv.get("chrome.north") == "true", "south": kv.get("chrome.south") == "true"},
    "chrome_cdp": {"north": kv.get("cdp.north") == "true", "south": kv.get("cdp.south") == "true"},
    "mem_pct": num(kv.get("mem_pct")),
    "disk_pct": num(kv.get("disk_pct")),
    "load1": num(kv.get("load1")),
    "uptime_s": int(uptime) if uptime is not None else None,
})
print(json.dumps(out, separators=(",", ":")))
' "$HOST" "$USER_" "$SSH_MS" "$RC" "$ETH_HOST" "$ETH_OK" "$ERR_TAIL" || json_error "could not build status JSON"
exit 0
