"""The art director's picture maker (routes/art_tags/director.py): the strong-match count, the caps, the "not in
Ambient" and "no wish" rules, the log, and the status route. Fakes for the room, show, prompt and generate calls:
never RunPod, never the LLM, never the live services."""
import json

from flask import Flask

from routes.art_tags import api, director
from routes.art_tags.tests.conftest import make_bucket

WISH = {"subjects": ["abstract", "interior", "urban"], "moods": ["dark", "elegant", "warm"], "media": ["painting"],
        "avoid": ["animals"], "theme": "late-night city, smoky rooms, painterly abstraction", "via": "listener"}
CFG = {"enabled": True, "interval_s": 60, "thin": 3, "per_screen_min_gap_s": 600, "per_day": 12}
T0 = 1_793_000_000.0


def sc(subject, moods, music=None, status="done", auto_ok=True, added=(), removed=(), prompt="p", workflow="w.json"):
    ai = {"subject": list(subject), "medium": "painting", "moods": list(moods), "auto_ok": auto_ok,
          "music": music or {}}
    return {"prompt": prompt, "workflow": workflow,
            "tagging": {"status": status, "ai": ai, "added": list(added), "removed": list(removed)}}


def name(i):
    return f"20261028-1000{i:02d}-{i:08x}.jpg"


class Fake:
    def __init__(self, mode="ambient", art=WISH, genre="jazz"):
        self.mode, self.art, self.genre = mode, art, genre
        self.prompts, self.made = [], []
        self.gen_ok = True

    def get_json(self, url):
        if url.endswith("/api/v1/status"):
            return {"mode": {"current": self.mode}}
        if url.endswith("/api/v1/look"):
            return {"direction": {"genre": self.genre, "genres": {}, "art": self.art}}
        if url.endswith("/api/v1/listening"):
            return {"character": "smoky late-night trio"}
        raise AssertionError(url)

    def prompt(self, wish, genre, character, recent):
        self.prompts.append((wish, genre, character, list(recent)))
        return {"ok": True, "prompt": f"picture {len(self.prompts)}", "cost_usd": 0.0006}

    def generate(self, screen, prompt, note):
        self.made.append((screen, prompt, note))
        return {"ok": True, "file": f"{screen}-{len(self.made)}.jpg"} if self.gen_ok else {"ok": False,
                                                                                           "error": "runpod down"}


def make_director(fake, tmp_path, cfg=None, screens=("north-screen", "south-screen"), clock=None):
    now = {"t": T0}
    d = director.Director(get_json_fn=fake.get_json, prompt_fn=fake.prompt, generate_fn=fake.generate,
                          screens_fn=lambda: list(screens), settings_fn=lambda: dict(cfg or CFG),
                          clock=clock or (lambda: now["t"]), log_path=tmp_path / "art_director.jsonl")
    return d, now


# -- the strong-match count -------------------------------------------------------------------------------------------
def test_count_strong_on_a_temp_bucket(out):
    files = {
        name(1): sc(["abstract"], ["dark"]),                                   # strong: subject + mood
        name(2): sc(["urban"], ["bright"], music={"jazz": 0.8}),               # strong: subject + jazz fit
        name(3): sc(["urban"], ["bright"], music={"jazz": 0.6}),               # fit too low
        name(4): sc(["urban", "animals"], ["dark"]),                           # an avoided subject
        name(5): sc(["landscape"], ["dark"]),                                  # no wished subject
        name(6): sc(["abstract"], ["dark"], auto_ok=False),                    # not for automatic picks
        name(7): sc(["abstract"], ["dark"], status="failed"),                  # not tagged
        name(8): sc(["abstract"], ["dark"], removed=["abstract"]),             # a person removed the subject
        name(9): sc(["landscape"], ["calm"], added=["interior", "warm"]),      # a person added lexicon words
        name(10): sc(["abstract"], ["dark"], workflow=None),                   # the gallery wouldn't take it
        "tmpabc.jpg": sc(["abstract"], ["dark"]),                              # not a DATE-HEX name
        name(11): {"prompt": "p", "workflow": "w"},                            # untagged
    }
    make_bucket(out, "north-screen", list(files), sidecars=files)
    c = director.count_strong("north-screen", WISH, director.groups_for("jazz"))
    assert c == {"strong": 3, "pool": 10, "tagged": 8}
    # no genre heard: only the mood rule counts
    assert director.count_strong("north-screen", WISH, [])["strong"] == 2
    assert director.count_strong("no-such-bucket", WISH, [])["strong"] == 0


