// The Room page's header: the app's one header (src/components/nav/PageHeader.tsx) with this page's own parts: the
// Mic and AI switches (TopToggles.tsx, owner 24 Oct), the status line saying what the room is doing (a tap opens the
// Listening fold), the room link's state (nothing while all is well; the menu's room section says it in a full
// sentence), and "PC busy". The generator and the Library are in the places tabs and the menu; a screen's own generator
// link is in its card.

import { PageHeader } from "@/components/nav/PageHeader";
import type { LinkState } from "./connection";
import type { PcLoad } from "./usePcLoad";
import type { TopToggles } from "./TopToggles";

export function Header({
  link,
  onMenu,
  toggles,
  onDetails,
  pc,
  line,
}: {
  link: LinkState;
  onMenu: () => void;
  /** The screen the page was opened for (/room?screen=<id>); its links are in its card. */
  screen?: string | null;
  /** The Mic and AI switches (the room mic in use shows its red dot: owner, 16 Oct: always know, anywhere on the page). */
  toggles?: TopToggles;
  /** Opens the Listening details (the switches' "Listening details ›", or a long press on a switch). */
  onDetails?: () => void;
  /** Other work is overloading the PC (owner, 22 Oct): a "PC busy" badge; tapping it says what it costs. */
  pc?: PcLoad | null;
  /** What the room is doing, the header's status line ("Normal · art on all 3 screens · lights off · nothing playing"). */
  line?: string | null;
}) {
  return <PageHeader title="Room" current="room" onMenu={onMenu} toggles={toggles ?? null} onDetails={onDetails} status={link} pc={pc} line={line} />;
}
