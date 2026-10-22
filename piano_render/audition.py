"""The audition set: five short excerpts of the test pieces played on every instrument, as tagged 16-bit FLACs to
listen to on PC22 (C:\\Users\\Greg\\Music\\Piano audition). This is how the 22 Oct set was made, so it can be made
again after an instrument changes.

    python -m piano_render audition [--out DIR] [--instruments ID ...] [--excerpts N ...] [--no-ab] [--work DIR]

1. cut_excerpt() cuts each excerpt (EXCERPTS) out of config/live/test-piano:
   - excerpts starting at 0 keep the piece's own timing;
   - a later start snaps to the note with the largest gap before it within +-3 s of the target; the excerpt begins
     0.3 s before that note, with CC64/66/67 set to their values at that moment (the pedal carried in) and no notes
     from before it (pedal changes in those 0.3 s are kept);
   - the end snaps to the note with the largest gap before it within +-4 s of the target (at least 20 s after the
     start); that note and everything after it is dropped, and keys still down are released and all three pedals
     lifted 2 s after the cut, so it rings out naturally.
2. Each excerpt is rendered with render.render() exactly as the live path renders a piece: the instrument's own
   room, realism on and its gain_db. The default instrument also gets two A/B files: "- dry samples" (realism off)
   and "- no room" (realism on, room "none"). Renders go to the audition's own cache (<work>/renders), never the
   live render cache.
3. export_flac() writes each one as FLAC 16-bit 48 kHz, TPDF-dithered (seeded by the file name, so a file is
   reproducible), with the trailing near-silence trimmed (trim_tail: 0.5 s after the last 50 ms window above
   -70 dBFS, faded out over 0.5 s), tagged (title, artist = instrument, album, track number, comment), written to a
   temp name and renamed over any old file of the same name. Then it measures the written file.

The measurements (LUFS, true peak, sample peak, clipped samples, the quietest 100 ms inside the music, ...) are
merged by file name into <work>/table.json and returned. <work> defaults to ~/.cache/screen-machine/piano/audition.
Nothing is played.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

import numpy as np

from . import instruments as _instruments
from . import score as _score

REPO = Path(__file__).resolve().parent.parent
LIBRARY = REPO / "config" / "live" / "test-piano"
AUDITION_DIR = Path("/mnt/c/Users/Greg/Music/Piano audition")       # PC22's C:\Users\Greg\Music\Piano audition
WORK_DIR = Path("~/.cache/screen-machine/piano/audition").expanduser()
ALBUM = "Piano audition (22 Oct 2026)"
SR = 48000

LEAD_S = 0.3              # the excerpt starts this long before its first note (pedals carried in)
HOLD_S = 2.0              # keys still down at the cut are released, and the pedals lifted, this long after it
START_WINDOW_S = 3.0
END_WINDOW_S = 4.0
MIN_LENGTH_S = 20.0       # the end snaps to a note at least this long after the first note
TAIL_FLOOR_DBFS = -70.0   # trim_tail: 50 ms windows (mean square of both channels) above this are music
TAIL_KEEP_S = 0.5
TAIL_FADE_S = 0.5
QUIET_BLOCK_DBFS = -60.0  # a 100 ms block inside the music below this counts as a dropout

Log = Callable[[str], None]


@dataclass(frozen=True)
class Excerpt:
    n: int
    name: str           # the file-name prefix: "1 Chopin Nocturne"
    source: str         # a .mid under LIBRARY
    start_s: float      # targets; cut_excerpt snaps them
    end_s: float
    title: str          # the tag's title prefix

    @property
    def path(self) -> Path:
        return LIBRARY / self.source


EXCERPTS = [
    Excerpt(1, "1 Chopin Nocturne", "Classical - Chopin - Nocturne in E-flat, Op. 9 No. 2.mid", 0, 40,
            "Chopin, Nocturne Op. 9 No. 2 (opening)"),
    Excerpt(2, "2 Debussy Reflets", "Classical - Debussy - Reflets dans l'eau.mid", 0, 41,
            "Debussy, Reflets dans l'eau (opening)"),
    Excerpt(3, "3 Bach Prelude", "Classical - Bach - Prelude and Fugue in F minor, WTC I.mid", 0, 40,
            "Bach, Prelude in F minor, WTC I (opening)"),
    Excerpt(4, "4 Bill Evans Rainy Day", "Jazz - Bill Evans - Here's That Rainy Day.mid", 165, 205,
            "Bill Evans, Here's That Rainy Day (from 2:46)"),
    Excerpt(5, "5 Rachmaninoff Prelude", "Classical - Rachmaninoff - Prelude in A minor, Op. 32 No. 8.mid", 28, 68,
            "Rachmaninoff, Prelude Op. 32 No. 8 (from 0:29)"),
]

# kind -> (file-name suffix, render.render keyword arguments)
KINDS = {"full": ("", {}), "dry": (" - dry samples", {"realism": False}), "noroom": (" - no room", {"room": "none"})}


# --- 1. the excerpt cutter -----------------------------------------------------------------------------------
def _snap(onsets: list, target: float, window: float, after: float = -1.0) -> tuple[Optional[float], float]:
    """The onset within +-window of target (and later than ``after``) with the largest gap before it."""
    best, gap = None, -1.0
    for i, t in enumerate(onsets):
        if i > 0 and abs(t - target) <= window and t > after:
            g = t - onsets[i - 1]
            if g > gap:
                best, gap = t, g
    return best, gap


def cut_events(perf: _score.Performance, start_s: float, end_s: Optional[float]) -> tuple[list, dict]:
    """The excerpt's events [(t_s, kind, num, value)] (time 0 = the excerpt's start) and what was decided."""
    evs = perf.events
    onsets = sorted({e.t_s for e in evs if e.kind == "on"})
    if not onsets:
        raise ValueError("no notes to cut from")
    if start_s <= 0:
        w0, t0, sgap = 0.0, onsets[0], None
    else:
        t0, sgap = _snap(onsets, start_s, START_WINDOW_S)
        if t0 is None:
            raise ValueError(f"no note within {START_WINDOW_S} s of {start_s} s")
        w0 = t0 - LEAD_S
    t1, egap = (None, -1.0) if end_s is None else _snap(onsets, end_s, END_WINDOW_S, after=t0 + MIN_LENGTH_S)
    if t1 is None:                                   # no note near the end target: keep everything to the end
        t1 = math.inf
    state: dict[int, int] = {}
    for e in evs:
        if e.t_s >= w0:
            break
        if e.kind == "cc":
            state[e.num] = e.value
    out = [(0.0, "cc", num, v) for num, v in sorted(state.items())]
    down: set[int] = set()
    last = 0.0
    for e in evs:
        if e.t_s < w0:
            continue
        if e.t_s >= t1 - 1e-9:
            break
        t = e.t_s - w0
        last = t
        if e.kind == "on":
            if e.t_s < t0 - 1e-9:
                continue                             # the lead-in carries pedals only, never the last phrase's notes
            down.add(e.num)
            out.append((t, "on", e.num, e.value))
        elif e.kind == "off":
            if e.num in down:
                down.discard(e.num)
                out.append((t, "off", e.num, e.value))
        else:
            out.append((t, "cc", e.num, e.value))
    tend = (t1 - w0 if math.isfinite(t1) else last) + HOLD_S
    out += [(tend, "off", k, 64) for k in sorted(down)]
    out += [(tend, "cc", num, 0) for num in (64, 66, 67)]
    info = {"from_s": round(w0, 3), "first_note_s": round(t0, 3),
            "cut_at_s": round(t1, 3) if math.isfinite(t1) else None,
            "start_gap_s": None if sgap is None else round(sgap, 3), "end_gap_s": round(egap, 3) if egap >= 0 else None,
            "excerpt_s": round(tend, 3), "notes": sum(1 for o in out if o[1] == "on"), "carried_in": state}
    return out, info


def write_midi(path: Path, events: list) -> None:
    """Type 0, 0.1 ms ticks (tempo 1 s per beat, 10000 ticks per beat); events already in time order. Written to a
    temp name and renamed, and left alone when the bytes are the same (the render cache keys on them)."""
    import io

    import mido
    mf = mido.MidiFile(type=0, ticks_per_beat=10000)
    tr = mido.MidiTrack()
    mf.tracks.append(tr)
    tr.append(mido.MetaMessage("set_tempo", tempo=1_000_000, time=0))
    now = 0
    for t, kind, a, b in events:
        tick = int(round(t * 10000))
        dt, now = tick - now, tick
        if kind == "on":
            tr.append(mido.Message("note_on", note=a, velocity=b, time=dt))
        elif kind == "off":
            tr.append(mido.Message("note_off", note=a, velocity=b, time=dt))
        else:
            tr.append(mido.Message("control_change", control=a, value=b, time=dt))
    tr.append(mido.MetaMessage("end_of_track", time=1000))
    buf = io.BytesIO()
    mf.save(file=buf)
    data = buf.getvalue()
    path = Path(path)
    if path.exists() and path.read_bytes() == data:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp{os.getpid()}")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def cut_excerpt(src: "str | Path", dst: "str | Path", start_s: float, end_s: Optional[float]) -> dict:
    """Cut ``src`` (a .mid) from start_s to end_s (snapped, see the module doc) into ``dst``; returns what was cut."""
    events, info = cut_events(_score.load_midi(src), start_s, end_s)
    write_midi(Path(dst), events)
    return {"src": str(src), "dst": str(dst), **info}


# --- 3. export -------------------------------------------------------------------------------------------------
def trim_tail(y: np.ndarray, sr: int, floor_dbfs: float = TAIL_FLOOR_DBFS, keep_s: float = TAIL_KEEP_S,
              fade_s: float = TAIL_FADE_S) -> tuple[np.ndarray, float]:
    """End ``keep_s`` after the last 50 ms window whose mean square (both channels) is above floor_dbfs, faded out
    over fade_s (raised cosine). Returns (a new float32 array, seconds trimmed)."""
    w = sr // 20
    nw = y.shape[0] // w
    lv = 10 * np.log10(np.mean(np.square(y[:nw * w].reshape(nw, w, y.shape[1])), axis=(1, 2)) + 1e-30)
    loud = np.flatnonzero(lv > floor_dbfs)
    end = min(y.shape[0], (int(loud[-1]) + 1) * w + int(keep_s * sr)) if loud.size else y.shape[0]
    out = np.array(y[:end], np.float32)
    fade = min(end, int(fade_s * sr))
    if fade > 0:
        out[end - fade:] *= (0.5 + 0.5 * np.cos(np.linspace(0, np.pi, fade)))[:, None].astype(np.float32)
    return out, round((y.shape[0] - end) / sr, 2)


def dither16(y: np.ndarray, seed: int) -> np.ndarray:
    """float -> int16 with TPDF dither (+-1 LSB)."""
    rng = np.random.default_rng(seed)
    q = np.round(y.astype(np.float64) * 32767.0 + (rng.random(y.shape) - rng.random(y.shape)))
    return np.clip(q, -32768, 32767).astype(np.int16)


def measure(z: np.ndarray, sr: int, music_from_s: float = 0.0, music_to_s: Optional[float] = None) -> dict:
    """Loudness and sanity numbers of a written file (float -1..1): LUFS, true and sample peak, clipped samples,
    the quietest 100 ms block and the longest run under QUIET_BLOCK_DBFS between music_from_s and music_to_s,
    and when sound first rises above -50 dBFS."""
    from . import master as _master
    blk = sr // 10
    seg = z[int(music_from_s * sr):int((music_to_s if music_to_s is not None else z.shape[0] / sr) * sr)]
    nb = seg.shape[0] // blk
    rms_db = 20 * np.log10(np.sqrt(np.mean(np.square(seg[:nb * blk].reshape(nb, blk, z.shape[1])), axis=(1, 2)))
                           + 1e-30) if nb else np.array([0.0])
    longest = run = 0
    for q in rms_db < QUIET_BLOCK_DBFS:
        run = run + 1 if q else 0
        longest = max(longest, run)
    idx = np.flatnonzero(np.max(np.abs(z), axis=1) > 10 ** (-50 / 20))
    return {"lufs": round(_master.integrated_loudness(z, sr), 2), "tp": round(_master.db(_master.true_peak(z)), 2),
            "sp": round(_master.db(_master.sample_peak(z)), 2),
            "clipped_samples": int(np.sum(np.abs(z) >= 32767 / 32768)),
            "longest_quiet_s": round(longest / 10, 1), "min_block_db": round(float(rms_db.min()), 1),
            "first_audible_s": round(idx[0] / sr, 3) if idx.size else None, "duration_s": round(z.shape[0] / sr, 2)}


def export_flac(src: "str | Path", dst: "str | Path", *, title: str, artist: str, album: str = ALBUM,
                tracknumber: Optional[int] = None, comment: str = "") -> dict:
    """A rendered FLAC -> the audition's 16-bit FLAC at ``dst`` (trimmed, dithered, tagged, temp name + rename)."""
    import soundfile as sf
    x, sr = sf.read(str(src), dtype="float32", always_2d=True)
    y, trimmed = trim_tail(x, sr)
    dst = Path(dst)
    seed = int.from_bytes(hashlib.sha256(dst.name.encode()).digest()[:8], "little")
    q = dither16(y, seed)
    tmp = dst.with_name(f".{dst.name}.tmp{os.getpid()}")
    try:
        with sf.SoundFile(str(tmp), "w", sr, q.shape[1], "PCM_16", format="FLAC") as fh:
            fh.title, fh.artist, fh.album = title, artist, album
            if tracknumber is not None:
                fh.tracknumber = str(tracknumber)
            if comment:
                fh.comment = comment
            fh.write(q)
        os.replace(tmp, dst)
    finally:
        if tmp.exists():
            tmp.unlink()
    return {"trimmed_tail_s": trimmed, "sample_rate": sr}


