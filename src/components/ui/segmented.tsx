// One choice of a few (a view, a layout, whose pictures), the app's one way of showing which is chosen (owner, 24 Nov:
// black on white "too prominent and distracts"). It is the same as the places in the top bar (AppNav): a soft stone
// track, with the chosen option raised on it as a white chip in teal. Filters that switch on and off (the tag chips) stay
// solid teal, because a pale "on" there read as off (owner, 28 Oct).
//
// `compact`: on a phone, options that have an icon show only the icon, with the label kept as their name.
import type { ComponentType, ReactNode } from 'react';
import { cn } from '@/lib/utils';

export interface SegmentedOption<T extends string> {
  value: T;
  label: ReactNode;
  icon?: ComponentType<{ className?: string }>;
  /** the label as text, where it isn't (the name of an icon-only option) */
  name?: string;
  title?: string;
}

export function Segmented<T extends string>({ value, onChange, options, ariaLabel, compact = false, className }: {
  value: T;
  onChange: (value: T) => void;
  options: readonly SegmentedOption<T>[];
  ariaLabel: string;
  compact?: boolean;
  className?: string;
}) {
  return (
    <div role="group" aria-label={ariaLabel}
      className={cn('inline-flex shrink-0 items-center gap-0.5 rounded-full bg-stone-200/70 p-0.5', className)}>
      {options.map((o) => {
        const on = o.value === value;
        const Icon = o.icon;
        const iconOnly = compact && !!Icon;
        return (
          <button
            key={o.value}
            type="button"
            aria-pressed={on}
            aria-label={iconOnly ? o.name ?? (typeof o.label === 'string' ? o.label : undefined) : undefined}
            title={o.title}
            onClick={() => onChange(o.value)}
            className={cn(
              'inline-flex h-8 items-center gap-1.5 whitespace-nowrap rounded-full px-3 text-sm transition-colors',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
              iconOnly && 'px-2.5 sm:px-3',
              on ? 'bg-white font-medium text-teal-800 shadow-sm ring-1 ring-stone-200' : 'text-stone-600 hover:text-stone-900',
            )}
          >
            {Icon && <Icon className="h-4 w-4 shrink-0" />}
            <span className={iconOnly ? 'hidden sm:inline' : undefined}>{o.label}</span>
          </button>
        );
      })}
    </div>
  );
}
