"""sound.api: /api/sound/* — where sound plays and how late it is heard, for every part of the app (docs/SOUND.md).

  GET  /sound/outputs[?for=karaoke|music]   {outputs: [row + latency]}: every sound output (smsound.outputs rows:
                                            {id, kind, kind_label, label, room, hint, calibrate}) with its latency
                                            (smsound.latency.describe), the phones and tablets show_service knows
                                            included; ?for= marks the ones that consumer can't play to (available,
                                            why)
  GET  /sound/latency                       every latency the deployment knows (smsound.latency.overview): sound
                                            outputs, screens, lights, sound sources
  PUT  /sound/outputs/<id>/latency          {trim_ms} | {delay_ms} (typed in) | {forget: true}: -> the output's latency
  PUT  /sound/latency/lights/<key>          {rise_ms, fall_ms?, also?, evidence?}: a light's measured response (the
                                            camera calibration, Settings › Sound & timing) -> {saved}
  GET  /sound/outputs/<id>/eq               its graphic EQ (smsound.eq.describe): {applies, preset, gains, bands,
                                            presets, range_db}
  PUT  /sound/outputs/<id>/eq               {preset} | {gains: [dB x 7]}: -> the same
  POST /sound/calibrate                     {output, mic?}: measure it (routes/sound/calibrate.py) -> the run's state;
                                            400 {error} when it can't be measured from here
  GET  /sound/calibrate                     the run's state: {id, state: idle|running|done|failed, output, label,
                                            phase, message, clicks, chirps, delay_ms...}; the display pages poll it
                                            and the one whose screen is being measured plays the chirps
"""
from __future__ import annotations

import json
import time
import urllib.request
from typing import Any

from flask import Blueprint, jsonify, request

import smconfig
from smsound import eq as EQ
from smsound import latency as L
from smsound import outputs as O

from .calibrate import calibrator

sound_api = Blueprint("sound_api", __name__)

#: What each consumer can play to (the consumers' own modules say why).
CONSUMERS: dict[str, tuple[set[str], str]] = {
    "karaoke": ({"screen", "device", "sonos", "none"}, "Karaoke plays from a screen's page, a browser or a Sonos"),
    "music": ({"pc22", "sonos", "device", "none"}, "The piano plays on the PC, a Sonos or a phone"),
}
DEVICES_TIMEOUT_S = 0.8


def _err(code: int, msg: str):
    return jsonify({"error": msg}), code


def device_rows() -> list[dict]:
    """The phones and tablets show_service's piano knows (GET /api/v1/piano/devices), as rows; [] if it doesn't answer."""
    if not smconfig.enabled("show"):
        return []
    try:
        with urllib.request.urlopen(smconfig.url("show").rstrip("/") + "/api/v1/piano/devices",   # noqa: S310
                                    timeout=DEVICES_TIMEOUT_S) as r:
            doc = json.loads(r.read() or b"{}")
    except Exception:  # noqa: BLE001 - show_service down: no devices
        return []
    out = []
    for d in doc.get("devices") or []:
        if isinstance(d, dict):
            r = O.device_row(str(d.get("id") or ""), d.get("label") or d.get("name"))
            if r:
                out.append(r)
    return out


def all_rows() -> list[dict]:
    rows = O.static_outputs()
    devs = device_rows()
    return rows[:-1] + devs + rows[-1:]                 # "none" last


def output_rows(who: str | None = None) -> list[dict]:
    """Every output with its latency; for a consumer (CONSUMERS), marked available or not (KeyError: no such one)."""
    doc = L.read()
    rows = [{**r, "latency": L.describe(r["id"], doc=doc)} for r in all_rows()]
    if who:
        kinds, why = CONSUMERS[who]
        rows = O.for_consumer(kinds, rows, why)
    return rows


@sound_api.get("/sound/outputs")
def outputs():
    who = request.args.get("for")
    if who and who not in CONSUMERS:
        return _err(400, f"for: one of {', '.join(CONSUMERS)}")
    return jsonify({"outputs": output_rows(who), "rule": "milliseconds; + = heard that much after it is scheduled"})


@sound_api.get("/sound/latency")
def latency():
    return jsonify(L.overview(extra_outputs=device_rows()))


@sound_api.put("/sound/outputs/<path:oid>/latency")
def set_latency(oid: str):
    if O.parse(oid) is None or O.kind(oid) == "none":
        return _err(404, "Not a sound output.")
    b: Any = request.get_json(silent=True) or {}
    try:
        if b.get("forget"):
            L.set_manual(oid, None)
        if "delay_ms" in b:
            L.set_manual(oid, None if b["delay_ms"] is None else float(b["delay_ms"]))
        if "trim_ms" in b:
            L.set_trim(oid, float(b["trim_ms"]))
    except (TypeError, ValueError) as e:
        return _err(400, str(e))
    return jsonify({"id": oid, "latency": L.describe(oid)})


@sound_api.put("/sound/latency/lights/<path:key>")
def set_light_latency(key: str):
    """{rise_ms, fall_ms?, also?: [keys], evidence?: {...}}: a light's measured response (the camera calibration),
    saved for it and for each of ``also`` (the same model on the same output) -> {saved: {key: entry}}."""
    b: Any = request.get_json(silent=True) or {}
    keys = [key] + [k for k in (b.get("also") or []) if isinstance(k, str) and k and k != key]
    ev = b.get("evidence") if isinstance(b.get("evidence"), dict) else {}
    try:
        saved = {k: L.record_light(k, float(b["rise_ms"]),
                                   None if b.get("fall_ms") is None else float(b["fall_ms"]),
                                   method="camera", extra={"evidence": ev, **({"from": key} if k != key else {})})
                 for k in keys}
    except (KeyError, TypeError, ValueError) as e:
        return _err(400, f"rise_ms (and fall_ms) in ms: {e}")
    return jsonify({"saved": saved})


@sound_api.get("/sound/outputs/<path:oid>/eq")
def get_eq(oid: str):
    if O.parse(oid) is None or O.kind(oid) == "none":
        return _err(404, "Not a sound output.")
    return jsonify({"id": oid, "eq": EQ.describe(oid)})


@sound_api.put("/sound/outputs/<path:oid>/eq")
def put_eq(oid: str):
    b: Any = request.get_json(silent=True) or {}
    try:
        d = EQ.set_eq(oid, preset=b.get("preset"), gains_db=b.get("gains"))
    except ValueError as e:
        return _err(400, str(e))
    return jsonify({"id": oid, "eq": d})


@sound_api.post("/sound/calibrate")
def calibrate():
    b: Any = request.get_json(silent=True) or {}
    out = str(b.get("output") or "")
    try:
        st = calibrator().start(out, mic=str(b["mic"]) if b.get("mic") else None)
    except ValueError as e:
        return _err(400, str(e))
    return jsonify(st)


@sound_api.get("/sound/calibrate")
def calibrate_state():
    return jsonify({**calibrator().state(), "now": time.time()})   # now: the pages' clock skew (the chirps' times)
