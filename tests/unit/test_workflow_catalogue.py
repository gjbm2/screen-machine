"""The workflow catalogue (src/data/workflows.json) against the graphs and refiners it names. No network.

A workflow that the generator, a schedule or Alexa can name must load, take what its inputs promise and have
somewhere to run; a broken entry otherwise shows up as a failed job on a TV."""
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CATALOGUE = json.loads((REPO / "src" / "data" / "workflows.json").read_text())
REFINERS = json.loads((REPO / "src" / "data" / "refiners.json").read_text())
BY_ID = {w["id"]: w for w in CATALOGUE}
LIVE = [w for w in CATALOGUE if not w.get("replaced_by")]


def graph(workflow_id):
    return json.loads((REPO / "routes" / "workflows" / workflow_id).read_text())


def titles(workflow_id):
    return [(node.get("_meta") or {}).get("title", "") for node in graph(workflow_id).values() if isinstance(node, dict)]


def has_node(workflow_id, placeholder):
    return any(placeholder in title for title in titles(workflow_id))


def test_ids_are_unique_and_one_is_the_default():
    ids = [w["id"] for w in CATALOGUE]
    assert len(ids) == len(set(ids))
    defaults = [w["id"] for w in CATALOGUE if w.get("default")]
    assert len(defaults) == 1, f"exactly one default workflow, found {defaults}"
    assert not BY_ID[defaults[0]].get("replaced_by") and not BY_ID[defaults[0]].get("hidden")


@pytest.mark.parametrize("workflow", [w["id"] for w in CATALOGUE if w.get("replaced_by")])
def test_a_retired_workflow_runs_on_a_live_one(workflow):
    replacement = BY_ID.get(BY_ID[workflow]["replaced_by"])
    assert replacement is not None, "replaced_by names a workflow that is not in the catalogue"
    assert not replacement.get("replaced_by"), "replaced_by must name a live workflow, not another retired one"
    assert sorted(replacement.get("input") or []) == sorted(BY_ID[workflow].get("input") or []), \
        "the replacement takes different inputs, so old callers would send it the wrong things"


@pytest.mark.parametrize("workflow", [w["id"] for w in LIVE])
def test_a_live_workflow_has_a_graph_an_endpoint_and_takes_its_inputs(workflow):
    entry = BY_ID[workflow]
    assert (REPO / "routes" / "workflows" / workflow).exists(), "no graph file"
    assert entry.get("runpod_id"), "no RunPod endpoint"
    inputs = entry.get("input") or []
    assert inputs, "no inputs declared"
    if "text" in inputs:
        assert has_node(workflow, "{{POSITIVE-PROMPT}}"), "takes text but no node is titled {{POSITIVE-PROMPT}}"
    if "image" in inputs:
        assert has_node(workflow, "{{LOAD-IMAGE}}"), "takes an image but no node is titled {{LOAD-IMAGE}}"
    if (entry.get("uses_images") or 1) == 2:
        assert has_node(workflow, "{{LOAD-IMAGE-2}}"), "takes two images but no node is titled {{LOAD-IMAGE-2}}"
    # each prompt node has the field routes.generate fills
    for node in graph(workflow).values():
        if isinstance(node, dict) and "{{POSITIVE-PROMPT}}" in (node.get("_meta") or {}).get("title", ""):
            assert {"text", "prompt"} & set(node.get("inputs") or {}), "the prompt node has no text or prompt input"


@pytest.mark.parametrize("workflow", [w["id"] for w in LIVE if w.get("default-refiner")])
def test_a_workflows_default_refiner_exists(workflow):
    assert BY_ID[workflow]["default-refiner"] in {r["id"] for r in REFINERS}


@pytest.mark.parametrize("refiner", [r["id"] for r in REFINERS if r.get("default-workflow")])
def test_a_refiners_default_workflow_exists(refiner):
    workflow = next(r for r in REFINERS if r["id"] == refiner)["default-workflow"]
    assert workflow in BY_ID, f"{refiner} names {workflow}, which is not in the catalogue"


def test_the_generators_fallback_workflow_is_in_the_catalogue():
    import routes.generate as generate
    assert generate.get_parser().parse_args([]).workflow in BY_ID


STILLS = [w["id"] for w in LIVE if not w.get("async")]


@pytest.mark.parametrize("workflow", STILLS)
def test_only_the_finish_node_may_crop(workflow):
    """A crop to a fixed size cuts away any picture of another shape (19 Nov: portrait photos came back landscape).
    The one crop allowed is the {{FINISH}} node's, which routes.generate sizes to the picture's own shape."""
    for node in graph(workflow).values():
        crop = (node.get("inputs") or {}).get("crop") if isinstance(node, dict) else None
        if crop and crop != "disabled":
            assert "{{FINISH}}" in (node.get("_meta") or {}).get("title", ""), \
                f"{node.get('class_type')} crops to a fixed size; title it {{{{FINISH}}}} so the app sizes it"


@pytest.mark.parametrize("workflow", [w for w in STILLS if has_node(w, "{{FINISH}}")])
def test_a_finished_workflow_knows_its_pictures_shape(workflow):
    """routes.generate reads the picture's shape from the {{LATENT-IMAGE}} node, else from the first reference
    image (the edit graphs' latent is made on it); a graph with neither would be finished to the screen's shape."""
    assert has_node(workflow, "{{LATENT-IMAGE}}") or "image" in (BY_ID[workflow].get("input") or []), \
        "no {{LATENT-IMAGE}} node and no reference image: the finish cannot know the picture's shape"
