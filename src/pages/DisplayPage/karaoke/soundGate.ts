// A new build reloads the TV page (index.tsx). While a karaoke song or the background music sounds on this page the
// reload waits for the end of the song (owner, 28 Nov: "background playing songs ... keep stopping halfway through":
// every deploy cut the song off and started it again). KaraokeLayer says each second whether sound is playing here.

let sounding = false;
let pending: (() => void) | null = null;
let gone = false;

/** The page is on its way out (a reload has been let through): nothing new should start sounding. */
export function leaving(): boolean {
  return gone;
}

/** KaraokeLayer, once a second: is a song (or the background music) sounding on this page now? */
export function setSounding(v: boolean): void {
  sounding = v;
  if (!v && pending) {
    const run = pending;
    pending = null;
    gone = true;
    run();
  }
}

/** Run ``reload`` now, or at the end of the song that is sounding here. */
export function reloadWhenQuiet(reload: () => void): void {
  if (!sounding) reload();
  else pending = reload;
}
