"""Generate plug-in files from upstream GLSL collections (one file + one presets file per shader).

  ~/.venvs/sm-stream/bin/python -m stream_service.plugins._lib.glsl.importer \
      --glt <gl-transitions checkout> --isf <ISF-Files checkout> --isf-coll <dir with collection checkouts> \
      --st <shadertoy candidates .tsv> --st-dump <dir of Shadertoy API-dump .jsonl> [--only glt,isf,coll,st] [--dry]

Writes stream_service/plugins/{transitions,sources,effects}/{glt,isf,st}_<slug>.py (+ .presets.yaml).  The GLSL
is embedded verbatim; licences come from the file headers / repo LICENSE (texts in LICENSES/).  Licence rules:
CONVENTIONS section 5 (permissive and attribution OK, noncommercial flagged, GPL / no-licence skipped).
Re-running overwrites generated files (they start with GENERATED_MARK); hand-written plug-ins are never touched.
"""
from __future__ import annotations

import argparse
import glob
import json
import pathlib
import re
import sys

from . import translate as T

PLUGINS = pathlib.Path(__file__).resolve().parents[2]
GENERATED_MARK = "Imported verbatim from GLSL"
GLT_REF = "b2dfdcfbec079af4a05306d32b9366aa495c5155"
ISF_REF = "395072d48b3ce7351ccb20a5fda54470591324df"

# gl-transitions already in the deployment as hand ports (core + lab batch 1): skip, never duplicate
GLT_SKIP = {"fade", "burn", "luma", "DoomScreenTransition", "pixelize", "crosswarp", "directionalwarp",
            "ButterflyWaveScrawler", "WaterDrop", "kaleidoscope", "cube", "doorway", "InvertedPageCurl", "GridFlip",
            "Mosaic", "FilmBurn", "ColourDistance", "DefocusBlur", "TVStatic", "Revolve_Left",
            "tangentMotionBlur"}   # tangentMotionBlur: endpoints impure upstream, blog-repost provenance
RESERVED = {"energy", "pulse", "speed", "intensity", "render_scale", "seed", "palette", "palette_period",
            "drift_speed", "palette_mix", "amount", "duration_s", "easing", "name", "label", "params"}
PERMISSIVE_SPDX = [(r"\bCC0\b|publicdomain/zero", "CC0-1.0"), (r"(?i)unlicense", "Unlicense"),
                   (r"(?i)\bMIT\b|Permission is hereby granted", "MIT"), (r"(?i)apache", "Apache-2.0"),
                   (r"(?i)BSD.?3|3.?clause", "BSD-3-Clause"), (r"(?i)BSD.?2|2.?clause", "BSD-2-Clause"),
                   (r"(?i)\bBSD\b", "BSD-3-Clause"), (r"(?i)WTFPL", "WTFPL"), (r"(?i)zlib", "Zlib"),
                   (r"(?i)public[- ]domain", "public-domain")]
NC_RE = re.compile(r"(?i)non-?commercial|\bCC[- ]?BY[- ]?NC|by-nc")
FORBID_RE = re.compile(r"(?i)\b(A?GPL|LGPL)\b|General Public License|glslsandbox|all rights reserved|"
                       r"educational purposes|bookofshaders|book of shaders|pixelspirit")


# ------------------------------------------------------------------------------------------- helpers

def slug(s: str, n: int = 40) -> str:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_").lower()
    s = re.sub(r"_+", "_", s)
    return (s[:n].rstrip("_")) or "x"


def camel(name: str) -> str:
    return "".join(p[:1].upper() + p[1:] for p in name.split("_") if p)


def title(s: str) -> str:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s)
    s = re.sub(r"[_\-]+", " ", s).strip()
    return (s[:1].upper() + s[1:])[:60] if s else s


def one_line(s: str, n: int = 160) -> str:
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    s = s.replace('"""', "'").replace("\\", "/")
    if len(s) > n:
        s = s[:n].rsplit(" ", 1)[0] + "..."
    return s


def spdx(text: str) -> str | None:
    for rx, sp in PERMISSIVE_SPDX:
        if re.search(rx, text):
            return sp
    return None


def py_str(src: str) -> str:
    if "'''" not in src and not src.endswith("\\"):
        return "r'''" + src + "'''"
    return repr(src)


