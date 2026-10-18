#!/usr/bin/env python3
"""cudabench.py v2.1 - bounded host-side cost of CuPy CUDA calls. The same file runs in WSL2 (GPU-PV: libcuda via /dev/dxg)
and natively on Windows 11 (nvcuda.dll, WDDM), so the two can be compared like for like on GPU 0 (RTX 4060) only.

Bounds (owner rule: GPU hangs never; production box):
  - fixed iteration caps AND a time cap per sub-phase; trivial kernels (1,024 floats, one add per thread, no loops);
  - phase S (sleep lateness) runs BEFORE cupy is imported: no CUDA context exists, hard cap SLEEP_BUDGET_S;
  - phase G (all GPU work) has a hard wall budget GPU_BUDGET_S from cupy import to exit;
  - an abort file is checked at least every ~0.5 s (a watcher outside creates it if a stream_service session appears);
  - the competing CPU thread has its own hard time cap;
  - nice 19 in WSL; BELOW_NORMAL priority class on Windows, set here before anything else.
Only GPU 0: CUDA_DEVICE_ORDER=PCI_BUS_ID + CUDA_VISIBLE_DEVICES=0, then checked by name ("4060") and count (1).

usage: cudabench.py --out result.json [--abort-file PATH] [--pid-file PATH] [--tag NAME]
"""
import argparse, json, os, platform, random, sys, threading, time

os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"          # before cupy / cuInit
os.environ["CUDA_VISIBLE_DEVICES"] = "0"                # the RTX 4060 only; the P4000 is not visible
WANT_NAME = "4060"
GPU_BUDGET_S = 30.0
SLEEP_BUDGET_S = 28.0
WIN = os.name == "nt"

ap = argparse.ArgumentParser()
ap.add_argument("--out", required=True)
ap.add_argument("--abort-file", default=None)
ap.add_argument("--pid-file", default=None)
ap.add_argument("--tag", default="")
args = ap.parse_args()
os.environ.setdefault("CUPY_CACHE_DIR", os.path.join(os.path.dirname(os.path.abspath(args.out)),
                                                     "cupy_cache_" + ("win" if WIN else "wsl")))

# ---- priority first ----
if WIN:
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.SetPriorityClass.restype = wintypes.BOOL
    k32.GetPriorityClass.argtypes = [wintypes.HANDLE]
    k32.GetPriorityClass.restype = wintypes.DWORD
    k32.SetPriorityClass(k32.GetCurrentProcess(), 0x00004000)
    got = k32.GetPriorityClass(k32.GetCurrentProcess())
    if got != 0x00004000:
        raise SystemExit(f"refusing: priority class is {hex(got)}, not BELOW_NORMAL")
    prio = "BELOW_NORMAL (0x4000)"
else:
    os.nice(19 - os.nice(0))
    prio = f"nice {os.nice(0)}"
if args.pid_file:
    with open(args.pid_file, "w") as f:
        f.write(str(os.getpid()))

pc = time.perf_counter
T_START = pc()
BUDGET = {"t0": T_START, "lim": SLEEP_BUDGET_S, "name": "sleep budget"}


class Abort(Exception):
    pass


def check():
    if pc() - BUDGET["t0"] > BUDGET["lim"]:
        raise Abort(BUDGET["name"])
    if args.abort_file and os.path.exists(args.abort_file):
        raise Abort("abort file")


def pct(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(round(p / 100 * (len(v) - 1))))]


def st(ts, scale=1e6, over=1e-3):
    x = [t * scale for t in ts]
    return {"n": len(x), "p50": round(pct(x, 50), 2), "p90": round(pct(x, 90), 2), "p99": round(pct(x, 99), 2),
            "max": round(max(x), 2), "mean": round(sum(x) / len(x), 2),
            "frac_over_1ms": round(sum(1 for t in ts if t > over) / len(ts), 4)}


def timer_res():
    """Windows global timer resolution (100 ns units -> ms), read-only via ntdll.NtQueryTimerResolution."""
    if not WIN:
        return None
    mn, mx, cur = wintypes.ULONG(), wintypes.ULONG(), wintypes.ULONG()
    ctypes.windll.ntdll.NtQueryTimerResolution(ctypes.byref(mn), ctypes.byref(mx), ctypes.byref(cur))
    return {"coarsest_ms": mn.value / 1e4, "finest_ms": mx.value / 1e4, "current_ms": cur.value / 1e4}


