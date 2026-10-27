"""The type lab: a page for trying bitmap fonts on the tag's card, rendered by the tag's own renderer.

GET /dev/type is the page. It lists the fonts in dot_service/fonts, the library (~/.local/share/screen-machine/
dot-fonts, fonts to try; not in git) and Windows' classic bitmap fonts (/mnt/c, used in place, never copied), and
re-renders the card as the controls change (POST /api/v1/preview -> PNG, exactly what the tag would get).
"Use on the tag" saves the type to config/room/dot.json (types); library fonts it uses are copied into
dot_service/fonts first, so the repo has them.
"""

from __future__ import annotations

import dataclasses
import io
import json
import math
import re
import shutil
from functools import reduce
from pathlib import Path
from typing import Any, Optional

import freetype
from aiohttp import web
from PIL import Image

from . import render as R

WINDOWS_FONTS = Path("/mnt/c/Windows/Fonts")
WINDOWS_BITMAP = ["sserife.fon", "sseriff.fon", "serife.fon", "seriff.fon", "smalle.fon", "vgasys.fon",
                  "8514sys.fon", "courf.fon"]
SAMPLES = {
    "art": dict(mode="Normal", auto=True, mic=True, theme="The Turning of the Hour",
                style="Long-exposure, motion-blur photography in the style of Harry Callahan, using high-contrast "
                      "silhouettes and light trails to suggest the movement of time"),
    "playing": dict(mode="Piano", auto=True, mic=True, ai=True, live=True,
                    title="Etude in G-flat, Op. 10 No. 5 (Black Keys)", by="Chopin", left="1:12 left",
                    progress=0.4, scene="Apollonian foam", feel="Dusk · elegant, dreamy · 86 bpm · F major"),
    "hearing": dict(mode="Party", mic=True, ai=True, live=True, changing_from="Normal", hearing="Music",
                    scene="Kaleido / Ink bloom", feel="Ember · euphoric, playful · 124 bpm",
                    ai_heard="Driving disco-funk groove, bright brass stabs, crowd energy rising"),
}


def _native(face: freetype.Face) -> list[int]:
    """The sizes a font is drawn on a pixel grid at: its bitmap strikes, else the outline grid's pixel size and
    the size in its name ("Jersey 20"), when they differ."""
    named = [int(m) for m in re.findall(r"(\d+)", face.family_name.decode(errors="replace")) if 5 <= int(m) <= 40]
    if not face.is_scalable:
        return sorted({(s.y_ppem >> 6) or s.height for s in face.available_sizes})
    xs: list[int] = []
    for ch in "HAMxgo":
        try:
            face.load_char(ch, freetype.FT_LOAD_NO_SCALE)
        except freetype.FT_Exception:
            continue
        xs += [abs(int(v)) for pt in face.glyph.outline.points for v in pt]
    g = reduce(math.gcd, [x for x in xs if x], 0)
    grid = []
    if g:
        px = face.units_per_EM / g
        if 5 <= px <= 40 and abs(px - round(px)) < 0.05:
            grid = [round(px)]
    strikes = [(st.y_ppem >> 6) or st.height for st in face.available_sizes]   # outlines with bitmap strikes
    if grid and named and abs(grid[0] - named[0]) <= 1:
        named = []
    return sorted(set(named[:1] + grid + strikes))


def library() -> list[dict]:
    """Every font the lab can use: {id, name, where, sizes (native px), index}."""
    out = []
    for where, folder in (("tag", R.FONTS), ("library", R.LIBRARY)):
        for p in sorted(folder.glob("*")) if folder.exists() else []:
            if p.suffix.lower() not in (".ttf", ".otf", ".otb", ".fon"):
                continue
            try:
                f = freetype.Face(str(p))
            except freetype.FT_Exception:
                continue
            out.append({"id": p.name, "name": f"{f.family_name.decode(errors='replace')} "
                        f"{f.style_name.decode(errors='replace')}".replace(" Regular", ""),
                        "where": where, "sizes": _native(f)})
    for name in WINDOWS_BITMAP:
        p = WINDOWS_FONTS / name
        if not p.exists():
            continue
        try:
            n = freetype.Face(str(p)).num_faces
            for i in range(n):
                f = freetype.Face(str(p), i)
                out.append({"id": f"{p}#{i}", "name": f"{f.family_name.decode(errors='replace')} "
                            f"{f.style_name.decode(errors='replace')}".replace(" Regular", ""),
                            "where": "windows", "sizes": _native(f)})
        except freetype.FT_Exception:
            continue
    seen, uniq = set(), []
    for e in out:   # the tag's own copies win over the library's
        if e["where"] == "library" and e["id"] in seen:
            continue
        seen.add(e["id"])
        uniq.append(e)
    return uniq


