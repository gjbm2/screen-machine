// /transitions: the screens' transitions for review (routes/transitions.py; owner, 24 Nov: "compile those into a
// review page so we can score and/or hone the logic"). Newest first, by day: the two pictures, the transition, how the
// writer briefed it (a link between the pictures, or a device), a 1-5 score and a note. At the top, how each kind
// of transition scores, and the ones that weren't made.
import { useEffect, useMemo, useState } from "react";
import { AppMenu } from "@/components/nav/AppMenu";
import { jobsUrl } from "@/components/nav/appNav";
import { PageHeader } from "@/components/nav/PageHeader";
import { usePublishDestinations } from "@/hooks/usePublishDestinations";
import { cn } from "@/lib/utils";
import { Notice, Spinner } from "@/pages/Room/bits";
import { CARD_CLASS, RoomThemeStyle, THEME_CLASS } from "@/pages/Room/theme";
import { getTransitions, scoreTransition, type Transition, type TransitionFailure } from "@/shared/api/transitions";

// the screens' own (made as a picture went up), the day's set's (made ahead by the cloud's schedule) and the-beast's
const SCREENS = ["north-screen", "south-screen", "cloud", "the-beast"];
const GBP_PER_USD = 1 / 1.3;

function kindOf(t: Transition): string {
  return t.connection === "device" ? t.device || "device" : "link";
}

function day(at?: number): string {
  return at ? new Date(at * 1000).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" }) : "—";
}

function time(at?: number): string {
  return at ? new Date(at * 1000).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }) : "";
}

