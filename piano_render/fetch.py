"""Install the test piano's instruments, room impulse responses and the sfizz engine. Stdlib only.

Everything lands outside git, where config/piano/instruments.json says:
  root          ~/.local/share/screen-machine/piano/instruments   instruments, plus room IRs under _irs/
  engines_root  ~/.local/share/screen-machine/piano/engines       sfizz/lib/libsfizz.so.1, tools/7zip/bin/7za

Downloads are cached in root/_downloads/ (engine debs in engines_root/_downloads/) and kept, so a re-unpack needs
no network. They resume with HTTP Range and are checked against the entry's byte count and sha256. Each archive is
unpacked into root/<subdir> through a temp dir and one rename; root/<subdir>/.fetched.json records the source, so
a second run does nothing (idempotent). Unpacking runs under ~/.cache/screen-machine/cpu.lock (shared with the
other heavy jobs on this box; set SM_CPU_LOCK_HELD=1 when a caller already holds it) at idle priority.

A "fetch" block (on an instrument or a room):
  url, sha256, bytes, unpack ("zip" | "tar" | "7z" | "deb" | "none"), subdir (a dir under root)
            url may be a BitTorrent magnet (a library whose only free release is a torrent, e.g. Piano in 162):
            nothing is downloaded; the archive must already be in root/_downloads/ under its cache name
            (<sha256[:12]>-<the magnet's dn>), else the entry fails at once saying so
  optional: strip   (int) leading path parts to drop from every member, like tar --strip-components
            members (list) fnmatch patterns a member (after strip) must match to be unpacked
            post    (str)  a recipe run on the unpacked dir: "bitklavier-sfz" writes the SFZ map bitKlavier lacks;
                           "sfz-wrap" writes an SFZ beside block["wrap"]["base"] with new defaults and/or exact
                           text fixes (post_sfz_wrap). When only "wrap" changes, a later run rewrites the wrapper
                           files in place (each to a temp name, then renamed) instead of unpacking again; when
                           this module's bitKlavier map changes, a later run rewrites that file the same way.
            levels  (dict) "bitklavier-sfz" only: this mic set's own noise levels, measured like the defaults
                           (BITKLAVIER_*): {"pedal_db": {take file name: region volume}, "rel_db", "harm_db"}; a
                           value left out keeps the default (a change rewrites the map in place, like the generator)
Entries that share a subdir (VCSL Keys: steinway-b, upright-vcsl, harpsichord) share one download and one unpack.
An instrument's "fetch" may be a list of blocks (a blend whose "layers" come from several downloads); each is
installed as above, and the instrument counts as installed when its file and every layer file exist.
An instrument without a fetch block may carry "copy_from": {"path", "sha256", "bytes"}: a local file (the owner's
own SoundFont) copied to root/<file>.

Engines (not in the registry: fixed here because the engine code depends on them):
  sfizz  libsfizz 1.2.3 + sfizz_render from the openSUSE Build Service xUbuntu_24.04 debs (sfztools' own builds),
         unpacked with dpkg-deb -x into engines_root/sfizz/{lib,bin}. libsfizz.so.1 needs only libc/libstdc++/libm.
  7zip   Ubuntu noble's 7zip 23.01 deb into engines_root/tools/7zip/bin/7za, only when no 7z/7za/7zz is on PATH.

Use: python -m piano_render fetch [IDS...]   (ids: instruments, rooms, or "sfizz"; none = everything)
     python -m piano_render.fetch --status
"""

from __future__ import annotations

import contextlib
import fcntl
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable, Optional

REPO = Path(__file__).resolve().parent.parent
DEFAULT_REGISTRY = REPO / "config" / "piano" / "instruments.json"
CPU_LOCK = Path("~/.cache/screen-machine/cpu.lock").expanduser()
CPU_LOCK_WAIT_S = 1800.0
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0 Safari/537.36")
MARKER = ".fetched.json"
CHUNK = 1 << 20
RETRIES = 8

OBS = "https://download.opensuse.org/repositories/home:/sfztools:/sfizz/xUbuntu_24.04/amd64/"
ENGINES = {
    "sfizz": {
        "dir": "sfizz",
        "check": "lib/libsfizz.so.1",
        "version": "1.2.3",
        "debs": [
            {"url": OBS + "libsfizz1_1.2.3-0_amd64.deb", "bytes": 797958,
             "sha256": "7de46ddc85d6ec710b7e660f484d81b6edd39d104e3367f7ce6a70f1fc16e7f4"},
            {"url": OBS + "sfizz_1.2.3-0_amd64.deb", "bytes": 3368080,
             "sha256": "4424135807cb4210fe2c332faf7cdfbb70605bba3f8a81fc1857360a59af04bd"},
        ],
        # deb path -> engine path; the LV2/VST3/Pd plugins and sfizz_jack are left out (nothing here hosts them).
        "layout": [("usr/lib/libsfizz.so.1.2.3", "lib/libsfizz.so.1.2.3"),
                   ("usr/lib/libsfizz.so.1", "lib/libsfizz.so.1"),
                   ("usr/bin/sfizz_render", "bin/sfizz_render"),
                   ("usr/lib/lv2/sfizz.lv2/LICENSE.md", "LICENSE.md")],
    },
    "7zip": {
        "dir": "tools/7zip",
        "check": "bin/7za",
        "version": "23.01+dfsg-11",
        "debs": [
            {"url": "http://archive.ubuntu.com/ubuntu/pool/universe/7/7zip/7zip_23.01+dfsg-11_amd64.deb",
             "bytes": 1846156, "sha256": "0f79450d81e64326a862a8645ae1613890e486115101e51e41ca07d929388d87"},
        ],
        "layout": [("usr/lib/7zip/7za", "bin/7za"), ("usr/share/doc/7zip/copyright", "copyright")],
    },
}