def _png(img: Image.Image) -> web.Response:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return web.Response(body=buf.getvalue(), content_type="image/png", headers={"Cache-Control": "no-store"})


def _card(base: Optional[R.Card], body: dict) -> R.Card:
    sample = body.get("sample") or "live"
    if sample in SAMPLES or base is None:
        fields = dict(room="Drawing room", hint="Say 'Computer, use AI' to interact",
                      qr="https://screen.finsburybankforsavings.com", stamp="20:58",
                      **SAMPLES.get(sample, SAMPLES["art"]))
        card = R.Card(**fields)
    else:
        card = dataclasses.replace(base, stamp=base.stamp or "20:58")
    edits = {k: v for k, v in (body.get("card") or {}).items() if k in R.Card.__dataclass_fields__ and v != ""}
    return dataclasses.replace(card, **edits)


def specimen(spec: dict) -> Image.Image:
    """The role's font: the alphabet, figures and a sample, regular and bold."""
    f = R._role(spec, R._fonts()[1])
    lines = ["ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz", "0123456789 · → … ' \" & ( ) / : , .",
             "The Turning of the Hour  AI Il1|"]
    h = (f.line + 2) * (len(lines) * 2) + 6
    img = Image.new("1", (R.W, h), 1)
    y = 3
    for bold in (False, True):
        for ln in lines:
            f.draw(img, (4, y), ln, bold=bold)
            y += f.line + 2
    return img


def save_types(types: dict, config: Path) -> dict:
    """Put types in config (top-level "types"); library fonts used are copied into dot_service/fonts."""
    for spec in types.values():
        for key in ("font", "bold"):
            name = spec.get(key)
            if not name or name in ("double", "none") or Path(name.partition("#")[0]).is_absolute():
                continue
            if not (R.FONTS / name).exists() and (R.LIBRARY / name).exists():
                shutil.copy2(R.LIBRARY / name, R.FONTS / name)
    cfg = json.loads(config.read_text())
    cfg["types"] = types
    config.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
    return types


