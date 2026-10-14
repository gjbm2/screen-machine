"""bodies.parse_body: same result as json.loads (PNG strings pre-decoded), exact fallbacks, short GIL holds."""
from __future__ import annotations

import base64
import json
import os
import threading
import time

import pytest

from stream_service.bodies import FAST_PATH_MIN, PngData, parse_body
from stream_service.plugins.base import ParamError
from stream_service.sessions import decode_still

PNG = b"\x89PNG\r\n\x1a\n" + os.urandom(FAST_PATH_MIN)   # big enough for the fast path
B64 = base64.b64encode(PNG).decode()


def test_fast_path_decodes_png_strings_and_keeps_everything_else():
    body = {"screen": "devtest", "anchor": {"png_b64": B64}, "scene": {"preset": "x", "params": {"a": [1, 2]}},
            "label": 'a "png_b64": "quoted" \\ value', "list": [{"png_b64": B64[:400]}]}
    raw = json.dumps(body).encode()
    got = parse_body(raw)
    assert isinstance(got["anchor"]["png_b64"], PngData) and got["anchor"]["png_b64"] == PNG
    assert got["list"][0]["png_b64"] == base64.b64decode(B64[:400])
    assert got["label"] == body["label"] and got["scene"] == body["scene"]
    # decode_still takes either form
    assert decode_still(got["anchor"], "anchor") == (PNG, None)
    assert decode_still({"png_b64": B64}, "anchor") == (PNG, None)


@pytest.mark.parametrize("raw", [
    # escapes inside the value: not plain base64 -> exact json.loads, decode_still then rejects it
    json.dumps({"target": {"png_b64": B64[:1000] + "\n" + B64[1000:]}}).encode(),
    # duplicate keys: json.loads keeps the last one; so must we
    b'{"target": {"png_b64": "' + B64.encode() + b'", "png_b64": "QUFBQQ=="}}',
    # whitespace around the colon, non-ASCII elsewhere
    ('{"target" : { "png_b64" :\n "' + B64 + '" }, "label": "café"}').encode(),
])
def test_results_match_json_loads(raw):
    got = parse_body(raw)
    want = json.loads(raw)

    def norm(o):
        if isinstance(o, dict):
            return {k: (base64.b64encode(v).decode() if isinstance(v, PngData) else norm(v)) for k, v in o.items()}
        if isinstance(o, list):
            return [norm(v) for v in o]
        return o

    assert norm(got) == want


def test_invalid_base64_is_left_for_decode_still():
    raw = json.dumps({"target": {"png_b64": "!!" + B64[2:]}}).encode()
    got = parse_body(raw)
    assert isinstance(got["target"]["png_b64"], str) and not isinstance(got["target"]["png_b64"], PngData)
    with pytest.raises(ParamError) as e:
        decode_still(got["target"], "target")
    assert e.value.code == "bad_still"


def test_invalid_json_raises_value_error():
    with pytest.raises(ValueError):
        parse_body(b'{"target": {"png_b64": "' + B64.encode() + b'"}')     # unbalanced
    with pytest.raises(ValueError):
        parse_body(b"{not json")


def test_gil_is_not_held_long_for_a_4k_sized_body():
    """A 21 MB body (a noisy 4K PNG) took ~125 ms in single GIL-holding calls (json.loads + b64decode)."""
    big = base64.b64encode(os.urandom(16_000_000)).decode()
    raw = json.dumps({"target": {"png_b64": big}, "transition": "crossfade-1s"}).encode()
    gaps: list[float] = []
    stop = threading.Event()

    def ticker():
        last = time.perf_counter()
        while not stop.is_set():
            time.sleep(0.001)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    t = threading.Thread(target=ticker)
    t.start()
    time.sleep(0.05)
    t0 = time.perf_counter()
    got = parse_body(raw)
    elapsed = time.perf_counter() - t0
    time.sleep(0.02)
    stop.set()
    t.join()
    assert len(got["target"]["png_b64"]) == 16_000_000
    worst = max(gaps)
    print(f"\nparse_body 21 MB: {elapsed * 1000:.0f} ms, longest stall of a 1 ms ticker thread {worst * 1000:.1f} ms")
    assert worst < 0.080, worst   # generous for a loaded host (load ~30 seen); ~5-10 ms expected; catches the old ~125 ms stall
