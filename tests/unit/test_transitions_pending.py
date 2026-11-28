"""A transition commissioned ahead survives a Flask restart (27 Nov: the cloud commissioned the day's 9 at 07:35, a
restart at 07:57 took the 7 not yet made with it, and the evening's replay cross-faded): it is kept in B's
transitions/pending.json until it has run, and Flask's start makes it again (resume_pending), a few times at most."""
import json
import threading

import routes.bucketer as bk
import routes.transitions as tr


def buckets(monkeypatch, tmp_path):
    monkeypatch.setattr(bk, "BASE_OUTPUT", tmp_path)
    monkeypatch.setattr(bk, "bucket_path", lambda name: tmp_path / name)
    for name in ("north-screen/a.jpg", "north-screen/b.jpg", "south-screen/c.jpg"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(b"picture")
    return tmp_path


def pending(folder):
    f = folder / tr.FOLDER / tr.PENDING
    return json.loads(f.read_text()) if f.exists() else []


def test_a_commission_is_kept_until_it_has_run(monkeypatch, tmp_path):
    root = buckets(monkeypatch, tmp_path)
    release, done = threading.Event(), threading.Event()

    def commission(a, b):
        release.wait(5)
        done.set()
        return {"idea": "made"}
    monkeypatch.setattr(tr, "commission", commission)
    tr.commission_later(root / "north-screen/a.jpg", root / "south-screen/c.jpg")
    rows = pending(root / "south-screen")
    assert [(r["a"], r["b"], r["tries"]) for r in rows] == [(str(root / "north-screen/a.jpg"),
                                                             str(root / "south-screen/c.jpg"), 1)]
    release.set()
    assert done.wait(5)
    for _ in range(100):
        if not pending(root / "south-screen"):
            break
        threading.Event().wait(0.02)
    assert pending(root / "south-screen") == [], "made: no longer pending"


def test_flasks_start_makes_again_what_a_restart_cut_short(monkeypatch, tmp_path):
    root = buckets(monkeypatch, tmp_path)
    a, b, c = (str(root / n) for n in ("north-screen/a.jpg", "north-screen/b.jpg", "south-screen/c.jpg"))
    (root / "north-screen" / tr.FOLDER).mkdir()
    (root / "north-screen" / tr.FOLDER / tr.PENDING).write_text(json.dumps([
        {"a": a, "b": b, "at": 1, "tries": 1},                       # cut short once: made again
        {"a": c, "b": b, "at": 1, "tries": tr.TRIES},                # cut short every time: given up, and said so
        {"a": str(root / "gone.jpg"), "b": b, "at": 1, "tries": 1},  # its picture deleted since: dropped
    ]))
    calls, ran = [], threading.Event()

    def commission(x, y):
        calls.append((x, y))
        ran.set()
        return {"idea": "made"}
    monkeypatch.setattr(tr, "commission", commission)
    assert tr.resume_pending() == 1
    assert ran.wait(5)
    assert calls == [(a, b)]
    failed = (root / "north-screen" / tr.FOLDER / "failed.jsonl").read_text().splitlines()
    assert len(failed) == 1 and json.loads(failed[0])["from"] == "south-screen/c.jpg"


def test_the_scheduler_animate_to_commissions_through_it(monkeypatch, tmp_path):
    """The cloud's `animate` with `to` (its day's set) is what goes through commission_later."""
    import routes.scheduler_handlers as sh
    root = buckets(monkeypatch, tmp_path)
    got = []
    monkeypatch.setattr(tr, "commission_later", lambda a, b, tries=0: got.append((str(a), str(b))))
    sh._animate_ahead(str(root / "north-screen/a.jpg"), str(root / "north-screen/b.jpg"), "", None, "cloud", None, [])
    assert got == [(str(root / "north-screen/a.jpg"), str(root / "north-screen/b.jpg"))]


def test_a_drawn_transition_gets_its_ease_however_long_the_renderers_queue_is(monkeypatch, tmp_path):
    """28 Nov: two of the night's nine were drawn, then their ease was killed at 300 s while it still waited its turn in
    the renderer's batch queue (one at a time, 2-3.5 min each, behind the night's clips), and those changes cross-faded.
    The draw's deadline is for the drawing; the ease, once drawn, is given EASE_AHEAD_S, queue and all."""
    import subprocess as _sp
    import routes.result_source as rs
    root = buckets(monkeypatch, tmp_path)
    clock = [1000.0]
    import time as _time
    from types import SimpleNamespace
    monkeypatch.setattr(tr, "time", SimpleNamespace(time=lambda: clock[0], strftime=_time.strftime))
    monkeypatch.setattr(tr, "write", lambda *a: {"full_prompt": "fog rolls in", "idea": "fog"})

    def draw(a, b, prompt, job_id, deadline):
        clock[0] = deadline - 10                     # a slow draw: done with 10 s of the deadline left
        return "drawn.mp4", 0.1
    monkeypatch.setattr(tr, "draw", draw)
    monkeypatch.setattr(rs, "read_bytes", lambda src: b"video")
    timeouts = []

    def run(cmd, **kw):
        timeouts.append(kw.get("timeout"))
        if cmd[0] == "nice":                         # the ease: writes its part file
            open(cmd[-4], "wb").write(b"eased")
        else:                                        # ffmpeg: the tagged copy
            open(cmd[-1], "wb").write(b"tagged")
        return _sp.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(tr.subprocess, "run", run)
    made = tr.commission(root / "north-screen/a.jpg", root / "north-screen/b.jpg")
    assert made and made["idea"] == "fog"
    assert timeouts[0] >= 30 * 60, f"the ease was given {timeouts[0]} s: a queue of clips ahead of it would kill it"
