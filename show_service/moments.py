"""A song's moments, performed: which of utils/song_moments.py's a song gets, and what each one does to the lights.

Owner, 27 Nov: "there are circumstances in a song where e.g. there's very strong rhythmic section, strong drumming,
orchestral hit, etc. ... a stronger situational lighting response"; "endings that just stop suddenly rather than which
fade out -- we'd expect a blackout ... we want to do this smartly in config and logic, not in hard coded patches".

config/live/moments.yaml says it all: per kind of moment its response (an envelope for the look engine, phrasing.py;
a unison hit; the scene's drop flash), and per idiom how many of each a song gets. This module is pure: the director
hands it the karaoke song's moments (song time) and where the song is, and gets back what to put in the look.

  choose(moments, idiom, beat_s, cfg)   the song's moments it will play: by salience, at most so many of each kind,
                                        two of a kind at least gap_bars apart; its ending always
  perform(chosen, pos_s, now_s, beat_s, cfg, idiom)
                                        -> {"envelopes": [...], "hits": [(t0_s, t1_s)], "flashes": [(key, at_s)]}
                                        for the moments from just behind ``now`` to lead_s ahead, in host time
"""
from __future__ import annotations

import pathlib
from typing import Any, Mapping, Optional

PATH = pathlib.Path(__file__).resolve().parents[1] / "config" / "live" / "moments.yaml"
FALLBACK: dict = {"version": 1, "lead_s": 3.0, "kinds": {}, "choose": {"min_salience": 0.5, "gap_bars": 8,
                                                                       "default": {}, "by_idiom": {}}}
ENVELOPES = ("bump", "stop", "out")


def load(path: Optional[pathlib.Path] = None) -> dict:
    """moments.yaml, or FALLBACK (nothing chosen) when it is missing or broken."""
    import yaml
    try:
        raw = yaml.safe_load((path or PATH).read_text()) or {}
    except (OSError, ValueError, yaml.YAMLError):
        return dict(FALLBACK)
    if not isinstance(raw, dict):
        return dict(FALLBACK)
    out = {**FALLBACK, **raw}
    out["choose"] = {**FALLBACK["choose"], **(raw.get("choose") or {})}
    return out


def _idiom_cfg(cfg: Mapping[str, Any], idiom: Optional[str]) -> dict:
    ch = cfg.get("choose") or {}
    return {**(ch.get("default") or {}), **((ch.get("by_idiom") or {}).get(idiom or "") or {})}


def recipe(cfg: Mapping[str, Any], m: Mapping[str, Any]) -> Optional[dict]:
    """A moment's response from moments.yaml: kinds.<kind>, or kinds.end.<style>; None when it has none."""
    k = (cfg.get("kinds") or {}).get(m.get("kind"))
    if m.get("kind") == "end" and isinstance(k, Mapping):
        k = k.get(m.get("style"))
    return dict(k) if isinstance(k, Mapping) and k.get("envelope") in ENVELOPES else None


def choose(moments: Any, idiom: Optional[str], beat_s: float, cfg: Mapping[str, Any]) -> list[dict]:
    """The moments this song plays, in time order (each a copy, with ``n``: its place in the song's list)."""
    if not isinstance(moments, (list, tuple)):
        return []
    ch = cfg.get("choose") or {}
    lim = _idiom_cfg(cfg, idiom)
    gap = float(ch.get("gap_bars") or 0.0) * 4.0 * max(0.2, float(beat_s or 0.5))
    floor_sal = float(ch.get("min_salience") or 0.0)
    picked: list[dict] = []
    by_kind: dict[str, list[dict]] = {}
    for n, m in enumerate(moments):
        if not isinstance(m, Mapping) or not isinstance(m.get("t"), (int, float)) or recipe(cfg, m) is None:
            continue
        if m.get("kind") == "end":
            picked.append({**m, "n": n})
            continue
        if float(m.get("salience") or 0.0) < floor_sal:
            continue
        by_kind.setdefault(str(m["kind"]), []).append({**m, "n": n})
    for kind, ms in by_kind.items():
        cap = int(lim.get(kind, 0) or 0)
        took: list[dict] = []
        for m in sorted(ms, key=lambda x: (-float(x.get("salience") or 0.0), float(x["t"]))):
            if len(took) >= cap:
                break
            if all(abs(float(m["t"]) - float(o["t"])) >= gap for o in took):
                took.append(m)
        picked += took
    return sorted(picked, key=lambda m: float(m["t"]))


def perform(chosen: list[dict], pos_s: float, now_s: float, beat_s: float, cfg: Mapping[str, Any],
            idiom: Optional[str]) -> dict:
    """What the chosen moments put in the look now: envelopes (phrasing.envelope's, ``at_ms`` in host time), unison
    hits (t0_s, t1_s) and drop flashes (key, at_s), for every moment from just behind now to lead_s ahead. An ending
    stays in once reached (its lights stay down until the song changes)."""
    lead = float(cfg.get("lead_s") or 3.0)
    lim = _idiom_cfg(cfg, idiom)
    lift_scale = float(lim.get("lift_scale", 1.0))
    beat = max(0.2, float(beat_s or 0.5))
    host = lambda t: now_s + (float(t) - pos_s)                      # noqa: E731  song time -> host time
    out: dict = {"envelopes": [], "hits": [], "flashes": []}
    for m in chosen:
        r = recipe(cfg, m)
        if r is None:
            continue
        t, end = float(m["t"]), float(m.get("end", m["t"]))
        sal = float(m.get("salience") or 1.0)
        settle = float(r.get("settle_beats") or 1.0) * beat
        line = end if r["envelope"] == "stop" else t
        if t - pos_s > lead:
            continue                                                    # not yet
        if r["envelope"] != "out" and line - pos_s < -settle - 0.5:
            continue                                                    # over (an ending never is)
        key = f"{m.get('kind')}:{m.get('n')}"
        lift = round(float(r.get("lift") or 0.0) * sal * lift_scale, 3)
        if r["envelope"] == "bump":
            out["envelopes"].append({"at_ms": round(host(t) * 1000.0, 1), "kind": "bump", "wind": 0.0, "lift": lift,
                                     "wind_ms": 0.0, "settle_ms": round(settle * 1000.0, 1), "beat_ms": 0.0,
                                     "key": key})
        elif r["envelope"] == "stop":
            floor = float(lim.get("floor", r.get("floor", 0.0)))
            out["envelopes"].append({"at_ms": round(host(end) * 1000.0, 1), "kind": "stop", "lift": lift,
                                     "dark_ms": round(max(0.0, end - t) * 1000.0, 1), "floor": floor,
                                     "settle_ms": round(settle * 1000.0, 1), "key": key})
        else:                                                           # out: the song's last light
            follow = r.get("fade") == "follow"
            down = t + float(r.get("hold_s") or 0.0)
            fade_s = max(0.0, end - down) if follow else float(r.get("fade_s") or 0.0)
            out["envelopes"].append({"at_ms": round(host(down + fade_s) * 1000.0, 1), "kind": "out",
                                     "fade_ms": round(fade_s * 1000.0, 1),
                                     "floor": float(lim.get("floor_end", r.get("floor", 0.0))), "key": key})
        hb = float(r.get("hit_beats") or 0.0) * beat
        if hb > 0 and r["envelope"] != "out":
            out["hits"].append((host(line), host(line) + hb))
        if r.get("flash") and r["envelope"] != "out":
            out["flashes"].append((key, host(line)))
    return out