function Scores({ items }: { items: Transition[] }) {
  const rows = useMemo(() => {
    const by = new Map<string, { n: number; scored: number; sum: number }>();
    for (const t of items) {
      const k = kindOf(t);
      const r = by.get(k) ?? { n: 0, scored: 0, sum: 0 };
      r.n += 1;
      if (t.score) {
        r.scored += 1;
        r.sum += t.score;
      }
      by.set(k, r);
    }
    return [...by.entries()].sort((a, b) => b[1].n - a[1].n);
  }, [items]);
  if (!rows.length) return null;
  return (
    <section className={cn(CARD_CLASS, "px-4 py-3")}>
      <h2 className="mb-2 text-[17px] font-semibold">How each kind scores</h2>
      <table className="w-full text-[15px]">
        <thead className="text-left text-sm text-muted-foreground">
          <tr>
            <th className="py-1 font-normal">Kind</th>
            <th className="py-1 text-right font-normal">Made</th>
            <th className="py-1 text-right font-normal">Scored</th>
            <th className="py-1 text-right font-normal">Average</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([k, r]) => (
            <tr key={k} className="border-t border-stone-200/70">
              <td className="py-1.5 capitalize">{k}</td>
              <td className="py-1.5 text-right tabular-nums">{r.n}</td>
              <td className="py-1.5 text-right tabular-nums">{r.scored}</td>
              <td className="py-1.5 text-right tabular-nums">{r.scored ? (r.sum / r.scored).toFixed(1) : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function Card({ t, onSaved }: { t: Transition; onSaved: (t: Transition) => void }) {
  const [note, setNote] = useState(t.note ?? "");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const save = async (score: number | null, n?: string) => {
    setSaving(true);
    setErr(null);
    try {
      onSaved({ ...t, ...(await scoreTransition(t.screen, t.name, score, n)) });
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };
  const cost = t.cost_usd != null ? `${Math.round(t.cost_usd * GBP_PER_USD * 100)}p` : null;
  return (
    <li className={cn(CARD_CLASS, "overflow-hidden")}>
      <div className="grid grid-cols-2 gap-1 bg-stone-100">
        {t.from_thumb ? <img src={t.from_thumb} alt="From" className="aspect-video w-full object-cover" loading="lazy" /> : <div />}
        {t.to_thumb ? <img src={t.to_thumb} alt="To" className="aspect-video w-full object-cover" loading="lazy" /> : <div />}
      </div>
      <video src={t.url} controls preload="none" poster={t.from_thumb} className="aspect-video w-full bg-black object-fill" />
      <div className="space-y-2 px-4 py-3">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <span className="rounded-full border border-stone-300 px-2 py-0.5 text-sm capitalize">{kindOf(t)}</span>
          <span className="text-[15px] font-medium">{t.idea}</span>
        </div>
        <p className="text-[15px] text-muted-foreground">{t.prompt}</p>
        {t.writer && (
          <details className="text-sm text-muted-foreground">
            <summary className="cursor-pointer">How it was worked out</summary>
            <dl className="mt-1 grid grid-cols-[auto,1fr] gap-x-3 gap-y-1">
              {t.writer.link_a && (<><dt>From</dt><dd>{t.writer.link_a}</dd></>)}
              {t.writer.link_b && (<><dt>To</dt><dd>{t.writer.link_b}</dd></>)}
              {t.writer.motion && (<><dt>Motion</dt><dd>{t.writer.motion}</dd></>)}
              {t.writer.rest && (<><dt>The rest</dt><dd>{t.writer.rest}</dd></>)}
            </dl>
          </details>
        )}
        <div className="flex flex-wrap items-center gap-2">
          {[1, 2, 3, 4, 5].map((n) => (
            <button
              key={n}
              type="button"
              disabled={saving}
              onClick={() => void save(t.score === n ? null : n)}
              aria-pressed={t.score === n}
              className={cn(
                "h-11 w-11 rounded-xl border text-base tabular-nums",
                t.score === n ? "border-foreground bg-foreground text-background" : "border-stone-300 bg-card",
              )}
            >
              {n}
            </button>
          ))}
          {saving && <Spinner />}
        </div>
        <textarea
          value={note}
          onChange={(e) => setNote(e.target.value)}
          onBlur={() => note !== (t.note ?? "") && void save(t.score ?? null, note)}
          placeholder="A note on it (what worked, what didn't)"
          rows={2}
          className="w-full rounded-xl border border-stone-200 bg-card px-3 py-2 text-[15px]"
        />
        {err && <Notice tone="bad">{err}</Notice>}
        <p className="text-sm text-muted-foreground">
          {[time(t.at), t.made_s ? `made in ${Math.round(t.made_s / 60)} min` : null, cost].filter(Boolean).join(" · ")}
        </p>
      </div>
    </li>
  );
}

export default function TransitionsPage() {
  const [menuOpen, setMenuOpen] = useState(false);
  const { destinations } = usePublishDestinations();
  const [screen, setScreen] = useState("");
  const [items, setItems] = useState<Transition[] | null>(null);
  const [failed, setFailed] = useState<Array<TransitionFailure & { screen: string }>>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    document.title = "Transitions · screen/machine";
    Promise.all(SCREENS.map((s) => getTransitions(s).then((r) => ({ s, r }))))
      .then((all) => {
        setItems(all.flatMap(({ r }) => r.transitions).sort((a, b) => (b.at ?? 0) - (a.at ?? 0)));
        setFailed(all.flatMap(({ s, r }) => r.failed.map((f) => ({ ...f, screen: s }))).sort((a, b) => b.at - a.at));
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  const nameOf = (id: string) => destinations.find((d) => d.id === id)?.name || id;
  const shown = (items ?? []).filter((t) => !screen || t.screen === screen);
  const days = useMemo(() => {
    const out: Array<[string, Transition[]]> = [];
    for (const t of shown) {
      const d = day(t.at);
      if (!out.length || out[out.length - 1][0] !== d) out.push([d, []]);
      out[out.length - 1][1].push(t);
    }
    return out;
  }, [shown]);

  return (
    <div className={cn(THEME_CLASS, "min-h-screen bg-background text-base text-foreground antialiased")}>
      <RoomThemeStyle />
      <div className="mx-auto w-full min-w-0 max-w-3xl space-y-4 px-4 pb-28 sm:px-6">
        <PageHeader title="Transitions" current="other" onMenu={() => setMenuOpen(true)} back={{ to: jobsUrl() }} compact />
        {error && <Notice tone="bad">{error}</Notice>}
        {items === null && !error && (
          <p className="flex items-center gap-2 text-muted-foreground">
            <Spinner /> Reading the transitions…
          </p>
        )}
        {items !== null && (
          <>
            <div className="flex flex-wrap gap-2">
              {["", ...SCREENS].map((s) => (
                <button
                  key={s || "all"}
                  type="button"
                  onClick={() => setScreen(s)}
                  aria-pressed={screen === s}
                  className={cn(
                    "h-11 rounded-xl border px-4 text-base",
                    screen === s ? "border-foreground bg-foreground text-background" : "border-stone-300 bg-card",
                  )}
                >
                  {s === "cloud" ? "The day's set" : s ? nameOf(s) : "All"}
                </button>
              ))}
            </div>
            <Scores items={shown} />
            {failed.filter((f) => !screen || f.screen === screen).length > 0 && (
              <details className={cn(CARD_CLASS, "px-4 py-3 text-[15px]")}>
                <summary className="cursor-pointer">
                  Not made: {failed.filter((f) => !screen || f.screen === screen).length} (the picture cross-faded)
                </summary>
                <ul className="mt-2 space-y-1 text-sm text-muted-foreground">
                  {failed
                    .filter((f) => !screen || f.screen === screen)
                    .map((f, i) => (
                      <li key={i}>
                        {day(f.at)} {time(f.at)} · {nameOf(f.screen)} · {f.error}
                      </li>
                    ))}
                </ul>
              </details>
            )}
            {shown.length === 0 && (
              <p className="rounded-2xl border border-dashed border-stone-300 px-4 py-4 text-[15px] text-muted-foreground">
                No transitions yet. They're made with the day's pictures.
              </p>
            )}
            {days.map(([d, list]) => (
              <section key={d} className="space-y-3">
                <h2 className="text-[17px] font-semibold">{d}</h2>
                <ul className="space-y-4">
                  {list.map((t) => (
                    <Card
                      key={t.screen + t.name}
                      t={{ ...t, idea: screen ? t.idea : `${t.screen === "cloud" ? "Day's set" : nameOf(t.screen)} · ${t.idea ?? ""}` }}
                      onSaved={(n) =>
                        setItems((all) => (all ?? []).map((x) => (x.screen === n.screen && x.name === n.name ? { ...x, score: n.score, note: n.note } : x)))
                      }
                    />
                  ))}
                </ul>
              </section>
            ))}
          </>
        )}
      </div>
      <AppMenu open={menuOpen} onOpenChange={setMenuOpen} current="other" />
    </div>
  );
}
