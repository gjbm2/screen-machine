"""karaoke.worker: one thread that makes requested songs singable, one song at a time (docs/KARAOKE.md).

fetching    resolve the request to a recording (YouTube URL, artist, title, length) and download its audio
lyrics      every LRCLIB upload of the song; after separating, Whisper hears the vocal (tools/karaoke/
            transcribe.py), the upload whose words fit the singing is kept, each line timed to where it is sung,
            and each word timed (tools/karaoke/align.py). No upload fits: the lines are what was heard.
separating  tools/karaoke/separate.py in the karaoke venv: instrumental.wav + vocals.wav (GPU, ~30 s a song)
            then both to AAC for the browser, and the lyrics lined up against the vocal
ready       the song can be played

One at a time because separation wants the whole GPU it is given; the live render keeps its frame rate alongside it
(measured 25 Nov). A failure marks the song failed with a reason a person can read; Retry queues it again.
"""
from __future__ import annotations

import json
import shutil
import os
import signal
import subprocess
import tempfile
import threading
import time
import traceback
from pathlib import Path

import smconfig
from utils.logger import error, info

from . import lyrics as lyr
from . import sources as src
from .sources import SourceError
from .store import Store, song_dir, song_key

SEPARATE = smconfig.REPO_ROOT / "tools" / "karaoke" / "separate.py"
ALIGN = smconfig.REPO_ROOT / "tools" / "karaoke" / "align.py"
TRANSCRIBE = smconfig.REPO_ROOT / "tools" / "karaoke" / "transcribe.py"
MIN_HEARD_WORDS = 12      # fewer heard than this: nothing to time lyrics by (an instrumental, or a failed hearing)
MIN_HEARD_PER_MIN = 20    # and at least this many a minute of song: a real vocal, not a few stray words
# Whisper's stock lines on silence or music without words ("Thank you." every 30 s: 26 Nov, The Sound of Silence's
# empty lead stem): never lyrics.
STOCK_HEARD = ("thank you", "thanks for watching", "thank you for watching", "subscribe", "you", "bye",
               "subtitles by", "amara org", "music")
# The lead vocal stem this much (dB) quieter than the backing, or quieter than QUIET_VOCAL_DB: the karaoke model kept
# the singing as "backing" (two equal voices: 26 Nov, Simon & Garfunkel). Then the singing is all taken out instead.
QUIET_VOCAL_GAP_DB = 35.0
QUIET_VOCAL_DB = -60.0
ALL_VOCALS_MODEL = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"   # BS-Roformer Viperx 1297: every voice out


def real_heard(sung: list[dict], duration: float | None) -> list[dict]:
    """What Whisper heard, if it is a real vocal (else []): its stock lines dropped, then enough words for the song's
    length."""
    import re as _re
    norm = lambda w: _re.sub(r"[^a-z ]", "", str(w.get("w") or "").lower()).strip()   # noqa: E731
    stock = set()
    for i, w in enumerate(sung):                         # the stock phrases, as consecutive words
        for phrase in STOCK_HEARD:
            parts = phrase.split()
            if [norm(x) for x in sung[i:i + len(parts)]] == parts:
                stock.update(range(i, i + len(parts)))
    words = [w for i, w in enumerate(sung) if i not in stock]
    minutes = (duration or 0) / 60.0
    if len(words) < MIN_HEARD_WORDS or (minutes and len(words) / minutes < MIN_HEARD_PER_MIN):
        return []
    return words


def mean_db(wav: Path) -> float:
    """A WAV's mean level (dBFS)."""
    import numpy as np
    import soundfile as sf
    total, n = 0.0, 0
    for block in sf.blocks(str(wav), blocksize=1 << 18, dtype="float32", always_2d=True):
        total += float(np.square(block, dtype=np.float64).sum())
        n += block.size
    return 10 * np.log10(total / n + 1e-20) if n else -200.0


def _venv_json(script: Path, args: list[str], timeout: float) -> dict:
    """Run a karaoke tool in the karaoke venv; its last stdout line is its JSON answer."""
    py = smconfig.deployment().host_path("karaoke_python")
    p = subprocess.run([str(py), str(script), *args], capture_output=True, text=True, timeout=timeout)
    last = (p.stdout or "").strip().splitlines()[-1:] or [""]
    if p.returncode != 0 or not last[0].startswith("{"):
        raise RuntimeError(((p.stderr or "").strip().splitlines() or ["no output"])[-1][:300])
    return json.loads(last[0])


