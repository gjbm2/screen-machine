"""The ML worker (README Stage 4, S4.3-S4.4): ``python -m show_service.ml.worker`` under ~/.venvs/sm-show-ml.

Modules:
- ``protocol``: the stdin frames and stdout lines (stdlib; both venvs import it).
- ``models``: the model back ends: onnxruntime + essentia (imported lazily), and ``--fake`` (numpy only).
- ``worker``: the process: its ring, the two compute lanes (a grid, never a queue), the budget watchdog, the CPU guard.
- ``supervisor``: the show_service side: spawn, feed, read, restart with backoff (asyncio; stdlib + contract).

Nothing here imports another show_service module but ``contract``. Models live in ``contract.MODELS_DIR`` (fetched by
tools/show/fetch_models.py, never in git).
"""
