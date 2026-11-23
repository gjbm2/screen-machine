// Loope's full-screen zoom view, for stills and video clips alike.
//
// Rules it keeps, so the view can never be got stuck in:
// - The whole overlay takes gestures (pointer-events: auto). It is portalled outside the Radix
//   dialog, which sets pointer-events: none on <body>; without this only the media itself
//   received touches, so a pinch or tap that landed on the black around it did nothing.
// - The zoomed box is the media's fit box, and its bounds keep it on screen: centred where it
//   is smaller than the viewport, edge to edge where it is larger. It cannot be panned away.
// - Always a way out: the X button, Escape (LoopeModal), a tap on the black, double-tap/click
//   back to fit, or pinch/scroll it smaller than fit and let go.
// - It opens from where the media sat in the carousel. A pinch that starts on the carousel
//   carries straight on here (the fingers' events still target the carousel element, so they
//   are followed on document until one lifts); a double-tap or scroll animates in at the point.
import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import ReactDOM from 'react-dom';
import { TransformWrapper, TransformComponent, type ReactZoomPanPinchContentRef } from 'react-zoom-pan-pinch';
import { Pause, Play, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { type Box, type Pose, fitBox, poseOver, zoomAbout, pinchPose, clampPose } from './zoomGeometry';

export type ZoomEntry =
  | { kind: 'pinch'; ids: [number, number]; m0: { x: number; y: number }; d0: number }
  | { kind: 'point'; x: number; y: number; scale: number | 'detail' };

export interface ZoomOrigin {
  src: string;
  isVideo: boolean;
  /** Where the media was drawn in the carousel (client px). */
  rect: Box;
  /** Natural pixel width (0 if unknown), for the 1:1 step. */
  naturalWidth: number;
  /** The carousel element: a video is paused while zoomed and picks up where the zoom left off. */
  sourceEl: HTMLElement;
  entry: ZoomEntry;
}

const TAP_MS = 300;
const TAP_SLOP = 10;
const DOUBLE_SLOP = 40;
const ANIM_MS = 250;

const LoopeZoom: React.FC<{ origin: ZoomOrigin; onClose: () => void }> = ({ origin, onClose }) => {
  const rootRef = useRef<HTMLDivElement>(null);
  const tw = useRef<ReactZoomPanPinchContentRef | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  // The overlay is fixed inset-0, i.e. the documentElement's client box.
  const [vp, setVp] = useState(() => ({ w: document.documentElement.clientWidth, h: document.documentElement.clientHeight }));
  const [playing, setPlaying] = useState(false);

  // Geometry is fixed for the life of the view except on a viewport resize (then it re-fits).
  const aspect = origin.rect.width / Math.max(1, origin.rect.height);
  const fit = useMemo(() => fitBox(aspect, vp.w, vp.h), [aspect, vp.w, vp.h]);
  const start = useMemo(() => poseOver(fit, origin.rect), []); // eslint-disable-line react-hooks/exhaustive-deps
  const oneToOne = origin.naturalWidth > 0 ? origin.naturalWidth / fit.width : 0;
  const detailScale = oneToOne ? Math.min(4, Math.max(2, oneToOne)) : 2.5;
  // Let go smaller than this and the view closes; anything between it and fit springs back to fit.
  const dismissBelow = Math.min(0.85, start.scale * 0.95);
  const minScale = dismissBelow * 0.75;
  const maxScale = Math.max(8, oneToOne * 2);

  const pose = (): Pose | null => {
    const s = tw.current?.instance.transformState;
    return s ? { x: s.positionX, y: s.positionY, scale: s.scale } : null;
  };
  const apply = (p: Pose, ms = 0) => tw.current?.setTransform(p.x, p.y, p.scale, ms, 'easeOut');
  const toFit = () => apply(clampPose({ x: 0, y: 0, scale: 1 }, fit, vp.w, vp.h), ANIM_MS);

  /** After any gesture: close if let go well under fit, spring back to fit if a little under, else keep on screen. */
  const settle = useCallback(() => {
    const p = pose();
    if (!p) return;
    if (p.scale < dismissBelow) { onClose(); return; }
    if (p.scale < 1) { toFit(); return; }
    const c = clampPose(p, fit, vp.w, vp.h);
    if (Math.abs(c.x - p.x) > 0.5 || Math.abs(c.y - p.y) > 0.5) apply(c, ANIM_MS);
  }, [dismissBelow, fit, vp.w, vp.h, onClose]); // eslint-disable-line react-hooks/exhaustive-deps

  /* -- viewport size ------------------------------------------------------------------------ */
  useLayoutEffect(() => {
    const el = rootRef.current;
    if (!el) return;
    const measure = () => setVp(v => (v.w === el.clientWidth && v.h === el.clientHeight ? v : { w: el.clientWidth, h: el.clientHeight }));
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const lastVp = useRef(vp);
  useEffect(() => {
    // Rotated or really resized: start again from fit. Small jitters (mobile bars) are ignored.
    if (Math.abs(vp.w - lastVp.current.w) < 50 && Math.abs(vp.h - lastVp.current.h) < 50) return;
    lastVp.current = vp;
    toFit();
  }, [vp.w, vp.h]); // eslint-disable-line react-hooks/exhaustive-deps

  /* -- entry -------------------------------------------------------------------------------- */
  const onInit = useCallback((ref: ReactZoomPanPinchContentRef) => {
    tw.current = ref;
    const e = origin.entry;
    if (e.kind === 'point') {
      const target = e.scale === 'detail' ? detailScale : Math.max(1, e.scale);
      // The library's ResizeObserver fires once after init and cancels any running animation,
      // so start ours the frame after that.
      requestAnimationFrame(() => requestAnimationFrame(() =>
        apply(clampPose(zoomAbout(start, e.x, e.y, target), fit, vp.w, vp.h), ANIM_MS)));
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // A pinch begun on the carousel: follow those two fingers until one lifts.
  useEffect(() => {
    const e = origin.entry;
    if (e.kind !== 'pinch') return;
    const find = (list: TouchList) => {
      let a: Touch | undefined, b: Touch | undefined;
      for (let i = 0; i < list.length; i++) {
        if (list[i].identifier === e.ids[0]) a = list[i];
        else if (list[i].identifier === e.ids[1]) b = list[i];
      }
      return a && b ? [a, b] as const : null;
    };
    let done = false;
    const move = (ev: TouchEvent) => {
      const pair = find(ev.touches);
      if (!pair) return;
      ev.preventDefault();
      const [a, b] = pair;
      const m = { x: (a.clientX + b.clientX) / 2, y: (a.clientY + b.clientY) / 2 };
      const d = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
      apply(pinchPose(start, e.m0, e.d0, m, d, minScale, maxScale));
    };
    const end = (ev: TouchEvent) => {
      if (done || find(ev.touches)) return;
      done = true;
      detach();
      settle();
    };
    const opts = { capture: true, passive: false } as AddEventListenerOptions;
    const detach = () => {
      document.removeEventListener('touchmove', move, opts);
      document.removeEventListener('touchend', end, opts);
      document.removeEventListener('touchcancel', end, opts);
    };
    document.addEventListener('touchmove', move, opts);
    document.addEventListener('touchend', end, opts);
    document.addEventListener('touchcancel', end, opts);
    return detach;
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Escape closes. Window capture runs before the dialog's own handlers, which stop the event
  // reaching LoopeModal's window listener (and would otherwise try to dismiss the dialog).
  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key !== 'Escape') return;
      ev.preventDefault();
      ev.stopPropagation();
      onClose();
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [onClose]);

  // iOS Safari zooms the page on its own gesture events unless told not to.
  useEffect(() => {
    const stop = (ev: Event) => ev.preventDefault();
    document.addEventListener('gesturestart', stop, { passive: false } as AddEventListenerOptions);
    return () => document.removeEventListener('gesturestart', stop);
  }, []);

  /* -- video: hand playback over and back ---------------------------------------------------- */
  useEffect(() => {
    if (!origin.isVideo) return;
    const src = origin.sourceEl as HTMLVideoElement;
    const wasPlaying = !src.paused;
    const t = src.currentTime;
    src.pause();
    const v = videoRef.current;
    if (v) {
      v.muted = src.muted;
      const go = () => {
        try { v.currentTime = t; } catch { /* not seekable yet */ }
        if (wasPlaying) v.play().catch(() => {});
      };
      if (v.readyState >= 1) go(); else v.addEventListener('loadedmetadata', go, { once: true });
    }
    return () => {
      if (!v) return;
      try { src.currentTime = v.currentTime; } catch { /* ignore */ }
      src.muted = v.muted;
      if (!v.paused) src.play().catch(() => {});
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const togglePlay = useCallback(() => {
    const v = videoRef.current;
    if (!v) return;
    if (v.paused) v.play().catch(() => {}); else v.pause();
  }, []);

  /* -- taps: one handler for mouse and touch ------------------------------------------------- */
  const tap = useRef({ pointers: 0, multi: false, downAt: 0, x: 0, y: 0, lastAt: 0, lastX: 0, lastY: 0, timer: undefined as ReturnType<typeof setTimeout> | undefined });
  useEffect(() => () => clearTimeout(tap.current.timer), []);

  const isUi = (t: EventTarget | null) => !!(t as Element | null)?.closest?.('[data-zoom-ui]');
  const onPointerDown = (e: React.PointerEvent) => {
    if (isUi(e.target)) return;
    const s = tap.current;
    s.pointers += 1;
    if (s.pointers === 1) { s.multi = false; s.downAt = performance.now(); s.x = e.clientX; s.y = e.clientY; }
    else s.multi = true;
  };
  const onPointerUp = (e: React.PointerEvent) => {
    if (isUi(e.target)) return;
    const s = tap.current;
    s.pointers = Math.max(0, s.pointers - 1);
    if (e.type === 'pointercancel') s.multi = true;
    if (s.pointers > 0 || s.multi) return;
    const now = performance.now();
    if (now - s.downAt > TAP_MS || Math.hypot(e.clientX - s.x, e.clientY - s.y) > TAP_SLOP) return;
    if (now - s.lastAt < TAP_MS && Math.hypot(e.clientX - s.lastX, e.clientY - s.lastY) < DOUBLE_SLOP) {
      clearTimeout(s.timer);
      s.lastAt = 0;
      // Double tap/click: back to fit if zoomed in, else in to detail at the point. Deferred a tick:
      // pointerup comes before the library's mouseup/touchend, whose pan-end animation would cancel ours.
      const { clientX: x, clientY: y } = e;
      setTimeout(() => {
        const p = pose();
        if (!p) return;
        if (p.scale > 1.05) toFit();
        else apply(clampPose(zoomAbout(p, x, y, detailScale), fit, vp.w, vp.h), ANIM_MS);
      }, 0);
      return;
    }
    s.lastAt = now; s.lastX = e.clientX; s.lastY = e.clientY;
    const onMedia = !!(e.target as Element).closest?.('[data-zoom-media]');
    clearTimeout(s.timer);
    // Single tap, once it is clear no second one is coming: black closes; a video toggles play.
    s.timer = setTimeout(() => {
      if (!onMedia) onClose();
      else if (origin.isVideo) togglePlay();
    }, TAP_MS);
  };

  const mediaStyle: React.CSSProperties = { width: '100%', height: '100%', objectFit: 'contain', display: 'block', userSelect: 'none' };

  return ReactDOM.createPortal(
    <div
      ref={rootRef}
      className="fixed inset-0 z-[1000] bg-black/95"
      style={{ pointerEvents: 'auto', touchAction: 'none', overscrollBehavior: 'contain' }}
      onPointerDown={onPointerDown}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      onContextMenu={e => e.preventDefault()}
    >
      <TransformWrapper
        ref={tw}
        initialScale={start.scale}
        initialPositionX={start.x}
        initialPositionY={start.y}
        minScale={minScale}
        maxScale={maxScale}
        limitToBounds
        centerZoomedOut={false}
        centerOnInit={false}
        wheel={{ step: 0.2 }}
        pinch={{ step: 5 }}
        doubleClick={{ disabled: true }}
        panning={{ velocityDisabled: false }}
        onInit={onInit}
        onPinchingStop={settle}
        onWheelStop={settle}
      >
        <TransformComponent
          wrapperStyle={{ width: vp.w, height: vp.h }}
          contentStyle={{ width: fit.width, height: fit.height }}
        >
          {origin.isVideo ? (
            <video
              ref={videoRef}
              data-zoom-media
              src={origin.src}
              loop
              playsInline
              disablePictureInPicture
              onPlay={() => setPlaying(true)}
              onPause={() => setPlaying(false)}
              style={mediaStyle}
            />
          ) : (
            <img
              data-zoom-media
              src={origin.src}
              alt=""
              draggable={false}
              style={mediaStyle}
            />
          )}
        </TransformComponent>
      </TransformWrapper>

      <Button
        data-zoom-ui
        variant="ghost"
        size="icon"
        aria-label="Close zoom"
        className="fixed top-4 right-4 z-[1001] bg-black/80 hover:bg-black text-white rounded-full"
        style={{ touchAction: 'manipulation' }}
        onClick={e => { e.stopPropagation(); onClose(); }}
      >
        <X className="h-5 w-5" />
      </Button>

      {origin.isVideo && (
        <Button
          data-zoom-ui
          variant="ghost"
          size="icon"
          aria-label={playing ? 'Pause' : 'Play'}
          className="fixed bottom-6 left-1/2 -translate-x-1/2 z-[1001] bg-black/80 hover:bg-black text-white rounded-full"
          style={{ touchAction: 'manipulation' }}
          onClick={e => { e.stopPropagation(); togglePlay(); }}
        >
          {playing ? <Pause className="h-5 w-5" /> : <Play className="h-5 w-5" />}
        </Button>
      )}
    </div>,
    document.body,
  );
};

export default LoopeZoom;
