// The room's mode, in the header of every page but the Room page (which has the mode tiles): the mode's icon and name
// in a pill, red with a dot while a screen is live; a tap opens the modes (icon and name, the one it's in ticked) and a
// pick switches the room (PUT /api/v1/mode, as the Room page's tiles do; the last pick wins). Owner, 27 Oct: "now you
// have mode icons you can use them ... as a top nav element in other screens (drop down menu to change)".
//   - iPad and desktop: in the app's top bar (AppNav), icon and name. Phone: in the page's header, the icon alone.
//   - Shown only while this page may switch the room (useRoomGlance: the shared room link).
//   - Modes kept off the Room page (on_room_page "more") come last, under a line.

import { useEffect, useState } from "react";
import { Check, ChevronDown, SlidersHorizontal } from "lucide-react";
import { roomApi } from "@/components/room/roomLink";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import { explainError } from "@/pages/Room/api";
import { Spinner } from "@/pages/Room/bits";
import { modeIcon } from "@/pages/Room/modeIcons";
import { THEME_CLASS } from "@/pages/Room/theme";
import type { ModeDef } from "@/pages/Room/types";
import { modeLabel } from "@/pages/Room/words";
import { AppLink } from "./AppLink";
import { roomPageUrl } from "./appNav";
import { useRoomGlance } from "./useRoomGlance";

let picks = 0;

export function ModeMenu({ iconOnly = false, className }: { iconOnly?: boolean; className?: string }) {
  const g = useRoomGlance();
  const [asked, setAsked] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (!err) return;
    const id = setTimeout(() => setErr(null), 6_000);
    return () => clearTimeout(id);
  }, [err]);
  // the room took the pick (it's switching or there): stop showing our own spinner
  useEffect(() => {
    if (asked && (g.current === asked || g.target === asked)) setAsked(null);
  }, [asked, g.current, g.target]);

  if (!g.canControl || !g.current) return null;
  const going = g.target ?? asked;
  const shown = going ?? g.current;
  const def = g.modes?.[shown] as (ModeDef & { icon?: unknown }) | undefined;
  const Icon = modeIcon(shown, def);
  const label = def?.label || modeLabel(shown, g.modes);
  const live = !!g.liveMode && !going;
  const entries = Object.entries(g.modes ?? {});
  const isMore = (d: ModeDef) => (d as ModeDef & { on_room_page?: unknown }).on_room_page === "more";
  const main = entries.filter(([n, d]) => !isMore(d) || n === g.current);
  const extra = entries.filter(([n, d]) => isMore(d) && n !== g.current);

  const pick = (name: string) => {
    if (name === g.current && !g.target) return;
    const mine = ++picks;
    setAsked(name);
    setErr(null);
    roomApi()
      .setMode(name, "header-menu")
      .catch((e) => {
        if (mine !== picks) return;
        setAsked(null);
        setErr(`Couldn't switch to ${modeLabel(name, g.modes)}: ${explainError(e)}`);
      });
  };

  const item = ([name, d]: [string, ModeDef]) => {
    const I = modeIcon(name, d as ModeDef & { icon?: unknown });
    const on = name === g.current;
    return (
      <DropdownMenuItem key={name} onSelect={() => pick(name)} className="min-h-11 gap-3 rounded-lg text-base" data-mode={name}>
        <I className={cn("h-5 w-5 shrink-0", on ? "text-teal-700" : "text-stone-500")} aria-hidden />
        <span className={cn("min-w-0 flex-1 truncate", on && "font-semibold")}>{d.label || modeLabel(name, g.modes)}</span>
        {going === name ? <Spinner /> : on ? <Check className="h-4 w-4 shrink-0 text-teal-700" aria-label="on now" /> : null}
      </DropdownMenuItem>
    );
  };

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        data-testid="header-mode"
        aria-label={`Mode: ${label}${going ? " (switching)" : live ? " (live)" : ""}. Change mode`}
        title={err ?? (going ? `Switching to ${label}` : `${label}${live ? " · live" : ""}: change mode`)}
        className={cn("group grid h-11 shrink-0 place-items-center rounded-full px-0.5 focus-visible:outline-none", className)}
      >
        <span
          className={cn(
            "inline-flex h-9 items-center gap-1.5 rounded-full border text-[15px] font-medium shadow-sm group-hover:brightness-[0.98] group-focus-visible:ring-2 group-focus-visible:ring-ring",
            iconOnly ? "w-9 justify-center" : "pl-3 pr-2",
            err
              ? "border-rose-300 bg-rose-50 text-rose-900"
              : going
                ? "border-amber-300 bg-amber-50 text-amber-900"
                : live
                  ? "border-red-200 bg-red-50 text-red-800"
                  : "border-stone-200/80 bg-card text-stone-800",
          )}
        >
          {going ? <Spinner className="h-[18px] w-[18px]" /> : <Icon className="h-[18px] w-[18px] shrink-0" aria-hidden />}
          {!iconOnly && <span className="max-w-[9rem] truncate">{label}</span>}
          {live && !iconOnly && <span aria-hidden className="h-2 w-2 shrink-0 rounded-full bg-red-600 motion-safe:animate-pulse" />}
          {!iconOnly && <ChevronDown className="h-4 w-4 shrink-0 opacity-60" aria-hidden />}
        </span>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className={cn(THEME_CLASS, "w-64 rounded-2xl p-1.5")}>
        <DropdownMenuLabel className="px-2 pb-1 pt-1.5 text-sm font-semibold text-muted-foreground">
          {live ? "Room mode · live" : "Room mode"}
        </DropdownMenuLabel>
        {err && <p className="px-2 pb-1.5 text-sm leading-snug text-rose-800">{err}</p>}
        {main.map(item)}
        {extra.length > 0 && <DropdownMenuSeparator />}
        {extra.map(item)}
        <DropdownMenuSeparator />
        <DropdownMenuItem asChild className="min-h-11 gap-3 rounded-lg text-base text-teal-800">
          <AppLink href={`${roomPageUrl()}#modes`}>
            <SlidersHorizontal className="h-5 w-5 shrink-0" aria-hidden /> Room page
          </AppLink>
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
