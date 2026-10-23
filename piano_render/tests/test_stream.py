"""piano_render.stream and dsp/streaming.py: a streamed render is the offline one block by block (engines exactly;
realism and master within float rounding, apart from the documented pedal-noise level), pacing, the limiter, and the
memory cap. Synthetic SFZ (conftest.make_sfz) and TimGM6mb; no sound, nothing outside tmp."""

import json
import os
import subprocess
import sys

import numpy as np
import pytest

from piano_render import engines, master, realism, score, stream
from piano_render.dsp import resonance as _res
from piano_render.dsp import streaming as S

from .conftest import SR, make_sfz, rms_db

PERF = score.from_events([
    (0.05, "cc", 64, 100), (0.2, "on", 60, 90), (0.25, "on", 64, 70), (0.3, "on", 67, 60), (1.0, "off", 60, 64),
    (1.1, "off", 64, 64), (1.2, "off", 67, 64), (1.5, "cc", 64, 0), (1.6, "cc", 67, 127), (1.7, "on", 72, 100),
    (2.2, "off", 72, 50), (2.4, "cc", 67, 0), (2.5, "cc", 64, 127), (2.6, "on", 48, 110), (3.0, "off", 48, 64),
    (3.4, "cc", 64, 0)])


@pytest.fixture(autouse=True)
def _no_probe(monkeypatch, tmp_path):
    """Both sides use the model tuning (the offline would otherwise measure the test SFZ, a 5-minute probe)."""
    from piano_render.dsp import analysis
    monkeypatch.setenv("PIANO_RENDER_ANALYSIS", str(tmp_path / "analysis"))
    monkeypatch.setattr(analysis, "tuning_for", lambda inst, engine, sr, progress=None: analysis.model_tuning())


@pytest.fixture
def reg(registry_factory, sfizz_tree):
    sfz = make_sfz(registry_factory.root / "synth")
    f = str(sfz.relative_to(registry_factory.root))
    base = {"engine": "sfizz", "file": f, "gain_db": 6.0, "room": "small", "resonance_db": -35.0,
            "eq": [{"type": "highshelf", "f": 3000, "gain_db": -2.0}], "width": 0.8}
    rooms = {"small": {"ir": None, "synth": {"rt60_s": 0.6, "predelay_ms": 8, "width": 1.0}, "wet_db": -16},
             "dry": {"ir": None, "synth": None, "wet_db": None}}
    return registry_factory({
        "full": base,
        "room-only": dict(base, resonance_db=None),
        "bank-only": dict(base, room="dry"),
        "bare": dict(base, resonance_db=None, room="dry"),
        "loud": dict(base, gain_db=30.0, resonance_db=None, room="dry"),
        "blend": dict(base, resonance_db=None, room="dry", layers=[{"file": f}, {"file": f, "gain_db": -6.0,
                                                                                 "offset_ms": 2.5}]),
    }, rooms=rooms)


def offline(reg, inst_id, perf=PERF):
    """What render.py makes (before the FLAC): engine -> realism.apply -> master.process."""
    inst = reg.get(inst_id)
    e = engines.create(inst, reg.engines_root)
    try:
        a = e.render(perf, SR)
        room = reg.room(inst["room"]) if inst.get("room") else None
        a = realism.apply(a, SR, perf, inst, room, engine=e)
        out, st = master.process(a, SR, gain_db=float(inst.get("gain_db") or 0.0))
    finally:
        e.close()
    return out, st


def streamed(reg, inst_id, perf=PERF, **kw):
    st = stream.Streamer(perf, inst_id, registry=reg, **kw)
    try:
        st.open()
        return st.render_all(), st.stats
    finally:
        st.close()


def rel_db(y, ref):
    n = min(len(y), len(ref))
    return rms_db(y[:n] - ref[:n]) - rms_db(ref[:n])


# --- engines ----------------------------------------------------------------------------------------------------
def _engine_cases(request):
    if request.param == "sfizz":
        r = request.getfixturevalue("sfz_registry")
        return engines.create(r.get("synth"), r.engines_root), 69
    r = request.getfixturevalue("gm_registry")
    return engines.create(r.get("gm"), r.engines_root), 60


@pytest.fixture(params=["sfizz", "fluidsynth"])
def eng(request):
    e, key = _engine_cases(request)
    yield e, key
    e.close()


