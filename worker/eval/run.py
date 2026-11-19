"""Standing eval of the generator: does what people commission come out as asked? Each case in suite.json goes through
the app's own refine step (routes.generate_handler._refine, the enrich refiner: same LLM chain, prompt guard and
workflow choice as a real job) and is rendered at full size on the endpoint the catalogue names. A deployment adds
its own cases in deployments/<id>/private/eval/suite.json (not in git). Nothing is published
or recorded in the app's jobs. Pictures stay outside git (default ~/.local/state/screen-machine/eval/<run>).

    .venv/bin/python worker/eval/run.py run RUN [--only id,id] [--seeds 1] [--par 3]
    .venv/bin/python worker/eval/run.py report RUN [--save-baseline]
    .venv/bin/python worker/eval/run.py route RUN [--repeats 3]

route: the triage alone (no pictures, a fraction of a penny): every case, including the "route_only" ones that sit on
a boundary between workflows, through the refiner --repeats times; prints each case's picks and how often they were
right, and saves route.json in RUN.

run: routes are checked automatically; one contact sheet per area (sheet_<area>.jpg) is for judging by eye against
each case's `must`. Write the verdicts to RUN/scores.json as {"<id>_<seed>": {"pass": true|false, "note": "..."}}.
report: a table per area (route right, pass, seconds) and every case that changed against the deployment's baseline
(private/eval/baseline.json);
--save-baseline makes this run the baseline (no pictures, just routes, verdicts and seconds).
"""
import argparse
import json
import os
import statistics
import sys
import textwrap
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..", "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "worker"))


def load_suite():
    """suite.json's cases plus the deployment's own (deployments/<id>/private/eval/suite.json, not in git)."""
    import smconfig
    suite = json.load(open(os.path.join(HERE, "suite.json")))
    mine = smconfig.path("private", "eval", "suite.json")
    if mine.exists():
        suite["cases"] += json.loads(mine.read_text())["cases"]
    return suite


def run_dir(name):
    return name if os.sep in name else os.path.expanduser(f"~/.local/state/screen-machine/eval/{name}")


def one(c, seed, out):
    from bakeoff.compliance import refine
    from run_jobs import fill, report, submit, wait, workers
    row = {"case": c["id"], "area": c["area"], "seed": seed, "request": c["request"]}
    wf, prompt, err = refine(c["request"], "enrich")
    row.update(workflow=wf, prompt=prompt, route_ok=wf in c["route"])
    if err or not wf:
        row["error"] = err or "no workflow chosen"
        return row
    from routes.utils import _load_json_once, findfile
    cat = {w["id"]: w for w in _load_json_once("workflow", "workflows.json")}
    endpoint = cat[wf]["runpod_id"]
    graph = json.load(open(findfile(wf)))
    before = workers(endpoint)
    job = submit(endpoint, fill(graph, prompt, None, 1000 + seed))
    r = report(endpoint, job, wait(endpoint, job), before, out, f"{c['id']}_{seed}")
    row.update({k: r.get(k) for k in ("image", "error", "exec_s", "delay_s", "gpu")})
    return row


