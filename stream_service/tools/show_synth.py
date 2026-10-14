"""show-synth (dev only): a stand-in for Maestro + room_service's show bus (LIVE_V1 proposal §1.12, K3 frame shape).

Publishes K3 ``show.state`` frames on ws://127.0.0.1:<port>/v1/show at Art-Net-like cadence (40 Hz, jittered),
driven by a synthetic song: tempo, beats with accents, sections with their own energy, colours and effect.

  python -m stream_service.tools.show_synth --port 8711 --song pop --bpm 124 --seed 7 [--record truth.jsonl]

Handshake (K3): client sends {"type":"hello","v":1,"role":...,"client_id":...,"token":...};
server replies {"type":"welcome","schema":1,"rate_hz":40,"stale_after_ms":250}.  The token is checked against
~/.config/screen-machine/show_bus.token when that file exists (or --token-file), unless --no-auth; the bus binds
127.0.0.1 only.  ``t_ms`` is time.monotonic() (CLOCK_MONOTONIC) in ms, like room_service stamps it.  Frames
follow K3 as amended (top-level ``feed``, formerly ``look``).  Sections reach the renderer only as
effect / variant / activity values; the truth record's section boundaries only score follow_dmx -> cue latency.

Runtime control (dev only, same port, no auth beyond the 127.0.0.1 bind; stream_service proxies it as /dev/synth):

  GET  /v1/control -> {song, bpm, target_bpm, ramp, pulses, section, bar, beat, pending_song, songs}
  POST /v1/control  (Content-Type: application/json; any of)
       {"bpm": 140}                              tempo 40-200 now; the beat position is continuous (no jump)
       {"ramp": {"to_bpm": 90, "over_s": 16}}    a linear tempo ramp (beat position = the integral of tempo)
       {"song": "techno"}                        switch style at the next bar (tempo kept unless bpm is given)
       {"pulses": false}                         pulse / level stop hitting (a breakdown: the follower unlocks);
                                                 true brings them back from the next beat (never mid-beat)
       {"next_section": true}                    jump to the next section now

Tempo changes keep the truth record exact: each beat's time is the inverse of the beat-position integral, and
every applied change is recorded as a ``control`` event.
"""
from __future__ import annotations

import argparse
import asyncio
import bisect
import colorsys
import json
import math
import pathlib
import random
import signal
import time
from dataclasses import dataclass
from typing import Optional, TextIO

from aiohttp import WSMsgType, web

BPM_MIN, BPM_MAX = 40.0, 200.0
RAMP_MAX_S = 600.0
CONTROL_KEYS = ("bpm", "ramp", "song", "pulses", "next_section")


@dataclass
class Section:
    name: str
    bars: int
    energy: float
    colours: tuple            # hues (0-1) used in this section
    effect: int


# style: bpm, beats per bar, which beats carry the kick, accent depth, swing, section plan
STYLES = {
    "pop": dict(bpm=124, kick=(0, 2), snare=(1, 3), accent=1.0, decay=0.16, plan=[
        Section("intro", 8, 0.30, (0.58, 0.62), 0), Section("verse", 16, 0.50, (0.55, 0.75), 1),
        Section("chorus", 16, 0.80, (0.95, 0.08), 2), Section("break", 8, 0.25, (0.70, 0.78), 0),
        Section("drop", 16, 1.00, (0.0, 0.83, 0.5), 3), Section("outro", 8, 0.35, (0.60, 0.66), 1)]),
    "techno": dict(bpm=132, kick=(0, 1, 2, 3), snare=(), accent=0.9, decay=0.10, plan=[
        Section("build", 16, 0.55, (0.50, 0.55), 1), Section("peak", 32, 0.95, (0.0, 0.33), 3),
        Section("breakdown", 16, 0.30, (0.66, 0.72), 0), Section("peak2", 32, 1.00, (0.83, 0.15), 3)]),
    "jazz": dict(bpm=96, kick=(0,), snare=(1, 3), accent=0.45, decay=0.28, plan=[
        Section("head", 32, 0.40, (0.07, 0.10), 0), Section("solo", 32, 0.55, (0.04, 0.12, 0.60), 1),
        Section("trading", 16, 0.65, (0.10, 0.55), 2), Section("head-out", 32, 0.35, (0.08, 0.11), 0)]),
    "ambient": dict(bpm=70, kick=(0,), snare=(), accent=0.25, decay=0.6, plan=[
        Section("drift", 32, 0.15, (0.55, 0.60), 0), Section("swell", 16, 0.35, (0.75, 0.85), 1),
        Section("drift2", 32, 0.20, (0.45, 0.52), 0)]),
}


