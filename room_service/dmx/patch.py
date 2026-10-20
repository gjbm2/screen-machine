"""The DMX patch: virtual devices on Maestro's Art-Net universe (AV_PROGRAMME v1 K2; config/room/patch.yaml).

A **virtual device** is one thing in the room that Maestro drives as if it were a DMX fixture: a Frame TV (the
renderer reads it from the show bus) or a Hue light (room_service streams it to the bridge). Each device has an id
(`screen.north`, `hue.sofa`), a `kind` that says who consumes it, a `profile` (its channel layout, the same one
patched in Maestro) and a 1-based start `address`.

```yaml
artnet: {bind: "0.0.0.0:6454", universe: 0, stale_after_ms: 1500}
profiles:
  hue-drgb:
    label: "SM Hue (Dim RGB)"                       # the Maestro fixture profile's name
    channels:
      - {attr: MASTER DIMMER, field: dimmer}        # Maestro attribute type -> our field
      - {attr: RED, field: r}
      - {attr: GREEN, field: g}
      - {attr: BLUE, field: b}
devices:
  hue.sofa: {label: Sofa lamp, kind: hue, profile: hue-drgb, address: 33, light: <CLIP v2 light id>}
```

**Fields.** Channel values are 0-255 on the wire and 0-1 here, except: a field with `range: [lo, hi]` is mapped
linearly onto it (pan/tilt are -1..1); a field with `raw: true` stays the DMX integer (GOBO steps -> `effect`). The
decoded device carries `rgb` [r, g, b] when it has r/g/b fields, multiplied by `dimmer` when it has one; `level`
defaults to max(rgb) when the profile doesn't patch it. Fields named `global.<x>` feed the bus's `global` block
(e.g. ACTIVITY): the largest value among the devices that carry them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

KINDS = ("screen", "hue", "other")
DEVICE_ID_RE = re.compile(r"[a-z][a-z0-9_-]*\.[a-z0-9][a-z0-9_-]*")
FIELD_RE = re.compile(r"(global\.)?[a-z][a-z0-9_]*")
DEFAULT_PATCH = Path(__file__).resolve().parents[2] / "config" / "room" / "patch.yaml"


class PatchError(ValueError):
    pass


@dataclass
class Channel:
    attr: str                       # Maestro attribute type (documentation + the Maestro profile we hand-build)
    field: str                      # our name for the value
    lo: float = 0.0
    hi: float = 1.0
    raw: bool = False
    default: int = 0                # what Maestro's static value should be (MASTER DIMMER: 255); informational


@dataclass
class Profile:
    name: str
    label: str
    channels: list[Channel]

    @property
    def footprint(self) -> int:
        return len(self.channels)


@dataclass
class Device:
    id: str
    label: str
    kind: str
    profile: Profile
    address: int                    # 1-based start channel
    extra: dict = field(default_factory=dict)   # kind-specific keys (hue: light; screen: screen)

    @property
    def end(self) -> int:
        return self.address + self.profile.footprint - 1

    def decode(self, dmx: bytes | bytearray | memoryview) -> dict[str, Any]:
        """This device's values from a universe's channel data (shorter data reads as zeros)."""
        out: dict[str, Any] = {}
        base = self.address - 1
        for i, ch in enumerate(self.profile.channels):
            j = base + i
            v = dmx[j] if j < len(dmx) else 0
            if ch.raw:
                out[ch.field] = v
            else:
                out[ch.field] = round(ch.lo + (ch.hi - ch.lo) * v / 255.0, 4)
        if {"r", "g", "b"} <= out.keys():
            dim = out.pop("dimmer", 1.0)
            out["rgb"] = [round(out.pop(k) * dim, 4) for k in ("r", "g", "b")]
            out.setdefault("level", max(out["rgb"]))
        return out

    def describe(self) -> dict:
        return {"id": self.id, "label": self.label, "kind": self.kind, "profile": self.profile.name,
                "address": self.address, "end": self.end, "footprint": self.profile.footprint, **self.extra}


@dataclass
class ArtNetConfig:
    bind_host: str = "0.0.0.0"
    bind_port: int = 6454
    universe: int = 0               # the 15-bit Port-Address: net << 8 | subnet << 4 | universe
    stale_after_ms: int = 1500


@dataclass
class Patch:
    artnet: ArtNetConfig
    profiles: dict[str, Profile]
    devices: dict[str, Device]
    path: Path | None = None
    hue: dict = field(default_factory=dict)     # bridge, host, area, stop_after_s, writes_enabled (output side)

    def of_kind(self, kind: str) -> list[Device]:
        return [d for d in self.devices.values() if d.kind == kind]

    def decode(self, dmx: bytes | bytearray | memoryview) -> tuple[dict[str, dict], dict[str, float]]:
        """(fixtures, global) for one universe frame."""
        fixtures: dict[str, dict] = {}
        sums: dict[str, list[float]] = {}
        for d in self.devices.values():
            vals = d.decode(dmx)
            for k in [k for k in vals if k.startswith("global.")]:
                sums.setdefault(k[7:], []).append(vals.pop(k))
            fixtures[d.id] = vals
        return fixtures, {k: round(max(v), 4) for k, v in sums.items()}   # max: an unpatched fixture reads 0

    def describe(self) -> dict:
        a = self.artnet
        return {"path": str(self.path) if self.path else None,
                "artnet": {"bind": f"{a.bind_host}:{a.bind_port}", "universe": a.universe,
                           "stale_after_ms": a.stale_after_ms},
                "profiles": {n: {"label": p.label, "footprint": p.footprint,
                                 "channels": [{"offset": i + 1, "attr": c.attr, "field": c.field,
                                               **({"range": [c.lo, c.hi]} if (c.lo, c.hi) != (0.0, 1.0) else {}),
                                               **({"raw": True} if c.raw else {}),
                                               **({"default": c.default} if c.default else {})}
                                              for i, c in enumerate(p.channels)]}
                             for n, p in self.profiles.items()},
                "devices": [d.describe() for d in sorted(self.devices.values(), key=lambda d: d.address)]}


def encode(device, values: dict) -> dict[int, int]:
    """1-based channel -> DMX value for one device from field values (rgb expands to r, g, b; dimmer defaults full).
    A channel with a `default` (MASTER DIMMER, the screens' INTENSITY: 255) that isn't given is written at its
    default: DMX has no "missing", and an unwritten byte reads 0 (dark)."""
    vals = dict(values)
    if "rgb" in vals:
        r, g, b = vals.pop("rgb")
        vals.update(r=r, g=g, b=b)
        vals.setdefault("dimmer", 1.0)
    out = {}
    for i, ch in enumerate(device.profile.channels):
        if ch.field not in vals:
            if ch.default:
                out[device.address + i] = max(0, min(255, ch.default))
            continue
        v = vals[ch.field]
        dmx = int(v) if ch.raw else round(255 * (float(v) - ch.lo) / (ch.hi - ch.lo))
        out[device.address + i] = max(0, min(255, dmx))
    return out


def _profile(name: str, raw: Any) -> Profile:
    if not isinstance(raw, dict) or not isinstance(raw.get("channels"), list) or not raw["channels"]:
        raise PatchError(f"profile {name}: needs a non-empty channels list")
    chans, seen = [], set()
    for i, c in enumerate(raw["channels"]):
        if not isinstance(c, dict) or not isinstance(c.get("field"), str) or not FIELD_RE.fullmatch(c["field"]):
            raise PatchError(f"profile {name} channel {i + 1}: needs a field (lower_snake, or global.<name>)")
        if c["field"] in seen:
            raise PatchError(f"profile {name}: field {c['field']} twice")
        seen.add(c["field"])
        lo, hi = c.get("range", [0.0, 1.0])
        chans.append(Channel(attr=str(c.get("attr", "")), field=c["field"], lo=float(lo), hi=float(hi),
                             raw=bool(c.get("raw", False)), default=int(c.get("default", 0))))
    return Profile(name=name, label=str(raw.get("label", name)), channels=chans)


def parse_patch(raw: Any, path: Path | None = None) -> Patch:
    if not isinstance(raw, dict):
        raise PatchError("patch: expected a mapping")
    a = raw.get("artnet") or {}
    host, _, port = str(a.get("bind", "0.0.0.0:6454")).rpartition(":")
    universe = int(a.get("universe", 0))
    if not 0 <= universe < 1 << 15:
        raise PatchError("artnet.universe: 0..32767 (the 15-bit Port-Address)")
    art = ArtNetConfig(bind_host=host or "0.0.0.0", bind_port=int(port), universe=universe,
                       stale_after_ms=int(a.get("stale_after_ms", 1500)))
    profiles = {n: _profile(n, p) for n, p in (raw.get("profiles") or {}).items()}
    devices: dict[str, Device] = {}
    for did, d in (raw.get("devices") or {}).items():
        if not DEVICE_ID_RE.fullmatch(str(did)):
            raise PatchError(f"device {did!r}: ids are <kind>.<name>, lower case (screen.north, hue.sofa)")
        if not isinstance(d, dict):
            raise PatchError(f"device {did}: expected a mapping")
        kind = d.get("kind", did.split(".", 1)[0])
        if kind not in KINDS:
            raise PatchError(f"device {did}: kind must be one of {', '.join(KINDS)}")
        prof = profiles.get(d.get("profile"))
        if prof is None:
            raise PatchError(f"device {did}: unknown profile {d.get('profile')!r}")
        addr = d.get("address")
        if not isinstance(addr, int) or not 1 <= addr <= 512 - prof.footprint + 1:
            raise PatchError(f"device {did}: address must be 1..{512 - prof.footprint + 1} for {prof.name}")
        extra = {k: v for k, v in d.items() if k not in ("label", "kind", "profile", "address")}
        devices[did] = Device(id=did, label=str(d.get("label", did)), kind=kind, profile=prof, address=addr,
                              extra=extra)
    used: dict[int, str] = {}
    for dev in devices.values():
        for ch in range(dev.address, dev.end + 1):
            if ch in used:
                raise PatchError(f"device {dev.id}: channel {ch} overlaps {used[ch]}")
            used[ch] = dev.id
    return Patch(artnet=art, profiles=profiles, devices=devices, path=path, hue=dict(raw.get("hue") or {}))


def load_patch(path: str | Path | None = None) -> Patch:
    p = Path(path) if path else DEFAULT_PATCH
    return parse_patch(yaml.safe_load(p.read_text()) or {}, p)
