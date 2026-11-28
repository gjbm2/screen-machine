// The display page's karaoke layer (docs/KARAOKE.md): an output, like a speaker and a screen. It plays the backing
// track and shows the lyrics over whatever the screen shows (art or live). It takes no input and reports nothing.
//
// - Only while the deployment's `karaoke` capability is on; otherwise it renders nothing and polls nothing.
// - The server owns the clock (routes/karaoke/store.py): a song, a state, and the position `pos_s` at server time `at`.
//   Every display page follows it: it polls /api/karaoke/now each second, plays instrumental + lead vocal together in
//   Web Audio (the vocal's gain is the guide level) at the server's position, and re-syncs if it drifts. Any number
//   of display pages can be open, or none; the server doesn't know or care.
// - The browser must allow the page to make sound without a click (the launcher, tools/karaoke/windows/
//   karaoke-screen.cmd, starts Chrome so). If it doesn't, the page still shows the lyrics, and its sound starts at
//   the right place as soon as the browser allows it.
// - The lyrics clock is the song position minus the delay to the room (settings.audio_delay_ms, measured by Settings →
//   Calibrate: the whole delay, Bluetooth and Sonos buffering included; while it is 0, the browser's own output
//   latency) and the song's shift (automatic + the person's nudge). Lyrics only: the picture and lights follow the
//   live mic's beat clock.
// - data-sm-karaoke keeps it visible while live video is up (index.tsx ART_GUARD_CSS hides everything else).

import { roomApi } from "@/components/room/roomLink";
import { leaving, setSounding } from "./soundGate";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { getDeploymentRelative, capabilityOn, type DeploymentInfo } from "@/shared/api/deployment";
import {
  karaoke,
  karaokeScreen,
  type DuetPart,
  type KaraokeLyrics,
  type KaraokeNow,
  type KaraokeSong,
  type LyricLine,
} from "@/shared/api/karaoke";
import { sound } from "@/shared/api/sound";
import { Faces, JoinCard, LOW_FROM_S, NEXT_UP_S, NextUp, namesText } from "./JoinOverlays";
import { Presence } from "./Presence";

const POLL_MS = 1000;
const LYRICS_RETRY_MS = 4000;
// The sound is placed by the server's clock only when the player itself moves (a new rev: play, seek, next...); in
// between it runs on this machine's own clock, and is re-placed only if it falls this far from where this machine's
// clock says it should be (a stall). Chasing the server's clock was an audible jump every few seconds (owner, 25 Nov:
// "jumping / jitter of the audio"): the laptop's WSL clock, which the server's is, ran 7.6% slow. The lyrics follow
// the sound itself, so they stay with it either way.
const RESYNC_S = 1.0;
const SKEW_N = 10;
// Between lines (owner, 25 Nov: the dots came "slightly too eagerly -- should only really be for slightly longer
// delays / intros / build ups. Show the next line earlier instead"): after a short pause the next line takes the big
// place at once, unlit until it is sung; only a gap of LONG_GAP_S or more (an intro, a break) shows ♪, then the dots
// for its last COUNT_IN_S.
const COUNT_IN_S = 3;
const BIG_VW = 4.6; // the sung line's size (vw)
const LINE_HEIGHT = 1.75; // the rows of a wrapped line: room between them for the ball (owner, 25 Nov)
const SMALL_VW = 2.5; // the next line's
const SMALL_OPACITY = 0.6; // the next line, a little back (owner, 25 Nov); the sung line is full strength
const MORPH_MS = 420; // the next line travelling into the big place
const LEAVE_MS = 450; // the finished line receding
const ANTICIPATE_S = 0.35; // the change starts this long before the next line (once the last word is sung)
const EASE_OUT = "cubic-bezier(0.22, 1, 0.36, 1)"; // quick to start, a soft landing
const EASE_IN_OUT = "cubic-bezier(0.4, 0, 0.6, 1)";
const LONG_GAP_S = 8;
// A song changed or stopped while it sounds fades out over FADE_S (owner, 27 Nov: "fade out / transition old one
// before new one starts"); the server starts the next one at least CHANGE_GAP_S (2 s) into its silent lead-in, so
// it is gone first (routes/karaoke/store.py _start_at).
const FADE_S = 1.6;
// Music while nothing plays (owner, 27 Nov: "if there's no song selected yet, we should play some (quiet) background
// music -- maybe replay last song with lyrics audible but quietly"; 28 Nov: "a bit too high"; "play (with lyrics) the
// one just done, and then pick at random from recent songs"): the server's /now background (the song just sung first,
// then the recent ones), the first and then one at random, quietly, vocals in (instrumental only in child mode), on
// the karaoke screen with the room in a karaoke mode. It fades in, and out when a song starts.
const BG_LEVEL = 0.18;
const BG_FADE_IN_S = 4;
// The room's mode alone saying "no" stops the background music only after this many polls: a Flask restart reads as
// "not karaoke" for a moment (it hasn't looked at the room yet), and that cut the song off (owner, 28 Nov).
const BG_OFF_POLLS = 5;
// A song's lead-in with at least this much silence left is a moment a new build's reload can take (soundGate.ts).
const QUIET_LEAD_S = 5;

// A PROBE, to be taken out once the cause is known (owner, 28 Nov: "background playing songs ... keep stopping halfway
// through"): each start, end and stop of the background music, with why, as one line in logs/room_service.log
// (a path-only request: GET /zz-diag/kbg_<page>_<what>).
const PAGE_TAG = Math.random().toString(36).slice(2, 6);
function bgProbe(what: string): void {
  try {
    void fetch(`${roomApi().baseUrl}/zz-diag/kbg_${PAGE_TAG}_${document.visibilityState}_${what}`, {
      mode: "no-cors",
      keepalive: true,
    }).catch(
      () => undefined,
    );
  } catch {
    /* no room to tell */
  }
}
/** The graphic EQ's bands (Hz): smsound/eq.py BANDS. */
const EQ_HZ = [60, 150, 400, 1000, 2400, 6000, 15000];
const PREVIEW_S = 3.6; // after a long break, the next line is in place this long before it starts: then the count-in

/** Web Audio playback of one song: two buffers started together, the vocal through its own gain. */
class Engine {
  ctx: AudioContext | null = null;
  inst: AudioBuffer | null = null;
  voc: AudioBuffer | null = null;
  songId: string | null = null;
  loading: string | null = null;
  private srcs: AudioBufferSourceNode[] = [];
  private vocGain: GainNode | null = null;
  private out: GainNode | null = null; // this play's own level (fades)
  /** The whole sound's level (the background music's is low). */
  level = 1;
  private startedCtx = 0; // ctx time when position 0 would have been
  private pausedAt = 0;
  playing = false;
  guide = 0;

  context(): AudioContext {
    if (!this.ctx) this.ctx = new AudioContext({ latencyHint: "playback" });
    return this.ctx;
  }

  get running(): boolean {
    return this.ctx?.state === "running";
  }

  /** Load a song's stems (once). Resolves false if another song was asked for meanwhile. */
  async load(songId: string): Promise<boolean> {
    if (this.songId === songId && this.inst) return true;
    if (this.loading === songId) return false;
    this.stop(FADE_S); // another song: the one sounding fades out while this one loads
    this.inst = this.voc = null;
    this.songId = null;
    this.loading = songId;
    const ctx = this.context();
    const get = async (stem: "instrumental" | "vocals") => {
      const r = await fetch(karaoke.audioUrl(songId, stem));
      if (!r.ok) throw new Error(`${stem}: ${r.status}`);
      return ctx.decodeAudioData(await r.arrayBuffer());
    };
    try {
      const [inst, voc] = await Promise.all([get("instrumental"), get("vocals").catch(() => null)]);
      if (this.loading !== songId) return false;
      this.inst = inst;
      this.voc = voc;
      this.songId = songId;
      return true;
    } finally {
      if (this.loading === songId) this.loading = null;
    }
  }

  position(): number {
    if (!this.ctx) return this.pausedAt;
    return this.playing ? this.ctx.currentTime - this.startedCtx : this.pausedAt;
  }

  /** Seconds between a sample leaving Web Audio and leaving the device (what the browser knows of it). */
  outputLatency(): number {
    const c = this.ctx as (AudioContext & { outputLatency?: number }) | null;
    return (c?.outputLatency ?? 0) + (c?.baseLatency ?? 0);
  }

  /** Over: it played to its end. */
  get ended(): boolean {
    return !!this.inst && this.playing && this.position() >= this.inst.duration - 0.05;
  }

  play(at: number, fadeIn = 0): void {
    if (!this.inst) return;
    const ctx = this.context();
    this.stopSources();
    const LEAD_S = 0.05; // it starts this much from now, so it starts that much further into the song
    // A position before 0 is the song's silent lead-in (routes/karaoke/store.py lead_in): the sound starts at 0.
    const at0 = at + LEAD_S;
    const pos = Math.max(0, at0);
    if (pos >= this.inst.duration) {
      this.pausedAt = this.inst.duration;
      this.playing = false;
      return;
    }
    const when = ctx.currentTime + LEAD_S + Math.max(0, -at0);
    const out = ctx.createGain();
    out.connect(this.eqInput(ctx));
    if (fadeIn > 0) {
      out.gain.setValueAtTime(0, when);
      out.gain.linearRampToValueAtTime(this.level, when + fadeIn);
    } else out.gain.value = this.level;
    this.out = out;
    const mk = (buf: AudioBuffer, gain: number) => {
      const s = ctx.createBufferSource();
      s.buffer = buf;
      const g = ctx.createGain();
      g.gain.value = gain;
      s.connect(g).connect(out);
      s.start(when, pos);
      this.srcs.push(s);
      return g;
    };
    mk(this.inst, 1);
    if (this.voc) {
      // The lead vocal: its source -> child mode's mute stage -> the guide level.
      const s = ctx.createBufferSource();
      s.buffer = this.voc;
      const censor = ctx.createGain();
      const g = ctx.createGain();
      g.gain.value = this.guide;
      s.connect(censor).connect(g).connect(out);
      s.start(when, pos);
      this.srcs.push(s);
      this.vocGain = g;
      this.censor = censor;
    } else this.vocGain = null;
    this.startedCtx = when - pos;
    this.playing = true;
    this.applyMutes();
  }

