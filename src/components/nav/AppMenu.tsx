// The one menu for the generator (home page) and the Room console: the same side panel, the same places
// in the same order, then whatever belongs to the page it was opened from (passed as children), then the
// developer pages. So the two read as one app: Go to · Screens · <this page> · Developer.

import type { ReactNode } from "react";
import { CalendarClock, ExternalLink, Library, Music, SlidersHorizontal, Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";
import { usePublishDestinations } from "@/hooks/usePublishDestinations";
import { Unroll } from "@/pages/Room/bits";
import { SidePanel } from "@/pages/Room/overlays";
import { RoomThemeStyle } from "@/pages/Room/theme";
import { liveStateOf, useRoomLink } from "@/components/room/roomLink";
import {
  DEV_LINKS,
  displayUrl,
  generatorUrl,
  musicPageUrl,
  rendererConsoleUrl,
  roomPageUrl,
  schedulerUrl,
  type AppPage,
} from "./appNav";

function NavRow({
  href,
  icon,
  label,
  hint,
  current,
  newTab,
}: {
  href: string;
  icon: ReactNode;
  label: string;
  hint?: string;
  current?: boolean;
  newTab?: boolean;
}) {
  return (
    <a
      href={href}
      aria-current={current ? "page" : undefined}
      {...(newTab ? { target: "_blank", rel: "noopener noreferrer" } : {})}
      className={cn(
        "flex min-h-12 items-center gap-3 rounded-xl px-3 py-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        current ? "bg-teal-50 text-teal-900" : "hover:bg-stone-100",
      )}
    >
      <span className={cn("grid h-9 w-9 shrink-0 place-items-center rounded-full", current ? "bg-teal-100 text-teal-800" : "bg-stone-100 text-stone-600")}>
        {icon}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-base font-semibold leading-snug">{label}</span>
        {hint && <span className="block truncate text-sm leading-snug text-muted-foreground">{hint}</span>}
      </span>
      {current ? (
        <span className="shrink-0 rounded-full bg-teal-700 px-2 py-0.5 text-xs font-semibold text-white">Here</span>
      ) : newTab ? (
        <ExternalLink className="h-4 w-4 shrink-0 text-stone-400" aria-hidden />
      ) : null}
    </a>
  );
}

function Chip({ href, children, newTab }: { href: string; children: ReactNode; newTab?: boolean }) {
  return (
    <a
      href={href}
      {...(newTab ? { target: "_blank", rel: "noopener noreferrer" } : {})}
      className="inline-flex h-9 items-center rounded-full border border-stone-200 bg-white px-3 text-[14px] font-medium text-stone-700 hover:bg-stone-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      {children}
    </a>
  );
}

function Heading({ children }: { children: ReactNode }) {
  return <h3 className="px-1 text-sm font-semibold text-muted-foreground">{children}</h3>;
}

export function AppMenu({
  open,
  onOpenChange,
  current,
  pageTitle,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  current: AppPage;
  /** The heading over this page's own section (children). */
  pageTitle?: string;
  children?: ReactNode;
}) {
  return (
    <>
      <RoomThemeStyle />
      <SidePanel open={open} onOpenChange={onOpenChange} title="Menu" description="Screen Machine: the generator and the room" testId="app-menu">
        <MenuBody current={current} pageTitle={pageTitle}>
          {children}
        </MenuBody>
      </SidePanel>
    </>
  );
}

/** Mounted only while the menu is open, so a closed menu costs no requests. */
function MenuBody({ current, pageTitle, children }: { current: AppPage; pageTitle?: string; children?: ReactNode }) {
  const room = useRoomLink();
  const { destinationsWithBuckets } = usePublishDestinations();
  const screens = destinationsWithBuckets.filter((d) => d.id !== "_recent");
  const roomScreens = room.status?.screens ?? {};

  return (
        <div className="space-y-5">
          <nav aria-label="Go to" className="space-y-1 border-b border-stone-200/80 pb-4">
            <Heading>Go to</Heading>
            <NavRow href={generatorUrl()} icon={<Sparkles className="h-5 w-5" />} label="Generator" hint="Make art and choose what each screen shows" current={current === "generator"} />
            {(room.onLan || current === "room") && (
              <NavRow href={roomPageUrl()} icon={<SlidersHorizontal className="h-5 w-5" />} label="Room" hint="Modes, lights, blinds and live" current={current === "room"} />
            )}
            {(room.onLan || current === "music") && (
              <NavRow href={musicPageUrl()} icon={<Music className="h-5 w-5" />} label="Music" hint="Piano pieces, playlists and recordings" current={current === "music"} />
            )}
            <NavRow href={schedulerUrl()} icon={<CalendarClock className="h-5 w-5" />} label="Scheduler" hint="What each screen does through the day" current={current === "scheduler"} />
            {room.onLan && (
              <NavRow href={rendererConsoleUrl()} icon={<Library className="h-5 w-5" />} label="Library" hint="Live scenes, effects and ratings" newTab />
            )}
          </nav>

          {screens.length > 0 && (
            <nav aria-label="Screens" className="space-y-2 border-b border-stone-200/80 pb-4">
              <Heading>Screens</Heading>
              <ul className="space-y-2">
                {screens.map((d) => {
                  const rs = roomScreens[d.id];
                  const ls = liveStateOf(rs);
                  return (
                    <li key={d.id} className="rounded-xl border border-stone-200/80 bg-white px-3 py-2">
                      <div className="flex items-center gap-2">
                        {/* The name opens the screen's own page in a new tab */}
                        <a
                          href={displayUrl(d.id)}
                          target="_blank"
                          rel="noopener noreferrer"
                          data-testid={`app-menu-screen-${d.id}`}
                          className="flex min-w-0 flex-1 items-center gap-1.5 rounded-md text-[15px] font-semibold hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                        >
                          <span className="truncate">{d.name || d.id}</span>
                          <ExternalLink className="h-3.5 w-3.5 shrink-0 text-stone-400" aria-hidden />
                        </a>
                        {ls === "live" && (
                          <span className="inline-flex items-center gap-1 rounded-full bg-red-600 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-white">
                            <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-white motion-safe:animate-pulse" /> Live
                          </span>
                        )}
                      </div>
                      <div className="mt-1.5 flex flex-wrap gap-1.5">
                        <Chip href={generatorUrl(d.id)}>Generator</Chip>
                        {rs && room.onLan && <Chip href={roomPageUrl(d.id)}>Room</Chip>}
                      </div>
                    </li>
                  );
                })}
              </ul>
            </nav>
          )}

          {children && (
            <div className="space-y-3">
              {pageTitle && <Heading>{pageTitle}</Heading>}
              {children}
            </div>
          )}

          <Unroll title="Developer" hint="tools and raw data" testId="app-menu-dev">
            <ul className="space-y-1 pb-2">
              {DEV_LINKS.filter((l) => !l.lan || room.onLan).map((l) => (
                <li key={l.label}>
                  <a
                    href={l.href()}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex min-h-11 items-center justify-between gap-2 rounded-xl px-3 text-[15px] hover:bg-stone-100"
                  >
                    {l.label}
                    <ExternalLink className="h-4 w-4 shrink-0 text-stone-400" aria-hidden />
                  </a>
                </li>
              ))}
            </ul>
          </Unroll>
        </div>
  );
}
