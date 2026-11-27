#!/usr/bin/env python3
"""The art director's eval: the song set through the real show, scored against each song's answer key.

    ~/.venvs/sm-show/bin/python tools/room/ad_eval/run.py [--songs ID ...] [--modes karaoke live] [--jobs 4]
    ~/.venvs/sm-show/bin/python tools/room/ad_eval/run.py --rescore <run dir>     # score its timelines again, no replay

No arguments: the fixed set (songs.yaml) plus every song an owner note names (ad_feedback.jsonl), both modes.
Writes ~/.cache/sm-ad-eval/runs/<UTC stamp>-<git sha>/: scorecard.json and <song id>.<mode>.json (each song's
timeline with its reference), appends the run to runs/history.jsonl, and prints the diff against the previous run
and the best one (regressions flagged). Everything at nice 19. Exits non-zero only when the run itself breaks.

The scorecard also says whether its own score can be trusted: ``controls`` (controls.py: per mode, does the real
show outscore deliberately wrong ones; ``pass`` for all the modes), and ``score_version`` (score.SCORE_VERSION): a
run is compared only with runs of the same version.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
import time
from typing import Any, Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import common  # noqa: E402
import controls as controlsmod  # noqa: E402
import form as formmod  # noqa: E402
import score as scoremod  # noqa: E402

RUNS = common.CACHE / "runs"
FEEDBACK = pathlib.Path.home() / ".local" / "state" / "screen-machine" / "show" / "ad_feedback.jsonl"
MODES = ("karaoke", "live")
HEADLINE = ("overall", "goals", "gate", "beat", "contour", "sections", "form", "contrast", "proportion", "arc", "peak",
            "release", "ending", "character",                                  # the goals (character: of the set)
            "palette", "kit", "flourish", "stillness",                         # the gate's parts (design.py)
            "snap", "climax", "coherence", "design", "reserve")                # reported only
REGRESSION = 0.01             # a score falling by more than this is flagged (the run is deterministic: starting point)


def notes() -> list[dict]:
    out = []
    try:
        for ln in FEEDBACK.read_text().splitlines():
            try:
                o = json.loads(ln)
            except ValueError:
                continue
            if isinstance(o, dict) and o.get("song"):
                out.append(o)
    except OSError:
        pass
    return out


def _sub(args: list[str], timeout: float, env: Optional[dict] = None) -> tuple[int, str]:
    p = subprocess.run(["nice", "-n", "19", *args], capture_output=True, text=True, timeout=timeout,
                       cwd=str(common.REPO), env={**os.environ, **env} if env else None)
    return p.returncode, (p.stdout[-2000:] + p.stderr[-3000:])


def tl_name(sid: str, mode: str, seed: int) -> str:
    """A timeline's file in a run: the first draw is <id>.<mode>.json, another seed's <id>.<mode>.s<k>.json."""
    return f"{sid}.{mode}.json" if seed == 0 else f"{sid}.{mode}.s{seed}.json"


DMX = False                                                   # --dmx: the replays record the wire's frames too


def one(sid: str, mode: str, out_dir: pathlib.Path, seed: int = 0) -> tuple[str, str, Optional[str]]:
    out = out_dir / tl_name(sid, mode, seed)
    env = {**({"SM_AD_EVAL_SEED": str(seed)} if seed else {}), **({"SM_AD_EVAL_DMX": "1"} if DMX else {})}
    rc, log = _sub([str(common.SHOW_PY), str(common.HERE / "replay.py"), sid, "--mode", mode, "--out", str(out)],
                   timeout=3600, env=env or None)
    return sid, mode, None if rc == 0 and out.is_file() else f"replay exit {rc}: {log[-1500:]}"


def ref(sid: str) -> tuple[str, Optional[str]]:
    rc, log = _sub([str(common.SHOW_PY), str(common.HERE / "reference.py"), sid], timeout=1800)
    return sid, None if rc == 0 else f"reference exit {rc}: {log[-1500:]}"


def history() -> list[dict]:
    p = RUNS / "history.jsonl"
    try:
        return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
    except (OSError, ValueError):
        return []


def load_card(d: str) -> Optional[dict]:
    try:
        return json.loads((RUNS / d / "scorecard.json").read_text())
    except (OSError, ValueError):
        return None


def note_key(n: dict) -> str:
    return json.dumps([n.get("song"), n.get("t_s"), n.get("dimension"), n.get("verdict"), n.get("words")],
                      sort_keys=True)


def fmt(v: Any) -> str:
    return "  -  " if v is None else f"{v:5.3f}" if isinstance(v, float) else str(v)


def diff(card: dict, other: Optional[dict], label: str) -> list[str]:
    if other is None:
        return [f"(no {label} run to compare with)"]
    lines = [f"vs {label} {other['run']['dir']} (overall {fmt(other['overall'])} -> {fmt(card['overall'])}):"]
    k1, k0 = card["run"].get("seeds", 1), other["run"].get("seeds", 1)
    if k1 != k0:
        lines.append(f"  (draws per song {k0} -> {k1}: the scores move with the draw count, so this is not like for like)")
    for mode in MODES:
        a, b = card["aggregate"].get(mode) or {}, other["aggregate"].get(mode) or {}
        row = []
        for k in HEADLINE:
            x, y = a.get(k), b.get(k)
            if x is None or y is None:
                continue
            d = x - y
            flag = " !" if d < -REGRESSION else ""
            row.append(f"{k} {x:.3f} ({d:+.3f}){flag}")
        if row:
            lines.append(f"  {mode:8s} " + "  ".join(row))
    regress = []
    for key, s in card["songs"].items():
        o = other["songs"].get(key)
        if not o or s.get("overall") is None or o.get("overall") is None:
            continue
        d = s["overall"] - o["overall"]
        if d < -max(REGRESSION, s.get("overall_spread") or 0.0, o.get("overall_spread") or 0.0):   # past its draws' own
            regress.append((d, key, s.get("title")))
    for d, key, t in sorted(regress)[:10]:
        lines.append(f"  REGRESSED {key} {str(t)[:30]}: overall {d:+.3f}")
    for c in card.get("cases") or []:
        prev = {note_key(x["note"]): x.get("value") for x in other.get("cases") or []}.get(note_key(c["note"]))
        v = c.get("value")
        if v is None or prev is None:
            continue
        bad = (c["note"].get("verdict") == "good" and v < prev - REGRESSION) or \
              (c["note"].get("verdict") == "bad" and v < prev - REGRESSION)
        if bad or abs(v - prev) > REGRESSION:
            lines.append(f"  case {c['note'].get('verdict')} {c['note'].get('song')} @{c['note'].get('t_s')}s "
                         f"{c['note'].get('dimension')}: {prev} -> {v}{'  REGRESSED' if bad else ''}")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--songs", nargs="*", help="song ids (default: songs.yaml + the noted songs)")
    ap.add_argument("--modes", nargs="*", default=list(MODES), choices=MODES)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--label", default="")
    ap.add_argument("--seeds", type=int, default=3,
                    help="draws of the show per song, averaged (27 Nov: one draw's arc and character moved +-0.09 "
                    "between seeds of the same code, so a one-seed change of less than that is a reshuffle)")
    ap.add_argument("--rescore", metavar="DIR", help="score an earlier run's timelines again (no replay): after a "
                    "change to the scorer; writes that run's scorecard.json and a new line of history")
    ap.add_argument("--dmx", action="store_true", help="also record each replay's DMX frames (<id>.<mode>.dmx.npz), "
                    "for the blind pairs (pairs.py)")
    a = ap.parse_args()
    global DMX
    DMX = a.dmx
    try:
        os.nice(19)
    except OSError:
        pass
    t_start = time.time()
    lib = common.library()
    nts = notes()
    ids = a.songs or [s["id"] for s in common.song_set()]
    if not a.songs:
        ids += [n["song"] for n in nts if n["song"] not in ids and n["song"] in lib]
    ids = [i for i in dict.fromkeys(ids) if i in lib]
    sha = common.git_sha()
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    errors: dict[str, str] = {}
    if a.rescore:
        out_dir = pathlib.Path(a.rescore) if pathlib.Path(a.rescore).is_dir() else RUNS / a.rescore
        name = out_dir.name
        have = set()
        for p in out_dir.glob("*.*.json"):
            parts = p.name.split(".")
            if len(parts) == 3 or (len(parts) == 4 and parts[2][:1] == "s" and parts[2][1:].isdigit()):
                have.add((parts[0], parts[1], int(parts[2][1:]) if len(parts) == 4 else 0))
        ids = [i for i in dict.fromkeys(sid for sid, _m, _k in sorted(have)) if i in lib]
        todo = [(sid, m, k) for sid, m, k in sorted(have) if sid in ids and m in a.modes]
        a.label = a.label or f"rescored {stamp}"
        print(f"ad_eval {name}: scoring again, {len(todo)} timelines", flush=True)
    else:
        name = f"{stamp}-{sha}"
        out_dir = RUNS / name
        out_dir.mkdir(parents=True, exist_ok=True)
        print(f"ad_eval {name}: {len(ids)} songs x {', '.join(a.modes)}, {a.jobs} jobs", flush=True)
        with cf.ThreadPoolExecutor(max(1, a.jobs)) as ex:
            for sid, err in ex.map(ref, ids):
                if err:
                    errors[f"{sid}:reference"] = err
                    print(f"  reference failed: {sid}", flush=True)
        todo = [(sid, m, k) for k in range(max(1, a.seeds)) for sid in ids for m in a.modes
                if f"{sid}:reference" not in errors]
        with cf.ThreadPoolExecutor(max(1, a.jobs)) as ex:
            futs = [ex.submit(one, sid, m, out_dir, k) for sid, m, k in todo]
            for f in cf.as_completed(futs):
                sid, mode, err = f.result()
                if err:
                    errors[f"{sid}:{mode}"] = err
                print(f"  {'FAILED ' if err else ''}{sid} {mode} ({time.time() - t_start:.0f} s)", flush=True)
    import reference
    W = scoremod.weights()
    songs: dict[str, dict] = {}
    cases = []
    by_mode: dict[str, list[dict]] = {m: [] for m in a.modes}       # the timelines, for the controls
    draws: dict[str, list[dict]] = {}                               # each song's scores, one per seed
    for sid, mode, seed in todo:
        p = out_dir / tl_name(sid, mode, seed)
        if not p.is_file():
            continue
        tl = json.loads(p.read_text())
        r = reference.load(sid)
        tl["reference"] = {k: v for k, v in r.items() if k not in ("song", "version", "made_at", "cost_s")}
        try:
            m = scoremod.score(tl, r, W)
        except Exception as e:                                # noqa: BLE001
            errors[f"{sid}:{mode}:score"] = f"{type(e).__name__}: {e}"
            continue
        tl["scores"] = m
        common.write_json(p, tl)
        draws.setdefault(f"{sid}.{mode}", []).append(m)
        if seed != 0:
            continue                                          # the controls and the notes: one draw per song
        by_mode[mode].append(tl)
        songs[f"{sid}.{mode}"] = {"id": sid, "mode": mode, "title": (lib.get(sid) or {}).get("title"), **m,
                                  "character_of": r.get("character"),
                                  "rt": (tl.get("run") or {}).get("rt"), "errors": tl.get("errors") or {}}
        for n in nts:
            if n.get("song") == sid and (n.get("mode") in (None, mode)):
                cases.append({"note": n, "mode": mode, "value": scoremod.case_value(tl, r, n, W)})
    for key, ms in draws.items():                             # a song's score: the mean of its draws
        if key not in songs or len(ms) < 2:
            continue
        num = {k for mm in ms for k, v in mm.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
        for k in num:
            vals = [float(mm[k]) for mm in ms if isinstance(mm.get(k), (int, float)) and not isinstance(mm.get(k), bool)]
            songs[key][k] = round(sum(vals) / len(vals), 3)
        ov = [float(mm["overall"]) for mm in ms if isinstance(mm.get("overall"), (int, float))]
        songs[key]["seeds"] = len(ms)
        songs[key]["overall_spread"] = round(max(ov) - min(ov), 3) if ov else None
    agg: dict[str, dict] = {}
    for mode in a.modes:
        rows = [s for s in songs.values() if s["mode"] == mode]
        keys = {k for s in rows for k, v in s.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
        agg[mode] = {}
        for k in sorted(keys):
            vals = [s[k] for s in rows if isinstance(s.get(k), (int, float)) and not isinstance(s.get(k), bool)]
            agg[mode][k] = round(sum(vals) / len(vals), 3) if vals else None
        # of the whole set: do songs that sound different get shows that look different? In the mode's overall.
        ch = formmod.character([(s.get("character_of"), s.get("look")) for s in rows], W)
        agg[mode]["character"], agg[mode]["character_rho"] = ch["character"], ch["character_rho"]
        x = float((W.get("set_weights") or {}).get("character") or 0.0)
        if agg[mode].get("overall") is not None and ch["character"] is not None:
            agg[mode]["overall_songs"] = agg[mode]["overall"]
            agg[mode]["overall"] = round((1.0 - x) * agg[mode]["overall"] + x * ch["character"], 3)
    ctl: dict[str, Any] = {"pass": True, "margins": {}}
    for mode in a.modes:
        try:
            res = controlsmod.check(by_mode[mode], W)
        except Exception as e:                                # noqa: BLE001
            res = {"pass": False, "why": f"{type(e).__name__}: {e}", "margins": {}}
            errors[f"controls:{mode}"] = res["why"]
        ctl[mode] = res
        ctl["pass"] = bool(ctl["pass"] and res.get("pass"))
        ctl["margins"][mode] = res.get("margins") or {}
    del by_mode
    # a replay in which the show itself raised (a director tick that threw: 27 Nov, a clobbered variable made every
    # tick fail and the run still scored) is not a run to steer by, whatever its numbers
    broken = {k: v["errors"] for k, v in songs.items() if any(str(e).startswith(("director.", "structure.", "cues."))
                                                              for e in (v.get("errors") or {}))}
    if broken:
        ctl["pass"] = False
        ctl["why"] = f"the show raised errors in {len(broken)} of {len(songs)} replays"
        errors["replay"] = f"{ctl['why']}: {dict(list(broken.items())[:3])}"
    ov = [agg[m].get("overall") for m in a.modes if agg.get(m, {}).get("overall") is not None]
    hist = [h for h in history() if set(h.get("songs") or []) >= set(ids) and h.get("modes") == a.modes
            and h.get("score_version") == scoremod.SCORE_VERSION]
    prev_dir = hist[-1]["dir"] if hist else None
    prev = load_card(prev_dir) if prev_dir else None
    if prev is not None:
        pv = {note_key(x["note"]) + x.get("mode", ""): x.get("value") for x in prev.get("cases") or []}
        for c in cases:
            c["prev_value"] = pv.get(note_key(c["note"]) + c["mode"])
    card = {"run": {"dir": name, "sha": sha, "stack": common.stack_id(), "dirty": common.dirty_files(),
                    "at": t_start, "wall_s": round(time.time() - t_start, 1), "label": a.label, "modes": a.modes,
                    "songs": ids, "weights": W, "seeds": max(1, a.seeds) if not a.rescore else max(
                        (1 + k for _s, _m, k in todo), default=1)},
            "score_version": scoremod.SCORE_VERSION, "controls": ctl,
            "overall": round(sum(ov) / len(ov), 3) if ov else None, "aggregate": agg, "songs": songs,
            "cases": cases, "errors": errors}
    common.write_json(out_dir / "scorecard.json", card)
    full = hist
    best_dir = max(full, key=lambda h: h.get("overall") or -1)["dir"] if full else None
    with (RUNS / "history.jsonl").open("a") as f:
        f.write(json.dumps({"dir": name, "sha": sha, "stack": card["run"]["stack"], "at": t_start,
                            "overall": card["overall"], "aggregate": {m: {k: agg[m].get(k) for k in HEADLINE}
                                                                       for m in agg},
                            "songs": ids, "modes": a.modes, "label": a.label, "errors": len(errors),
                            "seeds": card["run"]["seeds"], "score_version": scoremod.SCORE_VERSION, "controls_pass": ctl["pass"]}) + "\n")
    print(f"\nscorecard {out_dir / 'scorecard.json'}  overall {fmt(card['overall'])}  "
          f"({card['run']['wall_s']:.0f} s, {len(errors)} errors)")
    for mode in a.modes:
        print(f"  {mode:8s} " + "  ".join(f"{k} {fmt(agg[mode].get(k))}" for k in HEADLINE))
    for mode in a.modes:
        for ln in controlsmod.show(ctl[mode], mode):
            print(ln)
    if ctl.get("why"):
        print(f"THE RUN IS BROKEN: {ctl['why']}. Don't steer by it.")
    elif not ctl["pass"]:
        print("THE SCORE FAILS ITS CONTROLS: it can't tell the real show from a wrong one. Don't steer by it.")
    for ln in diff(card, prev, "previous"):
        print(ln)
    if best_dir and best_dir != prev_dir:
        for ln in diff(card, load_card(best_dir), "best"):
            print(ln)
    for k, e in list(errors.items())[:5]:
        print(f"  error {k}: {e.splitlines()[-1] if e else ''}")
    return 1 if todo and len(songs) == 0 else 0


if __name__ == "__main__":
    sys.exit(main())
