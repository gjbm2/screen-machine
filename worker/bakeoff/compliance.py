"""Does a commissioned picture come out as asked? Each probe goes through the app's own refine step (routes.generate_handler
._refine: same refiner prompts, LLM chain, prompt guard and workflow choice as a real job), then the chosen graph is
rendered on its RunPod endpoint at a small size. A second run per probe skips the refiner and uses the probe's own
workflow, so a failure can be put on the refiner or on the model. Prompts, workflows and pictures are saved to --out
with a contact sheet (sheet.jpg) for looking at; nothing is published or recorded in the app's jobs.

    .venv/bin/python worker/bakeoff/compliance.py --out DIR [--refiner enrich|none] [--only 1,4] [--no-raw] [--seeds 1]
        [--probes probes.json] [--wf zimage-turbo.json]

--probes: a JSON list of [request, workflow] pairs instead of the deployment's private set; --wf: render every raw run on
that workflow.
"""
import argparse
import json
import os
import random
import sys
import textwrap
import threading
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "worker"))

# The probes (request, the workflow a person would pick for it) are the deployment's own, not in git:
# deployments/<id>/private/eval/compliance-probes.json
SIZE = {"zimage-turbo.json": (1536, 864), "krea2-turbo.json": (1536, 864), "qwen21.json": (1664, 928),
        "qwen21-turbo.json": (1664, 928)}
lock = threading.Lock()


def refine(prompt, refiner_id):
    from routes.generate_handler import _refine
    from routes.utils import build_schema_subs, resolve_runtime_value
    subs = build_schema_subs()
    system = resolve_runtime_value("refiner", refiner_id, return_key="system_prompt")
    try:
        wf, refined, final = _refine({}, prompt, None, [], system, 0, [], {"prompt": prompt}, subs)
        return wf, final, None
    except Exception as e:
        return None, None, f"{type(e).__name__}: {e}"[:200]


def render(label, wf, prompt, out):
    from run_jobs import fill, submit, wait, workers, report
    from routes.utils import _load_json_once, findfile
    cat = {w["id"]: w for w in _load_json_once("workflow", "workflows.json")}
    endpoint = cat[wf]["runpod_id"]
    graph = json.load(open(findfile(wf)))
    before = workers(endpoint)
    job = submit(endpoint, fill(graph, prompt, SIZE.get(wf), random.randrange(2**31)))
    with lock:
        return report(endpoint, job, wait(endpoint, job), before, out, label)


def case(i, prompt, own_wf, refiner, raw, out, seed_n):
    rows = []
    for k in range(seed_n):
        if refiner:
            wf, final, err = refine(prompt, refiner)
            row = {"probe": i, "variant": refiner, "request": prompt, "workflow": wf, "prompt": final}
            if err:
                row["error"] = err
            else:
                wf = wf or own_wf
                row["workflow"] = wf
                row.update({k2: v for k2, v in render(f"p{i:02d}_{refiner}_{k}", wf, final, out).items()
                            if k2 in ("image", "error", "exec_s", "status")})
            rows.append(row)
        if raw:
            row = {"probe": i, "variant": "none", "request": prompt, "workflow": own_wf, "prompt": prompt}
            row.update({k2: v for k2, v in render(f"p{i:02d}_none_{k}", own_wf, prompt, out).items()
                        if k2 in ("image", "error", "exec_s", "status")})
            rows.append(row)
    return rows


def sheet(rows, out):
    from PIL import Image, ImageDraw
    rows = [r for r in rows if r.get("image") and os.path.exists(r["image"])]
    if not rows:
        return
    W, H, T = 512, 288, 64
    cols = 4
    img = Image.new("RGB", (W * cols, (H + T) * ((len(rows) + cols - 1) // cols)), "white")
    d = ImageDraw.Draw(img)
    for n, r in enumerate(sorted(rows, key=lambda r: (r["probe"], r["variant"]))):
        x, y = (n % cols) * W, (n // cols) * (H + T)
        im = Image.open(r["image"]).convert("RGB")
        im.thumbnail((W, H))
        img.paste(im, (x, y))
        cap = f"p{r['probe']} {r['variant']} {os.path.basename(r['image'])[-14:-6]} {r['workflow'].replace('.json', '')}: {r['request']}"
        d.multiline_text((x + 4, y + H + 2), "\n".join(textwrap.wrap(cap, 80)[:4]), fill="black")
    img.save(os.path.join(out, "sheet.jpg"), quality=85)


def main():
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--refiner", default="enrich")
    ap.add_argument("--only")
    ap.add_argument("--no-raw", action="store_true")
    ap.add_argument("--seeds", type=int, default=1)
    ap.add_argument("--par", type=int, default=6)
    ap.add_argument("--probes")
    ap.add_argument("--wf")
    a = ap.parse_args()
    import smconfig
    probes = json.load(open(a.probes or smconfig.path("private", "eval", "compliance-probes.json")))
    os.makedirs(a.out, exist_ok=True)
    only = {int(x) for x in a.only.split(",")} if a.only else None
    todo = [(i, p, a.wf or w) for i, (p, w) in enumerate(probes, 1) if not only or i in only]
    with ThreadPoolExecutor(a.par) as ex:
        rows = [r for f in [ex.submit(case, i, p, w, a.refiner if a.refiner != "none" else None, not a.no_raw, a.out,
                                      a.seeds) for i, p, w in todo] for r in f.result()]
    json.dump(rows, open(os.path.join(a.out, "rows.json"), "w"), indent=1)
    sheet(rows, a.out)
    for r in rows:
        print(f"p{r['probe']:02d} {r['variant']:<7} {str(r['workflow']):<18} {r.get('error') or r.get('image', '')}")


if __name__ == "__main__":
    main()
