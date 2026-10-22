// Style objects shared by the art stack of the controlled page and the art-only page.
// The media styles are copies of today's values in MediaDisplay.tsx and index.tsx; the mask layers
// animate opacity instead of background-color (rule 11). The controlled and art-only pages share all
// of them, so they render the same pixels for the same state (verified by screenshot diff; README.md).
import type React from "react";
import type { Fit } from "../control/protocol";

/** A clip base? The same test as MediaDisplay.tsx. */
export function isClip(src: string): boolean {
  return src.includes(".mp4");
}

/** The page's root container (index.tsx): the art stack. */
export const rootContainerStyle: React.CSSProperties = {
  position: "fixed",
  inset: 0,
  width: "100vw",
  height: "100vh",
  backgroundColor: "black",
};

/** MediaDisplay's container. */
export const mediaContainerStyle: React.CSSProperties = {
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

export function mediaStyle(fit: Fit): React.CSSProperties {
  return { width: "100%", height: "100%", objectFit: fit === "fill" ? "cover" : "contain" };
}

/**
 * Brightness layer: static black; MaskController writes `opacity` = 1 - brightness (LIVE_V1 §2.2
 * rule 11: compositor-only, the same alpha over black as today's rgba(0,0,0,1-brightness)).
 */
export const brightnessLayerStyle: React.CSSProperties = {
  position: "absolute",
  inset: 0,
  backgroundColor: "black",
  pointerEvents: "none",
  willChange: "opacity",
  zIndex: 5,
};

/** Warm layer: MaskController writes background-color (no transition) and opacity. */
export const warmLayerStyle: React.CSSProperties = {
  position: "absolute",
  inset: 0,
  mixBlendMode: "multiply",
  pointerEvents: "none",
  willChange: "opacity",
  zIndex: 6,
};