def num_prop(lo, hi, default, label, *, tier="regional", integer=False, glsl=None, enum=None, labels=None):
    lo, hi = float(lo), float(hi)
    if lo > hi:
        lo, hi = hi, lo
    d = float(default)
    lo, hi = min(lo, d), max(hi, d)
    if hi == lo:
        hi = lo + 1.0
    if integer:
        p = {"type": "integer", "minimum": int(round(lo)), "maximum": int(round(hi)), "default": int(round(d)),
             "x-ui": {"label": label, "step": 1, "widget": "slider"}, "x-tier": tier}
    else:
        span = hi - lo
        step = 10 ** round(__import__("math").log10(span / 100)) if span > 0 else 0.01
        p = {"type": "number", "minimum": lo, "maximum": hi, "default": d,
             "x-ui": {"label": label, "step": min(step, 1.0), "widget": "slider"}, "x-tier": tier}
    if enum is not None:
        p = {"type": "integer", "enum": [int(v) for v in enum], "default": int(round(d)),
             "x-ui": {"label": label, "widget": "select", **({"labels": [str(x) for x in labels]} if labels else {})},
             "x-tier": "structural"}
    if glsl:
        p["x-glsl"] = glsl
    return p


def guess_range(d: float) -> tuple[float, float]:
    if d is None:
        return 0.0, 1.0
    if 0.0 <= d <= 1.0:
        return 0.0, 1.0
    if d > 1.0:
        return 0.0, 2.0 * d
    return 2.0 * d, -2.0 * d


def hex_color(v) -> str:
    v = list(v or [1, 1, 1]) + [1, 1, 1]
    return "#%02x%02x%02x" % tuple(int(round(max(0.0, min(1.0, float(c))) * 255)) for c in v[:3])


def key_for(name: str, used: set) -> str:
    k = slug(name, 30)
    if k in RESERVED or not k[0].isalpha():
        k = "p_" + k
    base, i = k, 2
    while k in used:
        k = f"{base}_{i}"
        i += 1
    used.add(k)
    return k


# ------------------------------------------------------------------------------------------- tags

MOODS_BY_WORD = [
    (r"glitch|static|noise|tv|vhs|datamosh|corrupt|tear", ["glitchy", "tense"]),
    (r"fire|flame|burn|lava|heat", ["fiery", "warm"]), (r"ice|frost|snow|cold", ["icy"]),
    (r"water|wave|ripple|liquid|fluid|ocean|drop", ["liquid", "organic"]),
    (r"star|galax|nebula|space|cosmic|planet|universe", ["cosmic", "dreamy"]),
    (r"fractal|mandel|julia|kifs|kaleido|mandala|symmetr", ["hypnotic", "geometric"]),
    (r"tunnel|wormhole|warp|vortex|spiral|swirl|twirl", ["hypnotic"]),
    (r"neon|laser|light|glow|bloom|ray", ["bright", "euphoric"]),
    (r"retro|crt|scanline|pixel|8.?bit|ascii|halftone|dither", ["retro", "playful"]),
    (r"blur|soft|dream|haze|fog", ["dreamy", "mellow"]),
    (r"cell|voronoi|organic|growth|life|reaction|smooth", ["organic"]),
    (r"grid|hex|tri|square|box|geometr|tile|mosaic", ["geometric"]),
    (r"dark|shadow|night", ["dark"]), (r"heart|candy|bubble", ["playful"]),
]


def moods_for(text: str) -> list[str]:
    out = []
    for rx, ms in MOODS_BY_WORD:
        if re.search(rx, text, re.I):
            out += [m for m in ms if m not in out]
    return out[:3] or ["hypnotic"]


def transition_tags(name: str, tags: str, desc: str) -> dict:
    t = f"{name} {tags} {desc}".lower()
    fam = "blend"
    for rx, f in [(r"glitch|static|tv|noise|datamosh", "glitch"), (r"3d|flip|cube|page|fold|book|door|rotate", "3d"),
                  (r"mosaic|pixel|tile|square|hexagon|puzzle|grid|block|dots|polka", "fragment"),
                  (r"warp|zoom|swirl|ripple|morph|distort|kaleido|wave|squeeze|fly", "warp"),
                  (r"wipe|slide|circle|iris|shape|star|heart|radial|angular|split|window|clock|open|close", "wipe"),
                  (r"burn|flash|overexpos|light|luma", "light"), (r"water|drop|ink|melt|organic|leaf", "organic")]:
        if re.search(rx, t):
            fam = f
            break
    style = "wild" if fam == "glitch" or re.search(r"glitch|crazy|random|flicker|static", t) else \
        "gentle" if re.search(r"fade|dissolve|dreamy|blur|blend|soft", t) else "lively"
    energy = {"gentle": [0.0, 0.6], "lively": [0.3, 0.9], "wild": [0.6, 1.0]}[style]
    return {"energy": energy, "moods": moods_for(t), "style": style, "family": fam, "dims": "3d" if fam == "3d" else "2d",
            "technique": ["warp"] if fam == "warp" else ["glitch"] if fam == "glitch" else ["analytic"], "weight": 0.6}


