"""treatment: the art director's plan for a song (owner, 28-29 Oct 2026; README S5.33).

The owner, 28 Oct: occasional light moods "should be up to the a.d. and in a more sophisticated way"; 29 Oct: "we
need the a.d. to work with what it gets (midi, mic, ai, sonos metadata, scene/effect ratings, live operator feedback)
and do its best under most circumstances". Until now every channel drew on its own: the renderer's scene, the
palette, the lamps' light scene and the ensemble's form were separate weighted draws, so nothing chose them together
and nothing remembered the last song. A **treatment** is one decision per song, made together, that every channel
then plays on the beat clock as before:

  favour     scene families (catalog tags ``family:<word>``) the renderer gives at least ``favour_share`` of its picks
             (room.direction ``scene_favour``; stream_service's auto-director)
  avoid      families it steers away from (``scene_avoid``: x ``avoid_factor``, soft): the ones the last songs used
  palette    the look's palette for the song (one of the feel's list, or director.no_feel's), unless a person picked one
  lights     {light group: scene} from that group's pool (light_scenes.Catalogue.pool_for: a mode's tags still bind),
             preferred x ``light_prefer`` in its draw (the AI's pick; the director's own plan leaves the draw alone)
  form       an interplay form (config/live/interplays.yaml), preferred x ``form_prefer`` in the ensemble's draw
  moods, transition_style, intensity   the words and the wildness for the song (the AI's; None: the usual chain)
  bias       the room's taste for this kind of music: a small energy offset learned from Calmer / Wilder
  why        one plain line for the Room page

**Who makes it.** Whatever the room offers at the time (``Evidence``, built by the director each second):
  * the song itself: Sonos's title and artist, or the Music page player's piece, when either plays; else "live" (the
    mic, the piano's MIDI) with no metadata;
  * what is heard: the genre (the AI listener's when confident, else the local ML's, else the feel's), moods, the
    energy band, valence and acoustic, the tempo, the piano playing;
  * the room: the mode and its feel (with its persona), which appliances are on, what each light group may show;
  * the catalogue: scene families with how well they fit the music (stream_service/tags.py's own rule, as the light
    scenes use it) and the owner's stars (/renderer/dev/ratings, the renderer's weights);
  * taste: what people did during earlier songs of this kind in this mode (below);
  * history: the last songs' treatments, so consecutive songs differ.
The director's own plan (``by: director``) is made at once, at every song change, from those facts: no network, so it
is there for the song's first look. When the AI may listen (the listener's beliefs are live: the Room page's AI
listening switch is the only on/off, owner 21 Oct), one text call (llm.yaml task ``treatment``) is made per song:
at once when the song has a title, else once the listener has heard ``ai.refine_after_s`` of it. Its answer is
checked against the options it was given (every name must be one of them) and replaces the director's plan for the
rest of the song (``by: ai``). A failed or late call keeps the director's plan; nothing waits for it.

**A song** is structure's track change (Sonos, the player, or the sound's own evidence after a gap), a feel entering,
or ``replan_s`` of one unbroken stretch (a DJ mix or a long jam has no boundaries).

**Taste** (live operator feedback; ``Taste``, persisted in the state dir): keyed by mode and music group
(contract.art_group). Next look while a treatment plays: its favoured families -1, its palette -0.5. Keep this look:
+1 and +0.5. A palette picked by hand: +1 for it. Calmer / Wilder: the bias moves ``bias_per_step`` per step (at most
``bias_max`` either way). Every entry halves over ``half_life_days``. The scores multiply the families' and palettes'
fits by exp(TASTE_K x score), and the AI reads them as plain lines ("skipped twice: cosmic").

Nothing here knows a mode, feel, scene, palette or device by name: every name comes from the catalogue, the feel and
the room. Pure apart from the taste file and the one async call; show-brain only.
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import math
import os
import sys
import time
from typing import Any, Callable, Optional

from . import contract
from . import light_scenes as lscenes

log = logging.getLogger("show_service.treatment")

TASTE_FILE = contract.STATE_DIR / "treatment_taste.json"
TASK = "treatment"                    # config/llm.yaml's task
BY_DIRECTOR, BY_AI = "director", "ai"
TASTE_K = 0.35                        # exp(TASTE_K x score): +1 ~ x1.4, -2 ~ x0.5 (as stream_service/taste.py)
STAR_WEIGHT = {0: 0.35, 1: 0.05, 2: 0.3, 3: 1.5, 4: 4.0, 5: 8.0}   # config/live/rating_weights.json's table (0:
                                      # unrated): the renderer's own weights for the owner's stars
FIT_POWER = 3.0                       # stream_service/director.py STYLE_POWER: how much the music's fit counts
FAMILY_TOP = 8                        # a family scores by its best few scenes: a big family isn't diluted
FAMILY_WORD = "family"                # the catalog's facet for scene families (tags.family)
NOTES = "notes"                       # a plug-in's listens word for the piano's notes (stream_service sound.py)
NOTES_RECENT_S = 120.0                # the renderer's NOTES_RECENT_S[1]: no note this long, no notes scenes
HISTORY_N = 8
RECENT_FACTORS = (0.5, 0.25)          # a family the last song favoured x0.5, the one before x0.25 (one song back
                                      # first): consecutive songs look different
LEARN_S = 90.0                        # the director's plan follows the music's group for this long into a song
NEAR_BEST = 0.6                       # a second family is favoured when it scores within this share of the best

#: show.yaml ``treatment``: starting points, all of them. The file's values win.
FALLBACK: dict = {
    "enabled": True,                  # (not "on": YAML reads on/off as booleans)
    "favour_share": 0.5,              # the favoured families' share of the renderer's scene picks
    "families_max": 2,
    "avoid_factor": 0.3,              # a steered-away family's scenes x this (soft, never removed)
    "recent_songs": 2,                # a family the last N songs all favoured is steered away from
    "palette_songs_max": 3,           # a palette that ran this many songs moves on, even for a small change
    "replan_s": 480.0,                # one unbroken stretch this long is a new song for the plan
    "light_prefer": 8.0,
    "form_prefer": 4.0,
    "ai": {"timeout_s": 10.0, "refine_after_s": 12.0, "min_gap_s": 20.0, "max_per_song": 1},
    "taste": {"half_life_days": 14.0, "bias_per_step": 0.03, "bias_max": 0.15},
}


def _num(v: Any, default: Optional[float] = None) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return default
    return float(v)


def _c01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def parse(raw: Any, probs: list[str]) -> dict:
    """show.yaml ``treatment`` over FALLBACK: a bad value keeps its fallback and is listed."""
    cfg = json.loads(json.dumps(FALLBACK))
    if raw is None:
        return cfg
    if not isinstance(raw, dict):
        probs.append("treatment: not a mapping")
        return cfg
    for k, v in raw.items():
        if k not in cfg:
            probs.append(f"treatment.{k}: unknown key")
            continue
        d = cfg[k]
        if isinstance(d, dict):
            if not isinstance(v, dict):
                probs.append(f"treatment.{k}: not a mapping")
                continue
            for k2, v2 in v.items():
                if k2 in d and _num(v2) is not None and v2 >= 0:
                    d[k2] = type(d[k2])(v2) if isinstance(d[k2], int) and not isinstance(d[k2], bool) else float(v2)
                else:
                    probs.append(f"treatment.{k}.{k2}: {v2!r}")
        elif isinstance(d, bool):
            if isinstance(v, bool):
                cfg[k] = v
            else:
                probs.append(f"treatment.{k}: {v!r} is not true/false")
        elif _num(v) is not None and v >= 0:
            cfg[k] = int(v) if isinstance(d, int) else float(v)
        else:
            probs.append(f"treatment.{k}: {v!r} is not a number >= 0")
    cfg["favour_share"] = min(0.9, cfg["favour_share"])
    cfg["avoid_factor"] = max(0.05, min(1.0, cfg["avoid_factor"]))
    return cfg


def context_key(mode: Any, genre: Any, genres: Any) -> str:
    """Taste's key: the mode and the music's group (contract.art_group), "-" for either unknown."""
    g = contract.art_group(genre if isinstance(genre, str) else None, genres if isinstance(genres, dict) else None)
    return f"{mode if isinstance(mode, str) and mode else '-'}|{g or '-'}"


# -- the catalogue's scene families -----------------------------------------------------------------------------------
class SceneIndex:
    """The renderer's auto scenes by family (GET /renderer/api/catalog's presets.scenes) and the owner's stars per
    plug-in (GET /renderer/dev/ratings), refreshed with the director's catalog poll."""

    def __init__(self) -> None:
        self.families: dict[str, list[dict]] = {}
        self.moods: tuple[str, ...] = ()
        self.styles: tuple[str, ...] = ()
        self.stars: dict[str, int] = {}
        self.rejected: set[str] = set()
        self.notes_sources: set[str] = set()       # plug-ins that draw the piano's notes (catalog sources' listens)
        self.error: Optional[str] = None
        self._tags = lscenes._TagsModule(lscenes.TAGS_FILE)

    def ingest(self, catalog: Any) -> None:
        fam: dict[str, list[dict]] = collections.defaultdict(list)
        sc = ((catalog or {}).get("presets") or {}).get("scenes") if isinstance(catalog, dict) else None
        if not isinstance(sc, dict):
            self.error = "the renderer's catalog had no scenes"
            return
        for name, p in sc.items():
            if not isinstance(p, dict) or p.get("auto") is False:
                continue
            t = p.get("tags") if isinstance(p.get("tags"), dict) else {}
            if not t or t.get("auto") is False or not isinstance(t.get(FAMILY_WORD), str):
                continue
            fam[t[FAMILY_WORD]].append({"name": name, "source": p.get("source"), "tags": t})
        self.families = dict(fam)
        self.notes_sources = {str(x.get("name")) for x in catalog.get("sources") or []
                              if isinstance(x, dict) and NOTES in (x.get("listens") or ())}
        voc = catalog.get("vocab") if isinstance(catalog.get("vocab"), dict) else {}
        self.moods = tuple(w for w in voc.get("moods") or () if isinstance(w, str))
        self.styles = tuple(w for w in voc.get("transition_style") or () if isinstance(w, str))
        self.error = None

    def ingest_ratings(self, body: Any) -> None:
        r = (body or {}).get("ratings") if isinstance(body, dict) else None
        if not isinstance(r, dict):
            return
        stars, rej = {}, set()
        for key, e in r.items():
            if not isinstance(key, str) or not key.startswith("scene/") or not isinstance(e, dict):
                continue
            src = key.split("/", 1)[1]
            if e.get("reject"):
                rej.add(src)
            s = e.get("stars")
            if isinstance(s, int) and not isinstance(s, bool) and 1 <= s <= 5:
                stars[src] = s
        self.stars, self.rejected = stars, rej

    def scores(self, direction: dict, notes: bool = True) -> dict[str, dict]:
        """{family: {"score", "fit", "stars", "n"}}: the mean of its best FAMILY_TOP scenes' fit^FIT_POWER x star
        weight (the renderer's own product); fit = their mean fit alone; stars = their mean stars (rated only).
        ``notes`` False (no piano heard lately): the scenes that draw notes are left out, as the renderer leaves
        them (stream_service/director.py NOTES_NONE)."""
        mod = self._tags.get()
        out: dict[str, dict] = {}
        for f, scenes in self.families.items():
            rows = []
            for s in scenes:
                if s["source"] in self.rejected or (not notes and s["source"] in self.notes_sources):
                    continue
                try:
                    fit = float(mod.scene_weight(s["tags"], direction)) if mod is not None else 1.0
                except Exception:                     # noqa: BLE001 - a bad tag reads neutral
                    fit = 1.0
                st = self.stars.get(s["source"], 0)
                rows.append((fit ** FIT_POWER * STAR_WEIGHT.get(st, 1.0), fit, st))
            if not rows:
                continue
            rows.sort(key=lambda r: -r[0])
            top = rows[:FAMILY_TOP]
            rated = [r[2] for r in top if r[2]]
            out[f] = {"score": sum(r[0] for r in top) / len(top), "fit": round(sum(r[1] for r in top) / len(top), 3),
                      "stars": round(sum(rated) / len(rated), 1) if rated else None, "n": len(scenes)}
        return out


