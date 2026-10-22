"""hue_cal.py offline: the ArtDmx frames, the clock-offset estimator, analyse() on a simulated sensor with a planted
latency, and the latency.json writer. No network beyond loopback, no lamps, no room_service.

  flock /tmp/claude-1000/sm-build.lock nice -n 19 .venv/bin/python -m pytest -q -p no:cacheprovider \
      tools/room/latency_cal/test_hue_cal.py
"""
import bisect
import json
import random
import socket
import time

import pytest

import calibrate as cal
import hue_cal
from room_service.dmx.artnet import parse_artdmx
from room_service.dmx.patch import parse_patch
from show_service.artnet_out import parse_layout

PATCH = {   # config/room/patch.yaml's shape: two screens, then four Hue lamps at 33/37/41/45
    "artnet": {"bind": "0.0.0.0:6454", "universe": 0},
    "profiles": {
        "screen": {"channels": [{"attr": a, "field": f} for a, f in (
            ("RED", "r"), ("GREEN", "g"), ("BLUE", "b"), ("DIMMER", "pulse"), ("ACTIVITY", "global.activity"))]
            + [{"attr": "INTENSITY", "field": "level", "default": 255}]},
        "hue": {"channels": [{"attr": "MASTER DIMMER", "field": "dimmer", "default": 255},
                             {"attr": "RED", "field": "r"}, {"attr": "GREEN", "field": "g"},
                             {"attr": "BLUE", "field": "b"}]}},
    "devices": {"screen.north": {"profile": "screen", "address": 1},
                "screen.south": {"profile": "screen", "address": 17},
                "hue.ne": {"profile": "hue", "address": 33}, "hue.nw": {"profile": "hue", "address": 37},
                "hue.se": {"profile": "hue", "address": 41}, "hue.sw": {"profile": "hue", "address": 45}},
}
LAYOUT = parse_layout(PATCH)
LAMPS = ["hue.ne", "hue.nw", "hue.se", "hue.sw"]


# ---------------------------------------------------------------- frames

def test_universe_white_dark_and_nothing_else():
    data = hue_cal.universe(LAYOUT, {"hue.ne": 1.0, "hue.se": 0.0})
    assert len(data) == 512
    assert list(data[32:36]) == [255, 255, 255, 255]          # hue.ne white
    assert list(data[36:40]) == [0, 0, 0, 0]                  # hue.nw: not in this run
    assert list(data[40:44]) == [255, 0, 0, 0]                # hue.se dark
    assert not any(data[:32]) and not any(data[44:])          # screens and the rest of the universe: 0


def test_artdmx_packet_bytes_and_room_decodes_them():
    """The sender's packet, byte for byte, and what room_service's own receiver and patch make of it."""
    sender = hue_cal.ArtNetSender(LAYOUT, {lamp: 1.0 for lamp in LAMPS}, target=("127.0.0.1", 9))
    try:
        pkt = bytes(sender.frame.advance())
    finally:
        sender.stop()
    assert len(pkt) == 18 + 512
    assert pkt[:8] == b"Art-Net\x00"
    assert pkt[8:10] == b"\x00\x50"                           # OpOutput 0x5000, little-endian
    assert pkt[10:12] == b"\x00\x0e"                          # protocol 14
    assert pkt[12] == 1 and pkt[13] == 0                      # sequence 1 (never 0), physical 0
    assert pkt[14:16] == b"\x00\x00"                          # SubUni, Net: universe 0
    assert pkt[16:18] == b"\x02\x00"                          # length 512, big-endian
    assert list(pkt[18 + 32:18 + 48]) == [255] * 16

    port_address, seq, data = parse_artdmx(pkt)
    assert (port_address, seq, len(data)) == (0, 1, 512)
    fixtures, _ = parse_patch(PATCH).decode(data)
    assert all(fixtures[lamp]["rgb"] == [1.0, 1.0, 1.0] for lamp in LAMPS)
    dark, _ = parse_patch(PATCH).decode(hue_cal.universe(LAYOUT, {lamp: 0.0 for lamp in LAMPS}))
    assert all(dark[lamp]["rgb"] == [0.0, 0.0, 0.0] for lamp in LAMPS)


