// The app's one menu, on every page (the generator, Schedules, Room, Music, Lights setup, Settings): navigation and
// read-only status only, never a control (each switch lives once, on its page, next to what it changes). In order:
//   Go to       Room · Music · Lights setup · Generator · Schedules · Settings ("Here" marks this page; Room, Music
//               and Lights only while the room answers)
//   <this page> whatever the page passes as children (the Room page: its connection, last mode change, activity log)
//   Screens     folded ("7 screens"): each screen's live badge and its Room, Generator, Schedule and TV page links
//   Tools       LAN only, each in a new tab: the scene library, Spectre, the DSP monitor
//   Developer   folded: the page's technical extras (`dev`), then the developer pages
// Links are AppLinks (inside the app: no reload, the dock and the socket stay), and a tap closes the menu.

import type { ReactNode } from "react";
import { CalendarClock, ExternalLink, Lightbulb, Music, Settings, SlidersHorizontal, Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";
import { usePublishDestinations } from "@/hooks/usePublishDestinations";
import { Unroll } from "@/pages/Room/bits";
import { SidePanel } from "@/pages/Room/overlays";
import { RoomThemeStyle } from "@/pages/Room/theme";
import { liveStateOf, useRoomLink } from "@/components/room/roomLink";
import { AppLink } from "./AppLink";
import {
  DEV_LINKS,
  displayUrl,
  generatorUrl,
  lightsPageUrl,
  musicPageUrl,
  roomPageUrl,
  schedulerUrl,
  settingsUrl,
  TOOL_LINKS,
  type AppPage,
} from "./appNav";

function NavRow({
  href,
  icon,
  label,
  hint,
  current,
  newTab,
  onGo,
  testId,
}: {
  href: string;
  icon?: ReactNode;
  label: string;
  hint?: string;
  current?: boolean;
  newTab?: boolean;
  onGo: () => void;
  testId?: string;
}) {
  return (
    <AppLink
      href={href}
      newTab={newTab}
      onClick={onGo}
      aria-current={current ? "page" : undefined}
      data-testid={testId}
      className={cn(
        "flex min-h-12 items-center gap-3 rounded-xl px-3 py-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        current ? "bg-teal-50 text-teal-900" : "hover:bg-stone-100",
      )}
    >
      {icon && (
        <span className={cn("grid h-9 w-9 shrink-0 place-items-center rounded-full", current ? "bg-teal-100 text-teal-800" : "bg-stone-100 text-stone-600")}>
          {icon}
        </span>
      )}
      <span className="min-w-0 flex-1">
        <span className="block text-base font-semibold leading-snug">{label}</span>
        {hint && <span className="block text-sm leading-snug text-muted-foreground">{hint}</span>}
      </span>
      {current ? (
        <span className="shrink-0 rounded-full bg-teal-700 px-2 py-0.5 text-xs font-semibold text-white">Here</span>
      ) : newTab ? (
        <ExternalLink className="h-4 w-4 shrink-0 text-stone-400" aria-hidden />
      ) : null}
    </AppLink>
  );
}

function Chip({ href, children, newTab, small, onGo }: { href: string; children: ReactNode; newTab?: boolean; small?: boolean; onGo: () => void }) {
  return (
    <AppLink
      href={href}
      newTab={newTab}
      onClick={onGo}
      className={cn(
        "inline-flex items-center gap-1 rounded-full border border-stone-200 bg-white font-medium text-stone-700 hover:bg-stone-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        small ? "h-8 px-2.5 text-[13px] text-stone-500" : "h-9 px-3 text-[14px]",
      )}
    >
      {children}
      {newTab && <ExternalLink className="h-3 w-3 shrink-0 text-stone-400" aria-hidden />}
    </AppLink>
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
  dev,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  current: AppPage;
  /** The heading over this page's own section (children). */
  pageTitle?: string;
  children?: ReactNode;
  /** This page's technical extras, at the top of the folded Developer section (e.g. the room service address). */
  dev?: ReactNode;
}) {
  return (
    <>
      <RoomThemeStyle />
      <SidePanel open={open} onOpenChange={onOpenChange} title="Menu" description="Screen Machine" testId="app-menu">
        <MenuBody current={current} pageTitle={pageTitle} dev={dev} close={() => onOpenChange(false)}>
          {children}
        </MenuBody>
      </SidePanel>
    </>
  );
}

/** Mounted only while the menu is open, so a closed menu costs no requests. */
function MenuBody({
  current,
  pageTitle,
  children,
  dev,
  close,
}: {
  current: AppPage;
  pageTitle?: string;
  children?: ReactNode;
  dev?: ReactNode;
  close: () => void;
}) {
  const room = useRoomLink();
  const { destinationsWithBuckets } = usePublishDestinations();
  const screens = destinationsWithBuckets.filter((d) => d.id !== "_recent");
  const roomScreens = room.status?.screens ?? {};
  const liveCount = screens.filter((d) => liveStateOf(roomScreens[d.id]) === "live").length;

  return (
    <div className="space-y-5">
      <nav aria-label="Go to" className="space-y-1 border-b border-stone-200/80 pb-4">
        <Heading>Go to</Heading>
        {(room.onLan || current === "room") && (
          <NavRow href={roomPageUrl()} icon={<SlidersHorizontal className="h-5 w-5" />} label="Room" hint="Modes, screens, lights and music" current={current === "room"} onGo={close} />
        )}
        {(room.onLan || current === "music") && (
          <NavRow href={musicPageUrl()} icon={<Music className="h-5 w-5" />} label="Music" hint="Pieces, playlists, recordings" current={current === "music"} onGo={close} />
        )}
        {(room.onLan || current === "lights") && (
          <NavRow href={lightsPageUrl()} icon={<Lightbulb className="h-5 w-5" />} label="Lights setup" hint="Add, name and find lights" current={current === "lights"} onGo={close} testId="menu-lights" />
        )}
        <NavRow href={generatorUrl()} icon={<Sparkles className="h-5 w-5" />} label="Generator" hint="Make art for each screen" current={current === "generator"} onGo={close} />
        <NavRow href={schedulerUrl()} icon={<CalendarClock className="h-5 w-5" />} label="Schedules" hint="What each screen does through the day" current={current === "scheduler"} onGo={close} />
        <NavRow href={settingsUrl()} icon={<Settings className="h-5 w-5" />} label="Settings" hint="Things you set once in a while" current={current === "settings"} onGo={close} testId="menu-settings" />
      </nav>

      {children && (
        <div className="space-y-3 border-b border-stone-200/80 pb-4">
          {pageTitle && <Heading>{pageTitle}</Heading>}
          {children}
        </div>
      )}

      {screens.length > 0 && (
        <nav aria-label="Screens" className="border-b border-stone-200/80 pb-3">
          <Unroll
            title="Screens"
            hint={`${screens.length} screen${screens.length === 1 ? "" : "s"}${liveCount ? ` · ${liveCount} live` : ""}`}
            testId="app-menu-screens"
          >
            <ul className="space-y-2 pb-1">
              {screens.map((d) => {
                const rs = roomScreens[d.id];
                const ls = liveStateOf(rs);
                return (
                  <li key={d.id} className="rounded-xl border border-stone-200/80 bg-white px-3 py-2" data-testid={`app-menu-screen-${d.id}`}>
                    <div className="flex items-center gap-2">
                      <span className="min-w-0 flex-1 truncate text-[15px] font-semibold">{d.name || d.id}</span>
                      {ls === "live" && (
                        <span className="inline-flex items-center gap-1 rounded-full bg-red-600 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-white">
                          <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-white motion-safe:animate-pulse" /> Live
                        </span>
                      )}
                    </div>
                    <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                      {rs && room.onLan && (
                        <Chip href={roomPageUrl(d.id)} onGo={close}>
                          Room
                        </Chip>
                      )}
                      <Chip href={generatorUrl(d.id)} onGo={close}>
                        Generator
                      </Chip>
                      <Chip href={schedulerUrl(d.id)} onGo={close}>
                        Schedule
                      </Chip>
                      {/* what the TV itself loads: small, in a new tab, so a phone doesn't open it by accident */}
                      <Chip href={displayUrl(d.id)} newTab small onGo={close}>
                        TV page
                      </Chip>
                    </div>
                  </li>
                );
              })}
            </ul>
          </Unroll>
        </nav>
      )}

      {room.onLan && (
        <nav aria-label="Tools" className="space-y-1 border-b border-stone-200/80 pb-4">
          <Heading>Tools</Heading>
          {TOOL_LINKS.map((l) => (
            <NavRow key={l.label} href={l.href()} label={l.label} hint={l.hint} newTab onGo={close} />
          ))}
        </nav>
      )}

      <Unroll title="Developer" hint="technical details and raw data" testId="app-menu-dev">
        {dev && <div className="space-y-3 pb-3">{dev}</div>}
        <ul className="space-y-1 pb-2">
          {DEV_LINKS.filter((l) => !l.lan || room.onLan).map((l) => (
            <li key={l.label}>
              <a
                href={l.href()}
                target="_blank"
                rel="noopener noreferrer"
                onClick={close}
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
