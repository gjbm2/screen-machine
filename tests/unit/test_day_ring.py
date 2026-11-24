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


def run(block, ctx, important=False, generate=None):
    """Run a block as the scheduler loop does (queue, front-pushed for_each rounds, terminate), skipping imports and
    recording waits and publishes instead of doing them (generate(instruction, ctx) stands in for a generation)."""
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
            if generate and ins["action"] == "generate":
                generate(done[-1], ctx)
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
    tmp = buckets / "_inline" / "w.jpg"                                           # a generator's temp file: no origin
    tmp.parent.mkdir()
    tmp.write_bytes(b"x")
    assert "origin" not in pub._with_origin_and_transition(tmp, "north-screen", {"id": "north-screen"}, {}, True, False)
    (buckets / "cloud" / "bucket.json").write_text("{}")
    meta = pub._with_origin_and_transition(src, "north-screen", {"id": "north-screen"},
                                           {"prompt": "p", "transition": {"url": "old"}}, True, False)
    assert meta == {"prompt": "p", "origin": "cloud/b.jpg"}
    meta = pub._with_origin_and_transition(src, "north-screen", {"id": "north-screen"},
                                           {"origin": "cloud/z.jpg", "transition": {"url": "given"}}, False, False)
    assert meta["origin"] == "cloud/z.jpg" and meta["transition"] == {"url": "given"}


def test_a_copy_on_a_screen_remembers_its_original_and_the_screen_gets_the_transition(monkeypatch, tmp_path):
    import routes.bucketer as bk
    import routes.publisher as pub
    monkeypatch.setattr(bk, "BASE_OUTPUT", tmp_path)
    (tmp_path / "cloud").mkdir()
    (tmp_path / "north-screen").mkdir()
    src = tmp_path / "cloud" / "20261124-075037-abc.jpg"
    from PIL import Image
    Image.new("RGB", (64, 36)).save(src)
    (tmp_path / "cloud" / "20261124-075037-abc.jpg.json").write_text(json.dumps({"prompt": "p", "transition": {"url": "old"}}))
    copy = bk._append_to_bucket("north-screen", src, metadata={"prompt": "p", "origin": "cloud/20261124-075037-abc.jpg"})
    meta = json.loads(copy.with_suffix(".jpg.json").read_text())
    assert meta["origin"] == "cloud/20261124-075037-abc.jpg" and "transition" not in meta
    assert tr.origin(copy) == "cloud/20261124-075037-abc.jpg"
    side = copy.with_suffix(".jpg.json")
    assert pub._published_sidecar(side, {"origin": "x", "transition": {"url": "now"}})["transition"] == {"url": "now"}
    side.write_text(json.dumps({"prompt": "p", "transition": {"url": "old"}}))
    assert "transition" not in pub._published_sidecar(side, {"prompt": "p"})


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
    ctx["vars"]["day_set"][2] = None                                              # a set still coming
    at(monkeypatch, 9, 0)
    assert run(_rotation("north-screen"), ctx) == []
    ctx["vars"]["day_set"][2] = "p2"
    at(monkeypatch, 9, 4)
    assert [d["source"] for d in run(_rotation("north-screen"), ctx)] == ["p2"]  # up as soon as it is made
    ctx["vars"]["screen_layer"] = "live"
    at(monkeypatch, 10, 0)
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


def _event_block(screen, value):
    for layer in json.loads((SCHEDULES / f"{screen}.json").read_text())["schedule_stack"]:
        for t in layer["triggers"]:
            if t.get("value") == value:
                return t["trigger_actions"]["instructions_block"]
    pytest.skip(f"{screen} has no {value} yet")


def test_the_cloud_makes_two_takes_and_chooses_them_as_a_set(monkeypatch):
    import routes.openai
    blk = _event_block("cloud", "Commission_day")
    seen = {}

    def llm(**k):
        if "two takes" in str(k.get("system_prompt")):
            seen["images"] = k.get("images")
            return {"outputs": ["1", "0", "1", "0", "1", "0", "1", "0"]}
        return {"outputs": [f"prompt {i}" for i in range(8)]}
    monkeypatch.setattr(routes.openai, "openai_prompt", llm)
    at(monkeypatch, 10, 10)                                                         # north is on p3, next p4
    ctx = {"vars": {"ring_n": 8, "ring_step_min": 30, "ring_screens": 2, "day_set": [f"old{i}" for i in range(8)],
                    "ring_buckets": ["north-screen", "south-screen"]}}
    takes = lambda ins, c: c["vars"].__setitem__(ins["output_var"], [ins["input"]["prompt"] + " A", ins["input"]["prompt"] + " B"])
    done = run([x for x in blk if x.get("action") != "export_var"], ctx, important=True, generate=takes)
    gens = [d for d in done if d["action"] == "generate"]
    assert [d["input"]["prompt"] for d in gens] == [f"prompt {i}" for i in (4, 5, 6, 7, 0, 1, 2, 3)]
    assert "batch=2" in gens[0]["additionalProperties"]
    assert [d["save_to"] for d in gens] == ["south-screen"] * 4 + ["north-screen"] * 4  # filed with its first screen
    assert seen["images"][:4] == ["prompt 0 A", "prompt 0 B", "prompt 1 A", "prompt 1 B"]      # in ring order
    assert ctx["vars"]["day_set"] == [f"prompt {i} {'B' if i % 2 == 0 else 'A'}" for i in range(8)]
    ahead = [(d["image"], d.get("to")) for d in done if d["action"] == "animate"]
    assert ("prompt 3 A", "prompt 4 B") in ahead and ahead[-1] == ("prompt 3 A", "prompt 4 B")  # wrap: back to start
    assert len([a for a in ahead if a[1]]) == 9