Log = Callable[[str], None]


class FetchError(RuntimeError):
    pass


# ---------------------------------------------------------------- registry

def load_registry(registry_path=None) -> tuple[dict, Path, Path]:
    """(raw registry dict, root, engines_root) with ~ expanded."""
    path = Path(registry_path) if registry_path else DEFAULT_REGISTRY
    with open(path, encoding="utf-8") as f:
        reg = json.load(f)
    root = Path(os.path.expanduser(reg.get("root", "~/.local/share/screen-machine/piano/instruments")))
    engines_root = Path(os.path.expanduser(reg.get("engines_root", "~/.local/share/screen-machine/piano/engines")))
    return reg, root, engines_root


def _entries(reg: dict) -> dict[str, tuple[str, dict]]:
    """id -> (kind, entry) for every instrument and room."""
    out: dict[str, tuple[str, dict]] = {}
    for rid, room in (reg.get("rooms") or {}).items():
        out[rid] = ("room", room)
    for iid, inst in (reg.get("instruments") or {}).items():
        if iid in out:
            raise FetchError(f"id {iid!r} is both a room and an instrument")
        out[iid] = ("instrument", inst)
    return out


def _target_file(kind: str, entry: dict) -> Optional[str]:
    return entry.get("ir") if kind == "room" else entry.get("file")


def _target_files(kind: str, entry: dict) -> list[str]:
    """Every file the entry needs under root: its file (or IR) and, for a blend, each layer's file."""
    f = _target_file(kind, entry)
    out = [f] if f else []
    if kind == "instrument":
        out += [layer.get("file") or "" for layer in entry.get("layers") or [] if isinstance(layer, dict)]
    return out


def _blocks(entry: dict) -> list[dict]:
    """The entry's fetch blocks: none, one, or a list (a blend whose layers come from several downloads)."""
    b = entry.get("fetch")
    if not b:
        return []
    return [x for x in b if x] if isinstance(b, list) else [b]


# ---------------------------------------------------------------- priority and the shared CPU lock

def lower_priority() -> None:
    """nice 19 + SCHED_IDLE for this thread (and children): unpacks must never starve the live streams."""
    with contextlib.suppress(Exception):
        os.nice(19 - os.nice(0))
    with contextlib.suppress(Exception):
        os.sched_setscheduler(0, os.SCHED_IDLE, os.sched_param(0))


@contextlib.contextmanager
def cpu_lock(log: Log, wait_s: float = CPU_LOCK_WAIT_S):
    if os.environ.get("SM_CPU_LOCK_HELD"):
        yield
        return
    CPU_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with open(CPU_LOCK, "a") as fd:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log(f"  waiting for {CPU_LOCK} (another heavy job is running)")
            _flock_wait(fd, wait_s)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)


def _flock_wait(fd, wait_s: float) -> None:
    """A blocking flock (queued with the other waiters, like flock -w) with a timeout: SIGALRM in the main thread,
    else LOCK_NB polling."""
    import signal
    import threading
    if threading.current_thread() is threading.main_thread():
        def _timeout(_sig, _frm):
            raise TimeoutError
        prev = signal.signal(signal.SIGALRM, _timeout)
        signal.setitimer(signal.ITIMER_REAL, wait_s)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
        except TimeoutError:
            raise FetchError(f"{CPU_LOCK} busy for {wait_s:.0f} s") from None
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, prev)
        return
    t0 = time.monotonic()
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() - t0 > wait_s:
                raise FetchError(f"{CPU_LOCK} busy for {wait_s:.0f} s") from None
            time.sleep(0.2)


# ---------------------------------------------------------------- download

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(4 * CHUNK)
            if not b:
                return h.hexdigest()
            h.update(b)


def url_basename(url: str) -> str:
    if url.startswith("magnet:"):  # a BitTorrent magnet: its dn= (display name) is the file's name
        dn = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get("dn") or [""]
        return PurePosixPath(dn[0].replace("\\", "/")).name or "download"
    return urllib.parse.unquote(PurePosixPath(urllib.parse.urlsplit(url).path).name) or "download"


def _cache_name(url: str, sha256: Optional[str]) -> str:
    base = url_basename(url)
    base = re.sub(r"[^A-Za-z0-9._+-]", "_", base)
    return f"{sha256[:12]}-{base}" if sha256 else base


