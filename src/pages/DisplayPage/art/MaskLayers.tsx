// The brightness and warm layers, written imperatively by a MaskController (no React state per value).
// The warm layer is not rendered with ?nocolour, exactly as today.
import { brightnessLayerStyle, warmLayerStyle } from "./styles";
import type { MaskController } from "./MaskController";

export function MaskLayers({ controller, noColour }: { controller: MaskController; noColour: boolean }) {
  return (
    <>
      <div ref={controller.attachBrightness} data-sm-mask="brightness" style={brightnessLayerStyle} />
      {!noColour && <div ref={controller.attachWarm} data-sm-mask="warm" style={warmLayerStyle} />}
    </>
  );
}
