"""Art tags: the art director's picture maker (docs/ART_TAGS.md). Owner, 27-28 Oct 2026: "connect listening AI with
artistic director with AI image generator". While the drawing room is in Ambient and a screen's library holds few
pictures that fit the room's art wish (show_service's direction.art, from the AI listener or the music's group), one
new picture is made for that screen now and then. It goes into the screen's bucket only, never onto the wall; the
bucket save queues its tagging, and the renderer's gallery re-indexes within a minute, so it joins Ambient's pool.

One daemon thread inside Flask (started next to the tag worker), a pass every interval_s:
  1. the room's mode (room_service GET :8700/api/v1/status) must be Ambient;
  2. the art wish (show_service GET :8703/api/v1/look -> direction.art) must be there and not via "none";
  3. each of the room's screens: count its strong matches of the wish's LEAD subject (tagged, auto_ok; that subject,
     no avoided subject, and a wished mood or a music fit >= 0.7 for the genre heard); fewer than ``thin`` = thin;
  4. one thin screen (the one served longest ago) within the caps gets one picture: one prompt call (utils.llm task
     "prompt") and one generation through the scheduler's own path (generate with no target, then a bucket save with
     the published pointer left alone).
Each pass is one JSON line in ~/.local/state/screen-machine/logs/art_director.jsonl; today's count and each screen's
last try are read back from it at start, so a Flask restart doesn't reset the day's cap. Never raises into Flask."""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

import yaml

from routes.art_tags import lexicon, sidecar

ROOM_URL = os.environ.get("ART_DIRECTOR_ROOM_URL", "http://127.0.0.1:8700")
SHOW_URL = os.environ.get("ART_DIRECTOR_SHOW_URL", "http://127.0.0.1:8703")
REPO_ROOT = Path(__file__).resolve().parents[2]
ROOMS_FILE = REPO_ROOT / "routes" / "data" / "rooms.json"
SCHEDULER_DIR = REPO_ROOT / "routes" / "scheduler"
LOG_FILE = Path.home() / ".local" / "state" / "screen-machine" / "logs" / "art_director.jsonl"
LOG_MAX_BYTES = 5_000_000

ROOM = "drawing-room"
MODE = "ambient"
SOURCE = "art-director"      # the job tracker's source word
FIT_MIN = 0.7                # a picture's music fit that counts like a wished mood (the tagger's "good" anchor)
FIRST_PASS_S = 60.0          # after Flask starts: room_service and the show bus settle first
BACKOFF_MIN_S, BACKOFF_MAX_S = 600.0, 3600.0
PROMPT_MAX = 600
DEFAULTS = {"enabled": True, "interval_s": 60, "thin": 24, "per_screen_min_gap_s": 600, "per_day": 12}
# The gallery takes only these (stream_service/plugins/sources/gallery.py DATE_HEX), so only these count as pool.
DATE_HEX = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{8}\.(jpg|png)(_batch-[^/]+\.(jpg|png))?$")
# The music groups' genre words (stream_service/tools/tagpass.py GROUP_WORDS; a test keeps the two equal).
GROUP_WORDS = {
    "ambient": ["ambient", "downtempo"],
    "classical": ["classical", "piano", "soundtrack"],
    "jazz": ["jazz", "blues"],
    "soul": ["soul", "rnb", "funk", "gospel", "disco"],
    "pop": ["pop", "indie", "singer-songwriter", "folk", "country"],
    "rock": ["rock", "metal", "punk"],
    "house": ["house", "disco", "trance", "electronic"],
    "techno": ["techno", "trance", "drum-and-bass", "dubstep", "electronic"],
    "hiphop": ["hip-hop"],
    "world": ["world", "latin", "afrobeat", "reggae"],
}

