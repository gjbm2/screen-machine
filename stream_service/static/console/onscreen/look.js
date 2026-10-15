// The target screen's look (room's GET / PATCH /api/v1/screens/{id}/look): the director's direction, auto, palette,
// Auto effects.  Controls show the server's values: read on open, after each PATCH, on that screen's status changes
// (at most 1 a second) and every 5 s while On screen is visible and the screen is live.
import { emitter } from "../kit.js";
import * as room from "../data/room.js";
import { toastErr } from "../ui/toast.js";
import { errorWords } from "../words.js";

const ev = emitter();
export const L = { target: null, data: null, at: 0, err: null };
export const onLook = (fn) => ev.on("look", fn);

let inflight = null;
export async function refresh(target) {
  if (!target) return null;
  if (!room.isReady(room.screenOf(target))) return L.target === target ? L.data : null;   // room answers 409 mid-cut
  if (inflight && inflight.target === target) return inflight.p;
  const p = (async () => {
    const r = await room.look(target);
    if (target !== L.target && L.target != null && L.target !== target) { /* switched meanwhile */ }
    L.target = target; L.at = Date.now();
    if (r.ok) { L.data = r.data; L.err = null; } else { L.data = null; L.err = r; }
    ev.emit("look", L);
    return L.data;
  })();
  inflight = { target, p };
  try { return await p; } finally { if (inflight?.p === p) inflight = null; }
}

let busy = false, queued = null;
/** PATCH the look; bodies queued while one is in flight are merged (one in flight at a time). */
export async function patch(target, body, { silent = false } = {}) {
  if (busy) {
    queued = queued ? merge(queued, body) : { target, body };
    if (queued.target !== target) queued = { target, body };
    return null;
  }
  busy = true;
  let r;
  try {
    r = await room.patchLook(target, body);
    if (r.ok) { L.target = target; L.data = r.data; L.at = Date.now(); L.err = null; ev.emit("look", L); }
    else if (!silent) toastErr(errorWords(r, { screen: room.nameOf(target), target: "the room service" }));
  } finally { busy = false; }
  if (queued) { const q = queued; queued = null; patch(q.target, q.body, { silent }); }
  return r;
}
function merge(q, body) {
  const b = { ...q.body };
  for (const [k, v] of Object.entries(body)) {
    if (v && typeof v === "object" && !Array.isArray(v) && b[k] && typeof b[k] === "object") b[k] = { ...b[k], ...v };
    else b[k] = v;
  }
  return { target: q.target, body: b };
}