@pytest.mark.parametrize("realtime", [False, True])
def test_engine_blocks_are_the_offline_render(eng, realtime):
    e, key = eng
    perf = score.from_events([(0.1, "cc", 64, 127), (0.3, "on", key, 100), (0.6, "off", key, 64),
                              (0.65, "on", key + 4, 30), (1.5, "cc", 64, 0)])
    e.realtime = realtime
    e.render(perf, SR)                  # FluidSynth's first render after a load differs a little from the rest
    ref = e.render(perf, SR)
    for k in (1, 2, 3):
        e.start(perf, SR)
        parts = []
        while not e.finished:
            parts.append(e.render_blocks(k))
        got = np.concatenate(parts)
        assert got.shape == ref.shape and np.array_equal(got, ref), (e.name, realtime, k)
    hb = e.holdback_frames(SR)
    assert hb % e.block == 0 and hb >= round(0.05 * SR)


def test_engine_stream_keeps_the_tail_fade():
    from .test_engines import _hanging
    e = _hanging(-75.0, 10.0)                     # runs to the cap and fades (test_engines' case)
    perf = score.from_events([(0.2, "on", 60, 100), (0.5, "off", 60, 64)])
    ref = e.render(perf, SR)
    e.start(perf, SR)
    got = np.concatenate([e.render_blocks(7) for _ in range(10_000) if not e.finished])
    assert np.array_equal(got, ref) and e.stats["tail_end"] == "cap"


def test_layers_stream_like_the_offline_blend(reg):
    e = engines.create(reg.get("blend"), reg.engines_root)
    try:
        ref = e.render(PERF, SR)
        e.realtime = True
        e.start(PERF, SR)
        got = np.concatenate([e.render_blocks(2) for _ in range(10_000) if not e.finished])
        assert got.shape == ref.shape and np.array_equal(got, ref)
    finally:
        e.close()


def test_stream_mixer_shifts_and_gains():
    from piano_render.engines.base import StreamMixer
    rng = np.random.default_rng(1)
    a = rng.standard_normal((1000, 2)).astype(np.float32)
    b = rng.standard_normal((700, 2)).astype(np.float32)
    m = StreamMixer([1.0, 0.5], [3, -5])
    out = []
    for i in range(0, 1000, 64):
        m.push(0, a[i:i + 64], i + 64 >= 1000)
        if i < 700:
            m.push(1, b[i:i + 64], i + 64 >= 700)
        out.append(m.pull())
    out.append(m.pull())
    got = np.concatenate(out)
    want = np.zeros((1003, 2), np.float32)
    want[3:] += a
    want[:695] += b[5:] * np.float32(0.5)
    assert m.done and got.shape == want.shape and np.allclose(got, want, atol=1e-6)


# --- realism and master -------------------------------------------------------------------------------------------
def test_bare_chain_is_exact(reg):
    ref, _ = offline(reg, "bare")
    got, st = streamed(reg, "bare")
    assert got.shape == ref.shape and np.array_equal(got, ref)
    assert st["late_blocks"] == 0


def test_room_matches_the_offline_convolution(reg):
    ref, _ = offline(reg, "room-only")
    got, st = streamed(reg, "room-only")
    assert rel_db(got, ref) < -120
    # the IR's tail rings on after the last note, and the end is the realism trim's (within QUIET_END_S)
    assert len(ref) - SR // 100 <= len(got) <= len(ref) + int(S.QUIET_END_S * SR) + 2 * stream.CHAIN_FRAMES
    assert st["chain"]["room"]["ir_mode"] == "mono-in"


def test_bank_matches_the_offline_bank_without_pedal_noise(reg, monkeypatch):
    monkeypatch.setattr(_res, "BURST_DB", -300.0)
    ref, _ = offline(reg, "bank-only")
    got, _ = streamed(reg, "bank-only")
    assert rel_db(got, ref) < -120


def test_full_chain_is_close(reg):
    # the pedal-lift noise level follows the dry's running RMS (the offline: the whole piece's): a halo difference
    ref, _ = offline(reg, "full")
    got, st = streamed(reg, "full")
    assert rel_db(got, ref) < -50
    assert st["una_corda"]["segments"] >= 1 and st["chain"]["resonance"]["resonators"] > 1000


@pytest.mark.parametrize("block", [64, 256, 1000])
def test_block_size_never_changes_the_output(reg, block):
    a, _ = streamed(reg, "full", block=block)
    b, _ = streamed(reg, "full", block=512)
    n = min(len(a), len(b))
    assert np.array_equal(a[:n], b[:n])
    # the end: the trim's, plus whatever quiet frames had already been handed over when it was found
    assert abs(len(a) - len(b)) <= int(S.QUIET_END_S * SR) + 2 * stream.CHAIN_FRAMES
    assert np.max(np.abs(a[n:]), initial=0.0) < 1e-5 and np.max(np.abs(b[n:]), initial=0.0) < 1e-5


