// Shared request/response types for the Flask API.
// The `any`s are carried over unchanged from the old src/utils/api.ts; callers read arbitrary metadata fields.
/* eslint-disable @typescript-eslint/no-explicit-any */

export interface ReferenceImageInfo {
  index: number;
  original_filename: string;
  stored_path: string;
  thumbnail_path: string;
  content_type: string;
  size: number;
  source_type: string;
}

export interface BucketItem {
  filename: string;
  url?: string;
  thumbnail_url?: string;
  thumbnail_embedded?: string;
  favorite: boolean;
  metadata?: Record<string, any>;
  created_at?: number;
  reference_images?: ReferenceImageInfo[];
}

export interface Bucket {
  name: string;
  items: BucketItem[];
  metadata?: Record<string, any>;
  published?: string;
  publishedAt?: string;
  raw_url?: string;
  thumbnail_url?: string;
  favorites?: string[];
  sequence?: string[];
}

export interface PublishDestination {
  id: string;
  name: string;
  description?: string;
  icon?: string;
  has_bucket: boolean;
  file?: string;
  headless?: boolean;
  hidden?: boolean;
  groups?: string[];
}

export interface GenerateImageParams {
  prompt: string;
  workflow: string;
  params: Record<string, any>;
  global_params: Record<string, any>;
  imageFiles?: File[];
  referenceUrls?: string[];
  batch_id: string;
  placeholders: Array<{
    batch_index: number;
    placeholder_id: string;
  }>;
  refiner?: string;
  refiner_params?: Record<string, any>;
  is_async?: boolean;
}

export interface BucketDetails {
  name: string;
  items: Array<{
    filename: string;
    url: string;
    thumbnail_url: string;
    thumbnail_embedded?: string;
    favorite: boolean;
    metadata: Record<string, any>;
    created_at: number;
    reference_images?: ReferenceImageInfo[];
  }>;
  published: string | null;
  publishedAt: string | null;
  raw_url: string | null;
  thumbnail_url: string | null;
  favorites: string[];
  sequence: string[];
  error?: string | null;
}

export interface TranscriptionStatus {
  is_active: boolean;
  is_recording: Record<string, boolean>;
  client_ids: string[];
  target: string;
  current_transcription: string;
  last_event_time: string | null;
  last_event_word_count: number;
}

/** A scheduler event row (queue or history). Untyped, as before: callers assign it to their own event types. */
export type SchedulerEvent = any;

/** One entry of /buckets/<id>/complete `files`, as read by getBucketDetails. */
export interface RawBucketFile {
  filename: string;
  raw_url?: string;
  url?: string;
  thumbnail_url: string;
  thumbnail_embedded?: string;
  favorite?: boolean;
  metadata?: Record<string, any>;
  created_at?: number;
  modified?: number;
  reference_images?: ReferenceImageInfo[];
}

export interface MaskData {
  brightness: number;
  warm_hex: string;
  warm_alpha: number;
  timestamp: string;
}
