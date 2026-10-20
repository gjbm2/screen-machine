"""sonos: Sonos zones through SoCo (S2, UPnP on; plain HTTP to :1400, which works from WSL; no SSDP or events needed).

Device config: config/room/sonos.json {"zones": {"drawing-room": {"room": "Drawing Room", "ip": "192.168.1.17"}}}.
Members per zone `<zone>`:
  <zone>.volume       level, 0..100 (tolerance 1); opts {"over_s": n} ramps it
  <zone>.playback     level: "paused" | "stopped" | {"favourite": "<Sonos favourite title>"} |
                      {"sonos_playlist": "<Sonos playlist title>"} | {"spotify_link": "<share link>"} | {"uri": uri},
                      the object forms optionally with shuffle (bool), volume (0..100: the fade target) and only_if_idle
                      (bool: leave music that is already playing alone).
                      read() gives the playback *context*, never the track: what we started reads back as we said it
                      (same context URI), a playing favourite as {"favourite": ...}, anything else as {"uri": ...}; so a
                      store captures the station or playlist and a retrieve brings it back
  <zone>.now_playing  read-only: {state, source, title, artist, album, track_id, duration, position, mute, group}
SoCo is blocking, so every call runs in a thread with a timeout. Topology (the group coordinator), favourites and Sonos
playlists are cached for 60 s; transport state and the track are read on every poll (poll_s 2).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from ..roomstate.contract import Adapter, Outcome, Placeholder, Reading
from ..roomstate.schema import range_schema

log = logging.getLogger("room_service.sonos")

PLAYBACK_WORDS = ("paused", "stopped")
CONTEXTS = ("favourite", "sonos_playlist", "spotify_link", "uri")    # what a playback object plays (exactly one)
SPOTIFY_LINK = r"^(https://open.spotify.com/|spotify:)"
CACHE_S = 60.0
PLAYING = ("PLAYING", "TRANSITIONING")
EXTRAS = ("shuffle", "volume", "only_if_idle")        # options any playback object may carry
FIELD_WORDS = {"volume": "volume", "playback": "music", "now_playing": "now playing"}
SOURCE_WORDS = {"tv": "TV sound", "line-in": "Line-in", "radio": "A radio stream", "queue": "The Sonos queue",
                "spotify": "Spotify", "group": "Grouped with another room"}
# a favourite's kind, from the DIDL class of what it points at (SoCo: favourite.reference.item_class)
KINDS = (("audioBroadcast", "radio"), ("radioShow", "radio show"), ("playlistContainer", "playlist"),
         ("musicAlbum", "album"), ("musicTrack", "track"))


def _ctx(uri: str | None) -> str:
    """A context URI without its query string, unquoted, so favourites and live media compare equal."""
    return unquote((uri or "").split("?", 1)[0])


def _source(uri: str) -> str:
    for prefix, name in (("x-sonos-htastream", "tv"), ("x-rincon-stream", "line-in"), ("x-rincon-queue", "queue"),
                         ("x-sonosapi-hls", "radio"), ("x-sonosapi-stream", "radio"), ("x-rincon-mp3radio", "radio"),
                         ("aac:", "radio"), ("x-sonosapi-radio", "radio"), ("x-sonos-spotify", "spotify"),
                         ("x-rincon:", "group")):
        if uri.startswith(prefix):
            return name
    return "other" if uri else "none"


def _kind(fav) -> str | None:
    cls = str(getattr(getattr(fav, "reference", None), "item_class", "") or "")
    return next((word for key, word in KINDS if cls.endswith(key)), None)


def _add_share_link(co, link: str) -> None:
    """A Spotify share link into the (cleared) queue via SoCo's ShareLinkPlugin. Not yet tried against the player."""
    from soco.plugins.sharelink import ShareLinkPlugin
    ShareLinkPlugin(co).add_share_link_to_queue(link)


