// Karaoke (docs/KARAOKE.md): Flask's /api/karaoke/*, always same-origin, so the /karaoke page works from the laptop
// and from a phone on the tailnet alike, and the display page's lyrics layer from wherever it is served.

import { sendRelative } from '@/shared/http/client';
import type { SoundOutput } from '@/shared/api/sound';

export type SongStatus = 'queued' | 'fetching' | 'lyrics' | 'separating' | 'ready' | 'failed';

export interface KaraokeRefine {
  state: 'waiting' | 'working' | 'done' | 'no_better' | 'failed' | 'undone' | 'cancelled';
  detail?: string;
  error?: string | null;
  asked_at?: number;
  done_at?: number;
  /** Of the lines whose words were heard: how many the careful timing starts within 2 s of. */
  agreement?: { heard: number; agree: number; share: number | null; median_s: number | null };
}

export interface KaraokeSong {
  id: string;
  status: SongStatus;
  detail: string;
  error: string | null;
  title: string;
  artist: string;
  duration: number | null;
  /** The backing track's exact length: the server's clock ends the song here. */
  audio_s: number | null;
  video_id: string | null;
  source_url: string | null;
  /** The automatic shift of the lyrics against the vocal, ms (worker). */
  offset_ms: number;
  /** The person's nudge on top of it, ms. */
  user_offset_ms: number;
  created_at: number;
  ready_at: number | null;
  lyrics: { kind: 'synced' | 'plain' | 'none'; lines: number } | null;
  /** Bumped each time the lyrics are timed again (the careful pass, its undo): the display pages fetch them again. */
  lyrics_rev?: number;
  /** The careful pass over the lyrics, once someone referred the song ("Lyrics off?"). */
  refine?: KaraokeRefine | null;
  request_kind: string | null;
  /** Catalogue facts (routes/karaoke/sources.py song_facts), null until known. */
  genre?: string | null;
  year?: number | null;
  /** Deezer's popularity (higher: more played): "most popular first". */
  rank?: number | null;
  art?: string | null;
  album?: string | null;
  /** Genre and decade ("Pop", "1980s"). */
  tags?: string[];
  /** Who wrote it (MusicBrainz), when known: the intro card's "Written by". */
  writers?: string[] | null;
  /** Silence before the music so the title is up 8 s before the first line: the player starts the song at -lead_in_s. */
  lead_in_s?: number;
}

/** The one clock, on the server: while playing, the position now is pos_s + (server now - at). */
export interface KaraokePlayer {
  rev: number;
  state: 'playing' | 'paused' | 'stopped';
  song_id: string | null;
  /** The queue entry it is on (entries stay in the queue after they play). */
  qid: string | null;
  pos_s: number;
  at: number;
}

export interface KaraokeSettings {
  /** The lead vocal under the backing track: 0 off, 0.25 quiet, 1 full. */
  guide: number;
  autoplay: boolean;
  /** The delay from the player's clock to the room for the lyrics: the output's own (docs/SOUND.md: smsound.latency,
   *  measured + trim; 0 on a Sonos or with no sound); null: a screen output never measured (the page that plays it uses
   *  its browser's output latency). Read only: set it on the output (Settings › Sound & timing, or Calibrate). */
  audio_delay_ms: number | null;
  /** How the words show as they're sung: yellow, a bouncing dot, or both. */
  highlight?: 'words' | 'dot' | 'both';
  /** Child mode: swear words as dashes and muted (routes/karaoke/profanity.py). */
  child_mode?: boolean;
  /** The karaoke screen (its display page shows the lyrics; null: karaokeScreen's own choice). */
  screen?: string | null;
  /** Where the sound plays: one of the app's sound outputs (docs/SOUND.md) karaoke can play to: "screen:<id>" (that
   *  screen's own page), "sonos:<zone>" (it plays the song itself and the player's clock follows what the room hears)
   *  or "none". Always resolved here; `output_set` false: it's the karaoke screen's page by default. */
  output?: string;
  output_set?: boolean;
  /** The output's graphic EQ (dB per band, smsound/eq.py), for a page or browser that plays it; null: not applied. */
  eq?: number[] | null;
}

/** One place in the queue. The same song can be queued more than once. */
export interface QueueEntry {
  qid: string;
  song_id: string;
  /** Who sing it, if anyone said (one or more). */
  singers?: string[];
}

