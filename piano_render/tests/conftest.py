"""Fixtures: tiny synthetic MIDI files, a synthetic SFZ instrument (WAV samples made here), TimGM6mb for FluidSynth,
and a throwaway registry + render cache per test. Nothing here touches the real cache or config."""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

SR = 48000
TIMGM = Path("/usr/share/sounds/sf2/TimGM6mb.sf2")
SCRATCH_SFIZZ = Path("/tmp/claude-1000/-home-gjbm2-dev-screen-machine/1e542d58-42a4-4fbd-895f-1f2019aa49b3/"
                     "scratchpad/engines/sfizz-linux")
REAL_ENGINES = Path("~/.local/share/screen-machine/piano/engines").expanduser()


def find_sfizz_tree() -> "Path | None":
    """A directory holding libsfizz.so.1 somewhere below it (the real install first, then the research copy)."""
    env = os.environ.get("PIANO_RENDER_SFIZZ_LIB")
    if env and Path(env).exists():
        return Path(env).resolve().parent
    for d in (REAL_ENGINES / "sfizz", SCRATCH_SFIZZ):
        if d.is_dir() and any(d.rglob("libsfizz.so.1")):
            return d
    return None


def write_midi(path: Path, events, tail_s: float = 0.0) -> Path:
    """events: (t_s, type, a, b) with type 'on' (key, vel), 'off' (key, vel), 'cc' (num, value). 1 tick = 1 ms."""
    import mido
    mf = mido.MidiFile(type=0, ticks_per_beat=1000)
    tr = mido.MidiTrack()
    mf.tracks.append(tr)
    tr.append(mido.MetaMessage("set_tempo", tempo=1_000_000, time=0))      # 1 beat = 1 s -> 1 tick = 1 ms
    now = 0
    for t, kind, a, b in sorted(events, key=lambda e: e[0]):
        tick = int(round(t * 1000))
        dt = tick - now
        now = tick
        if kind == "on":
            tr.append(mido.Message("note_on", note=a, velocity=b, time=dt))
        elif kind == "off":
            tr.append(mido.Message("note_off", note=a, velocity=b, time=dt))
        else:
            tr.append(mido.Message("control_change", control=a, value=b, time=dt))
    tr.append(mido.MetaMessage("end_of_track", time=int(tail_s * 1000)))
    mf.save(str(path))
    return path


@pytest.fixture
def midi(tmp_path):
    def make(events, name="t.mid", tail_s=0.0):
        return write_midi(tmp_path / name, events, tail_s)
    return make


def make_sfz(d: Path, sample_s: float = 6.0, release_s: float = 0.08) -> Path:
    """A one-sample SFZ: a 440 Hz partial stack with an instant attack (full level from sample 0) and a slow
    decay, keycentre 69, spread over the whole keyboard; ampeg_release short so a released note dies fast."""
    import soundfile as sf
    d.mkdir(parents=True, exist_ok=True)
    t = np.arange(int(sample_s * SR)) / SR
    x = sum((0.5 / k) * np.sin(2 * np.pi * 440 * k * t + np.pi / 2 * (k == 1)) for k in (1, 2, 3))
    x = (x * np.exp(-t / 4.0)).astype(np.float32)
    sf.write(str(d / "a440.wav"), np.stack([x, x], 1), SR, subtype="FLOAT")
    (d / "test.sfz").write_text(
        "<control> default_path=./\n"
        f"<global> ampeg_release={release_s} amp_veltrack=100\n"
        "<region> sample=a440.wav pitch_keycenter=69 lokey=21 hikey=108\n")
    return d / "test.sfz"


@pytest.fixture
def sfizz_tree():
    tree = find_sfizz_tree()
    if tree is None:
        pytest.skip("libsfizz not installed (fetch sfizz, or set PIANO_RENDER_SFIZZ_LIB)")
    return tree


@pytest.fixture
def cache(tmp_path, monkeypatch):
    d = tmp_path / "cache"
    monkeypatch.setenv("PIANO_RENDER_CACHE", str(d))
    return d


@pytest.fixture
def registry_factory(tmp_path, monkeypatch):
    """Write an instruments.json in tmp (root = tmp/instruments, engines_root = tmp/engines with sfizz linked in
    when available) and return a loader; the config is also put in $PIANO_RENDER_CONFIG for the CLI tests."""
    from piano_render import instruments

    root = tmp_path / "instruments"
    engines_root = tmp_path / "engines"
    engines_root.mkdir(parents=True, exist_ok=True)
    tree = find_sfizz_tree()
    if tree is not None and not (engines_root / "sfizz").exists():
        (engines_root / "sfizz").symlink_to(tree, target_is_directory=True)
    monkeypatch.delenv("PIANO_RENDER_SFIZZ_LIB", raising=False)

    def make(instruments_: dict, rooms: "dict | None" = None, default: "str | None" = None):
        cfg = {"_comment": "test", "root": str(root), "engines_root": str(engines_root),
               "default": default or next(iter(instruments_)), "rooms": rooms or {}, "instruments": instruments_}
        p = tmp_path / "instruments.json"
        p.write_text(json.dumps(cfg, indent=1))
        monkeypatch.setenv("PIANO_RENDER_CONFIG", str(p))
        return instruments.load(p)

    make.root = root
    make.engines_root = engines_root
    return make


@pytest.fixture
def sfz_registry(registry_factory, sfizz_tree):
    sfz = make_sfz(registry_factory.root / "synth")
    return registry_factory({
        "synth": {"label": "Test sine piano", "engine": "sfizz", "file": str(sfz.relative_to(registry_factory.root)),
                  "order": 1, "gain_db": 0.0},
        "basic": {"label": "Basic (Windows synth)", "engine": "windows-gs", "order": 99},
    })


@pytest.fixture
def gm_registry(registry_factory):
    if not TIMGM.exists():
        pytest.skip("TimGM6mb.sf2 missing")
    return registry_factory({
        "gm": {"label": "GM piano", "engine": "fluidsynth", "file": str(TIMGM), "preset": {"bank": 0, "program": 0},
               "order": 1},
    })


def onset_s(audio: np.ndarray, threshold_dbfs: float = -50.0) -> float:
    env = np.max(np.abs(audio), axis=1)
    idx = np.flatnonzero(env > 10 ** (threshold_dbfs / 20))
    return idx[0] / SR if idx.size else float("nan")


def rms_db(x: np.ndarray) -> float:
    r = float(np.sqrt(np.mean(np.square(x.astype(np.float64))))) if x.size else 0.0
    return 20 * np.log10(r) if r > 0 else -300.0


def pitch_hz(x: np.ndarray, lo: float = 100.0, hi: float = 2000.0) -> float:
    x = x.astype(np.float64)
    w = np.hanning(len(x))
    n = 8 * len(x)
    X = np.abs(np.fft.rfft(x * w, n))
    f = np.fft.rfftfreq(n, 1 / SR)
    band = (f >= lo) & (f <= hi)
    return float(f[band][np.argmax(X[band])])