PROMPT_SYSTEM = (
    "You write one prompt for an AI image generator. The picture will hang as art on a 4K TV in a living room while "
    "music plays, chosen by an art director to suit what is heard. Write a single vivid prompt of 40-80 words: the "
    "subject, the setting, the light, the colour and the medium. It must be 16:9 landscape wall art: no text, "
    "letters, words, signatures, logos, watermarks or frames, and nothing ugly or disturbing. Follow the art wish: "
    "show one or more of the wished subjects, in the wished moods and media, and none of the avoided subjects. Make "
    "it a fresh picture, not a variation of the recent prompts listed.")
PROMPT_TOOL = {
    "name": "image_prompt",
    "description": "The one image prompt.",
    "parameters": {"type": "object",
                   "properties": {"prompt": {"type": "string", "description": "40-80 words."}},
                   "required": ["prompt"]},
}


def _log(level: str, msg: str) -> None:
    try:
        from utils import logger
        getattr(logger, level)(f"[art_director] {msg}")
    except Exception:                                 # noqa: BLE001
        pass


def _iso(t: Optional[float]) -> Optional[str]:
    if not t:
        return None
    return _dt.datetime.fromtimestamp(t, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s: Any) -> Optional[float]:
    try:
        return _dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


def _day(t: float) -> str:
    return _dt.datetime.fromtimestamp(t).strftime("%Y-%m-%d")      # local day: "12 a day" is the owner's day


def settings() -> dict:
    """config/art_tags.yaml director: block over DEFAULTS. Read each pass (a small file), so an edit takes at once;
    a bad file keeps the defaults."""
    out = dict(DEFAULTS)
    try:
        raw = yaml.safe_load(lexicon.LEXICON_FILE.read_text(encoding="utf-8")) or {}
        d = raw.get("director") if isinstance(raw, dict) else None
        if isinstance(d, dict):
            out.update(d)
    except Exception as e:                            # noqa: BLE001
        _log("warning", f"settings: {e} (using the defaults)")
    return out


def get_json(url: str, timeout: float = 5.0) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as r:      # noqa: S310 - our own LAN services
        return json.loads(r.read().decode("utf-8"))


def room_screens(room: str = ROOM) -> list[str]:
    """The room's screens (routes/data/rooms.json)."""
    try:
        d = json.loads(ROOMS_FILE.read_text(encoding="utf-8"))
        return [s for s in (d.get(room) or {}).get("screens") or [] if isinstance(s, str)]
    except (OSError, ValueError, AttributeError):
        return []


# -- the art wish and the library ---------------------------------------------------------------------------------
def _words(v: Any) -> list[str]:
    out: list[str] = []
    for x in v if isinstance(v, list) else []:
        w = lexicon.normalise(x)
        if w and w not in out:
            out.append(w)
    return out


def wish_of(look: Any) -> Optional[dict]:
    """show_service's art wish (contract2: direction.art), cleaned; None when there is none or it is via "none"."""
    if not isinstance(look, dict):
        return None
    d = look.get("direction") if isinstance(look.get("direction"), dict) else {}
    art = d.get("art") if isinstance(d.get("art"), dict) else look.get("art")
    if not isinstance(art, dict):
        return None
    via = art.get("via")
    if via in (None, "none"):
        return None
    w = {"subjects": _words(art.get("subjects")), "moods": _words(art.get("moods")), "media": _words(art.get("media")),
         "avoid": _words(art.get("avoid")), "theme": str(art.get("theme") or "").strip()[:120], "via": str(via)}
    if not w["subjects"] and not w["moods"] and not w["theme"]:
        return None
    return w


def genre_of(look: Any) -> Optional[str]:
    """The genre word heard: direction.genre, else the top share of direction.genres."""
    d = (look or {}).get("direction") if isinstance(look, dict) else None
    if not isinstance(d, dict):
        return None
    g = lexicon.normalise(d.get("genre"))
    if g:
        return g
    shares = d.get("genres") if isinstance(d.get("genres"), dict) else {}
    best = max(((w, p) for w, p in shares.items() if isinstance(p, (int, float))), key=lambda x: x[1], default=None)
    return lexicon.normalise(best[0]) if best else None


