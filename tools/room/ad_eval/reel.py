"""The owner's morning reel: a few minutes of the show's transitions, not every second of every song (owner, 28 Nov:
"my review can't take ages ... it's the transitions and pattern that I'm reviewing").

Each item is about 26 s around one transition (a section boundary, the climax, a stop, the ending), played in the 3D
room with the song (/lights/pairs, which shows reels and blind pairs alike). They come in this order, to a budget:
  1. changed   what the night's fix changed: the same transition before and after, side by side and blind (needs
               --vs, the run before the fix). The owner's pick says whether the fix is better by eye.
  2. flagged   what a fault check (checks.py) failed, one per check and song, the checks with fewest of the
               owner's verdicts first. The page never says which items were flagged or why: the owner judges only
               what the lights do, and a check is judged by those verdicts afterwards (owner, 28 Nov).
  3. pattern   a rotating sample of the set's other transitions (a different few songs each day), so over a week
               every song's pattern is seen.
A single item takes good / wrong / skip (and words); a pair takes left / right / same. The owner can widen any item
to its section or the whole song. Verdicts on single items also go into the a.d.'s feedback file as moment notes
(show_service/ad_pairs.py), which the eval scores as cases and the overnight loop reads first.

    ~/.venvs/sm-show/bin/python tools/room/ad_eval/reel.py make <run dir> [--vs <run dir>] [--n 12] [--batch NAME]
    ~/.venvs/sm-show/bin/python tools/room/ad_eval/reel.py said <batch>     add what the a.d. decided to a batch made
                                                                            before it was shown (its picks kept)

Needs runs made with run.py --dmx (the first draw's DMX beside its timeline). A batch is a pairs batch with
"kind": "reel" (pairs.py has the layout); key.json keeps each item's sides and why it was chosen.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import random
import sys
from typing import Any, Optional

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import checks  # noqa: E402
import common  # noqa: E402
import pairs  # noqa: E402

BEFORE_S, AFTER_S = 12.0, 14.0        # an item: this long before the transition and after it (owner, 28 Nov: +8 s each)
MAX_CHANGED, MAX_FLAGGED = 4, 5       # of the default 12: the rest is the pattern sample
PATTERN_SONGS = 4                     # the sample's songs per day
FEEDBACK = pathlib.Path.home() / ".local" / "state" / "screen-machine" / "show" / "ad_feedback.jsonl"


def _first_draws(run: pathlib.Path) -> dict[str, pathlib.Path]:
    """sid -> the first draw's karaoke timeline, where its DMX was recorded."""
    return {p.name.split(".")[0]: p for p in sorted(run.glob("*.karaoke.json")) if pairs._dmx_of(p).is_file()}


def _window(z: Any, t: float, a: float = BEFORE_S, b: float = AFTER_S) -> tuple[float, Optional[np.ndarray]]:
    d, f0, hop = z["dmx"], float(z["t0_s"]), float(z["hop_s"])
    t0 = max(f0, t - a)
    i0 = int(round((t0 - f0) / hop))
    i1 = min(len(d), int(round((t + b - f0) / hop)))
    return t0, (d[i0:i1] if i1 - i0 >= int(2.0 / hop) else None)


def _diff(a: np.ndarray, b: np.ndarray) -> float:
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    used = (a.std(0) > 0) | (b.std(0) > 0)
    if not used.any():
        return 0.0
    return float(np.mean(np.abs(a[:, used].astype(np.int16) - b[:, used].astype(np.int16)))) / 255.0


def _words(name: Any) -> str:
    return str(name or "").replace("-", " ")


