"""Both engines on tiny inputs: onset alignment, pedal, tail, pitch_cents, reuse. sfizz uses a synthetic SFZ made
in tmp; FluidSynth uses the system's TimGM6mb.sf2 (its key 69 sample has ~6 ms of lead-in silence, so key 60)."""

import numpy as np
import pytest

from piano_render import engines, score
from piano_render.engines.base import TAIL_CAP_S

from .conftest import SR, onset_s, pitch_hz, rms_db


def _engine(reg, inst_id):
    return engines.create(reg.get(inst_id), reg.engines_root)


@pytest.fixture(params=["sfizz", "fluidsynth"])
def eng(request):
    """(engine, key to play) for each engine."""
    if request.param == "sfizz":
        reg = request.getfixturevalue("sfz_registry")
        e, key = _engine(reg, "synth"), 69
    else:
        reg = request.getfixturevalue("gm_registry")
        e, key = _engine(reg, "gm"), 60
    yield e, key
    e.close()


def seg(a, t0, t1):
    return a[int(t0 * SR):int(t1 * SR)]


@pytest.mark.parametrize("t0", [0.5, 0.5 + 37 / SR, 1.234567])
def test_onset_alignment(eng, t0):
    e, key = eng
    perf = score.from_events([(t0, "on", key, 100), (t0 + 0.4, "off", key, 64)])
    a = e.render(perf, SR)
    assert a.dtype == np.float32 and a.ndim == 2 and a.shape[1] == 2
    assert not np.any(np.abs(a[: int(t0 * SR) - 1]) > 1e-6), "sound before the note-on"
    late_ms = (onset_s(a) - t0) * 1000
    # a note starts at frame round(t * sr): up to half a frame early
    assert -500 / SR <= late_ms < 5.0, f"{e.name}: first note audible {late_ms:.2f} ms after its MIDI time"


def test_sustain_pedal_holds_notes_past_note_off(eng):
    e, key = eng
    off = [(0.5, "on", key, 100), (1.0, "off", key, 64)]
    dry = e.render(score.from_events(off + [(3.0, "cc", 64, 0)]), SR)
    ped = e.render(score.from_events([(0.2, "cc", 64, 127)] + off + [(3.0, "cc", 64, 0)]), SR)
    # without the pedal the note is gone well before 2 s; with it, it still sounds at 2 s
    assert rms_db(seg(dry, 1.8, 2.2)) < -70
    assert rms_db(seg(ped, 1.8, 2.2)) > -50
    # pedal up at 3 s ends it
    assert rms_db(seg(ped, 3.6, 3.8)) < rms_db(seg(ped, 2.6, 2.9)) - 30


@pytest.mark.parametrize("cc", [64, 66])
def test_pedals_switch_at_64(eng, cc):
    # both engines: a damper or sostenuto value below 64 is up, 64 and above is down (sfizz alone would hold at 1)
    e, key = eng
    evs = [(0.5, "on", key, 100), (1.0, "off", key, 64), (3.0, "cc", cc, 0)]
    at = 0.2 if cc == 64 else 0.7                        # the sostenuto latches keys already down
    for v, held in ((1, False), (40, False), (63, False), (64, True), (100, True)):
        a = e.render(score.from_events([(at, "cc", cc, v)] + evs), SR)
        assert (rms_db(seg(a, 1.8, 2.2)) > -60) == held, (cc, v)


def test_half_pedal_instrument_gets_raw_cc64(sfz_registry):
    inst = dict(sfz_registry.get("synth"), half_pedal=True)
    e = engines.create(inst, sfz_registry.engines_root)
    try:
        a = e.render(score.from_events([(0.2, "cc", 64, 40), (0.5, "on", 69, 100), (1.0, "off", 69, 64),
                                        (3.0, "cc", 64, 0)]), SR)
        assert rms_db(seg(a, 1.8, 2.2)) > -50          # sfizz's own rule (any value >= 1 holds) now applies
    finally:
        e.close()


