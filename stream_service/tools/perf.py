"""Performance run: one session with a live scene for N seconds, measured from the service's per-frame records.

  ~/.venvs/sm-stream/bin/python -m stream_service.tools.perf --gpu 0 --profile hevc-2160p60 --seconds 60

Connects a draining media client (so fan-out is included), goes live, waits for the in transition, then
measures a window of exactly --seconds: late (repeated) frames, schedule slip, GPU render ms (render tree +
RGBA->NV12, CUDA events), host enqueue ms, NV12 pipe write ms, encoder latency (write done -> packet out),
encoder throughput and bitrate.  Prints one JSON object.  Never prints the token.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import time

import aiohttp

from stream_service.stats import pct


def summarise(vals: list[float]) -> dict:
    vals = [v for v in vals if v is not None]
    if not vals:
        return {}
    return {"p50": round(pct(vals, 50), 3), "p95": round(pct(vals, 95), 3), "p99": round(pct(vals, 99), 3),
            "max": round(max(vals), 3), "n": len(vals)}


async def run(args) -> dict:
    token = pathlib.Path(args.token_file).expanduser().read_text().strip()
    H = {"Authorization": f"Bearer {token}"}
    base = args.base.rstrip("/")
    async with aiohttp.ClientSession() as http:
        body = {"screen": "perf", "gpu": args.gpu, "profile": args.profile, "anchor": {"rgb": [20, 20, 20]},
                "scene": json.loads(args.scene) if args.scene.startswith("{") else args.scene,
                "transitions": {"in": "crossfade-1s", "out": "crossfade-1s"}, "ttl_s": args.seconds + 120}
        r = await http.post(f"{base}/api/sessions", json=body, headers=H)
        if r.status != 201:
            raise SystemExit(f"create failed: {r.status} {await r.text()}")
        res = await r.json()
        sid = res["session"]
        rx = {"frames": 0, "bytes": 0, "keys": 0, "last": None, "gaps": 0}

        async def drain():
            async with http.ws_connect(res["attach"]["url"], max_msg_size=64 << 20) as ws:
                async for m in ws:
                    if m.type == aiohttp.WSMsgType.BINARY and m.data[0] == 0x02:
                        fid = int.from_bytes(m.data[2:6], "big")
                        if rx["last"] is not None and fid != rx["last"] + 1:
                            rx["gaps"] += 1
                        rx["last"] = fid
                        rx["frames"] += 1
                        rx["bytes"] += len(m.data)
                        rx["keys"] += m.data[1]

        drainer = asyncio.create_task(drain()) if args.drain else None
        try:
            for _ in range(100):
                st = await (await http.get(f"{base}/api/sessions/{sid}", headers=H)).json()
                if st["state"] == "holding":
                    break
                if st["state"] in ("failed", "ended"):
                    raise SystemExit(f"session {st['state']}: {st.get('error')}")
                await asyncio.sleep(0.1)
            await http.post(f"{base}/api/sessions/{sid}/go", json={}, headers=H)
            await asyncio.sleep(args.warmup)
            st0 = await (await http.get(f"{base}/api/sessions/{sid}", headers=H)).json()
            f0, t0 = st0["frames"], time.monotonic()
            rx0 = dict(rx)
            await asyncio.sleep(args.seconds)
            st1 = await (await http.get(f"{base}/api/sessions/{sid}", headers=H)).json()
            t1 = time.monotonic()
            rx1 = dict(rx)
            recs = (await (await http.get(f"{base}/api/sessions/{sid}/frames?since={f0 - 1}",
                                          headers=H)).json())["frames"]
            recs = [r for r in recs if r["n"] < st1["frames"]]
            state = await (await http.get(f"{base}/api/state", headers=H)).json()
        finally:
            await http.delete(f"{base}/api/sessions/{sid}", headers=H)
            if drainer:
                drainer.cancel()
    wall = t1 - t0
    if args.dump:
        pathlib.Path(args.dump).write_text(json.dumps(recs))
    n = len(recs)
    repeats = sum(1 for r in recs if r["repeat"])
    fps = st1["fps"]
    late = sum(1 for r in recs if r["slip_ms"] > 1000 / fps)
    sizes = [r["size"] for r in recs if r.get("size")]
    return {
        "gpu": args.gpu, "gpu_name": next((g["name"] for g in state["gpus"] if g["index"] == args.gpu), None),
        "gpu_mem_used_mb": next((g["mem_used_mb"] for g in state["gpus"] if g["index"] == args.gpu), None),
        "profile": args.profile, "scene": args.scene, "window_s": round(wall, 2),
        "frames": n, "expected_frames": round(wall * fps), "late_frames": late,
        "late_pct": round(100 * late / max(n, 1), 3), "repeats": repeats,
        "frames_over_2_periods_late": sum(1 for r in recs if r["slip_ms"] > 2000 / fps),
        "resyncs": st1["stats"]["resyncs"] - st0["stats"]["resyncs"],
        "slip_ms": summarise([r["slip_ms"] for r in recs]),
        "render_gpu_ms": summarise([r["render_ms"] for r in recs if not r["repeat"]]),
        "copy_d2h_ms": summarise([r["copy_ms"] for r in recs if not r["repeat"]]),
        "host_enqueue_ms": summarise([r["host_ms"] for r in recs if not r["repeat"]]),
        "slot_wait_ms": summarise([r.get("wait_ms") for r in recs]),
        "nv12_write_ms": summarise([r["write_ms"] for r in recs]),
        "encode_latency_ms": summarise([r["encode_ms"] for r in recs]),
        "encoder_fps": round(sum(1 for r in recs if r.get("size")) / wall, 2),
        "bitrate_mbps": round(sum(sizes) * 8 / 1e6 / (len(sizes) / fps), 2) if sizes else None,
        "client_frames_per_s": round((rx1["frames"] - rx0["frames"]) / wall, 2) if args.drain else None,
        "client_gaps": rx1["gaps"] - rx0["gaps"] if args.drain else None,
        "server_client_drops": [c["dropped"] for c in st1.get("client_stats", [])],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8082")
    ap.add_argument("--token-file", default="~/.config/screen-machine/stream_service.token")
    ap.add_argument("--gpu", type=int, default=1)
    ap.add_argument("--profile", default="hevc-2160p60")
    ap.add_argument("--scene", default="fractal-zoom", help="preset name or a JSON spec")
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--warmup", type=float, default=4.0)
    ap.add_argument("--no-drain", dest="drain", action="store_false")
    ap.add_argument("--dump", default=None, help="write the window's raw per-frame records (JSON) here")
    args = ap.parse_args()
    print(json.dumps(asyncio.run(run(args)), indent=1))


if __name__ == "__main__":
    main()