def test_chain_state_carries_across_odd_pushes(reg):
    """Stages fed in irregular pieces give the same output as fed in one go (state across block boundaries)."""
    from piano_render.dsp import room as _room
    inst = reg.get("full")
    rng = np.random.default_rng(7)
    x = (0.05 * rng.standard_normal((SR * 2, 2))).astype(np.float32)
    ctl = PERF.controls(_res.FS_BANK / _res.BLOCK, duration_s=PERF.duration_s + 45)

    def run(sizes):
        stages = [S.ToneStage(inst, SR, ctl), S.ResonanceStage(SR, PERF, -35.0, None, ctl=ctl),
                  S.RoomStage(reg.room("small"), SR), S.MasterStage(SR, 20.0)]
        ch = S.Chain(stages)
        out, i, k = [], 0, 0
        while i < x.shape[0]:
            n = sizes[k % len(sizes)]
            out.append(ch.push(x[i:i + n]))
            i += n
            k += 1
        for _ in range(40):
            out.append(ch.push(np.zeros((1024, 2), np.float32)))
        return np.concatenate(out)

    a = run([4096])
    b = run([1, 333, 512, 7, 2048, 99])
    n = min(len(a), len(b))
    assert n > x.shape[0] and np.array_equal(a[:n], b[:n])
    assert _room.load_ir(reg.room("small"), SR) is not None