def hear(vocal: Path) -> list[dict]:
    """What is sung, word by word: [{w, t0, t1}] in song seconds (tools/karaoke/transcribe.py, Whisper)."""
    return _venv_json(TRANSCRIBE, ["--vocal", str(vocal)], 900)["words"]


def fit_lyrics(cands: list[dict], vocal: Path, duration: float | None, fallback: dict) -> dict:
    """The lyrics document for a recording, timed to its own singing (docs/KARAOKE.md "Lyrics"):
    the LRCLIB upload whose words match the most of what Whisper hears, each line where it is sung; else the lines
    as heard; else (nothing heard) ``fallback``, the looked-up lyrics as they came. Then each word is timed."""
    kept = vocal.parent / "sung.json"                    # heard before this run was cut short: not again
    try:
        sung = json.loads(kept.read_text()) if kept.is_file() else None
    except ValueError:
        sung = None
    if not isinstance(sung, list):
        sung = hear(vocal)
        tmp = kept.with_suffix(".tmp.json")
        tmp.write_text(json.dumps(sung))
        tmp.replace(kept)
    doc = None
    sung = real_heard(sung, duration)                    # stock lines and stray words aren't singing
    if sung:
        best, lines, stats = lyr.best_fit(cands, sung, duration)
        if best is not None:
            doc = {"kind": "synced", "source": "fitted", "lrclib_id": best.get("lrclib_id"), "fit": stats, "lines": lines}
        else:
            doc = {"kind": "synced", "source": "heard", "fit": stats, "lines": lyr.lines_from_sung(sung)}
    if doc is None:
        return fallback
    try:
        add_word_times(doc["lines"], vocal, 0.0)
        for ln in doc["lines"]:          # the line starts with its first word, as the aligner places it
            w = next((x for x in ln.get("words") or [] if x), None)
            if w and w["t0"] < ln["end"]:
                ln["start"] = round(w["t0"], 3)
        for a, b in zip(doc["lines"], doc["lines"][1:]):
            b["start"] = max(b["start"], a["start"])
            a["end"] = min(a["end"], b["start"]) if b["start"] > a["start"] else a["end"]
    except Exception as e:  # noqa: BLE001  (lines then sweep evenly)
        info(f"karaoke: word timing skipped: {e}")
    return doc


def audio_seconds(f: Path) -> float | None:
    """The file's exact length: the player's clock ends the song here."""
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(f)],
                             capture_output=True, text=True, timeout=30).stdout.strip()
        return round(float(out), 3)
    except (ValueError, OSError, subprocess.TimeoutExpired):
        return None


def add_word_times(lines: list[dict], vocal: Path, shift: float) -> None:
    """Each synced line gets ``words``: one {t0, t1} (or None) per space-separated token of its text, in line time
    (tools/karaoke/align.py, in the karaoke venv: the vocal force-aligned to the words). Lines it couldn't time keep
    none and sweep evenly."""
    py = smconfig.deployment().host_path("karaoke_python")
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(lines, f)
        lines_file = f.name
    try:
        p = subprocess.run([str(py), str(ALIGN), "--vocal", str(vocal), "--lines", lines_file, "--shift", str(shift)],
                           capture_output=True, text=True, timeout=600)
    finally:
        Path(lines_file).unlink(missing_ok=True)
    last = (p.stdout or "").strip().splitlines()[-1:] or [""]
    if p.returncode != 0 or not last[0].startswith("{"):
        raise RuntimeError(((p.stderr or "").strip().splitlines() or ["no output"])[-1][:300])
    res = json.loads(last[0])
    for ln, words in zip(lines, res.get("lines") or []):
        if words:
            ln["words"] = words
            ends = [w["t1"] for w in words if w]
            if ends and ln.get("end") is not None:
                ln["end"] = round(max(ln["end"], ends[-1] + 0.2), 3)
    info(f"karaoke: word timing {sum(1 for w in res.get('lines') or [] if w)}/{len(lines)} lines in {res.get('seconds')} s")


class Restarted(Exception):
    """The song's preparation was restarted (Worker.restart) while this run had it."""


def _tool_pids(match: str, orphans_only: bool = False) -> list[int]:
    """The processes whose command line names ``match`` (a song's folder, or the songs' root): the karaoke tools this
    worker started (yt-dlp, the separator, Whisper, the aligner, ffmpeg). ``orphans_only``: those left without their
    Flask (parent 1)."""
    out = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            cmd = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
            if match not in cmd:
                continue
            if orphans_only and int((proc / "stat").read_text().rsplit(")", 1)[1].split()[1]) != 1:
                continue
            out.append(int(proc.name))
        except (OSError, ValueError, IndexError):
            continue
    return out


