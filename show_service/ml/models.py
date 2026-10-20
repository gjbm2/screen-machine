"""The ML worker's model back ends (README S4.3, S4.4).

Real back ends run onnxruntime (CPU only, one intra-op and one inter-op thread, sequential) and essentia, both
imported lazily, so ``--fake`` runs with numpy alone (also under ~/.venvs/sm-show, for the other builders' tests).

**Model facts come from index.json** (tools/show/fetch_models.py writes it): each entry's ``facts`` says how to read the
model: its input, its outputs, its labels (inline or from the metadata JSON fetched with it), which label is the
"yes" of a binary head or the value of a regression (``yes``), a regression's ``range``, and which backbone feeds a
head (``via``). The code knows the *kinds* (audio_tagger, backbone, styles, multilabel, binary, regression,
beat_tracker), never a model's or a label's name.

**Which model fills which field** is config (show.yaml ``ml.tasks.*.model`` and ``ml.tasks.tags.heads``):
- ``genre`` -> a ``styles`` head: ``genre_parents`` (per patch, each parent the max of its "Parent---Style" labels;
  then the mean over patches) and ``styles_top3``;
- ``moods`` / ``instruments`` -> ``multilabel`` heads: ``moods_top5`` / ``instruments_top5``;
- every other field (dance, voice_p, engagement, bright, acoustic, arousal) -> a ``binary`` head's yes-probability, or
  a ``regression`` head's ``yes`` output mapped from its ``range`` onto 0..1 (clamped).

**Budgets.** Every onnxruntime run takes the task's ``Budget``: the worker's watchdog sets its RunOptions'
``terminate`` at the deadline, the run raises, and ``Overrun`` goes up (the result is dropped). Steps between runs
check the deadline too. essentia can't be interrupted: a run that ends past its deadline is an Overrun all the same.

**--fake** (tests only, never production): no files, deterministic numpy results:
- ced: from the window's RMS level ``L`` (dBFS): ``a = clip((L + 60) / 40, 0, 1)``; music ``a``, silence ``1 - a``,
  speech ``0.5·a·flatness``, conversation ``0.25·a·flatness``, singing ``0.1·a``, crowd ``0.1·a·flatness``, applause
  ``0.05·a·flatness`` (flatness: the spectrum's geometric / arithmetic mean, 0..1);
- tags: 40 log band energies (z-scored) through fixed seeded projections: the embedding (1280-d, L2-normalised; it
  moves with the spectrum, so novelty works), the 15 ``contract.DISCOGS_PARENTS`` as parents (sigmoids; styles are
  "<parent>---<parent>"), eight fake moods and instruments (``fake-mood-<i>``, ``fake-instrument-<i>``), and the
  scalars from simple features (dance: onset regularity; voice_p: the 300-3000 Hz share; engagement and arousal: ``a``;
  bright: the centroid / 4 kHz; acoustic: 1 - flatness), all clamped 0..1;
- key: a 12-bin chroma (Hann 4096-point frames, 55 Hz-5 kHz) against Krumhansl's major and minor profiles; strength is
  the best correlation (0..1); None when the window has no energy;
- beats: a log-energy flux at 100 Hz, its autocorrelation's best lag in 0.3-1.0 s is the period, the best comb offset
  the phase; every 4th beat from the strongest of the four is a downbeat (bar_len 4).
"""
from __future__ import annotations

import collections
import json
import math
import pathlib
import platform
import threading
import time
from typing import Any, Callable, Optional

import numpy as np

from show_service import contract as C

FS = C.ML_FS

# The kinds a model's facts may name (index.json). Code branches on these, never on a model or label name.
KINDS = ("audio_tagger", "backbone", "styles", "multilabel", "binary", "regression", "beat_tracker")
# contract.MlTags slots per config field (README S4.3): list fields take a multilabel head's top 5.
LIST_SLOTS = {"moods": "moods_top5", "instruments": "instruments_top5"}
GENRE_FIELD = "genre"
SCALAR_FIELDS = ("dance", "voice_p", "engagement", "bright", "acoustic", "arousal")


class Overrun(Exception):
    """The task's budget ran out (terminated, or finished past its deadline): its result is dropped."""


class ModelError(Exception):
    """A model can't be loaded or read (missing file, bad facts)."""


