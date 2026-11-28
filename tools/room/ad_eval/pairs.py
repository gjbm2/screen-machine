"""Blind pairs: does a higher score mean a better show, by the owner's eye? (docs/AD_EVAL.md §4, step 1)

The owner watches the same moment of a song lit two ways, side by side in the 3D room with the song playing, not
knowing which is which, and picks the better one (or "same"). Each pick is kept. The report says how often each of the
eval's measures agrees with the owner: the ones that do are the ones to steer by.

    ~/.venvs/sm-show/bin/python tools/room/ad_eval/pairs.py make <run dir> [--vs <run dir>] [--n 12] [--batch NAME]
    ~/.venvs/sm-show/bin/python tools/room/ad_eval/pairs.py report [BATCH ...]

``make`` needs runs made with ``run.py --dmx`` (each replay's DMX frames beside its timeline). With one run, a song's
two draws (seeds) that score furthest apart are paired: the same code, so whatever differs is the dice, and the
score's view of which draw is better is put to the eye. With ``--vs``, the same song and draw from two runs (two
commits, or briefs on and off).

The moment: a stretch around a place where the song lifts (a section the key says arrives, or the climax), where the
two sides look most different. Moments where they look the same are left out: nothing to choose.

A batch lives in ~/.local/state/screen-machine/show/ad_pairs/<batch>/:
  pairs.json      what the page may see: each pair's song, start, length (no word of which side is which)
  clip-<n>.json   the two sides' DMX frames, base64, 20 a second
  key.json        which run and draw is on which side, with each side's scores (the show's API never serves it)
  picks.jsonl     the owner's picks, written by show_service/ad_pairs.py
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import pathlib
import random
import sys
from typing import Any, Optional

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import common  # noqa: E402

PAIRS = pathlib.Path.home() / ".local" / "state" / "screen-machine" / "show" / "ad_pairs"
BEFORE_S, CLIP_S = 6.0, 18.0          # a moment: this long before the line, this long in all
MIN_DIFF = 0.04                       # the two sides' mean difference (0..1) on the channels in use: below it, nothing to choose
GOALS = ("overall", "goals", "gate", "beat", "contour", "sections", "form", "contrast", "proportion", "arc", "peak",
         "release", "ending", "kit", "flourish", "stillness", "palette")


def _draws(run: pathlib.Path) -> dict[str, list[tuple[int, pathlib.Path]]]:
    """Each song's karaoke draws in a run that have their DMX: sid -> [(seed, timeline path)]."""
    out: dict[str, list[tuple[int, pathlib.Path]]] = {}
    for p in sorted(run.glob("*.karaoke*.json")):
        parts = p.name.split(".")
        if len(parts) == 3:
            seed = 0
        elif len(parts) == 4 and parts[2][:1] == "s" and parts[2][1:].isdigit():
            seed = int(parts[2][1:])
        else:
            continue
        if _dmx_of(p).is_file():
            out.setdefault(parts[0], []).append((seed, p))
    return out


def _dmx_of(tl_path: pathlib.Path) -> pathlib.Path:
    return tl_path.with_name(tl_path.name[:-len(".json")] + ".dmx.npz")


def _scores(tl_path: pathlib.Path) -> dict:
    s = json.loads(tl_path.read_text()).get("scores") or {}
    return {k: s.get(k) for k in GOALS if isinstance(s.get(k), (int, float))}


def _moments(ref: dict) -> list[float]:
    """Where the song lifts: the starts of sections the key says arrive, and the climax's start."""
    ts = [float(s["start_s"]) for s in ref.get("sections") or ()
          if s.get("arrive") not in (None, "none") and float(s.get("start_s") or 0.0) >= BEFORE_S]
    c = ref.get("climax") or [None]
    if isinstance(c[0], (int, float)) and c[0] >= BEFORE_S:
        ts.append(float(c[0]))
    return sorted(set(round(t, 2) for t in ts))


def _clip(z: Any, t0: float) -> Optional[np.ndarray]:
    d, f0, hop = z["dmx"], float(z["t0_s"]), float(z["hop_s"])
    i0 = int(round((t0 - f0) / hop))
    n = int(round(CLIP_S / hop))
    if i0 < 0 or i0 + n > len(d):
        return None
    return d[i0:i0 + n]


def _b64(frames: np.ndarray) -> list[str]:
    return [base64.b64encode(bytes(f)).decode() for f in frames]