def said(tl: dict, a: float, b: float) -> list[dict]:
    """What the art director decided in [a, b) s and why, in its own words, for the page to show beside the lights:
    [{t, text}] at the time each lands (a look change at its landing, the rest when decided)."""
    out: list[dict] = []
    for d in tl.get("decisions") or []:
        k, det, why = d.get("kind"), d.get("detail") or {}, d.get("why")
        t = float(d["land_s"] if d.get("land_s") is not None else d["t_s"])
        if not a <= t < b:
            continue
        text = None
        if k == "section_look":
            sec = det.get("section") or {}
            land = d.get("land_s")
            if land is not None and float(d["t_s"]) < float(land) - 0.3:
                t = float(d["t_s"])
                text = f"expects the {_words(sec.get('section_kind')) or 'next section'} in {float(land) - t:.1f} s" \
                       + (" (predicted)" if sec.get("predicted") else "")
        elif k == "look_change":
            tr = det.get("transition") or {}
            sec = det.get("section") or {}
            how = f"{tr.get('style')}" + (f" over {tr['beats']:g} beat{'s' if tr['beats'] != 1 else ''}"
                                         if isinstance(tr.get("beats"), (int, float)) and tr.get("style") != "cut" else "")
            text = f"new look for the {_words(sec.get('section_kind')) or 'section'}" \
                   + (" (recalled)" if det.get("recalled") else f" ({_words(det.get('reason'))})" if det.get("reason") else "") \
                   + (f", {how}" if tr.get("style") else "")
        elif k == "lineup":
            text = f"line-up: {_words(det.get('lineup'))} into the {_words(det.get('into'))}" \
                   + (f" ({det.get('how')})" if det.get("how") else "")
        elif k == "light_scene":
            text = f"lamps: {_words(det.get('scene'))}" + (f", {why}" if why else "")
        elif k == "move":
            text = str(why or det.get("move"))
        elif k == "arrival":
            text = f"arrival: {det.get('kind')} into the {_words(det.get('into'))}"
        elif k == "token" and det.get("decision") == "spent":
            text = f"spends a {_words(det.get('on'))} on the {_words(det.get('trigger'))}"
        elif k == "song_moment":
            text = f"plays a {det.get('kind')}" + (f": {why}" if why else "")
        elif k == "flow":
            text = f"movement {det.get('was')} → {det.get('dir')}" + (f", {why}" if why else "")
        elif k == "song_end":
            text = "the song's end: lights down"
        if text and t >= a:
            out.append({"t": round(t, 2), "text": text})
    out.sort(key=lambda x: x["t"])
    return out


def _section_of(ref: dict, t: float) -> list[float]:
    """The stretch the owner widens an item to: from the section before the transition to the end of the one after."""
    secs = ref.get("sections") or []
    i = next((k for k, s in enumerate(secs) if float(s["start_s"]) <= t + 0.5 < float(s["end_s"])), None)
    if i is None:
        return [max(0.0, t - 15.0), t + 15.0]
    a = float(secs[max(0, i - 1)]["start_s"]) if float(secs[i]["start_s"]) >= t - 0.5 else float(secs[i]["start_s"])
    return [round(a, 2), round(float(secs[i]["end_s"]), 2)]


def _reviewed() -> dict[str, int]:
    """How many of the owner's reel verdicts each check has had (the least judged checks are shown first)."""
    out: dict[str, int] = {}
    try:
        for ln in FEEDBACK.read_text().splitlines():
            try:
                o = json.loads(ln)
            except ValueError:
                continue
            if isinstance(o, dict) and o.get("from") == "reel" and o.get("check"):
                out[o["check"]] = out.get(o["check"], 0) + 1
    except OSError:
        pass
    return out


