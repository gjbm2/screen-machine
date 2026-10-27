// The app's places, one tab bar for every page a person uses (owner, 27 Oct: "I don't mind the idea of a tab bar
// across the top of the app -- but if we have one it needs to be properly designed so it looks good on mobile, ipad
// and desktop"). Mounted once in App.tsx.
//
//   phone (< 640 px)   a bottom tab bar, the platform's own pattern: icon over label, thumb height, safe area under
//                      it; nothing at the top, so each page keeps its own short header. The docked player sits on it.
//   iPad and desktop   a slim bar at the top: the wordmark, the places as one segmented control in the middle, and a
//                      slot on the right (#app-nav-actions) where the page's header puts its Mic / AI switches,
//                      pills and menu, so the page's own header is just its status line. Before the slot, on every
//                      page but Room, the room's mode as a menu (ModeMenu.tsx).
//
// Its height is --app-nav-top / --app-nav-bottom on <html> (index.css: body padding; the docked player sits above
// the bottom bar). Not on the TVs' pages or the kiosk/tool pages.

import { useEffect, useLayoutEffect, useRef } from "react";
import { useLocation } from "react-router-dom";
import { Lamp, Music2, SlidersHorizontal, Sparkles, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { useRoomGlance } from "./useRoomGlance";
import { AppLink } from "./AppLink";
import { RoomThemeStyle, THEME_CLASS } from "@/pages/Room/theme";
import { PLACES, type AppPage } from "./appNav";
import { ModeMenu } from "./ModeMenu";

const NAV_PATHS = /^\/(room|music|lights|settings|scheduler|jobs)?\/?$/;
const ICONS: Partial<Record<AppPage, LucideIcon>> = {
  room: SlidersHorizontal,
  music: Music2,
  lights: Lamp,
  generator: Sparkles,
};

function currentPage(pathname: string): AppPage {
  const top = pathname.split("/")[1] ?? "";
  if (top === "") return "generator";
  if (top === "room" || top === "music" || top === "lights" || top === "settings" || top === "scheduler" || top === "jobs") return top;
  return "other";
}

/** Whether the app's tab bar is on this route (PageHeader reads it to move its switches into the top bar). */
export function useAppNavShown(): boolean {
  return NAV_PATHS.test(useLocation().pathname);
}

export const APP_NAV_ACTIONS_ID = "app-nav-actions";

export function AppNav() {
  const { pathname } = useLocation();
  const shown = NAV_PATHS.test(pathname);
  const glance = useRoomGlance();
  const here = currentPage(pathname);
  const places = PLACES.filter((p) => !p.lan || glance.onLan || p.page === here);
  const topRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLElement>(null);

  // the bars' heights, for the body's padding and the docked player
  useLayoutEffect(() => {
    const root = document.documentElement;
    if (!shown) {
      root.style.removeProperty("--app-nav-top");
      root.style.removeProperty("--app-nav-bottom");
      return;
    }
    const set = () => {
      const t = topRef.current?.getBoundingClientRect().height ?? 0;
      const b = bottomRef.current?.getBoundingClientRect().height ?? 0;
      root.style.setProperty("--app-nav-top", `${Math.ceil(t)}px`);
      root.style.setProperty("--app-nav-bottom", `${Math.ceil(b)}px`);
    };
    set();
    const ro = new ResizeObserver(set);
    if (topRef.current) ro.observe(topRef.current);
    if (bottomRef.current) ro.observe(bottomRef.current);
    window.addEventListener("resize", set);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", set);
      root.style.removeProperty("--app-nav-top");
      root.style.removeProperty("--app-nav-bottom");
    };
  }, [shown]);

  useEffect(() => {
    document.documentElement.classList.toggle("has-app-nav", shown);
    return () => document.documentElement.classList.remove("has-app-nav");
  }, [shown]);

  if (!shown) return null;

  const tab = (p: (typeof places)[number], variant: "top" | "bottom") => {
    const Icon = ICONS[p.page] ?? Sparkles;
    const on = p.page === here;
    return (
      <AppLink
        key={p.page}
        href={p.href()}
        aria-current={on ? "page" : undefined}
        data-testid={`nav-${variant}-${p.page}`}
        onClick={(e) => {
          if (on && pathname === p.href().split(/[?#]/)[0] && !window.location.search) {
            e.preventDefault();
            window.scrollTo({ top: 0, behavior: "smooth" });
          }
        }}
        className={cn(
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          variant === "top"
            ? cn(
                "inline-flex h-9 items-center gap-1.5 rounded-full px-3.5 text-[15px] font-medium transition-colors",
                on ? "bg-white text-teal-800 shadow-sm ring-1 ring-stone-200" : "text-stone-600 hover:text-stone-900",
              )
            : cn(
                "flex min-w-0 flex-1 flex-col items-center justify-center gap-0.5 rounded-xl pb-1 pt-1.5 text-[11px] font-medium leading-none",
                on ? "text-teal-800" : "text-stone-500",
              ),
        )}
      >
        <Icon aria-hidden className={variant === "top" ? "h-4 w-4" : cn("h-6 w-6", on && "stroke-[2.25]")} />
        <span className="truncate">{p.label}</span>
      </AppLink>
    );
  };

  return (
    <>
      {/* iPad and desktop: the top bar */}
      <div
        ref={topRef}
        className="sticky top-[var(--app-top,0px)] z-40 hidden border-b border-stone-200/80 bg-background/85 backdrop-blur-md sm:block"
        data-testid="app-nav-top"
      >
        {/* iPad: the places left, the page's switches right; desktop (>= 1280): the wordmark left, places centred */}
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-3 px-4 xl:grid xl:grid-cols-[1fr_auto_1fr]">
          <span className="hidden truncate text-[15px] font-semibold tracking-tight text-stone-700 xl:block">
            screen/machine
          </span>
          <nav aria-label="Places" className="flex items-center gap-0.5 rounded-full bg-stone-200/70 p-1">
            {places.map((p) => tab(p, "top"))}
          </nav>
          {/* the room's mode (a menu to change it) on every page but Room, which has the mode tiles; then the page's own
              switches and menu (the slot) */}
          <div className="ml-auto flex min-w-0 items-center justify-end gap-1 xl:ml-0">
            {here !== "room" && (
              <span className={cn(THEME_CLASS, "flex shrink-0 items-center")}>
                <RoomThemeStyle />
                <ModeMenu />
              </span>
            )}
            <div id={APP_NAV_ACTIONS_ID} className="flex min-w-0 items-center justify-end gap-1" />
          </div>
        </div>
      </div>

      {/* phone: the bottom tab bar */}
      <nav
        ref={bottomRef}
        aria-label="Places"
        className="fixed inset-x-0 bottom-0 z-50 border-t border-stone-200/80 bg-background/90 pb-[env(safe-area-inset-bottom)] backdrop-blur-md sm:hidden"
        data-testid="app-nav-bottom"
      >
        <div className="mx-auto flex h-14 max-w-md items-stretch px-2">{places.map((p) => tab(p, "bottom"))}</div>
      </nav>
    </>
  );
}
