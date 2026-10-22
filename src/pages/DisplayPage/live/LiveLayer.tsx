// The live layer's container: React renders only this empty box (z-index 1, under the art stack);
// LiveController puts the MSE <video> in it imperatively. No CSS effects on it or its ancestors.
import React from "react";

const liveLayerStyle: React.CSSProperties = {
  position: "fixed",
  inset: 0,
  width: "100vw",
  height: "100vh",
  zIndex: 1,
  backgroundColor: "black",
  pointerEvents: "none",
};

export function LiveLayer({ containerRef }: { containerRef: (el: HTMLDivElement | null) => void }) {
  return <div ref={containerRef} data-sm-live-layer="" style={liveLayerStyle} />;
}
