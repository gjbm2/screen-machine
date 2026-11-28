// The karaoke queue as the page shows it: the server keeps one list in three parts (routes/karaoke/store.py: the
// songs already sung, the one that is on, the songs to come) and says how many are sung; the page never works that
// out for itself (owner, 28 Nov: songs vanished into "already sung", and came back out of it). Here: which part a
// row is in, and where a dragged row lands. No React, no imports: queueView.test.ts runs it under node.

export interface QueueState {
  queue: { qid: string; song_id: string }[];
  /** How many entries, from the top, are sung. */
  sung?: number;
  player: { state: string; qid: string | null; stop?: string | null };
}

export interface Parts {
  /** queue[..sung) are sung. */
  sung: number;
  /** The index of the song that is on (playing or paused), or null. */
  on: number | null;
  /** Where the songs to come start. */
  top: number;
}

export function parts(st: QueueState): Parts {
  const n = st.queue.length;
  const live = st.player.state !== "stopped";
  let sung: number;
  if (typeof st.sung === "number") sung = Math.max(0, Math.min(n, st.sung));
  else {
    // a server from before it kept the count: by the player's entry (stopped on it: sung, unless a restart cut it off)
    const i = st.queue.findIndex((e) => e.qid === st.player.qid);
    sung = i < 0 ? 0 : live || st.player.stop === "boot" ? i : i + 1;
  }
  const on = live && sung < n ? sung : null;
  return { sung, on, top: sung + (on === null ? 0 : 1) };
}

export type Where = "played" | "current" | "next";

export function whereOf(i: number, p: Parts): Where {
  return i < p.sung ? "played" : i === p.on ? "current" : "next";
}

/** Where "Play next" sends a song to come (the top of the songs to come); undefined when it is there already. */
export function playNextIndex(i: number, p: Parts): number | undefined {
  return i > p.top ? p.top : undefined;
}

/** How many times each song is still to be sung (the one on counts). */
export function queuedCounts(st: QueueState): Map<string, number> {
  const m = new Map<string, number>();
  for (const e of st.queue.slice(parts(st).sung)) m.set(e.song_id, (m.get(e.song_id) ?? 0) + 1);
  return m;
}

/** Where a new entry dragged in from the library lands: the index to insert at. ``over``: the row it was dropped on
 *  (the queue's length: its foot). Never above the top of the songs to come. */
export function insertIndex(p: Parts, len: number, over: number): number {
  return Math.max(p.top, Math.min(len, over));
}

/** Where a dragged queue row lands: the index of the whole queue it ends at (what the server is sent), or null when
 *  nothing moves. ``from``: the row; ``over``: the row it was dropped on (the queue's length: its foot).
 *  The same rules as the server's queue_move: the song that is on doesn't move; a song to come stays to come (above
 *  the top are the songs sung: it goes to the top); a sung one dropped among the songs to come is to come again. */
export function dropIndex(p: Parts, len: number, from: number, over: number): number | null {
  if (from < 0 || from >= len || from === p.on) return null;
  const t = Math.max(0, Math.min(len - 1, over));
  const to = from < p.sung ? (t < p.sung ? t : Math.max(t, p.top - 1)) : Math.max(t, p.top);
  return to === from ? null : to;
}

/** The queue after that move, to show at once (the server's answer follows). */
export function moved<E>(queue: E[], p: Parts, from: number, to: number): { queue: E[]; sung: number } {
  const q = [...queue];
  const [e] = q.splice(from, 1);
  q.splice(to, 0, e);
  return { queue: q, sung: from < p.sung && to >= p.sung ? p.sung - 1 : p.sung };
}
