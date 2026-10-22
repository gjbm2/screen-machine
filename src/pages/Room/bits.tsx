// Small presentational pieces shared by the Room page's panels.

import { useEffect, useRef, useState, type ReactNode } from "react";
import { ChevronDown, Loader2 } from "lucide-react";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";
import { DOT_CLASSES, TEXT_CLASSES, TONE_CLASSES, type Tone } from "./tones";

export function Dot({ tone = "muted", pulse = false, className }: { tone?: Tone; pulse?: boolean; className?: string }) {
  return (
    <span
      aria-hidden
      className={cn("inline-block h-2 w-2 shrink-0 rounded-full", DOT_CLASSES[tone], pulse && "motion-safe:animate-pulse", className)}
    />
  );
}

export function Spinner({ className }: { className?: string }) {
  return <Loader2 aria-hidden className={cn("h-4 w-4 shrink-0 motion-safe:animate-spin", className)} />;
}

export function Pill({
  tone = "muted",
  pulse = false,
  className,
  title,
  children,
}: {
  tone?: Tone;
  pulse?: boolean;
  className?: string;
  title?: string;
  children: ReactNode;
}) {
  return (
    <span
      title={title}
      className={cn(
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 py-0.5 text-[13px] font-medium",
        TONE_CLASSES[tone],
        className,
      )}
    >
      <Dot tone={tone} pulse={pulse} className="h-1.5 w-1.5" />
      {children}
    </span>
  );
}

export function Stat({ label, value, tone, hint }: { label: string; value: ReactNode; tone?: Tone; hint?: string }) {
  return (
    <div className="min-w-0 rounded-xl bg-stone-50 px-2.5 py-2" title={hint}>
      <div className="truncate text-xs text-muted-foreground">{label}</div>
      <div className={cn("truncate font-mono text-sm tabular-nums", tone && TEXT_CLASSES[tone])}>{value}</div>
    </div>
  );
}

/** A label/value row for the technical readout. */
export function KV({ k, children }: { k: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-muted-foreground">{k}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </>
  );
}

export function SectionTitle({ children, right, className }: { children: ReactNode; right?: ReactNode; className?: string }) {
  return (
    <div className={cn("mb-2 flex min-h-8 items-center justify-between gap-2", className)}>
      <h3 className="text-[15px] font-semibold text-foreground">{children}</h3>
      {right}
    </div>
  );
}

export function Notice({ tone = "warn", children, className }: { tone?: Tone; children: ReactNode; className?: string }) {
  return (
    <div className={cn("rounded-xl border px-3.5 py-2.5 text-sm leading-relaxed", TONE_CLASSES[tone], className)}>{children}</div>
  );
}

/**
 * A titled section that unrolls in place. Its content is unmounted while closed, so
 * anything expensive inside (snapshots, long forms) costs nothing until opened.
 */
export function Unroll({
  title,
  hint,
  defaultOpen = false,
  children,
  className,
  contentClassName,
  testId,
  revealOnOpen = false,
}: {
  title: ReactNode;
  hint?: ReactNode;
  defaultOpen?: boolean;
  children: ReactNode;
  className?: string;
  contentClassName?: string;
  testId?: string;
  /** Scroll it to the top of its scroll area when opened (for a tall section near the bottom of a sheet). */
  revealOnOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const root = useRef<HTMLDivElement>(null);
  const opened = useRef(false);
  useEffect(() => {
    if (!open || !opened.current) return;
    opened.current = false;
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    root.current?.scrollIntoView({ block: "start", behavior: reduce ? "auto" : "smooth" });
  }, [open]);
  const onOpenChange = (o: boolean) => {
    opened.current = o && revealOnOpen;
    setOpen(o);
  };
  return (
    <Collapsible ref={root} open={open} onOpenChange={onOpenChange} className={className} data-testid={testId}>
      <CollapsibleTrigger className="flex min-h-11 w-full items-center gap-2 rounded-xl px-1 text-left text-[15px] font-medium text-foreground hover:bg-stone-100/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
        <span className={hint ? "shrink-0" : "min-w-0 flex-1"}>{title}</span>
        {hint && <span className="min-w-0 flex-1 truncate text-right text-sm font-normal text-muted-foreground">{hint}</span>}
        <ChevronDown
          aria-hidden
          className={cn("h-4 w-4 shrink-0 text-muted-foreground transition-transform motion-reduce:transition-none", open && "rotate-180")}
        />
      </CollapsibleTrigger>
      <CollapsibleContent className={cn("pt-2", contentClassName)}>{children}</CollapsibleContent>
    </Collapsible>
  );
}
