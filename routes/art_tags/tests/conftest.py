"""routes/art_tags tests: a temp output/ tree, never the live one; no network, no alerts. Run from the repo root:
    .venv/bin/python -m pytest routes/art_tags/tests -p no:cacheprovider"""
import json
import os

os.environ.setdefault("ALERTING_ENABLED", "0")

import pytest
from PIL import Image

from routes.art_tags import sidecar


def make_bucket(base, name, files, favs=(), sidecars=None, legacy=False):
    """output/<name>/ with bucket.json naming ``files`` (dict entries, or plain strings when legacy), a small JPEG for
    each, and optional sidecar dicts."""
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    for f in files:
        Image.new("RGB", (64, 36), (40, 80, 160)).save(d / f, "JPEG")
        sc = (sidecars or {}).get(f)
        if sc is not None:
            (d / f"{f}.json").write_text(json.dumps(sc))
    seq = list(files) if legacy else [{"file": f} for f in files]
    (d / "bucket.json").write_text(json.dumps({"sequence": seq, "favorites": list(favs)}))
    return d


@pytest.fixture
def out(tmp_path, monkeypatch):
    base = tmp_path / "output"
    base.mkdir()
    monkeypatch.setattr(sidecar, "BASE_OUTPUT", base)
    return base
