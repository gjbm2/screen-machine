"""Fit stream_service/score/metre.py's weights (metre_weights.json) on annotated performances, and report accuracy.

    ~/.venvs/sm-score-ml/bin/python tools/score/fit_metre.py cache  --asap DIR [--jazz DIR] --out CACHE_DIR
    ~/.venvs/sm-score-ml/bin/python tools/score/fit_metre.py fit    --cache CACHE_DIR [--write]

``cache`` runs PM2S over 24 s windows (single time signature) of ASAP's performances (github.com/fosfrancesco/
asap-dataset, CC BY-NC-SA 4.0: time signatures, beats and downbeats annotated per performance; --per windows each) and,
weakly labelled 4-beat simple metre with swing, PiJAMA jazz .mid files (--jazz-per windows each, the newest 600
notes as live).  --device cuda runs it on an idle GPU from a CUDA torch venv (~/.venvs/sm-score-train).  Nothing from the datasets
goes into the repo: only the fitted weights.  ``fit`` scores every window's nine candidate bars (metre.py),
fits the bar weights (conditional logit over the candidates; jazz windows count any 2- or 4-beat candidate), the
compound logistic (the fallback) and the class network (sklearn's MLPClassifier, one hidden layer of 128 with early stopping, on
metre.window_features; sklearn is needed here only, the worker runs the exported weights in numpy), reports 5-fold
accuracy split by piece (one window, and a performance's windows combined) against PM2S's own metre rule, and with
``--write`` saves everything fitted on all the windows.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys
import time

import numpy as np

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from stream_service.score import metre as M  # noqa: E402
from stream_service.score import pm2s_net as P  # noqa: E402

BPB = {"4/4": 4, "2/4": 2, "6/8": 2, "3/4": 3, "3/8": 3, "9/8": 3, "2/2": 2, "12/16": 4, "12/8": 4, "6/4": 2,
       "3/2": 3, "4/8": 4, "4/2": 4, "6/16": 2}
COMPOUND = {"6/8", "9/8", "12/8", "12/16", "6/4", "6/16"}
WIN = 24.0


def note_seq(path: pathlib.Path) -> np.ndarray:
    from stream_service.piano_replay import read_midi
    on, out = {}, []
    for t, st, d1, d2 in read_midi(path.read_bytes()):
        k = st & 0xF0
        if k == 0x90 and d2 > 0:
            if d1 in on:
                out.append((d1, on[d1][0], t - on[d1][0], on[d1][1]))
            on[d1] = (t, d2)
        elif (k == 0x80 or k == 0x90) and d1 in on:
            t0, v = on.pop(d1)
            out.append((d1, t0, max(0.02, t - t0), v))
    out.sort(key=lambda r: r[1])
    return np.array(out, float)


def _record(net, w, s, **gt) -> dict:
    o = net(w)
    return dict(start=s, notes=w, beat=o["beat"], down=o["down"], hand=o["hand"], key=o["key"], ts=o["ts"], **gt)


def cache(a) -> None:
    net = P.PM2S(pathlib.Path(a.weights).expanduser(), threads=a.threads, device=a.device)
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(7)
    t0, n = time.time(), 0
    if a.asap:
        root = pathlib.Path(a.asap)
        ann = json.loads((root / "asap_annotations.json").read_text())
        for k in sorted(ann, reverse=a.reverse):
            dst = out / (k.replace("/", "__") + f".w{int(WIN)}.npz")
            if dst.exists():
                continue
            v = ann[k]
            ns = note_seq(root / k)
            ts = sorted((float(t), s, int(b)) for t, (s, b) in v["perf_time_signatures"].items())
            beats, downs = np.array(v["performance_beats"]), np.array(v["performance_downbeats"])
            starts = np.arange(ns[0, 1], ns[-1, 1] - WIN, WIN)
            if len(starts) > a.per:
                starts = np.sort(rng.choice(starts, a.per, replace=False))
            recs = []
            for s in starts:
                if any(s < x[0] < s + WIN for x in ts):
                    continue
                cur = ([x for x in ts if x[0] <= s + 1e-6] or ts)[-1]
                w = ns[(ns[:, 1] >= s) & (ns[:, 1] < s + WIN)].copy()
                if len(w) < 24:
                    continue
                w[:, 1] -= s
                recs.append(_record(net, w, s, gt_beats=beats[(beats >= s) & (beats < s + WIN)] - s,
                                    gt_downs=downs[(downs >= s) & (downs < s + WIN)] - s, gt_ts=cur[1]))
            np.savez_compressed(dst, recs=np.array(recs, dtype=object))
            n += len(recs)
    if a.jazz:
        for f in sorted(pathlib.Path(a.jazz).glob("*.mid")):
            dst = out / f"jazz__{f.stem}.w{int(WIN)}.npz"
            if dst.exists():
                continue
            ns = note_seq(f)
            recs = []
            for frac in np.linspace(0.1, 0.85, a.jazz_per):
                s = ns[0, 1] + frac * (ns[-1, 1] - ns[0, 1] - WIN)
                w = ns[(ns[:, 1] >= s) & (ns[:, 1] < s + WIN)].copy()[-600:]   # as live: the newest 600 notes
                if len(w) >= 24:
                    w[:, 1] -= s
                    recs.append(_record(net, w, s, gt_beats=None, gt_downs=None, gt_ts="jazz"))
            np.savez_compressed(dst, recs=np.array(recs, dtype=object))
            n += len(recs)
    print(f"cached {n} windows in {time.time() - t0:.0f} s")


def load(cache_dir: pathlib.Path) -> list[dict]:
    rows = []
    for f in sorted(cache_dir.glob(f"*.w{int(WIN)}.npz")):
        parts = f.name.split("__")
        group = f.name if parts[0] == "jazz" else "/".join(parts[:-1])      # a piece: its performances together
        for r in np.load(f, allow_pickle=True)["recs"]:
            r = dict(r)
            r["group"] = group
            r["file"] = f.name                                              # a performance
            rows.append(r)
    return rows


def prep(r: dict) -> dict | None:
    beats, downs = P.beat_pps(r["beat"], r["down"], r["notes"][:, 1])
    if len(beats) < 6:
        return None
    triple = float(np.mean(r["ts"] > 0.5))
    s = M.saliences(r["notes"], beats, r["down"])
    X = M.candidate_features(s, triple)
    d = dict(X=X, xs=M.subdivision_features(r["notes"], beats, triple),
             wf=M.window_features(r["notes"], beats, s, X, r["ts"], downs),
             group=r["group"], file=r["file"], jazz=r["gt_ts"] == "jazz")
    if d["jazz"]:
        d.update(target=[i for i, (L, _p) in enumerate(M.CANDIDATES) if L in (2, 4)], family="duple",
                 compound=False, L=4, level_ok=False)
        return d
    if r["gt_ts"] not in BPB:
        return None
    L = BPB[r["gt_ts"]]
    d.update(L=L, compound=r["gt_ts"] in COMPOUND, family="triple" if L == 3 else "duple")
    gb, gd = r["gt_beats"], r["gt_downs"]
    if len(gb) < 4:
        return None
    dist = np.abs(beats[:, None] - gb[None, :])
    mp, mg = dist.min(1) < 0.07, dist.min(0) < 0.07
    d["level_ok"] = bool(mp.mean() > 0.7 and mg.mean() > 0.7)        # PM2S's beats are the annotated beats
    if d["level_ok"]:
        isdown = np.array([len(gd) > 0 and np.min(np.abs(gd - gb[j])) < 1e-3 for j in dist.argmin(1)]) & mp
        if isdown.any():
            ph = collections.Counter(int(i) % L for i in np.nonzero(isdown)[0]).most_common(1)[0][0]
            d["target"] = [M.CANDIDATES.index((L, ph))]
    return d


def _adam(grad, w, steps, lr):
    m = np.zeros_like(w)
    v = np.zeros_like(w)
    for t in range(1, steps + 1):
        g = grad(w)
        m = 0.9 * m + 0.1 * g
        v = 0.999 * v + 0.001 * g * g
        w = w - lr * (m / (1 - 0.9 ** t)) / (np.sqrt(v / (1 - 0.999 ** t)) + 1e-8)
    return w


def fit_bar(data: list[dict], lam: float = 0.02) -> np.ndarray:
    X = np.stack([d["X"] for d in data])
    T = np.zeros((len(data), len(M.CANDIDATES)))
    for i, d in enumerate(data):
        T[i, d["target"]] = 1.0

    def grad(w):
        z = X @ w
        p = np.exp(z - z.max(1, keepdims=True))
        p /= p.sum(1, keepdims=True)
        q = p * T / (p * T).sum(1, keepdims=True)           # the posterior within the target set
        return ((p - q)[:, :, None] * X).sum((0, 1)) / len(data) + lam * w
    return _adam(grad, M._default_bar_weights(X.shape[2]), 600, 0.05)


def fit_sub(data: list[dict], lam: float = 0.01) -> np.ndarray:
    X = np.stack([d["xs"] for d in data])
    y = np.array([d["compound"] for d in data], float)

    def grad(w):
        p = 1.0 / (1.0 + np.exp(-(X @ w)))
        return X.T @ (p - y) / len(y) + lam * w
    return _adam(grad, np.zeros(X.shape[1]), 800, 0.05)


def _cls(d: dict) -> str:
    return ("T" if d["family"] == "triple" else "D") + ("c" if d["compound"] else "s")


def fit_net(data: list[dict]):
    from sklearn.neural_network import MLPClassifier
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    m = make_pipeline(StandardScaler(), MLPClassifier(hidden_layer_sizes=(128,), alpha=1e-1, max_iter=2000,
                                                      random_state=0, early_stopping=True))
    m.fit(np.stack([d["wf"] for d in data]), [_cls(d) for d in data])
    return m


def export_net(m) -> dict:
    sc, net = m[0], m[1]
    return {"mean": [round(float(x), 6) for x in sc.mean_], "scale": [round(float(x), 6) for x in sc.scale_],
            "coefs": [[[round(float(v), 6) for v in row] for row in W] for W in net.coefs_],
            "intercepts": [[round(float(v), 6) for v in b] for b in net.intercepts_],
            "classes": [str(c) for c in net.classes_]}


def evaluate_net(test: list[dict], m, c: collections.Counter) -> None:
    pr = m.predict_proba(np.stack([d["wf"] for d in test]))
    classes = list(m.classes_)
    by_file: dict = collections.defaultdict(list)
    for d, p in zip(test, pr):
        tag = "jazz" if d["jazz"] else "asap"
        guess = classes[int(np.argmax(p))]
        c[f"{tag}_n"] += 1
        c[f"{tag}_signature"] += guess == _cls(d)
        c[f"{tag}_family"] += guess[0] == _cls(d)[0]
        c[f"{tag}_compound"] += guess[1] == _cls(d)[1]
        by_file[d["file"]].append((d, p))
    for rows in by_file.values():
        tag = "jazzperf" if rows[0][0]["jazz"] else "asapperf"
        guess = classes[int(np.argmax(sum(np.log(p + 1e-3) for _d, p in rows)))]
        c[f"{tag}_n"] += 1
        c[f"{tag}_signature"] += guess == _cls(rows[0][0])
        c[f"{tag}_family"] += guess[0] == _cls(rows[0][0])[0]


def evaluate(test: list[dict], w: np.ndarray, ws: np.ndarray) -> collections.Counter:
    c: collections.Counter = collections.Counter()
    for d in test:
        i = int(np.argmax(d["X"] @ w))
        L = M.CANDIDATES[i][0]
        comp = float(d["xs"] @ ws) > 0
        fam_ok = ("triple" if L == 3 else "duple") == d["family"]
        tag = "jazz" if d["jazz"] else "asap"
        c[f"{tag}_n"] += 1
        c[f"{tag}_family"] += fam_ok
        c[f"{tag}_compound"] += comp == d["compound"]
        c[f"{tag}_signature"] += fam_ok and comp == d["compound"]
        if not d["jazz"]:
            c[f"{tag}_beats"] += L == d["L"]
            c[f"triple_n"] += d["family"] == "triple"
            c[f"triple_found"] += d["family"] == "triple" and L == 3
            if "target" in d:
                c["phase_n"] += 1
                c["phase_bars"] += i in d["target"]
    return c


def show(c: collections.Counter, name: str) -> None:
    out = []
    for k in sorted(c):
        if k.endswith("_n"):
            continue
        n = c[k.split("_")[0] + "_n"]
        out.append(f"{k} {c[k] / n:.2f}" if n else k)
    print(f"{name:8s}", "  ".join(out), {k: v for k, v in c.items() if k.endswith("_n")})


def fit(a) -> None:
    rows = load(pathlib.Path(a.cache))
    data = [d for d in (prep(r) for r in rows) if d is not None]
    print(f"{len(rows)} windows, {len(data)} usable ({sum(d['jazz'] for d in data)} jazz)")
    groups = sorted({d["group"] for d in data})
    np.random.default_rng(3).shuffle(groups)
    fitted, net, pm2s = collections.Counter(), collections.Counter(), collections.Counter()
    for k in range(5):
        held = set(groups[k::5])
        train = [d for d in data if d["group"] not in held]
        test = [d for d in data if d["group"] in held]
        fitted += evaluate(test, fit_bar([d for d in train if "target" in d]), fit_sub(train))
        evaluate_net(test, fit_net(train), net)
    for r in rows:
        if r["gt_ts"] != "jazz" and r["gt_ts"] not in BPB:
            continue
        beats, downs = P.beat_pps(r["beat"], r["down"], r["notes"][:, 1])
        if len(beats) < 6:
            continue
        ts, _ = P.metre(beats, downs, r["ts"])
        gfam = "duple" if r["gt_ts"] == "jazz" else "triple" if BPB[r["gt_ts"]] == 3 else "duple"
        tag = "jazz" if r["gt_ts"] == "jazz" else "asap"
        pm2s[f"{tag}_n"] += 1
        pm2s[f"{tag}_family"] += ("triple" if ts in ("3/4", "6/8") else "duple") == gfam
    show(pm2s, "PM2S")
    show(fitted, "linear")
    show(net, "network")
    if a.write:
        w = fit_bar([d for d in data if "target" in d])
        ws = fit_sub(data)
        M.WEIGHTS_PATH.write_text(json.dumps({
            "bar": [round(float(x), 5) for x in w], "sub": [round(float(x), 5) for x in ws],
            "mlp": export_net(fit_net(data)),
            "fitted": time.strftime("%Y-%m-%d"), "windows": len(data),
            "features": {"bar": list(M.CUES) + ["half", "rep", "rep2", "L2", "L3", "L4", "L2xT", "L3xT", "L4xT"],
                         "sub": ["third1", "third2", "half", "quarters", "thirds_share", "t1_over_t2", "triple",
                                 "bias"]},
            "source": "tools/score/fit_metre.py on ASAP (CC BY-NC-SA 4.0) + PiJAMA jazz windows, PM2S beats"},
            indent=1) + "\n")
        print("wrote", M.WEIGHTS_PATH)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("cache")
    c.add_argument("--asap")
    c.add_argument("--jazz")
    c.add_argument("--out", required=True)
    c.add_argument("--weights", default="~/.local/share/screen-machine/pm2s")
    c.add_argument("--threads", type=int, default=4)
    c.add_argument("--reverse", action="store_true", help="work from the end (a second process alongside)")
    c.add_argument("--per", type=int, default=3, help="windows per ASAP performance (non-overlapping, sampled)")
    c.add_argument("--jazz-per", type=int, default=2, help="windows per jazz performance")
    c.add_argument("--device", default="cpu", help="cuda for a quick batch on an idle GPU (a CUDA torch venv)")
    f = sub.add_parser("fit")
    f.add_argument("--cache", required=True)
    f.add_argument("--write", action="store_true")
    a = ap.parse_args()
    cache(a) if a.cmd == "cache" else fit(a)


if __name__ == "__main__":
    main()
