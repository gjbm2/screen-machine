#!/usr/bin/env bash
# scripts/piano_setup.sh: set up the test piano renderer (piano_render) on this box. Safe to run again.
#
#   1. ~/.venvs/sm-piano from piano_render/requirements.txt, only if it does not exist yet.
#   2. python -m piano_render fetch [IDS...]: the sfizz engine, every instrument and room IR named in
#      config/piano/instruments.json (downloads resume and are sha256-checked; nothing is redone).
#
# Usage: scripts/piano_setup.sh [IDS...]        e.g. scripts/piano_setup.sh grand-c5 room
#        scripts/piano_setup.sh --status        what is installed (no network)
#
# Data lands outside git: ~/.local/share/screen-machine/piano/{instruments,engines} (about 6.6 GB installed + 5.9 GB of kept downloads in instruments/_downloads, which may be deleted).
# Everything runs at idle priority; pip and the unpacks take ~/.cache/screen-machine/cpu.lock (shared with the
# other heavy jobs on this box, which share the CPU with two live 4K TV streams). No sudo; plays no sound.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${PIANO_VENV:-$HOME/.venvs/sm-piano}"
LOCK="$HOME/.cache/screen-machine/cpu.lock"
mkdir -p "$(dirname "$LOCK")"
export OMP_NUM_THREADS=1 NUMBA_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1

idle() { chrt -i 0 nice -n 19 "$@"; }

if [ ! -x "$VENV/bin/python" ]; then
  echo "piano_setup: creating $VENV"
  PY="$(command -v python3.12 || command -v python3)"
  idle "$PY" -m venv "$VENV"
  chrt -i 0 flock -w 1800 "$LOCK" nice -n 19 "$VENV/bin/pip" install -q -r "$REPO/piano_render/requirements.txt"
fi

cd "$REPO"
if [ "${1:-}" = "--status" ]; then
  exec "$VENV/bin/python" -m piano_render fetch --status
fi
# fetch takes the CPU lock itself, around each unpack only (downloads do not hold it).
idle "$VENV/bin/python" -m piano_render fetch "$@"
