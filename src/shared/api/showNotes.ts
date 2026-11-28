// The owner's verdict on how the show played a song (routes/show_notes.py): Flask's /api/show-notes, same-origin.
// The overnight eval scores every run against them (tools/room/ad_eval).

import { sendRelative } from '@/shared/http/client';

export interface ShowNote {
  at: number;
  song: string;
  title?: string | null;
  scope?: 'song' | null;
  t_s?: number | null;
  verdict: 'good' | 'bad';
  dimension?: string;
  words: string;
  by?: string;
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await sendRelative(`/show-notes${path}`, init);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error((body as { error?: string }).error || `Notes: ${r.status}`);
  return body as T;
}

export const showNotes = {
  list: (song: string) => call<{ notes: ShowNote[] }>(`?song=${encodeURIComponent(song)}`),
  add: (song: string, title: string, verdict: 'good' | 'bad', words: string) =>
    call<ShowNote>('', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ song, title, verdict, words }),
    }),
};
