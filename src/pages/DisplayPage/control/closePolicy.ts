// What the control socket does after room_service closes it (ControlClient.ts), by close code. Pure, so plain node
// can test it: closePolicy.test.ts.
//   * 4008 (server backlog/rate): at once, if that connection had been up long enough; else the usual backoff;
//   * 4404 (unknown screen: room_service doesn't run this screen, e.g. an art-only one; room_service/pages.py): only
//     every few minutes, in case the config later adds it; the page meanwhile works as any un-welcomed page (its mask
//     from the plain poller);
//   * anything else: the usual full-jitter backoff.

export const CLOSE_BACKLOG = 4008;
export const CLOSE_UNKNOWN_SCREEN = 4404;
/** After a 4404, the next try is this far off. */
export const UNKNOWN_SCREEN_RETRY_MS = 300_000;

export type ReconnectPlan = { kind: "now" } | { kind: "backoff" } | { kind: "after"; ms: number };

export function reconnectPlan(code: number, upForMs: number, immediateAfterUpMs: number): ReconnectPlan {
  if (code === CLOSE_UNKNOWN_SCREEN) return { kind: "after", ms: UNKNOWN_SCREEN_RETRY_MS };
  if (code === CLOSE_BACKLOG && upForMs >= immediateAfterUpMs) return { kind: "now" };
  return { kind: "backoff" };
}
