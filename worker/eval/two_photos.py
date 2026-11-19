"""Eval of edits of one or two uploaded pictures (owner, 19 Nov 2026): "add this person into the painting", "replace that
woman with this one", "render this in the style of this painting", "put these two together", and the rest in
two_photos.json. Two things are checked automatically: that the adapt refiner names the right canvas (the picture the
result is built on: A = first uploaded, B = second, none = a new scene), and that the result has the canvas's shape
(or the new scene's). The pictures are judged by eye on the contact sheet against each case's `must`.

Everything goes through the app's own path (routes.generate_handler.handle_image_generation: the adapt refiner, the
canvas put first, Qwen 2.1 combine); nothing is published, and jobs go to a store in the run folder, not the app's.
Pictures stay outside git (~/.local/state/screen-machine/eval/two-photos/).

    .venv/bin/python worker/eval/two_photos.py inputs            # make the input pictures once (~6 x 2p)
    .venv/bin/python worker/eval/two_photos.py route RUN [--repeats 3]   # the refiner alone, a fraction of a penny
    .venv/bin/python worker/eval/two_photos.py run RUN [--only id,id] [--par 3]  # render, check shapes, contact sheet
"""
import argparse
import base64
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("SM_DEPLOYMENT", "finsbury")

BASE = os.path.expanduser("~/.local/state/screen-machine/eval/two-photos")
SUITE = json.load(open(os.path.join(HERE, "two_photos.json")))
SHAPES = {"landscape": 16 / 9, "portrait": 9 / 16, "square": 1.0}


def tracker_in(folder):
    from routes.jobs import set_tracker
    from routes.jobs.store import JobStore
    from routes.jobs.tracker import JobTracker
    os.makedirs(folder, exist_ok=True)
    set_tracker(JobTracker(store=JobStore(os.path.join(folder, "jobs.db")), progress_interval=1, persist_interval=2,
                           cancel_remote=lambda ep, rid: True))


def fetch(source):
    from PIL import Image
    from routes.result_source import read_bytes
    return Image.open(BytesIO(read_bytes(source))).convert("RGB")


def make_inputs(args):
    import routes.generate as generate
    folder = os.path.join(BASE, "inputs")
    tracker_in(folder)

    def one(item):
        name, spec = item
        path = os.path.join(folder, f"{name}.jpg")
        if os.path.exists(path):
            return f"{name}: kept"
        out = generate.start(prompt=spec["prompt"], workflow=spec["workflow"], width=spec["size"][0],
                             height=spec["size"][1], poll_interval=1)
        img = fetch(out["message"])
        img.save(path, quality=93)
        return f"{name}: {img.size} GBP {out.get('generation_cost_gbp')}"
    with ThreadPoolExecutor(3) as pool:
        for line in pool.map(one, SUITE["inputs"].items()):
            print(line)


def upload(name):
    """An input as the generator page's upload reaches the app (routes.utils.encode_image_uploads)."""
    from werkzeug.datastructures import FileStorage
    from routes.utils import encode_image_uploads
    with open(os.path.join(BASE, "inputs", f"{name}.jpg"), "rb") as f:
        return encode_image_uploads([FileStorage(stream=BytesIO(f.read()), filename=f"{name}.jpg")])[0]


def refine(case):
    """The adapt refiner's answer for a case, as handle_image_generation asks it."""
    from routes.generate_handler import _refine
    from routes.utils import build_schema_subs, resolve_runtime_value
    images = [upload(n) for n in case["uploads"]]
    system = resolve_runtime_value("refiner", "adapt", return_key="system_prompt")
    from routes.generate_handler import number_pictures
    wf, refined, final, framing = _refine({}, case["request"], None, images, system, len(images), images,
                                          {"prompt": case["request"]}, build_schema_subs())
    return wf, final, number_pictures(final, framing), framing


def cases(only):
    return [c for c in SUITE["cases"] if not only or c["id"] in only.split(",")]


def route(args):
    folder = os.path.join(BASE, args.run)
    os.makedirs(folder, exist_ok=True)
    rows = []
    jobs = [(c, i) for c in cases(args.only) for i in range(args.repeats)]
    with ThreadPoolExecutor(args.par) as pool:
        for c, (wf, raw, prompt, framing) in zip([j[0] for j in jobs], pool.map(lambda j: refine(j[0]), jobs)):
            ok = framing.get("canvas", "A") == c["canvas"]
            # the refiner names the pictures by letter only; the app numbers them (generate_handler.number_pictures)
            labels_ok = not re.search(r"\b(image|picture)\s+[12]\b", raw or "") and (
                len(c["uploads"]) < 2 or ("image A" in raw and "image B" in raw))
            rows.append({"case": c["id"], "canvas": framing.get("canvas"), "want": c["canvas"], "ok": ok,
                         "labels_ok": bool(labels_ok), "shape": framing.get("shape"), "workflow": wf,
                         "raw": raw, "prompt": prompt})
    json.dump(rows, open(os.path.join(folder, "route.json"), "w"), indent=2)
    for c in cases(args.only):
        mine = [r for r in rows if r["case"] == c["id"]]
        print(f"{c['id']:24} canvas {sum(r['ok'] for r in mine)}/{len(mine)} right (want {c['canvas']}; got "
              f"{','.join(str(r['canvas']) for r in mine)})  labels {sum(r['labels_ok'] for r in mine)}/{len(mine)}"
              f"  shape {','.join(str(r['shape']) for r in mine)}")
    print(f"canvas right: {sum(r['ok'] for r in rows)}/{len(rows)}; pictures named by letter: "
          f"{sum(r['labels_ok'] for r in rows)}/{len(rows)}")