# -- budgets -----------------------------------------------------------------------------------------------------------
class Budget:
    """One task run's hard deadline. ``terminate()`` (the watchdog, at the deadline) stops the onnxruntime run in
    progress and every later one; ``check()`` raises Overrun once it has passed."""

    def __init__(self, budget_ms: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.start = clock()
        self.deadline = self.start + max(0.0, budget_ms) / 1000.0
        self.expired = False
        self._ro = None
        self._lock = threading.Lock()

    def run_options(self):
        with self._lock:
            if self._ro is None:
                import onnxruntime as ort
                self._ro = ort.RunOptions()
                if self.expired:
                    self._ro.terminate = True
            return self._ro

    def terminate(self) -> None:
        with self._lock:
            self.expired = True
            if self._ro is not None:
                self._ro.terminate = True

    def check(self) -> None:
        if self.expired or self.clock() > self.deadline:
            self.expired = True
            raise Overrun("over budget")

    def elapsed_ms(self) -> float:
        return (self.clock() - self.start) * 1000.0


# -- index.json -------------------------------------------------------------------------------------------------------
def read_index(models_dir: pathlib.Path) -> dict:
    try:
        data = json.loads((pathlib.Path(models_dir) / "index.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _labels(models_dir: pathlib.Path, facts: dict) -> list[str]:
    spec = facts.get("labels")
    if isinstance(spec, list):
        return [str(x) for x in spec]
    if isinstance(spec, dict) and spec.get("file"):
        data = json.loads((pathlib.Path(models_dir) / spec["file"]).read_text(encoding="utf-8"))
        val = data.get(spec.get("key", "classes")) if isinstance(data, dict) else None
        if isinstance(val, dict):                  # {"0": "Speech", ...}: by integer key
            return [str(val[k]) for k in sorted(val, key=lambda s: int(s))]
        if isinstance(val, list):
            return [str(x) for x in val]
    raise ModelError("its facts give no labels")


def _session(path: pathlib.Path, *, arena: bool = True):
    """One CPU session: one intra-op and one inter-op thread, sequential. ``arena=False`` turns onnxruntime's CPU
    memory arena off: Beat This! on a 30 s window otherwise keeps its peak (about 1.3 GB, grown by powers of two) for
    the whole evening; without the arena the worker stays near 300 MB at the same cost per run (measured 16 Oct)."""
    import onnxruntime as ort
    o = ort.SessionOptions()
    o.intra_op_num_threads = 1
    o.inter_op_num_threads = 1
    o.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    o.log_severity_level = 3
    o.enable_cpu_mem_arena = arena
    return ort.InferenceSession(str(path), o, providers=["CPUExecutionProvider"])


def _run(sess, outputs: Optional[list], feeds: dict, budget: Budget) -> list:
    budget.check()
    try:
        return sess.run(outputs, feeds, budget.run_options())
    except Exception:
        if budget.expired or budget.clock() > budget.deadline:
            budget.expired = True
            raise Overrun("terminated at its budget") from None
        raise


class Model:
    """One index.json entry, loaded: its facts, labels, and onnxruntime session (when it has a file of its own)."""

    def __init__(self, name: str, models_dir: pathlib.Path, index: dict, *, session: bool = True) -> None:
        entry = index.get(name)
        if not isinstance(entry, dict):
            raise ModelError("missing")
        self.name, self.entry = name, entry
        self.facts = entry.get("facts") or {}
        kind = self.facts.get("kind")
        if kind not in KINDS:
            raise ModelError(f"facts.kind {kind!r} unknown")
        self.kind = kind
        self.path = pathlib.Path(models_dir) / str(entry.get("file", ""))
        if not self.path.is_file():
            raise ModelError("missing")
        self.labels: list[str] = []
        if self.facts.get("labels") is not None:
            self.labels = _labels(models_dir, self.facts)
        self.sess = None
        if session and self.path.suffix == ".onnx":
            self.sess = _session(self.path, arena=kind != "beat_tracker")   # the big window's peak isn't kept
            self.input_name = self.facts.get("input", {}).get("name") or self.sess.get_inputs()[0].name
            names = [o.name for o in self.sess.get_outputs()]
            out = self.facts.get("output")
            self.output_name = out if out in names else names[0]

    def index_of(self, label: Optional[str]) -> int:
        if label in self.labels:
            return self.labels.index(label)
        raise ModelError(f"facts.yes {label!r} is not one of its labels")


# -- front ends --------------------------------------------------------------------------------------------------------
class MusicnnMel:
    """Essentia's TensorflowInputMusiCNN bands (512-sample frames, hop 256, 96 bands at 16 kHz: what Discogs-EffNet and
    MSD-MusiCNN were trained on), cached per frame. Frame k of a ring segment covers samples [256k, 256k + 512)."""
    FRAME, HOP, BANDS = 512, 256, 96

    def __init__(self, keep: int = 512) -> None:
        import essentia.standard as es
        self._algo = es.TensorflowInputMusiCNN()
        self._cache: dict[int, np.ndarray] = {}
        self._gen = None
        self.keep = keep

    def frames(self, pcm: np.ndarray, n_start: int, n_end: int, gen: Any, count: int,
               budget: Optional[Budget] = None) -> Optional[np.ndarray]:
        """The last ``count`` whole frames that end by ``n_end`` ((count, 96) float32), or None with too little audio.
        ``pcm`` holds samples n_start..n_end of the segment ``gen``."""
        if gen != self._gen:
            self._cache.clear()
            self._gen = gen
        k_last = (n_end - self.FRAME) // self.HOP
        k_first = k_last - count + 1
        if k_first * self.HOP < n_start or k_last < 0:
            return None
        out = np.empty((count, self.BANDS), dtype=np.float32)
        for i, k in enumerate(range(k_first, k_last + 1)):
            row = self._cache.get(k)
            if row is None:
                if budget is not None and (i & 15) == 0:
                    budget.check()
                a = k * self.HOP - n_start
                row = np.asarray(self._algo(np.ascontiguousarray(pcm[a:a + self.FRAME], dtype=np.float32)),
                                 dtype=np.float32)
                self._cache[k] = row
            out[i] = row
        if len(self._cache) > self.keep:
            for k in [k for k in self._cache if k < k_last - self.keep]:
                del self._cache[k]
        return out


def _patches(mel: np.ndarray, size: int, hop: int) -> np.ndarray:
    starts = list(range(len(mel) - size, -1, -hop))[::-1]    # aligned to the newest frame
    return np.stack([mel[s:s + size] for s in starts]).astype(np.float32)


# Beat This!'s front end: torchaudio MelSpectrogram (periodic Hann, centred with reflect padding, the STFT scaled by
# 1/sqrt(n_fft) ("frame_length"), magnitude, Slaney mel scale without area norm), then log1p(multiplier * mel).
def _hz_to_mel_slaney(f):
    f = np.asarray(f, dtype=np.float64)
    f_sp, min_log_hz = 200.0 / 3, 1000.0
    min_log_mel, logstep = min_log_hz / f_sp, math.log(6.4) / 27.0
    return np.where(f >= min_log_hz, min_log_mel + np.log(np.maximum(f, 1e-12) / min_log_hz) / logstep, f / f_sp)


def _mel_to_hz_slaney(m):
    m = np.asarray(m, dtype=np.float64)
    f_sp, min_log_hz = 200.0 / 3, 1000.0
    min_log_mel, logstep = min_log_hz / f_sp, math.log(6.4) / 27.0
    return np.where(m >= min_log_mel, min_log_hz * np.exp(logstep * (m - min_log_mel)), f_sp * m)


_FB_CACHE: dict = {}


def mel_fbank(fs: int, n_fft: int, f_min: float, f_max: float, n_mels: int) -> np.ndarray:
    key = (fs, n_fft, f_min, f_max, n_mels)
    fb = _FB_CACHE.get(key)
    if fb is None:
        n_freqs = n_fft // 2 + 1
        all_freqs = np.linspace(0, fs // 2, n_freqs)
        m_pts = np.linspace(_hz_to_mel_slaney(f_min), _hz_to_mel_slaney(f_max), n_mels + 2)
        f_pts = _mel_to_hz_slaney(m_pts)
        f_diff = f_pts[1:] - f_pts[:-1]
        slopes = f_pts[None, :] - all_freqs[:, None]
        down = (-slopes[:, :-2]) / f_diff[:-1]
        up = slopes[:, 2:] / f_diff[1:]
        fb = np.maximum(0.0, np.minimum(down, up))
        _FB_CACHE[key] = fb
    return fb


def beat_logmel(x: np.ndarray, fs: int = 22050, n_fft: int = 1024, hop: int = 441, f_min: float = 30.0,
                f_max: float = 11000.0, n_mels: int = 128, log_multiplier: float = 1000.0) -> np.ndarray:
    """(T, n_mels) float32, T = 1 + len(x) // hop: torchaudio's MelSpectrogram as Beat This! configures it."""
    x = np.asarray(x, dtype=np.float32)
    pad = n_fft // 2
    if len(x) <= pad:
        x = np.pad(x, (0, pad + 1 - len(x)))
    xp = np.pad(x, (pad, pad), mode="reflect")
    n_frames = 1 + (len(xp) - n_fft) // hop
    idx = np.arange(n_fft)[None, :] + hop * np.arange(n_frames)[:, None]
    win = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(n_fft) / n_fft)).astype(np.float32)   # periodic Hann
    spec = np.abs(np.fft.rfft(xp[idx] * win, axis=1)) / math.sqrt(n_fft)
    mel = spec.astype(np.float32) @ mel_fbank(fs, n_fft, f_min, f_max, n_mels).astype(np.float32)
    return np.log1p(log_multiplier * mel).astype(np.float32)


def _maxpool_same(x: np.ndarray, k: int = 7) -> np.ndarray:
    r = k // 2
    p = np.pad(x, (r, r), constant_values=-np.inf)
    return np.lib.stride_tricks.sliding_window_view(p, k).max(axis=1)


def _dedup(frames: np.ndarray, width: int = 1) -> list[float]:
    out: list[float] = []
    it = iter(int(f) for f in frames)
    try:
        p: float = next(it)
    except StopIteration:
        return out
    c = 1
    for p2 in it:
        if p2 - p <= width:
            c += 1
            p += (p2 - p) / c
        else:
            out.append(p)
            p, c = p2, 1
    out.append(p)
    return out


def minimal_peaks(beat: np.ndarray, downbeat: np.ndarray, fps: float = 50.0) -> tuple[np.ndarray, np.ndarray]:
    """Beat This!'s "minimal" post-processing, in numpy: logits that are the max within +-3 frames (70 ms) and above
    0 (p > 0.5); adjacent peaks merged to their mean; each downbeat moved to its nearest beat; seconds from frame 0."""
    res = []
    for lg in (np.asarray(beat, dtype=np.float32), np.asarray(downbeat, dtype=np.float32)):
        peaks = (lg == _maxpool_same(lg, 7)) & (lg > 0)
        res.append(np.asarray(_dedup(np.nonzero(peaks)[0]), dtype=np.float64) / fps)
    beats, downs = res
    if len(beats):
        downs = np.array([beats[np.argmin(np.abs(beats - d))] for d in downs], dtype=np.float64)
    return beats, np.unique(downs)


def to_key_name(name: Optional[str]) -> Optional[str]:
    """Essentia's key name ("Eb", "C#", ...) -> contract.KEY_NAMES (sharps)."""
    if not name:
        return None
    letters = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
    pc = letters.get(name[0].upper())
    if pc is None:
        return None
    for ch in name[1:]:
        pc += 1 if ch == "#" else -1 if ch == "b" else 0
    return C.KEY_NAMES[pc % 12]


def level_dbfs(x: np.ndarray) -> float:
    """RMS level in dBFS (a full-scale square wave is 0), floored at -150."""
    if len(x) == 0:
        return -150.0
    rms = float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))
    return max(-150.0, 20.0 * math.log10(rms + 1e-12))