STAGE_RULES = [
    (r"feedback|trail|echo|long exposure|comet|motion blur|time blur|zooming feedback|slit|freeze|hold|buffer|"
     r"afterimage|smear", "feedback"),
    (r"glitch|mosh|tear|corrupt|lcd|bad tv|vhs|interlace|strobe|jpeg|artifact|sort", "glitch"),
    (r"kaleido|mirror|twirl|bump|warp|ripple|wave|displace|distort|circle|sphere|fisheye|bulge|pinch|tile|shockwave|"
     r"hyperspace|zoom|shift|rotate|geometry|perspective|flip|scale|stretch", "warp"),
    (r"bloom|glow|god ?ray|flare|light|leak|shine|neon", "light"),
    (r"colou?r|hue|saturat|tone|levels|contrast|bright|gamma|invert|duotone|sepia|thermal|night vision|tint|lut|"
     r"palette|false|posteri|channel|rgb|grey|gray|mono|chroma", "colour"),
    (r"shake|vibrat|jitter", "motion"),
    (r"overlay|text|grid|frame|border|vignette|lens|dirt|scratch|dust", "overlay"),
]


def effect_stage(name: str, cats: list[str]) -> str:
    t = f"{name} {' '.join(cats)}".lower()
    if any(c in ("Feedback",) for c in cats):
        return "feedback"
    if "Glitch" in cats:
        return "glitch"
    for rx, st in STAGE_RULES:
        if re.search(rx, t):
            return st
    if any(c in ("Color Effect", "Color Adjustment", "Color") for c in cats):
        return "colour"
    if any(c in ("Distortion Effect", "Geometry Adjustment", "Tile Effect", "Kaleidoscope") for c in cats):
        return "warp"
    return "stylise"


def scene_tags(name: str, text: str) -> dict:
    t = f"{name} {text}".lower()
    fam = "abstract"
    for rx, f in [(r"tunnel|wormhole|corridor", "tunnel"), (r"fractal|mandel|julia|kifs|apollon|menger|sierpinski", "fractal"),
                  (r"galax|nebula|star|space|planet|cosmic|universe", "cosmic"), (r"water|ocean|sea|wave|liquid", "water"),
                  (r"fire|flame|lava|smoke", "fire"), (r"particle|dust|spark", "particles"),
                  (r"cell|life|reaction|organic|growth|physarum|creature", "organic"),
                  (r"terrain|landscape|mountain|desert|cloud", "landscape"), (r"glitch|static", "glitch"),
                  (r"neon|laser|light|glow", "light"), (r"grid|truchet|geometr|hex|tile|box|cube|polygon", "geometric"),
                  (r"retro|8.?bit|crt|synthwave|outrun", "retro")]:
        if re.search(rx, t):
            fam = f
            break
    dims = "3d" if re.search(r"raymarch|ray march|\bsdf\b|distance field|3d|march", t) else "2d"
    tech = [x for x, rx in [("raymarch", r"raymarch|ray march|march"), ("fractal", r"fractal|mandel|julia|kifs"),
                            ("noise", r"noise|fbm"), ("feedback", r"feedback|buffer"), ("kaleido", r"kaleido|mandala"),
                            ("warp", r"warp|swirl|twist")] if re.search(rx, t)][:3] or ["analytic"]
    return {"energy": [0.2, 0.9], "moods": moods_for(t), "family": fam, "dims": dims, "technique": tech, "weight": 0.6}


# ------------------------------------------------------------------------------------------- writers