  pause(at: number): void {
    this.pausedAt = at;
    this.stopSources(0.08); // a breath of a fade: no click
    this.playing = false;
  }

  /** Silent, and back to the start; ``fade``: over that many seconds. */
  stop(fade = 0): void {
    this.stopSources(fade);
    this.playing = false;
    this.pausedAt = 0;
  }

  /** Child mode's mutes (song seconds), applied to the lead vocal from now on; the same list again changes nothing. */
  setMutes(spans: [number, number][]): void {
    const key = JSON.stringify(spans);
    if (key === this.mutesKey) return;
    this.mutesKey = key;
    this.mutes = spans;
    this.applyMutes();
  }

  private applyMutes(): void {
    const c = this.censor?.gain;
    if (!c || !this.ctx || !this.playing) return;
    const now = this.ctx.currentTime;
    const R = 0.015; // a short ramp each way: no click
    c.cancelScheduledValues(0);
    c.setValueAtTime(1, now);
    for (const [a, b] of this.mutes) {
      const ca = this.startedCtx + a;
      const cb = this.startedCtx + b;
      if (cb <= now) continue;
      if (ca <= now) c.setValueAtTime(0, now);
      else {
        c.setValueAtTime(1, Math.max(now, ca - R));
        c.linearRampToValueAtTime(0, ca);
      }
      c.setValueAtTime(0, Math.max(now, cb));
      c.linearRampToValueAtTime(1, Math.max(now, cb) + R);
    }
  }

  // The output's graphic EQ (smsound/eq.py; settings.eq): one chain per context that every play goes through, a low
  // shelf, five peaks and a high shelf at EQ_HZ, after a level lowered by the largest boost so a boost never clips.
  private eqIn: GainNode | null = null;
  private eqBands: BiquadFilterNode[] = [];
  private eqGains: number[] = EQ_HZ.map(() => 0);

  private eqInput(ctx: AudioContext): AudioNode {
    if (this.eqIn) return this.eqIn;
    const pre = ctx.createGain();
    let at: AudioNode = pre;
    this.eqBands = EQ_HZ.map((hz, i) => {
      const f = ctx.createBiquadFilter();
      f.type = i === 0 ? "lowshelf" : i === EQ_HZ.length - 1 ? "highshelf" : "peaking";
      f.frequency.value = hz;
      f.Q.value = 1.1;
      at.connect(f);
      at = f;
      return f;
    });
    // the mute (settings.muted): last, so the song and its clock run on silent
    const master = ctx.createGain();
    master.gain.value = this.muted ? 0 : 1;
    at.connect(master).connect(ctx.destination);
    this.master = master;
    this.eqIn = pre;
    this.applyEq(true);
    return pre;
  }

  setEq(g: number[] | null | undefined): void {
    const next = EQ_HZ.map((_, i) => (g && Number.isFinite(g[i]) ? Math.max(-12, Math.min(12, g[i])) : 0));
    if (next.every((v, i) => v === this.eqGains[i])) return;
    this.eqGains = next;
    this.applyEq(false);
  }

  private applyEq(now: boolean): void {
    const ctx = this.ctx;
    if (!ctx || !this.eqIn) return;
    const t = ctx.currentTime;
    const set = (p: AudioParam, v: number) => (now ? (p.value = v) : p.setTargetAtTime(v, t, 0.08));
    this.eqBands.forEach((f, i) => set(f.gain, this.eqGains[i]));
    set(this.eqIn.gain, Math.pow(10, -Math.max(0, ...this.eqGains) / 20));
  }

  private master: GainNode | null = null;
  private muted = false;

  setMuted(m: boolean): void {
    if (m === this.muted) return;
    this.muted = m;
    if (this.master && this.ctx) this.master.gain.setTargetAtTime(m ? 0 : 1, this.ctx.currentTime, 0.03);
  }

  setGuide(g: number): void {
    this.guide = g;
    if (this.vocGain && this.ctx) this.vocGain.gain.setTargetAtTime(g, this.ctx.currentTime, 0.05);
  }

  private censor: GainNode | null = null;
  private mutes: [number, number][] = [];
  private mutesKey = "[]";

  private stopSources(fade = 0): void {
    const ctx = this.ctx;
    const out = this.out;
    if (fade > 0 && ctx && out && this.srcs.length) {
      // The sources fade out on their own (nothing else touches this play's gain again) and then stop.
      const now = ctx.currentTime;
      out.gain.cancelScheduledValues(now);
      out.gain.setValueAtTime(out.gain.value, now);
      out.gain.linearRampToValueAtTime(0, now + fade);
      for (const s of this.srcs) {
        s.onended = () => s.disconnect();
        try {
          s.stop(now + fade + 0.05);
        } catch {
          /* already stopped */
        }
      }
      window.setTimeout(() => out.disconnect(), (fade + 0.5) * 1000);
    } else {
      for (const s of this.srcs) {
        try {
          s.stop();
        } catch {
          /* already stopped */
        }
        s.disconnect();
      }
      out?.disconnect();
    }
    this.srcs = [];
    this.out = null;
    this.vocGain = null;
    this.censor = null;
  }
}

/** The karaoke's sound plays on this page: its output is this screen's ("screen:<id>"), or any screen's on a page
 *  without one; on a browser's own player (``device``: the app's KaraokeHere), only its own "device:<id>". */
function soundHere(n: KaraokeNow, screen: string | undefined, device?: string): boolean {
  const out = n.settings.output ?? "";
  if (device) return out === device;
  return out.startsWith("screen:") && (!screen || out === `screen:${screen}`);
}

/** The song's position by the server's clock, at this moment. */
function serverPosition(n: KaraokeNow, skew: number): number {
  const p = n.player;
  return p.state === "playing" ? p.pos_s + Math.max(0, Date.now() / 1000 + skew - p.at) : p.pos_s;
}

/** The karaoke layer. ``device``: a browser's own player (This device, owner 27 Nov): it plays the sound when the
 *  karaoke's output is that id, and shows nothing (the lyrics stay on the karaoke screen). */