def groups_for(genre: Optional[str]) -> list[str]:
    """The music groups a genre word belongs to (a group's own name counts too: show may send "hiphop")."""
    if not genre:
        return []
    return [g for g, ws in GROUP_WORDS.items() if genre == g or genre in ws]


def _seq_files(meta: dict) -> list[str]:
    out = []
    for e in meta.get("sequence") or []:
        f = e.get("file") if isinstance(e, dict) else e
        if isinstance(f, str) and f not in out:
            out.append(f)
    return out


def is_strong(d: dict, wish: dict, groups: list[str], subj_words: set, mood_words: set) -> bool:
    """One picture's sidecar against the wish: tagged (done, auto_ok), a wished subject, no avoided subject, and a
    wished mood or a music fit >= FIT_MIN for the genre heard. Subjects and moods as the gallery reads them (a
    person's removed words dropped, their added lexicon words kept)."""
    t = d.get("tagging") if isinstance(d.get("tagging"), dict) else {}
    ai = t.get("ai") if isinstance(t.get("ai"), dict) else {}
    if t.get("status") != "done" or ai.get("auto_ok") is not True:
        return False
    removed = set(_words(t.get("removed")))
    added = [w for w in _words(t.get("added")) if w not in removed]
    subj = {w for w in _words(ai.get("subject")) if w not in removed} | {w for w in added if w in subj_words}
    moods = {w for w in _words(ai.get("moods")) if w not in removed} | {w for w in added if w in mood_words}
    if not subj & set(wish["subjects"]) or subj & set(wish["avoid"]):
        return False
    if moods & set(wish["moods"]):
        return True
    music = ai.get("music") if isinstance(ai.get("music"), dict) else {}
    fit = [music.get(g) for g in groups if isinstance(music.get(g), (int, float))]
    return bool(fit) and max(fit) >= FIT_MIN


