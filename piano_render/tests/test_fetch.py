"""piano_render.fetch: downloads (resume, size + sha256 checks), unpacking (strip, members, unsafe paths, macOS
litter), shared subdirs, idempotence, the bitKlavier SFZ map and the sfz-wrap recipe, and the shipped registry's
shape. Offline: a local HTTP server with Range support stands in for the internet."""

from __future__ import annotations

import hashlib
import http.server
import io
import json
import re
import tarfile
import threading
import zipfile
from pathlib import Path

import pytest

from piano_render import fetch as F

REPO = Path(__file__).resolve().parents[2]


class _RangeHandler(http.server.BaseHTTPRequestHandler):
    files: dict = {}
    log: list = []

    def do_GET(self):  # noqa: N802
        body = self.files.get(self.path)
        rng = self.headers.get("Range")
        self.log.append((self.path, rng))
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        if rng:
            start = int(re.match(r"bytes=(\d+)-", rng).group(1))
            if start >= len(body):
                self.send_response(416)
                self.end_headers()
                return
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(body) - 1}/{len(body)}")
            part = body[start:]
        else:
            self.send_response(200)
            part = body
        self.send_header("Content-Length", str(len(part)))
        self.end_headers()
        self.wfile.write(part)

    def log_message(self, *a):
        pass


@pytest.fixture(autouse=True)
def _no_shared_cpu_lock(monkeypatch):
    """Tests unpack tiny archives: never queue on the box's real ~/.cache/screen-machine/cpu.lock."""
    monkeypatch.setenv("SM_CPU_LOCK_HELD", "1")


@pytest.fixture()
def server():
    _RangeHandler.files = {}
    _RangeHandler.log = []
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RangeHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    yield base, _RangeHandler
    srv.shutdown()


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def _tar(files: dict) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:xz") as t:
        for name, data in files.items():
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            t.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def test_download_resumes_and_verifies(server, tmp_path):
    base, h = server
    body = bytes(range(256)) * 4000
    h.files["/x.bin"] = body
    dest = tmp_path / "x.bin"
    (tmp_path / "x.bin.part").write_bytes(body[:300000])
    F.download(base + "/x.bin", dest, _sha(body), len(body), log=lambda s: None)
    assert dest.read_bytes() == body
    assert ("/x.bin", "bytes=300000-") in h.log
    # a good cached file is kept without a request
    n = len(h.log)
    F.download(base + "/x.bin", dest, _sha(body), len(body), log=lambda s: None)
    assert len(h.log) == n


def test_download_rejects_wrong_sha(server, tmp_path):
    base, h = server
    h.files["/y.bin"] = b"hello world"
    with pytest.raises(F.FetchError, match="sha256"):
        F.download(base + "/y.bin", tmp_path / "y.bin", "0" * 64, 11, log=lambda s: None)
    assert not (tmp_path / "y.bin").exists() and not (tmp_path / "y.bin.part").exists()


def test_download_404_is_an_error(server, tmp_path):
    base, _ = server
    with pytest.raises(F.FetchError, match="404"):
        F.download(base + "/missing", tmp_path / "m", None, None, log=lambda s: None)


def test_unpack_zip_strip_members_and_litter(tmp_path):
    z = tmp_path / "a.zip"
    z.write_bytes(_zip({"top/keep/a.sfz": b"<region>", "top/keep/s.wav": b"w", "top/drop.txt": b"d",
                        "__MACOSX/top/._a.sfz": b"x", "top/keep/._s.wav": b"x"}))
    out = tmp_path / "out"
    n = F.unpack(z, "zip", out, strip=1, members=["keep/*"])
    assert n == 2
    assert sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()) == ["keep/a.sfz", "keep/s.wav"]


def test_unpack_tar_and_unsafe_member(tmp_path):
    t = tmp_path / "a.tar.xz"
    t.write_bytes(_tar({"lib/x.sfz": b"1", "lib/s/y.wav": b"2"}))
    out = tmp_path / "o"
    assert F.unpack(t, "tar", out) == 2 and (out / "lib/s/y.wav").read_bytes() == b"2"
    bad = tmp_path / "bad.zip"
    bad.write_bytes(_zip({"../evil": b"x"}))
    with pytest.raises(F.FetchError, match="unsafe"):
        F.unpack(bad, "zip", tmp_path / "o2")


