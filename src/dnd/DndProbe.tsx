// For the drag-and-drop eval (tools/ui_evals/dnd_zones.py): the drag's target and the page's drop zones, read from
// window.__dnd. Renders nothing.
import { useEffect } from 'react';
import { useDndContext } from '@dnd-kit/core';

export function DndProbe() {
  const { active, over, droppableContainers } = useDndContext();
  useEffect(() => {
    (window as unknown as { __dnd: unknown }).__dnd = {
      active: active?.id ?? null,
      over: over?.id ?? null,
      zones: () =>
        droppableContainers
          .getEnabled()
          .map((c) => ({ id: c.id, node: c.node.current, sortable: !!c.data.current?.sortable })),
    };
  });
  return null;
}
