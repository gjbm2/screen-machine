"""show.main: the room's Feel, i.e. the vibe show_service's director plays (docs/SHOW_ENGINE.md §8.1; the build spec is
show_service/README.md "Stage 3 > S3.11"). show.mic: whether the current mode follows the music, i.e. claims the room's
mic (owner, 16 Oct: the mic runs only while Auto is on or the mode follows the music; Mute on the Music card always
wins). show.ai: whether the current mode lets the AI listen (owner, 20 Oct: by default the AI hears the room only in a
live mode; people just talking in the room shouldn't cost anything; a mode can allow it, Normal too, if wanted). aa's
adapter contract v1.0 (roomstate/contract.py, ROOM_STATE_MODEL §5).

show.main's value is show_service's VibeTarget, canonical:
    {"vibe": "<a name in config/room/vibes.yaml>", "listening": bool, "overrides"?: {<vibe field>: value}}
`listening` is always there (false when a mode leaves it out); `overrides` only when it isn't empty (a null override
means unset, the vibe's own value, and is left out, as show_service does; a list keeps its first of each item, as
show_service does). {"vibe": null, "listening": false} is "no feel" (the director neutral): a real value, so the room
can store it and put it back when a mode with a show row is left. A mode's row is
    "show.main": {"set": {"vibe": "party", "listening": true}, "restore": true, "policy": "ignore"}
(name the member: a bare "show" selects show.mic and show.ai too, whose values are bools, so a feel there doesn't load).
Put-back is the room's own per-row `restore`; this adapter stores nothing of its own.

- read():  GET <url>/api/v1/vibe within read_timeout_s -> {vibe, listening[, overrides]} ({"vibe": null, ...} = no
           feel). Raises when show_service can't be reached or answers anything but 200 (the belief goes stale). If
           show_service came back cold (a new boot_id and no feel) after this adapter applied a feel, it applies that
           value again in the background: an "ignore" row that already landed is never retried by the room.
- apply(): PUT <url>/api/v1/vibe, then compares the answer's {vibe, listening, overrides}. show_service keeps any
           target as asked (an unknown vibe plays neutral; its read-back lists the problems), so 400 means only a body
           that isn't a target (failed, never retried); unreachable, a timeout or 5xx -> failed, retried.
- normalize(): shape only, offline. A vibe name that vibes.yaml doesn't have is NOT refused here: refusing would make
           the whole modes.json fail to load after a vibe is renamed (degrade, never refuse). The editor only offers
           real names (value_schema's enum) and show_service plays an unknown one neutral and says so.
- value_schema(): the Feel form. Vibe names and labels come from show_service's GET /api/v1/vibes (refreshed at most
           every 60 s by read(), best effort), else from vibes.yaml itself, else from the last catalog saved in the
           state dir; the fine-tune fields' schemas come from the catalog (live, else the saved one).
- situations(): the Auto vocabulary from GET /api/v1/situations (same cache), surfaced by describe() through
           vocabulary(): subsystems.show.situations and x-ui.situations on the mode form's conditions.

show.mic's value is a bool: true = this mode follows the music (its claim on the room's mic), false = it doesn't. It is
only the mode's claim, show_service's GET/PUT /api/v1/mic "mode": Auto's own claim and Mute are show_service's, not
the room's. A mode that follows the music has "show.mic": {"set": true, "restore": true}, so leaving it drops the claim.
- read():  GET /api/v1/mic -> its "mode". Each member fetches only its own route, all at once when several are asked.
           Asked alone, the mic fails like the Feel (unreachable, not 200 or no "mode": raise, the belief goes stale).
           Asked with the Feel, a mic route that fails while /api/v1/vibe answered leaves the mic out of that reading
           (its belief keeps its last value): show_service is up, and the Feel never goes stale over the mic (e.g. a
           show_service from before the mic API). The same cold-restart re-apply as the Feel: a new boot_id (from the
           mic's answer, else another answer's in the same read) with the claim off after this adapter landed it on.
- apply(): PUT /api/v1/mic {"mode": bool}; ok when the answer's "mode" is the value. 400 failed, never retried;
           unreachable, a timeout or 5xx failed, retried. Members in one apply go at once; Outcome.members has
           each, the status is the worst of them.
- The mic is not a console control: the subsystem is a "setting" (the mode editor's "Behind the scenes"); the Room
  page's one mic control is Mute on the Music card (show_service's own, not a room target).

show.ai's value is a bool too: true = this mode lets the AI listen, false = it doesn't. It is only the mode's
permission, show_service's GET/PUT /api/v1/listening "mode" (false until the room sets it: show_service fails closed).
Clips go to the cloud model only while the permission, the Music card's AI listening switch (the same route's "on": the
page's own master switch, never sent from here), Auto and the mic are all on, and there is real music. A mode that
allows it has "show.ai": {"set": true, "restore": true}, so leaving it drops the permission.
- read(), apply(): the mic's rules, member for member, on /api/v1/listening (each member fetches only its own route;
           with any other member answering, a failing one is left out of that reading; nothing answering: raise). Its
           answers carry show_service's boot_id, so the cold-restart re-apply can always tell the boot.
- normalize(): the mic's words (a bool, on/off, listen/listens, true/false).
- Also a "setting" ("Behind the scenes"), labelled "AI listening" like the Music card's switch it narrows.

No code here knows a vibe, a mode or a field by meaning: BASIC_FIELDS only decides which fine-tune fields the form shows
up front (§8.1's behaviour table); the rest sit behind "More".
"""

