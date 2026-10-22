"""show_service/library.py (the Music page's library, 8f 22 Oct): resolve for every kind, clean_midi, search ranking and
facets, pins, uploads, web search/import with stubbed sources, edit/delete of uploads and recordings, the routes
in-process, and sources.py's lib:/user: pieces. tmp folders only; nothing touches the network."""
from __future__ import annotations

import asyncio
import contextlib
import json
import pathlib
import struct

import pytest

from show_service import contract, library, recorder
from show_service.library import Library, Rejected, clean_midi, parse_smf, resolve

REC_ID = "20261022-140512-ab12"


# -- a tiny SMF writer for the tests --------------------------------------------------------------------------------
def vlq(n: int) -> bytes:
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append(0x80 | (n & 0x7F))
        n >>= 7
    return bytes(reversed(out))


def smf(tracks: list[list[tuple[int, bytes]]], ppq: int = 480, fmt: int = 1) -> bytes:
    out = b"MThd" + (6).to_bytes(4, "big") + struct.pack(">HHH", fmt, len(tracks), ppq)
    for tr in tracks:
        body = b"".join(vlq(d) + ev for d, ev in tr) + b"\x00\xff\x2f\x00"
        out += b"MTrk" + len(body).to_bytes(4, "big") + body
    return out


def run_notes(ch: int, n: int, pitch: int = 60, dur: int = 240, program: int | None = None) -> list:
    tr: list = []
    if program is not None:
        tr.append((0, bytes((0xC0 | ch, program))))
    for i in range(n):
        tr.append((0, bytes((0x90 | ch, pitch + i % 12, 80))))
        tr.append((dur, bytes((0x80 | ch, pitch + i % 12, 0))))
    return tr


def piano_file(n: int = 20) -> bytes:
    return smf([[(0, b"\xff\x51\x03\x07\xa1\x20")], run_notes(0, n)])


def channel_events(data: bytes) -> list[tuple]:
    _fmt, _ppq, ev = parse_smf(data)
    return [e for e in ev if e[2] != 0xFF]


# -- a library on tmp folders ---------------------------------------------------------------------------------------
class Folders:
    def __init__(self, tmp: pathlib.Path) -> None:
        self.lib = tmp / "lib"
        self.state = tmp / "state"
        self.files = tmp / "test-piano"
        self.recs = tmp / "recs"
        for d in (self.lib / "files" / "classical", self.files, self.recs):
            d.mkdir(parents=True)

    def catalog(self, items: dict) -> None:
        for iid, x in items.items():
            p = self.lib / x.setdefault("file", f"files/{x.get('genre') or 'classical'}/{iid}.mid")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(piano_file())
            x.setdefault("id", iid)
        (self.lib / "catalog.json").write_text(json.dumps({"v": 1, "items": items}))

    def recording(self, rid: str = REC_ID, title: str = "Take - Thu 22 Oct 14:05") -> None:
        (self.recs / f"{rid}.mid").write_bytes(piano_file())
        (self.recs / f"{rid}.json").write_text(json.dumps({"id": rid, "title": title, "recorded_at":
                                                           "2026-10-22T14:05:12+01:00", "duration_s": 12.5}))

    def library(self, **kw) -> Library:
        """A Library on these folders; recordings are the real recorder's (its list and its rename/delete)."""
        rec = recorder.Recorder(None, folder=self.recs)
        kw.setdefault("phrases_fn", lambda: {"medley": "Test pattern: a bit of everything"})
        kw.setdefault("recordings_fn", rec.recordings)
        kw.setdefault("recorder_ops", rec)
        return Library(lib_dir=self.lib, state_dir=self.state, file_dir_fn=lambda: self.files,
                       rec_dir_fn=lambda: self.recs, **kw)


@pytest.fixture
def f(tmp_path):
    return Folders(tmp_path)


CATALOG = {
    "classical-chopin-nocturne-op-9-no-2": {"title": "Nocturne in E-flat, Op. 9 No. 2", "composer": "Frédéric Chopin",
                                            "genre": "classical", "era": "Romantic", "rank": 0.9, "duration_s": 272.4,
                                            "tags": ["nocturne"]},
    "classical-chopin-nocturne-op-27-no-2": {"title": "Nocturne in D-flat, Op. 27 No. 2",
                                             "composer": "Frédéric Chopin", "genre": "classical", "rank": 0.6},
    "classical-satie-gymnopedie-1": {"title": "Gymnopédie No. 1", "composer": "Erik Satie", "genre": "classical",
                                     "rank": 0.95},
    "ragtime-joplin-maple-leaf-rag": {"title": "Maple Leaf Rag", "composer": "Scott Joplin", "genre": "ragtime",
                                      "rank": 0.8},
    "ragtime-joplin-the-entertainer": {"title": "The Entertainer", "composer": "Scott Joplin", "genre": "ragtime",
                                       "rank": 0.85},
    "jazz-ellington-nocturne": {"title": "Nocturne", "composer": "Duke Ellington", "genre": "jazz", "rank": 0.1},
}


