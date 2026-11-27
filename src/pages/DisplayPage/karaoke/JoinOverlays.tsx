// The karaoke screens' prompts for the singers (owner, 27 Nov), drawn by KaraokeLayer:
// - JoinCard: a QR code that opens the karaoke panel (/karaoke) on a phone. Large while karaoke rests (the room in a
//   karaoke mode, nothing playing: every screen), small in a corner while a song plays with nothing queued after it.
//   The address is the server's (routes/karaoke/join.py: the machine's LAN address when .env says localhost).
// - NextUp: towards the end of a song, who and what is next, so the next singer gets ready. Gentle: one banner that
//   fades in NEXT_UP_S before the end and stays, no ticking count (owner: "we don't want to be TOO aggressive about
//   countdowns, just enough to get the next person ready to roll").

import type { CSSProperties } from "react";
import { QRCodeSVG } from "qrcode.react";

export const NEXT_UP_S = 30; // the next-up banner, this long before the song's end
export const LOW_FROM_S = 20; // the small join code, from this far into a song with nothing after it

const FONT = "'Segoe UI', 'Helvetica Neue', Arial, sans-serif";
const textStyle: CSSProperties = {
  color: "#fff",
  textShadow: "0 0 0.12em #000, 0 0 0.3em rgba(0,0,0,0.85), 0 0.05em 0.1em #000",
};

/** The address without its scheme, for reading off the screen. */
const shortUrl = (url: string) => url.replace(/^https?:\/\//, "");

export function JoinCard({ url, small = false, opacity = 1 }: { url: string; small?: boolean; opacity?: number }) {
  const qr = small ? "13vh" : "26vh";
  return (
    <div
      data-sm-karaoke-join={small ? "small" : "rest"}
      style={{
        position: "absolute",
        ...(small ? { right: "3vw", top: "4vh" } : { right: "5vw", top: "50%", transform: "translateY(-50%)" }),
        display: "flex",
        flexDirection: small ? "row" : "column",
        alignItems: "center",
        gap: small ? "1.2vw" : "2vh",
        padding: small ? "1.2vh 1.2vw" : "3vh 2.4vw",
        borderRadius: small ? "1.6vh" : "2.4vh",
        background: "rgba(0,0,0,0.55)",
        backdropFilter: "blur(6px)",
        fontFamily: FONT,
        opacity,
        maxWidth: small ? "34vw" : "30vw",
      }}
    >
      <div style={{ background: "#fff", padding: small ? "0.8vh" : "1.4vh", borderRadius: "1vh", lineHeight: 0 }}>
        <QRCodeSVG value={url} level="M" style={{ width: qr, height: qr, display: "block" }} />
      </div>
      <div style={{ ...textStyle, textAlign: small ? "left" : "center" }}>
        <div style={{ fontSize: small ? "min(1.9vw, 32px)" : "min(3.2vw, 54px)", fontWeight: 800, lineHeight: 1.1 }}>
          {small ? "Nothing queued next" : "Pick a song"}
        </div>
        <div style={{ fontSize: small ? "min(1.4vw, 24px)" : "min(1.9vw, 32px)", opacity: 0.9, marginTop: "0.3em" }}>
          {small ? "Scan to add one" : "Scan with your phone's camera"}
        </div>
        {!small && (
          <div style={{ fontSize: "min(1.3vw, 22px)", opacity: 0.7, marginTop: "0.6em", wordBreak: "break-all" }}>
            {shortUrl(url)}
          </div>
        )}
      </div>
    </div>
  );
}

export function NextUp({
  title,
  artist,
  singer,
  opacity,
}: {
  title: string;
  artist: string;
  singer?: string | null;
  opacity: number;
}) {
  const song = artist ? `${title} (${artist})` : title;
  return (
    <div
      data-sm-karaoke-next=""
      style={{
        position: "absolute",
        right: "3vw",
        top: "4vh",
        maxWidth: "46vw",
        padding: "1.4vh 1.6vw",
        borderRadius: "1.6vh",
        background: "rgba(0,0,0,0.55)",
        backdropFilter: "blur(6px)",
        borderLeft: "0.5vh solid rgba(255,214,90,0.95)",
        fontFamily: FONT,
        opacity,
        transform: `translateY(${(1 - opacity) * -1.5}vh)`,
        ...textStyle,
      }}
    >
      <div style={{ fontSize: "min(2.3vw, 38px)", fontWeight: 800, lineHeight: 1.15 }}>
        {singer ? `${singer}, you're up next` : "Up next"}
      </div>
      <div style={{ fontSize: "min(1.7vw, 28px)", opacity: 0.92, marginTop: "0.25em" }}>
        {singer ? `Get ready for ${song}` : song}
      </div>
    </div>
  );
}
