"""Karaoke sync eval (docs/KARAOKE.md): do the display page's lyrics and its sound go together?

    .venv/bin/python tools/karaoke/evals/sync_eval.py --song <id> [--start 0] [--secs 0=all] [--faults] [--llm] [--out run.json]
    .venv/bin/python tools/karaoke/evals/sync_eval.py --from run.json --llm      (the model check on a saved run)

A muted headless Chromium opens the real display page under a screen id no deployment has (room_service turns it
away, so no real screen is touched) and plays one song straight through on a FAKE player clock (/api/karaoke/now is
answered here; the real player and queue are never read or written). The lead vocal is at full (guide 1) so the
sound has the words in it.

It records, on the page's own audio clock (AudioContext.currentTime):
  * the sound: a tap on everything the page sends to its speakers (mono 16 kHz);
  * the lyrics: each frame, the lyric clock the page drew with (window.__smKaraoke) and the current line's text
    from the DOM ([data-sm-karaoke-line=current]);
  * the sources it started (when, offset): where in the song the speakers are.

Checks:
  1. clock   - every frame: song position of the sound vs the lyric clock + shift (should be 0) and vs the fake
               server (should be < 0.3 s once settled).
  2. --llm   - 10 s clips through the song (the recorded sound, vocal in) go to the multimodal model (utils.llm,
               task "listen") with the timeline of lines the screen showed; it says what is sung and whether the
               screen is early, late or showing the wrong words.
--faults: the fake server's replies come 0.2-2 s late and out of order for a stretch, as a busy Flask would.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import random
import subprocess
import sys
import time
import urllib.request
import wave
from pathlib import Path

import numpy as np
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[3]
BASE = "http://localhost:5000"
EVAL_SCREEN = "zz-karaoke-eval"   # a screen no TV uses (room_service refuses it); named the karaoke screen below
URL = BASE + "/display/" + EVAL_SCREEN
SR = 16000

HOOK = """
(() => {
  const O = window.AudioContext; const ctxs = []; const starts = []; const pcm = []; const frames = [];
  window.__eval = { ctxs, starts, pcm, frames };
  const taps = new Map();
  function tapFor(ctx) {
    if (taps.has(ctx)) return taps.get(ctx);
    const sp = ctx.createScriptProcessor(4096, 2, 1);
    const k = Math.round(ctx.sampleRate / 16000);
    sp.onaudioprocess = (ev) => {
      const a = ev.inputBuffer.getChannelData(0), b = ev.inputBuffer.getChannelData(1);
      const n = Math.floor(a.length / k), out = new Int16Array(n);
      for (let i = 0; i < n; i++) { let s = 0; for (let j = 0; j < k; j++) s += a[i*k+j] + b[i*k+j]; out[i] = Math.max(-32767, Math.min(32767, s / (2*k) * 32767)); }
      pcm.push({ t: ev.playbackTime, sr: ctx.sampleRate / k, d: out });
    };
    const z = ctx.createGain(); z.__evalTap = true; z.gain.value = 0; sp.connect(z); z.connect(ctx.destination);
    taps.set(ctx, sp); return sp;
  }
  window.AudioContext = class extends O { constructor(...a) { super(...a); ctxs.push(this); } };
  const conn = AudioNode.prototype.connect;
  AudioNode.prototype.connect = function (dest, ...rest) {
    const r = conn.call(this, dest, ...rest);
    if (dest && dest === this.context.destination && !this.__evalTap) {
      try { conn.call(this, tapFor(this.context)); } catch (e) {}
    }
    return r;
  };
  const st = AudioBufferSourceNode.prototype.start;
  AudioBufferSourceNode.prototype.start = function (when, off) {
    const r = { when, off: off || 0, dur: this.buffer ? this.buffer.duration : 0, stopped: null, ctx: this.context };
    starts.push(r);
    const sp = this.stop.bind(this);
    this.stop = (...a) => { if (r.stopped == null) r.stopped = this.context.currentTime; return sp(...a); };
    return st.call(this, when, off);
  };
  function songPos(c) {   // where in the song the speakers are now, by the sources started (the long ones)
    const ct = c.currentTime;
    const live = starts.filter(r => r.ctx === c && r.dur > 60 && r.when <= ct && (r.stopped == null || r.stopped > ct) && ct - (r.when - r.off) < r.dur);
    const r = live[live.length - 1];
    return r ? { pos: ct - (r.when - r.off), n: live.length } : { pos: null, n: 0 };
  }
  const loop = () => {
    const c = ctxs[ctxs.length - 1]; const k = window.__smKaraoke;
    if (c && k) {
      const el = document.querySelector('[data-sm-karaoke-line="current"]');
      const sp = songPos(c);
      frames.push({ wall: Date.now() / 1000, ct: c.currentTime, state: c.state, t: k.t, shift: k.shift, audioPos: k.audioPos,
                    snd: sp.pos, nsrc: sp.n, line: el ? el.textContent : null });
    }
    requestAnimationFrame(loop);
  };
  requestAnimationFrame(loop);
  window.__pull = () => {
    const out = pcm.splice(0).map(p => ({ t: p.t, sr: p.sr, b64: btoa(String.fromCharCode(...new Uint8Array(p.d.buffer))) }));
    return { pcm: out, frames: frames.splice(0) };
  };
})();
"""


class FakeServer:
    """The player clock the page follows (the shape of /api/karaoke/now)."""

    def __init__(self, song: dict):
        self.song = song
        self.state, self.pos, self.at, self.rev = "stopped", 0.0, time.time(), 0
        self.delay = self.jitter = 0.0

    def position(self, now: float) -> float:
        return self.pos + (now - self.at) if self.state == "playing" else self.pos

    def set(self, state: str, pos: float | None = None) -> None:
        now = time.time()
        self.pos = self.position(now) if pos is None else pos
        self.at, self.state, self.rev = now, state, self.rev + 1

    def body(self) -> dict:
        s = self.song
        pub = {k: s.get(k) for k in ("id", "status", "title", "artist", "duration", "audio_s", "offset_ms", "user_offset_ms")}
        return {"player": {"rev": self.rev, "state": self.state, "song_id": s["id"], "qid": "q-eval", "pos_s": self.pos, "at": self.at},
                "settings": {"guide": 1.0, "autoplay": False, "audio_delay_ms": 0, "screen": EVAL_SCREEN, "output": "screen"}, "song": pub, "up_next": None, "now": time.time()}


def get(path: str):
    with urllib.request.urlopen(BASE + path, timeout=10) as r:
        return json.load(r)


async def record(song: dict, start: float, secs: float, faults: bool) -> tuple[list, list, FakeServer]:
    F = FakeServer(song)
    pcm, frames = [], []
    async with async_playwright() as pw:
        b = await pw.chromium.launch(args=["--mute-audio", "--autoplay-policy=no-user-gesture-required"])
        pg = await b.new_page(viewport={"width": 1280, "height": 720})
        await pg.add_init_script(HOOK)

        async def now_route(route):
            body = F.body()
            d = F.delay + random.random() * F.jitter
            if d:
                await asyncio.sleep(d)
            await route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

        await pg.route("**/api/karaoke/now", now_route)
        await pg.goto(URL)
        await pg.wait_for_timeout(1500)
        F.set("playing", start)
        t_end = time.time() + secs
        fault_at = (time.time() + secs * 0.4, time.time() + secs * 0.55)
        while time.time() < t_end:
            if faults:
                inside = fault_at[0] <= time.time() < fault_at[1]
                F.delay, F.jitter = (0.2, 1.8) if inside else (0.0, 0.0)
            await asyncio.sleep(2)
            got = await pg.evaluate("window.__pull()")
            pcm += got["pcm"]
            frames += got["frames"]
        await b.close()
    return pcm, frames, F


def stitch(pcm: list) -> tuple[np.ndarray, float]:
    """The tap's chunks on one array; returns (samples, ctx time of sample 0)."""
    if not pcm:
        return np.zeros(0, np.int16), 0.0
    t0 = pcm[0]["t"]
    total = int((pcm[-1]["t"] - t0) * SR) + 8192
    out = np.zeros(total, np.int16)
    for p in pcm:
        d = np.frombuffer(base64.b64decode(p["b64"]), np.int16)
        i = int(round((p["t"] - t0) * SR))
        out[i:i + len(d)] = d[: max(0, total - i)]
    return out, t0


