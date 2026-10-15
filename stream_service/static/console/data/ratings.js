// The owner's ratings (stars, reject, own tags, saved looks): GET /dev/ratings, POST /dev/rate (config/live/ratings.json).
// Optimistic: a change shows at once and reverts with a toast if the server refuses.  Refreshed on window focus and
// every 60 s while visible, because other devices rate too.
import { http, store } from "../kit.js";
import { applyRating } from "./query.js";
import { lib, bump } from "./library.js";
import { toastErr, toast } from "../ui/toast.js";
import { errorWords } from "../words.js";

export const ratings = new Map();
let loadedAt = 0;

export async function load() {
  const r = await http("GET", "/dev/ratings");
  if (!r.ok || !r.data?.ratings) return false;
  const next = new Map(Object.entries(r.data.ratings));
  let changed = next.size !== ratings.size;
  if (!changed) for (const [k, v] of next) if (JSON.stringify(ratings.get(k)) !== JSON.stringify(v)) { changed = true; break; }
  ratings.clear();
  for (const [k, v] of next) ratings.set(k, v);
  loadedAt = Date.now();
  if (changed) { applyAll(); bump(); }
  return true;
}
export function applyAll() { for (const it of lib.items) applyRating(it, ratings.get(it.key)); }

const inflight = new Map();
/** rate(it, {stars?, reject?, tags?, looks?}) -> Promise<boolean> */
export async function rate(it, patch) {
  if (!it) return false;
  const before = ratings.get(it.key);
  const next = { ...(before || {}), ...patch };
  if (next.stars === 0) delete next.stars;
  if (next.reject === false) delete next.reject;
  if (Array.isArray(next.tags) && !next.tags.length) delete next.tags;
  if (Array.isArray(next.looks) && !next.looks.length) delete next.looks;
  const empty = !Object.keys(next).filter((k) => k !== "at").length;
  if (empty) ratings.delete(it.key); else ratings.set(it.key, next);
  applyRating(it, ratings.get(it.key));
  bump();
  const prev = inflight.get(it.key) || Promise.resolve();
  const run = prev.then(async () => {
    const r = await http("POST", "/dev/rate", { key: it.key, ...patch });
    if (!r.ok) {
      if (before) ratings.set(it.key, before); else ratings.delete(it.key);
      applyRating(it, ratings.get(it.key));
      bump();
      toastErr(`Couldn't save the rating: ${errorWords(r)}`);
      return false;
    }
    if (r.data?.entry) ratings.set(it.key, r.data.entry); else ratings.delete(it.key);
    applyRating(it, ratings.get(it.key));
    bump();
    return true;
  });
  inflight.set(it.key, run.catch(() => {}));
  return run;
}

export function startRefresh() {
  window.addEventListener("focus", () => { if (Date.now() - loadedAt > 5000) load(); });
  setInterval(() => { if (document.visibilityState === "visible" && Date.now() - loadedAt > 55000) load(); }, 60000);
}

// The old per-browser favourites become the owner's tag "favourite", once.
const FAV_KEY = "sm.library.favs", FAV_TAG = "favourite";
export async function migrateFavs() {
  const favs = store.get(FAV_KEY, null);
  if (!Array.isArray(favs) || !favs.length) { if (favs != null && !Array.isArray(favs)) store.del(FAV_KEY); return; }
  const left = [];
  let moved = 0;
  for (const key of favs) {
    const it = lib.byKey.get(key);
    if (!it) { left.push(key); continue; }
    if (it._mytags.includes(FAV_TAG)) continue;
    const ok = await rate(it, { tags: [...it._mytags, FAV_TAG] });
    if (ok) moved++; else left.push(key);
  }
  if (left.length) store.set(FAV_KEY, left); else store.del(FAV_KEY);
  if (moved) toast(`Your ${moved} favourite${moved > 1 ? "s are" : " is"} now the tag "favourite" (Filters → More → My tags)`, { ms: 6000 });
}