def write_plugin(e: dict, dry: bool, out_root: pathlib.Path = PLUGINS) -> pathlib.Path:
    kind_dir = {"transition": "transitions", "source": "sources", "effect": "effects"}[e["kind"]]
    (out_root / kind_dir).mkdir(parents=True, exist_ok=True)
    path = out_root / kind_dir / f"{e['name']}.py"
    if path.exists() and GENERATED_MARK not in path.read_text(errors="replace")[:2000]:
        raise SystemExit(f"refusing to overwrite hand-written {path}")
    base = {"transition": "GlslTransition", "source": "GlslSource", "effect": "GlslEffect"}[e["kind"]]
    helper = {"transition": None, "source": "scene_params", "effect": "effect_params"}[e["kind"]]
    doc = [f"{e['label']}: {e['description']}", "",
           f"Source: {e['url']}", f"Author: {e['author']}", f"License: {e['license']}",
           f"license_class: {e['license_class']}",
           f"{GENERATED_MARK} ({e['origin']}); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA)."]
    if e.get("licence_note"):
        doc.append(e["licence_note"])
    lines = ['"""' + "\n".join(doc) + '\n"""',
             f"from stream_service.plugins._lib.glsl import {base}" + (f", {helper}" if helper else ""), "", ""]
    lines.append(f"GLSL = {py_str(e['glsl'])}")
    if e.get("vs"):
        lines.append(f"\nVS = {py_str(e['vs'])}")
    lines += ["", "", f"class {camel(e['name'])}({base}):",
              f"    name = {e['name']!r}", f"    label = {e['label']!r}", f"    description = {e['description']!r}",
              f"    dialect = {e['dialect']!r}", "    GLSL = GLSL"]
    if e.get("vs"):
        lines.append("    VS = VS")
    for attr in ("stage", "scale", "channels", "events", "default_duration_s"):
        if e.get(attr) not in (None, {}, ()):
            lines.append(f"    {attr} = {e[attr]!r}")
    params = json.dumps(e["params"], indent=4, ensure_ascii=False).replace("true", "True").replace(
        "false", "False").replace("null", "None")
    params = "\n".join("    " + ln if i else ln for i, ln in enumerate(params.split("\n")))
    if helper == "scene_params":
        rs = e.get("render_scale", 0.5)
        lines.append(f"    params = scene_params({params}, render_scale={rs})")
    elif helper:
        lines.append(f"    params = effect_params({params})")
    else:
        lines.append(f"    params = {params}")
    src = "\n".join(lines) + "\n"
    if not dry:
        path.write_text(src)
        preset = preset_yaml(e)
        (path.parent / f"{e['name']}.presets.yaml").write_text(preset)
    return path


def preset_yaml(e: dict) -> str:
    import yaml
    key = {"transition": "transition", "source": "source", "effect": "effect"}[e["kind"]]
    p = {"label": e["label"], key: e["name"], "params": {}}
    if e["kind"] == "source":
        p["bindings"] = {"energy": {"bind": "energy"}, "pulse": {"kick": "beat", "amount": 1.0}}
    if e["kind"] == "effect":
        p["params"] = {"amount": 1.0}
        p["bindings"] = {"pulse": {"kick": "beat", "amount": 1.0}}
    p["tags"] = e["tags"]
    return yaml.safe_dump({e["name"].replace("_", "-"): p}, sort_keys=False, allow_unicode=True, width=120)


# ------------------------------------------------------------------------------------------- gl-transitions

def import_glt(root: pathlib.Path, used: set) -> list[dict]:
    out = []
    for f in sorted((root / "transitions").glob("*.glsl")):
        if f.stem in GLT_SKIP:
            continue
        src = f.read_text()
        try:
            tr = T.translate_gl_transition(src)
        except Exception as ex:
            print(f"glt {f.stem}: translate failed: {ex}", file=sys.stderr)
            continue
        meta = tr.meta
        lic = meta.get("license", "MIT")
        sp = spdx(lic) or "MIT"
        params, keys = {}, set()
        for u in tr.params:
            m, d = u.meta or {}, u.default
            label = one_line(m.get("description") or title(u.name), 40)
            if u.type == "float":
                lo, hi = (m["min"], m["max"]) if "min" in m else guess_range(d)
                params[key_for(u.name, keys)] = num_prop(lo, hi, d, label, glsl={"u": u.name})
            elif u.type == "int":
                lo, hi = (m["min"], m["max"]) if "min" in m else (min(0, 4 * d), max(1, 4 * abs(d)))
                params[key_for(u.name, keys)] = num_prop(lo, hi, d, label, tier="structural", integer=True,
                                                         glsl={"u": u.name})
            elif u.type == "bool":
                params[key_for(u.name, keys)] = {"type": "boolean", "default": bool(d), "x-ui": {"label": label},
                                                 "x-tier": "structural", "x-glsl": {"u": u.name}}
            elif u.type in ("vec3", "vec4") and (m.get("color") or re.search(r"(?i)colou?r", u.name)):
                n = int(u.type[-1])
                params[key_for(u.name, keys)] = {"type": "string", "format": "color", "default": hex_color(d),
                                                 "x-ui": {"label": label, "widget": "color"}, "x-tier": "regional",
                                                 "x-glsl": {"u": u.name, "color": n,
                                                            "alpha": float((list(d or []) + [1, 1, 1, 1])[3])}}
            elif re.fullmatch(r"i?vec[234]", u.type):
                n = int(u.type[-1])
                vals = list(d) if isinstance(d, list) else [d or 0] * n
                for i in range(n):
                    v = vals[i] if i < len(vals) else 0
                    k = key_for(f"{u.name}_{'xyzw'[i]}", keys)
                    if u.type.startswith("i"):
                        params[k] = num_prop(min(0, v), max(2, 4 * abs(v)), v, f"{label} {'xyzw'[i]}",
                                             tier="structural", integer=True, glsl={"u": u.name, "c": i})
                    else:
                        lo, hi = guess_range(v)
                        params[k] = num_prop(lo, hi, v, f"{label} {'xyzw'[i]}", glsl={"u": u.name, "c": i})
        name = "glt_" + slug(f.stem)
        while name in used:
            name += "_2"
        used.add(name)
        desc = one_line(meta.get("description") or f"{title(f.stem)} transition from gl-transitions.")
        out.append({"kind": "transition", "name": name, "label": title(f.stem), "description": desc,
                    "dialect": "glt", "glsl": src, "params": params,
                    "url": f"https://github.com/gl-transitions/gl-transitions/blob/{GLT_REF}/transitions/{f.name}",
                    "author": one_line(meta.get("author") or "gl-transitions contributors", 80), "license": sp,
                    "license_class": "permissive", "origin": "gl-transitions",
                    "tags": transition_tags(f.stem, meta.get("tags", ""), desc)})
    return out


