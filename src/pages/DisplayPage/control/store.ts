// A tiny external store + pure reducer for the control plane's coarse state.
// Timing-critical work (cuts, mask writes) never waits for React: the controllers act on the DOM
// directly and then dispatch here so React can catch up (unmount overlays, close the controls panel).
import { DEFAULT_POLICY, type Fid, type Policy } from "./protocol";

export type LivePhase = "connecting" | "warming" | "playing" | "leaving";

export interface CtlState {
  socket: "connecting" | "up" | "down";
  epoch: string | number | null;
  policy: Policy;
  frozen: { fid: Fid } | null;
  /** What is on glass: the art stack, or the live video. */
  layer: "art" | "live";
  /** A live session is mounted (from live.attach until teardown ends). */
  live: { session: string; phase: LivePhase } | null;
}

export type CtlAction =
  | { type: "socket"; status: CtlState["socket"] }
  | { type: "welcome"; epoch: string | number; policy: Partial<Policy> | null | undefined }
  /** A `policy` message: fields absent from it keep their current value (LIVE_V1 §2.1). */
  | { type: "policy"; policy: Partial<Policy> | null | undefined }
  | { type: "freeze"; fid: Fid }
  | { type: "unfreeze" }
  | { type: "layer"; layer: CtlState["layer"] }
  | { type: "live"; live: CtlState["live"] };

export const initialCtlState: CtlState = {
  socket: "connecting",
  epoch: null,
  policy: { ...DEFAULT_POLICY },
  frozen: null,
  layer: "art",
  live: null,
};

export function ctlReducer(s: CtlState, a: CtlAction): CtlState {
  switch (a.type) {
    case "socket":
      return s.socket === a.status ? s : { ...s, socket: a.status };
    case "welcome":
      // The welcome is authoritative: a field it lacks takes the default, not the previous value.
      return { ...s, epoch: a.epoch, policy: mergePolicy(DEFAULT_POLICY, a.policy) };
    case "policy": {
      const policy = mergePolicy(s.policy, a.policy);
      const same = (Object.keys(policy) as (keyof Policy)[]).every((k) => policy[k] === s.policy[k]);
      return same ? s : { ...s, policy };
    }
    case "freeze":
      return { ...s, frozen: { fid: a.fid } };
    case "unfreeze":
      return s.frozen ? { ...s, frozen: null } : s;
    case "layer":
      return s.layer === a.layer ? s : { ...s, layer: a.layer };
    case "live":
      if (s.live === a.live) return s;
      if (s.live && a.live && s.live.session === a.live.session && s.live.phase === a.live.phase) return s;
      return { ...s, live: a.live };
  }
}

/** `base` with the boolean fields present in `p` applied (anything else in `p` is ignored). */
export function mergePolicy(base: Policy, p: Partial<Policy> | null | undefined): Policy {
  const out = { ...base };
  if (p && typeof p === "object") {
    for (const k of Object.keys(DEFAULT_POLICY) as (keyof Policy)[]) {
      if (typeof p[k] === "boolean") out[k] = p[k] as boolean;
    }
  }
  return out;
}

export class Store<S, A> {
  private state: S;
  private listeners = new Set<() => void>();

  constructor(private reducer: (s: S, a: A) => S, initial: S) {
    this.state = initial;
  }

  getState = (): S => this.state;

  dispatch = (a: A): void => {
    const next = this.reducer(this.state, a);
    if (next === this.state) return;
    this.state = next;
    this.listeners.forEach((l) => l());
  };

  subscribe = (l: () => void): (() => void) => {
    this.listeners.add(l);
    return () => this.listeners.delete(l);
  };
}

export type CtlStore = Store<CtlState, CtlAction>;

export function createCtlStore(): CtlStore {
  return new Store(ctlReducer, initialCtlState);
}