def to_level(pcm: np.ndarray, level: float, gain_max_db: float) -> np.ndarray:
    """The window scaled so its RMS is ``level`` dBFS, at most ``gain_max_db`` up; a louder one is left as it is."""
    gain_db = min(gain_max_db, level - level_dbfs(pcm))
    if gain_db <= 0.0:
        return pcm
    return (pcm * np.float32(10.0 ** (gain_db / 20.0))).astype(np.float32)


# -- the real tasks ---------------------------------------------------------------------------------------------------
def match_groups(labels: list[str], groups: dict) -> tuple[dict[str, list[int]], list[str]]:
    """show.yaml ml.ced_groups -> {word: [label indices]}, plus the configured classes the model lacks. A class matches
    a label that equals it, or equals its text before the first comma (AudioSet's display names, e.g. "Male speech,
    man speaking", against a model that keeps only "Male speech"); case is ignored."""
    by = {lab.casefold(): i for i, lab in enumerate(labels)}
    out: dict[str, list[int]] = {}
    lacking: list[str] = []
    for word in C.CED_GROUPS:
        idx: list[int] = []
        for cls in (groups or {}).get(word) or []:
            cls = str(cls)
            i = by.get(cls.casefold())
            if i is None:
                i = by.get(cls.split(",", 1)[0].strip().casefold())
            if i is None:
                lacking.append(f"{word}: {cls}")
            elif i not in idx:
                idx.append(i)
        out[word] = idx
    return out, lacking


