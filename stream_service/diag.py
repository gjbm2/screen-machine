"""Real-time diagnostics for the dev console (GET /api/diag, polled at ~2 Hz): host load, GPU and NVENC
utilisation, per-session timing over the last 2 s, the client's latest stats, and a one-line bottleneck verdict.

Cheap by construction: the GPU query is cached for 1 s and runs in a worker thread.  It uses NVML (nvidia-ml-py,
initialised once, lazily); only if NVML cannot load or initialise does it fall back to one ``nvidia-smi`` call per
refresh.  Per-session numbers come from the engine's in-memory frame records.

The window's ``render_ms`` is the GPU span of the frame (CUDA events around the render), not host render time:
it is reported as "GPU span".
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from typing import Any, Optional

GPU_CACHE_S = 1.0
_gpu_lock = threading.Lock()
_gpu_cache: tuple[float, list[dict]] = (0.0, [])


_nvml_lock = threading.Lock()
_nvml: Any = None           # None: not tried yet; False: NVML unavailable (nvidia-smi fallback); else the module


def _nvml_mod() -> Any:
    """The initialised pynvml module, or False if it cannot load or initialise.  Tried once."""
    global _nvml
    with _nvml_lock:
        if _nvml is None:
            try:
                import pynvml  # type: ignore

                pynvml.nvmlInit()
                _nvml = pynvml
            except Exception:
                _nvml = False
        return _nvml


def _nv(fn: Any, *a: Any) -> Any:
    """One NVML read; None if this field is not supported or fails (the others still report)."""
    try:
        return fn(*a)
    except Exception:
        return None


def _query_nvml(nv: Any) -> list[dict]:
    out = []
    for i in range(nv.nvmlDeviceGetCount()):
        h = nv.nvmlDeviceGetHandleByIndex(i)
        pci = _nv(nv.nvmlDeviceGetPciInfo, h)
        if pci is None:
            continue
        util = _nv(nv.nvmlDeviceGetUtilizationRates, h)
        enc = _nv(nv.nvmlDeviceGetEncoderUtilization, h)
        mem = _nv(nv.nvmlDeviceGetMemoryInfo, h)
        out.append({"pci_bus": int(pci.bus), "util": None if util is None else util.gpu,
                    "enc_util": None if enc is None else enc[0],
                    "mem_used_mb": None if mem is None else mem.used // (1 << 20),
                    "mem_total_mb": None if mem is None else mem.total // (1 << 20),
                    "temp_c": _nv(nv.nvmlDeviceGetTemperature, h, nv.NVML_TEMPERATURE_GPU),
                    "sm_mhz": _nv(nv.nvmlDeviceGetClockInfo, h, nv.NVML_CLOCK_SM),
                    "sm_max_mhz": _nv(nv.nvmlDeviceGetMaxClockInfo, h, nv.NVML_CLOCK_SM)})
    return out


def _query_smi() -> list[dict]:
    try:
        res = subprocess.run(["nvidia-smi", "--query-gpu=pci.bus_id,utilization.gpu,utilization.encoder,memory.used,"
                              "memory.total,temperature.gpu,clocks.sm,clocks.max.sm", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=3).stdout
    except Exception:
        return []

    def num(x: str) -> Optional[int]:
        try:
            return int(float(x))
        except ValueError:
            return None
    out = []
    for line in res.strip().splitlines():
        f = [x.strip() for x in line.split(",")]
        try:
            bus = int(f[0].split(":")[-2], 16)
        except (ValueError, IndexError):
            continue
        f += [""] * (8 - len(f))
        util, enc, used, total, temp, sm, smx = (num(x) for x in f[1:8])
        out.append({"pci_bus": bus, "util": util, "enc_util": enc, "mem_used_mb": used, "mem_total_mb": total,
                    "temp_c": temp, "sm_mhz": sm, "sm_max_mhz": smx})
    return out


def _query_gpus() -> list[dict]:
    """Per-GPU stats keyed by PCI bus: NVML when it works (no fork), else nvidia-smi."""
    nv = _nvml_mod()
    if nv:
        try:
            return _query_nvml(nv)
        except Exception:
            return []       # NVML is up but this read failed: report nothing this second, never fork
    return _query_smi()


GPU_KEYS = ("util", "enc_util", "mem_used_mb", "mem_total_mb", "temp_c", "sm_mhz", "sm_max_mhz")


def gpus(devices: list[dict]) -> list[dict]:
    """[{index, name, util, enc_util, mem_used_mb, mem_total_mb, temp_c, sm_mhz, sm_max_mhz}] in CUDA (PCI bus)
    order, cached 1 s.  A field NVML cannot read is None."""
    global _gpu_cache
    with _gpu_lock:
        t, cached = _gpu_cache
        if time.monotonic() - t < GPU_CACHE_S:
            return cached
        by_bus = {g["pci_bus"]: g for g in _query_gpus()}
        res = []
        for d in devices:
            g = by_bus.get(d.get("pci_bus"), {})
            res.append({"index": d["index"], "name": d["name"],
                        **{k: g.get(k) for k in GPU_KEYS}})
        _gpu_cache = (time.monotonic(), res)
        return res


def host() -> dict:
    load = os.getloadavg()
    return {"load": [round(x, 2) for x in load], "cpus": os.cpu_count() or 1}


def verdict(h: dict, gpu_list: list[dict], sessions: list[dict]) -> dict:
    """The most likely bottleneck, worst first -> {level: ok|warn|bad, text}."""
    cpus = h["cpus"]
    load1 = h["load"][0]
    if load1 >= cpus:
        return {"level": "bad", "text": f"host CPU saturated (load {load1:.1f}/{cpus})"}
    for s in sessions:
        w = s.get("window") or {}
        if not w.get("frames"):
            continue
        budget = w["budget_ms"]
        tag = f"{s['session']} ({s['profile']})"
        span = w.get("gpu_span_ms") or w.get("render_ms") or {}     # render_ms is the GPU span (old key)
        r95, w95 = (span.get("p95") or 0), (w["write_ms"]["p95"] or 0)
        e50 = w["encode_ms"]["p50"] or 0
        gpu = next((g for g in gpu_list if g["index"] == s.get("gpu")), {})
        if r95 > budget:
            return {"level": "bad", "text": f"GPU span over budget: p95 {r95:.1f} ms > {budget:.1f} ms ({tag})"}
        if w95 > budget:
            return {"level": "bad", "text": f"pipe write over budget: p95 {w95:.1f} ms > {budget:.1f} ms ({tag})"}
        enc_budget = (w.get("nvenc_delay_frames", 0) + 2) * budget
        if e50 > enc_budget or (gpu.get("enc_util") or 0) >= 95 or w["encode_queue"] > 6:
            return {"level": "bad", "text": f"encoder over budget: p50 {e50:.1f} ms, NVENC "
                                            f"{gpu.get('enc_util')} %, queue {w['encode_queue']} ({tag})"}
        c = s.get("client") or {}
        if c and ((c.get("buffered_ms") is not None and c["buffered_ms"] < 80)
                  or (c.get("dropped_per_s") or 0) > 0.05 * s.get("fps", 30)):
            return {"level": "bad", "text": f"network/client buffer starved: buffered {c.get('buffered_ms')} ms, "
                                            f"dropped {c.get('dropped_per_s')}/s ({tag})"}
        if (w.get("late_pct") or 0) > 2:
            return {"level": "warn", "text": f"{w['late_pct']} % of frames late; no single stage over budget ({tag})"}
    if load1 >= 0.8 * cpus:
        return {"level": "warn", "text": f"OK, but the host is busy (load {load1:.1f}/{cpus})"}
    return {"level": "ok", "text": "OK"}


def client_stats(session: Any, now: float) -> Optional[dict]:
    """The glass client's latest stats, plus dropped/s since the previous diag poll."""
    stats = [c.stats for c in session.media.clients.values() if c.stats]
    if not stats:
        return None
    st = max(stats, key=lambda x: x.get("presented") or 0)
    prev = getattr(session, "_diag_prev", None)
    rate = None
    dropped = st.get("dropped")
    if prev is not None and dropped is not None and prev[1] is not None and now > prev[0]:
        rate = round(max(0.0, dropped - prev[1]) / (now - prev[0]), 2)
    if prev is None or now - prev[0] >= 1.0:
        session._diag_prev = (now, dropped)
    return {k: st.get(k) for k in ("presented", "dropped", "buffered_ms", "edge_jumps", "stalls")} | \
        {"dropped_per_s": rate}
