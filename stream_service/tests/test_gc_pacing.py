"""Paced GC (renderer.Engine._gc_in_slack): collections only in the slack before a frame's deadline, the oldest due
generation first, gen2 regardless after GC_GEN2_MAX_S (7c, 21 Oct)."""
import gc

from stream_service import renderer as R


class _Eng:
    _gc_in_slack = R.Engine._gc_in_slack

    def __init__(self):
        self.gc_max_ms = [0.0, 0.0, 0.0]
        self.gc_runs = [0, 0, 0]


def _run(monkeypatch, now, deadline, last):
    calls = []
    monkeypatch.setattr(R, "pace_clock", lambda: now)
    monkeypatch.setattr(R.gc, "collect", lambda g=2: calls.append(g))
    _Eng()._gc_in_slack(deadline, last)
    return calls


def test_collects_only_with_slack(monkeypatch):
    assert _run(monkeypatch, 100.0, 100.001, [99.0, 99.9, 99.0]) == []          # 1 ms slack: nothing
    assert _run(monkeypatch, 100.0, 100.003, [99.0, 99.9, 99.0]) == [0]         # 3 ms: gen0 only (gen1 not due)


def test_oldest_due_generation_first(monkeypatch):
    assert _run(monkeypatch, 100.0, 100.005, [99.0, 99.0, 99.0]) == [1]         # gen1 due and fits; gen2 not due
    last = [70.0, 70.0, 70.0]
    assert _run(monkeypatch, 100.0, 100.010, last) == [2]                       # gen2 due (20 s) and fits
    assert last == [100.0, 100.0, 100.0]                                        # a gen2 counts for the younger ones


def test_gen2_runs_regardless_after_the_max(monkeypatch):
    assert _run(monkeypatch, 200.0, 200.0, [199.0, 199.0, 70.0]) == [2]          # no slack, but 130 s since gen2


def test_real_collect_is_harmless():
    e = _Eng()
    last = [0.0, 0.0, 0.0]
    e._gc_in_slack(R.pace_clock() + 1.0, last)
    assert gc.isenabled() and e.gc_max_ms[2] >= 0.0


def test_gen0_regardless_when_garbage_piles_up(monkeypatch):
    monkeypatch.setattr(R.gc, "get_count", lambda: (R.GC_GEN0_PILE * R.gc.get_threshold()[0], 0, 0))
    assert _run(monkeypatch, 100.0, 100.0, [99.99, 99.99, 99.99]) == [0]       # no slack, gen0 not due: still runs