CED_LEVEL_DBFS = -20.0         # CED-mini hears loudness as evidence: each window's RMS goes to this first
CED_GAIN_MAX_DB = 40.0         # ... by at most this much (the worker's silence rule has already read the raw level)


class CedTask:
    """An audio tagger (CED-mini): 16 kHz waveform in (at CED_LEVEL_DBFS), per-class probabilities out; each
    CED_GROUPS word is the max of its classes (show.yaml ml.ced_groups). 8f, 20 Oct: through the room mic music arrives
    at about -50 dBFS, where CED called 8 of the 9 test tracks music (>= 0.5) in 0-13% of 2 s windows (disco 0%), so
    the listener never found 6 s of music; at -20 dBFS 87-100% (techno 55%), while room noise stays under 0.1."""

    def __init__(self, model: Model, groups: dict, log: Callable[[str], None]) -> None:
        if model.kind != "audio_tagger":
            raise ModelError(f"kind {model.kind}, expected audio_tagger")
        self.m = model
        self.sigmoid = model.facts.get("output_is") == "logits"
        self.set_groups(groups, log)

    def set_groups(self, groups: dict, log: Callable[[str], None]) -> None:
        self.groups, lacking = match_groups(self.m.labels, groups)
        for item in lacking:
            log(f"ced_groups: the model has no class for {item}")

    def run(self, pcm: np.ndarray, budget: Budget, **_: Any) -> dict:
        pcm = to_level(pcm, CED_LEVEL_DBFS, CED_GAIN_MAX_DB)
        (p,) = _run(self.m.sess, [self.m.output_name], {self.m.input_name: pcm[None, :].astype(np.float32)}, budget)
        p = np.asarray(p, dtype=np.float64).reshape(-1)
        if self.sigmoid:
            p = 1.0 / (1.0 + np.exp(-p))
        return {f"{w}_p": float(max((p[i] for i in self.groups[w]), default=0.0)) for w in C.CED_GROUPS}


