#!/usr/bin/env python3
"""Song-shape eval: does the art director FEEL a karaoke song's shape (its swells, its climax)?

Owner, 26 Nov: "a.d. really failed to 'feel' the swell and then climax of 'My Way'. it's a good test case. we need
to build some evals for song shapes and a.d. handling logic".

For each karaoke song (its separated backing + lead vocal = the original mix, from the karaoke library):

1. **The song's shape** (the reference), from the audio file itself, independent of show_service: short-term
   loudness (EBU R128, 3 s window) every 0.5 s and spectral flux (how busy it is), each normalised within the song
   (its 5th..98th percentile -> 0..1), mixed 0.75 / 0.25 and smoothed over SMOOTH_S.  The climax is the 20 s
   window where that is highest.
2. **The director's response**: show_service's own Features (dsp.py) run over the mix as a clean feed, block by
   block on simulated time, and a Director with the deployment's real settings (show.yaml + the site layer), vibes,
   vocabulary and patch, ticked at 10 Hz with the karaoke context the live feed gives it (karaoke_feed: the song's
   facts, lyrics and lyric sections at the play position).  Recorded: the energy it directs (room.direction).
   ``--mic DB`` attenuates the feed by DB (the laptop's mic hears the screen ~30 dB down) to test that too.
3. **Scores** (each 0..1 and a plain verdict):
   * ``follow``  Pearson correlation of the directed energy with the shape (1 s steps, best lag within 0..LAG_MAX_S);
   * ``climax``  how far above its own song average the director is during the climax (0.15 up = 1);
   * ``range``   the spread of the directed energy (its p90 - p10; 0.4 = 1);
   * ``lag``     seconds the director trails the shape by (the best lag);
   * ``score``   0.5 follow + 0.3 climax + 0.2 range.
4. A chart per song (the shape, the directed energy, the lyric sections, the climax) and a JSON, in OUT/<song>/.

    ~/.venvs/sm-show/bin/python tools/room/shape_eval/run.py "My Way" "Hallelujah" ["Heroes"...] [--mic -30]
        [--out ~/.cache/sm-shape-eval]

Titles match the karaoke library's (case-insensitive substring).  Heavy-ish (the dsp over every block): run it
``nice -n 19``.  No network, no AI (the treatment's AI is off: no spawn), deterministic.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import subprocess
import sys
import time

import numpy as np

REPO = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

import smconfig  # noqa: E402
from show_service import contract  # noqa: E402
from show_service import director as dmod  # noqa: E402
from show_service import dsp  # noqa: E402
from show_service import palette as pal  # noqa: E402
from show_service.lyric_sections import sections_from_lyrics  # noqa: E402
from show_service.vibes import Vibes  # noqa: E402
from utils import song_shape  # noqa: E402

FS = 48000
BLOCK = 1024
TICK_S = 0.1
LAG_MAX_S = 15
T0 = 1000.0
STREAM = "eval"


# -- the song ----------------------------------------------------------------------------------------------------------
def karaoke_state() -> dict:
    return json.loads((smconfig.state("karaoke") / "state.json").read_text())


def find(title: str, songs: dict) -> tuple[str, dict]:
    t = title.lower()
    for sid, s in songs.items():
        if s.get("status") == "ready" and t in str(s.get("title") or "").lower():
            return sid, s
    raise SystemExit(f"no ready karaoke song matches {title!r}")


def mix(sid: str) -> np.ndarray:
    return song_shape.decode_mix(smconfig.state("karaoke") / "songs" / sid, FS)


# -- 1. the song's shape (utils/song_shape.py: the one measurement) ----------------------------------------------------
def shape(pcm: np.ndarray) -> dict:
    d = song_shape.measure(pcm, FS)
    return {"t": (np.arange(len(d["v"])) * d["hop"]).tolist(), "ref": d["v"], "loud": d["loud"], "climax": d["climax"]}


# -- 2. the director's response ----------------------------------------------------------------------------------------
def settings() -> dict:
    from show_service.brain import parse_settings
    base = parse_settings((REPO / "config/room/show.yaml").read_bytes())
    site = smconfig.path("show.yaml")
    if site.is_file():
        base = smconfig.deep_merge(base, parse_settings(site.read_bytes()))
    return base


def music_context(t_s: float, sec: dict) -> dict:
    """The structure's MusicContext while the lyric plan runs (structure.py _plan_view): its section and plan."""
    return {"t_ms": t_s * 1000.0, "stream": STREAM, "source": "ml",
            "genre": {"word": None, "conf": 0.0, "parent": None, "parent_p": 0.0, "via": "none"},
            "moods": [], "instruments": [], "vocal": None, "dance": None, "arousal": None, "engagement": None,
            "bright": None, "acoustic": None, "key": None, "mode": None, "key_strength": 0.0, "key_via": None,
            "talking": False, "ced": None,
            "section": {"id": sec["id"], "letter": sec["letter"], "repeat_of": None, "novelty": 0.1, "phase": "calm",
                        "build_progress": 0.0, "drop_eta_beats": None, "bars_in_section": sec["bars"],
                        "started_ms": sec["start"] * 1000.0, "expected_bars": sec["expected"], "chorus":
                        sec["kind"] == "chorus", "label": None, "kind": sec["kind"], "plan": sec["plan"]},
            "track": {"id": 1, "changed": False, "age_s": 10.0, "via": None},
            "ml": {"state": "running", "styles_top3": [], "moods_top5": [], "instruments_top5": [], "ages": {}}}