def _registry(tmp_path, base, blocks: dict) -> Path:
    reg = {"root": str(tmp_path / "inst"), "engines_root": str(tmp_path / "eng"), "default": "a",
           "rooms": {"dry": {"label": "Dry", "ir": None, "synth": None, "wet_db": None}},
           "instruments": {}}
    for iid, (file, block) in blocks.items():
        reg["instruments"][iid] = {"label": iid, "engine": "fluidsynth", "file": file, "fetch": block}
    reg["instruments"]["gs"] = {"label": "Basic (Windows synth)", "engine": "windows-gs"}
    p = tmp_path / "instruments.json"
    p.write_text(json.dumps(reg))
    return p


def test_fetch_shares_a_subdir_and_is_idempotent(server, tmp_path):
    base, h = server
    data = _zip({"lib/one.sf2": b"1" * 1000, "lib/two.sf2": b"2" * 1000})
    h.files["/lib.zip"] = data
    block = {"url": base + "/lib.zip", "sha256": _sha(data), "bytes": len(data), "unpack": "zip", "strip": 1,
             "subdir": "shared"}
    reg = _registry(tmp_path, base, {"a": ("shared/one.sf2", block), "b": ("shared/two.sf2", dict(block))})
    res = F.fetch(["a", "b", "gs", "dry"], reg, log=lambda s: None, lower=False)
    assert res == {"a": "installed", "b": "installed", "gs": "present", "dry": "present"}
    assert sum(1 for p, _ in h.log if p == "/lib.zip") == 1
    marker = json.loads((tmp_path / "inst/shared/.fetched.json").read_text())
    assert marker["sha256"] == _sha(data) and marker["files"] == 2
    again = F.fetch(["a", "b"], reg, log=lambda s: None, lower=False)
    assert again == {"a": "present", "b": "present"}
    assert sum(1 for p, _ in h.log if p == "/lib.zip") == 1
    st = F.status(reg)
    assert st["a"] == "ok" and st["gs"] == "no-file" and st["dry"] == "no-file"


def test_fetch_reports_a_bad_entry_without_stopping(server, tmp_path):
    base, h = server
    good = _zip({"g.sf2": b"g"})
    h.files["/g.zip"] = good
    reg = _registry(tmp_path, base, {
        "a": ("ga/g.sf2", {"url": base + "/g.zip", "sha256": _sha(good), "bytes": len(good), "unpack": "zip",
                           "subdir": "ga"}),
        "b": ("gb/x.sf2", {"url": base + "/nope.zip", "sha256": "0" * 64, "bytes": 5, "unpack": "zip",
                           "subdir": "gb"}),
        "c": ("ga/wrong-name.sf2", {"url": base + "/g.zip", "sha256": _sha(good), "bytes": len(good),
                                    "unpack": "zip", "subdir": "ga"}),
    })
    res = F.fetch(["a", "b", "c"], reg, log=lambda s: None, lower=False)
    assert res["a"] == "installed"
    assert res["b"].startswith("failed") and "404" in res["b"]
    assert res["c"].startswith("failed") and "not found" in res["c"]
    with pytest.raises(F.FetchError, match="unknown ids"):
        F.fetch(["zzz"], reg, log=lambda s: None, lower=False)


def test_fetch_conflicting_blocks_on_one_subdir(server, tmp_path):
    base, h = server
    d1, d2 = _zip({"a.sf2": b"a"}), _zip({"b.sf2": b"b"})
    h.files["/1.zip"], h.files["/2.zip"] = d1, d2
    reg = _registry(tmp_path, base, {
        "a": ("s/a.sf2", {"url": base + "/1.zip", "sha256": _sha(d1), "bytes": len(d1), "unpack": "zip",
                          "subdir": "s"}),
        "b": ("s/b.sf2", {"url": base + "/2.zip", "sha256": _sha(d2), "bytes": len(d2), "unpack": "zip",
                          "subdir": "s"}),
    })
    res = F.fetch(["a", "b"], reg, log=lambda s: None, lower=False)
    assert res["a"] == "installed" and "different fetch block" in res["b"]