@dataclass(frozen=True)
class TempoSeg:
    """From time ``t`` (beat position ``beat``) the tempo ramps linearly from ``bpm`` to ``bpm_end`` over ``dur``
    seconds (0: a step), then holds ``bpm_end`` until the next segment."""
    t: float
    beat: float
    bpm: float
    bpm_end: float
    dur: float = 0.0

    def tempo(self, t: float) -> float:
        dt = t - self.t
        if dt < 0:
            return self.bpm
        if dt < self.dur:
            return self.bpm + (self.bpm_end - self.bpm) * dt / self.dur
        return self.bpm_end

    def pos(self, t: float) -> float:
        """Beat position at t: the integral of tempo / 60."""
        dt = t - self.t
        if dt <= 0:                                   # before the first segment: extrapolate its tempo
            return self.beat + dt * self.bpm / 60.0
        r = min(dt, self.dur)
        b = self.beat
        if r > 0:
            b += r * (self.bpm + (self.bpm_end - self.bpm) * r / (2.0 * self.dur)) / 60.0
        return b + (dt - r) * self.bpm_end / 60.0

    def time_of(self, k: float) -> float:
        """Inverse of pos: the time at which the beat position reaches k."""
        c = k - self.beat
        if c <= 0:
            return self.t + c * 60.0 / self.bpm
        if self.dur > 0:
            t_end = self.t + self.dur
            b_end = self.pos(t_end)
            if k <= b_end:                            # A dt^2 + B dt = c, the stable root
                a = (self.bpm_end - self.bpm) / (120.0 * self.dur)
                b = self.bpm / 60.0
                return self.t + 2.0 * c / (b + math.sqrt(max(0.0, b * b + 4.0 * a * c)))
            return t_end + (k - b_end) * 60.0 / self.bpm_end
        return self.t + c * 60.0 / self.bpm


class TempoMap:
    """Piecewise tempo.  A change appends a segment that starts at the current beat position, so the beat phase
    never jumps; recent segments are kept so beat times just before a change stay exact."""
    KEEP = 32

    def __init__(self, bpm: float, t0: float):
        self.segs = [TempoSeg(t0, 0.0, bpm, bpm)]

    def _at_time(self, t: float) -> TempoSeg:
        i = bisect.bisect_right([s.t for s in self.segs], t) - 1
        return self.segs[max(0, i)]

    def pos(self, t: float) -> float:
        return self._at_time(t).pos(t)

    def tempo(self, t: float) -> float:
        return self._at_time(t).tempo(t)

    def beat_time(self, k: float) -> float:
        i = bisect.bisect_right([s.beat for s in self.segs], k) - 1
        return self.segs[max(0, i)].time_of(k)

    @property
    def last(self) -> TempoSeg:
        return self.segs[-1]

    def change(self, t: float, bpm: float, over_s: float = 0.0) -> None:
        """Step to ``bpm`` at t (over_s 0), or ramp to it from the tempo at t over ``over_s`` seconds."""
        b, cur = self.pos(t), self.tempo(t)
        segs = [s for s in self.segs if s.t <= t]
        segs.append(TempoSeg(t, b, cur, bpm, over_s) if over_s > 0 else TempoSeg(t, b, bpm, bpm))
        self.segs = segs[-self.KEEP:]

    def ramp(self, t: float) -> Optional[dict]:
        s = self.last
        if s.dur <= 0 or t >= s.t + s.dur:
            return None
        return {"from_bpm": round(s.bpm, 2), "to_bpm": round(s.bpm_end, 2), "over_s": round(s.dur, 2),
                "remaining_s": round(s.t + s.dur - t, 2)}