# -- taste (live operator feedback) -----------------------------------------------------------------------------------
class Taste:
    """{ctx: {"families": {f: [score, t]}, "palettes": {p: [score, t]}, "bias": [value, t]}} with half-life decay,
    persisted (atomically) after every change. A missing or broken file is an empty taste."""

    def __init__(self, path: Optional[os.PathLike] = TASTE_FILE, wall: Callable[[], float] = time.time) -> None:
        self.path = path
        self._wall = wall
        self.cfg = dict(FALLBACK["taste"])
        self.data: dict[str, dict] = {}
        self.error: Optional[str] = None
        if path is not None:
            try:
                with open(path, encoding="utf-8") as f:
                    d = json.load(f)
                if isinstance(d, dict) and isinstance(d.get("contexts"), dict):
                    self.data = d["contexts"]
            except FileNotFoundError:
                pass
            except Exception as e:                    # noqa: BLE001 - a broken file is an empty taste
                self.error = f"{type(e).__name__}: {e}"

    def _decayed(self, entry: Any) -> float:
        if not isinstance(entry, list) or len(entry) != 2:
            return 0.0
        v, t = _num(entry[0], 0.0), _num(entry[1], 0.0)
        hl = max(0.1, float(self.cfg["half_life_days"])) * 86400.0
        return v * 0.5 ** (max(0.0, self._wall() - t) / hl)

    def family(self, ctx: str, fam: str) -> float:
        return self._decayed(((self.data.get(ctx) or {}).get("families") or {}).get(fam))

    def palette(self, ctx: str, name: str) -> float:
        return self._decayed(((self.data.get(ctx) or {}).get("palettes") or {}).get(name))

    def bias(self, ctx: str) -> float:
        b = self._decayed((self.data.get(ctx) or {}).get("bias"))
        m = float(self.cfg["bias_max"])
        return max(-m, min(m, b))

    def _add(self, ctx: str, part: str, key: Optional[str], delta: float) -> None:
        c = self.data.setdefault(ctx, {})
        now = self._wall()
        if part == "bias":
            m = float(self.cfg["bias_max"])
            c["bias"] = [round(max(-m, min(m, self.bias(ctx) + delta)), 4), now]
            return
        d = c.setdefault(part, {})
        d[key] = [round(self._decayed(d.get(key)) + delta, 4), now]

    def record(self, kind: str, ctx: str, plan: Optional[dict], *, palette: Optional[str] = None,
               steps: int = 0) -> Optional[str]:
        """One piece of feedback: kind next | keep | palette | steps. Returns its plain words, or None."""
        words = None
        fams = list((plan or {}).get("families") or [])
        pal = (plan or {}).get("palette_used")
        if kind in ("next", "keep"):
            sign = -1.0 if kind == "next" else 1.0
            for f in fams:
                self._add(ctx, "families", f, sign)
            if pal:
                self._add(ctx, "palettes", pal, 0.5 * sign)
            if fams or pal:
                words = ("less " if sign < 0 else "more ") + ", ".join(fams + ([pal] if pal else []))
        elif kind == "palette" and palette:
            self._add(ctx, "palettes", palette, 1.0)
            words = f"more {palette}"
        elif kind == "steps" and steps:
            self._add(ctx, "bias", None, float(self.cfg["bias_per_step"]) * steps)
            words = "wilder" if steps > 0 else "calmer"
        if words:
            self._save()
        return words

    def lines(self, ctx: str, n: int = 6) -> list[str]:
        """The taste for ``ctx`` in plain lines for the AI ("liked: cosmic", "skipped: score"), strongest first."""
        c = self.data.get(ctx) or {}
        rows = [(self._decayed(e), f"{k} scenes") for k, e in (c.get("families") or {}).items()]
        rows += [(self._decayed(e), f"the {k} palette") for k, e in (c.get("palettes") or {}).items()]
        rows = [r for r in rows if abs(r[0]) >= 0.25]
        rows.sort(key=lambda r: -abs(r[0]))
        out = [("liked " if v > 0 else "skipped ") + w + f" ({v:+.1f})" for v, w in rows[:n]]
        b = self.bias(ctx)
        if abs(b) >= 0.01:
            out.append(f"people made it {'wilder' if b > 0 else 'calmer'} before ({b:+.2f})")
        return out

    def _save(self) -> None:
        if self.path is None:
            return
        try:
            p = str(self.path)
            os.makedirs(os.path.dirname(p), exist_ok=True, mode=0o700)
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "contexts": self.data}, f, separators=(",", ":"))
            os.replace(tmp, p)
            self.error = None
        except Exception as e:                        # noqa: BLE001 - a full disk never stops the show
            self.error = f"{type(e).__name__}: {e}"


