// The one back control of a sub-page (a score, Lights setup, Settings, Schedules). The iPad Home Screen app has no
// browser Back, so this never strands anyone:
//   - came here inside the app (react-router has an entry before this one): it goes back, so the page you came from
//     comes back as it was (its scroll, folds and tabs);
//   - opened directly (a bookmark, a QR code, a new tab): it goes to ?from=<path> when the link said where it came
//     from, else to the page's own parent (`to`: Score → /music, Lights setup → /room, Settings → /room).
// Its words name where it goes ("‹ Room"): `label`, else the from state a link passed ({from: "Room"}), else the
// parent's name.

import { ChevronLeft } from "lucide-react";
import type { MouseEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { cn } from "@/lib/utils";
import { AppLink } from "./AppLink";

/** A page's name from its path, for the back link's words. */
export function placeName(path: string): string {
  const p = path.split(/[?#]/)[0];
  if (p.startsWith("/room")) return "Room";
  if (p.startsWith("/music")) return path.includes("score=") ? "Score" : "Music";
  if (p.startsWith("/lights")) return "Lights";
  if (p.startsWith("/settings")) return "Settings";
  if (p.startsWith("/scheduler")) return "Schedules";
  if (p === "/" || p === "") return "Generator";
  return "Back";
}

/** A same-origin app path only (a ?from= value is never sent anywhere else). */
function safePath(v: string | null): string | null {
  return v && v.startsWith("/") && !v.startsWith("//") ? v : null;
}

export function BackLink({
  to,
  label,
  className,
  testId = "back-link",
}: {
  /** Where it goes when there is no in-app history and no ?from=. */
  to: string;
  /** The words after the chevron; by default the name of where it goes. */
  label?: string;
  className?: string;
  testId?: string;
}) {
  const location = useLocation();
  const navigate = useNavigate();
  const from = safePath(new URLSearchParams(location.search).get("from"));
  const stateFrom = (location.state as { from?: unknown } | null)?.from;
  const inApp = location.key !== "default";
  const target = from ?? to;
  const words = label ?? (typeof stateFrom === "string" && stateFrom ? stateFrom : placeName(target));
  const onClick = (e: MouseEvent<HTMLAnchorElement>) => {
    if (!inApp || e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
    e.preventDefault();
    navigate(-1);
  };
  return (
    <AppLink
      href={target}
      onClick={onClick}
      data-testid={testId}
      aria-label={`Back to ${words}`}
      className={cn(
        "-ml-2 inline-flex h-11 shrink-0 items-center gap-0.5 rounded-full pl-1 pr-3 text-[17px] font-semibold text-teal-800 hover:bg-stone-200/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        className,
      )}
    >
      <ChevronLeft aria-hidden className="h-6 w-6" />
      {words}
    </AppLink>
  );
}
