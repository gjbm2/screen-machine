// Behind the friendly HTTPS name (Caddy on PC22) an https page may not open ws:// sockets (mixed content),
// so the page would crash on its first socket. Caddy serves the same things same-origin: /rs -> room_service
// (:8700), which passes /renderer/* through to stream_service (media sockets included); the overlay socket's
// /ws/ is handled in hooks/useOverlayWebSocket.ts. Over http (the TVs, the public address) every URL is left
// exactly as it was.

const HTTPS = typeof window !== "undefined" && window.location.protocol === "https:";
const WSS_ORIGIN = HTTPS ? `wss://${window.location.host}` : "";

/** room_service's control plane: VITE_ROOM_URL (as ws) over http, /rs over https. */
export function roomSocketBase(fallback: string): string {
  return HTTPS ? `${WSS_ORIGIN}/rs` : fallback;
}

/** A live session's media socket (ws://<lan-host>:8702/<path>): over https, via room_service's /renderer pass-through. */
export function mediaSocketUrl(url: string): string {
  if (!HTTPS || !/^ws:\/\//i.test(url)) return url;
  try {
    const u = new URL(url);
    return `${WSS_ORIGIN}/rs/renderer${u.pathname}${u.search}`;
  } catch {
    return url;
  }
}
