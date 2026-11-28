"""The motion stage's mathematics (show_service/motion.py): what is promised of every path sent to a moving light."""
import math
import random

import pytest

from show_service import motion as mo

DT = 1.0 / 44.0
V = (180.0, 120.0)
A, J = 0.12, 0.06


def drive(targets, v=V, a=A, j=J, at=(0.0, 0.0), dt=DT):
    """The commands sent for a list of per-frame targets."""
    sm = mo.Smoother(at)
    return [sm.step(dt, tg, v, a, j) for tg in targets]


def derivs(xs, dt=DT):
    v = [(b - a) / dt for a, b in zip(xs, xs[1:])]
    acc = [(b - a) / dt for a, b in zip(v, v[1:])]
    jerk = [(b - a) / dt for a, b in zip(acc, acc[1:])]
    return v, acc, jerk


def test_a_jump_eases_away_and_in_and_lands_exactly_when_promised():
    tgt = (90.0, -30.0)
    out = drive([tgt] * 200)
    took = mo.travel_s(90.0, -30.0, V, A, J)
    assert took == pytest.approx(90.0 / 180.0 + A + J)
    arrive = next(k for k, p in enumerate(out) if p == tgt) + 1
    assert arrive * DT == pytest.approx(took, abs=2 * DT)
    assert out[-1] == tgt                                         # exactly, and it stays
    pans = [0.0] + [p[0] for p in out]
    assert all(b >= a - 1e-9 for a, b in zip(pans, pans[1:])) and max(pans) <= 90.0   # never back, never past it
    v, acc, jerk = derivs(pans)
    assert max(map(abs, v)) <= 180.0 + 1e-6
    assert max(map(abs, acc)) <= 180.0 / A * 1.02
    assert max(map(abs, jerk)) <= 180.0 / (A * J) * 1.02
    assert abs(v[0]) < 0.1 * 180.0 and abs(v[arrive - 2]) < 0.1 * 180.0   # it starts and ends gently


def test_both_axes_leave_and_arrive_together_on_one_line():
    out = drive([(120.0, 20.0)] * 200)
    for p, t in out:
        assert t == pytest.approx(p * 20.0 / 120.0, abs=1e-6)     # a straight line in (pan, tilt): no dog-leg
    assert out[-1] == (120.0, 20.0)


def test_whatever_is_asked_the_bounds_hold():
    rnd = random.Random(7)
    tg, targets = (0.0, 0.0), []
    for k in range(3000):
        if rnd.random() < 0.03:                                  # jumps, far and near
            tg = (rnd.uniform(-270, 270), rnd.uniform(-110, 110))
        elif rnd.random() < 0.5:                                 # and a target that never stops fidgeting
            tg = (tg[0] + rnd.uniform(-9, 9), tg[1] + rnd.uniform(-6, 6))
        targets.append(tg)
    out = drive(targets)
    for i, vmax in ((0, 180.0), (1, 120.0)):
        v, acc, jerk = derivs([p[i] for p in out])
        assert max(map(abs, v)) <= vmax + 1e-6
        assert max(map(abs, acc)) <= 2 * vmax / A * 1.02          # a full-speed turn-about: twice a start's
        assert max(map(abs, jerk)) <= 4 * vmax / (A * J) * 1.02
    lo = [min(t[i] for t in targets + [(0.0, 0.0)]) for i in (0, 1)]
    hi = [max(t[i] for t in targets + [(0.0, 0.0)]) for i in (0, 1)]
    assert all(lo[i] - 1e-9 <= p[i] <= hi[i] + 1e-9 for p in out for i in (0, 1))   # never outside what was asked


def test_no_easing_is_the_plain_speed_limit():
    out = drive([(90.0, 0.0)] * 60, a=0.0, j=0.0)
    assert out[0][0] == pytest.approx(180.0 * DT)
    assert next(k for k, p in enumerate(out) if p[0] == 90.0) + 1 == math.ceil(0.5 / DT)
    assert mo.travel_s(90.0, 0.0, V, 0.0, 0.0) == 0.5 and mo.travel_s(0.0, 0.0, V, A, J) == 0.0


def test_a_pattern_runs_a_known_lag_behind_and_keeps_its_size():
    per, amp = 2.0, 20.0                                         # a one-bar sweep at 120 bpm
    n = int(8 * per / DT)
    path = lambda t: (amp * math.sin(2 * math.pi * t / per), 0.0)   # noqa: E731
    out = drive([path((k + 1) * DT) for k in range(n)])
    tail = [p[0] for p in out[n // 2:]]
    gain = (max(tail) - min(tail)) / (2 * amp)
    pts = [path(per * k / 64) for k in range(65)]
    assert gain == pytest.approx(mo.kept(pts, per, A, J), abs=0.01) and gain > 0.97
    lag = mo.lag_s(A, J)                                         # drawn that much ahead, it is on the beat
    ahead = drive([path((k + 1) * DT + lag) for k in range(n)])
    err = max(abs(p[0] - gain * path((k + 1) * DT)[0]) for k, p in enumerate(ahead) if k > n // 2)
    assert err < 0.02 * amp


def test_kept_says_when_a_pattern_is_too_quick_to_keep_its_shape():
    circle = lambda per: [(10 * math.cos(2 * math.pi * k / 64), 10 * math.sin(2 * math.pi * k / 64))   # noqa: E731
                          for k in range(65)]
    assert mo.kept(circle(4.0), 4.0, A, J) > 0.99
    assert mo.kept(circle(0.25), 0.25, A, J) == pytest.approx(0.60, abs=0.02)   # four turns a second: drawn at 60%
    assert mo.kept(circle(0.25), 0.25, 0.0, 0.0) == 1.0
    wander = [(k * 1.0, math.sin(k)) for k in range(65)]         # an open path isn't judged
    assert mo.kept(wander, 1.0, A, J) == 1.0
    tri = [(1.0 - 2.0 * min(w, 1 - w) * 2, 0.0) for w in (k / 64 for k in range(65))]
    assert 0.8 < mo.kept(tri, 1.0, A, J) < 1.0                   # a sweep's hard turns come out rounded, a bit short
