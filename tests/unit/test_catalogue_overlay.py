"""A deployment's private catalogue entries (deployments/<id>/private/catalogue.json, not in git) merged into the
shared workflows.json: routes/utils.merge_catalogue (src/data/typedWorkflows.ts does the same for the page)."""
from routes.utils import merge_catalogue

PUBLIC = [{"id": "a.json", "style_guidance": "A"}, {"id": "b.json", "style_guidance": "B"}]


def test_private_fields_change_a_public_entry():
    out = merge_catalogue(PUBLIC, [{"id": "a.json", "style_guidance": "A, and more"}])
    assert out == [{"id": "a.json", "style_guidance": "A, and more"}, {"id": "b.json", "style_guidance": "B"}]
    assert PUBLIC[0]["style_guidance"] == "A"


def test_new_entry_goes_after_the_named_one_else_at_the_end():
    out = merge_catalogue(PUBLIC, [{"id": "x.json", "after": "a.json"}, {"id": "y.json", "after": "gone.json"}])
    assert [w["id"] for w in out] == ["a.json", "x.json", "b.json", "y.json"]
    assert "after" not in out[1]


def test_no_private_file():
    assert merge_catalogue(PUBLIC, None) == PUBLIC
