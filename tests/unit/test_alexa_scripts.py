"""Alexa loads a scheduler script with the inputs it heard (owner, 25 Nov: "computer, use ai to show favourites from
library" loads the favourites script with bucket=library): which buckets a voice may name, and how what the AI heard
becomes the script's vars."""
import routes.alexa as alexa

DESTS = [
    {"id": "_recent", "name": "Recent", "has_bucket": True, "hidden": True},
    {"id": "north-screen", "name": "North screen", "has_bucket": True},
    {"id": "library", "name": "Library", "has_bucket": True, "headless": True},
    {"id": "private", "name": "Private", "has_bucket": True, "locked": True},
    {"id": "cloud-abc", "name": "Ann's cloud", "has_bucket": True, "owner": "ann@example.com"},
    {"id": "work-laptop", "name": "Work laptop"},
]
FAVOURITES = {"description": "Show a bucket's favourites.",
              "inputs": {"bucket": {"description": "whose favourites", "choices": "buckets"}}}


def test_a_voice_names_the_open_buckets_only(monkeypatch):
    monkeypatch.setattr(alexa, "_load_json_once", lambda *a, **k: DESTS)
    assert alexa._voice_buckets() == {"north-screen": "North screen", "library": "Library"}


def test_what_the_ai_heard_becomes_the_scripts_vars():
    buckets = {"north-screen": "North screen", "library": "Library"}
    assert alexa._script_vars(FAVOURITES, {"bucket": "library"}, buckets) == {"bucket": "library"}
    assert alexa._script_vars(FAVOURITES, {"bucket": "north screen"}, buckets) == {"bucket": "north-screen"}
    assert alexa._script_vars(FAVOURITES, {"bucket": "private"}, buckets) == {}, "not a bucket a voice may name"
    assert alexa._script_vars(FAVOURITES, {"other": "x"}, buckets) == {}, "only the inputs it declares"
    assert alexa._script_vars({}, {"bucket": "library"}, buckets) == {}


def test_the_ai_is_told_each_scripts_inputs_and_answers_with_them():
    buckets = {"library": "Library"}
    text = alexa._scripts_for_ai({"favourites": FAVOURITES, "showcase": {}}, buckets)
    assert "- favourites: Show a bucket's favourites." in text and "library (Library)" in text and "- showcase" in text
    schema = alexa._script_vars_schema({"favourites": FAVOURITES}, buckets)
    assert schema["properties"]["bucket"]["enum"] == ["library"]


def test_the_shipped_scripts_read():
    scripts = alexa._scripts()
    assert "favourites" in scripts and scripts["favourites"]["inputs"]["bucket"]["choices"] == "buckets"
    assert not any(name.startswith("_") for name in scripts)
