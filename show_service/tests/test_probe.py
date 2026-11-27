"""The lights' timing probe (looks.probe, outputs Runner._probe_edge): a scheduled flash series for the camera
calibration, sent in the frame at each pulse edge."""
from __future__ import annotations

from show_service import fixtures, outputs
from show_service.tests.test_looks import DMX_CAPS, fx, look, out, rigview
from show_service.tests.test_looks_motion import engine

RGB = fixtures.BUILTIN_TYPES["generic-rgb"]


def _engine(box: dict):
    rig = rigview([fx("p1", RGB, "dmx", address=1), fx("p2", RGB, "dmx", address=10)], [RGB], [out("dmx", "artnet")])
    box["look"] = look()
    return engine(rig, box)


def test_a_probe_lights_its_pulses_and_holds_the_light_dark_around_them():
    box: dict = {}
    e = _engine(box)
    assert e.probe("p1", [(20.0, 0.2), (21.0, 0.2)], dark_s=1.0)
    inten = lambda t: e.evaluate("dmx", t, DMX_CAPS)["fixtures"]["p1"]["intensity"]   # noqa: E731
    assert inten(18.5) > 0.5                                   # before the dark lead: the look as it was
    assert inten(19.5) == 0.0 and inten(20.1) == 1.0 and inten(20.3) == 0.0 and inten(21.05) == 1.0
    assert inten(22.5) > 0.5                                   # after: the look again
    assert e.evaluate("dmx", 20.1, DMX_CAPS)["fixtures"]["p2"]["intensity"] > 0.5   # only that light
    assert e.next_probe_edge(19.0) == 20.0 and e.next_probe_edge(20.05) == 20.2 and e.next_probe_edge(21.3) is None
    e.probe("p1", [])
    assert e.next_probe_edge(0.0) is None


class _Driver:
    caps = DMX_CAPS

    def __init__(self, t):
        self.t = t

    def plan(self, t_last, now, active):
        return self.t


class _Eng:
    def __init__(self, edge):
        self.edge = edge

    def next_probe_edge(self, after):
        return self.edge if self.edge is not None and self.edge >= after else None


def test_the_runner_sends_a_frame_at_a_probe_edge_before_its_planned_one():
    r = outputs._Runner.__new__(outputs._Runner)
    r.hit, r.driver, r.engine = False, _Driver(11.0), _Eng(10.4)
    r._plan_hit = r._on_hit = None
    assert r._plan(10.0, 10.1, False) == 10.4                 # the edge comes first
    r.engine = _Eng(12.0)
    assert r._plan(10.0, 10.1, False) == 11.0                 # the planned frame comes first
    r.engine = _Eng(None)
    assert r._plan(10.0, 10.1, False) == 11.0


def test_an_output_with_no_lead_of_its_own_leads_by_its_lights_measured_median():
    from show_service.artnet_lan import output_lead
    out = {"id": "dmx-a", "latency_key": "dmx-a"}
    meas = lambda ms: {"latency": {"rise_ms": ms, "source": {"rise": "latency.json outputs.x.intensity"}}}   # noqa: E731
    unset = {"latency": {"rise_ms": 0.0, "source": {"rise": "unset"}}}
    assert output_lead(out, [meas(30.0), meas(50.0), meas(40.0), unset]) == (40.0, "latency.json: the median of 3 light(s)")
    assert output_lead(out, [unset]) == (0.0, "unset")
    keyed = {"latency": {"rise_ms": 25.0, "source": {"rise": "latency.json outputs.dmx-a.intensity"}}}
    assert output_lead(out, [keyed, meas(80.0)])[0] == 25.0          # the output's own measurement first