def count_strong(bucket: str, wish: dict, groups: list[str]) -> dict:
    """{strong, pool, tagged} over the bucket's gallery stills (DATE-HEX names in its sequence, with a sidecar
    holding a prompt and a workflow, as the gallery requires)."""
    base = sidecar.output_base() / bucket
    out = {"strong": 0, "pool": 0, "tagged": 0}
    try:
        meta = json.loads((base / "bucket.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out
    try:
        subj_words, mood_words = set(lexicon.words("subject")), set(lexicon.words("moods"))
    except Exception:                                 # noqa: BLE001 - lexicon unreadable: no added words count
        subj_words, mood_words = set(), set()
    for f in _seq_files(meta):
        if not DATE_HEX.match(f) or not (base / f).is_file():
            continue
        try:
            d = sidecar.read(base / f)
        except (OSError, ValueError):
            continue
        if not d.get("prompt") or not d.get("workflow"):
            continue
        out["pool"] += 1
        out["tagged"] += sidecar.status_of(d) == "done"
        out["strong"] += is_strong(d, wish, groups, subj_words, mood_words)
    return out


def pick_screen(thin: list[str], last_at: dict, today_count: int, now: float, cfg: dict,
                backoff_until: float = 0.0) -> Optional[str]:
    """The caps (config director:): off, the day's count spent, a failure's backoff, or each screen's minimum gap;
    of the thin screens left, the one served longest ago (so two thin screens take turns)."""
    if not cfg.get("enabled") or today_count >= int(cfg.get("per_day") or 0) or now < backoff_until:
        return None
    gap = float(cfg.get("per_screen_min_gap_s") or 0)
    ok = [s for s in thin if now - (last_at.get(s) or 0.0) >= gap]
    if not ok:
        return None
    return min(ok, key=lambda s: (last_at.get(s) or 0.0, thin.index(s)))


# -- the prompt and the generation (the defaults; tests inject fakes) ----------------------------------------------
def write_prompt(wish: dict, genre: Optional[str], character: Any, recent: list[str]) -> dict:
    """One utils.llm call (task "prompt", the app's prompt work) -> {ok, prompt, cost_usd, model, error}."""
    import utils.llm as llm
    lines = [f"Theme: {wish['theme'] or '(none)'}",
             f"Lead subject (the picture is mainly this): {wish['subjects'][0] if wish['subjects'] else '(any)'}",
             f"Wished subjects: {', '.join(wish['subjects']) or '(any)'}",
             f"Wished moods: {', '.join(wish['moods']) or '(any)'}",
             f"Wished media: {', '.join(wish['media']) or '(any)'}",
             f"Avoid: {', '.join(wish['avoid']) or '(nothing)'}"]
    if genre:
        lines.append(f"Music heard: {genre}")
    if character:
        c = character if isinstance(character, str) else json.dumps(character, ensure_ascii=False)
        lines.append(f"The listener's sense of the music: {c[:400]}")
    if recent:
        lines.append("Recent prompts (make something different):\n" + "\n".join(f"- {p[:200]}" for p in recent[-5:]))
    res = llm.call_json("prompt", system=PROMPT_SYSTEM, user="\n".join(lines), tool=PROMPT_TOOL, lenient=True)
    p = res.data.get("prompt") if res.ok and isinstance(res.data, dict) else None
    p = re.sub(r"\s+", " ", p).strip()[:PROMPT_MAX] if isinstance(p, str) else ""
    if not p:
        return {"ok": False, "error": res.error or "no prompt in the answer", "cost_usd": res.cost_usd}
    return {"ok": True, "prompt": p, "cost_usd": res.cost_usd, "model": res.model}


def _destination(screen: str) -> dict:
    try:
        from routes.utils import findfile
        with open(findfile("publish-destinations.json"), "r", encoding="utf-8") as f:
            return next((d for d in json.load(f) if d.get("id") == screen), {}) or {}
    except Exception:                                 # noqa: BLE001
        return {}


def scheduler_defaults(screen: str) -> tuple[Optional[str], Optional[str]]:
    """(refiner, workflow) of the screen's own scheduled text-to-image generate (routes/scheduler/<screen>.json), so
    the art director makes pictures as the screen's schedule does. Neither found: (None, None), and the generate
    path picks its own defaults."""
    try:
        d = json.loads((SCHEDULER_DIR / f"{screen}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    found: list = []

    def walk(o):
        if found:
            return
        if isinstance(o, dict):
            if o.get("action") == "generate" and not o.get("images"):
                found.append((o.get("refiner") or None, o.get("workflow") or None))
                return
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(d)
    return found[0] if found else (None, None)


def generate_for_screen(screen: str, prompt: str, note: dict) -> dict:
    """One picture into the screen's bucket, never onto the wall: scheduler_handlers.handle_generate's publish:false
    path (the generation service with no target, so it is tracked but publishes nothing; then publish_to_destination
    with update_published=False, which saves to the bucket and queues tagging). -> {ok, file, cost_gbp, error}."""
    try:
        from routes.service_factory import get_generation_service
        from routes.publisher import publish_to_destination
        refiner, workflow = scheduler_defaults(screen)
        dest = _destination(screen)
        send_obj = {"data": {"prompt": prompt, "images": [], "refiner": refiner, "workflow": workflow,
                             "targets": [], "silent": True,
                             "maxwidth": dest.get("maxwidth"), "maxheight": dest.get("maxheight")}}
        job_ids: list = []
        svc = get_generation_service()
        call = svc.handle_image_generation if hasattr(svc, "handle_image_generation") else svc
        # No target on the job: the TV's display page shows a job indicator for jobs aimed at it, and this picture
        # is never for the wall.
        results = call(input_obj=send_obj, wait=True, source=SOURCE, job_ids_out=job_ids) or []
        r = next((x for x in results if isinstance(x, dict) and (x.get("published_path") or x.get("message"))), None)
        if r is None:
            err = "the generation returned nothing"
            try:
                from routes.jobs import get_tracker
                job = get_tracker().get(job_ids[0]) if job_ids else None
                err = (job or {}).get("error") or err
            except Exception:                         # noqa: BLE001
                pass
            return {"ok": False, "error": str(err)[:300]}
        meta = {"prompt": r.get("prompt") or r.get("actual_final_prompt") or prompt, "original_prompt": prompt,
                # the gallery takes only pictures whose sidecar names a workflow
                "workflow": r.get("actual_workflow_id") or workflow, "refiner": refiner, "seed": r.get("seed"),
                "maxwidth": dest.get("maxwidth"), "maxheight": dest.get("maxheight"),
                "generation_time_seconds": r.get("generation_time_seconds"),
                "generation_cost_gbp": r.get("generation_cost_gbp"),
                "when_generated": _dt.datetime.now().isoformat(), "publish": False, "art_director": note}
        # batch_id None: _append_to_bucket would add "_batch-<id>" to the name, which the gallery's DATE-HEX
        # pattern never takes.
        pub = publish_to_destination(source=r.get("published_path") or r.get("message"),
                                     publish_destination_id=screen, silent=True, metadata=meta, batch_id=None,
                                     update_published=False)
        if not pub.get("success"):
            return {"ok": False, "error": f"saving to the bucket failed: {pub.get('error')}"[:300],
                    "cost_gbp": r.get("generation_cost_gbp")}
        return {"ok": True, "file": (pub.get("meta") or {}).get("filename"), "cost_gbp": r.get("generation_cost_gbp"),
                "job_id": job_ids[0] if job_ids else None}
    except Exception as e:                            # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}


# -- the director -------------------------------------------------------------------------------------------------
class Director:
    def __init__(self, get_json_fn: Callable[[str], Any] = get_json,
                 prompt_fn: Callable[..., dict] = write_prompt,
                 generate_fn: Callable[[str, str, dict], dict] = generate_for_screen,
                 screens_fn: Callable[[], list] = room_screens, settings_fn: Callable[[], dict] = settings,
                 clock: Callable[[], float] = time.time, log_path: Optional[Path] = LOG_FILE):
        self.get_json = get_json_fn
        self.prompt_fn = prompt_fn
        self.generate_fn = generate_fn
        self.screens_fn = screens_fn
        self.settings_fn = settings_fn
        self.clock = clock
        self.log_path = log_path
        self.lock = threading.Lock()
        self._stop = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.last_at: dict = {}          # screen -> time of its last try
        self.day: Optional[str] = None
        self.today_count = 0             # tries today (each may cost a generation)
        self.backoff_s = 0.0
        self.backoff_until = 0.0
        self.recent_prompts: list = []
        self.last: dict = {"mode": None, "wish": None, "genre": None, "per_screen": {}}
        self.last_prompt: Optional[str] = None
        self.last_error: Optional[str] = None
        self.last_pass_at: Optional[float] = None
        self.busy: Optional[dict] = None
        self._seeded = False

    # -- the log ----------------------------------------------------------------------------------------------------
    def _seed(self) -> None:
        """Today's tries and each screen's last try, read back from the log (a restart keeps the day's cap). Once,
        whichever of the thread and the status route gets here first."""
        with self.lock:
            if not self._seeded:
                self._seeded = True
                self._read_log()

    def _read_log(self) -> None:
        self.day = _day(self.clock())
        if self.log_path is None:
            return
        try:
            lines = self.log_path.read_text(encoding="utf-8").splitlines()[-5000:]
        except OSError:
            return
        for ln in lines:
            try:
                e = json.loads(ln)
            except ValueError:
                continue
            if not isinstance(e, dict) or e.get("dry") or e.get("action") not in ("generated", "failed"):
                continue
            t = _parse_iso(e.get("at"))
            s = e.get("screen")
            if t is None or not isinstance(s, str):
                continue
            self.last_at[s] = max(self.last_at.get(s, 0.0), t)
            if _day(t) == self.day:
                self.today_count += 1
            if e.get("action") == "generated" and e.get("prompt"):
                self.recent_prompts = (self.recent_prompts + [str(e["prompt"])])[-5:]

    def _write_log(self, entry: dict) -> None:
        if self.log_path is None:
            return
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                if self.log_path.stat().st_size > LOG_MAX_BYTES:
                    os.replace(self.log_path, self.log_path.with_suffix(".jsonl.1"))
            except OSError:
                pass
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:                        # noqa: BLE001
            _log("warning", f"log: {e}")

    # -- one pass ---------------------------------------------------------------------------------------------------
    def pass_once(self, dry: bool = False) -> dict:
        """One decision. -> the log entry: {at, mode, wish, genre, per_screen, action, screen?, prompt?, file?, error?}.
        action: off | not-ambient | no-wish | enough | waiting (a thin screen, the caps say not yet) | would-generate
        (dry) | generated | failed. dry: decide only (no prompt call, no generation, no log line, no state)."""
        self._seed()
        now = self.clock()
        if _day(now) != self.day:
            self.day, self.today_count = _day(now), 0
        cfg = self.settings_fn()
        entry: dict = {"at": _iso(now), "mode": None, "wish": None, "genre": None, "per_screen": {}}
        if dry:
            entry["dry"] = True

        def done(action: str, **kw) -> dict:
            entry.update(action=action, **kw)
            if not dry:
                self.last = {k: entry.get(k) for k in ("mode", "wish", "genre", "per_screen", "action")}
                self.last_pass_at = now
                self._write_log(entry)
            return entry

        if not cfg.get("enabled"):
            return done("off")
        try:
            status = self.get_json(f"{ROOM_URL}/api/v1/status")
            mode = ((status or {}).get("mode") or {}).get("current")
        except Exception as e:                        # noqa: BLE001 - room_service restarting: just not this pass
            return done("not-ambient", error=f"room status: {type(e).__name__}: {e}"[:200])
        entry["mode"] = mode
        if mode != MODE:
            return done("not-ambient")
        try:
            look = self.get_json(f"{SHOW_URL}/api/v1/look")
        except Exception as e:                        # noqa: BLE001
            return done("no-wish", error=f"show look: {type(e).__name__}: {e}"[:200])
        wish = wish_of(look)
        genre = genre_of(look)
        entry["wish"], entry["genre"] = wish, genre
        if wish is None:
            return done("no-wish")
        groups = groups_for(genre)
        thin: list[str] = []
        need = int(cfg.get("thin") or 0)
        # thin = few strong pictures of the wish's LEAD subject (what this music most wants: abstract for jazz, sky
        # for ambient); counted over every wished subject the house library was never thin, so nothing was made
        lead = {**wish, "subjects": wish["subjects"][:1]}
        entry["lead"] = lead["subjects"][0] if lead["subjects"] else None
        for s in self.screens_fn():
            c = count_strong(s, lead, groups)
            c["thin"] = c["strong"] < need
            c["last_at"] = _iso(self.last_at.get(s))
            entry["per_screen"][s] = c
            if c["thin"]:
                thin.append(s)
        if not thin:
            return done("enough")
        thin.sort(key=lambda s: entry["per_screen"][s]["strong"])      # a tie on the caps: the thinner one first
        screen = pick_screen(thin, self.last_at, self.today_count, now, cfg, self.backoff_until)
        if screen is None:
            return done("waiting", thin=thin, today_count=self.today_count)
        if dry:
            return done("would-generate", screen=screen, refiner_workflow=list(scheduler_defaults(screen)))

        # A try from here on counts against the caps whatever happens: a failed one may still have cost money.
        self.last_at[screen] = now
        self.today_count += 1
        self.busy = {"screen": screen, "since": _iso(now)}
        try:
            character = None
            try:
                character = (self.get_json(f"{SHOW_URL}/api/v1/listening") or {}).get("character")
            except Exception:                         # noqa: BLE001 - the prompt does without it
                pass
            pr = self.prompt_fn(wish, genre, character, list(self.recent_prompts))
            if not pr.get("ok"):
                return self._failed(done, screen, f"prompt: {pr.get('error')}")
            prompt = pr["prompt"]
            self.last_prompt = prompt
            note = {"screen": screen, "theme": wish["theme"], "subjects": wish["subjects"], "moods": wish["moods"],
                    "media": wish["media"], "avoid": wish["avoid"], "via": wish["via"], "genre": genre}
            res = self.generate_fn(screen, prompt, note) or {}
            if not res.get("ok"):
                return self._failed(done, screen, str(res.get("error") or "failed"), prompt=prompt)
            self.backoff_s, self.backoff_until, self.last_error = 0.0, 0.0, None
            self.recent_prompts = (self.recent_prompts + [prompt])[-5:]
            _log("info", f"made {res.get('file')} for {screen}: {prompt[:80]}")
            return done("generated", screen=screen, prompt=prompt, file=res.get("file"),
                        cost_gbp=res.get("cost_gbp"), prompt_cost_usd=pr.get("cost_usd"))
        finally:
            self.busy = None

    def _failed(self, done: Callable[..., dict], screen: str, error: str, **kw) -> dict:
        """Backs off 10 min, doubling to an hour, so a broken generator never loops."""
        self.backoff_s = min(BACKOFF_MAX_S, max(BACKOFF_MIN_S, self.backoff_s * 2))
        self.backoff_until = self.clock() + self.backoff_s
        self.last_error = f"{screen}: {error}"[:300]
        _log("warning", f"{screen}: {error}; next try in {self.backoff_s:.0f} s")
        return done("failed", screen=screen, error=error[:300], **kw)

    # -- the thread -------------------------------------------------------------------------------------------------
    def start(self) -> None:
        if self.thread is not None and self.thread.is_alive():
            return
        self._stop.clear()
        self.thread = threading.Thread(target=self._run, daemon=True, name="art-director")
        self.thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self.thread is not None:
            self.thread.join(timeout)

    def _run(self) -> None:
        try:                                          # this thread only: the host serves live 4K streams
            os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 10)
        except Exception:                             # noqa: BLE001
            pass
        _log("info", "started")
        self._stop.wait(FIRST_PASS_S)
        while not self._stop.is_set():
            try:
                self.pass_once()
            except Exception as e:                    # noqa: BLE001 - the loop never dies
                self.last_error = f"pass: {type(e).__name__}: {e}"[:300]
                _log("error", self.last_error)
            try:
                every = max(10.0, float(self.settings_fn().get("interval_s") or 60))
            except Exception:                         # noqa: BLE001
                every = 60.0
            self._stop.wait(every)

    # -- status -----------------------------------------------------------------------------------------------------
    def snapshot(self) -> dict:
        """GET /api/art-tags/director."""
        try:
            cfg = self.settings_fn()
        except Exception:                             # noqa: BLE001
            cfg = dict(DEFAULTS)
        self._seed()
        now = self.clock()
        p = self.last_prompt
        return {"enabled": bool(cfg.get("enabled")), "running": bool(self.thread and self.thread.is_alive()),
                "mode": self.last.get("mode"), "wish": self.last.get("wish"), "genre": self.last.get("genre"),
                "action": self.last.get("action"), "per_screen": self.last.get("per_screen") or {},
                "making": self.busy, "today_count": self.today_count if _day(now) == self.day else 0,
                "per_day": cfg.get("per_day"), "last_pass_at": _iso(self.last_pass_at),
                "backoff_until": _iso(self.backoff_until) if self.backoff_until > now else None,
                "last_prompt": (p[:200] + "...") if p and len(p) > 200 else p, "last_error": self.last_error}


_director: Optional[Director] = None
_director_lock = threading.Lock()


def get_director() -> Director:
    """The process-wide director, made on first use; its thread starts only in routes.art_tags.startup()."""
    global _director
    if _director is None:
        with _director_lock:
            if _director is None:
                _director = Director()
    return _director


def set_director(d: Optional[Director]) -> None:
    """Swap the process-wide director (tests)."""
    global _director
    _director = d
