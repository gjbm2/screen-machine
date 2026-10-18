"""beatproc.py (madmom in its own process) and the clock's side of coalesced blocks and holes. Shared plumbing, so real
tests, with real children (each one is closed and, failing that, killed at teardown, pass or fail):
- the wire: hop and record frames round-trip, a reader resyncs on junk and leaves a torn frame for the next read;
- the child (``--activation fake``): every hop answered in order with its times echoed, a reset reported on the hop
  it rode (or an epoch change), a slow child (``--fake-delay-ms``) costing show-rx nothing (drops counted, a reset on
  the first hop after each run of drops), the child gone within a second of its parent, ``stop()`` leaving no zombie
  and no fd;
- the clock: madmom in the process tracks exactly like inline on test_clock_thread's loops; hops before the hello run
  inline; a SIGKILLed child goes inline at once (still locked), is respawned and switched back to;
- blocks: a 4096-frame (or any mix of whole-packet) block gives the frames 128-frame packets give; a 250 ms hole
  computes nothing (no activation, no segment, no lane reset), keeps every frame's time, and resets the activation
  on the next hop (inline, and as a flag riding the hop to the process);
- the measurement (``-s`` prints it): CPU on the calling thread per 10 ms of audio, inline vs process (and the child's
  own), with 128- and 1024-frame blocks. ``SHOW_CLOCK_MEASURE_S`` sets the seconds per run (default 6).
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import threading
import time

import numpy as np
import pytest

from show_service import beatproc as B
from show_service import clock as C
from show_service.contract import AUDIO_GAP, AUDIO_RESUME, AUDIO_SEGMENT, REPO_ROOT
from show_service.tests import test_clock as TC
from show_service.tests import test_clock_thread as TT

FS = TC.FS
T0 = TC.T0
needs_madmom = pytest.mark.skipif(not TC.HAVE_MADMOM, reason="madmom not installed")


@pytest.fixture
def made():
    """Every BeatProc a test makes (``made.append(clk._bt)``): closed at teardown, and its child killed if it is
    somehow still there."""
    procs: list[B.BeatProc] = []
    yield procs
    for bp in procs:
        pid = bp.pid
        try:
            bp.close(0.5)
        except Exception:                               # noqa: BLE001
            pass
        for p in (pid, bp.pid):
            if p:
                try:
                    os.killpg(p, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass


def proc_clock(made: list, publish=None, activation="madmom", **kw) -> C.Clock:
    clk = C.Clock(publish, activation=activation, beat_proc=True, **kw)
    assert isinstance(clk._bt, B.BeatProc)
    made.append(clk._bt)
    return clk


def gone(pid: int) -> bool:
    """No such process, or a zombie its new parent has yet to reap."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            raw = f.read()
    except OSError:
        return True
    return raw[raw.rindex(b")") + 2:].split()[0] == b"Z"


def fds() -> set[str]:
    return set(os.listdir("/proc/self/fd"))


# -- the wire ----------------------------------------------------------------------------------------------------------
def test_the_frames_round_trip_and_a_reader_resyncs_on_junk():
    assert B.HOP == C.HOP and B.HOP_HDR.size == 32 and B.HOP_BYTES == 1796 and B.REC.size == 40
    hop = (np.arange(B.HOP, dtype=np.float32) - 220.0) / 441.0
    buf = bytearray(B.HOP_BYTES)
    B.pack_hop(buf, 7, 42, hop, 123.25, 9.5, B.HOP_RESET)
    one = bytes(buf)
    assert B.split_hops(one) == ([0], len(one), 0)
    assert B.HOP_HDR.unpack_from(one, 0) == (B.HOP_MAGIC, B.VERSION, B.HOP_RESET, B.HOP, 7, 42, 123.25, 9.5)
    assert np.array_equal(np.frombuffer(one, "<f4", B.HOP, B.HOP_HDR.size), hop)
    B.pack_hop(buf, 2 ** 32 + 5, 2 ** 32 + 6, hop, 0.0, 0.0)        # u32 on the wire
    assert B.HOP_HDR.unpack_from(buf, 0)[4:6] == (5, 6)
    bad_version = bytearray(one)
    bad_version[4] = 9
    bad_n = bytearray(one)
    bad_n[6:8] = (440).to_bytes(2, "little")
    stream = (b"xx\0SMB" + one + B.HOP_MAGIC + bytes(50) + bytes(bad_version) + bytes(bad_n) + one + one[:100])
    offs, used, junk = B.split_hops(stream)
    assert len(offs) == 2 and all(stream[o:o + B.HOP_BYTES] == one for o in offs)
    assert used == len(stream) - 100                    # the torn frame waits for the rest
    assert junk == used - 2 * B.HOP_BYTES               # everything else before it was skipped, and counted
    offs, used, junk = B.split_hops(stream[used:] + one[100:])
    assert offs == [0] and junk == 0
    noise = bytes(range(256)) * 20
    offs, used, junk = B.split_hops(noise)
    assert offs == [] and used == junk == len(noise) - 3
    r = B.pack_rec(3, 9, 0.25, 1234, 1.5, 2.5, B.REC_RESET_DONE)
    assert B.REC.unpack(r) == (B.REC_MAGIC, B.VERSION, B.REC_RESET_DONE, 0, 3, 9, 0.25, 1234, 1.5, 2.5)
    offs, used, junk = B.split_recs(b"\xffSMBR" + r + b"junkjunk" + r + r[:39])
    assert len(offs) == 2 and junk == 5 + 8 and used == 5 + 40 + 8 + 40


