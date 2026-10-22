#!/usr/bin/env python3
"""Is the ENCODED stream smooth? (isolates generator + encoder from decode/display problems)

Connects to the lab server as an extra viewer, records N seconds of the live fMP4 stream to a file, decodes it
with ffmpeg (software, on the workstation) and measures, frame by frame:
  * source pacing: the capture-time step between frames (should be exactly 1000/fps ms);
  * frame-id continuity (no frames missing from the stream);
  * the white sweep bar's position in the DECODED pictures: it moves a fixed number of pixels per frame, so a
    constant step means the encoded video is smooth; 0 = repeated frame, 2x = skipped frame.
Usage (lab venv with websockets + numpy):  python tools/stream_smoothness.py [ws://127.0.0.1:8081/ws] [seconds]
"""
import asyncio, json, os, struct, subprocess, sys, tempfile, time
import numpy as np
import websockets

FRAME_HDR = struct.Struct("<BBIdddI")
MSG_INIT, MSG_FRAME = 1, 2


async def record(url, seconds, path):
    meta, frames, init = None, [], None
    async with websockets.connect(url, max_size=None) as ws:
        await ws.send(json.dumps({"type": "hello", "role": "viewer", "mode": "mse",
                                  "info": {"tool": "stream_smoothness"}}))
        t_end = None
        with open(path, "wb") as f:
            while True:
                msg = await ws.recv()
                if isinstance(msg, str):
                    m = json.loads(msg)
                    if m.get("type") == "config":
                        meta = m
                    continue
                if msg[0] == MSG_INIT:
                    if init is None:
                        init = msg[1:]
                        f.write(init)
                        t_end = time.monotonic() + seconds
                    continue
                if msg[0] == MSG_FRAME and init is not None:
                    _, key, fid, t_cap, t_enc, t_send, _ = FRAME_HDR.unpack_from(msg)
                    if not frames and not key:
                        continue  # start at a keyframe so the file decodes from frame 0
                    f.write(msg[FRAME_HDR.size:])
                    frames.append((fid, t_cap, key))
                    if time.monotonic() > t_end:
                        break
    return meta, frames


def bar_positions(path, w, h):
    """x of the full-height white bar in each decoded frame (luma only, sampled rows)."""
    cmd = ["ffmpeg", "-v", "error", "-i", path, "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    rows = np.linspace(int(h * 0.15), int(h * 0.80), 40).astype(int)  # avoid barcode (top) and clock box (bottom)
    xs, n = [], w * h
    while True:
        buf = p.stdout.read(n)
        if len(buf) < n:
            break
        y = np.frombuffer(buf, np.uint8).reshape(h, w)[rows]
        col_min = y.min(axis=0)                      # a full-height bar is bright in every sampled row
        bright = np.flatnonzero(col_min > 200)
        xs.append(int(bright[0]) if bright.size else -1)
    p.wait()
    return xs


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "ws://127.0.0.1:8081/ws"
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 15
    path = os.path.join(tempfile.gettempdir(), "stream_smoothness.mp4")
    meta, frames = asyncio.run(record(url, seconds, path))
    fps, w, h = meta["fps"], meta["width"], meta["height"]
    ids = [f[0] for f in frames]
    caps = np.diff([f[1] for f in frames])
    missing = sum(b - a - 1 for a, b in zip(ids, ids[1:]) if b - a > 1)
    print(f"recorded {len(frames)} frames ({meta['codec']} {w}x{h}@{fps}, {os.path.getsize(path) / 1e6:.1f} MB) -> {path}")
    print(f"stream: frame ids {ids[0]}..{ids[-1]}, missing from stream: {missing}")
    print(f"source pacing (capture step, ms): median {np.median(caps):.2f}  p1 {np.percentile(caps, 1):.2f}  "
          f"p99 {np.percentile(caps, 99):.2f}  max {caps.max():.2f}  (ideal {1000 / fps:.2f})")
    xs = bar_positions(path, w, h)
    d = np.diff([x for x in xs if x >= 0])
    step = int(np.median(d[d > 0])) if (d > 0).any() else 0
    wrap = d < -w // 2                                # bar wrapped to the left edge
    good = d[~wrap]
    bad = good[np.abs(good - step) > 2]
    print(f"decoded {len(xs)} pictures; bar step median {step} px/frame; frames off-step: {bad.size} "
          f"(repeats {np.sum(good == 0)}, skips {np.sum(np.abs(good - 2 * step) <= 2)}), not found {xs.count(-1)}")
    print("VERDICT:", "encoded stream is SMOOTH" if bad.size == 0 and missing == 0 else "encoded stream has irregular motion")


if __name__ == "__main__":
    main()