def sheets(suite, rows, out):
    from PIL import Image, ImageDraw
    W, H, T, cols = 768, 432, 70, 3
    must = {c["id"]: c.get("must", "") for c in suite["cases"]}
    for area in dict.fromkeys(c["area"] for c in suite["cases"]):
        mine = [r for r in rows if r["area"] == area and r.get("image") and os.path.exists(r["image"])]
        if not mine:
            continue
        img = Image.new("RGB", (W * cols, (H + T) * ((len(mine) + cols - 1) // cols)), "white")
        d = ImageDraw.Draw(img)
        for k, r in enumerate(mine):
            x, y = (k % cols) * W, (k // cols) * (H + T)
            im = Image.open(r["image"]).convert("RGB")
            im.thumbnail((W, H))
            img.paste(im, (x, y))
            route = "route ok" if r["route_ok"] else "ROUTE WRONG"
            cap = f"{r['case']}_{r['seed']}  {r['workflow']}  {route}.  Must: {must[r['case']]}"
            d.multiline_text((x + 4, y + H + 2), "\n".join(textwrap.wrap(cap, 120)[:4]), fill="black")
        img.save(os.path.join(out, f"sheet_{area}.jpg"), quality=88)


def summary(rows, scores):
    out = {}
    for r in rows:
        key = f"{r['case']}_{r['seed']}"
        out[key] = {"area": r["area"], "workflow": r.get("workflow"), "route_ok": r.get("route_ok"),
                    "pass": (scores.get(key) or {}).get("pass"), "note": (scores.get(key) or {}).get("note"),
                    "exec_s": r.get("exec_s"), "error": r.get("error")}
    return out


def report_cmd(out, save):
    rows = json.load(open(os.path.join(out, "rows.json")))
    sp = os.path.join(out, "scores.json")
    now = summary(rows, json.load(open(sp)) if os.path.exists(sp) else {})
    import smconfig
    baseline = smconfig.path("private", "eval", "baseline.json")   # the verdicts name the private cases too
    base = json.load(open(baseline))["cases"] if baseline.exists() else {}
    print(f"{'area':<10} {'pictures':>8} {'route ok':>8} {'pass':>6} {'baseline':>8} {'median s':>8}")
    for area in dict.fromkeys(v["area"] for v in now.values()):
        mine = {k: v for k, v in now.items() if v["area"] == area}
        judged = [v for v in mine.values() if v["pass"] is not None]
        was = [base[k]["pass"] for k in mine if k in base and base[k].get("pass") is not None]
        secs = [v["exec_s"] for v in mine.values() if v.get("exec_s")]
        print(f"{area:<10} {len(mine):>8} {sum(bool(v['route_ok']) for v in mine.values()):>8} "
              f"{(str(sum(v['pass'] for v in judged)) + '/' + str(len(judged))) if judged else '-':>6} "
              f"{(str(sum(was)) + '/' + str(len(was))) if was else '-':>8} "
              f"{round(statistics.median(secs)) if secs else '-':>8}")
    for k, v in now.items():
        b = base.get(k) or {}
        if v.get("error"):
            print(f"  {k}: ERROR {v['error']}")
        elif b and (b.get("pass") != v["pass"] or b.get("workflow") != v["workflow"]):
            print(f"  {k}: was {b.get('workflow')} pass={b.get('pass')}, now {v['workflow']} pass={v['pass']} {v['note'] or ''}")
        elif not v["route_ok"]:
            print(f"  {k}: route {v['workflow']}")
    if save:
        json.dump({"run": os.path.basename(out.rstrip("/")), "cases": now}, open(baseline, "w"), indent=1)
        print("baseline saved:", baseline)


def route_cmd(suite, out, repeats, par):
    from bakeoff.compliance import refine
    todo = [(c, k) for c in suite["cases"] for k in range(repeats)]
    with ThreadPoolExecutor(par) as ex:
        picks = list(ex.map(lambda ck: (ck[0]["id"], refine(ck[0]["request"], "enrich")), todo))
    rows, right, total = [], 0, 0
    for c in suite["cases"]:
        got = [wf or f"error: {err}" for cid, (wf, _, err) in picks if cid == c["id"]]
        ok = sum(g in c["route"] for g in got)
        right, total = right + ok, total + len(got)
        rows.append({"case": c["id"], "area": c["area"], "expected": c["route"], "picks": got, "right": ok})
        print(f"{c['id']:<14} {c['area']:<10} {ok}/{len(got)}  {', '.join(dict.fromkeys(got))}"
              + ("" if ok == len(got) else f"   expected {' or '.join(c['route'])}"))
    print(f"route right {right}/{total}")
    json.dump(rows, open(os.path.join(out, "route.json"), "w"), indent=1)


def main():
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "report", "route"])
    ap.add_argument("run")
    ap.add_argument("--only")
    ap.add_argument("--seeds", type=int, default=1)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--par", type=int, default=3)
    ap.add_argument("--save-baseline", action="store_true")
    a = ap.parse_args()
    out = run_dir(a.run)
    if a.cmd == "report":
        return report_cmd(out, a.save_baseline)
    suite = load_suite()
    os.makedirs(out, exist_ok=True)
    only = set(a.only.split(",")) if a.only else None
    if a.cmd == "route":
        return route_cmd(suite, out, a.repeats, max(a.par, 6))
    todo = [(c, s) for c in suite["cases"] if (not only or c["id"] in only) and not c.get("route_only")
            for s in range(a.seeds)]
    with ThreadPoolExecutor(a.par) as ex:
        rows = list(ex.map(lambda cs: one(*cs, out), todo))
    path = os.path.join(out, "rows.json")
    if only and os.path.exists(path):
        keep = {(r["case"], r["seed"]) for r in rows}
        rows = [r for r in json.load(open(path)) if (r["case"], r["seed"]) not in keep] + rows
    json.dump(rows, open(path, "w"), indent=1)
    sheets(suite, rows, out)
    for r in rows:
        print(f"{r['case']}_{r['seed']:<3} {str(r.get('workflow')):<18} {'ok   ' if r.get('route_ok') else 'ROUTE'} "
              f"{r.get('error') or r.get('exec_s')}")


if __name__ == "__main__":
    main()
