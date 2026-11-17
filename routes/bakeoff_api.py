"""Blind model bake-off (docs/genai-review/PROJECT_PLAN.md, step 2).

worker/bakeoff/run.py makes every prompt on every contender and writes a round file in
~/.local/state/screen-machine/bakeoff/<round>.json; this serves the page that shows each prompt's pictures as A, B, C
in a shuffled order, takes the owner's pick for each, can put a picture on the devtest screen, and reveals the models
and costs once every prompt has a pick."""
import json
import os
import threading

from flask import Blueprint, Response, jsonify, request

bakeoff_api = Blueprint("bakeoff_api", __name__)
_lock = threading.Lock()
STATE_DIR = os.path.expanduser("~/.local/state/screen-machine/bakeoff")


def _path(round_id):
    if not round_id.replace("-", "").replace("_", "").isalnum():
        return None
    return os.path.join(STATE_DIR, f"{round_id}.json")


def load(round_id):
    p = _path(round_id)
    if not p or not os.path.exists(p):
        return None
    with open(p) as f:
        return json.load(f)


def save(round_id, data):
    p = _path(round_id)
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, p)


def _public(data):
    """The round as the page sees it: no model names or costs until every prompt has a pick."""
    items = [it for lane in data["lanes"] for it in lane["items"]]
    done = all(it["id"] in data.get("picks", {}) for it in items if it["entries"])
    out = {"round": data["round"], "revealed": done, "picks": data.get("picks", {}), "lanes": []}
    for lane in data["lanes"]:
        lo = {"lane": lane["lane"], "items": []}
        for it in lane["items"]:
            entries = []
            for e in it["entries"]:
                pub = {"key": e["key"], "image": e.get("image"), "ok": bool(e.get("image")), "error": e.get("error")}
                if done:
                    pub.update(workflow=e.get("workflow"), cost_gbp=e.get("cost_gbp"), seconds=e.get("seconds"))
                entries.append(pub)
            lo["items"].append({"id": it["id"], "prompt": it["prompt"], "entries": entries})
        out["lanes"].append(lo)
    if done:
        tally = {}
        for lane in data["lanes"]:
            for it in lane["items"]:
                pick = data["picks"].get(it["id"])
                for e in it["entries"]:
                    t = tally.setdefault(lane["lane"], {}).setdefault(e["workflow"], {"wins": 0, "shown": 0, "cost_gbp": 0.0})
                    t["shown"] += 1
                    t["cost_gbp"] += e.get("cost_gbp") or 0
                    if pick == e["key"]:
                        t["wins"] += 1
        out["tally"] = tally
    return out


@bakeoff_api.route("/bakeoff/<round_id>/data", methods=["GET"])
def round_data(round_id):
    data = load(round_id)
    return (jsonify(_public(data)), 200) if data else (jsonify({"error": "no such round"}), 404)


@bakeoff_api.route("/bakeoff/<round_id>/pick", methods=["POST"])
def pick(round_id):
    body = request.get_json(silent=True) or {}
    with _lock:
        data = load(round_id)
        if not data:
            return jsonify({"error": "no such round"}), 404
        data.setdefault("picks", {})[str(body.get("item"))] = str(body.get("key"))
        save(round_id, data)
    return jsonify(_public(data))


@bakeoff_api.route("/bakeoff/<round_id>/show", methods=["POST"])
def show(round_id):
    """Put one picture on the devtest screen (bucket-to-bucket publish of the file the run saved there)."""
    body = request.get_json(silent=True) or {}
    data = load(round_id)
    if not data:
        return jsonify({"error": "no such round"}), 404
    entry = next((e for lane in data["lanes"] for it in lane["items"] if it["id"] == body.get("item")
                  for e in it["entries"] if e["key"] == body.get("key")), None)
    if not entry or not entry.get("filename"):
        return jsonify({"error": "no such picture"}), 404
    from routes.publisher import publish_to_destination
    from routes.bucket_api import bucket_path
    res = publish_to_destination(source=bucket_path("devtest") / entry["filename"], publish_destination_id="devtest",
                                 skip_bucket=True, cross_bucket_mode=False)
    return jsonify({"ok": bool(res.get("success")), "error": res.get("error")})


