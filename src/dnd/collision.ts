// The page's collision detection: the drop zone under the pointer (pickDrop.ts says why). The keyboard has no pointer,
// so it keeps dnd-kit's closestCenter.
import { closestCenter, type Collision, type CollisionDetection } from '@dnd-kit/core';
import { pickDrop, type Zone } from './pickDrop';

/** Give every <DragOverlay> this class: the picture following the pointer is not what the drag is over. */
export const DRAG_OVERLAY_CLASS = 'dnd-drag-overlay';

/** The card being dragged: dnd-kit marks it pressed while it drags. */
const draggedNode = (): Element | null =>
  document.querySelector('[aria-roledescription="draggable"][aria-pressed="true"]');

/** What is drawn at the point, looking past the dragged card and the overlay (they sit under the pointer). */
function drawnAt(x: number, y: number, dragged: Element | null): Element | null {
  for (const el of document.elementsFromPoint(x, y)) {
    if (dragged?.contains(el) || el.closest(`.${DRAG_OVERLAY_CLASS}`)) continue;
    return el;
  }
  return null;
}

/** In a bar that stays put while the page scrolls (the pinned tab bar, the pinned screen strip). */
function inPinnedBar(el: Element | null): boolean {
  for (let e = el; e; e = e.parentElement) {
    const pos = getComputedStyle(e).position;
    if (pos === 'fixed' || pos === 'sticky') return true;
  }
  return false;
}

let onPinnedZone = false;

/** DndContext's autoScroll: the page holds still while the pointer is on a drop zone in a pinned bar. dnd-kit scrolls
 *  whenever the pointer is in the top fifth of the window, which is where the pinned tab bar sits: the page scrolled
 *  away under the pointer, the bar unpinned and jumped down, and the tab was never reached. */
export const autoScrollOptions = { canScroll: () => !onPinnedZone };

export const pointerZone: CollisionDetection = (args) => {
  const { pointerCoordinates: p, droppableContainers, droppableRects, active } = args;
  onPinnedZone = false;
  // window.__dndOld: the old detection, for the eval's before/after (tools/ui_evals/dnd_zones.py --old)
  if (!p || (window as unknown as { __dndOld?: boolean }).__dndOld) return closestCenter(args);
  const dragged = draggedNode() ?? droppableContainers.find((c) => c.id === active.id)?.node.current ?? null;
  const zones: Zone[] = droppableContainers.map((c) => ({
    id: c.id,
    node: c.node.current,
    sortable: !!c.data.current?.sortable,
    rect: droppableRects.get(c.id) ?? null,
    list: c.node.current?.parentElement ?? null,
  }));
  const byId = new Map(droppableContainers.map((c) => [c.id, c]));
  const hits = pickDrop(p, drawnAt(p.x, p.y, dragged), zones, active.id);
  onPinnedZone = hits.length > 0 && inPinnedBar(byId.get(hits[0])?.node.current ?? null);
  return hits.map(
    (id): Collision => ({ id, data: { droppableContainer: byId.get(id), value: 0 } }),
  );
};