def replay(sid: str, song: dict, pcm: np.ndarray, mic_db: float, tmp: pathlib.Path, vj: bool = False) -> dict:
    lyr = json.loads((smconfig.state("karaoke") / "songs" / sid / "lyrics.json").read_text())
    dur = len(pcm) / FS
    off = (float(song.get("offset_ms") or 0) + float(song.get("user_offset_ms") or 0)) / 1000.0
    secs = sections_from_lyrics(lyr, dur)
    secs = [{**x, "start_s": x["start_s"] + off} for x in secs]
    lines = [ln.get("text", "") for ln in lyr.get("lines") or [] if isinstance(ln, dict)]
    text = " ".join(lines)
    from show_service.song_character import excerpt
    state = {"t": T0, "feat": None}
    pubs: list = []
    ctx = contract.BrainContext(publish=pubs.append)

    def cur_section():
        pos = state["t"] - T0
        i = max([k for k, x in enumerate(secs) if x["start_s"] <= pos] or [0])
        if not secs:
            return None
        x = secs[i]
        nxt = secs[i + 1] if i + 1 < len(secs) else None
        lens = [b["start_s"] - a["start_s"] for a, b in zip(secs, secs[1:])] or [30.0]
        return {"id": i + 1, "letter": x["letter"], "kind": x["kind"], "start": T0 + x["start_s"],
                "bars": int((pos - x["start_s"]) / 2.0), "expected": None,
                "plan": {"next_kind": nxt["kind"] if nxt else None,
                         "next_in_s": (nxt["start_s"] - pos) if nxt else None,
                         "median_s": float(np.median(lens))}}

    ctx.music_context = (lambda: None) if vj else (lambda: (lambda s: music_context(state["t"], s) if s else None)(
        cur_section()))
    ctx.features = lambda: state["feat"]
    ctx.clock = lambda: None
    ctx.piano = lambda: None
    ctx.room = lambda: None
    ctx.situations = lambda: None
    ctx.rhythm = lambda: None
    ctx.harmony = lambda: None
    ctx.section_change = lambda: None
    ctx.set_look_out = lambda **kw: None
    ctx.set_gate_ref = lambda v: None
    ctx.persist = lambda: None
    ctx.notify = lambda k, p: None
    ctx.now_playing = (lambda: None) if vj else lambda: {
        "key": f"karaoke:{sid}:eval", "state": "playing", "pos_s": state["t"] - T0, "dur_s": dur, "speed": 1.0,
        "title": f"{song.get('title')} — {song.get('artist')}", "source": "karaoke", "artist": song.get("artist"),
        "genre": song.get("genre"), "year": song.get("year"),
        "sections": [{"pos_s": x["start_s"], "kind": x["kind"], "letter": x["letter"]} for x in secs],
        "lyrics_text": text, "lyrics_excerpt": excerpt(lines)}
    cat = pal.Catalog()
    vibes = Vibes(REPO / "config/room/vibes.yaml", vocab_path=REPO / "config/live/vocab.yaml", palette_names=cat.names)
    d = dmod.Director(ctx, vibes=vibes, catalog=cat, patch_path=smconfig.path("lights", "patch.yaml"),
                      log_path=tmp / "director.jsonl", wall=lambda: 1.8e9 + state["t"], mono=lambda: state["t"])
    d.configure(settings())
    d.start()
    feats = dsp.Features(publish=lambda m: None)
    gain = 10.0 ** (mic_db / 20.0)
    rows = []
    next_tick = T0 + TICK_S
    for n0 in range(0, len(pcm) - BLOCK, BLOCK):
        blk = (pcm[n0:n0 + BLOCK] * gain).reshape(-1, 1)
        t_end = T0 + (n0 + BLOCK) / FS
        feats.process(STREAM, n0, T0 + n0 / FS, blk, FS, 0)
        while next_tick <= t_end:
            state["t"] = next_tick
            feats.tick(next_tick)
            state["feat"] = feats.latest()
            d.tick(next_tick)
            dirs = [p["data"] for p in pubs if p.get("type") == contract.ROOM_DIRECTION]
            pubs.clear()
            if dirs:
                m = d.music or {}
                rows.append((next_tick - T0, float(dirs[-1]["energy"]), float(m.get("band_energy") or 0.0),
                             float((state["feat"] or {}).get("lufs_s") or -70.0)))
            next_tick = round(next_tick + TICK_S, 6)
    t = np.array([r[0] for r in rows])
    return {"t": t.tolist(), "energy": [r[1] for r in rows], "band": [r[2] for r in rows],
            "lufs_s": [r[3] for r in rows], "sections": secs}