def test_sender_streams_and_stamps_a_flip_when_it_leaves():
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0))
    rx.settimeout(1.0)
    sender = hue_cal.ArtNetSender(LAYOUT, {"hue.se": 0.0}, target=rx.getsockname(), hz=40.0)
    sender.start()
    try:
        pkt, addr = rx.recvfrom(1024)
        assert pkt[18 + 40:18 + 44] == bytes([255, 0, 0, 0])
        assert addr[0] == sender.source                       # 127.0.0.2 where the host allows it
        t_ns = sender.set_levels({"hue.se": 1.0})
        while True:
            pkt, _ = rx.recvfrom(1024)
            t_rx = time.time_ns()
            if pkt[18 + 40:18 + 44] == bytes([255] * 4):
                break
        assert 0 <= t_rx - t_ns < 50e6                        # stamped just before it left
        n0 = sender.frames
        time.sleep(0.5)
        assert 15 <= sender.frames - n0 <= 25                 # ~40 Hz
    finally:
        sender.stop()
        rx.close()


def test_foreign_reason():
    class S:
        source, recent = "127.0.0.2", [hue_cal.universe(LAYOUT, {"hue.se": 0.0})]
    ours = list(S.recent[0][:48])
    assert hue_cal.foreign_reason({"input": {"last_from": "127.0.0.2", "pps": 40}, "channels": ours}, S) is None
    assert "127.0.0.1" in hue_cal.foreign_reason({"input": {"last_from": "127.0.0.1", "pps": 40}, "channels": ours}, S)
    assert "packets/s" in hue_cal.foreign_reason({"input": {"last_from": "127.0.0.2", "pps": 83}, "channels": ours}, S)
    amber = ours[:]
    amber[26] = 255                                           # a screen's INTENSITY: not ours
    assert hue_cal.foreign_reason({"input": {"last_from": "127.0.0.2", "pps": 40}, "channels": amber}, S)


# ---------------------------------------------------------------- clock

def test_clock_offset_from_min_rtt_probes():
    rng = random.Random(3)
    true_ns = -57_300_000                                     # box − WSL
    probes, t = [], 1_792_000_000_000_000_000
    for _ in range(200):
        d1 = 400_000 + int(rng.expovariate(1 / 300_000))      # one-way 0.4 ms plus queueing, each way
        d2 = 400_000 + int(rng.expovariate(1 / 300_000))
        probes.append((t, t + d1 + true_ns, t + d1 + d2))
        t += 5_000_000
    r = hue_cal.offset_from_probes(probes)
    min_rtt = min(t1 - t0 for t0, _, t1 in probes) / 1e6
    assert r["offset_uncertainty_ms"] == round(min_rtt / 2, 3)
    assert abs(r["offset_box_minus_wsl_ms"] - true_ns / 1e6) <= r["offset_uncertainty_ms"]
    assert abs(r["offset_box_minus_wsl_ms"] - true_ns / 1e6) < 0.15
    assert r["perf_minus_date_ms"] == 0.0


def test_clock_needs_enough_probes_and_flags_a_step():
    with pytest.raises(RuntimeError):
        hue_cal.offset_from_probes([(0, 0, 1_000_000)] * 3)
    a = {"offset_box_minus_wsl_ms": -50.0, "offset_uncertainty_ms": 1.1, "perf_minus_date_ms": 0.0}
    assert hue_cal.combine_clock(a, {**a, "offset_box_minus_wsl_ms": -50.8})["stable"] is True
    c = hue_cal.combine_clock(a, {**a, "offset_box_minus_wsl_ms": -55.0})
    assert c["stable"] is False and c["offset_box_minus_wsl_ms"] == -52.5


# ---------------------------------------------------------------- analysis

def simulate_sensor(steps, meas_ms=100.0, report_ms=10.0, base=1.0, lux=20.0, noise=0.02, seed=1):
    """Yocto-like timed reports on the box clock for light steps [(t_box_ms, level 0..1)]: a measurement every
    meas_ms integrating the preceding meas_ms, reported every report_ms as {t_ns, v, lo, hi} over the report period."""
    rng = random.Random(seed)
    st = [t for t, _ in steps]

    def level(t):
        k = bisect.bisect_right(st, t) - 1
        return steps[k][1] if k >= 0 else 0.0

    def mean(a, b):
        pts = [a] + [t for t in st if a < t < b] + [b]
        return sum(level((p + q) / 2) * (q - p) for p, q in zip(pts, pts[1:])) / (b - a)

    t0, t_end = st[0] - 4000.0, st[-1] + 3000.0
    mt, mv = [], []
    t = t0 + rng.uniform(0, meas_ms)
    while t < t_end:
        mt.append(t)
        mv.append(base + lux * mean(t - meas_ms, t) + rng.gauss(0, noise))
        t += meas_ms
    lines = [json.dumps({"kind": "header", "t_ns": int(t0 * 1e6)})]
    r = mt[1] + rng.uniform(0, report_ms)
    while r < t_end:
        a = r - report_ms
        k = bisect.bisect_right(mt, a) - 1
        segs, start, cur = [], a, mv[k]
        for j in range(k + 1, len(mt)):
            if mt[j] > r:
                break
            segs.append((mt[j] - start, cur))
            start, cur = mt[j], mv[j]
        segs.append((r - start, cur))
        vals = [v for _, v in segs]
        lines.append(json.dumps({"t_ns": int(r * 1e6), "v": sum(w * v for w, v in segs) / report_ms,
                                 "lo": min(vals), "hi": max(vals)}))
        r += report_ms
    return lines


