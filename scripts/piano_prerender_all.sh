#!/usr/bin/env bash
# Prerender every installed piano sound x every test-piano piece into piano_render's cache, so the Room page's test
# piano starts at once (a cache hit) instead of rendering on the first press (22 Oct: only concert-d was prerendered,
# and any other sound rendered on its first press: 15 s to over 2 min a piece).
#
#   scripts/piano_prerender_all.sh [--dry-run] [ID ...]
#
#   ID ...      the sounds to do, in that order (default: every installed one, the likeliest first: fazioli-sf2,
#               grand-c5, steinway-b, bright-cf3, upright, honky-tonk, then the rest in the Room page's order)
#   --dry-run   say what is cached and what would be rendered; render nothing
#
# - Sequential, one piece at a time, at SCHED_IDLE + nice 19, single-threaded. Each new render takes the shared CPU
#   lock (~/.cache/screen-machine/cpu.lock, the CLAUDE.md rule; waits up to PIANO_PRERENDER_LOCK_WAIT_S, 3600 s) and
#   lets go of it between pieces, so other jobs get in. The sound's samples stay loaded from one piece to the next.
# - Resumable: a piece already in the cache is skipped at once (no lock). Stop it any time and run it again.
# - A piece the Room page renders at the same moment is rendered once (render.py's per-key lock: one waits for the
#   other and takes its result).
# - Log: ~/.local/state/screen-machine/piano-prerender.log ($PIANO_PRERENDER_LOG), a line per piece rendered or
#   failed and one per sound. Exit 0 when every piece is in the cache, 1 when any failed or was skipped.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PIANO_PY:-$HOME/.venvs/sm-piano/bin/python}"
export PIANO_PRERENDER_LOG="${PIANO_PRERENDER_LOG:-$HOME/.local/state/screen-machine/piano-prerender.log}"
export PIANO_CPU_LOCK="${PIANO_CPU_LOCK:-$HOME/.cache/screen-machine/cpu.lock}"
export PIANO_PRERENDER_LOCK_WAIT_S="${PIANO_PRERENDER_LOCK_WAIT_S:-3600}"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMBA_NUM_THREADS=1

if [[ ! -x "$PY" ]]; then
    echo "piano_prerender_all: no $PY (see piano_render/README.md, Run)" >&2
    exit 2
fi
mkdir -p "$(dirname "$PIANO_PRERENDER_LOG")" "$(dirname "$PIANO_CPU_LOCK")"
cd "$REPO" || exit 2

RUN=()
command -v chrt >/dev/null && RUN+=(chrt -i 0)
RUN+=(nice -n 19)

exec "${RUN[@]}" "$PY" - "$@" <<'PYEOF'
import datetime
import errno
import fcntl
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.getcwd())
from piano_render import engines, instruments, render                    # noqa: E402
from piano_render.__main__ import TEST_PIANO_DIR, lower_priority         # noqa: E402

LIKELY = ["fazioli-sf2", "grand-c5", "steinway-b", "bright-cf3", "upright", "honky-tonk"]
LOG = Path(os.environ["PIANO_PRERENDER_LOG"])
LOCK = Path(os.environ["PIANO_CPU_LOCK"])
LOCK_WAIT_S = float(os.environ["PIANO_PRERENDER_LOCK_WAIT_S"])


def say(msg: str) -> None:
    line = f"{datetime.datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError as e:
        print(f"(log {LOG}: {e})", file=sys.stderr)


class CpuLock:
    """flock on the shared CPU lock file (the one `flock -w N ~/.cache/screen-machine/cpu.lock` takes)."""

    def __enter__(self):
        self.fd = os.open(LOCK, os.O_RDWR | os.O_CREAT, 0o644)
        end = time.monotonic() + LOCK_WAIT_S
        while True:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError as e:
                if e.errno not in (errno.EAGAIN, errno.EACCES):
                    os.close(self.fd)
                    raise
                if time.monotonic() > end:
                    os.close(self.fd)
                    raise TimeoutError(f"the CPU lock was busy for {LOCK_WAIT_S:.0f} s")
                time.sleep(1.0)

    def __exit__(self, *exc):
        os.close(self.fd)                                                    # closing it lets go of the lock
        return False


def main(argv: list) -> int:
    dry = "--dry-run" in argv
    want = [a for a in argv if a != "--dry-run"]
    lower_priority()
    reg = instruments.load()
    rendered = [i for i in reg.ids() if reg.get(i).get("engine") not in engines.NEVER_RENDERED]
    avail = [i for i in rendered if reg.why_unavailable(i) is None]
    if want:
        order = []
        for i in want:
            if i in avail:
                order.append(i)
            elif i in rendered:
                say(f"{i}: skipped ({reg.why_unavailable(i)})")
            else:
                say(f"{i}: skipped (not a rendered sound in {reg.path})")
    else:
        order = [i for i in LIKELY if i in avail] + [i for i in avail if i not in LIKELY]
    mids = sorted(p for p in TEST_PIANO_DIR.iterdir() if p.suffix.lower() in (".mid", ".midi")) \
        if TEST_PIANO_DIR.is_dir() else []
    if not order or not mids:
        say(f"nothing to do ({len(order)} sounds, {len(mids)} pieces in {TEST_PIANO_DIR})")
        return 1 if want else 0
    say(f"{'dry run' if dry else 'start'}: {len(order)} sounds x {len(mids)} pieces: {', '.join(order)}")
    bad = 0
    t_all = time.monotonic()
    for inst in order:
        new = hit = fail = 0
        t_inst = time.monotonic()
        for m in mids:
            try:
                if render.cached(m, inst, registry=reg) is not None:
                    hit += 1
                    continue
                if dry:
                    new += 1
                    continue
                with CpuLock():
                    res = render.render(m, inst, registry=reg)
                s = res.stats or {}
                if res.cached:
                    hit += 1
                    say(f"{inst}: {m.name}: in the cache (rendered by another process meanwhile)")
                else:
                    new += 1
                    say(f"{inst}: {m.name}: rendered in {s.get('wall_s', 0):.0f} s "
                        f"({res.duration_s:.0f} s of audio, {s.get('x_realtime_wall')}x realtime)")
            except Exception as e:                                           # noqa: BLE001  one piece never stops it
                fail += 1
                say(f"{inst}: {m.name}: FAILED {type(e).__name__}: {e}")
        bad += fail
        if dry:
            say(f"{inst}: {hit} cached, {new} to render{f', {fail} failed' if fail else ''}")
        else:
            say(f"{inst}: done in {time.monotonic() - t_inst:.0f} s: {new} rendered, {hit} already cached"
                f"{f', {fail} failed' if fail else ''}")
            render.release_engines()                                         # free the samples before the next sound
    say(f"{'dry run done' if dry else 'finished'} in {time.monotonic() - t_all:.0f} s"
        f"{f': {bad} failed (see above)' if bad else ''}")
    return 1 if bad else 0


sys.exit(main(sys.argv[1:]))
PYEOF