def clock_check(frames: list, F: FakeServer, shift_s: float) -> dict:
    rows = [f for f in frames if f["snd"] is not None]
    lyr = np.array([f["snd"] - (f["t"] + f["shift"]) for f in rows])            # sound vs lyric clock
    srv = np.array([f["snd"] - F.position(f["wall"]) for f in rows])             # sound vs server
    silent = sum(1 for f in frames if f["snd"] is None)
    q = lambda a: {"p50": round(float(np.median(a)), 3), "p95|x|": round(float(np.percentile(np.abs(a), 95)), 3),
                   "max|x|": round(float(np.max(np.abs(a))), 3)} if len(a) else None
    return {"frames": len(frames), "frames_without_sound": silent, "sound_minus_lyric_clock": q(lyr), "sound_minus_server": q(srv)}


TOOL = {
    "name": "report_karaoke_sync",
    "description": "Report whether a karaoke screen's lyrics match the singing in the audio clip.",
    "parameters": {
        "type": "object",
        "properties": {
            "sung": {"type": "array", "description": "What the lead singer sings in the clip, in order, one entry per "
                     "phrase, with the clip time it starts.",
                     "items": {"type": "object", "properties": {"at_s": {"type": "number"}, "text": {"type": "string"}},
                               "required": ["at_s", "text"]}},
            "verdict": {"type": "string", "enum": ["in_sync", "screen_early", "screen_late", "wrong_words", "no_singing"]},
            "screen_offset_s": {"type": "number", "description": "How much later (+) or earlier (-) the screen shows "
                                "each line than it is sung, in seconds; 0 when in sync."},
            "confidence": {"type": "number", "description": "0..1"},
            "note": {"type": "string", "description": "At most 20 words."},
        },
        "required": ["sung", "verdict", "screen_offset_s", "confidence", "note"],
    },
}

