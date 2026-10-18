"""STR-R1 reporting (docs/perf/PROGRAMME.md W0.1, W1.6): the HUD's guard line reads the guard's frame GPU time,
"render" is reported as "GPU span", diag reads NVML (once initialised, no forks) and falls back to nvidia-smi,
and hevc-2160p30 carries NVENC delay 1."""
from __future__ import annotations

import sys
import time
from types import SimpleNamespace

import pytest

from stream_service import diag as dg
from stream_service import hud as H
from stream_service.encode import encoder as enc_mod
from stream_service.profiles import PROFILES

from .test_cue import AUTO, live
from .test_sessions import client  # noqa: F401 (fixture)


# -- HUD ------------------------------------------------------------------------
async def test_hud_guard_line_shows_frame_gpu_ms(client):
    _, s, eng = await live(client, auto=AUTO)
    eng.root = None                                                  # the fake engine has no layer tree
    eng.guard_stats = lambda: {"samples": 9, "trips": 0, "backlog": 0, "frame_gpu_ms": 7.3, "budget_ms": 50.0,
                               "enabled": True, "slow_trips": 0}
    eng.window_stats = lambda seconds=2.0: {"frames": 60, "render_ms": {"p50": 5.0, "p95": 6.0}, "budget_ms": 33.3,
                                            "late_pct": 0.0, "mbps": 20.0}
    lines, _ = H.build_lines(s, time.monotonic())
    guard = next(x for x in lines if x.startswith("guard "))
    assert guard.startswith("guard 7.3/33.3 ms  trips 0")
    assert any(x.startswith("gpu span 5.0/6.0 ms") for x in lines)
    eng.guard_stats = lambda: {"samples": 0, "trips": 0, "frame_gpu_ms": None}     # no samples yet: a dash
    lines, _ = H.build_lines(s, time.monotonic())
    assert next(x for x in lines if x.startswith("guard ")).startswith("guard -/33.3")


# -- diag: NVML and the fallback --------------------------------------------------
class FakeNvml:
    NVML_TEMPERATURE_GPU, NVML_CLOCK_SM = 0, 1

    def __init__(self, fail_init=False, fail_temp=False):
        self.inits, self.fail_init, self.fail_temp = 0, fail_init, fail_temp

    def nvmlInit(self):
        self.inits += 1
        if self.fail_init:
            raise RuntimeError("NVML Shared Library Not Found")

    def nvmlDeviceGetCount(self):
        return 2

    def nvmlDeviceGetHandleByIndex(self, i):
        return i

    def nvmlDeviceGetPciInfo(self, h):
        return SimpleNamespace(bus=(23, 101)[h])

    def nvmlDeviceGetUtilizationRates(self, h):
        return SimpleNamespace(gpu=40 + h)

    def nvmlDeviceGetEncoderUtilization(self, h):
        return [10 + h, 200000]

    def nvmlDeviceGetMemoryInfo(self, h):
        return SimpleNamespace(used=512 << 20, total=8192 << 20)

    def nvmlDeviceGetTemperature(self, h, kind):
        if self.fail_temp:
            raise RuntimeError("Not Supported")
        return 60 + h

    def nvmlDeviceGetClockInfo(self, h, kind):
        return 1500

    def nvmlDeviceGetMaxClockInfo(self, h, kind):
        return 1708


DEVICES = [{"index": 0, "name": "RTX 4060", "pci_bus": 23}, {"index": 1, "name": "P4000", "pci_bus": 101}]


@pytest.fixture
def fresh(monkeypatch):
    monkeypatch.setattr(dg, "_nvml", None)
    monkeypatch.setattr(dg, "_gpu_cache", (0.0, []))
    forks = []

    def run(cmd, **kw):
        forks.append(cmd)
        return SimpleNamespace(stdout="00000000:17:00.0, 30, 5, 600, 8188, 58, 2505, 3105\n"
                                      "00000000:65:00.0, 2, 0, 200, 8192, [N/A], 139, 1708\n")
    monkeypatch.setattr(dg.subprocess, "run", run)
    return forks


def _expire(monkeypatch):
    monkeypatch.setattr(dg, "_gpu_cache", (0.0, dg._gpu_cache[1]))


