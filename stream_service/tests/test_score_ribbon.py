"""Does-it-crash test for the score ribbon (plus the few layout invariants the scene's continuity rests on).

  pytest stream_service/tests/test_score_ribbon.py                 (1, the layout: no GPU)
  SM_GPU_TESTS=1 [SM_TEST_GPU=1] pytest stream_service/tests/test_score_ribbon.py   (and 2, the scene)

1. layout (no GPU): a real transcription (a test pattern through a ScoreBuilder on its beat clock): every written
   note has its head, piece 0 keyed by the note; ties join consecutive pieces of one note; beams stay within a beat;
   provisional bars laid out from the same events give the written bars' keys and x.
2. scene (GPU, P4000): the scene with an in-process room hearing a test piano through a SoundView, 1080p, in several
   configurations (looks, 1-3 systems, render_scale 0.5 and 1), a tape rebase mid-run: no exception, the frame not
   blank, notes born and drawn; the rebase leaves the picture where it was.
"""
import math
import os
import time

import numpy as np
import pytest

from stream_service.piano_replay import CC, NOTE_OFF, NOTE_ON, TestPiano, phrase
from stream_service.score.quantize import ScoreBuilder
from stream_service.score import ribbon_layout as RL

GPU = int(os.environ.get("SM_TEST_GPU", "1"))


def _transcribe(name="arpeggios", secs=14.0):
    ev = sorted(e for e in phrase(name) if e[0] < secs)
    b = ScoreBuilder()
    bars, t, i = [], 0.0, 0
    while t < secs + 6.0:
        while i < len(ev) and ev[i][0] <= t:
            tt, st, a1, a2 = ev[i]
            if st == NOTE_ON and a2 > 0:
                b.note_on(a1, a2 / 127.0, tt)
            elif st in (NOTE_ON, NOTE_OFF):
                b.note_off(a1, tt)
            elif st == CC and a1 == 64:
                b.pedal(a2 / 127.0, tt)
            i += 1
        bars += b.poll(t)
        t += 0.02
    return [x for x in bars if not x.marker]


def test_layout_invariants():
    for name in ("arpeggios", "scale", "chords", "sostenuto"):
        bars = _transcribe(name)
        assert bars, name
        pl = RL.PhraseLayout(1)
        q0 = min(bar.pos * bar.units + e.start for bar in bars[:1] for st in bar.staves for e in st if e.pitches)
        written_ids = set()
        for bar in bars:
            pl.write(bar, 0, q0)
            for st in bar.staves:
                for e in st:
                    written_ids.update(e.ids)
        out = pl.targets()
        heads0 = {k[2] for k in out.heads if k[3] == 0}
        assert written_ids <= heads0, (name, written_ids - heads0)          # one object per note
        for key, (k0, k1, _up) in out.ties.items():                          # ties join consecutive pieces
            assert k0[2] == k1[2] and k1[3] == k0[3] + 1, key
            assert k0 in out.heads and k1 in out.heads
        for key, (stems, level, let) in out.beams.items():                   # a beam's stems are real stems
            assert all(s in out.stems for s in stems), key
        xs = [v[0] for k, v in out.heads.items()]
        assert all(math.isfinite(x) for x in xs)
        # the same events laid out provisionally (spaced with their rests, not drawing them): same heads, same x
        pl2 = RL.PhraseLayout(1)
        pl2.provisional([(bar.pos, bar.staves) for bar in bars], None, 0, q0, bars[0].units, bars[0].unit,
                        bars[0].beats)
        for k, v in pl2.prov.heads.items():
            assert k in out.heads, k
            assert abs(v[0] - out.heads[k][0]) < 1e-6, (k, v[0], out.heads[k][0])
        assert not pl2.prov.rests


def _run_scene(params, frames=420, w=1920, h=1080, rebase_at=None, piano="medley"):
    cp = pytest.importorskip("cupy")
    from stream_service.gpu import RenderContext
    from stream_service.sound_view import SoundView
    from stream_service.plugins.sources.score_ribbon import ScoreRibbon

    class ScoreRibbonLocal(ScoreRibbon):              # an in-process room fed from the scene's own sound frame
        ROOM_LOCAL = True

    ctx = RenderContext(GPU, w, h, 60, prealloc_buffers=0)
    with ctx.device, ctx.stream:
        return _drive(ctx, cp, SoundView, ScoreRibbonLocal, params, frames, w, h, rebase_at, piano)


def _drive(ctx, cp, SoundView, ScoreRibbonLocal, params, frames, w, h, rebase_at, piano):
    out = cp.zeros((h, w, 4), cp.uint8)
    s = ScoreRibbonLocal()
    snd = type("Snd", (), {"frame": None})()
    s.sound = snd
    view = SoundView()
    t0 = time.monotonic() * 1000.0
    ev = [e for e in phrase(piano)]
    test_piano = TestPiano(ev, start_ms=t0 + 300.0)
    snd.frame = view.update(t0)
    s.setup(ctx, ScoreRibbonLocal.validate(params))
    before = None
    diffs = None
    for i in range(frames):
        now = t0 + i * 1000.0 / 60.0                 # the test runs on its own clock (no real-time pacing)
        for m in test_piano.poll(now):
            view.on_message(m)
        snd.frame = view.update(now)
        if rebase_at is not None and i == rebase_at:
            cp.cuda.Device().synchronize()
            s.render(i / 60.0, 0.0, out)             # dt 0: the same moment drawn twice, around the rebase
            before = cp.asnumpy(out)
            s.stage.rebase_x(12345.0)
            s.render(i / 60.0, 0.0, out)
            diffs = np.abs(cp.asnumpy(out).astype(int) - before.astype(int))
            continue
        s.render(i / 60.0, 0.0 if i == 0 else 1.0 / 60.0, out)
    cp.cuda.Device().synchronize()
    img = cp.asnumpy(out)[..., :3]
    stats = dict(s.stats)
    heads = len(s.stage.T["heads"].idx)
    s.close()
    return img, stats, heads, diffs


@pytest.mark.gpu
def test_scene_runs(tmp_path):
    os.environ.setdefault("CUDA_DEVICE_ORDER", "PCI_BUS_ID")
    from PIL import Image
    configs = [
        {"look": "paper"},
        {"look": "night", "rows": 3},
        {"look": "night", "rows": 1, "render_scale": 0.5, "staff_size": 1.3},
        {"look": "paper", "pace": 18.0, "flourish": 0.0, "playhead": 1.0, "energy": 1.0, "pulse": 1.0},
    ]
    for k, params in enumerate(configs):
        img, stats, heads, diffs = _run_scene(params, rebase_at=300 if k == 0 else None)
        Image.fromarray(img).save(tmp_path / f"crash_{k}.png")
        bg = np.median(img.reshape(-1, 3), axis=0)
        ink = (np.abs(img.astype(int) - bg).sum(2) > 60).mean()
        print(params, stats, "heads", heads, "ink share", round(float(ink), 4))
        assert stats["births"] > 10, stats
        assert heads > 0
        assert 0.002 < ink < 0.5, ink                 # not blank, not flooded
        if diffs is not None:
            print("rebase: max px diff", int(diffs.max()), "share > 8:", float((diffs > 8).mean()))
            assert (diffs > 8).mean() < 1e-3


