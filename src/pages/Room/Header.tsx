// The calm header: the room's name, a connection dot with one word, and the
// menu button (the app's shared menu, with this room's details). Tapping the status opens the menu too,
// whose room section explains it in a full sentence (phones can't show tooltips).
// Under it: the way to the generator (on this screen's tab when opened for one screen,
// /room?screen=<id>, the TVs' QR codes) and the renderer's Library.

import { Library, Menu, Sparkles } from "lucide-react";
import { generatorUrl } from "@/components/nav/appNav";
import { Button } from "@/components/ui/button";
import { Dot } from "./bits";
import type { LinkState } from "./connection";
import { friendlyName } from "./words";
import { defaultRoomUrl } from "./api";

const PILL =
  "inline-flex h-9 items-center gap-2 rounded-full border border-stone-200/80 bg-card px-3 text-[15px] font-medium text-stone-700 shadow-sm group-hover:bg-stone-50 group-focus-visible:ring-2 group-focus-visible:ring-ring";

/** The renderer's Library and console, through room_service's /renderer pass-through (LAN door). */
const libraryHref = () => `${defaultRoomUrl()}/renderer/dev`;

export function Header({
  link,
  onMenu,
  screen,
  micOn,
}: {
  link: LinkState;
  onMenu: () => void;
  screen?: string | null;
  /** The room mic is listening (owner, 16 Oct: always know, anywhere on the page). */
  micOn?: boolean;
}) {
  return (
    <header className="pb-1 pt-3">
      <div className="flex items-center gap-1">
        <h1 className="mr-auto text-[28px] font-semibold leading-tight tracking-tight">Room</h1>
        {micOn && (
          <span
            data-testid="mic-on-badge"
            title="The room mic is listening"
            className="inline-flex items-center gap-1.5 rounded-full border border-rose-200 bg-rose-50 px-2.5 py-1 text-sm font-semibold text-rose-800"
          >
            <span aria-hidden className="h-2 w-2 rounded-full bg-rose-600 motion-safe:animate-pulse" />
            Mic on
          </span>
        )}
        <button
          type="button"
          onClick={onMenu}
          aria-label={`${link.word}: ${link.sentence} Open settings and details.`}
          title={link.sentence}
          data-testid="conn-pill"
          className="group grid h-11 place-items-center rounded-full px-0.5 focus-visible:outline-none"
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
          aria-label="Menu: settings and details"
          data-testid="menu-button"
          className="-mr-2 h-11 w-11 rounded-full hover:bg-stone-200/60 [&_svg]:size-6"
        >
          <Menu />
        </Button>
      </div>
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
