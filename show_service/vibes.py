"""vibes: config/room/vibes.yaml, hot-reloaded and checked (docs/SHOW_ENGINE.md §8.1; README "Stage 3 > vibes.yaml").

A vibe is a flat set of fields (``contract.VIBE_FIELDS``). Nothing inherits, and no code here or in the director knows
any vibe by name: every behaviour is a field, and a field a vibe leaves out takes its **neutral** value (``NEUTRAL``,
README S3.5's table), never a hidden default.

**Load rules** (``Vibes.poll``, every RELOAD_S on show-brain; mtime and size of vibes.yaml and vocab.yaml):
  * a YAML error, or a file that isn't a mapping, keeps the last good file and sets ``error``;
  * a bad value drops that field to neutral and adds a line to ``problems``
    (``vibes.party.level: [0.9, 0.2] is not a range``);
  * an unknown field is ignored, with a problem line;
  * an unknown palette name or vocab word is dropped from its list, with a problem line. Palette names are checked
    only once the catalog has loaded (``palette_names()`` non-empty); vocab words only while vocab.yaml reads.

**The mode's show target** (``contract.VibeTarget``): ``{"vibe", "listening", "overrides"?}``; ``check_target`` makes
it canonical (``listening`` always present, ``overrides`` only when non-empty, null overrides left out: null means
unset) and keeps it **as asked** (degrade, never refuse): an unknown vibe plays neutral (``known`` False) and a bad
override is dropped at run time by ``effective``; ``target_problems`` lists both for the read-back. Only a body that
isn't a target at all is refused. ``{"vibe": null}`` is the neutral target (None).

**The composer's limits** (README S5.18): a vibe may carry one block that is not a field, ``compose`` (follow,
floor, q, base per appliance class and dimension; ``compose.parse_limits``). It is checked at load like a field (a bad
cell is dropped with a problem line, the rest stays: degrade, never refuse), never overridden by a mode, and never in
the catalog's ``fields`` (the mode editor has no config). ``compose_limits(name)``: the clean block, or None when the
vibe has none (the director then uses show.yaml director.compose.defaults).

**Neutral as a real choice.** A word field's neutral is offered as the word ``auto`` (WORD_AUTO: "the music decides"),
and ``loudness_ref_lufs``'s as the gate's own default (contract.GATE_REF_DEFAULT_LUFS, preset "The usual"), so a mode's
fine-tune can set either back to neutral without a null.
"""

from __future__ import annotations

import copy
import hashlib
import logging
import math
import pathlib
import re
from typing import Any, Callable, Collection, Optional

from . import compose as comp
from . import contract

log = logging.getLogger("show_service.vibes")

NAME_RE = r"^[a-z0-9][a-z0-9_-]{0,31}$"
_NAME = re.compile(NAME_RE)
NOT_OVERRIDABLE: tuple[str, ...] = ("label", "description", "persona")
WORD_AUTO = "auto"           # a word field's neutral as a choice: "the music decides" (= null, unset in the vibe)
VOCAB_FACETS: tuple[str, ...] = ("moods", "genre", "transition_style", "palette_family")
LABEL_MAX = 40
TEXT_MAX = 2000
COMPOSE_BLOCK = "compose"    # the composer's limits: a block beside the fields, not a field (module docstring)