def test_tail_stops_when_voices_die_and_is_capped(eng):
    e, key = eng
    a = e.render(score.from_events([(0.3, "on", key, 100), (0.6, "off", key, 64)]), SR)
    assert a.shape[0] / SR < 0.6 + 5.0
    assert e.stats["tail_s"] >= 0
    # pedal never released: ends when the sample runs out or at the cap, never later
    b = e.render(score.from_events([(0.1, "cc", 64, 127), (0.3, "on", key, 100), (0.6, "off", key, 64)]), SR)
    assert b.shape[0] / SR <= 0.6 + TAIL_CAP_S + e.block / SR + 1e-9


def test_state_reset_between_renders(eng):
    e, key = eng
    e.render(score.from_events([(0.1, "cc", 64, 127), (0.2, "on", key, 100)]), SR)     # leaves pedal and note on
    a = e.render(score.from_events([(0.5, "on", key, 100), (1.0, "off", key, 64)]), SR)
    assert not np.any(np.abs(a[: int(0.5 * SR) - 1]) > 1e-6)
    assert rms_db(seg(a, 1.8, 2.2)) < -70


@pytest.mark.parametrize("cents", [50.0, -30.0])
def test_pitch_cents_moves_pitch_not_timing(eng, cents):
    e, key = eng
    perf = score.from_events([(0.5, "on", key, 100), (2.0, "off", key, 64)])
    a0 = e.render(perf, SR)
    a1 = e.render(perf, SR, pitch_cents=cents)
    f0 = pitch_hz(seg(a0, 0.7, 1.7)[:, 0])
    f1 = pitch_hz(seg(a1, 0.7, 1.7)[:, 0])
    assert 1200 * np.log2(f1 / f0) == pytest.approx(cents, abs=3.0)
    assert abs(onset_s(a1) - onset_s(a0)) < 1.5e-3
    a2 = e.render(perf, SR)                      # and back to 0 on the next render
    assert pitch_hz(seg(a2, 0.7, 1.7)[:, 0]) == pytest.approx(f0, rel=1e-3)


def test_velocity_gamma(sfz_registry):
    reg = sfz_registry
    inst = reg.get("synth")
    e1 = engines.create(inst, reg.engines_root)
    e2 = engines.create(dict(inst, velocity_gamma=2.0), reg.engines_root)
    try:
        perf = score.from_events([(0.2, "on", 69, 64), (0.8, "off", 69, 64)])
        quiet = rms_db(seg(e2.render(perf, SR), 0.3, 0.7))
        normal = rms_db(seg(e1.render(perf, SR), 0.3, 0.7))
        assert quiet < normal - 3
    finally:
        e1.close()
        e2.close()


def test_windows_gs_is_never_rendered():
    with pytest.raises(engines.EngineError):
        engines.engine_class("windows-gs")
    with pytest.raises(engines.EngineError):
        engines.engine_class("no-such")
    assert not engines.available("no-such", "/")


def test_register_an_engine(tmp_path):
    class Silent(engines.Engine):
        name = "silent"

        @classmethod
        def available(cls, root):
            return True

        def _load(self, sr):
            pass

        def _reset(self):
            pass

        def _set_pitch(self, c):
            pass

        def _render_block(self, l, r, evs):
            pass

        def _active_voices(self):
            return 0

    engines.register("silent", Silent)
    try:
        assert engines.available("silent", tmp_path)
        e = engines.create({"engine": "silent"}, tmp_path)
        a = e.render(score.from_events([(1.0, "on", 60, 1)]), SR)
        assert a.shape[1] == 2 and a.shape[0] >= SR
    finally:
        engines.unregister("silent")


def test_sfizz_never_cuts_a_fresh_note_short(sfz_registry):
    # regression: with sfizz's default 8192-frame preload, about 1 fresh load in 20 stopped the first note
    # after 0.17 s (its background load not picked up in time); whole-sample preload makes it deterministic
    perf = score.from_events([(0.5, "on", 69, 100), (2.0, "off", 69, 64)])
    for _ in range(40):
        e = _engine(sfz_registry, "synth")
        try:
            a = e.render(perf, SR)
            assert rms_db(seg(a, 1.0, 1.1)) > -60
        finally:
            e.close()


