// A friendly thumbnail picker: a sheet from the bottom on a phone, a centred dialog on a desktop. Used for scenes and
// for effects.
//
// Filtering (owner, 18 Oct: the one clipped row of chips hid most tags, piano among them): the tags come in groups
// ("Made for", "Kind", "Mood", "Music", …), every group's tags wrapping onto as many lines as they need, each with how
// many tiles it leaves. Tags in one group widen (piano or jazz), groups narrow (piano and calm). On a desktop the
// groups sit in a column beside the grid; on a phone they open under "Filters". What's chosen shows as chips with an
// ×, beside the count and Clear. Search matches names and tags, and offers the matching tags (with their group) as
// you type. The grid grows as it scrolls (a library of hundreds renders a screenful at a time).

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { Check, ChevronDown, Search, SlidersHorizontal, X } from "lucide-react";
import { DialogOverlay, DialogPortal } from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import { THEME_CLASS } from "./theme";
import { ThumbRoot } from "./liveLib";
import { Thumb } from "./Thumb";

const PAGE = 36;
const GROUP_SHOWN = 10;                       // a group's tags shown before "+ n more"

export interface PickItem {
  key: string;
  label: string;
  /** A second line (e.g. what kind of effect it is). */
  sub?: string;
  thumb: string | null;
  /** Chip values this item carries (family, moods, stage…): what plain `chips` filter on. */
  tags: string[];
  /** Per filter group (`facets`), the values this item carries. */
  facets?: Record<string, string[]>;
  /** Lower-case text the search box matches. */
  search: string;
}

export interface PickChip {
  value: string;
  label: string;
}

/** A group of filter tags ("Made for": Piano, Ambient). */
export interface PickFacet {
  key: string;
  label: string;
  values: PickChip[];
}

type Sel = Record<string, string[]>;

const PLAIN_GROUP = "_";

function valuesOf(it: PickItem, group: string): string[] {
  return group === PLAIN_GROUP ? it.tags : it.facets?.[group] ?? [];
}

function passes(it: PickItem, sel: Sel, skip?: string): boolean {
  for (const [g, vs] of Object.entries(sel)) {
    if (g === skip || !vs.length) continue;
    const have = valuesOf(it, g);
    if (!vs.some((v) => have.includes(v))) return false;
  }
  return true;
}

