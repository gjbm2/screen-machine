// The Loope's zoom arithmetic (ZoomSlide.tsx), kept pure so it is tested (zoomMath.test.ts). Coordinates are the
// zoom library's: the content (the fitted picture) sits at (x, y) inside the wrapper (the stage) and is scaled from
// its top-left corner.

export interface Size { w: number; h: number }
export interface Transform { x: number; y: number; scale: number }

/** The picture fitted inside the stage (contain), never enlarged past its own pixels. */
export function fitBox(natural: Size, stage: Size): Size {
  if (!natural.w || !natural.h || !stage.w || !stage.h) return { w: 0, h: 0 };
  const k = Math.min(1, stage.w / natural.w, stage.h / natural.h);
  return { w: Math.round(natural.w * k), h: Math.round(natural.h * k) };
}

/** Where a double tap zooms to: the picture's own pixels (1:1), but at least 2.5x and at most `max`. */
export function detailScale(natural: Size, fitted: Size, max: number): number {
  if (!fitted.w) return Math.min(max, 2.5);
  return Math.min(max, Math.max(2.5, natural.w / fitted.w));
}

/** One axis kept in bounds: content smaller than the room is centred; larger, it may not leave a gap at either edge. */
export function clampAxis(pos: number, size: number, room: number): number {
  if (size <= room) return (room - size) / 2;
  return Math.min(0, Math.max(room - size, pos));
}

/** Zoom to `scale` keeping the point under the finger (p, wrapper coordinates) where it is, within bounds. */
export function zoomAt(from: Transform, p: { x: number; y: number }, scale: number, content: Size, wrapper: Size): Transform {
  const cx = (p.x - from.x) / from.scale;
  const cy = (p.y - from.y) / from.scale;
  return {
    scale,
    x: clampAxis(p.x - cx * scale, content.w * scale, wrapper.w),
    y: clampAxis(p.y - cy * scale, content.h * scale, wrapper.h),
  };
}