# -- the child ---------------------------------------------------------------------------------------------------------
def send(bp: B.BeatProc, epoch: int, seq: int, hop: np.ndarray, t_raw: float, t_put: float) -> None:
    for _ in range(5000):
        r = bp.send_hop(epoch, seq, hop, t_raw, t_put)
        if r == 0:
            return
        assert r == 1                                   # full: let the child catch up
        bp.wait(0.005)
    raise AssertionError("the child never caught up")


def test_the_child_answers_every_hop_in_order_with_its_times_and_reports_each_reset_on_its_hop(made):
    bp = B.BeatProc("fake", 0, backoff_s=(0.2,))
    made.append(bp)
    assert bp.wait_ready(30) and bp.hello["pid"] == bp.pid
    rng = np.random.default_rng(1)
    hops = [rng.standard_normal(B.HOP).astype(np.float32) for _ in range(300)]
    for seq, hop in enumerate(hops, 1):
        if seq == 150:
            bp.send_reset(1)                            # rides hop 150
        send(bp, 1 if seq <= 250 else 2, seq, hop, 1000.0 + seq / 100.0, seq * 0.5)   # 251: a new epoch, no flag
    end = time.monotonic() + 20
    while bp.inflight and time.monotonic() < end:
        bp.wait(0.01)
    items = list(bp.out)
    a = [it for it in items if it[0] == "a"]
    assert [it[2] for it in a] == list(range(1, 301))   # in order, every one
    assert all(it[4] == 1000.0 + it[2] / 100.0 and it[5] == it[2] * 0.5 for it in a)     # times echoed exactly
    assert all(it[3] == float(np.mean(np.abs(hops[it[2] - 1]))) for it in a)           # float32 both ways: exact
    assert all(it[1] == (1 if it[2] <= 250 else 2) for it in a)
    resets = [i for i, it in enumerate(items) if it[0] == "reset"]
    assert [items[i + 1][2] for i in resets] == [1, 150, 251]    # each right before the hop it was done for
    assert bp.reset_done == 3 and bp.done == 300 and bp.errors == 0 and bp.junk == 0 and len(bp.cost) == 300
    pid = bp.pid
    bp.close()
    assert gone(pid) and bp.snapshot()["last_exit"]["code"] == 0 and not bp.alive


