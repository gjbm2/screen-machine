// The Tags view's chip bar: ★ Favourites, then the bucket's tag words with counts, then Untagged. Tapping chips
// narrows to images carrying all of them. One scrolling line on a phone, wrapping on a wide screen.
import { useState } from 'react';
import { Check, Star, X } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { TagChip } from './tagIndex';

/** TagPicker's chip shape (Room page). A chosen chip is filled solid with a tick (owner, 28 Oct: the pale teal on-state
 *  was too like the white off-state to see what a multi-select had chosen). */
const CHIP = 'inline-flex min-h-9 shrink-0 items-center gap-1 rounded-full border px-3 text-[15px] transition-colors';
const CHIP_ON = 'border-teal-700 bg-teal-700 font-semibold text-white shadow-sm hover:bg-teal-800';
const CHIP_OFF = 'border-stone-200 bg-white hover:bg-stone-50';
const FIRST_CHIPS = 14;

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
  // The first FIRST_CHIPS (★, then subjects, by count) plus any chosen; the rest behind "More" so a fully tagged
  // bucket's 40-odd words don't bury the pictures
  const [all, setAll] = useState(false);
  if (!chips.length) {
    return <p className="px-1 py-2 text-sm text-muted-foreground">No tags yet: new images are tagged a minute or so after they’re saved.</p>;
  }
  const shown = all ? chips : chips.filter((c, i) => i < FIRST_CHIPS || chosen.includes(c.key) || c.kind === 'untagged');
  const hidden = chips.length - shown.length;
  return (
    <div className="-mx-1 flex gap-1.5 overflow-x-auto px-1 py-1 sm:flex-wrap sm:overflow-visible [scrollbar-width:none]">
      {shown.map((c) => {
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
              c.kind === 'favourite' && on && 'border-amber-600 bg-amber-600 hover:bg-amber-700',
              c.kind === 'untagged' && !on && 'italic text-stone-600',
            )}
          >
            {on && <Check className="h-4 w-4" strokeWidth={3} aria-hidden />}
            {c.kind === 'favourite' && (
              <Star className={cn('h-3.5 w-3.5', on ? 'fill-white text-white' : 'fill-amber-400 text-amber-500')} aria-hidden />
            )}
            {c.label}{' '}
            <span className={cn('text-sm font-normal', on ? 'text-white/80' : 'text-muted-foreground')}>{c.count}</span>
          </button>
        );
      })}
      {(hidden > 0 || all) && (
        <button type="button" onClick={() => setAll(!all)} className={cn(CHIP, CHIP_OFF, 'text-stone-600')}>
          {all ? 'Fewer' : `More (${hidden})`}
        </button>
      )}
      {/* last, so nothing moves under the pointer when a chip is chosen (owner, 28 Oct: "x All" appearing on the
          left pushed the chip just tapped away) */}
      {chosen.length > 0 && (
        <button type="button" onClick={onClear} className={cn(CHIP, CHIP_OFF, 'text-stone-600')} aria-label="Clear chosen tags">
          <X className="h-3.5 w-3.5" aria-hidden />
          Clear
        </button>
      )}
    </div>
  );
}