class Song:
    def __init__(self, style: str, bpm: float | None, seed: int, t0: float | None = None):
        self.rng = random.Random(seed)
        self._load(style)
        self.t_start = time.monotonic() if t0 is None else t0
        self.tempo_map = TempoMap(float(bpm or STYLES[style]["bpm"]), self.t_start)
        self.bar_offset = 0                     # the plan restarts here (a song switch, next_section)
        self.pending: Optional[tuple[str, int]] = None     # (style, at bar)
        self.pulses = True
        self.pulses_from: Optional[int] = None   # after a breakdown, the beat the pulses come back on

    def _load(self, style: str) -> None:
        s = STYLES[style]
        self.style = style
        self.kick, self.snare = s["kick"], s["snare"]
        self.accent, self.decay = s["accent"], s["decay"]
        self.plan: list[Section] = s["plan"]
        self.song_bars = sum(x.bars for x in self.plan)

    # the target tempo (after any ramp) and, for a constant tempo, beat 0's time (tests set it)
    @property
    def bpm(self) -> float:
        return self.tempo_map.last.bpm_end

    @property
    def spb(self) -> float:
        return 60.0 / self.bpm

    @property
    def bar_s(self) -> float:
        return 4 * self.spb

    @property
    def t0(self) -> float:
        s = self.tempo_map.last
        t_ref = s.t + s.dur
        return t_ref - s.pos(t_ref) * 60.0 / s.bpm_end

    @t0.setter
    def t0(self, value: float) -> None:
        self.t_start = value
        self.tempo_map = TempoMap(self.bpm, value)

    def beat_time(self, k: int) -> float:
        return self.tempo_map.beat_time(k)

    def _apply_pending(self, t: float) -> None:
        if self.pending and math.floor(self.tempo_map.pos(t)) // 4 >= self.pending[1]:
            style, at_bar = self.pending
            self._load(style)
            self.bar_offset, self.pending = at_bar % self.song_bars, None

    def where(self, t: float):
        """-> (beat_index, beat_frac, bar_index, section, section_index, bar_in_section)"""
        bt = self.tempo_map.pos(t)
        beat = math.floor(bt)
        bar = beat // 4
        b = (bar - self.bar_offset) % self.song_bars
        for i, sec in enumerate(self.plan):
            if b < sec.bars:
                return beat, bt - beat, bar, sec, i, b
            b -= sec.bars
        raise AssertionError

    # -- runtime control (the caller passes the current time) ------------------------------------------
    def set_bpm(self, t: float, bpm: float) -> None:
        self.tempo_map.change(t, bpm)

    def ramp_to(self, t: float, bpm: float, over_s: float) -> None:
        self.tempo_map.change(t, bpm, over_s)

    def set_pulses(self, t: float, on: bool) -> None:
        """A breakdown (off) or its end (on).  The pulses come back from the next beat: music resumes on the
        beat, and a part-decayed pulse mid-beat would be an off-beat edge to the follower."""
        self.pulses_from = math.floor(self.tempo_map.pos(t)) + 1 if on and not self.pulses else None
        self.pulses = on

    def switch(self, t: float, style: str) -> int:
        """Switch style at the next bar (the new plan starts there).  -> that bar."""
        self._apply_pending(t)
        at_bar = math.floor(self.tempo_map.pos(t)) // 4 + 1
        self.pending = (style, at_bar)
        return at_bar

    def next_section(self, t: float) -> str:
        self._apply_pending(t)
        _, _, bar, _, si, _ = self.where(t)
        start = sum(s.bars for s in self.plan[:si + 1]) % self.song_bars
        self.bar_offset = (bar - start) % self.song_bars
        return self.where(t)[3].name

    def control(self, t: float, body) -> dict:
        """Apply a POST /v1/control body at time t.  -> what was applied.  ValueError on a bad body (nothing
        is applied then)."""
        if not isinstance(body, dict):
            raise ValueError("body must be a JSON object")
        unknown = sorted(set(body) - set(CONTROL_KEYS))
        if unknown:
            raise ValueError(f"unknown key(s) {unknown}; allowed: {list(CONTROL_KEYS)}")

        def bpm_value(v, name):
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not BPM_MIN <= v <= BPM_MAX:
                raise ValueError(f"{name} must be a number {BPM_MIN:.0f}-{BPM_MAX:.0f}")
            return float(v)

        bpm = ramp = None
        if "bpm" in body:
            bpm = bpm_value(body["bpm"], "bpm")
        if "ramp" in body:
            r = body["ramp"]
            if not isinstance(r, dict) or set(r) != {"to_bpm", "over_s"}:
                raise ValueError("ramp must be {\"to_bpm\": 40-200, \"over_s\": seconds}")
            over = r["over_s"]
            if isinstance(over, bool) or not isinstance(over, (int, float)) or not 0 < over <= RAMP_MAX_S:
                raise ValueError(f"ramp.over_s must be a number > 0 and <= {RAMP_MAX_S:.0f}")
            ramp = (bpm_value(r["to_bpm"], "ramp.to_bpm"), float(over))
        if bpm is not None and ramp is not None:
            raise ValueError("give bpm or ramp, not both")
        song = body.get("song")
        if "song" in body and song not in STYLES:
            raise ValueError(f"song must be one of {sorted(STYLES)}")
        for k in ("pulses", "next_section"):
            if k in body and not isinstance(body[k], bool):
                raise ValueError(f"{k} must be true or false")

        done: dict = {}
        if song is not None:
            done["song"], done["at_bar"] = song, self.switch(t, song)
        if bpm is not None:
            self.set_bpm(t, bpm)
            done["bpm"] = bpm
        if ramp is not None:
            self.ramp_to(t, *ramp)
            done["ramp"] = {"to_bpm": ramp[0], "over_s": ramp[1]}
        if "pulses" in body and body["pulses"] != self.pulses:
            self.set_pulses(t, body["pulses"])
            done["pulses"] = body["pulses"]
        if body.get("next_section"):
            done["next_section"] = self.next_section(t)
        return done

    def status(self, t: float) -> dict:
        self._apply_pending(t)
        beat, _, bar, sec, si, bis = self.where(t)
        return {"song": self.style, "bpm": round(self.tempo_map.tempo(t), 2), "target_bpm": round(self.bpm, 2),
                "ramp": self.tempo_map.ramp(t), "pulses": self.pulses, "section": sec.name, "section_index": si,
                "bar": int(bar), "bar_in_section": int(bis), "section_bars": sec.bars, "beat": int(beat),
                "pending_song": {"song": self.pending[0], "at_bar": self.pending[1]} if self.pending else None,
                "songs": sorted(STYLES), "bpm_range": [BPM_MIN, BPM_MAX]}

    def state(self, t: float) -> dict:
        self._apply_pending(t)
        beat, frac, bar, sec, si, bis = self.where(t)
        bib = beat % 4
        since = frac * 60.0 / self.tempo_map.tempo(t)     # seconds since this beat
        hit = 0.0
        if bib in self.kick:
            hit = self.accent
        elif bib in self.snare:
            hit = 0.85 * self.accent
        if self.style == "jazz" and self.rng.random() < 0.02:   # loose ghost accents
            hit = max(hit, 0.3)
        if bis % 8 == 7 and bib == 3:                     # a fill at the end of every 8 bars
            hit = max(hit, 0.8 * self.accent)
        pulsing = self.pulses and (self.pulses_from is None or beat >= self.pulses_from)
        pulse = hit * math.exp(-since / self.decay) * (0.6 + 0.4 * sec.energy) if pulsing else 0.0
        level = min(1.0, 0.25 + 0.55 * sec.energy + 0.25 * pulse)
        # colour: cycle the section's hues once per bar, eased
        hues = sec.colours
        k = (bar % len(hues))
        u = min(1.0, (frac + bib) / 4 * 3)                # fade over the first 3/4 of the bar
        h0, h1 = hues[k - 1], hues[k]
        dh = ((h1 - h0 + 0.5) % 1.0) - 0.5
        h = (h0 + dh * (u * u * (3 - 2 * u))) % 1.0
        r, g, b = colorsys.hsv_to_rgb(h, 0.85, 0.5 + 0.5 * level)
        ph = (t - self.t_start) * (0.05 + 0.25 * sec.energy)
        activity = min(1.0, max(0.0, sec.energy + 0.05 * math.sin(t * 0.7) + self.rng.gauss(0, 0.02)))
        fx = {"rgb": [round(r, 4), round(g, 4), round(b, 4)], "level": round(level, 4), "pulse": round(pulse, 4),
              "pan": round(math.sin(2 * math.pi * ph), 4), "tilt": round(0.5 * math.sin(2 * math.pi * ph * 0.7), 4),
              "zoom": round(0.3 + 0.6 * sec.energy, 4), "effect": sec.effect, "variant": bar % 2}
        south = dict(fx, rgb=[round(b, 4), round(r, 4), round(g, 4)], pan=round(-fx["pan"], 4))
        return {"activity": round(activity, 4), "fixtures": {"screen.north": fx, "screen.south": south},
                "_truth": {"beat": int(beat), "bar": int(bar), "section": sec.name, "section_index": si,
                           "song": self.style, "section_start_bar": int(bar - bis)}}