class _Zone:
    def __init__(self, key: str, room: str, ip: str, factory) -> None:
        self.key, self.room, self.ip, self.factory = key, room, ip, factory
        self.lock = threading.Lock()                      # SoCo isn't safe to drive from two threads at once
        self._dev = self._co = None
        self._co_at = 0.0
        self._favs: list = []
        self._favs_at = 0.0
        self._pls: list = []
        self._pls_at = 0.0
        self.played: tuple[str, dict] | None = None      # (context URI, the value we started it with)
        self.gen = 0                                      # bumped by each change: a running ramp sees it and stops

    def dev(self):
        if self._dev is None:
            self._dev = self.factory(self.ip)
        return self._dev

    def coordinator(self):
        if self._co is None or time.monotonic() - self._co_at > CACHE_S:
            self._co, self._co_at = self.dev().group.coordinator, time.monotonic()
        return self._co

    def favourites(self) -> list:
        if not self._favs_at or time.monotonic() - self._favs_at > CACHE_S:
            self._favs, self._favs_at = list(self.coordinator().music_library.get_sonos_favorites()), time.monotonic()
        return self._favs

    def playlists(self) -> list:
        if not self._pls_at or time.monotonic() - self._pls_at > CACHE_S:
            self._pls, self._pls_at = list(self.coordinator().get_sonos_playlists()), time.monotonic()
        return self._pls

    @staticmethod
    def fav_uri(f) -> str:
        res = getattr(f, "resources", None) or []
        return res[0].uri if res else ""

    def read(self) -> dict[str, Any]:
        with self.lock:
            dev, co = self.dev(), self.coordinator()
            state = co.get_current_transport_info()["current_transport_state"]
            uri = co.avTransport.GetMediaInfo([("InstanceID", 0)]).get("CurrentURI", "") or ""
            track = co.get_current_track_info()
            favs = self.favourites()                      # refreshed every 60 s: also feed the editor's pickers
            try:
                self.playlists()
            except Exception as e:                        # a picker list: never worth making the zone unavailable
                log.debug("%s: Sonos playlists not read: %s", self.key, e)
            if state in PLAYING:
                fav = next((f.title for f in favs if _ctx(self.fav_uri(f)) == _ctx(uri) and uri), None)
                if self.played and uri and self.played[0] == _ctx(uri):
                    playback: Any = dict(self.played[1])  # ours: reads back as we said it (shuffle, volume ...)
                else:
                    playback = {"favourite": fav} if fav else {"uri": uri}
            else:
                playback = "paused" if state == "PAUSED_PLAYBACK" else "stopped"
            return {
                "volume": int(dev.volume),
                "playback": playback,
                "now_playing": {"state": state.lower(), "source": _source(uri), "title": track.get("title") or "",
                                "artist": track.get("artist") or "", "album": track.get("album") or "",
                                "track_id": track.get("uri") or "", "duration": track.get("duration") or "",
                                "position": track.get("position") or "", "mute": bool(dev.mute),
                                "group": [m.player_name for m in dev.group.members]},
            }

    STEP_S = 0.2                                          # ramp step (owner, 16 Oct: smooth, never a jump)

    def _vol(self) -> int:
        with self.lock:
            return int(self.dev().volume)

    def _set_vol(self, v: int) -> None:
        with self.lock:
            self.dev().volume = int(v)

    def _state(self) -> str:
        with self.lock:
            return self.coordinator().get_current_transport_info()["current_transport_state"]

    def ramp(self, target: int, over_s: float, gen: int) -> bool:
        """From the current volume to `target` in ~STEP_S steps. False if a newer change took over (the latest wins,
        and it carries on from wherever this one stopped)."""
        start = self._vol()
        steps = max(1, round(over_s / self.STEP_S)) if over_s > 0 else 1
        for i in range(1, steps + 1):
            if self.gen != gen:
                return False
            self._set_vol(round(start + (target - start) * i / steps))
            if i < steps:
                time.sleep(over_s / steps)
        return self.gen == gen

    @staticmethod
    def _queue(co, item) -> None:
        co.clear_queue()
        co.add_to_queue(item)
        co.play_from_queue(0)

    def _play(self, v: dict) -> None:
        with self.lock:
            co = self.coordinator()
            if "favourite" in v:
                f = next((f for f in self.favourites() if f.title == v["favourite"]), None)
                if f is None:
                    raise ValueError(f"no Sonos favourite {v['favourite']!r}")
                try:
                    co.play_uri(self.fav_uri(f), getattr(f, "resource_meta_data", "") or "")
                except Exception:                         # a playlist or album: through the queue
                    self._queue(co, f.reference)
            elif "sonos_playlist" in v:
                p = next((p for p in self.playlists() if p.title == v["sonos_playlist"]), None)
                if p is None:
                    raise ValueError(f"no Sonos playlist {v['sonos_playlist']!r}")
                self._queue(co, p)
            elif "spotify_link" in v:
                co.clear_queue()
                _add_share_link(co, v["spotify_link"])
                co.play_from_queue(0)
            else:
                co.play_uri(v["uri"])
            if "shuffle" in v:
                try:
                    co.shuffle = bool(v["shuffle"])       # the play mode: only a queue shuffles (a stream refuses)
                except Exception as e:
                    log.info("%s: shuffle not set (%s)", self.key, e)
            uri = co.avTransport.GetMediaInfo([("InstanceID", 0)]).get("CurrentURI", "") or ""
            self.played = (_ctx(uri), dict(v))

    def change(self, volume: int | None, playback: Any, over_s: float, gen: int) -> str:
        """One mode-driven change, smoothly. Pause or stop: fade out, pause, then put the volume back silently so the
        next play isn't at zero. Play: fade out what's playing, switch, fade up from 0 to the target (the volume
        member, else the playback's own volume, else where the volume was). Volume alone: a ramp while playing, a
        silent set while not. Returns a note when only_if_idle left playing music alone ("" otherwise)."""
        playing = self._state() in PLAYING
        before = self._vol()
        note = ""
        if isinstance(playback, dict) and playback.get("only_if_idle") and playing:
            playback, note = None, "music already playing: left alone (only_if_idle)"
        if playback in ("paused", "stopped"):
            if playing:
                if not self.ramp(0, over_s, gen):
                    return note
                with self.lock:
                    co = self.coordinator()
                    co.pause() if playback == "paused" else co.stop()
            self._set_vol(before if volume is None else volume)
        elif playback is not None:
            if playing and not self.ramp(0, over_s, gen):
                return note
            self._set_vol(0)
            self._play(playback)
            self.ramp(volume if volume is not None else playback.get("volume", before), over_s, gen)
        elif volume is not None:
            if playing:
                self.ramp(volume, over_s, gen)
            else:
                self._set_vol(volume)
        return note


