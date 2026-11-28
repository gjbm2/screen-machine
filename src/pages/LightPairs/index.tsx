// The owner's review (docs/AD_EVAL.md §4): a batch of moments played in the 3D room with the song. A blind pair (the
// same moment lit two ways, which run is on which side never sent here) takes left / right / same; a reel item (one
// transition of the show, tools/room/ad_eval/reel.py) takes good / wrong / skip. Either takes words, and either can
// widen to its section or the whole song. show_service ad_pairs.py serves the batches and keeps the verdicts.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Check, ChevronLeft, ChevronRight, Equal, Maximize2, RotateCcw, SkipForward, X } from "lucide-react";
import { resolveRoomUrl } from "@/pages/Room/api";
import { karaoke } from "@/shared/api/karaoke";
import Room3DPage from "@/pages/Room3D";

type Item = {
  n: number; song: string; title?: string | null; artist?: string | null; t0_s: number; dur_s: number; hop_s: number;
  guide?: number; sides?: number; kind?: string; at_s?: number; label?: string | null; flag?: string | null;
  section?: [number, number]; song_s?: number;
};
type Batch = { batch: string; kind?: string; pairs: Item[]; picked: Record<string, string> };
type Span = { t0: number; dur: number; hop: number; left: Uint8Array[]; right: Uint8Array[] };
type Pick = "left" | "right" | "same" | "good" | "wrong" | "skip";

const decode = (b64: string) => {
  const s = atob(b64), u = new Uint8Array(512);
  for (let i = 0; i < 512 && i < s.length; i++) u[i] = s.charCodeAt(i);
  return u;
};
const clock = (t: number) => `${Math.floor(Math.max(0, t) / 60)}:${String(Math.floor(Math.max(0, t) % 60)).padStart(2, "0")}`;

