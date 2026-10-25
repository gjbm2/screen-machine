// From the web: look for a MIDI file on the web's MIDI libraries (every source at once; solo piano first where it's
// known), then "Add to Mine" or "Add and play". Each result is one line and says where it's from. A source that didn't answer says
// so in words ("BitMidi didn't answer") and the others' results still show.

import { useRef, useState } from "react";
import { Check, Globe, Play, Plus } from "lucide-react";
import { cn } from "@/lib/utils";
import { Notice, Spinner } from "@/pages/Room/bits";
import { TAP_BUTTON_CLASS } from "@/pages/Room/theme";
import { musicWords, type WebResult, type WebSearch } from "./api";
import { InfoTip, PIECE_LIST, playGuess, PRIMARY, SearchBox, SECONDARY } from "./bits";
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
        <SearchBox value={q} onChange={setQ} onSubmit={() => void search()} placeholder="Tune or composer" label="Look on the web" />
        <button type="button" onClick={() => void search()} className={cn(TAP_BUTTON_CLASS, PRIMARY, "inline-flex shrink-0 items-center gap-2 px-4 font-semibold")}>
          {loading ? <Spinner /> : <Globe className="h-4 w-4" aria-hidden />} Look
        </button>
        <InfoTip label="About Web">Finds MIDI files in libraries on the web. What you add goes into Mine, cleaned so it plays as piano.</InfoTip>
      </div>
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
            <ul className={PIECE_LIST} aria-label="Found on the web">
              {res.results.map((r) => {
                const a = added[r.ref];
                const b = busy[r.ref];
                const from = [r.source_label, r.detail, sizeWords(r.size)].filter(Boolean).join(" · ");
                return (
                  <li key={r.ref} className="px-3 py-1">
                    {/* one line: the title, where it's from, then Add and play (Play once in Mine) and Add to Mine */}
                    <div className="flex min-h-11 items-center gap-2">
                      <p className="flex min-w-0 flex-1 items-center gap-1.5" title={[r.title, from].filter(Boolean).join(" · ")}>
                        <span className="min-w-0 max-w-[70%] shrink-0 truncate text-base font-medium">{r.title}</span>
                        <span className="min-w-0 flex-1 truncate text-sm text-muted-foreground">{from}</span>
                      </p>
                      <button
                        type="button"
                        onClick={() => void add(r, true)}
                        aria-label={a ? `Play ${r.title}` : `Add and play ${r.title}`}
                        className={cn("inline-flex h-10 shrink-0 items-center gap-1.5 rounded-full px-3 text-[15px] font-semibold", PRIMARY)}
                      >
                        {b === "play" ? <Spinner /> : <Play className="h-4 w-4" aria-hidden />}
                        <span className="max-sm:sr-only">{a ? "Play" : "Add and play"}</span>
                      </button>
                      {a ? (
                        <span className="grid h-10 w-10 shrink-0 place-items-center text-emerald-700" title="In Mine" aria-label="In Mine">
                          <Check className="h-5 w-5" aria-hidden />
                        </span>
                      ) : (
                        <button
                          type="button"
                          onClick={() => void add(r, false)}
                          aria-label={`Add ${r.title} to Mine`}
                          title="Add to Mine"
                          className={cn("inline-flex h-10 shrink-0 items-center gap-1.5 rounded-full px-3 text-[15px]", SECONDARY)}
                        >
                          {b === "add" ? <Spinner /> : <Plus className="h-4 w-4" aria-hidden />}
                          <span className="max-sm:sr-only">Add to Mine</span>
                        </button>
                      )}
                    </div>
                    {a?.words && <p className="pb-1 text-sm text-emerald-800">In Mine. {a.words}</p>}
                    {rowErr[r.ref] && <p className="pb-1 text-sm text-rose-700">{rowErr[r.ref]}</p>}
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
