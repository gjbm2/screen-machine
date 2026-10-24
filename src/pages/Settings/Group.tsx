// One group of the Settings page (ia 24 Oct, §2): a fold with its title and a one-line summary while closed. Every
// group can be linked to as /settings#<id> (the "… settings ›" links at the foot of the Room page's folds): that group
// opens and scrolls into view, on arrival and whenever the link is followed again on this page. With no hash every
// group starts closed (the closed page reads as a table of contents). Open or closed is NOT remembered on this device:
// the Room page's folds remember theirs under the same ids ("look", "lights"), and Settings must not share them.
//
// A group may hold sub-groups (one per screen: #screen-<id>); a link to a sub-group opens its parent too.
//
// Row / ReadRow / LinkRow / Hint are the plain pieces every group is made of, so the groups read alike.

import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { ChevronDown, ExternalLink } from "lucide-react";
import { AppLink } from "@/components/nav/AppLink";
import { cn } from "@/lib/utils";

/** The event Room/Section.tsx's openFold() sends (AppLink sends it for a #<fold> link to the page already showing). */
const OPEN_EVENT = "sm-open-fold";

const hashId = (hash: string) => {
  try {
    return decodeURIComponent(hash.replace(/^#/, ""));
  } catch {
    return hash.replace(/^#/, "");
  }
};

function useFoldOpen(id: string, owns?: (id: string) => boolean) {
  const { hash } = useLocation();
  const target = hashId(hash);
  // owns is usually an inline function: read through a ref, so a re-render doesn't count as a new link
  const ownsRef = useRef(owns);
  ownsRef.current = owns;
  const matches = useCallback((t: string) => t === id || !!ownsRef.current?.(t), [id]);
  const [open, setOpen] = useState(() => !!target && matches(target));
  const ref = useRef<HTMLElement>(null);
  const reveal = useCallback(
    (t: string) => {
      setOpen(true);
      // only the exact group scrolls (a parent opened for its sub-group leaves the scroll to it)
      if (t !== id) return;
      const smooth = !window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
      requestAnimationFrame(() => requestAnimationFrame(() => ref.current?.scrollIntoView({ behavior: smooth ? "smooth" : "auto", block: "start" })));
    },
    [id],
  );
  // arriving with #id, or a link on this page changing the hash
  useEffect(() => {
    if (target && matches(target)) reveal(target);
  }, [target, matches, reveal]);
  // the same link followed again (the hash doesn't change)
  useEffect(() => {
    const on = (e: Event) => {
      const t = String((e as CustomEvent).detail ?? "");
      if (matches(t)) reveal(t);
    };
    window.addEventListener(OPEN_EVENT, on);
    return () => window.removeEventListener(OPEN_EVENT, on);
  }, [matches, reveal]);
  return { open, setOpen, ref };
}

export function Group({
  id,
  title,
  summary,
  icon,
  owns,
  children,
  sub = false,
}: {
  /** The #<id> it answers to. */
  id: string;
  title: ReactNode;
  /** One plain line shown while closed. */
  summary?: ReactNode;
  icon?: ReactNode;
  /** Other ids that open this group (its sub-groups'). */
  owns?: (id: string) => boolean;
  children: ReactNode;
  /** A group inside a group (a screen inside Screens): smaller, in a card. */
  sub?: boolean;
}) {
  const { open, setOpen, ref } = useFoldOpen(id, owns);
  const hid = useId();
  const cid = useId();
  return (
    <section
      ref={ref}
      id={id}
      aria-labelledby={hid}
      data-testid={`settings-${id}`}
      className={cn(
        "scroll-mt-[calc(var(--app-top,0px)+0.5rem)]",
        sub ? "rounded-2xl border border-stone-200/80 bg-card px-3 py-1" : "py-1.5",
      )}
    >
      <h2 className="m-0">
        <button
          type="button"
          id={hid}
          aria-expanded={open}
          aria-controls={cid}
          onClick={() => setOpen(!open)}
          className="flex min-h-14 w-full items-center gap-3 rounded-xl px-1 py-2 text-left hover:bg-stone-100/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {icon && !sub && (
            <span aria-hidden className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-stone-100 text-stone-600 [&_svg]:h-5 [&_svg]:w-5">
              {icon}
            </span>
          )}
          <span className="min-w-0 flex-1">
            <span className={cn("block font-semibold leading-snug text-stone-800", sub ? "text-base" : "text-lg")}>{title}</span>
            {!open && summary && (
              <span className="block truncate text-[15px] font-normal leading-snug text-muted-foreground" data-testid={`settings-${id}-summary`}>
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
      <div id={cid} hidden={!open} className={cn("space-y-4 pb-4 pt-1", !sub && "px-1")}>
        {open && children}
      </div>
    </section>
  );
}

/** A small heading inside a group. */
export function Sub({ children, className }: { children: ReactNode; className?: string }) {
  return <h3 className={cn("pt-1 text-sm font-semibold text-muted-foreground", className)}>{children}</h3>;
}

/** A labelled setting: the words on the left (with a line under them), the control on the right or under them. */
export function Row({
  label,
  hint,
  children,
  stack = false,
  testId,
}: {
  label: ReactNode;
  hint?: ReactNode;
  children?: ReactNode;
  /** The control goes under the words (a wide one: a slider, a picker). */
  stack?: boolean;
  testId?: string;
}) {
  return (
    <div className={cn("flex gap-x-3 gap-y-2", stack ? "flex-col" : "flex-wrap items-center")} data-testid={testId}>
      <div className={cn("min-w-0", !stack && "flex-1 basis-48")}>
        <p className="text-base font-medium leading-snug text-foreground">{label}</p>
        {hint && <p className="text-sm leading-snug text-muted-foreground">{hint}</p>}
      </div>
      {children !== undefined && <div className={cn("min-w-0", stack ? "w-full" : "shrink-0")}>{children}</div>}
    </div>
  );
}

/** A value shown, not set here. */
export function ReadRow({ label, value, hint, testId }: { label: ReactNode; value: ReactNode; hint?: ReactNode; testId?: string }) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5" data-testid={testId}>
      <p className="min-w-0 flex-1 basis-40 text-[15px] leading-snug text-muted-foreground">{label}</p>
      <p className="min-w-0 max-w-full break-words text-right text-[15px] font-medium leading-snug text-foreground sm:max-w-[60%]">{value}</p>
      {hint && <p className="basis-full text-sm leading-snug text-muted-foreground">{hint}</p>}
    </div>
  );
}

/** A link to where a thing is set up or used ("Set up lights ›"); `newTab` for the LAN tools. */
export function LinkRow({ href, children, newTab, state }: { href: string; children: ReactNode; newTab?: boolean; state?: unknown }) {
  return (
    <AppLink
      href={href}
      newTab={newTab}
      state={state}
      className="inline-flex min-h-11 items-center gap-1 rounded-md text-[15px] font-medium text-teal-800 underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      {children}
      {newTab ? <ExternalLink className="h-3.5 w-3.5 text-stone-400" aria-hidden /> : <span aria-hidden>›</span>}
    </AppLink>
  );
}

/** A plain line of explanation. */
export function Hint({ children, className }: { children: ReactNode; className?: string }) {
  return <p className={cn("text-[15px] leading-snug text-muted-foreground", className)}>{children}</p>;
}

/** Choice chips (one of a few), as the Room page's. */
export function Chips<T extends string>({
  value,
  options,
  onChange,
  label,
  testId,
}: {
  value: T | null;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
  label: string;
  testId?: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="flex flex-wrap gap-2" data-testid={testId}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          onClick={() => onChange(o.value)}
          className={cn(
            "inline-flex min-h-11 items-center rounded-full border px-4 text-[15px] font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            value === o.value ? "border-teal-700 bg-teal-700 text-white" : "border-stone-200 bg-white text-stone-700 hover:bg-stone-50",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
