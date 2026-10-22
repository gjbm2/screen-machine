// From the web: look for a MIDI file on the web's MIDI libraries (every source at once; solo piano first where it's
// known), then "Add to Mine" or "Add and play". Each result says where it's from. A source that didn't answer says
// so in words ("BitMidi didn't answer") and the others' results still show.

import { useRef, useState } from "react";
import { Check, Globe, Play, Plus } from "lucide-react";
import { cn } from "@/lib/utils";
import { Notice, Spinner } from "@/pages/Room/bits";
import { TAP_BUTTON_CLASS } from "@/pages/Room/theme";
import { musicWords, type WebResult, type WebSearch } from "./api";
import { playGuess, PRIMARY, SearchBox, SECONDARY } from "./bits";
import { useMusic } from "./context";

/** 23456 -> "23 KB". */
const sizeWords = (n: number | null | undefined) =>
  typeof n === "number" && n > 0 ? (n < 1024 * 1024 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / (1024 * 1024)).toFixed(1)} MB`) : "";

type Added = { title: string; words: string | null; id: string };

export function FromWeb() {
  const { api, player, flash, bumpLibrary } = useMusic();
  const [q, setQ] = useState("");
  const [asked, setAsked] = useState("");
  const [res, setRes] = useState<WebSearch | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<Record<string, "add" | "play">>({});
  const [added, setAdded] = useState<Record<string, Added>>({});
  const [rowErr, setRowErr] = useState<Record<string, string>>({});
  const seq = useRef(0);

  const search = async () => {
    const text = q.trim();
    if (!text) return;
    const n = ++seq.current;
    setLoading(true);
    setError(null);
    setAsked(text);
    try {
      const r = await api.webSearch(text);
      if (n !== seq.current) return;
      setRes({ results: Array.isArray(r?.results) ? r.results : [], errors: Array.isArray(r?.errors) ? r.errors : [] });
    } catch (e) {
      if (n === seq.current) setError(musicWords(e));
    } finally {
      if (n === seq.current) setLoading(false);
    }
  };

  const add = async (r: WebResult, andPlay: boolean) => {
    if (busy[r.ref]) return;
    setBusy((b) => ({ ...b, [r.ref]: andPlay ? "play" : "add" }));
    setRowErr((x) => ({ ...x, [r.ref]: "" }));
    try {
      let got = added[r.ref];
      if (!got) {
        const out = await api.webImport({ ref: r.ref, title: r.title });
        got = { id: out.item.id, title: out.item.title || r.title, words: out.cleaned?.words ?? null };
        setAdded((a) => ({ ...a, [r.ref]: got }));
        bumpLibrary();
      }
      if (andPlay) {
        const p = await player.act({ action: "play", piece: got.id }, playGuess({ id: got.id, title: got.title }));
        flash(p.ok ? `Playing ${got.title}` : p.words);
      } else flash(`Added ${got.title} to Mine`);
    } catch (e) {
      setRowErr((x) => ({ ...x, [r.ref]: musicWords(e) }));
    } finally {
      setBusy((b) => {
        const { [r.ref]: _, ...rest } = b;
        return rest;
      });
    }
  };

  return (
    <div className="space-y-3" data-testid="music-web">
      <div className="flex items-center gap-2">
        <SearchBox value={q} onChange={setQ} onSubmit={() => void search()} placeholder="A tune, a composer, a show" label="Look on the web" />
        <button type="button" onClick={() => void search()} className={cn(TAP_BUTTON_CLASS, PRIMARY, "inline-flex h-12 shrink-0 items-center gap-2 px-4 font-semibold")}>
          {loading ? <Spinner /> : <Globe className="h-4 w-4" aria-hidden />} Look
        </button>
      </div>
      {!res && !loading && !error && (
        <p className="text-[15px] text-muted-foreground">
          Finds MIDI files in libraries on the web. What you add goes into Mine, cleaned so it plays as piano.
        </p>
      )}
      {loading && (
        <p className="flex items-center gap-2 text-base text-muted-foreground">
          <Spinner /> Looking on the web for “{asked}”…
        </p>
      )}
      {error && <p className="text-base text-amber-800">{error}</p>}
      {res && !loading && (
        <>
          {res.errors.length > 0 && (
            <Notice tone="muted" className="text-[15px]">
              {res.errors.map((x, i) => (
                <p key={`${x.source_label}-${i}`}>{x.words || `${x.source_label} didn't answer.`}</p>
              ))}
            </Notice>
          )}
          {res.results.length === 0 ? (
            <p className="text-base text-muted-foreground">Nothing found for “{asked}”. Try fewer words, or another spelling.</p>
          ) : (
            <ul className="space-y-2" aria-label="Found on the web">
              {res.results.map((r) => {
                const a = added[r.ref];
                const b = busy[r.ref];
                return (
                  <li key={r.ref} className="space-y-2 rounded-xl border border-stone-200 bg-white px-3 py-2.5">
                    <div className="min-w-0">
                      <p className="break-words text-base font-medium leading-snug">{r.title}</p>
                      <p className="text-sm text-muted-foreground">{[r.source_label, r.detail, sizeWords(r.size)].filter(Boolean).join(" · ")}</p>
                    </div>
                    {a && (
                      <p className="flex items-start gap-1.5 text-sm text-emerald-800">
                        <Check className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
                        <span>
                          In Mine.{a.words ? ` ${a.words}` : ""}
                        </span>
                      </p>
                    )}
                    <div className="flex flex-wrap gap-2">
                      <button type="button" onClick={() => void add(r, true)} className={cn("inline-flex h-11 items-center gap-2 rounded-xl px-4 text-[15px] font-semibold", PRIMARY)}>
                        {b === "play" ? <Spinner /> : <Play className="h-4 w-4" aria-hidden />} {a ? "Play" : "Add and play"}
                      </button>
                      {!a && (
                        <button type="button" onClick={() => void add(r, false)} className={cn("inline-flex h-11 items-center gap-2 rounded-xl px-4 text-[15px]", SECONDARY)}>
                          {b === "add" ? <Spinner /> : <Plus className="h-4 w-4" aria-hidden />} Add to Mine
                        </button>
                      )}
                    </div>
                    {rowErr[r.ref] && <p className="text-sm text-rose-700">{rowErr[r.ref]}</p>}
                  </li>
                );
              })}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
