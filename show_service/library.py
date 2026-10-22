"""The Music page's library (8f, 22 Oct; docs: the music SPEC §1, §2, §6): every piece the test piano can play, under
one id namespace, searchable, pinnable, with uploads and imports from the web.

**Piece ids** (used everywhere: the library, playlists, the player, pins, sources.py):
- ``lib:<id>``   a catalogue item: LIB_DIR / catalog.json ``items[id].file`` (tools/music_library builds it);
- ``user:<id>``  an upload or a web import: LIB_DIR / user / <id>.mid (+ user.json's metadata);
- ``rec:<id>``   a Room-page recording (recorder.py's folder; played by absolute path);
- ``file:<name>.mid``  the curated config/live/test-piano folder;
- a bare phrase id: piano_replay's built-in test patterns (no file).

**No side effects at import, no package imports at module level**: sources.py (and scripts) load this file by path for
:func:`resolve` (and reads STATE_DIR/library-pins.json). aiohttp is imported where it is used (no mido); show.yaml
(``tests.piano.pieces`` / ``tests.piano.recordings``) is read only when PyYAML is there, else the defaults hold.

**Cleaning** (:func:`clean_midi`, the same for the build and for every import; a small SMF reader/writer of its own,
about 20x faster than mido's checked messages): piano_render and piano_replay merge
every channel into one piano, so channel 10 (drums) always goes; when any note is played under a piano program (GM 0-7;
a channel without a program change is GM's program 0) only those notes stay, else every melodic channel stays (and the
words say so); kept notes merge to channel 1 (a re-strike sounds, the key is released when every source has let go);
CC 64/66/67 of the kept parts and tempo / time-signature / key-signature meta stay; other CCs, program changes and sysex
go. Written as SMF type 0 at the source PPQ. Rejected: not MIDI, no notes, < MIN_NOTES notes, > MAX_SECONDS, > MAX_BYTES.

**Library** (thread-safe; the routes run its work in a worker thread so show-bus never blocks): catalog.json and
user.json re-read when their mtime changes, pins in STATE_DIR (``{"v":1,"pins":[ids]}``), recordings from an injected
``recordings_fn`` (the recorder's list) and their rename/delete through an injected ``recorder_ops`` (the recorder's own
methods, on show-brain). Every JSON write is atomic (tmp + fsync + os.replace); a missing or bad file degrades to empty
and says so in ``problems``.

**Web sources** (WEB_SOURCES: ``label``, async ``search(session, q)``, async ``fetch(session, ref)``): BitMidi (its
JSON search; checked 22 Oct: /api/midi/all and /uploads/<id>.mid answer, /api/midi/search answered 502 from its
origin all day, so it reports "BitMidi didn't answer" until it is back) and the Mutopia Project (its search page,
parsed; verified 22 Oct with a real search and a real download). Each is searched in parallel with WEB_TIMEOUT_S, a
browser User-Agent, downloads capped at MAX_BYTES. A plain http(s) URL to a .mid imports too.

CLI: ``python -m show_service.library resolve <id> | stats | search <q>``.
"""
from __future__ import annotations

import asyncio
import collections
import datetime
import hashlib
import html
import inspect
import io
import json
import logging
import os
import pathlib
import re
import struct
import sys
import threading
import time
import unicodedata
import zipfile
from typing import Any, Callable, Optional

log = logging.getLogger("show.library")

# -- places (mirrors of contract.py / recorder.py: this file is loaded by path, see the docstring) ---------------------
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
LIB_DIR = pathlib.Path.home() / ".local" / "share" / "screen-machine" / "midi-library"
STATE_DIR = pathlib.Path.home() / ".local" / "state" / "screen-machine" / "show"        # contract.STATE_DIR
TEST_PIANO_DIR = REPO_ROOT / "config" / "live" / "test-piano"                          # contract.TEST_PIANO_DIR
RECORDINGS_DIR = pathlib.Path.home() / ".local" / "share" / "screen-machine" / "piano-recordings"   # recorder's
SHOW_FILE = REPO_ROOT / "config" / "room" / "show.yaml"                                # contract.SHOW_FILE
PIANO_REPLAY = REPO_ROOT / "stream_service" / "piano_replay.py"                        # PHRASE_LABELS
CATALOG = "catalog.json"
USER_JSON = "user.json"
USER_DIR = "user"
PINS_FILE = "library-pins.json"

LIB_PREFIX, USER_PREFIX, REC_PREFIX, FILE_PREFIX = "lib:", "user:", "rec:", "file:"
MIDI_EXTS = (".mid", ".midi")
SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?$")
REC_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{4}$")                            # recorder.ID_RE
PHRASE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")

GENRES: tuple[tuple[str, str], ...] = (
    ("classical", "Classical"), ("ragtime", "Ragtime & stride"), ("jazz", "Jazz"), ("standards", "Standards"),
    ("pop", "Pop & rock"), ("film", "Film, TV & games"))
GENRE_LABEL = dict(GENRES)
KINDS: tuple[tuple[str, str], ...] = (
    ("lib", "Library"), ("user", "Added by you"), ("rec", "Recordings"), ("file", "Test tracks"),
    ("phrase", "Test patterns"))
KIND_LABEL = dict(KINDS)
SORTS = ("rank", "title", "composer", "recent")

MAX_BYTES = 1_000_000                  # the room proxy's body cap
MIN_NOTES = 16
MAX_SECONDS = 45 * 60.0
TITLE_MAX = 120
LIMIT_MAX = 200
WEB_TIMEOUT_S = 6.0
WEB_SEEN_MAX = 500
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
RELOAD_S = 2.0                         # stat() the JSON files at most this often
PIANO_PROGRAMS = range(0, 8)
DRUM_CHANNEL = 9
PEDAL_CCS = (64, 66, 67)
KEEP_META_TYPES = (0x51, 0x58, 0x59)          # tempo, time signature, key signature
#: GM program families (program // 8) in page words
FAMILY_WORDS = ("piano", "bells", "organ", "guitar", "bass", "strings", "choir and ensemble", "brass", "reeds",
                "flutes", "synth lead", "synth pads", "synth effects", "folk instruments", "percussion",
                "sound effects")
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


class Rejected(ValueError):
    """A file the library won't take: ``code`` (bad_midi | too_big | no_notes) and page ``words``."""

    def __init__(self, code: str, words: str) -> None:
        super().__init__(f"{code}: {words}")
        self.code, self.words = code, words