def test_a_slow_child_costs_show_rx_nothing_drops_are_counted_and_a_reset_rides_the_hop_after_them(made, monkeypatch,
                                                                                                  capsys):
    monkeypatch.setattr(B.BeatProc, "extra_args", ("--fake-delay-ms", "50"))     # 20 hops a second
    clk = proc_clock(made, activation=B.FakeActivation())
    bp = clk._bt
    sent: list[tuple[float, bool]] = []                 # (t_raw, the reset flag rides it) for every hop that went
    orig = bp.send_hop

    def spy(epoch, seq, hop, t_raw, t_put):
        flag = bp._reset_next
        r = orig(epoch, seq, hop, t_raw, t_put)
        if r == 0:
            sent.append((t_raw, flag))
        return r

    bp.send_hop = spy
    assert clk.wait_beat_ready(30)
    x = TT.loop(4.0)
    wall, cpu = [], []                                  # ms per process() call: wall clock, and this thread's CPU
    nt = T0
    start = time.perf_counter()
    for i, n0 in enumerate(range(0, len(x), 128)):     # paced as live: 100 hops a second against 20
        t0 = T0 + n0 / FS
        while time.perf_counter() - start < n0 / FS:    # (unpaced, a few records would each come back after
            time.sleep(0.0005)                          # hundreds of drops: the tempogram's gap fill, not a wait)
        w0, c0 = time.perf_counter(), time.thread_time()
        clk.process("room-mic", n0, t0, x[n0:n0 + 128].reshape(-1, 1), FS, AUDIO_SEGMENT if i == 0 else 0)
        cpu.append((time.thread_time() - c0) * 1000.0)
        wall.append((time.perf_counter() - w0) * 1000.0)
        while nt <= t0 + 128 / FS:
            clk.tick(nt)
            nt += 0.01
    st = clk.stats()["beat_thread"]
    assert st["via"] and st["queued"] <= C.BEAT_Q_MAX and st["drops"] > 200 and st["stalls"] == 0
    with capsys.disabled():
        print(f"\nprocess() with a 50 ms child, {len(wall)} calls: CPU p50 {np.percentile(cpu, 50):.3f} p99 "
              f"{np.percentile(cpu, 99):.3f} ms; wall p50 {np.percentile(wall, 50):.3f} p99 "
              f"{np.percentile(wall, 99):.3f} max {max(wall):.2f} ms")
    assert float(np.percentile(cpu[1:], 99)) < 1.0      # show-rx's own cost per call (past the segment's first)
    assert sum(w > 20.0 for w in wall) <= 3             # it never waited for the child (a hop takes 50 ms there:
                                                        # that would be every call that sends); the host may preempt
    assert clk.wait_beat(15.0)
    clk.tick(T0 + 4.1)
    st = clk.stats()
    assert st["beat_thread"]["queued"] == 0 and st["errors"] == 0 and st.get("beat_order", 0) == 0
    assert sent[0][1]                                   # the switch to the process: a clean start there
    for (t1, _), (t2, flag) in zip(sent, sent[1:]):
        if t2 - t1 > 1.5 / C.FPS:                       # hops were dropped between these two ...
            assert flag                                 # ... so the activation was reset first: no splice
    assert bp.reset_done == sum(f for _, f in sent) >= 2     # the switch's, then one per run of drops that ended


