// Rate what's on glass: five stars and Never, as in the Scene library (useRatings.ts). Tapping the stars it has
// clears them. Under a scene button, and in an effect's sheet; an effect chip shows its stars, or an empty star while
// unrated (owner, 29 Oct: rating an effect from its chip "just wasn't obvious").
// "This is good" (owner, 29 Oct: "right now, this is good", so we use that again): the scene on screen, its effects
// and the two together come up more often (stream_service taste.py).

import { useEffect, useRef, useState } from "react";
import { Ban, Star, ThumbsUp } from "lucide-react";
import { cn } from "@/lib/utils";
import { explainError, type RoomApi } from "./api";
import { Spinner } from "./bits";
import { rate, useRatings } from "./useRatings";

function words(what: string, stars: number, reject: boolean): string {
  if (reject) return "Never picked by itself";
  if (!stars) return `Rate this ${what}`;
  return stars >= 3 ? "Picked more often" : "Picked less often";
}

export function RatingRow({
  api,
  ratingKey,
  what,
  className,
  testId,
  bare = false,
}: {
  api: RoomApi;
  /** Stars and Never only, without the words beside them. */
  bare?: boolean;
  /** "scene/<source>" or "effect/<effect>"; nothing shows without one. */
  ratingKey: string | null | undefined;
  /** "scene" or "effect", for the words. */
  what: string;
  className?: string;
  testId?: string;
}) {
  const ratings = useRatings(api);
  const [error, setError] = useState<string | null>(null);
  if (!ratingKey) return null;
  const e = ratings[ratingKey];
  const stars = e?.stars ?? 0;
  const reject = !!e?.reject;
  const set = (patch: { stars?: number; reject?: boolean }) => {
    setError(null);
    void rate(ratingKey, patch).then(setError);
  };
  return (
    <div className={className} data-testid={testId}>
      <div className="flex min-h-11 items-center gap-2">
        {!bare && <span className="min-w-0 flex-1 text-[15px] leading-tight text-muted-foreground">{words(what, stars, reject)}</span>}
        <div role="group" aria-label={`Rate this ${what}`} className="flex shrink-0 items-center">
          {[1, 2, 3, 4, 5].map((n) => (
            <button
              key={n}
              type="button"
              aria-label={`${n} star${n > 1 ? "s" : ""}`}
              aria-pressed={stars === n}
              title={stars === n ? "Clear the rating" : `${n} star${n > 1 ? "s" : ""}`}
              onClick={() => set({ stars: stars === n ? 0 : n })}
              data-testid={testId ? `${testId}-${n}` : undefined}
              className="grid h-11 w-8 place-items-center rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <Star aria-hidden className={cn("h-6 w-6", n <= stars ? "fill-amber-400 text-amber-500" : "text-stone-400")} />
            </button>
          ))}
          <button
            type="button"
            aria-label="Never pick it by itself"
            aria-pressed={reject}
            title={reject ? "Let it be picked again" : "Never pick it by itself"}
            onClick={() => set({ reject: !reject })}
            data-testid={testId ? `${testId}-never` : undefined}
            className={cn(
              "ml-1 grid h-11 w-9 place-items-center rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              reject ? "bg-rose-50 text-rose-700" : "text-stone-400",
            )}
          >
            <Ban aria-hidden className="h-5 w-5" />
          </button>
        </div>
      </div>
      {error && <p className="text-sm leading-snug text-rose-700">{error}</p>}
    </div>
  );
}

/** A chip's rating, small: its stars, the Never sign, or an empty star while unrated (tap the chip to rate it). */
export function RatingBadge({ api, ratingKey }: { api: RoomApi; ratingKey: string }) {
  const e = useRatings(api)[ratingKey];
  if (e?.reject) return <Ban aria-label="Never picked by itself" className="h-3.5 w-3.5 shrink-0 text-rose-600" />;
  if (!e?.stars) return <Star aria-label="Not rated: tap to rate it" className="h-3.5 w-3.5 shrink-0 text-stone-400" />;
  return (
    <span className="inline-flex shrink-0 items-center gap-0.5 text-sm text-amber-700" aria-label={`${e.stars} star${e.stars > 1 ? "s" : ""}`}>
      <Star aria-hidden className="h-3.5 w-3.5 fill-amber-400 text-amber-500" />
      {e.stars}
    </span>
  );
}

const NOTE_MS = 8_000;

/** "This is good": what's on screen now (the scene, its effects, the two together) comes up more often. */
export function ThisIsGood({
  api,
  session,
  effectNames,
  className,
  testId,
}: {
  api: RoomApi;
  /** The live session that holds the look (a room's leader); nothing shows without one. */
  session: string | null | undefined;
  /** What the effects on screen are called, for the thank-you line ("with Kaleidoscope"). */
  effectNames?: string[];
  className?: string;
  testId?: string;
}) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ tone: "ok" | "bad"; text: string } | null>(null);
  const timer = useRef<number | null>(null);
  useEffect(() => () => void (timer.current !== null && window.clearTimeout(timer.current)), []);
  if (!session) return null;
  const show = (n: { tone: "ok" | "bad"; text: string }) => {
    setNote(n);
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setNote(null), NOTE_MS);
  };
  const press = () => {
    if (busy) return;
    setBusy(true);
    api
      .good(session)
      .then((r) => {
        const fx = effectNames?.length ? effectNames : null;
        const n = r?.effects?.length ?? 0;
        show({
          tone: "ok",
          text: fx
            ? `Noted: this scene with ${fx.join(" and ")} will come up more often.`
            : n
              ? "Noted: this scene and its effects will come up more often."
              : "Noted: this scene will come up more often.",
        });
      })
      .catch((e) => show({ tone: "bad", text: `Couldn't note it: ${explainError(e)}` }))
      .finally(() => setBusy(false));
  };
  return (
    <div className={cn("flex min-h-11 flex-wrap items-center gap-x-3 gap-y-1", className)} data-testid={testId}>
      <button
        type="button"
        onClick={press}
        aria-busy={busy}
        title="Right now, this is good: the art director uses this scene, these effects and the two together more often"
        className="inline-flex h-11 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-xl border border-teal-300 bg-teal-50 px-3.5 text-[15px] font-medium text-teal-900 hover:bg-teal-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        {busy ? <Spinner className="h-4 w-4" /> : <ThumbsUp className="h-4 w-4" aria-hidden />} This is good
      </button>
      {note && (
        <span role="status" className={cn("min-w-0 flex-1 text-[15px] leading-snug", note.tone === "bad" ? "text-rose-700" : "text-muted-foreground")}>
          {note.text}
        </span>
      )}
    </div>
  );
}
