"""make_thumbs: render a one-tile thumbnail for every registered plug-in that has none yet.

  ~/.venvs/sm-stream/bin/python -m stream_service.tools.make_thumbs [--all] [--shard=i/n] [names...]

Writes ~/.cache/sm-fxtest/out/<name>/sheet.png (what the /dev Library shows).  Scenes: a frame after ~2 s with
energy 0.6.  Transitions: progress 0.5 between two reference scenes.  Effects: amount 0.8 over a reference
scene.  One process, GPU 1, shares the fxtest slot lock; a plug-in that fails is skipped and reported.
"""
from __future__ import annotations

import sys
import traceback

from stream_service.tools import fxtest as fx
from stream_service.plugins import Registry

W, H, FPS = 480, 270, 30


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    redo = "--all" in sys.argv
    reg = Registry().discover()
    from stream_service.gpu import RenderContext
    from PIL import Image
    todo = []
    for kind, table in (("source", reg.sources), ("transition", reg.transitions),
                        ("effect", getattr(reg, "effects", {}))):
        for name, cls in sorted(table.items()):
            if getattr(cls, "internal", False) or (args and name not in args):
                continue
            if not redo and (fx.WORK / "out" / name / "sheet.png").exists():
                continue
            todo.append((kind, name, cls))
    shard = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--shard=")), None)
    if shard:   # --shard=i/n: only every n-th item, for running a few processes side by side
        i, n = (int(x) for x in shard.split("/"))
        todo = todo[i::n]
    print(f"{len(todo)} thumbnails to make", flush=True)
    ok = bad = 0
    with fx.gpu_slots(False):
        ctx = RenderContext(fx.GPU, W, H, FPS, prealloc_buffers=0)
        with ctx.device, ctx.stream:
            cp = ctx.cp
            A, B = fx.ref_scene(ctx, "A"), fx.ref_scene(ctx, "B")
            a, b, out = ctx.new_frame(), ctx.new_frame(), ctx.new_frame()
            for i in range(20):
                A.render(i / FPS, 1 / FPS, a)
                B.render(i / FPS, 1 / FPS, b)
            for kind, name, cls in todo:
                try:
                    if kind == "source":
                        s = cls()
                        p = cls.validate({k: v for k, v in {"energy": 0.6}.items() if k in cls.param_props()})
                        s.setup(ctx, p)
                        for i in range(60):
                            s.render(i / FPS, 0.0 if i == 0 else 1 / FPS, out)
                    elif kind == "transition":
                        t = cls(cls.validate({}))
                        t.setup(ctx)
                        t.render(0.5, a, b, out)
                    else:
                        e = cls()
                        e.setup(ctx, cls.validate({k: v for k, v in {"amount": 0.8}.items()
                                                   if k in cls.param_props()}))
                        for i in range(15):
                            e.render(i / FPS, 1 / FPS, a, out)
                    ctx.stream.synchronize()
                    d = fx.WORK / "out" / name
                    d.mkdir(parents=True, exist_ok=True)
                    Image.fromarray(cp.asnumpy(out)[:, :, :3]).save(d / "sheet.png")
                    ok += 1
                    print(f"ok   {kind:10} {name}", flush=True)
                except Exception as e:  # noqa: BLE001 - report and carry on
                    bad += 1
                    print(f"FAIL {kind:10} {name}: {type(e).__name__}: {e}", flush=True)
                    traceback.print_exc(limit=1, file=sys.stdout)
    print(f"done: {ok} made, {bad} failed", flush=True)


if __name__ == "__main__":
    main()