# Each field: kind and bounds (the checker), and its JSON schema (the editor; room's catalog subset + x-ui).
#   range   [lo, hi] with lo <= hi inside (min, max)      num     a number inside (min, max)
#   lufs    a number inside (min, max), or null           int     an integer inside (min, max)
#   bool    true | false                                  enum    one of choices
#   list    items from choices (unknown dropped)          palettes  catalog names and/or "art"
#   words   vocab words of a facet (unknown dropped)      word    one vocab word of a facet, or null
#   label   a short string                                text    a paragraph
_SPECS: dict[str, dict] = {
    "label": {"kind": "label"},
    "description": {"kind": "text"},
    "energy": {"kind": "range", "min": 0.0, "max": 1.0},
    "energy_centre": {"kind": "num", "min": 0.0, "max": 1.0},
    "loudness_ref_lufs": {"kind": "lufs", "min": -60.0, "max": -5.0},
    "follow": {"kind": "list", "choices": contract.FOLLOW_INPUTS},
    "follow_amount": {"kind": "num", "min": 0.0, "max": 1.0},
    "beat_lock": {"kind": "bool"},
    "change_looks": {"kind": "enum", "choices": contract.CHANGE_LOOKS},
    "change_rate": {"kind": "num", "min": 0.0, "max": 1.0},
    "dwell_s": {"kind": "range", "min": 1.0, "max": 86400.0},
    "relax_on_talk": {"kind": "bool"},
    "palettes": {"kind": "palettes"},
    "key_cct": {"kind": "num", "min": 1500.0, "max": 6500.0},
    "lamp_chroma_max": {"kind": "num", "min": 0.0, "max": 0.37},
    "chroma_max": {"kind": "num", "min": 0.0, "max": 0.37},
    "saturation": {"kind": "range", "min": 0.0, "max": 1.0},
    "contrast": {"kind": "range", "min": 0.0, "max": 1.0},
    "level": {"kind": "range", "min": 0.0, "max": 1.0},
    "floor": {"kind": "num", "min": 0.0, "max": 1.0},
    "motion": {"kind": "range", "min": 0.0, "max": 1.0},
    "accents": {"kind": "enum", "choices": contract.ACCENT_STYLES},
    "strobe": {"kind": "bool"},
    "peak_tokens_per_hour": {"kind": "int", "min": 0, "max": 1000},
    "moods": {"kind": "words", "facet": "moods"},
    "genre": {"kind": "word", "facet": "genre"},
    "transition_style": {"kind": "word", "facet": "transition_style"},
    "palette_family": {"kind": "word", "facet": "palette_family"},
    "step_size": {"kind": "num", "min": 0.0, "max": 0.5},
    "max_steps": {"kind": "int", "min": 0, "max": 6},
    "persona": {"kind": "text"},
}
assert tuple(_SPECS) == contract.VIBE_FIELDS
#: fields a vibe no longer has, ignored without a problem line where old files or stored overrides still carry them
#: (``listener``: 09's audit ai-F6, 21 Oct: the AI listening switch alone decides)
RETIRED_FIELDS = frozenset({"listener"})

#: S3.5's neutral column. ``label`` is the vibe's name and ``energy_centre`` the middle of ``energy``: both are filled
#: per vibe by ``Vibes.get`` / ``effective``; the values here are what an unnamed, unbounded vibe would have.
NEUTRAL: contract.VibeSettings = {
    "label": "", "description": "", "energy": [0.0, 1.0], "energy_centre": 0.5, "loudness_ref_lufs": None,
    "follow": ["energy"], "follow_amount": 0.5, "beat_lock": True, "change_looks": "phrase", "change_rate": 0.5,
    "dwell_s": [45.0, 180.0], "relax_on_talk": True, "palettes": [], "key_cct": 2700.0,
    "lamp_chroma_max": 0.05, "chroma_max": 0.37, "saturation": [0.5, 1.0], "contrast": [0.3, 0.7],
    "level": [0.5, 1.0], "floor": 0.1, "motion": [0.1, 0.5], "accents": "none", "strobe": False,
    "peak_tokens_per_hour": 0, "moods": [], "genre": None, "transition_style": None, "palette_family": None,
    "step_size": 0.1, "max_steps": 3, "persona": ""}
assert tuple(NEUTRAL) == contract.VIBE_FIELDS


def _ui(label: str, widget: Optional[str] = None, unit: Optional[str] = None, **more: Any) -> dict:
    ui: dict = {"label": label}
    if widget:
        ui["widget"] = widget
    if unit:
        ui["unit"] = unit
    ui.update(more)
    return ui


def _num_schema(f: str, label: str, unit: Optional[str] = None, step: Optional[float] = None) -> dict:
    s = _SPECS[f]
    ui = _ui(label, "slider", unit)
    if step:
        ui["step"] = step
    return {"type": "number", "minimum": s["min"], "maximum": s["max"], "default": NEUTRAL[f], "x-ui": ui}


def _range_schema(f: str, label: str, unit: Optional[str] = None) -> dict:
    s = _SPECS[f]
    return {"type": "array", "items": {"type": "number", "minimum": s["min"], "maximum": s["max"]},
            "default": list(NEUTRAL[f]), "x-ui": _ui(label, "range", unit, ends={"min": "low", "max": "high"})}


def _bool_schema(f: str, label: str) -> dict:
    return {"type": "boolean", "default": NEUTRAL[f], "x-ui": _ui(label, "checkbox")}


