"""The day's set (owner, 24 Nov): the cloud commissions n pictures, their clips and the transitions between them; the
screens take turns rotating them from the clock; a replay walks the set one change at a time. The generic scheduler
pieces (for_each, set_var index/length, reason output_list, native Jinja values), the transition cache a publish reads,
and the live schedules' own arithmetic (routes/scheduler/<id>.json)."""
import json
from datetime import datetime as real_datetime
from pathlib import Path

import pytest

import routes.scheduler as sch
import routes.scheduler_utils as su
from routes.scheduler_queue import get_instruction_queue
from routes import transitions as tr

DEST = "test-ring"
SCHEDULES = Path(__file__).resolve().parents[2] / "routes" / "scheduler"


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    """No scheduler state written to disk, no exported-var registry."""
    monkeypatch.setattr(sch, "update_scheduler_state", lambda *a, **k: None)
    monkeypatch.setattr(sch, "get_context_stack", lambda d: [])
    get_instruction_queue(DEST).clear()


def at(monkeypatch, hh, mm):
    class Fixed(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime(2026, 11, 24, hh, mm, 0)
    monkeypatch.setattr(su, "datetime", Fixed)


def run(block, ctx, important=False):
    """Run a block as the scheduler loop does (queue, front-pushed for_each rounds, terminate), skipping imports and
    recording waits and publishes instead of doing them."""
    q = get_instruction_queue(DEST)
    q.clear()
    q.push_block(block, important=important)
    done = []
    while (e := q.pop_next()):
        ins = e["instruction"]
        if ins.get("action") == "import_var":
            continue
        if ins.get("action") in ("wait", "publish", "animate", "generate"):
            done.append(su.process_instruction_jinja(ins, ctx, DEST))
            continue
        if sch.run_instruction(ins, ctx, real_datetime.now(), [], DEST) == "EXIT_BLOCK":
            q.remove_non_important()
    return done


# --- the generic pieces ---------------------------------------------------------------------------------------------

def test_for_each_runs_its_block_per_item_in_order_with_item_and_place():
    ctx = {"vars": {"pics": ["a.jpg", "b.jpg", "c.jpg"]}}
    done = run([{"action": "for_each", "items": "pics", "as": "p", "index_as": "k", "instructions_block": [
        {"action": "publish", "source": "{{ k }}:{{ p }}"}, {"action": "wait", "duration": "{{ k * 10 }}s"}]}], ctx)
    assert [d.get("source") or d.get("duration") for d in done] == ["0:a.jpg", "0s", "1:b.jpg", "10s", "2:c.jpg", "20s"]


def test_for_each_takes_an_expression_and_nests():
    ctx = {"vars": {"n": 3, "start": 1}}
    done = run([{"action": "for_each", "items": "(range(start, n)|list) + (range(0, start)|list)", "as": "i",
                 "instructions_block": [{"action": "for_each", "items": "range(2)", "as": "j", "instructions_block": [
                     {"action": "publish", "source": "{{ i }}{{ j }}"}]}]}], ctx)
    assert [d["source"] for d in done] == ["10", "11", "20", "21", "00", "01"]


def test_for_each_rounds_keep_the_blocks_flags():
    q = get_instruction_queue(DEST)
    q.push_block([{"action": "for_each", "items": [1, 2], "instructions_block": [{"action": "log", "message": "x"}]}],
                 important=True, urgent=True)
    e = q.pop_next()
    sch.run_instruction(e["instruction"], {"vars": {}}, real_datetime.now(), [], DEST)
    assert len(q.queue) == 6 and all(x["important"] and x["urgent"] for x in q.queue)


def test_set_var_sets_one_element_and_cuts_a_list():
    ctx = {"vars": {"day_set": ["a", "b"]}}
    run([{"action": "set_var", "var": "day_set", "index": "3", "input": {"value": "d"}},
         {"action": "set_var", "var": "day_set", "index": "{{ 0 }}", "input": {"value": "A"}}], ctx)
    assert ctx["vars"]["day_set"] == ["A", "b", None, "d"]
    run([{"action": "set_var", "var": "day_set", "length": "2"}], ctx)
    assert ctx["vars"]["day_set"] == ["A", "b"]
    run([{"action": "set_var", "var": "day_sets", "index": "2026-11-24", "input": {"var_ref": "day_set"}}], ctx)
    assert ctx["vars"]["day_sets"] == {"2026-11-24": ["A", "b"]}
    run([{"action": "set_var", "var": "day_set", "index": "0", "input": {"value": "Z"}}], ctx)
    assert ctx["vars"]["day_sets"]["2026-11-24"] == ["A", "b"]      # a copy, not the same list


def test_reason_can_put_all_its_results_in_one_list(monkeypatch):
    import routes.openai
    monkeypatch.setattr(routes.openai, "openai_prompt", lambda **k: {"outputs": ["p1", "p2", "p3"]})
    ctx = {"vars": {}}
    run([{"action": "reason", "reasoner": "day_set", "output_list": "prompts", "text_input": "x"}], ctx)
    assert ctx["vars"]["prompts"] == ["p1", "p2", "p3"]
    monkeypatch.setattr(routes.openai, "openai_prompt", lambda **k: None)       # a failure keeps what it had
    run([{"action": "reason", "reasoner": "day_set", "output_list": "prompts", "text_input": "x"}], ctx)
    assert ctx["vars"]["prompts"] == ["p1", "p2", "p3"]


def test_jinja_value_keeps_a_list_a_list():
    assert su.jinja_value("{{ xs[1:] }}", {"vars": {"xs": [1, 2, 3]}}) == [2, 3]
    assert list(su.jinja_value("range(n)", {"vars": {"n": 2}})) == [0, 1]
    assert su.jinja_value("{{ nope( }}", {"vars": {}}) is None


# --- the transition cache -------------------------------------------------------------------------------------------

@pytest.fixture
def buckets(monkeypatch, tmp_path):
    import routes.bucketer
    import routes.publisher
    for mod in (routes.bucketer, routes.publisher):
        monkeypatch.setattr(mod, "bucket_path", lambda b: tmp_path / b)
    for b in ("cloud", "north-screen"):
        (tmp_path / b).mkdir()
    return tmp_path


def test_a_transition_made_ahead_is_kept_once_with_both_pictures(monkeypatch, buckets):
    a, b = buckets / "cloud" / "a.jpg", buckets / "cloud" / "b.jpg"
    a.write_bytes(b"a")
    b.write_bytes(b"b")
    drawn = []
    monkeypatch.setattr(tr, "write", lambda *x: {"full_prompt": "the tide comes in from the left", "idea": "tide"})
    monkeypatch.setattr(tr, "draw", lambda *x, **k: drawn.append(1) or ("worker.mp4", 0.1))
    monkeypatch.setattr(tr, "ease", lambda src, dst, deadline: Path(dst).write_bytes(b"mp4"))
    t = tr.commission(a, b)
    assert t["from"] == "cloud/a.jpg" and t["to"] == "cloud/b.jpg" and (buckets / "cloud" / t["file"]).exists()
    for p in (a, b):
        assert json.loads(p.with_suffix(".jpg.json").read_text())["transitions"][0]["file"] == t["file"]
    assert tr.commission(a, b)["file"] == t["file"] and len(drawn) == 1          # cached: not drawn again
    assert tr.records("cloud")["transitions"][0]["from_thumb"] == "/output/cloud/thumbnails/a.jpg.jpg"


def test_a_publish_plays_the_transition_for_its_pair_or_cross_fades(monkeypatch, buckets):
    import routes.clips
    cloud, screen = buckets / "cloud", buckets / "north-screen"
    for n in ("a.jpg", "b.jpg", "c.jpg"):
        (cloud / n).write_bytes(b"x")
    (cloud / "transitions").mkdir()
    (cloud / "transitions" / "ab.mp4").write_bytes(b"v")
    made = {"file": "transitions/ab.mp4", "url": "/output/cloud/transitions/ab.mp4", "from": "cloud/a.jpg", "to": "cloud/b.jpg"}
    (cloud / "b.jpg.json").write_text(json.dumps({"transitions": [made]}))
    # the screen shows its copy of a (it keeps a's origin)
    (screen / "a-copy.jpg").write_bytes(b"x")
    (screen / "a-copy.jpg.json").write_text(json.dumps({"origin": "cloud/a.jpg"}))
    monkeypatch.setattr(routes.clips, "published_still", lambda s: "a-copy.jpg")
    t = tr.cached("north-screen", cloud / "b.jpg", {})
    assert t["file"] == "transitions/ab.mp4" and t["at"] > 0
    assert tr.cached("north-screen", cloud / "c.jpg", {}) is None                  # no such pair: cross-fade
    monkeypatch.setattr(routes.clips, "published_still", lambda s: None)
    assert tr.cached("north-screen", cloud / "b.jpg", {}) is None                  # nothing showing


def test_a_published_picture_keeps_its_origin_and_drops_an_old_transition(monkeypatch, buckets):
    import routes.publisher as pub
    src = buckets / "cloud" / "b.jpg"
    src.write_bytes(b"x")
    meta = pub._with_origin_and_transition(src, "north-screen", {"id": "north-screen"},
                                           {"prompt": "p", "transition": {"url": "old"}}, True, False)
    assert meta == {"prompt": "p", "origin": "cloud/b.jpg"}
    meta = pub._with_origin_and_transition(src, "north-screen", {"id": "north-screen"},
                                           {"origin": "cloud/z.jpg", "transition": {"url": "given"}}, False, False)
    assert meta["origin"] == "cloud/z.jpg" and meta["transition"] == {"url": "given"}


# --- the live schedules' arithmetic ---------------------------------------------------------------------------------

def _trigger(screen, pick):
    for layer in json.loads((SCHEDULES / f"{screen}.json").read_text())["schedule_stack"]:
        for t in layer["triggers"]:
            for sa in t.get("scheduled_actions", []) or [t]:
                blk = (sa.get("trigger_actions") or {}).get("instructions_block") or []
                if pick(t, blk):
                    return blk
    pytest.skip(f"{screen} has no such trigger yet")


def _rotation(screen):
    return _trigger(screen, lambda t, blk: any(x.get("var") == "_ring_slot" for x in blk))


def _shown(monkeypatch, screen, hh, mm, n=8, step=30):
    at(monkeypatch, hh, mm)
    ctx = {"vars": {"day_set": [f"p{i}" for i in range(n)], "ring_step_min": step, "ring_day_starts": "07:00",
                    "ring_screens": 2, "screen_layer": "art"}}
    run(_rotation(screen), ctx)
    return ctx["vars"]["_ring_slot"]


def test_the_screens_take_turns_and_never_show_the_same_picture(monkeypatch):
    north = [_shown(monkeypatch, "north-screen", h, m) for h in range(7, 24) for m in (0, 30)]
    south = [_shown(monkeypatch, "south-screen", h, m) for h in range(7, 24) for m in (0, 30)]
    assert north[:6] == [0, 0, 1, 1, 2, 2] and south[:6] == [3, 4, 4, 5, 5, 6]   # north on the hour, south on the half
    assert all(a != b for a, b in zip(north, south))
    changes = [i for i in range(1, len(north)) if (north[i], south[i]) != (north[i - 1], south[i - 1])]
    assert changes == list(range(1, len(north)))                                  # one screen changes each half hour


def test_the_step_is_one_setting(monkeypatch):
    assert [_shown(monkeypatch, "north-screen", 7, m, step=5) for m in (0, 5, 10, 15)] == [0, 0, 1, 1]


def test_the_ring_publishes_only_when_the_turn_comes(monkeypatch):
    at(monkeypatch, 8, 0)
    ctx = {"vars": {"day_set": [f"p{i}" for i in range(8)], "ring_step_min": 30, "ring_screens": 2}}
    assert [d["source"] for d in run(_rotation("north-screen"), ctx)] == ["p1"]
    at(monkeypatch, 8, 1)
    assert run(_rotation("north-screen"), ctx) == []
    ctx["vars"]["screen_layer"] = "live"
    at(monkeypatch, 9, 0)
    assert run(_rotation("north-screen"), ctx) == []                              # live: the ring waits


def test_a_replay_takes_turns_and_shows_every_transition_once(monkeypatch):
    at(monkeypatch, 15, 0)
    pick = lambda t, blk: t.get("value") == "Replay_day"
    steps = {}
    for screen in ("north-screen", "south-screen"):
        ctx = {"vars": {"day_set": [], "day_sets": {"2026-11-23": [f"p{i}" for i in range(8)]},
                        "replay_turn_s": 25, "ring_screens": 2}, "_event": {"payload": {"day": "2026-11-23"}}}
        done = run(_trigger(screen, pick), ctx, important=True)
        clock, out = 0, []
        for d in done:
            if d["action"] == "publish":
                assert d["clips"] is False and d["transition"] is True
                out.append((clock, d["source"]))
            else:
                clock += int(d["duration"].rstrip("s"))
        steps[screen] = out
    changes = sorted([(t, s, p) for s, xs in steps.items() for t, p in xs if t > 0])
    assert [t for t, _, _ in changes] == [25 * k for k in range(1, 9)]             # one change at a time, 25 s apart
    assert [s for _, s, _ in changes] == ["north-screen", "south-screen"] * 4
    pairs = {(a[1], b[1]) for xs in steps.values() for a, b in zip(xs, xs[1:])}
    assert pairs == {(f"p{i}", f"p{(i + 1) % 8}") for i in range(8)}               # each transition once


def test_the_cloud_commissions_from_the_north_screens_next_turn(monkeypatch):
    blk = None
    for layer in json.loads((SCHEDULES / "cloud.json").read_text())["schedule_stack"]:
        blk = next((t["trigger_actions"]["instructions_block"] for t in layer["triggers"]
                    if t.get("value") == "Commission_day"), blk)
    if not blk:
        pytest.skip("cloud has no Commission_day yet")
    import routes.openai
    monkeypatch.setattr(routes.openai, "openai_prompt", lambda **k: {"outputs": [f"prompt {i}" for i in range(8)]})
    at(monkeypatch, 10, 10)                                                         # north is on p3, next p4
    ctx = {"vars": {"ring_n": 8, "ring_step_min": 30, "ring_screens": 2, "day_set": [f"old{i}" for i in range(8)]}}
    done = run([x for x in blk if x.get("action") != "export_var"], ctx, important=True)
    gens = [d["input"]["prompt"] for d in done if d["action"] == "generate"]
    assert gens == [f"prompt {i}" for i in (4, 5, 6, 7, 0, 1, 2, 3)]
    ahead = [(d["image"], d.get("to")) for d in done if d["action"] == "animate"]
    assert ahead[0] == ("", None) and ahead[1] == ("old3", "")                  # no picture made in the test: nothing