# ------------------------------------------------------------------------------------------- ISF

ISF_SKIP_CATS = {"Utility", "Masking", "Histogram", "Audio Visualizer", "Test Pattern"}


def isf_params(meta: dict) -> tuple[dict, list[str]]:
    params, keys, events = {}, set(), []
    for inp in meta.get("INPUTS", []) or []:
        ty, nm = inp.get("TYPE"), inp.get("NAME")
        if not nm or ty in ("image", "audio", "audioFFT"):
            continue
        label = one_line(inp.get("LABEL") or title(nm), 40)
        d = inp.get("DEFAULT")
        if ty == "event":
            events.append(nm)
        elif ty == "float":
            lo, hi = inp.get("MIN", 0.0), inp.get("MAX", 1.0)
            lo = lo if isinstance(lo, (int, float)) else 0.0
            hi = hi if isinstance(hi, (int, float)) else 1.0
            d = d if isinstance(d, (int, float)) else (lo + hi) / 2
            params[key_for(nm, keys)] = num_prop(lo, hi, d, label, glsl={"u": nm})
        elif ty == "bool":
            params[key_for(nm, keys)] = {"type": "boolean", "default": bool(True if d is None else d),
                                         "x-ui": {"label": label}, "x-tier": "regional", "x-glsl": {"u": nm}}
        elif ty == "long":
            vals, labels = inp.get("VALUES"), inp.get("LABELS")
            if not isinstance(vals, list) or not vals:
                vals = list(range(len(labels))) if isinstance(labels, list) and labels else None
            if vals:
                try:
                    vals = [int(v) for v in vals]
                except (TypeError, ValueError):
                    continue
                dd = int(d) if isinstance(d, (int, float)) and int(d) in vals else vals[0]
                params[key_for(nm, keys)] = num_prop(min(vals), max(vals), dd, label, enum=vals,
                                                     labels=labels if isinstance(labels, list) and
                                                     len(labels) == len(vals) else None, glsl={"u": nm})
            else:
                lo, hi = inp.get("MIN", 0), inp.get("MAX", 10)
                params[key_for(nm, keys)] = num_prop(lo, hi, d if isinstance(d, (int, float)) else lo, label,
                                                     tier="structural", integer=True, glsl={"u": nm})
        elif ty == "color":
            dv = d if isinstance(d, list) else [1, 1, 1, 1]
            params[key_for(nm, keys)] = {"type": "string", "format": "color", "default": hex_color(dv),
                                         "x-ui": {"label": label, "widget": "color"}, "x-tier": "regional",
                                         "x-glsl": {"u": nm, "color": 4, "alpha": float((dv + [1, 1, 1, 1])[3])}}
        elif ty == "point2D":
            dv = d if isinstance(d, list) and len(d) >= 2 else [0.5, 0.5]
            mn = inp.get("MIN") if isinstance(inp.get("MIN"), list) else None
            mx = inp.get("MAX") if isinstance(inp.get("MAX"), list) else None
            for i in range(2):
                v = float(dv[i])
                lo, hi = (mn[i], mx[i]) if mn and mx else guess_range(v)
                params[key_for(f"{nm}_{'xy'[i]}", keys)] = num_prop(lo, hi, v, f"{label} {'xy'[i]}",
                                                                    glsl={"u": nm, "c": i})
    return params, events


