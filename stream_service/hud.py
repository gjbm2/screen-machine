"""The live diagnostics panel, burned into one screen's own stream (owner 17 Oct; docs/LIVE_DIAGNOSTICS.md).

Three parts, on three threads:

* :func:`build_lines` (loop thread, 2 Hz, only while the panel is on, or on request for GET .../hud at most once a
  second): about 14 lines of up to COLS characters from in-memory fields only (never ``Session.inputs()``,
  ``summary()`` or nvidia-smi), and the indices of lines to show as warnings.  :func:`encode` turns them into a
  ROWS x COLS uint8 code grid (code = character - 32; anything outside printable ASCII becomes a plain fallback)
  plus one tone byte per row (1 = warning).
* :func:`bake_atlas` (worker thread, once per cell size per process): DejaVuSansMono, one alpha cell per code.
* :class:`HudDraw` (render thread): the device atlas, two device grids and two pinned host blocks, all made at
  engine start (toggling allocates nothing).  Each rendered frame, after rgba_to_nv12, one ``hud_nv12`` kernel
  blends the panel into the NV12 frame over a fixed rectangle, only while the master bus is open (``bus.level``:
  0 until art->live is done, faded on exit, 0 once sealed, so anchor, cut and hold frames carry no panel) and the
  toggle's own fade is up.  A new grid is uploaded asynchronously into the spare buffer when its previous upload
  is done.  Self-timed with a CUDA event pair read via ``Event.done`` (never a sync): over 0.5 ms (EMA) for 2 s,
  or any frame over 5 ms, switches it off ("too slow"); any exception latches it off ("stopped after an error").
  The stream never depends on it.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from typing import Any, Optional

import numpy as np

log = logging.getLogger("stream_service.hud")

COLS, ROWS = 64, 16
FIRST, NGLYPH = 32, 95                      # printable ASCII 32..126 -> codes 0..94
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
CELL_DIV = 60                               # cell height = frame height / 60 (36 px at 2160)
INSET = 64                                  # px from the top-left, a multiple of 32 (HEVC CTU)
ALIGN = 32
FADE_S = 0.3                                # the toggle's own fade
SLOW_EMA_MS, SLOW_FOR_S, HARD_MS = 0.5, 2.0, 5.0
EMA_A = 0.1
BUILD_SLOW_MS = 5.0                         # a model build over this drops it to 1 Hz
FALLBACK = {"·": ".", "•": "*", "→": ">", "←": "<", "↑": "^", "↓": "v", "×": "x", "–": "-", "—": "-",
            "★": "*", "♪": "~", "…": "...", "°": "o", "µ": "u", "≥": ">=", "≤": "<="}


def _ascii(s: str) -> str:
    out = []
    for ch in s:
        if FIRST <= ord(ch) < FIRST + NGLYPH:
            out.append(ch)
        else:
            out.append(FALLBACK.get(ch, "?"))
    return "".join(out)


def encode(lines: list[str], warn: list[int]) -> tuple[bytes, bytes]:
    """-> (ROWS x COLS codes, ROWS tone bytes)."""
    grid = np.zeros((ROWS, COLS), np.uint8)
    for r, line in enumerate(lines[:ROWS]):
        a = _ascii(line)[:COLS]
        if a:
            grid[r, :len(a)] = np.frombuffer(a.encode("ascii"), np.uint8) - FIRST
    tone = np.zeros(ROWS, np.uint8)
    for i in warn:
        if 0 <= i < ROWS:
            tone[i] = 1
    return grid.tobytes(), tone.tobytes()


# -- the atlas ---------------------------------------------------------------------------------------------------
_ATLAS: dict[int, tuple[np.ndarray, int, int]] = {}
_ATLAS_LOCK = threading.Lock()


def bake_atlas(cell_h: int) -> tuple[np.ndarray, int, int]:
    """-> (atlas uint8 [NGLYPH, ch, cw], cw, ch), cached per cell height."""
    with _ATLAS_LOCK:
        hit = _ATLAS.get(cell_h)
        if hit is not None:
            return hit
    from PIL import Image, ImageDraw, ImageFont

    size = max(8, int(round(cell_h * 0.78)))
    font = ImageFont.truetype(FONT, size)
    cw = max(4, int(math.ceil(font.getlength("M"))))
    ascent, descent = font.getmetrics()
    ch = cell_h
    top = max(0, (ch - (ascent + descent)) // 2)
    out = np.zeros((NGLYPH, ch, cw), np.uint8)
    for i in range(NGLYPH):
        img = Image.new("L", (cw, ch), 0)
        ImageDraw.Draw(img).text((0, top), chr(FIRST + i), fill=255, font=font)
        out[i] = np.asarray(img, np.uint8)
    res = (out, cw, ch)
    with _ATLAS_LOCK:
        _ATLAS[cell_h] = res
    return res


def layout(w: int, h: int, cw: int, ch: int) -> tuple[int, int, int, int, int]:
    """-> (x0, y0, pw, ph, pad): the panel rectangle, top-left, CTU-aligned, clamped to the frame (portrait)."""
    pad = max(2, (ch // 2) & ~1)
    up = lambda v: (v + ALIGN - 1) // ALIGN * ALIGN            # noqa: E731
    pw = min(up(COLS * cw + 2 * pad), (w - 2 * INSET) & ~1)
    ph = min(up(ROWS * ch + 2 * pad), (h - 2 * INSET) & ~1)
    return INSET, INSET, max(0, pw), max(0, ph), pad


# -- the draw (render thread) ------------------------------------------------------------------------------------
class HudDraw:
    def __init__(self, cp, kernel: Any, w: int, h: int, fps: int):
        self.cp, self.kernel, self.w, self.h, self.fps = cp, kernel, w, h, fps
        atlas, self.cw, self.ch = bake_atlas(max(10, h // CELL_DIV))
        self.x0, self.y0, self.pw, self.ph, self.pad = layout(w, h, self.cw, self.ch)
        self.d_atlas = cp.asarray(atlas.ravel())
        n = ROWS * COLS + ROWS
        self.n = n
        self.d_buf = [cp.zeros(n, cp.uint8), cp.zeros(n, cp.uint8)]
        self.pinned = [cp.cuda.alloc_pinned_memory(n), cp.cuda.alloc_pinned_memory(n)]
        self.host = [np.frombuffer(p, np.uint8, n) for p in self.pinned]
        self.up_ev = [cp.cuda.Event(disable_timing=True), cp.cuda.Event(disable_timing=True)]
        self.up_live = [False, False]
        self.t0, self.t1 = cp.cuda.Event(), cp.cuda.Event()
        self.timing = False
        # state (loop thread writes on / pending; the render thread the rest)
        self.on = False
        self.pending: Optional[tuple[bytes, bytes]] = None
        self.active = 0
        self.ready = False
        self.fade = 0.0
        self.failed: Optional[str] = None
        self.ema_ms: Optional[float] = None
        self.slow_since: Optional[float] = None
        self.lamp = None                     # () -> 0..1 (the session's beat at photon time)

    def set(self, grid: bytes, tone: bytes) -> None:
        """Loop thread: the next text (swapped in by reference)."""
        self.pending = (grid, tone)

    def draw(self, nv12, gate: float, frame_no: int, stream) -> None:
        """Render thread, after rgba_to_nv12.  ``gate``: the master bus's open level (0 = no panel)."""
        if self.failed is not None:
            return
        dt = 1.0 / self.fps
        target = 1.0 if self.on else 0.0
        step = dt / FADE_S
        self.fade = min(target, self.fade + step) if self.fade < target else max(target, self.fade - step)
        self._read_timing()
        if self.failed is not None:
            return
        pend = self.pending
        if pend is not None:
            spare = 1 - self.active
            if not self.up_live[spare] or self.up_ev[spare].done:
                self.pending = None
                buf = self.host[spare]
                g, t = pend
                buf[:len(g)] = np.frombuffer(g, np.uint8)
                buf[ROWS * COLS:ROWS * COLS + len(t)] = np.frombuffer(t, np.uint8)
                cp = self.cp
                cp.cuda.runtime.memcpyAsync(self.d_buf[spare].data.ptr, buf.ctypes.data, self.n,
                                            cp.cuda.runtime.memcpyHostToDevice, stream.ptr)
                self.up_ev[spare].record(stream)
                self.up_live[spare] = True
                self.active, self.ready = spare, True
        f = self.fade * self.fade * (3.0 - 2.0 * self.fade)
        a = float(gate) * f
        if a <= 0.0 or not self.ready or self.pw <= 0 or self.ph <= 0:
            return
        lamp = 0.0
        if self.lamp is not None:
            try:
                lamp = max(0.0, min(1.0, float(self.lamp() or 0.0)))
            except Exception:
                lamp = 0.0
        w, h = self.w, self.h
        y_plane, uv_plane = nv12[: w * h], nv12[w * h:]
        buf = self.d_buf[self.active]
        block = (16, 8, 1)
        grid = ((self.pw // 2 + 15) // 16, (self.ph // 2 + 7) // 8, 1)
        time_it = not self.timing
        if time_it:
            self.t0.record(stream)
        self.kernel(grid, block, (y_plane, uv_plane, np.int32(w), np.int32(h), np.int32(self.x0), np.int32(self.y0),
                                  np.int32(self.pw), np.int32(self.ph), self.d_atlas, np.int32(self.cw),
                                  np.int32(self.ch), np.int32(NGLYPH), buf[:ROWS * COLS], buf[ROWS * COLS:],
                                  np.int32(COLS), np.int32(ROWS), np.int32(self.pad), np.float32(a),
                                  np.float32(lamp), np.int32(frame_no % 10_000_000)))
        if time_it:
            self.t1.record(stream)
            self.timing = True

    def _read_timing(self) -> None:
        if not self.timing or not self.t1.done:
            return
        self.timing = False
        ms = self.cp.cuda.get_elapsed_time(self.t0, self.t1)
        self.ema_ms = ms if self.ema_ms is None else self.ema_ms + EMA_A * (ms - self.ema_ms)
        now = time.monotonic()
        if ms > HARD_MS:
            self._fail("too slow", ms)
        elif self.ema_ms > SLOW_EMA_MS:
            if self.slow_since is None:
                self.slow_since = now
            elif now - self.slow_since >= SLOW_FOR_S:
                self._fail("too slow", self.ema_ms)
        else:
            self.slow_since = None

    def _fail(self, why: str, ms: Optional[float] = None) -> None:
        if self.failed is None:
            self.failed = why
            log.warning("details panel off: %s%s", why, f" ({ms:.2f} ms)" if ms is not None else "")


# -- the model (loop thread) -------------------------------------------------------------------------------------
def _f(v: Any, nd: int = 1, dash: str = "-") -> str:
    return dash if v is None else f"{v:.{nd}f}"


def _mmss(s: Optional[float]) -> str:
    if s is None:
        return "-"
    s = max(0, int(s))
    return f"{s // 60}:{s % 60:02d}"


def _cut(t: Any, n: int) -> str:
    """At most ``n`` characters, an ellipsis ("~") when cut: so a line's parts all fit in COLS."""
    t = "-" if t is None else str(t)
    return t if len(t) <= n else t[:max(1, n - 1)] + "~"


def _eta(e: Any) -> str:
    if e is None:
        return "on Next"
    if isinstance(e, (list, tuple)):
        return f"{int(e[0])}-{int(e[1])}s"
    return f"{int(e)}s"


def _first_source_layer(root: Any) -> Any:
    from .layers import SourceLayer
    stack, seen = [root], 0
    while stack and seen < 16:
        x = stack.pop()
        seen += 1
        if isinstance(x, SourceLayer):
            return x
        for k in ("b", "a", "inner", "layer", "child"):
            c = getattr(x, k, None)
            if c is not None:
                stack.append(c)
    return None


def build_lines(s: Any, now: float, hud_ms: Optional[float] = None) -> tuple[list[str], list[int]]:
    """About 14 lines for a session (docs/LIVE_DIAGNOSTICS.md "What the panel shows"), and its warning lines."""
    lines: list[str] = []
    warn: list[int] = []
    eng, p = s.engine, s.profile

    def add(text: str, bad: bool = False) -> None:
        if bad:
            warn.append(len(lines))
        lines.append(text)

    # stream
    add(f"{s.screen} {p.w}x{p.h}@{p.fps} {p.name} GPU{s.gpu}")
    ws = eng.window_stats(2.0) if hasattr(eng, "window_stats") else {}
    rm = ws.get("render_ms") or {}
    late = ws.get("late_pct")
    add(f"render {_f(rm.get('p50'))}/{_f(rm.get('p95'))} ms  late {_f(late, 0)}%  {_f(ws.get('mbps'))} Mb/s",
        bad=bool(late and late > 5))
    gs = eng.guard_stats() if hasattr(eng, "guard_stats") else {}
    trips = int(gs.get("trips") or 0) + int(gs.get("slow_trips") or 0)
    add(f"guard {_f(gs.get('ema_ms') or gs.get('frame_ms'))}/{_f(ws.get('budget_ms'))} ms  trips {trips}"
        f"  details {_f(hud_ms, 2)} ms", bad=trips > 0)
    # glass (the TV's own player)
    glass = None
    for c in list(getattr(s.media, "clients", {}).values()):
        st = getattr(c, "stats", None) or {}
        if st.get("role") in (None, "kiosk") and st:
            glass = (c, st)
            if st.get("role") == "kiosk":
                break
    if glass is None:
        add("glass  no player connected")
    else:
        c, st = glass
        prev = getattr(c, "_hud_prev", None)
        fps = drops = None
        pres, drop = st.get("presented"), st.get("dropped")
        if prev is not None and pres is not None and now > prev[0]:
            fps = (pres - prev[1]) / (now - prev[0])
            if drop is not None and prev[2] is not None:
                drops = (drop - prev[2]) / (now - prev[0])
        c._hud_prev = (now, pres, drop)
        stalls = int(st.get("stalls") or 0)
        add(f"glass {_f(fps)} fps  drop {_f(drops)}/s  buf {_f(st.get('buffered_ms'), 0)} ms  stalls {stalls}",
            bad=bool((drops or 0) > 1.0 or (fps is not None and fps < 0.8 * p.fps)))
    # scene
    lay = _first_source_layer(eng.root)
    rate = "" if lay is None or getattr(lay, "rate_div", 1) <= 1 else \
        f"  1/{lay.rate_div} ({_f(getattr(lay, 'cost_ema', None))} ms)"
    last = s.last_cue or {}
    on_s = None
    if getattr(s.director, "dwell_start", None) is not None:
        on_s = now - s.director.dwell_start
    add(f"now  {_cut(s.current_name, 34)}  {_mmss(on_s)}{rate}", bad=bool(rate))
    why = (s.director.last or {}).get("detail") or last.get("detail") or ""
    add(f"why  {_cut(why, COLS - 5)}")
    up = s.upnext_view(now)
    for label, e in zip(("next", "then"), up.get("scenes", [])[:2]):
        add(f"{label} {_cut(e.get('scene'), 28)} via {_cut(e.get('transition') or 'auto', 18)} {_eta(e.get('eta_s'))}")
    # effects
    stack = [st for st in getattr(s.fx, "stack", [])]
    if stack:
        add("fx   " + " | ".join(f"{_cut(st.spec.preset or st.spec.effect, 18)} {getattr(st, 'level', 1.0):.1f}"
                                 for st in stack[:3]))
    else:
        add("fx   none")
    fx = s.fx_director.status(now) if hasattr(s.fx_director, "status") else {}
    nxt = (up.get("effects") or [{}])[0].get("effect") if up.get("effects") else None
    lastfx = (fx.get("last") or {}).get("why") or ""
    add(f"fx>  {_cut(nxt, 26)} in {_eta(fx.get('next_in_s'))}  {_cut(lastfx, 22)}")
    # tempo / drive
    feed = s.mgr.music_feed
    c = feed.clock(now)
    if c:
        from .music_feed import beat_at
        b = beat_at(c, now + s.sampler.lead)
        bpb = max(1, int(c.get("beats_per_bar") or 4))
        rel = b - float(c.get("bar_anchor_beat") or 0)
        add(f"tempo {float(c['bpm']):.1f} bpm  clock {_f(c.get('conf'), 2)} {c.get('gate')}"
            f"  bar {int(rel // bpb) % 1000}.{int(rel % bpb) + 1}")
    else:
        add("tempo no clock")
    mod = getattr(s.current_scene, "modulation", None)
    terms = []
    if mod is not None:
        try:
            for d in mod.describe()[:3]:
                ins = d.get("in") or d.get("signals") or []
                sig = ins[0].get("sig") if ins and isinstance(ins[0], dict) else d.get("signal")
                v = d.get("value", d.get("last"))
                terms.append(f"{_cut(d.get('param'), 9)}<{_cut(sig, 8)}={_f(v, 2)}")
        except Exception:
            terms = []
    add("drive " + ("  ".join(terms) if terms else "-"))
    # director
    eff = s.direction_effective or {}
    add(f"dir  energy {_f(eff.get('energy'), 2)} pace {_f(eff.get('pace'), 2)}  "
        + ", ".join((eff.get("moods") or [])[:3]))
    r = s.mgr.room_direction_now(now)
    ctx = getattr(feed, "context", None) or {}
    genre = (ctx.get("genre") or {}).get("word") if isinstance(ctx.get("genre"), dict) else ctx.get("genre")
    key = " ".join(x for x in (ctx.get("key"), ctx.get("mode")) if x)
    sec = (ctx.get("section") or {})
    add(f"room {(r or {}).get('vibe') or '-'}  {sec.get('letter') or sec.get('phase') or '-'}  {genre or '-'}"
        f"  {key or '-'}" + ("  HELD" if r and r.get("hold") else ""))
    rw = getattr(s.mgr, "room_why", None)
    if rw is not None and now - rw[1] <= 5.0:
        d = rw[0]
        au, mic, li, lk = (d.get(k) or {} for k in ("auto", "mic", "listening", "look"))
        ai = li.get("why") or ("on" if li.get("on") else "off")
        age = f" {int(lk['age_s'])}s" if isinstance(lk.get("age_s"), (int, float)) else ""
        add(f"Auto {_cut(au.get('last') or au.get('line'), 24)} | mic {'on' if mic.get('on') else 'off'}"
            f" | AI {_cut(ai, 8)} | {_cut(lk.get('why'), 12)}{age}")
    evs = list(getattr(s.mgr, "room_events_ring", []))[-3:]
    add("evt  " + ("; ".join(f"{time.strftime('%H:%M:%S', time.localtime(t))} {k}" for t, k, _ in reversed(evs))
                   if evs else "-"))
    return lines[:ROWS], warn
