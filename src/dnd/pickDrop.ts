// Which drop zone a drag is over: the one under the pointer, as the page draws it (owner, 19 Nov: "I have to point some
// way vertically above the main area for it to show up as a drop zone"). Pure, so it is tested without a browser:
//   node --experimental-strip-types --test src/dnd/pickDrop.test.ts
//
// dnd-kit's closestCenter picked the zone whose CENTRE was nearest the dragged card's centre: a big zone (the prompt box)
// only won near its middle, and a small one beside it (a tab) took its edges. Its rects are also measured when the drag
// starts and only corrected for scrolling, which is wrong for the tab bar and the screen strip once they pin (fixed,
// sticky). So: hit-test the page at the pointer and take the innermost zone holding what is drawn there.
//
// Sortable items (the favourites' order) are the exception: while dragging, their neighbours slide aside, and a hit test
// on the slid cards would flip the target back and forth. Those use dnd-kit's own measured rects (their places before
// the drag), as dnd-kit's sorting expects.

export type ZoneId = string | number;

/** Just enough of a DOM node: what contains what. */
export interface NodeLike {
  contains(other: NodeLike | null): boolean;
}

export interface RectLike {
  top: number;
  left: number;
  width: number;
  height: number;
}

export interface Zone {
  id: ZoneId;
  node: NodeLike | null;
  /** a sortable item: matched on `rect` (its measured place), not on what is drawn */
  sortable: boolean;
  rect: RectLike | null;
  /** a sortable item's list (its parent): its slot counts only where the list is what is drawn */
  list?: NodeLike | null;
}

const within = (p: { x: number; y: number }, r: RectLike) =>
  p.x >= r.left && p.x <= r.left + r.width && p.y >= r.top && p.y <= r.top + r.height;

/** The zones the pointer is over, best first; empty when it is over none.
 *  `top` is the element drawn at the pointer, not counting the card being dragged. */
export function pickDrop(
  pointer: { x: number; y: number },
  top: NodeLike | null,
  zones: Zone[],
  activeId: ZoneId | null,
): ZoneId[] {
  const live = zones.filter((z) => z.id !== activeId && z.node);
  // every zone holding the drawn element is one of its ancestors, so they nest: innermost first
  const drawn = top ? live.filter((z) => z.node!.contains(top)) : [];
  drawn.sort((a, b) => (a.node!.contains(b.node) ? 1 : b.node!.contains(a.node) ? -1 : 0));
  // a sortable item's measured place under the pointer counts only where its list is what is drawn there (a slid
  // neighbour, the gap, the empty slot), not under a pinned tab bar or the app's own bars
  const slots = live
    .filter((z) => z.sortable && z.rect && within(pointer, z.rect) && !!top && !!z.list?.contains(top))
    .sort((a, b) => a.rect!.width * a.rect!.height - b.rect!.width * b.rect!.height);
  if (slots.length) return [...slots, ...drawn.filter((z) => !slots.includes(z))].map((z) => z.id);
  return drawn.map((z) => z.id);
}