export interface KaraokeState {
  /** The Sonos driver's last problem (a Sonos room as the output), else null. */
  sonos_error?: string | null;
  songs: KaraokeSong[];
  queue: QueueEntry[];
  /** The player, with where the song is now by the server's clock. */
  player: KaraokePlayer & { position_s: number };
  settings: KaraokeSettings;
  calibration?: KaraokeCalibration | null;
  now: number;
  /** The names given songs in the last 12 hours, most recent first: the singer box's suggestions. */
  singers?: string[];
}

/** The speaker-delay calibration (routes/karaoke/calibrate.py): while running, the chirps every output window plays
 *  (server times, each leaving the browser's output then); when done, the result for the panel. */
export interface KaraokeCalibration {
  id: string;
  state: 'starting' | 'running' | 'done' | 'failed';
  message?: string | null;
  clicks?: number[];
  /** Each click's own sweep (they all differ, so each is found on its own). */
  chirps?: { f0: number; f1: number; dur_s: number }[];
  delay_ms?: number;
  spread_ms?: number;
  agree?: number;
  of?: number;
  at?: number;
}

export interface KaraokeNow {
  player: KaraokePlayer;
  settings: KaraokeSettings;
  song: KaraokeSong | null;
  up_next: { id: string; title: string; artist: string; singers?: string[] } | null;
  /** Who sing the song on now, if anyone said. */
  singers?: string[];
  /** Entries after the one on now, ready or still being made (0: the screens ask for more). */
  queued_after?: number;
  /** The panel's address for a phone (null: unknown) and whether the room is in a karaoke mode: the screens' QR. */
  join?: { url: string | null; room_karaoke: boolean };
  /** While nothing plays: songs for the quiet background music, in order (the last sung first). */
  background?: string[];
  calibration?: KaraokeCalibration | null;
  now: number;
}

export interface LyricLine {
  start: number | null;
  end: number | null;
  text: string;
  /** When each space-separated token of `text` is sung (null: no letters, or not timed). Absent: sweep evenly. */
  words?: ({ t0: number; t1: number } | null)[];
}

export interface KaraokeLyrics {
  kind: 'synced' | 'plain' | 'none';
  lines: LyricLine[];
  /** Child mode (settings.child_mode; routes/karaoke/profanity.py): the swear words are already dashes in the text,
   *  and these are the song times [t0, t1] to mute the lead vocal over. */
  child?: boolean;
  mutes?: [number, number][];
}

/** A song from the web search (iTunes + Deezer, steered by YouTube: routes/karaoke/sources.py catalog_search). */
export interface SearchHit {
  key: string;
  artist: string;
  title: string;
  album: string | null;
  duration: number | null;
  /** Cover art (small), when the catalogue has it. */
  art: string | null;
  genre?: string | null;
  year?: number | null;
  rank?: number | null;
  from: 'itunes' | 'deezer' | 'lrclib';
  /** Already in the library: its song id. */
  have: string | null;
}

/** Where a new song goes in the queue: up next (right after the one playing) or at the end. */
export type QueueAt = 'next' | 'end';

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await sendRelative(`/karaoke${path}`, init);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error((body as { error?: string }).error || `Karaoke: ${r.status}`);
  return body as T;
}

const post = (body: unknown): RequestInit => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

/** One of a song's artist pictures (routes/karaoke/pictures.py): found, vetted and kept per artist. */
export interface SongPicture {
  id: string; // "<artist-slug>/<file>"
  artist: string;
  url: string; // the JPEG, same origin
  kind: 'portrait' | 'live' | 'group' | 'cover' | 'other';
  era: string | null;
  caption: string | null;
  source: string;
  credit: string | null;
  license: string | null;
}

export interface SongPictures {
  state: 'ready' | 'fetching' | 'none';
  artists: string[];
  pictures: SongPicture[];
}

