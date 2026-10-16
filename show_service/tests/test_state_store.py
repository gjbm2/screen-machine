"""state.json persistence (show_service/state_store.py) is shared plumbing: real tests on tmp paths (never the real
state dir)."""

from __future__ import annotations

import json
import os
import stat
import threading

import pytest

from show_service import state_store as ss
from show_service.state_store import StateStore


class Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def store(tmp_path, **kw) -> StateStore:
    return StateStore(tmp_path / "show" / "state.json", version="9.9.9", **kw)


DIRECTOR = {"target": {"vibe": "party", "listening": True, "overrides": {"level": [0.6, 1.0]}},
            "target_source": "mode", "target_set_at": 1_760_000_000.5, "mode": "party",
            "nudges": {"steps": -2, "palette": "ember", "moods": ["mellow"], "keep_until": 1_760_000_900.0,
                       "house": False, "low": True}}
LISTENER = {"on": True, "set_at": 1_760_000_001.0}
AUTOPILOT = {"entered": {"mode": "piano", "previous": "gallery", "rule": "piano/enter_when", "at": 1.0,
                         "transition_id": "t1", "by": "auto"},
             "hold": None, "pause": None, "backoff_until": None,
             "suppressed": [{"mode": "party", "rule": "party/enter_when", "since": 1.0, "until": 2.0}],
             "switches": [1.0, 2.5], "last": None}


# -- round trip and shape ----------------------------------------------------------------------------------------------
def test_missing_file_is_empty_and_no_error(tmp_path):
    s = store(tmp_path)
    assert s.load() == {}
    assert s.error is None and s.status()["saved_age_s"] is None


def test_round_trip_keeps_every_section(tmp_path):
    wall = Clock(1_760_000_100.0)
    s = store(tmp_path, wall=wall)
    assert s.save({"director": DIRECTOR, "listener": LISTENER, "autopilot": AUTOPILOT, "sources": None})
    raw = json.loads(s.path.read_text())
    assert raw["v"] == 1 and raw["saved_at"] == 1_760_000_100.0 and raw["version"] == "9.9.9"
    assert "sources" not in raw                        # a None section is left out (tests stop on restart)
    back = store(tmp_path, wall=wall).load()
    assert back == {"director": DIRECTOR, "listener": LISTENER, "autopilot": AUTOPILOT}


def test_atomic_write_leaves_no_tmp_and_private_modes(tmp_path):
    s = store(tmp_path)
    assert s.save({"listener": LISTENER})
    names = os.listdir(s.path.parent)
    assert names == ["state.json"]                     # the .tmp was renamed into place
    assert stat.S_IMODE(os.stat(s.path.parent).st_mode) & 0o077 == 0     # the directory: 0700 (umask can only narrow)
    assert stat.S_IMODE(os.stat(s.path).st_mode) & 0o077 == 0


def test_save_replaces_atomically_old_or_new_never_half(tmp_path):
    """A reader racing the writer always sees a whole file (os.replace)."""
    s = store(tmp_path)
    s.save({"listener": {"on": False}})
    stop, seen, bad = threading.Event(), set(), []

    def reader():
        while not stop.is_set():
            try:
                seen.add(json.loads(s.path.read_text())["listener"]["on"])
            except FileNotFoundError:
                bad.append("missing")
            except ValueError:
                bad.append("torn")

    t = threading.Thread(target=reader)
    t.start()
    for i in range(200):
        s.save({"listener": {"on": bool(i % 2)}, "pad": {"x": "y" * 5000}})
    stop.set()
    t.join()
    assert not bad and seen <= {True, False}


def test_nan_and_tuples_are_written_as_strict_json(tmp_path):
    s = store(tmp_path)
    assert s.save({"director": {"nudges": {"steps": 1}, "x": float("nan"), "y": (1, 2)}})
    raw = json.loads(s.path.read_text())          # strict json.loads would reject NaN
    assert raw["director"]["x"] is None and raw["director"]["y"] == [1, 2]


# -- bad files ----------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("content", [b'{"v": 1, "director": {"tar', b"\xff\xfe", b"[1, 2]", b'{"v": 7}', b""])
def test_torn_or_invalid_file_is_moved_aside_and_starts_cold(tmp_path, content):
    wall = Clock(1_760_000_123.9)
    s = store(tmp_path, wall=wall)
    s.path.parent.mkdir(parents=True)
    s.path.write_bytes(content)
    assert s.load() == {}
    bad = s.path.with_name("state.json.bad-1760000123")
    assert bad.exists() and bad.read_bytes() == content and not s.path.exists()
    assert s.error and s.error.startswith("Started cold") and "state.json.bad-1760000123" in s.error
    assert s.status()["bad_file"] == str(bad)
    # the next good write clears nothing of the load story, but works
    assert s.save({"listener": LISTENER}) and s.error.startswith("Started cold")


def test_second_bad_file_in_the_same_second_gets_a_suffix(tmp_path):
    wall = Clock(5.0)
    for _ in range(2):
        s = store(tmp_path, wall=wall)
        s.path.parent.mkdir(parents=True, exist_ok=True)
        s.path.write_text("nope")
        s.load()
    assert sorted(os.listdir(s.path.parent)) == ["state.json.bad-5", "state.json.bad-5.1"]