class SonosAdapter(Adapter):
    subsystem = "sonos"
    subsystem_label = "Music"
    category = "device"
    kind = "level"
    change_time_s = 10.0
    poll_s = 2.0
    read_timeout_s = 4.0
    tolerance = 1
    poll_all = True                                       # now_playing is read even though no mode sets it

    def __init__(self, zones: dict[str, dict], factory=None, ramp_s: float = 4.0) -> None:
        """ramp_s: how long every mode-driven volume change fades (sonos.json "ramp_s"); a mode's opts.over_s wins."""
        self.ramp_s = ramp_s
        if factory is None:
            import soco
            factory = soco.SoCo
        self.zones = {k: _Zone(k, z.get("room", k), z["ip"], factory) for k, z in zones.items()}
        self.members = [f"{k}.{m}" for k in self.zones for m in ("volume", "playback", "now_playing")]

    def _split(self, member: str) -> tuple[_Zone, str]:
        zone, field = member.rsplit(".", 1)
        return self.zones[zone], field

    def label(self, member: str) -> str:
        z, field = self._split(member)
        return f"{z.room} {FIELD_WORDS[field]}"

    def value_schema(self, member: str) -> dict:
        z, field = self._split(member)
        if field == "volume":
            return range_schema(0, 100, "Volume", unit="%", ends=("Silent", "Loud"))
        if field == "playback":
            return self._playback_schema(z)
        return {"type": "object", "readOnly": True, "x-ui": {"label": "Now playing", "widget": "readonly"}}

    @staticmethod
    def _playback_schema(z: _Zone) -> dict:
        """Pause, stop, or play one thing: the household's real favourites and Sonos playlists from the poll's cache
        (no I/O here; empty until the first poll), or a Spotify link. {"uri": ...} is accepted but not offered."""
        extras = {"shuffle": {"type": "boolean", "x-ui": {"label": "Shuffle"}},
                  "volume": range_schema(0, 100, "Volume", unit="%", ends=("Silent", "Loud")),
                  "only_if_idle": {"type": "boolean", "x-ui": {"label": "Don't interrupt music that's already playing"}}}
        for x in extras.values():
            x.pop("default", None)

        def play(key: str, title: str, value: dict) -> dict:
            return {"type": "object", "title": title, "required": [key], "additionalProperties": False,
                    "properties": {key: value, **extras}}
        favs = [f.title for f in z._favs]
        kinds = {f.title: k for f in z._favs if (k := _kind(f))}
        pls = [p.title for p in z._pls]
        return {"oneOf": [
            {"const": "paused", "title": "Pause"},
            {"const": "stopped", "title": "Stop"},
            play("favourite", "A favourite", {"type": "string", "enum": favs, "x-ui": {
                "label": "Favourite", "kinds": kinds, "description": "Edit favourites in the Sonos app"}}),
            play("sonos_playlist", "A Sonos playlist", {"type": "string", "enum": pls, "x-ui": {"label": "Playlist"}}),
            play("spotify_link", "A Spotify link", {"type": "string", "pattern": SPOTIFY_LINK, "x-ui": {
                "label": "Spotify link", "description": "Share > Copy link in Spotify"}}),
        ], "default": "paused", "x-ui": {
            "label": "Music", "default_policy": "ignore",            # people change the music: never fought
            "store_if_labels": [{"value": list(CONTEXTS), "label": "Only if music was playing"}]}}

    def normalize(self, member: str, value: Any) -> Any:
        z, field = self._split(member)
        if field == "volume":
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 100:
                raise ValueError("volume is 0..100")
            return int(round(value))
        if field == "playback":
            if value in PLAYBACK_WORDS:
                return value
            if isinstance(value, dict):
                return self._normalize_play(value)
            raise ValueError('playback is "paused", "stopped" or an object: {"favourite": name}, '
                             '{"sonos_playlist": name}, {"spotify_link": link} or {"uri": uri}')
        raise ValueError(f"{member} is read-only")

    def _normalize_play(self, value: dict) -> dict:
        ctx = [k for k in CONTEXTS if k in value]
        if len(ctx) != 1 or not isinstance(value[ctx[0]], str) or not value[ctx[0]]:
            raise ValueError(f"playback plays exactly one of {list(CONTEXTS)}")
        if extra := sorted(set(value) - set(CONTEXTS) - set(EXTRAS)):
            raise ValueError(f"playback has unknown keys {extra}; it takes {list(EXTRAS)}")
        if ctx[0] == "spotify_link" and not re.match(SPOTIFY_LINK, value["spotify_link"]):
            raise ValueError("a Spotify link starts https://open.spotify.com/ or spotify:")
        out = dict(value)
        for k in ("shuffle", "only_if_idle"):
            if k in out and not isinstance(out[k], bool):
                raise ValueError(f"{k} is true or false")
        if "volume" in out:
            v = out["volume"]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 100:
                raise ValueError("volume is 0..100")
            out["volume"] = int(round(v))
        return out

    def show(self, member: str, value: Any) -> str:
        if isinstance(value, dict) and (k := next((k for k in CONTEXTS[:2] if k in value), None)):
            return f"play {value[k]}"
        return value if isinstance(value, str) else json.dumps(value, separators=(",", ":"))

    def words(self, member: str, value: Any) -> str:
        if not isinstance(value, dict):
            return {"paused": "Paused", "stopped": "Stopped"}.get(value, super().words(member, value))
        what = (value.get("favourite") or value.get("sonos_playlist")
                or ("a Spotify link" if "spotify_link" in value else "a stream"))
        bits = [f"Play {what}"] + (["shuffled"] if value.get("shuffle") else []) \
            + ([f"at {value['volume']}%"] if "volume" in value else []) \
            + (["unless music is already playing"] if value.get("only_if_idle") else [])
        return ", ".join(bits)

    async def _call(self, fn, *args):
        return await asyncio.wait_for(asyncio.to_thread(fn, *args), self.read_timeout_s + 6)

    async def read(self, members: list[str]) -> dict[str, Reading]:
        out = {}
        for zk in dict.fromkeys(m.rsplit(".", 1)[0] for m in members):
            vals = await self._call(self.zones[zk].read)          # raises when the player can't be reached
            for m in members:
                if m.startswith(zk + "."):
                    out[m] = Reading(vals[m.rsplit(".", 1)[1]])
        return out

    async def apply(self, desired: dict[str, Any], opts: dict | None = None) -> Outcome:
        over = float((opts or {}).get("over_s", self.ramp_s))
        results: dict[str, Outcome] = {}
        by_zone: dict[str, dict[str, Any]] = {}
        for m, v in desired.items():
            z, field = self._split(m)
            if field not in ("volume", "playback"):
                results[m] = Outcome("failed", "read-only", retry=False)
            else:
                by_zone.setdefault(z.key, {})[field] = v
        for zk, fields in by_zone.items():
            z, members = self.zones[zk], [f"{zk}.{f}" for f in fields]
            z.gen += 1
            try:
                note = await asyncio.wait_for(asyncio.to_thread(z.change, fields.get("volume"), fields.get("playback"),
                                                                over, z.gen), over * 3 + 15)
            except asyncio.CancelledError:
                z.gen += 1                                # stop the ramp: the next change carries on from here
                raise
            except ValueError as e:                       # e.g. no such favourite: retrying won't help
                results.update({m: Outcome("failed", str(e), retry=False) for m in members})
                continue
            except Exception as e:
                z.gen += 1
                results.update({m: Outcome("failed", f"{type(e).__name__}: {e}") for m in members})
                continue
            for m in members:
                if note and m.endswith(".playback"):      # only_if_idle: the music already playing was left alone
                    results[m] = Outcome("ok", note)
                else:
                    results[m] = await self._confirm(m, desired[m])
        rank = {"ok": 0, "unconfirmed": 1, "failed": 2}
        worst = max((o.status for o in results.values()), key=rank.get, default="ok")
        return Outcome(worst, "; ".join(f"{m}: {o.detail}" for m, o in results.items() if o.status != "ok"),
                       results, retry=all(o.retry for o in results.values()))

    async def _confirm(self, member: str, v: Any) -> Outcome:
        deadline = time.monotonic() + self.change_time_s
        got = None
        while time.monotonic() < deadline:
            try:
                got = (await self.read([member]))[member].value
            except Exception:
                got = None
            if got == v or (isinstance(v, int) and isinstance(got, int) and abs(got - v) <= self.tolerance) \
                    or (v == "paused" and got == "stopped"):  # nothing was playing: paused or stopped is the same here
                return Outcome("ok")
            await asyncio.sleep(0.5)
        return Outcome("unconfirmed", f"reads {self.show(member, got)}")


def from_config(path: str | Path | None = None) -> Adapter | None:
    """SonosAdapter for config/room/sonos.json; a Placeholder (same members) without SoCo; None without the file."""
    p = Path(path) if path else Path(__file__).resolve().parents[2] / "config" / "room" / "sonos.json"
    try:
        raw = json.loads(p.read_text())
        zones = raw["zones"]
    except (OSError, ValueError, KeyError):
        return None
    try:
        return SonosAdapter(zones, ramp_s=float(raw.get("ramp_s", 4.0)))
    except ImportError:
        return Placeholder("sonos", [f"{k}.{m}" for k in zones for m in ("volume", "playback", "now_playing")])