export default function LightPairsPage() {
  const room = useMemo(() => resolveRoomUrl(window.location.search).replace(/\/+$/, ""), []);
  const api = `${room}/show/api/v1/eval/pairs`;
  const [batch, setBatch] = useState<Batch | null>(null);
  const [n, setN] = useState<number | null>(null);
  const [span, setSpan] = useState<Span | null>(null);
  const [wide, setWide] = useState<"clip" | "section" | "song">("clip");
  const [err, setErr] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const [busy, setBusy] = useState(false);
  const [words, setWords] = useState("");
  const [pos, setPos] = useState(0);
  const inst = useRef<HTMLAudioElement | null>(null);
  const voc = useRef<HTMLAudioElement | null>(null);
  const started = useRef(false);                      // after the first play the browser lets the next ones start alone
  const item = batch?.pairs.find((p) => p.n === n) ?? null;
  const two = (item?.sides ?? 2) === 2;
  const spanRef = useRef<Span | null>(null);
  spanRef.current = span;

  // the newest batch with an item still to judge (or ?batch=)
  useEffect(() => {
    (async () => {
      try {
        const want = new URLSearchParams(window.location.search).get("batch");
        const all = await (await fetch(api, { cache: "no-store" })).json();
        const name = want || all?.next?.batch || all?.batches?.[0]?.batch;
        if (!name) { setErr("Nothing to review"); return; }
        const b: Batch = await (await fetch(`${api}/${encodeURIComponent(name)}`, { cache: "no-store" })).json();
        setBatch(b);
        const first = b.pairs.find((p) => !(String(p.n) in (b.picked ?? {})));
        setN(first ? first.n : null);
      } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    })();
  }, [api]);

  // the item's song, once per item
  useEffect(() => {
    if (!item) return;
    const a = new Audio(karaoke.audioUrl(item.song, "instrumental")), v = new Audio(karaoke.audioUrl(item.song, "vocals"));
    a.preload = v.preload = "auto";
    v.volume = Math.max(0, Math.min(1, item.guide ?? 1));
    a.currentTime = v.currentTime = item.t0_s;
    inst.current = a; voc.current = v;
    setWords(""); setWide("clip");
    return () => { a.pause(); v.pause(); a.src = ""; v.src = ""; };
  }, [item]);

  // the frames for the stretch shown: the clip, its section, or the whole song
  useEffect(() => {
    if (!batch || !item) return;
    let dead = false;
    setSpan(null); setPlaying(false);
    (async () => {
      try {
        const base = `${api}/${encodeURIComponent(batch.batch)}/${item.n}`;
        if (wide === "clip") {
          const c = await (await fetch(base, { cache: "no-store" })).json();
          if (dead) return;
          setSpan({ t0: item.t0_s, dur: item.dur_s, hop: item.hop_s, left: (c.left as string[]).map(decode),
                    right: ((c.right as string[] | undefined) ?? []).map(decode) });
        } else {
          const [a, b] = wide === "song" ? [0, item.song_s ?? 1e9] : (item.section ?? [item.t0_s - 15, item.t0_s + 25]);
          const c = await (await fetch(`${base}/frames?from=${a}&to=${b}`, { cache: "no-store" })).json();
          if (dead) return;
          const left = ((c.left as string[] | undefined) ?? []).map(decode);
          setSpan({ t0: c.t0_s ?? a, dur: left.length * (c.hop_s ?? item.hop_s), hop: c.hop_s ?? item.hop_s, left,
                    right: ((c.right as string[] | undefined) ?? []).map(decode) });
        }
      } catch (e) { if (!dead) setErr(e instanceof Error ? e.message : String(e)); }
    })();
    return () => { dead = true; };
  }, [api, batch, item, wide]);

  const play = useCallback(() => {
    const a = inst.current, v = voc.current, s = spanRef.current;
    if (!a || !v || !s) return;
    a.currentTime = v.currentTime = s.t0;
    void a.play().then(() => { started.current = true; }).catch(() => setPlaying(false));
    void v.play().catch(() => undefined);
    setPlaying(true);
  }, []);

  // each new stretch plays by itself once the owner has pressed play once
  useEffect(() => { if (span && started.current) play(); }, [span, play]);

  // stop at the stretch's end; the play head for the bar
  useEffect(() => {
    if (!playing) return;
    const t = window.setInterval(() => {
      const a = inst.current, v = voc.current, s = spanRef.current;
      if (!a || !s) return;
      if (v && Math.abs(v.currentTime - a.currentTime) > 0.08) v.currentTime = a.currentTime;
      setPos(a.currentTime);
      if (a.currentTime >= s.t0 + s.dur || a.ended) { a.pause(); v?.pause(); setPlaying(false); }
    }, 100);
    return () => window.clearInterval(t);
  }, [playing]);

  const frameAt = useCallback((side: "left" | "right") => () => {
    const s = spanRef.current, a = inst.current;
    if (!s) return null;
    const fs = s[side];
    if (!fs.length) return null;
    const t = a ? a.currentTime - s.t0 : 0;
    return fs[Math.max(0, Math.min(fs.length - 1, Math.floor(t / s.hop)))];
  }, []);
  const left = useMemo(() => frameAt("left"), [frameAt]);
  const right = useMemo(() => frameAt("right"), [frameAt]);

  const choose = useCallback(async (pick: Pick) => {
    if (!batch || n == null || busy) return;
    setBusy(true);
    try {
      const r = await fetch(`${api}/${encodeURIComponent(batch.batch)}/${n}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ pick, words }) });
      if (!r.ok) throw new Error(`verdict: ${r.status}`);
      const picked = { ...batch.picked, [String(n)]: pick };
      setBatch({ ...batch, picked });
      const next = batch.pairs.find((p) => p.n > n && !(String(p.n) in picked)) ?? batch.pairs.find((p) => !(String(p.n) in picked));
      setN(next ? next.n : null);
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }, [api, batch, n, busy, words]);

  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) {
        if (e.key === "Escape") (e.target as HTMLElement).blur();
        return;
      }
      if (e.key === " ") { e.preventDefault(); play(); return; }
      if (two) {
        if (e.key === "ArrowLeft") void choose("left");
        else if (e.key === "ArrowRight") void choose("right");
        else if (e.key === "ArrowDown" || e.key === "=") void choose("same");
      } else {
        if (e.key === "ArrowRight" || e.key === "g") void choose("good");
        else if (e.key === "ArrowLeft" || e.key === "w") void choose("wrong");
        else if (e.key === "ArrowDown" || e.key === "s") void choose("skip");
      }
      if (e.key === "e") setWide((w) => (w === "clip" ? "section" : w === "section" ? "song" : "clip"));
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [choose, play, two]);

  const done = batch ? Object.keys(batch.picked ?? {}).length : 0;
  const total = batch?.pairs.length ?? 0;
  const btn = "flex shrink-0 items-center gap-1.5 rounded-md border border-[#2c3244] bg-[#151926] px-2.5 py-1.5 text-sm text-[#d8dce8] hover:bg-[#1d2233] disabled:opacity-40";
  const on = "border-[#4b6bd6] bg-[#1d2a4d]";
  const lab = "hidden md:inline";
  const frac = span && span.dur > 0 ? Math.max(0, Math.min(1, (pos - span.t0) / span.dur)) : 0;
  const mark = span && item?.at_s != null && span.dur > 0 ? (item.at_s - span.t0) / span.dur : null;
  return (
    <div className="flex h-screen flex-col bg-[#07080c] text-[#d8dce8]">
      <header className="flex items-center gap-2 overflow-hidden border-b border-[#252a38] px-3 py-2">
        <span className="shrink-0 text-sm tabular-nums text-[#8a90a2]">{batch ? `${Math.min(done + 1, total)} / ${total}` : ""}</span>
        <div className="min-w-0 flex-1 truncate text-sm">
          {item ? (
            <>
              <span>{item.title ?? item.song}</span>
              {item.label && <span className="text-[#8a90a2]"> · {item.label}{item.at_s != null ? ` ${clock(item.at_s)}` : ""}</span>}
              {item.flag && <span className="text-[#d6a24b]"> · {item.flag}</span>}
            </>
          ) : batch && n == null ? "All done" : err ?? ""}
        </div>
        <button className={btn} onClick={play} disabled={!item || !span} title="Space"><RotateCcw size={15} /><span className={lab}>{playing ? "Again" : "Play"}</span></button>
        <div className="flex shrink-0 overflow-hidden rounded-md border border-[#2c3244]" title="e">
          {(["clip", "section", "song"] as const).map((w) => (
            <button key={w} className={`px-2 py-1.5 text-xs ${wide === w ? "bg-[#1d2a4d] text-white" : "bg-[#151926] text-[#8a90a2] hover:bg-[#1d2233]"}`}
                    onClick={() => setWide(w)} disabled={!item || (w === "section" && !item.section)}>
              {w === "clip" ? "Moment" : w === "section" ? "Section" : <Maximize2 size={13} />}
            </button>
          ))}
        </div>
        <input type="search" value={words} onChange={(e) => setWords(e.target.value)} placeholder="Words"
               className="w-28 min-w-0 rounded-md border border-[#2c3244] bg-[#0e1119] px-2 py-1.5 text-sm outline-none focus:border-[#4b6bd6] md:w-56" />
        {two ? (
          <>
            <button className={btn} onClick={() => void choose("left")} disabled={!item || busy} title="←"><ChevronLeft size={15} /><span className={lab}>Left</span></button>
            <button className={btn} onClick={() => void choose("same")} disabled={!item || busy} title="↓"><Equal size={15} /><span className={lab}>Same</span></button>
            <button className={btn} onClick={() => void choose("right")} disabled={!item || busy} title="→"><span className={lab}>Right</span><ChevronRight size={15} /></button>
          </>
        ) : (
          <>
            <button className={btn} onClick={() => void choose("wrong")} disabled={!item || busy} title="← or w"><X size={15} /><span className={lab}>Wrong</span></button>
            <button className={btn} onClick={() => void choose("skip")} disabled={!item || busy} title="↓ or s"><SkipForward size={15} /><span className={lab}>Skip</span></button>
            <button className={`${btn} ${on}`} onClick={() => void choose("good")} disabled={!item || busy} title="→ or g"><Check size={15} /><span className={lab}>Good</span></button>
          </>
        )}
      </header>
      <div className="relative h-1 bg-[#151926]">
        <div className="absolute inset-y-0 left-0 bg-[#4b6bd6]" style={{ width: `${frac * 100}%` }} />
        {mark != null && mark >= 0 && mark <= 1 && <div className="absolute inset-y-[-3px] w-0.5 bg-[#d6a24b]" style={{ left: `${mark * 100}%` }} />}
      </div>
      {item && (
        <div className={`grid min-h-0 flex-1 gap-1 p-1 ${two ? "grid-cols-2" : "grid-cols-1"}`}>
          <div className="relative min-h-0 overflow-hidden rounded" key={`l${n}`}><Room3DPage embedded shotView="front" frames={left} /></div>
          {two && <div className="relative min-h-0 overflow-hidden rounded" key={`r${n}`}><Room3DPage embedded shotView="front" frames={right} /></div>}
        </div>
      )}
    </div>
  );
}
