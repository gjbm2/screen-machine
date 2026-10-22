// The still/clip layer of the art stack for the controlled (?ctl=) and art-only pages.
//
// Renders the same DOM and styles as MediaDisplay.tsx (which stays untouched for the default page),
// with two additions the live cut needs:
//   * `hold`: pause a clip on one frame (freeze, or while the art stack is hidden under live);
//   * an imperative handle that reports what is drawn (`kind`, `clip_t`) and resolves once the base
//     is decoded, so a freeze ack, an exit cut or an art-only render never races the decoder.
// mode="cycle": today's behaviour (through-black fade-out, slow-motion clip loop).
// mode="static": the art-only page (no fades, no loop; a clip is shown paused at `clipT`).
import React, { forwardRef, useEffect, useImperativeHandle, useLayoutEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { Fit } from "../control/protocol";
import { ClipCycle, CROSSFADE_DURATION, FIRST_FRAME_FADE_OUT_DURATION } from "./ClipCycle";
import { isClip, mediaContainerStyle, mediaStyle } from "./styles";

const FADE_OUT_TIME = 1000;
const OPACITY_EASING = "cubic-bezier(0.65, 0, 0.35, 1)";
const READY_TIMEOUT_MS = 4000;

export interface ArtMediaHandle {
  kind(): "image" | "video";
  /** Position of a held/static clip, else null (a playing clip has no single frame). */
  clipTime(): number | null;
  /** Resolves once the base is decoded (img.decode) or a held/static clip shows its frame. */
  ready(): Promise<void>;
  element(): HTMLImageElement | HTMLVideoElement | null;
}

interface Props {
  src: string;
  videoKey: string;
  displayMode: Fit;
  mode: "cycle" | "static";
  /** Overrides the ".mp4" test (the art-only page is told the kind in its ArtState). */
  kind?: "image" | "video";
  fadeOut?: boolean;
  onFadeOutComplete?: () => void;
  shouldPlay?: boolean;
  hold?: boolean;
  clipT?: number | null;
}

function withTimeout(p: Promise<unknown>, ms: number): Promise<void> {
  return new Promise((resolve) => {
    const t = setTimeout(resolve, ms);
    p.then(
      () => {
        clearTimeout(t);
        resolve();
      },
      () => {
        clearTimeout(t);
        resolve();
      },
    );
  });
}

function waitEvent(el: HTMLMediaElement, ok: string, ms: number): Promise<void> {
  return new Promise((resolve) => {
    const done = () => {
      clearTimeout(t);
      el.removeEventListener(ok, done);
      el.removeEventListener("error", done);
      resolve();
    };
    const t = setTimeout(done, ms);
    el.addEventListener(ok, done);
    el.addEventListener("error", done);
  });
}

function twoFrames(): Promise<void> {
  return new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => r())));
}

/** Seek a paused clip to `t` and wait for the frame (art-only renders). */
async function seekClip(v: HTMLVideoElement, t: number): Promise<void> {
  v.pause();
  if (v.readyState < 1) await waitEvent(v, "loadedmetadata", READY_TIMEOUT_MS);
  const target = Math.max(0, Number.isFinite(v.duration) ? Math.min(t, v.duration) : t);
  const seeked = waitEvent(v, "seeked", READY_TIMEOUT_MS);
  v.currentTime = target; // always seeks, even to the current position, so `seeked` fires
  await seeked;
  const t0 = performance.now();
  while (v.readyState < 2 && performance.now() - t0 < READY_TIMEOUT_MS) await twoFrames();
}

