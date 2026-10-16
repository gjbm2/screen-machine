#!/usr/bin/env python3
"""Fetch the ML worker's models (show_service/README.md Stage 4, S4.4) into ~/.cache/screen-machine/models/.

    python tools/show/fetch_models.py                 # fetch what's missing, verify it, write index.json
    python tools/show/fetch_models.py --list          # the catalogue, licences, and how to make the exported ones
    python tools/show/fetch_models.py --verify        # check every file against its pinned sha256 (no network)
    python tools/show/fetch_models.py --only ced-mini,discogs-effnet --force
    python tools/show/fetch_models.py --dir /tmp/models

Run it under ``flock /tmp/claude-1000/sm-build.lock nice -n 19``. Standard library only (urllib), like
fetch_test_music.py. Models never go into git.

Each catalogue entry pins its file (and the metadata JSON the worker reads labels from, ``meta``) by URL, sha256 and
size, checked by hand on 16 Oct 2026; a download whose checksum differs is not installed. ``facts`` is what the worker
needs to read the model (show_service/ml/models.py): its kind, input, outputs, labels, which label is a binary head's
"yes" (or a regression's value), a regression's range, and which backbone feeds a head (``via``). The worker reads the
facts from ``D/index.json``; code never names a model.

``D/index.json`` = {name: {file, sha256, bytes, license, license_url, page, source: "fetch" | "export", facts,
fetched_at, meta?, upstream?}}. ``D/LICENSES.md`` has one entry per model with its credit line. tools/show/export_onnx.py
adds the exported models (Beat This!) to the same index. Re-running is cheap: a present file with the right checksum
is skipped.

Licences (re-verified 16 Oct 2026):
- Essentia's models (Discogs-EffNet, MSD-MusiCNN and the heads): CC BY-NC-SA 4.0 (https://essentia.upf.edu/models.html:
  "All the models created by the MTG are licensed under CC BY-NC-SA 4.0"). Fine for this private install; flagged
  if the system is ever sold (SHOW_ENGINE §15).
- CED-mini: Apache-2.0 (its model card).
- Beat This!: MIT, code and published weights (its README, "License").
- Not a model, but beside them: the essentia library the worker runs is AGPL-3.0. Fine privately; flag it before any
  distribution (README S4.4).

Not in the catalogue, and why:
- ``nsynth_bright_dark`` and ``nsynth_acoustic_electronic``: Essentia publishes them as TensorFlow .pb only (no ONNX),
  and they were trained on single NSynth notes, not mixes. ``timbre`` (bright/dark, trained on music, ONNX published)
  takes ``bright``; ``mood_acoustic`` already takes ``acoustic``.
- ``genre_discogs400``'s own head file: .pb only. Its 400 styles are Discogs-EffNet's own ``activations`` output (the
  same 400 labels in the same order, checked), so the entry is its label file and ``facts.backbone_output``.
- A Discogs-EffNet arousal head: none exists. ``deam`` (arousal/valence on MSD-MusiCNN embeddings, range 1..9) is
  fetched, but MSD-MusiCNN costs 45-55 ms a run (8x EffNet; README S4.4a), so show.yaml leaves ``arousal`` off.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import pathlib
import sys
import time
import urllib.request
from typing import Callable, Optional

DEFAULT_DIR = pathlib.Path.home() / ".cache" / "screen-machine" / "models"   # = contract.MODELS_DIR
USER_AGENT = "screen-machine-models/1.0 (private install; python-urllib)"
MAX_BYTES = 200 * 1024 * 1024
TIMEOUT_S = 120.0

ESSENTIA = "https://essentia.upf.edu/models"
ESSENTIA_PAGE = "https://essentia.upf.edu/models.html"
ESSENTIA_LICENSE = ("CC BY-NC-SA 4.0", "https://creativecommons.org/licenses/by-nc-sa/4.0/")
ESSENTIA_CREDIT = ("{title}, Music Technology Group (Universitat Pompeu Fabra), Essentia models, CC BY-NC-SA 4.0. "
                   "Alonso-Jiménez et al., \"Tensorflow Audio Models in Essentia\", ICASSP 2020.")
CED_REV = "26c3ebcae85d4330f4fc26763f029539a3afcda0"          # mispeech/ced-mini, the commit checked on 16 Oct
CED_BASE = f"https://huggingface.co/mispeech/ced-mini/resolve/{CED_REV}"
BEAT_THIS_CKPT = "https://cloud.cp.jku.at/public.php/dav/files/7ik4RrBKTS273gp"
BEAT_THIS_LICENSE = ("MIT", "https://github.com/CPJKU/beat_this/blob/main/LICENSE")


def _essentia(name: str, path: str, title: str, task: str, onnx: Optional[tuple], meta: tuple, facts: dict) -> dict:
    """An Essentia model: ``path`` is its directory under /models/, ``onnx`` (sha256, bytes) or None (labels only)."""
    stem = path.rsplit("/", 1)[-1]
    entry = {"name": name, "task": task, "page": ESSENTIA_PAGE, "license": ESSENTIA_LICENSE[0],
             "license_url": ESSENTIA_LICENSE[1], "credit": ESSENTIA_CREDIT.format(title=title), "source": "fetch",
             "meta": {"file": f"{stem}.json", "url": f"{ESSENTIA}/{path}.json", "sha256": meta[0], "bytes": meta[1]},
             "facts": facts}
    if onnx:
        entry.update({"file": f"{stem}.onnx", "url": f"{ESSENTIA}/{path}.onnx", "sha256": onnx[0], "bytes": onnx[1]})
    else:                                   # labels only: the meta file is the entry's file
        entry.update({"file": entry["meta"]["file"], "url": entry["meta"]["url"], "sha256": meta[0],
                      "bytes": meta[1]})
        del entry["meta"]
    return entry


def _labels(stem: str) -> dict:
    return {"file": f"{stem}.json", "key": "classes"}


EFFNET = "discogs-effnet-bsdynamic-1"
MUSICNN = "msd-musicnn-1"
MUSICNN_MEL = {"type": "musicnn_mel", "fs": 16000, "frame": 512, "hop": 256, "bands": 96}

CATALOGUE: list[dict] = [
    {"name": "ced-mini", "task": "ced", "file": "ced-mini.onnx", "url": f"{CED_BASE}/model.onnx",
     "sha256": "2a86374dbe1fa03e96b0acf6a02618ac71852d12aa3d72a030b062ff1e5c5434", "bytes": 10890069,
     "page": "https://huggingface.co/mispeech/ced-mini", "license": "Apache-2.0",
     "license_url": "https://www.apache.org/licenses/LICENSE-2.0", "source": "fetch",
     "credit": "CED-Mini (Dinkel et al., \"CED: Consistent ensemble distillation for audio tagging\", ICASSP 2024), "
               "mispeech/ced-mini on Hugging Face, Apache-2.0.",
     "meta": {"file": "ced-mini-config.json", "url": f"{CED_BASE}/config.json",
              "sha256": "4036cba16f3759f5588e731029327969ff30a475fe23268fa698530cd7fd10ea", "bytes": 25058},
     "facts": {"kind": "audio_tagger", "input": {"name": "waveform", "type": "waveform", "fs": 16000},
               "output": "logits", "output_is": "probability",      # the model's head applies the sigmoid (checked)
               "labels": {"file": "ced-mini-config.json", "key": "id2label"},
               "label_names": "AudioSet's short display names (\"Male speech\", not \"Male speech, man speaking\")",
               "quantized": "int8 (DequantizeLinear in the graph); 527 AudioSet classes"}},
    _essentia("discogs-effnet", f"feature-extractors/discogs-effnet/{EFFNET}", "Discogs-EffNet (bsdynamic)", "tags",
              ("a280825b334797cf677939db8cd5762c0392aedd0ca6415dbc1cd083f045e43c", 18027718),
              ("a2e85b2e7372d5f8e0f35bdd6aeae1139f101087d183d0b2fb60b0ea0f01a0ff", 14986),
              {"kind": "backbone", "input": {"name": "melspectrogram", "frames": 128, "patch_hop": 62, **MUSICNN_MEL},
               "outputs": {"activations": "activations", "embeddings": "embeddings"}, "emb_dim": 1280,
               "labels": _labels(EFFNET)}),
    _essentia("genre_discogs400", "classification-heads/genre_discogs400/genre_discogs400-discogs-effnet-1",
              "Genre Discogs400", "tags", None,
              ("2d367319d9b782ffa10f69abf0e805b3ac4e10899025e5bdbaceda3919b243e0", 14951),
              {"kind": "styles", "via": "discogs-effnet", "backbone_output": "activations", "label_sep": "---",
               "labels": _labels("genre_discogs400-discogs-effnet-1"),
               "note": "the backbone's own 400-style output (same labels, same order as discogs-effnet's: checked); "
                       "the head itself is published as .pb only"}),
    _essentia("mtg_jamendo_moodtheme", "classification-heads/mtg_jamendo_moodtheme/mtg_jamendo_moodtheme-discogs-effnet-1",
              "MTG-Jamendo mood and theme", "tags",
              ("7d6270acaa5f4bba4b115a0d6849aca05ed6bd153dcb6d9da4f6ab9f99ef10ff", 2739322),
              ("d62cd90263e4d613fa7fcce7a831e339450394794af63685f96e065c1a896ab0", 3646),
              {"kind": "multilabel", "via": "discogs-effnet", "output": "activations",
               "labels": _labels("mtg_jamendo_moodtheme-discogs-effnet-1")}),
    _essentia("mtg_jamendo_instrument",
              "classification-heads/mtg_jamendo_instrument/mtg_jamendo_instrument-discogs-effnet-1",
              "MTG-Jamendo instrument", "tags",
              ("9ae2d9e763d66bd8eed654d1ac3aa171e6539cb8a0e11f3dcd53df1428980802", 2706492),
              ("7d02204c6451b5615e2968ec6364bbae3b915c886e608f05f00d3a38dc5177c4", 3382),
              {"kind": "multilabel", "via": "discogs-effnet", "output": "activations",
               "labels": _labels("mtg_jamendo_instrument-discogs-effnet-1")}),
    _essentia("danceability", "classification-heads/danceability/danceability-discogs-effnet-1", "Danceability",
              "tags", ("9ce9b8c44f1dd5df5ffc124e5d41d67acf254232c1b90c7e057e079ab7cead73", 514101),
              ("589e61dc05f0d7935be4b5fa7ab4341e5dc7cfecc6898f83503558564790cd75", 1989),
              {"kind": "binary", "via": "discogs-effnet", "output": "activations",
               "labels": ["danceable", "not_danceable"], "yes": "danceable"}),
    _essentia("voice_instrumental", "classification-heads/voice_instrumental/voice_instrumental-discogs-effnet-1",
              "Voice/instrumental", "tags",
              ("20155e4c439714b0c45c08644b73c8e12d9dccb173bd4ab9934bf1e5aee837ca", 514114),
              ("43ac2c3b055dfaed20f6232e0f10636c287f1c5a6e5bd02c5585860031964c8f", 2013),
              {"kind": "binary", "via": "discogs-effnet", "output": "activations",
               "labels": ["instrumental", "voice"], "yes": "voice"}),
    _essentia("engagement_regression", "classification-heads/engagement/engagement_regression-discogs-effnet-1",
              "Engagement (regression)", "tags",
              ("9551a6c162c9803aef5ce4e4d29a2e4fae99b5bfdc95dae9dd8fe5335e0fb567", 513626),
              ("6dc6f93a5c023f74bcb4ba38297bf9a4fd9e106d2dd1f913b880ed87fcfbd958", 1988),
              {"kind": "regression", "via": "discogs-effnet", "output": "activations", "labels": ["engagement"],
               "yes": "engagement", "range": None,
               "note": "a linear output; Essentia doesn't publish its range, so it is unset and the field reads null "
                       "(measured on the test tracks: see the README's As built)"}),
    _essentia("approachability_regression",
              "classification-heads/approachability/approachability_regression-discogs-effnet-1",
              "Approachability (regression)", "tags",
              ("f783b17ee994394f30f27d05ccd3fb845e589d107802e55e0b9cf1ea041ec894", 513637),
              ("39c033e4af6d4f543be420b92e17c8aa3feffe70a88caf2b152d91731320b73f", 2013),
              {"kind": "regression", "via": "discogs-effnet", "output": "activations", "labels": ["approachability"],
               "yes": "approachability", "range": None,
               "note": "no MlTags field reads it yet; range unpublished, as engagement's"}),
    _essentia("timbre", "classification-heads/timbre/timbre-discogs-effnet-1", "Timbre (bright/dark)", "tags",
              ("3fbeacb0006b6ca3445b8696dcd5d89917a608ab29074f935dfa68816b23b44e", 513429),
              ("7bc71d932b4bd1254de9941d169c3b5b6daf5aaec7b3eeb6787b87f395b6d053", 1980),
              {"kind": "binary", "via": "discogs-effnet", "output": "activations", "labels": ["bright", "dark"],
               "yes": "bright"}),
    _essentia("mood_acoustic", "classification-heads/mood_acoustic/mood_acoustic-discogs-effnet-1", "Mood Acoustic",
              "tags", ("56b02abf772b9c1cf528e4d6521d6e09515cedccce47895ee5de8de33fbdf848", 514103),
              ("45f61055d1d87d5e7f8a413b06b57eca20c3af1a23cf8912e0d916422e5207e1", 2380),
              {"kind": "binary", "via": "discogs-effnet", "output": "activations",
               "labels": ["acoustic", "non_acoustic"], "yes": "acoustic"}),
    _essentia("msd-musicnn", f"feature-extractors/musicnn/{MUSICNN}", "MSD-MusiCNN", "tags",
              ("49668ffec47e52e94b96f45930bb46a28a1368d4bdfb5c05378fa834aca616e1", 3168334),
              ("8e6b3b509f0610c0e65dce467fd459d6777509388eaddb13ed138d8ac1341ffe", 3299),
              {"kind": "backbone", "input": {"name": "melspectrogram", "frames": 187, "patch_hop": 93, **MUSICNN_MEL},
               "outputs": {"activations": "activations", "embeddings": "embeddings"}, "emb_dim": 200,
               "labels": _labels(MUSICNN)}),
    _essentia("deam", "classification-heads/deam/deam-msd-musicnn-2", "Arousal/valence DEAM", "tags",
              ("2cb8f33f188d73fcb651f7c065b049959166c7852f454a59c13e66fe2c092bde", 82032),
              ("079df8d538d093ba35c34b7fbee01ff2773c4469be5281520f55d585295a3c58", 2698),
              {"kind": "regression", "via": "msd-musicnn", "labels": ["valence", "arousal"], "yes": "arousal",
               "range": [1, 9], "range_source": "essentia.upf.edu/models.html: \"2 dimensions, range [1, 9]\""}),
    # exported once by tools/show/export_onnx.py (no ONNX is published); listed here so --list shows how
    {"name": "beat-this-small0", "task": "beats", "file": "beat-this-small0.onnx", "source": "export",
     "page": "https://github.com/CPJKU/beat_this", "license": BEAT_THIS_LICENSE[0],
     "license_url": BEAT_THIS_LICENSE[1],
     "credit": "Beat This! (Foscarin, Schlüter, Widmer, \"Beat this! Accurate beat tracking without DBN "
               "postprocessing\", ISMIR 2024), JKU Linz, MIT.",
     "upstream": {"url": f"{BEAT_THIS_CKPT}/small0.ckpt",
                  "sha256": "6074be2c4d490c5f6101fcc374a1ec72ae93456e23bb6019783b849f5dc7d47b", "bytes": 8451101},
     "make": "flock /tmp/claude-1000/sm-build.lock nice -n 19 /tmp/claude-1000/onnx-export/venv/bin/python "
             "tools/show/export_onnx.py beat-this-small0   (README S4.4 \"Exports\")"},
]
BY_NAME = {e["name"]: e for e in CATALOGUE}


# -- helpers -----------------------------------------------------------------------------------------------------------
def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_catalogue(cat: list[dict]) -> list[str]:
    problems, seen = [], set()
    for i, e in enumerate(cat):
        where = e.get("name") or f"#{i}"
        for key in ("name", "file", "page", "license", "license_url", "source", "credit"):
            if not isinstance(e.get(key), str) or not e[key]:
                problems.append(f"{where}: {key} missing")
        if e.get("name") in seen:
            problems.append(f"{where}: duplicate name")
        seen.add(e.get("name"))
        if e.get("source") == "fetch":
            for key in ("url", "sha256"):
                if not isinstance(e.get(key), str) or not e[key]:
                    problems.append(f"{where}: {key} missing")
            if len(e.get("sha256") or "") != 64:
                problems.append(f"{where}: sha256 must be 64 hex characters")
            if not isinstance(e.get("facts"), dict) or "kind" not in e["facts"]:
                problems.append(f"{where}: facts.kind missing")
            meta = e.get("meta")
            if meta is not None and (len(meta.get("sha256") or "") != 64 or not meta.get("url")):
                problems.append(f"{where}: meta needs url and sha256")
        elif e.get("source") == "export":
            if not isinstance(e.get("upstream"), dict) or len(e["upstream"].get("sha256") or "") != 64:
                problems.append(f"{where}: upstream checkpoint url and sha256 missing")
        else:
            problems.append(f"{where}: source must be fetch or export")
    return problems


def _download(url: str, dest: pathlib.Path, *, timeout: float) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    part = dest.with_name(dest.name + ".part")
    h, n = hashlib.sha256(), 0
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp, open(part, "wb") as out:
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                n += len(chunk)
                if n > MAX_BYTES:
                    raise ValueError(f"larger than {MAX_BYTES} bytes")
                h.update(chunk)
                out.write(chunk)
        os.replace(part, dest)
    finally:
        if part.exists():
            part.unlink()
    return n, h.hexdigest()


def read_index(path: pathlib.Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_index(dest: pathlib.Path, index: dict) -> None:
    """index.json and LICENSES.md, each replaced atomically."""
    path = dest / "index.json"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(index, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    lines = ["# Model licences", "",
             "Written by tools/show/fetch_models.py and tools/show/export_onnx.py. One entry per model in index.json.",
             "", "The essentia library the worker runs (not a model) is AGPL-3.0: fine for this private install, "
             "flagged before any distribution (show_service/README.md S4.4).", ""]
    for name in sorted(index):
        e = index[name]
        lines += [f"## {name}", "", f"- file: `{e.get('file')}` (sha256 `{e.get('sha256')}`, {e.get('source')})",
                  f"- licence: {e.get('license')} ({e.get('license_url')})", f"- page: {e.get('page')}",
                  f"- credit: {e.get('credit')}", ""]
    lic = dest / "LICENSES.md"
    tmp = lic.with_name(lic.name + ".tmp")
    tmp.write_text("\n".join(lines), encoding="utf-8")
    os.replace(tmp, lic)


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _fetch_one(url: str, want: str, path: pathlib.Path, force: bool, timeout: float) -> tuple[bool, str]:
    """(fetched now?, sha256). Raises when the download's checksum isn't ``want``."""
    if path.is_file() and not force and sha256_file(path) == want:
        return False, want
    tmp = path.with_name(path.name + ".new")
    try:
        n, sha = _download(url, tmp, timeout=timeout)
        if sha != want:
            raise ValueError(f"sha256 {sha[:12]}... is not the pinned {want[:12]}... (changed upstream: check the page)")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return True, sha


def entry_for_index(e: dict, fetched_at: str) -> dict:
    out = {k: e[k] for k in ("file", "sha256", "bytes", "license", "license_url", "page", "source", "facts",
                             "credit", "task") if k in e}
    if e.get("meta"):
        out["meta"] = {"file": e["meta"]["file"], "sha256": e["meta"]["sha256"]}
    out["fetched_at"] = fetched_at
    return out


def fetch(cat: list[dict], dest: pathlib.Path, *, force: bool = False, timeout: float = TIMEOUT_S,
          log: Callable[[str], None] = print) -> int:
    dest = pathlib.Path(dest).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    index = read_index(dest / "index.json")
    failures = 0
    for e in cat:
        name = e["name"]
        if e["source"] != "fetch":
            have = index.get(name)
            ok = isinstance(have, dict) and (dest / str(have.get("file"))).is_file()
            log(f"{'ok' if ok else 'export':<7} {name}: {'exported ' + str(have.get('fetched_at')) if ok else e['make']}")
            continue
        t0 = time.monotonic()
        try:
            got = []
            if e.get("meta"):
                got.append(_fetch_one(e["meta"]["url"], e["meta"]["sha256"], dest / e["meta"]["file"], force,
                                      timeout)[0])
            got.append(_fetch_one(e["url"], e["sha256"], dest / e["file"], force, timeout)[0])
        except Exception as ex:                        # noqa: BLE001  one model failing never stops the rest
            failures += 1
            log(f"FAILED  {name}: {ex}")
            continue
        old = index.get(name) if isinstance(index.get(name), dict) else {}
        when = old.get("fetched_at") if not any(got) and old.get("sha256") == e["sha256"] else _now()
        index[name] = entry_for_index(e, when or _now())
        log(f"{'fetched' if any(got) else 'ok':<7} {name} ({e['bytes'] / 1e6:.1f} MB"
            f"{f' in {time.monotonic() - t0:.1f} s' if any(got) else ', already here'})")
    write_index(dest, index)
    return failures


def verify(dest: pathlib.Path, log: Callable[[str], None] = print) -> int:
    dest = pathlib.Path(dest).expanduser()
    index = read_index(dest / "index.json")
    bad = 0
    for name, e in sorted(index.items()):
        files = [(e.get("file"), e.get("sha256"))]
        if isinstance(e.get("meta"), dict):
            files.append((e["meta"].get("file"), e["meta"].get("sha256")))
        for f, want in files:
            p = dest / str(f)
            ok = p.is_file() and sha256_file(p) == want
            bad += 0 if ok else 1
            log(f"{'ok' if ok else 'BAD':<4} {name}: {f}")
    for e in CATALOGUE:
        if e["name"] not in index:
            log(f"{'--':<4} {e['name']}: not installed ({'run ' + e['make'] if e['source'] == 'export' else 'fetch it'})")
    return bad


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=str(DEFAULT_DIR), help=f"where (default {DEFAULT_DIR})")
    ap.add_argument("--only", default=None, help="comma-separated catalogue names")
    ap.add_argument("--list", action="store_true", help="the catalogue; no network")
    ap.add_argument("--verify", action="store_true", help="check the installed files' sha256; no network")
    ap.add_argument("--force", action="store_true", help="download again even when present and correct")
    ap.add_argument("--timeout", type=float, default=TIMEOUT_S)
    a = ap.parse_args(argv)
    problems = check_catalogue(CATALOGUE)
    if problems:
        for p in problems:
            print(f"CATALOGUE: {p}", file=sys.stderr)
        return 2
    chosen = CATALOGUE
    if a.only:
        names = [s.strip() for s in a.only.split(",") if s.strip()]
        unknown = sorted(set(names) - set(BY_NAME))
        if unknown:
            print(f"unknown: {', '.join(unknown)}; choose from {', '.join(BY_NAME)}", file=sys.stderr)
            return 2
        chosen = [BY_NAME[n] for n in names]
    if a.list:
        total = 0
        for e in chosen:
            total += int(e.get("bytes") or 0)
            print(f"{e['name']:<28} {e['task']:<6} {e['source']:<7} {e['license']:<16} {e['file']}")
            if e["source"] == "export":
                print(f"{'':<28} make it: {e['make']}")
        print(f"{len(chosen)} models, about {total / 1e6:.0f} MB fetched (plus the exports)")
        return 0
    if a.verify:
        return 1 if verify(pathlib.Path(a.dir)) else 0
    failures = fetch(chosen, pathlib.Path(a.dir), force=a.force, timeout=a.timeout)
    print(f"{len([e for e in chosen if e['source'] == 'fetch']) - failures} fetched models in "
          f"{pathlib.Path(a.dir).expanduser()}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
