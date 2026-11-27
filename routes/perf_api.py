"""GET /api/perf/gpu: where the GPU's time has gone over the last minutes (utils/gpu_watch.py), for the GPU card on
Settings > Developer. Read-only."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from utils import gpu_watch

perf_api = Blueprint("perf_api", __name__)


@perf_api.route("/perf/gpu", methods=["GET"])
def gpu():
    w = gpu_watch.get()
    if w is None:
        return jsonify({"running": False})
    try:
        minutes = max(1.0, min(60.0, float(request.args.get("minutes", 15))))
    except ValueError:
        minutes = 15.0
    return jsonify(w.view(minutes))
