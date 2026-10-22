#!/usr/bin/env bash
# scripts/wsl-boot.sh: pre-logon WSL boot script for screen-machine (owner screen-machine-ca).
#
# Meant to run when the Windows task '\Start WSL Services' boots the Ubuntu distro (that task runs
# `wsl -d Ubuntu -- /home/gjbm2/wsl-autostart.sh`), through a one-line shim at ~/wsl-autostart.sh.
#
# PENDING (2026-10-14): the shim is NOT installed, so nothing runs this script yet. Until it is,
# every boot runs the old ~/wsl-autostart.sh, which truncates ~/startup.log, runs
# ~/shutdown-webservers.sh (pkill -f "python app.py" / "npm run dev" / "ngrok http"), then starts
# Flask in dev mode, the Vite dev server and ngrok in screen sessions.
# The shim, exactly (outside the repo: the owner approves it; screen-machine-ca installs it with
# chmod +x, keeping the old body as ~/wsl-autostart.sh.pre-ctl). See docs/OPS_CONTROL.md section 1.
#     #!/bin/bash
#     exec /home/gjbm2/dev/screen-machine/scripts/wsl-boot.sh "$@"
#
# What it does:
#   - appends timestamped lines to ~/startup.log (never truncates it)
#   - starts Flask (API + built SPA on :5000, overlay WS :8765) and ngrok, through ./start.sh in prod
#     mode; both start commands are idempotent (an already-running screen session is left alone)
#   - logs, read-only, what the AV services are doing
#
# What it never does:
#   - kill anything (no kill-by-name, no stop or restart)
#   - touch the Vite target or run start.sh without an explicit flask/ngrok target: in prod mode
#     that rebuilds dist/ from the live working tree, which must only happen as a deliberate deploy
#   - force-start an AV service that is still on hold: `scripts/ctl start all` skips any service
#     whose deploy/sm-*.env still has a *_HOLD line. See docs/OPS_CONTROL.md.

set -uo pipefail

REPO="/home/gjbm2/dev/screen-machine"
LOG="${HOME:-/home/gjbm2}/startup.log"
STATE_DIR="${HOME:-/home/gjbm2}/.local/state/screen-machine"
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${HOME:-/home/gjbm2}/.local/bin:${PATH:-}"

log() {
  printf '%s [wsl-boot %s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$$" "$*" >> "$LOG"
}

# run_logged <label> <cmd...>: runs the command, then appends each output line to the log.
# Output goes through a temp file, not a pipe, so a daemon the command leaves behind (screen -dm)
# can never hold a pipe open and hang this script.
run_logged() {
  local label="$1" rc tmp line
  shift
  log "$label: \$ $*"
  tmp="$(mktemp "${TMPDIR:-/tmp}/wsl-boot.XXXXXX")" || { log "$label: mktemp failed; running unlogged"; "$@" < /dev/null > /dev/null 2>&1; return; }
  "$@" < /dev/null > "$tmp" 2>&1
  rc=$?
  while IFS= read -r line || [[ -n "$line" ]]; do
    log "$label: $line"
  done < "$tmp"
  rm -f "$tmp"
  log "$label: exit $rc"
}

log "---- boot script start (user $(id -un), args: ${*:-none}; arguments are ignored) ----"

# One run at a time: a second trigger while the first is still going just logs and leaves.
mkdir -p "$STATE_DIR"
exec 9>>"$STATE_DIR/wsl-boot.lock"
if ! flock -n 9; then
  log "another wsl-boot.sh run holds $STATE_DIR/wsl-boot.lock; exiting without doing anything"
  exit 0
fi

if ! cd "$REPO"; then
  log "ERROR: cannot cd to $REPO; nothing started"
  exit 1
fi

# Flask, then ngrok (ngrok tunnels to :5000). Both: start only if their screen session is absent.
run_logged flask env SCREEN_MACHINE_MODE=prod ./start.sh start flask
run_logged ngrok env SCREEN_MACHINE_MODE=prod ./start.sh start ngrok
run_logged screens ./start.sh status

# AV services (room_service, stream_service): screen sessions via scripts/ctl, same pattern as Flask.
# Held services are skipped with their reason (exit 2 is expected until the owners release them).
run_logged av-start ./scripts/ctl start all
run_logged av-status ./scripts/ctl status all

log "---- boot script done ----"
exit 0