def cpu_ref():
    """pure-Python CPU yardstick (no GPU, no syscalls): normalises for vCPU vs LP speed and clock."""
    out = []
    for _ in range(7):
        t = pc(); s = 0
        for i in range(200_000):
            s += i
        out.append(pc() - t)
    return round(pct(out, 50) * 1e3, 3)


class Hog:
    """A CPU-bound Python thread competing for the GIL, hard-capped at `cap` seconds."""
    vals = [random.random() for _ in range(4096)]

    def __init__(self, cap):
        self.cap = cap; self.stop = threading.Event(); self.iters = 0; self.t_end = None

    def _run(self):
        t_end = pc() + self.cap
        while not self.stop.is_set() and pc() < t_end:
            s2 = sorted(self.vals); m = 0.0
            for v in s2[:512]:
                m += v
            self.iters += 1
        self.t_end = pc()

    def alive(self):
        return self.th.is_alive()

    def __enter__(self):
        self.th = threading.Thread(target=self._run, daemon=True); self.th.start(); time.sleep(0.05)
        return self

    def __exit__(self, *a):
        self.stop.set(); self.th.join(5)


def tagged(fn, hog=None):
    """run fn(); record whether the competitor was alive at start and end (so a dead hog cannot hide)."""
    a0 = hog.alive() if hog else None
    out = fn()
    if hog:
        out["hog_alive_start_end"] = [a0, hog.alive()]
    return out


def sleeps(n, tmax, dt=0.004):
    late = []; t0 = pc()
    for i in range(n):
        if i % 10 == 0:
            check()
            if pc() - t0 > tmax:
                break
        t = pc(); time.sleep(dt); late.append(pc() - t - dt)
    o = st(late, 1e3, over=1e-3); o["n_cap"] = n
    return o


res = {"tag": args.tag, "platform": platform.platform(), "python": sys.version.split()[0], "priority": prio,
       "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "switchinterval": sys.getswitchinterval(), "aborted": None,
       "timer_res_start": timer_res(), "cpu_ref_ms_start": cpu_ref()}
try:
    # ================= phase S: sleep lateness, no CUDA context =================
    S = res["sleep4ms_lateness_ms"] = {}
    S["alone"] = sleeps(2000, 20.0)
    with Hog(cap=4.0) as h:
        S["with_cpu_thread"] = tagged(lambda: sleeps(300, 2.5), h)
    if WIN:
        winmm = ctypes.windll.winmm
        winmm.timeBeginPeriod(1)
        try:
            res["timer_res_hires"] = timer_res()
            S["alone_timeBeginPeriod1"] = sleeps(300, 2.0)
        finally:
            winmm.timeEndPeriod(1)
    res["sleep_phase_s"] = round(pc() - T_START, 2)
except Abort as e:
    res["aborted"] = str(e)
    if str(e) == "sleep budget":                 # a non-GPU phase ran long: note it; the GPU phase has its own budget
        res["sleep_phase_overran"] = True; res["aborted"] = None

