// A still's own clip, played over it now and then (owner, 24 Nov: "when it's resting the mp4 should be transparent or
// hidden ... when it plays, we should cross fade them briefly, then play the video with whatever ease / out settings
// and pacing is configured, and then drop it back to still"). clipPlan.ts decides when; this carries it out:
//
// - No <video> exists between plays, so the kiosk holds no decoder and no frames while the still rests.
// - A play mounts the video invisible over the still, laid out in the still's exact frame (the clip was drawn from it
//   squeezed to the model's shape, so it is stretched back), waits until it can play through, starts it, fades it in
//   while it is still all but at rest (its ease is rendered into the file), and when it ends, back on its first frame,
//   fades it out to the still and lets the video go (src removed, load(): the decoder is freed at once).
// - Fades are opacity only, which the compositor does without the CPU (cheap on the N95).
// - Anything that goes wrong (no load in time, an error, a stall) just ends the play: the still stays.
import React, { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import type { Fit } from "../control/protocol";
import { ClipPlan } from "./clipPlan";
import { boxFor, mediaStyle } from "./styles";
import type { ClipRef } from "./screenState";

/** Fade from the still to the clip as it starts (still all but at rest). */
export const FADE_IN_MS = 700;
/** Fade from the clip back to the still once it has come back to rest. */
export const FADE_OUT_MS = 2000;
/** Give up on a play whose video can't play through by then. */
const LOAD_TIMEOUT_MS = 20_000;

export interface ClipLayerHandle {
  canPlayNow(): boolean;
  playNow(): boolean;
  /** A play is on screen (from mounting to letting go). */
  playing(): boolean;
  /** Resolves once no play is on screen (a freeze waits for the fade back to the still). */
  settled(): Promise<void>;
  url(): string | null;
  /** Play another video once over the still (a clip of it, a transition into or out of it): false if it can't now. */
  playUrl(url: string): boolean;
}

interface Props {
  clip: ClipRef | null;
  /** The still's width / height: the clip is laid exactly over it. */
  aspect: number | null;
  displayMode: Fit;
  /** Frozen, live, or the base changing: nothing plays, a playing clip fades out quickly. */
  quiet: boolean;
  /** No transition wants the screen. */
  slotFree: () => boolean;
}

export const ClipLayer = forwardRef<ClipLayerHandle, Props>(function ClipLayer(
  { clip, aspect, displayMode, quiet, slotFree },
  ref,
) {
  const [play, setPlay] = useState<{ url: string; n: number } | null>(null);
  const [shown, setShown] = useState(false);
  const [fadeMs, setFadeMs] = useState(FADE_IN_MS);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const slotRef = useRef(slotFree);
  slotRef.current = slotFree;
  const waiters = useRef<(() => void)[]>([]);
  const outTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const playN = useRef(0);

  const planRef = useRef<ClipPlan | null>(null);
  if (!planRef.current) {
    planRef.current = new ClipPlan({
      start: () => {
        const url = planRef.current?.playingUrl;
        if (!url) return planRef.current?.played();
        setFadeMs(FADE_IN_MS);
        setShown(false);
        setPlay({ url, n: ++playN.current });
      },
      stop: (ms) => fadeOut(ms),
      slotFree: () => slotRef.current(),
    });
  }
  const plan = planRef.current;

  /** Let the video go: no src, load() frees the decoder; then the plan rests. */
  const release = () => {
    if (outTimer.current) clearTimeout(outTimer.current);
    outTimer.current = null;
    const v = videoRef.current;
    if (v) {
      v.pause();
      v.removeAttribute("src");
      v.load();
    }
    setPlay(null);
    setShown(false);
    plan.played();
    const w = waiters.current.splice(0);
    w.forEach((f) => f());
  };
  const fadeOut = (ms: number) => {
    if (outTimer.current) return;
    setFadeMs(ms);
    setShown(false);
    outTimer.current = setTimeout(release, ms + 50);
  };

  useImperativeHandle(ref, () => ({
    canPlayNow: () => plan.canPlayNow(),
    playNow: () => plan.playNow(),
    playing: () => plan.phase === "playing" || plan.phase === "stopping",
    settled: () =>
      plan.phase === "playing" || plan.phase === "stopping"
        ? new Promise<void>((res) => waiters.current.push(res))
        : Promise.resolve(),
    url: () => plan.url,
    playUrl: (url: string) => plan.playOnce(url),
  }));

  useEffect(() => {
    plan.setClip(clip?.url ? { url: clip.url, rest_s: clip.rest_s, play_s: clip.play_s } : null);
  }, [plan, clip?.url, clip?.rest_s, clip?.play_s]);
  useEffect(() => plan.setQuiet(quiet), [plan, quiet]);
  useEffect(() => {
    const onVis = () => plan.setHidden(document.visibilityState === "hidden");
    onVis();
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, [plan]);
  useEffect(
    () => () => {
      if (outTimer.current) clearTimeout(outTimer.current);
      plan.dispose();
    },
    [plan],
  );

  // One play: load, start, fade in on its first presented frame, fade out when it ends.
  useEffect(() => {
    const v = videoRef.current;
    if (!play || !v) return;
    let over = false;
    const end = (ms: number) => {
      if (over) return;
      over = true;
      fadeOut(ms);
    };
    const loadGuard = setTimeout(() => end(0), LOAD_TIMEOUT_MS);
    const start = () => {
      clearTimeout(loadGuard);
      v.play().then(
        () => {
          const show = () => requestAnimationFrame(() => !over && setShown(true));
          if ("requestVideoFrameCallback" in v) v.requestVideoFrameCallback(show);
          else show();
        },
        () => end(0),
      );
    };
    const onEnded = () => end(FADE_OUT_MS);
    const onError = () => end(0);
    if (v.readyState >= 4) start();
    else v.addEventListener("canplaythrough", start, { once: true });
    v.addEventListener("ended", onEnded);
    v.addEventListener("error", onError);
    return () => {
      clearTimeout(loadGuard);
      v.removeEventListener("canplaythrough", start);
      v.removeEventListener("ended", onEnded);
      v.removeEventListener("error", onError);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [play]);

  if (!play) return null;
  const video = (
    <video
      key={play.n}
      ref={videoRef}
      src={play.url}
      muted
      playsInline
      preload="auto"
      style={aspect ? { width: "100%", height: "100%", objectFit: "fill" } : mediaStyle(displayMode)}
    />
  );
  return (
    <div
      data-sm-clip=""
      style={{
        position: "absolute",
        inset: 0,
        zIndex: 1,
        pointerEvents: "none",
        opacity: shown ? 1 : 0,
        transition: `opacity ${fadeMs}ms ease-in-out`,
        willChange: "opacity",
      }}
    >
      {aspect ? <div style={boxFor(aspect, displayMode)}>{video}</div> : video}
    </div>
  );
});
