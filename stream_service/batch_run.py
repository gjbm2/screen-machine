"""One batch job in its own process (batch.py): python -m stream_service.batch_run <kind> <json args> <control pid>.

The job is stream_service/batch_jobs/<kind>.py ``run(args, progress)``: it does the work or raises; ``progress(0..1)``
now and then.  This process sees one GPU only, as device 0 (CUDA_VISIBLE_DEVICES, set by batch.py), and may be killed
at any moment (a session took the GPU): the job writes its result as a temp file and renames it at the end.  It dies
with the control, and runs niced so the control's threads stay ahead of it.
"""
from __future__ import annotations

import importlib
import json
import os
import sys


def main(argv: list[str]) -> None:
    kind, args, parent = argv[0], json.loads(argv[1]), int(argv[2]) if len(argv) > 2 else 0
    try:                                        # as a render worker: killed when the control goes
        import ctypes
        import signal

        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGKILL, 0, 0, 0)    # PR_SET_PDEATHSIG
    except Exception:
        pass
    if parent and os.getppid() != parent:
        os._exit(0)
    try:
        os.nice(10)
    except OSError:
        pass
    out = sys.stdout

    def progress(p: float) -> None:
        out.write(f"progress {float(p):.4f}\n")
        out.flush()

    sys.stdout = sys.stderr                     # a job's own prints must not be read as progress
    importlib.import_module(f"stream_service.batch_jobs.{kind}").run(args, progress)


if __name__ == "__main__":
    main(sys.argv[1:])
