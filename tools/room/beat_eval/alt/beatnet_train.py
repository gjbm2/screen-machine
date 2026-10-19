#!/usr/bin/env python3
"""Fine-tune BeatNet's CRNN on music heard through this room (the room model), so the candidate's activation keeps
the beat where the stock one (trained on mastered audio) loses it.

Why: on the dev subset in condition C the stock CRNN's activation barely tells beats from off-beats (AUC of the
activation at annotated beats against the off-beat midpoints: about 0.60 for each of BeatNet's three models and
their mean, madmom's two nets about 0.70; clean audio about 0.9): the mic's spectral scramble and room sound only
5 dB under the music make onset-like clutter everywhere, and no filter or line on top recovers what isn't in the
activation. The CRNN is small (about 0.4 M weights), so it can learn the room.

  ~/.venvs/sm-beat-beatnet/bin/python tools/room/beat_eval/alt/beatnet_train.py data [--n 600]   # heavy, CPU lock
  ~/.venvs/sm-beat-beatnet/bin/python tools/room/beat_eval/alt/beatnet_train.py train --tag r1 [--steps 3000]
  # then: beatnet_stack.py --weights ~/.cache/screen-machine/beat-eval/alt-beatnet/models/bnroom-r1.pt ...

Data (``data``): tracks from the combined index that are in NEITHER the dev subset NOR the held-out test
(baseline-subset.json), no duplicates, no ASAP (rubato piano: the expressive check stays unseen), spread over dataset
and genre. Per track one 30 s excerpt (a random window of a full track), then:
- the room: roomsim.apply with the PC22 model, a seed of its own, at the measured room-sound level (70 %) or up to
  10 dB cleaner (30 %), with 0-8 s of room sound alone before the music on half of them (no beat there: the clock
  must not hear a beat in typing and talk). The room sound is the model's own recording, but only its FIRST HALF
  (a copy of the model with noise.wav cut to 81 s, in the cache): the dev and test replays loop all 162 s, so half
  of what they hear was never trained on;
- the same excerpt clean as well (half of the training set stays clean: the candidate must not get worse on A);
- then the live lane exactly (Clock's AGC, soxr MQ to 22.05 kHz, BeatNet's features), and the targets at 50 fps:
  beat / downbeat / none on the frame nearest each annotation, the neighbours half (target widening).
Saved per item under ~/.cache/screen-machine/beat-eval/alt-beatnet/train-data/ (features float16).

Run r1 (19 Oct; the room preset's weights, ~/.cache/screen-machine/beat-eval/alt-beatnet/models/bnroom-r1.pt): the
first 187 tracks of ``data --n 400`` (gtzan, ballroom, harmonix, tapcorrect), 2000 steps, kept at step 1250 (best
held-out loss); 15 min of CPU. The activation's AUC on the dev subset in condition C went from 0.59 (stock model_1)
to 0.80 (madmom's two nets: about 0.70 on the tracks where it could be compared); classical stays near 0.5 (no beat
to find). r2 (344 tracks, 2500 steps, kept at 750) was no better (0.78). The real recording (rec-music.wav, not
simulated) does not get worse: F 0.97 with the room preset. Not done: more data, other room models (a model of one
room and one mic is all it has seen), a held-out room-sound recording.

Training (``train``): from model_1's weights, soft-target cross entropy (the beat classes up-weighted), Adam
1e-4, batches of 8 random 12 s crops, one torch thread; 10 % of the items held out for the loss curve (the dev subset
is the real check, through beatnet_batch.py sweep). Weights to ~/.cache/screen-machine/beat-eval/alt-beatnet/models/.
"""
from __future__ import annotations

import argparse
import collections
import fcntl
import json
import math
import os
import pathlib
import random
import shutil
import sys
import time
import zlib

HERE = pathlib.Path(__file__).resolve().parent
BEVAL = HERE.parent
REPO = BEVAL.parents[2]
sys.path.insert(0, str(BEVAL))
sys.path.insert(0, str(HERE))
import numpy as np  # noqa: E402

import baseline  # noqa: E402
import run_batch  # noqa: E402

