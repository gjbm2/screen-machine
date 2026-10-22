import numpy as np
import pytest

from piano_render import master

SR = 48000


def sine(f=1000.0, amp=1.0, secs=5.0, phase=0.0, sr=SR):
    t = np.arange(int(secs * sr)) / sr
    return (amp * np.sin(2 * np.pi * f * t + phase)).astype(np.float32)


def test_k_weighting_matches_bs1770_at_48k():
    sos = master.k_weighting_sos(48000)
    # ITU-R BS.1770-4 Table 1 / Table 2
    assert sos[0, :3] == pytest.approx([1.53512485958697, -2.69169618940638, 1.19839281085285], abs=1e-9)
    assert sos[0, 4:] == pytest.approx([-1.69065929318241, 0.73248077421585], abs=1e-9)
    assert sos[1, 4:] == pytest.approx([-1.99004745483398, 0.99007225036621], abs=1e-9)


def test_full_scale_1k_sine_one_channel_reads_minus_3_lufs():
    # BS.1770: a 0 dBFS 1 kHz sine (RMS -3.01 dBFS) in one channel reads -3.01 LKFS
    x = sine()
    st = np.stack([x, np.zeros_like(x)], 1)
    assert master.integrated_loudness(st, SR) == pytest.approx(-3.01, abs=0.05)
    assert master.integrated_loudness(x, SR) == pytest.approx(-3.01, abs=0.05)        # mono input too
    # in both channels the powers add: 0.0 LKFS
    assert master.integrated_loudness(np.stack([x, x], 1), SR) == pytest.approx(0.0, abs=0.05)


def test_level_and_gating():
    x = sine(amp=10 ** (-20 / 20))                       # -20 dBFS peak in one channel -> -23.01 LUFS
    st = np.stack([x, np.zeros_like(x)], 1)
    assert master.integrated_loudness(st, SR) == pytest.approx(-23.01, abs=0.05)
    # 10 s of silence around it changes nothing (absolute gate), nor does a much quieter passage (relative gate)
    pad = np.zeros((10 * SR, 2), np.float32)
    quiet = np.stack([sine(amp=10 ** (-60 / 20), secs=5), np.zeros(5 * SR, np.float32)], 1)
    y = np.concatenate([pad, st, quiet, pad])
    # 47 full blocks of the tone plus 3 partial ones at each edge (1/4, 2/4, 3/4 of the tone: all pass the
    # relative gate); the -63 LUFS passage and the silence are gated out: mean = 50/53 of the tone's power
    assert master.integrated_loudness(y, SR) == pytest.approx(-23.01 + 10 * np.log10(50 / 53), abs=0.02)
    shorter_pads = np.concatenate([pad[: 4 * SR], st, pad[: 4 * SR]])
    assert master.integrated_loudness(shorter_pads, SR) == pytest.approx(-23.01 + 10 * np.log10(50 / 53), abs=0.02)
    assert master.integrated_loudness(np.zeros((SR, 2), np.float32), SR) == float("-inf")
    assert master.integrated_loudness(st[: SR // 4], SR) == float("-inf")          # shorter than one block


def test_true_peak_sees_intersample_peaks():
    # fs/4 sine at 45 degrees: every sample is +-0.7071, the waveform peaks at 1.0 between them
    x = sine(f=SR / 4, amp=1.0, secs=1.0, phase=np.pi / 4)
    assert master.sample_peak(x) == pytest.approx(0.7071, abs=1e-3)
    assert master.db(master.true_peak(x)) == pytest.approx(0.0, abs=0.2)
    # chunk edges and the skip-the-quiet-chunks shortcut don't change the answer
    assert master.true_peak(x, chunk=1000) == pytest.approx(master.true_peak(x), abs=1e-6)
    rng = np.random.default_rng(3)
    y = (rng.standard_normal(SR * 3) * 0.05).astype(np.float32)
    y[SR + 17: SR + 19] = [0.6, 0.6]                  # an inter-sample over between two equal samples
    full = master.true_peak(y, chunk=len(y) + 1)
    assert master.true_peak(y, chunk=4096) == pytest.approx(full, abs=1e-9) and full > 0.6


def test_process_applies_gain_when_there_is_room():
    x = np.stack([sine(amp=0.1), sine(amp=0.1)], 1)
    out, st = master.process(x, SR, gain_db=6.0)
    assert not st.limited and st.gain_db == pytest.approx(6.0)
    assert np.allclose(out, x * 10 ** (6 / 20), atol=1e-6)
    assert st.lufs == pytest.approx(st.lufs_in + 6.0, abs=1e-3)


def test_process_cuts_gain_to_keep_true_peak_under_minus_1():
    x = np.stack([sine(f=SR / 4, amp=0.9, secs=1.0, phase=np.pi / 4)] * 2, 1)
    out, st = master.process(x, SR, gain_db=12.0)
    assert st.limited
    assert st.true_peak_dbtp <= -1.0
    assert master.db(master.true_peak(out)) <= -1.0 + 1e-6
    # plain gain: the waveform is a scaled copy (no limiter, no delay)
    k = out[:, 0].max() / x[:, 0].max()
    assert np.allclose(out, x * k, atol=1e-6)


def test_write_flac_24bit(tmp_path):
    import soundfile as sf
    x = np.stack([sine(amp=0.5, secs=0.5), -sine(amp=0.5, secs=0.5)], 1)
    p = tmp_path / "o.flac"
    master.write_flac(str(p), x, SR)
    info = sf.info(str(p))
    assert (info.format, info.subtype, info.samplerate, info.channels) == ("FLAC", "PCM_24", 48000, 2)
    y, _ = sf.read(str(p), dtype="float32")
    assert np.max(np.abs(y - x)) < 1e-6
