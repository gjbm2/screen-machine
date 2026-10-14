"""Render fractal_zoom stills to PNG, and check that the zoom loop is seamless.

  ~/.venvs/sm-stream/bin/python -m stream_service.tools.fractal_preview --gpu 1 --size 1280x720 \
      --params '{"preset": "rabbit"}' --u 0 0.5 --out /tmp/previews

  --seam: renders u = 0 and u = 1 - eps with identical rotation/drift and reports the mean absolute
  difference (the self-similarity identity says they should be near-identical), saving both PNGs and a
  10x amplified difference image.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import time

import numpy as np


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gpu", type=int, default=1)
    ap.add_argument("--size", default="1280x720")
    ap.add_argument("--params", default="{}")
    ap.add_argument("--u", type=float, nargs="*", default=[0.0])
    ap.add_argument("--seam", action="store_true")
    ap.add_argument("--bench", type=int, default=0, help="time N draws")
    ap.add_argument("--out", default=".")
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    from PIL import Image

    from stream_service.gpu import RenderContext
    from stream_service.plugins.sources.fractal_zoom import FractalZoom

    w, h = (int(v) for v in args.size.lower().split("x"))
    out_dir = pathlib.Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    ctx = RenderContext(args.gpu, w, h, 60, prealloc_buffers=0)
    cp = ctx.cp
    with ctx.device, ctx.stream:
        src = FractalZoom()
        src.setup(ctx, FractalZoom.validate(json.loads(args.params)))
        src.advance(0.0)
        geo = src.geometry
        print(f"c={geo.c} point={geo.point} z*={geo.zstar:.4f} lam={geo.lam:.4f} |lam|={abs(geo.lam):.4f} "
              f"arg={np.degrees(np.angle(geo.lam)):.1f}deg N={geo.n} fallback={geo.fallback}")
        frame = ctx.new_frame()
        tag = args.tag or "_".join(f"{k}-{v}" for k, v in json.loads(args.params).items()) or "default"

        def save(u: float, name: str) -> np.ndarray:
            src.draw(frame, u=u, theta=0.0, drift=0.0)
            img = cp.asnumpy(frame)[:, :, :3]
            Image.fromarray(img).save(out_dir / name)
            return img

        for u in args.u:
            save(u, f"fz_{tag}_u{u:.3f}.png")
        if args.seam:
            a = save(0.0, f"fz_{tag}_seam_u0.png").astype(np.int16)
            b = save(1.0 - 1e-6, f"fz_{tag}_seam_u1.png").astype(np.int16)
            d = np.abs(a - b)
            Image.fromarray(np.clip(d * 10, 0, 255).astype(np.uint8)).save(out_dir / f"fz_{tag}_seam_diff10.png")
            print(f"seam: mean |diff| {d.mean():.3f}/255, p99 {np.percentile(d, 99):.1f}, "
                  f"pixels > 8: {(d.max(axis=2) > 8).mean() * 100:.3f}%")
            mid = save(0.5, f"fz_{tag}_seam_u0.5.png").astype(np.int16)
            # one frame at 60 fps with the current zoom rate: the change a viewer sees every frame anyway
            du = abs(src.easer.values()["zoom_rate"]) / 60 / src.geometry.log_lam.real
            nxt = save(du, f"fz_{tag}_seam_next.png").astype(np.int16)
            dn = np.abs(a - nxt)
            print(f"   for scale: consecutive frames (du={du:.4f}) mean |diff| {dn.mean():.3f}/255, "
                  f"pixels > 8: {(dn.max(axis=2) > 8).mean() * 100:.3f}%; u=0 vs u=0.5 mean |diff| "
                  f"{np.abs(a - mid).mean():.3f}/255")
        if args.bench:
            ev0, ev1 = cp.cuda.Event(), cp.cuda.Event()
            ev0.record()
            t0 = time.perf_counter()
            for i in range(args.bench):
                src.draw(frame, u=(i / args.bench) % 1.0)
            ev1.record()
            ev1.synchronize()
            print(f"bench: {cp.cuda.get_elapsed_time(ev0, ev1) / args.bench:.2f} ms/frame GPU "
                  f"({(time.perf_counter() - t0) / args.bench * 1000:.2f} ms wall) at {w}x{h}")


if __name__ == "__main__":
    main()
