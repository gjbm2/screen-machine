"""A job that only takes time: for trying the batch queue (and its tests).  args: {"s": seconds, "fail"?: message}."""
import time


def run(args: dict, progress) -> None:
    s, steps = float(args.get("s", 1.0)), 20
    for i in range(steps):
        time.sleep(s / steps)
        progress((i + 1) / steps)
    if args.get("fail"):
        raise RuntimeError(str(args["fail"]))
    if args.get("touch"):
        open(args["touch"], "a").close()
