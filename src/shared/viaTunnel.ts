// Opened through the phones' tunnel (routes/phone_door.py: a Cloudflare quick tunnel, so guests' phones reach the
// karaoke panel on venue Wi-Fi that keeps them from the laptop). There the app is the karaoke panel and nothing else:
// no menu, no places, no screen or room controls (owner, 28 Nov: "generator ends up exposed, which I don't think is
// appropriate"); the server lets only the karaoke paths through as well.
export const VIA_TUNNEL = typeof window !== "undefined" && /\.trycloudflare\.com$/i.test(window.location.hostname);
