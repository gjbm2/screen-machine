"""The engraving worker: MEI in, ink out, in its own process (Verovio and resvg hold the GIL for ~0.1-0.8 s, which
the render thread must never wait on).

    python -m stream_service.score.engrave        (stdin / stdout pipes; started by live_score.py's Engraver)

Protocol, both ways: a 4-byte big-endian length, then that many bytes of JSON.  A reply's JSON is followed by a
second length-prefixed blob: the ink coverage, w * h bytes (0 = paper, 255 = ink).
  request  {"id", "mei", "width": px, "scale": verovio scale}
  reply    {"id", "ok", "w", "h", "ms", "measures": [{"id", "x0", "x1", "staves": [[top, bottom], [top, bottom]]}],
            "error"?}
Geometry is in the bitmap's pixels: each measure's x range and its staves' top and bottom line heights, so
the scene can place a note at a pitch before it is engraved.  Verovio's SVG nests a definition-scale viewBox and a
page-margin translate inside the page; both are applied here.
"""
from __future__ import annotations

import io
import json
import re
import struct
import sys
import time
import xml.etree.ElementTree as ET

OPTIONS = {"adjustPageHeight": True, "breaks": "none", "header": "none", "footer": "none", "svgViewBox": True,
           "pageMarginLeft": 60, "pageMarginRight": 60, "pageMarginTop": 40, "pageMarginBottom": 40,
           "font": "Leipzig"}
_PATH = re.compile(r"M\s*([-\d.]+)[ ,]([-\d.]+)\s*L\s*([-\d.]+)[ ,]([-\d.]+)")
_TRANSLATE = re.compile(r"translate\(\s*([-\d.]+)[ ,]+([-\d.]+)\s*\)")
SVG = "{http://www.w3.org/2000/svg}"


def geometry(svg: str, width_px: int) -> list[dict]:
    root = ET.fromstring(svg)
    inner = next(e for e in root.iter(f"{SVG}svg") if "definition-scale" in (e.get("class") or ""))
    vw = float(inner.get("viewBox").split()[2])
    k = width_px / vw
    ox = oy = 0.0
    for g in inner.iter(f"{SVG}g"):
        if "page-margin" in (g.get("class") or ""):
            m = _TRANSLATE.search(g.get("transform") or "")
            if m:
                ox, oy = float(m.group(1)), float(m.group(2))
            break
    out = []
    for g in inner.iter(f"{SVG}g"):
        if "measure" not in (g.get("class") or "").split():
            continue
        staves, xs = [], []
        for st in g:
            if "staff" not in (st.get("class") or "").split():
                continue
            ys = []
            for p in st:
                if p.tag != f"{SVG}path":
                    continue
                m = _PATH.match(p.get("d") or "")
                if m and abs(float(m.group(2)) - float(m.group(4))) < 1e-3:
                    ys.append((float(m.group(2)) + oy) * k)
                    xs += [(float(m.group(1)) + ox) * k, (float(m.group(3)) + ox) * k]
            if len(ys) >= 2:
                staves.append([min(ys), max(ys)])            # top and bottom line
        if staves and xs:
            out.append({"id": g.get("id"), "x0": round(min(xs), 1), "x1": round(max(xs), 1),
                        "staves": [[round(y, 1) for y in s] for s in staves]})
    return out


def engrave(tk, req: dict) -> tuple[dict, bytes]:
    import resvg_py
    from PIL import Image
    t0 = time.perf_counter()
    tk.setOptions({**OPTIONS, "pageWidth": int(req.get("page_width", 3800)), "scale": int(req.get("scale", 50))})
    if not tk.loadData(req["mei"]):
        return {"id": req.get("id"), "ok": False, "error": tk.getLog()[-500:]}, b""
    svg = tk.renderToSVG(1)
    width = int(req["width"])
    png = bytes(resvg_py.svg_to_bytes(svg_string=svg, width=width, skip_system_fonts=True))
    im = Image.open(io.BytesIO(png))
    ink = im.getchannel("A") if "A" in im.getbands() else im.convert("L").point(lambda v: 255 - v)
    return ({"id": req.get("id"), "ok": True, "w": ink.width, "h": ink.height, "measures": geometry(svg, width),
             "ms": round(1000 * (time.perf_counter() - t0))}, ink.tobytes())


def _read(f) -> bytes | None:
    head = f.read(4)
    if len(head) < 4:
        return None
    n, = struct.unpack(">I", head)
    return f.read(n)


def _write(f, data: bytes) -> None:
    f.write(struct.pack(">I", len(data)))
    f.write(data)


def main() -> None:
    import verovio
    verovio.enableLog(verovio.LOG_ERROR)
    tk = verovio.toolkit()
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    while True:
        raw = _read(stdin)
        if raw is None:
            return
        try:
            meta, ink = engrave(tk, json.loads(raw))
        except Exception as e:  # report, keep serving
            meta, ink = {"ok": False, "error": f"{type(e).__name__}: {e}"}, b""
        _write(stdout, json.dumps(meta).encode())
        _write(stdout, ink)
        stdout.flush()


if __name__ == "__main__":
    main()