# -- 3. scores ---------------------------------------------------------------------------------------------------------
def score(sh: dict, rp: dict) -> dict:
    tt = np.arange(0.0, min(sh["t"][-1], rp["t"][-1]), 1.0)
    ref = np.interp(tt, sh["t"], sh["ref"])
    en = np.interp(tt, rp["t"], rp["energy"])
    best, lag = -1.0, 0
    for L in range(0, LAG_MAX_S + 1):
        a, b = ref[:len(ref) - L], en[L:]
        if len(a) > 20 and np.std(b) > 1e-6:
            c = float(np.corrcoef(a, b)[0, 1])
            if c > best:
                best, lag = c, L
    c0, c1 = sh["climax"]
    inc = (tt >= c0) & (tt <= c1 + lag)
    climax_up = float(en[inc].mean() - en.mean()) if inc.any() else 0.0
    spread = float(np.percentile(en, 90) - np.percentile(en, 10))
    follow = max(0.0, best)
    s = {"follow": round(follow, 3), "climax_up": round(climax_up, 3),
         "climax": round(min(1.0, max(0.0, climax_up / 0.15)), 3), "range": round(min(1.0, spread / 0.4), 3),
         "spread": round(spread, 3), "lag_s": lag, "energy_mean": round(float(en.mean()), 3)}
    s["score"] = round(0.5 * s["follow"] + 0.3 * s["climax"] + 0.2 * s["range"], 3)
    s["verdict"] = ("feels it" if s["score"] >= 0.7 else "partly" if s["score"] >= 0.45 else "doesn't feel it")
    return s