def test_the_child_dies_with_its_parent_within_a_second():
    script = textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {str(REPO_ROOT)!r})
        from show_service import beatproc as B
        bp = B.BeatProc("fake", 0)
        assert bp.wait_ready(30)
        print(bp.pid, flush=True)
        time.sleep(60)
    """)
    p = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, cwd=str(REPO_ROOT),
                         start_new_session=True)
    child = None
    try:
        line = p.stdout.readline()
        child = int(line)
        assert not gone(child)
        os.kill(p.pid, signal.SIGKILL)                  # no stop, no EOF by hand: the parent just dies
        p.wait(5)
        t0 = time.monotonic()
        while not gone(child) and time.monotonic() - t0 < 1.0:
            time.sleep(0.01)
        assert gone(child), "the beat child outlived its parent by a second"
    finally:
        for pid in (p.pid, child):
            if pid:
                try:
                    os.killpg(pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
        p.stdout.close()
        p.wait(5)


def test_stop_ends_the_child_reaped_with_every_fd_closed_and_audio_after_it_runs_inline(made, tmp_path):
    before = fds()
    log_path = tmp_path / "beat-worker.log"
    clk = proc_clock(made, activation=B.FakeActivation(), beat_log=log_path)
    bp = clk._bt
    assert clk.wait_beat_ready(30)
    x = TT.loop(2.0)
    TT.feed(clk, x[:FS])
    pid = clk.beat_pid()
    st = clk.stats()["beat_thread"]
    assert pid and st["pid"] == pid and st["via"] and st["mode"] == "process" and st["state"] == "running"
    t0 = time.monotonic()
    other = threading.Thread(target=clk.stop)           # any thread, idempotent
    other.start()
    clk.stop(1.0)
    clk.stop()
    other.join(5.0)
    assert time.monotonic() - t0 < 2.0 and not bp.alive and not bp._sup.is_alive()
    assert gone(pid) and not os.path.exists(f"/proc/{pid}")      # reaped: no zombie
    with pytest.raises(ChildProcessError):
        os.waitpid(pid, os.WNOHANG)
    assert fds() == before                              # both pipes (and the log) closed
    assert clk.beat_pid() is None and "beat-worker start" in log_path.read_text()
    n = clk.stats()["frames"]
    TT.feed(clk, x[FS:], t_start=T0 + 1.0, n_start=FS, first_flags=0)
    st = clk.stats()
    assert st["beat_inline_after_stop"] == 1 and st["frames"] > n + 80 and st["errors"] == 0
    assert st["beat_thread"]["mode"] == "inline" and not st["beat_thread"]["on"]


def script(tmp_path, name: str, body: str) -> str:
    """A stand-in for the interpreter (it ignores the -m ... argv): a child that never says hello, or crashes."""
    p = tmp_path / name
    p.write_text("#!/bin/sh\n" + body + "\n")
    p.chmod(0o755)
    return str(p)


def test_a_child_that_never_says_hello_or_crashes_is_retried_forever_and_cleaned_up(made, tmp_path):
    before = fds()
    log_path = tmp_path / "beat-worker.log"
    for name, body, why in (("silent", "exec sleep 30", "no hello in 0.3 s"),
                            ("crash", "echo 'no madmom here' >&2; exit 1", "exited before hello: ")):
        bp = B.BeatProc("fake", 0, python=script(tmp_path, name + ".sh", body), hello_s=0.3, backoff_s=(0.05, 0.1),
                        log_path=log_path)
        made.append(bp)
        end = time.monotonic() + 15
        while bp.attempts < 4 and time.monotonic() < end:
            bp.poll()                                   # show-rx's side: nothing to adopt, never ready
            assert not bp.ready and bp.send_hop(1, 1, np.zeros(B.HOP, np.float32), 0.0, 0.0) == 2
            time.sleep(0.02)
        assert bp.attempts >= 4 and bp.restarts >= 3 and bp.failures_in_row >= 3 and bp.alive   # never gives up
        assert bp.last_exit["why"].startswith(why) and bp.sup_errors == 0
        if name == "crash":
            assert bp.last_exit["code"] == 1 and "no madmom here" in bp.last_exit["why"]   # the log's last line
        pid = bp.pid
        bp.close()
        assert not bp.alive and bp.pid is None and (pid is None or gone(pid))
    assert fds() == before and log_path.read_text().count("beat-worker start") >= 8


def test_with_no_child_ever_ready_the_clock_runs_inline_and_counts_it(made, monkeypatch, tmp_path):
    monkeypatch.setattr(B.BeatProc, "python", script(tmp_path, "crash.sh", "exit 1"))
    monkeypatch.setattr(B.BeatProc, "backoff_s", (0.05,))
    act = B.FakeActivation()
    clk = proc_clock(made, activation=act)
    TT.feed(clk, TT.loop(2.0), wait=False)
    st = clk.stats()
    bt = st["beat_thread"]
    assert bt["mode"] == "process" and not bt["via"] and bt["switches"] == 0 and bt["proc_lost"] == 0
    assert bt["inline_hops"] == act.hops == st["frames"] > 180 and bt["restarts"] >= 1 and st["errors"] == 0
    assert clk.wait_beat(0.1)                           # nothing in flight


# -- the clock and the process -----------------------------------------------------------------------------------------
def test_hops_before_the_hello_run_inline_and_the_frames_are_the_inline_ones(made):
    act = B.FakeActivation()
    clk = proc_clock(made, activation=act)
    rec = TT.spy_frames(clk)
    x = TT.loop(2.0)
    TT.feed(clk, x[:FS // 2], wait=False)              # the child is still starting (hundreds of ms)
    st = clk.stats()["beat_thread"]
    assert st["mode"] == "process" and not st["via"] and st["inline_hops"] > 30 and st["switches"] == 0
    assert act.hops == st["inline_hops"]
    assert clk.wait_beat_ready(30)
    TT.feed(clk, x[FS // 2:], t_start=T0 + 0.5, n_start=FS // 2, first_flags=0)
    TT.settle(clk, T0 + 2.0)
    st = clk.stats()
    assert st["beat_thread"]["via"] and st["beat_thread"]["switches"] == 1 and st["errors"] == 0
    assert act.hops == st["beat_thread"]["inline_hops"] and clk._bt.done > 100     # the rest went to the child
    ref = C.Clock(None, activation=B.FakeActivation())
    rec_ref = TT.spy_frames(ref)
    TT.feed(ref, x)
    assert np.allclose([a for a, _, _ in rec], [a for a, _, _ in rec_ref], rtol=1e-6, atol=1e-9)   # the same frames
    assert np.allclose([t for _, t, _ in rec], [t for _, t, _ in rec_ref], rtol=0, atol=1e-9)   # ... times, order


def proc_run(x: np.ndarray, made: list, proc: bool, block: int = 256) -> tuple[C.Clock, list[dict]]:
    pubs: list[dict] = []
    if proc:
        clk = proc_clock(made, lambda m: pubs.append(m["data"]))
        assert clk.wait_beat_ready(60)
    else:
        clk = C.Clock(lambda m: pubs.append(m["data"]), activation="madmom")
    TT.feed(clk, x, block=block)
    TT.settle(clk, T0 + len(x) / FS)
    return clk, pubs


@needs_madmom
@pytest.mark.parametrize("sig", ["lock", "breakdown"])
def test_madmom_in_the_process_tracks_exactly_like_inline(made, sig):
    if sig == "lock":
        x = np.zeros(int(16 * FS))
        for t in TC.grid(120.0, 0.31, 15.8):
            TC.click(x, t)
            TC.drum(x, t, 0.5, "kick")
        x = TC.finish(x)
    else:
        x, _, _ = TC.breakdown_signal(silent=False)
    out = {}
    for proc in (False, True):
        clk, pubs = proc_run(x, made, proc)
        st = clk.stats()
        counts = {k: v for k, v in st.items() if isinstance(v, int) and k not in ("published", "epoch")
                  and not k.startswith("beat_")}
        out[proc] = (counts, TT._line(clk), pubs, st)
        clk.stop()
    assert out[True][0] == out[False][0]                # every count: frames, peaks, seeds, hits, misses, relocks ...
    assert out[True][1] == out[False][1]                # the same published line, bar and phrase, state, gate, conf
    assert out[True][2][-1]["state"] == "locked"
    bt = out[True][3]["beat_thread"]
    assert bt["mode"] == "process" and bt["inline_hops"] == 0 and bt["switches"] == 1 and bt["drops"] == 0
    assert bt["act_ms_p95"] > 0.0 and bt["proc_lost"] == 0 and out[True][3]["errors"] == 0


@needs_madmom
def test_a_killed_child_goes_inline_at_once_stays_locked_and_is_switched_back_once_respawned(made, monkeypatch,
                                                                                           tmp_path):
    monkeypatch.setattr(B.BeatProc, "backoff_s", (0.3,))
    pubs: list[dict] = []
    clk = proc_clock(made, lambda m: pubs.append(m["data"]), beat_log=tmp_path / "beat.log")
    assert clk.wait_beat_ready(60)
    secs = 30.0
    x = TC.finish(TC.kick_loop(TC.grid(120.0, 0.3, secs - 0.2), secs))
    TT.feed(clk, x[:10 * FS])
    st = clk.stats()["beat_thread"]
    assert clk.snapshot()["state"] == "locked" and st["via"]
    pid0 = clk.beat_pid()
    n_pubs = len(pubs)
    os.kill(pid0, signal.SIGKILL)
    t_kill = time.monotonic()
    while not gone(pid0) and time.monotonic() - t_kill < 5:
        time.sleep(0.005)
    TT.feed(clk, x[10 * FS:12 * FS], t_start=T0 + 10.0, n_start=10 * FS, first_flags=0)
    st = clk.stats()["beat_thread"]
    assert st["proc_lost"] == 1 and not st["via"] and st["inline_hops"] > 150    # inline at once, nothing lost
    assert clk.wait_beat_ready(60)                      # respawned (0.3 s backoff + a cold start)
    TT.feed(clk, x[12 * FS:], t_start=T0 + 12.0, n_start=12 * FS, first_flags=0)
    TT.settle(clk, T0 + secs)
    st = clk.stats()
    bt = st["beat_thread"]
    assert bt["via"] and bt["switches"] == 2 and bt["restarts"] >= 1 and bt["pid"] != pid0
    assert bt["last_exit"]["code"] == -signal.SIGKILL and bt["last_exit"]["pid"] == pid0
    after = pubs[n_pubs:]
    assert all(p["state"] != "searching" for p in after)            # the grid held through both switches
    assert after[-1]["state"] == "locked" and after[-1]["bpm"] == pytest.approx(120.0, abs=0.3)
    for t in TC.grid(120.0, 26.3, 29.5):                # still on the kicks
        b = C.beat_at(after[-1], T0 + t)
        assert abs(b - round(b)) < 0.03
    assert st["errors"] == 0


# -- blocks and holes --------------------------------------------------------------------------------------------------
def same_line(a: tuple, b: tuple) -> bool:
    """Two published lines (test_clock_thread._line) equal, floats to 1e-9 relative (block cuts round t0 + i/fs and
    the fits' sums differently)."""
    return len(a) == len(b) and all(x == pytest.approx(y, rel=1e-9, abs=1e-9) if isinstance(x, float) else x == y
                                    for x, y in zip(a, b))


def blocks_run(x: np.ndarray, sizes, source: str, **kw) -> tuple[C.Clock, list[tuple], list[dict]]:
    """Feed ``x`` in blocks of ``sizes`` (cycled), each with its exact t0, and 10 ms ticks of stream time."""
    pubs: list[dict] = []
    clk = C.Clock(lambda m: pubs.append(m["data"]), activation=source, **kw)
    rec = TT.spy_frames(clk)
    n0, i, nt = 0, 0, T0
    while n0 < len(x):
        b = sizes[i % len(sizes)]
        blk = x[n0:n0 + b].reshape(-1, 1)
        t0 = T0 + n0 / FS
        clk.process("room-mic", n0, t0, blk, FS, AUDIO_SEGMENT if n0 == 0 else 0)
        clk.wait_beat()
        while nt <= t0 + blk.shape[0] / FS:
            clk.tick(nt)
            nt += 0.01
        n0 += b
        i += 1
    return clk, rec, pubs


@pytest.mark.parametrize("source", TC.SOURCES)
def test_coalesced_blocks_give_the_frames_and_beats_of_128_frame_packets(source):
    x = np.zeros(int(12 * FS))
    TC.drums_part(x, 0.3, 12.0, 124.0)
    x = TC.finish(x)
    rng = np.random.default_rng(5)
    runs = {name: blocks_run(x, sizes, source) for name, sizes in
            (("128", [128]), ("4096", [4096]), ("mix", [128 * int(k) for k in rng.integers(1, 33, 64)]))}
    _, ref, ref_pubs = runs["128"]
    assert len(ref) > 1000 and ref_pubs[-1]["state"] == "locked"
    for name in ("4096", "mix"):
        clk, rec, pubs = runs[name]
        assert len(rec) == len(ref)
        a, b = np.array([r[:2] for r in rec]), np.array([r[:2] for r in ref])
        assert np.allclose(a[:, 0], b[:, 0], rtol=1e-5, atol=1e-7)     # the activation (float noise at most)
        assert np.allclose(a[:, 1], b[:, 1], rtol=0, atol=1e-9)        # each frame's time
        st, st_ref = clk.stats(), runs["128"][0].stats()
        keep = lambda s: {k: v for k, v in s.items() if isinstance(v, int) and k != "published"}   # noqa: E731
        assert keep(st) == keep(st_ref)                 # every count: frames, peaks, seeds, hits ...
        assert same_line(TT._line(clk), TT._line(runs["128"][0]))


def gap_feed(clk: C.Clock, x: np.ndarray, t_from: float, hole_at: float, hole: int, gap_block: int = 0,
             block: int = 256, probe=None) -> None:
    """``x`` from stream time ``t_from``, with ``hole`` frames missing at ``hole_at``: one AUDIO_GAP block (a read-only
    broadcast view, as the receiver sends it), or zero-fill in GAP blocks of ``gap_block`` frames (each a hop or
    less: the old path), then RESUME on the next real block. ``wait_beat`` after every block but the last 4
    before the hole (so hops are still out there when it comes); ``probe()`` right after the hole's block."""
    n_from = int(t_from * FS)
    h0 = int(hole_at * FS) - n_from
    nt = T0 + t_from
    pieces = []
    for n0 in range(0, h0, block):
        pieces.append((n0, x[n0:min(n0 + block, h0)].reshape(-1, 1), 0))
    zeros = np.broadcast_to(np.zeros((1, 1), np.float32), (hole, 1))
    if gap_block:
        for g in range(0, hole, gap_block):
            pieces.append((h0 + g, np.zeros((min(gap_block, hole - g), 1), np.float32), AUDIO_GAP))
    else:
        pieces.append((h0, zeros, AUDIO_GAP))
    for i, n0 in enumerate(range(h0 + hole, len(x), block)):
        pieces.append((n0, x[n0:n0 + block].reshape(-1, 1), AUDIO_RESUME if i == 0 else 0))
    for i, (n0, blk, flags) in enumerate(pieces):
        t0 = T0 + t_from + n0 / FS
        clk.process("room-mic", n_from + n0, t0, blk, FS, flags)
        if flags & AUDIO_GAP and probe is not None:
            probe()
        if flags & AUDIO_GAP or not any(pc[2] & AUDIO_GAP for pc in pieces[i + 1:i + 5]):
            clk.wait_beat()
        while nt <= t0 + blk.shape[0] / FS:
            clk.tick(nt)
            nt += 0.01


def test_a_250ms_hole_computes_nothing_keeps_every_time_and_resets_the_activation_on_the_next_hop():
    runs = {}
    for name, gap_block in (("skip", 0), ("zero-fill", 480)):
        clk, pubs, _ = TC.loop_clock("aubio")
        calls: list[str] = []
        real_hop, real_reset = clk.act.process_hop, clk.act.reset
        clk.act.process_hop = lambda hop: calls.append("hop") or real_hop(hop)
        clk.act.reset = lambda: calls.append("reset") or real_reset()
        lane_resets: list[int] = []
        real_lane = clk._lane_reset
        clk._lane_reset = lambda *a: lane_resets.append(1) or real_lane(*a)
        rec = TT.spy_frames(clk)
        segs = clk.stats()["segments"]
        x = TC.finish(TC.kick_loop([t - 14.0 for t in TC.grid(120.0, 14.3, 20.0)], 6.0))
        orig = clk.process
        marks: dict[str, int] = {}

        def process(stream, n0, t0, pcm, fs, flags, _orig=orig):
            if flags & AUDIO_GAP and "gap" not in marks:
                marks["gap"] = len(calls)
            _orig(stream, n0, t0, pcm, fs, flags)
            if flags & AUDIO_GAP:
                marks["after_gap"] = len(calls)

        clk.process = process
        gap_feed(clk, x, 14.0, 15.0, 12000, gap_block)
        st = clk.stats()
        runs[name] = (rec, st)
        assert st["segments"] == segs and not lane_resets and st["errors"] == 0     # never a segment or lane reset
        assert clk.snapshot()["state"] == "locked"
        if name == "skip":
            assert calls[marks["gap"]:marks["after_gap"]] == []          # the hole ran no activation at all
            assert calls[marks["after_gap"]:marks["after_gap"] + 2] == ["reset", "hop"]   # clean on the next hop
            assert 24 <= st["gap_hops_skipped"] <= 28 and st["gap_blocks"] == 1     # 25 + soxr's ~1.4 held back
        else:
            assert calls[marks["gap"]:marks["after_gap"]].count("hop") >= 24 and "reset" not in calls[marks["gap"]:]
            assert st["gap_blocks"] == 25 and st.get("gap_hops_skipped", 0) == 0
    (rs, ss), (rz, sz) = runs["skip"], runs["zero-fill"]
    assert abs(ss["frames"] - sz["frames"]) <= 1        # a frame for every hop of the hole either way ...
    n = min(len(rs), len(rz))
    ts, tz = np.array([r[1] for r in rs[:n]]), np.array([r[1] for r in rz[:n]])
    assert np.max(np.abs(ts - tz)) < 30e-6              # ... at the same times (within a 44.1 kHz sample)
    d = np.diff(ts)
    assert np.all(np.abs(d - 1.0 / C.FPS) < 30e-6)      # one time axis straight through the hole
    i_gap = np.searchsorted(ts, T0 + 15.0)
    assert all(r[0] == 0.0 for r in rs[i_gap + 1:i_gap + 24])        # silent frames inside it


def test_the_rollbacks_zero_fill_every_hole_and_step_the_agc_per_block():
    pubs: list[dict] = []
    clk = C.Clock(lambda m: pubs.append(m["data"]), activation="aubio", gap_skip=False, agc_chunks=False)
    TC.feed(clk, TC.finish(TC.kick_loop(TC.grid(120.0, 0.3, 13.8), 14.0)), T0, flags_first=AUDIO_SEGMENT)
    assert pubs[-1]["state"] == "locked"                # the per-block AGC still tracks
    calls: list[int] = []
    real_hop = clk.act.process_hop
    clk.act.process_hop = lambda hop: calls.append(1) or real_hop(hop)
    f0, segs = clk.stats()["frames"], clk.stats()["segments"]
    x = TC.finish(TC.kick_loop([t - 14.0 for t in TC.grid(120.0, 14.3, 20.0)], 6.0))
    gap_feed(clk, x, 14.0, 15.0, 12000)                 # one 250 ms GAP block, a read-only view
    st = clk.stats()
    assert st["gap_blocks"] == 1 and st.get("gap_hops_skipped", 0) == 0 and st["segments"] == segs
    assert len(calls) == st["frames"] - f0 > 500        # every hop, the hole's zeros too, went to the activation
    assert st["errors"] == 0 and clk.snapshot()["state"] == "locked"


def test_a_hole_with_the_process_sends_nothing_and_the_reset_rides_the_next_hop(made, monkeypatch):
    monkeypatch.setattr(B.BeatProc, "extra_args", ("--fake-delay-ms", "10"))     # a hop is out there at the hole
    act = B.FakeActivation()
    clk = proc_clock(made, activation=act)
    bp = clk._bt
    assert clk.wait_beat_ready(30)
    rec = TT.spy_frames(clk)
    x = TT.loop(3.0)
    TT.feed(clk, x[:FS])
    resets0 = bp.reset_done
    waiting: list[int] = []
    gap_feed(clk, x[FS:], 1.0, 2.0, 12000, probe=lambda: waiting.append(len(clk._zq)))
    TT.settle(clk, T0 + 3.0)
    st = clk.stats()
    assert st["beat_thread"]["via"] and st["gap_hops_skipped"] >= 24 and st["errors"] == 0
    assert st["beat_thread"]["drops"] == 0 and not clk._zq
    assert waiting and waiting[0] >= 24                 # the hole's silent frames waited behind the hop in flight
    assert bp.reset_done == resets0 + 1                 # the splice: one reset, on the first hop after the hole
    assert st["frames"] == bp.done + st["gap_hops_skipped"] and act.hops == 0    # hole hops never left show-rx
    ts = np.array([r[1] for r in rec])
    assert np.all(np.abs(np.diff(ts) - 1.0 / C.FPS) < 30e-6)     # ... so every frame came in time order


# -- the measurement ---------------------------------------------------------------------------------------------------
def _cpu_run(x: np.ndarray, proc: bool, block: int, made: list) -> dict:
    """CPU on the calling thread (process() and its ticks; not wait_beat, which only a test does) per 10 ms of audio,
    and the child's own."""
    if proc:
        clk = proc_clock(made)
        assert clk.wait_beat_ready(60)
        pid = clk.beat_pid()
        c_child0 = B._proc_cpu_s(pid)
    else:
        clk = C.Clock(None, activation="madmom")
    rx = 0.0
    nt = T0
    tt = time.thread_time
    for i, n0 in enumerate(range(0, len(x) - block + 1, block)):
        t0 = T0 + n0 / FS
        c0 = tt()
        clk.process("room-mic", n0, t0, x[n0:n0 + block].reshape(-1, 1), FS, AUDIO_SEGMENT if i == 0 else 0)
        while nt <= t0 + block / FS:
            clk.tick(nt)
            nt += 0.01
        rx += tt() - c0
        clk.wait_beat()
    secs = len(x) / FS
    out = {"rx_ms": rx * 1000.0 / (secs * 100.0), "child_ms": None, "stats": clk.stats()}
    if proc:
        TT.settle(clk, nt)
        c1 = B._proc_cpu_s(pid)
        if c_child0 is not None and c1 is not None:
            out["child_ms"] = (c1 - c_child0) * 1000.0 / (secs * 100.0)
    clk.stop()
    return out


@needs_madmom
def test_bench_calling_thread_cpu_inline_vs_process(capsys, made):
    secs = float(os.environ.get("SHOW_CLOCK_MEASURE_S", "6"))
    x = np.zeros(int(secs * FS))
    TC.drums_part(x, 0.3, secs, 124.0)
    x = TC.finish(x)
    lines, res = [], {}
    for block in (128, 1024):
        for proc in (False, True):
            r = res[(block, proc)] = _cpu_run(x, proc, block, made)
            bt = r["stats"]["beat_thread"]
            lines.append(f"clock, madmom {'process' if proc else 'inline '}, {block:4d}-frame blocks: calling thread "
                         f"{r['rx_ms']:.3f} ms per 10 ms of audio"
                         + (f"; the child {r['child_ms']:.3f} ms (CPU tick 10 ms: coarse), act p50 "
                            f"{bt['act_ms_p50']} p95 {bt['act_ms_p95']} ms, drops {bt['drops']}"
                            if proc and r["child_ms"] is not None else ""))
    with capsys.disabled():
        print("\n" + "\n".join(lines))
    for block in (128, 1024):
        assert res[(block, True)]["rx_ms"] < res[(block, False)]["rx_ms"]   # madmom left the calling thread
        assert res[(block, True)]["stats"]["beat_thread"]["drops"] == 0