def isf_entry(fs: pathlib.Path, origin: str, url: str, author_default: str, lic: tuple[str, str, str | None],
              used: set, prefix: str = "isf") -> dict | None:
    src = fs.read_text(errors="replace")
    vs_path = fs.with_suffix(".vs")
    vs = vs_path.read_text(errors="replace") if vs_path.exists() else None
    try:
        meta, _ = T.parse_isf(src)
        tr = T.translate_isf(src, vs)
    except Exception as ex:
        print(f"{origin} {fs.name}: translate failed: {ex}", file=sys.stderr)
        return None
    kind = tr.meta["kind"]
    cats = [c for c in (meta.get("CATEGORIES") or []) if isinstance(c, str)]
    if kind == "transition" or set(cats) & ISF_SKIP_CATS or meta.get("IMPORTED"):
        return None
    params, events = isf_params(meta)
    name = f"{prefix}_" + slug(fs.stem)
    while name in used:
        name += "_2"
    used.add(name)
    desc = one_line(meta.get("DESCRIPTION") or f"{title(fs.stem)} ({', '.join(cats) or 'ISF'}).")
    credit = one_line(meta.get("CREDIT") or author_default, 100)
    e = {"name": name, "label": title(fs.stem), "description": desc, "dialect": "isf", "glsl": src, "vs": vs,
         "params": params, "events": tuple(events), "url": url, "author": credit or author_default,
         "license": lic[0], "license_class": lic[1], "licence_note": lic[2], "origin": origin}
    if kind == "filter":
        e["kind"] = "effect"
        e["stage"] = effect_stage(fs.stem, cats)
        e["tags"] = {"energy": [0.2, 1.0], "moods": moods_for(f"{fs.stem} {' '.join(cats)} {desc}"),
                     "style": "wild" if e["stage"] in ("glitch", "motion") else "lively", "family": e["stage"],
                     "dims": "2d", "technique": [e["stage"] if e["stage"] in ("glitch", "feedback", "colour", "light")
                                                 else "warp"], "weight": 0.5}
    else:
        e["kind"] = "source"
        e["tags"] = scene_tags(fs.stem, f"{' '.join(cats)} {desc}")
        e["render_scale"] = 0.5
    return e


def isf_licence(text: str, repo_spdx: str | None, provenance: str) -> tuple[str, str, str | None] | None:
    """(SPDX, class, note) or None (skip)."""
    if FORBID_RE.search(text):
        return None
    if NC_RE.search(text):
        return ("CC-BY-NC-SA-3.0", "noncommercial", None)
    if re.search(r"(?i)shadertoy\.com", text):
        explicit = re.search(r"(?i)license[^\n]{0,40}\b(MIT|CC0|public domain|BSD|Apache)", text)
        if explicit:
            return (spdx(explicit.group(0)) or "MIT", "permissive", None)
        return ("CC-BY-NC-SA-3.0", "noncommercial", "Shadertoy-derived: Shadertoy's default licence applies.")
    if re.search(r"(?i)creative commons attribution|CC[- ]BY\b", text):
        return ("CC-BY-3.0", "attribution", None)
    if repo_spdx:
        return (repo_spdx, "permissive", f"Licence: repository {provenance} LICENSE ({repo_spdx}).")
    sp = spdx(text)
    if sp:
        return (sp, "permissive", None)
    return None


def import_isf_files(root: pathlib.Path, used: set) -> list[dict]:
    out = []
    for fs in sorted((root / "ISF").glob("*.fs")):
        text = fs.read_text(errors="replace")
        lic = isf_licence(text, "MIT", "Vidvox/ISF-Files")
        if lic is None:
            continue
        if re.search(r"(?i)v002", text):
            lic = ("CC-BY-NC-SA-3.0", "noncommercial", "v002 port: v002 sources are CC-BY-NC-SA-3.0.")
        url = f"https://github.com/Vidvox/ISF-Files/blob/{ISF_REF}/ISF/{fs.name.replace(' ', '%20')}"
        e = isf_entry(fs, "Vidvox ISF-Files", url, "VIDVOX", lic, used)
        if e:
            out.append(e)
    return out


COLLECTIONS = [  # (dir name, github slug, repo licence or None (per file), name prefix)
    ("Gnomalab_VDMX-ISF-Effects-Suite", "Gnomalab/VDMX-ISF-Effects-Suite", "MIT", "isf"),
    ("daitomanabe_isf-shaders-for-vj", "daitomanabe/isf-shaders-for-vj", "MIT", "isf"),
    ("ProjectileObjects_MiscISFShaders", "ProjectileObjects/MiscISFShaders", "MIT", "isf"),
    ("bareimage_ISF", "bareimage/ISF", None, "isf"),
    ("nwhetsell_isf-shaders", "nwhetsell/isf-shaders", None, "isf"),
    ("icalvin102_ISF", "icalvin102/ISF", "MIT", "isf"),
]