CACHE = pathlib.Path("~/.cache/screen-machine/beat-eval").expanduser()
OUT = CACHE / "alt-beatnet"
DATA = OUT / "train-data"
MODELS = OUT / "models"
ROOM_TRAIN = OUT / "train-room"
LOCK = pathlib.Path("~/.cache/screen-machine/cpu.lock").expanduser()
FS = 48000
EXCERPT_S = 30.0


def train_room_model() -> pathlib.Path:
    """A copy of the PC22 room model whose room sound is the first half of its recording only."""
    src_model = pathlib.Path(baseline.calibration()[0])
    dst = ROOM_TRAIN / "model.json"
    if dst.exists():
        return dst
    import soundfile as sf
    ROOM_TRAIN.mkdir(parents=True, exist_ok=True)
    for f in src_model.parent.iterdir():
        if f.name != "noise.wav" and f.is_file():
            shutil.copy2(f, ROOM_TRAIN / f.name)
    x, fs = sf.read(str(src_model.parent / "noise.wav"), dtype="float32", always_2d=False)
    sf.write(str(ROOM_TRAIN / "noise.wav"), x[:len(x) // 2], fs, subtype="FLOAT")
    return dst


def pick_tracks(n: int, seed: int) -> list[dict]:
    held = set()
    for p in (CACHE / "dev-subset.json", CACHE / "baseline-subset.json"):
        held |= {(i["dataset"], i["id"]) for i in json.loads(p.read_text())["items"]}
    _, entries = run_batch.load_index(baseline.COMBINED)
    entries = [e for e in entries if not e["duplicate"] and (e["dataset"], e["id"]) not in held
               and e["dataset"] in ("ballroom", "gtzan", "harmonix", "tapcorrect") and e["audio"]
               and pathlib.Path(e["audio"]).exists() and e["beats"] and pathlib.Path(e["beats"]).exists()]
    return run_batch.pick(entries, n, "dataset,genre", seed)


def read_beats_pos(path: pathlib.Path) -> tuple[np.ndarray, np.ndarray]:
    t, pos = [], []
    for line in path.read_text().splitlines():
        p = line.replace(",", " ").split()
        if not p or p[0].startswith("#"):
            continue
        try:
            t.append(float(p[0]))
            pos.append(int(float(p[1])) if len(p) > 1 else 0)
        except ValueError:
            continue
    return np.asarray(t), np.asarray(pos)


def targets(nf: int, t: np.ndarray, pos: np.ndarray, offset_s: float) -> np.ndarray:
    """(nf, 3) soft targets at 50 fps: beat, downbeat, none (BeatNet's class order); the nearest frame 1, its
    neighbours 0.5."""
    y = np.zeros((nf, 3), np.float32)
    for ti, pi in zip(t, pos):
        k = int(round((ti + offset_s) * 50))
        c = 1 if pi == 1 else 0
        for d, w in ((0, 1.0), (-1, 0.5), (1, 0.5)):
            if 0 <= k + d < nf:
                y[k + d, c] = max(y[k + d, c], w)
    y[:, 2] = np.clip(1.0 - y[:, 0] - y[:, 1], 0.0, 1.0)
    s = y.sum(axis=1, keepdims=True)
    return y / np.maximum(s, 1e-6)


def lane_features(y48: np.ndarray) -> np.ndarray:
    import soxr
    import beatnet_stack as bs
    agc = bs.AGC()
    g = np.concatenate([agc.block(y48[i:i + 128], i) for i in range(0, len(y48), 128)])
    z = soxr.resample(g, FS, bs.FS_BN, quality="MQ").astype(np.float32)
    return bs.features(z)


def cmd_data(a) -> int:
    import importlib.util
    import run_stack
    try:
        os.nice(19)
    except OSError:
        pass
    spec = importlib.util.spec_from_file_location("roomsim", run_stack.AUDIO_CAL / "roomsim.py")
    roomsim = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(roomsim)                          # type: ignore[union-attr]
    model = train_room_model()
    snr0 = float(json.loads(model.read_text())["snr_db"])
    tracks = pick_tracks(a.n, a.seed)
    DATA.mkdir(parents=True, exist_ok=True)
    print(f"{len(tracks)} training tracks (none in dev or test) -> {DATA}", flush=True)
    lk, t_lock = None, 0.0
    done = 0
    for i, e in enumerate(tracks, 1):
        name = f"{run_batch.safe(e['dataset'])}__{run_batch.safe(e['id'])}"
        if (DATA / f"{name}.npz").exists():
            continue
        if lk is None or time.time() - t_lock > a.hold_s:     # the shared CPU lock, in short slices
            if lk is not None:
                fcntl.flock(lk, fcntl.LOCK_UN)
                lk.close()
            lk = LOCK.open("a")
            fcntl.flock(lk, fcntl.LOCK_EX)
            t_lock = time.time()
        rng = random.Random(zlib.crc32(name.encode()) + a.seed)
        dur = float(e.get("duration_s") or EXCERPT_S)
        start = rng.uniform(0.0, max(0.0, dur - EXCERPT_S)) if dur > EXCERPT_S + 1 else 0.0
        x = run_stack.decode(pathlib.Path(e["audio"]), start, EXCERPT_S)
        t, pos = read_beats_pos(pathlib.Path(e["beats"]))
        m = (t >= start) & (t < start + x.shape[0] / FS)
        t, pos = t[m] - start, pos[m]
        if len(t) < 8:
            continue
        clean = run_stack.to_mono(x)
        pre = rng.uniform(0.0, 8.0) if rng.random() < 0.5 else 0.0
        xp = np.concatenate([np.zeros((int(pre * FS), x.shape[1]), np.float32), x]) if pre else x
        snr = None if rng.random() < 0.7 else snr0 + rng.uniform(0.0, 10.0)
        y, _ = roomsim.apply(xp, FS, str(model), seed=rng.randrange(1 << 30), snr_db=snr)
        y = run_stack.int16_wire(np.asarray(y, np.float32))
        fc = lane_features(run_stack.int16_wire(clean))
        fr = lane_features(y)
        np.savez(DATA / f"{name}.tmp.npz", f_clean=fc.astype(np.float16), y_clean=targets(len(fc), t, pos, 0.0),
                 f_room=fr.astype(np.float16), y_room=targets(len(fr), t, pos, pre),
                 meta=np.array(json.dumps({"id": e["id"], "dataset": e["dataset"], "genre": e["genre"],
                                           "start_s": start, "preroll_s": pre, "snr_db": snr})))
        (DATA / f"{name}.tmp.npz").replace(DATA / f"{name}.npz")
        done += 1
        if done % 20 == 0:
            print(f"  [{i}/{len(tracks)}] {done} new", flush=True)
    if lk is not None:
        fcntl.flock(lk, fcntl.LOCK_UN)
        lk.close()
    print(f"done: {done} new, {len(list(DATA.glob('*.npz')))} in all", flush=True)
    return 0


def cmd_train(a) -> int:
    import torch
    import beatnet_stack as bs
    try:
        os.nice(19)
    except OSError:
        pass
    torch.set_num_threads(a.threads)
    torch.manual_seed(a.seed)
    rng = np.random.default_rng(a.seed)
    files = sorted(DATA.glob("*.npz"))
    files = [f for f in files if ".tmp" not in f.name]
    items = []
    for f in files:
        z = np.load(f)
        for k in ("clean", "room"):
            if k == "clean" and a.room_only:
                continue
            items.append((f.stem, k, z[f"f_{k}"].astype(np.float32), z[f"y_{k}"].astype(np.float32)))
    names = sorted({s for s, _, _, _ in items})
    rs = random.Random(a.seed)
    val_names = set(rs.sample(names, max(1, len(names) // 10)))
    tr = [it for it in items if it[0] not in val_names]
    va = [it for it in items if it[0] in val_names]
    print(f"{len(names)} tracks: {len(tr)} training items, {len(va)} held-out items", flush=True)
    net = bs.load_bnet(1)
    torch.set_num_threads(a.threads)
    net.train()
    opt = torch.optim.Adam(net.parameters(), lr=a.lr)
    w = torch.tensor([a.beat_weight, a.beat_weight, 1.0])
    L = int(a.crop_s * 50)

    def batch(pool: list, n: int) -> tuple:
        X, Y = [], []
        for _ in range(n):
            _, _, f, y = pool[rng.integers(len(pool))]
            if len(f) > L:
                o = int(rng.integers(len(f) - L))
                f, y = f[o:o + L], y[o:o + L]
            else:
                f = np.pad(f, ((0, L - len(f)), (0, 0)))
                y = np.pad(y, ((0, L - len(y)), (0, 0)))
                y[np.sum(y, axis=1) == 0, 2] = 1.0
            X.append(f)
            Y.append(y)
        return torch.from_numpy(np.stack(X)), torch.from_numpy(np.stack(Y))

    def loss_of(X, Y) -> torch.Tensor:
        B = X.shape[0]
        net.hidden = torch.zeros(2, B, net.dim_hd)
        net.cell = torch.zeros(2, B, net.dim_hd)
        out = net(X)                                     # (B, 3, T)
        logp = torch.log_softmax(out, dim=1).transpose(1, 2)   # (B, T, 3)
        return -(Y * logp * w).sum(dim=2).mean()

    def val_loss() -> float:
        net.eval()
        with torch.no_grad():
            tot = 0.0
            for k in range(0, len(va), 8):
                X, Y = [], []
                for _, _, f, y in va[k:k + 8]:
                    f, y = f[:L], y[:L]
                    if len(f) < L:
                        f = np.pad(f, ((0, L - len(f)), (0, 0)))
                        y = np.pad(y, ((0, L - len(y)), (0, 0)))
                        y[np.sum(y, axis=1) == 0, 2] = 1.0
                    X.append(f)
                    Y.append(y)
                tot += float(loss_of(torch.from_numpy(np.stack(X)), torch.from_numpy(np.stack(Y)))) * len(X)
        net.train()
        return tot / max(1, len(va))

    MODELS.mkdir(parents=True, exist_ok=True)
    best, best_step = math.inf, 0
    out_p = MODELS / f"bnroom-{a.tag}.pt"
    log = []
    v0 = val_loss()
    print(f"step 0: held-out loss {v0:.4f}", flush=True)
    t0 = time.process_time()
    lk, t_lock = None, 0.0
    for step in range(1, a.steps + 1):
        if lk is None or time.time() - t_lock > a.hold_s:   # the shared CPU lock, in short slices
            if lk is not None:
                fcntl.flock(lk, fcntl.LOCK_UN)
                lk.close()
            lk = LOCK.open("a")
            fcntl.flock(lk, fcntl.LOCK_EX)
            t_lock = time.time()
        X, Y = batch(tr, a.batch)
        loss = loss_of(X, Y)
        opt.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(net.parameters(), 5.0)
        opt.step()
        if step % a.eval_every == 0 or step == a.steps:
            v = val_loss()
            log.append([step, float(loss), v])
            print(f"step {step}: train {float(loss):.4f}, held-out {v:.4f} ({time.process_time() - t0:.0f} s CPU)",
                  flush=True)
            if v < best:
                best, best_step = v, step
                torch.save({k: t.detach().clone() for k, t in net.state_dict().items()}, out_p)
    if lk is not None:
        fcntl.flock(lk, fcntl.LOCK_UN)
        lk.close()
    (MODELS / f"bnroom-{a.tag}.json").write_text(json.dumps({"tag": a.tag, "args": vars(a), "best_step": best_step,
                                                            "best_val": best, "val0": v0, "log": log,
                                                            "n_tracks": len(names), "val_tracks": sorted(val_names)},
                                                           indent=1, default=str))
    print(f"-> {out_p} (best held-out {best:.4f} at step {best_step}; it started at {v0:.4f})")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("data", "train"))
    ap.add_argument("--n", type=int, default=600)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--hold-s", type=float, default=45.0)
    ap.add_argument("--tag", default="r1")
    ap.add_argument("--steps", type=int, default=3000)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--crop-s", type=float, default=12.0)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--beat-weight", type=float, default=4.0)
    ap.add_argument("--eval-every", type=int, default=250)
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--room-only", action="store_true", help="train on the room versions only")
    a = ap.parse_args(argv)
    return cmd_data(a) if a.cmd == "data" else cmd_train(a)


if __name__ == "__main__":
    raise SystemExit(main())
