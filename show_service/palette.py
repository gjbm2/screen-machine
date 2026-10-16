"""palette: colour maths in OKLab/OKLCh, the palette catalog, and palettes from the art (docs/SHOW_ENGINE.md §8.8;
README "Stage 3 > director.py, palette.py, vibes.py").

Pure functions plus one small class. No colour library: OKLab is Björn Ottosson's (2020) pair of matrices around a
cube root, about 30 lines below. Every ``Rgb`` is gamma-encoded sRGB, each channel 0..1.

**Gamut.** ``from_oklch`` stays in the sRGB gamut by reducing chroma (a bisection), never by shifting the hue, so a
capped or gamut-mapped colour keeps the hue a person picked. ``from_oklab`` (used by ``mix``) clips.

**The catalog** is the renderer's own palette list, so the screens and the lamps name the same palettes:
  * the renderer's catalog API block (``GET /api/catalog``'s ``palettes``; the director fetches it through the room's
    one front door, ``/renderer/api/catalog``, and hands it to ``Catalog.ingest``), when it has been reachable;
  * else ``stream_service/palettes.py``, loaded **by file path** (``importlib``; never ``import stream_service``),
    re-read when its mtime changes.
Each palette is an Inigo Quilez cosine palette ``a + b cos(2 pi (c t + d))``. Its roles come from five samples at
t = 0, 1/5, ..., 4/5 through ``roles_from``.

**From the art.** ``from_image`` turns the on-glass still that room_service captures into ``k`` OKLab clusters
(k-means, 8 iterations, a fixed seed, so the same still always gives the same colours), largest first. Near-black
pixels (letterbox bars, the masked dark of a dim room) are left out unless they are nearly the whole picture.
"""

from __future__ import annotations

import functools
import importlib.util
import io
import logging
import math
import pathlib
from typing import Any, Iterable, Optional, Sequence

from . import contract

log = logging.getLogger("show_service.palette")

Rgb = tuple[float, float, float]

# -- OKLab (Ottosson) -----------------------------------------------------------------------------------------------
_M1 = ((0.4122214708, 0.5363325363, 0.0514459929),       # linear sRGB -> LMS
       (0.2119034982, 0.6806995451, 0.1073969566),
       (0.0883024619, 0.2817188376, 0.6299787005))
_M2 = ((0.2104542553, 0.7936177850, -0.0040720468),      # LMS' -> Lab
       (1.9779984951, -2.4285922050, 0.4505937099),
       (0.0259040371, 0.7827717662, -0.8086757660))
_GAMUT_EPS = 1e-6
ACHROMATIC_C = 1e-4         # below this OKLCh chroma a colour is a grey: C 0, h 0 (its hue would be rounding noise)
ACCENT_MIN_C = 0.03         # an accent "far from base" must carry at least this chroma...
ACCENT_MIN_SHARE = 0.5      # ...and at least half the most colourful sample's, or a near-black's noise hue wins
WHITE_RGB: Rgb = (1.0, 1.0, 1.0)


def _lin(c: float) -> float:
    """sRGB transfer, decode."""
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _gam(c: float) -> float:
    """sRGB transfer, encode (a negative input encodes to its negative, so callers can clip after)."""
    if c <= 0.0031308:
        return 12.92 * c
    return 1.055 * c ** (1.0 / 2.4) - 0.055


def _c01(x: float) -> float:
    return 0.0 if not x > 0.0 else 1.0 if x > 1.0 else x


def _lab_from_linear(r: float, g: float, b: float) -> tuple[float, float, float]:
    l_ = math.cbrt(_M1[0][0] * r + _M1[0][1] * g + _M1[0][2] * b)
    m_ = math.cbrt(_M1[1][0] * r + _M1[1][1] * g + _M1[1][2] * b)
    s_ = math.cbrt(_M1[2][0] * r + _M1[2][1] * g + _M1[2][2] * b)
    return (_M2[0][0] * l_ + _M2[0][1] * m_ + _M2[0][2] * s_,
            _M2[1][0] * l_ + _M2[1][1] * m_ + _M2[1][2] * s_,
            _M2[2][0] * l_ + _M2[2][1] * m_ + _M2[2][2] * s_)