def test_genre_groups_match_tagpass():
    from stream_service.tools.tagpass import GROUP_WORDS
    assert director.GROUP_WORDS == GROUP_WORDS
    assert director.groups_for("disco") == ["soul", "house"]
    assert director.groups_for("hiphop") == ["hiphop"] and director.groups_for(None) == []


def test_wish_and_genre_are_read_from_show_look():
    w = director.wish_of({"direction": {"art": dict(WISH, subjects=["Abstract", "Still Life"])}})
    assert w["subjects"] == ["abstract", "still-life"] and w["via"] == "listener"
    assert director.wish_of({"direction": {"art": dict(WISH, via="none")}}) is None
    assert director.wish_of({"direction": {"art": None}}) is None
    assert director.wish_of({"direction": {}}) is None and director.wish_of(None) is None
    assert director.genre_of({"direction": {"genre": None, "genres": {"jazz": 0.6, "pop": 0.3}}}) == "jazz"
    assert director.genre_of({"direction": {"genre": "hip-hop", "genres": {"jazz": 0.9}}}) == "hip-hop"


# -- the caps ---------------------------------------------------------------------------------------------------------
def test_pick_screen_caps():
    thin = ["north-screen", "south-screen"]
    assert director.pick_screen(thin, {}, 0, T0, CFG) == "north-screen"
    # two thin screens take turns: the one served longest ago
    assert director.pick_screen(thin, {"north-screen": T0 - 700}, 1, T0, CFG) == "south-screen"
    assert director.pick_screen(thin, {"north-screen": T0 - 700, "south-screen": T0 - 650}, 2, T0, CFG) \
        == "north-screen"
    # the per-screen gap
    assert director.pick_screen(["north-screen"], {"north-screen": T0 - 100}, 1, T0, CFG) is None
    # the day's count, off, and a failure's backoff
    assert director.pick_screen(thin, {}, 12, T0, CFG) is None
    assert director.pick_screen(thin, {}, 0, T0, dict(CFG, enabled=False)) is None
    assert director.pick_screen(thin, {}, 0, T0, CFG, backoff_until=T0 + 1) is None


# -- the rules --------------------------------------------------------------------------------------------------------
def test_not_in_ambient_does_nothing(out, tmp_path):
    make_bucket(out, "north-screen", [])
    fake = Fake(mode="gallery")
    d, _ = make_director(fake, tmp_path)
    e = d.pass_once()
    assert e["action"] == "not-ambient" and e["mode"] == "gallery"
    assert fake.prompts == [] and fake.made == []


def test_no_wish_does_nothing(out, tmp_path):
    make_bucket(out, "north-screen", [])
    for art in (None, dict(WISH, via="none")):
        fake = Fake(art=art)
        d, _ = make_director(fake, tmp_path)
        assert d.pass_once()["action"] == "no-wish"
        assert fake.prompts == [] and fake.made == []


def test_off_does_nothing(out, tmp_path):
    fake = Fake()
    d, _ = make_director(fake, tmp_path, cfg=dict(CFG, enabled=False))
    assert d.pass_once()["action"] == "off" and fake.made == []


def test_a_full_library_does_nothing(out, tmp_path):
    files = {name(i): sc(["abstract"], ["dark"]) for i in range(1, 4)}
    make_bucket(out, "north-screen", list(files), sidecars=files)
    fake = Fake()
    d, _ = make_director(fake, tmp_path, screens=("north-screen",))
    e = d.pass_once()
    assert e["action"] == "enough" and e["per_screen"]["north-screen"]["strong"] == 3 and fake.made == []


def test_thin_screens_get_one_picture_each_in_turn_within_the_caps(out, tmp_path):
    make_bucket(out, "north-screen", [])
    make_bucket(out, "south-screen", [])
    fake = Fake()
    d, now = make_director(fake, tmp_path)
    e = d.pass_once()
    assert e["action"] == "generated" and e["screen"] == "north-screen" and e["prompt"] == "picture 1"
    wish, genre, character, _ = fake.prompts[0]
    assert wish["theme"] == WISH["theme"] and genre == "jazz" and character == "smoky late-night trio"
    assert fake.made[0][2]["subjects"] == WISH["subjects"] and fake.made[0][2]["via"] == "listener"
    now["t"] += 60
    assert d.pass_once()["screen"] == "south-screen"                # the other one, a minute later
    now["t"] += 60
    assert d.pass_once()["action"] == "waiting"                     # both inside their 10-minute gap
    now["t"] += 600
    e = d.pass_once()
    assert e["screen"] == "north-screen" and fake.prompts[-1][3] == ["picture 1", "picture 2"]   # recent prompts
    assert d.today_count == 3 and len(fake.made) == 3
    s = d.snapshot()
    assert s["today_count"] == 3 and s["last_prompt"] == "picture 3" and s["action"] == "generated"
    assert s["per_screen"]["north-screen"]["thin"] is True


