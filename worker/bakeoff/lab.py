"""Render a lab bake-off (plan step 3g) on the sm-lab endpoint: every commission on each of its lanes' graphs
(routes/workflows/lab/), N seeds. For judging by eye, each commission gets a contact sheet with the pictures shuffled
and numbered; key.json says which number is which lane. Resumable: pictures already in rows.json are skipped.

    .venv/bin/python worker/bakeoff/lab.py worker/bakeoff/lab1.json --out DIR [--endpoint ID] [--only id,id] [--seeds N]
"""
import argparse
import json
import os
import random
import sys
import textwrap

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, os.path.join(ROOT, "worker"))


def sheets(spec, rows, out):
    from PIL import Image, ImageDraw
    key = {}
    W, H, cols = 768, 432, 4
    for c in spec["commissions"]:
        mine = [r for r in rows if r["commission"] == c["id"] and r.get("image") and os.path.exists(r["image"])]
        if not mine:
            continue
        random.Random(c["id"]).shuffle(mine)
        n = (len(mine) + cols - 1) // cols
        img = Image.new("RGB", (W * cols, 40 + (H + 30) * n), "white")
        d = ImageDraw.Draw(img)
        d.text((6, 6), "\n".join(textwrap.wrap(c["request"], 200)), fill="black")
        for k, r in enumerate(mine, 1):
            x, y = ((k - 1) % cols) * W, 40 + ((k - 1) // cols) * (H + 30)
            im = Image.open(r["image"]).convert("RGB")
            im.thumbnail((W, H))
            img.paste(im, (x, y))
            d.text((x + 6, y + H + 6), f"#{k}", fill="black")
            key[f"{c['id']}#{k}"] = {"lane": r["lane"], "seed": r["seed"], "image": r["image"]}
        img.save(os.path.join(out, f"sheet_{c['id']}.jpg"), quality=88)
    json.dump(key, open(os.path.join(out, "key.json"), "w"), indent=1)


def main():
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
    from run_jobs import fill, report, submit, wait, workers
    ap = argparse.ArgumentParser()
    ap.add_argument("spec")
    ap.add_argument("--out", required=True)
    ap.add_argument("--endpoint", default=json.load(open(os.path.join(ROOT, "worker/endpoints.json")))["endpoints"]["sm-lab"]["id"])
    ap.add_argument("--only")
    ap.add_argument("--seeds", type=int)
    a = ap.parse_args()
    spec = json.load(open(a.spec))
    os.makedirs(a.out, exist_ok=True)
    rows_path = os.path.join(a.out, "rows.json")
    rows = json.load(open(rows_path)) if os.path.exists(rows_path) else []
    done = {(r["commission"], r["lane"], r["seed"]) for r in rows if r.get("image")}
    only = set(a.only.split(",")) if a.only else None
    for c in spec["commissions"]:
        if only and c["id"] not in only:
            continue
        for lane in spec["lanes"][c["kind"]]:
            graph = json.load(open(os.path.join(ROOT, "routes/workflows/lab", lane + ".json")))
            prompt = c["tags"] if lane == "lab-noobai" else c["prompt"]
            for s in range(a.seeds or spec["seeds"]):
                if (c["id"], lane, s) in done:
                    continue
                before = workers(a.endpoint)
                job = submit(a.endpoint, fill(graph, prompt, None, 1000 + s))
                r = report(a.endpoint, job, wait(a.endpoint, job), before, a.out, f"{c['id']}_{lane}_{s}")
                rows = [x for x in rows if (x["commission"], x["lane"], x["seed"]) != (c["id"], lane, s)]
                rows.append({"commission": c["id"], "lane": lane, "seed": s,
                             **{k: r.get(k) for k in ("image", "error", "exec_s", "delay_s", "gpu")}})
                json.dump(rows, open(rows_path, "w"), indent=1)
                print(c["id"], lane, s, r.get("error") or f"{r.get('exec_s')} s", flush=True)
    sheets(spec, rows, a.out)


if __name__ == "__main__":
    main()