export function KaraokeLayer({ screen, device }: { screen?: string; device?: string } = {}) {
  const [on, setOn] = useState(false);
  // The deployment (its screens): which screen is the karaoke screen (karaokeScreen). Only that screen's page shows
  // the lyrics and, with output "screen", plays the sound; on any other screen this layer stays empty and silent.
  const depRef = useRef<DeploymentInfo | null>(null);
  const [now, setNow] = useState<KaraokeNow | null>(null);
  const [lyrics, setLyrics] = useState<{
    id: string;
    key: string;
    doc: KaraokeLyrics;
  } | null>(null);
  const [, setTick] = useState(0);
  const engine = useRef(new Engine());
  // This browser's output latency, told to the app's one store while its output is unmeasured (smsound.latency
  // set_browser), so the lights and the screens' lyric feeds use the delay this page's lyrics use (a8's audit, 27 Nov:
  // the page used its own figure, the feeds 0). Every 10 s, when it moves by 5 ms or more.
  const nowRef = useRef<KaraokeNow | null>(null);
  nowRef.current = now;
  const posted = useRef<number | null>(null);
  useEffect(() => {
    const id = window.setInterval(() => {
      const n = nowRef.current;
      const e = engine.current;
      const out = n?.settings.output;
      if (!n || !out || !e.running || !soundHere(n, screen, device)) return;
      const ms = Math.round(e.outputLatency() * 1000);
      if (posted.current != null && Math.abs(posted.current - ms) < 5) return;
      posted.current = ms;
      void sound.setBrowser(out, ms).catch(() => {
        posted.current = null;
      });
    }, 10_000);
    return () => window.clearInterval(id);
  }, [screen, device]);
  const bgEngine = useRef(new Engine()); // the music while nothing plays
  const mutesRef = useRef<[number, number][]>([]);
  const skew = useRef(0); // server clock - this clock, seconds
  const heldSince = useRef<number | null>(null); // when the browser began holding the sound back (SoundPrompt)

  // The browser holds the sound until the page is clicked (owner, 26 Nov: "if no sound is playing ... but it should be
  // playing, show a 'click for sound' on the display"): any click, tap or key anywhere on the page lets it start, in
  // that gesture (a resume outside one is refused). Nothing else on the page is changed or stopped by it.
  useEffect(() => {
    if (!on) return;
    const unlock = () => {
      const c = engine.current.context();
      if (c.state !== "running") void c.resume().catch(() => undefined);
    };
    const opts = { capture: true, passive: true } as const;
    window.addEventListener("pointerdown", unlock, opts);
    window.addEventListener("keydown", unlock, opts);
    return () => {
      window.removeEventListener("pointerdown", unlock, opts);
      window.removeEventListener("keydown", unlock, opts);
    };
  }, [on]);

  // Is karaoke on here at all?
  useEffect(() => {
    let dead = false;
    getDeploymentRelative()
      .then((d) => {
        if (dead) return;
        depRef.current = d;
        setOn(capabilityOn(d, "karaoke") === true);
      })
      .catch(() => undefined);
    return () => {
      dead = true;
    };
  }, []);

  // Follow the server: poll, and make the sound match it.
  useEffect(() => {
    if (!on) return;
    let dead = false;
    let busy = false;
    let placed: string | null = null; // the player's song + rev the sound was last placed for
    let placedAt = { wall: 0, pos: 0 }; // where, by this machine's clock
    const eng = engine.current;
    // The background music: its own engine, quiet; the song it is on; whether it is wanted now (read after a load).
    const bg = bgEngine.current;
    bg.level = BG_LEVEL;
    let bgSong: string | null = null;
    let bgWanted = false;
    let bgBusy = false;
    let bgOff = 0; // polls in a row the room's mode alone has said no
    const background = async (list: string[], child: boolean) => {
      if (bgBusy || (bg.playing && !bg.ended)) return;
      bgBusy = true;
      if (!bg.ctx) bg.ctx = eng.context(); // the songs' own context: one sound, unlocked by the same click
      try {
        // the one just sung first; then any of the others at random, never the same twice running
        const pool = list.slice(1).filter((x) => x !== bgSong);
        const id = !bgSong || !pool.length ? list[0] : pool[Math.floor(Math.random() * pool.length)];
        if (!(await bg.load(id).catch(() => false)) || dead || !bgWanted) return;
        bg.guide = child ? 0 : 1;
        if (bg.playing && bg.ended) bgProbe(`end_${bgSong}`);
        bg.play(0, BG_FADE_IN_S);
        bgSong = id;
        bgProbe(`start_${id}_of${Math.round(bg.inst?.duration ?? 0)}s_ctx${bg.ctx?.state}`);
        if (bg.ctx?.state !== "running") void bg.ctx?.resume().catch(() => undefined);
      } finally {
        bgBusy = false;
      }
    };
    // The server's clock minus this one, from the quickest of the last SKEW_N replies (its midpoint): a reply that
    // came back late would otherwise put the sound that much behind for as long as it was the latest.
    const samples: { rtt: number; skew: number }[] = [];
    const poll = async (): Promise<KaraokeNow | null> => {
      const sent = Date.now();
      const n = await karaoke.now();
      const got = Date.now();
      samples.push({ rtt: got - sent, skew: n.now - (sent + got) / 2000 });
      if (samples.length > SKEW_N) samples.shift();
      skew.current = samples.reduce((a, b) => (b.rtt < a.rtt ? b : a)).skew;
      return n;
    };
    const follow = async () => {
      if (busy) return; // one at a time: a slow reply or a song still loading isn't overtaken by the next poll
      busy = true;
      try {
        await step();
      } finally {
        busy = false;
      }
    };
    const step = async () => {
      let n: KaraokeNow | null;
      try {
        n = await poll();
      } catch {
        return; // Flask restarting: next time
      }
      if (dead || !n) return;
      setNow(n);
      const e = engine.current;
      const p = n.player;
      // Sound here only when the karaoke's output is this screen's page (docs/SOUND.md: "screen:<id>"; a Sonos plays
      // it itself: routes/karaoke/sonos_out.py; a page without a screen plays any screen output).
      if (!soundHere(n, screen, device)) {
        e.stop();
        if (bg.playing) bgProbe(`stop_elsewhere_at${Math.round(bg.position())}s`);
        bg.stop();
        bgWanted = false;
        setSounding(false);
        return;
      }
      // A new build's reload waits for the end of the song sounding here (soundGate.ts); once it is let through,
      // nothing new starts.
      setSounding(
        (p.state === "playing" && serverPosition(n, skew.current) > -QUIET_LEAD_S) || (bg.playing && !bg.ended),
      );
      if (leaving()) return;
      // The background music: while nothing plays, the room in a karaoke mode, no calibration running.
      bgWanted =
        p.state === "stopped" &&
        !!n.join?.room_karaoke &&
        !!n.background?.length &&
        n.calibration?.state !== "running";
      const modeOnly = p.state === "stopped" && !!n.background?.length && n.calibration?.state !== "running";
      if (bgWanted) {
        bgOff = 0;
        void background(n.background ?? [], !!n.settings.child_mode);
      } else if (bg.playing && (!modeOnly || ++bgOff >= BG_OFF_POLLS)) {
        const why =
          p.state !== "stopped"
            ? `song_${p.state}`
            : !n.join?.room_karaoke
              ? "room_not_karaoke"
              : !n.background?.length
                ? "no_list"
                : "calibration";
        bgProbe(`stop_${why}_at${Math.round(bg.position())}s`);
        bg.stop(FADE_S);
      }
      if (p.state !== "stopped") bgSong = null; // a song plays: the next background starts with it
      e.setGuide(n.settings.guide);
      e.setEq(n.settings.eq);
      bg.setEq(n.settings.eq);
      e.setMuted(!!n.settings.muted);
      bg.setMuted(!!n.settings.muted);
      e.setMutes(mutesRef.current);
      if (p.state === "stopped" || !p.song_id) {
        e.stop(FADE_S);
        return;
      }
      if (!(await e.load(p.song_id).catch(() => false)) || dead) return;
      const ctx = e.context();
      if (ctx.state !== "running") void ctx.resume().catch(() => undefined); // starts whenever the browser allows
      const target = serverPosition(n, skew.current);
      if (p.state === "playing") {
        const want = `${p.song_id}:${p.rev}:${e.running}`; // and again once the browser lets it sound
        const expected = placedAt.pos + (Date.now() - placedAt.wall) / 1000;
        const off = e.playing && e.running ? e.position() - expected : 0;
        if (!e.playing || placed !== want || Math.abs(off) > RESYNC_S) {
          const at = placed === want ? expected : target;
          if (placed === want) console.info(`karaoke: sound ${off.toFixed(2)} s off where it should be: re-placed`);
          e.play(at);
          placed = want;
          placedAt = { wall: Date.now(), pos: at };
        } else if (soundHere(n, screen, device)) {
          // In place and sounding: tell the server where the sound really is, so its clock (the lights' grid, the
          // other screens) follows this one rather than drifting on its own (a8's audit, 27 Nov).
          void karaoke.heard(p.rev, p.song_id, e.position()).catch(() => undefined);
        }
      } else if (e.playing || Math.abs(e.position() - target) > 0.05) {
        e.pause(target);
      }
    };
    void follow();
    const t = window.setInterval(follow, POLL_MS);
    const leavingPage = () => bg.playing && bgProbe(`stop_pagehide_at${Math.round(bg.position())}s`);   // (the probe)
    window.addEventListener("pagehide", leavingPage);
    return () => {
      dead = true;
      window.clearInterval(t);
      window.removeEventListener("pagehide", leavingPage);
      eng.stop();
      if (bg.playing) bgProbe(`stop_unmount_at${Math.round(bg.position())}s`);
      bg.stop();
      setSounding(false);
    };
  }, [on, device]);

  const song = now?.song ?? null;
  const p = now?.player;

  // The song's lyrics; again when child mode changes (the server then serves them with dashes and mute times), and
  // when the careful pass (or its undo) has timed them again (lyrics_rev).
  const child = !!now?.settings.child_mode;
  const lyricsKey = song?.id ? `${song.id}:${child}:${song.lyrics_rev ?? 0}` : null;
  useEffect(() => {
    if (!lyricsKey || !song?.id || lyrics?.key === lyricsKey) return;
    let dead = false;
    const id = song.id;
    karaoke
      .lyrics(id)
      .then((doc) => !dead && setLyrics({ id, key: lyricsKey, doc }))
      .catch(() => {
        // Flask restarting, or an error: no lyrics for now, and ask again in a few seconds (never cached as "none").
        if (dead) return;
        setLyrics({ id, key: lyricsKey, doc: { kind: "none", lines: [] } });
        retry = window.setTimeout(() => !dead && setLyrics(null), LYRICS_RETRY_MS);
      });
    let retry = 0;
    return () => {
      dead = true;
      window.clearTimeout(retry);
    };
  }, [lyricsKey, song?.id, lyrics?.key]);
  // Child mode's mutes, for the engine (the poll loop reads them).
  mutesRef.current = child && lyrics?.id === song?.id ? (lyrics?.doc.mutes ?? []) : [];

  // Redraw every frame while a song is up.
  const active = on && !!p && p.state !== "stopped" && !!song;
  useEffect(() => {
    if (!active || device) return; // a browser's own player draws nothing
    let raf = 0;
    const loop = () => {
      setTick((x) => (x + 1) % 1_000_000);
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [active, device]);

  // A song that ends in the dark (its outro, Curtain) keeps the screen dark a little past its end (Handover), while
  // the art director takes the scene down to black on its own (the song's end: a fade-dark), so the two fades read as
  // one: without it the veil went with the song and the scene showed again, mid-fade (owner, 26 Nov: "double fade").
  type Frame = { key: string; t: number; dur: number | null };
  const lastFrame = useRef<Frame | null>(null);
  const endedFrame = useRef<Frame | null>(null); // the song before's last frame, once the next one has drawn
  const [handover, setHandover] = useState<number | null>(null);
  const songKey = active && song ? `${song.id}:${p?.qid ?? ""}` : null;
  useEffect(() => {
    const prev = lastFrame.current;
    const last = endedFrame.current ?? (prev && prev.key !== songKey ? prev : null);
    endedFrame.current = null;
    if (!songKey) lastFrame.current = null;
    if (last?.dur && last.t >= last.dur - HANDOVER_NEAR_S) setHandover(Date.now());
  }, [songKey]);

  if (!on || !now || device) return null;
  // At rest (the room in a karaoke mode, nothing playing) every screen shows the code that opens the panel on a phone
  // (JoinOverlays.tsx); the other screens show nothing else.
  const joinUrl = now.join?.url ?? null;
  const resting = !active && !!now.join?.room_karaoke && !!joinUrl;
  if (screen && screen !== karaokeScreen(depRef.current, now.settings.screen))
    return resting && joinUrl ? (
      <div data-sm-karaoke="" style={layerStyle}>
        <JoinCard url={joinUrl} />
      </div>
    ) : null; // not the karaoke screen

  // The position: the sound's own while it plays here, else the server's clock; less the delay to the room: the
  // output's (settings.audio_delay_ms: its measured delay from the app's one store, smsound.latency; 0 on a Sonos,
  // whose clock the server already anchors to what the room hears), else, when this page plays it and it was never
  // measured, the browser's own idea of its output latency. The lyrics on another screen follow the same delay.
  const e = engine.current;
  const audioPos = e.playing && e.running && e.songId === song?.id ? e.position() : serverPosition(now, skew.current);
  const ownSound = soundHere(now, screen);
  const measured = now.settings.audio_delay_ms;
  const lag = measured != null ? measured / 1000 : ownSound ? e.outputLatency() : 0;
  const shift = ((song?.offset_ms ?? 0) + (song?.user_offset_ms ?? 0)) / 1000;
  const t = audioPos - lag - shift;
  // Read by the sync evals (tools/karaoke/evals/): the clocks this frame was drawn with. Nothing reads it in use.
  (window as unknown as { __smKaraoke?: object }).__smKaraoke = {
    t,
    audioPos,
    shift,
    ctxTime: e.ctx?.currentTime ?? null,
    enginePlaying: e.playing,
    running: e.running,
    songId: song?.id ?? null,
  };
  // Should be sounding but the browser holds it (the context exists, the song is loaded, and it isn't running), for
  // over a second: the "Click for sound" prompt (no flash while a song loads or a resume is on its way).
  const shouldSound = p?.state === "playing" && !!song && e.songId === song.id;
  const held = shouldSound && !!e.ctx && e.ctx.state !== "running";
  if (!held) heldSince.current = null;
  else if (heldSince.current === null) heldSince.current = Date.now();
  const soundHeld = held && heldSince.current !== null && Date.now() - heldSince.current > 1000;
  // While a song plays: who is next, towards its end (NextUp); with nothing queued after it, the small join code.
  const dur = song?.audio_s ?? song?.duration ?? null;
  const left = active && dur ? dur - t : null;
  const nearEnd = now.up_next && left != null && left > 0 && left <= NEXT_UP_S ? ramp(NEXT_UP_S - left, 0, 1.2) : 0;
  // ...and for a few seconds as a song starts, with its singer's card, so the next person knows early (owner, 27 Nov).
  const t0 = -(song?.lead_in_s ?? 0);
  const atStart =
    active && now.up_next ? ramp(t, t0 + 1, t0 + 2.2) * (1 - ramp(t, t0 + 1 + NEXT_AT_START_S, t0 + 2.2 + NEXT_AT_START_S)) : 0;
  const nextUp = Math.max(nearEnd, atStart);
  const low = active && joinUrl && now.queued_after === 0 ? ramp(t, LOW_FROM_S, LOW_FROM_S + 1.2) : 0;
  if (songKey && song) {
    if (lastFrame.current && lastFrame.current.key !== songKey) endedFrame.current = lastFrame.current; // a new song
    lastFrame.current = { key: songKey, t, dur: song.audio_s ?? song.duration ?? null };
  }

  return (
    <div data-sm-karaoke="" style={layerStyle}>
      {handover !== null && <Handover key={handover} onDone={() => setHandover(null)} />}
      {active && song && (
        <>
          <Curtain
            t={t}
            song={song}
            doc={lyrics?.id === song.id ? lyrics.doc : null}
            singer={namesText(now.singers)}
            photos={photosOf(now.singers, now.singer_photos)}
          />
          <Lyrics
            t={t}
            doc={lyrics?.id === song.id ? lyrics.doc : null}
            highlight={(now.settings.highlight as Highlight) ?? "words"}
            leadIn={song.lead_in_s ?? 0}
            singers={now.singers}
          />
        </>
      )}
      {/* every overlay comes and goes through Presence: it rises or slides in out of a blur, and sinks back out */}
      <Presence show={!active && !!now.up_next}>
        {now.up_next && (
          <UpNext
            title={now.up_next.title}
            artist={now.up_next.artist}
            singer={namesText(now.up_next.singers)}
            photos={photosOf(now.up_next.singers, now.singer_photos)}
          />
        )}
      </Presence>
      <Presence show={!!(resting && joinUrl)} from="right">
        {joinUrl && <JoinCard url={joinUrl} />}
      </Presence>
      <Presence show={nextUp > 0.001 && !!now.up_next} from="right">
        {now.up_next && (
          <NextUp
            title={now.up_next.title}
            artist={now.up_next.artist}
            singers={now.up_next.singers}
            photos={photosOf(now.up_next.singers, now.singer_photos)}
            opacity={1}
            left={left != null ? Math.min(1, left / NEXT_UP_S) : null}
            inS={left != null && left > NEXT_UP_S ? left : null}
          />
        )}
      </Presence>
      <Presence show={low > 0.001 && !!joinUrl} from="right">
        {joinUrl && <JoinCard url={joinUrl} small />}
      </Presence>
      <Presence show={soundHeld} from="centre">
        <SoundPrompt />
      </Presence>
    </div>
  );
}

/** "Click for sound": shown while a song (or the speaker calibration) should be sounding and the browser has held the
 *  sound back for over a second (it allows sound only after a click unless the site may autoplay). Any click or key
 *  on the page lets it start (KaraokeLayer's unlock); it goes the moment the sound runs. */
function SoundPrompt() {
  return (
    <div style={soundPromptStyle} data-sm-karaoke-sound="held">
      <span style={{ fontSize: "1.3em", lineHeight: 1 }} aria-hidden>
        🔇
      </span>
      <span>Click anywhere for sound</span>
    </div>
  );
}

const soundPromptStyle: CSSProperties = {
  position: "absolute",
  top: "5vh",
  left: "50%",
  transform: "translateX(-50%)",
  display: "flex",
  alignItems: "center",
  gap: "0.6em",
  padding: "0.6em 1.2em",
  borderRadius: "999px",
  background: "rgba(0,0,0,0.72)",
  border: "2px solid rgba(255,214,90,0.9)",
  color: "#fff",
  fontFamily: "'Segoe UI', 'Helvetica Neue', Arial, sans-serif",
  fontWeight: 700,
  fontSize: "min(2.6vw, 44px)",
  boxShadow: "0 0.6vh 3vh rgba(0,0,0,0.5)",
  animation: "smKaraokeSoundPulse 1.6s ease-in-out infinite",
};
if (typeof document !== "undefined" && !document.getElementById("sm-karaoke-sound-pulse")) {
  const st = document.createElement("style");
  st.id = "sm-karaoke-sound-pulse";
  st.textContent = "@keyframes smKaraokeSoundPulse{0%,100%{opacity:1}50%{opacity:.55}}";
  document.head.appendChild(st);
}

// ---------------------------------------------------------------- the curtain: each song's start and end
// (owner, 25 Nov: "introduce each song properly and signal end / beginning theatre ... singers will be changing over
// at this point"). On the lyrics clock (the room's), so it lines up with what is heard.
const NEXT_AT_START_S = 7; // the next-up banner also shows this long as a song starts
const INTRO_MIN_S = 3; // the intro card stays at least this long (a song that starts singing at once)...
const INTRO_MAX_S = 12; // ...and at most this long, else until a second before the first line
const FADE_UP_S = 2.5; // the scene fades up from dark at the start
const OUTRO_S = 10; // and down to dark over the song's last seconds
const OUTRO_VEIL = 0.92; // how dark
const BALANCE = { textWrap: "balance" } as CSSProperties;
const ramp = (x: number, a: number, b: number) => Math.max(0, Math.min(1, (x - a) / Math.max(1e-6, b - a)));

/** A cover at a size fit for the screen (the catalogues hand out thumbnails). */
function bigArt(url: string | null | undefined): string | null {
  if (!url) return null;
  return url.replace(/\/\d+x\d+bb\./, "/600x600bb.").replace(/\/\d+x\d+-000000-80-0-0\./, "/500x500-000000-80-0-0.");
}

const HANDOVER_NEAR_S = 2.5; // a song that ended this close to its end ended in the dark (the outro's veil)...
const HANDOVER_HOLD_S = 3.2; // ...which stays this long (the art director's fade-dark: 2.5 s)...
const HANDOVER_FADE_S = 2.5; // ...then lifts, onto the black the scene went to

/** The dark across a song's end (KaraokeLayer): the outro's veil held, then lifted. Its own animation (the frame loop
 *  stops with the song). */
function Handover({ onDone }: { onDone: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const done = useRef(onDone);
  done.current = onDone;
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const total = HANDOVER_HOLD_S + HANDOVER_FADE_S;
    const anim = el.animate(
      [
        { opacity: OUTRO_VEIL, offset: 0 },
        { opacity: OUTRO_VEIL, offset: HANDOVER_HOLD_S / total, easing: "ease-in-out" },
        { opacity: 0, offset: 1 },
      ],
      { duration: total * 1000, fill: "forwards" },
    );
    anim.onfinish = () => done.current();
    return () => anim.cancel();
  }, []);
  return <div ref={ref} style={{ position: "absolute", inset: 0, background: "#000", opacity: OUTRO_VEIL }} />;
}

/** The singers' own photos (routes/karaoke/singer_photos.py; owner, 28 Nov), in the names' order. */
function photosOf(names: string[] | undefined, map: Record<string, string> | undefined): string[] {
  return (names ?? []).map((n) => map?.[n]).filter((u): u is string => !!u);
}

function Curtain({
  t,
  song,
  doc,
  singer,
  photos = [],
}: {
  t: number;
  song: KaraokeSong;
  doc: KaraokeLyrics | null;
  singer?: string | null;
  photos?: string[];
}) {
  const lines = doc?.kind === "synced" ? doc.lines : [];
  const first = lines.find((l) => l.start != null)?.start ?? null;
  const last = [...lines].reverse().find((l) => l.start != null) ?? null;
  const lastEnd = last ? (last.end ?? (last.start ?? 0) + 3) : null;
  const dur = song.audio_s ?? song.duration ?? null;
  // The intro, from the start of the song's silent lead-in (t0 <= 0: routes/karaoke/store.py lead_in): the card until
  // a second before the first line (INTRO_MIN_S..INTRO_MAX_S), over a scene fading up.
  const t0 = -(song.lead_in_s ?? 0);
  const introEnd = t0 + (first == null ? 8 : Math.min(INTRO_MAX_S, Math.max(INTRO_MIN_S, first - t0 - 1)));
  const card = ramp(t, t0, t0 + 0.6) * (1 - ramp(t, introEnd - 0.8, introEnd));
  const fadeUp = 1 - ramp(t, t0, t0 + FADE_UP_S);
  // The outro: the scene fades to dark over the song's last OUTRO_S (the lyrics stay above it, readable); the song
  // is named in the corner from then, or from a second after its last line if that comes sooner (a long play-out).
  let outro = 0;
  let cornerAt = Infinity;
  if (dur) {
    outro = ramp(t, dur - OUTRO_S, dur);
    cornerAt = Math.min(dur - OUTRO_S, (lastEnd ?? dur) + 1);
  }
  const veil = Math.max(OUTRO_VEIL * fadeUp, 0.5 * card, OUTRO_VEIL * outro * outro * (3 - 2 * outro));
  const corner = ramp(t, cornerAt, cornerAt + 1);
  const art = bigArt(song.art);
  const writers = (song.writers ?? []).filter(Boolean);
  const facts = [song.album, song.year].filter(Boolean).join(" · ");
  return (
    <>
      {veil > 0.001 && <div style={{ position: "absolute", inset: 0, background: "#000", opacity: veil }} />}
      {card > 0.001 && (
        <div style={{ ...introStyle, opacity: card, transform: `translate(-50%, -50%) scale(${0.96 + 0.04 * card})` }}>
          {art && <img src={art} alt="" style={introArtStyle} />}
          <div style={{ minWidth: 0, ...baseTextStyle }}>
            {/* The singer as big as the song (owner, 27 Nov: "show the name of the singer ... prominently"). */}
            {singer && (
              <div
                style={{ fontSize: "min(5vw, 84px)", fontWeight: 800, lineHeight: 1.05, color: "#ffd65a", marginBottom: "0.25em", ...BALANCE }}
              >
                {photos.length ? (
                  <span style={{ marginRight: "0.3em" }}>
                    <Faces photos={photos} size="1.5em" />
                  </span>
                ) : (
                  <span aria-hidden style={{ fontSize: "0.8em", marginRight: "0.25em" }}>🎤</span>
                )}
                {singer}
              </div>
            )}
            <div style={{ fontSize: "min(5.5vw, 92px)", fontWeight: 800, lineHeight: 1.05, ...BALANCE }}>
              {song.title}
            </div>
            {song.artist && (
              <div style={{ fontSize: "min(3vw, 50px)", fontWeight: 600, opacity: 0.92, marginTop: "0.25em" }}>
                {song.artist}
              </div>
            )}
            {writers.length > 0 && (
              <div style={{ fontSize: "min(1.8vw, 30px)", opacity: 0.8, marginTop: "0.9em" }}>
                Written by {writers.slice(0, 4).join(", ")}
                {writers.length > 4 ? " and others" : ""}
              </div>
            )}
            {facts && <div style={{ fontSize: "min(1.6vw, 26px)", opacity: 0.7, marginTop: "0.35em" }}>{facts}</div>}
            {doc && doc.kind !== "synced" && (
              <div style={{ fontSize: "min(1.6vw, 26px)", opacity: 0.7, marginTop: "0.8em" }}>
                {doc.kind === "plain" ? "Lyrics without timings" : "No lyrics found"}
              </div>
            )}
          </div>
        </div>
      )}
      {corner > 0.001 && (
        <div style={{ ...cornerStyle, opacity: corner }}>
          {art && (
            <img src={art} alt="" style={{ width: "9vh", height: "9vh", borderRadius: "0.8vh", objectFit: "cover" }} />
          )}
          <div style={{ minWidth: 0, ...baseTextStyle }}>
            <div style={{ fontSize: "min(2.4vw, 40px)", fontWeight: 800, lineHeight: 1.1 }}>{song.title}</div>
            {song.artist && <div style={{ fontSize: "min(1.6vw, 26px)", opacity: 0.85 }}>{song.artist}</div>}
          </div>
        </div>
      )}
    </>
  );
}

function currentIndex(lines: LyricLine[], t: number): number {
  let idx = -1;
  for (let i = 0; i < lines.length; i++) {
    if ((lines[i].start ?? Infinity) <= t) idx = i;
    else break;
  }
  return idx;
}

function Lyrics({
  t,
  doc,
  highlight,
  leadIn = 0,
  singers,
}: {
  t: number;
  doc: KaraokeLyrics | null;
  highlight: Highlight;
  /** The song's silent lead-in (s): the countdown to its first line runs from its start. */
  leadIn?: number;
  /** Who sing it: with two or more, a duet's parts carry their names (the first named takes the voice that sings
   *  first). */
  singers?: string[];
}) {
  // A line starts no later than its first timed word (the aligner can place it a little before the line's own
  // start): the change of lines then comes before that word, and the ball finds it on screen.
  const lines = useMemo(
    () =>
      (doc?.kind === "synced" ? doc.lines : []).map((l) => {
        const w0 = l.words?.find(Boolean)?.t0;
        return w0 != null && l.start != null && w0 < l.start ? { ...l, start: w0 } : l;
      }),
    [doc],
  );
  const i = currentIndex(lines, t);
  const cur = i >= 0 ? lines[i] : null;
  const next = lines[i + 1] ?? null;
  // A line is done at its end, or ANTICIPATE_S before the next one once its last word is sung: the change has settled
  // by the time the next line's first word lands.
  const lastWord = cur?.words ? [...cur.words].reverse().find(Boolean) : null;
  const sung = !cur?.words || !lastWord || t >= lastWord.t1;
  const ball = highlight !== "words";
  // With the ball, the lines change exactly as it takes off for the next line's first word (ballAt's hop across), so
  // it leaves from the word where it is while the old line evaporates behind it; without it, ANTICIPATE_S ahead once
  // the last word is sung.
  const nextFirst = next?.words?.find(Boolean);
  const hopAcross =
    ball && lastWord && nextFirst && nextFirst.t0 - lastWord.t1 < LONG_GAP_S
      ? Math.min(HOP_ACROSS_S, (nextFirst.t0 - lastWord.t0) * HOP_SHARE)
      : null;
  const done =
    !cur ||
    cur.end == null ||
    t >= cur.end ||
    (next?.start != null &&
      (hopAcross != null ? t >= nextFirst!.t0 - hopAcross : sung && t >= next.start - ANTICIPATE_S));
  const curOn = !!cur && !done;
  const untilNext = next?.start != null ? next.start - t : Infinity;
  // The silence before the next line: from the end of this one (from the song's start before the first).
  const gap = next?.start != null ? next.start - (cur ? (cur.end ?? next.start) : -leadIn) : Infinity;
  const longGap = gap >= LONG_GAP_S;
  // Which line is big, which small (by index).
  // After a long break the next line takes its big place PREVIEW_S before it starts, and the dots count it in from
  // above it (owner, 25 Nov: "see the lyric in place a bit earlier and then count it in").
  const countIn = !curOn && !!next && longGap && untilNext <= PREVIEW_S;
  const bigIdx = curOn ? i : next && (!longGap || countIn) ? i + 1 : null;
  const bigLine = bigIdx != null ? lines[bigIdx] : null;
  const smallIdx = bigIdx != null ? bigIdx + 1 : i + 1;
  const small = lines[smallIdx] ?? null;
  // A duet (owner, 28 Nov): each voice on its own side in its own colour, shared lines centred in a third; the name
  // of whoever is up as the part changes.
  const duet = !!doc?.duet;
  const voiceOf = (l: LyricLine | null) => (duet && l ? l.voice : undefined);
  const tag = duet && bigLine && bigIdx != null ? partTag(lines, bigIdx, singers, t) : null;

  // The change of lines (owner, 25 Nov: smoother, more graceful, not distracting; "visual theatre"): the small line
  // travels from where it was into the big place, growing as it goes (measured: the eye never loses it); the finished
  // line recedes: drifts up, shrinks a little, fades; the new small line fades in a beat later. Transforms and
  // opacity only (the compositor's work, so the live render underneath doesn't feel it).
  const bigRef = useRef<HTMLDivElement>(null);
  const smallRef = useRef<HTMLDivElement>(null);
  const ballRef = useRef<HTMLDivElement>(null);
  const wordEls = useRef(new Map<string, HTMLSpanElement>());
  // Where each word was last seen (screen px): x its middle, y the top of its letters, `under` whether a row of its
  // line is above it (the ball's arc there stays below that row's letters).
  const wordPos = useRef(new Map<string, { x: number; y: number; under: boolean }>());
  const W = useMemo(() => ballWords(lines), [lines]);
  const register = (idx: number | null) => (k: number, el: HTMLSpanElement | null) => {
    if (idx == null) return;
    const key = `${idx}:${k}`;
    if (el) wordEls.current.set(key, el);
    else if (wordEls.current.get(key) && !wordEls.current.get(key)!.isConnected) wordEls.current.delete(key);
  };
  // Both lines' words: the ball can aim at the next line's first word while it is still small, and follow it as it
  // slides up into the big place (the slide starts from exactly there).
  const onBigWord = useCallback(register(bigIdx), [bigIdx]); // eslint-disable-line react-hooks/exhaustive-deps
  const onSmallWord = useCallback(register(smallIdx), [smallIdx]); // eslint-disable-line react-hooks/exhaustive-deps
  const outRef = useRef<HTMLDivElement>(null);
  const was = useRef<{ big: number | null; small: number | null; smallRect: DOMRect | null; bigRect: DOMRect | null }>({
    big: null,
    small: null,
    smallRect: null,
    bigRect: null,
  });
  // The finished line leaves from exactly where it was, at exactly its width (so its words wrap as they did: owner,
  // 25 Nov: "don't change its line wrapping ... it should just move up and fade away").
  const leaving = useRef<{ idx: number; until: number; rect: DOMRect | null } | null>(null);
  const bigChanged = bigIdx !== was.current.big;
  const smallChanged = smallIdx !== was.current.small;
  if (bigChanged && was.current.big != null && lines[was.current.big])
    leaving.current = { idx: was.current.big, until: performance.now() + LEAVE_MS, rect: was.current.bigRect };
  const out = leaving.current && performance.now() < leaving.current.until ? lines[leaving.current.idx] : null;
  const outKey = leaving.current?.idx;
  useLayoutEffect(() => {
    const prev = was.current;
    if (bigChanged && bigRef.current) {
      const to = bigRef.current.getBoundingClientRect();
      const from = prev.small === bigIdx ? prev.smallRect : null;
      bigRef.current.animate(
        from
          ? [
              {
                transform: `translate(${from.left + from.width / 2 - (to.left + to.width / 2)}px, ${
                  from.top + from.height / 2 - (to.top + to.height / 2)
                }px) scale(${SMALL_VW / BIG_VW})`,
                opacity: SMALL_OPACITY,
              },
              { transform: "none", opacity: 1 },
            ]
          : [
              { transform: "translateY(18%) scale(0.96)", opacity: 0 },
              { transform: "none", opacity: 1 },
            ],
        { duration: MORPH_MS, easing: EASE_OUT },
      );
    }
    if (smallChanged && smallRef.current)
      smallRef.current.animate(
        [
          { transform: "translateY(35%)", opacity: 0 },
          { transform: "none", opacity: 1 },
        ],
        { duration: 420, delay: bigChanged ? 160 : 0, easing: EASE_OUT, fill: "backwards" },
      );
    was.current = {
      big: bigIdx,
      small: smallIdx,
      smallRect: smallRef.current?.getBoundingClientRect() ?? null,
      bigRect: bigRef.current?.getBoundingClientRect() ?? null,
    };
    placeBall();
  });

  /** The ball, each frame: where the words are now (a line sliding into place carries its words with it), then
   *  where the ball is between them. */
  function placeBall() {
    const el = ballRef.current;
    if (!el) return;
    // On the screen's own coordinates (the ball is position: fixed): the lyrics block's top moves as its lines change
    // height, so positions remembered against it would go stale.
    const size = el.offsetHeight;
    const seen: [string, DOMRect][] = [];
    for (const [key, w] of wordEls.current) if (w.isConnected) seen.push([key, w.getBoundingClientRect()]);
    const firstRow = new Map<string, number>(); // each line's top row
    for (const [key, r] of seen) {
      const li = key.split(":")[0];
      firstRow.set(li, Math.min(firstRow.get(li) ?? Infinity, r.top));
    }
    for (const [key, r] of seen) {
      const font = r.height / LINE_HEIGHT; // a word's box is its row's line height: its letters sit in the middle
      wordPos.current.set(key, {
        x: r.left + r.width / 2,
        y: r.top + (r.height - font) / 2,
        under: r.top > (firstRow.get(key.split(":")[0]) ?? r.top) + 2,
      });
    }
    const st = ball ? ballAt(W, t) : null;
    const pos = (k: string) => wordPos.current.get(k) ?? null;
    const far = window.innerWidth * 0.4;
    let a: { x: number; y: number; under?: boolean } | null = null;
    let b: { x: number; y: number; under?: boolean } | null = null;
    let u = 0;
    let op = 1;
    let landed = Infinity;
    // Only words on screen hold the ball: a word whose line has gone, or whose line isn't up yet (a line with no word
    // timings in between), sends it off to the right, and it comes back in from the left onto the next one shown.
    const shown = (k: string) => !!wordEls.current.get(k)?.isConnected;
    if (st?.kind === "hop") {
      const fromOn = shown(st.from);
      const toOn = shown(st.to);
      if (toOn && (fromOn || st.u > 0)) {
        a = pos(st.from) ?? pos(st.to); // a hop under way carries on from where the word was
        b = pos(st.to);
        u = st.u;
        landed = st.landed;
      } else if (toOn) {
        // Its word gone and the hop not begun: in from the left onto the next one.
        b = pos(st.to);
        a = b ? { x: Math.max(-size * 2, b.x - far), y: b.y } : null;
        u = Math.max(0, 1 - (st.toT0 - t) / DOT_LEAD_S);
        op = u > 0 ? Math.min(1, u * 4) : 0;
      } else if (fromOn && st.u === 0) {
        a = b = pos(st.from); // resting on its word
        landed = st.landed;
      } else {
        // Heading for a word not on screen: off to the right from where it is.
        a = pos(st.from);
        b = a ? { x: Math.min(window.innerWidth + size * 2, a.x + far), y: a.y } : null;
        u = st.u;
        op = !fromOn && st.u === 0 ? 0 : Math.max(0, 1 - Math.max(0, u - 0.5) * 2); // nothing to leave from: hidden
      }
    } else if (st?.kind === "in") {
      b = pos(st.to);
      a = b ? { x: Math.max(-size * 2, b.x - far), y: b.y } : null;
      u = st.u;
      op = Math.min(1, u * 4);
    } else if (st?.kind === "out") {
      a = pos(st.from);
      b = a ? { x: Math.min(window.innerWidth + size * 2, a.x + far), y: a.y } : null;
      u = st.u;
      op = Math.max(0, 1 - Math.max(0, u - 0.5) * 2);
    }
    // Read by the evals (tools/karaoke/evals/): the ball's state this frame. Nothing reads it in use.
    (window as unknown as { __smBall?: object }).__smBall = { st, a, b, known: [...wordPos.current.keys()].slice(-6) };
    if (!st || !a || !b) {
      el.style.opacity = "0";
      return;
    }
    // A duet's ball is the colour of the part it is with (owner, 28 Nov: "blue singing has a blue bouncing ball, and
    // pink has a pink bouncing ball"): the word it rests on, or the one it is flying to.
    const at = st.kind === "hop" ? (u > 0 ? st.to : st.from) : st.kind === "in" ? st.to : st.from;
    const [li, wi] = at.split(":").map(Number);
    const ln = duet ? lines[li] : undefined;
    const hue = ln ? DUET_HUES[ln.voices?.[wi] ?? ln.voice ?? ""] : undefined;
    const fill = hue ? hue.sung : (dotStyle.background as string);
    if (el.dataset.fill !== fill) {
      el.dataset.fill = fill;
      el.style.background = fill;
      el.style.boxShadow = hue ? `0 0 0.15em #000, 0 0 0.35em ${hue.sung}` : (dotStyle.boxShadow as string);
    }
    const e = u * u * (3 - 2 * u); // across: eased in and out
    const x = a.x + (b.x - a.x) * e;
    // Longer hops go higher; on a row with a row above, no higher than the gap between them allows.
    let h = Math.max(size * 1.2, Math.min(size * 3.5, Math.hypot(b.x - a.x, b.y - a.y) * 0.3));
    if (a.under || b.under) h = Math.min(h, Math.max(0, (LINE_HEIGHT - 1) * (size / BALL_EM) - size - REST_GAP * size));
    // Resting just above the word's letters; a thrown ball's parabola between words.
    const y = a.y + (b.y - a.y) * e - size * (1 + REST_GAP) - 4 * h * u * (1 - u);
    const q = u === 0 && landed < SQUASH_S ? 1 - landed / SQUASH_S : 0; // a little squash as it lands
    const sx = 1 + 0.22 * q;
    const sy = 1 - 0.3 * q;
    el.style.opacity = String(op);
    el.style.transform = `translate(${(x - size / 2).toFixed(1)}px, ${(y + (size * (1 - sy)) / 2).toFixed(1)}px) scale(${sx.toFixed(3)}, ${sy.toFixed(3)})`;
  }
  useLayoutEffect(() => {
    outRef.current?.animate(
      [
        { transform: "none", opacity: 1, filter: "blur(0px)" },
        { transform: "translateY(-38%) scale(0.92)", opacity: 0, filter: "blur(5px)" }, // it evaporates
      ],
      { duration: LEAVE_MS, easing: EASE_IN_OUT, fill: "forwards" },
    );
  }, [outKey]);

  return (
    <div style={lyricsBoxStyle}>
      <div aria-hidden style={{ ...bandStyle, opacity: bigLine || small ? 1 : 0 }} />
      <div
        style={{
          position: "relative",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: "0.8vh",
          alignSelf: duet ? "stretch" : undefined,
        }}
      >
        {out && (
          <div
            ref={outRef}
            key={`out-${outKey}`}
            style={
              leaving.current?.rect
                ? {
                    ...leaveStyle,
                    left: leaving.current.rect.left,
                    top: leaving.current.rect.top,
                    width: leaving.current.rect.width,
                  }
                : floatStyle
            }
          >
            <Line
              text={out.text}
              words={out.words}
              t={Number.MAX_SAFE_INTEGER}
              progress={1}
              big
              highlight={highlight}
              voice={voiceOf(out)}
              voices={duet ? out.voices : undefined}
            />
          </div>
        )}
        <div aria-hidden style={{ ...backdropStyle, opacity: bigLine || small ? 1 : 0 }} />
        {bigLine ? (
          <div
            ref={bigRef}
            key={`big-${bigIdx}`}
            style={{
              position: "relative",
              transformOrigin: "50% 50%",
              willChange: "transform, opacity",
              ...sideStyle(voiceOf(bigLine)),
            }}
          >
            {countIn && untilNext <= COUNT_IN_S && <CountDots seconds={untilNext} />}
            {tag && <PartTag key={`tag-${bigIdx}`} {...tag} lift={countIn && untilNext <= COUNT_IN_S} />}
            <Line
              current={curOn}
              text={bigLine.text}
              words={bigLine.words}
              t={t}
              progress={curOn && cur ? (t - (cur.start ?? 0)) / Math.max(0.3, (cur.end ?? 0) - (cur.start ?? 0)) : 0}
              big
              highlight={highlight}
              onWord={ball ? onBigWord : undefined}
              voice={voiceOf(bigLine)}
              voices={duet ? bigLine.voices : undefined}
            />
          </div>
        ) : next ? (
          <CountIn seconds={untilNext} total={gap} />
        ) : (
          <div style={{ height: "1.3em", fontSize: lineFont() }} />
        )}
        {small && (
          <div
            ref={smallRef}
            key={`small-${smallIdx}`}
            style={{ willChange: "transform, opacity", ...sideStyle(voiceOf(small)) }}
          >
            <Line
              text={small.text}
              progress={0}
              big={false}
              onWord={ball ? onSmallWord : undefined}
              voice={voiceOf(small)}
              voices={duet ? small.voices : undefined}
            />
          </div>
        )}
        {ball && <div ref={ballRef} aria-hidden style={{ ...dotStyle, position: "fixed", fontSize: lineFont() }} />}
      </div>
    </div>
  );
}

/** One size for the sung line and one for the next (owner, 25 Nov: "pick one sensible size and stick to it"; the next
 *  "a little too large"): a long line wraps, balanced, rather than shrinking. By the screen's width, capped by its
 *  height for a tall window. */
function lineFont(big = true): string {
  return big ? `min(${BIG_VW}vw, ${BIG_VW * 1.78}vh)` : `min(${SMALL_VW}vw, ${SMALL_VW * 1.78}vh)`;
}

type WordTime = { t0: number; t1: number } | null;

/** How far through each token of the line the singing is (0..1): by its own word timing (tools/karaoke/align.py); a
 *  token without one (no letters) is done once the word before it is. */
function tokenProgress(words: WordTime[], t: number): number[] {
  let prevDone = true;
  return words.map((w) => {
    if (!w) return prevDone ? 1 : 0;
    const p = t <= w.t0 ? 0 : t >= w.t1 ? 1 : (t - w.t0) / Math.max(0.05, w.t1 - w.t0);
    prevDone = p >= 1;
    return p;
  });
}

/** No word timings: an even sweep across the line's characters, as each token's share of it. */
function sweepProgress(tokens: string[], progress: number): number[] {
  const total = tokens.reduce((a, tok) => a + tok.length, 0) || 1;
  const at = Math.max(0, Math.min(1, progress)) * total;
  let before = 0;
  return tokens.map((tok) => {
    const p = tok.length ? Math.max(0, Math.min(1, (at - before) / tok.length)) : 1;
    before += tok.length;
    return p;
  });
}

export type Highlight = "words" | "dot" | "both";
// The ball (owner, 25 Nov: "a bouncing dot"; "smoother / more graceful ... visual theatre"; "it should [live] on the
// screen but bounce to the right place on the next line as that line slides into place"). Like a real karaoke ball it
// rests on the word being sung, then makes one quick hop (HOP_MAX_S at most) to land exactly as the next word starts,
// on a gravity-like arc whose height follows the hop's length, and squashes a little as it lands. From a line's last
// word it hops to the next line's first word, following it as it slides into the big place. Only across a break of
// LONG_GAP_S or more does it hop off to the right after the last word and in from the left before the first.
const HOP_MAX_S = 0.32;
const HOP_ACROSS_S = 0.45; // to the next line's first word
const CUE_HOP_S = 0.28; // waiting on the next line's first word: a small hop in place as it starts
const HOP_SHARE = 0.85; // of a short gap between words: the hop takes most of it
const DOT_LEAD_S = 0.5; // its hop in from the left, ending on the first word after a break
const DOT_EXIT_S = 0.45; // its hop off to the right, after the last word before a break
const SQUASH_S = 0.14;
const BALL_EM = 0.3; // the ball's size, of the sung line's font (dotStyle's width and height)
const REST_GAP = 0.25; // between the resting ball and the letters, of the ball's size

type BallWord = { key: string; t0: number; t1: number; lineEnd: number };
type BallState =
  | { kind: "hop"; from: string; to: string; u: number; landed: number; toT0: number }
  | { kind: "in"; to: string; u: number }
  | { kind: "out"; from: string; u: number };

/** Every timed word of the song in order, keyed "line:word", for the ball; `lineEnd` its line's end (the lines then
 *  change: Lyrics). */
function ballWords(lines: LyricLine[]): BallWord[] {
  const out: BallWord[] = [];
  lines.forEach((ln, li) => {
    const toks = ln.text.split(/\s+/).filter(Boolean);
    if (!ln.words || ln.words.length !== toks.length) return;
    const lastT1 = Math.max(...ln.words.map((w) => (w ? w.t1 : -Infinity)));
    const lineEnd = ln.end ?? lastT1 + 0.5;
    ln.words.forEach((w, k) => w && out.push({ key: `${li}:${k}`, t0: w.t0, t1: w.t1, lineEnd }));
  });
  return out.sort((a, b) => a.t0 - b.t0);
}

const lineOf = (key: string) => key.split(":")[0];

/** Where the ball is at `t` (null: off screen). `u` 0..1 of a hop (0: resting on `from`). */
function ballAt(W: BallWord[], t: number): BallState | null {
  if (!W.length) return null;
  let j = -1;
  let lo = 0;
  let hi = W.length - 1;
  while (lo <= hi) {
    const m = (lo + hi) >> 1;
    if (W[m].t0 <= t) {
      j = m;
      lo = m + 1;
    } else hi = m - 1;
  }
  const next = W[j + 1];
  if (j < 0) return next.t0 - t <= DOT_LEAD_S ? { kind: "in", to: next.key, u: 1 - (next.t0 - t) / DOT_LEAD_S } : null;
  const a = W[j];
  const landed = t - a.t0;
  const rest = (k: string, since: number, t0: number): BallState => ({
    kind: "hop",
    from: k,
    to: k,
    u: 0,
    landed: since,
    toT0: t0,
  });
  const restOrOut = (): BallState | null => {
    if (t < a.t1) return rest(a.key, landed, a.t0);
    const u = (t - a.t1) / DOT_EXIT_S;
    return u < 1 ? { kind: "out", from: a.key, u } : null;
  };
  if (!next) return restOrOut();
  if (next.t0 - a.t1 >= LONG_GAP_S) {
    // A break: off to the right, then back in from the left just before the next word.
    if (next.t0 - t <= DOT_LEAD_S) return { kind: "in", to: next.key, u: 1 - (next.t0 - t) / DOT_LEAD_S };
    return restOrOut();
  }
  if (lineOf(next.key) === lineOf(a.key)) {
    const hop = Math.min(HOP_MAX_S, (next.t0 - a.t0) * HOP_SHARE);
    const start = next.t0 - hop;
    const u = t <= start ? 0 : Math.min(1, (t - start) / Math.max(0.05, hop));
    return { kind: "hop", from: a.key, to: next.key, u, landed, toT0: next.t0 };
  }
  // To the next line: the hop starts when the lines change (Lyrics), which is the old line's end or just in time for
  // the next line's first word, whichever comes first; so the ball goes with the new line as it slides into place,
  // never left behind where the old line was. Landed early, it waits on the first word and gives a small hop in
  // place as that word starts (owner, 25 Nov: the ball "left stranded on the right after the new line arrives").
  const natural = Math.min(HOP_ACROSS_S, (next.t0 - a.t0) * HOP_SHARE);
  const start = Math.max(a.t0, Math.min(next.t0 - natural, a.lineEnd));
  const dur = Math.max(0.05, Math.min(HOP_ACROSS_S, next.t0 - start));
  if (t <= start) return rest(a.key, landed, a.t0);
  if (t < start + dur) return { kind: "hop", from: a.key, to: next.key, u: (t - start) / dur, landed, toT0: next.t0 };
  const cue = Math.min(CUE_HOP_S, next.t0 - (start + dur));
  if (t < next.t0 - cue || cue < 0.12) return rest(next.key, t - (start + dur), next.t0);
  return { kind: "hop", from: next.key, to: next.key, u: (t - (next.t0 - cue)) / cue, landed: Infinity, toT0: next.t0 };
}

function Line({
  text,
  progress,
  big,
  words,
  t,
  current,
  highlight = "words",
  plain,
  onWord,
  voice,
  voices,
}: {
  text: string;
  progress: number;
  big: boolean;
  words?: WordTime[];
  t?: number;
  current?: boolean;
  highlight?: Highlight;
  /** Just the words (a line leaving): no highlight. */
  plain?: boolean;
  /** Each word's element, for the ball (Lyrics). */
  onWord?: (k: number, el: HTMLSpanElement | null) => void;
  /** A duet's part: its side and colours. */
  voice?: DuetPart;
  /** A line traded word by word: each word's part (the line stays centred). */
  voices?: DuetPart[];
}) {
  const font = lineFont(big);
  const sided = !voices && (voice === "A" || voice === "B");
  const box: CSSProperties = {
    position: "relative",
    fontSize: font,
    // The next line is an exact scaled copy of how it will look big (the same weight, spacing and relative width):
    // it wraps the same, so it can slide and grow into the big place with no re-wrap (and the ball's target with it).
    fontWeight: 800,
    lineHeight: LINE_HEIGHT, // room above a wrapped row for the ball (owner, 25 Nov)
    opacity: big ? 1 : SMALL_OPACITY,
    maxWidth: `${(((sided ? SIDED_VW : 90) * (big ? BIG_VW : SMALL_VW)) / BIG_VW).toFixed(2)}vw`,
    textAlign: sided ? (voice === "A" ? "left" : "right") : "center",
    fontStyle: voice === "backing" && !voices ? "italic" : undefined,
    whiteSpace: "normal",
    overflowWrap: "anywhere",
    ...BALANCE, // a wrapped line's rows evenly filled: no word left alone (owner, 25 Nov: "avoid orphaned words")
  };
  const tokens = text.split(/\s+/).filter(Boolean);
  const timed = !!(words && t != null && words.length === tokens.length);
  // Yellow as sung unless the ball alone was asked for (a line without word timings keeps its yellow sweep).
  const yellow = !plain && big && (!timed || highlight !== "dot");
  const prog = !yellow ? null : timed ? tokenProgress(words!, t!) : sweepProgress(tokens, progress);
  return (
    <div style={box} data-sm-karaoke-line={current ? "current" : undefined}>
      {tokens.map((tok, k) => {
        const hue = DUET_HUES[voices?.[k] ?? voice ?? ""];
        return (
          <span key={k}>
            <span
              ref={onWord ? (el) => onWord(k, el) : undefined}
              style={{ position: "relative", display: "inline-block" }}
            >
              <span style={hue ? { ...baseTextStyle, color: hue.base } : baseTextStyle}>{tok}</span>
              {prog && (
                <span
                  aria-hidden
                  style={{
                    ...baseTextStyle,
                    ...sungStyle,
                    ...(hue ? { color: hue.sung } : null),
                    clipPath: `inset(-50% ${100 - prog[k] * 100}% -50% 0)`,
                  }}
                >
                  {tok}
                </span>
              )}
            </span>
            {k < tokens.length - 1 ? " " : null}
          </span>
        );
      })}
    </div>
  );
}

/** A duet's colours: each voice its own hue, shared lines a third; sung is that hue at full brightness, still to sing
 *  the same hue held back, so the highlight never fights the part's colour. */
const DUET_HUES: Record<string, { base: string; sung: string }> = {
  A: { base: "hsl(200 42% 68%)", sung: "hsl(194 100% 64%)" },
  B: { base: "hsl(332 38% 72%)", sung: "hsl(332 100% 70%)" },
  both: { base: "hsl(272 34% 74%)", sung: "hsl(272 100% 76%)" },
  backing: { base: "rgba(255,255,255,0.6)", sung: "rgba(255,255,255,0.9)" },
};
const SIDED_VW = 66; // a voice's line, on its own side: at most this wide (of the 90 a centred line may take)
const SIDE_MARGIN = "6vw";
const TAG_S = 2.6; // a part's name shows this long after its line starts (from when the line is up)

/** A duet line's place: A on the left, B on the right, the rest centred. */
function sideStyle(v: DuetPart | undefined): CSSProperties {
  return v === "A"
    ? { alignSelf: "flex-start", marginLeft: SIDE_MARGIN }
    : v === "B"
      ? { alignSelf: "flex-end", marginRight: SIDE_MARGIN }
      : {};
}

/** The name over a duet's line where the part changes (two or more singers named), else null. */
function partTag(
  lines: LyricLine[],
  idx: number,
  singers: string[] | undefined,
  t: number,
): { text: string; voice: DuetPart; opacity: number } | null {
  const line = lines[idx];
  const v = line?.voice;
  if (!v || v === "backing" || !singers || singers.length < 2) return null;
  let before: DuetPart | undefined;
  for (let j = idx - 1; j >= 0 && !before; j--) before = lines[j].voice === "backing" ? undefined : lines[j].voice;
  if (before === v) return null;
  const left = (line.start ?? 0) + TAG_S - t;
  if (left <= 0) return null;
  const text = v === "A" ? singers[0] : v === "B" ? singers[1] : `${singers[0]} & ${singers[1]}`;
  return { text, voice: v, opacity: Math.min(1, left / 0.6) };
}

function PartTag({ text, voice, opacity, lift }: { text: string; voice: DuetPart; opacity: number; lift?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    ref.current?.animate([{ opacity: 0, transform: "translateY(30%)" }, { opacity: 1, transform: "none" }], {
      duration: 320,
      easing: EASE_OUT,
    });
  }, []);
  const at = voice === "A" ? { left: 0 } : voice === "B" ? { right: 0 } : { left: 0, right: 0, textAlign: "center" as const };
  return (
    <div
      ref={ref}
      aria-hidden
      style={{
        position: "absolute",
        ...at,
        bottom: lift ? "calc(100% + 1.4em)" : "100%",
        fontSize: lineFont(false),
        fontWeight: 700,
        letterSpacing: "0.04em",
        whiteSpace: "nowrap",
        opacity,
        ...baseTextStyle,
        color: DUET_HUES[voice].sung,
      }}
    >
      {text}
    </div>
  );
}

