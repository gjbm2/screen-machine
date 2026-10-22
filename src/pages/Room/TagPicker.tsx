// Pick scene tags for a mode: every tag word in the scene library, grouped by facet, searchable, each with how many
// scenes carry it, and a live line saying how many scenes the choice leaves the Art Director. Nothing here knows any
// particular tag: the words come from the library (sceneTags.ts), so a new tag on a scene shows up by itself. Folded
// to one summary line ("Only piano scenes · 8 · Change") until tapped.

import { useMemo, useState } from "react";
import { ChevronDown, Search, X } from "lucide-react";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { scenesMatching, tagCount, type SceneTagIndex } from "./sceneTags";

/** Words shown per facet before "more"; a search shows every match. */
const PER_FACET = 8;

function friendly(s: string): string {
  const t = s.replace(/[-_]+/g, " ").trim();
  return t.charAt(0).toUpperCase() + t.slice(1);
}

/** A chosen tag's word, without the facet an exact tag ("modes:piano") names. */
function plainWord(tag: string): string {
  const i = tag.indexOf(":");
  return i > 0 ? tag.slice(i + 1) : tag;
}

/** A chosen tag in words: "piano", or "piano (modes only)" for an exact "modes:piano". */
function tagWords(tag: string): string {
  const i = tag.indexOf(":");
  return i > 0 ? `${tag.slice(i + 1)} (${friendly(tag.slice(0, i)).toLowerCase()} only)` : tag;
}

/** "a", "a or b", "a, b or c". */
function orList(ws: string[]): string {
  return ws.length <= 1 ? (ws[0] ?? "") : `${ws.slice(0, -1).join(", ")} or ${ws[ws.length - 1]}`;
}

export function TagPicker({
  index,
  value,
  onChange,
  title = "Only use these scenes",
  help = "The Art Director only picks scenes with any of these tags.",
}: {
  index: SceneTagIndex;
  value: string[];
  onChange: (tags: string[]) => void;
  /** The folded heading and the line under it when open: the field's x-ui label and help, where it has them. */
  title?: string;
  help?: string;
}) {
  const [unfolded, setUnfolded] = useState(false);
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const total = index.scenes.length;
  const matching = scenesMatching(index, value);
  const query = q.trim().toLowerCase();

  // Facets with their words, most-used first; the facet with the fewest words (the hand-made sets) leads.
  const groups = useMemo(() => {
    return Object.entries(index.facets)
      .map(([facet, words]) => ({
        facet,
        words: Object.entries(words)
          .filter(([w]) => !query || w.toLowerCase().includes(query) || facet.toLowerCase().includes(query))
          .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])),
        size: Object.keys(words).length,
      }))
      .filter((g) => g.words.length)
      .sort((a, b) => a.size - b.size || a.facet.localeCompare(b.facet));
  }, [index, query]);

  // A word is chosen as itself or as its exact form under this facet ("ambient", "modes:ambient"); tapping it again
  // takes either out.
  const chosen = (facet: string, w: string) => value.includes(w) || value.includes(`${facet}:${w}`);
  const toggle = (facet: string, w: string) =>
    onChange(chosen(facet, w) ? value.filter((x) => x !== w && x !== `${facet}:${w}`) : [...value, w]);
  const remove = (t: string) => onChange(value.filter((x) => x !== t));

  const summary = value.length ? `Only ${orList([...new Set(value.map(plainWord))])} scenes` : "Any scene";

  return (
    <div className="rounded-xl border border-stone-200">
      <button
        type="button"
        aria-expanded={unfolded}
        onClick={() => setUnfolded((u) => !u)}
        className="flex min-h-11 w-full items-center gap-2 px-3 py-2 text-left"
      >
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-semibold text-stone-800">{title}</span>
          <span className={cn("block text-sm", value.length && !matching ? "font-semibold text-rose-700" : "text-muted-foreground")}>
            {summary} · {value.length && !matching ? "no scene matches" : matching} ·{" "}
            <span className="text-teal-800 underline underline-offset-2">{unfolded ? "Done" : "Change"}</span>
          </span>
        </span>
        <ChevronDown aria-hidden className={cn("h-4 w-4 shrink-0 text-muted-foreground transition-transform", unfolded && "rotate-180")} />
      </button>
      {unfolded && (
        <div className="space-y-2 border-t border-stone-100 p-3">
          <p className="text-sm text-muted-foreground">{help}</p>
          {value.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {value.map((t) => (
                <span
                  key={t}
                  className="inline-flex min-h-9 items-center gap-1 rounded-full border border-teal-300 bg-teal-50 pl-3 pr-1 text-[15px] font-semibold text-teal-900"
                >
                  {tagWords(t)}
                  <span className="text-sm font-normal opacity-75">· {tagCount(index, t)}</span>
                  <button
                    type="button"
                    aria-label={`Take out ${plainWord(t)}`}
                    onClick={() => remove(t)}
                    className="grid h-7 w-7 place-items-center rounded-full hover:bg-white/70"
                  >
                    <X className="h-3.5 w-3.5" aria-hidden />
                  </button>
                </span>
              ))}
            </div>
          )}
          <p className={cn("text-sm", value.length && !matching ? "font-semibold text-rose-700" : "text-muted-foreground")}>
            {value.length
              ? `${matching} of ${total} scenes match${value.length > 1 ? " (any of these tags)" : ""}.`
              : `No tags: all ${total} scenes.`}
          </p>
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-stone-400" aria-hidden />
            <Input className="h-11 pl-9 text-base" placeholder="Find a tag" value={q} onChange={(e) => setQ(e.currentTarget.value)} />
          </div>
          <div className="space-y-3">
            {groups.map((g) => {
              const all = !!query || open[g.facet];
              const shown = all ? g.words : g.words.slice(0, PER_FACET);
              return (
                <div key={g.facet}>
                  <p className="mb-1 text-sm font-medium text-stone-700">{friendly(g.facet)}</p>
                  <div className="flex flex-wrap gap-1.5">
                    {shown.map(([w, n]) => {
                      const on = chosen(g.facet, w);
                      return (
                        <button
                          key={w}
                          type="button"
                          aria-pressed={on}
                          onClick={() => toggle(g.facet, w)}
                          className={cn(
                            "min-h-9 rounded-full border px-3 text-[15px] transition-colors",
                            on ? "border-teal-300 bg-teal-50 font-semibold text-teal-900" : "border-stone-200 bg-white hover:bg-stone-50",
                          )}
                        >
                          {w} <span className="text-sm font-normal text-muted-foreground">{n}</span>
                        </button>
                      );
                    })}
                    {!all && g.words.length > PER_FACET && (
                      <button
                        type="button"
                        onClick={() => setOpen((o) => ({ ...o, [g.facet]: true }))}
                        className="min-h-9 px-2 text-sm text-stone-600 underline underline-offset-2 hover:text-stone-900"
                      >
                        {g.words.length - PER_FACET} more
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
            {!groups.length && <p className="text-sm text-muted-foreground">No tag matches “{q}”.</p>}
          </div>
        </div>
      )}
    </div>
  );
}
