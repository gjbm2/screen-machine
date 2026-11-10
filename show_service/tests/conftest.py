"""show_service/pytest.ini anchors pytest in show_service/, so the repo root's conftest.py (which selects the
deployment) is not loaded for this suite: select it here the same way. The tests read the shipped finsbury deployment
unless SM_DEPLOYMENT says otherwise; smconfig refuses to guess, so without this contract.py would stop at import."""
import os

os.environ.setdefault("SM_DEPLOYMENT", "finsbury")