@bakeoff_api.route("/bakeoff/<round_id>", methods=["GET"])
def page(round_id):
    return Response(PAGE.replace("__ROUND__", json.dumps(round_id)), mimetype="text/html")


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Model bake-off</title>
<style>
:root{--bg:#faf7f2;--card:#fff;--ink:#2b2622;--muted:#7a7068;--line:#e7e0d6;--accent:#0f6b63;--pick:#e6f3f1}
@media (prefers-color-scheme:dark){:root{--bg:#1c1a18;--card:#262320;--ink:#efe9e1;--muted:#a59b90;--line:#3a3530;--accent:#5cc2b5;--pick:#1f3532}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1100px;margin:0 auto;padding:16px}h1{font-size:22px;margin:8px 0 4px}h2{font-size:18px;margin:28px 0 8px}
.muted{color:var(--muted)}.item{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:12px;margin:12px 0}
.prompt{font-size:14px;color:var(--muted);margin:0 0 10px}.grid{display:grid;gap:10px;grid-template-columns:repeat(auto-fit,minmax(260px,1fr))}
figure{margin:0;border:2px solid transparent;border-radius:12px;padding:4px}figure.picked{border-color:var(--accent);background:var(--pick)}
img{width:100%;border-radius:8px;display:block;cursor:zoom-in;background:var(--line);aspect-ratio:16/9;object-fit:cover}
figcaption{display:flex;gap:8px;align-items:center;justify-content:space-between;margin-top:6px;flex-wrap:wrap}
button{font:inherit;min-height:40px;border-radius:10px;border:1px solid var(--line);background:var(--card);color:var(--ink);padding:0 12px;cursor:pointer}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff}.tag{font-weight:600}
.none{margin-top:8px}.reveal{font-size:13px;color:var(--muted)}table{border-collapse:collapse;width:100%;font-size:14px}
td,th{border-bottom:1px solid var(--line);padding:6px 4px;text-align:left}.err{color:#b4442c;font-size:13px}
#zoom{position:fixed;inset:0;background:#000;display:none;align-items:center;justify-content:center;z-index:9}
#zoom img{width:auto;max-width:none;aspect-ratio:auto;object-fit:none;cursor:zoom-out}#zoom.on{display:flex;overflow:auto}
</style></head><body><main>
<h1>Which picture is best?</h1>
<p class="muted">Each prompt was made by two or three models, shown in a random order. Pick the best for each (or "None good").
Tap a picture to see it at full size; "On the TV" puts it on the dev test screen. The models are shown once every prompt has a pick.</p>
<div id="summary"></div><div id="lanes">Loading…</div></main>
<div id="zoom" onclick="this.className=''"><img alt=""></div>
<script>
const ROUND=__ROUND__;const api=p=>`/api/bakeoff/${ROUND}${p}`;let state=null;
async function load(){const r=await fetch(api('/data'));state=await r.json();render()}
async function pick(item,key){const r=await fetch(api('/pick'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({item,key})});state=await r.json();render()}
async function tv(item,key,btn){btn.textContent='Sending…';const r=await fetch(api('/show'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({item,key})});const j=await r.json();btn.textContent=j.ok?'On the TV ✓':'Failed'}
function zoom(src){const z=document.getElementById('zoom');z.querySelector('img').src=src;z.className='on'}
function esc(s){return String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function render(){const L=document.getElementById('lanes');let n=0,p=0;
 L.innerHTML=state.lanes.map(l=>`<h2>${esc(l.lane)}</h2>`+l.items.map(it=>{n++;const pk=state.picks[it.id];if(pk)p++;
  return `<div class="item"><p class="prompt">${esc(it.prompt)}</p><div class="grid">`+it.entries.map(e=>`<figure class="${pk===e.key?'picked':''}">
   ${e.ok?`<img loading="lazy" src="${esc(e.image)}" onclick="zoom(this.src)" alt="Picture ${e.key}">`:`<div class="err">${esc(e.error||'Not made')}</div>`}
   <figcaption><span class="tag">${e.key}${state.revealed?` · <span class="reveal">${esc((e.workflow||'').replace('.json',''))}${e.cost_gbp!=null?` · £${e.cost_gbp.toFixed(3)}`:''}${e.seconds?` · ${Math.round(e.seconds)} s`:''}</span>`:''}</span>
   <span>${e.ok?`<button onclick="tv('${it.id}','${e.key}',this)">On the TV</button> `:''}<button class="${pk===e.key?'primary':''}" onclick="pick('${it.id}','${e.key}')">${pk===e.key?'Picked':'Pick'}</button></span></figcaption></figure>`).join('')+
   `</div><div class="none"><button class="${pk==='none'?'primary':''}" onclick="pick('${it.id}','none')">None good</button></div></div>`}).join('')).join('');
 let s=`<p><b>${p} of ${n}</b> picked.</p>`;
 if(state.tally){s+='<h2>Result</h2>'+Object.entries(state.tally).map(([lane,t])=>`<p class="muted">${esc(lane)}</p><table><tr><th>Model</th><th>Wins</th><th>Shown</th><th>GPU cost</th></tr>`+
  Object.entries(t).sort((a,b)=>b[1].wins-a[1].wins).map(([w,v])=>`<tr><td>${esc(w.replace('.json',''))}</td><td>${v.wins}</td><td>${v.shown}</td><td>£${v.cost_gbp.toFixed(2)}</td></tr>`).join('')+'</table>').join('')}
 document.getElementById('summary').innerHTML=s}
load();
</script></body></html>"""
