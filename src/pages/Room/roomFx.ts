// Effects on the live screens, shared by a screen's card (useLiveLook) and a room's live card (useRoomLive).
//
// Two kinds of effect sit on a screen's bus (stream_service fx_state.py, rooms.py):
//   - the room's picks (origin "auto"): the leader's FX director chooses them and every screen in the room mirrors
//     the leader's, so they are the room's, never one screen's;
//   - yours (origin "manual"): put on one screen by hand; they stay on that screen until taken off.
// Effects that come with a scene (a Gallery look's own) aren't on the bus at all.
//
// screen-machine-04's routes (17 Oct): POST fx/next {effect, replace, only, scope?} and POST fx/clear {effect?, scope?}.
// The renderer's catalog lists "fx_controls" when it has them (the library's features); only that list is trusted.
// No probe POST is ever sent (a probe on fx/clear would wipe every effect in the room if its validation changed).
// Without the feature the page uses today's paths (Up next add + play; a PUT per screen), and nothing ever crashes
// on either. `scope` goes only where the renderer has "look_scope" (the callers decide).

import { explainError, RoomApi, RoomApiError } from "./api";
import type { FxBus, FxEntry, FxPut, Library } from "./types";

export const FX_CONTROLS_FEATURE = "fx_controls";
export type Scope = "screen" | "room" | undefined;
const scoped = (scope: Scope) => (scope ? { scope } : {});

/** One live screen of a room: its id, its friendly name and its live session. */
export interface LiveMember {
  id: string;
  name: string;
  session: string;
}

/** The renderer's per-screen bus limit (the catalog's fx_bus.max_slots; 6 today). */
export function effectSlots(lib: Library | null | undefined): number {
  const n = lib?.fx_bus?.max_slots;
  return typeof n === "number" && n > 0 ? n : 6;
}

export const isPicked = (e: FxEntry) => e.origin === "auto";

/** The ensemble's gesture layer (origin "gesture", "Room gestures"; ff 74193ca3): the renderer keeps it last on the bus
 *  by itself and ignores it in a PUT, so the page never shows it, counts it or echoes it. */
export const ownStack = (stack?: FxEntry[] | null): FxEntry[] => (stack ?? []).filter((e) => e.origin !== "gesture");

/** A GET /fx entry as a PUT item: same slot (id), same settings. */
export function echo(e: FxEntry): Partial<FxEntry> {
  const out: Partial<FxEntry> = { id: e.id, level: e.level, bypass: e.bypass };
  if (e.preset) out.preset = e.preset;
  else out.effect = e.effect;
  if (e.params && Object.keys(e.params).length) out.params = e.params;
  if (e.bindings && Object.keys(e.bindings).length) out.bindings = e.bindings;
  return out;
}

/** PUT replaces a screen's whole stack: read, change, write (if_rev; one retry on a race). */
export async function putFxStack(
  api: RoomApi,
  screen: string,
  change: (stack: FxEntry[]) => FxPut["stack"],
  extra: { master?: number; fade_s?: number } = {},
): Promise<FxBus> {
  for (let attempt = 0; ; attempt++) {
    const cur = await api.fx(screen);
    const body: FxPut = {
      stack: change(ownStack(cur.stack)),
      fade_s: extra.fade_s ?? cur.fade_s,
      ...(typeof cur.rev === "number" ? { if_rev: cur.rev } : {}),
      ...(extra.master !== undefined ? { master: { amount: extra.master } } : {}),
    };
    try {
      return await api.putFx(screen, body);
    } catch (e) {
      if (attempt === 0 && e instanceof RoomApiError && e.code === "fx_conflict") continue;
      throw e;
    }
  }
}

/** The stack with `preset` added on top as yours. At the limit one comes off first: your oldest, else the oldest. */
export function withOnTop(stack: FxEntry[], preset: string, max: number): { stack: FxPut["stack"]; dropped: FxEntry | null } {
  let keep = stack;
  let dropped: FxEntry | null = null;
  if (stack.length >= max) {
    dropped = stack.find((e) => !isPicked(e)) ?? stack[0] ?? null;
    keep = stack.filter((e) => e !== dropped);
  }
  return { stack: [...keep.map(echo), preset], dropped };
}

