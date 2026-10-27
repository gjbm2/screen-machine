// The Tags view's chip bar: ★ Favourites, then the bucket's tag words with counts, then Untagged. Tapping chips
// narrows to images carrying all of them. One scrolling line on a phone, wrapping on a wide screen.
import { Star, X } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { TagChip } from './tagIndex';

/** TagPicker's on/off chip classes (Room page), the house pattern. */
const CHIP = 'inline-flex min-h-9 shrink-0 items-center gap-1 rounded-full border px-3 text-[15px] transition-colors';
const CHIP_ON = 'border-teal-300 bg-teal-50 font-semibold text-teal-900';
const CHIP_OFF = 'border-stone-200 bg-white hover:bg-stone-50';

export function TagFilterBar({
  chips,
  chosen,
  onToggle,
  onClear,
}: {
  chips: TagChip[];
  chosen: string[];
  onToggle: (key: string) => void;
  onClear: () => void;
}) {
  if (!chips.length) {
    return <p className="px-1 py-2 text-sm text-muted-foreground">No tags yet: new images are tagged a minute or so after they’re saved.</p>;
  }
  return (
    <div className="-mx-1 flex gap-1.5 overflow-x-auto px-1 py-1 sm:flex-wrap sm:overflow-visible [scrollbar-width:none]">
      {chosen.length > 0 && (
        <button type="button" onClick={onClear} className={cn(CHIP, CHIP_OFF, 'text-stone-600')} aria-label="Show all tags">
          <X className="h-3.5 w-3.5" aria-hidden />
          All
        </button>
      )}
      {chips.map((c) => {
        const on = chosen.includes(c.key);
        return (
          <button
            key={c.key}
            type="button"
            aria-pressed={on}
            onClick={() => onToggle(c.key)}
            className={cn(
              CHIP,
              on ? CHIP_ON : CHIP_OFF,
              c.kind === 'favourite' && !on && 'border-amber-300 bg-amber-50 text-amber-900 hover:bg-amber-100',
              c.kind === 'untagged' && !on && 'italic text-stone-600',
            )}
          >
            {c.kind === 'favourite' && <Star className="h-3.5 w-3.5 fill-amber-400 text-amber-500" aria-hidden />}
            {c.label} <span className="text-sm font-normal text-muted-foreground">{c.count}</span>
          </button>
        );
      })}
    </div>
  );
}