def download(url: str, dest: Path, sha256: Optional[str] = None, size: Optional[int] = None,
             log: Log = print, label: str = "") -> Path:
    """Download url to dest (resuming dest.part), verify size + sha256, return dest. A good dest is kept."""
    label = label or dest.name
    if dest.exists():
        if (size is None or dest.stat().st_size == size) and (not sha256 or sha256_file(dest) == sha256):
            return dest
        log(f"  {label}: cached file is wrong, downloading again")
        dest.unlink()
    if url.startswith("magnet:"):
        raise FetchError(f"{label}: BitTorrent only (no direct download): fetch {url_basename(url)} with a BitTorrent "
                         f"client from the magnet in the registry, then put it at {dest}"
                         + (f" (sha256 {sha256})" if sha256 else ""))
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    last_err: Optional[BaseException] = None
    for attempt in range(1, RETRIES + 1):
        have = part.stat().st_size if part.exists() else 0
        if size is not None and have > size:
            part.unlink()
            have = 0
        if size is not None and have == size:
            break
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
        if have:
            req.add_header("Range", f"bytes={have}-")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                if have and r.status != 206:
                    have = 0  # the server ignored the range: start again
                total = size or (int(r.headers.get("Content-Length", 0)) + have) or None
                mode = "ab" if have else "wb"
                t_last = time.monotonic()
                got0, got = have, have
                t0 = t_last
                with open(part, mode) as f:
                    while True:
                        b = r.read(CHUNK)
                        if not b:
                            break
                        f.write(b)
                        got += len(b)
                        now = time.monotonic()
                        if now - t_last >= 10.0:
                            rate = (got - got0) / max(1e-3, now - t0) / 1e6
                            tot = f"/{total / 1e6:.0f}" if total else ""
                            log(f"  {label}: {got / 1e6:.0f}{tot} MB, {rate:.1f} MB/s")
                            t_last = now
            if size is None or part.stat().st_size == size:
                break
            last_err = FetchError(f"short read: {part.stat().st_size} of {size} bytes")
        except urllib.error.HTTPError as e:
            if e.code == 416 and size is not None and part.exists() and part.stat().st_size == size:
                break
            last_err = e
            if e.code in (403, 404, 410):
                raise FetchError(f"{url}: HTTP {e.code}") from e
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last_err = e
        wait = min(60, 5 * attempt)
        log(f"  {label}: attempt {attempt} failed ({last_err}); retrying in {wait} s")
        time.sleep(wait)
    else:
        raise FetchError(f"{url}: gave up after {RETRIES} attempts ({last_err})")
    n = part.stat().st_size
    if size is not None and n != size:
        raise FetchError(f"{url}: {n} bytes, expected {size}")
    digest = sha256_file(part)
    if sha256 and digest != sha256:
        part.unlink()
        raise FetchError(f"{url}: sha256 {digest}, expected {sha256}")
    if not sha256:
        log(f"  {label}: UNVERIFIED (no sha256 in the registry): sha256 {digest}, {n} bytes")
    os.replace(part, dest)
    return dest


# ---------------------------------------------------------------- unpack

def _rel(name: str, strip: int) -> Optional[str]:
    parts = [p for p in PurePosixPath(name.replace("\\", "/")).parts if p not in ("", ".")]
    if any(p == ".." for p in parts) or (parts and parts[0] == "/"):
        raise FetchError(f"unsafe archive member {name!r}")
    if any(p == "__MACOSX" for p in parts) or (parts and parts[-1].startswith("._")):
        return None  # macOS resource-fork litter
    parts = parts[strip:]
    return "/".join(parts) if parts else None


def _wanted(rel: str, members: Optional[list]) -> bool:
    return not members or any(fnmatch.fnmatchcase(rel, p) for p in members)


def _move_tree(src: Path, dst: Path, strip: int, members: Optional[list]) -> int:
    """Move regular files from src into dst applying strip/members; returns the file count."""
    n = 0
    for dirpath, _dirs, files in os.walk(src):
        for fn in files:
            p = Path(dirpath) / fn
            if p.is_symlink() or not p.is_file():
                continue
            rel = _rel(p.relative_to(src).as_posix(), strip)
            if rel is None or not _wanted(rel, members):
                continue
            t = dst / rel
            t.parent.mkdir(parents=True, exist_ok=True)
            os.replace(p, t)
            n += 1
    return n


def seven_zip(engines_root: Path, log: Log = print) -> str:
    for name in ("7zz", "7za", "7z"):
        p = shutil.which(name)
        if p:
            return p
    p = engines_root / ENGINES["7zip"]["dir"] / ENGINES["7zip"]["check"]
    if not p.exists():
        install_engine("7zip", engines_root, log)
    return str(p)


def unpack(archive: Path, kind: str, dst: Path, *, strip: int = 0, members: Optional[list] = None,
           engines_root: Optional[Path] = None, name: Optional[str] = None, log: Log = print) -> int:
    """Unpack archive into the (new, empty) dir dst; returns the number of files written."""
    import tarfile
    import zipfile
    dst.mkdir(parents=True, exist_ok=True)
    n = 0
    if kind == "zip":
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                rel = _rel(info.filename, strip)
                if rel is None or not _wanted(rel, members):
                    continue
                t = dst / rel
                t.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, open(t, "wb") as out:
                    shutil.copyfileobj(src, out, CHUNK)
                n += 1
    elif kind == "tar":
        with tarfile.open(archive, "r:*") as t:
            for m in t:
                if not m.isfile():
                    continue
                rel = _rel(m.name, strip)
                if rel is None or not _wanted(rel, members):
                    continue
                p = dst / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                src = t.extractfile(m)
                assert src is not None
                with src, open(p, "wb") as out:
                    shutil.copyfileobj(src, out, CHUNK)
                n += 1
    elif kind in ("7z", "deb"):
        raw = Path(tempfile.mkdtemp(prefix=".raw-", dir=dst.parent))
        try:
            if kind == "7z":
                exe = seven_zip(engines_root or Path("~/.local/share/screen-machine/piano/engines").expanduser(), log)
                cmd = [exe, "x", "-y", "-bd", "-mmt=1", f"-o{raw}", str(archive)]
            else:
                if not shutil.which("dpkg-deb"):
                    raise FetchError("dpkg-deb is needed to unpack a .deb")
                cmd = ["dpkg-deb", "-x", str(archive), str(raw)]
            r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            if r.returncode != 0:
                raise FetchError(f"{' '.join(cmd[:2])} failed: {r.stdout[-800:]}")
            n = _move_tree(raw, dst, strip, members)
        finally:
            shutil.rmtree(raw, ignore_errors=True)
    elif kind == "none":
        shutil.copyfile(archive, dst / (name or archive.name))
        n = 1
    else:
        raise FetchError(f"unknown unpack type {kind!r}")
    if n == 0:
        raise FetchError(f"{archive.name}: nothing unpacked (check strip/members)")
    return n