def _linear_from_lab(L: float, a: float, b: float) -> tuple[float, float, float]:
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_ * l_ * l_, m_ * m_ * m_, s_ * s_ * s_
    return (4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
            -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
            -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s)


def to_oklab(rgb: Sequence[float]) -> tuple[float, float, float]:
    r, g, b = rgb
    return _lab_from_linear(_lin(r), _lin(g), _lin(b))


def from_oklab(lab: Sequence[float]) -> Rgb:
    """OKLab -> sRGB, clipped to 0..1 per channel."""
    r, g, b = _linear_from_lab(*lab)
    return (_c01(_gam(r)), _c01(_gam(g)), _c01(_gam(b)))


def to_oklch(rgb: Sequence[float]) -> tuple[float, float, float]:
    """(L 0..1, C 0..~0.37, h degrees 0..360). h is 0 for a grey."""
    L, a, b = to_oklab(rgb)
    C = math.hypot(a, b)
    if C < ACHROMATIC_C:
        return L, 0.0, 0.0
    return L, C, math.degrees(math.atan2(b, a)) % 360.0


def _in_gamut(lin: Sequence[float]) -> bool:
    return all(-_GAMUT_EPS <= c <= 1.0 + _GAMUT_EPS for c in lin)


def from_oklch(L: float, C: float, h: float) -> Rgb:
    """OKLCh -> sRGB, in gamut by reducing C (bisection), never shifting h. L is clamped to 0..1, C to >= 0."""
    L = _c01(float(L))
    C = max(0.0, float(C))
    hr = math.radians(float(h))
    ca, sa = math.cos(hr), math.sin(hr)
    lin = _linear_from_lab(L, C * ca, C * sa)
    if not _in_gamut(lin):
        lo, hi = 0.0, C
        for _ in range(20):
            mid = 0.5 * (lo + hi)
            if _in_gamut(_linear_from_lab(L, mid * ca, mid * sa)):
                lo = mid
            else:
                hi = mid
        lin = _linear_from_lab(L, lo * ca, lo * sa)
    return (_c01(_gam(lin[0])), _c01(_gam(lin[1])), _c01(_gam(lin[2])))


def hex_of(rgb: Sequence[float]) -> str:
    return "#" + "".join(f"{max(0, min(255, round(_c01(float(c)) * 255))):02x}" for c in rgb)


def rgb_of(hex_: str) -> Rgb:
    """'#rrggbb' or '#rgb' -> sRGB 0..1; ValueError otherwise."""
    h = str(hex_).strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        raise ValueError(f"colour {hex_!r}: expected #rrggbb")
    return (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0)


def mix(a: Sequence[float], b: Sequence[float], t: float) -> Rgb:
    """``a`` -> ``b`` at ``t`` (0..1), interpolated in OKLab."""
    t = _c01(float(t))
    la, lb = to_oklab(a), to_oklab(b)
    return from_oklab(tuple(x + (y - x) * t for x, y in zip(la, lb)))


def mix_lab(la: Sequence[float], lb: Sequence[float], t: float) -> Rgb:
    """``mix`` with both ends already in OKLab (palette_fn's crossfade: no conversion on the way in)."""
    return from_oklab((la[0] + (lb[0] - la[0]) * t, la[1] + (lb[1] - la[1]) * t, la[2] + (lb[2] - la[2]) * t))


def _xyz_to_rgb_full(X: float, Y: float, Z: float) -> Rgb:
    """XYZ -> sRGB with negative channels clipped and the largest channel at 1 (a colour, not a level)."""
    r = 3.2404542 * X - 1.5371385 * Y - 0.4985314 * Z
    g = -0.9692660 * X + 1.8760108 * Y + 0.0415560 * Z
    b = 0.0556434 * X - 0.2040259 * Y + 1.0572252 * Z
    r, g, b = max(0.0, r), max(0.0, g), max(0.0, b)
    m = max(r, g, b)
    if m <= 0.0:
        return WHITE_RGB
    return (_c01(_gam(r / m)), _c01(_gam(g / m)), _c01(_gam(b / m)))


