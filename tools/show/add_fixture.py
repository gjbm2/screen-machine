#!/usr/bin/env python3
"""Fetch an Open Fixture Library fixture into config/room/fixture-types/ofl/ and list its modes (README S5.2).

    python tools/show/add_fixture.py <maker>/<model>                # fetch, save, list the modes and their channels
    python tools/show/add_fixture.py <maker>/<model> --mode 6ch     # one mode's channel map, as show_service reads it
    python tools/show/add_fixture.py --file some.json [--mode 6ch]  # a local OFL file: list only, nothing is written
    python tools/show/add_fixture.py <maker>/<model> --dest /tmp/x  # save under /tmp/x/ofl/... instead
    python tools/show/add_fixture.py <maker>/<model> --force        # replace a saved copy that differs

``<maker>/<model>`` is the fixture's address on https://open-fixture-library.org (its page is
``open-fixture-library.org/<maker>/<model>``; the file is that plus ``.json``). The Open Fixture Library is MIT.

The tool never edits fixtures.yaml (c8's file; code never writes it): it prints the ``types`` entry to paste, with the
mode's name. Then add the fixtures (``type``, ``output``, ``address``), and the output and node (README S5.12 step 5).
The channel map it prints is exactly what show_service drives (show_service/ofl.py): which channels are intensity,
emitters, a colour wheel, a shutter (held open, never strobed), and every channel's default, with any problem lines.

Standard library only (urllib), like fetch_models.py. Exit codes: 0 done, 2 bad arguments, 3 the fetch or the file
failed, 4 a saved copy differs (use --force).
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import tempfile
import urllib.error
import urllib.request
from typing import Callable, Optional

REPO = pathlib.Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from show_service import contract, ofl  # noqa: E402

USER_AGENT = "screen-machine-add-fixture/1.0 (private install; python-urllib)"
TIMEOUT_S = 30.0
MAX_BYTES = 2 * 1024 * 1024                         # a fixture file is a few KB; the largest are well under 1 MB
_KEY = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")     # OFL manufacturer and fixture keys

Fetch = Callable[[str], bytes]


def fetch_url(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:     # noqa: S310 (a fixed https origin)
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError(f"more than {MAX_BYTES} bytes")
    return data


def parse_key(key: str) -> tuple[str, str]:
    """``<maker>/<model>`` (or a full open-fixture-library.org URL) -> (maker, model). ValueError when it isn't one."""
    k = key.strip()
    for pre in (ofl.OFL_URL + "/", ofl.OFL_URL.replace("https://", "http://") + "/"):
        if k.startswith(pre):
            k = k[len(pre):]
    if k.endswith(".json"):
        k = k[:-len(".json")]
    parts = k.strip("/").split("/")
    if len(parts) != 2 or not all(_KEY.match(p) for p in parts):
        raise ValueError(f"{key!r} isn't <maker>/<model> (lower case, digits and dashes, as in the site's URL)")
    return parts[0], parts[1]


