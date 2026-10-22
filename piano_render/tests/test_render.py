"""render(): cache hit/miss, the key, the sidecar, progress, per-key locking (threads and processes), alignment."""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from piano_render import engines, render
from piano_render.engines.base import Engine

from .conftest import SR, onset_s

REPO = Path(__file__).resolve().parents[2]
PY = sys.executable


class FakeEngine(Engine):
    """A 1 kHz burst per note-on; ``delay_s`` of sleep per block makes renders slow on purpose."""
    name = "fake"
    renders = 0
    delay_s = 0.0
    _lock = threading.Lock()

    @classmethod
    def available(cls, root):
        return True

    def _load(self, sr):
        self.sr = sr
        self.pos = 0
        self.burst = (0.5 * np.sin(2 * np.pi * 1000 * np.arange(int(0.1 * sr)) / sr)).astype(np.float32)
        self.active = []

    def _reset(self):
        self.pos = 0
        self.active = []
        with FakeEngine._lock:
            FakeEngine.renders += 1

    def _set_pitch(self, c):
        pass

    def _render_block(self, l, r, evs):
        for off, e, v in evs:
            if e.kind == "on":
                self.active.append(self.pos + off)
        n = len(l)
        for s in self.active:
            a, b = max(s, self.pos), min(s + len(self.burst), self.pos + n)
            if b > a:
                l[a - self.pos:b - self.pos] += self.burst[a - s:b - s]
                r[a - self.pos:b - self.pos] += self.burst[a - s:b - s]
        self.active = [s for s in self.active if s + len(self.burst) > self.pos + n]
        self.pos += n
        if FakeEngine.delay_s:
            time.sleep(FakeEngine.delay_s)

    def _active_voices(self):
        return len(self.active)


@pytest.fixture
def fake(registry_factory, cache):
    engines.register("fake", FakeEngine)
    FakeEngine.renders = 0
    FakeEngine.delay_s = 0.0
    render.release_engines()
    reg = registry_factory({
        "f": {"label": "Fake", "engine": "fake", "file": "unused", "order": 1, "gain_db": 0.0, "room": "small"},
        "f2": {"label": "Fake 2", "engine": "fake", "file": "unused", "order": 2},
        "basic": {"label": "Basic (Windows synth)", "engine": "windows-gs", "order": 9},
    }, rooms={"small": {"label": "Small room", "ir": None, "synth": {"rt60_s": 0.8, "predelay_ms": 12, "width": 1.0},
                        "wet_db": -16},
              "hall": {"label": "Hall", "ir": None, "synth": {"rt60_s": 2.0, "predelay_ms": 20, "width": 1.0},
                       "wet_db": -18}})
    yield reg
    render.release_engines()
    engines.unregister("fake")


def test_miss_then_hit_and_sidecar(fake, midi, cache):
    m = midi([(0.25, "on", 60, 100), (0.5, "off", 60, 64)])
    r1 = render.render(m, "f", registry=fake)
    assert not r1.cached and FakeEngine.renders == 1
    assert Path(r1.path).exists() and Path(r1.path).parent == cache and r1.path.endswith(f"{r1.cache_key}.flac")
    side = json.loads((cache / f"{r1.cache_key}.json").read_text())
    assert side == json.loads(json.dumps(r1.to_json()))
    assert side["room"] == "small" and side["realism"] is True and side["engine"] == "fake"
    assert not list(cache.glob("*.progress.json")) and not list(cache.glob("*.lock")) and not list(cache.glob("*tmp*"))
    r2 = render.render(m, "f", registry=fake)
    assert r2.cached and FakeEngine.renders == 1 and r2.path == r1.path and r2.cache_key == r1.cache_key
    assert render.cached(m, "f", registry=fake).cache_key == r1.cache_key
    r3 = render.render(m, "f", registry=fake, force=True)
    assert not r3.cached and FakeEngine.renders == 2


def test_file_time_zero_is_midi_time_zero(fake, midi):
    m = midi([(0.75, "on", 60, 100), (1.0, "off", 60, 64)])
    r = render.render(m, "f", registry=fake)
    a, sr = sf.read(r.path, dtype="float32", always_2d=True)
    assert sr == 48000 and a.shape[1] == 2
    assert int(np.flatnonzero(np.abs(a).max(1) > 1e-4)[0]) == int(0.75 * SR) + 1   # sin(0) = 0, then the burst
    assert r.stats["first_onset_ms"] == pytest.approx(1000 / SR, abs=0.05)
    assert r.duration_s == pytest.approx(a.shape[0] / SR)


