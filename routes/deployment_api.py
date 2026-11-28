"""GET /api/deployment: which deployment this app is, what it can do, and its screens (deployments/README.md).

The frontend reads it to show only what this site has (a generator page without generation says so, the Room page
lists this site's screens, the admin page its kiosk's screens). Shape:

    {"id": "finsbury", "name": "Drawing room (PC22)",
     "capabilities": {"generation": true, ...every capability smconfig knows...},
     "default_room": "drawing-room",
     "screens": [{"id": "north-screen", "name": "North Screen", "live": true, "mask": true, "art_only": false,
                  "room": "drawing-room"}, ...],
     "kiosk": {"screens": [{"id": "north-screen", "name": "north", "cdp_port": 9222}, ...]},
     "wording": {"lights_menu": "Lamps, glitterball, pinspots", ...}}

screens are the non-headless destinations with a ``live`` block, an ``intensity_cfg``, or named in a rooms.json room's
"screens", in publish-destinations.json order: live = it can go live, mask = it has intensity_cfg, art_only = it only
shows art (no live block), room = the rooms.json room holding it (null when none does). kiosk is null when the
deployment has no kiosk; its screens are the manifest's kiosk.screens the admin API can drive (short name + CDP port).
wording is the manifest's site phrases ({} when none): a page uses wording[key] or its own generic default.
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


def _kiosk(dep):
    """{"screens": [{id, name, cdp_port}]} of the manifest's kiosk, or None when the deployment has none."""
    k = dep.kiosk
    if not k:
        return None
    if not isinstance(k.get("screens") or {}, dict):        # smconfig check says so; the rest of the page still works
        error("[deployment] kiosk.screens is not a mapping: the admin page gets no kiosk screens")
        return None
    from routes.admin_api import kiosk_screens
    return {"screens": [{"id": sid, "name": name, "cdp_port": port} for sid, name, port in kiosk_screens(k)]}


def _wording(dep) -> dict:
    """The manifest's site phrases ({key: phrase}; smconfig.check.WORDING_KEYS), {} when it has none."""
    w = dep.manifest.get("wording")
    return {k: v for k, v in w.items() if isinstance(k, str) and isinstance(v, str) and v.strip()} \
        if isinstance(w, dict) else {}


def deployment_info() -> dict:
    dep = smconfig.deployment()
    from smsound.outputs import screens as site_screens   # the one rule (each screen is also a sound output)
    screens = site_screens(dep)
    return {
        "id": dep.id,
        "name": dep.name,
        "capabilities": dep.capabilities,
        "default_room": _default_room(dep),
        "screens": screens,
        "kiosk": _kiosk(dep),
        "wording": _wording(dep),
    }


@deployment_api.route("/deployment", methods=["GET"])
def get_deployment():
    try:
        return jsonify(deployment_info())
    except Exception as e:                            # noqa: BLE001 - a bad deployment file: say so, as JSON
        error(f"[deployment] {type(e).__name__}: {e}")
        return jsonify({"error": f"deployment: {e}"}), 500
