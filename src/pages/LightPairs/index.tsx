// Blind pairs (docs/AD_EVAL.md §4): the same moment of a song lit two ways, side by side in the 3D room with the song
// playing; the owner picks the better side, or same. Which run is on which side is never sent here (show_service
// ad_pairs.py; tools/room/ad_eval/pairs.py makes the batches and reports how often each score agrees with the picks).
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, Equal, RotateCcw } from "lucide-react";
import { resolveRoomUrl } from "@/pages/Room/api";
import { karaoke } from "@/shared/api/karaoke";
import Room3DPage from "@/pages/Room3D";

type Pair = { n: number; song: string; title?: string | null; artist?: string | null; t0_s: number; dur_s: number; hop_s: number; guide?: number };
type Batch = { batch: string; pairs: Pair[]; picked: Record<string, string> };
type Clip = { left: Uint8Array[]; right: Uint8Array[] };
type Pick = "left" | "right" | "same";

const decode = (b64: string) => {
  const s = atob(b64), u = new Uint8Array(512);
  for (let i = 0; i < 512 && i < s.length; i++) u[i] = s.charCodeAt(i);
  return u;
};

export default function LightPairsPage() {
  const room = useMemo(() => resolveRoomUrl(window.location.search).replace(/\/+$/, ""), []);
  const api = `${room}/show/api/v1/eval/pairs`;
  const [batch, setBatch] = useState<Batch | null>(null);
  const [n, setN] = useState<number | null>(null);
  const [clip, setClip] = useState<Clip | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const [busy, setBusy] = useState(false);
  const inst = useRef<HTMLAudioElement | null>(null);
  const voc = useRef<HTMLAudioElement | null>(null);
  const pair = batch?.pairs.find((p) => p.n === n) ?? null;
  const pairRef = useRef<Pair | null>(null);
  pairRef.current = pair;
  const clipRef = useRef<Clip | null>(null);
  clipRef.current = clip;

  // the newest batch with a pair still to pick (or ?batch=)
  useEffect(() => {
    (async () => {
      try {
        const want = new URLSearchParams(window.location.search).get("batch");
        const all = await (await fetch(api, { cache: "no-store" })).json();
        const name = want || all?.next?.batch || all?.batches?.[0]?.batch;
        if (!name) { setErr("No pairs yet"); return; }
        const b: Batch = await (await fetch(`${api}/${encodeURIComponent(name)}`, { cache: "no-store" })).json();
        setBatch(b);
        const first = b.pairs.find((p) => !(String(p.n) in (b.picked ?? {})));
        setN(first ? first.n : null);
      } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    })();
  }, [api]);

  // the pair's frames and the song
  useEffect(() => {
    if (!batch || n == null || !pair) return;
    let dead = false;
    setClip(null); setPlaying(false);
    (async () => {
      try {
        const c = await (await fetch(`${api}/${encodeURIComponent(batch.batch)}/${n}`, { cache: "no-store" })).json();
        if (!dead) setClip({ left: (c.left as string[]).map(decode), right: (c.right as string[]).map(decode) });
      } catch (e) { if (!dead) setErr(e instanceof Error ? e.message : String(e)); }
    })();
    const a = new Audio(karaoke.audioUrl(pair.song, "instrumental")), v = new Audio(karaoke.audioUrl(pair.song, "vocals"));
    a.preload = v.preload = "auto";
    v.volume = Math.max(0, Math.min(1, pair.guide ?? 1));
    a.currentTime = v.currentTime = pair.t0_s;
    inst.current = a; voc.current = v;
    return () => { dead = true; a.pause(); v.pause(); a.src = ""; v.src = ""; };
  }, [api, batch, n, pair]);

  const play = useCallback(() => {
    const a = inst.current, v = voc.current, p = pairRef.current;
    if (!a || !v || !p) return;
    a.currentTime = v.currentTime = p.t0_s;
    void a.play(); void v.play();
    setPlaying(true);
  }, []);

  // stop at the clip's end
  useEffect(() => {
    if (!playing) return;
    const t = window.setInterval(() => {
      const a = inst.current, v = voc.current, p = pairRef.current;
      if (!a || !p) return;
      if (voc.current && Math.abs(v!.currentTime - a.currentTime) > 0.08) v!.currentTime = a.currentTime;
      if (a.currentTime >= p.t0_s + p.dur_s || a.ended) { a.pause(); v?.pause(); setPlaying(false); }
    }, 100);
    return () => window.clearInterval(t);
  }, [playing]);

  // each side's frame now: the song's clock picks it
  const frameAt = useCallback((side: "left" | "right") => () => {
    const c = clipRef.current, p = pairRef.current, a = inst.current;
    if (!c || !p) return null;
    const fs = c[side];
    if (!fs.length) return null;
    const t = a ? a.currentTime - p.t0_s : 0;
    return fs[Math.max(0, Math.min(fs.length - 1, Math.floor(t / p.hop_s)))];
  }, []);
  const left = useMemo(() => frameAt("left"), [frameAt]);
  const right = useMemo(() => frameAt("right"), [frameAt]);

  const choose = useCallback(async (pick: Pick) => {
    if (!batch || n == null || busy) return;
    setBusy(true);
    try {
      const r = await fetch(`${api}/${encodeURIComponent(batch.batch)}/${n}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ pick }) });
      if (!r.ok) throw new Error(`pick: ${r.status}`);
      const picked = { ...batch.picked, [String(n)]: pick };
      setBatch({ ...batch, picked });
      const next = batch.pairs.find((p) => !(String(p.n) in picked));
      setN(next ? next.n : null);
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }, [api, batch, n, busy]);

  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if (e.key === "ArrowLeft") void choose("left");
      else if (e.key === "ArrowRight") void choose("right");
      else if (e.key === "ArrowDown" || e.key === "=") void choose("same");
      else if (e.key === " ") { e.preventDefault(); play(); }
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [choose, play]);

  const done = batch ? Object.keys(batch.picked ?? {}).length : 0;
  const total = batch?.pairs.length ?? 0;
  const btn = "flex items-center gap-1.5 rounded-md border border-[#2c3244] bg-[#151926] px-3 py-1.5 text-sm text-[#d8dce8] hover:bg-[#1d2233] disabled:opacity-40";
  return (
    <div className="flex h-screen flex-col bg-[#07080c] text-[#d8dce8]">
      <header className="flex items-center gap-3 border-b border-[#252a38] px-3 py-2">
        <span className="text-sm tabular-nums text-[#8a90a2]">{batch ? `${Math.min(done + 1, total)} / ${total}` : ""}</span>
        <span className="min-w-0 truncate text-sm">{pair ? `${pair.title ?? pair.song}${pair.artist ? ` · ${pair.artist}` : ""}` : batch && n == null ? "All picked" : err ?? ""}</span>
        <div className="ml-auto flex items-center gap-2">
          <button className={btn} onClick={play} disabled={!pair || !clip} title="Space"><RotateCcw size={15} />{playing ? "Again" : "Play"}</button>
          <button className={btn} onClick={() => void choose("left")} disabled={!pair || busy} title="←"><ChevronLeft size={15} />Left</button>
          <button className={btn} onClick={() => void choose("same")} disabled={!pair || busy} title="↓"><Equal size={15} />Same</button>
          <button className={btn} onClick={() => void choose("right")} disabled={!pair || busy} title="→">Right<ChevronRight size={15} /></button>
        </div>
      </header>
      {pair && (
        <div className="grid min-h-0 flex-1 grid-cols-2 gap-1 p-1">
          <div className="relative min-h-0 overflow-hidden rounded" key={`l${n}`}><Room3DPage embedded shotView="front" frames={left} /></div>
          <div className="relative min-h-0 overflow-hidden rounded" key={`r${n}`}><Room3DPage embedded shotView="front" frames={right} /></div>
        </div>
      )}
    </div>
  );
}