def test_analyse_recovers_a_planted_300ms_latency_from_artnet_send_times():
    rng = random.Random(7)
    offset_ms, latency_ms = -57.3, 300.0                      # box − WSL; Art-Net send -> light
    at, pulses, steps = 1_792_000_000_000_000_000, [], []
    for i in range(40):
        at += int(rng.uniform(1.3, 1.7) * 1e9)
        white = i % 2 == 0
        pulses.append({"i": i, "c": hue_cal.WHITE if white else hue_cal.BLACK, "lamps": LAMPS,
                       "at_ns": at, "t_cmd_ms": at / 1e6 - 0.1})
        steps.append((at / 1e6 + offset_ms + latency_ms, 1.0 if white else 0.0))
    samples = cal.parse_samples(simulate_sensor(steps))
    sync = {"offset_box_minus_wsl_ms": offset_ms, "offset_uncertainty_ms": 1.0, "perf_minus_date_ms": 0.0}
    rows, s = cal.analyse(samples, pulses, sync)
    assert s["detected"] == 40
    assert s["cmd_to_apply_ms"]["mean"] == pytest.approx(0.1, abs=0.05)    # at_ns is taken as WSL time zero
    for d in ("rise", "fall"):
        assert s["apply_to_photon_ms"][d]["t50"]["mean"] == pytest.approx(latency_ms, abs=10)
    t50 = hue_cal.edge_t50(s)
    assert t50["rise"]["n"] == t50["fall"]["n"] == 20


# ---------------------------------------------------------------- latency.json

def test_latency_json_writer_changes_only_its_keys(tmp_path):
    path = tmp_path / "latency.json"
    path.write_text(json.dumps({"version": 2, "screens": {"south": {"x": 1}}, "sources": {"pc22": {"a": 1}},
                                "outputs": {"tv-south": {"brightness": {"trim_ms": 3.0}},
                                            "hue": {"intensity": {"trim_ms": 12.0}, "colour": {"keep": True}}}}))
    summary = {"apply_to_photon_ms": {d: {f"t{f}": {"n": 10, "mean": 100.0 + f + (d == "fall") * 50, "sd": 5.0}
                                          for f in (10, 50, 90)} for d in ("rise", "fall")},
               "standard_error_ms": 1.6}
    result = {"summary": summary, "method": hue_cal.METHOD, "measured_at": "2026-10-18T21:00:00+00:00",
              "lamps": LAMPS, "latency_keys": ["hue"]}
    written = hue_cal.update_latency_json(result, str(tmp_path / "runs" / "x"), path=str(path))
    out = json.loads(path.read_text())
    assert written == ["outputs.hue.intensity"]
    assert out["screens"] == {"south": {"x": 1}} and out["sources"] == {"pc22": {"a": 1}}
    assert out["outputs"]["tv-south"] == {"brightness": {"trim_ms": 3.0}}
    assert out["outputs"]["hue"]["colour"] == {"keep": True}
    e = out["outputs"]["hue"]["intensity"]
    assert e["trim_ms"] == 12.0 and e["zero"] == hue_cal.ZERO and e["se_ms"] == 1.6
    assert e["rise"] == {"t10": 110.0, "t50": 150.0, "t90": 190.0, "n": 10}
    assert e["fall"]["t50"] == 200.0
    assert not (tmp_path / "latency.json.tmp").exists()


def test_latency_keys(tmp_path):
    fx = tmp_path / "fixtures.yaml"
    fx.write_text("outputs:\n  hue: {driver: hue}\nfixtures:\n"
                  + "".join(f"  {lamp}: {{output: hue}}\n" for lamp in LAMPS))
    assert hue_cal.latency_keys(LAMPS, LAMPS, fixtures_file=str(fx)) == ["hue"]
    assert hue_cal.latency_keys(["hue.se", "hue.sw"], LAMPS, fixtures_file=str(fx)) == ["hue.se", "hue.sw"]
    fx.write_text(fx.read_text().replace("{driver: hue}", "{driver: hue, latency_key: lamps}"))
    assert hue_cal.latency_keys(LAMPS, LAMPS, fixtures_file=str(fx)) == ["lamps"]
