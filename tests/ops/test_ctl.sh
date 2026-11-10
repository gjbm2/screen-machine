#!/usr/bin/env bash
# Smoke test for scripts/ctl: fake stream/show/dot services on 18702-18704 in screen sessions sm-*-test.
# Never touches the real sm-* sessions or deployments/*/services/*.env (CTL_ENV_DIR and CTL_PORT_* stand in for
# the deployment's env files and ports; the deployment still decides which services are on).
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CTL="$REPO/scripts/ctl"
T="$(mktemp -d)" || exit 1
export CTL_ENV_DIR="$T/env" CTL_LOG_DIR="$T/logs" CTL_RUN_DIR="$T/run" CTL_SESSION_SUFFIX=-test CTL_FAKE=1
export SM_DEPLOYMENT=finsbury CTL_PORT_STREAM=18702 CTL_PORT_SHOW=18703 CTL_PORT_DOT=18704
mkdir -p "$CTL_ENV_DIR"
printf 'STREAM_PORT=18702\nSTREAM_ARGS=""\nCUDA_DEVICE_ORDER=PCI_BUS_ID\n' >"$CTL_ENV_DIR/sm-stream.env"
printf 'SHOW_ARGS=""\n' >"$CTL_ENV_DIR/sm-show.env"
printf 'DOT_ARGS=""\n' >"$CTL_ENV_DIR/sm-dot.env"
pass=0; fail=0
ok()  { echo "ok - $1"; pass=$((pass + 1)); }
bad() { echo "not ok - $1"; fail=$((fail + 1)); }
check() { local name="$1" want="$2"; shift 2; local out rc; out="$("$@" 2>&1)"; rc=$?
  if [[ "$rc" == "$want" ]]; then ok "$name"; else bad "$name (exit $rc, want $want): $out"; fi; }
cleanup() { "$CTL" stop all >/dev/null 2>&1; kill "${stray:-0}" 2>/dev/null; rm -rf "$T"; }
trap cleanup EXIT

for p in 18702 18703 18704; do ss -Hltn "sport = :$p" | grep -q . && { echo "port $p busy, skipping"; exit 0; }; done

check "status before start = stopped (3)" 3 "$CTL" status all
check "start all" 0 "$CTL" start all
"$CTL" start stream | grep -q "already running" && ok "start again says already running" || bad "already running"
check "status all healthy (0)" 0 "$CTL" status all
screen -ls | grep -q "\.sm-stream-test" && ok "screen session sm-stream-test exists" || bad "screen session"
"$CTL" logs stream 50 | grep -q "ctl start stream" && ok "logs show start banner" || bad "logs"
check "stop all" 0 "$CTL" stop all
check "room is refused (inside Flask now)" 64 "$CTL" start room
screen -ls | grep -q "\.sm-stream-test" && bad "sessions left after stop" || ok "no sessions after stop"
ss -Hltn "sport = :18702" | grep -q . && bad "port 18702 still listening" || ok "port released"

python3 -m http.server 18702 --bind 127.0.0.1 >/dev/null 2>&1 & stray=$!; sleep 0.7
check "start refused when port is held by another process (2)" 2 "$CTL" start stream
kill "$stray"; wait "$stray" 2>/dev/null

echo 'STREAM_HOLD="test hold"' >>"$CTL_ENV_DIR/sm-stream.env"
check "start refused while on hold (2)" 2 "$CTL" start stream
check "start --force overrides hold" 0 "$CTL" start stream --force
check "restart refused while on hold (2)" 2 "$CTL" restart stream
"$CTL" status stream | grep -q "HOLD: test hold" && ok "status shows hold" || bad "status hold"
check "stop is never held" 0 "$CTL" stop stream

# The port comes from the deployment; a <KEY>_PORT line that disagrees refuses (1).
printf 'STREAM_PORT=18799\nSTREAM_ARGS=""\n' >"$CTL_ENV_DIR/sm-stream.env"
check "start refused when STREAM_PORT disagrees with the deployment (1)" 1 "$CTL" start stream
out="$("$CTL" status stream 2>&1)"; [[ "$out" == *disagrees* ]] && ok "status names the port disagreement" || bad "status disagreement"

# ${VAR} in ARGS and extra lines comes from the environment / repo .env; an unset one refuses (1).
printf 'STREAM_ARGS="--public-base ws://${SM_TEST_UNSET_HOST}:1"\n' >"$CTL_ENV_DIR/sm-stream.env"
check "start refused when an \${VAR} is unset (1)" 1 env -u SM_TEST_UNSET_HOST "$CTL" start stream
printf 'STREAM_ARGS="--public-base ws://${SM_TEST_HOST}:1"\nSM_TEST_EXTRA=${SM_TEST_HOST}/x\n' >"$CTL_ENV_DIR/sm-stream.env"
check "start with \${VAR} expanded" 0 env SM_TEST_HOST=10.9.8.7 "$CTL" start stream
"$CTL" logs stream 50 | grep -q "ws://10.9.8.7:1" && ok "ARGS carried the expanded host" || bad "ARGS expansion"
check "stop after expansion test" 0 "$CTL" stop stream

# A service whose capability is off: `start all` skips it and says so; `start <it>` is refused (2).
out="$(SM_DEPLOYMENT=example "$CTL" start dot 2>&1)"; rc=$?
[[ "$rc" == 2 && "$out" == *"off in this deployment"* ]] && ok "start dot refused when dot is off (2)" || bad "capability refuse (exit $rc): $out"
out="$(SM_DEPLOYMENT=example "$CTL" status dot 2>&1)"; [[ "$out" == *"off (capability 'dot'"* ]] && ok "status says dot is off" || bad "status off"
out="$(SM_DEPLOYMENT=example "$CTL" start all 2>&1)"; [[ "$out" == *"dot: skipped (capability 'dot' is off"* ]] && ok "start all skips dot" || bad "start all skip"
"$CTL" stop all >/dev/null 2>&1

echo "# pass $pass fail $fail"
[[ "$fail" == 0 ]]