# -- small pure helpers ---------------------------------------------------------------------------------------------
def norm(text: Any) -> str:
    """Case- and accent-insensitive form: NFKD, combining marks dropped, casefolded, apostrophes removed."""
    s = unicodedata.normalize("NFKD", str(text or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.casefold().replace("'", "").replace("’", "")


def tokens(text: Any) -> list[str]:
    return [t for t in re.findall(r"\w+", norm(text)) if t != "_"]


def slug(text: Any, limit: int = 60) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", norm(text)).strip("-")
    return s[:limit].strip("-")


def split_id(piece: Any) -> Optional[tuple[str, str]]:
    """``piece`` -> (kind, rest) when it is a well-formed piece id, else None (no file is touched)."""
    if not isinstance(piece, str) or not piece:
        return None
    for prefix, kind, ok in ((LIB_PREFIX, "lib", SLUG_RE), (USER_PREFIX, "user", SLUG_RE),
                             (REC_PREFIX, "rec", REC_ID_RE)):
        if piece.startswith(prefix):
            rest = piece[len(prefix):]
            return (kind, rest) if ok.match(rest) else None
    if piece.startswith(FILE_PREFIX):
        name = piece[len(FILE_PREFIX):]
        if (not name or "/" in name or "\\" in name or name.startswith(".") or "\x00" in name
                or not name.lower().endswith(MIDI_EXTS)):
            return None
        return "file", name
    if PHRASE_RE.match(piece):
        return "phrase", piece
    return None


def kind_of(piece: Any) -> Optional[str]:
    got = split_id(piece)
    return got[0] if got else None


def clean_text(value: Any, what: str = "title", limit: int = TITLE_MAX) -> Optional[str]:
    """Text as a page sent it, tidied; None when empty. ValueError("bad_<what>: …") when it isn't text or too long."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"bad_{what}: a {what} is text")
    t = " ".join(_CTRL.sub(" ", value).split())
    if len(t) > limit:
        raise ValueError(f"bad_{what}: a {what} has at most {limit} characters")
    return t or None


def clean_genre(value: Any) -> Optional[str]:
    """A genre id, or None (an unknown one is dropped: degrade, never refuse)."""
    if isinstance(value, str) and value.strip().lower() in GENRE_LABEL:
        return value.strip().lower()
    if isinstance(value, str):                         # a label works too ("Jazz")
        for gid, label in GENRES:
            if norm(label) == norm(value.strip()):
                return gid
    return None


def title_from_filename(name: Any) -> str:
    stem = pathlib.PurePath(str(name or "")).name
    for ext in MIDI_EXTS:
        if stem.lower().endswith(ext):
            stem = stem[: -len(ext)]
    t = " ".join(stem.replace("_", " ").split())
    return (t[:1].upper() + t[1:])[:TITLE_MAX] if t else "Untitled"


def surname_key(name: Any) -> str:
    """"Frédéric Chopin" -> "chopin frederic" (an A-Z composer list)."""
    words = tokens(name)
    return " ".join(words[-1:] + words[:-1]) if words else ""


def _iso_ts(value: Any) -> Optional[float]:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.timestamp()


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _num(v: Any) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v else None


def atomic_write(path: pathlib.Path, data: bytes) -> None:
    """tmp in the same folder, flushed and fsynced, then os.replace."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def write_json(path: pathlib.Path, doc: Any) -> None:
    atomic_write(path, (json.dumps(doc, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))


def _stamp(path: pathlib.Path) -> Any:
    try:
        st = pathlib.Path(path).stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


# -- files read by stamp (module-level, thread-safe) ----------------------------------------------------------------
class _JsonFile:
    """A JSON file re-read when its mtime/size changes (stat at most every RELOAD_S). ``get()`` -> (doc | None, error |
    None); a missing file is (None, None)."""

    def __init__(self, path_fn: Callable[[], pathlib.Path]) -> None:
        self.path_fn = path_fn
        self.lock = threading.Lock()
        self.path: Optional[pathlib.Path] = None
        self.stamp: Any = None
        self.doc: Any = None
        self.error: Optional[str] = None
        self.checked = -1e18
        self.gen = 0

    def get(self, force: bool = False) -> tuple[Any, Optional[str]]:
        with self.lock:
            path = pathlib.Path(self.path_fn())
            now = time.monotonic()
            if not force and path == self.path and now - self.checked < RELOAD_S:
                return self.doc, self.error
            self.checked = now
            stamp = _stamp(path)
            if path == self.path and stamp == self.stamp:
                return self.doc, self.error
            self.path, self.stamp, self.gen = path, stamp, self.gen + 1
            if stamp is None:
                self.doc, self.error = None, None
                return None, None
            try:
                self.doc, self.error = json.loads(path.read_text(encoding="utf-8")), None
            except (OSError, ValueError) as e:
                self.doc, self.error = None, f"{path.name}: not read ({type(e).__name__}: {e})"
            return self.doc, self.error


def _show_dirs_read() -> dict:
    try:
        import yaml                                    # show_service's venv has it; another venv may not
    except Exception:                                  # noqa: BLE001
        return {}
    try:
        doc = yaml.safe_load(SHOW_FILE.read_text(encoding="utf-8"))
    except Exception:                                  # noqa: BLE001
        return {}
    tests = doc.get("tests") if isinstance(doc, dict) else None
    piano = tests.get("piano") if isinstance(tests, dict) else None
    return piano if isinstance(piano, dict) else {}


_show_cache: dict[str, Any] = {"stamp": None, "checked": -1e18, "piano": {}}
_show_lock = threading.Lock()


def _show_piano() -> dict:
    with _show_lock:
        now = time.monotonic()
        if now - _show_cache["checked"] >= RELOAD_S:
            _show_cache["checked"] = now
            stamp = _stamp(SHOW_FILE)
            if stamp != _show_cache["stamp"]:
                _show_cache["stamp"], _show_cache["piano"] = stamp, _show_dirs_read()
        return _show_cache["piano"]


def _dir_from(raw: Any, default: pathlib.Path) -> pathlib.Path:
    if not isinstance(raw, str) or not raw.strip():
        return pathlib.Path(default)
    p = pathlib.Path(raw.strip()).expanduser()
    return p if p.is_absolute() else REPO_ROOT / p


def file_dir() -> pathlib.Path:
    """The test piano's folder: show.yaml ``tests.piano.pieces``, else TEST_PIANO_DIR."""
    return _dir_from(_show_piano().get("pieces"), TEST_PIANO_DIR)


def rec_dir() -> pathlib.Path:
    """The recordings folder: show.yaml ``tests.piano.recordings``, else RECORDINGS_DIR (recorder.recordings_folder)."""
    return _dir_from(_show_piano().get("recordings"), RECORDINGS_DIR)


_catalogs: dict[str, _JsonFile] = {}
_users: dict[str, _JsonFile] = {}
_files_lock = threading.Lock()


def _catalog_file(lib_dir: pathlib.Path) -> _JsonFile:
    with _files_lock:
        key = str(lib_dir)
        if key not in _catalogs:
            _catalogs[key] = _JsonFile(lambda d=pathlib.Path(lib_dir): d / CATALOG)
        return _catalogs[key]


def _user_file(lib_dir: pathlib.Path) -> _JsonFile:
    with _files_lock:
        key = str(lib_dir)
        if key not in _users:
            _users[key] = _JsonFile(lambda d=pathlib.Path(lib_dir): d / USER_JSON)
        return _users[key]


def _items_of(doc: Any) -> dict:
    items = doc.get("items") if isinstance(doc, dict) else None
    return items if isinstance(items, dict) else {}


def _inside(path: pathlib.Path, root: pathlib.Path) -> bool:
    try:
        return path.resolve().is_relative_to(root.resolve())
    except OSError:
        return False


def _rec_meta(folder: pathlib.Path, rid: str) -> Optional[dict]:
    """recorder.read_meta: the recording's JSON while its .mid is there."""
    if not REC_ID_RE.match(rid or ""):
        return None
    try:
        meta = json.loads((folder / f"{rid}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict) or not (folder / f"{rid}.mid").is_file():
        return None
    return meta


# -- resolve (module level: sources.py and scripts) -------------------------------------------------------------
def resolve(piece_id: str, *, lib_dir: Optional[pathlib.Path] = None, files: Optional[pathlib.Path] = None,
            recs: Optional[pathlib.Path] = None) -> Optional[pathlib.Path]:
    """The absolute .mid a piece id plays, or None (unknown, missing, or a built-in phrase: no file). Never raises."""
    try:
        got = split_id(piece_id)
        if got is None:
            return None
        kind, rest = got
        base = pathlib.Path(lib_dir) if lib_dir is not None else LIB_DIR
        if kind == "lib":
            doc, _ = _catalog_file(base).get()
            entry = _items_of(doc).get(rest)
            f = entry.get("file") if isinstance(entry, dict) else None
            if not isinstance(f, str) or not f:
                return None
            p = base / f
            return p.resolve() if p.suffix.lower() in MIDI_EXTS and p.is_file() and _inside(p, base) else None
        if kind == "user":
            p = base / USER_DIR / f"{rest}.mid"
            return p.resolve() if p.is_file() else None
        if kind == "rec":
            folder = pathlib.Path(recs) if recs is not None else rec_dir()
            return (folder / f"{rest}.mid").resolve() if _rec_meta(folder, rest) is not None else None
        if kind == "file":
            folder = pathlib.Path(files) if files is not None else file_dir()
            p = folder / rest
            return p.resolve() if p.is_file() else None
    except Exception:                                  # noqa: BLE001  never raises into a caller
        log.debug("resolve %r failed", piece_id, exc_info=True)
    return None


def piece_title(piece_id: str, *, lib_dir: Optional[pathlib.Path] = None) -> Optional[str]:
    """A lib:/user: piece's title (sources.py's label), else None."""
    got = split_id(piece_id)
    if got is None or got[0] not in ("lib", "user"):
        return None
    base = pathlib.Path(lib_dir) if lib_dir is not None else LIB_DIR
    doc, _ = (_catalog_file(base) if got[0] == "lib" else _user_file(base)).get()
    entry = _items_of(doc).get(got[1])
    t = entry.get("title") if isinstance(entry, dict) else None
    return t if isinstance(t, str) and t.strip() else None


# -- MIDI cleaning ------------------------------------------------------------------------------------------------
def _unwrap(data: bytes) -> bytes:
    """An RMID (RIFF) wrapper or a few bytes of junk before MThd: the SMF inside."""
    if data[:4] == b"RIFF" and data[8:12] == b"RMID":
        i = 12
        while i + 8 <= len(data):
            cid, size = data[i:i + 4], int.from_bytes(data[i + 4:i + 8], "little")
            if cid == b"data":
                return data[i + 8:i + 8 + size]
            i += 8 + size + (size & 1)
    k = data.find(b"MThd", 0, 512)
    return data[k:] if k > 0 else data


def _family_words(families: set) -> list[str]:
    return [FAMILY_WORDS[f] for f in sorted(families) if 0 <= f < len(FAMILY_WORDS)]


def _and(words: list[str]) -> str:
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def _vlq_read(b: bytes, i: int) -> tuple[int, int]:
    v = 0
    for _ in range(4):
        c = b[i]
        i += 1
        v = (v << 7) | (c & 0x7F)
        if not c & 0x80:
            return v, i
    raise ValueError("bad variable-length number")


def _vlq(n: int) -> bytes:
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append(0x80 | (n & 0x7F))
        n >>= 7
    return bytes(reversed(out))


def parse_smf(data: bytes) -> tuple[int, int, list[tuple]]:
    """A standard MIDI file -> (format, ticks per quarter, events in time order). An event is ``(tick, order, status,
    d1, d2)`` for a channel message or ``(tick, order, 0xFF, meta type, payload bytes)``; sysex is skipped. SMPTE
    timing becomes 120 bpm with the matching PPQ (tempo metas are then ignored by the caller: ``ppq < 0`` marks it).
    Tolerant: a truncated track ends where it ends. ValueError when it isn't an SMF."""
    if len(data) < 14 or data[:4] != b"MThd":
        raise ValueError("not a MIDI file")
    hlen = int.from_bytes(data[4:8], "big")
    fmt, _ntrk, div = struct.unpack(">HHH", data[8:14])
    if div & 0x8000:                                   # SMPTE: -fps, ticks per frame
        fps = 256 - (div >> 8)
        ppq = -max(1, (fps * (div & 0xFF)) // 2)       # 2 beats a second at 120 bpm
    else:
        ppq = div or 480
    events: list[tuple] = []
    order = 0
    i = 8 + hlen
    while i + 8 <= len(data):
        cid, ln = data[i:i + 4], int.from_bytes(data[i + 4:i + 8], "big")
        body = data[i + 8:i + 8 + ln]
        i += 8 + ln
        if cid != b"MTrk":
            continue
        t, j, running = 0, 0, None
        n = len(body)
        try:
            while j < n:
                delta, j = _vlq_read(body, j)
                t += delta
                st = body[j]
                if st == 0xFF:
                    typ = body[j + 1]
                    ln2, j = _vlq_read(body, j + 2)
                    events.append((t, order, 0xFF, typ, body[j:j + ln2]))
                    j += ln2
                    order += 1
                    if typ == 0x2F:
                        break
                    continue
                if st in (0xF0, 0xF7):
                    ln2, j = _vlq_read(body, j + 1)
                    j += ln2
                    running = None
                    continue
                if st > 0xF0:                          # a stray real-time byte
                    j += 1
                    continue
                if st & 0x80:
                    running = st
                    j += 1
                elif running is None:
                    raise ValueError("data byte without a status")
                else:
                    st = running
                hi = st & 0xF0
                if hi in (0xC0, 0xD0):
                    events.append((t, order, st, body[j] & 0x7F, 0))
                    j += 1
                else:
                    events.append((t, order, st, body[j] & 0x7F, body[j + 1] & 0x7F))
                    j += 2
                order += 1
        except IndexError:                             # truncated: keep what was read
            pass
    events.sort(key=lambda e: (e[0], e[1]))
    return fmt, ppq, events


def _seconds(events: list[tuple], ppq: int) -> float:
    """The time of the last event, through the tempo map (SMPTE: ppq < 0, a fixed rate)."""
    if ppq < 0:
        return (events[-1][0] if events else 0) / (-ppq * 2.0)
    tempo, last_t, sec = 500_000, 0, 0.0
    for e in events:
        sec += (e[0] - last_t) * tempo / (ppq * 1e6)
        last_t = e[0]
        if e[2] == 0xFF and e[3] == 0x51 and len(e[4]) == 3:
            tempo = int.from_bytes(e[4], "big") or 500_000
    return sec


def clean_midi(data: bytes, title: Optional[str] = None) -> tuple[bytes, dict]:
    """(cleaned SMF type 0 bytes, report) — see the module docstring. Raises :class:`Rejected`. The report:
    ``{"kept_channels": [1-16], "dropped": [words], "drums": bool, "piano": bool, "words": str | None, "notes": n,
    "duration_s": s, "ppq": n}``."""
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise Rejected("bad_midi", "That file is empty.")
    if len(data) > MAX_BYTES:
        raise Rejected("too_big", "That file is over 1 MB; MIDI files are usually far smaller.")
    raw = _unwrap(bytes(data))
    if raw[:4] != b"MThd":
        raise Rejected("bad_midi", "That isn't a MIDI file.")
    try:
        fmt, ppq, events = parse_smf(raw)
    except Exception as e:                             # noqa: BLE001  any parse failure is the file's
        raise Rejected("bad_midi", "That MIDI file couldn't be read (it may be damaged).") from e
    if fmt == 2:
        raise Rejected("bad_midi", "That's a pattern-style MIDI file (type 2), which can't play as one piece.")
    smpte = ppq < 0
    out_ppq = -ppq if smpte else ppq
    # pass 1: who plays what (a channel's program when each note starts; GM's program 0 until a change)
    prog = [0] * 16
    total_notes = 0
    piano_present = False
    families: set = set()
    drums = False
    for e in events:
        st = e[2]
        if st == 0xFF:
            continue
        hi, ch = st & 0xF0, st & 0x0F
        if hi == 0xC0:
            prog[ch] = e[3]
        elif hi == 0x90 and e[4] > 0:
            total_notes += 1
            if ch == DRUM_CHANNEL:
                drums = True
            elif prog[ch] in PIANO_PROGRAMS:
                piano_present = True
            else:
                families.add(prog[ch] // 8)
    if total_notes == 0:
        raise Rejected("no_notes", "There are no notes in that file.")
    # pass 2: what stays, merged to channel 1
    prog = [0] * 16
    kept: dict[tuple[int, int], int] = {}
    held = [0] * 128
    out: list[tuple[int, bytes]] = []
    channels: set = set()
    notes = 0
    last = 0                                           # the last kept note or pedal: the piece ends there
    tempo, last_t, sec, end_sec = 500_000, 0, 0.0, 0.0
    for e in events:
        t, st = e[0], e[2]
        if smpte:
            sec = t / (out_ppq * 2.0)
        else:
            sec += (t - last_t) * tempo / (ppq * 1e6)
            last_t = t
        if st == 0xFF:
            typ, payload = e[3], e[4]
            if typ in KEEP_META_TYPES and not (smpte and typ == 0x51):
                out.append((t, b"\xff" + bytes((typ,)) + _vlq(len(payload)) + payload))
                if typ == 0x51 and len(payload) == 3:
                    tempo = int.from_bytes(payload, "big") or 500_000
            continue
        hi, ch = st & 0xF0, st & 0x0F
        if hi == 0xC0:
            prog[ch] = e[3]
            continue
        if ch == DRUM_CHANNEL:
            continue
        keep = (not piano_present) or prog[ch] in PIANO_PROGRAMS
        note = e[3]
        if hi == 0x90 and e[4] > 0:
            if not keep:
                continue
            kept[(ch, note)] = kept.get((ch, note), 0) + 1
            held[note] += 1
            out.append((t, bytes((0x90, note, e[4]))))
            channels.add(ch + 1)
            notes += 1
            last, end_sec = t, sec
        elif hi in (0x80, 0x90):
            if kept.get((ch, note), 0) > 0:
                kept[(ch, note)] -= 1
                held[note] -= 1
                if held[note] == 0:
                    out.append((t, bytes((0x80, note, e[4] if hi == 0x80 else 0))))
                    last, end_sec = t, sec
        elif hi == 0xB0 and note in PEDAL_CCS and keep:
            out.append((t, bytes((0xB0, note, e[4]))))
            last, end_sec = t, sec
    if notes == 0:
        raise Rejected("no_notes", "It only has drums: nothing for the piano to play.")
    if notes < MIN_NOTES:
        raise Rejected("no_notes", f"It has only {notes} notes: too few to play as a piece.")
    if end_sec > MAX_SECONDS:
        raise Rejected("too_big", "It's longer than 45 minutes.")
    for n in range(128):                               # a note never released: released at the end
        if held[n] > 0:
            out.append((last, bytes((0x80, n, 0))))
    trk = bytearray()
    if smpte:                                          # SMPTE timing: written as 120 bpm at the matching PPQ
        trk += b"\x00\xff\x51\x03" + (500_000).to_bytes(3, "big")
    if title:
        name = title.encode("latin-1", "replace")[:255]
        trk += b"\x00\xff\x03" + _vlq(len(name)) + name
    prev = 0
    for t, raw_ev in out:
        trk += _vlq(t - prev) + raw_ev
        prev = t
    trk += _vlq(max(0, last - prev)) + b"\xff\x2f\x00"
    body = (b"MThd" + (6).to_bytes(4, "big") + struct.pack(">HHH", 0, 1, min(0x7FFF, out_ppq))
            + b"MTrk" + len(trk).to_bytes(4, "big") + bytes(trk))
    if len(body) > MAX_BYTES:
        raise Rejected("too_big", "Cleaned, it's still over 1 MB.")
    dropped = (["drums"] if drums else []) + (_family_words(families) if piano_present else [])
    if not piano_present:
        words: Optional[str] = ("No piano part was marked, so every instrument plays on the piano"
                                + ("; dropped the drums." if drums else "."))
    elif dropped:
        words = f"Kept the piano; dropped the {_and(dropped)}."
    else:
        words = None
    report = {"kept_channels": sorted(channels), "dropped": dropped, "drums": drums, "piano": piano_present,
              "words": words, "notes": notes, "duration_s": round(end_sec, 1), "ppq": out_ppq}
    return body, report


def midi_length(path: pathlib.Path) -> Optional[float]:
    """A .mid's length in seconds (its last event through the tempo map), None if it can't be read."""
    try:
        _fmt, ppq, events = parse_smf(pathlib.Path(path).read_bytes())
        return round(_seconds(events, ppq), 1)
    except Exception:                                  # noqa: BLE001
        return None


# -- web sources (§6) ---------------------------------------------------------------------------------------------
class WebError(Exception):
    """A source failed; ``words`` are the page's."""

    def __init__(self, words: str) -> None:
        super().__init__(words)
        self.words = words


async def read_capped(resp: Any, cap: int = MAX_BYTES) -> bytes:
    """A response body, at most ``cap`` bytes (Rejected("too_big") beyond)."""
    if resp.content_length is not None and resp.content_length > cap:
        raise Rejected("too_big", "That file is over 1 MB.")
    buf = bytearray()
    async for chunk in resp.content.iter_chunked(65536):
        buf += chunk
        if len(buf) > cap:
            raise Rejected("too_big", "That file is over 1 MB.")
    return bytes(buf)


async def fetch_url(session: Any, url: str, label: str) -> bytes:
    async with session.get(url, allow_redirects=True) as r:
        if r.status != 200:
            raise WebError(f"{label} didn't have that file (it answered {r.status}).")
        return await read_capped(r)


class BitMidi:
    """bitmidi.com: ``GET /api/midi/search?q=&page=0&pageSize=`` -> ``{"result": {"results": [{id, name, slug,
    downloadUrl, plays}], "total"}}``; the file is ``/uploads/<id>.mid``."""

    id = "bitmidi"
    label = "BitMidi"
    base = "https://bitmidi.com"
    page_size = 20

    async def search(self, session: Any, q: str) -> list[dict]:
        url = f"{self.base}/api/midi/search"
        async with session.get(url, params={"q": q, "page": "0", "pageSize": str(self.page_size)}) as r:
            if r.status != 200:
                raise WebError(f"{self.label} didn't answer.")
            doc = await r.json(content_type=None)
        res = doc.get("result") if isinstance(doc, dict) else None
        rows = (res or {}).get("results") if isinstance(res, dict) else (doc or {}).get("results")
        if not isinstance(rows, list):
            raise WebError(f"{self.label} answered with something unexpected.")
        out = []
        for x in rows:
            if not isinstance(x, dict) or not isinstance(x.get("id"), int) or not isinstance(x.get("name"), str):
                continue
            title = title_from_filename(x["name"])
            plays = x.get("plays")
            piano = True if "piano" in norm(x["name"]) else None
            out.append({"ref": f"{self.id}:{x['id']}", "title": title, "source_label": self.label,
                        "detail": f"{plays:,} plays" if isinstance(plays, int) else None, "piano": piano})
        return out

    async def fetch(self, session: Any, ref: str) -> tuple[bytes, str]:
        rid = ref.split(":", 1)[1] if ":" in ref else ""
        if not rid.isdigit():
            raise WebError("That isn't a BitMidi file.")
        url = f"{self.base}/uploads/{int(rid)}.mid"
        return await fetch_url(session, url, self.label), url


class Mutopia:
    """The Mutopia Project (public-domain engravings, with MIDI): its search page ``/cgibin/make-table.cgi?
    searchingfor=`` (10 results a page, each a ``result-table``), searched twice in parallel (all instruments, and
    Instrument=Piano) so piano pieces come up first; files under ``/ftp/``. A multi-part piece is a ``-mids.zip``: its
    first part is imported."""

    id = "mutopia"
    label = "Mutopia Project"
    base = "https://www.mutopiaproject.org"
    PATH_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]*(?:\.mid|-mids\.zip)$")
    STYLE_GENRE = {"baroque": "classical", "classical": "classical", "romantic": "classical",
                   "renaissance": "classical", "modern": "classical", "medieval": "classical",
                   "jazz": "ragtime", "ragtime": "ragtime"}
    KEYBOARDS = ("piano", "harpsichord", "clavichord", "fortepiano", "keyboard", "clavier")
    NOT_SOLO = ("voice", "violin", "viola", "cello", "flute", "oboe", "clarinet", "bassoon", "horn", "trumpet",
                "trombone", "guitar", "lute", "choir", "satb", "soprano", "alto", "tenor", "baritone", "orchestra",
                "string", "recorder", "ensemble", "quartet", "trio", "organ", "bass")

    async def search(self, session: Any, q: str) -> list[dict]:
        async def one(extra: dict) -> list[dict]:
            async with session.get(f"{self.base}/cgibin/make-table.cgi", params={"searchingfor": q, **extra}) as r:
                if r.status != 200:
                    raise WebError(f"{self.label} didn't answer.")
                text = await r.text(errors="replace")
            return self.parse(text)
        got = await asyncio.gather(one({"Instrument": "Piano"}), one({}), return_exceptions=True)
        rows, seen = [], set()
        for g in got:
            if isinstance(g, BaseException):
                continue
            for x in g:
                if x["ref"] not in seen:
                    seen.add(x["ref"])
                    rows.append(x)
        if not rows and all(isinstance(g, BaseException) for g in got):
            e = got[0]
            raise e if isinstance(e, WebError) else WebError(f"{self.label} didn't answer.")
        return rows

    def parse(self, text: str) -> list[dict]:
        out = []
        for block in text.split('class="table-bordered result-table"')[1:]:
            block = block.split("</table>", 1)[0]
            cells = [" ".join(html.unescape(re.sub(r"<[^>]*>", " ", c)).split())
                     for c in re.findall(r"<td[^>]*>(.*?)</td>", block, re.S)]
            link = re.search(r'href="https?://www\.mutopiaproject\.org/ftp/([^"]+?(?:\.mid|-mids\.zip))"', block)
            if not link or len(cells) < 7 or not cells[0]:
                continue
            path = link.group(1)
            if not self.PATH_RE.match(path) or ".." in path:
                continue
            who = re.sub(r"^by\s+", "", cells[1])
            composer = re.sub(r"\s*\([^)]*\)\s*$", "", who).strip() or None
            instruments = re.sub(r"^for\s+", "", cells[4]).strip()
            low = norm(instruments)
            piano = (any(k in low for k in self.KEYBOARDS) and not any(w in low for w in self.NOT_SOLO))
            zipped = path.endswith("-mids.zip")
            detail = " · ".join(x for x in (composer, f"for {instruments}" if instruments else None,
                                            "first part of several" if zipped else None) if x)
            out.append({"ref": f"{self.id}:{path}", "title": cells[0], "source_label": self.label,
                        "detail": detail or None, "piano": piano, "composer": composer,
                        "genre": self.STYLE_GENRE.get(norm(cells[6]))})
        return out

    async def fetch(self, session: Any, ref: str) -> tuple[bytes, str]:
        path = ref.split(":", 1)[1] if ":" in ref else ""
        if not self.PATH_RE.match(path) or ".." in path:
            raise WebError("That isn't a Mutopia file.")
        url = f"{self.base}/ftp/{path}"
        data = await fetch_url(session, url, self.label)
        if path.endswith("-mids.zip"):
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    names = sorted((n for n in z.namelist() if n.lower().endswith(MIDI_EXTS)),
                                   key=lambda n: [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", n)])
                    if not names:
                        raise WebError("That Mutopia piece has no MIDI inside.")
                    info = z.getinfo(names[0])
                    if info.file_size > MAX_BYTES:
                        raise Rejected("too_big", "That file is over 1 MB.")
                    data = z.read(names[0])
            except zipfile.BadZipFile as e:
                raise WebError("Mutopia sent a damaged zip.") from e
        return data, url


WEB_SOURCES: list[Any] = [BitMidi(), Mutopia()]


class RecorderOps:
    """``recorder_ops`` for __main__: the recorder's rename/delete, run on show-brain through Brain.call."""

    TIMEOUT_S = 10.0

    def __init__(self, brain_ref: Callable[[], Any], name: str = "recorder") -> None:
        self.brain_ref, self.name = brain_ref, name

    def _parts(self) -> tuple[Any, Any]:
        brain = self.brain_ref()
        comps = getattr(brain, "components", None)
        rec = comps.get(self.name) if isinstance(comps, dict) else None
        if brain is None or rec is None or not getattr(brain, "running", True):
            raise ValueError("unavailable: the recorder isn't running right now")
        return brain, rec

    async def rename(self, rid: str, title: str) -> Any:
        brain, rec = self._parts()
        return await brain.call(rec.rename, rid, title, timeout=self.TIMEOUT_S)

    async def delete(self, rid: str) -> Any:
        brain, rec = self._parts()
        return await brain.call(rec.delete, rid, timeout=self.TIMEOUT_S)


async def _maybe_await(v: Any) -> Any:
    return await v if inspect.isawaitable(v) else v


# -- the library -----------------------------------------------------------------------------------------------------
class Library:
    """Search, pins, uploads, web imports, edits (module docstring). Every method is thread-safe; ``web_search``,
    ``web_import``, ``edit`` and ``delete`` are coroutines."""

    def __init__(self, *, lib_dir: Optional[pathlib.Path] = None, state_dir: Optional[pathlib.Path] = None,
                 file_dir_fn: Optional[Callable[[], pathlib.Path]] = None,
                 rec_dir_fn: Optional[Callable[[], Optional[pathlib.Path]]] = None,
                 recordings_fn: Optional[Callable[[], list]] = None, recorder_ops: Any = None,
                 phrases_fn: Optional[Callable[[], dict]] = None, web_sources: Optional[list] = None,
                 session_factory: Optional[Callable[[], Any]] = None, wall: Callable[[], float] = time.time) -> None:
        self.lib_dir = pathlib.Path(lib_dir) if lib_dir is not None else LIB_DIR
        self.state_dir = pathlib.Path(state_dir) if state_dir is not None else STATE_DIR
        self.file_dir_fn = file_dir_fn or file_dir
        self.rec_dir_fn = rec_dir_fn
        self.recordings_fn = recordings_fn
        self.recorder_ops = recorder_ops
        self.phrases_fn = phrases_fn or _phrase_labels
        self.web_sources = list(WEB_SOURCES if web_sources is None else web_sources)
        self.session_factory = session_factory
        self.wall = wall
        self.lock = threading.RLock()
        self._catalog = _catalog_file(self.lib_dir)       # shared with resolve()/piece_title() (sources.py)
        self._user = _user_file(self.lib_dir)
        self._pins_file = _JsonFile(lambda: self.state_dir / PINS_FILE)
        self._lib_entries: tuple[Any, list[dict], dict] = (None, [], {})
        self._user_entries: tuple[Any, list[dict], dict] = (None, [], {})
        self._file_entries: tuple[Any, list[dict]] = (None, [])
        self._phrase_entries: Optional[list[dict]] = None
        self._durations: dict[tuple, Optional[float]] = {}
        self._dur_gen = 0
        self._dur_thread: Optional[threading.Thread] = None
        self._web_seen: collections.OrderedDict = collections.OrderedDict()
        self._problems: list[str] = []

    # -- folders ---------------------------------------------------------------------------------------------------
    def files_dir(self) -> pathlib.Path:
        try:
            return pathlib.Path(self.file_dir_fn())
        except Exception:                              # noqa: BLE001
            return TEST_PIANO_DIR

    def recs_dir(self) -> pathlib.Path:
        if self.rec_dir_fn is not None:
            try:
                got = self.rec_dir_fn()
                if got is not None:
                    return pathlib.Path(got)
            except Exception:                          # noqa: BLE001
                pass
        return rec_dir()

    def resolve(self, piece: str) -> Optional[pathlib.Path]:
        return resolve(piece, lib_dir=self.lib_dir, files=self.files_dir(), recs=self.recs_dir())

    # -- the sources of items --------------------------------------------------------------------------------------
    def problems(self) -> list[str]:
        out = []
        doc, err = self._catalog.get()
        if err:
            out.append(err)
        elif doc is None:
            out.append("The library hasn't been built yet (no catalog.json): only uploads, recordings and the test "
                       "tracks are here.")
        _, err = self._user.get()
        if err:
            out.append(err)
        _, err = self._pins_file.get()
        if err:
            out.append(err)
        return out

    def _entry(self, item: dict, *, rank: float = 0.0, tags: Any = (), added: Any = None) -> dict:
        """An item with what search needs precomputed: the normalised title, one haystack of words (title, composer,
        performer, genre label, era, tags), the composer's sort key."""
        title_n = " ".join(tokens(item.get("title")))
        rest = tokens(" ".join(str(x) for x in (item.get("composer"), item.get("performer"), item.get("genre_label"),
                                                item.get("era"), *(t for t in tags if isinstance(t, str))) if x))
        comp = item.get("composer")
        return {"item": item, "title_n": title_n, "hay": f" {title_n} {' '.join(rest)} ", "rank": rank,
                "composer_n": norm(comp) if comp else None, "composer_key": surname_key(comp) if comp else "",
                "added_ts": _iso_ts(added)}

    def _lib(self) -> list[dict]:
        return self._lib_indexed()[0]

    def _lib_indexed(self) -> tuple[list[dict], dict]:
        doc, _ = self._catalog.get()
        gen = (self._catalog.gen, id(doc))
        with self.lock:
            if self._lib_entries[0] == gen:
                return self._lib_entries[1], self._lib_entries[2]
            rows = []
            for iid, x in _items_of(doc).items():
                item = self._lib_item(iid, x)
                if item is not None:
                    tags = x.get("tags") if isinstance(x.get("tags"), list) else []
                    rows.append(self._entry(item, rank=_num(x.get("rank")) or 0.0, tags=tags))
            self._lib_entries = (gen, rows, {e["item"]["id"]: e for e in rows})
            return rows, self._lib_entries[2]

    @staticmethod
    def _lib_item(iid: Any, x: Any) -> Optional[dict]:
        """A catalogue entry as an Item (without ``pinned``), None when it isn't one."""
        if not isinstance(x, dict) or not isinstance(iid, str) or not SLUG_RE.match(iid):
            return None
        if not isinstance(x.get("file"), str) or not isinstance(x.get("title"), str):
            return None
        g = clean_genre(x.get("genre"))
        return _drop_none({"id": f"{LIB_PREFIX}{iid}", "title": x["title"], "composer": _str(x.get("composer")),
                           "performer": _str(x.get("performer")), "genre": g,
                           "genre_label": GENRE_LABEL.get(g) if g else None, "era": _str(x.get("era")),
                           "year": x.get("year") if isinstance(x.get("year"), int) else None,
                           "duration_s": _num(x.get("duration_s")), "kind": "lib", "editable": False,
                           "source": _str(x.get("source"))})

    def _users(self) -> list[dict]:
        return self._users_indexed()[0]

    def _users_indexed(self) -> tuple[list[dict], dict]:
        doc, _ = self._user.get()
        gen = (self._user.gen, id(doc))
        with self.lock:
            if self._user_entries[0] == gen:
                return self._user_entries[1], self._user_entries[2]
            rows = []
            for uid, x in _items_of(doc).items():
                if not isinstance(x, dict) or not isinstance(uid, str) or not SLUG_RE.match(uid):
                    continue
                rows.append(self._entry(self._user_item(uid, x), added=x.get("added_at")))
            self._user_entries = (gen, rows, {e["item"]["id"]: e for e in rows})
            return rows, self._user_entries[2]

    @staticmethod
    def _user_item(uid: str, x: dict) -> dict:
        g = clean_genre(x.get("genre"))
        return _drop_none({"id": f"{USER_PREFIX}{uid}", "title": _str(x.get("title")) or "Untitled",
                           "composer": _str(x.get("composer")), "genre": g,
                           "genre_label": GENRE_LABEL.get(g) if g else None,
                           "duration_s": _num(x.get("duration_s")), "kind": "user", "editable": True,
                           "added_at": _str(x.get("added_at")), "source": _str(x.get("source_label"))
                           or ("Uploaded" if x.get("origin") == "upload" else None)})

    def _recs(self) -> list[dict]:
        """The recorder's list (``recordings_fn``; None: no recordings)."""
        rows = []
        try:
            got = (self.recordings_fn() or []) if self.recordings_fn is not None else []
        except Exception:                              # noqa: BLE001  degrade: no recordings listed
            log.debug("recordings_fn failed", exc_info=True)
            got = []
        for r in got:
            if not isinstance(r, dict) or not isinstance(r.get("id"), str) or not REC_ID_RE.match(r["id"]):
                continue
            rows.append(self._entry(self._rec_item(r), added=r.get("recorded_at")))
        return rows

    def _recs_from_folder(self) -> list[dict]:
        """The recordings folder read directly (the CLI's recordings_fn: no recorder component there)."""
        folder = self.recs_dir()
        try:
            names = [p.name[:-5] for p in folder.iterdir() if p.suffix == ".json" and not p.name.startswith(".")]
        except OSError:
            return []
        out = []
        for rid in sorted(names, reverse=True):
            meta = _rec_meta(folder, rid)
            if meta is not None:
                out.append({"id": rid, "title": meta.get("title"), "recorded_at": meta.get("recorded_at"),
                            "duration_s": meta.get("duration_s")})
        return out

    @staticmethod
    def _rec_item(r: dict) -> dict:
        return _drop_none({"id": f"{REC_PREFIX}{r['id']}", "title": _str(r.get("title")) or "Recording",
                           "duration_s": _num(r.get("duration_s")), "kind": "rec", "editable": True,
                           "added_at": _str(r.get("recorded_at")), "source": "Recorded here"})

    def _files(self) -> list[dict]:
        folder = self.files_dir()
        stamp = (str(folder), _stamp(folder), self._dur_gen)
        with self.lock:
            if self._file_entries[0] == stamp:
                return self._file_entries[1]
            try:
                paths = sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in MIDI_EXTS
                                and not p.name.startswith(".")), key=lambda p: p.name.lower())
            except OSError:
                paths = []
            rows = []
            want = []
            for p in paths:
                key = (str(p), _stamp(p))
                if key not in self._durations:
                    want.append((key, p))
                rows.append(self._entry(self._file_item(p.name, self._durations.get(key))))
            self._file_entries = (stamp, rows)
            if want:
                self._measure(want)
            return rows

    def _measure(self, want: list) -> None:
        """Durations of the test tracks, in a daemon thread (each parsed once); the list is rebuilt when done."""
        if self._dur_thread is not None and self._dur_thread.is_alive():
            return

        def run() -> None:
            for key, p in want:
                self._durations[key] = midi_length(p)
            with self.lock:
                self._dur_gen += 1                     # the list is rebuilt with them
        self._dur_thread = threading.Thread(target=run, name="show-library-durations", daemon=True)
        self._dur_thread.start()

    @staticmethod
    def _file_item(name: str, duration: Optional[float]) -> dict:
        """"Classical - Chopin - Nocturne in E-flat, Op. 9 No. 2.mid": genre, composer (a performer outside
        Classical), title; any other name is its own title."""
        stem = name.rsplit(".", 1)[0]
        parts = [p.strip() for p in stem.split(" - ")]
        genre = who = None
        title = title_from_filename(name)
        if len(parts) >= 3 and clean_genre(parts[0]):
            genre, who, title = clean_genre(parts[0]), parts[1] or None, " - ".join(parts[2:])
        item = {"id": f"{FILE_PREFIX}{name}", "title": title, "genre": genre,
                "genre_label": GENRE_LABEL.get(genre) if genre else None, "duration_s": duration, "kind": "file",
                "editable": False, "source": "Test track"}
        if who:
            item["composer" if genre == "classical" else "performer"] = who
        return _drop_none(item)

    def _phrases(self) -> list[dict]:
        with self.lock:
            if self._phrase_entries is None:
                try:
                    labels = dict(self.phrases_fn() or {})
                except Exception:                      # noqa: BLE001
                    labels = {}
                self._phrase_entries = [self._entry({"id": k, "title": str(v), "kind": "phrase", "editable": False,
                                                     "source": "Test pattern"}, rank=-1.0)
                                        for k, v in labels.items() if isinstance(k, str) and PHRASE_RE.match(k)]
            return self._phrase_entries

    def _entries(self, kinds: Optional[set] = None) -> list[dict]:
        out: list[dict] = []
        for k, fn in (("lib", self._lib), ("user", self._users), ("rec", self._recs), ("file", self._files),
                      ("phrase", self._phrases)):
            if kinds is None or k in kinds:
                out += fn()
        return out

    def _pin_ids(self) -> list[str]:
        doc, _ = self._pins_file.get()
        pins = doc.get("pins") if isinstance(doc, dict) else None
        return [p for p in pins if isinstance(p, str)] if isinstance(pins, list) else []

    def _out(self, e: dict, pins: set) -> dict:
        return {**e["item"], "pinned": e["item"]["id"] in pins}

    # -- search ----------------------------------------------------------------------------------------------------
    def search(self, q: Optional[str] = None, genre: Optional[str] = None, composer: Optional[str] = None,
               kind: Optional[str] = "all", pinned: bool = False, sort: Optional[str] = None, limit: int = 50,
               offset: int = 0) -> dict:
        """GET /api/v1/library (module docstring; ``kind`` also takes a comma list, e.g. "user,rec")."""
        kinds = None
        if kind and kind != "all":
            kinds = {k.strip() for k in str(kind).split(",") if k.strip() in KIND_LABEL} or {"none"}
        pins = set(self._pin_ids())
        qn = " ".join(tokens(q))
        toks = qn.split()
        comp_n = norm(composer).strip() if composer else None
        gen = genre.strip().lower() if isinstance(genre, str) and genre.strip() else None
        matched: list[tuple[dict, int]] = []
        for e in self._entries():
            if toks:
                tier = _tier(e, qn, toks)
                if tier == 0:
                    continue
            else:
                tier = 0
            matched.append((e, tier))

        def ok_kind(e: dict) -> bool:
            return kinds is None or e["item"]["kind"] in kinds

        def ok_genre(e: dict) -> bool:
            return gen is None or e["item"].get("genre") == gen

        def ok_comp(e: dict) -> bool:
            return comp_n is None or e["composer_n"] == comp_n

        def ok_pin(e: dict) -> bool:
            return not pinned or e["item"]["id"] in pins

        rows = [(e, t) for e, t in matched if ok_kind(e) and ok_genre(e) and ok_comp(e) and ok_pin(e)]
        g_count: collections.Counter = collections.Counter()
        c_count: collections.Counter = collections.Counter()
        c_name: dict[str, tuple[str, str]] = {}
        k_count: collections.Counter = collections.Counter()
        for e, _t in matched:
            k, g, c, p = ok_kind(e), ok_genre(e), ok_comp(e), ok_pin(e)
            if k and c and p and e["item"].get("genre"):
                g_count[e["item"]["genre"]] += 1
            if k and g and p and e["composer_n"]:
                c_count[e["composer_n"]] += 1
                c_name.setdefault(e["composer_n"], (e["composer_key"], e["item"]["composer"]))
            if g and c and p:
                k_count[e["item"]["kind"]] += 1
        facets = {"genres": [{"id": gid, "label": label, "count": g_count.get(gid, 0)} for gid, label in GENRES],
                  "composers": [{"name": c_name[n][1], "count": c_count[n]}
                                for n in sorted(c_count, key=lambda n: (c_name[n][0], n))],
                  "kinds": [{"id": kid, "label": label, "count": k_count.get(kid, 0)} for kid, label in KINDS]}
        s = sort if sort in SORTS else "rank"
        if s == "title":
            rows.sort(key=lambda r: (r[0]["title_n"], r[0]["item"]["id"]))
        elif s == "composer":
            rows.sort(key=lambda r: (r[0]["composer_key"] or "~", r[0]["title_n"]))
        elif s == "recent":
            rows.sort(key=lambda r: (-(r[0]["added_ts"] or -1e18), r[0]["title_n"]))
        else:
            rows.sort(key=lambda r: (-r[1], -r[0]["rank"], r[0]["title_n"]))
        limit = max(1, min(LIMIT_MAX, int(limit)))
        offset = max(0, int(offset))
        return {"items": [self._out(e, pins) for e, _t in rows[offset:offset + limit]], "total": len(rows),
                "facets": facets, "problems": self.problems()}

    def item(self, piece: str) -> Optional[dict]:
        """One piece as an Item, or None (unknown or gone). Cheap (the player calls it on show-brain): a lib:/user:
        piece is read from its own catalogue entry, never through the search index."""
        got = split_id(piece)
        if got is None:
            return None
        kind, rest = got
        pins = set(self._pin_ids())
        if kind == "lib":
            doc, _ = self._catalog.get()
            it = self._lib_item(rest, _items_of(doc).get(rest))
            return {**it, "pinned": piece in pins} if it is not None else None
        if kind == "user":
            doc, _ = self._user.get()
            x = _items_of(doc).get(rest)
            if not isinstance(x, dict) or self.resolve(piece) is None:
                return None
            return {**self._user_item(rest, x), "pinned": piece in pins}
        if kind == "rec":
            e = next((x for x in self._recs() if x["item"]["id"] == piece), None)
        elif kind == "file":
            e = next((x for x in self._files() if x["item"]["id"] == piece), None)
        else:
            e = next((x for x in self._phrases() if x["item"]["id"] == piece), None)
        return self._out(e, pins) if e is not None else None

    def stats(self) -> dict:
        counts = collections.Counter(e["item"]["kind"] for e in self._entries())
        genres = collections.Counter(e["item"].get("genre") or "-" for e in self._entries({"lib"}))
        return {"kinds": dict(counts), "genres": dict(genres), "pins": len(self._pin_ids()),
                "lib_dir": str(self.lib_dir), "problems": self.problems()}

    # -- pins ------------------------------------------------------------------------------------------------------
    def pins(self) -> list[dict]:
        out = []
        for p in self._pin_ids():
            it = self.item(p)
            if it is not None:
                out.append(it)
        return out

    def pin(self, piece: str, on: bool) -> list[dict]:
        """Pin (an unknown piece: LookupError("unknown_piece")) or unpin; returns pins()."""
        with self.lock:
            ids = self._pin_ids()
            if on:
                if self.item(piece) is None:
                    raise LookupError("unknown_piece")
                if piece not in ids:
                    ids.append(piece)
            else:
                ids = [p for p in ids if p != piece]
            write_json(self.state_dir / PINS_FILE, {"v": 1, "pins": ids})
            self._pins_file.get(force=True)
        return self.pins()


    # -- uploads and imports -----------------------------------------------------------------------------------------
    def add_upload(self, data: bytes, filename: Optional[str] = None, title: Any = None, composer: Any = None,
                   genre: Any = None, *, origin: str = "upload", source_url: Optional[str] = None,
                   source_label: Optional[str] = None) -> tuple[dict, dict]:
        """Clean and keep a MIDI file as ``user:<id>`` -> (Item, report). Raises Rejected / ValueError("bad_title")."""
        t = clean_text(title) or title_from_filename(filename)
        c = clean_text(composer, "composer")
        g = clean_genre(genre)
        body, report = clean_midi(data, t)
        digest = hashlib.sha1(body).hexdigest()
        uid = f"{slug(' '.join(x for x in (c, t) if x), 60) or 'piece'}-{digest[:6]}"
        with self.lock:
            doc = self._read_user_doc()
            items = doc["items"]
            path = self.lib_dir / USER_DIR / f"{uid}.mid"
            if uid not in items or not path.is_file():
                atomic_write(path, body)
                items[uid] = {"id": uid, "title": t, "composer": c, "genre": g, "origin": origin,
                              "source_url": source_url, "source_label": source_label,
                              "added_at": _now_iso(), "duration_s": report["duration_s"], "notes": report["notes"],
                              "sha1": digest, "cleaned": {k: report[k] for k in ("kept_channels", "dropped", "drums")}}
                write_json(self.lib_dir / USER_JSON, doc)
                self._user.get(force=True)
                log.info("library: added user:%s (%s, %d notes)", uid, origin, report["notes"])
        item = self.item(f"{USER_PREFIX}{uid}") or self._user_item(uid, items[uid])
        return item, report

    def _read_user_doc(self) -> dict:
        try:
            doc = json.loads((self.lib_dir / USER_JSON).read_text(encoding="utf-8"))
        except FileNotFoundError:
            doc = None
        except (OSError, ValueError) as e:
            raise ValueError(f"failed: user.json couldn't be read ({type(e).__name__}); nothing was changed") from e
        if not isinstance(doc, dict) or not isinstance(doc.get("items"), dict):
            doc = {"v": 1, "items": {}}
        doc["v"] = 1
        return doc

    async def web_search(self, q: str) -> dict:
        """Every web source in parallel (WEB_TIMEOUT_S each) -> {"results": [...], "errors": [{source_label, words}]},
        solo piano first where a source says so."""
        q = " ".join(str(q or "").split())[:200]
        if not q:
            return {"results": [], "errors": []}

        async def one(src: Any, session: Any) -> list[dict]:
            return await asyncio.wait_for(src.search(session, q), WEB_TIMEOUT_S)

        async with self._session() as session:
            got = await asyncio.gather(*(one(s, session) for s in self.web_sources), return_exceptions=True)
        results: list[tuple[int, int, int, dict]] = []
        errors = []
        for si, (src, g) in enumerate(zip(self.web_sources, got)):
            label = getattr(src, "label", "A source")
            if isinstance(g, BaseException):
                words = g.words if isinstance(g, WebError) else f"{label} didn't answer."
                if not isinstance(g, (WebError, asyncio.TimeoutError)):
                    log.info("library: web search %s failed: %s: %s", label, type(g).__name__, g)
                errors.append({"source_label": label, "words": words})
                continue
            for i, r in enumerate(g):
                results.append((0 if r.get("piano") is True else 1, si, i, r))
        results.sort(key=lambda x: x[:3])
        out = []
        with self.lock:
            for *_k, r in results:
                self._web_seen[r["ref"]] = r
                self._web_seen.move_to_end(r["ref"])
            while len(self._web_seen) > WEB_SEEN_MAX:
                self._web_seen.popitem(last=False)
        for *_k, r in results:
            out.append(_drop_none({"ref": r["ref"], "title": r["title"], "source_label": r["source_label"],
                                   "detail": r.get("detail"), "size": r.get("size"),
                                   "composer": r.get("composer")}))
        return {"results": out, "errors": errors}

    async def web_import(self, ref: str, title: Any = None, composer: Any = None, genre: Any = None) -> tuple[dict,
                                                                                                         dict]:
        """A web result's ``ref`` (or an http(s) URL to a .mid) -> as add_upload. WebError (words) when the fetch
        fails; Rejected when the file isn't usable."""
        ref = str(ref or "").strip()
        if not ref:
            raise ValueError("bad_request: say which file to add")
        seen = self._web_seen.get(ref) or {}
        if ref.lower().startswith(("http://", "https://")):
            label = re.sub(r"^https?://(www\.)?", "", ref).split("/", 1)[0].split(":", 1)[0] or "That site"

            async def get(session: Any) -> tuple[bytes, str]:
                return await fetch_url(session, ref, label), ref
        else:
            sid = ref.split(":", 1)[0]
            src = next((s for s in self.web_sources if getattr(s, "id", None) == sid), None)
            if src is None:
                raise ValueError("unknown_ref: that web file isn't from a source this library knows")
            label = getattr(src, "label", sid)

            async def get(session: Any) -> tuple[bytes, str]:
                return await src.fetch(session, ref)
        try:
            async with self._session() as session:
                data, url = await asyncio.wait_for(get(session), WEB_TIMEOUT_S)
        except (Rejected, WebError, asyncio.CancelledError):
            raise
        except asyncio.TimeoutError:
            raise WebError(f"{label} didn't answer in time.") from None
        except Exception as e:                         # noqa: BLE001  a network failure is the source's
            log.info("library: import %s failed: %s: %s", ref, type(e).__name__, e)
            raise WebError(f"{label} didn't answer.") from None
        name = url.split("?", 1)[0].rsplit("/", 1)[-1]
        t = clean_text(title) or seen.get("title") or title_from_filename(name)
        c = clean_text(composer, "composer") or seen.get("composer")
        g = clean_genre(genre) or seen.get("genre")
        return await asyncio.get_running_loop().run_in_executor(
            None, lambda: self.add_upload(data, name, t, c, g, origin="web", source_url=url, source_label=label))

    def _session(self) -> Any:
        if self.session_factory is not None:
            return self.session_factory()
        import aiohttp
        return aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=WEB_TIMEOUT_S, sock_connect=WEB_TIMEOUT_S),
                                     headers={"User-Agent": USER_AGENT})

    # -- edits -----------------------------------------------------------------------------------------------------
    async def edit(self, piece: str, title: Any = None, composer: Any = None, genre: Any = None, *,
                   fields: Optional[set] = None) -> dict:
        """PATCH: a user: piece's title/composer/genre, a rec: piece's title (the recorder's rename). ``fields``: the
        keys the request named (a named null clears composer/genre). ValueError("not_editable") for other kinds,
        LookupError("unknown_piece")."""
        got = split_id(piece)
        fields = set(fields) if fields is not None else {k for k, v in (("title", title), ("composer", composer),
                                                                         ("genre", genre)) if v is not None}
        if got is None:
            raise LookupError("unknown_piece")
        kind, rest = got
        if kind == "rec":
            if "title" not in fields:
                it = self.item(piece)
                if it is None:
                    raise LookupError("unknown_piece")
                return it
            if self.recorder_ops is None:
                raise ValueError("unavailable: recordings can't be renamed right now")
            try:
                await _maybe_await(self.recorder_ops.rename(rest, title))
            except LookupError:
                raise LookupError("unknown_piece") from None
            it = self.item(piece)
            return it if it is not None else {"id": piece, "title": clean_text(title) or "Recording", "kind": "rec",
                                              "pinned": piece in self._pin_ids(), "editable": True}
        if kind != "user":
            raise ValueError("not_editable: only your uploads and recordings can be changed")
        new_title = clean_text(title) if "title" in fields else None
        if "title" in fields and not new_title:
            raise ValueError("bad_title: a title can't be empty")
        new_comp = clean_text(composer, "composer") if "composer" in fields else None

        def run() -> dict:
            with self.lock:
                doc = self._read_user_doc()
                x = doc["items"].get(rest)
                if not isinstance(x, dict):
                    raise LookupError("unknown_piece")
                if "title" in fields:
                    x["title"] = new_title
                if "composer" in fields:
                    x["composer"] = new_comp
                if "genre" in fields:
                    x["genre"] = clean_genre(genre)
                write_json(self.lib_dir / USER_JSON, doc)
                self._user.get(force=True)
            return self.item(piece) or self._user_item(rest, x)
        return await asyncio.get_running_loop().run_in_executor(None, run)

    async def delete(self, piece: str) -> dict:
        """DELETE: a user: piece (file and entry) or a rec: piece (the recorder's delete); it leaves the pins. Playlists
        keep it, shown missing. Already gone: said, not an error."""
        got = split_id(piece)
        if got is None:
            raise LookupError("unknown_piece")
        kind, rest = got
        if kind not in ("user", "rec"):
            raise ValueError("not_editable: only your uploads and recordings can be deleted")
        detail = None
        if kind == "rec":
            if self.recorder_ops is None:
                raise ValueError("unavailable: recordings can't be deleted right now")
            try:
                await _maybe_await(self.recorder_ops.delete(rest))
            except LookupError:
                detail = "That recording was already gone."
        else:
            def run() -> Optional[str]:
                with self.lock:
                    doc = self._read_user_doc()
                    gone = doc["items"].pop(rest, None) is None
                    try:
                        (self.lib_dir / USER_DIR / f"{rest}.mid").unlink()
                    except FileNotFoundError:
                        pass
                    if not gone:
                        write_json(self.lib_dir / USER_JSON, doc)
                        self._user.get(force=True)
                    return "That piece was already gone." if gone else None
            detail = await asyncio.get_running_loop().run_in_executor(None, run)
        if piece in self._pin_ids():
            await asyncio.get_running_loop().run_in_executor(None, self.pin, piece, False)
        log.info("library: deleted %s", piece)
        return _drop_none({"deleted": piece, "detail": detail})

    def file_of(self, piece: str) -> tuple[pathlib.Path, str]:
        """(the .mid, its title) for a download. LookupError("unknown_piece")."""
        it = self.item(piece)
        p = self.resolve(piece) if it is not None else None
        if p is None:
            raise LookupError("unknown_piece")
        return p, str(it.get("title") or "piece")

    # -- routes (show-bus) -----------------------------------------------------------------------------------------
    def routes(self, app: Any) -> None:
        r = app.router
        r.add_get("/api/v1/library", self._guard(self._h_search))
        r.add_get("/api/v1/library/pins", self._guard(self._h_pins))
        r.add_put("/api/v1/library/pins/{id}", self._guard(self._h_pin_on))
        r.add_delete("/api/v1/library/pins/{id}", self._guard(self._h_pin_off))
        r.add_post("/api/v1/library/upload", self._guard(self._h_upload))
        r.add_get("/api/v1/library/web", self._guard(self._h_web))
        r.add_post("/api/v1/library/web/import", self._guard(self._h_web_import))
        r.add_get("/api/v1/library/item/{id}", self._guard(self._h_item))
        r.add_patch("/api/v1/library/item/{id}", self._guard(self._h_edit))
        r.add_delete("/api/v1/library/item/{id}", self._guard(self._h_delete))

    def _guard(self, fn: Callable) -> Callable:
        from aiohttp import web

        async def handler(request: Any) -> Any:
            try:
                return await fn(request)
            except asyncio.CancelledError:
                raise
            except web.HTTPException:
                raise
            except Rejected as e:
                return _err(400, e.code, e.words)
            except WebError as e:
                return _err(400, "fetch_failed", e.words)
            except asyncio.TimeoutError:
                return _err(400, "fetch_failed", "The web source didn't answer in time.")
            except LookupError as e:
                if isinstance(e, KeyError):            # a bug, not an unknown piece
                    log.exception("library: %s %s failed", request.method, request.path)
                    return _err(500, "failed", "Something went wrong (KeyError); the show carries on.")
                return _err(404, "unknown_piece", "That piece isn't in the library.")
            except ValueError as e:
                code, _, detail = str(e).partition(":")
                code = code.strip() if re.match(r"^[a-z_]{2,40}$", code.strip()) else "bad_request"
                return _err(400, code, detail.strip() or "That request wasn't understood.")
            except Exception as e:                     # noqa: BLE001
                log.exception("library: %s %s failed", request.method, request.path)
                return _err(500, "failed", f"Something went wrong ({type(e).__name__}); the show carries on.")
        return handler

    @staticmethod
    async def _run(fn: Callable, *args: Any) -> Any:
        return await asyncio.get_running_loop().run_in_executor(None, fn, *args)

    async def _h_search(self, request: Any) -> Any:
        q = request.query

        def num(key: str, default: int) -> int:
            try:
                return int(q.get(key, default))
            except ValueError:
                return default
        pinned = str(q.get("pinned", "0")).lower() in ("1", "true", "yes")
        body = await self._run(lambda: self.search(q.get("q") or None, q.get("genre") or None,
                                                   q.get("composer") or None, q.get("kind") or "all", pinned,
                                                   q.get("sort") or None, num("limit", 50), num("offset", 0)))
        return _json(body)

    async def _h_pins(self, request: Any) -> Any:
        return _json({"pins": await self._run(self.pins)})

    async def _h_pin_on(self, request: Any) -> Any:
        return _json({"pins": await self._run(self.pin, request.match_info["id"], True)})

    async def _h_pin_off(self, request: Any) -> Any:
        return _json({"pins": await self._run(self.pin, request.match_info["id"], False)})

    async def _h_item(self, request: Any) -> Any:
        from aiohttp import web
        pid = request.match_info["id"]
        it = await self._run(self.item, pid)
        if it is not None:
            return _json(it)
        if pid.lower().endswith(".mid") and split_id(pid[:-4]) is not None:      # GET …/item/<id>.mid: the file
            path, title = await self._run(self.file_of, pid[:-4])
            data = await self._run(path.read_bytes)
            return web.Response(body=data, content_type="audio/midi",
                                headers={"Content-Disposition": content_disposition(title)})
        raise LookupError("unknown_piece")

    async def _h_edit(self, request: Any) -> Any:
        body = await read_json(request)
        if not isinstance(body, dict):
            raise ValueError('bad_request: send {"title"?, "composer"?, "genre"?}')
        fields = {k for k in ("title", "composer", "genre") if k in body}
        return _json(await self.edit(request.match_info["id"], body.get("title"), body.get("composer"),
                                     body.get("genre"), fields=fields))

    async def _h_delete(self, request: Any) -> Any:
        return _json(await self.delete(request.match_info["id"]))

    async def _h_upload(self, request: Any) -> Any:
        q = request.query
        data = await read_body(request, MAX_BYTES)
        item, report = await self._run(lambda: self.add_upload(data, q.get("filename"), q.get("title") or None,
                                                               q.get("composer") or None, q.get("genre") or None))
        return _json({"item": item, "cleaned": {"words": report.get("words")}})

    async def _h_web(self, request: Any) -> Any:
        return _json(await self.web_search(request.query.get("q", "")))

    async def _h_web_import(self, request: Any) -> Any:
        body = await read_json(request)
        if not isinstance(body, dict) or not isinstance(body.get("ref") or body.get("url"), str):
            raise ValueError('bad_request: send {"ref", "title"?, "composer"?, "genre"?}')
        item, report = await self.web_import(body.get("ref") or body.get("url"), body.get("title"),
                                             body.get("composer"), body.get("genre"))
        return _json({"item": item, "cleaned": {"words": report.get("words")}})


# -- HTTP helpers (shared with playlist.py) -----------------------------------------------------------------------
def _json(body: Any, status: int = 200) -> Any:
    from aiohttp import web
    return web.json_response(body, status=status, dumps=lambda o: json.dumps(o, ensure_ascii=False))


def _err(status: int, code: str, detail: str) -> Any:
    return _json({"error": code, "detail": detail}, status)


async def read_body(request: Any, cap: int) -> bytes:
    """The raw body, at most ``cap`` bytes (Rejected("too_big") beyond). Read from the stream: the bus's own
    client_max_size (64 KB) is for its JSON."""
    if request.content_length is not None and request.content_length > cap:
        raise Rejected("too_big", "That file is over 1 MB; MIDI files are usually far smaller.")
    buf = bytearray()
    while True:
        chunk = await request.content.read(65536)
        if not chunk:
            break
        buf += chunk
        if len(buf) > cap:
            raise Rejected("too_big", "That file is over 1 MB; MIDI files are usually far smaller.")
    return bytes(buf)


async def read_json(request: Any, cap: int = 512 * 1024) -> Any:
    """A JSON body (None when empty); ValueError("bad_request") when it isn't JSON or is over ``cap``."""
    try:
        raw = await read_body(request, cap)
    except Rejected:
        raise ValueError("bad_request: that request is too large") from None
    if not raw.strip():
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ValueError("bad_request: the request body isn't JSON") from None


def content_disposition(title: str) -> str:
    from urllib.parse import quote
    safe = re.sub(r"[^A-Za-z0-9 ._()\-]", "_", title).strip(" ._") or "piece"
    return f"attachment; filename=\"{safe[:100]}.mid\"; filename*=UTF-8''{quote(title[:100] + '.mid')}"


def _str(v: Any) -> Optional[str]:
    return v.strip() if isinstance(v, str) and v.strip() else None


def _drop_none(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


def _tier(e: dict, qn: str, toks: list[str]) -> int:
    """4 exact title, 3 title prefix, 2 every word whole, 1 every word as a prefix, 0 no match."""
    hay = e["hay"]
    if not all(f" {t}" in hay for t in toks):
        return 0
    if e["title_n"] == qn:
        return 4
    if e["title_n"].startswith(qn):
        return 3
    if all(f" {t} " in hay for t in toks):
        return 2
    return 1


_phrases_cache: Optional[dict] = None


def _phrase_labels() -> dict:
    """piano_replay.PHRASE_LABELS, loaded once by path ({} if that fails)."""
    global _phrases_cache
    if _phrases_cache is None:
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("_show_library_piano_replay", PIANO_REPLAY)
            mod = importlib.util.module_from_spec(spec)                 # type: ignore[arg-type]
            spec.loader.exec_module(mod)                                # type: ignore[union-attr]
            _phrases_cache = {str(k): str(v) for k, v in dict(mod.PHRASE_LABELS).items()}
        except Exception:                                               # noqa: BLE001
            _phrases_cache = {}
    return _phrases_cache


# -- CLI ------------------------------------------------------------------------------------------------------------
def main(argv: Optional[list[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["resolve"] and len(args) == 2:
        p = resolve(args[1])
        print(p if p is not None else f"{args[1]}: not found (or a built-in phrase: no file)")
        return 0 if p is not None else 1
    lib = Library()
    lib.recordings_fn = lib._recs_from_folder
    if args[:1] == ["stats"]:
        print(json.dumps(lib.stats(), indent=1, ensure_ascii=False))
        return 0
    if args[:1] == ["search"]:
        got = lib.search(" ".join(args[1:]) or None, limit=30)
        for it in got["items"]:
            who = " · ".join(x for x in (it.get("composer"), it.get("performer"), it.get("genre_label")) if x)
            dur = f"{int(it['duration_s'] // 60)}:{int(it['duration_s'] % 60):02d}" if it.get("duration_s") else ""
            print(f"{it['id']:<60} {it['title']}" + (f" — {who}" if who else "") + (f" ({dur})" if dur else ""))
        print(f"{got['total']} found" + "".join(f"\n! {p}" for p in got["problems"]))
        return 0
    print("usage: python -m show_service.library resolve <piece id> | stats | search <words>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