# --- 2. the batch ----------------------------------------------------------------------------------------------
def _order(reg) -> list[str]:
    return sorted(reg.ids(), key=lambda i: (reg.raw(i).get("order", 999), i))


def default_instruments(reg) -> list[str]:
    """Every instrument that renders (not windows-gs) and is installed, in the Room page's order."""
    from . import engines as _engines
    return [i for i in _order(reg)
            if reg.raw(i).get("engine") not in _engines.NEVER_RENDERED and reg.why_unavailable(i) is None]


def _merge_table(path: Path, rows: list) -> None:
    try:
        old = json.loads(path.read_text())
    except (OSError, ValueError):
        old = []
    new = {r["file"]: r for r in rows}
    merged = [new.pop(r["file"], r) for r in old if isinstance(r, dict) and "file" in r] + list(new.values())
    tmp = path.with_name(f".{path.name}.tmp{os.getpid()}")
    tmp.write_text(json.dumps(merged, indent=1))
    os.replace(tmp, path)


def run(instruments: Optional[Iterable[str]] = None, excerpts: Optional[Iterable[int]] = None, *,
        out_dir: "str | Path | None" = None, work_dir: "str | Path | None" = None, ab: bool = True, registry=None,
        log: Log = print) -> list[dict]:
    """Cut, render and export (see the module doc). Returns one row per file written (also merged into
    <work>/table.json)."""
    import soundfile as sf

    from . import render as _render
    reg = registry if registry is not None else _instruments.load()
    out = Path(out_dir) if out_dir else AUDITION_DIR
    work = Path(work_dir).expanduser() if work_dir else WORK_DIR
    out.mkdir(parents=True, exist_ok=True)
    (work / "excerpts").mkdir(parents=True, exist_ok=True)
    ids = list(instruments) if instruments else default_instruments(reg)
    for i in ids:
        reg.get(i)                                    # KeyError for an unknown id, before any work
    want = set(excerpts) if excerpts else None
    exs = [e for e in EXCERPTS if want is None or e.n in want]
    if want and len(exs) != len(want):
        raise KeyError(f"unknown excerpt numbers: {sorted(want - {e.n for e in EXCERPTS})} (1-{len(EXCERPTS)})")
    cuts = {}
    for e in exs:
        cuts[e.n] = cut_excerpt(e.path, work / "excerpts" / f"{e.name}.mid", e.start_s, e.end_s)
    order = _order(reg)
    rows = []
    today = _dt.date.today().isoformat()
    try:
        for inst_id in ids:
            label = reg.raw(inst_id).get("label", inst_id)
            kinds = ["full", "dry", "noroom"] if ab and inst_id == reg.default else ["full"]
            for e in exs:
                cut = cuts[e.n]
                for kind in kinds:
                    suffix, kw = KINDS[kind]
                    res = _render.render(cut["dst"], inst_id, registry=reg, cache_dir=work / "renders", **kw)
                    name = f"{e.name} - {label}{suffix}.flac"
                    m = (res.stats or {}).get("master") or {}
                    ex = export_flac(
                        res.path, out / name, title=f"{e.title} - {label}{suffix}", artist=label + suffix,
                        tracknumber=e.n * 100 + order.index(inst_id) * 3 + list(KINDS).index(kind) + 1,
                        comment=(f"piano_render instrument={inst_id} room={res.room} realism={res.realism} "
                                 f"gain_db={m.get('gain_db')} (requested {m.get('requested_gain_db')}); excerpt "
                                 f"{cut['from_s']}-{cut['cut_at_s']} s of {Path(cut['src']).name}; rendered {today}"))
                    z, sr = sf.read(str(out / name), dtype="float32", always_2d=True)
                    meas = measure(z, sr, cut["first_note_s"] - cut["from_s"],
                                   (cut["cut_at_s"] - cut["from_s"]) if cut["cut_at_s"] is not None else None)
                    st = res.stats or {}
                    row = {"excerpt": str(e.n), "file": name, "instrument": inst_id, "kind": kind, "room": res.room,
                           **meas, "gain_applied": m.get("gain_db"), "gain_requested": m.get("requested_gain_db"),
                           "limited": m.get("limited"), "excerpt_s": cut["excerpt_s"],
                           "first_note_s": round(cut["first_note_s"] - cut["from_s"], 3),
                           "trimmed_tail_s": ex["trimmed_tail_s"], "cached": res.cached,
                           "render_wall_s": st.get("wall_s"), "render_cpu_s": st.get("cpu_s"),
                           "load_s": (st.get("engine") or {}).get("load_s"), "engine_s": st.get("engine_s"),
                           "realism_s": st.get("realism_s"), "tail_s": (st.get("engine") or {}).get("tail_s"),
                           "peak_voices": (st.get("engine") or {}).get("peak_voices")}
                    rows.append(row)
                    how = "cached render" if res.cached else f"rendered in {st.get('wall_s')} s"
                    log(f"{name}: {row['lufs']} LUFS, TP {row['tp']} dBTP, {row['duration_s']} s ({how})")
    finally:
        _render.release_engines()
        if rows:
            _merge_table(work / "table.json", rows)
    return rows