export const karaoke = {
  pictures: (sid: string) => call<SongPictures>(`/songs/${encodeURIComponent(sid)}/pictures`),
  hidePicture: (id: string, hidden = true) => call<{ ok: boolean }>(`/pictures/${id}/hide`, post({ hidden })),
  state: () => call<KaraokeState>('/state'),
  now: () => call<KaraokeNow>('/now'),
  search: (q: string, limit = 8) =>
    call<{ results: SearchHit[] }>(`/search?q=${encodeURIComponent(q)}&limit=${limit}`),
  artist: (name: string) =>
    call<{ artist: string; picture: string | null; fans: number | null; results: SearchHit[] }>(`/artist?name=${encodeURIComponent(name)}`),
  request: (body: {
    q?: string;
    url?: string;
    artist?: string;
    title?: string;
    duration?: number | null;
    queue?: QueueAt;
    singers?: string[];
    genre?: string | null;
    year?: number | null;
    rank?: number | null;
    art?: string | null;
    album?: string | null;
  }) =>
    call<{ song_id: string; existing: boolean }>('/requests', post(body)),
  retry: (id: string) => call<KaraokeSong>(`/songs/${id}/retry`, post({})),
  /** Lyrics off: the careful pass over them (in the background); or undo it, or stop waiting. */
  refine: (id: string, body: { undo?: boolean; cancel?: boolean; note?: string } = {}) =>
    call<{ ok: boolean }>(`/songs/${id}/refine`, post(body)),
  nudge: (id: string, user_offset_ms: number) =>
    call<{ id: string; user_offset_ms: number }>(`/songs/${id}`, { ...post({ user_offset_ms }), method: 'PATCH' }),
  remove: (id: string) => call<{ ok: boolean }>(`/songs/${id}`, { method: 'DELETE' }),
  queueAdd: (song_id: string, index?: number, next?: boolean, singers?: string[]) =>
    call<{ queue: QueueEntry[] }>('/queue', post({ op: 'add', song_id, index, next, singers })),
  /** Who sing a queue entry ([]: nobody said). */
  queueSingers: (qid: string, singers: string[]) =>
    call<{ queue: QueueEntry[] }>('/queue', post({ op: 'singers', qid, singers })),
  queueRemove: (qid: string) => call<{ queue: QueueEntry[] }>('/queue', post({ op: 'remove', qid })),
  /** Clears the queue, except the song playing (or paused) now. */
  queueClear: () => call<{ queue: QueueEntry[] }>('/queue', post({ op: 'clear' })),
  queueMove: (qid: string, index: number) => call<{ queue: QueueEntry[] }>('/queue', post({ op: 'move', qid, index })),
  player: (action: string, extra: { song_id?: string; qid?: string; pos_s?: number } = {}) =>
    call<{ player: KaraokePlayer }>('/player', post({ action, ...extra })),
  /** A Spotify playlist or album: its name and songs (each `have` when the library has it). */
  playlist: (url: string) =>
    call<{ kind: string; name: string; tracks: { title: string; artist: string; duration: number | null; have: string | null }[] }>(
      `/playlist?url=${encodeURIComponent(url)}`,
    ),
  /** Every song of a Spotify playlist or album into the library (and, with queue, into the queue). */
  importList: (url: string, queue = false) =>
    call<{ name: string; added: number; had: number; total: number }>('/import', post({ url, queue })),
  /** The app's sound outputs, marked for karaoke (GET /api/sound/outputs?for=karaoke). */
  outputs: () => call<{ outputs: SoundOutput[] }>('/outputs'),
  calibrate: () => call<{ calibration: KaraokeCalibration }>('/calibrate', post({})),
  /** Tonight's names, set before anyone sings. */
  roster: (body: { add?: string[]; remove?: string[] }) => call<{ singers: string[] }>('/singers', post(body)),
  /** A new session: tonight's names and the songs already sung are cleared. */
  newSession: () => call<{ singers: string[] }>('/session', post({ op: 'new' })),
  settings: (s: Partial<KaraokeSettings>) => call<{ settings: KaraokeSettings }>('/settings', post(s)),
  lyrics: (id: string) => call<KaraokeLyrics>(`/songs/${id}/lyrics`),
  audioUrl: (id: string, stem: 'instrumental' | 'vocals') => `/api/karaoke/songs/${id}/${stem}.m4a`,
};

/** The karaoke screen (owner, 26 Nov: "specify which screen we're outputting to"): the setting, else the first screen
 *  of the deployment's default room that can go live, else the first that can go live. Only its display page shows
 *  the lyrics (and, with output "screen", plays the sound); every other screen leaves karaoke alone. */
export function karaokeScreen(
  d: { screens?: { id: string; live: boolean; room?: string | null }[]; default_room?: string | null } | null,
  setting: string | null | undefined,
): string | null {
  if (setting) return setting;
  const live = (d?.screens ?? []).filter((s) => s.live);
  return (live.find((s) => s.room === d?.default_room) ?? live[0])?.id ?? null;
}