#: VIBE_FIELDS -> JSON schema (room's catalog subset: type, minimum, maximum, enum, items, default; x-ui). Enums that
#: depend on vocab.yaml or the palette catalog are filled in by ``Vibes.catalog`` (a copy).
FIELD_SCHEMAS: dict[str, dict] = {
    "label": {"type": "string", "default": "", "x-ui": _ui("Name on the card", unset_label="the vibe's name")},
    "description": {"type": "string", "default": "", "x-ui": _ui("Description", "textarea")},
    "energy": _range_schema("energy", "Energy range"),
    "energy_centre": {**_num_schema("energy_centre", "Resting energy"),
                      "x-ui": _ui("Resting energy", "slider", unset_label="the middle of the energy range")},
    "loudness_ref_lufs": {"type": "number", "minimum": -60.0, "maximum": -5.0,
                          "x-ui": _ui("Expected loudness", "slider", "LUFS",
                                      unset_label="leave the listener's reference as it is",
                                      presets={"The usual": contract.GATE_REF_DEFAULT_LUFS})},
    "follow": {"type": "array", "items": {"type": "string", "enum": list(contract.FOLLOW_INPUTS)},
               "default": list(NEUTRAL["follow"]),
               "x-ui": _ui("Follows", "select", labels={"dynamics": "How hard it's played", "register": "High or low",
                                                         "harmony": "Harmony", "energy": "Energy",
                                                         "onsets": "Hits and rhythm"})},
    "follow_amount": _num_schema("follow_amount", "How closely it follows"),
    "beat_lock": _bool_schema("beat_lock", "Lock to the beat"),
    "change_looks": {"type": "string", "enum": list(contract.CHANGE_LOOKS), "default": NEUTRAL["change_looks"],
                     "x-ui": _ui("Change looks", "select", labels={"phrase": "On phrases",
                                                                   "between_pieces": "Between pieces",
                                                                   "time": "Every so often"})},
    "change_rate": _num_schema("change_rate", "How often looks change"),
    "dwell_s": _range_schema("dwell_s", "Time between looks", "s"),
    "relax_on_talk": _bool_schema("relax_on_talk", "Relax when people talk"),
    "palettes": {"type": "array", "items": {"type": "string"}, "default": [],
                 "x-ui": _ui("Palettes, best first", "select", unset_label="warm white only")},
    "key_cct": _num_schema("key_cct", "Key lamps' white", "K", 50),
    "lamp_chroma_max": _num_schema("lamp_chroma_max", "Key lamps' colour (max)", None, 0.01),
    "chroma_max": _num_schema("chroma_max", "Other lamps' colour (max)", None, 0.01),
    "saturation": _range_schema("saturation", "Saturation"),
    "contrast": _range_schema("contrast", "Contrast"),
    "level": _range_schema("level", "Lamp level (never the screens)"),
    "floor": _num_schema("floor", "Lamp floor (never the screens)"),
    "motion": _range_schema("motion", "Movement"),
    # accents: what each style does today is the depth of the lamps' beat swell (show.yaml director.accent_depth);
    # the labels say so. strobe and peak tokens are carried and published but nothing acts on them yet, so the form
    # keeps them behind "More" and says "(not built yet)".
    "accents": {"type": "string", "enum": list(contract.ACCENT_STYLES), "default": NEUTRAL["accents"],
                "x-ui": _ui("Lamps' beat swell", "select",
                            labels={"none": "None (steady)", "soft": "Gentle swell", "downbeat": "Medium swell",
                                    "beat": "Strong swell", "full": "Full swell"},
                            help="How deep the lamps breathe on the beat (show.yaml director.accent_depth); only "
                                 "while the beat is locked and the vibe locks to it")},
    "strobe": {**_bool_schema("strobe", "Strobe allowed (not built yet)"),
               "x-ui": _ui("Strobe allowed (not built yet)", "checkbox", advanced=True, not_built=True)},
    "peak_tokens_per_hour": {"type": "integer", "minimum": 0, "maximum": 1000, "default": 0,
                             "x-ui": _ui("Big moments per hour", "number", advanced=True,
                                         help="Drops after a build that may cut the look and hit the lamps, at most "
                                              "this many in any hour (0: none; a drop then shows inside the look)")},
    "moods": {"type": "array", "items": {"type": "string"}, "default": [], "x-ui": _ui("Moods", "select")},
    "genre": {"type": "string", "x-ui": _ui("Genre", "select", unset_label="the music decides",
                                            labels={WORD_AUTO: "The music decides"})},
    "transition_style": {"type": "string", "x-ui": _ui("Transitions", "select", unset_label="the music decides",
                                                       labels={WORD_AUTO: "The music decides"})},
    "palette_family": {"type": "string", "x-ui": _ui("Palette family", "select", unset_label="any",
                                                     labels={WORD_AUTO: "Any"})},
    "step_size": _num_schema("step_size", "One Calmer/Wilder step", None, 0.01),
    "max_steps": {"type": "integer", "minimum": 0, "maximum": 6, "default": 3,
                  "x-ui": _ui("Steps each way", "number")},
    "persona": {"type": "string", "default": "", "x-ui": _ui("Persona (Describe it only)", "textarea")},
}
assert tuple(FIELD_SCHEMAS) == contract.VIBE_FIELDS


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _fmt(v: Any) -> str:
    return repr(v) if isinstance(v, str) else str(v).replace("'", "")


