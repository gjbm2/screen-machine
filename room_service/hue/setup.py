"""Inventory and Entertainment-area setup for the paired bridge.

  python -m room_service.hue.setup                       # list colour lights and areas
  python -m room_service.hue.setup --create "Screen Machine" --lights <light id>... [--positions "x,y,z;x,y,z"]

`--create` makes (or, when an area of that name exists, re-members) an Entertainment area holding exactly those
lights, so the hue.* virtual devices stream to their own area and never touch the house's existing ones.
"""

from __future__ import annotations

import argparse
import asyncio
import json

from .client import BridgeSecrets, HueClient


async def main_async(a) -> int:
    sec = BridgeSecrets.load(a.bridge)
    if sec is None:
        print("no paired bridge: run python -m room_service.hue.pair <ip>")
        return 1
    c = HueClient(sec)
    try:
        inv = await c.inventory()
        lights = {li["id"]: li for li in inv["lights"]}
        if not a.create:
            for li in sorted(inv["lights"], key=lambda x: (x["room"] or "", x["name"] or "")):
                if li["renderer"]:
                    print(f"{li['id']}  {li['room'] or '-':14s} {li['name']:32s} {li['model']} gamut {li['gamut_type']}")
            for ar in inv["areas"]:
                names = [next((li["name"] for li in inv["lights"] if li["entertainment"] in ch["service"]), "?")
                         for ch in ar["channels"]]
                print(f"area {ar['id']} {ar['name']!r} {ar['type']} {ar['status']}: {names}")
            return 0
        missing = [lid for lid in a.lights if lid not in lights or not lights[lid]["renderer"]]
        if missing:
            print(f"not streamable or unknown: {missing}")
            return 2
        pos = [tuple(float(v) for v in p.split(",")) for p in a.positions.split(";")] if a.positions else []
        locs = []
        for i, lid in enumerate(a.lights):
            x, y, z = pos[i] if i < len(pos) else (0.0, 0.0, 0.0)
            locs.append({"service": {"rid": lights[lid]["entertainment"], "rtype": "entertainment"},
                         "positions": [{"x": x, "y": y, "z": z}]})
        existing = next((ar for ar in inv["areas"] if ar["name"] == a.create), None)
        if existing:
            await c.put("entertainment_configuration", existing["id"],
                        {"locations": {"service_locations": locs}})
            area_id = existing["id"]
            print(f"updated area {area_id} {a.create!r}")
        else:
            res = await c.post("entertainment_configuration",
                               {"type": "entertainment_configuration", "metadata": {"name": a.create},
                                "configuration_type": a.type, "locations": {"service_locations": locs}})
            area_id = res["data"][0]["rid"]
            print(f"created area {area_id} {a.create!r}")
        area = (await c.get("entertainment_configuration", area_id))[0]
        print(json.dumps({"area": area_id, "channels": [{"channel_id": ch["channel_id"],
                                                          "light": next((lid for lid in a.lights
                                                                         if lights[lid]["entertainment"] in
                                                                         [m["service"]["rid"] for m in ch["members"]]),
                                                                        None)}
                                                         for ch in area.get("channels", [])]}, indent=1))
        return 0
    finally:
        await c.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bridge", help="bridge id (default: the only paired bridge)")
    ap.add_argument("--create", metavar="NAME")
    ap.add_argument("--type", default="music", choices=["screen", "music", "3dspace", "other"])
    ap.add_argument("--lights", nargs="*", default=[])
    ap.add_argument("--positions", help="x,y,z per light, -1..1, separated by ; (\"1,1,0;-1,1,0\")")
    raise SystemExit(asyncio.run(main_async(ap.parse_args())))


if __name__ == "__main__":
    main()