def test_key_changes(fake, midi, registry_factory, cache):
    m = midi([(0.1, "on", 60, 100), (0.3, "off", 60, 64)])
    m2 = midi([(0.1, "on", 61, 100), (0.3, "off", 61, 64)], name="u.mid")
    base = render.plan(m, "f", registry=fake).key
    assert render.plan(m, "f", registry=fake).key == base                       # deterministic
    assert render.plan(m2, "f", registry=fake).key != base                      # MIDI bytes
    assert render.plan(m, "f2", registry=fake).key != base                      # instrument
    assert render.plan(m, "f", room="hall", registry=fake).key != base          # room
    assert render.plan(m, "f", room="none", registry=fake).key != base          # dry
    assert render.plan(m, "f", realism=False, registry=fake).key != base        # realism flag
    no_r = render.plan(m, "f", realism=False, registry=fake)
    assert no_r.room_id is None
    assert render.plan(m, "f", realism=False, room="hall", registry=fake).key == no_r.key   # room moot w/o realism
    raw = {k: fake.raw(k) for k in fake.ids()}
    rooms = fake.data["rooms"]
    louder = registry_factory(dict(raw, f=dict(raw["f"], gain_db=3.0)), rooms=rooms)
    assert render.plan(m, "f", registry=louder).key != base                     # any sound-affecting field
    relabel = registry_factory(dict(raw, f=dict(raw["f"], label="Renamed", order=77, credit="x")), rooms=rooms)
    assert render.plan(m, "f", registry=relabel).key == base                    # cosmetic fields don't count
    rooms2 = dict(rooms, small=dict(rooms["small"], wet_db=-10))
    assert render.plan(m, "f", registry=registry_factory(raw, rooms=rooms2)).key != base
    orig = render.RENDER_VERSION
    try:
        render.RENDER_VERSION = orig + "x"
        assert render.plan(m, "f", registry=fake).key != base
    finally:
        render.RENDER_VERSION = orig


def test_gain_and_ceiling_through_render(fake, midi, registry_factory):
    m = midi([(0.1, "on", 60, 100), (0.6, "off", 60, 64)])
    raw = {k: fake.raw(k) for k in fake.ids()}
    reg = registry_factory(dict(raw, f=dict(raw["f"], gain_db=20.0)), rooms=fake.data["rooms"])
    r = render.render(m, "f", registry=reg)
    assert r.stats["master"]["limited"] and r.true_peak_dbtp <= -1.0
    a, _ = sf.read(r.path, dtype="float32")
    assert 20 * np.log10(np.abs(a).max()) <= -1.0 + 0.01


def test_progress_file_while_rendering(fake, midi, cache):
    FakeEngine.delay_s = 0.008                          # ~ 1.5 s for 2 s of audio
    m = midi([(0.1, "on", 60, 100), (2.0, "off", 60, 64)])
    key = render.plan(m, "f", registry=fake).key
    seen, states = [], []
    stop = threading.Event()

    def watch():
        while not stop.is_set():
            p = render.read_progress(key)
            if p:
                seen.append(p)
            time.sleep(0.05)

    w = threading.Thread(target=watch)
    w.start()
    try:
        render.render(m, "f", registry=fake, progress=states.append)
    finally:
        stop.set()
        w.join()
    assert seen, "no progress file seen"
    assert {s["stage"] for s in seen} & {"engine"}
    assert all({"stage", "done_s", "total_s"} <= set(s) for s in seen)
    eng = [s for s in seen if s["stage"] == "engine"]
    assert eng and eng[-1]["total_s"] == pytest.approx(2.0)
    updates = sorted({s["updated"] for s in seen})
    assert len(updates) >= 2 and max(np.diff(updates)) <= 1.0          # rewritten at least once a second
    assert [s["stage"] for s in states][-1] == "write" and "engine" in {s["stage"] for s in states}
    assert render.read_progress(key) is None                            # removed when done