def test_the_day_cap_survives_a_restart_via_the_log(out, tmp_path):
    make_bucket(out, "north-screen", [])
    fake = Fake()
    cfg = dict(CFG, per_day=2, per_screen_min_gap_s=0)
    d, now = make_director(fake, tmp_path, cfg=cfg, screens=("north-screen",))
    assert d.pass_once()["action"] == "generated"
    lines = [json.loads(x) for x in (tmp_path / "art_director.jsonl").read_text().splitlines()]
    assert lines[-1]["action"] == "generated" and lines[-1]["file"] == "north-screen-1.jpg"
    d2, now2 = make_director(fake, tmp_path, cfg=cfg, screens=("north-screen",))   # "Flask restarted"
    now2["t"] = now["t"] + 60
    assert d2.pass_once()["action"] == "generated"
    now2["t"] += 60
    assert d2.pass_once()["action"] == "waiting" and d2.today_count == 2
    now2["t"] += 86_400                                             # tomorrow
    assert d2.pass_once()["action"] == "generated" and d2.today_count == 1


def test_a_failure_backs_off_and_never_raises(out, tmp_path):
    make_bucket(out, "north-screen", [])
    fake = Fake()
    fake.gen_ok = False
    d, now = make_director(fake, tmp_path, cfg=dict(CFG, per_screen_min_gap_s=0), screens=("north-screen",))
    e = d.pass_once()
    assert e["action"] == "failed" and "runpod down" in e["error"] and d.last_error
    now["t"] += 60
    assert d.pass_once()["action"] == "waiting"                     # backing off, no loop
    now["t"] += director.BACKOFF_MIN_S
    assert d.pass_once()["action"] == "failed" and d.backoff_s == 2 * director.BACKOFF_MIN_S
    fake.gen_ok = True
    now["t"] += 2 * director.BACKOFF_MIN_S
    assert d.pass_once()["action"] == "generated" and d.backoff_s == 0 and d.last_error is None

    def boom(*a):
        raise OSError("show down")
    d.get_json = boom
    now["t"] += 60
    assert d.pass_once()["action"] == "not-ambient"                 # an unreachable room: just not this pass


def test_dry_decides_without_calls_or_state(out, tmp_path):
    make_bucket(out, "north-screen", [])
    fake = Fake()
    d, _ = make_director(fake, tmp_path, screens=("north-screen",))
    e = d.pass_once(dry=True)
    assert e["action"] == "would-generate" and e["screen"] == "north-screen" and e["dry"] is True
    assert fake.prompts == [] and fake.made == [] and d.today_count == 0
    assert not (tmp_path / "art_director.jsonl").exists()


def test_scheduler_defaults_read_the_screens_schedule(tmp_path, monkeypatch):
    (tmp_path / "north-screen.json").write_text(json.dumps({"schedule_stack": [{"triggers": [
        {"trigger_actions": {"instructions_block": [
            {"action": "generate", "images": ["x.jpg"], "workflow": "upscale.json"},
            {"action": "generate", "images": [], "refiner": "enrich"}]}}]}]}))
    monkeypatch.setattr(director, "SCHEDULER_DIR", tmp_path)
    assert director.scheduler_defaults("north-screen") == ("enrich", None)
    assert director.scheduler_defaults("nowhere") == (None, None)


def test_api_director_status(out, tmp_path):
    fake = Fake(mode="gallery")
    d, _ = make_director(fake, tmp_path)
    director.set_director(d)
    try:
        d.pass_once()
        app = Flask(__name__)
        app.register_blueprint(api.art_tags_api, url_prefix="/api")
        r = app.test_client().get("/api/art-tags/director")
        assert r.status_code == 200
        j = r.get_json()
        assert j["enabled"] is True and j["mode"] == "gallery" and j["action"] == "not-ambient"
        assert j["today_count"] == 0 and set(j) >= {"wish", "per_screen", "last_prompt", "last_error"}
    finally:
        director.set_director(None)