class Synth:
    """The bus (WebSocket fan-out), the control API and the truth record around one Song."""

    def __init__(self, song: Song, *, rate: float = 40.0, token: Optional[str] = None,
                 record: Optional[TextIO] = None):
        self.song, self.rate, self.token, self.rec = song, rate, token, record
        self.clients: set[web.WebSocketResponse] = set()
        self.seq = 0
        self.last_sec = None
        self.last_beat = None

    def _record(self, obj: dict) -> None:
        if self.rec:
            self.rec.write(json.dumps(obj) + "\n")
            self.rec.flush()

    async def ws_handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=10)
        await ws.prepare(request)
        try:
            msg = await asyncio.wait_for(ws.receive(), 5)
            hello = json.loads(msg.data) if msg.type == WSMsgType.TEXT else {}
        except (asyncio.TimeoutError, ValueError):
            await ws.close(code=4400, message=b"hello expected")
            return ws
        if not isinstance(hello, dict) or hello.get("type") != "hello" or (self.token and hello.get("token") != self.token):
            await ws.close(code=4403, message=b"bad hello")
            return ws
        await ws.send_json({"type": "welcome", "schema": 1, "rate_hz": self.rate, "stale_after_ms": 250,
                            "source": "show-synth", "song": self.song.style, "bpm": self.song.bpm})
        self.clients.add(ws)
        try:
            async for _ in ws:
                pass                                         # clients only listen
        finally:
            self.clients.discard(ws)
        return ws

    async def control_handler(self, request: web.Request) -> web.Response:
        now = time.monotonic()
        if request.method == "POST":
            if request.content_type != "application/json":
                return web.json_response({"error": "unsupported_media_type",
                                          "detail": "send a JSON object with Content-Type: application/json"},
                                         status=415)
            try:
                body = json.loads(await request.text())
            except ValueError:
                return web.json_response({"error": "bad_json", "detail": "the body is not valid JSON"}, status=400)
            try:
                done = self.song.control(now, body)
            except ValueError as e:
                return web.json_response({"error": "bad_request", "detail": str(e)}, status=400)
            if done:
                print(f"  t={now:.2f} control: {json.dumps(done)}", flush=True)
                self._record({"t_ms": round(now * 1000, 2), "event": "control", **done})
        return web.json_response(self.song.status(now), headers={"Cache-Control": "no-store"})

    def app(self) -> web.Application:
        app = web.Application(client_max_size=64 << 10)
        app.router.add_get("/v1/show", self.ws_handler)
        app.router.add_get("/v1/control", self.control_handler)
        app.router.add_post("/v1/control", self.control_handler)
        return app

    def tick(self, t: float) -> dict:
        """One bus frame at time t: -> the K3 frame; writes the truth record (section starts, beat times)."""
        st = self.song.state(t)
        truth = st.pop("_truth")
        self.seq += 1
        frame = {"v": 1, "type": "show.state", "seq": self.seq, "t_ms": round(t * 1000, 2),
                 "mode": {"id": "synth", "rev": 1}, "feed": "live",
                 "input": {"kind": "synth", "online": True, "age_ms": 0, "pps": self.rate},
                 "safety": {"photosafe": False, "master": 1.0, "blackout": False},
                 "global": {"activity": st["activity"]}, "fixtures": st["fixtures"]}
        sec_key = (truth["song"], truth["section_index"], truth["section_start_bar"])
        if sec_key != self.last_sec:
            self.last_sec = sec_key
            print(f"  t={t:.2f} bar {truth['bar']}: section -> {truth['section']} ({truth['song']})", flush=True)
            self._record({"t_ms": frame["t_ms"], "event": "section", **truth})
        if truth["beat"] != self.last_beat:
            self._record({"t_ms": round(self.song.beat_time(truth["beat"]) * 1000, 2), "event": "beat",
                          "beat": truth["beat"]})
        self.last_beat = truth["beat"]
        return frame

    async def run(self, stop: asyncio.Event) -> None:
        period = 1.0 / self.rate
        nxt = time.monotonic()
        while not stop.is_set():
            nxt += period
            await asyncio.sleep(max(0.0, nxt - time.monotonic() + random.uniform(-0.003, 0.003)))
            data = json.dumps(self.tick(time.monotonic()), separators=(",", ":"))
            for ws in list(self.clients):
                if not ws.closed:
                    await ws.send_str(data)
        for ws in list(self.clients):
            await ws.close()