def make(a: argparse.Namespace) -> int:
    run = pathlib.Path(a.run)
    other = pathlib.Path(a.vs) if a.vs else None
    ours, theirs = _draws(run), _draws(other) if other else None
    import reference
    lib = common.library()
    cands = []
    for sid, ds in ours.items():
        if theirs is not None:
            mine = dict(ds)
            th = dict(theirs.get(sid) or [])
            if 0 not in mine or 0 not in th:
                continue
            A, B = (run, 0, mine[0]), (other, 0, th[0])
        else:
            if len(ds) < 2:
                continue
            sc = sorted(((_scores(p).get("overall") or 0.0, k, p) for k, p in ds))
            (_, ka, pa), (_, kb, pb) = sc[0], sc[-1]
            A, B = (run, ka, pa), (run, kb, pb)
        try:
            ref = reference.load(sid)
        except Exception as e:                                  # noqa: BLE001
            print(f"  {sid}: no reference ({e})", file=sys.stderr)
            continue
        za, zb = np.load(_dmx_of(A[2])), np.load(_dmx_of(B[2]))
        best = None
        for t in _moments(ref):
            t0 = t - BEFORE_S
            ca, cb = _clip(za, t0), _clip(zb, t0)
            if ca is None or cb is None:
                continue
            used = (ca.std(0) > 0) | (cb.std(0) > 0) | (ca[0] != cb[0])      # the channels either side works
            if not used.any():
                continue
            diff = float(np.mean(np.abs(ca[:, used].astype(np.int16) - cb[:, used].astype(np.int16)))) / 255.0
            if best is None or diff > best[0]:
                best = (diff, t0, ca, cb)
        if best is None or best[0] < MIN_DIFF:
            continue
        cands.append((best[0], sid, best[1], best[2], best[3], A, B))
    rng = random.Random(a.seed)
    cands.sort(key=lambda c: -c[0])
    cands = cands[:a.n]
    rng.shuffle(cands)
    batch = a.batch or dt.datetime.now().strftime("%Y%m%d-%H%M")
    out = PAIRS / batch
    if out.exists() and any(out.iterdir()):
        print(f"batch {batch} exists: pick another --batch", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    public, key = [], []
    for n, (diff, sid, t0, ca, cb, A, B) in enumerate(cands, 1):
        swap = rng.random() < 0.5
        L, R = (B, A) if swap else (A, B)
        cl, cr = (cb, ca) if swap else (ca, cb)
        meta = common.song_meta(sid, lib)
        public.append({"n": n, "song": sid, "title": meta.get("title"), "artist": meta.get("artist"),
                       "t0_s": round(t0, 2), "dur_s": CLIP_S, "hop_s": float(np.load(_dmx_of(A[2]))["hop_s"]),
                       "guide": 1.0})
        common.write_json(out / f"clip-{n}.json", {"left": _b64(cl), "right": _b64(cr)})
        side = lambda s: {"run": s[0].name, "seed": s[1], "timeline": str(s[2]), "scores": _scores(s[2])}  # noqa: E731
        key.append({"n": n, "song": sid, "t0_s": round(t0, 2), "look_diff": round(diff, 3),
                    "left": side(L), "right": side(R)})
    common.write_json(out / "pairs.json", {"batch": batch, "made_at": dt.datetime.now().isoformat(timespec="seconds"),
                                           "kind": "versus" if other else "draws", "pairs": public})
    common.write_json(out / "key.json", {"batch": batch, "run": str(run), "vs": str(other) if other else None,
                                         "pairs": key})
    print(f"batch {batch}: {len(public)} pairs -> {out}  (open /lights/pairs)")
    return 0


def _picks(d: pathlib.Path) -> dict[int, str]:
    """The last pick for each pair."""
    out: dict[int, str] = {}
    p = d / "picks.jsonl"
    if p.is_file():
        for line in p.read_text().splitlines():
            try:
                o = json.loads(line)
                out[int(o["n"])] = str(o["pick"])
            except (ValueError, KeyError, TypeError):
                continue
    return out


def report(a: argparse.Namespace) -> int:
    names = a.batches or sorted(p.name for p in PAIRS.iterdir() if (p / "key.json").is_file()) if PAIRS.is_dir() else []
    agree: dict[str, list[int]] = {}
    same = decisive = 0
    for b in names:
        d = PAIRS / b
        key = {k["n"]: k for k in json.loads((d / "key.json").read_text())["pairs"]}
        for n, pick in _picks(d).items():
            k = key.get(n)
            if k is None:
                continue
            if pick == "same":
                same += 1
                continue
            if pick not in ("left", "right"):
                continue
            decisive += 1
            won, lost = k[pick]["scores"], k["right" if pick == "left" else "left"]["scores"]
            for g in GOALS:
                if g in won and g in lost and abs(won[g] - lost[g]) > 1e-6:
                    agree.setdefault(g, []).append(1 if won[g] > lost[g] else 0)
    print(f"{len(names)} batch(es): {decisive} picks with a winner, {same} 'same'")
    if not decisive:
        return 0
    print("how often the side the owner picked scores higher (50% = no better than a coin):")
    for g, xs in sorted(agree.items(), key=lambda kv: -sum(kv[1]) / len(kv[1])):
        k, n = sum(xs), len(xs)
        p = sum(__import__("math").comb(n, j) for j in range(k, n + 1)) / 2.0 ** n
        print(f"  {g:11s} {100.0 * k / n:5.1f}%  ({k} of {n}; by chance p {p:.3f})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make")
    m.add_argument("run")
    m.add_argument("--vs")
    m.add_argument("--n", type=int, default=12)
    m.add_argument("--batch")
    m.add_argument("--seed", type=int, default=0)
    r = sub.add_parser("report")
    r.add_argument("batches", nargs="*")
    a = ap.parse_args()
    return make(a) if a.cmd == "make" else report(a)


if __name__ == "__main__":
    sys.exit(main())
