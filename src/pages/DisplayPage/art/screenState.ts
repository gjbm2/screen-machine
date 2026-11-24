// What the screen shows, as one document (routes/screen_state.py, owner 24 Nov): output/<screen>.state.json.
// The page renders it: a still (with its own clip played over it now and then, and the transition into it played
// once), or a video someone published as the picture itself. Every URL in it names a file that never changes.
//
// The page reads the document itself (at start, on waking, on reconnecting, every POLL_MS) and room_service also
// pushes it (`art.state`); each copy is the whole state, so whichever arrives is applied if it is newer. A page that
// slept or lost its socket is right on its next read.

export interface StillRef {
  url: string;
  width: number | null;
  height: number | null;
  origin?: string | null;
}
export interface ClipRef {
  url: string;
  eased?: boolean;
  pace?: string | null;
  play_s?: number | null;
  rest_s?: [number, number] | null;
  returns?: boolean | null;
}
export interface TransitionRef {
  url: string;
  from?: string | null;
  to?: string | null;
  at?: number | null;
}
export interface ScreenState {
  v: number;
  screen?: string;
  seq: number;
  at: number;
  still: StillRef | null;
  clip: ClipRef | null;
  transition: TransitionRef | null;
  video: { url: string; clip?: Record<string, unknown> | null } | null;
}

export const POLL_MS = 10_000;
/** A transition is played only by a page that saw its picture arrive, within this long of the publish. */
export const TRANSITION_FRESH_S = 600;

export function isState(x: unknown): x is ScreenState {
  const s = x as ScreenState;
  return !!s && typeof s === "object" && typeof s.seq === "number" && (!!s.still?.url || !!s.video?.url);
}

/** The newer of two documents (a push and a read can arrive in either order). */
export function newer(cur: ScreenState | null, next: ScreenState | null): ScreenState | null {
  if (!next) return cur;
  if (!cur) return next;
  if (next.seq > cur.seq) return next;
  // the document was rewritten from scratch (a lower seq, but written later)
  if (next.seq < cur.seq && next.at > cur.at) return next;
  return cur;
}

/** The base layer's source: the published video, else the still. */
export function baseUrl(s: ScreenState | null): string | null {
  return s?.video?.url ?? s?.still?.url ?? null;
}

/** The still's width / height, for laying a clip or transition exactly over it. */
export function stillAspect(s: ScreenState | null): number | null {
  const w = s?.still?.width;
  const h = s?.still?.height;
  return w && h ? w / h : null;
}

/** The transition to play as the page goes from `shown` to `next`: only for a new still that this page saw arrive
 *  (not on first load or after waking to a long-gone change), fresh, and not already played. */
export function transitionToPlay(
  shown: ScreenState | null,
  next: ScreenState,
  nowS: number,
  played: Set<string>,
): TransitionRef | null {
  const t = next.transition;
  if (!shown || !t?.url || !next.still || next.video) return null;
  if (baseUrl(shown) === baseUrl(next)) return null;
  if (!(nowS - Number(t.at ?? next.at) < TRANSITION_FRESH_S)) return null;
  // it starts on the picture it was made from: a page that missed a change in between (asleep) just cross-fades
  if (t.from && shown.still?.origin && t.from !== shown.still.origin) return null;
  const key = `${t.url}@${t.at ?? next.at}`;
  if (played.has(key)) return null;
  played.add(key);
  return t;
}