def check_field(field: str, value: Any, *, vocab: Optional[dict[str, list[str]]],
                palette_names: Collection[str]) -> tuple[Any, list[str], bool]:
    """One field's value -> (clean value, problems, usable). ``usable`` False: the field falls to neutral. List fields
    drop their unknown items (a problem each) and stay usable."""
    s = _SPECS.get(field)
    if s is None:
        return None, [f"unknown field {field!r}"], False
    k = s["kind"]
    if k in ("label", "text"):
        if not isinstance(value, str):
            return None, [f"{_fmt(value)} is not text"], False
        lim = LABEL_MAX if k == "label" else TEXT_MAX
        v = " ".join(value.split()) if k == "label" else value.strip()
        return (v[:lim], [f"longer than {lim} characters: cut"] if len(v) > lim else [], True)
    if k == "range":
        if (not isinstance(value, (list, tuple)) or len(value) != 2 or not all(_is_num(x) for x in value)
                or value[0] > value[1]):
            return None, [f"{_fmt(value)} is not a range [low, high]"], False
        lo, hi = float(value[0]), float(value[1])
        if lo < s["min"] or hi > s["max"]:
            return None, [f"{_fmt(value)} is outside {s['min']:g}..{s['max']:g}"], False
        return [lo, hi], [], True
    if k in ("num", "lufs"):
        if value is None and k == "lufs":
            return None, [], True
        if not _is_num(value):
            return None, [f"{_fmt(value)} is not a number"], False
        if not s["min"] <= value <= s["max"]:
            return None, [f"{_fmt(value)} is outside {s['min']:g}..{s['max']:g}"], False
        return float(value), [], True
    if k == "int":
        if not _is_num(value) or float(value) != int(value):
            return None, [f"{_fmt(value)} is not a whole number"], False
        if not s["min"] <= int(value) <= s["max"]:
            return None, [f"{_fmt(value)} is outside {s['min']}..{s['max']}"], False
        return int(value), [], True
    if k == "bool":
        if not isinstance(value, bool):
            return None, [f"{_fmt(value)} is not true or false"], False
        return value, [], True
    if k == "enum":
        if value not in s["choices"]:
            return None, [f"{_fmt(value)} is not one of {', '.join(s['choices'])}"], False
        return value, [], True
    if k in ("list", "palettes", "words"):
        if not isinstance(value, (list, tuple)):
            return None, [f"{_fmt(value)} is not a list"], False
        if k == "list":
            known: Optional[Collection[str]] = s["choices"]
        elif k == "palettes":
            known = (set(palette_names) | {contract.PALETTE_ART}) if palette_names else None
        else:
            known = vocab.get(s["facet"]) if vocab is not None else None
        out, probs = [], []
        for x in value:
            if not isinstance(x, str):
                probs.append(f"{_fmt(x)} is not a word: dropped")
            elif known is not None and x not in known:
                probs.append(f"{x!r} is not known: dropped")
            elif x not in out:
                out.append(x)
        return out, probs, True
    if k == "word":
        if value is None or value == WORD_AUTO:
            return None, [], True
        if not isinstance(value, str):
            return None, [f"{_fmt(value)} is not a word"], False
        known = vocab.get(s["facet"]) if vocab is not None else None
        if known is not None and value not in known:
            return None, [f"{value!r} is not a {s['facet']} word in vocab.yaml"], False
        return value, [], True
    return None, [f"unchecked kind {k}"], False                    # pragma: no cover


