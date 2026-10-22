// Today's brightness and warm layers (default page, no ?ctl=), unchanged. A component of their own so
// their error boundary can isolate them.
export function LegacyMaskLayers({
  brightnessLayer,
  warmHex,
  warmAlpha,
  noColour,
}: {
  brightnessLayer: string;
  warmHex: string;
  warmAlpha: number;
  noColour: boolean;
}) {
  return (
    <>
      {/* Brightness layer */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          backgroundColor: brightnessLayer,
          pointerEvents: "none",
          transition: "background-color 1000ms ease-in-out",
          zIndex: 5,
        }}
      />

      {/* Warm color layer - only hide if nocolour is set */}
      {!noColour && (
        <div
          style={{
            position: "absolute",
            inset: 0,
            backgroundColor: warmHex,
            mixBlendMode: "multiply",
            opacity: warmAlpha,
            pointerEvents: "none",
            transition: "background-color 1000ms ease-in-out, opacity 1000ms ease-in-out",
            zIndex: 6,
          }}
        />
      )}
    </>
  );
}