class _Hanging(engines.Engine):
    """A test engine with a voice that never ends: each note-on writes a full-scale click at its exact frame, and
    from then on a steady tone at ``level_db`` dBFS sounds forever with one voice active (like the release voices
    sfizz 1.2.3 left hanging on K18 and Maestro CF3)."""
    name = "hanging"
    level_db = -95.0

    def _load(self, sr):
        self.sr = sr

    def _reset(self):
        self.on = False
        self.n = 0

    def _set_pitch(self, c):
        pass

    def _render_block(self, l, r, evs):
        n = len(l)
        t = (self.n + np.arange(n)) / self.sr
        tone = (10 ** (self.level_db / 20) * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
        on = np.zeros(n, bool)
        click = []
        for off, e, _ in evs:
            if e.kind == "on":
                on[off:] = True
                click.append(off)
        if self.on:
            on[:] = True
        l[:] = np.where(on, tone, 0.0)
        r[:] = l
        for off in click:
            l[off] = r[off] = 1.0
        self.on = self.on or bool(on[-1])
        self.n += n

    def _active_voices(self):
        return 1 if self.on else 0


def _hanging(level_db, gain_db):
    cls = type("H", (_Hanging,), {"level_db": level_db})
    return cls({"engine": "hanging", "gain_db": gain_db}, "/")


def test_tail_floor_ends_hanging_voices_and_fades():
    from piano_render.engines.base import SILENCE_HOLD_S, TAIL_FADE_S, tail_floor_dbfs
    assert tail_floor_dbfs(12.9) == pytest.approx(-82.9) and tail_floor_dbfs(None) == -70.0
    assert tail_floor_dbfs(-30) == -60.0 and tail_floor_dbfs(80) == -120.0
    e = _hanging(-95.0, 12.9)                          # the tone sits under the floor (-82.9 dBFS before gain)
    t0 = 1.0 + 123 / SR
    a = e.render(score.from_events([(t0, "on", 60, 100), (1.5, "off", 60, 64)]), SR)
    assert e.stats["tail_end"] == "floor" and e.stats["tail_floor_dbfs"] == -82.9
    assert SILENCE_HOLD_S - e.block / SR <= e.stats["tail_s"] < SILENCE_HOLD_S + 2 * e.block / SR   # not 30 s
    assert int(np.flatnonzero(np.abs(a[:, 0]) > 0.5)[0]) == round(t0 * SR)            # sample 0 = MIDI time 0
    nf = round(TAIL_FADE_S * SR)
    assert abs(a[-1, 0]) < 1e-9 and np.max(np.abs(a[-nf // 4:])) < 0.2 * 10 ** (-95 / 20)
    assert np.max(np.abs(a[-2 * nf:-nf])) > 0.9 * 10 ** (-95 / 20)                  # only the last 50 ms fade


def test_tail_above_the_floor_runs_to_the_cap_and_fades():
    e = _hanging(-75.0, 0.0)                           # -75 dBFS after gain 0 is under the -70 floor: it ends
    a = e.render(score.from_events([(0.2, "on", 60, 100), (0.5, "off", 60, 64)]), SR)
    assert e.stats["tail_end"] == "floor" and a.shape[0] / SR < 0.5 + 1.0
    e = _hanging(-75.0, 10.0)                          # the same tone 10 dB louder after gain (-65): it is heard
    a = e.render(score.from_events([(0.2, "on", 60, 100), (0.5, "off", 60, 64)]), SR)
    assert e.stats["tail_end"] == "cap"
    assert a.shape[0] / SR == pytest.approx(0.5 + TAIL_CAP_S, abs=2 * e.block / SR)
    assert abs(a[-1, 0]) < 1e-9 and np.max(np.abs(a[-SR // 2:-SR // 10])) > 0.9 * 10 ** (-75 / 20)


def test_tail_floor_keeps_a_real_decay_above_it(eng):
    # a real note released into its own release: it ends when its voice ends (or under the floor), never cut
    # while still above the floor
    e, key = eng
    a = e.render(score.from_events([(0.1, "cc", 64, 127), (0.3, "on", key, 100), (0.6, "off", key, 64)]), SR)
    from piano_render.engines.base import tail_floor_dbfs
    floor = 10 ** (tail_floor_dbfs(e.inst.get("gain_db")) / 20)
    if e.stats["tail_end"] == "floor":
        hold = int(0.5 * SR)
        assert np.max(np.abs(a[-hold:])) < floor
