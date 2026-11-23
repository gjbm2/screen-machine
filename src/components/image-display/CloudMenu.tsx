// The Cloud tab's menu (owner, 23 Nov): the cloud stores this viewer may see, i.e. the house's Cloud, Private while
// it is unlocked, and each person's own (an admin sees everyone's; anyone else only their own, and gets no menu).
// Held over during a drag (`dropping`), its rows are drop zones: the picture goes to the cloud store let go on.
import * as LucideIcons from 'lucide-react';
import { Check, Image, Lock } from 'lucide-react';
import { useDroppable } from '@dnd-kit/core';
import type { PublishDestination } from '@/shared/api';
import { DROP_ZONES } from '@/dnd/dropZones';

function CloudRow({ cloud, current, dropping, onPick }: {
  cloud: PublishDestination; current: boolean; dropping: boolean; onPick: (id: string) => void;
}) {
  // "#menu": not the tab's own zone, which has the same cloud's id (ImageDisplay strips it at the drop)
  const { setNodeRef, isOver } = useDroppable({ id: `${DROP_ZONES.TAB_PREFIX}${cloud.id}#menu`, disabled: !dropping });
  const Icon = (cloud.icon && (LucideIcons as any)[cloud.icon]) || Image;
  return (
    <button
      ref={setNodeRef}
      role="menuitem"
      onClick={() => onPick(cloud.id)}
      className={`flex min-h-11 w-full items-center gap-2 px-3 text-left text-[15px] hover:bg-stone-50
        ${isOver ? 'bg-teal-50 ring-2 ring-inset ring-teal-600' : ''}`}
    >
      <Icon className="h-4 w-4 shrink-0 text-stone-500" />
      <span className="flex-1 truncate">{cloud.name}</span>
      {current && !dropping && <Check className="h-4 w-4 text-teal-700" />}
    </button>
  );
}

export function CloudMenu({ at, clouds, current, unlockedUntil, dropping = false, onPick, onLock, onClose }: {
  /** The Cloud tab's box: the menu drops from it. */
  at: DOMRect;
  clouds: PublishDestination[];
  current: string | undefined;
  /** While the hidden buckets are open (epoch seconds), the menu offers to lock them. */
  unlockedUntil: number | null | undefined;
  /** Opened by a drag held over the tab: rows take the drop, and nothing covers the rest of the page. */
  dropping?: boolean;
  onPick: (id: string) => void;
  onLock: () => void;
  onClose: () => void;
}) {
  const left = Math.max(8, Math.min(at.left, window.innerWidth - 248));
  const minutes = unlockedUntil ? Math.max(1, Math.ceil((unlockedUntil * 1000 - Date.now()) / 60000)) : 0;
  return (
    <>
      {!dropping && <div className="fixed inset-0 z-[60]" onClick={onClose} />}
      <div
        role="menu"
        className="fixed z-[61] w-60 rounded-lg border border-stone-200 bg-white py-1 shadow-lg"
        style={{ top: at.bottom + 4, left }}
      >
        {clouds.map((c) => (
          <CloudRow key={c.id} cloud={c} current={c.id === current} dropping={dropping} onPick={onPick} />
        ))}
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