def routes(app: web.Application, svc: Any, config: Path) -> None:
    async def page(_: web.Request) -> web.Response:
        return web.Response(text=PAGE, content_type="text/html", headers={"Cache-Control": "no-store"})

    async def fonts(_: web.Request) -> web.Response:
        return web.json_response({"fonts": library(), "defaults": R.DEFAULT_TYPES, "current": svc.types or {},
                                  "samples": list(SAMPLES)})

    async def preview(request: web.Request) -> web.Response:
        body = await request.json()
        tag = next(iter(svc.tags.values()), None)
        try:
            return _png(R.render(_card(tag.card if tag else None, body), body.get("types")))
        except (FileNotFoundError, freetype.FT_Exception, KeyError, ValueError) as e:
            return web.json_response({"error": str(e)}, status=400)

    async def spec_img(request: web.Request) -> web.Response:
        body = await request.json()
        try:
            return _png(specimen(body["spec"]))
        except (FileNotFoundError, freetype.FT_Exception, KeyError, ValueError) as e:
            return web.json_response({"error": str(e)}, status=400)

    async def apply(request: web.Request) -> web.Response:
        body = await request.json()
        types = body.get("types") or {}
        try:
            R._fonts(types)                       # it must build before it goes in config
        except (FileNotFoundError, freetype.FT_Exception, KeyError, ValueError) as e:
            return web.json_response({"error": str(e)}, status=400)
        save_types(types, config)
        return web.json_response({"saved": types})

    app.router.add_get("/dev/type", page)
    app.router.add_get("/api/v1/fonts", fonts)
    app.router.add_post("/api/v1/preview", preview)
    app.router.add_post("/api/v1/specimen", spec_img)
    app.router.add_post("/api/v1/types", apply)


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tag type lab</title>
<style>
:root{--bg:#f7f3ec;--card:#fffdf9;--ink:#2b2620;--muted:#7a7066;--line:#e3dbcf;--accent:#8a5a2b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.4 system-ui,sans-serif}
main{max-width:1500px;margin:0 auto;padding:16px;display:grid;gap:16px;grid-template-columns:minmax(0,1fr)}
.preview{position:sticky;top:0;z-index:2;box-shadow:0 6px 16px -10px rgba(60,40,20,.35)}
.controls{display:grid;gap:16px;grid-template-columns:repeat(auto-fit,minmax(260px,1fr))}#roles{display:contents}
h1{font-size:18px;margin:0 0 4px}p.sub{margin:0 0 12px;color:var(--muted)}
section{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin:0 0 8px}
label{display:block;font-size:12px;color:var(--muted);margin:6px 0 2px}
select,input{width:100%;padding:6px 8px;border:1px solid var(--line);border-radius:8px;background:#fff;font:inherit}
.row{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px}.row2{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.check{display:flex;gap:6px;align-items:center;margin-top:8px;font-size:13px;color:var(--ink)}.check input{width:auto}
.preview{display:flex;flex-direction:column;gap:12px;align-items:flex-start}
.screen{background:#fff;border:10px solid #e9e6e1;border-radius:10px;box-shadow:0 1px 0 #d8d2c8;line-height:0;display:inline-block;max-width:100%}
.spec{overflow:auto}
.screen img{image-rendering:pixelated;display:block}
.bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
button{padding:8px 14px;border-radius:999px;border:1px solid var(--accent);background:var(--accent);color:#fff;font:inherit;cursor:pointer}
button.ghost{background:transparent;color:var(--accent)}
.note{color:var(--muted);font-size:12px}.err{color:#a3311b;font-size:12px}
.spec img{image-rendering:pixelated;display:block;max-width:100%}
</style></head><body><main>
<div><h1>Tag type lab</h1><p class="sub">Pick fonts for the e-ink tag; the card re-renders with the tag's own renderer, pixel for pixel.</p></div>
<section class="preview"><h2>The tag</h2><div class="screen"><img id="out" alt="The tag's card"></div>
<div class="bar" style="margin-top:10px"><button id="apply">Use on the tag</button><button class="ghost" id="reset">Back to the tag's type</button>
<span class="note" id="saved"></span><span class="note" id="status">Native size in brackets; other sizes are outlines forced onto the grid and rarely look right.</span></div></section>
<div class="controls">
<section><h2>Card</h2>
<div class="row2"><div><label>Content</label><select id="sample"></select></div>
<div><label>Zoom</label><select id="zoom"><option value="fit" selected>Fit</option><option>1</option><option>2</option><option>3</option><option>4</option></select></div></div>
<label>Headline text</label><input id="headline" placeholder="(from the content)">
<label>Mode</label><input id="mode" placeholder="(from the content)">
</section>
<div id="roles"></div>
</div>
<section class="spec"><h2>Specimen <select id="specrole" style="width:auto;display:inline-block;margin-left:8px"><option value="head">Headline</option><option value="text">Text</option><option value="ui">Mode &amp; bottom line</option></select></h2><img id="spec" alt="Specimen"></section>
</main>
<script>
const ROLES={head:"Headline",text:"Text, labels, switches",ui:"Mode & bottom line"};
let FONTS=[],TYPES={},timer=null;
const $=id=>document.getElementById(id);
function opt(f){const s=f.sizes.length?` [${f.sizes.join(", ")} px]`:"";return `<option value="${f.id}">${f.name}${s} — ${f.where}</option>`}
function fontOptions(){const g={tag:"On the tag now",library:"Library",windows:"Windows bitmap fonts (this PC only)"};
 return Object.keys(g).map(w=>{const fs=FONTS.filter(f=>f.where===w);return fs.length?`<optgroup label="${g[w]}">${fs.map(opt).join("")}</optgroup>`:""}).join("")}
function build(){$("roles").innerHTML=Object.entries(ROLES).map(([r,label])=>`<section><h2>${label}</h2>
<label>Font</label><select id="${r}-font">${fontOptions()}</select>
<div class="row"><div><label>Size px</label><input id="${r}-px" type="number" min="5" max="64"></div>
<div><label>Line px</label><input id="${r}-line" type="number" min="0" max="80" placeholder="auto"></div>
<div><label>Scale</label><select id="${r}-scale"><option>1</option><option>2</option><option>3</option></select></div></div>
<label>Bold</label><select id="${r}-bold"><option value="double">Pixel-doubled</option><option value="none">None (regular)</option><optgroup label="A bold cut">${FONTS.map(f=>`<option value="${f.id}">${f.name}</option>`).join("")}</optgroup></select>
<label class="check"><input type="checkbox" id="${r}-serif_i"> Serifed capital I (“AI” not “Al”)</label></section>`).join("");
 Object.keys(ROLES).forEach(r=>{$(r+"-font").onchange=()=>{const f=FONTS.find(x=>x.id===$(r+"-font").value);if(f&&f.sizes.length)$(r+"-px").value=f.sizes[0];$(r+"-line").value="";go()};
  ["px","line","scale","bold","serif_i"].forEach(k=>$(r+"-"+k).oninput=go)})}
function setTypes(t){Object.entries(t).forEach(([r,s])=>{if(!$(r+"-font"))return;$(r+"-font").value=s.font;$(r+"-px").value=s.px;$(r+"-line").value=s.line||"";
 $(r+"-scale").value=s.scale||1;$(r+"-bold").value=s.bold||"double";$(r+"-serif_i").checked=!!s.serif_i})}
function types(){const t={};Object.keys(ROLES).forEach(r=>{const s={font:$(r+"-font").value,px:+$(r+"-px").value,bold:$(r+"-bold").value};
 if($(r+"-line").value)s.line=+$(r+"-line").value;if(+$(r+"-scale").value>1)s.scale=+$(r+"-scale").value;if($(r+"-serif_i").checked)s.serif_i=true;t[r]=s});return t}
function cardEdits(){const c={},h=$("headline").value,m=$("mode").value,s=$("sample").value;
 if(h)c[{art:"theme",live:"theme",playing:"title",hearing:"hearing"}[s]]=h;if(m)c.mode=m;return c}
function go(){clearTimeout(timer);timer=setTimeout(draw,120)}
async function img(url,body,el){const r=await fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
 if(!r.ok){$("status").innerHTML=`<span class="err">${(await r.json()).error}</span>`;return}
 const b=await r.blob(),u=URL.createObjectURL(b);el.onload=()=>{size(el);URL.revokeObjectURL(u)};el.src=u}
function zoomFor(el){const v=$("zoom").value;if(v!=="fit")return +v;
 const room=el.closest("section").clientWidth-44,w=el.naturalWidth||296,h=el.naturalHeight||152;
 const tall=el.id==="out"?Math.floor(window.innerHeight*0.5/h):99;return Math.max(1,Math.min(Math.floor(room/w),tall))}
function size(el){if(el.naturalWidth)el.style.width=el.naturalWidth*zoomFor(el)+"px"}
async function draw(){$("status").textContent="";const t=types();
 await img("/api/v1/preview",{types:t,sample:$("sample").value,card:cardEdits()},$("out"));
 await img("/api/v1/specimen",{spec:t[$("specrole").value]},$("spec"))}
async function init(){const d=await (await fetch("/api/v1/fonts")).json();FONTS=d.fonts;build();
 $("sample").innerHTML=`<option value="live">What the tag shows now</option>`+d.samples.map(s=>`<option>${s}</option>`).join("");
 const cur={...d.defaults,...d.current};setTypes(cur);window.TAG=cur;
 ["sample","zoom","headline","mode","specrole"].forEach(id=>$(id).oninput=go);
 $("reset").onclick=()=>{setTypes(window.TAG);go()};
 $("apply").onclick=async()=>{const r=await fetch("/api/v1/types",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({types:types()})});
  const j=await r.json();$("saved").textContent=r.ok?"Saved — the tag updates within a few seconds.":j.error;if(r.ok)window.TAG=types()};draw()}
window.addEventListener("resize",()=>{size($("out"));size($("spec"))});
init();
</script></body></html>
"""