# -- the AI's tool ------------------------------------------------------------------------------------------------------
TOOL: dict = {
    "name": "set_treatment",
    "description": "Choose the room's treatment for the song now playing, only from the options given.",
    "parameters": {
        "type": "object",
        "properties": {
            "families": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
            "avoid_families": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
            "palette": {"type": ["string", "null"]},
            "lights": {"type": "array", "maxItems": 4, "items": {
                "type": "object", "properties": {"group": {"type": "string"}, "scene": {"type": "string"}},
                "required": ["group", "scene"]}},
            "form": {"type": ["string", "null"]},
            "moods": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
            "transition_style": {"type": ["string", "null"]},
            "intensity": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
            "why": {"type": "string"},
        },
        "required": ["families", "avoid_families", "palette", "lights", "form", "moods", "transition_style",
                     "intensity", "why"],
    },
}

#: the room the AI art-directs and what is in it, in its system prompt: deployment.yaml ``wording.ad_room`` and
#: ``wording.ad_rig`` (finsbury: its drawing room, two TVs, lamps, the glitterball and pinspots), else generic phrases
AD_ROOM = contract.DEPLOYMENT.words("ad_room", "a room")
AD_RIG = contract.DEPLOYMENT.words("ad_rig", "screens showing generative scenes, and lights")