def expected_shape(case):
    if case["shape"] in SHAPES:
        return SHAPES[case["shape"]]
    w, h = SUITE["inputs"][case["shape"]]["size"]
    return w / h


def run_case(case, folder):
    from routes.generate_handler import handle_image_generation
    images = [upload(n) for n in case["uploads"]]
    results = handle_image_generation({"data": {"prompt": case["request"], "images": images, "refiner": "adapt"}},
                                      wait=True, source="api", poll_interval=1)
    row = {"case": case["id"], "request": case["request"], "must": case["must"]}
    if not results:
        row["error"] = "no picture"
        return row
    out = results[0]
    img = fetch(out["message"])
    img.save(os.path.join(folder, f"{case['id']}.jpg"), quality=92)
    shape = img.width / img.height
    row.update(size=img.size, shape_ok=abs(shape / expected_shape(case) - 1) <= 0.03,
               prompt=out.get("actual_final_prompt") or out.get("prompt"),
               cost=out.get("generation_cost_gbp"), seconds=out.get("generation_time_seconds"))
    return row


def sheet(rows, folder):
    """One row per case: the uploads (A, B) and the result, with the request and what it must show."""
    from PIL import Image, ImageDraw
    H, pad, text_h = 300, 10, 54
    lines = []
    for r in rows:
        case = next(c for c in SUITE["cases"] if c["id"] == r["case"])
        tiles = [Image.open(os.path.join(BASE, "inputs", f"{n}.jpg")) for n in case["uploads"]]
        result = os.path.join(folder, f"{r['case']}.jpg")
        tiles.append(Image.open(result) if os.path.exists(result) else Image.new("RGB", (H, H), "black"))
        tiles = [t.resize((round(t.width * H / t.height), H)) for t in tiles]
        width = sum(t.width for t in tiles) + pad * (len(tiles) + 2) + 30
        line = Image.new("RGB", (max(width, 1400), H + text_h + pad), "white")
        d = ImageDraw.Draw(line)
        x = pad
        for i, t in enumerate(tiles):
            if i == len(tiles) - 1:
                d.text((x, H // 2), "->", fill="black")
                x += 30
            line.paste(t, (x, text_h))
            if i < len(tiles) - 1:
                d.text((x + 4, text_h + 4), "AB"[i], fill="yellow")
            x += t.width + pad
        d.text((pad, 4), f"{r['case']}: \"{r['request']}\"  shape {'OK' if r.get('shape_ok') else 'WRONG'} "
                         f"{r.get('size')}", fill="black")
        d.text((pad, 22), f"must: {r['must']}"[:200], fill="black")
        lines.append(line)
    W = max(l.width for l in lines)
    out = Image.new("RGB", (W, sum(l.height for l in lines)), "white")
    y = 0
    for l in lines:
        out.paste(l, (0, y))
        y += l.height
    out.save(os.path.join(folder, "sheet.jpg"), quality=88)


def run(args):
    folder = os.path.join(BASE, args.run)
    tracker_in(folder)
    todo = cases(args.only)
    with ThreadPoolExecutor(args.par) as pool:
        rows = list(pool.map(lambda c: run_case(c, folder), todo))
    json.dump(rows, open(os.path.join(folder, "run.json"), "w"), indent=2, default=str)
    for r in rows:
        print(f"{r['case']:24} {r.get('error') or ('shape OK' if r['shape_ok'] else 'shape WRONG')} {r.get('size')} "
              f"GBP {r.get('cost')}  {r.get('seconds')} s")
    print(f"shape right: {sum(bool(r.get('shape_ok')) for r in rows)}/{len(rows)}")
    sheet(rows, folder)
    print(os.path.join(folder, "sheet.jpg"))


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("inputs")
    for name in ("route", "run"):
        s = sub.add_parser(name)
        s.add_argument("run")
        s.add_argument("--only")
        s.add_argument("--par", type=int, default=3)
        if name == "route":
            s.add_argument("--repeats", type=int, default=3)
    args = p.parse_args()
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
    {"inputs": make_inputs, "route": route, "run": run}[args.cmd](args)


if __name__ == "__main__":
    main()
