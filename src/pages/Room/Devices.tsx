// The one way a room's devices are shown, whatever they are (lamps, effects such as the glitterball, and whatever
// comes next): a DeviceSection per kind (a heading, an optional badge, one card) holding a DeviceRow per device.
// Rolled up (the default) a row shows the device's visual, its name, a status pill and one line of context; unrolling
// it shows its controls. Each kind only supplies those pieces, so every device looks and behaves the same.

import { useId, type ReactNode } from "react";
import { cn } from "@/lib/utils";
import { Pill, Unroll } from "./bits";
import { CARD_CLASS } from "./theme";
import type { Tone } from "./tones";

export interface DeviceStatus {
  tone: Tone;
  text: string;
  pulse?: boolean;
}

/** A round visual at the start of a row: a lamp's colour swatch, an effect's icon. */
export function DeviceDisc({
  active,
  children,
  style,
  className,
}: {
  active?: boolean;
  children?: ReactNode;
  style?: React.CSSProperties;
  className?: string;
}) {
  return (
    <span
      aria-hidden
      style={style}
      className={cn(
        "relative grid h-11 w-11 shrink-0 place-items-center rounded-full border",
        active ? "border-teal-300 bg-teal-50 text-teal-700" : "border-stone-300/80 bg-stone-100 text-stone-500",
        className,
      )}
    >
      {children}
    </span>
  );
}

export function DeviceRow({
  testId,
  visual,
  name,
  status,
  summary,
  defaultOpen = false,
  children,
}: {
  testId?: string;
  visual: ReactNode;
  name: ReactNode;
  status?: DeviceStatus | null;
  /** One line under the name, e.g. where it is or when it's on. */
  summary?: ReactNode;
  defaultOpen?: boolean;
  /** The controls, shown when unrolled. */
  children: ReactNode;
}) {
  return (
    <li className="py-1" data-testid={testId}>
      <Unroll
        defaultOpen={defaultOpen}
        title={
          <span className="flex min-w-0 flex-1 items-center gap-3 py-1">
            {visual}
            <span className="min-w-0 flex-1">
              <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5">
                <span className="truncate text-[17px] font-semibold">{name}</span>
                {status && (
                  <Pill tone={status.tone} pulse={status.pulse}>
                    {status.text}
                  </Pill>
                )}
              </span>
              {summary && <span className="block text-sm font-normal leading-snug text-muted-foreground">{summary}</span>}
            </span>
          </span>
        }
      >
        <div className="space-y-4 px-1 pb-3">{children}</div>
      </Unroll>
    </li>
  );
}

export function DeviceSection({
  title,
  badge,
  sub = false,
  children,
}: {
  title: string;
  /** Shown at the right of the heading (e.g. whether Maestro is sending). */
  badge?: ReactNode;
  /** Inside a room's section: a smaller heading under the room name. */
  sub?: boolean;
  /** DeviceRows, or a line saying why there are none yet. */
  children: ReactNode;
}) {
  const headingId = useId();
  return (
    <section aria-labelledby={headingId}>
      <div className="mb-2 flex items-center justify-between gap-2">
        {sub ? (
          <h3 id={headingId} className="text-base font-semibold text-stone-700">
            {title}
          </h3>
        ) : (
          <h2 id={headingId} className="text-lg font-semibold text-stone-800">
            {title}
          </h2>
        )}
        {badge}
      </div>
      <div className={cn(CARD_CLASS, "px-3 py-2 sm:px-4")}>{children}</div>
    </section>
  );
}

/** The list inside a DeviceSection's card. */
export function DeviceList({ children }: { children: ReactNode }) {
  return <ul className="divide-y divide-stone-100">{children}</ul>;
}
