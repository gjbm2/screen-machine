
// src/pages/DisplayPage/OverlayContainer.tsx
import React from "react";

const OVERLAY_FADEIN_DURATION = 2000;
const OVERLAY_FADEOUT_DURATION = 5000;

export interface Overlay {
  id: string;
  html: string;
  position?: string;
  visible: boolean;
  fadein?: number;
}

function getOverlayStyle(
  position: string,
  visible: boolean,
  fadein?: number
): React.CSSProperties {
  const fadeDuration = visible
    ? typeof fadein === "number"
      ? fadein
      : OVERLAY_FADEIN_DURATION
    : OVERLAY_FADEOUT_DURATION;

  const baseStyle: React.CSSProperties = {
    position: "absolute",
    zIndex: 10000,
    opacity: visible ? 1 : 0,
    transition: fadein === 0 ? "none" : `opacity ${fadeDuration}ms ease`,
    pointerEvents: "none" as React.CSSProperties["pointerEvents"],
  };

  switch (position) {
    case "top-left":
      return { ...baseStyle, top: "20px", left: "20px" };
    case "top-center":
      return { ...baseStyle, top: "20px", left: "50%", transform: "translateX(-50%)" };
    case "top-right":
      return { ...baseStyle, top: "20px", right: "20px" };
    case "bottom-left":
      return { ...baseStyle, bottom: "20px", left: "20px" };
    case "bottom-center":
      return { ...baseStyle, bottom: "20px", left: "50%", transform: "translateX(-50%)" };
    case "bottom-right":
      return { ...baseStyle, bottom: "20px", right: "20px" };
    default:
      return baseStyle;
  }
}

export function OverlayContainer({ overlays }: { overlays: Overlay[] }) {
  return (
    <div
      style={{
        position: "absolute",
        top: 0,
        left: 0,
        width: "100vw",
        height: "100vh",
        zIndex: 9999,
        pointerEvents: "none" as React.CSSProperties["pointerEvents"],
      }}
    >
      {overlays.map((o) => {
        const hasPosition = !!o.position;

        const wrapperStyle: React.CSSProperties = hasPosition
          ? {
              ...getOverlayStyle(o.position!, o.visible, o.fadein),
              fontSize: "clamp(0.5rem, 2.5vmin, 1.8rem)",
              display: "block",
              maxWidth: "100%",
            }
          : {
              // Give the HTML a fullscreen canvas to position itself
              position: "absolute",
              top: 0,
              left: 0,
              width: "100vw",
              height: "100vh",
              zIndex: 10000,
              opacity: o.visible ? 1 : 0,
              transition: `opacity ${(o.fadein || OVERLAY_FADEIN_DURATION)}ms ease`,
              pointerEvents: "none" as React.CSSProperties["pointerEvents"],
              fontSize: "clamp(0.5rem, 2.5vmin, 1.8rem)",
            };

        return (
          <div
            key={o.id}
            style={wrapperStyle}
            dangerouslySetInnerHTML={{ __html: o.html }}
          />
        );
      })}
    </div>
  );
}