def test_concurrent_same_key_threads_render_once(fake, midi):
    FakeEngine.delay_s = 0.002
    m = midi([(0.1, "on", 60, 100), (1.0, "off", 60, 64)])
    out = [None] * 4
    errs = []

    def go(i):
        try:
            out[i] = render.render(m, "f", registry=fake)
        except Exception as e:  # noqa: BLE001
            errs.append(e)

    ts = [threading.Thread(target=go, args=(i,)) for i in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errs
    assert FakeEngine.renders == 1
    assert sum(not r.cached for r in out) == 1 and len({r.path for r in out}) == 1


def test_lock_waits_then_times_out(tmp_path):
    lock = tmp_path / "k.lock"
    held = threading.Event()
    release = threading.Event()

    def holder():
        with render._key_lock(lock):
            held.set()
            release.wait(5)

    t = threading.Thread(target=holder)
    t.start()
    held.wait(5)
    t0 = time.monotonic()
    with pytest.raises(TimeoutError):
        with render._key_lock(lock, timeout=0.3):
            pass
    assert time.monotonic() - t0 >= 0.3
    release.set()
    t.join()
    with render._key_lock(lock, timeout=1):
        assert lock.exists()
    assert not lock.exists()


def test_windows_gs_and_unknowns_refused(fake, midi):
    m = midi([(0.1, "on", 60, 100)])
    with pytest.raises(ValueError):
        render.render(m, "basic", registry=fake)
    with pytest.raises(KeyError):
        render.render(m, "nope", registry=fake)
    with pytest.raises(KeyError):
        render.render(m, "f", room="attic", registry=fake)


def test_default_instrument(fake, midi):
    m = midi([(0.1, "on", 60, 100), (0.2, "off", 60, 64)])
    r = render.render(m, None, registry=fake)
    assert r.instrument == "f"


# --- real engines end to end ----------------------------------------------------------------------------------
def test_sfizz_end_to_end_alignment(sfz_registry, midi, cache):
    m = midi([(0.5, "on", 69, 100), (0.9, "off", 69, 64), (1.0, "on", 72, 80), (1.3, "off", 72, 64)])
    r = render.render(m, "synth", registry=sfz_registry)
    a, sr = sf.read(r.path, dtype="float32", always_2d=True)
    assert sr == 48000
    assert 0 <= (onset_s(a) - 0.5) * 1000 < 5.0
    assert r.engine == "sfizz" and r.lufs is not None and r.true_peak_dbtp <= -1.0 + 1e-6
    assert render.render(m, "synth", registry=sfz_registry).cached


def test_fluidsynth_end_to_end(gm_registry, midi, cache):
    m = midi([(0.4, "on", 60, 100), (0.9, "off", 60, 64)])
    r = render.render(m, "gm", registry=gm_registry)
    a, _ = sf.read(r.path, dtype="float32", always_2d=True)
    assert 0 <= (onset_s(a) - 0.4) * 1000 < 5.0
    assert r.engine == "fluidsynth" and r.stats["engine_version"].startswith("fluidsynth:2.")


def test_concurrent_same_key_processes_render_once(sfz_registry, midi, cache):
    # 40 s of notes so the first render is still going when the second process asks
    evs = []
    for i in range(80):
        evs += [(0.5 * i, "on", 48 + i % 24, 90), (0.5 * i + 0.4, "off", 48 + i % 24, 64)]
    m = midi(evs)
    env = dict(os.environ, PYTHONPATH=str(REPO))
    cmd = [PY, "-m", "piano_render", "--no-idle", "render", str(m), "--instrument", "synth", "--json"]
    procs = [subprocess.Popen(cmd, cwd=str(REPO), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
             for _ in range(2)]
    outs = [p.communicate(timeout=300) for p in procs]
    for p, (o, e) in zip(procs, outs):
        assert p.returncode == 0, e.decode()
    res = [json.loads(o) for o, _ in outs]
    assert sorted(r["cached"] for r in res) == [False, True]
    assert res[0]["path"] == res[1]["path"]


def test_room_resolution(fake, registry_factory):
    raw = {k: fake.raw(k) for k in fake.ids()}
    reg = registry_factory(raw, rooms=dict(fake.data["rooms"], dry={"label": "Dry", "ir": None, "synth": None}))
    inst = reg.get("f")
    assert render.resolve_room(reg, inst, None, True) == "small"       # the instrument's own room
    assert render.resolve_room(reg, inst, "hall", True) == "hall"
    assert render.resolve_room(reg, inst, "none", True) is None        # alias for no room
    assert render.resolve_room(reg, inst, "dry", True) == "dry"        # a configured room wins over any alias
    assert render.resolve_room(reg, inst, "hall", False) is None       # no realism, no room
    assert render.resolve_room(reg, reg.get("f2"), None, True) is None
