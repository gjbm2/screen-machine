export type GroupVariant = 'generated' | 'favourites' | 'dated';

export interface ImageMeta {
  /** Optional thumbnail.  Bucket images always have one; generated images may omit it. */
  urlThumb?: string;
  /** Full-resolution asset.  Used as thumbnail when urlThumb is absent. */
  urlFull: string;
  /** Key that identifies the prompt run that produced this image. */
  promptKey: string;
  /** Random seed used during generation (if applicable). */
  seed: number;
  /** ISO timestamp representing when the image was created. */
  createdAt: string;
  /** Whether user has marked this as a favourite.  Persisted server-side via MOVE_TO / favourite endpoints. */
  isFavourite: boolean;
  /** Type of media - 'image' or 'video' */
  mediaType?: 'image' | 'video';
  /** Room for any other metadata the FullscreenViewer or future features might need. */
  [extra: string]: unknown;
}

export interface ImageItem extends ImageMeta {
  /** Stable unique identifier.  For generated images this might be a UUID; for bucket items the filename usually suffices. */
  id: string;
  /** Optional unique key used for React key props - combines id with batchId to ensure no collisions */
  uniqueKey?: string;
  /** Raw URL for the image, used for direct access */
  raw_url?: string;
  /** Optional custom component to render instead of the image */
  customComponent?: React.ReactNode;
  /** Bucket ID where this image is stored (e.g., '_recent', 'my_bucket') */
  bucketId?: string;
  /** Reference images used for this generation */
  reference_images?: any[];
}

export interface ImageGroup {
  /** Unique id for the group – promptKey for generated, bucket name like "Today" etc. */
  id: string;
  /** Label rendered in the UI header. */
  label: string;
  /** Type of group which determines visuals / behaviour. */
  variant: GroupVariant;
  /** Ordered list of image ids contained in this group. */
  imageIds: string[];
  /** UI flag – true when the user collapsed the container during current session.  Not persisted. */
  collapsed: boolean;
}

export interface BucketImage {
  id: string;
  urlFull: string;
  urlThumb: string;
  raw_url?: string;
  promptKey: string;
  seed: number;
  isFavourite: boolean;
  createdAt: string;
  metadata?: ImageMeta;
  batchId?: string;
} 