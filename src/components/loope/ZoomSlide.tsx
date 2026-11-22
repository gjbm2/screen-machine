import React, { useEffect, useRef, useState } from 'react';
import { TransformWrapper, TransformComponent, type ReactZoomPanPinchRef } from 'react-zoom-pan-pinch';
import { Loader2 } from 'lucide-react';
import { detailScale, fitBox, zoomAt, type Size } from './zoomMath';

const MAX_SCALE = 8;
const TAP_MS = 280;
const TAP_SLOP = 12;

interface Props {
  src: string;
  alt: string;
  /** Only the slide in view zooms; leaving it drops its zoom. */
  active: boolean;
  /** Load now (the slide in view and its neighbours). */
  near: boolean;
  /** The stage's size: the picture is fitted to it, and a new size (a phone turned) starts the zoom afresh. */
  stage: Size;
  /** Zoomed in or back to fit; the carousel only swipes at fit. */
  onZoomed: (zoomed: boolean) => void;
  /** The zoom controls of the slide in view, for Escape. */
  onApi: (api: ReactZoomPanPinchRef | null) => void;
  /** A single tap (not part of a double tap, a pan or a pinch): shows or hides the viewer's controls. */
  onTap: () => void;
}

/**
 * One picture in the Loope, zoomable in place: pinch, wheel or double tap/click zoom it; zoomed, one finger (or the
 * mouse) pans; it never goes smaller than fit, so there is always a way back (owner, 22 Nov: "easy to get 'stuck'
 * after pinch zooming & panning"). The picture is its own fitted size inside the zoom area, so panning stops at its
 * edges rather than in the black around it.
 */
export function ZoomSlide({ src, alt, active, near, stage, onZoomed, onApi, onTap }: Props) {
  const [natural, setNatural] = useState<Size | null>(null);
  const api = useRef<ReactZoomPanPinchRef | null>(null);
  const [zoomed, setZoomed] = useState(false);
  const fitted = natural ? fitBox(natural, stage) : null;

  // taps: one pointer, short, still; two within TAP_MS is a double tap
  const press = useRef<{ x: number; y: number; t: number; multi: boolean } | null>(null);
  const lastTap = useRef<{ x: number; y: number; t: number } | null>(null);
  const tapTimer = useRef<number | undefined>(undefined);
  const pointers = useRef(0);
  // a double tap's touchend is swallowed: the mouse events a browser makes up after it would start the library's
  // mouse pan, which cancels the zoom just begun
  const swallow = useRef(false);

  useEffect(() => () => window.clearTimeout(tapTimer.current), []);
  useEffect(() => {
    if (active) return;
    setZoomed(false);
    onZoomed(false);
    api.current = null;
    onApi(null);
  }, [active, onZoomed, onApi]);

  const toggleZoom = (clientX: number, clientY: number) => {
    const ref = api.current;
    const wrapper = ref?.instance.wrapperComponent;
    if (!ref || !wrapper || !natural || !fitted) return;
    const { scale, positionX, positionY } = ref.instance.transformState;   // live (ref.state is from its start)
    if (scale > 1.01) {
      ref.centerView(1, 250);
      return;
    }
    const r = wrapper.getBoundingClientRect();
    const t = zoomAt({ x: positionX, y: positionY, scale }, { x: clientX - r.left, y: clientY - r.top },
      detailScale(natural, fitted, MAX_SCALE), fitted, { w: r.width, h: r.height });
    ref.setTransform(t.x, t.y, t.scale, 250);
  };

  const onPointerDown = (e: React.PointerEvent) => {
    // the first finger of a gesture is the primary pointer: the count starts again there
    pointers.current = e.isPrimary ? 1 : pointers.current + 1;
    if (pointers.current > 1) {
      if (press.current) press.current.multi = true;
      return;
    }
    press.current = { x: e.clientX, y: e.clientY, t: Date.now(), multi: false };
  };

  const onPointerUp = (e: React.PointerEvent) => {
    pointers.current = Math.max(0, pointers.current - 1);
    const p = press.current;
    if (pointers.current > 0 || !p) return;
    press.current = null;
    const now = Date.now();
    if (p.multi || now - p.t > TAP_MS || Math.hypot(e.clientX - p.x, e.clientY - p.y) > TAP_SLOP) return;
    const last = lastTap.current;
    if (last && now - last.t < TAP_MS && Math.hypot(e.clientX - last.x, e.clientY - last.y) < 40) {
      lastTap.current = null;
      window.clearTimeout(tapTimer.current);
      swallow.current = e.pointerType === 'touch';
      // after this click's mouseup: the library ends its mouse pan there, which would cancel the zoom
      const { clientX, clientY } = e;
      window.setTimeout(() => toggleZoom(clientX, clientY), 0);
      return;
    }
    lastTap.current = { x: e.clientX, y: e.clientY, t: now };
    window.clearTimeout(tapTimer.current);
    tapTimer.current = window.setTimeout(onTap, TAP_MS);
  };

  const onTouchEnd = (e: React.TouchEvent) => {
    if (!swallow.current) return;
    swallow.current = false;
    e.preventDefault();
  };

  const onPointerCancel = () => {
    pointers.current = Math.max(0, pointers.current - 1);
    press.current = null;
  };

  const loader = !natural && (
    <div className="absolute inset-0 flex flex-col items-center justify-center text-white/70 pointer-events-none">
      <Loader2 className="h-8 w-8 animate-spin mb-2" />
      <span className="text-sm">Loading image...</span>
    </div>
  );

  const measure = (e: React.SyntheticEvent<HTMLImageElement>) => {
    const img = e.currentTarget;
    if (img.naturalWidth) setNatural({ w: img.naturalWidth, h: img.naturalHeight });
  };

  // Not in view, or not measured yet: a plain fitted picture (it measures itself as it loads).
  if (!active || !fitted || !fitted.w) {
    return (
      <div className="relative h-full w-full flex items-center justify-center" onPointerDown={onPointerDown}
           onPointerUp={onPointerUp} onPointerCancel={onPointerCancel} onTouchEnd={onTouchEnd}>
        {loader}
        <img
          src={near || natural ? src : undefined}
          alt={alt}
          draggable={false}
          onLoad={measure}
          className={`max-h-full max-w-full object-contain select-none ${natural ? '' : 'opacity-0'}`}
        />
      </div>
    );
  }

  return (
    <div className="h-full w-full" onPointerDown={onPointerDown} onPointerUp={onPointerUp} onPointerCancel={onPointerCancel} onTouchEnd={onTouchEnd}>
      <TransformWrapper
        key={`${stage.w}x${stage.h}`}
        minScale={1}
        maxScale={MAX_SCALE}
        limitToBounds
        centerOnInit
        centerZoomedOut
        doubleClick={{ disabled: true }}
        wheel={{ step: 0.15 }}
        // at fit a one-finger swipe belongs to the carousel; zoomed, it pans the picture
        panning={{ disabled: !zoomed, velocityDisabled: true }}
        onInit={(ref) => {
          api.current = ref;
          onApi(ref);
        }}
        onTransformed={(_ref, s) => {
          const z = s.scale > 1.01;
          if (z !== zoomed) {
            setZoomed(z);
            onZoomed(z);
          }
        }}
      >
        <TransformComponent wrapperStyle={{ width: '100%', height: '100%' }}>
          <img
            src={src}
            alt={alt}
            draggable={false}
            className="select-none"
            style={{ width: fitted.w, height: fitted.h, maxWidth: 'none' }}
          />
        </TransformComponent>
      </TransformWrapper>
    </div>
  );
}
