// Comments on what a screen showed (routes/screen_notes.py): Flask's /api/screen-notes, same-origin.

import { sendRelative } from '@/shared/http/client';

export interface ScreenNote {
  at: number;
  screen: string;
  kind: 'scene' | 'effect';
  key: string;
  label: string;
  when: 'was' | 'now' | 'next';
  words: string;
  song?: { id: string; title?: string | null; t_s: number };
}

export async function addScreenNote(n: Omit<ScreenNote, 'at' | 'song'>): Promise<ScreenNote> {
  const r = await sendRelative('/screen-notes', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(n),
  });
  if (!r.ok) throw new Error(`Couldn't keep the comment (${r.status})`);
  return r.json();
}

export async function screenNotes(screen: string, limit = 100): Promise<ScreenNote[]> {
  const r = await sendRelative(`/screen-notes?screen=${encodeURIComponent(screen)}&limit=${limit}`);
  if (!r.ok) return [];
  return (await r.json()).notes ?? [];
}
