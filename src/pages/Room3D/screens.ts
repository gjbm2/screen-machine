// A screen's picture for the 3D room: what that screen shows, fed directly (owner, 26 Nov: "push the vj screen ...
// into the placeholder screen object(s) directly rather than my having to do a weird client-side fudge").
//
//   * Live: this page joins room_service's screen as a viewer (WS /v1/screen/<id>?role=viewer: it drives nothing),
//     is handed the running session's attach (welcome.live, then live.attach / live.abort), and plays the media
//     socket with the TV page's own MseLivePlayer into a hidden, muted <video>: the same stream the glass shows.
//   * Art: Flask's current picture for the screen (/output/<screen>.jpg), read again every ART_REFRESH_MS.
//   The karaoke lyrics are drawn by the TV page itself, so they aren't here (owner: not needed).

import { probeCaps } from "@/pages/DisplayPage/control/caps";
import type { LiveAttach } from "@/pages/DisplayPage/control/protocol";
import { MseLivePlayer } from "@/pages/DisplayPage/live/MseLivePlayer";
import { roomSocketBase } from "@/pages/DisplayPage/secureUrls";
import { startTicker } from "@/pages/DisplayPage/control/ticker";

const HB_MS = 2000;
const RETRY_MS = 3000;
const ART_REFRESH_MS = 15000;
// The live picture must keep moving: a browser can pause a hidden muted video, and a stream can stall (the screen's
// own page reconnecting). Every WATCH_MS: a paused video is played again; a picture that hasn't moved for STALL_MS
// rejoins the stream (a fresh viewer socket: room_service hands the running session's attach again).
const WATCH_MS = 1000;
const STALL_MS = 4000;

// The heartbeat and the watch run on a clock that keeps time in a hidden or covered window (ticker.ts): on the page's
// own timers a 3D room left behind another window stopped sending its heartbeat after a few seconds (Chrome's
// intensive throttling), room_service dropped each screen's viewer socket every 28 s and the screens rejoined (28 Nov).

export type ScreenState = "connecting" | "art" | "live" | "down";

export class ScreenFeed {
  state: ScreenState = "connecting";
  session: string | null = null;
  /** The element to draw: the live video once it has frames, else the art image (null until one has loaded). */
  source: HTMLVideoElement | HTMLImageElement | null = null;
  private ws: WebSocket | null = null;
  private player: MseLivePlayer | null = null;
  private art = new Image();
  private artOk = false;
  private artTag: string | null = null;
  private holder: HTMLDivElement;
  private timers: number[] = [];
  private stops: (() => void)[] = [];
  private closed = false;
  private lastT = -1;
  private movedAt = 0;
  /** How many times the live picture stalled and was rejoined (the side panel shows it). */
  rejoins = 0;

  constructor(private room: string, readonly screen: string) {
    this.holder = document.createElement("div");
    Object.assign(this.holder.style, { position: "fixed", left: "0", top: "0", width: "2px", height: "2px",
      overflow: "hidden", opacity: "0.01", pointerEvents: "none", zIndex: "-1" });
    document.body.appendChild(this.holder);
    this.loadArt();
    this.timers.push(window.setInterval(() => !this.player && this.loadArt(), ART_REFRESH_MS));
    this.stops.push(startTicker(WATCH_MS, () => this.watch()));
    this.connect();
  }

  /** The screen's current picture, into a fresh image that replaces the shown one once it has loaded: reloading the
   *  shown image in place left it half-loaded while the scene read it (the room's light from it dropped) and never
   *  updated its texture, so a new picture didn't show. */
  private loadArt() {
    const url = `/output/${encodeURIComponent(this.screen)}.jpg`;
    // a 3 MB picture every 15 s for nothing: only when it changed (its ETag)
    void fetch(url, { method: "HEAD", cache: "no-store" }).then((r) => {
      const tag = r.headers.get("etag") ?? r.headers.get("last-modified");
      if (tag && tag === this.artTag && this.artOk) return;
      this.artTag = tag;
      this.loadArtNow();
    }).catch(() => this.loadArtNow());
  }