/** The count-in over the line about to start: 3, 2, 1 dots, just above it (out of the layout: nothing moves). */
function CountDots({ seconds }: { seconds: number }) {
  const n = Math.max(0, Math.min(COUNT_IN_S, Math.ceil(seconds)));
  return (
    <div
      aria-hidden
      style={{
        position: "absolute",
        left: 0,
        right: 0,
        bottom: "100%",
        textAlign: "center",
        fontSize: lineFont(false),
        fontWeight: 800,
        letterSpacing: "0.5em",
        lineHeight: 1.2,
        ...baseTextStyle,
      }}
    >
      {"●".repeat(n)}
    </div>
  );
}

/** A long break (LONG_GAP_S or more: an intro, a solo) counted down (owner, 25 Nov: "on longer music breaks we need to
 *  show some kind of countdown timer"): ♪, a bar draining across the break and the seconds left; the last COUNT_IN_S
 *  as dots. */
function CountIn({ seconds, total }: { seconds: number; total: number }) {
  const n = Math.max(0, Math.min(COUNT_IN_S, Math.ceil(seconds)));
  if (seconds <= COUNT_IN_S)
    return (
      <div style={{ fontSize: lineFont(), fontWeight: 800, lineHeight: 1.3, letterSpacing: "0.4em", ...baseTextStyle }}>
        {"●".repeat(n) || " "}
      </div>
    );
  const left = Math.max(0, Math.min(1, seconds / Math.max(1, total)));
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: "1.2vw",
        fontSize: lineFont(false),
        fontWeight: 700,
        lineHeight: 1.6,
        ...baseTextStyle,
      }}
    >
      <span>♪</span>
      <span
        style={{
          position: "relative",
          width: "28vw",
          height: "0.5vh",
          borderRadius: "0.5vh",
          background: "rgba(255,255,255,0.25)",
        }}
      >
        <span
          style={{
            position: "absolute",
            left: 0,
            top: 0,
            bottom: 0,
            width: `${(left * 100).toFixed(2)}%`,
            borderRadius: "0.5vh",
            background: "#ffd23f",
            boxShadow: "0 0 0.4vh rgba(0,0,0,0.6)",
          }}
        />
      </span>
      <span style={{ minWidth: "2.2em", textAlign: "left", fontVariantNumeric: "tabular-nums" }}>
        {Math.ceil(seconds)}
      </span>
    </div>
  );
}