export function Picker({
  open,
  onOpenChange,
  title,
  description,
  items,
  chips,
  facets,
  selected,
  onPick,
  disabledKey,
  footer,
  notice,
  testId,
  lead,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: ReactNode;
  description: ReactNode;
  items: PickItem[];
  /** Plain filter chips (one group), when there are no `facets`. */
  chips: PickChip[];
  /** Filter groups (scenes: Made for, Kind, Mood, Music…); they replace `chips`. */
  facets?: PickFacet[];
  /** Tiles shown as chosen ("Now" / "On"). */
  selected: ReadonlySet<string>;
  onPick: (item: PickItem) => void;
  /** Disables tiles that aren't selected (e.g. the effects bus is full). */
  disabledKey?: (item: PickItem) => boolean;
  footer?: ReactNode;
  notice?: ReactNode;
  testId?: string;
  /** Kept for callers: tag words to offer while typing (the groups' tags are offered anyway). */
  tags?: (PickChip & { count: number })[];
  /** A first tile that isn't an item (e.g. "No effects"); shown while nothing is filtered. */
  lead?: { label: string; sub?: string; icon: ReactNode; onPick: () => void; testId?: string };
}) {
  const [q, setQ] = useState("");
  const [sel, setSel] = useState<Sel>({});
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [limit, setLimit] = useState(PAGE);
  const [scroller, setScroller] = useState<HTMLDivElement | null>(null);
  const sentinel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) {
      setQ("");
      setSel({});
      setFiltersOpen(false);
    }
  }, [open]);

  // The groups: the caller's, or its plain chips as one group without a heading.
  const groups: PickFacet[] = useMemo(
    () => (facets?.length ? facets : chips.length ? [{ key: PLAIN_GROUP, label: "", values: chips }] : []),
    [facets, chips],
  );
  const labelOf = (g: string, v: string) => groups.find((x) => x.key === g)?.values.find((c) => c.value === v)?.label ?? v;
  const groupLabel = (g: string) => groups.find((x) => x.key === g)?.label ?? "";

  const words = useMemo(() => q.trim().toLowerCase().split(/\s+/).filter(Boolean), [q]);

  const filtered = useMemo(
    () => items.filter((it) => passes(it, sel) && words.every((w) => it.search.includes(w))),
    [items, sel, words],
  );

  // Each tag's count: the tiles it would leave, given the other groups' choices and the search.
  const counts = useMemo(() => {
    const out: Record<string, Map<string, number>> = {};
    for (const g of groups) {
      const m = new Map<string, number>();
      for (const it of items) {
        if (!passes(it, sel, g.key) || !words.every((w) => it.search.includes(w))) continue;
        for (const v of valuesOf(it, g.key)) m.set(v, (m.get(v) ?? 0) + 1);
      }
      out[g.key] = m;
    }
    return out;
  }, [groups, items, sel, words]);

  // Typing offers the matching tags, with their group ("Piano · Made for · 10").
  const suggested = useMemo(() => {
    const word = q.trim().toLowerCase();
    if (!word) return [];
    const out: Array<{ group: string; value: string; label: string; n: number }> = [];
    for (const g of groups) {
      for (const c of g.values) {
        if ((sel[g.key] ?? []).includes(c.value)) continue;
        if (c.label.toLowerCase().includes(word) || c.value.toLowerCase().includes(word)) {
          out.push({ group: g.key, value: c.value, label: c.label, n: counts[g.key]?.get(c.value) ?? 0 });
        }
      }
    }
    return out.sort((a, b) => b.n - a.n).slice(0, 8);
  }, [q, groups, sel, counts]);

  const toggle = (g: string, v: string) =>
    setSel((s) => {
      const cur = s[g] ?? [];
      return { ...s, [g]: cur.includes(v) ? cur.filter((x) => x !== v) : [...cur, v] };
    });
  const active = Object.entries(sel).flatMap(([g, vs]) => vs.map((v) => ({ g, v })));

  useEffect(() => {
    setLimit(PAGE);
    scroller?.scrollTo({ top: 0 });
  }, [q, sel, scroller]);

  // Grow the grid as its end comes into view.
  useEffect(() => {
    const el = sentinel.current;
    if (!el || !scroller || typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver(
      (es) => {
        if (es.some((e) => e.isIntersecting)) setLimit((n) => n + PAGE);
      },
      { root: scroller, rootMargin: "400px 0px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [scroller, filtered.length, limit]);

  const shown = filtered.slice(0, limit);
  const leadShown = !!lead && !q.trim() && active.length === 0;
  const hasGroups = groups.length > 0;

  const groupsPanel = (
    <div className="space-y-4" role="group" aria-label="Filter">
      {groups.map((g) => (
        <FacetGroup
          key={g.key}
          facet={g}
          counts={counts[g.key]}
          chosen={sel[g.key] ?? []}
          onToggle={(v) => toggle(g.key, v)}
          testId={testId ? `${testId}-group-${g.key}` : undefined}
        />
      ))}
    </div>
  );

  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPortal>
        <DialogOverlay className="bg-stone-900/30 motion-reduce:!animate-none" />
        <DialogPrimitive.Content
          data-testid={testId}
          className={cn(
            THEME_CLASS,
            "fixed inset-x-0 bottom-0 z-50 flex h-[90dvh] flex-col rounded-t-2xl bg-background shadow-xl outline-none",
            "sm:inset-x-auto sm:bottom-auto sm:left-1/2 sm:top-[4vh] sm:h-[90vh] sm:w-[min(72rem,calc(100vw-2rem))] sm:-translate-x-1/2 sm:rounded-2xl",
            "data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=open]:fade-in-0 data-[state=closed]:fade-out-0",
            "data-[state=open]:slide-in-from-bottom-8 sm:data-[state=open]:slide-in-from-bottom-2 motion-reduce:!animate-none",
          )}
        >
          {/* Title, search, and what's chosen */}
          <div className="border-b border-stone-200/80 px-4 pb-3 pt-3 sm:px-5 sm:pt-4">
            <div className="mx-auto mb-2 h-1.5 w-10 rounded-full bg-stone-300 sm:hidden" aria-hidden />
            <div className="flex items-start gap-2">
              <div className="min-w-0 flex-1">
                <DialogPrimitive.Title className="text-xl font-semibold">{title}</DialogPrimitive.Title>
                <DialogPrimitive.Description className="text-[15px] text-muted-foreground">{description}</DialogPrimitive.Description>
              </div>
              <DialogPrimitive.Close
                className="-mr-1 grid h-11 w-11 shrink-0 place-items-center rounded-full text-muted-foreground hover:bg-stone-100 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                aria-label="Close"
              >
                <X className="h-5 w-5" />
              </DialogPrimitive.Close>
            </div>
            <div className="mt-2 flex gap-2">
              <label className="relative block min-w-0 flex-1">
                <span className="sr-only">Search</span>
                <Search aria-hidden className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <input
                  type="search"
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                  placeholder="Search names and tags"
                  data-testid={testId ? `${testId}-search` : undefined}
                  className="h-11 w-full rounded-xl border border-input bg-white pl-9 pr-3 text-base outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring"
                />
              </label>
              {hasGroups && (
                <button
                  type="button"
                  onClick={() => setFiltersOpen((o) => !o)}
                  aria-expanded={filtersOpen}
                  data-testid={testId ? `${testId}-filters` : undefined}
                  className={cn(
                    "inline-flex h-11 shrink-0 items-center gap-1.5 rounded-xl border px-3 text-base md:hidden",
                    filtersOpen || active.length ? "border-teal-700 bg-teal-50 text-teal-900" : "border-stone-300 bg-white",
                  )}
                >
                  <SlidersHorizontal className="h-4 w-4" aria-hidden />
                  Filters{active.length ? ` · ${active.length}` : ""}
                </button>
              )}
            </div>
            {suggested.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1.5" role="group" aria-label="Matching tags">
                {suggested.map((t) => (
                  <SmallChip
                    key={`${t.group}:${t.value}`}
                    active={false}
                    onClick={() => {
                      toggle(t.group, t.value);
                      setQ("");
                    }}
                  >
                    {t.label}
                    {groupLabel(t.group) && <span className="text-muted-foreground"> · {groupLabel(t.group)}</span>}
                    <span className="text-muted-foreground"> · {t.n}</span>
                  </SmallChip>
                ))}
              </div>
            )}
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              {active.map(({ g, v }) => (
                <SmallChip key={`${g}:${v}`} active onClick={() => toggle(g, v)} aria-label={`Remove ${labelOf(g, v)}`}>
                  {groupLabel(g) && <span className="opacity-80">{groupLabel(g)}: </span>}
                  {labelOf(g, v)} <X className="h-3.5 w-3.5" aria-hidden />
                </SmallChip>
              ))}
              <span className="px-1 text-[15px] text-muted-foreground" data-testid={testId ? `${testId}-count` : undefined}>
                {filtered.length === items.length ? `${items.length} in all` : `${filtered.length} of ${items.length}`}
              </span>
              {(active.length > 0 || q) && (
                <button
                  type="button"
                  onClick={() => {
                    setSel({});
                    setQ("");
                  }}
                  className="h-9 rounded-full px-2 text-[15px] font-medium text-teal-800 underline underline-offset-2 hover:text-teal-950"
                >
                  Clear
                </button>
              )}
            </div>
            {notice}
          </div>

          <div className="flex min-h-0 flex-1 flex-col md:flex-row">
            {/* The groups: a column beside the grid on a desktop; under "Filters" on a phone */}
            {hasGroups && (
              <aside
                className={cn(
                  "shrink-0 overflow-y-auto overscroll-contain border-stone-200/80 px-4 py-3 sm:px-5",
                  "max-h-[45%] border-b md:block md:max-h-none md:w-72 md:border-b-0 md:border-r lg:w-80",
                  filtersOpen ? "block" : "hidden",
                )}
                data-testid={testId ? `${testId}-groups` : undefined}
              >
                {groupsPanel}
              </aside>
            )}

            <div ref={setScroller} className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 pb-4 pt-3 sm:px-5">
              <ThumbRoot.Provider value={scroller}>
                {shown.length === 0 && !leadShown ? (
                  <p className="py-10 text-center text-base text-muted-foreground">Nothing matches.</p>
                ) : (
                  <ul className={cn("grid grid-cols-2 gap-3 sm:grid-cols-3", hasGroups ? "lg:grid-cols-3 xl:grid-cols-4" : "lg:grid-cols-4")}>
                    {lead && leadShown && (
                      <li>
                        <button
                          type="button"
                          data-testid={lead.testId}
                          onClick={lead.onPick}
                          className="group w-full rounded-xl p-1 text-left hover:bg-stone-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                        >
                          <span className="grid aspect-video w-full place-items-center rounded-lg border border-dashed border-stone-300 bg-stone-50 text-stone-500">
                            {lead.icon}
                          </span>
                          <span className="mt-1.5 block px-1 pb-1">
                            <span className="line-clamp-2 text-base font-medium leading-snug">{lead.label}</span>
                            {lead.sub && <span className="block truncate text-[15px] leading-snug text-muted-foreground">{lead.sub}</span>}
                          </span>
                        </button>
                      </li>
                    )}
                    {shown.map((it) => {
                      const on = selected.has(it.key);
                      const off = !on && !!disabledKey?.(it);
                      return (
                        <li key={it.key}>
                          <button
                            type="button"
                            data-pick={it.key}
                            disabled={off}
                            onClick={() => onPick(it)}
                            className={cn(
                              "group w-full rounded-xl p-1 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-40",
                              on ? "bg-teal-50 ring-2 ring-teal-600" : "hover:bg-stone-100",
                            )}
                          >
                            <span className="relative block">
                              <Thumb src={it.thumb} className="aspect-video w-full rounded-lg" />
                              {on && (
                                <span className="absolute right-1.5 top-1.5 grid h-6 w-6 place-items-center rounded-full bg-teal-700 text-white">
                                  <Check className="h-4 w-4" aria-hidden />
                                </span>
                              )}
                            </span>
                            <span className="mt-1.5 block px-1 pb-1">
                              <span className="line-clamp-2 text-base font-medium leading-snug">{it.label}</span>
                              {it.sub && <span className="block truncate text-[15px] leading-snug text-muted-foreground">{it.sub}</span>}
                            </span>
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                )}
                {shown.length < filtered.length && <div ref={sentinel} className="h-10" aria-hidden />}
              </ThumbRoot.Provider>
            </div>
          </div>

          {footer && <div className="border-t border-stone-200/80 px-4 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] sm:px-5">{footer}</div>}
        </DialogPrimitive.Content>
      </DialogPortal>
    </DialogPrimitive.Root>
  );
}

/** One group's tags, wrapping, most used first; the rest behind "+ n more". */
function FacetGroup({
  facet,
  counts,
  chosen,
  onToggle,
  testId,
}: {
  facet: PickFacet;
  counts: Map<string, number> | undefined;
  chosen: string[];
  onToggle: (value: string) => void;
  testId?: string;
}) {
  const [all, setAll] = useState(false);
  const n = (v: string) => counts?.get(v) ?? 0;
  const sorted = useMemo(
    () => [...facet.values].sort((a, b) => n(b.value) - n(a.value) || a.label.localeCompare(b.label)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [facet.values, counts],
  );
  // What's chosen always shows, even past the fold.
  const visible = all ? sorted : sorted.filter((c, i) => i < GROUP_SHOWN || chosen.includes(c.value));
  const hidden = sorted.length - visible.length;
  return (
    <section data-testid={testId}>
      {facet.label && <h3 className="mb-1.5 text-sm font-semibold text-muted-foreground">{facet.label}</h3>}
      <div className="flex flex-wrap gap-1.5">
        {visible.map((c) => (
          <SmallChip key={c.value} active={chosen.includes(c.value)} dim={n(c.value) === 0} onClick={() => onToggle(c.value)}>
            {c.label}
            <span className={chosen.includes(c.value) ? "opacity-80" : "text-muted-foreground"}> {n(c.value)}</span>
          </SmallChip>
        ))}
        {(hidden > 0 || all) && sorted.length > GROUP_SHOWN && (
          <button
            type="button"
            onClick={() => setAll((a) => !a)}
            aria-expanded={all}
            className="inline-flex h-9 items-center gap-1 rounded-full px-2 text-[15px] font-medium text-teal-800 hover:bg-stone-100"
          >
            {all ? "Fewer" : `+ ${hidden} more`}
            <ChevronDown aria-hidden className={cn("h-4 w-4 transition-transform motion-reduce:transition-none", all && "rotate-180")} />
          </button>
        )}
      </div>
    </section>
  );
}

function SmallChip({
  active,
  dim,
  onClick,
  children,
  ...rest
}: {
  active: boolean;
  dim?: boolean;
  onClick: () => void;
  children: ReactNode;
  "aria-label"?: string;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      aria-label={rest["aria-label"]}
      className={cn(
        "inline-flex h-9 shrink-0 items-center gap-1 whitespace-nowrap rounded-full border px-3 text-[15px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        active ? "border-teal-700 bg-teal-700 text-white" : "border-stone-300 bg-white text-foreground hover:bg-stone-50",
        dim && !active && "text-stone-400",
      )}
    >
      {children}
    </button>
  );
}

export function Chip({
  active,
  onClick,
  children,
  disabled,
  className,
  testId,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
  disabled?: boolean;
  className?: string;
  testId?: string;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      disabled={disabled}
      data-testid={testId}
      className={cn(
        "inline-flex h-11 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border px-4 text-base transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50",
        active ? "border-teal-700 bg-teal-700 text-white" : "border-stone-300 bg-white text-foreground hover:bg-stone-50",
        className,
      )}
    >
      {children}
    </button>
  );
}