def import_isf_collections(root: pathlib.Path, used: set) -> list[dict]:
    out = []
    for d, gh, repo_lic, prefix in COLLECTIONS:
        repo = root / d / "repo"
        if not repo.exists():
            print(f"collection {d} missing", file=sys.stderr)
            continue
        for fs in sorted(repo.rglob("*.fs")):
            text = fs.read_text(errors="replace")
            if "nwhetsell" in d and re.search(r"(?i)lygia", text):
                lic = ("Prosperity-3.0.0", "noncommercial", "Uses LYGIA (Prosperity licence, noncommercial).")
            else:
                lic = isf_licence(text, repo_lic, gh)
            if lic is None:
                continue
            rel = fs.relative_to(repo).as_posix().replace(" ", "%20")
            e = isf_entry(fs, gh, f"https://github.com/{gh}/blob/HEAD/{rel}", gh.split("/")[0], lic, used, prefix)
            if e:
                out.append(e)
    return out


# ------------------------------------------------------------------------------------------- Shadertoy

ST_OK_INPUTS = {"music", "musicstream", "mic", "texture"}


def likes_weight(likes: int) -> float:
    """Director weight from Shadertoy likes: popular pieces come up more, tutorials and tests rarely."""
    return 1.0 if likes >= 100 else 0.7 if likes >= 30 else 0.4 if likes >= 10 else 0.15


def import_shadertoy(tsv: pathlib.Path, dump: pathlib.Path, used: set) -> list[dict]:
    rows = [ln.split("\t") for ln in tsv.read_text().splitlines()[1:]]
    want = {r[1]: r for r in rows if r[5] in ("image", "image+common")}
    recs = {}
    for f in sorted(dump.glob("*.jsonl")):
        for line in open(f, encoding="utf-8"):
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("id") in want:
                recs[d["id"]] = d
    out = []
    for sid, r in sorted(want.items(), key=lambda kv: -int(kv[1][0])):
        d = recs.get(sid)
        if not d:
            continue
        chans = {}
        bad = False
        for inp in d.get("image_inputs") or []:
            ct = inp.get("ctype")
            if ct not in ST_OK_INPUTS:
                bad = True
                break
            chans[int(inp.get("channel", 0))] = "music" if ct != "texture" else "noise"
        if bad:
            continue
        code = (d.get("common_code") or "") + "\n" + d["image_code"] if d.get("common_code") else d["image_code"]
        try:
            T.translate_shadertoy(code)
        except Exception as ex:
            print(f"st {sid}: translate failed: {ex}", file=sys.stderr)
            continue
        lic_line = r[8]
        sp = spdx(lic_line) or "MIT"
        name = "st_" + slug(d["name"], 34)
        if name in used:
            name = f"{name}_{sid.lower()}"
        used.add(name)
        desc = one_line(d.get("description") or f"{d['name']} by {d['author']} (Shadertoy).")
        out.append({"kind": "source", "name": name, "label": title(d["name"]), "description": desc, "dialect": "st",
                    "glsl": code, "params": {}, "channels": chans, "url": f"https://www.shadertoy.com/view/{sid}",
                    "author": one_line(d["author"], 60), "license": sp, "license_class": "permissive",
                    "licence_note": f"Licence line in the shader: {one_line(lic_line, 120)}",
                    "origin": "Shadertoy", "render_scale": 0.5,
                    "tags": {**scene_tags(d["name"], f"{r[4]} {' '.join(d.get('tags') or [])} {desc}"),
                             "weight": likes_weight(int(r[0]))}})
    return out


# ------------------------------------------------------------------------------------------- main

SCENE_BUDGET_MS, EFFECT_BUDGET_MS, MAX_MS = 6.0, 2.5, 14.0   # P4000 at 4K; MAX: never worth a slot above
TIMING_FACTOR = 1.0   # --timing-factor: scale measured times (e.g. 0.5 when the smoke GPU was contended)


def apply_tune(e: dict, r: dict) -> bool:
    """Set render scales from the smoke test's timing (960x540 on the P4000); False = drop it (too heavy, a
    scene that does not move, or an effect that leaves its input unchanged at default settings)."""
    if e["kind"] == "source" and float(r.get("moving", 1.0)) < 0.05:
        return False
    if e["kind"] == "effect" and float(r.get("d_src", 1.0)) < 0.3:
        return False
    if e["kind"] == "source":
        s0 = e.get("render_scale", 0.5)
        est = float(r.get("est_ms_4k") or 0.0) * TIMING_FACTOR   # at s0
        if est > SCENE_BUDGET_MS:
            s = max(0.25, s0 * (SCENE_BUDGET_MS / est) ** 0.5)
            s = round(int(s * 20) / 20, 2)
            if est * (s / s0) ** 2 > MAX_MS:
                return False
            e["render_scale"] = s
    elif e["kind"] == "effect":
        est = float(r.get("est_ms_4k") or 0.0) * TIMING_FACTOR   # full resolution (scale 1)
        if est > EFFECT_BUDGET_MS:
            s = max(0.35, (EFFECT_BUDGET_MS / est) ** 0.5)
            s = round(int(s * 20) / 20, 2)
            if est * s * s > MAX_MS:
                return False
            e["scale"] = s
    return True


