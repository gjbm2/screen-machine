// Geometry for the Loope zoom view. The zoomed content box is the media's "fit" box
// (contained in the viewport); a pose is its screen translate (x, y) and scale, origin top-left,
// as react-zoom-pan-pinch applies it. Pure, so it is tested with plain node:
//   node --experimental-strip-types --test src/components/loope/zoomGeometry.test.ts

export interface Box { left: number; top: number; width: number; height: number }
export interface Pose { x: number; y: number; scale: number }

/** The media's size and place when contained (letterboxed) in a vw x vh viewport. */
export function fitBox(aspect: number, vw: number, vh: number): Box {
  const a = aspect > 0 && Number.isFinite(aspect) ? aspect : 1;
  const width = Math.min(vw, vh * a);
  const height = width / a;
  return { left: (vw - width) / 2, top: (vh - height) / 2, width, height };
}

/** The pose that shows a fit-sized content box exactly over `target` on screen. */
export function poseOver(fit: Box, target: Box): Pose {
  return { x: target.left, y: target.top, scale: target.width / fit.width };
}

/** Rescale to `scale`, keeping the content point under screen point (px, py) where it is. */
export function zoomAbout(pose: Pose, px: number, py: number, scale: number): Pose {
  const cx = (px - pose.x) / pose.scale;
  const cy = (py - pose.y) / pose.scale;
  return { x: px - cx * scale, y: py - cy * scale, scale };
}

/** Pinch: the content point under the starting midpoint follows the current midpoint. */
export function pinchPose(
  start: Pose, m0: { x: number; y: number }, d0: number,
  m: { x: number; y: number }, d: number, minScale: number, maxScale: number,
): Pose {
  const scale = Math.min(maxScale, Math.max(minScale, start.scale * (d0 > 0 ? d / d0 : 1)));
  const cx = (m0.x - start.x) / start.scale;
  const cy = (m0.y - start.y) / start.scale;
  return { x: m.x - cx * scale, y: m.y - cy * scale, scale };
}

/** Keep the media on screen: centred on an axis where it is smaller than the viewport,
 *  otherwise covering it edge to edge (no panning past its edges into black). */
export function clampPose(pose: Pose, fit: Box, vw: number, vh: number): Pose {
  const axis = (t: number, size: number, view: number) => {
    const s = size * pose.scale;
    if (s <= view) return (view - s) / 2;
    return Math.min(0, Math.max(view - s, t));
  };
  return { x: axis(pose.x, fit.width, vw), y: axis(pose.y, fit.height, vh), scale: pose.scale };
}