def make(a: argparse.Namespace) -> int:
    run = pathlib.Path(a.run)
    if not run.is_dir():
        run = common.CACHE / "runs" / a.run
    base = (pathlib.Path(a.vs) if pathlib.Path(a.vs).is_dir() else common.CACHE / "runs" / a.vs) if a.vs else None
    ours = _first_draws(run)
    if not ours:
        print(f"{run}: no DMX recorded (score with run.py --dmx)", file=sys.stderr)
        return 2
    theirs = _first_draws(base) if base else {}
    import reference
    lib = common.library()
    items: list[dict] = []
    taken: set[tuple[str, int]] = set()                 # (song, second): one item per moment
    refs: dict[str, dict] = {}
    tls: dict[str, dict] = {}
    for sid, p in ours.items():
        tl = json.loads(p.read_text())
        refs[sid] = tl.get("reference") or reference.load(sid)
        tls[sid] = tl
    # 1. changed: the night's fix, before and after, where the two look most different
    if theirs:
        cands = []
        moved: dict[str, list[float]] = {}            # where a check's answer changed: the fix's own effects
        for sid in set(ours) & set(theirs):
            fa = checks.failed(json.loads(theirs[sid].read_text()).get("checks") or [])
            fb = checks.failed(tls[sid].get("checks") or [])
            moved[sid] = [float(t) for _c, t in fa ^ fb]
        for sid in sorted(set(ours) & set(theirs)):
            za, zb = np.load(pairs._dmx_of(theirs[sid])), np.load(pairs._dmx_of(ours[sid]))
            for tr in checks.transitions(refs[sid]):
                t0, ca = _window(za, tr["t_s"])
                _, cb = _window(zb, tr["t_s"])
                if ca is None or cb is None:
                    continue
                d = _diff(ca, cb)
                if d >= pairs.MIN_DIFF:
                    cands.append((d, sid, tr, t0, ca, cb))
        # a moment whose check answer changed comes first; then the biggest look difference (which alone is often
        # the dice reshuffled downstream of the fix, not the fix)
        cands.sort(key=lambda c: (not any(abs(c[2]["t_s"] - t) <= 2.0 for t in moved.get(c[1], ())), -c[0]))
        per_song: dict[str, int] = {}
        for d, sid, tr, t0, ca, cb in cands:
            if len([i for i in items if i["kind"] == "changed"]) >= min(MAX_CHANGED, a.n):
                break
            if per_song.get(sid, 0) >= 1:
                continue
            per_song[sid] = 1
            taken.add((sid, int(tr["t_s"])))
            items.append({"kind": "changed", "sid": sid, "tr": tr, "t0": t0, "frames": [ca[:len(cb)], cb[:len(ca)]],
                          "sides": [("before", base, theirs[sid]), ("after", run, ours[sid])], "diff": round(d, 3)})
    # 2. flagged: a fault check's failures, the least judged checks first
    seen = _reviewed()
    fails = []
    for sid, tl in tls.items():
        res = tl.get("checks") or checks.run(tl, refs[sid])
        for r in res:
            if not r["ok"]:
                fails.append((seen.get(r["check"], 0), r["check"], sid, r))
    rng = random.Random(a.seed if a.seed is not None else dt.date.today().toordinal())
    rng.shuffle(fails)
    fails.sort(key=lambda f: f[0])
    used_pairs: set[tuple[str, str]] = set()
    by_check: dict[str, int] = {}
    room = min(MAX_FLAGGED, a.n - len(items))
    for _n, check, sid, r in fails:
        if room <= 0:
            break
        if (check, sid) in used_pairs or (sid, int(r["t_s"])) in taken or by_check.get(check, 0) >= 2:
            continue
        z = np.load(pairs._dmx_of(ours[sid]))
        t0, c = _window(z, float(r["t_s"]))
        if c is None:
            continue
        used_pairs.add((check, sid))
        by_check[check] = by_check.get(check, 0) + 1
        taken.add((sid, int(r["t_s"])))
        tr = next((x for x in checks.transitions(refs[sid]) if abs(x["t_s"] - r["t_s"]) <= 1.0), None)
        inside = next((x for x in refs[sid].get("sections") or ()
                       if float(x["start_s"]) <= float(r["t_s"]) < float(x["end_s"])), {})
        items.append({"kind": "flagged", "sid": sid, "tr": tr or {"t_s": float(r["t_s"]), "what": check,     # neutral:
                                                                     "label": f"in the {inside.get('kind') or 'song'}"},  # no verdict
                      "t0": t0, "frames": [c], "sides": [("show", run, ours[sid])], "check": check, "why": r["why"]})
        room -= 1
    # 3. pattern: the day's songs, their transitions in order
    day = dt.date.today().toordinal() if a.seed is None else a.seed
    order = sorted(ours)
    k0 = (day * PATTERN_SONGS) % max(1, len(order))
    today = [order[(k0 + j) % len(order)] for j in range(min(PATTERN_SONGS, len(order)))]
    pool = []
    for sid in today:
        trs = [t for t in checks.transitions(refs[sid]) if (sid, int(t["t_s"])) not in taken]
        pool.append([(sid, t) for t in trs])
    while len(items) < a.n and any(pool):
        for lst in pool:
            if not lst or len(items) >= a.n:
                continue
            sid, tr = lst.pop(rng.randrange(len(lst)))
            z = np.load(pairs._dmx_of(ours[sid]))
            t0, c = _window(z, tr["t_s"])
            if c is None:
                continue
            taken.add((sid, int(tr["t_s"])))
            items.append({"kind": "pattern", "sid": sid, "tr": tr, "t0": t0, "frames": [c],
                          "sides": [("show", run, ours[sid])]})
    if not items:
        print("nothing to review", file=sys.stderr)
        return 1
    # in song order within each kind, the kinds in order: the owner sees a song's moments together
    kinds = {"changed": 0, "flagged": 1, "pattern": 2}
    items.sort(key=lambda i: (kinds[i["kind"]], i["sid"], i["tr"]["t_s"]))
    batch = a.batch or ("reel-" + dt.datetime.now().strftime("%Y%m%d-%H%M"))
    out = pairs.PAIRS / batch
    if out.exists() and any(out.iterdir()):
        print(f"batch {batch} exists: pick another --batch", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    public, key = [], []
    for n, it in enumerate(items, 1):
        sid, tr = it["sid"], it["tr"]
        meta = common.song_meta(sid, lib)
        z = np.load(pairs._dmx_of(it["sides"][0][2]))
        hop = float(z["hop_s"])
        sides = list(it["sides"])
        frames = list(it["frames"])
        if len(sides) == 2 and rng.random() < 0.5:
            sides.reverse()
            frames.reverse()
        names = ["left", "right"][:len(frames)]
        common.write_json(out / f"clip-{n}.json", {nm: pairs._b64(f) for nm, f in zip(names, frames)})
        dur = float(refs[sid].get("end_s") or (tls[sid].get("song") or {}).get("duration_s") or 0.0)
        public.append({"n": n, "song": sid, "title": meta.get("title"), "artist": meta.get("artist"),
                       "t0_s": round(it["t0"], 2), "dur_s": round(len(frames[0]) * hop, 2), "hop_s": hop, "guide": 1.0,
                       "sides": len(frames), "kind": it["kind"], "at_s": round(float(tr["t_s"]), 2),
                       "label": tr.get("label"), "section": _section_of(refs[sid], tr["t_s"]),
                       "song_s": round(dur, 1),
                       "said": {nm: said(json.loads(pathlib.Path(sd[2]).read_text()) if sd[2] != it["sides"][0][2]
                                          or sid not in tls else tls[sid], it["t0"], it["t0"] + len(f) * hop)
                                for nm, sd, f in zip(names, sides, frames)}})
        key.append({"n": n, "song": sid, "kind": it["kind"], "t0_s": round(it["t0"], 2), "at_s": round(tr["t_s"], 2),
                    "what": tr.get("what"), "check": it.get("check"), "why": it.get("why"), "diff": it.get("diff"),
                    **{nm: {"side": s[0], "run": s[1].name, "seed": 0, "timeline": str(s[2]),
                            "scores": pairs._scores(s[2])} for nm, s in zip(names, sides)}})
    common.write_json(out / "pairs.json", {"batch": batch, "made_at": dt.datetime.now().isoformat(timespec="seconds"),
                                           "kind": "reel", "pairs": public})
    common.write_json(out / "key.json", {"batch": batch, "run": str(run), "vs": str(base) if base else None,
                                         "pairs": key})
    k = {x: sum(1 for i in items if i["kind"] == x) for x in kinds}
    print(f"reel {batch}: {len(public)} items ({k['changed']} changed, {k['flagged']} flagged, {k['pattern']} pattern), "
          f"about {sum(p['dur_s'] for p in public) / 60:.1f} min of play -> {out}  (open /lights/pairs)")
    return 0


def add_said(a: argparse.Namespace) -> int:
    d = pairs.PAIRS / a.batch
    pub = json.loads((d / "pairs.json").read_text())
    key = {int(k["n"]): k for k in json.loads((d / "key.json").read_text())["pairs"]}
    for it in pub["pairs"]:
        k = key.get(int(it["n"])) or {}
        it["said"] = {nm: said(json.loads(pathlib.Path(k[nm]["timeline"]).read_text()), it["t0_s"],
                               it["t0_s"] + it["dur_s"]) for nm in ("left", "right") if k.get(nm)}
    common.write_json(d / "pairs.json", pub)
    print(f"{a.batch}: what the a.d. decided added to {len(pub['pairs'])} items")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("said")
    w.add_argument("batch")
    m = sub.add_parser("make")
    m.add_argument("run")
    m.add_argument("--vs")
    m.add_argument("--n", type=int, default=12)
    m.add_argument("--batch")
    m.add_argument("--seed", type=int, default=None, help="the day's draw (default: today)")
    a = ap.parse_args()
    return add_said(a) if a.cmd == "said" else make(a)


if __name__ == "__main__":
    sys.exit(main())
