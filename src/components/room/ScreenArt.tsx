// What a screen shows, small, for the Room console's screen cards: the art published to it (the same
// picture and prompt as its tab in the generator, from GET /api/published/<id>, whose small thumbnail
// comes inline), or, while it's live, the live scene. The TV page reports the art it shows
// (status observed.kiosk_state.base_url, with the file's mtime), so a new picture re-reads it at once.
// Under the card's More, two one-line links: "In the generator" with the art's prompt, and "Schedule" with what the
// screen's schedule is doing (useScreenSchedules.ts, one read for the page). Both stay inside the app (AppLink).

import { useEffect, useState } from "react";
import { CalendarClock, CircleAlert, Sparkles, Tv } from "lucide-react";
import { cn } from "@/lib/utils";
import { API_URL } from "@/shared/http/client";
import { AppLink } from "@/components/nav/AppLink";
import { generatorUrl, schedulerUrl } from "@/components/nav/appNav";
import { scheduleWords, useScreenSchedule } from "./useScreenSchedules";

export interface PublishedArt {
  thumb: string | null;
  prompt: string | null;
}

const cache = new Map<string, PublishedArt>();

/** The art published to a screen; `artKey` (the TV's current art URL) changing re-reads it. */
export function usePublishedArt(screenId: string, artKey: string | null | undefined): PublishedArt | null {
  const key = `${screenId}|${artKey ?? ""}`;
  const [art, setArt] = useState<PublishedArt | null>(() => cache.get(key) ?? null);
  useEffect(() => {
    const hit = cache.get(key);
    if (hit) {
      setArt(hit);
      return;
    }
    let gone = false;
    fetch(`${API_URL}/published/${encodeURIComponent(screenId)}`, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        const v: PublishedArt = {
          thumb: typeof d?.thumbnail === "string" && d.thumbnail ? `data:image/jpeg;base64,${d.thumbnail}` : null,
          prompt: typeof d?.meta?.prompt === "string" ? d.meta.prompt : null,
        };
        cache.set(key, v);
        if (!gone) setArt(v);
      })
      .catch(() => {
        /* keep the last picture; the card still works without one */
      });
    return () => {
      gone = true;
    };
  }, [key, screenId]);
  return art;
}

/** The thumbnail on a screen card: the art, or the live scene with a LIVE tag; a problem adds a warning. */
export function ArtThumb({
  src,
  live,
  problem,
  className,
}: {
  src: string | null | undefined;
  live?: boolean;
  problem?: boolean;
  className?: string;
}) {
  return (
    <span aria-hidden className={cn("relative block h-12 w-[4.5rem] shrink-0 overflow-hidden rounded-lg bg-stone-200", className)}>
      {src ? (
        <img src={src} alt="" className="h-full w-full object-cover" draggable={false} />
      ) : (
        <span className="grid h-full w-full place-items-center text-stone-500">
          <Tv className="h-5 w-5" />
        </span>
      )}
      {live && (
        <span className="absolute left-1 top-1 rounded bg-red-600 px-1 text-[9px] font-bold leading-[14px] tracking-wide text-white">LIVE</span>
      )}
      {problem && (
        <span className="absolute bottom-0.5 right-0.5 grid h-5 w-5 place-items-center rounded-full bg-white text-rose-700">
          <CircleAlert className="h-4 w-4" />
        </span>
      )}
    </span>
  );
}

const ROW = "flex min-h-11 min-w-0 items-center gap-2";
const LINK =
  "inline-flex h-11 shrink-0 items-center gap-1.5 rounded-xl px-1 text-[15px] font-medium text-teal-800 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

/** "In the generator": this screen's tab in the generator (where its art is made and chosen), and the art's prompt. */
export function GeneratorLink({ screenId, prompt }: { screenId: string; prompt?: string | null }) {
  return (
    <div className={ROW}>
      <AppLink href={generatorUrl(screenId)} state={{ from: "Room" }} data-testid={`generator-${screenId}`} className={LINK}>
        <Sparkles className="h-4 w-4 text-teal-700" aria-hidden /> In the generator
      </AppLink>
      {prompt && (
        <span className="min-w-0 truncate text-sm text-muted-foreground" title={prompt}>
          {prompt}
        </span>
      )}
    </div>
  );
}

/** "Schedule": this screen's schedule on the Schedules page, and what it's doing in a line
 *  ("New art every 2 h · next 20:04", "Schedule paused", "No schedule running"). */
export function ScheduleLink({ screenId }: { screenId: string }) {
  const words = scheduleWords(useScreenSchedule(screenId));
  return (
    <div className={ROW}>
      <AppLink href={schedulerUrl(screenId)} state={{ from: "Room" }} data-testid={`schedule-${screenId}`} className={LINK}>
        <CalendarClock className="h-4 w-4 text-teal-700" aria-hidden /> Schedule
      </AppLink>
      {words && (
        <span className="min-w-0 truncate text-sm text-muted-foreground" title={words} data-testid={`schedule-words-${screenId}`}>
          {words}
        </span>
      )}
    </div>
  );
}