if res["aborted"] is None:
    t_imp = pc()
    import numpy as np
    import cupy as cp
    import cupyx
    BUDGET.update(t0=pc(), lim=GPU_BUDGET_S, name="gpu budget")   # from first CUDA call (import makes no context)
    res.update({"cupy": cp.__version__, "numpy": np.__version__, "import_s": round(pc() - t_imp, 3)})
    try:
        res["cuda_runtime"] = cp.cuda.runtime.runtimeGetVersion()
        res["cuda_driver"] = cp.cuda.runtime.driverGetVersion()
        res["visible_devices"] = cp.cuda.runtime.getDeviceCount()
        props = cp.cuda.runtime.getDeviceProperties(0)
        name = props["name"].decode() if isinstance(props["name"], bytes) else props["name"]
        try:
            bus = cp.cuda.runtime.deviceGetPCIBusId(0)
        except Exception as e:                                 # noqa: BLE001
            bus = f"n/a ({e})"
        res["device"] = {"ordinal": 0, "name": name, "bus": bus}
        if WANT_NAME not in name or res["visible_devices"] != 1:
            raise SystemExit(f"refusing: visible device 0 is {name!r} ({res['visible_devices']} visible)")
        check()

        t_ctx = pc()
        s = cp.cuda.Stream(non_blocking=True)
        s_empty = cp.cuda.Stream(non_blocking=True)
        ek = cp.ElementwiseKernel("float32 x", "float32 y", "y = x + 1.0f", "sm_addone")
        rk = cp.RawKernel(r'extern "C" __global__ void rk(float* a, int n){int i=blockIdx.x*blockDim.x+threadIdx.x;'
                          r' if(i<n) a[i]+=1.0f;}', "rk")
        NB = 3840 * 2160 * 3 // 2                       # 12,441,600 B = one 4K NV12 frame
        n1024 = np.int32(1024)
        with s:
            a = cp.zeros(1024, cp.float32)
            b = cp.zeros(512, cp.float32)              # 2 KB
            host_pageable = np.zeros(512, np.float32)
            host_pinned = cupyx.empty_pinned((512,), np.float32)
            big_d = cp.zeros(NB, cp.uint8)
            big_h = cupyx.empty_pinned((NB,), np.uint8)
            ev = [cp.cuda.Event() for _ in range(8)]
            for i in range(60):                        # warm-up, bounded
                if i % 20 == 0:
                    check()
                ev[i % 8].record(s); ek(a, a); rk((4,), (256,), (a, n1024)); b.set(host_pageable)
                b.data.copy_from_host_async(host_pinned.ctypes.data, 2048, s); s_empty.synchronize()
            big_d.data.copy_to_host_async(big_h.ctypes.data, NB, s); s.synchronize()
        res["setup_s"] = round(pc() - t_ctx, 3)

        graph = None                                    # CUDA graph of the same 50 tiny raw kernels
        try:
            gs = cp.cuda.Stream(non_blocking=True)
            with gs:
                gs.begin_capture()
                for _ in range(50):
                    rk((4,), (256,), (a, n1024))
                graph = gs.end_capture()
            if hasattr(graph, "upload"):
                graph.upload(gs)
            graph.launch(gs); gs.synchronize()
            res["graph"] = "ok"
        except Exception as e:                             # noqa: BLE001
            res["graph"] = f"failed: {e!r}"; graph = None

        def cpu_times():
            o = os.times(); return time.thread_time(), o.user, o.system

        def cpu_split(c0, n):
            c1 = cpu_times(); u, k = c1[1] - c0[1], c1[2] - c0[2]
            return {"thread_cpu_per_iter_us": round((c1[0] - c0[0]) / n * 1e6, 2),
                    "proc_user_per_iter_us": round(u / n * 1e6, 2), "proc_sys_per_iter_us": round(k / n * 1e6, 2),
                    "proc_sys_share": round(k / max(1e-9, u + k), 3)}

        def per_op(n, tmax):
            r = {k: [] for k in ("perf_counter_null", "event_record", "elementwise_launch", "rawkernel_launch",
                                 "set_2KB_pageable", "h2d_2KB_pinned_async", "sync_empty_stream")}
            t0 = pc(); c0 = cpu_times(); done = 0
            with s:
                for i in range(n):
                    if i % 10 == 0:
                        check()
                        if pc() - t0 > tmax:
                            break
                    t = pc(); r["perf_counter_null"].append(pc() - t)
                    t = pc(); ev[i % 8].record(s); r["event_record"].append(pc() - t)
                    t = pc(); ek(a, a); r["elementwise_launch"].append(pc() - t)
                    t = pc(); rk((4,), (256,), (a, n1024)); r["rawkernel_launch"].append(pc() - t)
                    t = pc(); b.set(host_pageable); r["set_2KB_pageable"].append(pc() - t)
                    t = pc(); b.data.copy_from_host_async(host_pinned.ctypes.data, 2048, s)
                    r["h2d_2KB_pinned_async"].append(pc() - t)
                    t = pc(); s_empty.synchronize(); r["sync_empty_stream"].append(pc() - t)
                    done += 1
                    if i % 50 == 49:
                        s.synchronize()
                s.synchronize()
            o = {k: st(v) for k, v in r.items()}
            o["iters"] = done; o["n_cap"] = n; o["loop_wall_per_iter_us"] = round((pc() - t0) / max(1, done) * 1e6, 1)
            o["cpu"] = cpu_split(c0, max(1, done))
            return o

        def d2h(n, tmax):
            enq, tot = [], []; t0 = pc()
            with s:
                for i in range(n):
                    if i % 10 == 0:
                        check()
                        if pc() - t0 > tmax:
                            break
                    t = pc(); big_d.data.copy_to_host_async(big_h.ctypes.data, NB, s); t1 = pc()
                    s.synchronize(); t2 = pc()
                    enq.append(t1 - t); tot.append(t2 - t)
            o = {"enqueue_us": st(enq), "enqueue_plus_sync_ms": st(tot, 1e3, over=5e-3), "n_cap": n}
            o["GBps_p50"] = round(NB / pct(tot, 50) / 1e9, 2)
            return o

        def frames(n, kind, tmax):
            walls, enq, gpu = [], [], []
            e0, e1 = cp.cuda.Event(), cp.cuda.Event()
            stream = gs if kind == "graph" else s
            t0 = pc(); c0 = cpu_times(); done = 0
            with stream:
                for f in range(n):
                    if f % 2 == 0:
                        check()
                        if pc() - t0 > tmax:
                            break
                    t = pc()
                    e0.record(stream)
                    if kind == "elementwise":
                        for _ in range(50):
                            ek(a, a)
                    elif kind == "rawkernel":
                        for _ in range(50):
                            rk((4,), (256,), (a, n1024))
                    elif kind == "mixed":              # render-thread shape: 40 launches, 6 small uploads, 3 events
                        for j in range(40):
                            rk((4,), (256,), (a, n1024))
                            if j % 7 == 0:
                                b.set(host_pageable)
                            if j % 15 == 0:
                                ev[j % 8].record(stream)
                    else:
                        graph.launch(stream)
                    e1.record(stream); t1 = pc()
                    e1.synchronize(); t2 = pc()
                    walls.append(t2 - t); enq.append(t1 - t); done += 1
                    gpu.append(cp.cuda.get_elapsed_time(e0, e1) / 1e3)
            o = {"frame_wall_ms": st(walls, 1e3, over=5e-3), "frame_enqueue_ms": st(enq, 1e3, over=5e-3),
                 "gpu_span_ms": st(gpu, 1e3, over=5e-3), "frames": done, "n_cap": n}
            o["cpu"] = cpu_split(c0, max(1, done))
            return o

        kinds = ["elementwise", "rawkernel", "mixed"] + (["graph"] if graph is not None else [])
        # ---------------- A: one thread ----------------
        A = res["alone"] = {}
        A["per_op"] = per_op(2000, 2.5)
        for k in kinds:
            A["frame50_" + k] = frames(300, k, 1.2)
        A["d2h_12_4MB_pinned"] = d2h(100, 1.2)
        # ---------------- B: plus one CPU-bound Python thread (GIL competition) ----------------
        B = res["with_cpu_thread"] = {}
        tB = pc()
        with Hog(cap=14.0) as h:
            B["per_op"] = tagged(lambda: per_op(300, 3.0), h)
            for k in [x for x in kinds if x != "elementwise"]:
                B["frame50_" + k] = tagged(lambda: frames(30, k, 2.0), h)
            B["d2h_12_4MB_pinned"] = tagged(lambda: d2h(20, 1.0), h)
        B["phase_s"] = round(pc() - tB, 2); B["hog_iters"] = h.iters
        # ---------------- C (Windows only): same competitor, 1 ms timer resolution for this process ----------------
        if WIN:
            winmm.timeBeginPeriod(1)
            try:
                C = res["with_cpu_thread_timeBeginPeriod1"] = {"timer_res": timer_res()}
                with Hog(cap=6.0) as h:
                    C["per_op"] = tagged(lambda: per_op(300, 2.5), h)
                    C["frame50_mixed"] = tagged(lambda: frames(30, "mixed", 2.0), h)
            finally:
                winmm.timeEndPeriod(1)
        res["cpu_ref_ms_end"] = cpu_ref()
    except Abort as e:
        res["aborted"] = str(e)
    res["gpu_phase_s"] = round(pc() - BUDGET["t0"], 2)
res["timer_res_end"] = timer_res()
res["elapsed_s"] = round(pc() - T_START, 2)
with open(args.out, "w") as f:
    json.dump(res, f, indent=1)
print(json.dumps({"tag": args.tag, "elapsed_s": res["elapsed_s"], "gpu_phase_s": res.get("gpu_phase_s"),
                  "aborted": res["aborted"], "device": res.get("device"), "priority": prio, "graph": res.get("graph")}))
