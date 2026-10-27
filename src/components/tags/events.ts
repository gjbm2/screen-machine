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