async def main_async(args):
    song = Song(args.song, args.bpm, args.seed)
    token_file = pathlib.Path(args.token_file).expanduser()
    token = None if args.no_auth or not token_file.exists() else token_file.read_text().strip()
    rec = open(args.record, "w") if args.record else None
    synth = Synth(song, rate=args.rate, token=token, record=rec)
    runner = web.AppRunner(synth.app(), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, args.host, args.port).start()
    print(f"show-synth: ws://{args.host}:{args.port}/v1/show  song={song.style} bpm={song.bpm:.1f} "
          f"song length={song.song_bars * song.bar_s:.0f}s  control: http://{args.host}:{args.port}/v1/control",
          flush=True)

    stop = asyncio.Event()             # SIGINT / SIGTERM end it cleanly (also when started in the background,
    for sig in (signal.SIGINT, signal.SIGTERM):   # where SIGINT is otherwise ignored)
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    await synth.run(stop)
    await runner.cleanup()
    if rec:
        rec.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8711)
    ap.add_argument("--song", choices=sorted(STYLES), default="pop")
    ap.add_argument("--bpm", type=float, default=None, help=f"{BPM_MIN:.0f}-{BPM_MAX:.0f} (default: the song's)")
    ap.add_argument("--rate", type=float, default=40.0)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--token-file", default="~/.config/screen-machine/show_bus.token")
    ap.add_argument("--no-auth", action="store_true", help="accept any hello token (still localhost only)")
    ap.add_argument("--record", default=None)
    args = ap.parse_args()
    if args.bpm is not None and not BPM_MIN <= args.bpm <= BPM_MAX:
        ap.error(f"--bpm must be {BPM_MIN:.0f}-{BPM_MAX:.0f}")
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
