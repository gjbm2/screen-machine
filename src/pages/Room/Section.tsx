// One fold of the Room page (owner, 24 Oct, layout A: "certainly too much going on today and not enough folded away").
// Every section of the page is one of these: its title, a one-line summary while closed (so the closed page reads as
// an overview), and a chevron. Open or closed is remembered per section on this device (localStorage, in try/catch:
// a private window or blocked storage just falls back to the default). Until the person taps it, a fold follows
// `defaultOpen`, which may change (Look opens while the screens are live).
//
// The content stays mounted while closed (only hidden), so what's inside keeps the state and readings it has today and
// the summaries above can come from it; anything expensive inside rolls up under its own Unroll as before.
//
// openFold(id) opens a fold and scrolls to it (the top bar's Mic and AI details); so does a link to /room#<id>.

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

const OPEN_EVENT = "sm-open-fold";

/** Opens the fold `id` on this page and scrolls it into view (remembered open, as a tap would). */
export function openFold(id: string) {
  window.dispatchEvent(new CustomEvent(OPEN_EVENT, { detail: id }));
}

export function Section({
  id,
  title,
  summary,
  badge,
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
  /** Shown at the right of the title (e.g. a Recording pill), open or closed. */
  badge?: ReactNode;
  defaultOpen?: boolean;
  /** Open whatever was remembered (e.g. /room?screen=<id> opens Screens). */
  forceOpen?: boolean;
  /** Left out of the page (still mounted, so what's inside keeps reading). */
  hidden?: boolean;
  children: ReactNode;
  className?: string;
}) {
  const [stored, setStored] = useState<boolean | null>(() => readFold(id));
  const open = forceOpen || (stored ?? defaultOpen);
  const hid = useId();
  const cid = useId();
  const toggle = () => {
    const next = !open;
    setStored(next);
    writeFold(id, next);
  };
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
    window.addEventListener(OPEN_EVENT, onOpen);
    if (window.location.hash === `#${id}`) show();
    return () => window.removeEventListener(OPEN_EVENT, onOpen);
  }, [id]);
  return (
    <section
      ref={ref}
      aria-labelledby={hid}
      data-testid={`fold-${id}`}
      hidden={hidden}
      className={cn("scroll-mt-[calc(var(--app-top,0px)+0.5rem)] py-1.5", className)}
    >
      <h2 className="m-0">
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
          {badge && <span className="shrink-0">{badge}</span>}
          <ChevronDown
            aria-hidden
            className={cn("h-5 w-5 shrink-0 text-muted-foreground transition-transform motion-reduce:transition-none", open && "rotate-180")}
          />
        </button>
      </h2>
      <div id={cid} hidden={!open} className="space-y-3 pb-3 pt-1">
        {children}
      </div>
    </section>
  );
}
