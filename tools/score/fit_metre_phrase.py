"""Fit metre.py's phrase network (metre_weights.json "phrase"): the metre class over a phrase's held pulse (at any
level of it), from live-like sequences of annotated performances.

    ~/.venvs/sm-score-train/bin/python tools/score/fit_metre_phrase.py cache --asap DIR --out SEQ_DIR [--device cuda]
    ~/.venvs/sm-score-train/bin/python tools/score/fit_metre_phrase.py cache --pijama ZIP --out SEQ_DIR
    ~/.venvs/sm-score-train/bin/python tools/score/fit_metre_phrase.py cache --midi DIR --labels LABELS.json --out SEQ_DIR
    ~/.venvs/sm-score-ml/bin/python   tools/score/fit_metre_phrase.py rows SEQ_DIR ... --out ROWS.pkl
    ~/.venvs/sm-score-ml/bin/python   tools/score/fit_metre_phrase.py fit ROWS.pkl [--exclude REGEX] [--write]

``cache``: per performance one stretch of up to 46 s inside one time signature from its first note, and the
builder's analysis requests over it (the newest 12 s of notes, at most 600, unreleased notes cut at the request,
one a second; ``--step 2`` for big sets), PM2S's per-note outputs for each (a GPU when there is one; GPU 0 is the
desktop's, use CUDA_VISIBLE_DEVICES).  Sources: ASAP (fosfrancesco/asap-dataset: time signatures, beats annotated),
PiJAMA jazz (weakly 4/4), or any MIDI files with a labels JSON {file: {"sig": "3/4", "composition": ...}} (e.g.
PianoCoRe performances labelled by their PDMX score's time signature).
``rows``: per request (every 3rd), the phrase as the worker keeps it (metre.Phrase: the held grid, the notes' PM2S
outputs) and metre.phrase_features over its last PHRASE_S at each level_grids candidate, labelled with the
performance's class (Ds Dc Ts Tc; jazz Ds).
``fit``: an MLP (one hidden layer, sklearn; the worker runs it in numpy) on every candidate row, class-balanced
(each sequence weighs 1), cross-validated by piece (5 folds): held-out class accuracy per class, per request
(the class summed over the candidates) and per sequence at its end.  ``--exclude`` leaves pieces out (the labelled
live set's).  ``--write`` adds the fit to metre_weights.json as "phrase" (the rest of the file is kept).  Datasets
stay outside the repo; only the weights go in.  Run with OPENBLAS_NUM_THREADS=1 (threaded BLAS at nice 19 is ~50x
slower for these small matrices).
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import pathlib
import re
import sys
import zipfile

import numpy as np

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from stream_service.score import metre as M  # noqa: E402

WIN, STEP, DUR, MAXN, MINN = 12.0, 1.0, 46.0, 600, 12
BPB = {"4/4": 4, "2/4": 2, "6/8": 2, "3/4": 3, "3/8": 3, "9/8": 3, "2/2": 2, "12/16": 4, "12/8": 4, "6/4": 2,
       "3/2": 3, "4/8": 4, "4/2": 4, "6/16": 2, "9/16": 3, "12/4": 4, "9/4": 3, "3/16": 3}
COMPOUND = {"6/8", "9/8", "12/8", "12/16", "6/4", "6/16", "9/16", "12/4", "9/4"}
CLASSES = ("Ds", "Dc", "Ts", "Tc")


def cls_of(sig: str) -> str:
    if sig == "jazz":
        return "Ds"
    return ("T" if BPB[sig] == 3 else "D") + ("c" if sig in COMPOUND else "s")


def note_seq(data: bytes) -> np.ndarray:
    from stream_service.piano_replay import read_midi
    on, out = {}, []
    for t, st, d1, d2 in read_midi(data):
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


def requests(net, ns: np.ndarray, s0: float, s1: float, step: float) -> list:
    recs, tq = [], s0 + 2.0
    while tq <= s1:
        w = ns[(ns[:, 1] >= max(s0, tq - WIN)) & (ns[:, 1] <= tq)].copy()[-MAXN:]
        if len(w) >= MINN:
            w[:, 2] = np.clip(np.minimum(w[:, 2], tq - w[:, 1]), 0.03, None)
            t0 = float(w[0, 1])
            w[:, 1] -= t0
            o = net(w)
            recs.append(dict(t=tq, t0=t0, notes=w.astype(np.float32), beat=o["beat"].astype(np.float32),
                             down=o["down"].astype(np.float32), ts=o["ts"].astype(np.float32)))
        tq += step
    return recs


def cache(a) -> None:
    from stream_service.score import pm2s_net as P
    net = P.PM2S(pathlib.Path(a.weights).expanduser(), threads=2, device=a.device)
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(11)
    jobs = []                                   # (name, piece, sig, bytes-getter, start rule)
    if a.asap:
        root = pathlib.Path(a.asap)
        ann = json.loads((root / "asap_annotations.json").read_text())
        by_piece = collections.defaultdict(list)
        for k in sorted(ann):
            by_piece["/".join(k.split("/")[:-1])].append(k)
        for piece, perfs in by_piece.items():
            sigs = {s for k in perfs for s, _b in ann[k]["perf_time_signatures"].values()}
            cap = 4 if any(BPB.get(s) == 3 or s in COMPOUND for s in sigs) else 2
            rng.shuffle(perfs)
            for k in perfs[:cap]:
                ts = sorted((float(t), s) for t, (s, _b) in ann[k]["perf_time_signatures"].items())
                jobs.append((k.replace("/", "__"), piece, ts, (root / k).read_bytes))
    if a.pijama:
        z = zipfile.ZipFile(a.pijama)
        names = sorted(x for x in z.namelist() if x.endswith(".midi"))
        for i in sorted(rng.choice(len(names), min(a.n, len(names)), replace=False)):
            title = names[i].split("/")[-1]
            jobs.append((f"jazz__{i:04d}", "jazz/" + title, "jazz", (lambda n=names[i]: z.read(n))))
    if a.midi:
        labels = json.loads(pathlib.Path(a.labels).read_text())
        for f, lab in sorted(labels.items()):
            jobs.append(("midi__" + pathlib.Path(f).stem, "midi/" + lab.get("composition", f), lab["sig"],
                         (lambda p=pathlib.Path(a.midi) / f: p.read_bytes())))
    for name, piece, sig, get in jobs:
        dst = out / (name + ".seq.npz")
        if dst.exists():
            continue
        try:
            ns = note_seq(get())
        except Exception as e:                  # noqa: BLE001
            print("skip", name, e)
            continue
        if len(ns) < 60:
            continue
        if isinstance(sig, list):               # ASAP: the longest stretch of one signature, a seeded start in it
            end = ns[-1, 1]
            segs = [(t, sig[i + 1][0] if i + 1 < len(sig) else end, s) for i, (t, s) in enumerate(sig)]
            segs = [x for x in segs if x[2] in BPB and x[1] - x[0] >= 20.0]
            if not segs:
                continue
            t, e, s = max(segs, key=lambda x: x[1] - x[0])
            lo, hi = max(t, ns[0, 1]), e - min(DUR, e - t)
            s0 = lo if hi <= lo or rng.random() < 0.35 else float(rng.uniform(lo, hi))
            sig_ = s
        elif sig == "jazz":
            lo, hi = ns[0, 1], ns[-1, 1] - DUR
            s0 = lo if hi <= lo or rng.random() < 0.3 else float(rng.uniform(lo + 0.1 * (hi - lo), lo + 0.7 * (hi - lo)))
            e, sig_ = ns[-1, 1], "jazz"
        else:                                   # a score's time signature: from the first note
            s0, e, sig_ = ns[0, 1], ns[-1, 1], sig
        s0 = float(ns[int(np.searchsorted(ns[:, 1], s0 - 1e-6)), 1])
        recs = requests(net, ns, s0, min(e, s0 + (a.dur or DUR)), a.step)
        meta = dict(piece=piece, sig=sig_, s0=s0)
        np.savez_compressed(dst, recs=np.array(recs, dtype=object), meta=np.array(meta, dtype=object))
        print(name, sig_, len(recs), flush=True)


def _rows_of(path, every=3, H=None):
    from stream_service.score import pm2s_net as P
    z = np.load(path, allow_pickle=True)
    meta, recs = z["meta"].item(), [dict(r) for r in z["recs"]]
    if meta["sig"] != "jazz" and meta["sig"] not in BPB:
        return []
    ph, out = M.Phrase(), []
    for i, r in enumerate(recs):
        ns = r["notes"].astype(float)
        W, _D = P.beat_pps(r["beat"].astype(float), r["down"].astype(float), ns[:, 1])
        ids = [(round(float(o) + r["t0"], 3), int(p)) for p, o in zip(ns[:, 0], ns[:, 1])]
        ph.add(ns, ids, r["t0"], {k: r[k].astype(float) for k in ("down", "ts", "beat")})
        ph.grid.update(W + r["t0"], r["t0"] + float(ns[-1, 1]))
        if ph.grid.t is None or i % every:
            continue
        now = r["t0"] + float(ns[-1, 1])
        lo = max(meta["s0"], now - M.PHRASE_S)
        v = ph.arrays(lo, now + 0.05)
        g0 = ph.grid.t[(ph.grid.t >= lo - 0.05) & (ph.grid.t <= now + 0.05)]
        if v is None or len(g0) < 6 or len(v[0]) < 8:
            continue
        notes, down, ts = v
        for cand, g, _step, _j in M.level_grids(notes, g0, down):
            if len(g) < 6:
                continue
            n = notes.copy()
            n[:, 1] -= g[0]
            f = M.phrase_features(n, g - g[0], down, ts)
            if f is None:
                continue
            out.append(dict(file=path.name, piece=meta["piece"], sig=meta["sig"], cls=cls_of(meta["sig"]),
                            el=r["t"] - meta["s0"], cand=cand, x=M._phrase_x({"feats": FEATS}, f)))
    return out


FEATS = ["wfx", "sub", "n", "per", "lp"]


def rows(a) -> None:
    import multiprocessing as mp
    import pickle
    paths = [p for d in a.seqdirs for p in sorted(pathlib.Path(d).glob("*.seq.npz"))]
    out = []
    with mp.get_context("fork").Pool(a.jobs) as pool:
        for k, res in enumerate(pool.imap_unordered(_rows_of, paths, chunksize=2)):
            out += res
            if k % 100 == 0:
                print(k, len(out), flush=True)
    pickle.dump({"feats": FEATS, "rows": out}, open(a.out, "wb"))
    print("rows", len(out))


def _group(r: dict) -> str:
    if r["sig"] == "jazz":
        return "jazz:" + r["piece"].split("/")[-1].split(" - ")[0].lower()
    return r["piece"]


def fit(a) -> None:
    import pickle
    from sklearn.neural_network import MLPClassifier
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    d = pickle.load(open(a.rows, "rb"))
    ex = re.compile(a.exclude, re.I) if a.exclude else None
    rs = [r for r in d["rows"] if not (ex and ex.search(r["piece"]))]
    X = np.stack([r["x"] for r in rs])
    y = np.array([r["cls"] for r in rs])
    seq = np.array([r["file"] for r in rs])
    grp = np.array([_group(r) for r in rs])
    w = np.ones(len(rs))
    nseq = collections.Counter(seq)
    w /= np.array([nseq[s] for s in seq])
    w[np.array([r["sig"] == "jazz" for r in rs])] *= a.jazz_weight     # swing must stay simple duple
    cw = collections.Counter()
    for c, wi in zip(y, w):
        cw[c] += wi
    w *= np.array([sum(cw.values()) / (len(cw) * cw[c]) * (a.c3w if c == "Tc" else 1.0) for c in y])
    w *= len(w) / w.sum()
    print(f"{len(rs)} rows, {len(nseq)} sequences, {len(set(grp))} pieces;",
          dict(collections.Counter(y[np.unique(seq, return_index=True)[1]])))

    def make():
        return make_pipeline(StandardScaler(), MLPClassifier(hidden_layer_sizes=(a.hidden,), alpha=a.alpha,
                                                             max_iter=a.iters, random_state=0))
    groups = sorted(set(grp))
    np.random.default_rng(3).shuffle(groups)
    proba = np.zeros((len(rs), 4))
    for k in range(5):
        held = set(groups[k::5])
        te = np.array([g in held for g in grp])
        m = make()
        m.fit(X[~te], y[~te], mlpclassifier__sample_weight=w[~te])
        p = m.predict_proba(X[te])
        proba[te] = np.stack([p[:, list(m.classes_).index(c)] for c in CLASSES], 1)
    if a.oof:
        pickle.dump({"proba": proba, "rows": [{k: r[k] for k in ("file", "piece", "sig", "cls", "el", "cand")} for r in rs]},
                    open(a.oof, "wb"))
    by = collections.defaultdict(list)
    for i, r in enumerate(rs):
        by[(r["file"], round(r["el"], 1))].append(i)
    res, last = collections.defaultdict(collections.Counter), {}
    for (f, el), idx in by.items():
        src = rs[idx[0]]["piece"].split("/")[0] + ":" if a.by_source else ""
        c = src + ("jazz" if rs[idx[0]]["sig"] == "jazz" else y[idx[0]])
        want = y[idx[0]]
        pred = CLASSES[int(np.argmax(proba[idx].sum(0)))]
        if el >= 12:
            res[c]["n"] += 1
            res[c]["ok"] += pred == want
        if f not in last or el > last[f][0]:
            last[f] = (el, c, pred == want)
    for _f, (_el, c, ok) in last.items():
        res[c]["sn"] += 1
        res[c]["sok"] += ok
    print("held out by piece: requests after 12 s / sequences at their end")
    for c in sorted(res, key=lambda k: (k.split(":")[0], ("Ds", "Ts", "Dc", "Tc", "jazz").index(k.split(":")[-1]))):
        if res[c]["n"]:
            print(f"  {c:4s} {res[c]['ok'] / res[c]['n']:.2f} (n={res[c]['n']})   {res[c]['sok'] / res[c]['sn']:.2f} (n={res[c]['sn']})")
    if a.write:
        m = make()
        m.fit(X, y, mlpclassifier__sample_weight=w)
        sc, mlp = m[0], m[1]
        net = {"mean": [round(float(x), 6) for x in sc.mean_], "scale": [round(float(x), 6) for x in sc.scale_],
               "coefs": [[[round(float(v), 6) for v in row] for row in W] for W in mlp.coefs_],
               "intercepts": [[round(float(v), 6) for v in b] for b in mlp.intercepts_],
               "classes": [str(c) for c in mlp.classes_], "feats": d["feats"], "levels": a.levels,
               "rows": len(rs), "sequences": len(nseq)}
        weights = json.loads(M.WEIGHTS_PATH.read_text()) if M.WEIGHTS_PATH.exists() else {}
        weights["phrase"] = net
        M.WEIGHTS_PATH.write_text(json.dumps(weights) + "\n")
        print("wrote", M.WEIGHTS_PATH)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("cache")
    c.add_argument("--asap")
    c.add_argument("--pijama")
    c.add_argument("--n", type=int, default=170)
    c.add_argument("--midi")
    c.add_argument("--labels")
    c.add_argument("--out", required=True)
    c.add_argument("--weights", default="~/.local/share/screen-machine/pm2s")
    c.add_argument("--device", default="cpu")
    c.add_argument("--step", type=float, default=STEP)
    c.add_argument("--dur", type=float, default=None)
    r = sub.add_parser("rows")
    r.add_argument("seqdirs", nargs="+")
    r.add_argument("--out", required=True)
    r.add_argument("--jobs", type=int, default=3)
    f = sub.add_parser("fit")
    f.add_argument("rows")
    f.add_argument("--exclude", default=None)
    f.add_argument("--hidden", type=int, default=64)
    f.add_argument("--alpha", type=float, default=0.3)
    f.add_argument("--iters", type=int, default=200)
    f.add_argument("--c3w", type=float, default=0.3)
    f.add_argument("--jazz-weight", type=float, default=4.0, help="a jazz sequence's weight against another's")
    f.add_argument("--write", action="store_true")
    f.add_argument("--levels", default="held", help="the worker reads the class at the held pulse (held) or summed "
                   "over its levels (all: 5x the work; in the live set's runs fewer triple pieces found)")
    f.add_argument("--oof", default=None, help="save the out-of-fold probabilities (pickle)")
    f.add_argument("--by-source", action="store_true", help="report per source (the piece's first path part)")
    a = ap.parse_args()
    {"cache": cache, "rows": rows, "fit": fit}[a.cmd](a)


if __name__ == "__main__":
    main()
