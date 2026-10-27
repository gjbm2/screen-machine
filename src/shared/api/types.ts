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

/** The AI tagger's reading of one image (sidecar `tagging.ai`); never shown as lozenges except subject/medium/moods. */
export interface TaggingAi {
  subject?: string[];
  medium?: string;
  moods?: string[];
  palette?: string;
  look?: string;
  energy?: number;
  valence?: number;
  organic?: number;
  density?: number;
  quality?: number;
  music?: Record<string, number>;
  auto_ok?: boolean;
  confidence?: number;
}

/** Sidecar `tagging` (docs/ART_TAGS.md §1). No `status` yet = the AI has not tagged it (a person may have). */
export interface TaggingInfo {
  v?: number;
  status?: 'done' | 'failed';
  at?: string;
  model?: string;
  cost_usd?: number;
  lexicon?: number;
  ai?: TaggingAi;
  added?: string[];
  removed?: string[];
  error?: string;
  attempts?: number;
}

/** The sidecar keys the tagger adds; every other sidecar key is carried as before. */
export interface TaggedMetadata {
  /** Effective tags, in order: AI subject, medium, moods, then hand-added words. */
  tags?: string[];
  tagging?: TaggingInfo;
}

export interface TagWord {
  word: string;
  desc?: string;
}

export interface TagLexicon {
  version: number;
  facets: {
    subject: TagWord[];
    medium: TagWord[];
    moods: TagWord[];
    palette: TagWord[];
  };
}

export interface TagStatus {
  running: boolean;
  queued: number;
  current: string | null;
  done_session: number;
  failed_session: number;
  paused_until: string | null;
  pause_reason: string | null;
  spend_today_usd: number;
  model: string;
  coverage: Record<string, { total: number; tagged: number; failed: number }>;
}

export interface BucketItem {
  filename: string;
  url?: string;
  thumbnail_url?: string;
  thumbnail_embedded?: string;
  favorite: boolean;
  metadata?: Record<string, any> & TaggedMetadata;
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
    metadata: Record<string, any> & TaggedMetadata;
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