/** A 404/405 for a route the renderer doesn't have yet (not a session that has gone). */
export function routeMissing(e: unknown): boolean {
  return e instanceof RoomApiError && (e.status === 405 || (e.status === 404 && !/no session/i.test(e.detail)));
}

/** Whether this session's renderer has 04's fx/next body and fx/clear: its features say so (the library refreshes
 *  every 30 s, so a renderer that just got them shows up shortly). */
export async function hasFxRoutes(_api: RoomApi, _session: string, lib?: Library | null): Promise<boolean> {
  return !!lib?.features?.includes(FX_CONTROLS_FEATURE);
}

/** Run `fn` on each screen at once. All failed: the first error. Some failed: says which, in words. */
export async function eachOf<T>(members: LiveMember[], fn: (m: LiveMember) => Promise<T>): Promise<T[]> {
  const got = await Promise.allSettled(members.map(fn));
  const bad = got.flatMap((r, i) => (r.status === "rejected" ? [{ m: members[i], e: r.reason as unknown }] : []));
  if (!bad.length) return got.map((r) => (r as PromiseFulfilledResult<T>).value);
  if (bad.length === members.length) throw bad[0].e;
  throw new Error(`${bad.map((b) => b.m.name).join(" and ")} didn't take it: ${explainError(bad[0].e)}`);
}

/**
 * Take effects off: `effect` (the room's pick of a preset, or a slot id) or every effect. `targets` are one live screen
 * per renderer room (04's fx/clear reaches every screen in it); without that route, a PUT on each of `members`.
 */
export async function clearEffects(
  api: RoomApi,
  targets: LiveMember[],
  members: LiveMember[],
  effect?: string,
  opts: { lib?: Library | null; scope?: Scope } = {},
): Promise<void> {
  if (!targets.length) return;
  if (await hasFxRoutes(api, targets[0].session, opts.lib)) {
    await eachOf(targets, (m) => api.fxClear(m.session, { ...(effect ? { effect } : {}), ...scoped(opts.scope) }));
    return;
  }
  // TODO(04): remove once every renderer has fx/clear. A follower re-copies the leader's picks every 2 s, so this
  // settles once the leader's bus has it off too.
  const hit = (x: FxEntry) => isPicked(x) && (x.preset === effect || x.id === effect);
  await eachOf(members, (m) => putFxStack(api, m.id, (stack) => (effect ? stack.filter((x) => !hit(x)).map(echo) : [])));
}

/**
 * Put `preset` on now, in place of the room's pick. 04's fx/next {effect, only} when the renderer has it (exactly that
 * one; your own stay). Else today's way: add it at the top of Up next and play it, which swaps the oldest picked
 * effect if it fits (else it brings in another). Returns the preset that went on.
 */
export async function pickEffect(
  api: RoomApi,
  session: string,
  preset: string,
  opts: { lib?: Library | null; scope?: Scope } = {},
): Promise<string | null> {
  if (await hasFxRoutes(api, session, opts.lib)) {
    const r = await api.nextEffect(session, { effect: preset, only: true, ...scoped(opts.scope) });
    return r?.preset ?? preset;
  }
  // TODO(04): remove once every renderer has fx/next {effect}.
  const added = await api.upnextEdit(session, { op: "add", kind: "effect", effect: preset, at: 0 });
  const id = added?.id;
  if (!id) throw new RoomApiError(409, "no_effect", "");
  try {
    const played = await api.upnextEdit(session, { op: "play", id });
    const fx = played?.fx as { preset?: string | null } | undefined;
    return fx?.preset ?? null;
  } catch (e) {
    await api.upnextEdit(session, { op: "remove", id }).catch(() => undefined); // don't leave it queued
    throw e;
  }
}

/** Swap the room's pick `slot` (this session's slot id) for another. An older renderer swaps its oldest pick (the same
 *  thing while it runs one); a follower's own slot id isn't the leader's, so that one falls back to the same. */
export async function swapPicked(api: RoomApi, session: string, slot: string | null, scope?: Scope): Promise<void> {
  if (!slot) {
    await api.nextEffect(session, scoped(scope));
    return;
  }
  try {
    await api.nextEffect(session, { replace: slot, ...scoped(scope) });
  } catch (e) {
    if (e instanceof RoomApiError && e.status === 400 && /replace/i.test(e.detail)) await api.nextEffect(session, scoped(scope));
    else throw e;
  }
}