from __future__ import annotations

import asyncio
import copy
import functools
import logging
import re
import time
from pathlib import Path
from typing import Any

import aiohttp
import yaml

from ..roomstate.contract import Adapter, Outcome, Reading
from .room import _load, _save

log = logging.getLogger("room_service.show")

REPO = Path(__file__).resolve().parents[2]
VIBES_FILE = REPO / "config" / "room" / "vibes.yaml"     # c8's; read for names and labels only
SHOW_URL = "http://127.0.0.1:8703"   # show_service's API over loopback (show_service.contract BUS_PORT): a default
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")      # a vibe name (S3.5)
FIELD_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")         # a vibe field's shape; show_service checks the name itself
KEYS = ("vibe", "listening", "overrides")
NO_FEEL = {"vibe": None, "listening": False}            # the director neutral: a value the room can store and restore
FIXED_FIELDS = ("label", "description", "persona")       # a vibe's own words: never overridden by a mode (S3.5)
# §8.1's behaviour table: the fine-tune fields shown up front (those that do something today; a field whose schema
# says x-ui.not_built, like strobe and peak tokens, goes behind "More"). Every other field is behind "More".
BASIC_FIELDS = ("beat_lock", "change_looks", "follow", "relax_on_talk", "listener", "accents")
CACHE_S = 60.0          # catalog and situations: refreshed at most this often
RETRY_S = 15.0          # ... or this soon after a failed fetch
FETCH_TIMEOUT_S = 5.0
LISTEN_HELP = ("Not used: whether the AI listens in a mode is its own AI listening row (show.ai; owner, 20 Oct); the "
               "feel's listening has gated nothing since 17 Oct and is hidden in the editor")
MIC_HELP = "The room's mic listens while this mode is on. Auto always listens; Mute on the Music card always wins."
AI_HELP = ("Lets the AI hear the music in this mode (while the Music card's AI listening switch is on). It sends short "
           "clips to a cloud model and costs money.")
MIC_WORDS = {"on": True, "listen": True, "listens": True, "true": True, "off": False, "false": False}   # mic and ai
PATHS = {"main": "/api/v1/vibe", "mic": "/api/v1/mic", "ai": "/api/v1/listening"}   # a read fetches only those asked
# The bool members: the mode's claim on the mic, its leave for the AI to listen; each is show_service's {"mode": bool}
# on its own route. Their words, for errors, a reading's detail and the log.
FLAGS: dict[str, dict[str, str]] = {
    "mic": {"is": "the mic is true (listens in this mode)", "noun": "mic", "claim": "the mic's claim",
            "again": "this mode follows the music again", "redo": "claiming the mic again", "short": "Mic"},
    "ai": {"is": "AI listening is true (the AI may listen in this mode)", "noun": "AI listening",
           "claim": "the mode's AI listening", "again": "this mode lets the AI listen again",
           "redo": "allowing AI listening again", "short": "AI"},
}
STATUSES = ("ok", "unconfirmed", "failed")                # best to worst (contract rule 4)


class ShowUnreachable(ConnectionError):
    """show_service can't be reached, or didn't answer 200: the belief goes stale."""