def test_diag_uses_nvml_once_and_never_forks(fresh, monkeypatch):
    nv = FakeNvml()
    monkeypatch.setitem(sys.modules, "pynvml", nv)
    g = dg.gpus(DEVICES)
    assert g[0] == {"index": 0, "name": "RTX 4060", "util": 40, "enc_util": 10, "mem_used_mb": 512,
                    "mem_total_mb": 8192, "temp_c": 60, "sm_mhz": 1500, "sm_max_mhz": 1708}
    assert g[1]["util"] == 41 and g[1]["enc_util"] == 11
    for _ in range(3):
        _expire(monkeypatch)
        dg.gpus(DEVICES)
    assert nv.inits == 1 and fresh == []


def test_diag_nvml_field_failure_is_none_not_a_fork(fresh, monkeypatch):
    monkeypatch.setitem(sys.modules, "pynvml", FakeNvml(fail_temp=True))
    g = dg.gpus(DEVICES)
    assert g[0]["temp_c"] is None and g[0]["util"] == 40 and fresh == []


@pytest.mark.parametrize("mod", [None, FakeNvml(fail_init=True)])   # not installed / cannot initialise
def test_diag_falls_back_to_nvidia_smi(fresh, monkeypatch, mod):
    monkeypatch.setitem(sys.modules, "pynvml", mod)
    g = dg.gpus(DEVICES)
    assert g[0]["util"] == 30 and g[0]["enc_util"] == 5 and g[0]["sm_mhz"] == 2505 and g[0]["temp_c"] == 58
    assert g[1]["util"] == 2 and g[1]["temp_c"] is None and g[1]["sm_max_mhz"] == 1708
    assert len(fresh) == 1 and dg._nvml is False
    _expire(monkeypatch)
    dg.gpus(DEVICES)
    assert len(fresh) == 2                                           # NVML is not retried: one fork per refresh


def test_diag_no_nvml_no_smi_reports_nothing_without_raising(fresh, monkeypatch):
    monkeypatch.setitem(sys.modules, "pynvml", None)

    def missing(*a, **kw):
        raise FileNotFoundError("nvidia-smi")
    monkeypatch.setattr(dg.subprocess, "run", missing)
    g = dg.gpus(DEVICES)
    assert [x["index"] for x in g] == [0, 1] and all(x[k] is None for x in g for k in dg.GPU_KEYS)


def _win(**over):
    w = {"frames": 60, "budget_ms": 33.3, "render_ms": {"p50": 40.0, "p95": 45.0}, "write_ms": {"p95": 1.0},
         "encode_ms": {"p50": 5.0}, "encode_queue": 0, "late_pct": 0}
    return w | over


def test_verdict_names_the_gpu_span():
    h = {"cpus": 16, "load": [1.0, 1.0, 1.0]}
    sess = [{"session": "s1", "profile": "hevc-2160p30", "gpu": 1, "window": _win()}]
    v = dg.verdict(h, [], sess)
    assert v["level"] == "bad" and v["text"].startswith("GPU span over budget: p95 45.0 ms")
    sess[0]["window"] = _win(gpu_span_ms={"p95": 50.0})                 # the new key wins when present
    assert dg.verdict(h, [], sess)["text"].startswith("GPU span over budget: p95 50.0 ms")
    sess[0]["window"] = _win(render_ms={"p95": 10.0})
    assert dg.verdict(h, [], sess)["level"] == "ok"


# -- W1.6: NVENC delay on hevc-2160p30 only ----------------------------------------
def test_p30_profile_carries_delay_1_and_others_are_unchanged():
    want = {"hevc-2160p60": 2, "hevc-2160p30": 1, "hevc-1080p60": 0, "hevc-1080p30": 0, "h264-720p30": 0,
            "av1-720p30": 0}
    assert {k: p.nvenc_delay for k, p in PROFILES.items()} == want
    assert PROFILES["hevc-2160p30"].describe()["nvenc_delay_frames"] == 1


def test_p30_delay_reaches_ffmpeg(monkeypatch):
    seen = []

    def popen(cmd, **kw):
        seen.append(cmd)
        raise OSError("not spawned in tests")
    monkeypatch.setattr(enc_mod.subprocess, "Popen", popen)
    p = PROFILES["hevc-2160p30"]
    with pytest.raises(OSError):
        enc_mod.NvencPipeline(p.codec, p.w, p.h, p.fps, preset=p.preset, extra_args=p.encoder_args(),
                              delay=p.nvenc_delay, clock=time.monotonic, on_config=None, on_frame=None,
                              on_exit=None)
    cmd = seen[0]
    assert cmd[cmd.index("-delay") + 1] == "1" and cmd[cmd.index("-c:v") + 1] == "hevc_nvenc"
