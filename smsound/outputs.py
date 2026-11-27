"""Where sound plays: the one vocabulary of sound outputs (docs/SOUND.md; owner, 27 Nov: "control of output device
(for sound) isn't standard across karaoke mode vs other modes ... let's generalise this and ensure it's properly
engineered and exposed in a consistent standard way").

An output id is one of:

* ``screen:<screen id>``  a screen's own display page plays it (Web Audio in that browser: the TV, or the speakers on
  the computer showing it). Every screen of the deployment (``screens()``: the rule GET /api/deployment uses).
* ``pc22``                the Windows host's speakers (show_sender's Windows synth or its WASAPI player). Only where the
  deployment can play there (deployment.yaml ``host.windows_python``). The id predates deployments; its label is the
  site's wording (``windows_speakers``).
* ``sonos:<zone>``        a Sonos zone of the deployment's room/sonos.json (with an ``ip``), through room_service; only
  with the ``sonos`` capability.
* ``device:<id>``         a phone or tablet playing it itself (show_service piano_live's devices: dynamic, so they come
  from show_service, never from here).
* ``none``                no sound.

Every list of outputs anyone shows is built here, as rows of one shape (``row()``): ``{id, kind, label, room, hint,
calibrate}`` with ``calibrate`` saying how that output's delay is measured (smsound.latency). A consumer (karaoke, the
Music player) says which kinds it can deliver; ``for_consumer`` marks the rest unavailable with the reason, so every
picker shows the same places in the same words.
"""
from __future__ import annotations

import json
from typing import Any, Iterable, Optional

import smconfig

NONE = "none"
PC22 = "pc22"
SCREEN_PREFIX = "screen:"
SONOS_PREFIX = "sonos:"
DEVICE_PREFIX = "device:"
KINDS = ("screen", "pc22", "sonos", "device", "none")
ID_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")

#: How each kind's delay is known (smsound.latency): measured with chirps and the room mic (from this app), measured
#: by the piano while it plays (piano_render/calibrate.py), measured by the device itself (its browser), or nothing.
CALIBRATE = {"screen": "chirp", "sonos": "chirp", "pc22": "live", "device": "device", "none": None}

KIND_WORDS = {"screen": "Screen", "pc22": "Speakers", "sonos": "Sonos", "device": "Phone or tablet", "none": "No sound"}
NONE_LABEL = "No sound"


def parse(oid: Any) -> Optional[tuple[str, str]]:
    """``(kind, arg)`` of an output id (arg: the screen, zone or device; "" for pc22 and none), None if it isn't one."""
    if not isinstance(oid, str):
        return None
    if oid == NONE:
        return "none", ""
    if oid == PC22:
        return "pc22", ""
    for kind, pre in (("screen", SCREEN_PREFIX), ("sonos", SONOS_PREFIX), ("device", DEVICE_PREFIX)):
        if oid.startswith(pre):
            arg = oid[len(pre):]
            return (kind, arg) if 0 < len(arg) <= 64 and set(arg) <= ID_OK else None
    return None


def kind(oid: Any) -> Optional[str]:
    p = parse(oid)
    return p[0] if p else None


def screen_id(oid: Any) -> Optional[str]:
    p = parse(oid)
    return p[1] if p and p[0] == "screen" else None


def zone(oid: Any) -> Optional[str]:
    p = parse(oid)
    return p[1] if p and p[0] == "sonos" else None


# ---------------------------------------------------------------- what the deployment has

def screens(dep: Optional[smconfig.Deployment] = None) -> list[dict]:
    """The deployment's screens, in publish-destinations.json order: the non-headless destinations with a ``live``
    block, an ``intensity_cfg``, or named in a rooms.json room. ``[{id, name, live, mask, art_only, room}]``."""
    dep = dep or smconfig.deployment()
    in_room: dict[str, str] = {}
    try:
        for rid, r in smconfig.rooms(dep).items():
            for s in r.get("screens") or []:
                if isinstance(s, str):
                    in_room.setdefault(s, rid)
    except (OSError, ValueError):
        pass
    out = []
    for d in smconfig.destinations(dep):
        if d.get("headless"):
            continue
        live, mask = isinstance(d.get("live"), dict), isinstance(d.get("intensity_cfg"), dict)
        if live or mask or d["id"] in in_room:
            out.append({"id": d["id"], "name": str(d.get("name") or d["id"]), "live": live, "mask": mask,
                        "art_only": not live, "room": in_room.get(d["id"])})
    return out


