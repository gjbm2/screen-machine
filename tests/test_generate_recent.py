import json, os
import tempfile
from pathlib import Path
import pytest

import app as flask_app

@pytest.fixture()
def client(monkeypatch):
    flask_app.app.config.update({"TESTING": True})
    with flask_app.app.test_client() as client:
        yield client

def test_generate_endpoint_copies_to_recent(client, monkeypatch):
    """POST /api/generate-image should include recent_files and forward batch_id to publisher."""
    # stub data to be returned by handle_image_generation
    dummy_response = [
        {
            "message": "http://example.com/img1.jpg",
            "seed": 1,
            "prompt": "cat",
            "negative_prompt": "",
            "input": {"workflow": "flux1"},
        },
        {
            "message": "http://example.com/img2.jpg",
            "seed": 2,
            "prompt": "cat",
            "negative_prompt": "",
            "input": {"workflow": "flux1"},
        },
    ]

    # Monkey-patch the heavy generator call
    monkeypatch.setattr(
        flask_app.routes.alexa,
        "handle_image_generation",
        lambda *args, **kwargs: dummy_response,
    )

    # Capture filenames that publisher would create
    captured_filenames = []
    def fake_publish_to_destination(**kwargs):
        assert kwargs["publish_destination_id"] == "_recent"
        assert kwargs.get("batch_id") is not None
        fname = f"{kwargs['batch_id']}_{len(captured_filenames)}.jpg"
        captured_filenames.append(fname)
        return {"success": True, "meta": {"filename": fname}}

    monkeypatch.setattr(
        flask_app.routes.publisher,
        "publish_to_destination",
        fake_publish_to_destination,
    )

    # Build minimal payload
    payload = {
        "prompt": "cat",
        "workflow": "flux1",
        "params": {},
        "global_params": {"batch_size": 2},
    }
    data = {"data": json.dumps(payload)}

    rv = client.post("/api/generate-image", data=data)
    assert rv.status_code == 200
    out = rv.get_json()

    # Expect 2 images and recent_files list matching
    assert out["success"] is True
    assert len(out["images"]) == 2
    assert len(out["recent_files"]) == 2
    assert captured_filenames == out["recent_files"] 