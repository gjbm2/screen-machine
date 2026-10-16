"""palette: OKLab maths, gamut mapping, roles, the catalog (file and API block) and palettes from a picture.

Pure tests: no network. The catalog's file is the repo's stream_service/palettes.py (read only), or a tmp copy for the
hot-reload cases.
"""

from __future__ import annotations

import io
import itertools
import math
import shutil
import sys

import pytest

from show_service import contract
from show_service import palette as pal


def lab_dist(a, b) -> float:
    return math.dist(pal.to_oklab(a), pal.to_oklab(b))


def hue_dist(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


# -- OKLab ------------------------------------------------------------------------------------------------------------
def test_oklab_reference_values():
    L, a, b = pal.to_oklab((1.0, 0.0, 0.0))                      # Ottosson's published sRGB red
    assert (L, a, b) == pytest.approx((0.62796, 0.22486, 0.12585), abs=2e-4)
    L, a, b = pal.to_oklab((1.0, 1.0, 1.0))
    assert L == pytest.approx(1.0, abs=1e-4) and abs(a) < 1e-4 and abs(b) < 1e-4
    assert pal.to_oklab((0.0, 0.0, 0.0)) == pytest.approx((0.0, 0.0, 0.0), abs=1e-9)


def test_oklab_round_trip():
    steps = [0.0, 0.1, 0.33, 0.5, 0.77, 1.0]
    worst = max(max(abs(x - y) for x, y in zip(c, pal.from_oklab(pal.to_oklab(c))))
                for c in itertools.product(steps, repeat=3))
    assert worst < 2e-4


def test_oklch_round_trip_and_greys():
    for c in [(0.9, 0.2, 0.1), (0.1, 0.5, 0.9), (0.3, 0.8, 0.4)]:
        assert max(abs(x - y) for x, y in zip(c, pal.from_oklch(*pal.to_oklch(c)))) < 2e-4
    L, C, h = pal.to_oklch((0.5, 0.5, 0.5))
    assert C == 0.0 and h == 0.0                                  # a grey has no hue (not rounding noise)


def test_gamut_mapping_reduces_chroma_and_keeps_hue():
    for h in range(0, 360, 15):
        for L in (0.45, 0.6, 0.75):
            rgb = pal.from_oklch(L, 0.5, h)                       # far outside sRGB everywhere
            assert all(0.0 <= x <= 1.0 for x in rgb)
            L2, C2, h2 = pal.to_oklch(rgb)
            assert C2 < 0.5
            assert L2 == pytest.approx(L, abs=2e-3)
            assert hue_dist(h2, h) < 0.5, (L, h, h2)


def test_hex_round_trip_and_bad_hex():
    for hx in ("#000000", "#ffffff", "#ff8f35", "#0a1b2c"):
        assert pal.hex_of(pal.rgb_of(hx)) == hx
    assert pal.rgb_of("#fff") == (1.0, 1.0, 1.0)
    with pytest.raises(ValueError):
        pal.rgb_of("#12345")


def test_mix_is_in_oklab():
    a, b = (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)
    assert pal.mix(a, b, 0.0) == pytest.approx(a, abs=2e-4)
    assert pal.mix(a, b, 1.0) == pytest.approx(b, abs=2e-4)
    mid = pal.to_oklab(pal.mix(a, b, 0.5))
    want = [(x + y) / 2 for x, y in zip(pal.to_oklab(a), pal.to_oklab(b))]
    assert math.dist(mid, want) < 0.02                            # (clipped into gamut a little)


def test_white_and_xy():
    w65 = pal.white(6500)
    assert min(w65) > 0.93 and max(w65) == pytest.approx(1.0)
    w27 = pal.white(2700)
    assert w27[0] == pytest.approx(1.0) and w27[0] > w27[1] > w27[2] > 0.3
    blues = [pal.white(k)[2] for k in (1800, 2400, 3000, 4000, 5000, 6500)]
    assert blues == sorted(blues)                                 # bluer as it gets cooler
    assert pal.from_xy(0.3127, 0.3290) == pytest.approx((1.0, 1.0, 1.0), abs=0.01)   # D65


def test_full_and_level():
    c = pal.full((0.25, 0.125, 0.0))
    assert max(c) == pytest.approx(1.0)
    assert hue_dist(pal.to_oklch(c)[2], pal.to_oklch((0.25, 0.125, 0.0))[2]) < 1.0
    assert pal.full((0.0, 0.0, 0.0)) == (0.0, 0.0, 0.0)
    assert pal.scale_level((1.0, 0.5, 0.2), 0.5) == pytest.approx((0.5, 0.25, 0.1))


def test_limit_chroma_and_scale_saturation():
    c = (1.0, 0.1, 0.1)
    L, C, h = pal.to_oklch(c)
    lim = pal.limit_chroma(c, 0.05)
    L2, C2, h2 = pal.to_oklch(lim)
    assert C2 == pytest.approx(0.05, abs=2e-3) and hue_dist(h, h2) < 1.0 and L2 == pytest.approx(L, abs=2e-3)
    assert pal.limit_chroma(c, 0.37) == c                         # under the cap: untouched
    half = pal.to_oklch(pal.scale_saturation(c, 0.5))
    assert half[1] == pytest.approx(C * 0.5, abs=3e-3) and hue_dist(half[2], h) < 1.0
    assert pal.to_oklch(pal.scale_saturation(c, 0.0))[1] < 1e-3


def test_avoid_hues():
    green = pal.from_oklch(0.7, 0.15, 150.0)
    out = pal.to_oklch(pal.avoid_hues(green, [[140, 230]]))
    assert hue_dist(out[2], 140.0) < 0.5                          # nearer edge
    cyan = pal.from_oklch(0.7, 0.1, 220.0)
    assert hue_dist(pal.to_oklch(pal.avoid_hues(cyan, [[140, 230]]))[2], 230.0) < 0.5
    red = pal.from_oklch(0.6, 0.15, 30.0)
    assert pal.avoid_hues(red, [[140, 230]]) == red               # outside: untouched
    assert pal.avoid_hues((0.5, 0.5, 0.5), [[0, 360]]) == (0.5, 0.5, 0.5)   # greys are left alone
    pink = pal.from_oklch(0.6, 0.15, 2.0)                         # a range that wraps through 0: 350 is nearer
    assert hue_dist(pal.to_oklch(pal.avoid_hues(pink, [[350, 20]]))[2], 350.0) < 0.5
    assert pal.avoid_hues(red, [["bad"], None, [1]]) == red       # junk ranges are skipped


# -- roles ------------------------------------------------------------------------------------------------------------
def test_roles_from_rules():
    cols = [pal.from_oklch(0.3, 0.1, 30), pal.from_oklch(0.5, 0.12, 40), pal.from_oklch(0.6, 0.2, 250),
            pal.from_oklch(0.9, 0.05, 90), pal.from_oklch(0.55, 0.1, 50)]
    r = pal.roles_from(cols)
    assert r["base"]["hex"] == pal.hex_of(cols[4])                # median L (0.55)
    assert r["highlight"]["hex"] == pal.hex_of(cols[3])           # the lightest
    assert r["accent"]["hex"] == pal.hex_of(cols[2])              # the most colourful far (>= 60°) from base
    for role in contract.PALETTE_ROLES:
        assert set(r[role]) == {"hex", "oklch"} and len(r[role]["oklch"]) == 3


def test_roles_from_never_takes_a_near_black_as_accent():
    # ember's samples: the only colour >= 60° from its base is near black (its hue is noise)
    cols = [pal.rgb_of(h) for h in ("#ff8f35", "#a7430b", "#180000", "#181e1c", "#a77840")]
    r = pal.roles_from(cols)
    assert r["accent"]["oklch"][1] > 0.1
    assert pal.roles_from([]) == pal.roles_from([pal.WHITE_RGB])


def test_catalog_roles_for_every_palette():
    cat = pal.Catalog()
    cat.poll()
    assert cat.error is None and cat.source == "file" and len(cat.names()) >= 5
    for n in cat.names():
        r = cat.roles(n)
        Ls = [pal.to_oklch(c)[0] for c in cat.colours(n, 5)]
        assert r["highlight"]["oklch"][0] == pytest.approx(max(Ls), abs=1e-3)
        ch = cat.choice(n)
        assert ch["name"] == n and len(ch["swatch"]) == 5 and all(s.startswith("#") for s in ch["swatch"])
    with pytest.raises(LookupError):
        cat.choice("no-such-palette")


# -- the catalog ------------------------------------------------------------------------------------------------------
def test_catalog_loads_by_file_path_not_as_a_package():
    before = set(sys.modules)
    cat = pal.Catalog()
    assert cat.poll()
    new = set(sys.modules) - before
    assert not any(m.startswith("stream_service") for m in new)
    assert "_show_palettes_catalog" not in sys.modules


def test_catalog_hot_reload_and_bad_file(tmp_path):
    p = tmp_path / "palettes.py"
    shutil.copy(contract.PALETTES_FILE, p)
    cat = pal.Catalog(p)
    assert cat.poll() and not cat.poll()                          # unchanged: no reload
    names = cat.names()
    src = p.read_text()
    p.write_text(src.replace('"mono": {"label": "Mono"', '"slate": {"label": "Slate"'))
    assert cat.poll()
    assert "slate" in cat.names() and "mono" not in cat.names()
    p.write_text("this is ( not python")
    assert not cat.poll()
    assert cat.error and "slate" in cat.names()                   # the last good set stays
    p.unlink()
    assert not cat.poll() and cat.error and len(cat.names()) == len(names)


def test_catalog_api_block_wins_and_bad_block_is_noted():
    cat = pal.Catalog()
    cat.poll()
    block = {"only": {"label": "Only", "kind": "cosine",
                      "cosine": {"a": [0.5, 0.5, 0.5], "b": [0.5, 0.5, 0.5], "c": [1, 1, 1], "d": [0, 0.33, 0.67]},
                      "swatch": ["#ff0000"] * 5, "tags": {"family": "vivid", "moods": ["bright"]}}}
    assert cat.ingest(block) and cat.source == "api" and cat.names() == ["only"]
    assert cat.family("only") == "vivid" and cat.choice("only")["swatch"] == ["#ff0000"] * 5
    assert not cat.ingest({"bad": {"cosine": "nope"}})
    assert cat.api_error and cat.names() == ["only"]


def test_block_from_catalog_reads_only_the_palettes_object():
    body = (b'{"presets": {"x": {"palettes": "a word, not palettes"}}, "palettes": {"p": {"label": "P", '
            b'"cosine": {"a": [0.5,0.5,0.5], "b": [0.5,0.5,0.5], "c": [1,1,1], "d": [0,0,0]}}}, "signals": []}')
    blk = pal.block_from_catalog(body)
    assert list(blk) == ["p"]
    assert pal.block_from_catalog(b"<html>no</html>") is None
    assert pal.block_from_catalog(b'{"palettes": {"broken": 1}}') is None


# -- palettes from a picture ------------------------------------------------------------------------------------------
def _png(colours_and_shares, size=(160, 90), fmt="PNG") -> bytes:
    from PIL import Image
    im = Image.new("RGB", size)
    x0 = 0
    for rgb, share in colours_and_shares:
        w = round(size[0] * share)
        im.paste(tuple(round(255 * c) for c in rgb), (x0, 0, min(size[0], x0 + w), size[1]))
        x0 += w
    buf = io.BytesIO()
    im.save(buf, fmt)
    return buf.getvalue()


def test_from_image_two_colours_largest_first():
    a, b = (0.9, 0.3, 0.1), (0.1, 0.4, 0.8)
    png = _png([(a, 0.7), (b, 0.3)], size=(128, 72))            # the edge falls between two downscaled pixels
    got = pal.from_image(png, k=5)
    assert got is not None and len(got) == 2
    assert lab_dist(got[0], a) < 0.02 and lab_dist(got[1], b) < 0.02
    again = pal.from_image(png, k=5)
    assert again == got                                          # fixed seed: the same still, the same colours


def test_from_image_jpeg_and_letterbox():
    a, b = (0.95, 0.75, 0.2), (0.2, 0.7, 0.3)
    jpg = _png([((0, 0, 0), 0.2), (a, 0.5), (b, 0.3)], size=(640, 360), fmt="JPEG")
    got = pal.from_image(jpg, k=3)
    assert got is not None and len(got) >= 2
    assert all(pal.to_oklch(c)[0] >= pal.DARK_L for c in got)    # the black bars are left out
    assert min(lab_dist(c, a) for c in got) < 0.04 and min(lab_dist(c, b) for c in got) < 0.04
    dark = pal.from_image(_png([((0.02, 0.02, 0.03), 1.0)]), k=3)
    assert dark is not None and len(dark) == 1                    # an all-dark picture keeps its dark


def test_from_image_failure_is_none():
    assert pal.from_image(b"not an image") is None
    assert pal.from_image(b"") is None