# -- 4. the chart ------------------------------------------------------------------------------------------------------
def chart(path: pathlib.Path, title: str, sh: dict, rp: dict, sc: dict) -> None:
    from PIL import Image, ImageDraw
    W, H, L, R, T, B = 1400, 520, 60, 20, 50, 60
    im = Image.new("RGB", (W, H), (250, 249, 246))
    dr = ImageDraw.Draw(im)
    dur = max(sh["t"][-1], rp["t"][-1])
    X = lambda t: L + (W - L - R) * t / dur        # noqa: E731
    Y = lambda v: T + (H - T - B) * (1.0 - v)      # noqa: E731
    kind_col = {"verse": (232, 236, 242), "chorus": (246, 232, 214), "instrumental": (226, 240, 230),
                "outro": (236, 230, 244)}
    secs = rp["sections"]
    for i, x in enumerate(secs):
        x1 = secs[i + 1]["start_s"] if i + 1 < len(secs) else dur
        dr.rectangle([X(x["start_s"]), T, X(x1), H - B], fill=kind_col.get(x["kind"], (240, 240, 240)))
        dr.text((X(x["start_s"]) + 3, H - B + 4), x["kind"], fill=(110, 110, 110))
    c0, c1 = sh["climax"]
    dr.rectangle([X(c0), T, X(c1), T + 6], fill=(200, 60, 40))
    dr.text((X(c0), T - 14), "climax", fill=(200, 60, 40))
    for v in (0.0, 0.25, 0.5, 0.75, 1.0):
        dr.line([L, Y(v), W - R, Y(v)], fill=(215, 215, 215))
        dr.text((8, Y(v) - 6), f"{v:.2f}", fill=(120, 120, 120))
    for m in range(0, int(dur) + 1, 30):
        dr.text((X(m) - 10, H - B + 20), f"{m // 60}:{m % 60:02d}", fill=(120, 120, 120))

    def line(ts, vs, col, w):
        pts = [(X(t), Y(min(max(v, 0.0), 1.0))) for t, v in zip(ts, vs)]
        dr.line(pts, fill=col, width=w)
    line(sh["t"], sh["ref"], (60, 60, 60), 3)
    line(rp["t"], rp["energy"], (30, 110, 200), 2)
    dr.text((L, 12), f"{title}   score {sc['score']} ({sc['verdict']}): follow {sc['follow']}, climax "
                     f"{sc['climax_up']:+.2f}, range {sc['spread']}, lag {sc['lag_s']} s", fill=(30, 30, 30))
    dr.text((W - 420, 12), "grey: the song's shape   blue: the director's energy", fill=(30, 30, 30))
    im.save(path)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("titles", nargs="+")
    ap.add_argument("--mic", type=float, default=0.0, help="attenuate the feed by this many dB (e.g. -30)")
    ap.add_argument("--out", default=str(pathlib.Path.home() / ".cache/sm-shape-eval"))
    ap.add_argument("--tag", default="current")
    ap.add_argument("--vj", action="store_true", help="no karaoke context: the live audio alone (a DJ set's case)")
    a = ap.parse_args()
    st = karaoke_state()
    out = pathlib.Path(a.out)
    rows = []
    for title in a.titles:
        sid, song = find(title, st["songs"])
        t0 = time.time()
        pcm = mix(sid)
        sh = shape(pcm)
        d = out / f"{sid}"
        d.mkdir(parents=True, exist_ok=True)
        rp = replay(sid, song, pcm, a.mic, d, a.vj)
        sc = score(sh, rp)
        name = f"{song.get('title')} — {song.get('artist')}"
        tag = a.tag + ("_vj" if a.vj else "") + (f"_mic{int(a.mic)}" if a.mic else "")
        chart(d / f"{tag}.png", name, sh, rp, sc)
        (d / f"{tag}.json").write_text(json.dumps({"song": name, "score": sc, "shape": sh, "replay": rp}))
        rows.append((name, sc))
        print(f"{name:45s} {sc['score']:.2f} {sc['verdict']:16s} follow {sc['follow']:.2f}  climax "
              f"{sc['climax_up']:+.2f}  range {sc['spread']:.2f}  lag {sc['lag_s']:2d} s   ({time.time() - t0:.0f} s)",
              flush=True)
    if len(rows) > 1:
        print(f"{'mean':45s} {np.mean([r[1]['score'] for r in rows]):.2f}")


if __name__ == "__main__":
    main()