function UpNext({
  title,
  artist,
  singer,
  photos = [],
}: {
  title: string;
  artist: string;
  singer?: string | null;
  photos?: string[];
}) {
  return (
    <div style={{ ...lyricsBoxStyle, bottom: "6vh" }}>
      <div
        style={{
          ...baseTextStyle,
          position: "relative",
          fontSize: "min(2.6vw, 40px)",
          opacity: 0.9,
        }}
      >
        {photos.length > 0 && (
          <span style={{ marginRight: "0.4em" }}>
            <Faces photos={photos} size="1.6em" />
          </span>
        )}
        {singer ? `Up next, ${singer}: ` : "Up next: "}
        <b>{title}</b>
        {artist ? ` — ${artist}` : ""}
      </div>
    </div>
  );
}

const layerStyle: CSSProperties = {
  position: "fixed",
  inset: 0,
  zIndex: 2147483000,
  pointerEvents: "none",
};
const lyricsBoxStyle: CSSProperties = {
  position: "absolute",
  left: 0,
  right: 0,
  bottom: "7vh",
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  gap: "0.6vh",
  fontFamily: "'Segoe UI', 'Helvetica Neue', Arial, sans-serif",
};
const baseTextStyle: CSSProperties = {
  color: "#fff",
  textShadow: "0 0 0.12em #000, 0 0 0.3em rgba(0,0,0,0.85), 0 0.05em 0.1em #000",
  WebkitTextStroke: "0.02em rgba(0,0,0,0.6)",
};
const introStyle: CSSProperties = {
  position: "absolute",
  left: "50%",
  top: "40%",
  display: "flex",
  alignItems: "center",
  gap: "3vw",
  maxWidth: "86vw",
  fontFamily: "'Segoe UI', 'Helvetica Neue', Arial, sans-serif",
};
const introArtStyle: CSSProperties = {
  width: "26vh",
  height: "26vh",
  flexShrink: 0,
  borderRadius: "1.5vh",
  objectFit: "cover",
  boxShadow: "0 1vh 4vh rgba(0,0,0,0.6)",
};
const cornerStyle: CSSProperties = {
  position: "absolute",
  left: "3vw",
  top: "4vh",
  display: "flex",
  alignItems: "center",
  gap: "1.5vw",
  maxWidth: "60vw",
  fontFamily: "'Segoe UI', 'Helvetica Neue', Arial, sans-serif",
};
/** A soft dark pool behind the lyrics (owner, 25 Nov: "20% dark blurry area behind them (no sharp edges) to improve
 *  legibility on complex dynamic rendered backgrounds"; 26 Nov: deeper, over every scene; "more spread out and a bit more
 *  pronounced"): darker in the middle, to nothing at the edges, sized to the
 *  lines it sits behind (a wrapped line, the next line). Darkens only: no backdrop blur over the live render. */