class TagsTask:
    """Backbones (EffNet, and MusiCNN when a head is ``via`` it) on the window's mel patches, then the heads."""

    def __init__(self, backbone: Model, heads: dict[str, Model], backbones: dict[str, Model], mel: MusicnnMel,
                 window_s: float, styles_top: int = 5) -> None:
        self.backbone, self.heads, self.backbones, self.mel = backbone, heads, backbones, mel
        self.window_s = window_s
        self.styles_top = max(3, int(styles_top))      # show.yaml ml.tasks.tags.styles_top (structure's genre words)
        # the audio a run needs: the tags window, or a backbone's whole patch when that is longer (MusiCNN: 187
        # frames), plus one hop: frames sit on the segment's 256-sample grid, so the newest whole one ends up to 255
        # samples before the window's end
        frames = max(int(m.facts.get("input", {}).get("frames", 128)) for m in backbones.values())
        self.need_s = max(window_s, ((frames - 1) * MusicnnMel.HOP + MusicnnMel.FRAME) / FS) + MusicnnMel.HOP / FS
        self.emb_name = backbone.facts.get("outputs", {}).get("embeddings", "embeddings")
        self.emb_dim = int(backbone.facts.get("emb_dim", C.EMB_DIM))
        self.emb_id = 0
        self.bb_costs: dict[str, collections.deque] = {n: collections.deque(maxlen=32) for n in backbones}
        self.bb_skipped: dict[str, int] = {n: 0 for n in backbones}
        self._yes: dict[str, int] = {}
        for field, m in heads.items():
            if m.kind in ("binary", "regression"):
                self._yes[field] = m.index_of(m.facts.get("yes"))

    def _bb_out(self, bb: Model, pcm, n_start, n_end, gen, budget, window_s) -> Optional[dict]:
        inp = bb.facts.get("input", {})
        size, hop = int(inp.get("frames", 128)), int(inp.get("patch_hop", inp.get("frames", 128)))
        n_frames = max(size, int((window_s * FS - MusicnnMel.FRAME) // MusicnnMel.HOP) + 1)
        mel = self.mel.frames(pcm, n_start, n_end, gen, n_frames, budget)
        if mel is None:
            mel = self.mel.frames(pcm, n_start, n_end, gen, size, budget)
            if mel is None:
                return None
        patches = _patches(mel, size, hop)
        names = [o.name for o in bb.sess.get_outputs()]
        t0 = time.perf_counter()
        outs = _run(bb.sess, names, {bb.input_name: patches}, budget)
        self.bb_costs[bb.name].append((time.perf_counter() - t0) * 1000.0)
        return dict(zip(names, outs))

    def _fits(self, bb: Model, budget: Budget) -> bool:
        """A secondary backbone (one only some heads need) runs only when its own p95 fits what is left of the
        task's budget, so it can never push the main backbone's results over."""
        c = self.bb_costs.get(bb.name)
        if not c:
            return True
        left_ms = (budget.deadline - budget.clock()) * 1000.0
        if float(np.percentile(np.fromiter(c, float), 95)) <= left_ms:
            return True
        self.bb_skipped[bb.name] += 1
        return False

    def run(self, pcm: np.ndarray, budget: Budget, *, n_start: int, n_end: int, gen: Any) -> Optional[dict]:
        outs = {self.backbone.name: self._bb_out(self.backbone, pcm, n_start, n_end, gen, budget, self.window_s)}
        if outs[self.backbone.name] is None:
            return None
        res: dict[str, Any] = {"genre_parents": {}, "styles_top3": [], "styles_top": [], "moods_top5": [],
                               "instruments_top5": []}
        for f in SCALAR_FIELDS:
            res[f] = None
        for field, m in self.heads.items():
            via = m.facts.get("via", self.backbone.name)
            if via not in outs:
                bb = self.backbones.get(via)
                outs[via] = (self._bb_out(bb, pcm, n_start, n_end, gen, budget, self.window_s)
                             if bb is not None and self._fits(bb, budget) else None)
            bo = outs[via]
            if bo is None:
                continue
            budget.check()
            if m.kind == "styles":                 # the backbone's own style output (facts.backbone_output)
                act = np.asarray(bo[m.facts.get("backbone_output", "activations")], dtype=np.float64)
                sep = m.facts.get("label_sep", "---")
                parents = [lab.split(sep, 1)[0] for lab in m.labels]
                uniq = sorted(set(parents))
                pidx = np.array([uniq.index(p) for p in parents])
                per = np.zeros((act.shape[0], len(uniq)))
                for j in range(len(uniq)):
                    per[:, j] = act[:, pidx == j].max(axis=1)
                mean_par = per.mean(axis=0)
                res["genre_parents"] = {p: float(mean_par[j]) for j, p in enumerate(uniq)}
                mean_act = act.mean(axis=0)
                res["styles_top"] = [[m.labels[i], float(mean_act[i])] for i in np.argsort(-mean_act)[:self.styles_top]]
                res["styles_top3"] = res["styles_top"][:3]
                continue
            emb_key = m.facts.get("input_output", "embeddings")
            emb_out = self.backbones.get(via, self.backbone).facts.get("outputs", {}).get(emb_key, emb_key)
            (y,) = _run(m.sess, [m.output_name], {m.input_name: np.asarray(bo[emb_out], dtype=np.float32)}, budget)
            y = np.asarray(y, dtype=np.float64).mean(axis=0)
            if m.kind == "multilabel":
                slot = LIST_SLOTS.get(field)
                if slot:
                    res[slot] = [[m.labels[i], float(y[i])] for i in np.argsort(-y)[:5]]
            elif field in SCALAR_FIELDS:
                v = float(y[self._yes[field]])
                if m.kind == "regression":
                    lo, hi = (m.facts.get("range") or [None, None])[:2]
                    v = None if lo is None or hi is None or hi == lo else min(1.0, max(0.0, (v - lo) / (hi - lo)))
                res[field] = v
        emb = np.asarray(outs[self.backbone.name][self.emb_name], dtype=np.float64).mean(axis=0)
        emb = emb / (np.linalg.norm(emb) + 1e-12)
        self.emb_id += 1
        res["emb_vec"] = emb.astype(np.float32)
        res["emb_id"] = self.emb_id
        res["emb_dim"] = int(emb.shape[0])
        return res


class KeyTask:
    """Essentia KeyExtractor (HPCP + Key) at 16 kHz with show.yaml's profile."""

    def __init__(self, profile: str) -> None:
        import essentia.standard as es
        self.profile = profile
        self.algo = es.KeyExtractor(sampleRate=float(FS), profileType=profile)

    def run(self, pcm: np.ndarray, budget: Budget, **_: Any) -> dict:
        key, scale, strength = self.algo(np.ascontiguousarray(pcm, dtype=np.float32))
        budget.check()
        strength = float(strength)
        if not math.isfinite(strength) or strength <= 0:
            return {"key": None, "mode": None, "strength": 0.0}
        mode = scale if scale in ("major", "minor") else None
        return {"key": to_key_name(key), "mode": mode, "strength": min(1.0, max(0.0, strength))}


BEATS_LEVEL_DBFS = -20.0       # Beat This! has no gain stage of its own: each window's RMS goes to this first
BEATS_GAIN_MAX_DB = 40.0       # ... by at most this much (the worker's silence rule has already read the raw level)


def beats_level(pcm: np.ndarray) -> np.ndarray:
    """The window scaled so its RMS is BEATS_LEVEL_DBFS (at most BEATS_GAIN_MAX_DB up; a louder one is left as it is). 26, 19 Oct
    (tools/room/beat_eval/alt/bt_lane.py): through PC22's mic music arrives at about -48 dBFS, where Beat This! found
    no beats in most windows (0 in a 30 s ChaChaCha window at -48.8); scaled to -20 dBFS it found the right tempo."""
    return to_level(pcm, BEATS_LEVEL_DBFS, BEATS_GAIN_MAX_DB)


class BeatsTask:
    """A beat tracker on log-mel frames (Beat This!, exported to ONNX): 16 kHz -> its rate (soxr), the log-mel, the
    model, then the minimal post-processing. Times are seconds from the window's first sample."""

    def __init__(self, model: Model) -> None:
        if model.kind != "beat_tracker":
            raise ModelError(f"kind {model.kind}, expected beat_tracker")
        import soxr  # noqa: F401  (fail at load, not at the first run)
        self.m = model
        inp = model.facts.get("input", {})
        self.fs = int(inp.get("fs", 22050))
        self.mel_kw = dict(fs=self.fs, n_fft=int(inp.get("n_fft", 1024)), hop=int(inp.get("hop", 441)),
                           f_min=float(inp.get("f_min", 30.0)), f_max=float(inp.get("f_max", 11000.0)),
                           n_mels=int(inp.get("n_mels", 128)), log_multiplier=float(inp.get("log_multiplier", 1000.0)))
        self.fps = float(inp.get("fps", self.fs / self.mel_kw["hop"]))
        outs = model.facts.get("outputs", {})
        names = [o.name for o in model.sess.get_outputs()]
        self.out_names = [outs.get("beat", names[0]), outs.get("downbeat", names[-1])]

    def run(self, pcm: np.ndarray, budget: Budget, **_: Any) -> dict:
        import soxr
        pcm = beats_level(pcm)
        x = soxr.resample(pcm.astype(np.float32), FS, self.fs, quality="HQ") if self.fs != FS else pcm
        budget.check()
        spect = beat_logmel(x, **self.mel_kw)
        beat, down = _run(self.m.sess, self.out_names, {self.m.input_name: spect[None]}, budget)
        beats, downs = minimal_peaks(np.asarray(beat)[0], np.asarray(down)[0], self.fps)
        return {"beats_rel": beats, "downbeats_rel": downs}


# -- the fake tasks ---------------------------------------------------------------------------------------------------
_FAKE_RNG = np.random.default_rng(20261016)
_FAKE_W = _FAKE_RNG.standard_normal((C.EMB_DIM, 40)).astype(np.float32)
_FAKE_V = _FAKE_RNG.standard_normal((len(C.DISCOGS_PARENTS), 40)).astype(np.float32) * 0.5
_FAKE_MOODS = [f"fake-mood-{i}" for i in range(8)]
_FAKE_INSTR = [f"fake-instrument-{i}" for i in range(8)]
_FAKE_M = _FAKE_RNG.standard_normal((8, 40)).astype(np.float32) * 0.5
_FAKE_I = _FAKE_RNG.standard_normal((8, 40)).astype(np.float32) * 0.5


def _spectrum(x: np.ndarray, n: int = 2048) -> np.ndarray:
    if len(x) < n:
        x = np.pad(x, (0, n - len(x)))
    frames = np.lib.stride_tricks.sliding_window_view(x, n)[::n // 2]
    return np.abs(np.fft.rfft(frames * np.hanning(n), axis=1)).mean(axis=0)


def _flatness(mag: np.ndarray) -> float:
    m = mag[1:] + 1e-12
    return float(np.exp(np.mean(np.log(m))) / np.mean(m))


def _bands(mag: np.ndarray, n: int = 40) -> np.ndarray:
    freqs = np.linspace(0, FS / 2, len(mag))
    edges = np.geomspace(40.0, FS / 2, n + 1)
    e = np.array([mag[(freqs >= lo) & (freqs < hi)].sum() for lo, hi in zip(edges[:-1], edges[1:])])
    return np.log10(e + 1e-9)


def _sig(z):
    return 1.0 / (1.0 + np.exp(-z))


def _clamp(v: float) -> float:
    return float(min(1.0, max(0.0, v)))


class FakeCed:
    def run(self, pcm: np.ndarray, budget: Budget, **_: Any) -> dict:
        a = _clamp((level_dbfs(pcm) + 60.0) / 40.0)
        f = _flatness(_spectrum(pcm))
        return {"music_p": a, "speech_p": 0.5 * a * f, "conversation_p": 0.25 * a * f, "singing_p": 0.1 * a,
                "crowd_p": 0.1 * a * f, "applause_p": 0.05 * a * f, "silence_p": 1.0 - a}


class FakeTags:
    def __init__(self) -> None:
        self.emb_id = 0

    def run(self, pcm: np.ndarray, budget: Budget, **_: Any) -> dict:
        mag = _spectrum(pcm)
        b = _bands(mag)
        z = (b - b.mean()) / (b.std() + 1e-9)
        emb = np.tanh(_FAKE_W @ z)
        emb = emb / (np.linalg.norm(emb) + 1e-12)
        par = _sig(_FAKE_V @ z)
        moods, inst = _sig(_FAKE_M @ z), _sig(_FAKE_I @ z)
        freqs = np.linspace(0, FS / 2, len(mag))
        tot = float(mag.sum()) + 1e-12
        a = _clamp((level_dbfs(pcm) + 60.0) / 40.0)
        env = np.abs(np.diff(np.abs(pcm[::160]))) if len(pcm) > 320 else np.zeros(2)
        dance = _clamp(float(env.std() / (env.mean() + 1e-9)) / 4.0)
        top = np.argsort(-par)[:3]
        self.emb_id += 1
        return {"genre_parents": {p: float(par[i]) for i, p in enumerate(C.DISCOGS_PARENTS)},
                "styles_top3": [[f"{C.DISCOGS_PARENTS[i]}---{C.DISCOGS_PARENTS[i]}", float(par[i])] for i in top],
                "styles_top": [[f"{C.DISCOGS_PARENTS[i]}---{C.DISCOGS_PARENTS[i]}", float(par[i])]
                               for i in np.argsort(-par)[:5]],
                "moods_top5": [[_FAKE_MOODS[i], float(moods[i])] for i in np.argsort(-moods)[:5]],
                "instruments_top5": [[_FAKE_INSTR[i], float(inst[i])] for i in np.argsort(-inst)[:5]],
                "dance": dance, "voice_p": _clamp(float(mag[(freqs >= 300) & (freqs < 3000)].sum()) / tot),
                "engagement": a, "bright": _clamp(float((freqs * mag).sum()) / tot / 4000.0),
                "acoustic": _clamp(1.0 - _flatness(mag)), "arousal": a,
                "emb_vec": emb.astype(np.float32), "emb_id": self.emb_id, "emb_dim": C.EMB_DIM}


_KK_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_KK_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def fake_chroma(pcm: np.ndarray, n: int = 4096) -> np.ndarray:
    if len(pcm) < n:
        pcm = np.pad(pcm, (0, n - len(pcm)))
    frames = np.lib.stride_tricks.sliding_window_view(pcm, n)[::n]
    mag = np.abs(np.fft.rfft(frames * np.hanning(n), axis=1)).mean(axis=0)
    freqs = np.arange(len(mag)) * FS / n
    sel = (freqs >= 55) & (freqs <= 5000)
    pc = np.round(12 * np.log2(freqs[sel] / 440.0)).astype(int) % 12     # 0 = A
    chroma = np.bincount((pc + 9) % 12, weights=mag[sel] ** 2, minlength=12)   # 0 = C
    return chroma


class FakeKey:
    def run(self, pcm: np.ndarray, budget: Budget, **_: Any) -> dict:
        ch = fake_chroma(pcm)
        if ch.sum() <= 1e-9:
            return {"key": None, "mode": None, "strength": 0.0}
        best = (-2.0, 0, "major")
        for mode, prof in (("major", _KK_MAJOR), ("minor", _KK_MINOR)):
            for k in range(12):
                r = float(np.corrcoef(ch, np.roll(prof, k))[0, 1])
                if r > best[0]:
                    best = (r, k, mode)
        return {"key": C.KEY_NAMES[best[1]], "mode": best[2], "strength": _clamp(best[0])}


class FakeBeats:
    def run(self, pcm: np.ndarray, budget: Budget, **_: Any) -> dict:
        hop = FS // 100
        n = len(pcm) // hop
        if n < 200:
            return {"beats_rel": np.zeros(0), "downbeats_rel": np.zeros(0)}
        e = np.log10(np.square(pcm[:n * hop].reshape(n, hop), dtype=np.float64).sum(axis=1) + 1e-9)
        env = np.maximum(0.0, np.diff(e, prepend=e[0]))
        env = env - env.mean()
        ac = np.correlate(env, env, mode="full")[n - 1:] / (n - np.arange(n))   # unbiased: per overlapping pair
        lo, hi = 30, 100                             # 0.3-1.0 s at 100 Hz
        seg = ac[lo - 1:hi + 2]
        peaks = [i for i in range(1, len(seg) - 1) if seg[i] >= seg[i - 1] and seg[i] >= seg[i + 1]]
        top = max((seg[i] for i in peaks), default=0.0)
        # the shortest lag that is a local peak within 90 % of the best (a beat, not a bar)
        period = lo - 1 + min((i for i in peaks if seg[i] >= 0.9 * top), default=int(np.argmax(seg[1:-1])) + 1)
        phase = int(np.argmax([env[p::period].sum() for p in range(period)]))
        beats = np.arange(phase, n, period) / 100.0
        strength = [env[int(round(b * 100))::4 * period].sum() if int(round(b * 100)) < n else -1e9 for b in beats[:4]]
        first = int(np.argmax(strength)) if len(strength) else 0
        return {"beats_rel": beats, "downbeats_rel": beats[first::4]}


# -- loading -----------------------------------------------------------------------------------------------------------
def versions() -> dict:
    out = {"python": platform.python_version(), "numpy": np.__version__, "onnxruntime": "", "essentia": ""}
    try:
        import onnxruntime
        out["onnxruntime"] = onnxruntime.__version__
    except Exception:                                 # noqa: BLE001  absent: ""
        pass
    try:
        import essentia
        out["essentia"] = str(essentia.__version__)
    except Exception:                                 # noqa: BLE001
        pass
    return out


class Backends:
    """What the worker runs: ``tasks`` (ML_TASKS -> runner or None) and ``models`` (hello's catalogue name -> "ok" |
    "missing" | "failed: <why>" | "fake")."""

    def __init__(self) -> None:
        self.tasks: dict[str, Any] = {t: None for t in C.ML_TASKS}
        self.models: dict[str, str] = {}
        self.key_sig: tuple = ()


def model_set(cfg: dict) -> tuple:
    """The parts of the ml config that decide which models load (a change means a reload)."""
    t = cfg.get("tasks", {})
    heads = t.get("tags", {}).get("heads") or {}
    return (t.get("ced", {}).get("model"), t.get("tags", {}).get("model"), tuple(sorted(heads.items())),
            t.get("key", {}).get("profile"), t.get("beats", {}).get("model"), t.get("tags", {}).get("window_s"))


def load(cfg: dict, models_dir: pathlib.Path, *, fake: bool = False,
         log: Callable[[str], None] = lambda s: None) -> Backends:
    """Load every model the config names. A missing or failing model leaves its task (or its field) out and says so
    in ``models``; nothing raises."""
    b = Backends()
    b.key_sig = model_set(cfg)
    t = cfg.get("tasks", {})
    heads_cfg = dict(t.get("tags", {}).get("heads") or {})
    names = [t.get("ced", {}).get("model"), t.get("tags", {}).get("model"), *heads_cfg.values(),
             t.get("beats", {}).get("model")]
    if fake:
        for n in names:
            if n:
                b.models[str(n)] = "fake"
        b.tasks = {"ced": FakeCed(), "tags": FakeTags(), "key": FakeKey(), "beats": FakeBeats()}
        return b

    index = read_index(models_dir)
    loaded: dict[str, Model] = {}

    def get(name: Optional[str], *, session: bool = True) -> Optional[Model]:
        if not name:
            return None
        if name in loaded:
            return loaded[name]
        try:
            m = Model(name, models_dir, index, session=session)
        except ModelError as e:
            b.models[name] = "missing" if str(e) == "missing" else f"failed: {e}"[:200]
            return None
        except Exception as e:                        # noqa: BLE001  onnxruntime/essentia errors
            b.models[name] = f"failed: {type(e).__name__}: {e}"[:200]
            log(f"model {name}: {b.models[name]}")
            return None
        loaded[name] = m
        b.models[name] = "ok"
        return m

    try:
        ced = get(t.get("ced", {}).get("model"))
        if ced is not None:
            b.tasks["ced"] = CedTask(ced, cfg.get("ced_groups") or {}, log)
    except Exception as e:                            # noqa: BLE001
        b.models[t["ced"]["model"]] = f"failed: {e}"[:200]

    try:
        mel = None
        backbone = get(t.get("tags", {}).get("model"))
        if backbone is not None:
            mel = MusicnnMel()
            heads: dict[str, Model] = {}
            backbones = {backbone.name: backbone}
            for field, hname in heads_cfg.items():
                hm = get(hname, session=True)
                if hm is None:
                    continue
                via = hm.facts.get("via")
                if via and via not in backbones:
                    vb = get(via)
                    if vb is None:
                        b.models[hname] = f"failed: its backbone {via} is {b.models.get(via, 'missing')}"
                        continue
                    backbones[via] = vb
                if hm.kind in ("binary", "regression"):
                    try:
                        hm.index_of(hm.facts.get("yes"))
                    except ModelError as e:
                        b.models[hname] = f"failed: {e}"[:200]
                        continue
                heads[str(field)] = hm
            b.tasks["tags"] = TagsTask(backbone, heads, backbones, mel, float(t.get("tags", {}).get("window_s", 2.1)),
                                       int(t.get("tags", {}).get("styles_top", 5)))
    except Exception as e:                            # noqa: BLE001
        b.models[str(t.get("tags", {}).get("model"))] = f"failed: {type(e).__name__}: {e}"[:200]

    try:
        b.tasks["key"] = KeyTask(str(t.get("key", {}).get("profile") or "bgate"))
    except Exception as e:                            # noqa: BLE001  no essentia: no key
        log(f"key: {type(e).__name__}: {e}")

    try:
        bm = get(t.get("beats", {}).get("model"))
        if bm is not None:
            b.tasks["beats"] = BeatsTask(bm)
    except Exception as e:                            # noqa: BLE001
        b.models[str(t.get("beats", {}).get("model"))] = f"failed: {type(e).__name__}: {e}"[:200]
    return b
