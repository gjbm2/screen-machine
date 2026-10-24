// The calm header: the room's name, the Mic and AI switches (TopToggles.tsx, owner 24 Oct) with their state in words
// on the line under them, a connection dot with one word (at phone width, while all is well, just a dot on the menu
// button), and the menu button (the app's shared menu, with this room's details). Tapping the status opens the menu too,
// whose room section explains it in a full sentence (phones can't show tooltips).
// Under it: the way to the generator (on this screen's tab when opened for one screen,
// /room?screen=<id>, the TVs' QR codes) and the renderer's Library.

import { useState } from "react";
import { Library, Menu, Sparkles } from "lucide-react";
import { generatorUrl } from "@/components/nav/appNav";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { Dot } from "./bits";
import type { LinkState } from "./connection";
import { friendlyName } from "./words";
import { defaultRoomUrl } from "./api";
import type { PcLoad } from "./usePcLoad";
import { TopToggleButtons, TopToggleLine, type TopToggles } from "./TopToggles";

const PILL =
  "inline-flex h-9 items-center gap-2 rounded-full border border-stone-200/80 bg-card px-3 text-[15px] font-medium text-stone-700 shadow-sm group-hover:bg-stone-50 group-focus-visible:ring-2 group-focus-visible:ring-ring";

/** The renderer's Library and console, through room_service's /renderer pass-through (LAN door). */
const libraryHref = () => `${defaultRoomUrl()}/renderer/dev`;

export function Header({
  link,
  onMenu,
  screen,
  toggles,
  onDetails,
  pc,
}: {
  link: LinkState;
  onMenu: () => void;
  screen?: string | null;
  /** The Mic and AI switches (the room mic in use shows its red dot: owner, 16 Oct: always know, anywhere on the page). */
  toggles?: TopToggles;
  /** Opens the Listening details (a tap on the switches' line, or a long press on a switch). */
  onDetails?: () => void;
  /** Other work is overloading the PC (owner, 22 Oct): a "PC busy" badge; tapping it says what it costs. */
  pc?: PcLoad | null;
}) {
  const [pcOpen, setPcOpen] = useState(false);
  const pcBusy = !!pc?.busy;
  // At phone width the switches need the room: "Connected" is just its dot there; any other state keeps its word.
  const quietLink = !!toggles && link.tone === "ok";
  const pcBadge = (cls?: string) =>
    pcBusy && (
      <button
        type="button"
        data-testid="pc-busy-badge"
        onClick={() => setPcOpen((o) => !o)}
        aria-expanded={pcOpen}
        title={pc?.sentence}
        className={cn(
          "inline-flex shrink-0 items-center gap-1.5 rounded-full border border-amber-200 bg-amber-50 px-2.5 py-1 text-sm font-semibold text-amber-900",
          cls,
        )}
      >
        <span aria-hidden className="h-2 w-2 rounded-full bg-amber-500" />
        PC busy
      </button>
    );
  return (
    <header className="pb-1 pt-3">
      <div className="flex flex-wrap items-center gap-x-1">
        <h1 className="mr-auto text-[28px] font-semibold leading-tight tracking-tight">Room</h1>
        {/* at phone width with the switches, "PC busy" sits on the line under them */}
        {pcBadge(toggles ? "max-sm:hidden" : undefined)}
        {toggles && <TopToggleButtons t={toggles} onDetails={onDetails} />}
        <span className="flex shrink-0 items-center">
          <button
            type="button"
            onClick={onMenu}
            aria-label={`${link.word}: ${link.sentence} Open settings and details.`}
            title={link.sentence}
            data-testid="conn-pill"
            className={cn("group grid h-11 place-items-center rounded-full px-0.5 focus-visible:outline-none", quietLink && "max-sm:hidden")}
          >
            <span className={PILL}>
              <Dot tone={link.tone} pulse={link.pulse} className="h-2.5 w-2.5" />
              <span data-testid="conn-state">{link.word}</span>
            </span>
          </button>
          <Button
            variant="ghost"
            size="icon"
            onClick={onMenu}
            aria-label={quietLink ? `Menu: settings and details. ${link.sentence}` : "Menu: settings and details"}
            data-testid="menu-button"
            className="relative -mr-2 h-11 w-11 rounded-full hover:bg-stone-200/60 [&_svg]:size-6"
          >
            <Menu />
            {/* phone width, all well: "Connected" is this dot on the menu (the menu says it in full) */}
            {quietLink && <Dot tone={link.tone} className="absolute right-1.5 top-2 h-2.5 w-2.5 ring-2 ring-background sm:hidden" />}
          </Button>
        </span>
      </div>
      {toggles && (
        <div className="flex min-w-0 items-center gap-2">
          {pcBadge("sm:hidden")}
          <div className="min-w-0 flex-1">
            <TopToggleLine t={toggles} onDetails={onDetails} />
          </div>
        </div>
      )}
      {pcBusy && pcOpen && (
        <p data-testid="pc-busy-sentence" className="pb-1 text-[15px] leading-snug text-amber-900">
          {pc?.sentence}
        </p>
      )}
      <nav
        aria-label="Elsewhere in the app"
        data-testid="screen-links"
        className="flex flex-wrap items-center gap-x-1 text-[15px]"
      >
        <a
          href={generatorUrl(screen)}
          title={screen ? `${friendlyName(screen)} in the generator` : "The generator: make art, choose what each screen shows"}
          data-testid="generator-link"
          className="group grid h-11 place-items-center rounded-full px-0.5 focus-visible:outline-none"
        >
          <span className={PILL}>
            <Sparkles className="h-4 w-4 text-teal-700" aria-hidden /> {screen ? `${friendlyName(screen)} in the generator` : "Generator"}
          </span>
        </a>
        <a href={libraryHref()} className="group grid h-11 place-items-center rounded-full px-0.5 focus-visible:outline-none">
          <span className={PILL}>
            <Library className="h-4 w-4 text-stone-500" aria-hidden /> Library
          </span>
        </a>
      </nav>
    </header>
  );
}
