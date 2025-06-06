import React, { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useIsMobile } from "@/hooks/useIsMobile";
import styles from "./MediaDisplay.module.css";

const EASE_IN_DURATION = 2500;
const EASE_OUT_DURATION = 2500;
const HOLD_FINAL_FRAME = 4000;
const FADE_OUT_TIME = 1000;
const PRIMER_PLAYBACK_DURATION = 1500;
const LOOP_RESTART_DELAY = 2000;
const CROSSFADE_DURATION = 8000;
const FIRST_FRAME_FADE_OUT_DURATION = 500;
const pace = 0.6;
const OPACITY_EASING = "cubic-bezier(0.65, 0, 0.35, 1)";

interface Props {
  src: string;
  fadeInSrc: string | null;
  fadeInVisible: boolean;
  videoKey: string;
  onFadeOutComplete?: () => void;
  fadeOut: boolean;
  shouldPlay: boolean;
  displayMode?: 'fit' | 'fill';
}

export function MediaDisplay({
  src,
  fadeInSrc,
  fadeInVisible,
  videoKey,
  onFadeOutComplete,
  fadeOut,
  shouldPlay,
  displayMode = 'fit',
}: Props) {
  const isVideo = src.includes(".mp4");
  const isMobile = useIsMobile();
  const videoRef = useRef<HTMLVideoElement | null>(null);

  // ❗ clear any lingering video state only when the parent has fully faded out and swapped src
  useEffect(() => {
    cancelAnimationFrame(animationFrame.current!);
    cancelAnimationFrame(rafDebug.current!);
    clearTimeout(easeOutTimeout.current!);
    setFirstFrame(null);
    setShowFirstFrame(false);
    setPaused(false);
  }, [src]);

  const [paused, setPaused] = useState(false);
  const [firstFrame, setFirstFrame] = useState<string | null>(null);
  const [showFirstFrame, setShowFirstFrame] = useState(false); // ✅ replaces paused tie-in

  const [debugInfo, setDebugInfo] = useState<any>({});
  const [searchParams] = useSearchParams();
  const showDebug = searchParams.has("debug");

  const animationFrame = useRef<number>();
  const rafDebug = useRef<number>();
  const easeOutTimeout = useRef<NodeJS.Timeout>();

  const objectFit = displayMode === 'fill' ? 'cover' : 'contain';

  const extractFirstFrame = async () => {
    if (!isVideo || !src) return;

    const video = document.createElement("video");
    video.crossOrigin = "anonymous";
    video.src = src;
    video.muted = true;
    video.playsInline = true;
    video.preload = "auto";

    await new Promise<void>((resolve, reject) => {
      video.addEventListener("loadedmetadata", () => {
        resolve();
      });
      video.addEventListener("error", reject);
    });

    video.currentTime = 0;

    await new Promise<void>((resolve, reject) => {
      const check = () => {
        if (video.readyState >= 2 && !video.seeking) {
          resolve();
        } else {
          requestAnimationFrame(check);
        }
      };
      requestAnimationFrame(check);
    });

    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    const dataUrl = canvas.toDataURL("image/jpeg", 0.9);
    setFirstFrame(dataUrl);
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
    setShowFirstFrame(false);

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

	function easeInOutCubic(t: number): number {
	  return t < 0.5
		? 4 * t * t * t
		: 1 - Math.pow(-2 * t + 2, 3) / 2;
	}
	function easeOutExpo(t: number): number {
	  return t === 1 ? 1 : 1 - Math.pow(2, -10 * t);
	}
	function easeInOutQuad(t: number): number {
	  return t < 0.5
		? 2 * t * t
		: 1 - Math.pow(-2 * t + 2, 2) / 2;
	}

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
		const easedProgress = easeInOutQuad(progress); // use your chosen easing function
        const rawRate = start + (end - start) * easedProgress;
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
                const easeInSeconds = (EASE_IN_DURATION / 1000) * ((pace + 0.065) / 2);
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

					// ✅ Step 4: Hold paused video frame
					setTimeout(() => {
					  // ✅ Step 5: Start fading in firstFrame
					  setShowFirstFrame(true);

					  // ✅ Step 6: Wait until crossfade is finished
					  setTimeout(() => {
						// ✅ Step 7: Hold fully visible frame
						setTimeout(() => {
						  // ✅ Restart
						  setShowFirstFrame(false);
						  setPaused(false);
						  console.log("Go again...");
						  video.play().then(() => {
							beginPlayback();
						  });
						}, LOOP_RESTART_DELAY);
					  }, CROSSFADE_DURATION);
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

  const containerStyle: React.CSSProperties = {
    position: "fixed",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    width: "100%",
    height: "100%",
    backgroundColor: "black",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    pointerEvents: "none",
  };

  const mediaStyle: React.CSSProperties = {
    width: "100%",
    height: "100%",
    objectFit: displayMode === 'fill' ? 'cover' : 'contain',
  };

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
    transition: showFirstFrame
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
    transition: `opacity 1000ms ease`,
    zIndex: 9998,
  };

  return (
    <div style={containerStyle}>
      {isVideo ? (
        <video 
          key={videoKey} 
          ref={videoRef} 
          src={src} 
          muted 
          playsInline 
          style={mediaStyle}
        />
      ) : (
        <img 
          src={src} 
          style={mediaStyle}
          alt="media" 
        />
      )}

      {firstFrame && (
        <img 
          src={firstFrame} 
          alt="first frame" 
          style={firstFrameStyle}
        />
      )}

      <div style={overlayStyle}>
        <div style={{ color: "white", textAlign: "center", fontSize: "2rem" }}>
          Paused on Final Frame
        </div>
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
}
