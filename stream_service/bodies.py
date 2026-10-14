"""Parse request bodies that carry big base64 PNGs without holding the GIL for long.

A 4K still arrives as ~10-30 MB of base64 inside the JSON body (anchor on create, target on exit).  Plain
``json.loads`` plus ``base64.b64decode`` on 21 MB are single C calls that hold the GIL for ~55 + ~65 ms, and
every live session's render thread waits for that long (~7 late frames at 60 fps, on every screen), right at
the start of an out transition.  So:

* the body is parsed in a worker thread (never on the event loop);
* each ``"png_b64": "<base64>"`` string is cut out of the raw bytes first (base64 never contains a quote or a
  backslash, so the value ends at the next quote) and only the small remainder goes through ``json.loads``;
* the base64 is decoded in 1 MiB chunks, so the GIL is released between chunks.

The result is what ``json.loads`` would give, except that each ``png_b64`` value is a :class:`PngData`
(``bytes``: the decoded PNG) instead of a ``str``.  Anything unexpected (escapes inside the value, a key match
that was not a real key, a parse error) falls back to plain ``json.loads`` and leaves the strings to
``decode_still``, so the result never depends on the fast path.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
from typing import Any

CHUNK = 1 << 20                     # base64 characters per decode call (a multiple of 4)
FAST_PATH_MIN = 256 << 10           # smaller bodies: plain json.loads is quick enough
_KEY = re.compile(rb'"png_b64"\s*:\s*"')


class PngData(bytes):
    """A ``png_b64`` value already base64-decoded by :func:`parse_body`."""


class _Bad(Exception):
    pass


def b64decode_chunked(data: bytes | memoryview) -> bytes:
    """``base64.b64decode(data, validate=True)``, in chunks (each call holds the GIL for ~2 ms)."""
    mv = memoryview(data)
    if len(mv) % 4:
        raise binascii.Error("Incorrect padding")
    parts = []
    for i in range(0, len(mv), CHUNK):
        parts.append(base64.b64decode(mv[i:i + CHUNK], validate=True))
    return b"".join(parts)


def parse_body(raw: bytes) -> Any:
    """JSON-decode a request body; ``png_b64`` strings come back as :class:`PngData` when the fast path applies.

    Raises ``ValueError`` (``json.JSONDecodeError``) for invalid JSON.  Invalid base64 in a ``png_b64`` value is
    not an error here: that value is left as a ``str`` for ``decode_still`` to reject with its usual 400."""
    if len(raw) < FAST_PATH_MIN:
        return json.loads(raw)
    try:
        return _fast(raw)
    except _Bad:
        return json.loads(raw)


def _fast(raw: bytes) -> Any:
    pieces: list[bytes] = []
    values: dict[str, tuple[int, int]] = {}     # placeholder -> (start, end) of the base64 in raw
    pos = 0
    while True:
        m = _KEY.search(raw, pos)
        if m is None:
            break
        start = m.end()
        end = raw.find(b'"', start)
        if end < 0 or raw.find(b"\\", start, end) >= 0:
            raise _Bad()                         # unterminated, or escapes: not plain base64
        ph = f"\u0000sm-png-{len(values)}"
        values[ph] = (start, end)
        pieces.append(raw[pos:start])
        pieces.append(json.dumps(ph)[1:-1].encode())
        pos = end
    if not values:
        return json.loads(raw)
    pieces.append(raw[pos:])
    obj = json.loads(b"".join(pieces))           # small: the base64 is out
    found: set[str] = set()

    def walk(o: Any) -> Any:
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "png_b64" and isinstance(v, str) and v in values:
                    found.add(v)
                    a, b = values[v]
                    try:
                        o[k] = PngData(b64decode_chunked(memoryview(raw)[a:b]))
                    except (binascii.Error, ValueError):
                        o[k] = raw[a:b].decode("ascii", "replace")   # decode_still reports it
                else:
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        return o

    walk(obj)
    if found != set(values):                     # a match that was not a real "png_b64" key: be exact instead
        raise _Bad()
    return obj