def _complete(name: str, fields: dict) -> contract.VibeSettings:
    """NEUTRAL + fields, with the per-vibe neutrals (label, energy_centre) and energy_centre kept inside energy."""
    out: dict = copy.deepcopy(NEUTRAL)
    out["label"] = name
    out.update(copy.deepcopy(fields))
    lo, hi = out["energy"]
    if "energy_centre" not in fields:
        out["energy_centre"] = round((lo + hi) / 2.0, 6)
    else:
        out["energy_centre"] = min(hi, max(lo, out["energy_centre"]))
    return out                                                    # type: ignore[return-value]


def _sha(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()[:12]


class Vibes:
    """vibes.yaml, hot-reloaded (module docstring). ``poll`` runs on show-brain; the read methods read the last good
    load, swapped by reference, so they are safe anywhere."""

    def __init__(self, path: pathlib.Path = contract.VIBES_FILE, *, vocab_path: pathlib.Path = contract.VOCAB_FILE,
                 palette_names: Callable[[], Collection[str]] = lambda: ()) -> None:
        self.path = pathlib.Path(path)
        self.vocab_path = pathlib.Path(vocab_path)
        self.palette_names = palette_names
        self.revision: str = ""
        self.generation = 0                              # +1 per successful load (a palette re-check keeps the sha)
        self.error: Optional[str] = None
        self.problems: list[str] = []
        self.order: list[str] = []
        self.vocab: Optional[dict[str, list[str]]] = None
        self.vocab_error: Optional[str] = None
        self._sig: Optional[tuple] = None
        self._vsig: Optional[tuple] = None
        self._pal_seen: tuple[str, ...] = ()
        self._raw: dict[str, dict] = {}                  # name -> the fields as written (valid ones)
        self._vibes: dict[str, contract.VibeSettings] = {}
        self._compose: dict[str, dict] = {}              # name -> its compose block (clean), when it has one

    # -- loading (show-brain) ---------------------------------------------------------------------------------------
    @staticmethod
    def _stat(p: pathlib.Path) -> Optional[tuple]:
        try:
            st = p.stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def _load_vocab(self) -> None:
        try:
            import yaml
            with open(self.vocab_path, encoding="utf-8") as f:
                raw = yaml.safe_load(f)
            if not isinstance(raw, dict):
                raise ValueError("not a mapping")
            vocab = {}
            for facet in VOCAB_FACETS:
                words = raw.get(facet)
                vocab[facet] = [w for w in words if isinstance(w, str)] if isinstance(words, list) else []
            self.vocab, self.vocab_error = vocab, None
        except Exception as e:                            # words can't be checked: they are kept, and it is said
            self.vocab, self.vocab_error = None, f"{self.vocab_path.name}: {type(e).__name__}: {e}"

    def poll(self, force: bool = False) -> bool:
        """Re-read vibes.yaml (and vocab.yaml) when either changed, or when the palette catalog first appears or
        changes (palette names are checked against it). True when the vibes in use were reloaded."""
        vsig = self._stat(self.vocab_path)
        if force or vsig != self._vsig:
            self._vsig = vsig
            self._load_vocab()
            force = True
        try:
            pal = tuple(sorted(self.palette_names() or ()))
        except Exception:
            pal = ()
        if pal != self._pal_seen:
            self._pal_seen = pal
            force = True
        sig = self._stat(self.path)
        if not force and sig == self._sig:
            return False
        self._sig = sig
        try:
            data = self.path.read_bytes()
        except OSError as e:
            self.error = f"{self.path.name}: {e.strerror or e} (the last good vibes stay in use)"
            return False
        try:
            import yaml
            raw = yaml.safe_load(data)
            if not isinstance(raw, dict):
                raise ValueError("the file is not a mapping")
            vibes = raw.get("vibes")
            if not isinstance(vibes, dict):
                raise ValueError("'vibes' is not a mapping of name -> fields")
        except Exception as e:
            self.error = f"{self.path.name}: {type(e).__name__}: {e} (the last good vibes stay in use)"
            log.warning("vibes: %s", self.error)
            return False
        problems: list[str] = []
        if self.vocab_error:
            problems.append(f"vocab: {self.vocab_error}; words are not checked")
        clean: dict[str, dict] = {}
        limits: dict[str, dict] = {}
        for name, fields in vibes.items():
            if not isinstance(name, str) or not _NAME.match(name):
                problems.append(f"vibes.{name}: not a vibe name ({NAME_RE}): skipped")
                continue
            if fields is None:
                fields = {}
            if not isinstance(fields, dict):
                problems.append(f"vibes.{name}: not a mapping of fields: skipped")
                continue
            ok: dict = {}
            for f, v in fields.items():
                if f == COMPOSE_BLOCK:                       # the composer's limits (README S5.18)
                    limits[name] = comp.parse_limits(v, f"vibes.{name}.{f}", problems)
                    continue
                if f in RETIRED_FIELDS:
                    continue
                if f not in _SPECS:
                    problems.append(f"vibes.{name}.{f}: unknown field: ignored")
                    continue
                val, probs, usable = check_field(f, v, vocab=self.vocab, palette_names=pal)
                problems += [f"vibes.{name}.{f}: {p}" for p in probs]
                if usable:
                    ok[f] = val
            if "energy_centre" in ok and "energy_centre" in fields:
                lo, hi = ok.get("energy", NEUTRAL["energy"])
                if not lo <= ok["energy_centre"] <= hi:
                    problems.append(f"vibes.{name}.energy_centre: {ok['energy_centre']:g} is outside the energy "
                                    f"range: kept at its nearer end")
            clean[name] = ok
        order_raw = raw.get("order")
        order: list[str] = []
        if order_raw is not None and not isinstance(order_raw, list):
            problems.append("order: not a list: ignored")
            order_raw = []
        for n in order_raw or []:
            if n in clean and n not in order:
                order.append(n)
            elif n not in clean:
                problems.append(f"order: {n!r} is not a vibe: ignored")
        order += sorted(n for n in clean if n not in order)
        version = raw.get("version")
        if version not in (None, 1):
            problems.append(f"version: {version!r} (this reader knows 1)")
        self._raw = clean
        self._vibes = {n: _complete(n, f) for n, f in clean.items()}
        self._compose = {n: lim for n, lim in limits.items() if n in clean}
        self.order = order
        self.problems = problems
        self.revision = _sha(data)
        self.generation += 1
        self.error = None
        log.info("vibes: loaded %d (%s), %d problem(s)", len(order), self.revision, len(problems))
        return True

    # -- reads --------------------------------------------------------------------------------------------------------
    def names(self) -> list[str]:
        return list(self.order)

    def get(self, name: str) -> Optional[contract.VibeSettings]:
        """NEUTRAL + the vibe's valid fields (a copy), or None for an unknown name."""
        v = self._vibes.get(name)
        return copy.deepcopy(v) if v is not None else None

    def compose_limits(self, name: Optional[str]) -> Optional[dict]:
        """The vibe's compose block (clean, ``compose.parse_limits``; a copy), or None: no such vibe, or no block."""
        lim = self._compose.get(name) if isinstance(name, str) else None
        return copy.deepcopy(lim) if lim is not None else None

    def effective(self, target: Optional[contract.VibeTarget]) -> tuple[contract.VibeSettings, bool]:
        """(settings, known): the target's vibe with its overrides. No target, or a vibe that isn't in the file:
        the neutral settings and False. A bad override is dropped here (check_target refused it on the way in)."""
        if not target or not isinstance(target, dict):
            return copy.deepcopy(NEUTRAL), False
        name = target.get("vibe")
        base = self._vibes.get(name) if isinstance(name, str) else None
        if base is None:
            out = copy.deepcopy(NEUTRAL)
            out["label"] = name if isinstance(name, str) else ""
            return out, False
        ov = target.get("overrides") or {}
        if not ov:
            return copy.deepcopy(base), True
        fields = dict(self._raw.get(name, {}))
        for f, v in ov.items():
            if f in NOT_OVERRIDABLE:
                continue
            val, _probs, usable = check_field(f, v, vocab=self.vocab, palette_names=self._pal_seen)
            if usable:
                fields[f] = val
        return _complete(name, fields), True

    def check_target(self, body: Any) -> Optional[contract.VibeTarget]:
        """A PUT /api/v1/vibe body -> the canonical VibeTarget **as asked**, or None (no feel: the director goes
        neutral). Accepts ``{"vibe", "listening"?, "overrides"?}``, §11.2's ``{"name"}``, a bare name, and
        ``{"vibe": null}`` / ``null`` (neutral). Degrade, never refuse: a name vibes.yaml doesn't have, or an override
        it can't use, is kept as asked (so the room's read-back matches) and played as well as it can be (an unknown
        vibe: neutral; a bad override: dropped); ``target_problems`` says what. A null override means unset (the
        vibe's own value), as the room's form means it, and is left out. ValueError ``bad_target: <why>`` only for a
        body that isn't a target at all."""
        if body is None:
            return None
        if isinstance(body, str):
            body = {"vibe": body}
        if not isinstance(body, dict):
            raise ValueError("bad_target: expected {\"vibe\": name, \"listening\": bool, \"overrides\": {...}}")
        extra = set(body) - {"vibe", "name", "listening", "overrides"}
        if extra:
            raise ValueError(f"bad_target: unknown key(s) {', '.join(sorted(map(str, extra)))}")
        if "vibe" in body and "name" in body and body["vibe"] != body["name"]:
            raise ValueError("bad_target: 'vibe' and 'name' differ")
        name = body.get("vibe", body.get("name"))
        if name is None:
            return None
        if not isinstance(name, str) or not _NAME.match(name):
            raise ValueError(f"bad_target: 'vibe' must be a vibe name ({NAME_RE})")
        listening = body.get("listening", False)
        if listening is None:
            listening = False
        if not isinstance(listening, bool):
            raise ValueError("bad_target: 'listening' must be true or false")
        ov = body.get("overrides")
        if ov is None:
            ov = {}
        if not isinstance(ov, dict) or not all(isinstance(k, str) for k in ov):
            raise ValueError("bad_target: 'overrides' must be a mapping of field -> value")
        clean = {f: copy.deepcopy(v) for f, v in ov.items() if v is not None}
        out: contract.VibeTarget = {"vibe": name, "listening": listening}
        if clean:
            out["overrides"] = clean
        return out

    def target_problems(self, target: Optional[contract.VibeTarget]) -> list[str]:
        """Page words for what of ``target`` can't be played as asked: a vibe vibes.yaml doesn't have (the neutral
        look plays), and each override that is dropped or trimmed (as at load, against the vocab and catalog now)."""
        if not target or not isinstance(target, dict):
            return []
        probs: list[str] = []
        name = target.get("vibe")
        if name not in self._vibes:
            probs.append(f"the feel {name!r} isn't in vibes.yaml: the neutral look plays")
        for f, v in (target.get("overrides") or {}).items():
            if f in NOT_OVERRIDABLE:
                probs.append(f"overrides.{f}: belongs to the vibe and can't be overridden: ignored")
                continue
            if f in RETIRED_FIELDS:
                continue
            if f not in _SPECS:
                probs.append(f"overrides.{f}: unknown field: ignored")
                continue
            _val, p, usable = check_field(f, v, vocab=self.vocab, palette_names=self._pal_seen)
            if not usable:
                probs.append(f"overrides.{f}: {'; '.join(p) or 'bad value'}: the vibe's own value plays")
            else:
                probs += [f"overrides.{f}: {x}" for x in p]
        return probs

    def catalog(self, palettes: list[contract.PaletteChoice]) -> contract.VibesCatalog:
        fields = copy.deepcopy(FIELD_SCHEMAS)
        pal_names = [p["name"] for p in palettes]
        fields["palettes"]["items"]["enum"] = pal_names
        vocab = self.vocab or {f: [] for f in VOCAB_FACETS}
        if self.vocab is not None:
            fields["moods"]["items"]["enum"] = list(vocab["moods"])
            for f in ("genre", "transition_style", "palette_family"):
                fields[f]["enum"] = list(vocab[f]) + [WORD_AUTO]      # the neutral as a real choice
        try:
            rel = str(self.path.resolve().relative_to(contract.REPO_ROOT))
        except ValueError:
            rel = str(self.path)
        neutral = copy.deepcopy(NEUTRAL)
        return {"revision": self.revision, "path": rel, "error": self.error, "order": list(self.order),
                "vibes": {n: {"label": self._vibes[n]["label"], "description": self._vibes[n]["description"],
                              "fields": copy.deepcopy(self._raw.get(n, {}))} for n in self.order},
                "fields": fields, "neutral": neutral, "palettes": list(palettes),
                "vocab": {f: list(vocab.get(f, [])) for f in VOCAB_FACETS}}