SYSTEM = (
    f"You are the art director of {AD_ROOM}: {AD_RIG}. "
    "For the song now playing choose ONE treatment that makes the room feel right for "
    "this music, within the room's mode and its feel. Use only the names offered. Rules: pick 1-2 scene families "
    "that suit the music (fit and the owner's stars are given; stars matter); make this song look different from "
    "the last ones (avoid repeating their families or palette unless the music is a continuation); respect what "
    "people liked or skipped; pick a palette from the list; for each light group pick one scene from its pool or "
    "leave it out; pick an interplay form or null; moods from the vocabulary; intensity 0 (gentle) to 1 (wild) for "
    "how hard the room should go. 'why' is one plain sentence for the owner, under 20 words, no jargon, no names "
    "of files or ids. Answer with the tool only.")


def _short(v: Any, n: int = 80) -> Optional[str]:
    return str(v)[:n] if isinstance(v, str) and v.strip() else None


class Treatments:
    """The director's per-song plans. ``song`` (a new song: the director's own plan at once), ``tick`` (once a
    second: the AI's call when due; a long stretch: a new plan), ``feedback`` (people), ``view`` (snapshot / card)."""

    def __init__(self, *, log_event: Optional[Callable[[str, dict], None]] = None,
                 taste: Optional[Taste] = None, mono: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time) -> None:
        self.cfg = json.loads(json.dumps(FALLBACK))
        self.index = SceneIndex()
        self.taste = taste if taste is not None else Taste()
        self._log = log_event or (lambda k, d: None)
        self._mono = mono
        self._wall = wall
        self.plan: Optional[dict] = None
        self.history: collections.deque[dict] = collections.deque(maxlen=HISTORY_N)
        self.rev = 0
        self._n = 0
        self._song_s: Optional[float] = None
        self._ai_busy = False
        self._ai_last_s: Optional[float] = None
        self._ai_song: Optional[str] = None
        self._ai_calls = 0
        self.ai_state: dict = {"last": None, "error": None, "calls": 0, "cost_usd": 0.0}
        self._llm: Any = None

    def configure(self, raw: Any, probs: list[str]) -> None:
        self.cfg = parse(raw, probs)
        self.taste.cfg = dict(self.cfg["taste"])

    # -- a new song: the director's own plan ---------------------------------------------------------------------------
    def song(self, now_s: float, song: str, why: str, ev: dict) -> Optional[dict]:
        """A new song (``song``: its key; ``why``: the director's words for the change): the director's own plan,
        made now from ``ev``. The plan it replaces goes to the history (what the next ones differ from)."""
        if not self.cfg["enabled"]:
            self.plan = None
            return None
        if self.plan is not None:
            self.history.appendleft(self.plan)
        self._song_s = now_s
        self._ai_song, self._ai_calls = song, 0
        self.plan = self._local(now_s, song, why, ev)
        self.rev += 1
        self._log("treatment", {"plan": self._log_view(self.plan)})
        return self.plan

    def _ctx(self, ev: dict) -> str:
        d = ev.get("direction") or {}
        return context_key(ev.get("mode"), d.get("genre"), d.get("genres"))

    def _family_scores(self, ev: dict, ctx: str) -> dict[str, dict]:
        fams = self.index.scores(ev.get("direction") or {}, notes=bool(ev.get("notes_recent", True)))
        recent = [set(p.get("families") or ()) for p in list(self.history)[:len(RECENT_FACTORS)]]
        for f, row in fams.items():
            s = row["score"] * math.exp(TASTE_K * self.taste.family(ctx, f))
            for i, used in enumerate(recent):
                if f in used:
                    s *= RECENT_FACTORS[i]
            row["plan_score"] = s
        return fams

    def _local(self, now_s: float, song: str, why: str, ev: dict) -> dict:
        ctx = self._ctx(ev)
        fams = self._family_scores(ev, ctx)
        ranked = sorted(fams, key=lambda f: -fams[f]["plan_score"])
        chosen: list[str] = []
        if ranked:
            best = fams[ranked[0]]["plan_score"]
            chosen = [f for f in ranked[:int(self.cfg["families_max"])]
                      if best > 0 and fams[f]["plan_score"] >= NEAR_BEST * best]
        n = int(self.cfg["recent_songs"])
        last = [set(p.get("families") or ()) for p in list(self.history)[:n]]
        avoid = sorted(set.intersection(*last) - set(chosen)) if n and len(last) == n and last else []
        palette = self._palette(ev, ctx)
        d = ev.get("direction") or {}
        heard = ", ".join(x for x in [d.get("genre"), *(d.get("moods") or [])[:2]] if isinstance(x, str)) or "the music"
        words = f"{heard}: {' and '.join(chosen) or 'any'} scenes"
        if palette:
            words += f", {palette} colours"
        if avoid:
            words += f"; a rest from {', '.join(avoid)}"
        return self._plan(now_s, song, BY_DIRECTOR, why, ev, ctx, families=chosen, avoid=avoid, palette=palette,
                          why_line=words, fams=fams)

    def _palette(self, ev: dict, ctx: str) -> Optional[str]:
        """The song's palette: None leaves the look's own (a person's pick, or a small change while it has run fewer
        than palette_songs_max songs); else the next in the feel's ranked list after the one showing (as the
        director always moved through it), passing over the ones people disliked here (taste < -0.5) and the ones
        the last two songs used, while another is left."""
        cands = [p for p in ev.get("palettes") or [] if isinstance(p, str)]
        cur = ev.get("palette_now")
        if not cands or ev.get("palette_pick"):
            return None
        run = 0                                           # the songs it has already run
        for h in self.history:
            if h.get("palette_used") == cur:
                run += 1
            else:
                break
        if ev.get("change") == "small" and cur in cands and run < int(self.cfg["palette_songs_max"]):
            return None
        i0 = cands.index(cur) + 1 if cur in cands else 0
        order = [cands[(i0 + k) % len(cands)] for k in range(len(cands))]
        order = [x for x in order if x != cur] or order
        prev = {h.get("palette_used") for h in list(self.history)[:2]}
        for rule in (lambda x: self.taste.palette(ctx, x) >= -0.5 and x not in prev,
                     lambda x: self.taste.palette(ctx, x) >= -0.5):
            got = [x for x in order if rule(x)]
            if got:
                return got[0]
        return order[0]

    def _plan(self, now_s: float, song: str, by: str, why: str, ev: dict, ctx: str, *, families: list[str],
              avoid: list[str], palette: Optional[str], why_line: str, fams: Optional[dict] = None,
              lights: Optional[dict] = None, form: Optional[str] = None, moods: Optional[list] = None,
              style: Optional[str] = None, intensity: Optional[float] = None, model: Optional[str] = None) -> dict:
        self._n += 1
        share = float(self.cfg["favour_share"])
        return {"id": f"T{self._n}", "song": song, "by": by, "model": model, "at_s": now_s,
                "at_wall": round(self._wall(), 1), "change_why": why,
                "ctx": ctx, "families": list(families), "avoid_families": list(avoid),
                "favour": {"tags": [f"{FAMILY_WORD}:{f}" for f in families], "share": share} if families else None,
                "avoid": ({"tags": [f"{FAMILY_WORD}:{f}" for f in avoid], "factor": float(self.cfg["avoid_factor"])}
                          if avoid else None),
                "palette": palette, "palette_used": palette or ev.get("palette_now"),
                "lights": dict(lights or {}), "form": form, "moods": list(moods) if moods else None,
                "transition_style": style, "intensity": intensity, "bias": round(self.taste.bias(ctx), 3),
                "why": why_line, "song_words": ev.get("song_words"),
                "based_on": list(ev.get("based_on") or []),
                "fit": ({f: {"fit": fams[f]["fit"], "stars": fams[f]["stars"]} for f in families if f in fams}
                        if fams else {})}

    # -- each second: the AI's call, a long stretch -------------------------------------------------------------------
    def tick(self, now_s: float, ev: dict, spawn: Optional[Callable[[Any], None]]) -> bool:
        """Once a second. True when a long stretch made a new plan (the director then applies it as a song's)."""
        p = self.plan
        if p is None or not self.cfg["enabled"]:
            return False
        if self._song_s is not None and now_s - self._song_s >= float(self.cfg["replan_s"]):
            self.song(now_s, p["song"], "a long stretch", ev)
            return True
        age = now_s - (self._song_s if self._song_s is not None else now_s)
        if p["by"] == BY_DIRECTOR and age < LEARN_S and self._ctx(ev) != p["ctx"]:
            # what the music is became known (or changed) after the song began: the director's plan follows it
            # (its palette stays: the song's look has landed)
            q = self._local(now_s, p["song"], p["change_why"], {**ev, "palette_pick": True})
            q["palette_used"] = p.get("palette_used")
            self.plan = q
            self.rev += 1
            self._log("treatment", {"plan": self._log_view(q), "why": "the music became clearer"})
            return False
        ac = self.cfg["ai"]
        if (spawn is None or not ev.get("ai_on") or self._ai_busy or p["by"] == BY_AI
                or self._ai_calls >= int(ac["max_per_song"]) or not self.index.families):
            return False
        if self._ai_last_s is not None and now_s - self._ai_last_s < float(ac["min_gap_s"]):
            return False
        m = self._llm_mod()
        try:
            if m is None or (hasattr(m, "over_budget") and m.over_budget(TASK)):
                return False                           # llm.yaml's daily budget: the director's own plans carry on
        except Exception:                             # noqa: BLE001
            return False
        if not ev.get("song_words") and age < float(ac["refine_after_s"]):
            return False                               # no title: let the listener hear some of it first
        self._ai_busy, self._ai_last_s = True, now_s
        self._ai_calls += 1
        spawn(self._ai(now_s, p["song"], ev))
        return False

    def _llm_mod(self) -> Any:
        if self._llm is None:
            try:
                try:
                    import utils.llm as m
                except ImportError:
                    root = str(contract.REPO_ROOT)
                    if root not in sys.path:
                        sys.path.append(root)
                    import utils.llm as m
                self._llm = m
            except Exception as e:                    # noqa: BLE001 - no LLM layer: the director's own plans
                self.ai_state["error"] = f"utils.llm: {type(e).__name__}"
                return None
        return self._llm

    def options(self, ev: dict, ctx: str) -> dict:
        """What the AI may choose from, with the facts beside each."""
        fams = self._family_scores(ev, ctx)
        return {"families": {f: {"fit": r["fit"], "stars": r["stars"], "scenes": r["n"]}
                             for f, r in sorted(fams.items(), key=lambda kv: -kv[1]["plan_score"])},
                "palettes": list(ev.get("palettes") or []),
                "lights": {g: list(pool) for g, pool in (ev.get("light_pools") or {}).items() if pool},
                "forms": list(ev.get("forms") or []), "moods": list(self.index.moods),
                "transition_styles": list(self.index.styles)}

    def _prompt(self, ev: dict, ctx: str, opts: dict) -> str:
        d = ev.get("direction") or {}
        hist = [{"families": p.get("families"), "palette": p.get("palette_used"), "why": p.get("why")}
                for p in list(self.history)[:4]]
        body = {"song": ev.get("song_words") or "no title: live music or a stream",
                "sources": ev.get("based_on"), "mode": ev.get("mode_label"), "feel": ev.get("feel_label"),
                "feel_persona": _short(ev.get("persona"), 400),
                "heard": {"genre": d.get("genre"), "moods": d.get("moods"), "energy": d.get("energy"),
                          "valence": d.get("valence"), "acoustic": d.get("organic"), "bpm": ev.get("bpm"),
                          "ai_character": ev.get("character"), "piano_playing": ev.get("piano_playing")},
                "appliances_on": ev.get("appliances"), "last_songs": hist,
                "people_before": self.taste.lines(ctx), "options": opts}
        return json.dumps(body, separators=(",", ":"), default=str)

    async def _ai(self, now_s: float, song: str, ev: dict) -> None:
        try:
            m = self._llm_mod()
            if m is None:
                return
            ctx = self._ctx(ev)
            opts = self.options(ev, ctx)
            tmo = float(self.cfg["ai"]["timeout_s"])
            res = await asyncio.wait_for(m.acall_json(TASK, system=SYSTEM, user=self._prompt(ev, ctx, opts),
                                                      tool=TOOL, timeout_s=tmo, lenient=True), timeout=tmo + 2.0)
            self.ai_state["calls"] += 1
            self.ai_state["cost_usd"] = round(self.ai_state["cost_usd"] + float(res.cost_usd or 0.0), 6)
            if not res.ok or not isinstance(res.data, dict):
                self.ai_state["error"] = res.error or "no answer"
                self._log("treatment_ai", {"ok": False, "error": res.error, "song": song})
                return
            p = self.plan
            if p is None or p["song"] != song:
                self._log("treatment_ai", {"ok": True, "late": True, "song": song})
                return                                 # the song moved on while it thought
            plan = self._from_ai(self._mono(), song, p, ev, ctx, opts, res.data, getattr(res, "model", None))
            self.plan = plan
            self.rev += 1
            self.ai_state.update(error=None, last={"model": getattr(res, "model", None),
                                                    "latency_ms": round(float(res.latency_ms or 0.0))})
            self._log("treatment", {"plan": self._log_view(plan), "model": getattr(res, "model", None),
                                    "latency_ms": round(float(res.latency_ms or 0.0)), "cost_usd": res.cost_usd})
        except asyncio.CancelledError:
            raise
        except Exception as e:                        # noqa: BLE001 - the director's plan stays
            self.ai_state["error"] = f"{type(e).__name__}: {e}"
            log.debug("treatment: AI call failed", exc_info=True)
        finally:
            self._ai_busy = False

    def _from_ai(self, now_s: float, song: str, old: dict, ev: dict, ctx: str, opts: dict, data: dict,
                 model: Optional[str]) -> dict:
        """The AI's answer, every name checked against the options it was given (anything else is dropped; an
        empty part keeps the director's own)."""
        fam_ok = set(opts["families"])
        fams = [f for f in data.get("families") or [] if isinstance(f, str) and f in fam_ok][:3] or old["families"]
        avoid = [f for f in data.get("avoid_families") or [] if isinstance(f, str) and f in fam_ok
                 and f not in fams][:3]
        pal = data.get("palette") if data.get("palette") in opts["palettes"] and not ev.get("palette_pick") else None
        lights = {}
        for row in data.get("lights") or []:
            if isinstance(row, dict) and row.get("scene") in (opts["lights"].get(row.get("group")) or ()):
                lights[row["group"]] = row["scene"]
        form = data.get("form") if data.get("form") in opts["forms"] else None
        moods = [w for w in data.get("moods") or [] if isinstance(w, str) and w in opts["moods"]][:3] or None
        style = data.get("transition_style") if data.get("transition_style") in opts["transition_styles"] else None
        inten = _num(data.get("intensity"))
        why = _short(data.get("why"), 160) or old["why"]
        return self._plan(now_s, song, BY_AI, old["change_why"], ev, ctx, families=fams, avoid=avoid,
                          palette=pal, why_line=why, lights=lights, form=form, moods=moods, style=style,
                          intensity=None if inten is None else _c01(inten), model=model,
                          fams=self._family_scores(ev, ctx))

    # -- people ---------------------------------------------------------------------------------------------------------
    def feedback(self, kind: str, *, palette: Optional[str] = None, steps: int = 0, ev: Optional[dict] = None
                 ) -> Optional[str]:
        """Next look, Keep this look, a palette pick or a Calmer / Wilder step while a plan plays."""
        p = self.plan
        ctx = p["ctx"] if p is not None else self._ctx(ev or {})
        words = self.taste.record(kind, ctx, p, palette=palette, steps=steps)
        if words:
            self._log("taste", {"kind": kind, "ctx": ctx, "words": words})
        return words

    # -- views ------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _log_view(p: Optional[dict]) -> Optional[dict]:
        if p is None:
            return None
        return {k: p.get(k) for k in ("id", "song", "by", "model", "families", "avoid_families", "palette", "lights",
                                      "form", "moods", "transition_style", "intensity", "bias", "why", "song_words",
                                      "based_on", "ctx", "change_why")}

    def view(self, now_s: float) -> dict:
        p = self.plan
        out = {"on": bool(self.cfg["enabled"]), "plan": self._log_view(p), "rev": self.rev,
               "age_s": int(max(0.0, now_s - p["at_s"])) if p else None, "ai": dict(self.ai_state),
               "history": [self._log_view(h) for h in list(self.history)[:4]],
               "families": len(self.index.families), "rated": len(self.index.stars),
               "index_error": self.index.error, "taste_error": self.taste.error}
        return out
