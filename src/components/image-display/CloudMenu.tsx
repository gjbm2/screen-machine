// The Cloud tab's menu (owner, 23 Nov): the cloud stores this viewer may see, i.e. the house's Cloud, Private while
// it is unlocked, and each person's own (an admin sees everyone's; anyone else only their own, and gets no menu).
import * as LucideIcons from 'lucide-react';
import { Check, Image, Lock } from 'lucide-react';
import type { PublishDestination } from '@/shared/api';

export function CloudMenu({ at, clouds, current, unlockedUntil, onPick, onLock, onClose }: {
  /** The Cloud tab's box: the menu drops from it. */
  at: DOMRect;
  clouds: PublishDestination[];
  current: string | undefined;
  /** While the hidden buckets are open (epoch seconds), the menu offers to lock them. */
  unlockedUntil: number | null | undefined;
  onPick: (id: string) => void;
  onLock: () => void;
  onClose: () => void;
}) {
  const left = Math.max(8, Math.min(at.left, window.innerWidth - 248));
  const minutes = unlockedUntil ? Math.max(1, Math.ceil((unlockedUntil * 1000 - Date.now()) / 60000)) : 0;
  return (
    <>
      <div className="fixed inset-0 z-[60]" onClick={onClose} />
      <div
        role="menu"
        className="fixed z-[61] w-60 rounded-lg border border-stone-200 bg-white py-1 shadow-lg"
        style={{ top: at.bottom + 4, left }}
      >
        {clouds.map((c) => {
          const Icon = (c.icon && (LucideIcons as any)[c.icon]) || Image;
          return (
            <button
              key={c.id}
              role="menuitem"
              onClick={() => onPick(c.id)}
              className="flex min-h-11 w-full items-center gap-2 px-3 text-left text-[15px] hover:bg-stone-50"
            >
              <Icon className="h-4 w-4 shrink-0 text-stone-500" />
              <span className="flex-1 truncate">{c.name}</span>
              {c.id === current && <Check className="h-4 w-4 text-teal-700" />}
            </button>
          );
        })}
        {unlockedUntil ? (
          <button
            role="menuitem"
            onClick={onLock}
            className="flex min-h-11 w-full items-center gap-2 border-t border-stone-100 px-3 text-left text-[15px] text-stone-600 hover:bg-stone-50"
          >
            <Lock className="h-4 w-4 shrink-0" />
            <span className="flex-1">Lock now</span>
            <span className="text-xs text-stone-400">{minutes} min left</span>
          </button>
        ) : null}
      </div>
    </>
  );
}