def sonos_zones(dep: Optional[smconfig.Deployment] = None) -> dict[str, dict]:
    """``{zone: {"room": its Sonos room's name, "ip"}}`` of room/sonos.json, in file order: only with the ``sonos``
    capability, and only zones with an ``ip`` and a sane key. {} without Sonos or with an unreadable file."""
    dep = dep or smconfig.deployment()
    if not dep.enabled("sonos"):
        return {}
    try:
        doc = json.loads(dep.path("room", "sonos.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    zones = doc.get("zones") if isinstance(doc, dict) else None
    out: dict[str, dict] = {}
    for z, spec in (zones or {}).items() if isinstance(zones, dict) else ():
        if not isinstance(z, str) or not z or not set(z) <= ID_OK or not isinstance(spec, dict) or not spec.get("ip"):
            continue
        room = spec.get("room") if isinstance(spec.get("room"), str) and spec["room"].strip() else z
        out[z] = {"room": room.strip(), "ip": str(spec["ip"])}
    return out


def windows_sound(dep: Optional[smconfig.Deployment] = None) -> bool:
    """The deployment can play on the Windows host's speakers (deployment.yaml ``host.windows_python`` set)."""
    dep = dep or smconfig.deployment()
    return dep.host_path("windows_python") is not None


# ---------------------------------------------------------------- rows

def row(oid: str, label: str, *, room: Optional[str] = None, hint: Optional[str] = None) -> dict:
    """One output as every list shows it."""
    k = kind(oid) or "none"
    return {"id": oid, "kind": k, "kind_label": KIND_WORDS[k], "label": label, "room": room, "hint": hint,
            "calibrate": CALIBRATE[k]}


def static_outputs(dep: Optional[smconfig.Deployment] = None) -> list[dict]:
    """Every output the deployment has, devices aside (show_service knows those): the screens, the Windows host's
    speakers, the Sonos zones, then no sound."""
    dep = dep or smconfig.deployment()
    rows = [row(SCREEN_PREFIX + s["id"], s["name"], room=s.get("room"),
                hint="From the screen's own page: its TV, or the speakers of the computer showing it")
            for s in screens(dep)]
    if windows_sound(dep):
        rows.append(row(PC22, dep.words("windows_speakers", "This PC's speakers")))
    for z, spec in sonos_zones(dep).items():
        rows.append(row(SONOS_PREFIX + z, f"Sonos ({spec['room']})", room=spec["room"]))
    rows.append(row(NONE, NONE_LABEL))
    return rows


def device_row(oid: str, label: Optional[str] = None) -> Optional[dict]:
    """A device's row (show_service's devices list gives the id and the browser's name)."""
    if kind(oid) != "device":
        return None
    return row(oid, str(label or "A phone or tablet"), hint="Plays on that phone or tablet itself")


def standard(rows: Iterable[dict], with_latency: bool = True) -> list[dict]:
    """Rows a service built itself (``{id, label, ...}``: show_service's piano outputs, its devices) in the standard
    shape and words, each with its latency (smsound.latency.describe): what every picker shows. Unknown ids keep
    their own label."""
    from . import latency as L
    base = {r["id"]: r for r in static_outputs()}
    doc = L.read() if with_latency else None
    out = []
    for r in rows:
        oid = r.get("id") if isinstance(r, dict) else None
        if parse(oid) is None:
            continue
        std = base.get(oid) or (device_row(oid, r.get("label")) if kind(oid) == "device" else
                                row(oid, str(r.get("label") or oid)))
        out.append({**r, **std, **({"latency": L.describe(oid, doc=doc)} if with_latency else {})})
    return out


def for_consumer(kinds: Iterable[str], rows: Iterable[dict], why: str = "") -> list[dict]:
    """``rows`` marked for a consumer that delivers sound to ``kinds``: each gets ``available`` and, when not, ``why``
    (``why``: the consumer's words, e.g. "Karaoke can't play there yet")."""
    ok = set(kinds)
    out = []
    for r in rows:
        avail = r.get("kind") in ok
        out.append({**r, "available": avail, **({} if avail else {"why": why or "Can't play there"})})
    return out


def known(oid: Any, dep: Optional[smconfig.Deployment] = None, devices: Iterable[str] = ()) -> bool:
    """``oid`` is one of the deployment's outputs now (a device: one of ``devices``, else any well-formed one)."""
    k = kind(oid)
    if k is None:
        return False
    if k == "device":
        devs = set(devices)
        return not devs or oid in devs
    return any(r["id"] == oid for r in static_outputs(dep))


def label_of(oid: Any, dep: Optional[smconfig.Deployment] = None) -> str:
    """An output's label (a gone or unknown one: its id)."""
    for r in static_outputs(dep):
        if r["id"] == oid:
            return r["label"]
    return str(oid)
