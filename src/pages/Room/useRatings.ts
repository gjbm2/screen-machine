// The owner's ratings of what's on glass (owner, 28 Oct: "expose the * rating (editable) within the /room page for the
// currently playing scene and effect"): the Scene library's own (stream_service ratings.py, config/live/ratings.json,
// per plug-in: "scene/<source>", "effect/<effect>"), through room_service's /renderer pass-through. The art director
// favours 3 stars and up, picks 1-2 stars rarely and never picks a reject by itself.
//
// One store for the page: read once, again on focus and every 60 s while visible (the Library and other phones rate
// too). A tap shows at once; a failed save puts the old rating back and says why.

import { useEffect, useSyncExternalStore } from "react";
import { explainError, type RoomApi } from "./api";
import type { RatingEntry } from "./types";

const REFRESH_MS = 60_000;

type Ratings = Record<string, RatingEntry>;

let api: RoomApi | null = null;
let data: Ratings = {};
let loadedAt = 0;
let loading = false;
let saving = 0;
const queue = new Map<string, Promise<unknown>>();
const listeners = new Set<() => void>();
let timer: number | undefined;

function emit(next: Ratings) {
  data = next;
  listeners.forEach((l) => l());
}

function setEntry(key: string, e: RatingEntry | null | undefined) {
  const next = { ...data };
  if (e && (e.stars || e.reject || e.tags?.length)) next[key] = e;
  else delete next[key];
  emit(next);
}

async function load(force = false) {
  const a = api;
  if (!a || loading || (!force && Date.now() - loadedAt < REFRESH_MS - 5_000)) return;
  loading = true;
  try {
    const r = await a.ratings();
    // a save under way would be undone by a read taken before it landed
    if (a === api && !saving) {
      loadedAt = Date.now();
      emit(r?.ratings ?? {});
    }
  } catch {
    /* keep what we have; the next focus or tick tries again */
  } finally {
    loading = false;
  }
}

const onFocus = () => void load();
const onTick = () => document.visibilityState === "visible" && void load();

function subscribe(l: () => void) {
  listeners.add(l);
  if (listeners.size === 1) {
    window.addEventListener("focus", onFocus);
    timer = window.setInterval(onTick, REFRESH_MS);
  }
  return () => {
    listeners.delete(l);
    if (listeners.size === 0) {
      window.removeEventListener("focus", onFocus);
      window.clearInterval(timer);
    }
  };
}

const read = () => data;

/** Every rating, by key; re-renders when any changes. */
export function useRatings(roomApi: RoomApi): Ratings {
  useEffect(() => {
    if (api?.baseUrl !== roomApi.baseUrl) {
      api = roomApi;
      loadedAt = 0;
      emit({});
    }
    void load();
  }, [roomApi]);
  return useSyncExternalStore(subscribe, read, read);
}

/** Stars 1-5 (0 clears) or reject, as the server does it (a rating lifts a reject; a reject clears the stars).
 *  Resolves to null when saved, or the words for why it wasn't (the old rating is back). */
export function rate(key: string, patch: { stars?: number; reject?: boolean }): Promise<string | null> {
  const a = api;
  if (!a) return Promise.resolve("The ratings aren't loaded yet.");
  const before = data[key];
  const next: RatingEntry = { ...(before ?? {}) };
  if (patch.stars !== undefined) {
    if (patch.stars) {
      next.stars = patch.stars;
      delete next.reject;
    } else delete next.stars;
  }
  if (patch.reject !== undefined) {
    if (patch.reject) {
      next.reject = true;
      delete next.stars;
    } else delete next.reject;
  }
  setEntry(key, next);
  saving++;
  // one save at a time per item, in tap order
  const run = (queue.get(key) ?? Promise.resolve()).then(async () => {
    try {
      const r = await a.rate({ key, ...patch });
      if (a === api) setEntry(key, r?.entry);
      return null;
    } catch (e) {
      if (a === api) setEntry(key, before);
      return `Couldn't save the rating: ${explainError(e)}`;
    } finally {
      saving--;
    }
  });
  queue.set(key, run);
  return run;
}