# ---------------------------------------------------------------- post-unpack recipes

_NOTE = {"C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5, "F#": 6, "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11}


def _midi(name: str, octave: str) -> int:
    return 12 * (int(octave) + 1) + _NOTE[name]


def write_text_atomic(path: Path, text: str) -> bool:
    """Write text to a temp name beside path, then rename it over path (a reader sees the old file or the new).
    A file that already holds exactly this text is left alone (its mtime keys the realism analysis cache), and
    False is returned."""
    with contextlib.suppress(OSError):
        if path.read_text(encoding="utf-8") == text:
            return False
    tmp = path.with_name(f".{path.name}.tmp{os.getpid()}")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
    return True


def post_sfz_wrap(dst: Path, block: dict, log: Log = print) -> Path:
    """Write an SFZ beside a library's own (so its relative paths still resolve) with fixed defaults.
    block["wrap"] (one spec, or a list of them for a subdir several instruments share) =
                    {"base": SFZ under the subdir, "out": the new SFZ (same dir), "note": why, and
      "set_cc": {cc: value}   controller defaults (a <control> after the library's own), e.g. Accurate-Salamander's
                              release, hammer and pedal noise and pedal resonance, which ship switched off (CC20-23);
      "global": "opcodes"     region opcodes put in force for the whole library: inserted after each <global> header
                              of base (or a <global> of their own on top when it has none), e.g. "sustain_lo=64":
                              sfizz's sustain pedal is on/off at sustain_lo, default 1, so without it any CC64 > 0
                              (the classical test pieces' half pedalling) held every note;
      "inline": [path, ...]   files base #includes (the path exactly as base writes it) copied into out in place of
                              their #include line, first, so "replace" can fix text a library keeps in an included
                              file (Maestro CF3's pedal and lid noise regions are in Data/effect.txt); each #include
                              line must occur exactly once in base;
      "replace": [{"old", "new", "count"}]   exact text fixes to base, applied in order before "global" (line ends
                              read as \\n); "old" must occur exactly "count" times (default: at least once), else
                              the fetch fails, so a changed library is never patched blindly. E.g. K18's pedal-noise
                              groups set lokey=-1 without hikey=-1, so sfizz played pedal.on.wav on every key.}
    Without "global", "inline" or "replace" the out file just #includes base; with any, out is a patched copy of
    base. Each out file is written to a temp name and renamed, so a renderer never reads half a file."""
    spec = block.get("wrap") or {}
    if isinstance(spec, list):
        outs = [post_sfz_wrap(dst, {"wrap": w}, log) for w in spec]
        return outs[-1]
    w = spec
    base, out = dst / w["base"], dst / w["out"]
    if not base.exists():
        raise FetchError(f"sfz-wrap: {w['base']} not found")
    if base.parent != out.parent or base == out:
        raise FetchError("sfz-wrap: out must be a new file beside base (the library's paths are relative to it)")
    cc = " ".join(f"set_cc{int(k)}={int(v)}" for k, v in sorted(w.get("set_cc", {}).items(), key=lambda kv: int(kv[0])))
    head = "// written by piano_render/fetch.py (sfz-wrap): " + (w.get("note") or f"{base.name} with new defaults")
    g = (w.get("global") or "").strip()
    reps = w.get("replace") or []
    inline = w.get("inline") or []
    if g or reps or inline:
        body = base.read_text(encoding="utf-8", errors="replace")
        for rel in inline:
            line = f'#include "{rel}"'
            inc = base.parent / rel.replace("\\", "/")
            n = body.count(line)
            if n != 1 or not inc.is_file():
                raise FetchError(f"sfz-wrap: {base.name}: {line!r} occurs {n} times (expected 1)"
                                 + ("" if inc.is_file() else f", and {rel} is missing"))
            body = body.replace(line, inc.read_text(encoding="utf-8", errors="replace").rstrip("\n"))
        for r in reps:
            n, want = body.count(r["old"]), r.get("count")
            if n == 0 or (want is not None and n != int(want)):
                raise FetchError(f"sfz-wrap: {base.name}: {r['old']!r} occurs {n} times, expected "
                                 f"{want if want is not None else 'at least 1'}")
            body = body.replace(r["old"], r["new"])
        if g:
            body, n = re.subn(r"<global>", lambda m: f"<global> {g}", body)
            if n == 0:
                body = f"<global> {g}\n" + body
        text = [head, body]
    else:
        text = [head, f'#include "{base.name}"']
    if cc:
        text += ["", "<control>", cc]
    wrote = write_text_atomic(out, "\n".join(text) + "\n")
    log(f"  sfz-wrap: {out.name} = {base.name}" + (f" + {len(inline)} inlined" if inline else "")
        + (f" + {len(reps)} fix(es)" if reps else "") + (f" + <global> {g}" if g else "") + (f" + {cc}" if cc else "")
        + ("" if wrote else " (unchanged)"))
    return out


# bitKlavier Grand's mechanical noises (measured 22 Oct through sfizz on the Stage Lid ORTF set, in dB relative to
# the C4 velocity-64 note's peak; research r4 item 9: mechanical noises at least ~35 dB under a mezzo-forte note).
# Pedal takes: region volumes that put each take 38 dB under that note (the two takes of each pedal were recorded
# 4-17 dB apart; before: down -28.7/-45.5, up -15.7/-19.6). A take not listed (another mic set) keeps the old level.
BITKLAVIER_PEDAL_DB = {"pedalD1.wav": 1.5, "pedalD2.wav": -15.3, "pedalU1.wav": -27.4, "pedalU2.wav": -31.2}
BITKLAVIER_PEDAL_FALLBACK_DB = {"D": -6.0, "U": -9.0}
# Key-release noise: 35 dB under the note at velocity 64 (before: -12.8, and -14.6 after a velocity-20 C4, louder than
# that soft note), fired at the key's release even with the pedal down (trigger=release_key): with trigger=release
# sfizz held every one back to the pedal lift and fired them together (8 notes: -0.8 dB, as loud as the mf note).
BITKLAVIER_REL_DB = -28.2
# Release resonance: 6 dB down, and losing 3 (A0) to 8 (C8) dB per second the key was down (was 0 dB and a flat
# 2 dB/s: after 2-8 s under the pedal it came in 8-16 dB above the decayed strings it followed when the pedal lifted).
BITKLAVIER_HARM_DB = -6.0
BITKLAVIER_HARM_RT_DECAY = (3.0, 8.0)


def bitklavier_sfz_text(dst: Path, levels: Optional[dict] = None) -> tuple[str, dict]:
    """The SFZ map for a bitKlavier Grand mic set unpacked in dst (post_bitklavier_sfz), and the sample counts.
    levels (a fetch block's "levels"): this mic set's own {"pedal_db": {take: volume}, "rel_db", "harm_db"} in place of
    the BITKLAVIER_* defaults (each mic position hears the mechanical noises at its own level against the notes)."""
    lv = levels or {}
    pedal_db = {**BITKLAVIER_PEDAL_DB, **(lv.get("pedal_db") or {})}
    rel_db = float(lv.get("rel_db", BITKLAVIER_REL_DB))
    harm_db = float(lv.get("harm_db", BITKLAVIER_HARM_DB))
    wavs: dict[str, str] = {}
    for p in dst.rglob("*.wav"):
        if not p.name.startswith("._"):
            wavs.setdefault(p.name, p.relative_to(dst).as_posix())
    main: dict[int, dict[int, str]] = {}
    harm: dict[int, dict[int, str]] = {}
    rel: dict[int, str] = {}
    pedal: dict[str, dict[int, str]] = {"D": {}, "U": {}}
    for name, path in wavs.items():
        if m := re.fullmatch(r"(harm)?([A-G]#?)(-?\d)v(\d+)\.wav", name):
            (harm if m.group(1) else main).setdefault(_midi(m.group(2), m.group(3)), {})[int(m.group(4))] = path
        elif m := re.fullmatch(r"rel(\d+)\.wav", name):
            rel[int(m.group(1)) + 20] = path
        elif m := re.fullmatch(r"pedal([DU])(\d+)\.wav", name):
            pedal[m.group(1)][int(m.group(2))] = path
    if len(main) < 20:
        raise FetchError(f"bitklavier-sfz: only {len(main)} sampled keys found under {dst}")

    def ranges(keys: list[int]) -> list[tuple[int, int, int]]:
        """(lokey, hikey, center): each sample covers the keys nearest to it (minor thirds: center -1..+1)."""
        keys = sorted(keys)
        out = []
        for i, k in enumerate(keys):
            lo = 21 if i == 0 else (keys[i - 1] + k) // 2 + 1
            hi = 108 if i == len(keys) - 1 else (k + keys[i + 1]) // 2
            out.append((lo, hi, k))
        return out

    def vel_split(layers: dict[int, str]) -> list[tuple[int, int, str]]:
        """Velocity layers split evenly over 1..127 (bitKlavier balanced them to be evenly spaced, soft to loud)."""
        vs = sorted(layers)
        n = len(vs)
        out = []
        for i, v in enumerate(vs):
            lo = 1 if i == 0 else round(i * 127 / n) + 1
            hi = round((i + 1) * 127 / n)
            out.append((lo, hi, layers[v]))
        return out

    L = ["// bitKlavier Grand (Steinway D, Princeton, CC-BY 4.0): SFZ map written by piano_render/fetch.py from the",
         "// library's documented file naming. The 16 layers carry the dynamics (bitKlavier balanced them about",
         "// 1.7-2.2 dB apart, measured 22 Oct); amp_velcurve makes each rise 1.9 dB across its own velocity span",
         "// (sfizz applies a velcurve only through amp_veltrack, hence 100). The noise levels are measured through",
         "// sfizz against the C4 velocity-64 note (fetch.py, BITKLAVIER_*): pedal takes 38 dB under it, key release",
         "// 35 dB under it at velocity 64.",
         "<control>", "default_path=./", "",
         "<global>", "amp_veltrack=100", "ampeg_release=0.5",
         "// sfizz's sustain pedal is on/off at sustain_lo (default 1, so any CC64 > 0 held every note): 64 = GM",
         "sustain_lo=64", "", "<group>", "// main notes", "note_polyphony=2"]
    for lo, hi, k in ranges(list(main)):
        for vlo, vhi, path in vel_split(main[k]):
            L.append(f"<region> sample={path} lokey={lo} hikey={hi} pitch_keycenter={k} lovel={vlo} hivel={vhi}"
                     f" amp_velcurve_{vlo}=0.8 amp_velcurve_{vhi}=1")
    if harm:
        d0, d1 = BITKLAVIER_HARM_RT_DECAY
        L += ["", "<group>", "// release resonance: the ring after a key comes up (played when the damper falls, so after",
              "// the pedal lifts), quieter the longer the key was held (rt_decay, faster in the treble)",
              "trigger=release", f"volume={harm_db:g}", "amp_veltrack=0"]
        for lo, hi, k in ranges(list(harm)):
            rt = d0 + (d1 - d0) * (k - 21) / 87
            for vlo, vhi, path in vel_split(harm[k]):
                L.append(f"<region> sample={path} lokey={lo} hikey={hi} pitch_keycenter={k}"
                         f" lovel={vlo} hivel={vhi} rt_decay={rt:.1f}")
    if rel:
        L += ["", "<group>", "// key-release noise (hammer and key return), one per key, at the key's release even with",
              "// the pedal down", "trigger=release_key", f"volume={rel_db:g}", "amp_veltrack=50",
              "pitch_keytrack=0"]
        for k in sorted(rel):
            L.append(f"<region> sample={rel[k]} key={k} pitch_keycenter={k}")
    # pedal noise as Accurate-Salamander does it: down on CC64 = 127, up on CC64 = 0 (so continuous half-pedal
    # data does not fire a thump on every change), one of the two takes at random, one at a time; each take has its
    # own volume (BITKLAVIER_PEDAL_DB)
    for kind, head in (("D", "group=901 off_by=902 polyphony=1 lokey=-1 hikey=-1 on_locc64=127 on_hicc64=127"),
                       ("U", "group=902 off_by=901 polyphony=1 lokey=-1 hikey=-1 on_locc64=0 on_hicc64=0")):
        if not pedal[kind]:
            continue
        L += ["", f"<group> {head} pitch_keytrack=0 amp_veltrack=0"]
        vs = sorted(pedal[kind])
        for i, v in enumerate(vs):
            path = pedal[kind][v]
            vol = pedal_db.get(PurePosixPath(path).name, BITKLAVIER_PEDAL_FALLBACK_DB[kind])
            L.append(f"<region> sample={path} lorand={i / len(vs):.3f} hirand={(i + 1) / len(vs):.3f} volume={vol:g}")
    counts = {"main": sum(len(v) for v in main.values()), "resonance": sum(len(v) for v in harm.values()),
              "release": len(rel), "pedal": sum(len(v) for v in pedal.values())}
    return "\n".join(L) + "\n", counts


BITKLAVIER_OUT = "bitKlavierGrand.sfz"


def post_bitklavier_sfz(dst: Path, block: dict, log: Log = print) -> Path:
    """Write an SFZ for a bitKlavier Grand mic set, from its documented file naming (bitklavier.com, 'File
    Naming Convention'): [note][octave]v[vel].wav main notes (16 layers every minor third from A0),
    harm[note][octave]v[vel].wav release resonance (the ring after key release), rel[1-88].wav one hammer/damper
    release noise per key, pedal[D|U][vel].wav pedal down/up noise. bitKlavier publishes no SFZ of its own.
    Written to a temp name and renamed; refresh_generated() rewrites an installed map when this code changes."""
    text, n = bitklavier_sfz_text(dst, block.get("levels"))
    out = dst / BITKLAVIER_OUT
    write_text_atomic(out, text)
    log(f"  bitklavier-sfz: {n['main']} main, {n['resonance']} resonance, {n['release']} release, {n['pedal']} pedal"
        f" samples -> {out.name}")
    return out


def refresh_generated(dst: Path, block: dict, log: Log = print) -> bool:
    """An installed subdir whose SFZ map this module writes (post "bitklavier-sfz"): write the map again when the
    generator's output differs from the installed file (a fix to the generator reaches the install without a new
    unpack). True when it was rewritten."""
    if block.get("post") != "bitklavier-sfz":
        return False
    text, _ = bitklavier_sfz_text(dst, block.get("levels"))
    out = dst / BITKLAVIER_OUT
    if not write_text_atomic(out, text):
        return False
    log(f"  bitklavier-sfz: {out.name} rewritten (the generator changed; the unpacked library is unchanged)")
    return True


POST = {"bitklavier-sfz": post_bitklavier_sfz, "sfz-wrap": post_sfz_wrap}


# ---------------------------------------------------------------- engines

def engine_path(name: str, engines_root: Path) -> Path:
    e = ENGINES[name]
    return engines_root / e["dir"] / e["check"]


def install_engine(name: str, engines_root: Path, log: Log = print) -> str:
    e = ENGINES[name]
    dst = engines_root / e["dir"]
    marker = dst / MARKER
    if (dst / e["check"]).exists() and marker.exists():
        with contextlib.suppress(Exception):
            if json.loads(marker.read_text()).get("version") == e["version"]:
                return "present"
    log(f"{name}: installing engine {e['version']} into {dst}")
    debs = [download(d["url"], engines_root / "_downloads" / _cache_name(d["url"], d["sha256"]), d["sha256"],
                     d["bytes"], log, f"{name} deb") for d in e["debs"]]
    if not shutil.which("dpkg-deb"):
        raise FetchError("dpkg-deb is needed to unpack the engine debs")
    dst.parent.mkdir(parents=True, exist_ok=True)
    raw = Path(tempfile.mkdtemp(prefix=f".{name}-raw-", dir=dst.parent))
    new = Path(tempfile.mkdtemp(prefix=f".{name}-new-", dir=dst.parent))
    try:
        for deb in debs:
            r = subprocess.run(["dpkg-deb", "-x", str(deb), str(raw)], stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
            if r.returncode != 0:
                raise FetchError(f"dpkg-deb -x {deb.name}: {r.stdout[-800:]}")
        for src_rel, dst_rel in e["layout"]:
            src = Path(os.path.normpath(raw / src_rel))
            t = new / dst_rel
            t.parent.mkdir(parents=True, exist_ok=True)
            if src.is_symlink():
                os.symlink(os.readlink(src), t)
            elif src.exists():
                shutil.copy2(src, t)
            elif dst_rel.startswith(("lib/", "bin/")):
                raise FetchError(f"{name}: {src_rel} missing from the debs")
        marker_data = {"engine": name, "version": e["version"], "debs": e["debs"], "installed": time.time()}
        (new / MARKER).write_text(json.dumps(marker_data, indent=1))
        os.chmod(new, 0o755)
        old = None
        if dst.exists():
            old = dst.with_name(f".{dst.name}.old-{os.getpid()}")
            os.replace(dst, old)
        os.replace(new, dst)
        if old:
            shutil.rmtree(old, ignore_errors=True)
    finally:
        shutil.rmtree(raw, ignore_errors=True)
        shutil.rmtree(new, ignore_errors=True)
    return "installed"


# ---------------------------------------------------------------- instruments and rooms

def _block_key(block: dict) -> tuple:
    return (block.get("url"), block.get("sha256"), block.get("unpack"), int(block.get("strip", 0)),
            tuple(block.get("members") or ()), block.get("post"), json.dumps(block.get("wrap"), sort_keys=True),
            json.dumps(block.get("levels"), sort_keys=True))


def _marker(dst: Path) -> Optional[dict]:
    m = dst / MARKER
    if not m.exists():
        return None
    try:
        d = json.loads(m.read_text())
    except Exception:
        return None
    return d if isinstance(d, dict) else None


def _same_unpack(d: dict, block: dict) -> bool:
    return (d.get("url") == block.get("url") and d.get("sha256") == block.get("sha256")
            and d.get("strip", 0) == int(block.get("strip", 0)) and d.get("members") == block.get("members")
            and d.get("post") == block.get("post"))


def _subdir_ok(dst: Path, block: dict) -> bool:
    d = _marker(dst)
    return d is not None and _same_unpack(d, block) and d.get("wrap") == block.get("wrap")


def rewrap(dst: Path, block: dict, log: Log = print) -> bool:
    """An installed sfz-wrap subdir whose only change is its "wrap" spec: write the wrapper SFZs again in place
    (each to a temp name, then renamed: a render running meanwhile reads the old file or the new one) and record
    the new spec in the marker. False (nothing done) when the unpack itself differs."""
    d = _marker(dst)
    if d is None or block.get("post") != "sfz-wrap" or not _same_unpack(d, block):
        return False
    post_sfz_wrap(dst, block, log)
    d["wrap"] = block.get("wrap")
    d["rewrapped"] = time.time()
    tmp = dst / f".{MARKER}.tmp{os.getpid()}"
    tmp.write_text(json.dumps(d, indent=1))
    os.replace(tmp, dst / MARKER)
    return True


def fetch_block(block: dict, root: Path, engines_root: Path, log: Log = print, label: str = "") -> str:
    """Download + unpack one fetch block into root/<subdir>; 'present' when already done."""
    sub = block["subdir"].strip("/")
    if not sub or ".." in PurePosixPath(sub).parts:
        raise FetchError(f"bad subdir {sub!r}")
    dst = root / sub
    if _subdir_ok(dst, block):
        if refresh_generated(dst, block, log):
            log(f"{label or sub}: SFZ map rewritten in {dst}")
            return "regenerated"
        return "present"
    if rewrap(dst, block, log):
        log(f"{label or sub}: wrapper rewritten in {dst} (the unpacked library is unchanged)")
        return "rewrapped"
    url, sha, size = block["url"], block.get("sha256"), block.get("bytes")
    log(f"{label or sub}: fetching {url}" + (f" ({size / 1e6:.0f} MB)" if size else ""))
    archive = download(url, root / "_downloads" / _cache_name(url, sha), sha, size, log, label or sub)
    with cpu_lock(log):
        dst.parent.mkdir(parents=True, exist_ok=True)
        _clean_stale(dst)
        tmp = Path(tempfile.mkdtemp(prefix=f".{dst.name}-new-", dir=dst.parent))
        try:
            t0 = time.monotonic()
            n = unpack(archive, block.get("unpack", "none"), tmp, strip=int(block.get("strip", 0)),
                       members=block.get("members"), engines_root=engines_root,
                       name=block.get("name") or url_basename(url), log=log)
            if block.get("post"):
                if block["post"] not in POST:
                    raise FetchError(f"unknown post recipe {block['post']!r}")
                POST[block["post"]](tmp, block, log)
            marker = {k: block.get(k) for k in ("url", "sha256", "bytes", "unpack", "members", "post", "wrap")}
            marker["strip"] = int(block.get("strip", 0))
            marker["files"] = n
            marker["unpacked"] = time.time()
            (tmp / MARKER).write_text(json.dumps(marker, indent=1))
            os.chmod(tmp, 0o755)  # mkdtemp makes it 0700
            old = None
            if dst.exists():
                old = dst.with_name(f".{dst.name}.old-{os.getpid()}")
                os.replace(dst, old)
            os.replace(tmp, dst)
            if old:
                shutil.rmtree(old, ignore_errors=True)
            log(f"{label or sub}: {n} files unpacked into {dst} in {time.monotonic() - t0:.0f} s")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return "installed"


def _clean_stale(dst: Path) -> None:
    """Remove temp dirs an interrupted run left next to dst (safe: fetch() holds the fetch lock)."""
    for p in dst.parent.glob(f".{dst.name}-new-*"):
        shutil.rmtree(p, ignore_errors=True)
    for p in dst.parent.glob(f".{dst.name}.old-*"):
        shutil.rmtree(p, ignore_errors=True)
    for p in dst.parent.glob(".raw-*"):
        shutil.rmtree(p, ignore_errors=True)


def copy_local(entry: dict, root: Path, log: Log = print, label: str = "") -> str:
    c = entry["copy_from"]
    src = Path(c["path"])
    dst = root / entry["file"]
    if dst.exists() and (not c.get("bytes") or dst.stat().st_size == c["bytes"]):
        return "present"
    if not src.exists():
        raise FetchError(f"local source {src} not found")
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".part")
    shutil.copyfile(src, tmp)
    if c.get("sha256") and sha256_file(tmp) != c["sha256"]:
        tmp.unlink()
        raise FetchError(f"{src}: sha256 differs from the registry")
    os.replace(tmp, dst)
    log(f"{label}: copied {src} -> {dst}")
    return "installed"


def status(registry_path=None) -> dict[str, str]:
    """id -> 'ok' | 'missing' | 'no-file' (windows-gs, synthetic rooms) without touching the network."""
    reg, root, engines_root = load_registry(registry_path)
    out = {"sfizz": "ok" if engine_path("sfizz", engines_root).exists() else "missing"}
    for eid, (kind, entry) in _entries(reg).items():
        fs = _target_files(kind, entry)
        out[eid] = "no-file" if not fs else ("ok" if all(f and (root / f).exists() for f in fs) else "missing")
    return out


def fetch(ids: Optional[Iterable[str]] = None, registry_path=None, *, log: Log = print,
          lower: bool = True) -> dict[str, str]:
    """Install what the registry names (all of it, or `ids`: instrument ids, room ids, 'sfizz'), idempotently.
    Returns id -> 'present' | 'installed' | 'rewrapped' | 'regenerated' | 'skipped: ...' | 'failed: ...'. Never raises
    for one bad entry."""
    if lower:
        lower_priority()
    reg, root, engines_root = load_registry(registry_path)
    entries = _entries(reg)
    want = list(ids) if ids else ["sfizz", *entries]
    unknown = [i for i in want if i != "sfizz" and i not in entries]
    if unknown:
        raise FetchError(f"unknown ids: {', '.join(unknown)} (known: {', '.join(entries)})")
    results: dict[str, str] = {}
    lock_path = root / "_downloads" / ".fetch.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)  # one fetch at a time (a second waits, then finds everything present)
        need_sfizz = "sfizz" in want or any(entries[i][1].get("engine") == "sfizz" for i in want if i in entries)
        if need_sfizz:
            try:
                results["sfizz"] = install_engine("sfizz", engines_root, log)
            except Exception as e:  # noqa: BLE001
                results["sfizz"] = f"failed: {e}"
        by_sub: dict[str, tuple] = {}
        for eid in want:
            if eid == "sfizz":
                continue
            kind, entry = entries[eid]
            blocks = _blocks(entry)
            try:
                if blocks:
                    sts = []
                    for block in blocks:
                        k = _block_key(block)
                        sub = block["subdir"].strip("/")
                        if sub in by_sub and by_sub[sub][0] != k:
                            raise FetchError(f"subdir {sub!r} is used by {by_sub[sub][1]} with a different fetch block")
                        if sub in by_sub:
                            sts.append(by_sub[sub][2])
                        else:
                            st = fetch_block(block, root, engines_root, log, eid)
                            by_sub[sub] = (k, eid, st)
                            sts.append(st)
                    results[eid] = next((st for st in sts if st != "present"), "present")
                elif entry.get("copy_from"):
                    results[eid] = copy_local(entry, root, log, eid)
                else:
                    results[eid] = "present" if not _target_file(kind, entry) else "skipped: no fetch block"
                for f in _target_files(kind, entry):
                    if not f or not (root / f).exists():
                        results[eid] = f"failed: {f or '(a layer without a file)'} not found under {root} after install"
                        break
            except Exception as e:  # noqa: BLE001
                results[eid] = f"failed: {e}"
                log(f"{eid}: FAILED: {e}")
    return results


def main(argv: Optional[list] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m piano_render fetch", description=__doc__.split("\n")[0])
    ap.add_argument("ids", nargs="*", help="instrument ids, room ids or 'sfizz' (default: everything)")
    ap.add_argument("--registry", default=None, help="instruments.json (default config/piano/instruments.json)")
    ap.add_argument("--status", action="store_true", help="only report what is installed (no network)")
    a = ap.parse_args(argv)
    if a.status:
        for k, v in status(a.registry).items():
            print(f"{k:16s} {v}")
        return 0
    res = fetch(a.ids or None, a.registry, log=lambda s: print(s, flush=True))
    print()
    for k, v in res.items():
        print(f"{k:16s} {v}")
    return 1 if any(v.startswith("failed") for v in res.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