def from_xy(x: float, y: float) -> Rgb:
    """CIE 1931 xy (a Hue light state's colour) -> sRGB at full value (largest channel 1)."""
    x, y = float(x), float(y)
    if not y > 0.0:
        return WHITE_RGB
    return _xyz_to_rgb_full(x / y, 1.0, (1.0 - x - y) / y)


@functools.lru_cache(maxsize=256)
def white(kelvin: float) -> Rgb:
    """A correlated colour temperature -> sRGB, largest channel 1 (Kim et al. 2002's Planckian-locus fit, valid
    1667-25000 K; colder or warmer inputs are clamped to it)."""
    T = min(25000.0, max(1667.0, float(kelvin)))
    if T <= 4000.0:
        x = -0.2661239e9 / T ** 3 - 0.2343589e6 / T ** 2 + 0.8776956e3 / T + 0.179910
    else:
        x = -3.0258469e9 / T ** 3 + 2.1070379e6 / T ** 2 + 0.2226347e3 / T + 0.240390
    if T <= 2222.0:
        y = -1.1063814 * x ** 3 - 1.34811020 * x ** 2 + 2.18555832 * x - 0.20219683
    elif T <= 4000.0:
        y = -0.9549476 * x ** 3 - 1.37418593 * x ** 2 + 2.09137015 * x - 0.16748867
    else:
        y = 3.0817580 * x ** 3 - 5.87338670 * x ** 2 + 3.75112997 * x - 0.37001483
    return from_xy(x, y)


def full(rgb: Sequence[float]) -> Rgb:
    """The same chromaticity at full value: the largest channel 1, scaled in linear light (black stays black). A
    lamp's colour carries hue and chroma; its brightness is the look's level, applied after."""
    lin = [_lin(_c01(float(c))) for c in rgb]
    m = max(lin)
    if m <= 1e-9:
        return (0.0, 0.0, 0.0)
    return (_c01(_gam(lin[0] / m)), _c01(_gam(lin[1] / m)), _c01(_gam(lin[2] / m)))


def scale_level(rgb: Sequence[float], k: float) -> Rgb:
    """A lamp level: every channel x k, as artnet_out and the room's Hue output read it (a lamp's brightness is its
    largest channel, so ``full(c)`` x 0.6 is that colour at level 0.6)."""
    k = _c01(float(k))
    return (_c01(float(rgb[0])) * k, _c01(float(rgb[1])) * k, _c01(float(rgb[2])) * k)


def limit_chroma(rgb: Sequence[float], c_max: float) -> Rgb:
    L, C, h = to_oklch(rgb)
    if C <= c_max:
        return (float(rgb[0]), float(rgb[1]), float(rgb[2]))
    return from_oklch(L, max(0.0, float(c_max)), h)


def _hue_in(h: float, lo: float, hi: float) -> bool:
    lo, hi = lo % 360.0, hi % 360.0
    return lo <= h <= hi if lo <= hi else (h >= lo or h <= hi)


