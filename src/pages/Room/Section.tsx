// One fold of the Room page (owner, 24 Oct, layout A: "certainly too much going on today and not enough folded away").
// Every section of the page is one of these: its title, a one-line summary while closed (so the closed page reads as
// an overview), and a chevron. Open or closed is remembered per section on this device (localStorage, in try/catch:
// a private window or blocked storage just falls back to the default). Until the person taps it, a fold follows
// `defaultOpen`, which may change (Look opens while the screens are live).
//
// The content stays mounted while closed (only hidden), so what's inside keeps the state and readings it has today and
// the summaries above can come from it; anything expensive inside rolls up under its own Unroll as before.
//
//   - `action`: a control at the right of the heading, outside the open/close button, so it works with the fold
//     closed (Piano's Record, Lights' "Set up lights ›").
//   - `foot`: the fold's last line, e.g. its "… settings ›" link to /settings#<group> (ia, 24 Oct).
//   - `forceOpen`: opens the fold once when it turns on (or its value changes: /room?screen=<id> passes the id), and
//     the person's taps work after that.
//
// openFold(id) opens a fold and scrolls to it (the top bar's Mic and AI details); so does a link to /room#<id>.
// setFold(id, open) opens or closes it without scrolling (the page does this when the room's state changes: Look on
// entering a live mode, Piano on a recording; the taps are remembered after that).

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

const KEY = (id: string) => `sm_room_fold_${id}`;

function readFold(id: string): boolean | null {
  try {
    const v = localStorage.getItem(KEY(id));
    return v === "1" ? true : v === "0" ? false : null;
  } catch {
    return null;
  }
}

function writeFold(id: string, open: boolean) {
  try {
    localStorage.setItem(KEY(id), open ? "1" : "0");
  } catch {
    /* not remembered on this device */
  }
}

/** detail: the fold's id (Settings/Group.tsx listens for the same event). */
const OPEN_EVENT = "sm-open-fold";
/** detail: {id, open}: no scroll. */
const SET_EVENT = "sm-set-fold";

/** Opens the fold `id` on this page and scrolls it into view (remembered open, as a tap would). */
export function openFold(id: string) {
  window.dispatchEvent(new CustomEvent(OPEN_EVENT, { detail: id }));
}

/** Opens or closes the fold `id` on this page where it is, without scrolling (remembered, as a tap would). */
export function setFold(id: string, open: boolean) {
  window.dispatchEvent(new CustomEvent(SET_EVENT, { detail: { id, open } }));
}

export function Section({
  id,
  title,
  summary,
  action,
  foot,
  defaultOpen = false,
  forceOpen = false,
  hidden = false,
  children,
  className,
}: {
  /** Names the fold for remembering it (and its test id). */
  id: string;
  title: ReactNode;
  /** One plain line shown while closed. */
  summary?: ReactNode;
  /** At the right of the heading, open or closed, outside the toggle (e.g. a Record pill, a setup link); a function
   *  gets whether the fold is open (Piano's Record pill shows only while closed, since the fold has its own). */
  action?: ReactNode | ((open: boolean) => ReactNode);
  /** The fold's last line while open (its "… settings ›" link). */
  foot?: ReactNode;
  defaultOpen?: boolean;
  /** Opens the fold once when truthy, again whenever the value changes (e.g. the screen id of /room?screen=<id>). */
  forceOpen?: boolean | string | null;
  /** Left out of the page (still mounted, so what's inside keeps reading). */
  hidden?: boolean;
  children: ReactNode;
  className?: string;
}) {
  const [stored, setStored] = useState<boolean | null>(() => (forceOpen ? true : readFold(id)));
  const open = stored ?? defaultOpen;
  const actionNode = typeof action === "function" ? action(open) : action;
  const hid = useId();
  const cid = useId();
  const toggle = () => {
    const next = !open;
    setStored(next);
    writeFold(id, next);
  };
  // forceOpen: once per value, then the toggle is the person's (not written down: it's this visit's)
  useEffect(() => {
    if (forceOpen) setStored(true);
  }, [forceOpen]);
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    const show = () => {
      setStored(true);
      writeFold(id, true);
      // after the content is shown, so the scroll lands on the opened fold
      requestAnimationFrame(() => ref.current?.scrollIntoView({ behavior: "smooth", block: "start" }));
    };
    const onOpen = (e: Event) => {
      if ((e as CustomEvent).detail === id) show();
    };
    const onSet = (e: Event) => {
      const d = (e as CustomEvent).detail as { id?: string; open?: boolean } | null;
      if (d?.id !== id || typeof d.open !== "boolean") return;
      setStored(d.open);
      writeFold(id, d.open);
    };
    window.addEventListener(OPEN_EVENT, onOpen);
    window.addEventListener(SET_EVENT, onSet);
    if (window.location.hash === `#${id}`) show();
    return () => {
      window.removeEventListener(OPEN_EVENT, onOpen);
      window.removeEventListener(SET_EVENT, onSet);
    };
  }, [id]);
  return (
    <section
      ref={ref}
      aria-labelledby={hid}
      data-testid={`fold-${id}`}
      hidden={hidden}
      className={cn("scroll-mt-[calc(var(--app-top,0px)+0.5rem)] py-1.5", className)}
    >
      <div className="flex items-center gap-2">
        <h2 className="m-0 min-w-0 flex-1">
          <button
            type="button"
            id={hid}
            aria-expanded={open}
            aria-controls={cid}
            onClick={toggle}
            className="flex min-h-14 w-full items-center gap-3 rounded-xl px-1 py-2 text-left hover:bg-stone-100/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <span className="min-w-0 flex-1">
              <span className="block text-lg font-semibold leading-snug text-stone-800">{title}</span>
              {!open && summary && (
                <span className="block truncate text-[15px] font-normal leading-snug text-muted-foreground" data-testid={`fold-${id}-summary`}>
                  {summary}
                </span>
              )}
            </span>
            <ChevronDown
              aria-hidden
              className={cn("h-5 w-5 shrink-0 text-muted-foreground transition-transform motion-reduce:transition-none", open && "rotate-180")}
            />
          </button>
        </h2>
        {actionNode && (
          <div className="shrink-0" data-testid={`fold-${id}-action`}>
            {actionNode}
          </div>
        )}
      </div>
      <div id={cid} hidden={!open} className="space-y-3 pb-3 pt-1">
        {children}
        {foot && <div className="flex flex-wrap items-center gap-x-4 px-1">{foot}</div>}
      </div>
    </section>
  );
}

/** A fold's foot link ("Look settings ›"): small, plain, same style everywhere on the page. */
export const FOOT_LINK =
  "inline-flex min-h-11 items-center gap-1 text-[15px] font-medium text-stone-600 underline-offset-4 hover:text-stone-900 hover:underline";