def existing_names() -> set:
    names = set()
    for kind in ("sources", "transitions", "effects"):
        for f in (PLUGINS / kind).glob("*.py"):
            if GENERATED_MARK not in f.read_text(errors="replace")[:2000]:
                names.add(f.stem)
    return names


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--glt")
    ap.add_argument("--isf")
    ap.add_argument("--isf-coll")
    ap.add_argument("--st")
    ap.add_argument("--st-dump")
    ap.add_argument("--only", default="glt,isf,coll,st")
    ap.add_argument("--out", default=str(PLUGINS), help="root holding sources/ transitions/ effects/ (staging)")
    ap.add_argument("--skip", default=None, help="file with plug-in names to leave out (smoke-test rejects)")
    ap.add_argument("--tune", default=None, help="smoke.py results (.jsonl): drop failures, set render scales")
    ap.add_argument("--prune", action="store_true", help="remove generated plug-ins not written by this run")
    ap.add_argument("--timing-factor", type=float, default=1.0)
    ap.add_argument("--calibration", default=None,
                    help="tools/calibrate.py results (~/.cache/sm-fxtest/calibration.json): measured 4K cost wins "
                         "over the smoke estimate; a plug-in that fits on neither GPU is dropped")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    skip = set(pathlib.Path(a.skip).read_text().split()) if a.skip else set()
    global TIMING_FACTOR
    TIMING_FACTOR = a.timing_factor
    tune = {}
    if a.tune:
        for line in open(a.tune):
            d = json.loads(line)
            if "ok" in d:
                tune[d["name"]] = d
    only = set(a.only.split(","))
    used = existing_names()
    entries = []
    if "glt" in only and a.glt:
        entries += import_glt(pathlib.Path(a.glt), used)
    if "isf" in only and a.isf:
        entries += import_isf_files(pathlib.Path(a.isf), used)
    if "coll" in only and a.isf_coll:
        entries += import_isf_collections(pathlib.Path(a.isf_coll), used)
    if "st" in only and a.st and a.st_dump:
        entries += import_shadertoy(pathlib.Path(a.st), pathlib.Path(a.st_dump), used)
    counts = {}
    entries = [e for e in entries if e["name"] not in skip]
    if a.tune:          # results given: land only what passed (an empty file lands nothing)
        kept = []
        for e in entries:
            r = tune.get(e["name"])
            if r is None or not r.get("ok"):
                continue
            if not apply_tune(e, r):
                continue
            kept.append(e)
        print(f"tune: kept {len(kept)} of {len(entries)}")
        entries = kept
    if a.calibration and pathlib.Path(a.calibration).exists():
        cal = json.loads(pathlib.Path(a.calibration).read_text()).get("plugins", {})
        kept, dropped = [], 0
        for e in entries:
            c = cal.get(e["name"])
            gpus = [c[g] for g in ("gpu1", "gpu0") if c and g in c]      # P4000 first: the slower card decides
            if not gpus:
                kept.append(e)
                continue
            if not any(g.get("status") == "ok" or g.get("fits") for g in gpus):
                dropped += 1
                continue
            g = gpus[0]
            if e["kind"] == "source" and g.get("fit_scale"):
                e["render_scale"] = g["fit_scale"]
            kept.append(e)
        print(f"calibration: dropped {dropped} that fit on neither GPU")
        entries = kept
    for e in entries:
        write_plugin(e, a.dry, pathlib.Path(a.out))
        k = (e["origin"], e["kind"], e["license_class"])
        counts[k] = counts.get(k, 0) + 1
    if a.prune and not a.dry:
        keep = {e["name"] for e in entries}
        for kind in ("sources", "transitions", "effects"):
            for f in (pathlib.Path(a.out) / kind).glob("*.py"):
                if f.stem not in keep and GENERATED_MARK in f.read_text(errors="replace")[:2000]:
                    f.unlink()
                    pre = f.with_name(f.stem + ".presets.yaml")
                    if pre.exists():
                        pre.unlink()
                    print(f"pruned {kind}/{f.stem}")
    for k, n in sorted(counts.items()):
        print(f"{n:4d}  {k[0]:40s} {k[1]:10s} {k[2]}")
    print(f"{len(entries)} plug-ins {'(dry run)' if a.dry else 'written'}")


if __name__ == "__main__":
    main()
