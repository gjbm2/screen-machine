"""Async CLIP v2 client for one Hue bridge, plus the paired secrets file (room_service.hue.pair writes it).

Rate rules (r7 §7): at most 3 requests in flight (the bridge answers 429 beyond that), about 10 light commands a
second; a 429/503 backs off and retries twice. The bridge's certificate is signed by Signify's own CA with the
bridge id as CN, so verification is off and the host is pinned by config instead.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp

from .pair import SECRETS_DIR

log = logging.getLogger("room_service.hue")


@dataclass
class BridgeSecrets:
    host: str
    bridge_id: str
    name: str
    model: str
    app_key: str
    clientkey: str

    @classmethod
    def load(cls, bridge_id: str | None = None, host: str | None = None) -> "BridgeSecrets | None":
        """The paired bridge by id, else by host, else the only one; None when nothing is paired."""
        files = sorted(SECRETS_DIR.glob("*.json")) if SECRETS_DIR.is_dir() else []
        recs = []
        for f in files:
            try:
                recs.append(json.loads(f.read_text()))
            except (OSError, ValueError):
                continue
        pick = [r for r in recs if (bridge_id and r.get("bridge_id") == bridge_id.lower())
                or (host and r.get("host") == host)] or (recs if len(recs) == 1 else [])
        if not pick:
            return None
        r = pick[0]
        return cls(host=host or r["host"], bridge_id=r["bridge_id"], name=r.get("name", ""),
                   model=r.get("model", ""), app_key=r["app_key"], clientkey=r["clientkey"])


class HueError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"hue {status}: {detail}")
        self.status = status


class HueClient:
    def __init__(self, secrets: BridgeSecrets, timeout_s: float = 5.0) -> None:
        self.secrets = secrets
        self.timeout = aiohttp.ClientTimeout(total=timeout_s)
        self._sem = asyncio.Semaphore(3)
        self._session: aiohttp.ClientSession | None = None
        self.reachable: bool | None = None
        self.last_error: str | None = None

    @property
    def base(self) -> str:
        return f"https://{self.secrets.host}/clip/v2/resource"

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def _req(self, method: str, path: str, body: Any = None) -> Any:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False), timeout=self.timeout)
        headers = {"hue-application-key": self.secrets.app_key}
        for attempt in range(3):
            async with self._sem:
                try:
                    async with self._session.request(method, f"{self.base}{path}", json=body,
                                                     headers=headers) as r:
                        text = await r.text()
                        if r.status in (429, 503) and attempt < 2:
                            await asyncio.sleep(0.5 * (attempt + 1))
                            continue
                        data = json.loads(text) if text else {}
                        if r.status >= 400:
                            errs = data.get("errors") if isinstance(data, dict) else None
                            raise HueError(r.status, "; ".join(e.get("description", "") for e in errs or [])
                                           or text[:200])
                        self.reachable, self.last_error = True, None
                        return data
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    self.reachable, self.last_error = False, f"{type(e).__name__}: {e}"
                    raise HueError(0, self.last_error) from e
        raise HueError(429, "bridge busy")

    async def get(self, rtype: str, rid: str | None = None) -> list[dict]:
        data = await self._req("GET", f"/{rtype}" + (f"/{rid}" if rid else ""))
        return data.get("data", []) if isinstance(data, dict) else []

    async def put(self, rtype: str, rid: str, body: dict) -> Any:
        return await self._req("PUT", f"/{rtype}/{rid}", body)

    async def post(self, rtype: str, body: dict) -> Any:
        return await self._req("POST", f"/{rtype}", body)

    async def delete(self, rtype: str, rid: str) -> Any:
        return await self._req("DELETE", f"/{rtype}/{rid}")

    async def inventory(self) -> dict:
        """Lights with their device names, models, gamut and entertainment service; rooms; entertainment areas."""
        lights, devices, ents, rooms, areas = await asyncio.gather(
            self.get("light"), self.get("device"), self.get("entertainment"), self.get("room"),
            self.get("entertainment_configuration"))
        dev = {d["id"]: d for d in devices}
        ent_by_owner = {e.get("owner", {}).get("rid"): e for e in ents}
        room_of = {}
        for r in rooms:
            for c in r.get("children", []):
                room_of[c.get("rid")] = r.get("metadata", {}).get("name")
        out = []
        for li in lights:
            owner = li.get("owner", {}).get("rid")
            d = dev.get(owner, {})
            e = ent_by_owner.get(owner, {})
            col = li.get("color") or {}
            out.append({"id": li["id"], "name": li.get("metadata", {}).get("name") or d.get("metadata", {}).get("name"),
                        "device": owner, "model": d.get("product_data", {}).get("product_name"),
                        "model_id": d.get("product_data", {}).get("model_id"),
                        "room": room_of.get(owner), "on": li.get("on", {}).get("on"),
                        "brightness": li.get("dimming", {}).get("brightness"),
                        "color": "xy" in col, "gamut_type": col.get("gamut_type"),
                        "gradient": bool(li.get("gradient")), "mode": li.get("mode"),
                        "entertainment": e.get("id"), "renderer": e.get("renderer", False)})
        return {"lights": out,
                "areas": [{"id": a["id"], "name": a.get("metadata", {}).get("name"), "status": a.get("status"),
                           "type": a.get("configuration_type"),
                           "active_streamer": (a.get("active_streamer") or {}).get("rid"),
                           "channels": [{"channel_id": c["channel_id"],
                                         "service": [m["service"]["rid"] for m in c.get("members", [])]}
                                        for c in a.get("channels", [])]}
                          for a in areas]}


async def resolve_area(client: HueClient, area_id: str | None = None) -> tuple[str, dict[str, int]]:
    """(area id, {light id: entertainment channel id}) for the area to stream: `area_id` if given and present, else
    the one named "Screen Machine", else the only one. Shared with show_service."""
    inv = await client.inventory()
    areas = inv["areas"]
    area = next((a for a in areas if a["id"] == area_id), None) if area_id else None
    area = area or next((a for a in areas if (a["name"] or "").lower() == "screen machine"), None)
    if area is None and len(areas) == 1:
        area = areas[0]
    if area is None:
        raise HueError(404, "no entertainment area: run python -m room_service.hue.setup")
    light_of_ent = {li["entertainment"]: li["id"] for li in inv["lights"] if li["entertainment"]}
    channels: dict[str, int] = {}
    for ch in area["channels"]:
        for svc in ch["service"]:
            if svc in light_of_ent:
                channels.setdefault(light_of_ent[svc], ch["channel_id"])
    return area["id"], channels