def test_wrong_typed_keys_are_dropped_with_problems(tmp_path):
    s = store(tmp_path)
    s.path.parent.mkdir(parents=True)
    s.path.write_text(json.dumps({
        "v": 1, "saved_at": 1.0, "version": "0",
        "director": {"target": {"vibe": 3}, "target_source": "mode", "mode": "party",
                     "nudges": {"steps": "two", "palette": "ember", "house": 1, "low": False, "extra": 5}},
        "listener": {"on": "yes"},
        "autopilot": {"switches": [1, "x"], "suppressed": [], "backoff_until": 12.5},
        "future": {"anything": [1]},
        "junk": 5}))
    back = s.load()
    assert back["director"] == {"target_source": "mode", "mode": "party",
                                "nudges": {"palette": "ember", "low": False, "extra": 5}}
    assert back["listener"] == {}
    assert back["autopilot"] == {"suppressed": [], "backoff_until": 12.5}
    assert back["future"] == {"anything": [1]}             # a section nobody described passes through
    assert "junk" not in back
    assert set(s.problems) == {"director.target: dropped (not the saved shape)",
                               "director.nudges.steps: dropped (not the saved shape)",
                               "director.nudges.house: dropped (not the saved shape)",
                               "listener.on: dropped (not the saved shape)",
                               "autopilot.switches: dropped (not the saved shape)",
                               "junk: dropped (not an object)"}
    assert s.error is None and s.path.exists()            # a bad key is not a bad file


# -- throttle ------------------------------------------------------------------------------------------------------------
def test_flush_writes_only_when_dirty_and_at_most_once_per_interval(tmp_path):
    mono = Clock(100.0)
    s = store(tmp_path, mono=mono, min_interval_s=1.0)
    calls = []

    def collect():
        calls.append(mono.t)
        return {"listener": {"on": len(calls) % 2 == 1}}

    assert not s.flush(collect)                       # nothing changed
    s.dirty()
    assert s.flush(collect) and calls == [100.0]
    s.dirty()
    mono.t = 100.5
    assert not s.flush(collect)                       # too soon: stays dirty
    assert s.is_dirty
    mono.t = 101.0
    assert s.flush(collect) and calls == [100.0, 101.0] and not s.is_dirty
    assert s.flush(collect, force=True)               # stop(): a final write whatever
    assert s.saves == 3


def test_dirty_during_collect_rearms(tmp_path):
    mono = Clock(0.0)
    s = store(tmp_path, mono=mono)

    def collect():
        s.dirty()                                     # another thread changes something mid-write
        return {"listener": LISTENER}

    s.dirty()
    assert s.flush(collect) and s.is_dirty


def test_write_failure_never_raises_and_retries(tmp_path):
    blocker = tmp_path / "show"
    blocker.write_text("a file where the directory should be")
    mono = Clock(0.0)
    s = StateStore(blocker / "state.json", mono=mono, min_interval_s=1.0)
    s.dirty()
    assert s.flush(lambda: {"listener": LISTENER}) is False
    assert s.error and s.error.startswith("Can't save the state") and s.is_dirty and s.failures == 1
    assert not s.flush(lambda: {"listener": LISTENER})    # not again within the interval
    blocker.unlink()
    mono.t = 1.5
    assert s.flush(lambda: {"listener": LISTENER}) and s.error is None


def test_collect_that_raises_is_counted_not_raised(tmp_path):
    s = store(tmp_path)
    s.dirty()

    def boom():
        raise RuntimeError("component broke")

    assert s.flush(boom) is False and "component broke" in s.error and s.is_dirty


def test_status_ages(tmp_path):
    wall = Clock(1000.0)
    s = store(tmp_path, wall=wall)
    s.save({"listener": LISTENER})
    wall.t = 1004.25
    st = s.status()
    assert st["path"].endswith("state.json") and st["saved_age_s"] == 4.2 and st["error"] is None
    s2 = store(tmp_path, wall=wall)
    s2.load()
    assert s2.status()["saved_age_s"] == 4.2              # the loaded file's age before this process writes


# -- jsonl ----------------------------------------------------------------------------------------------------------------
def test_jsonl_append_rotate_and_tail(tmp_path):
    p = tmp_path / "logs" / "director.jsonl"
    for i in range(30):
        assert ss.append_jsonl(p, {"i": i, "pad": "x" * 40}, max_bytes=600)
    rotated = p.with_name("director.jsonl.1")
    assert rotated.exists() and p.stat().st_size < 700
    got = ss.tail_jsonl(p, 12)
    assert [e["i"] for e in got] == list(range(18, 30))          # newest last, reaching into .1
    assert [e["i"] for e in ss.tail_jsonl(p, 3)] == [27, 28, 29]
    assert ss.tail_jsonl(tmp_path / "nope.jsonl", 5) == [] and ss.tail_jsonl(p, 0) == []


def test_tail_skips_bad_lines_and_reads_big_files_from_the_end(tmp_path):
    p = tmp_path / "a.jsonl"
    with open(p, "w") as f:
        for i in range(5000):
            f.write(json.dumps({"i": i, "pad": "y" * 30}) + "\n")
        f.write("not json\n[1,2]\n")
    assert [e["i"] for e in ss.tail_jsonl(p, 4)] == [4996, 4997, 4998, 4999]     # the 2 bad lines don't count
    got = ss.tail_jsonl(p, 50)
    assert got[-1]["i"] == 4999 and all(isinstance(e, dict) for e in got)


def test_append_never_raises(tmp_path):
    blocker = tmp_path / "f"
    blocker.write_text("x")
    assert ss.append_jsonl(blocker / "sub" / "log.jsonl", {"a": 1}) is False