const backdropStyle: CSSProperties = {
  position: "absolute",
  zIndex: -1,
  left: "-22vw",
  right: "-22vw",
  top: "-16vh",
  bottom: "-12vh",
  background:
    "radial-gradient(closest-side, rgba(0,0,0,0.5), rgba(0,0,0,0.42) 40%, rgba(0,0,0,0.24) 68%, rgba(0,0,0,0.08) 86%, rgba(0,0,0,0))",
  pointerEvents: "none",
  transition: "opacity 0.6s ease",
};
/** The lyrics' part of the screen, a shade darker, over every scene (owner, 26 Nov: "more local contrast for the
 *  lyrics ... a black faded area for the lyric area of the screen -- not too conspicuous"; "it's a general problem"):
 *  from the bottom edge, fading to nothing above the lines; the pool (backdropStyle) darkens the words' own place more. */
const bandStyle: CSSProperties = {
  position: "absolute",
  zIndex: -1,
  left: 0,
  right: 0,
  bottom: "-7vh",
  height: "72vh",
  background:
    "linear-gradient(to top, rgba(0,0,0,0.5), rgba(0,0,0,0.44) 30%, rgba(0,0,0,0.26) 55%, rgba(0,0,0,0.1) 78%, rgba(0,0,0,0))",
  pointerEvents: "none",
  transition: "opacity 0.8s ease",
};
/** The leaving line: just above the big one, out of the layout. */
const leaveStyle: CSSProperties = {
  position: "fixed",
  pointerEvents: "none",
  display: "flex",
  justifyContent: "center",
};
const floatStyle: CSSProperties = {
  position: "absolute",
  left: 0,
  right: 0,
  bottom: "100%",
  display: "flex",
  justifyContent: "center",
  pointerEvents: "none",
};
const sungStyle: CSSProperties = {
  position: "absolute",
  left: 0,
  top: 0,
  color: "#ffd23f",
};
const dotStyle: CSSProperties = {
  position: "absolute",
  left: 0,
  top: 0,
  width: "0.3em",
  height: "0.3em",
  borderRadius: "50%",
  background: "#ffd23f",
  boxShadow: "0 0 0.15em #000, 0 0 0.35em rgba(255,210,63,0.6)",
  opacity: 0,
  pointerEvents: "none",
  willChange: "transform",
};