def _hue_dist(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def avoid_hues(rgb: Sequence[float], ranges: Iterable[Sequence[float]]) -> Rgb:
    """A hue inside one of ``ranges`` ([lo, hi] degrees, OKLCh; lo > hi wraps through 0) moves to the range's nearer
    edge; L and C are kept (C is reduced only if the new hue needs it to stay in gamut). Greys are left alone."""
    L, C, h = to_oklch(rgb)
    out = (float(rgb[0]), float(rgb[1]), float(rgb[2]))
    if C < 1e-3:
        return out
    moved = False
    for _ in range(3):                                   # adjacent ranges: settle in at most a few moves
        for rg in ranges or ():
            try:
                lo, hi = float(rg[0]), float(rg[1])
            except (TypeError, ValueError, IndexError):
                continue
            if _hue_in(h, lo, hi):
                h = lo % 360.0 if _hue_dist(h, lo) <= _hue_dist(h, hi) else hi % 360.0
                # step just outside, so the edge itself doesn't count as inside
                h = (h - 0.01) % 360.0 if h == lo % 360.0 else (h + 0.01) % 360.0
                moved = True
    return from_oklch(L, C, h) if moved else out


def scale_saturation(rgb: Sequence[float], s: float) -> Rgb:
    """OKLCh C x s (s >= 0), hue and L kept."""
    L, C, h = to_oklch(rgb)
    return from_oklch(L, C * max(0.0, float(s)), h)


def role(rgb: Sequence[float]) -> contract.ColourRole:
    L, C, h = to_oklch(rgb)
    return {"hex": hex_of(rgb), "oklch": [round(L, 4), round(C, 4), round(h, 2)]}


def roles_from(colours: Sequence[Sequence[float]]) -> contract.PaletteRoles:
    """base = the median-L colour; accent = the highest-C colour at least 60° from base (else the highest C);
    highlight = the lightest. No colours: white in every role. A "far" accent must carry real colour (ACCENT_MIN_C, and
    ACCENT_MIN_SHARE of the most colourful): a near-black sample's hue is noise, never an accent."""
    cols = [tuple(float(c) for c in x) for x in colours if x is not None and len(x) == 3]
    if not cols:
        r = role(WHITE_RGB)
        return {"base": r, "accent": dict(r), "highlight": dict(r)}           # type: ignore[typeddict-item]
    lch = [to_oklch(c) for c in cols]
    by_l = sorted(range(len(cols)), key=lambda i: (lch[i][0], i))
    base = by_l[(len(by_l) - 1) // 2]
    others = [i for i in range(len(cols)) if i != base] or [base]
    c_top = max(lch[i][1] for i in others)
    c_min = max(ACCENT_MIN_C, ACCENT_MIN_SHARE * c_top)
    far = [i for i in others if lch[i][1] >= c_min
           and (lch[base][1] < ACCENT_MIN_C or _hue_dist(lch[i][2], lch[base][2]) >= 60.0)]
    pool = far or others
    accent = max(pool, key=lambda i: (lch[i][1], -i))
    highlight = by_l[-1]
    return {"base": role(cols[base]), "accent": role(cols[accent]), "highlight": role(cols[highlight])}


# -- the catalog ----------------------------------------------------------------------------------------------------
def _row(x: Any) -> tuple[float, float, float]:
    r = tuple(float(v) for v in x)
    if len(r) != 3 or not all(math.isfinite(v) for v in r):
        raise ValueError("a cosine row is three numbers")
    return r                                                                 # type: ignore[return-value]


def _parse_block(block: Any) -> dict[str, dict]:
    """The renderer's ``palettes`` block (catalog API) or palettes.py's ``catalog()`` / ``PALETTES`` -> our entries
    ``{name: {"label", "cosine": (a, b, c, d), "family", "moods", "swatch"}}``. A bad entry is skipped."""
    out: dict[str, dict] = {}
    if not isinstance(block, dict):
        return out
    for name, p in block.items():
        if not isinstance(name, str) or not isinstance(p, dict):
            continue
        try:
            cos = p.get("cosine")
            if isinstance(cos, dict):
                a, b, c, d = (_row(cos[k]) for k in ("a", "b", "c", "d"))
            else:
                a, b, c, d = (_row(r) for r in cos)
        except Exception:
            continue
        tags = p.get("tags") if isinstance(p.get("tags"), dict) else {}
        fam = tags.get("family")
        sw = p.get("swatch")
        out[name] = {"label": str(p.get("label") or name), "cosine": (a, b, c, d),
                     "family": fam if isinstance(fam, str) else None,
                     "moods": [m for m in (tags.get("moods") or []) if isinstance(m, str)],
                     "swatch": [s for s in sw if isinstance(s, str)] if isinstance(sw, list) else None}
    return out


def block_from_catalog(body: bytes) -> Optional[dict]:
    """The ``palettes`` object out of the renderer's catalog response (about 2.5 MB of JSON) without parsing the whole
    document: each ``"palettes":`` key is tried with ``raw_decode``, and the first value that holds usable palettes wins.
    A full parse would hold the GIL for ~45 ms, which the audio thread shares. None when there is none."""
    import json
    dec = json.JSONDecoder()
    raw = bytes(body) if isinstance(body, (bytes, bytearray)) else str(body).encode("utf-8")
    key = b'"palettes"'
    i = raw.find(key)
    while i >= 0:
        text = raw[i + len(key):].decode("utf-8", errors="replace")     # only from the key on (the tail is small)
        j = len(text) - len(text.lstrip(" \t\r\n"))
        if text[j:j + 1] == ":":
            j += 1
            while j < len(text) and text[j] in " \t\r\n":
                j += 1
            try:
                val, _end = dec.raw_decode(text, j)
            except ValueError:
                val = None
            if isinstance(val, dict) and _parse_block(val):
                return val
        i = raw.find(key, i + 1)
    return None


def cosine_colour(cos: Sequence[Sequence[float]], t: float) -> Rgb:
    """stream_service/palettes.py's ``colour``: a + b cos(2 pi (c t + d)), clamped."""
    a, b, c, d = cos
    return tuple(_c01(a[i] + b[i] * math.cos(2.0 * math.pi * (c[i] * t + d[i]))) for i in range(3))  # type: ignore


class Catalog:
    """The palette catalog (module docstring). Show-brain only, except the read methods, which read a dict swapped by
    reference."""

    def __init__(self, path: pathlib.Path = contract.PALETTES_FILE) -> None:
        self.path = pathlib.Path(path)
        self.error: Optional[str] = None               # the last file problem (the last good set stays in use)
        self.api_error: Optional[str] = None           # the last catalog-API problem
        self.source: Optional[str] = None              # "api" | "file" | None (nothing loaded)
        self._file: dict[str, dict] = {}
        self._api: Optional[dict[str, dict]] = None
        self._sig: Optional[tuple[float, int]] = None
        self._entries: dict[str, dict] = {}
        self._roles: dict[str, contract.PaletteRoles] = {}

    # -- loading (show-brain) ---------------------------------------------------------------------------------------
    def poll(self) -> bool:
        """Re-read palettes.py when its mtime or size changed. True when the palettes in use changed."""
        try:
            st = self.path.stat()
            sig = (st.st_mtime, st.st_size)
        except OSError as e:
            if self._sig is not None or self.error is None:
                self.error = f"{self.path.name}: {e.strerror or e}"
            self._sig = None
            return False
        if sig == self._sig:
            return False
        self._sig = sig
        try:
            spec = importlib.util.spec_from_file_location("_show_palettes_catalog", self.path)
            if spec is None or spec.loader is None:
                raise ImportError("no loader")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)                # never registered in sys.modules
            fn = getattr(mod, "catalog", None)
            block = fn() if callable(fn) else getattr(mod, "PALETTES", None)
            parsed = _parse_block(block)
            if not parsed:
                raise ValueError("no palettes in it")
        except Exception as e:
            self.error = f"{self.path.name}: {type(e).__name__}: {e}"
            log.warning("palette catalog: %s (keeping the last good set)", self.error)
            return False
        self.error = None
        self._file = parsed
        return self._swap()

    def ingest(self, block: Any) -> bool:
        """The renderer's catalog API ``palettes`` block. A usable block wins over the file from then on; a bad one is
        noted and changes nothing. True when the palettes in use changed."""
        parsed = _parse_block(block)
        if not parsed:
            self.api_error = "the renderer's catalog had no usable palettes"
            return False
        self.api_error = None
        self._api = parsed
        return self._swap()

    def api_failed(self, why: str) -> None:
        self.api_error = why

    def _swap(self) -> bool:
        src, entries = ("api", self._api) if self._api else ("file", self._file) if self._file else (None, {})
        same = src == self.source and {k: v["cosine"] for k, v in entries.items()} == \
            {k: v["cosine"] for k, v in self._entries.items()} and list(entries) == list(self._entries)
        self.source = src
        self._roles = {}
        self._entries = dict(entries)
        return not same

    # -- reads ------------------------------------------------------------------------------------------------------
    def names(self) -> list[str]:
        return list(self._entries)

    def has(self, name: str) -> bool:
        return name in self._entries

    def family(self, name: str) -> Optional[str]:
        e = self._entries.get(name)
        return e["family"] if e else None

    def label(self, name: str) -> str:
        e = self._entries.get(name)
        return e["label"] if e else name

    def colours(self, name: str, n: int = 5) -> list[Rgb]:
        """The cosine palette at t = 0, 1/n, ..., (n-1)/n. LookupError for an unknown name."""
        e = self._entries.get(name)
        if e is None:
            raise LookupError(f"unknown palette {name!r}")
        n = max(1, int(n))
        return [cosine_colour(e["cosine"], i / n) for i in range(n)]

    def roles(self, name: str) -> contract.PaletteRoles:
        r = self._roles.get(name)
        if r is None:
            r = roles_from(self.colours(name, 5))
            self._roles[name] = r
        return r

    def choice(self, name: str) -> contract.PaletteChoice:
        e = self._entries.get(name)
        if e is None:
            raise LookupError(f"unknown palette {name!r}")
        sw = e["swatch"] if e["swatch"] else [hex_of(c) for c in self.colours(name, 5)]
        return {"name": name, "label": e["label"], "family": e["family"], "swatch": list(sw)}

    def choices(self) -> list[contract.PaletteChoice]:
        return [self.choice(n) for n in self._entries]


# -- palettes from the art ------------------------------------------------------------------------------------------
IMAGE_WIDTH = 64
KMEANS_ITERATIONS = 8
KMEANS_SEED = 20261016
DARK_L = 0.12               # OKLab L below this is "black" (letterbox, a masked dark room)
DARK_KEEP = 0.9             # ... and is kept only when it is at least this share of the picture


def _oklab_np(rgb):
    import numpy as np
    c = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    lms = np.cbrt(c @ np.array(_M1).T)
    return lms @ np.array(_M2).T


def from_image(data: bytes, k: int = 5) -> Optional[list[Rgb]]:
    """Pillow decode, 64 px wide, k-means in OKLab (8 iterations, fixed seed), largest cluster first, as sRGB; fewer
    than ``k`` when the picture has fewer distinct colours. None on any failure (degrade: no art palette)."""
    try:
        import numpy as np
        from PIL import Image

        k = max(1, int(k))
        im = Image.open(io.BytesIO(data))
        im.draft("RGB", (IMAGE_WIDTH * 2, IMAGE_WIDTH * 2))     # a JPEG decodes at 1/2..1/8 scale; others ignore it
        im = im.convert("RGB")
        w, h = im.size
        if w < 1 or h < 1:
            return None
        nh = max(1, round(h * IMAGE_WIDTH / w))
        im = im.resize((IMAGE_WIDTH, nh), Image.Resampling.BOX)
        px = np.asarray(im, dtype=np.float64).reshape(-1, 3) / 255.0
        lab = _oklab_np(px)
        lit = lab[:, 0] >= DARK_L
        if lit.mean() > 1.0 - DARK_KEEP:
            lab = lab[lit]
        n = len(lab)
        if n == 0:
            return None
        rng = np.random.default_rng(KMEANS_SEED)
        # k-means++ seeding (deterministic with the fixed seed)
        cent = [lab[rng.integers(n)]]
        for _ in range(1, min(k, n)):
            d2 = np.min(((lab[:, None, :] - np.array(cent)[None, :, :]) ** 2).sum(-1), axis=1)
            tot = d2.sum()
            if tot <= 1e-12:
                break                                   # every pixel already sits on a centre
            cent.append(lab[rng.choice(n, p=d2 / tot)])
        cent = np.array(cent)
        for _ in range(KMEANS_ITERATIONS):
            lbl = np.argmin(((lab[:, None, :] - cent[None, :, :]) ** 2).sum(-1), axis=1)
            for j in range(len(cent)):
                sel = lab[lbl == j]
                if len(sel):
                    cent[j] = sel.mean(axis=0)
        lbl = np.argmin(((lab[:, None, :] - cent[None, :, :]) ** 2).sum(-1), axis=1)
        counts = np.bincount(lbl, minlength=len(cent))
        order = [j for j in np.argsort(-counts, kind="stable") if counts[j] > 0]
        return [from_oklab(tuple(float(v) for v in cent[j])) for j in order]
    except Exception as e:                              # a bad image, no Pillow: no art palette, never an error page
        log.debug("from_image: %s", e)
        return None