def _end(pids: list[int]) -> None:
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


class Worker:
    def __init__(self, store: Store):
        self.store = store
        self.thread: threading.Thread | None = None
        self.current: str | None = None
        self._restart: set[str] = set()                  # songs whose run is being restarted (restart)

    def restart(self, sid: str) -> bool:
        """Prepare the song again from nothing (owner, 26 Nov: "I currently have no way to restart an extraction. it's
        just stuck"): the step it is on ends (its tool's process), its files go, and it waits its turn afresh. False
        for no such song."""
        with self.store.lock:
            if sid not in self.store.s["songs"]:
                return False
            running = self.current == sid
            if running:
                self._restart.add(sid)
        if running:
            _end(_tool_pids(str(song_dir(sid))))        # the run stops at its next step and comes back here (_loop)
            self.store.update_song(sid, status="queued", detail="restarting", error=None)
            return True
        self._clear(sid)
        return True

    def _clear(self, sid: str) -> None:
        d = song_dir(sid)
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
        self.store.update_song(sid, status="queued", detail="", error=None, source_url=None, video_id=None,
                               lyrics=None, audio_s=None, offset_ms=0, ready_at=None)

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        # A Flask that went (a restart) leaves its tools running on their own: end them; their songs carry on here.
        left = _tool_pids(str(song_dir("x").parent), orphans_only=True)
        if left:
            info(f"karaoke: ending {len(left)} tool process(es) a previous Flask left running")
            _end(left)
        self.thread = threading.Thread(target=self._loop, name="karaoke-worker", daemon=True)
        self.thread.start()

    def _loop(self) -> None:
        while True:
            with self.store.lock:
                job = self.store.next_job()
                if job is None:
                    self.store.changed.wait(timeout=30)
                    continue
            self.current = job["id"]
            try:
                self.process(job)
                if job["id"] in self._restart:
                    raise Restarted()
            except Restarted:
                info(f"karaoke: {job['id']} restarted")
            except Exception as e:  # noqa: BLE001
                if job["id"] in self._restart:          # its step was ended by a restart: not a failure, it goes again
                    info(f"karaoke: {job['id']} restarted")
                elif isinstance(e, SourceError):
                    self.store.update_song(job["id"], status="failed", error=str(e), detail="")
                    info(f"karaoke: {job['id']} failed: {e}")
                else:
                    self.store.update_song(job["id"], status="failed", error=f"Something went wrong: {e}", detail="")
                    error(f"karaoke: {job['id']} crashed: {e}\n{traceback.format_exc()}")
            finally:
                self.current = None
                if job["id"] in self._restart:          # restarted mid-run: from nothing, once this run has stopped
                    self._restart.discard(job["id"])
                    self._clear(job["id"])

    # ------------------------------------------------------------ one song
    def step(self, sid: str, status: str, detail: str = "") -> None:
        if sid in self._restart:
            raise Restarted()
        if self.store.update_song(sid, status=status, detail=detail, error=None) is None:
            raise SourceError("The song was removed.")

    def process(self, job: dict) -> None:
        sid, req = job["id"], job.get("request") or {}
        d = song_dir(sid)
        d.mkdir(parents=True, exist_ok=True)
        # A run cut short (Flask restarted: owner, 26 Nov: "ensure graceful recovery in case flask does restart") picks
        # up after the last step it finished: each step's result is kept (the recording on the song, the download,
        # the separated stems, the encoded ones, what Whisper heard); only its separator's temp folders go.
        for t in d.glob("tmp*"):
            if t.is_dir():
                shutil.rmtree(t, ignore_errors=True)
        with self.store.lock:
            song = dict(self.store.s["songs"].get(sid) or {})

        # 1. which recording
        if song.get("source_url"):
            meta = {"url": song["source_url"], "video_id": song.get("video_id"), "title": song.get("title") or "",
                    "artist": song.get("artist") or "", "duration": song.get("duration")}
            info(f"karaoke: {sid} carries on ({meta['artist']} – {meta['title']})")
        else:
            self.step(sid, "fetching", "finding the recording")
            meta = self.resolve(req)
            dup = (self.store.find_existing(video_id=meta.get("video_id"), exclude=sid)
                   or self.store.find_existing(key=song_key(meta["artist"], meta["title"]), exclude=sid))
            if dup:                               # already in the library (or on its way): sing that one
                self.store.merge_into(sid, dup["id"])
                info(f"karaoke: {sid} is {dup['id']} ({dup.get('title')}), already in the library")
                return
            self.store.update_song(sid, title=meta["title"], artist=meta["artist"], duration=meta.get("duration"),
                                   video_id=meta.get("video_id"), source_url=meta["url"])
        stems = all((d / f"{x}.wav").is_file() for x in ("instrumental", "vocals"))
        have = [f for f in d.glob("source.*") if f.suffix not in (".part", ".ytdl") and ".tmp" not in f.name]
        if stems or have:
            audio = next((f for f in have if f.name == "source.wav"), have[0] if have else d / "source.wav")
        else:
            self.step(sid, "fetching", "downloading")
            t0 = time.time()
            audio = src.download_audio(meta["url"], d)
            info(f"karaoke: {sid} downloaded {meta['artist']} – {meta['title']} in {time.time() - t0:.0f} s")

        # 2. lyrics
        self.step(sid, "lyrics", "looking up lyrics")
        found, cands = None, []
        try:
            found = src.lrclib_lyrics(meta["artist"], meta["title"], meta.get("duration"))
        except Exception as e:  # noqa: BLE001  (no lyrics is not a failure: the song still plays)
            info(f"karaoke: {sid} lyrics lookup failed: {e}")
        try:
            cands = src.lrclib_candidates(meta["artist"], meta["title"])
        except Exception as e:  # noqa: BLE001
            info(f"karaoke: {sid} lyrics candidates failed: {e}")
        if found:
            cands = [found] + [c for c in cands if c.get("lrclib_id") != found.get("lrclib_id")]
        lyrics_doc = self.lyrics_doc(found, meta.get("duration"))

        # 3. the backing track and the lead vocal
        self.step(sid, "separating", "removing the lead vocal")
        # Whatever came down (m4a, opus, webm), the separator gets plain WAV: its reader (libsndfile) can't open
        # m4a ("Format not recognised", 25 Nov: Pharrell Williams, Happy).
        wav = d / "source.wav"
        if not stems:
            if audio != wav:
                tmp = d / "source.tmp.wav"                # written aside, then named: a cut-short one never counts
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(audio), "-ac", "2", "-ar", "44100", str(tmp)],
                               check=True, capture_output=True, timeout=300)
                tmp.replace(wav)
                audio.unlink(missing_ok=True)
            audio = wav
            self.separate(audio, d)
        audio = wav
        self.check_lead(sid, d, wav)
        for stem in ("instrumental", "vocals"):
            if not (d / f"{stem}.m4a").is_file():
                self.encode(d / f"{stem}.wav", d / f"{stem}.m4a")
        self.store.update_song(sid, audio_s=audio_seconds(d / "instrumental.m4a"))

        # 4. the lyrics timed to this recording's own singing
        self.step(sid, "lyrics", "listening to the words")
        try:
            lyrics_doc = fit_lyrics(cands, d / "vocals.wav", audio_seconds(d / "instrumental.m4a"), lyrics_doc)
        except Exception as e:  # noqa: BLE001  (the looked-up lyrics as they came, lined up the old way)
            info(f"karaoke: {sid} hearing the words failed: {e}")
        if lyrics_doc.get("source") in ("fitted", "heard"):
            self.store.update_song(sid, offset_ms=0)
            info(f"karaoke: {sid} lyrics {lyrics_doc['source']} {lyrics_doc.get('fit')}")
        elif lyrics_doc["kind"] == "synced":
            try:
                shift, conf = lyr.estimate_offset(lyr.vocal_activity(d / "vocals.wav"), lyrics_doc["lines"])
            except Exception as e:  # noqa: BLE001
                shift, conf = 0.0, 0.0
                info(f"karaoke: {sid} lyric alignment skipped: {e}")
            lyrics_doc["auto_shift_s"], lyrics_doc["auto_confidence"] = shift, round(conf, 2)
            self.store.update_song(sid, offset_ms=int(shift * 1000))
            try:
                add_word_times(lyrics_doc["lines"], d / "vocals.wav", shift)
            except Exception as e:  # noqa: BLE001  (lines then sweep evenly)
                info(f"karaoke: {sid} word timing skipped: {e}")
        (d / "lyrics.json").write_text(json.dumps(lyrics_doc))
        for stem in ("instrumental", "vocals"):
            (d / f"{stem}.wav").unlink(missing_ok=True)
        audio.unlink(missing_ok=True)
        self.store.update_song(sid, status="ready", detail="", lyrics={"kind": lyrics_doc["kind"],
                               "lines": lyrics_doc["lines"]}, ready_at=time.time())
        info(f"karaoke: {sid} ready ({lyrics_doc['kind']} lyrics, {lyrics_doc.get('source', 'as looked up')})")

    def resolve(self, req: dict) -> dict:
        kind = req.get("kind")
        if kind in ("youtube", "url"):
            v = src.ytdlp_info(req["url"])
            return {**v, "url": v["url"]}
        if kind == "spotify":
            t = src.spotify_track(req["url"])
        elif kind == "lrclib":
            t = {"artist": req.get("artist") or "", "title": req.get("title") or "", "duration": req.get("duration")}
        elif kind == "text":
            hits = src.lrclib_search(req["q"], limit=1)
            if hits:
                t = {"artist": hits[0]["artist"], "title": hits[0]["title"], "duration": hits[0]["duration"]}
            else:                                          # not in LRCLIB: take YouTube's best guess, no lyrics
                a, ti = src.split_video_title(req["q"], "")
                t = {"artist": a if a != ti else "", "title": ti, "duration": None}
        else:
            raise SourceError(f"Unknown request kind {kind!r}.")
        v = src.best_video(t["artist"], t["title"], t.get("duration"))
        return {"video_id": v["video_id"], "url": v["url"], "artist": t["artist"] or v.get("channel") or "",
                "title": t["title"], "duration": t.get("duration") or v.get("duration")}

    @staticmethod
    def lyrics_doc(found: dict | None, duration: float | None) -> dict:
        if not found:
            return {"kind": "none", "lines": []}
        if found["kind"] == "synced":
            return {"kind": "synced", "lrclib_id": found.get("lrclib_id"),
                    "lines": lyr.display_lines(lyr.parse_lrc(found["lrc"]), duration)}
        return {"kind": "plain", "lrclib_id": found.get("lrclib_id"), "lines": lyr.plain_lines(found.get("text") or "")}

    @staticmethod
    def separate(audio: Path, d: Path, model: str | None = None) -> None:
        py = smconfig.deployment().host_path("karaoke_python")
        if not py or not Path(py).exists():
            raise SourceError("The karaoke tools aren't installed on this machine (deployment host.karaoke_python).")
        p = subprocess.run([str(py), str(SEPARATE), "--in", str(audio), "--out-dir", str(d)] + (["--model", model] if model else []),
                           capture_output=True, text=True, timeout=900)
        last = (p.stdout or "").strip().splitlines()[-1:] or [""]
        try:
            res = json.loads(last[0])
        except ValueError:
            res = {"ok": False, "error": ((p.stderr or "").strip().splitlines() or ["no output"])[-1][:300]}
        if p.returncode != 0 or not res.get("ok"):
            raise SourceError(f"Removing the vocal failed: {res.get('error')}")
        info(f"karaoke: separated in {res.get('seconds')} s")

    def check_lead(self, sid: str, d: Path, source: Path) -> None:
        """The lead vocal stem near silent while the backing is loud (QUIET_VOCAL_*): the karaoke model kept the singing
        as backing vocals (two equal voices), so the backing track still has it. Separate again with every voice
        out (ALL_VOCALS_MODEL); the m4a stems are made again from the new ones. Once per song (song "separation")."""
        with self.store.lock:
            done = (self.store.s["songs"].get(sid) or {}).get("separation")
        voc, inst = d / "vocals.wav", d / "instrumental.wav"
        if done or not voc.is_file() or not inst.is_file() or not source.is_file():
            return
        v, i = mean_db(voc), mean_db(inst)
        if v > QUIET_VOCAL_DB and i - v < QUIET_VOCAL_GAP_DB:
            self.store.update_song(sid, separation="lead")
            return
        info(f"karaoke: {sid} lead vocal near silent ({v:.0f} dB, backing {i:.0f} dB): taking every voice out")
        self.step(sid, "separating", "taking every voice out")
        self.separate(source, d, ALL_VOCALS_MODEL)
        for stale in ("instrumental.m4a", "vocals.m4a", "sung.json"):   # made from the old stems: again
            (d / stale).unlink(missing_ok=True)
        self.store.update_song(sid, separation="all")

    @staticmethod
    def encode(wav: Path, out: Path) -> None:
        tmp = out.with_name(out.stem + ".tmp" + out.suffix)  # aside, then named (a cut-short one never counts)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(wav), "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                        str(tmp)], check=True, capture_output=True, timeout=300)
        tmp.replace(out)