def test_bitklavier_sfz_covers_the_keyboard(tmp_path):
    names = ["A0", "C1", "D#1", "F#1"]
    for o in range(1, 8):
        names += [f"A{o}", f"C{o + 1}", f"D#{o + 1}", f"F#{o + 1}"]
    names = [n for n in names if n not in ("D#8", "F#8")]  # A0 .. C8 every minor third: 30 keys
    assert len(names) == 30
    for n in names:
        for v in range(1, 17):
            (tmp_path / f"{n}v{v}.wav").write_bytes(b"")
        for v in range(1, 4):
            (tmp_path / f"harm{n}v{v}.wav").write_bytes(b"")
    for k in range(1, 89):
        (tmp_path / f"rel{k}.wav").write_bytes(b"")
    for p in ("pedalD1", "pedalD2", "pedalU1", "pedalU2"):
        (tmp_path / f"{p}.wav").write_bytes(b"")
    (tmp_path / "._C4v1.wav").write_bytes(b"")
    out = F.post_bitklavier_sfz(tmp_path, {}, log=lambda s: None)
    text = out.read_text()
    main = [l for l in text.splitlines() if l.startswith("<region>") and re.search(r"sample=[A-G]#?\dv", l)]
    assert len(main) == 30 * 16
    cover = {}
    for l in main:
        lo, hi = int(re.search(r"lokey=(\d+)", l).group(1)), int(re.search(r"hikey=(\d+)", l).group(1))
        vlo, vhi = int(re.search(r"lovel=(\d+)", l).group(1)), int(re.search(r"hivel=(\d+)", l).group(1))
        c = int(re.search(r"pitch_keycenter=(\d+)", l).group(1))
        assert lo <= c <= hi and hi - lo <= 2
        for k in range(lo, hi + 1):
            for v in range(vlo, vhi + 1):
                cover[(k, v)] = cover.get((k, v), 0) + 1
    assert all(cover.get((k, v)) == 1 for k in range(21, 109) for v in range(1, 128))
    assert text.count("trigger=release") == 2 and "key=108" in text and "on_locc64=127" in text
    assert "._C4" not in text


def test_sfz_wrap(tmp_path):
    (tmp_path / "set").mkdir()
    (tmp_path / "set/base.sfz").write_text("<region> sample=x.wav\n")
    block = {"wrap": {"base": "set/base.sfz", "out": "set/wrapped.sfz", "set_cc": {"21": 127, "20": 100}}}
    out = F.post_sfz_wrap(tmp_path, block, log=lambda s: None)
    t = out.read_text()
    assert '#include "base.sfz"' in t and t.index("#include") < t.index("<control>")
    assert "set_cc20=100 set_cc21=127" in t
    with pytest.raises(F.FetchError):
        F.post_sfz_wrap(tmp_path, {"wrap": {"base": "set/base.sfz", "out": "other.sfz"}}, log=lambda s: None)
    # "global": a patched copy, the opcodes in force after every <global> (or a <global> of their own on top)
    (tmp_path / "set/g.sfz").write_text("<control> default_path=s/\n<global> volume=2\n<region> sample=a.wav\n"
                                        "<global>\n<region> sample=b.wav\n")
    spec = [{"base": "set/g.sfz", "out": "set/g2.sfz", "global": "sustain_lo=64"},
            {"base": "set/base.sfz", "out": "set/b2.sfz", "global": "sustain_lo=64", "set_cc": {"64": 0}}]
    F.post_sfz_wrap(tmp_path, {"wrap": spec}, log=lambda s: None)
    g2 = (tmp_path / "set/g2.sfz").read_text()
    assert g2.count("<global> sustain_lo=64") == 2 and "#include" not in g2 and "default_path=s/" in g2
    b2 = (tmp_path / "set/b2.sfz").read_text()
    assert b2.index("<global> sustain_lo=64") < b2.index("<region>") and b2.rstrip().endswith("set_cc64=0")


def test_sfz_wrap_replace(tmp_path):
    (tmp_path / "base.sfz").write_bytes(b"<global> pan=1\r\n<group> lokey=-1 on_locc64=126\r\n<region> sample=a.wav\r\n"
                                        b"<group> lokey=-1 on_locc64=0\r\n<region> sample=b.wav\r\n")
    spec = {"base": "base.sfz", "out": "fixed.sfz", "global": "sustain_lo=64",
            "replace": [{"old": "lokey=-1 ", "new": "lokey=-1 hikey=-1 ", "count": 2},
                        {"old": "sample=b.wav\n", "new": "sample=b.wav volume=-3\n"}]}
    out = F.post_sfz_wrap(tmp_path, {"wrap": spec}, log=lambda s: None)
    text = out.read_text()
    assert text.count("lokey=-1 hikey=-1 on_locc64") == 2 and "sample=b.wav volume=-3" in text
    assert "<global> sustain_lo=64 pan=1" in text and "#include" not in text
    assert sorted(p.name for p in tmp_path.iterdir()) == ["base.sfz", "fixed.sfz"]        # no temp file left
    # a library that changed is never patched blindly, and a failed wrap leaves the old wrapper as it was
    for bad in ({"old": "lokey=-1 ", "new": "x", "count": 3}, {"old": "no such text", "new": "x"}):
        with pytest.raises(F.FetchError, match="occurs"):
            F.post_sfz_wrap(tmp_path, {"wrap": dict(spec, replace=[bad])}, log=lambda s: None)
    assert out.read_text() == text


