"""The example room (example_room.json, ROOM_STATE_MODEL §2.2) on fake adapters, at §6's common starting point, plus
helpers for the scenario tests. Pure asyncio: the only I/O is the state file under pytest's tmp_path.
"""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from types import SimpleNamespace

from roomstate.coordinator import Coordinator
from roomstate.fakes import FakeEdge, FakeLevel, FakeSignals
from roomstate.model import RoomConfig

EXAMPLE = json.loads((Path(__file__).parents[1] / "example_room.json").read_text())
BLINDS0 = {"ch02": 0, "ch03": 80, "ch14": 100, "ch09": 50}       # ch09 is not in privacy_blinds: unmanaged
SCREENS = ["north-screen", "south-screen", "devtest"]
RADIO = {"uri": "x-rincon-mp3radio://radio"}
ORIGINAL = {"ch02": 0, "ch03": 80, "ch14": 100}
DOWN = {"ch02": 100, "ch03": 100, "ch14": 100}


def fast(raw: dict, delay: float = 0.01) -> dict:
    raw = copy.deepcopy(raw)
    for t in raw.get("transitions", []):
        for s in t["steps"]:
            if isinstance(s, dict) and "delay" in s:
                s["delay"] = delay
    return raw


def example(patch=None, delay: float = 0.01) -> dict:
    raw = fast(EXAMPLE, delay)
    if patch:
        patch(raw)
    return raw


def fake_adapters(ball_kind: str = "edge_discrete", blinds_edge: bool = False, screens=None) -> SimpleNamespace:
    """§6's starting point: blinds 0/80/100 (ch09 unmanaged), TVs hdmi/art_mode, screens art, mask follow, reloads on,
    DMX off, ball stopped, Sonos at 25 playing the radio."""
    t = 0.005                                              # travel time: a step costs about this much
    return SimpleNamespace(
        blinds=FakeEdge("blinds", list(BLINDS0)) if blinds_edge else FakeLevel("blinds", dict(BLINDS0), t, tolerance=3),
        tv=FakeLevel("tv", {"north-screen": "hdmi", "south-screen": "art_mode"}, t),
        screens=screens or FakeLevel("screens", {s: "art" for s in SCREENS}, t),
        mask=FakeLevel("sensor_mask", {"main": "follow"}, t),
        page=FakeLevel("page", {f"{s}.timed_reload": True for s in SCREENS}, t),
        plug=FakeLevel("plug", {"dmx_power": "off"}, t),
        ball=FakeEdge("mirror_ball", ["main"], kind=ball_kind),
        sonos=FakeLevel("sonos", {"lounge.volume": 25, "lounge.playback": RADIO}, t, tolerance=1),
        signals=FakeSignals("log"),
    )


def make_room(tmp_path, raw: dict | None = None, *, now=None, adapters: SimpleNamespace | None = None,
              without: tuple[str, ...] = (), margin_s: float = 0.2, **kw) -> SimpleNamespace:
    """r.c is the coordinator; r.log every apply() in order as (subsystem, desired, time); r.events every event."""
    a = adapters or fake_adapters(**kw)
    for name in without:                                   # no adapter: the config makes it a placeholder
        delattr(a, name)
    every = list(vars(a).values())
    log: list[tuple[str, dict, float]] = []
    for x in every:
        if x.kind == "action":
            continue
        orig = x.apply

        async def logged(desired, opts=None, _x=x, _orig=orig):
            log.append((_x.subsystem, dict(desired), time.time()))
            return await _orig(desired, opts)
        x.apply = logged
    cfg = RoomConfig(raw or example(), every)
    events: list[dict] = []
    path = tmp_path / "room" / "state.json"
    c = Coordinator(cfg, every, path, now=now or time.time, margin_s=margin_s,
                    on_event=lambda e: events.append({**copy.deepcopy(e), "t": time.time()}))
    return SimpleNamespace(c=c, cfg=cfg, log=log, events=events, path=path, every=every, a=a, **vars(a))


def restart(r, now=None, **kw) -> Coordinator:
    """A new coordinator on the same state file and devices (room_service restarted)."""
    return Coordinator(r.cfg, r.every, r.path, now=now or r.c.now, margin_s=r.c.margin_s, on_event=r.c.on_event, **kw)


async def boot(r) -> None:
    await r.c.start(reconcile=False)                       # returns once the boot transition is launched
    await r.c.wait_idle()


async def go(r, mode: str, source: str = "web") -> dict:
    await r.c.request_mode(mode, source)
    await r.c.wait_idle()
    return r.c.s["last_transition"]


def stored(r, slot: str = "pre_live") -> dict:
    return {t: e["value"] for t, e in r.c.s["stored"].get(slot, {}).items()}


def sent(r, subsystem: str) -> list[dict]:
    return [d for s, d, _ in r.log if s == subsystem]


def order(r) -> list[str]:
    return [s for s, _, _ in r.log]


def privacy(r) -> dict:
    return {m: r.blinds.value[m] for m in ORIGINAL}


def row(tr: dict, subsystem: str) -> dict | None:
    return next((x for x in tr["steps"] if x["subsystem"] == subsystem), None)


def signals_sent(r, msg: str) -> int:
    return sum(1 for _, _, args in r.signals.sent if args.get("msg") == msg)


def started(r) -> list[dict]:
    return [e["data"] for e in r.events if e["type"] == "mode.transition" and e["data"]["phase"] == "started"]