def _atomic_write(path: pathlib.Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def describe_mode(doc: dict, mode: str, *, type_id: str, file: Optional[str]) -> list[str]:
    """One mode's channel map as show_service reads it (ofl.parse), as lines."""
    info, usable = ofl.parse(doc, mode, type_id=type_id, file=file)
    lines = [f"mode {info['mode']!r}: {info['footprint']} channels, drives {', '.join(info['attrs']) or 'nothing'}"]
    for c in info["channels"]:
        what = c["attr"] + (f" {c['emitter']}" if c.get("emitter") else "")
        fine = f", fine on channel {c['fine'] + 1}" if "fine" in c else ""
        lines.append(f"  {c['offset'] + 1:>3}  {c['name']:<28} {what:<16} default {c['default']}{fine}")
    if info["wheel"]:
        lines.append("  colour wheel: " + ", ".join(f"{s['name']} {s['hex']} @{s['dmx']}" for s in info["wheel"]))
    for p in info["problems"]:
        lines.append(f"  ! {p}")
    if not usable:
        lines.append("  (not usable: show_service would leave this type out)")
    return lines


def report(doc: dict, *, type_id: str, file: Optional[str], mode: Optional[str]) -> list[str]:
    name = doc.get("name") or type_id
    lines = [f"{name}: {len(doc.get('modes') or [])} mode(s) (Open Fixture Library, MIT)"]
    ms = ofl.modes(doc)
    for m in ms:
        short = f" ({m['shortName']})" if m.get("shortName") else ""
        flag = "  [pixel channels: inert in Stage 5]" if m["matrix"] else ""
        lines.append(f"  - {m['name']!r}{short}: {m['channels']} channels{flag}")
    if mode is not None:
        lines += [""] + describe_mode(doc, mode, type_id=type_id, file=file)
    else:
        for m in ms:
            lines += [""] + describe_mode(doc, m["name"], type_id=type_id, file=file)
    pick = mode or next((m.get("shortName") or m["name"] for m in ms if not m["matrix"]), None)
    if file and pick:
        lines += ["", "fixtures.yaml (paste under types:, then pick the mode the fixture is set to):",
                  f"  {type_id}:", "    source: ofl", f"    file: {file}", f"    mode: {json.dumps(pick)}"]
    return lines


def main(argv: Optional[list[str]] = None, *, fetch: Fetch = fetch_url, out=sys.stdout) -> int:
    ap = argparse.ArgumentParser(description="Fetch an Open Fixture Library fixture and list its modes.")
    ap.add_argument("key", nargs="?", help="<maker>/<model>, as in open-fixture-library.org/<maker>/<model>")
    ap.add_argument("--file", type=pathlib.Path, help="read a local OFL JSON instead (nothing is written)")
    ap.add_argument("--mode", help="show only this mode's channel map (its name or shortName)")
    ap.add_argument("--dest", type=pathlib.Path, default=contract.FIXTURE_TYPES_DIR,
                    help="the fixture-types directory (default: config/room/fixture-types); files go in ofl/<maker>/")
    ap.add_argument("--force", action="store_true", help="replace a saved copy that differs")
    a = ap.parse_args(argv)

    def say(*lines: str) -> None:
        for ln in lines:
            print(ln, file=out)

    if (a.key is None) == (a.file is None):
        say("give <maker>/<model> or --file (one of them)")
        return 2
    if a.file is not None:
        doc, why = ofl.read(a.file)
        if doc is None:
            say(f"{a.file}: {why}")
            return 3
        say(*report(doc, type_id=a.file.stem, file=None, mode=a.mode))
        return 0
    try:
        maker, model = parse_key(a.key)
    except ValueError as e:
        say(str(e))
        return 2
    url = f"{ofl.OFL_URL}/{maker}/{model}.json"
    try:
        data = fetch(url)
        doc = json.loads(data)
    except urllib.error.HTTPError as e:
        say(f"{url}: HTTP {e.code}" + (" (no such fixture: check the maker and model in the site's URL)"
                                       if e.code == 404 else ""))
        return 3
    except (urllib.error.URLError, OSError, ValueError) as e:
        say(f"{url}: {type(e).__name__}: {e}")
        return 3
    if not isinstance(doc, dict) or not isinstance(doc.get("modes"), list):
        say(f"{url}: not an Open Fixture Library fixture (no modes list)")
        return 3
    rel = f"ofl/{maker}/{model}.json"
    dest = pathlib.Path(a.dest) / rel
    body = (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    if dest.exists() and dest.read_bytes() != body and not a.force:
        say(f"{dest}: a saved copy differs from {url}: kept (use --force to replace it)")
        say(*report(doc, type_id=model, file=rel, mode=a.mode))
        return 4
    if not dest.exists() or dest.read_bytes() != body:
        _atomic_write(dest, body)
        say(f"saved {dest} (from {url})")
    else:
        say(f"{dest}: already up to date")
    say("", *report(doc, type_id=model, file=rel, mode=a.mode))
    return 0


if __name__ == "__main__":
    sys.exit(main())
