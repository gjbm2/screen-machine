"""Plan 3f cut-over (run once, when the owner is off the app): each workflow on its one-model endpoint, no interactive (dwell) endpoint. Minimal text edits to src/data/workflows.json; then set sm-image and sm-live to 0 and sm-krea2 3, sm-qwen 3, sm-zimage 2 in worker/endpoints.json, run worker/endpoints.py, build the frontend."""
import json, re
p = "/home/gjbm2/dev/screen-machine/src/data/workflows.json"
s = open(p).read()
EP = {"krea2-turbo.json": "jgd96xqhx0r9np", "zimage-turbo.json": "bm00rlfpej1cyj", "upscale-finish.json": "bm00rlfpej1cyj",
      "qwen21.json": "rwl6rp3wgg890o", "qwen21-turbo.json": "rwl6rp3wgg890o", "qwen21-edit.json": "rwl6rp3wgg890o",
      "qwen21-combine.json": "rwl6rp3wgg890o"}
cat = json.loads(s)
lines = s.split("\n")
# map each top-level entry's line span by its "id"
starts = [i for i, l in enumerate(lines) if re.match(r'^  \{', l)] + [len(lines)]
for a, b in zip(starts, starts[1:]):
    block = "\n".join(lines[a:b])
    m = re.search(r'^    "id": "([^"]+)"', block, re.M) or re.search(r'"id": "([^"]+)"', block)
    if not m or m.group(1) not in EP:
        continue
    for i in range(a, b):
        if lines[i].startswith('    "runpod_id": "1p4605rkgfo392"'):
            lines[i] = lines[i].replace("1p4605rkgfo392", EP[m.group(1)])
        if lines[i].startswith('    "runpod_id_live":'):
            lines[i] = None
out = "\n".join(l for l in lines if l is not None)
new = {w["id"]: (w.get("runpod_id"), w.get("runpod_id_live")) for w in json.loads(out)}
assert all(new[k] == (v, None) for k, v in EP.items()), {k: new[k] for k in EP}
open(p, "w").write(out)
print("ok", {k: new[k][0] for k in EP})
