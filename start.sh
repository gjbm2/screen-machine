#!/bin/bash
# start.sh — canonical start/stop/status script for Screen Machine
#
# Usage:
#   ./start.sh                  Start all (flask + vite + ngrok)
#   ./start.sh start            Same as above
#   ./start.sh start flask      Start Flask only
#   ./start.sh start vite       Start Vite dev server only
#   ./start.sh start ngrok      Start ngrok tunnel only
#   ./start.sh stop             Stop all
#   ./start.sh stop flask       Stop Flask only
#   ./start.sh stop vite        Stop Vite only
#   ./start.sh stop ngrok       Stop ngrok only
#   ./start.sh restart          Restart all
#   ./start.sh restart flask    Restart Flask only
#   ./start.sh status           Show status of all components
#   ./start.sh status flask     Show status of a single component
#
# Session types: all components use GNU screen sessions.
#   Flask  → screen session "flask"
#   Vite   → screen session "vite"
#   Ngrok  → screen session "ngrok"
#
# Environment variables (optional overrides):
#   NGROK_DOMAIN   — ngrok custom domain (default: adapted-vervet-eternal.ngrok-free.app)
#   NGROK_PORT     — port to tunnel (default: 5000)
#   SCREEN_MACHINE_MODE — "dev" (default) or "prod"

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT"

# ── Defaults ──────────────────────────────────────────────────────────

NGROK_DOMAIN="${NGROK_DOMAIN:-adapted-vervet-eternal.ngrok-free.app}"
NGROK_PORT="${NGROK_PORT:-5000}"
MODE="${SCREEN_MACHINE_MODE:-dev}"

# ── Helpers ───────────────────────────────────────────────────────────

screen_running() {
  # Returns 0 if the named screen session exists
  screen -ls 2>/dev/null | grep -q "\\.$1" 2>/dev/null
}

screen_wipe() {
  screen -wipe >/dev/null 2>&1 || true
}

# ── Start functions ───────────────────────────────────────────────────

start_flask() {
  screen_wipe
  if screen_running flask; then
    echo "Flask: already running"
    return 0
  fi
  screen -dmS flask bash -c "cd '$PROJECT_ROOT' && source .venv/bin/activate && python3 app.py"
  echo "Flask: started (screen session 'flask')"
}

start_vite() {
  screen_wipe
  if screen_running vite; then
    echo "Vite: already running"
    return 0
  fi
  if [[ "$MODE" == "prod" ]]; then
    cd "$PROJECT_ROOT"
    npm run build
    screen -dmS vite bash -c "cd '$PROJECT_ROOT' && npx serve -s dist -l 5173"
    echo "Vite: started in PROD mode (screen session 'vite')"
  else
    screen -dmS vite bash -c "cd '$PROJECT_ROOT' && npm run dev"
    echo "Vite: started in DEV mode (screen session 'vite')"
  fi
}

start_ngrok() {
  screen_wipe
  if screen_running ngrok; then
    echo "Ngrok: already running"
    return 0
  fi
  if [[ -z "$NGROK_DOMAIN" ]]; then
    echo "Ngrok: skipped (no NGROK_DOMAIN set)"
    return 0
  fi
  screen -dmS ngrok bash -c "ngrok http --domain=$NGROK_DOMAIN $NGROK_PORT"
  echo "Ngrok: started (screen session 'ngrok', domain=$NGROK_DOMAIN, port=$NGROK_PORT)"
}

# ── Stop functions ────────────────────────────────────────────────────

stop_flask() {
  pkill -f "python.*app\\.py" 2>/dev/null || true
  screen -S flask -X quit 2>/dev/null || true
  screen_wipe
  echo "Flask: stopped"
}

stop_vite() {
  pkill -f "npm run dev" 2>/dev/null || true
  pkill -f "npx serve" 2>/dev/null || true
  screen -S vite -X quit 2>/dev/null || true
  screen_wipe
  echo "Vite: stopped"
}

stop_ngrok() {
  pkill -f "ngrok http" 2>/dev/null || true
  screen -S ngrok -X quit 2>/dev/null || true
  screen_wipe
  echo "Ngrok: stopped"
}

# ── Status ────────────────────────────────────────────────────────────

status_component() {
  local name="$1"
  screen_wipe
  if screen_running "$name"; then
    echo "$name: running"
  else
    echo "$name: not running"
  fi
}

status_all() {
  status_component flask
  status_component vite
  status_component ngrok
}

# ── Dispatch ──────────────────────────────────────────────────────────

ACTION="${1:-start}"
TARGET="${2:-all}"

case "$ACTION" in
  start)
    case "$TARGET" in
      flask)  start_flask ;;
      vite)   start_vite ;;
      ngrok)  start_ngrok ;;
      all|"") start_flask; start_vite; start_ngrok ;;
      *)      echo "Unknown target: $TARGET"; exit 1 ;;
    esac
    ;;
  stop)
    case "$TARGET" in
      flask)  stop_flask ;;
      vite)   stop_vite ;;
      ngrok)  stop_ngrok ;;
      all|"") stop_flask; stop_vite; stop_ngrok ;;
      *)      echo "Unknown target: $TARGET"; exit 1 ;;
    esac
    ;;
  restart)
    case "$TARGET" in
      flask)  stop_flask;  sleep 1; start_flask ;;
      vite)   stop_vite;   sleep 1; start_vite ;;
      ngrok)  stop_ngrok;  sleep 1; start_ngrok ;;
      all|"") stop_flask; stop_vite; stop_ngrok; sleep 1; start_flask; start_vite; start_ngrok ;;
      *)      echo "Unknown target: $TARGET"; exit 1 ;;
    esac
    ;;
  status)
    case "$TARGET" in
      flask|vite|ngrok) status_component "$TARGET" ;;
      all|"")           status_all ;;
      *)                echo "Unknown target: $TARGET"; exit 1 ;;
    esac
    ;;
  *)
    echo "Usage: $0 {start|stop|restart|status} [flask|vite|ngrok|all]"
    exit 1
    ;;
esac
