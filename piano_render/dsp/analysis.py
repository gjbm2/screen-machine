"""Where each key's partials really are, measured from the instrument itself (research r4 items 3 and 11:
"resonators that miss the samples' real partials won't ring").

measure(engine, sr): the loaded engine plays every key 21..108 once (velocity 96, held 2 s, 1.5 s apart, no
pedal); for each key a 1.6 s window from 60 ms after the strike is FFT'd (Hann, zero-padded to 2^19 at 48 kHz,
0.09 Hz bins, parabolic interpolation), partials are found near f_k = k f0 sqrt(1 + B k^2) up to 5.5 kHz (20 dB
above the local median), and f0 and B come from a weighted least-squares fit of (f_k / k)^2 = f0^2 + f0^2 B k^2,
refitted once without partials more than 15 cents off. A key is "measured" when the fit lands within 60 cents of
equal temperament with 0 <= B <= 0.1; otherwise it takes the model below (and so does every key of an engine
whose notes are not piano-like, e.g. a test tone).

Model (no engine, or a key that failed): equal temperament at A4 = 440 Hz, with Rigaud et al.'s inharmonicity
curve (DAFx 2011) B(m) = exp(SB m + YB) + exp(0.0926 m - 13.64); the treble term is the paper's (shared by all
pianos), the bass term is ours (B(A0) = 2.6e-4, lowest near C3 at 1.2e-4; the paper fits it per piano).

tuning_for() caches the measurement as JSON per engine version, instrument file (path, size, mtime), preset,
velocity_gamma and sample rate, in $PIANO_RENDER_ANALYSIS, else <$PIANO_RENDER_CACHE>/_analysis, else
~/.cache/screen-machine/piano/analysis. The probe costs one render of 5 min of single notes, once per instrument.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import scipy.fft as sfft

from .. import score as _score

ANALYSIS_VERSION = "1"
ANALYSIS_ENV = "PIANO_RENDER_ANALYSIS"
DEFAULT_DIR = Path("~/.cache/screen-machine/piano/analysis").expanduser()

KEYS = list(range(_score.KEY_LO, _score.KEY_HI + 1))
PROBE_VELOCITY = 96
PROBE_HOLD_S = 2.0
PROBE_GAP_S = 1.5
PROBE_LEAD_S = 0.5
WINDOW_S = (0.06, 1.66)
NFFT_48K = 1 << 19
F_ANALYSIS_MAX = 5500.0
MAX_FIT_PARTIALS = 24
PROMINENCE = 10.0            # 20 dB above the window's median
OUTLIER_CENTS = 15.0
MAX_OFF_CENTS = 60.0
B_RANGE = (0.0, 0.1)

# Rigaud et al. 2011: treble term fixed; bass term chosen here (module docstring).
B_TREBLE = (0.0926, -13.64)
B_BASS = (-0.0935, -6.33)


def nominal_f0(m: float) -> float:
    """Equal-tempered pitch of MIDI key m (A4 = 440 Hz)."""
    return 440.0 * 2.0 ** ((m - 69) / 12.0)


def model_B(m: float) -> float:
    return math.exp(B_BASS[0] * m + B_BASS[1]) + math.exp(B_TREBLE[0] * m + B_TREBLE[1])


def partial_hz(f0: float, B: float, k: int) -> float:
    return k * f0 * math.sqrt(1.0 + B * k * k)


@dataclass
class KeyTuning:
    key: int
    f0: float                  # the stiff-string f0 (partial 1 = f0 sqrt(1 + B))
    B: float
    status: str                # "measured" | "model"
    partials: dict = field(default_factory=dict)    # k -> measured Hz (str keys after JSON)

    def freq(self, k: int) -> float:
        f = self.partials.get(k) or self.partials.get(str(k))
        return float(f) if f else partial_hz(self.f0, self.B, k)


@dataclass
class Tuning:
    keys: list
    source: str                # "measured" | "model"
    info: dict = field(default_factory=dict)

    def key(self, m: int) -> KeyTuning:
        return self.keys[m - _score.KEY_LO]

    @property
    def measured(self) -> int:
        return sum(1 for k in self.keys if k.status == "measured")

    def to_json(self) -> dict:
        return {"version": ANALYSIS_VERSION, "source": self.source, "info": self.info,
                "keys": [asdict(k) for k in self.keys]}

    @classmethod
    def from_json(cls, d: dict) -> "Tuning":
        keys = [KeyTuning(int(k["key"]), float(k["f0"]), float(k["B"]), str(k["status"]),
                          {int(a): float(b) for a, b in (k.get("partials") or {}).items()}) for k in d["keys"]]
        return cls(keys=keys, source=str(d.get("source", "measured")), info=dict(d.get("info") or {}))


def model_tuning() -> Tuning:
    keys = []
    for m in KEYS:
        B = model_B(m)
        keys.append(KeyTuning(m, nominal_f0(m) / math.sqrt(1.0 + B), B, "model"))
    return Tuning(keys=keys, source="model")


# --- measuring -------------------------------------------------------------------------------------------------
def probe_performance() -> _score.Performance:
    evs = []
    for i, m in enumerate(KEYS):
        t = PROBE_LEAD_S + i * (PROBE_HOLD_S + PROBE_GAP_S)
        evs.append((t, "on", m, PROBE_VELOCITY))
        evs.append((t + PROBE_HOLD_S, "off", m, 64))
    return _score.from_events(evs)


def _peak(X: np.ndarray, logX: np.ndarray, df: float, fc: float, half: float) -> Optional[tuple[float, float]]:
    lo = max(1, int((fc - half) / df))
    hi = min(len(X) - 2, int((fc + half) / df) + 1)
    if hi - lo < 5:
        return None
    j = lo + int(np.argmax(X[lo:hi]))
    if j <= lo or j >= hi - 1:
        return None
    if X[j] < PROMINENCE * float(np.median(X[lo:hi])):
        return None
    a, b, c = logX[j - 1], logX[j], logX[j + 1]
    den = a - 2 * b + c
    p = 0.5 * (a - c) / den if den < 0 else 0.0
    return (j + p) * df, float(math.exp(b - 0.25 * (a - c) * p))


def _fit(found: dict) -> Optional[tuple[float, float]]:
    ks = np.array(sorted(found), np.float64)
    if len(ks) < 3:
        return None
    f = np.array([found[int(k)][0] for k in ks])
    w = np.sqrt(np.array([found[int(k)][1] for k in ks]))
    y = (f / ks) ** 2
    A = np.stack([np.ones_like(ks), ks ** 2], axis=1) * w[:, None]
    sol, *_ = np.linalg.lstsq(A, y * w, rcond=None)
    a, b = float(sol[0]), float(sol[1])
    if a <= 0:
        return None
    return math.sqrt(a), b / a


def analyse_key(seg: np.ndarray, sr: int, m: int) -> KeyTuning:
    """One key's partials from a mono segment of it sounding alone (module docstring)."""
    nominal = nominal_f0(m)
    Bm = model_B(m)
    f0, B = nominal / math.sqrt(1 + Bm), Bm
    model = KeyTuning(m, f0, B, "model")
    if seg.size < sr // 4 or not np.any(seg):
        return model
    nfft = sfft.next_fast_len(max(NFFT_48K * sr // 48000, 4 * seg.size), real=True)
    X = np.abs(sfft.rfft(seg.astype(np.float64) * np.hanning(seg.size), nfft))
    logX = np.log(X + 1e-30)
    df = sr / nfft
    fmax = min(F_ANALYSIS_MAX, 0.45 * sr)
    found: dict = {}
    for limit in (3, 6, 12, MAX_FIT_PARTIALS):
        for k in range(1, limit + 1):
            fc = partial_hz(f0, B, k)
            if fc > fmax:
                break
            if k in found:
                continue
            pk = _peak(X, logX, df, fc, 0.25 * f0)
            if pk is not None:
                found[k] = pk
        fit = _fit(found)
        if fit is not None and B_RANGE[0] <= fit[1] <= B_RANGE[1]:
            f0, B = fit
        elif found:
            k = min(found)
            f0 = found[k][0] / (k * math.sqrt(1 + B * k * k))
    # drop outliers, refit once
    good = {k: v for k, v in found.items()
            if abs(1200 * math.log2(v[0] / partial_hz(f0, B, k))) <= OUTLIER_CENTS}
    fit = _fit(good)
    if fit is not None and B_RANGE[0] <= fit[1] <= B_RANGE[1]:
        f0, B = fit
    elif good:
        k = min(good)
        f0 = good[k][0] / (k * math.sqrt(1 + Bm * k * k))
        B = Bm
    else:
        return model
    p1 = f0 * math.sqrt(1 + B)
    if abs(1200 * math.log2(p1 / nominal)) > MAX_OFF_CENTS or not B_RANGE[0] <= B <= B_RANGE[1]:
        return model
    partials = {int(k): round(float(v[0]), 4) for k, v in good.items()
                if abs(1200 * math.log2(v[0] / partial_hz(f0, B, k))) <= OUTLIER_CENTS}
    return KeyTuning(m, float(f0), float(B), "measured", partials)


def measure(engine, sr: int, progress=None) -> Tuning:
    t0 = time.monotonic()
    c0 = time.process_time()
    perf = probe_performance()
    audio = engine.render(perf, sr=sr, progress=progress)
    mono = audio[:, 0].astype(np.float64) + audio[:, 1]
    keys = []
    for i, m in enumerate(KEYS):
        t = PROBE_LEAD_S + i * (PROBE_HOLD_S + PROBE_GAP_S)
        a, b = int(round((t + WINDOW_S[0]) * sr)), int(round((t + WINDOW_S[1]) * sr))
        keys.append(analyse_key(mono[a:min(b, mono.size)], sr, m))
    n_ok = sum(1 for k in keys if k.status == "measured")
    return Tuning(keys=keys, source="measured" if n_ok else "model",
                  info={"measured_keys": n_ok, "probe_wall_s": round(time.monotonic() - t0, 2),
                        "probe_cpu_s": round(time.process_time() - c0, 2)})


# --- the cache -------------------------------------------------------------------------------------------------
def analysis_dir() -> Path:
    env = os.environ.get(ANALYSIS_ENV)
    if env:
        return Path(env).expanduser()
    cache = os.environ.get("PIANO_RENDER_CACHE")
    if cache:
        return Path(cache).expanduser() / "_analysis"
    return DEFAULT_DIR


def cache_key(inst: dict, engine, sr: int) -> str:
    f = inst.get("file")
    try:
        st = os.stat(f) if f else None
        fid = [str(Path(f).resolve()), st.st_size, st.st_mtime_ns] if st else [f]
    except OSError:
        fid = [f]
    try:
        ever = type(engine).version(engine.engines_root)
    except Exception:
        ever = getattr(engine, "name", type(engine).__name__)
    payload = {"v": ANALYSIS_VERSION, "engine": inst.get("engine"), "engine_version": ever, "file": fid,
               "preset": inst.get("preset"), "velocity_gamma": inst.get("velocity_gamma"),
               "half_pedal": inst.get("half_pedal"), "sr": int(sr), "probe": [PROBE_VELOCITY, PROBE_HOLD_S]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:24]


def tuning_for(inst: dict, engine, sr: int, progress=None) -> Tuning:
    """The instrument's measured tuning (cached), or the model when there is no engine."""
    if engine is None:
        return model_tuning()
    d = analysis_dir()
    key = cache_key(inst, engine, sr)
    path = d / f"{inst.get('id', 'inst')}-{key}.json"
    with contextlib.suppress(OSError, ValueError, KeyError, TypeError):
        with open(path, encoding="utf-8") as f:
            t = Tuning.from_json(json.load(f))
        t.info["cached"] = True
        return t
    t = measure(engine, sr, progress)
    t.info["cached"] = False
    with contextlib.suppress(OSError):
        d.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(t.to_json(), f)
        os.replace(tmp, path)
    return t
