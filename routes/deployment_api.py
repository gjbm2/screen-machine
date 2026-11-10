"""GET /api/deployment: which deployment this app is, what it can do, and its screens (deployments/README.md).

The frontend reads it to show only what this site has (a generator page without generation says so, the Room page
lists this site's screens). Shape:

    {"id": "finsbury", "name": "Drawing room (PC22)",
     "capabilities": {"generation": true, ...every capability smconfig knows...},
     "default_room": "drawing-room",
     "screens": [{"id": "north-screen", "name": "North Screen", "live": true, "mask": true}, ...]}

screens are the destinations with a ``live`` block or an ``intensity_cfg`` (mask = has intensity_cfg), in
publish-destinations.json order.
"""
from flask import Blueprint, jsonify

import smconfig
from utils.logger import error

deployment_api = Blueprint("deployment_api", __name__)


def _default_room(dep):
    """The deployment's default room; None (not a 500) when it names none and has no readable rooms.json."""
    try:
        return smconfig.default_room(dep)
    except (OSError, ValueError):
        return None


def deployment_info() -> dict:
    dep = smconfig.deployment()
    screens = []
    for d in smconfig.destinations(dep):
        live, mask = isinstance(d.get("live"), dict), isinstance(d.get("intensity_cfg"), dict)
        if live or mask:
            screens.append({"id": d["id"], "name": str(d.get("name") or d["id"]), "live": live, "mask": mask})
    return {
        "id": dep.id,
        "name": dep.name,
        "capabilities": dep.capabilities,
        "default_room": _default_room(dep),
        "screens": screens,
    }


@deployment_api.route("/deployment", methods=["GET"])
def get_deployment():
    try:
        return jsonify(deployment_info())
    except Exception as e:                            # noqa: BLE001 - a bad deployment file: say so, as JSON
        error(f"[deployment] {type(e).__name__}: {e}")
        return jsonify({"error": f"deployment: {e}"}), 500
