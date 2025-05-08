import React from 'react';
import {
  DragEndEvent,
  useDndContext,
  useDraggable,
  useDroppable,
} from '@dnd-kit/core';
import {
  SortableContext,
  rectSortingStrategy,
  useSortable,
  arrayMove,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import ImageCard from './ImageCard';
import { ImageItem } from '@/types/image-types';

interface SortableImageGridProps {
  images: ImageItem[];
  /** Called when ordering within the grid changes. Provides new array of image ids. */
  onOrderChange?: (newOrder: string[]) => void;
  /** Allow reordering inside this grid. Dragging out always allowed. Default true */
  sortable?: boolean;
  /** Additional classes for grid container */
  className?: string;
}

export const SortableImageGrid: React.FC<SortableImageGridProps> = ({
  images,
  onOrderChange,
  sortable = true,
  className = '',
}) => {
  const [items, setItems] = React.useState<string[]>(images.map((i) => i.id));

  React.useEffect(() => setItems(images.map((i) => i.id)), [images]);

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!sortable || !over || active.id === over.id) return;

    const oldIndex = items.indexOf(String(active.id));
    const newIndex = items.indexOf(String(over.id));

    const newItems = arrayMove(items, oldIndex, newIndex);
    setItems(newItems);
    onOrderChange?.(newItems);
  };

  const { active } = useDndContext();

  const gridContent = (
    <div
      className={`drag-grid grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-1 overflow-x-hidden ${className}`}
      style={{ touchAction: active ? 'none' : 'pan-y' }}
    >
      {images.map((img, idx) => (
        sortable ? (
          <SortableImage key={img.id} image={img} index={idx} />
        ) : (
          <DraggableImage key={img.id} image={img} index={idx} />
        )
      ))}
    </div>
  );

  return sortable ? (
    <SortableContext items={items} strategy={rectSortingStrategy}>
      {gridContent}
    </SortableContext>
  ) : (
    gridContent
  );
};

interface ImageProps {
  image: ImageItem;
  index: number;
}

const SortableImage: React.FC<ImageProps> = ({ image, index }) => {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: image.id });

  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.6 : 1,
    cursor: isDragging ? 'grabbing' : 'grab',
    boxShadow: isDragging ? '0 4px 14px rgba(0,0,0,0.3)' : undefined,
    zIndex: isDragging ? 50 : undefined,
  };

  return (
    <div
      ref={setNodeRef}
      style={style}
      {...attributes}
      {...listeners}
      onContextMenu={(e) => {
        if (window.matchMedia('(pointer: coarse)').matches) {
          e.preventDefault();
        }
      }}
    >
      <ImageCard image={image} index={index} />
    </div>
  );
};

// Draggable-only item used when grid is not sortable
const DraggableImage: React.FC<ImageProps> = ({ image, index }) => {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    isDragging,
  } = useDraggable({ id: image.id });

  const {
    setNodeRef: setDropNodeRef,
    isOver,
  } = useDroppable({ id: image.id });

  const mergedRef = (node: HTMLElement | null) => {
    setNodeRef(node);
    setDropNodeRef(node);
  };

  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition: isDragging ? undefined : 'transform 0.2s ease',
    opacity: isDragging ? 0.6 : 1,
    cursor: isDragging ? 'grabbing' : 'grab',
    boxShadow: isDragging ? '0 4px 14px rgba(0,0,0,0.3)' : undefined,
    zIndex: isDragging ? 50 : undefined,
  };

  return (
    <div
      ref={mergedRef}
      style={style}
      {...attributes}
      {...listeners}
      onContextMenu={(e) => {
        if (window.matchMedia('(pointer: coarse)').matches) {
          e.preventDefault();
        }
      }}
    >
      <ImageCard image={image} index={index} />
    </div>
  );
};

export default SortableImageGrid; 