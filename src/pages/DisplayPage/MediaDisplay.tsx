import React, { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

const EASE_IN_DURATION = 2000;
const EASE_OUT_DURATION = 2000;
const HOLD_FINAL_FRAME = 2000;
const FADE_OUT_TIME = 1000;
const PRIMER_PLAYBACK_DURATION = 1500;
const B_EASE_OUT_TIME = 1000;
const pace = 0.75;

interface Props {
  src: string;
  fadeInSrc: string | null;
  fadeInVisible: boolean;
  videoKey: string;
  onFadeOutComplete?: () => void;
  fadeOut: boolean;
  shouldPlay: boolean;
}

export function MediaDisplay({
  src,
  fadeInSrc,
  fadeInVisible,
  videoKey,
  onFadeOutComplete,
  fadeOut,
  shouldPlay,
}: Props) {
  const isVideo = src.includes(".mp4");

  const videoRef = useRef<HTMLVideoElement | null>(null);

  const [paused, setPaused] = useState(false);
  const [firstFrame, setFirstFrame] = useState<string | null>(null);

  const [debugInfo, setDebugInfo] = useState<any>({});
  const [searchParams] = useSearchParams();
  const showDebug = searchParams.has("debug");

  const animationFrame = useRef<number>();
  const rafDebug = useRef<number>();
  const easeOutTimeout = useRef<NodeJS.Timeout>();

  const extractFirstFrame = async () => {
    if (!isVideo || !src) return;
    const video = document.createElement("video");
    video.crossOrigin = "anonymous";
    video.src = src;
    video.muted = true;
    video.playsInline = true;
    video.preload = "auto";

    video.addEventListener("loadeddata", () => {
      const canvas = document.createElement("canvas");
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;

      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
      const dataUrl = canvas.toDataURL("image/jpeg", 0.9);
      setFirstFrame(dataUrl);
    });
  };

  useEffect(() => {
    extractFirstFrame();
  }, [src]);

  useEffect(() => {
    if (!fadeOut) return;
    console.log("[MediaDisplay] FADE OUT triggered");

    cancelAnimationFrame(animationFrame.current!);
    cancelAnimationFrame(rafDebug.current!);
    clearTimeout(easeOutTimeout.current!);
    setPaused(false);

    const timeout = setTimeout(() => {
      console.log("[MediaDisplay] FADE OUT COMPLETE — calling onFadeOutComplete()");
      onFadeOutComplete?.();
    }, FADE_OUT_TIME);

    return () => clearTimeout(timeout);
  }, [fadeOut]);

  useEffect(() => {
    if (!isVideo || !shouldPlay) return;

    const video = videoRef.current;
    if (!video) return;

    const log = (...args: any[]) => {
      if (showDebug) console.log("[MediaDisplay]", ...args);
    };

    const updateDebug = () => {
      setDebugInfo({
        currentTime: video.currentTime.toFixed(2),
        playbackRate: video.playbackRate.toFixed(2),
        duration: video.duration.toFixed(2),
      });
      rafDebug.current = requestAnimationFrame(updateDebug);
    };

    const easePlaybackRate = (
      start: number,
      end: number,
      duration: number,
      onComplete?: () => void
    ) => {
      const startTime = performance.now();
      const step = (now: number) => {
        const elapsed = now - startTime;
        const progress = Math.min(1, elapsed / duration);
        const rawRate = start + (end - start) * progress;
        const clampedRate = Math.max(0.0625, Math.min(16, rawRate));
        video.playbackRate = clampedRate;
        if (progress < 1) {
          animationFrame.current = requestAnimationFrame(step);
        } else {
          log(`Ease complete. playbackRate=${clampedRate}`);
          onComplete?.();
        }
      };
      animationFrame.current = requestAnimationFrame(step);
    };

    const beginPlayback = () => {
      log("\n=== Begin Playback Cycle ===");
      setPaused(false);
      video.pause();
      video.playbackRate = 0.065;
      video.currentTime = 0;

      const ensureFrameRendered = () =>
        new Promise<void>((resolve) => {
          const checkFrame = () => {
            requestAnimationFrame(() => {
              if (video.readyState >= 2 && !video.seeking) resolve();
              else checkFrame();
            });
          };
          checkFrame();
        });

      video.addEventListener(
        "seeked",
        async () => {
          log("Video seeked to frame 0.");
          await ensureFrameRendered();

          video.play().then(() => {
            log(`Primer playback started (${PRIMER_PLAYBACK_DURATION}ms at 0.065x)`);

            setTimeout(() => {
              log("Ease-in from primer");
              easePlaybackRate(0.065, 1.0 * pace, EASE_IN_DURATION, () => {
                log("A at full speed now.");

                const primerSeconds = (PRIMER_PLAYBACK_DURATION / 1000) * 0.065;
                const easeInSeconds = (EASE_IN_DURATION / 1000) * ((1 + 0.065) / 2);
                const totalDelay = primerSeconds + easeInSeconds;
                const playUntil = Math.max(
                  0,
                  (video.duration / pace - totalDelay - EASE_OUT_DURATION / 1000) * 1000
                );

                easeOutTimeout.current = setTimeout(() => {
                  log("Easing out...");
                  easePlaybackRate(1.0 * pace, 0.065, EASE_OUT_DURATION, () => {
                    video.pause();
                    setPaused(true);

                    setTimeout(() => {
                      video.pause();
                      video.currentTime = 0;
                      video.playbackRate = 0.065;

                      video.play().then(() => {
                        setTimeout(() => {
                          beginPlayback();
                        }, B_EASE_OUT_TIME);
                      });
                    }, HOLD_FINAL_FRAME);
                  });
                }, playUntil);
              });
            }, PRIMER_PLAYBACK_DURATION);
          });
        },
        { once: true }
      );
    };

    const maybeStartPlayback = () => {
      console.log("***maybeStartPlayback");
      video.load();
      if (video.readyState >= 1) {
        beginPlayback();
      } else {
        video.addEventListener("loadedmetadata", beginPlayback, { once: true });
      }
    };

    maybeStartPlayback();
    if (showDebug) rafDebug.current = requestAnimationFrame(updateDebug);

    return () => {
      cancelAnimationFrame(animationFrame.current!);
      cancelAnimationFrame(rafDebug.current!);
      clearTimeout(easeOutTimeout.current!);
      video.removeEventListener("loadedmetadata", beginPlayback);
    };
  }, [shouldPlay]);

  const videoStyle: React.CSSProperties = {
    position: "absolute",
    top: 0,
    left: 0,
    width: "100%",
    height: "100%",
    objectFit: "contain",
    zIndex: 0,
  };

  const overlayStyle: React.CSSProperties = {
    ...videoStyle,
    zIndex: 1,
    opacity: paused && showDebug ? 1 : 0,
    transition: "opacity 1000ms ease",
    pointerEvents: "none",
  };

  const firstFrameStyle: React.CSSProperties = {
    ...videoStyle,
    zIndex: 2,
    opacity: paused ? 1 : 0,
    transition: "opacity 1000ms ease",
    pointerEvents: "none",
  };

const fadeOverlayStyle: React.CSSProperties = {
  position: "absolute",
  top: 0,
  left: 0,
  width: "100%",
  height: "100%",
  backgroundColor: "black",
  opacity: fadeOut ? 1 : 0,
  transition: `opacity ${EASE_OUT_DURATION}ms ease`,
  zIndex: 9998,
  pointerEvents: "none",
};


  return (
    <>
      {isVideo ? (
        <video key={videoKey} ref={videoRef} src={src} muted playsInline style={videoStyle} />
      ) : (
        <img src={src} style={videoStyle} alt="media" />
      )}

      {firstFrame && (
        <img src={firstFrame} alt="first frame" style={firstFrameStyle} />
      )}

      <div style={overlayStyle}>
        <div style={{ color: "white", textAlign: "center", fontSize: "2rem", marginTop: "50%" }}>
          Paused on Final Frame
        </div>
      </div>

	  <div style={fadeOverlayStyle} />

      {showDebug && (
        <div
          style={{
            position: "absolute",
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
    </>
  );
}
