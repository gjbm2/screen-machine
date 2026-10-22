"""Manual-test CLI for the RF/blinds spike. Run from experiments/rf-spike:

  .venv/bin/python -m rfspike hub                       # hub + every blind, from the hub's cache (no RF)
  .venv/bin/python -m rfspike set-key                   # store the Bloc hub key (0600, outside git)
  .venv/bin/python -m rfspike blinds read  <target>     # target: a group (disco), chNN, or "all"
  .venv/bin/python -m rfspike blinds watch <target> [--secs 120]   # print cache changes at 2 Hz (no RF)
  .venv/bin/python -m rfspike blinds down|up|stop <target> --confirm   # TRANSMITS: owner present only
  .venv/bin/python -m rfspike blinds set   <target> <0..100> --confirm  # TRANSMITS: targetPosition (restore test)
  .venv/bin/python -m rfspike blinds time  <chNN> down|up --confirm    # TRANSMITS: moves one blind, times it

Anything that transmits refuses to run without --confirm. Nothing here loops a transmit.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
import time

import os as _os

sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))))
from room_service.adapters.blinds import BlocBlinds, BlocHub, HubError   # one copy of the adapter: room_service's
from .config import load_blinds, load_hub_key, save_blinds, save_hub_key

POSITION_FOR = {"down": 100, "up": 0}


def resolve(cfg: dict, target: str) -> list[tuple[str, str]]:
    blinds = cfg["blinds"]
    if target == "all":
        names = list(blinds)
    elif target in cfg["groups"]:
        names = cfg["groups"][target]
        if not names:
            sys.exit(f"group {target!r} is empty: set its channels in config/room/rf/blinds.json")
    elif target in blinds:
        names = [target]
    else:
        sys.exit(f"unknown target {target!r}: use 'all', a group ({', '.join(cfg['groups'])}) or a channel")
    return [(n, blinds[n]["mac"]) for n in names]


def hub_from(cfg: dict, need_key: bool) -> BlocHub:
    key = load_hub_key()
    if need_key and not key:
        sys.exit("no hub key yet: run `.venv/bin/python -m rfspike set-key`")
    return BlocHub(cfg["hub"]["ip"], key)


def fmt(name: str, r) -> str:
    batt = f"{r.battery_v:.2f} V" if r.battery_v is not None else "-"
    return f"{name:6} pos {r.position!s:>3}  {r.operation:8} wireless {r.wireless:4} batt {batt:8} rssi {r.rssi}"


async def cmd_hub(cfg: dict) -> None:
    hub = hub_from(cfg, need_key=False)
    t0 = time.monotonic()
    resp = await hub.device_list()
    print(f"hub {hub.ip}  mac {resp['mac']}  fw {resp.get('fwVersion')}  protocol {resp.get('ProtocolVersion')}"
          f"  blinds {len(hub.children)}  rtt {1000 * (time.monotonic() - t0):.0f} ms"
          f"  key {'present' if hub.key else 'MISSING'}")
    by_mac = {b["mac"]: n for n, b in cfg["blinds"].items()}
    for mac in sorted(hub.children):
        print(" ", fmt(by_mac.get(mac, mac), await hub.read(mac)))


async def cmd_read(cfg: dict, target: str) -> None:
    hub = hub_from(cfg, need_key=False)
    for name, mac in resolve(cfg, target):
        print(fmt(name, await hub.read(mac)))


def adapter_from(cfg: dict, hub: BlocHub) -> BlocBlinds:
    travel = cfg.get("travel_time_s", {}).get("per_channel", {})
    longest = max((t for ch in travel.values() for t in ch.values()), default=60.0)
    return BlocBlinds(hub, {n: b["mac"] for n, b in cfg["blinds"].items()}, change_time_s=longest,
                      writes_enabled=True)              # the CLI's own gate is --confirm, with the owner present


async def cmd_watch(cfg: dict, target: str, secs: float) -> None:
    hub = hub_from(cfg, need_key=False)
    members, last, t0 = resolve(cfg, target), {}, time.monotonic()
    print(f"watching {len(members)} blind(s) for {secs:.0f} s (hub cache, 2 Hz, no RF)")
    while time.monotonic() - t0 < secs:
        for name, mac in members:
            try:
                r = await hub.read(mac)
            except HubError as e:
                print(f"{time.monotonic() - t0:7.1f}s {name}: {e}")
                continue
            now = (r.position, r.operation)
            if last.get(name) != now:
                print(f"{time.monotonic() - t0:7.1f}s {fmt(name, r)}")
                last[name] = now
        await asyncio.sleep(0.5)


async def cmd_move(cfg: dict, action: str, target: str, value: str | None = None) -> None:
    hub = hub_from(cfg, need_key=True)
    adapter = adapter_from(cfg, hub)
    members = [n for n, _ in resolve(cfg, target)]
    t0 = time.monotonic()
    if action == "stop":
        out = await adapter.stop(members)
    else:
        pos = POSITION_FOR.get(action) if action != "set" else BlocBlinds.position(float(value))
        out = await adapter.apply({m: pos for m in members})
    print(f"{action} {target}: {out.status}  ({time.monotonic() - t0:.1f} s)")
    for m, o in out.members.items():
        print(f"  {m}: {o.status} {o.detail}")


async def cmd_time(cfg: dict, channel: str, action: str) -> None:
    """Moves one blind and records, by polling the cache at 4 Hz: send→first cache change, send→arrival."""
    hub = hub_from(cfg, need_key=True)
    (name, mac), = resolve(cfg, channel)
    target = 100 if action == "down" else 0
    before = await hub.read(mac)
    print("before:", fmt(name, before))
    t0 = time.monotonic()
    await hub.write(mac, {"operation": 0 if action == "down" else 1})
    t_ack = time.monotonic() - t0
    first_change = None
    while time.monotonic() - t0 < 180:
        r = await hub.read(mac)
        t = time.monotonic() - t0
        if first_change is None and (r.position, r.operation) != (before.position, before.operation):
            first_change = t
            print(f"{t:6.2f}s first cache change: {fmt(name, r)}")
        if r.operation == "stopped" and r.position is not None and abs(r.position - target) <= 3:
            print(f"{t:6.2f}s arrived: {fmt(name, r)}")
            break
        await asyncio.sleep(0.25)
    else:
        print("no arrival report within 180 s")
        return
    print(f"write ack {1000 * t_ack:.0f} ms; first change {first_change}; arrival {t:.1f} s "
          f"(cache-observed; compare with the owner's stopwatch)")
    cfg.setdefault("travel_time_s", {}).setdefault("per_channel", {}).setdefault(name, {})[
        "lowered" if action == "down" else "raised"] = round(t, 1)
    save_blinds(cfg)


def main() -> None:
    ap = argparse.ArgumentParser(prog="rfspike")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("hub")
    sub.add_parser("set-key")
    b = sub.add_parser("blinds")
    b.add_argument("action", choices=["read", "watch", "down", "up", "stop", "set", "time"])
    b.add_argument("target")
    b.add_argument("arg", nargs="?", help="time: down|up; set: 0..100")
    b.add_argument("--secs", type=float, default=120)
    b.add_argument("--confirm", action="store_true", help="required for anything that transmits")
    args = ap.parse_args()
    cfg = load_blinds()

    if args.cmd == "hub":
        return asyncio.run(cmd_hub(cfg))
    if args.cmd == "set-key":
        key = getpass.getpass("Bloc hub key (16 chars, from the app: Settings > About, tap the icon 5 times): ").strip()
        if len(key) != 16:
            sys.exit(f"expected 16 characters, got {len(key)}")
        return print(f"saved to {save_hub_key(key)} (mode 0600)")
    if args.action in ("down", "up", "stop", "set", "time") and not args.confirm:
        sys.exit("this transmits and moves blinds: re-run with --confirm, with the owner present")
    if args.action == "read":
        asyncio.run(cmd_read(cfg, args.target))
    elif args.action == "watch":
        asyncio.run(cmd_watch(cfg, args.target, args.secs))
    elif args.action == "time":
        if args.arg not in ("down", "up"):
            sys.exit("usage: blinds time <chNN> down|up --confirm")
        asyncio.run(cmd_time(cfg, args.target, args.arg))
    elif args.action == "set":
        if args.arg is None:
            sys.exit("usage: blinds set <target> <0..100> --confirm")
        asyncio.run(cmd_move(cfg, "set", args.target, args.arg))
    else:
        asyncio.run(cmd_move(cfg, args.action, args.target))


if __name__ == "__main__":
    main()
