// Rate what's on glass: five stars and Never, as in the Scene library (useRatings.ts). Tapping the stars it has
// clears them. Under a scene button, and in an effect's sheet; an effect chip shows its stars.

import { useState } from "react";
import { Ban, Star } from "lucide-react";
import { cn } from "@/lib/utils";
import type { RoomApi } from "./api";
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
}: {
  api: RoomApi;
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
        <span className="min-w-0 flex-1 text-[15px] leading-tight text-muted-foreground">{words(what, stars, reject)}</span>
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

/** A chip's rating, small: its stars, or the Never sign; nothing when unrated. */
export function RatingBadge({ api, ratingKey }: { api: RoomApi; ratingKey: string }) {
  const e = useRatings(api)[ratingKey];
  if (e?.reject) return <Ban aria-label="Never picked by itself" className="h-3.5 w-3.5 shrink-0 text-rose-600" />;
  if (!e?.stars) return null;
  return (
    <span className="inline-flex shrink-0 items-center gap-0.5 text-sm text-amber-700" aria-label={`${e.stars} star${e.stars > 1 ? "s" : ""}`}>
      <Star aria-hidden className="h-3.5 w-3.5 fill-amber-400 text-amber-500" />
      {e.stars}
    </span>
  );
}