def canonical(value: Any) -> dict:
    """A VibeTarget in canonical form, from "party", {"vibe", "listening"?, "overrides"?} or {"name", ...}. Pure;
    ValueError on a bad shape."""
    if isinstance(value, str):
        value = {"vibe": value}
    if not isinstance(value, dict):
        raise ValueError('the feel is a vibe name, or {"vibe": name, "listening": true|false, "overrides": {...}}')
    v = dict(value)
    if "name" in v:
        name = v.pop("name")
        if "vibe" in v and v["vibe"] != name:
            raise ValueError("name the vibe once: vibe or name, not both")
        v.setdefault("vibe", name)
    if extra := sorted(map(str, set(v) - set(KEYS))):
        raise ValueError(f"unknown keys {extra}; the feel takes vibe, listening and overrides")
    if "vibe" not in v:
        raise ValueError('say which feel: {"vibe": name} (or {"vibe": null} for no feel)')
    vibe = v["vibe"]
    if vibe is None:
        return dict(NO_FEEL)                      # no feel: the director neutral (the put-back of "nothing set")
    if not isinstance(vibe, str) or not NAME_RE.match(vibe):
        raise ValueError(f"{vibe!r} is not a vibe name (a-z, 0-9, _ and -, up to 32 characters)")
    listening = v.get("listening")
    if listening is None:
        listening = False
    if not isinstance(listening, bool):
        raise ValueError("listening is true or false")
    out: dict = {"vibe": vibe, "listening": listening}
    over = v.get("overrides")
    if over is not None:
        if not isinstance(over, dict):
            raise ValueError("overrides is an object: {vibe field: value}")
        if bad := sorted(str(k) for k in over if not isinstance(k, str) or not FIELD_RE.match(k)):
            raise ValueError(f"overrides: {bad} are not vibe field names")
        if fixed := sorted(k for k in over if k in FIXED_FIELDS):
            raise ValueError(f"overrides: {fixed} belong to the vibe and can't be overridden")
        over = {k: _dedupe(x) for k, x in over.items() if x is not None}     # null = unset: the vibe's own value
        if over:
            out["overrides"] = over
    return out


def _dedupe(x: Any) -> Any:
    """A list keeps the first of each item, in order (show_service's own rule for list fields)."""
    if not isinstance(x, list):
        return x
    out: list = []
    for item in x:
        if item not in out:
            out.append(item)
    return out


def readback(body: Any) -> dict:
    """GET/PUT /api/v1/vibe's answer (VibeReadback) -> the canonical value ({"vibe": null, ...} for no feel, a fact:
    show_service answers for its own target). ValueError when the answer isn't one."""
    if not isinstance(body, dict):
        raise ValueError("show_service's /api/v1/vibe answer is not an object")
    if body.get("vibe") is None:
        return dict(NO_FEEL)
    return canonical({k: body[k] for k in KEYS if k in body})


def flag_value(member: str, value: Any) -> bool:
    """show.mic's or show.ai's value, canonical: True (this mode follows the music: the room's mic listens; this mode
    lets the AI listen) or False, from a bool or "on"/"off"/"listen"/"listens"/"true"/"false" in any case. Pure;
    ValueError on anything else."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in MIC_WORDS:
        return MIC_WORDS[value.strip().lower()]
    said = f"{value!r}" if isinstance(value, (str, int, float)) or value is None else f"a {type(value).__name__}"
    raise ValueError(f'{FLAGS[member]["is"]} or false, not {said}; a feel goes on "show.main" '
                     f'(a bare "show" names the mic and AI listening too)')


def mic_value(value: Any) -> bool:
    return flag_value("mic", value)


def ai_value(value: Any) -> bool:
    return flag_value("ai", value)


def flag_mode(member: str, body: Any) -> bool:
    """GET/PUT /api/v1/mic's or /api/v1/listening's answer -> its "mode" (the mode's claim on the mic; the mode's leave
    for the AI to listen). ValueError when the answer has none."""
    if not isinstance(body, dict) or not isinstance(body.get("mode"), bool):
        raise ValueError(f"show_service's {PATHS[member]} answer has no mode (true or false)")
    return body["mode"]


def mic_mode(body: Any) -> bool:
    return flag_mode("mic", body)


def _boot_of(body: Any) -> str | None:
    return body.get("boot_id") if isinstance(body, dict) and isinstance(body.get("boot_id"), str) else None


def _why(e: BaseException) -> str:
    return "timeout" if isinstance(e, (asyncio.TimeoutError, TimeoutError)) else (str(e) or type(e).__name__)


def _refusal(body: Any) -> str:
    """show_service's 400 in words: '<error>: <detail>' plus the choices or problems it lists."""
    if not isinstance(body, dict):
        return "show_service refused it"
    words = [str(body.get("error") or "refused")]
    if body.get("detail"):
        words.append(str(body["detail"]))
    for key in ("choices", "problems"):
        if isinstance(body.get(key), list) and body[key]:
            words.append(f"{key}: {', '.join(map(str, body[key][:12]))}")
    return "; ".join(words)


