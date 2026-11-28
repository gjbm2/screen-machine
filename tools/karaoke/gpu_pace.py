"""Lets Flask's karaoke worker slow a karaoke tool down while something plays live (routes/karaoke/pace.py).

install() after torch is imported: every module's forward then checks the clock, and after each RUN_S of work it
waits for the GPU to finish what it was given (so nothing is left queued) and sleeps long enough that the tool has
only ``share`` of the time. The share is read from the file SM_PACE_FILE names (the worker rewrites it each second;
re-read here every 0.5 s); no file, or 1.0: flat out. Never fails the tool.
"""
from __future__ import annotations

import os
import time

RUN_S = 0.1
MAX_SLEEP_S = 2.0
_installed = False


def install() -> None:
    global _installed
    path = os.environ.get("SM_PACE_FILE")
    if not path or _installed:
        return
    try:
        import torch
        if not torch.cuda.is_available():
            return
    except Exception:
        return
    st = {"t": time.monotonic(), "share": 1.0, "read": 0.0}

    def share() -> float:
        now = time.monotonic()
        if now - st["read"] >= 0.5:
            st["read"] = now
            try:
                with open(path) as f:
                    st["share"] = max(0.05, min(1.0, float(f.read().strip() or 1)))
            except (OSError, ValueError):
                st["share"] = 1.0
        return st["share"]

    def hook(_mod, _inp, _out):
        if time.monotonic() - st["t"] < RUN_S:
            return
        s = share()
        if s < 1.0:
            torch.cuda.synchronize()
            ran = time.monotonic() - st["t"]
            time.sleep(min(MAX_SLEEP_S, ran * (1.0 / s - 1.0)))
        st["t"] = time.monotonic()

    torch.nn.modules.module.register_module_forward_hook(hook)
    _installed = True
