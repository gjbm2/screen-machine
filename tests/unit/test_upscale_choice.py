"""The AI upscaler is chosen by size where the graph's picture size is known (routes/generate.py)."""
import json
from pathlib import Path

from routes.generate import _upscales_known_size

WORKFLOWS = Path(__file__).resolve().parents[2] / "routes" / "workflows"


def graph(name):
    return json.loads((WORKFLOWS / name).read_text())


def test_text_to_image_and_upscale_graphs_know_their_size():
    for name in ("krea2-turbo.json", "zimage-turbo.json", "qwen21.json", "qwen21-turbo.json", "upscale-finish.json"):
        assert _upscales_known_size(graph(name)), name


def test_edit_graphs_keep_their_own_upscaler():
    for name in ("qwen21-edit.json", "qwen21-combine.json"):
        assert not _upscales_known_size(graph(name)), name


def test_every_finish_is_fed_by_the_ai_upscaler():
    for name in ("krea2-turbo.json", "zimage-turbo.json", "qwen21.json", "qwen21-turbo.json", "qwen21-edit.json",
                 "qwen21-combine.json", "upscale-finish.json"):
        g = graph(name)
        finish = next(n for n in g.values() if "{{FINISH}}" in n["_meta"]["title"])
        assert g[finish["inputs"]["image"][0]]["class_type"] == "ImageUpscaleWithModel", name


def test_a_fast_adapt_skips_the_upscale_and_finish():
    from argparse import Namespace
    from routes.generate import apply_workflow_mutations
    catalogue = json.loads((WORKFLOWS.parents[1] / "src" / "data" / "workflows.json").read_text())
    for name in ("qwen21-edit.json", "qwen21-combine.json"):
        config = next(w for w in catalogue if w["id"] == name)
        args = Namespace(**{"skip-upscaling": True})
        g = apply_workflow_mutations(graph(name), config, args)
        assert not {"20", "21", "22"} & set(g), name
        assert g["9"]["inputs"]["images"] == ["8", 0], name