def _failed(detail: str, retry: bool = True, member: str = "main") -> Outcome:
    return Outcome("failed", detail, {member: Outcome("failed", detail, retry=retry)}, retry=retry)


class _Flag:
    """A bool member's own state: the value apply() last landed (None while one is in flight) and show_service's
    boot_id then, the boots already re-applied to (once each), the re-apply task, and why it was last left out of a
    reading another route answered."""

    def __init__(self) -> None:
        self.applied: bool | None = None
        self.applied_boot: str | None = None
        self.reapplied: set[str] = set()
        self.reapply: asyncio.Task | None = None
        self.gap: str | None = None


class ShowAdapter(Adapter):
    subsystem = "show"
    subsystem_label = "Feel"
    category = "setting"          # the room's own behaviour: the mode editor's "Behind the scenes", never a console row
    kind = "level"
    members = ["main", "mic", "ai"]
    change_time_s = 2.0
    poll_s = 5.0
    read_timeout_s = 2.0

    def __init__(self, url: str = SHOW_URL, vibes_file: str | Path = VIBES_FILE,
                 cache_file: str | Path | None = None) -> None:
        """cache_file: where the last good catalog and situations are kept (the room's state dir), so the Feel form
        and the Auto vocabulary survive a restart while show_service is down. None: memory only."""
        self.url = url.rstrip("/")
        self.vibes_file = Path(vibes_file)
        self.cache_file = Path(cache_file) if cache_file else None
        self._yaml_key: tuple[float, int] | None = None                  # vibes.yaml's (mtime, size) last looked at
        self._yaml: tuple[list[str], dict[str, str]] | None = None       # its last good (order, labels)
        self._live: tuple[list[str], dict[str, str]] | None = None   # (order, labels) fetched by this process
        self._fields: dict[str, dict] = {}      # field -> JSON schema (catalog)
        self._situations: list[dict] = []
        self._next_fetch = 0.0
        self._task: asyncio.Task | None = None
        self._applied: dict | None = None        # the last value apply() landed, and show_service's boot_id then
        self._applied_boot: str | None = None
        self._reapplied: set[str] = set()        # boot_ids already re-applied to (once each)
        self._reapply: asyncio.Task | None = None
        self._boot: str | None = None            # show_service's boot_id, as last seen in any answer
        self._flags = {m: _Flag() for m in FLAGS}    # the mic's claim and AI listening's leave, each its own
        try:
            saved = _load(self.cache_file) if self.cache_file else {}
        except OSError as e:                     # unreadable: start without it (never stop the room starting)
            log.warning("show: catalog cache not read: %s", e)
            saved = {}
        saved = saved if isinstance(saved, dict) else {}
        self._saved_names = self._names_from(saved)          # {"order", "vibes": {name: {"label"}}}
        if isinstance(saved.get("fields"), dict):
            self._fields = {k: s for k, s in saved["fields"].items() if isinstance(s, dict)}
        if isinstance(saved.get("situations"), list):
            self._situations = [s for s in saved["situations"] if isinstance(s, dict) and isinstance(s.get("id"), str)]

    # -- names, labels, fields (no I/O beyond a stat of vibes.yaml) ----------------------------------------------------
    @staticmethod
    def _names_from(cat: Any) -> tuple[list[str], dict[str, str]] | None:
        """(order, labels) from a VibesCatalog-shaped {"order", "vibes": {name: {"label"}}}; None if it isn't one."""
        if not isinstance(cat, dict) or not isinstance(cat.get("vibes"), dict):
            return None
        vibes = {n: x for n, x in cat["vibes"].items() if isinstance(n, str) and NAME_RE.match(n)}
        order = [n for n in (cat.get("order") or []) if isinstance(n, str) and n in vibes]
        order = list(dict.fromkeys(order)) + sorted(set(vibes) - set(order))     # missing ones: alphabetically (S3.5)
        labels = {n: str(vibes[n].get("label") or n) if isinstance(vibes[n], dict) else n for n in order}
        return order, labels

    def _from_yaml(self) -> tuple[list[str], dict[str, str]] | None:
        """Names and labels parsed from vibes.yaml (mtime-cached; the last good parse on a bad edit)."""
        try:
            st = self.vibes_file.stat()
        except OSError:
            return None
        key = (st.st_mtime, st.st_size)
        if self._yaml_key != key:                # parsed once per change; a bad edit keeps the last good names
            self._yaml_key = key
            try:
                got = self._names_from(yaml.safe_load(self.vibes_file.read_text()))
            except Exception as e:               # OSError, YAMLError: this is a getter the editor calls
                log.debug("show: %s not read: %s", self.vibes_file, e)
                got = None
            self._yaml = got or self._yaml
        return self._yaml

    def vibes(self) -> tuple[list[str], dict[str, str]]:
        """(names in card order, {name: label}): show_service's live catalog, else vibes.yaml, else the saved one."""
        for got in (self._live, self._from_yaml(), self._saved_names):
            if got and got[0]:
                return list(got[0]), dict(got[1])
        return [], {}

    def situations(self) -> list[dict]:
        """The Auto vocabulary (SituationDef list) from show_service; [] before the first fetch."""
        return [dict(s) for s in self._situations]

    def vocabulary(self) -> dict[str, list]:
        return {"situations": self.situations()}

    # -- the editor ----------------------------------------------------------------------------------------------------
    def label(self, member: str) -> str:
        return {"mic": "Microphone", "ai": "AI listening"}.get(member, "Feel")

    def value_schema(self, member: str) -> dict:
        if member in FLAGS:                         # two choices, as the pages' on/off settings (PageAdapter)
            words = MIC_HELP if member == "mic" else AI_HELP
            return {"type": "boolean", "default": True, "description": words, "x-ui": {
                "label": self.label(member), "labels": {"true": "Listens in this mode", "false": "Off"},
                "description": words, "help": words}}
        order, labels = self.vibes()
        vibe: dict = {"type": "string", "x-ui": {"label": "Feel", "widget": "select", "labels": labels,
                                                 "unset_label": "No feel"}}
        if order:                                   # no enum at all while no name is known (not "nothing is valid")
            vibe["enum"] = order
        fine: dict[str, dict] = {}
        for name, schema in self._fields.items():
            if name in FIXED_FIELDS or not isinstance(schema, dict):
                continue
            s = copy.deepcopy(schema)
            ui = dict(s.get("x-ui") or {})
            ui.pop("advanced", None)
            if name not in BASIC_FIELDS or ui.get("not_built"):
                ui["advanced"] = True
            s["x-ui"] = ui
            fine[name] = s
        return {"type": "object", "required": ["vibe"], "x-ui": {"label": "Feel", "default_policy": "ignore"},
                "properties": {
                    "vibe": vibe,
                    "listening": {"type": "boolean", "default": False, "description": LISTEN_HELP, "x-ui": {
                        "label": "Listen with AI", "widget": "checkbox", "description": LISTEN_HELP, "help": LISTEN_HELP,
                        "hidden": True,           # one AI listening switch per mode: show.ai's (8f, 20 Oct)
                        "labels": {"true": "AI listening: on", "false": "AI listening: off"}}},
                    "overrides": {"type": "object", "properties": fine, "x-ui": {
                        "label": "Fine-tune", "advanced": True, "unset_label": "As the feel",
                        # the words for an unset field by the chosen vibe ("As the Party feel"), for a page that can
                        "unset_label_by": {"vibe": {n: f"As the {labels.get(n, n)} feel" for n in order}}}}}}

    def normalize(self, member: str, value: Any) -> dict | bool:
        if member in FLAGS:
            return flag_value(member, value)
        if member not in self.members:
            raise ValueError(f"show has three members, main (the feel), mic (the mode follows the music) and ai (the "
                             f"mode lets the AI listen), not {member!r}")
        return canonical(value)

    def show(self, member: str, value: Any) -> str:
        """'Party · listening', 'Dinner', 'Chill · fine-tuned', 'No feel'; the mic: 'Mic listens', 'Mic off'; AI
        listening: 'AI listens', 'AI off'."""
        if member in FLAGS and isinstance(value, bool):
            return f"{FLAGS[member]['short']} {'listens' if value else 'off'}"
        if isinstance(value, dict) and "vibe" in value and value.get("vibe") is None:
            return "No feel"
        if not isinstance(value, dict) or not isinstance(value.get("vibe"), str):
            return super().show(member, value)
        _, labels = self.vibes()
        bits = [labels.get(value["vibe"], value["vibe"])]
        if value.get("listening"):
            bits.append("listening")
        if value.get("overrides"):
            bits.append("fine-tuned")
        return " · ".join(bits)

    # -- show_service ----------------------------------------------------------------------------------------------------
    async def _http(self, method: str, path: str, body: Any = None, timeout: float | None = None) -> tuple[int, Any]:
        """(status, JSON body or None). A short-lived session per call: one call every few seconds over loopback, and
        nothing left open on a loop that has gone (tests, a restart). Raises on connection errors and timeouts."""
        t = aiohttp.ClientTimeout(total=timeout or self.read_timeout_s)
        async with aiohttp.ClientSession(timeout=t) as s:
            async with s.request(method, self.url + path, json=body) as r:
                try:
                    data = await r.json(content_type=None)
                except ValueError:
                    data = None
                return r.status, data

    async def _get(self, path: str) -> Any:
        """GET path -> its JSON body. ShowUnreachable when show_service can't be reached or doesn't answer 200."""
        try:
            status, body = await self._http("GET", path)
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, OSError, asyncio.TimeoutError) as e:
            raise ShowUnreachable(f"show_service unreachable ({_why(e)})") from None
        if status != 200:
            raise ShowUnreachable(f"show_service answered {status} to GET {path}")
        return body

    async def read(self, members: list[str]) -> dict[str, Reading]:
        asked = [m for m in self.members if m in members]
        if not asked:
            return {}
        # each member's own route, at once (all within read_timeout_s); the Feel's failure fails the read as before
        got = dict(zip(asked, await asyncio.gather(*(self._get(PATHS[m]) for m in asked), return_exceptions=True)))
        for x in got.values():
            if isinstance(x, BaseException) and not isinstance(x, Exception):
                raise x                             # CancelledError and the like: never swallowed
        out: dict[str, Reading] = {}
        if "main" in got:
            body = got["main"]
            if isinstance(body, BaseException):
                raise body
            self._refresh_soon()
            self._boot = _boot_of(body) or self._boot
            value = readback(body)                  # ValueError on a malformed answer: stale, like unreachable
            self._maybe_reapply(body, value)
            out["main"] = Reading(value)
        seen = next((b for b in map(_boot_of, got.values()) if b), None)    # the boot any answer in this read gave
        gaps: dict[str, Exception] = {}
        for m in FLAGS:
            if m not in got:
                continue
            body, flag = got[m], self._flags[m]
            try:
                if isinstance(body, BaseException):
                    raise body
                mode = flag_mode(m, body)           # ValueError on an answer without "mode": stale, like the Feel
            except (ShowUnreachable, ValueError) as e:
                gaps[m] = e
                continue
            flag.gap = None
            self._boot = _boot_of(body) or self._boot
            self._maybe_reapply_flag(m, _boot_of(body) or seen, mode)
            out[m] = Reading(mode)
        if gaps and not out:
            raise next(iter(gaps.values()))         # nothing answered (a bool member alone): stale, like the Feel
        for m, e in gaps.items():                   # another route answered: show_service is up. Said once per reason
            if str(e) != self._flags[m].gap:
                self._flags[m].gap = str(e)
                log.warning("show: %s isn't read (%s); the rest still is", FLAGS[m]["claim"], e)
        return out

    def _maybe_reapply(self, body: dict, value: dict) -> None:
        """show_service came back cold (state.json gone: a first enable, a wiped or torn state dir): a new boot_id
        and no feel, after this adapter landed one. The room never retries an "ignore" row that already landed, so
        put the value it last applied back once (best effort, in the background)."""
        boot = body.get("boot_id") if isinstance(body.get("boot_id"), str) else None
        want = self._applied
        if (boot is None or value.get("vibe") is not None or want is None or want.get("vibe") is None
                or boot == self._applied_boot or boot in self._reapplied):
            return
        if self._reapply is not None and not self._reapply.done():
            return
        self._reapplied.add(boot)
        log.info("show: show_service restarted cold (boot %s): applying %s again", boot, self.show("main", want))
        self._reapply = asyncio.get_running_loop().create_task(self._put_again(dict(want)), name="show-reapply")

    async def _put_again(self, want: dict) -> None:
        try:
            status, body = await self._http("PUT", "/api/v1/vibe", want, timeout=self.change_time_s + self.read_timeout_s)
            if status == 200 and isinstance(body, dict):
                self._applied_boot = body.get("boot_id") if isinstance(body.get("boot_id"), str) else None
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("show: applying the feel again failed: %s", _why(e))

    def _maybe_reapply_flag(self, member: str, boot: str | None, mode: bool) -> None:
        """The Feel's cold-restart rule for a bool member: a new boot_id with the mode's claim (or leave) off, after
        this adapter landed it on (show_service keeps the mic's claim in its state file, so for the mic this only
        covers a lost one: belt and braces); once per boot, in the background. Nothing while an apply is in flight
        (applied is None then), and nothing on a read that can't tell the boot (the mic alone, when its answer carries
        no boot_id; /api/v1/listening's always carries one)."""
        flag = self._flags[member]
        if boot is None or mode or flag.applied is not True or boot == flag.applied_boot or boot in flag.reapplied:
            return
        if flag.reapply is not None and not flag.reapply.done():
            return
        flag.reapplied.add(boot)
        log.info("show: show_service restarted cold (boot %s): %s", boot, FLAGS[member]["again"])
        flag.reapply = asyncio.get_running_loop().create_task(self._put_flag_again(member),
                                                              name=f"show-{member}-reapply")

    async def _put_flag_again(self, member: str) -> None:
        try:
            status, body = await self._http("PUT", PATHS[member], {"mode": True},
                                            timeout=self.change_time_s + self.read_timeout_s)
            if status == 200 and isinstance(body, dict):
                self._flags[member].applied_boot = _boot_of(body) or self._boot
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("show: %s failed: %s", FLAGS[member]["redo"], _why(e))

    async def apply(self, desired: dict[str, Any], opts: dict | None = None) -> Outcome:
        asked = [m for m in self.members if m in desired]
        if not asked:
            return Outcome("ok")
        one = {"main": self._apply_main, **{m: functools.partial(self._apply_flag, m) for m in FLAGS}}
        if len(asked) == 1:
            return await one[asked[0]](desired[asked[0]])
        outs = await asyncio.gather(*(one[m](desired[m]) for m in asked))     # independent routes: at once
        members = {m: o.members.get(m, o) for m, o in zip(asked, outs)}
        bad = {m: o for m, o in members.items() if o.status != "ok"}
        worst = max((o.status for o in members.values()), key=STATUSES.index)
        detail = "; ".join(f"{self.label(m)}: {o.detail}" for m, o in bad.items() if o.detail)
        return Outcome(worst, detail, members, retry=any(o.retry for o in bad.values()) if bad else True)

    async def _apply_flag(self, member: str, value: Any) -> Outcome:
        """PUT {"mode": value} on the member's route (the mic's claim, AI listening's leave); ok once show_service's
        answer (or a read-back within change_time_s) says so."""
        path, flag = PATHS[member], self._flags[member]
        try:
            want = flag_value(member, value)
        except ValueError as e:
            return _failed(str(e), retry=False, member=member)
        flag.applied = None                         # unknown until it lands: no cold-restart re-apply meanwhile
        try:
            status, body = await self._http("PUT", path, {"mode": want}, timeout=self.change_time_s + self.read_timeout_s)
        except asyncio.CancelledError:
            raise
        except Exception as e:                      # refused, reset, timeout: show_service is down or busy
            return _failed(f"show_service unreachable ({_why(e)})", member=member)
        if status == 400:                           # e.g. a show_service whose /api/v1/listening takes only "on"
            return _failed(_refusal(body), retry=False, member=member)
        if status >= 500:
            code = body.get("error") if isinstance(body, dict) else None
            return _failed(f"show_service unreachable ({status}{f' {code}' if code else ''})", member=member)
        if status != 200:                           # e.g. 404: show_service without that API yet
            return _failed(f"show_service answered {status} to PUT {path}", member=member)
        try:
            got: bool | None = flag_mode(member, body)
        except ValueError:
            got = None
        deadline = time.monotonic() + self.change_time_s
        while got is not want and time.monotonic() < deadline:
            await asyncio.sleep(0.25)
            try:
                got = (await self.read([member]))[member].value
            except (ShowUnreachable, ValueError):
                got = None
        if got is want:
            flag.applied, flag.applied_boot = want, _boot_of(body) or self._boot
            return Outcome("ok", "", {member: Outcome("ok")})
        detail = f"reads {self.show(member, got) if got is not None else 'no ' + FLAGS[member]['noun'] + ' answer'}"
        return Outcome("unconfirmed", detail, {member: Outcome("unconfirmed", detail)})

    async def _apply_main(self, value: Any) -> Outcome:
        try:
            want = canonical(value)
        except ValueError as e:
            return _failed(str(e), retry=False)
        try:
            status, body = await self._http("PUT", "/api/v1/vibe", want, timeout=self.change_time_s + self.read_timeout_s)
        except asyncio.CancelledError:
            raise
        except Exception as e:                      # refused, reset, timeout: show_service is down or busy
            return _failed(f"show_service unreachable ({_why(e)})")
        if status == 400:
            return _failed(_refusal(body), retry=False)
        if status >= 500:
            code = body.get("error") if isinstance(body, dict) else None
            return _failed(f"show_service unreachable ({status}{f' {code}' if code else ''})")
        if status != 200:                           # e.g. 404: show_service without its Stage 3 API yet
            return _failed(f"show_service answered {status} to PUT /api/v1/vibe")
        self._refresh_soon()
        try:
            got = readback(body)
        except ValueError:
            got = None
        deadline = time.monotonic() + self.change_time_s
        while got != want and time.monotonic() < deadline:
            await asyncio.sleep(0.25)
            try:
                got = (await self.read(["main"]))["main"].value
            except (ShowUnreachable, ValueError):
                got = None
        if got == want:
            self._applied = want
            self._applied_boot = body.get("boot_id") if isinstance(body, dict) and isinstance(
                body.get("boot_id"), str) else self._applied_boot
            return Outcome("ok", "", {"main": Outcome("ok")})
        detail = f"reads {self.show('main', got) if got else 'no vibe'}"
        return Outcome("unconfirmed", detail, {"main": Outcome("unconfirmed", detail)})

    # -- the catalog cache (best effort, never in the read's time budget) ------------------------------------------------
    def _refresh_soon(self) -> None:
        loop = asyncio.get_running_loop()
        if self._task is not None and not self._task.done() and self._task.get_loop() is loop:
            return
        if time.monotonic() < self._next_fetch:
            return
        self._next_fetch = time.monotonic() + RETRY_S       # pushed out to CACHE_S when the fetch works
        self._task = loop.create_task(self._refresh(), name="show-catalog")

    async def _refresh(self) -> None:
        ok = False
        try:
            status, cat = await self._http("GET", "/api/v1/vibes", timeout=FETCH_TIMEOUT_S)
            names = self._names_from(cat) if status == 200 else None
            if names is not None:
                self._live = names
                fields = cat.get("fields")
                if isinstance(fields, dict):
                    self._fields = {k: s for k, s in fields.items() if isinstance(k, str) and isinstance(s, dict)}
                ok = True
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.debug("show: GET /api/v1/vibes failed: %s", _why(e))
        try:
            status, sit = await self._http("GET", "/api/v1/situations", timeout=FETCH_TIMEOUT_S)
            voc = sit.get("vocabulary") if status == 200 and isinstance(sit, dict) else None
            if isinstance(voc, list):
                self._situations = [s for s in voc if isinstance(s, dict) and isinstance(s.get("id"), str)]
                ok = True
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.debug("show: GET /api/v1/situations failed: %s", _why(e))
        if not ok:
            return
        self._next_fetch = time.monotonic() + CACHE_S
        if self.cache_file is not None:
            order, labels = self._live or self._saved_names or ([], {})     # a failed half keeps what was saved
            saved = {"vibes": {n: {"label": labels.get(n, n)} for n in order}, "order": order,
                     "fields": self._fields, "situations": self._situations, "saved_at": time.time()}
            try:
                await asyncio.to_thread(_save, self.cache_file, saved)   # off the room's loop (fsync)
            except OSError as e:
                log.warning("show: catalog cache not saved: %s", e)