# -- the mirrors of contract.py / recorder.py (this file is loaded by path, so it can't import them) ----------------
def test_mirrored_places_match_the_contract_and_the_recorder():
    assert library.STATE_DIR == contract.STATE_DIR and library.TEST_PIANO_DIR == contract.TEST_PIANO_DIR
    assert library.SHOW_FILE == contract.SHOW_FILE and library.REPO_ROOT == contract.REPO_ROOT
    assert library.RECORDINGS_DIR == recorder.RECORDINGS_DIR
    assert library.REC_ID_RE.pattern == recorder.ID_RE.pattern


def test_loads_by_path_without_the_package(tmp_path):
    """1c's prerender loads library.py by file path: no relative imports at import, resolve works."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("_lib_by_path", pathlib.Path(library.__file__))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    (tmp_path / "user").mkdir()
    (tmp_path / "user" / "a-b.mid").write_bytes(piano_file())
    assert mod.resolve("user:a-b", lib_dir=tmp_path) == (tmp_path / "user" / "a-b.mid").resolve()


# -- resolve ----------------------------------------------------------------------------------------------------------
def test_resolve_every_kind(f):
    f.catalog({"classical-chopin-nocturne": {"title": "Nocturne", "genre": "classical"},
               "pop-escape": {"title": "Escape", "file": "../outside.mid"}})
    (f.lib.parent / "outside.mid").write_bytes(piano_file())
    (f.lib / "user").mkdir()
    (f.lib / "user" / "my-piece-abc123.mid").write_bytes(piano_file())
    (f.files / "Classical - Bach - Prelude.mid").write_bytes(piano_file())
    f.recording()
    kw = {"lib_dir": f.lib, "files": f.files, "recs": f.recs}
    assert resolve("lib:classical-chopin-nocturne", **kw) == (f.lib / "files/classical/classical-chopin-nocturne.mid"
                                                              ).resolve()
    assert resolve("user:my-piece-abc123", **kw) == (f.lib / "user" / "my-piece-abc123.mid").resolve()
    assert resolve(f"rec:{REC_ID}", **kw) == (f.recs / f"{REC_ID}.mid").resolve()
    assert resolve("file:Classical - Bach - Prelude.mid", **kw) == (f.files / "Classical - Bach - Prelude.mid"
                                                                     ).resolve()
    assert resolve("medley", **kw) is None                                    # a built-in phrase: no file
    for bad in ("lib:nope", "lib:pop-escape", "lib:../x", "user:../../etc/passwd", "user:nope", "rec:../x",
                "rec:20991231-000000-ffff", "file:../x.mid", "file:Nope.mid", "file:notes.txt", "", None, 3,
                "Rec:x", "lib:"):
        assert resolve(bad, **kw) is None, bad
    lib = f.library()
    assert lib.resolve("lib:classical-chopin-nocturne") is not None and lib.resolve(f"rec:{REC_ID}") is not None


def test_cli_resolve_and_search(f, monkeypatch, capsys):
    f.catalog(dict(CATALOG))
    monkeypatch.setattr(library, "LIB_DIR", f.lib)
    monkeypatch.setattr(library, "STATE_DIR", f.state)
    assert library.main(["resolve", "lib:ragtime-joplin-maple-leaf-rag"]) == 0
    assert "ragtime-joplin-maple-leaf-rag.mid" in capsys.readouterr().out
    assert library.main(["resolve", "lib:nope"]) == 1
    assert library.main(["search", "maple"]) == 0
    assert "Maple Leaf Rag" in capsys.readouterr().out
    assert library.main(["stats"]) == 0 and library.main([]) == 2


# -- cleaning -------------------------------------------------------------------------------------------------------
def test_clean_keeps_the_piano_and_its_pedals_and_drops_the_rest():
    piano = run_notes(0, 20, program=0)
    piano.insert(1, (0, bytes((0xB0, 64, 127))))                  # damper down: kept
    piano.insert(2, (0, bytes((0xB0, 7, 100))))                   # volume: dropped
    piano.insert(3, (0, bytes((0xB0, 67, 40))))                   # soft pedal: kept
    bass = run_notes(1, 30, pitch=36, program=33)
    drums = run_notes(9, 40, pitch=36)
    meta = [(0, b"\xff\x51\x03\x07\xa1\x20"), (0, b"\xff\x58\x04\x04\x02\x18\x08"), (0, b"\xff\x01\x03abc"),
            (0, b"\xf0\x03\x7e\x7f\xf7")]                          # tempo, time signature kept; text and sysex not
    body, rep = clean_midi(smf([meta, piano, bass, drums]), "Test piece")
    assert rep["piano"] is True and rep["drums"] is True and rep["notes"] == 20 and rep["kept_channels"] == [1]
    assert rep["dropped"] == ["drums", "bass"] and rep["words"] == "Kept the piano; dropped the drums and bass."
    fmt, ppq, ev = parse_smf(body)
    assert fmt == 0 and ppq == 480
    ch = [e for e in ev if e[2] != 0xFF]
    assert all(e[2] & 0x0F == 0 for e in ch)                      # merged to channel 1
    assert {e[2] & 0xF0 for e in ch} == {0x90, 0x80, 0xB0}        # no program changes, no other messages
    assert sorted({e[3] for e in ch if e[2] == 0xB0}) == [64, 67]
    assert sum(1 for e in ch if e[2] == 0x90) == 20
    metas = [e[3] for e in ev if e[2] == 0xFF]
    assert 0x51 in metas and 0x58 in metas and 0x01 not in metas and 0x03 in metas    # the title as track name
    assert rep["duration_s"] == pytest.approx(20 * 240 / 480 * 0.5, abs=0.1)


def test_clean_without_a_piano_keeps_every_melodic_part_and_says_so():
    strings = run_notes(0, 20, program=48)
    guitar = run_notes(2, 10, pitch=50, program=25)
    drums = run_notes(9, 10)
    _body, rep = clean_midi(smf([strings, guitar, drums]))
    assert rep["piano"] is False and rep["notes"] == 30 and rep["kept_channels"] == [1, 3]
    assert rep["words"] == "No piano part was marked, so every instrument plays on the piano; dropped the drums."


def test_clean_no_program_change_is_gms_piano_and_a_clean_file_says_nothing():
    body, rep = clean_midi(piano_file(16))
    assert rep["piano"] is True and rep["words"] is None and rep["dropped"] == [] and rep["notes"] == 16
    assert clean_midi(body)[0] == body                             # cleaning is idempotent


def test_clean_merges_overlapping_keys_from_two_parts():
    a = [(0, b"\x90\x3c\x50"), (480, b"\x80\x3c\x00")]                    # C4 on 0..480 (channel 1)
    b = [(240, b"\x91\x3c\x60"), (480, b"\x81\x3c\x00")]                  # C4 on 240..720 (channel 2)
    filler = run_notes(0, 16, pitch=70)
    _body, rep = clean_midi(smf([a, b, filler]))
    ev = [e for e in channel_events(_body) if e[3] == 0x3C]
    assert [(e[0], e[2]) for e in ev] == [(0, 0x90), (240, 0x90), (720, 0x80)]   # re-struck, released when both let go


def test_clean_rejects_what_it_cant_play():
    cases = [(b"", "bad_midi"), (b"RIFF0000WAVE", "bad_midi"), (b"MThd\x00\x00\x00\x06\x00\x02\x00\x01\x01\xe0"
                                                                  b"MTrk\x00\x00\x00\x04\x00\xff\x2f\x00", "bad_midi"),
             (smf([[(0, b"\xff\x51\x03\x07\xa1\x20")]]), "no_notes"), (smf([run_notes(9, 30)]), "no_notes"),
             (smf([run_notes(0, 15)]), "no_notes"), (b"MThd" + b"\x00" * (library.MAX_BYTES + 1), "too_big")]
    long = [(0, b"\xff\x51\x03\x7f\xff\xff")] + run_notes(0, 16, dur=480 * 200)   # ~8.4 s a beat, 200 beats a note
    cases.append((smf([long]), "too_big"))
    for data, code in cases:
        with pytest.raises(Rejected) as e:
            clean_midi(data)
        assert e.value.code == code, (code, e.value.words)
    with pytest.raises(Rejected, match="only 15 notes"):
        clean_midi(smf([run_notes(0, 15)]))


def test_clean_unwraps_rmid_and_tolerates_a_truncated_track():
    inner = piano_file(20)
    riff = b"RIFF" + (len(inner) + 12).to_bytes(4, "little") + b"RMID" + b"data" + len(inner).to_bytes(4, "little") + inner
    assert clean_midi(riff)[1]["notes"] == 20
    assert clean_midi(inner[:-30])[1]["notes"] >= 16               # the end-of-track cut off: what was read plays


# -- search -----------------------------------------------------------------------------------------------------------
def test_search_ranks_exact_then_prefix_then_words_then_rank(f):
    f.catalog({k: dict(v) for k, v in CATALOG.items()})
    lib = f.library()
    got = lib.search("nocturne", kind="lib")
    assert [i["title"] for i in got["items"]][:3] == ["Nocturne", "Nocturne in E-flat, Op. 9 No. 2",
                                                       "Nocturne in D-flat, Op. 27 No. 2"]   # exact, then prefix by rank
    assert [i["title"] for i in lib.search("chopin noct")["items"]] == ["Nocturne in E-flat, Op. 9 No. 2",
                                                                        "Nocturne in D-flat, Op. 27 No. 2"]
    assert lib.search("frederic")["total"] == 2 and lib.search("GYMNOPEDIE")["total"] == 1     # accents, case
    assert lib.search("rag")["items"][0]["title"] == "Maple Leaf Rag"
    first = lib.search(None, kind="lib")["items"]
    assert [i["title"] for i in first][:2] == ["Gymnopédie No. 1", "Nocturne in E-flat, Op. 9 No. 2"]   # rank desc
    item = first[0]
    assert item == {"id": "lib:classical-satie-gymnopedie-1", "title": "Gymnopédie No. 1", "composer": "Erik Satie",
                    "genre": "classical", "genre_label": "Classical", "kind": "lib", "editable": False,
                    "pinned": False}
    assert [i["title"] for i in lib.search(None, kind="lib", sort="title")["items"]][0] == "Gymnopédie No. 1"
    assert lib.search(None, kind="lib", sort="composer")["items"][0]["composer"] == "Frédéric Chopin"
    page = lib.search(None, kind="lib", limit=2, offset=2)
    assert page["total"] == 6 and len(page["items"]) == 2


def test_facets_count_with_the_other_filters(f):
    f.catalog({k: dict(v) for k, v in CATALOG.items()})
    (f.files / "Jazz - Bill Evans - Here's That Rainy Day.mid").write_bytes(piano_file())
    f.recording()
    lib = f.library()
    got = lib.search(None, genre="ragtime")
    assert got["total"] == 2
    g = {x["id"]: x["count"] for x in got["facets"]["genres"]}
    assert g["classical"] == 3 and g["ragtime"] == 2 and g["jazz"] == 2      # genres ignore the genre filter
    assert [c["name"] for c in got["facets"]["composers"]] == ["Scott Joplin"]  # composers keep it
    k = {x["id"]: x["count"] for x in got["facets"]["kinds"]}
    assert k == {"lib": 2, "user": 0, "rec": 0, "file": 0, "phrase": 0}
    got = lib.search(None, composer="frederic chopin")
    assert got["total"] == 2 and {x["name"] for x in got["facets"]["composers"]} >= {"Frédéric Chopin", "Erik Satie"}
    names = [x["name"] for x in lib.search(None)["facets"]["composers"]]
    assert names == ["Frédéric Chopin", "Duke Ellington", "Scott Joplin", "Erik Satie"]   # A-Z by surname
    mine = lib.search(None, kind="user,rec", sort="recent")
    assert [i["id"] for i in mine["items"]] == [f"rec:{REC_ID}"]
    rainy = lib.search("rainy day")["items"][0]
    assert rainy["kind"] == "file" and rainy["performer"] == "Bill Evans" and rainy["genre"] == "jazz"
    assert lib.search("everything")["items"][0]["kind"] == "phrase"


def test_no_catalog_degrades_and_says_so(f):
    lib = f.library()
    got = lib.search(None)
    assert got["total"] == 1 and any("hasn't been built" in p for p in got["problems"])   # just the phrase
    (f.lib / "catalog.json").write_text("{not json")
    lib2 = f.library(phrases_fn=lambda: {})
    assert lib2.search(None)["total"] == 0 and any("catalog.json" in p for p in lib2.problems())


# -- pins -------------------------------------------------------------------------------------------------------------
def test_pins_persist_and_unknown_pieces_cant_be_pinned(f):
    f.catalog({k: dict(v) for k, v in CATALOG.items()})
    lib = f.library()
    got = lib.pin("lib:ragtime-joplin-maple-leaf-rag", True)
    assert [p["id"] for p in got] == ["lib:ragtime-joplin-maple-leaf-rag"] and got[0]["pinned"] is True
    lib.pin("medley", True)
    assert json.loads((f.state / "library-pins.json").read_text()) == {"v": 1, "pins": [
        "lib:ragtime-joplin-maple-leaf-rag", "medley"]}
    again = f.library()
    assert [p["id"] for p in again.pins()] == ["lib:ragtime-joplin-maple-leaf-rag", "medley"]
    assert again.search(None, pinned=True)["total"] == 2
    with pytest.raises(LookupError):
        again.pin("lib:nope", True)
    assert [p["id"] for p in again.pin("medley", False)] == ["lib:ragtime-joplin-maple-leaf-rag"]


def test_a_new_pin_starts_the_prerender_detached_and_at_most_every_ten_seconds(f, monkeypatch):
    """1c's prerender (22 Oct): a NEW pin starts scripts/piano_prerender_all.sh --pins in its own session, output
    nowhere; a repin, an unpin or a pin within PRERENDER_MIN_S starts nothing; no prerender_cmd (tests) is off."""
    import subprocess
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: calls.append((cmd, kw)))
    f.catalog({k: dict(v) for k, v in CATALOG.items()})
    f.library().pin("lib:ragtime-joplin-maple-leaf-rag", True)
    assert calls == []                                              # off without prerender_cmd
    lib = f.library(prerender_cmd=("/x/piano_prerender_all.sh", "--pins"))
    lib.pin("lib:ragtime-joplin-the-entertainer", True)
    assert len(calls) == 1 and calls[0][0] == ["/x/piano_prerender_all.sh", "--pins"]
    assert calls[0][1]["start_new_session"] is True and calls[0][1]["stdout"] == subprocess.DEVNULL
    lib.pin("lib:ragtime-joplin-the-entertainer", True)             # already pinned
    lib.pin("lib:ragtime-joplin-the-entertainer", False)            # an unpin
    lib.pin("lib:classical-satie-gymnopedie-1", True)   # new, but within 10 s
    assert len(calls) == 1
    lib._prerender_at -= 11
    lib.pin("lib:jazz-ellington-nocturne", True)
    assert len(calls) == 2


# -- uploads ----------------------------------------------------------------------------------------------------------
def test_upload_cleans_keeps_and_dedupes(f):
    lib = f.library()
    data = smf([run_notes(0, 20, program=0), run_notes(9, 20)])
    item, rep = lib.add_upload(data, "my_song.mid", None, "Me", "Jazz")
    assert item["id"].startswith("user:me-my-song-") and item["title"] == "My song" and item["composer"] == "Me"
    assert item["genre"] == "jazz" and item["kind"] == "user" and item["editable"] is True and item["added_at"]
    assert rep["words"] == "Kept the piano; dropped the drums."
    uid = item["id"][5:]
    doc = json.loads((f.lib / "user.json").read_text())
    assert doc["items"][uid]["cleaned"] == {"kept_channels": [1], "dropped": ["drums"], "drums": True}
    assert doc["items"][uid]["origin"] == "upload" and lib.resolve(item["id"]) == (f.lib / "user" / f"{uid}.mid")
    again, _ = lib.add_upload(data, "my_song.mid", None, "Me", "jazz")
    assert again["id"] == item["id"] and len(json.loads((f.lib / "user.json").read_text())["items"]) == 1
    assert library.piece_title(item["id"], lib_dir=f.lib) == "My song"
    for bad, code in ((b"hello", "bad_midi"), (smf([run_notes(0, 3)]), "no_notes")):
        with pytest.raises(Rejected) as e:
            lib.add_upload(bad, "x.mid")
        assert e.value.code == code
    with pytest.raises(ValueError, match="bad_title"):
        lib.add_upload(data, "x.mid", "x" * 200)
    odd, _ = lib.add_upload(data, "y.mid", "Odd genre", None, "polka")
    assert "genre" not in odd                                      # an unknown genre is dropped, not refused


# -- edits and deletes ------------------------------------------------------------------------------------------------
def test_edit_and_delete_uploads_and_recordings(f):
    f.catalog({k: dict(v) for k, v in CATALOG.items()})
    f.recording()
    lib = f.library()
    item, _ = lib.add_upload(piano_file(), "a.mid", "A piece", "Someone", "pop")
    lib.pin(item["id"], True)
    lib.pin(f"rec:{REC_ID}", True)

    async def go():
        got = await lib.edit(item["id"], "Better title", None, "jazz", fields={"title", "composer", "genre"})
        assert got["title"] == "Better title" and "composer" not in got and got["genre"] == "jazz"
        got = await lib.edit(f"rec:{REC_ID}", "My take")                 # the recorder's rename
        assert got["title"] == "My take" and json.loads((f.recs / f"{REC_ID}.json").read_text())["title"] == "My take"
        with pytest.raises(ValueError, match="not_editable"):
            await lib.edit("lib:ragtime-joplin-maple-leaf-rag", "x")
        with pytest.raises(ValueError, match="bad_title"):
            await lib.edit(item["id"], "  ", fields={"title"})
        with pytest.raises(LookupError):
            await lib.edit("user:nope-123456", "x")
        assert (await lib.delete(item["id"])) == {"deleted": item["id"]}
        assert lib.item(item["id"]) is None and not lib.resolve(item["id"])
        assert (await lib.delete(item["id"]))["detail"] == "That piece was already gone."
        assert (await lib.delete(f"rec:{REC_ID}")) == {"deleted": f"rec:{REC_ID}"}
        assert not (f.recs / f"{REC_ID}.mid").exists() and lib.item(f"rec:{REC_ID}") is None
        assert (await lib.delete(f"rec:{REC_ID}"))["detail"] == "That recording was already gone."
        with pytest.raises(ValueError, match="not_editable"):
            await lib.delete("medley")
    asyncio.run(go())
    assert lib.pins() == [] and json.loads((f.state / "library-pins.json").read_text())["pins"] == []


def test_recordings_come_from_recordings_fn(f):
    rows = [{"id": REC_ID, "title": "From the recorder", "recorded_at": "2026-10-22T14:05:12+01:00",
             "duration_s": 3.0, "notes": 20}]
    lib = f.library(recordings_fn=lambda: rows)
    assert lib.item(f"rec:{REC_ID}")["title"] == "From the recorder"
    broken = f.library(recordings_fn=lambda: 1 / 0)
    assert broken.search(None, kind="rec")["total"] == 0           # degrade: none listed
    f.recording()
    assert f.library(recordings_fn=None).search(None, kind="rec")["total"] == 0     # None: no recordings


# -- the web --------------------------------------------------------------------------------------------------------------
class StubSource:
    def __init__(self, sid, label, rows=None, fail=None, data=None):
        self.id, self.label, self.rows, self.fail, self.data = sid, label, rows or [], fail, data
        self.fetched = []

    async def search(self, session, q):
        if self.fail == "slow":
            await asyncio.sleep(30)
        if self.fail:
            raise library.WebError(f"{self.label} didn't answer.")
        return [dict(r) for r in self.rows]

    async def fetch(self, session, ref):
        self.fetched.append(ref)
        return self.data, f"https://example.test/{ref.split(':', 1)[1]}.mid"


@contextlib.asynccontextmanager
async def no_session():
    yield None


def test_web_search_merges_piano_first_and_reports_failures(f, monkeypatch):
    monkeypatch.setattr(library, "WEB_TIMEOUT_S", 0.2)
    a = StubSource("a", "Alpha", [{"ref": "a:1", "title": "Band arrangement", "source_label": "Alpha", "piano": None},
                                  {"ref": "a:2", "title": "Solo", "source_label": "Alpha", "piano": True}])
    b = StubSource("b", "Beta", [{"ref": "b:1", "title": "Piano one", "source_label": "Beta", "piano": True,
                                  "composer": "S. Joplin", "detail": "for Piano"}])
    lib = f.library(web_sources=[a, b, StubSource("c", "Gamma", fail=True), StubSource("d", "Delta", fail="slow")],
                    session_factory=no_session)
    got = asyncio.run(lib.web_search("maple"))
    assert [r["ref"] for r in got["results"]] == ["a:2", "b:1", "a:1"]
    assert got["results"][1] == {"ref": "b:1", "title": "Piano one", "source_label": "Beta", "detail": "for Piano",
                                 "composer": "S. Joplin"}
    assert got["errors"] == [{"source_label": "Gamma", "words": "Gamma didn't answer."},
                             {"source_label": "Delta", "words": "Delta didn't answer."}]
    assert asyncio.run(lib.web_search("  ")) == {"results": [], "errors": []}


def test_web_import_uses_the_search_rows_words_and_cleans(f):
    src = StubSource("b", "Beta", [{"ref": "b:7", "title": "Maple Leaf Rag", "source_label": "Beta", "piano": True,
                                    "composer": "S. Joplin", "genre": "ragtime"}], data=piano_file(30))
    lib = f.library(web_sources=[src], session_factory=no_session)

    async def go():
        await lib.web_search("maple")
        item, rep = await lib.web_import("b:7")
        assert item["title"] == "Maple Leaf Rag" and item["composer"] == "S. Joplin" and item["genre"] == "ragtime"
        assert item["source"] == "Beta" and rep["notes"] == 30 and src.fetched == ["b:7"]
        meta = json.loads((f.lib / "user.json").read_text())["items"][item["id"][5:]]
        assert meta["origin"] == "web" and meta["source_url"] == "https://example.test/7.mid"
        with pytest.raises(ValueError, match="unknown_ref"):
            await lib.web_import("zz:1")
        src.data = b"not midi"
        with pytest.raises(Rejected):
            await lib.web_import("b:8", "x")
    asyncio.run(go())


MUTOPIA_PAGE = """<h2>Music Listing</h2><table class="outer-table"><tr><td>
<table class="table-bordered result-table">
<tr><td>Maple Leaf Rag</td><td>by S. Joplin (1868&ndash;1917)</td><td>&nbsp;</td><td>&nbsp;</td></tr><tr>
<td>for Piano</td><td>c. 1899</td><td>Jazz</td><td></td></tr><tr>
<td>Reproduction</td><td><a href="../legal.html">Public Domain</a></td><td><a href="piece-info.cgi?id=23">More</a></td>
<td>2011/11/13</td></tr><tr>
<td>Download: <a href="https://www.mutopiaproject.org/ftp/JoplinS/maple/maple.ly">.ly file</a></td>
<td><a href="https://www.mutopiaproject.org/ftp/JoplinS/maple/maple.mid">.mid file</a></td></tr>
</table>
<table class="table-bordered result-table">
<tr><td>Cantata 16</td><td>by J. S. Bach (1685&ndash;1750)</td><td></td><td></td></tr><tr>
<td>for Voice, Strings and Basso Continuo</td><td>1726</td><td>Baroque</td><td></td></tr><tr>
<td>x</td><td>x</td><td>x</td><td>x</td></tr><tr>
<td class="zipped"><a href="https://www.mutopiaproject.org/ftp/BachJS/BWV16/c16/c16-mids.zip">.mid files</a></td></tr>
</table></table>"""


def test_mutopia_parses_its_result_tables():
    rows = library.Mutopia().parse(MUTOPIA_PAGE)
    assert rows[0] == {"ref": "mutopia:JoplinS/maple/maple.mid", "title": "Maple Leaf Rag",
                       "source_label": "Mutopia Project", "detail": "S. Joplin · for Piano", "piano": True,
                       "composer": "S. Joplin", "genre": "ragtime"}
    assert rows[1]["ref"] == "mutopia:BachJS/BWV16/c16/c16-mids.zip" and rows[1]["piano"] is False
    assert rows[1]["genre"] == "classical" and "first part of several" in rows[1]["detail"]
    assert library.Mutopia().parse("<html>Sorry, no matches were found</html>") == []


class FakeResp:
    def __init__(self, status=200, body=b"", js=None):
        self.status, self.body, self.js = status, body, js
        self.content_length = len(body)
        self.content = self

    async def iter_chunked(self, n):
        yield self.body

    async def json(self, content_type=None):
        return self.js

    async def text(self, errors=None):
        return self.body.decode()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class FakeSession:
    def __init__(self, routes):
        self.routes, self.seen = routes, []

    def get(self, url, params=None, allow_redirects=True):
        self.seen.append((url, dict(params or {})))
        return self.routes(url, params or {})


def test_bitmidi_search_and_fetch_shapes():
    js = {"result": {"results": [{"id": 112561, "name": "maple leaf rag piano.mid", "plays": 1234,
                                  "downloadUrl": "/uploads/112561.mid"}, {"bad": 1}], "total": 1}}
    s = FakeSession(lambda url, p: FakeResp(js=js) if url.endswith("/search") else FakeResp(body=piano_file()))
    bm = library.BitMidi()
    rows = asyncio.run(bm.search(s, "maple"))
    assert rows == [{"ref": "bitmidi:112561", "title": "Maple leaf rag piano", "source_label": "BitMidi",
                     "detail": "1,234 plays", "piano": True}]
    assert s.seen[0] == ("https://bitmidi.com/api/midi/search", {"q": "maple", "page": "0", "pageSize": "20"})
    data, url = asyncio.run(bm.fetch(s, "bitmidi:112561"))
    assert url == "https://bitmidi.com/uploads/112561.mid" and data[:4] == b"MThd"
    down = FakeSession(lambda url, p: FakeResp(status=502, body=b"error code: 502"))
    with pytest.raises(library.WebError, match="BitMidi didn't answer"):
        asyncio.run(bm.search(down, "x"))
    with pytest.raises(library.WebError):
        asyncio.run(bm.fetch(s, "bitmidi:../../x"))
    big = FakeSession(lambda url, p: FakeResp(body=b"x" * (library.MAX_BYTES + 1)))
    with pytest.raises(Rejected, match="too_big"):
        asyncio.run(bm.fetch(big, "bitmidi:1"))


def test_mutopia_fetch_takes_the_first_part_of_a_zip():
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("c16-10.mid", piano_file(40))
        z.writestr("c16-2.mid", piano_file(30))
        z.writestr("readme.txt", "x")
    s = FakeSession(lambda url, p: FakeResp(body=buf.getvalue()))
    data, url = asyncio.run(library.Mutopia().fetch(s, "mutopia:BachJS/BWV16/c16/c16-mids.zip"))
    assert url == "https://www.mutopiaproject.org/ftp/BachJS/BWV16/c16/c16-mids.zip"
    assert clean_midi(data)[1]["notes"] == 30                      # c16-2 before c16-10
    with pytest.raises(library.WebError):
        asyncio.run(library.Mutopia().fetch(s, "mutopia:../etc/passwd.mid"))


# -- the routes, in-process -------------------------------------------------------------------------------------------
def test_routes_in_process(f):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    f.catalog({k: dict(v) for k, v in CATALOG.items()})
    f.recording()
    src = StubSource("b", "Beta", [{"ref": "b:7", "title": "Maple", "source_label": "Beta", "piano": True}],
                     data=piano_file(30))
    lib = f.library(web_sources=[src], session_factory=no_session)

    async def go():
        app = web.Application(client_max_size=64 << 10)               # the bus's own cap
        lib.routes(app)
        async with TestClient(TestServer(app, host="127.0.0.1", port=0)) as cl:
            r = await cl.get("/api/v1/library", params={"q": "nocturne", "limit": "2"})
            b = await r.json()
            assert r.status == 200 and b["total"] == 3 and len(b["items"]) == 2 and set(b["facets"]) == {
                "genres", "composers", "kinds"} and b["items"][0]["title"] == "Nocturne"
            r = await cl.get("/api/v1/library/item/lib:ragtime-joplin-maple-leaf-rag")
            assert r.status == 200 and (await r.json())["composer"] == "Scott Joplin"
            r = await cl.get("/api/v1/library/item/lib:nope")
            assert r.status == 404 and (await r.json())["error"] == "unknown_piece"
            r = await cl.get("/api/v1/library/item/lib:ragtime-joplin-maple-leaf-rag.mid")
            assert r.status == 200 and (await r.read())[:4] == b"MThd"
            assert 'filename="Maple Leaf Rag.mid"' in r.headers["Content-Disposition"]
            r = await cl.put("/api/v1/library/pins/lib:ragtime-joplin-maple-leaf-rag")
            assert [p["id"] for p in (await r.json())["pins"]] == ["lib:ragtime-joplin-maple-leaf-rag"]
            assert (await (await cl.get("/api/v1/library/pins")).json())["pins"][0]["pinned"] is True
            r = await cl.delete("/api/v1/library/pins/lib:ragtime-joplin-maple-leaf-rag")
            assert (await r.json()) == {"pins": []}
            big = smf([run_notes(0, 8000)])                               # > 64 KB: read past the bus's cap
            assert 64 << 10 < len(big) < library.MAX_BYTES
            r = await cl.post("/api/v1/library/upload", params={"filename": "big_one.mid", "genre": "pop"}, data=big)
            b = await r.json()
            assert r.status == 200 and b["item"]["title"] == "Big one" and b["cleaned"] == {"words": None}
            uid = b["item"]["id"]
            r = await cl.post("/api/v1/library/upload", data=b"x" * (library.MAX_BYTES + 10))
            assert r.status == 400 and (await r.json())["error"] == "too_big"
            r = await cl.post("/api/v1/library/upload", params={"filename": "x.mid"}, data=b"nope")
            assert r.status == 400 and (await r.json()) == {"error": "bad_midi", "detail": "That isn't a MIDI file."}
            r = await cl.patch(f"/api/v1/library/item/{uid}", json={"title": "Renamed", "composer": None})
            assert r.status == 200 and (await r.json())["title"] == "Renamed"
            r = await cl.patch(f"/api/v1/library/item/rec:{REC_ID}", json={"title": "Take two"})
            assert r.status == 200 and (await r.json())["title"] == "Take two"
            r = await cl.patch("/api/v1/library/item/lib:ragtime-joplin-maple-leaf-rag", json={"title": "x"})
            assert r.status == 400 and (await r.json())["error"] == "not_editable"
            r = await cl.get("/api/v1/library/web", params={"q": "maple"})
            assert (await r.json())["results"][0]["ref"] == "b:7"
            r = await cl.post("/api/v1/library/web/import", json={"ref": "b:7"})
            b = await r.json()
            assert r.status == 200 and b["item"]["title"] == "Maple" and "words" in b["cleaned"]
            r = await cl.post("/api/v1/library/web/import", json={})
            assert r.status == 400 and (await r.json())["error"] == "bad_request"
            r = await cl.delete(f"/api/v1/library/item/{uid}")
            assert (await r.json()) == {"deleted": uid}
            r = await cl.delete(f"/api/v1/library/item/rec:{REC_ID}")
            assert (await r.json()) == {"deleted": f"rec:{REC_ID}"}
            r = await cl.get("/api/v1/library", params={"kind": "user,rec", "sort": "recent"})
            assert [i["id"] for i in (await r.json())["items"]] == [b["item"]["id"]]
    asyncio.run(go())


# -- sources.py: lib: and user: pieces play like rec: ---------------------------------------------------------------------
def test_sources_plays_lib_and_user_pieces_by_absolute_path_once(tmp_path, monkeypatch):
    from show_service.tests import test_sources_piano as tsp
    (tmp_path / "piano").mkdir()
    (tmp_path / "music").mkdir()
    (tmp_path / "show_sender.py").write_text(tsp.NEW_SENDER)
    libdir = tmp_path / "midi-library"
    (libdir / "user").mkdir(parents=True)
    (libdir / "files" / "ragtime").mkdir(parents=True)
    (libdir / "files" / "ragtime" / "ragtime-joplin-maple-leaf-rag.mid").write_bytes(piano_file())
    (libdir / "catalog.json").write_text(json.dumps({"v": 1, "items": {"ragtime-joplin-maple-leaf-rag": {
        "id": "ragtime-joplin-maple-leaf-rag", "file": "files/ragtime/ragtime-joplin-maple-leaf-rag.mid",
        "title": "Maple Leaf Rag", "composer": "Scott Joplin", "genre": "ragtime"}}}))
    (libdir / "user" / "mine-abc123.mid").write_bytes(piano_file())
    monkeypatch.setattr(library, "LIB_DIR", libdir)
    r = tsp.make(tmp_path)
    try:
        assert all(not p["id"].startswith(("lib:", "user:")) for p in r.src.pieces()["piano"])
        st = r.src.start_test("piano", "lib:ragtime-joplin-maple-leaf-rag")
        assert st["label"] == "Maple Leaf Rag" and st["piece"] == "lib:ragtime-joplin-maple-leaf-rag" and st["once"]
        assert r.popen.argv[:4] == [tsp.PY, str(tmp_path / "show_sender.py"), "--test-piano",
                                    str((libdir / "files/ragtime/ragtime-joplin-maple-leaf-rag.mid").resolve())]
        assert r.popen.argv[-1] == "--once"
        r.src.start_test("piano", "user:mine-abc123", once=True)
        r.src.tick(0.0)
        assert r.popen.argv[3] == str((libdir / "user" / "mine-abc123.mid").resolve())
        assert r.src.status()["piano"]["label"] == "Mine-abc123"            # no user.json entry: the file's name
        for bad in ("lib:nope", "user:../../x", "lib:"):
            with pytest.raises(ValueError, match="unknown_piece"):
                r.src.start_test("piano", bad)
    finally:
        r.src.stop()
