#!/usr/bin/env python3
"""Bounded GPU-resident NVENC spike (PyNvVideoCodec 2.2.3 + CuPy), GPU 1 (Quadro P4000) only.  v2, 18 Oct.

Safety (owner rules, production box):
  * refuses to start if stream_service has any session on GPU 1, or any session not ended/failed;
  * a guard thread polls GET /api/sessions every 0.5 s and aborts the loop within ~1.5 s if one appears
    (a failed poll also aborts: we cannot prove the GPU is idle);
  * hard caps: --frames (<= 600) and a 55 s wall deadline from the first GPU op;
  * the fill kernel is one thread per pixel, no loops; nothing can spin.
Run with nice -n 19, CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES=1.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import statistics
import subprocess
import sys
import threading
import time
import urllib.request

SESSIONS_URL = "http://127.0.0.1:8702/api/sessions"
MY_GPU = 1
MAX_FRAMES = 600
DEADLINE_S = 55.0
WARM = 10  # frames excluded from the steady-state stats


def sessions_block_reason():
    """None if we may use GPU 1, else a reason string."""
    try:
        with urllib.request.urlopen(SESSIONS_URL, timeout=1.0) as r:
            body = json.loads(r.read().decode())
    except Exception as e:  # cannot prove idle -> do not run
        return f"sessions poll failed: {e!r}"
    sess = body.get("sessions", body) if isinstance(body, dict) else body
    for s in sess or []:
        st = str(s.get("state", "")).lower()
        if str(s.get("gpu")) == str(MY_GPU):
            return f"session on GPU {MY_GPU}: {s.get('session')} state={st}"
        if st not in ("ended", "failed"):
            return f"session live: {s.get('session')} gpu={s.get('gpu')} state={st}"
    return None


class Guard(threading.Thread):
    def __init__(self):
        super().__init__(name="session-guard", daemon=True)
        self.abort = threading.Event()
        self.reason = None
        self.stop = threading.Event()
        self.polls = 0

    def run(self):
        while not self.stop.is_set():
            r = sessions_block_reason()
            self.polls += 1
            if r:
                self.reason = r
                self.abort.set()
                return
            self.stop.wait(0.5)


class Counter(threading.Thread):
    """Pure-Python competitor: shows whether the main thread releases the GIL."""
    def __init__(self):
        super().__init__(name="gil-counter", daemon=True)
        self.n = 0
        self.stop = False

    def run(self):
        n = 0
        while not self.stop:
            n += 1
            if not (n & 255):
                self.n = n
        self.n = n


class SmiSampler:
    """nvidia-smi -lms 250 on GPU 1 (our own child), parsed after the run."""
    Q = "timestamp,utilization.gpu,utilization.encoder,clocks.video,clocks.sm,temperature.gpu,clocks_throttle_reasons.active"

    def __init__(self):
        self.p = subprocess.Popen(["nvidia-smi", "-i", "1", f"--query-gpu={self.Q}", "--format=csv,noheader,nounits",
                                   "-lms", "250"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)

    def finish(self):
        try:
            self.p.terminate()
            out, _ = self.p.communicate(timeout=3)
        except Exception:
            self.p.kill()
            out = ""
        rows = [r.split(", ") for r in out.strip().splitlines() if r.count(",") >= 6]
        def col(k):
            v = []
            for r in rows:
                try:
                    v.append(float(r[k]))
                except ValueError:
                    pass
            return v
        enc, sm, vclk, temp = col(2), col(1), col(3), col(5)
        return {"n": len(rows), "enc_util_pct": summ(enc), "sm_util_pct": summ(sm), "video_clock_mhz": summ(vclk),
                "temp_c": summ(temp), "throttle_reasons": sorted({r[6] for r in rows})}


FILL_SRC = r"""
extern "C" __global__ void fill_nv12(unsigned char* __restrict__ y, unsigned char* __restrict__ uv,
                                     const int w, const int h, const int f, const int noise) {
    const int x = blockIdx.x * blockDim.x + threadIdx.x;
    const int r = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || r >= h) return;
    unsigned int s = (unsigned int)(x * 1973u) ^ (unsigned int)(r * 9277u) ^ (unsigned int)(f * 26699u);
    s ^= s >> 13; s *= 0x5bd1e995u; s ^= s >> 15;                 /* cheap hash, no loop */
    int n = noise ? (int)(s % (unsigned int)(2 * noise + 1)) - noise : 0;
    int v = 16 + ((x / 4 + r / 4 + 6 * f) % 220) + n;
    y[(size_t)r * w + x] = (unsigned char)(v < 16 ? 16 : (v > 235 ? 235 : v));
    if (((x | r) & 1) == 0) {
        const int cy = r >> 1;
        uv[(size_t)cy * w + x]     = (unsigned char)(96 + ((x / 8 + 2 * f) % 64));
        uv[(size_t)cy * w + x + 1] = (unsigned char)(96 + ((cy / 4 + 3 * f) % 64));
    }
}
"""


class CAI:
    def __init__(self, shape, strides, ptr):
        self.__cuda_array_interface__ = {"shape": tuple(shape), "strides": tuple(strides),
                                         "data": (int(ptr), False), "typestr": "|u1", "version": 3}


class NV12Frame:
    """What PyNvVideoCodec wants for NV12 device input: .cuda() -> [luma CAI, interleaved chroma CAI]."""
    def __init__(self, cp, w, h):
        self.buf = cp.empty(w * h * 3 // 2, dtype=cp.uint8)
        self.ptr = self.buf.data.ptr
        self.luma_ptr, self.chroma_ptr = self.ptr, self.ptr + w * h
        self.cai = [CAI((h, w, 1), (w, 1, 1), self.luma_ptr), CAI((h // 2, w // 2, 2), (w, 2, 1), self.chroma_ptr)]

    def cuda(self):
        return self.cai


def nal_types(data: bytes, limit: int = 12):
    """HEVC NAL unit types in an Annex-B packet (start-code scan; only used on a few packets)."""
    out, i, n = [], 0, len(data)
    while i < n - 3 and len(out) < limit:
        j = data.find(b"\x00\x00\x01", i)
        if j < 0 or j + 3 >= n:
            break
        out.append((data[j + 3] >> 1) & 0x3F)
        i = j + 3
    return out


def pct(v, p):
    if not v:
        return None
    s = sorted(v)
    k = min(len(s) - 1, max(0, int(round(p / 100.0 * (len(s) - 1)))))
    return round(s[k], 3)


def summ(v):
    return {"p50": pct(v, 50), "p95": pct(v, 95), "p99": pct(v, 99), "max": round(max(v), 3) if v else None,
            "mean": round(statistics.fmean(v), 3) if v else None, "n": len(v)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--preset", default="P4")
    ap.add_argument("--tuning", default="ultra_low_latency")
    ap.add_argument("--frames", type=int, default=600)
    ap.add_argument("--fps", type=int, default=60)
    ap.add_argument("--bitrate", type=int, default=50_000_000)
    ap.add_argument("--mode", choices=["paced", "max"], default="paced")
    ap.add_argument("--idr-at", type=int, default=137)
    ap.add_argument("--gil-counter", action="store_true")
    ap.add_argument("--no-sync", action="store_true", help="skip stream sync after fill (rely on stream order)")
    ap.add_argument("--noise", type=int, default=6)
    ap.add_argument("--flush-each", action="store_true", help="EndEncode() after every Encode() (zero-delay probe)")
    ap.add_argument("--out", default="")
    ap.add_argument("--json", required=True)
    ap.add_argument("--extra", default="", help="extra encoder kv pairs k=v,k=v (probe)")
    ap.add_argument("--src-ctx", default="", help="tagged media file: pass its AVFormatContext (VUI colour route)")
    ap.add_argument("--no-smi", action="store_true")
    ap.add_argument("--start-at", type=float, default=0.0, help="epoch time to start the loop (2-process sync)")
    ap.add_argument("--reconf-at", type=int, default=-1, help="frame index at which to Reconfigure() (probe)")
    ap.add_argument("--reconf-fps", type=int, default=30)
    ap.add_argument("--reconf-bitrate", type=int, default=30_000_000)
    a = ap.parse_args()
    a.frames = min(a.frames, MAX_FRAMES)
    res = {"name": a.name, "args": vars(a), "pid": os.getpid(), "started": time.strftime("%Y-%m-%dT%H:%M:%S%z")}

    def dump():
        with open(a.json, "w") as f:
            json.dump(res, f, indent=1)

    r = sessions_block_reason()
    if r:
        res["refused"] = r
        dump()
        print("REFUSED:", r)
        return 3
    assert os.environ.get("CUDA_VISIBLE_DEVICES") == "1" and os.environ.get("CUDA_DEVICE_ORDER") == "PCI_BUS_ID"

    guard = Guard()
    guard.start()

    import cupy as cp
    import PyNvVideoCodec as nvc
    props = cp.cuda.runtime.getDeviceProperties(0)
    devname = props["name"].decode() if isinstance(props["name"], bytes) else str(props["name"])
    res["device"] = devname
    if "P4000" not in devname:
        res["refused"] = f"wrong device {devname}"
        dump()
        return 4
    t_first_gpu = time.monotonic()
    deadline = t_first_gpu + DEADLINE_S
    w, h = 3840, 2160
    cp.cuda.Device(0).use()
    stream = cp.cuda.Stream(non_blocking=True)
    ctx = cp.cuda.driver.ctxGetCurrent()
    mod = cp.RawModule(code=FILL_SRC)
    fill = mod.get_function("fill_nv12")
    frames = [NV12Frame(cp, w, h) for _ in range(3)]
    block = (32, 8, 1)
    grid = ((w + 31) // 32, (h + 7) // 8, 1)
    # warm-up: JIT/first launch outside the measured loop
    t0 = time.perf_counter()
    fill(grid, block, (frames[0].luma_ptr, frames[0].chroma_ptr, cp.int32(w), cp.int32(h), cp.int32(0),
                       cp.int32(a.noise)), stream=stream)
    stream.synchronize()
    res["fill_warmup_ms"] = round((time.perf_counter() - t0) * 1e3, 2)

    opts = dict(codec="hevc", preset=a.preset, tuning_info=a.tuning, rc="cbr", fps=str(a.fps), gop=str(a.fps),
                idrperiod=str(a.fps), bf="0", bitrate=str(a.bitrate), maxbitrate=str(a.bitrate),
                vbvbufsize=str(2 * a.bitrate), repeatspspps="1")
    for kv in filter(None, a.extra.split(",")):
        k, v = kv.split("=", 1)
        opts[k] = v
    demux = None
    if a.src_ctx:
        demux = nvc.CreateDemuxer(a.src_ctx)
        opts["source_av_format_context"] = str(int(demux.GetAVFormatInputContext()))
    res["encoder_opts"] = {k: v for k, v in opts.items() if k != "source_av_format_context"}
    t0 = time.perf_counter()
    try:
        enc = nvc.CreateEncoder(w, h, "NV12", False, cudacontext=int(ctx), cudastream=int(stream.ptr), **opts)
    except Exception as e:
        res["create_error"] = repr(e)
        dump()
        print("CREATE ERROR", repr(e))
        guard.stop.set()
        return 5
    res["create_ms"] = round((time.perf_counter() - t0) * 1e3, 2)
    try:
        res["num_bframes"] = enc.GetNumBFrames()
        seq = bytes(enc.GetSequenceParams())
        res["seq_params_len"] = len(seq)
        res["seq_params_hex"] = seq.hex()
    except Exception as e:
        res["seq_err"] = repr(e)

    out = open(a.out, "wb") if a.out else None
    md5 = hashlib.md5()
    counter = None
    gil = {}
    if a.gil_counter:
        counter = Counter()
        counter.start()
        time.sleep(0.2)
        c0 = counter.n; s0 = time.perf_counter(); time.sleep(0.5); c1 = counter.n; s1 = time.perf_counter()
        gil["baseline_rate_per_ms_main_sleeping"] = round((c1 - c0) / ((s1 - s0) * 1e3), 1)

    submit_t = {}
    pkt_log = []  # (frame_idx_in, ts_out, picture_type, size, t_out)
    nal_at = {}
    enc_wall, enc_cpu, fill_wall, frame_cpu_proc, lateness, gil_rates_enc, gil_rates_fill = [], [], [], [], [], [], []
    first_pkt_at_frame = None
    nsent = 0
    aborted = None
    flag_idr = int(nvc.NV_ENC_PIC_FLAGS.FORCEIDR.value) | int(nvc.NV_ENC_PIC_FLAGS.OUTPUT_SPSPPS.value)

    if a.start_at:
        while time.time() < a.start_at and not guard.abort.is_set() and time.monotonic() < deadline:
            time.sleep(0.01)
    smi = None if a.no_smi else SmiSampler()
    ru0 = resource.getrusage(resource.RUSAGE_SELF)
    tc0 = time.thread_time()
    loop_t0 = time.perf_counter()
    period = 1.0 / a.fps
    for i in range(a.frames):
        if guard.abort.is_set():
            aborted = guard.reason
            break
        if time.monotonic() > deadline:
            aborted = "deadline"
            break
        if a.mode == "paced":
            slot = loop_t0 + i * period
            now = time.perf_counter()
            if slot > now:
                time.sleep(slot - now)
            lateness.append((time.perf_counter() - slot) * 1e3)
        ru_a = resource.getrusage(resource.RUSAGE_SELF)
        fr = frames[i % len(frames)]
        cf = counter.n if counter else 0
        tf = time.perf_counter()
        fill(grid, block, (fr.luma_ptr, fr.chroma_ptr, cp.int32(w), cp.int32(h), cp.int32(i), cp.int32(a.noise)),
             stream=stream)
        if not a.no_sync:
            stream.synchronize()
        tfe = time.perf_counter()
        fill_wall.append((tfe - tf) * 1e3)
        if counter and tfe > tf:
            gil_rates_fill.append((counter.n - cf) / ((tfe - tf) * 1e3))
        if i == a.reconf_at:
            try:
                rp = enc.GetEncodeReconfigureParams()
                rp.frameRateNum, rp.frameRateDen = a.reconf_fps, 1
                rp.averageBitrate = rp.maxBitRate = a.reconf_bitrate
                rp.vbvBufferSize = 2 * a.reconf_bitrate
                tr = time.perf_counter()
                ok = enc.Reconfigure(rp)
                rp2 = enc.GetEncodeReconfigureParams()
                res["reconfigure"] = {"at": i, "returned": repr(ok), "ms": round((time.perf_counter() - tr) * 1e3, 2),
                                      "after": {k: str(getattr(rp2, k)) for k in dir(rp2) if not k.startswith("_")}}
            except Exception as e:
                res["reconfigure"] = {"at": i, "error": repr(e)}
            if a.mode == "paced":
                period_new = 1.0 / a.reconf_fps
                loop_t0 = loop_t0 + i * period - i * period_new  # keep slot(i) continuous at the new period
                period = period_new
        pp = nvc.NV_ENC_PIC_PARAMS()
        pp.inputTimeStamp = i
        if i == a.idr_at:
            pp.encodePicFlags = flag_idr
        c_before = counter.n if counter else 0
        tc = time.thread_time()
        te = time.perf_counter()
        submit_t[i] = te
        pk = enc.Encode(fr, pp)
        if a.flush_each:
            try:
                pk = list(pk) + list(enc.EndEncode())
            except Exception as e:
                res["flush_each_error"] = f"frame {i}: {e!r}"
                aborted = "flush_each_error"
                break
        t_after = time.perf_counter()
        enc_wall.append((t_after - te) * 1e3)
        enc_cpu.append((time.thread_time() - tc) * 1e3)
        if counter:
            dt = (t_after - te) * 1e3
            gil_rates_enc.append(((counter.n - c_before) / dt) if dt > 0 else 0.0)
        nsent += 1
        for p in pk:
            data = bytes(p["data"])
            ts = int(p["timestamp"])
            pt = int(p["picture_type"])
            pkt_log.append((i, ts, pt, len(data), t_after))
            if first_pkt_at_frame is None:
                first_pkt_at_frame = i
            if pt == 3 or ts in (1, a.idr_at, a.idr_at + 1):
                if len(nal_at) < 24:
                    nal_at[ts] = nal_types(data)
            md5.update(data)
            if out:
                out.write(data)
        ru_b = resource.getrusage(resource.RUSAGE_SELF)
        frame_cpu_proc.append(((ru_b.ru_utime + ru_b.ru_stime) - (ru_a.ru_utime + ru_a.ru_stime)) * 1e3)
    loop_t1 = time.perf_counter()
    tc1 = time.thread_time()
    ru1 = resource.getrusage(resource.RUSAGE_SELF)
    tflush = time.perf_counter()
    flush = enc.EndEncode()
    flush_ms = (time.perf_counter() - tflush) * 1e3
    tfl = time.perf_counter()
    for p in flush:
        data = bytes(p["data"])
        pkt_log.append((nsent, int(p["timestamp"]), int(p["picture_type"]), len(data), tfl))
        md5.update(data)
        if out:
            out.write(data)
    if out:
        out.close()
    if counter:
        counter.stop = True
    guard.stop.set()
    smi_res = smi.finish() if smi else None

    W = WARM if nsent > 3 * WARM else 0
    delays_frames = [fi - ts for (fi, ts, _, _, _) in pkt_log if fi < nsent]
    lat_ms = [(t - submit_t[ts]) * 1e3 for (fi, ts, _, _, t) in pkt_log if fi < nsent and ts in submit_t and ts >= W]
    elapsed = loop_t1 - loop_t0
    cpu_total_ms = ((ru1.ru_utime + ru1.ru_stime) - (ru0.ru_utime + ru0.ru_stime)) * 1e3
    ptypes = {}
    for (_, ts, pt, _, _) in pkt_log:
        ptypes.setdefault(pt, []).append(ts)
    bytes_total = sum(sz for (_, _, _, sz, _) in pkt_log)
    res.update({
        "aborted": aborted, "frames_sent": nsent, "packets_total": len(pkt_log),
        "packets_in_loop": sum(1 for x in pkt_log if x[0] < nsent),
        "packets_from_flush": len(flush), "flush_ms": round(flush_ms, 2),
        "flush_timestamps": [int(p["timestamp"]) for p in flush],
        "first_packet_at_frame_index": first_pkt_at_frame,
        "first_packet_timestamp": pkt_log[0][1] if pkt_log else None,
        "output_delay_frames": summ(delays_frames), "submit_to_packet_ms_steady": summ(lat_ms),
        "elapsed_s": round(elapsed, 3), "fps_achieved": round(nsent / elapsed, 2) if elapsed > 0 else None,
        "encode_call_wall_ms_steady": summ(enc_wall[W:]), "encode_call_thread_cpu_ms_steady": summ(enc_cpu[W:]),
        "encode_call_wall_ms_first10": [round(x, 2) for x in enc_wall[:10]],
        "fill_plus_sync_wall_ms_steady": summ(fill_wall[W:]), "proc_cpu_per_frame_ms_steady": summ(frame_cpu_proc[W:]),
        "proc_cpu_total_ms_per_frame_avg": round(cpu_total_ms / max(1, nsent), 3),
        "main_thread_cpu_ms_per_frame_avg": round((tc1 - tc0) * 1e3 / max(1, nsent), 3),
        "proc_cpu_cores_avg": round(cpu_total_ms / 1e3 / elapsed, 3) if elapsed > 0 else None,
        "pace_lateness_ms_steady": summ(lateness[W:]) if lateness else None,
        "pace_lateness_ms_first10": [round(x, 1) for x in lateness[:10]] if lateness else None,
        "picture_types": {str(k): {"count": len(v), "first_ts": v[:8]} for k, v in ptypes.items()},
        "idr_frames": sorted(set(ptypes.get(3, []))), "i_frames": sorted(set(ptypes.get(2, []))),
        "nal_types_at": {str(k): v for k, v in sorted(nal_at.items())},
        "bitrate_mbps": round(bytes_total * 8 / (nsent / a.fps) / 1e6, 2) if nsent else None,
        "pkt_size_bytes": summ([sz for (_, _, _, sz, _) in pkt_log]),
        "bitstream_md5": md5.hexdigest(), "bitstream_bytes": bytes_total,
        "nvidia_smi_gpu1": smi_res, "guard_polls": guard.polls,
        "wall_first_gpu_to_end_s": round(time.monotonic() - t_first_gpu, 2),
    })
    if counter:
        gil["rate_per_ms_during_encode_steady"] = summ(gil_rates_enc[W:])
        gil["rate_per_ms_during_fill_sync_steady"] = summ(gil_rates_fill[W:])
        gil["switchinterval_s"] = sys.getswitchinterval()
        res["gil"] = gil
    res["series"] = {"enc_wall_ms": [round(x, 3) for x in enc_wall], "enc_cpu_ms": [round(x, 3) for x in enc_cpu],
                     "lateness_ms": [round(x, 2) for x in lateness], "fill_ms": [round(x, 3) for x in fill_wall],
                     "proc_cpu_ms": [round(x, 3) for x in frame_cpu_proc]}
    dump()
    keys = ("name", "aborted", "frames_sent", "fps_achieved", "first_packet_at_frame_index", "output_delay_frames",
            "submit_to_packet_ms_steady", "encode_call_wall_ms_steady", "encode_call_thread_cpu_ms_steady",
            "proc_cpu_total_ms_per_frame_avg", "idr_frames", "bitrate_mbps", "bitstream_md5")
    print(json.dumps({k: res[k] for k in keys}))
    if smi_res:
        print("SMI", json.dumps(smi_res))
    if counter:
        print("GIL", json.dumps(gil))
    del enc
    return 0


if __name__ == "__main__":
    sys.exit(main())
