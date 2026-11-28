// The karaoke panel alone: no menu, no places, no other page (owner, 28 Nov: "THERE SHOULD BE NO GENERATOR OPTION WHEN
// USER ENTERS THE APP IN THIS WAY. it's not relevant to this entry point and just eats precious real estate").
// That is how a phone arrives from the screens' QR code (routes/karaoke/join.py adds ?from=qr; this tab remembers
// it), and always through the phones' tunnel (routes/phone_door.py: the server lets the karaoke panel only through).
const TUNNEL = typeof window !== "undefined" && /\.trycloudflare\.com$/i.test(window.location.hostname);

function fromQr(): boolean {
  if (typeof window === "undefined") return false;
  try {
    if (new URLSearchParams(window.location.search).get("from") === "qr") sessionStorage.setItem("sm_entry", "qr");
    return sessionStorage.getItem("sm_entry") === "qr";
  } catch {
    return new URLSearchParams(window.location.search).get("from") === "qr";
  }
}

export const VIA_TUNNEL = TUNNEL;
/** The app is the karaoke panel and nothing else (the QR code's way in, or the tunnel). */
export const KARAOKE_ONLY = TUNNEL || fromQr();