def test_the_beast_keeps_its_safety_check_and_tries_again(monkeypatch):
    import routes.openai
    blk = _event_block("the-beast", "Commission_day")
    verdicts = iter(["REJECT", "APPROVE"] + ["APPROVE"] * 8 + ["REJECT"] * 3)

    def llm(**k):
        if "compliance reviewer" in str(k.get("system_prompt")):
            return {"outputs": [next(verdicts), "90%", "ok"]}
        return {"outputs": ["2", "90%", "the clearest text"]}
    monkeypatch.setattr(routes.openai, "openai_prompt", llm)
    at(monkeypatch, 6, 0)
    ctx = {"vars": {"ring_n": 10, "ring_step_min": 30, "ring_screens": 1, "ring_day_starts": "08:00",
                    "day_set": [f"old{i}" for i in range(10)]}}
    n = iter(range(100))

    def batch(ins, c):
        b = next(n)
        c["vars"]["set_of_images"] = [f"b{b}-{k}" for k in range(4)]
    done = run(blk, ctx, important=True, generate=batch)
    gens = [d for d in done if d["action"] == "generate"]
    assert all("batch=4" in d["additionalProperties"] and d["refiner"] == "nous_values" for d in gens)
    assert len(gens) == 2 + 8 + 3                                                   # a retry; the last never passes
    day = ctx["vars"]["day_set"]
    assert day[0] == "b1-2" and day[1] == "b2-2" and day[9] == "old9"              # the chosen of a passing batch
    assert len([d for d in done if d["action"] == "animate" and d.get("to")]) == 11
    clips = [d["image"] for d in done if d["action"] == "animate" and not d.get("to")]
    assert clips[:2] == ["b1-2", "b2-2"] and clips[-1] == ""                    # a clip of each passing picture
    assert "unlike these recent" not in gens[0]["input"]["prompt"]                # nothing seen yet


def test_the_beast_feeds_back_its_recent_pictures(monkeypatch):
    blk = _event_block("the-beast", "Commission_day")
    gen = next(x for x in json.dumps(blk) and _walk(blk) if x.get("action") == "generate")
    ctx = {"vars": {"inspiration": "see through",
                    "beast_history": [{"refined_prompt": f"idea {k}"} for k in range(60)] + [{"prompt": "old, unrefined"}]}}
    p = su.process_instruction_jinja(gen, ctx, DEST)["input"]["prompt"]
    assert p.startswith("see through") and "idea 59" in p and "idea 11" in p and "idea 10" not in p
    assert gen["history_size"] == 50 and gen["history_var"] == "beast_history"


def _walk(block):
    for x in block:
        yield x
        yield from _walk(x.get("instructions_block") or [])


def test_the_beast_changes_every_half_hour_round_its_ten(monkeypatch):
    blk = _rotation("the-beast")
    slots = []
    for h, m in ((8, 0), (8, 30), (9, 0), (12, 59), (13, 0), (7, 59)):
        at(monkeypatch, h, m)
        ctx = {"vars": {"day_set": [f"p{i}" for i in range(10)], "ring_step_min": 30, "ring_screens": 1,
                        "ring_day_starts": "08:00"}}
        run(blk, ctx)
        slots.append(ctx["vars"]["_ring_slot"])
    assert slots == [0, 1, 2, 9, 0, 9]                                              # 300 minutes round, then again


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
    short = {"vars": {"ring_n": 8, "day_set": ["x"]}}                              # a ring is n long from the start
    run([x for x in blk if x.get("action") == "set_var" and x.get("var") == "day_set" and "{{ i }}" not in str(x)], short)
    assert short["vars"]["day_set"][0] == "x" and len(short["vars"]["day_set"]) == 8
    done = run([x for x in blk if x.get("action") != "export_var"], ctx, important=True)
    gens = [d["input"]["prompt"] for d in done if d["action"] == "generate"]
    assert gens == [f"prompt {i}" for i in (4, 5, 6, 7, 0, 1, 2, 3)]
    assert ctx["vars"]["day_set"] == [f"old{i}" for i in range(8)]                 # nothing made: the old ones stay