def test_limiter_holds_the_ceiling_and_is_a_plain_gain_when_idle():
    sr = SR
    n = 2 * sr
    t = np.arange(n) / sr
    x = np.zeros((n, 2), np.float32)
    x[:, 0] = (0.1 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    x[:, 1] = x[:, 0]
    x[sr // 2:sr // 2 + 300] *= 12.0                    # a 6 ms burst 21.6 dB louder
    m = S.MasterStage(sr, 6.0)
    y = np.concatenate([m.push(x[i:i + 512]) for i in range(0, n, 512)] +
                       [m.push(np.zeros((512, 2), np.float32)) for _ in range(2)])[:n]
    assert m.delay <= round(0.005 * sr) + S.TP_HALF
    g = np.float32(10 ** (6 / 20))
    assert 20 * np.log10(master.true_peak(y)) <= -1.0 + 1e-3
    quiet = slice(0, sr // 2 - 2 * m.delay)
    assert np.array_equal(y[quiet], x[quiet] * g)       # untouched (and not shifted) away from the burst
    assert np.allclose(y[-sr // 4:], x[-sr // 4:] * g, rtol=1e-3, atol=0)    # released again
    assert m.stats()["min_gain_db"] < -8              # 2.4 (+7.6 dBFS) down to the -1 dBTP ceiling
    # no limiter: exactly master.process's gain
    m2 = S.MasterStage(sr, 6.0, limiter=False)
    assert np.array_equal(m2.push(x), x * g)


def test_loud_instrument_is_limited_not_turned_down(reg):
    ref, mst = offline(reg, "loud")                       # 30 dB of gain: master turns the whole render down
    got, st = streamed(reg, "loud")
    assert mst.limited
    assert 20 * np.log10(master.true_peak(got)) <= -1.0 + 1e-3
    assert st["chain"]["master"]["limited_s"] > 0
    assert rms_db(got) > rms_db(ref)                     # only the peaks come down


# --- pacing -----------------------------------------------------------------------------------------------------
def test_pacing_stays_within_ahead_of_the_sink(reg):
    now = [0.0]
    st = stream.Streamer(PERF, "bare", registry=reg, ahead_s=0.2, block=512, clock=lambda: now[0], max_speed=None,
                         sleep=lambda dt: now.__setitem__(0, now[0] + dt))
    st.open()
    ahead = int(0.2 * SR)
    first_fill = None
    try:
        for blk in st.blocks():
            end = blk.pos + blk.data.shape[0]
            if first_fill is None and end >= ahead:
                first_fill = now[0]                       # the buffer filled before any time passed
            assert end <= now[0] * SR + ahead + 512 + 1   # never more than ahead_s (+ a block) ahead
    finally:
        st.close()
    assert first_fill == 0.0
    total = st.stats["audio_s"]
    assert now[0] == pytest.approx(total - 0.2, abs=0.05)     # played out at 1x after the fill
    assert st.stats["late_blocks"] == 0


def test_live_sfizz_never_renders_faster_than_its_loader_allows(reg):
    from piano_render.engines import sfizz
    now = [0.0]
    st = stream.Streamer(PERF, "bare", registry=reg, ahead_s=0.5, clock=lambda: now[0],
                         sleep=lambda dt: now.__setitem__(0, now[0] + dt))
    st.open()
    cap = sfizz.STREAM_PRELOAD / SR / sfizz.LOADER_BUDGET_S
    assert st.max_speed == pytest.approx(cap, rel=1e-3) and cap > 2
    try:
        for blk in st.blocks():
            # the engine never more than one chunk ahead of the speed line
            assert st._eng_frames <= now[0] * cap * SR + 2 * stream.CHAIN_FRAMES + 1
    finally:
        st.close()
    assert st.stats["speed_waits"] > 0 and st.stats["late_blocks"] == 0
    # a fresh note's preloaded start lasts at least LOADER_BUDGET_S of wall time
    assert sfizz.STREAM_PRELOAD / SR / cap >= sfizz.LOADER_BUDGET_S - 1e-9


def test_pacing_follows_a_sink_position_and_counts_late_blocks(reg):
    played = [0.0]
    st = stream.Streamer(PERF, "bare", registry=reg, ahead_s=0.1, sleep=lambda dt: played.__setitem__(
        0, played[0] + dt * SR))
    st.open()
    try:
        it = st.blocks(position=lambda: played[0])
        for i, blk in enumerate(it):
            if i == 20:
                played[0] += SR                            # the sink jumps 1 s ahead: the next blocks are late
            assert blk.pos + blk.data.shape[0] <= played[0] + 0.1 * SR + 512 + 1
    finally:
        st.close()
    assert st.stats["late_blocks"] > 0 and st.stats["max_late_ms"] > 500


# --- the process -----------------------------------------------------------------------------------------------
def test_memory_cap_bounds_the_process():
    code = r"""
import json, resource, numpy as np
from piano_render import stream
info = stream.prepare_process(nice=5, data_cap_mb=400)
soft, hard = resource.getrlimit(resource.RLIMIT_DATA)
ok_small = np.ones(20 << 20, np.uint8).sum() > 0
try:
    big = np.ones(800 << 20, np.uint8)
    big_ok = True
except MemoryError:
    big_ok = False
import os
print(json.dumps({"info": info, "soft": soft, "small": bool(ok_small), "big": big_ok, "nice": os.nice(0)}))
"""
    env = dict(os.environ, PYTHONPATH=os.getcwd())
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0, r.stderr
    d = json.loads(r.stdout.strip().splitlines()[-1])
    assert d["soft"] == 400 << 20 and d["small"] and not d["big"]
    assert d["nice"] >= 5 and d["info"]["data_cap_mb"] == 400
    assert d["info"]["core_dumps"] is False             # at the cap sfizz aborts: no 1 GB WSL crash dump


@pytest.mark.parametrize("inst_id", ["room-only", "full"])
def test_rewind_plays_the_same_piece_again_with_the_samples_still_loaded(reg, inst_id):
    """A loop or a re-press: rewind() after a whole stream, and after one cut short, gives the same audio again."""
    st = stream.Streamer(PERF, inst_id, registry=reg)
    try:
        st.open()
        a = st.render_all()
        eng = st.engines[0]
        took = st.rewind()
        assert took["rewind_s"] < 2.0 and st.engines[0] is eng                    # nothing loaded again
        b = st.render_all()
        for _ in range(20):                                                       # cut short...
            st.next_block()
        st.rewind()
        c = st.render_all()                                                       # ...and from frame 0 again
    finally:
        st.close()
    assert len(a) == len(b) == len(c)
    limit = -100.0 if inst_id == "room-only" else -40.0                           # the bank's pedal noise is random
    assert rel_db(b, a) < limit and rel_db(c, a) < limit


def test_stream_voices_caps_the_live_engine_only(registry_factory, sfizz_tree):
    """instruments.json "stream_voices": the voice cap while streaming (a lighter live load); offline keeps 256."""
    sfz = make_sfz(registry_factory.root / "synth")
    reg = registry_factory({"capped": {"engine": "sfizz", "file": str(sfz.relative_to(registry_factory.root)),
                                       "stream_voices": 2}})
    chord = score.from_events([(0.0, "on", k, 90) for k in (48, 52, 55, 60, 64)] + [(1.0, "off", 48, 64)])
    st = stream.Streamer(chord, "capped", registry=reg, realism=False)
    try:
        st.open()
        st.render_all()
        assert st.stats["engine_load"]["voices"] == 8 and st.stats["engine"]["peak_voices"] <= 8    # at least 8
    finally:
        st.close()
    e = engines.create(reg.get("capped"), reg.engines_root)
    try:
        e.render(chord, SR)
        assert e.stats["voices"] == 256
    finally:
        e.close()

