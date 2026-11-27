// Karaoke on this device (owner, 27 Nov: "'this device' is always a meaningful notion in any device that can host a
// browser"): when the karaoke's sound output is this browser's own ("device:<id>", listenStream.myOutput), this browser
// plays it with the screens' own player (KaraokeLayer, sound only: the lyrics stay on the karaoke screen), wherever in
// the app it is. Mounted once in App; it only follows the player on a browser that chose This device for karaoke.

import { lazy, Suspense, useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { myOutput } from "@/pages/Room/listenStream";

// its own chunk, loaded only on a browser that plays the karaoke
const KaraokeLayer = lazy(() => import("@/pages/DisplayPage/karaoke/KaraokeLayer").then((m) => ({ default: m.KaraokeLayer })));

const KEY = "karaoke.here";
const EVENT = "sm-karaoke-here";

function stored(): boolean {
  try {
    return window.localStorage.getItem(KEY) === "1";
  } catch {
    return false;
  }
}

/** This browser plays the karaoke (on) or not (off): set when its picker chooses This device or anything else. */
export function setKaraokeHere(on: boolean): void {
  try {
    if (on) window.localStorage.setItem(KEY, "1");
    else window.localStorage.removeItem(KEY);
  } catch {
    /* a blocked store: this page only */
  }
  window.dispatchEvent(new Event(EVENT));
}

export function KaraokeHere() {
  const [on, setOn] = useState(stored);
  const display = useLocation().pathname.startsWith("/display"); // a screen's page plays as that screen
  useEffect(() => {
    const sync = () => setOn(stored());
    window.addEventListener(EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);
  return on && !display ? (
    <Suspense fallback={null}>
      <KaraokeLayer device={myOutput()} />
    </Suspense>
  ) : null;
}
