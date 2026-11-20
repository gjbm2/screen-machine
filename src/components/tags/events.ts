// One image changed somewhere (the loupe's tags or star): views holding a copy of the bucket patch it in place
// instead of refetching (owner, 27 Oct: the grid went stale after a loupe edit).
import type { TaggingInfo } from '@/shared/api/types';

export const ITEM_UPDATED_EVENT = 'bucket:item-updated';

export type ItemPatch = { tags: string[]; tagging: TaggingInfo } | { favorite: boolean };

export interface ItemUpdatedDetail {
  bucket: string;
  filename: string;
  patch: ItemPatch;
}

export function notifyItemUpdated(detail: ItemUpdatedDetail): void {
  window.dispatchEvent(new CustomEvent<ItemUpdatedDetail>(ITEM_UPDATED_EVENT, { detail }));
}


/** A bucket item left its bucket (moved or deleted from the Loope): views holding it drop it (owner, 20 Nov: a moved
 *  picture stayed in Generated). */
export const ITEM_REMOVED_EVENT = 'bucket:item-removed';

export function notifyItemRemoved(detail: { bucket: string; filename: string }): void {
  window.dispatchEvent(new CustomEvent(ITEM_REMOVED_EVENT, { detail }));
}