export const ArtMedia = forwardRef<ArtMediaHandle, Props>(function ArtMedia(
  { src, videoKey, displayMode, mode, kind, fadeOut = false, onFadeOutComplete, shouldPlay = false, hold = false, clipT = null },
  ref,
) {
  const isVideo = kind ? kind === "video" : isClip(src);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const cycleRef = useRef<ClipCycle | null>(null);
  const holdRef = useRef(hold);
  const holdDone = useRef<Promise<void>>(Promise.resolve());
  const onFadeOutCompleteRef = useRef(onFadeOutComplete);
  onFadeOutCompleteRef.current = onFadeOutComplete;

  const [paused, setPaused] = useState(false);
  const [firstFrame, setFirstFrame] = useState<string | null>(null);
  const [showFirstFrame, setShowFirstFrame] = useState(false);
  const [snapFirstFrame, setSnapFirstFrame] = useState(false);
  const [debugInfo, setDebugInfo] = useState<{ currentTime?: string; playbackRate?: string; duration?: string }>({});
  const [searchParams] = useSearchParams();
  const showDebug = mode === "cycle" && searchParams.has("debug");

  useImperativeHandle(
    ref,
    () => ({
      kind: () => (isVideo ? "video" : "image"),
      clipTime: () => {
        const v = videoRef.current;
        if (!isVideo || !v) return null;
        if (mode === "static") return clipT ?? 0;
        return holdRef.current ? v.currentTime : null;
      },
      ready: () => {
        if (isVideo) {
          const v = videoRef.current;
          if (!v) return Promise.resolve();
          if (mode === "static") return withTimeout(seekClip(v, clipT ?? 0), READY_TIMEOUT_MS * 2);
          return withTimeout(holdDone.current, READY_TIMEOUT_MS);
        }
        const img = imgRef.current;
        return img ? withTimeout(img.decode(), READY_TIMEOUT_MS) : Promise.resolve();
      },
      element: () => (isVideo ? videoRef.current : imgRef.current),
    }),
    [isVideo, mode, clipT],
  );

  // New base: clear any clip state (as MediaDisplay does).
  useEffect(() => {
    setFirstFrame(null);
    setShowFirstFrame(false);
    setSnapFirstFrame(false);
    setPaused(false);
  }, [src]);

  // Extract the clip's first frame for the end-of-loop cross-fade (as MediaDisplay does).
  useEffect(() => {
    if (mode !== "cycle" || !isVideo || !src) return;
    let cancelled = false;
    const v = document.createElement("video");
    v.crossOrigin = "anonymous";
    v.muted = true;
    v.playsInline = true;
    v.preload = "auto";
    v.src = src;
    const release = () => {
      v.removeAttribute("src");
      v.load(); // free the decoder (today's version leaves it to GC)
    };
    (async () => {
      await waitEvent(v, "loadedmetadata", 15000);
      if (cancelled) return;
      v.currentTime = 0;
      const t0 = performance.now();
      while ((v.readyState < 2 || v.seeking) && performance.now() - t0 < 15000) {
        await twoFrames();
        if (cancelled) return;
      }
      const canvas = document.createElement("canvas");
      canvas.width = v.videoWidth;
      canvas.height = v.videoHeight;
      const ctx = canvas.getContext("2d");
      if (!ctx || !canvas.width) return;
      ctx.drawImage(v, 0, 0, canvas.width, canvas.height);
      if (!cancelled) setFirstFrame(canvas.toDataURL("image/jpeg", 0.9));
    })()
      .catch((e) => console.warn("[ArtMedia] first-frame extraction failed", e))
      .finally(release);
    return () => {
      cancelled = true;
    };
  }, [src, mode, isVideo]);

  // Through-black fade-out: after FADE_OUT_TIME the parent swaps the source.
  useEffect(() => {
    if (!fadeOut) return;
    // As MediaDisplay: the callback of the render that started the fade is the one called.
    const done = onFadeOutCompleteRef.current;
    cycleRef.current?.dispose();
    setPaused(false);
    setShowFirstFrame(false);
    const t = setTimeout(() => done?.(), FADE_OUT_TIME);
    return () => clearTimeout(t);
  }, [fadeOut]);

  // The slow-motion loop.
  useEffect(() => {
    if (mode !== "cycle" || !isVideo || !shouldPlay) return;
    const v = videoRef.current;
    if (!v) return;
    const cycle = new ClipCycle(v, {
      setPaused,
      setShowFirstFrame: (s) => {
        if (s) setSnapFirstFrame(false);
        setShowFirstFrame(s);
      },
      snapFirstFrameOff: () => {
        setSnapFirstFrame(true);
        setShowFirstFrame(false);
      },
      log: showDebug ? (...a: unknown[]) => console.log("[ArtMedia]", ...a) : undefined,
    });
    cycleRef.current = cycle;
    if (holdRef.current) holdDone.current = cycle.hold();
    cycle.start();
    return () => {
      cycle.dispose();
      if (cycleRef.current === cycle) cycleRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shouldPlay, videoKey, src, mode]);

  // Hold / release a clip.
  useLayoutEffect(() => {
    if (holdRef.current === hold) return;
    holdRef.current = hold;
    const cycle = cycleRef.current;
    if (hold) {
      if (cycle) holdDone.current = cycle.hold();
      else if (videoRef.current) {
        videoRef.current.pause();
        holdDone.current = Promise.resolve();
      }
    } else {
      cycle?.resume();
    }
  }, [hold]);

  // ?debug: today's time/speed readout.
  useEffect(() => {
    if (!showDebug || !isVideo) return;
    let raf = 0;
    const tick = () => {
      const v = videoRef.current;
      if (v) {
        setDebugInfo({
          currentTime: v.currentTime.toFixed(2),
          playbackRate: v.playbackRate.toFixed(2),
          duration: v.duration.toFixed(2),
        });
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [showDebug, isVideo, videoKey]);

  const media = mediaStyle(displayMode);

  const overlayStyle: React.CSSProperties = {
    position: "fixed",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    opacity: paused && showDebug ? 1 : 0,
    transition: "opacity 1000ms ease",
    zIndex: 1,
  };

  const firstFrameStyle: React.CSSProperties = {
    position: "fixed",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    opacity: showFirstFrame ? 1 : 0,
    transition: snapFirstFrame
      ? "none"
      : showFirstFrame
        ? `opacity ${CROSSFADE_DURATION}ms ${OPACITY_EASING}`
        : `opacity ${FIRST_FRAME_FADE_OUT_DURATION}ms ${OPACITY_EASING}`,
    zIndex: 2,
  };

  const fadeOverlayStyle: React.CSSProperties = {
    position: "fixed",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    backgroundColor: "black",
    opacity: fadeOut ? 1 : 0,
    transition: mode === "static" ? "none" : `opacity 1000ms ease`,
    zIndex: 9998,
  };

  return (
    <div style={mediaContainerStyle}>
      {isVideo ? (
        <video
          key={videoKey}
          ref={videoRef}
          src={src}
          muted
          playsInline
          preload={mode === "static" || hold ? "auto" : undefined}
          style={media}
        />
      ) : (
        <img ref={imgRef} src={src} style={media} alt="media" />
      )}

      {firstFrame && <img src={firstFrame} alt="first frame" style={firstFrameStyle} />}

      <div style={overlayStyle}>
        <div style={{ color: "white", textAlign: "center", fontSize: "2rem" }}>Paused on Final Frame</div>
      </div>

      <div style={fadeOverlayStyle} />

      {showDebug && (
        <div
          style={{
            position: "fixed",
            bottom: 10,
            left: 10,
            background: "rgba(0,0,0,0.6)",
            color: "white",
            padding: "0.5em 1em",
            borderRadius: 8,
            fontFamily: "monospace",
            fontSize: 14,
            zIndex: 9999,
          }}
        >
          <div>Time: {debugInfo.currentTime}s</div>
          <div>Speed: {debugInfo.playbackRate}x</div>
          <div>Duration: {debugInfo.duration}s</div>
        </div>
      )}
    </div>
  );
});