  private loadArtNow() {
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => {
      if (this.closed) return;
      this.art = img;
      this.artOk = true;
      if (!this.player?.started) this.source = img;
    };
    img.src = `/output/${encodeURIComponent(this.screen)}.jpg?t=${Date.now()}`;
  }

  private connect() {
    if (this.closed) return;
    const base = roomSocketBase(this.room.replace(/^http/i, "ws")).replace(/\/+$/, "");
    const ws = new WebSocket(`${base}/v1/screen/${encodeURIComponent(this.screen)}?role=viewer`);
    this.ws = ws;
    const send = (type: string, data: object = {}) => ws.readyState === WebSocket.OPEN && ws.send(JSON.stringify({ v: 1, type, data }));
    ws.onopen = () => {
      send("hello", { role: "viewer", screen: this.screen, page_load_id: `room3d-${Math.random().toString(36).slice(2, 10)}`,
        build: "room3d", caps: probeCaps(), flags: { nomask: true, nocolour: true, fit: "fit" } });
      const stop = startTicker(HB_MS, () => send("hb"));
      this.stops.push(stop);
      ws.addEventListener("close", stop);
    };
    ws.onmessage = (ev) => {
      let m: any;
      try { m = JSON.parse(ev.data); } catch { return; }
      const d = m?.data ?? {};
      if (m?.type === "welcome") {
        const live = d.live;
        const att = live?.attach ? { session: live.session, ...live.attach } : live?.url ? live : null;
        if (att) this.play(att as LiveAttach); else this.toArt();
      } else if (m?.type === "live.attach") {
        if (d.url) this.play(d as LiveAttach);
      } else if (m?.type === "live.abort" || m?.type === "live.leave") {
        if (!d.session || d.session === this.session) this.toArt();
      }
    };
    ws.onclose = () => {
      if (this.ws !== ws || this.closed) return;
      this.state = "down";
      this.timers.push(window.setTimeout(() => this.connect(), RETRY_MS));
    };
  }

  private watch() {
    const p = this.player;
    if (!p || !p.started) return;
    const v = p.video, now = performance.now();
    if (v.paused) void v.play().catch(() => undefined);
    if (v.currentTime !== this.lastT) { this.lastT = v.currentTime; this.movedAt = now; return; }
    if (now - this.movedAt < STALL_MS) return;
    this.rejoins++;                                         // stalled: rejoin as a viewer
    this.stopPlayer();
    this.session = null;
    this.state = "connecting";
    this.movedAt = now;
    try { this.ws?.close(); } catch { /* closed */ }        // onclose reconnects
  }

  private play(a: LiveAttach) {
    if (this.session === a.session && this.player) return;
    this.stopPlayer();
    this.session = a.session;
    this.state = "connecting";
    const p = new MseLivePlayer(this.holder, a, {
      onFrame: () => {
        if (this.player === p) { this.source = p.video; this.state = "live"; }
      },
      onStarted: () => undefined,
      onTick: () => undefined,
      onFatal: () => { if (this.player === p) this.toArt(); },
      onEnded: () => { if (this.player === p) this.toArt(); },
      log: () => undefined,
    }, "viewer");
    this.player = p;
    this.lastT = -1; this.movedAt = performance.now();
    p.start();
  }

  private toArt() {
    this.stopPlayer();
    this.session = null;
    this.state = "art";
    this.source = this.artOk ? this.art : null;
    this.loadArt();
  }

  private stopPlayer() {
    const p = this.player;
    this.player = null;
    if (p) { try { p.destroy(); } catch { /* already gone */ } }
  }

  close() {
    this.closed = true;
    this.stopPlayer();
    this.timers.forEach((t) => { window.clearInterval(t); window.clearTimeout(t); });
    this.stops.forEach((s) => s());
    try { this.ws?.close(); } catch { /* closed */ }
    this.holder.remove();
  }
}
