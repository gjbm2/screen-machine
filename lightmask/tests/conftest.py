"""lightmask/pytest.ini anchors this suite here, so the repo root's conftest.py is not loaded: select the deployment the
same way (the shipped finsbury unless SM_DEPLOYMENT says otherwise). lightmask reads the deployment's intensity.json and
publish-destinations.json, and Flask's config.py (which it executes) reads the deployment too."""
import os

os.environ.setdefault("SM_DEPLOYMENT", "finsbury")