SYSTEM = ("You check a karaoke screen. You get an audio clip of a song WITH its lead vocal, and the timeline of the "
          "lyric line the screen highlighted as current during the clip (clip seconds). Listen to when each line is "
          "actually sung. A line should be highlighted from when its first word is sung until the next line starts. "
          "Report what is sung and when, and whether the screen is in sync (within about 0.5 s), early (shows a line "
          "before it is sung), late, or showing words that aren't being sung at all. Judge only by what you hear.")


def mp3(samples: np.ndarray) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(SR), w.writeframes(samples.tobytes())
    p = subprocess.run(["ffmpeg", "-v", "error", "-f", "wav", "-i", "-", "-b:a", "64k", "-f", "mp3", "-"],
                       input=buf.getvalue(), capture_output=True, check=True)
    return p.stdout


def line_timeline(frames: list, c0: float, c1: float) -> list[tuple[float, str | None]]:
    out: list[tuple[float, str | None]] = []
    for f in frames:
        if c0 <= f["ct"] < c1 and (not out or out[-1][1] != f["line"]):
            out.append((round(f["ct"] - c0, 2), f["line"]))
    return out


def llm_check(audio: np.ndarray, a0: float, frames: list, clip_s: float, every_s: float) -> list[dict]:
    sys.path.insert(0, str(ROOT))
    from utils import llm
    res = []
    ct_end = a0 + len(audio) / SR
    c = a0 + 2.0
    while c + clip_s <= ct_end:
        i0, i1 = int((c - a0) * SR), int((c + clip_s - a0) * SR)
        clip = audio[i0:i1]
        tl = line_timeline(frames, c, c + clip_s)
        songpos = next((f["snd"] for f in frames if f["ct"] >= c and f["snd"] is not None), None)
        if np.sqrt(np.mean((clip.astype(np.float32) / 32767) ** 2)) < 0.003:
            res.append({"song_s": songpos, "verdict": "silent clip (sound missing)", "timeline": tl})
            c += every_s
            continue
        user = ("Screen timeline (clip seconds -> line highlighted as current; null = no line, count-in or gap):\n"
                + "\n".join(f"{t:5.2f}s  {txt!r}" for t, txt in tl))
        r = llm.call_json("listen", system=SYSTEM, user=user, tool=TOOL, audio=mp3(clip), audio_format="mp3",
                          lenient=True)
        row = {"song_s": None if songpos is None else round(songpos, 1), "timeline": tl}
        row |= (r.data or {}) if r.ok else {"error": r.error}
        row["cost_usd"] = r.cost_usd
        res.append(row)
        c += every_s
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--song", default="")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--secs", type=float, default=0.0, help="0 = to the end")
    ap.add_argument("--faults", action="store_true")
    ap.add_argument("--llm", action="store_true")
    ap.add_argument("--clip", type=float, default=10.0)
    ap.add_argument("--every", type=float, default=15.0)
    ap.add_argument("--out", default="")
    ap.add_argument("--from", dest="src", default="", help="a saved --out run: only the --llm check, on its recording")
    a = ap.parse_args()
    if a.src:
        src = Path(a.src)
        audio = np.load(src.with_suffix(".npy"))
        fr = json.loads(src.with_suffix(".frames.json").read_text())
        rows = llm_check(audio, fr["a0"], fr["frames"], a.clip, a.every)
        if a.out:
            Path(a.out).write_text(json.dumps(rows, indent=1))
        for r in rows:
            print(f"{r.get('song_s')!s:>6}  {r.get('verdict')!s:14} off={r.get('screen_offset_s')!s:6} conf={r.get('confidence')!s:4} "
                  f"{r.get('note') or r.get('error') or ''}")
        return 0
    songs = {s["id"]: s for s in get("/api/karaoke/state")["songs"]}
    song = songs[a.song]
    secs = a.secs or (song["audio_s"] - a.start + 1.0)
    pcm, frames, F = asyncio.run(record(song, a.start, secs, a.faults))
    audio, a0 = stitch(pcm)
    report = {"song": f"{song['artist']} - {song['title']}", "shift_s": (song.get("offset_ms") or 0) / 1000,
              "clock": clock_check(frames, F, (song.get("offset_ms") or 0) / 1000)}
    if a.llm:
        report["llm"] = llm_check(audio, a0, frames, a.clip, a.every)
    if a.out:
        Path(a.out).write_text(json.dumps(report, indent=1))
        np.save(Path(a.out).with_suffix(".npy"), audio)
        Path(a.out).with_suffix(".frames.json").write_text(json.dumps({"a0": a0, "frames": frames}))
    print(json.dumps(report["clock"], indent=1))
    for r in report.get("llm", []):
        print(f"{r.get('song_s')!s:>6}  {r.get('verdict')!s:14} off={r.get('screen_offset_s')!s:6} conf={r.get('confidence')!s:4} "
              f"{r.get('note') or r.get('error') or ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
