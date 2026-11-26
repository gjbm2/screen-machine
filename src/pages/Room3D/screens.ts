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

const HB_MS = 2000;
const RETRY_MS = 3000;
const ART_REFRESH_MS = 15000;

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
  private holder: HTMLDivElement;
  private timers: number[] = [];
  private closed = false;

  constructor(private room: string, readonly screen: string) {
    this.holder = document.createElement("div");
    Object.assign(this.holder.style, { position: "fixed", left: "0", top: "0", width: "2px", height: "2px",
      overflow: "hidden", opacity: "0.01", pointerEvents: "none", zIndex: "-1" });
    document.body.appendChild(this.holder);
    this.art.crossOrigin = "anonymous";
    this.art.onload = () => {
      this.artOk = true;
      if (!this.player?.started) this.source = this.art;
    };
    this.loadArt();
    this.timers.push(window.setInterval(() => !this.player && this.loadArt(), ART_REFRESH_MS));
    this.connect();
  }

  private loadArt() {
    this.art.src = `/output/${encodeURIComponent(this.screen)}.jpg?t=${Date.now()}`;
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
      const hb = window.setInterval(() => send("hb"), HB_MS);
      ws.addEventListener("close", () => window.clearInterval(hb));
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
    try { this.ws?.close(); } catch { /* closed */ }
    this.holder.remove();
  }
}