def test_fetch_rewraps_in_place_when_only_the_wrap_changes(server, tmp_path):
    base, h = server
    data = _zip({"lib/p.sfz": b"<group> lokey=-1 on_locc64=126\n<region> sample=a.wav\n"})
    h.files["/p.zip"] = data
    block = {"url": base + "/p.zip", "sha256": _sha(data), "bytes": len(data), "unpack": "zip", "strip": 1,
             "subdir": "p", "post": "sfz-wrap", "wrap": {"base": "p.sfz", "out": "p.sm.sfz", "global": "sustain_lo=64"}}
    reg = _registry(tmp_path, base, {"a": ("p/p.sm.sfz", block)})
    assert F.fetch(["a"], reg, log=lambda s: None, lower=False) == {"a": "installed"}
    marker0 = json.loads((tmp_path / "inst/p/.fetched.json").read_text())
    wrapped = tmp_path / "inst/p/p.sm.sfz"
    assert "hikey" not in wrapped.read_text()
    block2 = dict(block, wrap=dict(block["wrap"], replace=[{"old": "lokey=-1 ", "new": "lokey=-1 hikey=-1 ",
                                                            "count": 1}]))
    reg = _registry(tmp_path, base, {"a": ("p/p.sm.sfz", block2)})
    n = len(h.log)
    assert F.fetch(["a"], reg, log=lambda s: None, lower=False) == {"a": "rewrapped"}
    assert len(h.log) == n                                             # no download, no unpack
    marker = json.loads((tmp_path / "inst/p/.fetched.json").read_text())
    assert marker["unpacked"] == marker0["unpacked"] and marker["wrap"] == block2["wrap"]
    assert "lokey=-1 hikey=-1 on_locc64" in wrapped.read_text()
    assert F.fetch(["a"], reg, log=lambda s: None, lower=False) == {"a": "present"}
    assert not [p for p in (tmp_path / "inst/p").iterdir() if ".tmp" in p.name]
    # a different archive is a real re-install, not a rewrap
    assert F.rewrap(tmp_path / "inst/p", dict(block2, sha256="0" * 64), log=lambda s: None) is False


def test_shipped_registry_shape():
    reg, root, er = F.load_registry(REPO / "config/piano/instruments.json")
    rooms, inst = reg["rooms"], reg["instruments"]
    assert reg["default"] in inst and inst[reg["default"]]["engine"] != "windows-gs"
    assert {"dry", "room", "recital", "hall"} <= set(rooms)
    for rid, r in rooms.items():
        assert set(r) >= {"label", "ir", "synth", "wet_db"}, rid
        if r["ir"]:
            assert r["fetch"]["subdir"] and r["ir"].startswith(r["fetch"]["subdir"] + "/"), rid
    orders = []
    subdirs: dict = {}
    for iid, i in inst.items():
        assert i["label"] and i["engine"] in ("sfizz", "fluidsynth", "windows-gs"), iid
        for k in ("order", "velocity_gamma", "gain_db", "onset_ms", "width", "detune_cents", "eq", "credit"):
            assert k in i, (iid, k)
        assert i["room"] is None or i["room"] in rooms, iid
        orders.append(i["order"])
        if i["engine"] == "windows-gs":
            assert "file" not in i and "fetch" not in i
            continue
        assert i.get("fetch") or i.get("copy_from"), iid
        b = i.get("fetch")
        if b:
            assert re.fullmatch(r"[0-9a-f]{64}", b["sha256"]) and b["bytes"] > 0 and b["url"].startswith("http")
            assert b["unpack"] in ("zip", "tar", "7z", "deb", "none")
            assert i["file"].startswith(b["subdir"] + "/"), iid
            key = F._block_key(b)
            assert subdirs.setdefault(b["subdir"], key) == key, f"{iid}: subdir {b['subdir']} block differs"
        if i["engine"] == "fluidsynth":
            assert set(i.get("preset", {})) <= {"bank", "program"}
    assert max(orders) == inst["windows-gs"]["order"] and len(set(orders)) == len(orders)
