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
  /** Called when favorite status should be toggled */
  onToggleFavorite?: (img: ImageItem) => void;
  /** Called when an image is clicked */
  onImageClick?: (img: ImageItem) => void;
  /** Called when image should be deleted */
  onDelete?: (img: ImageItem) => void;
  /** Called to copy image to other bucket */
  onCopyTo?: (img: ImageItem, destId: string) => void;
  /** Called to publish image */
  onPublish?: (img: ImageItem, destId: string) => void;
  /** List of publishable destinations */
  publishDestinations?: Array<{id: string, name: string, headless: boolean}>;
  /** The bucket ID of the current bucket (for raw URL construction) */
  bucketId?: string;
  /** The type of section this grid is in ('favourites' or 'dated') */
  sectionVariant?: 'favourites' | 'dated';
}

export const SortableImageGrid: React.FC<SortableImageGridProps> = ({
  images,
  onOrderChange,
  sortable = true,
  className = '',
  onToggleFavorite,
  onImageClick,
  onDelete,
  onCopyTo,
  onPublish,
  publishDestinations,
  bucketId = '',
  sectionVariant,
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
          <SortableImage 
            key={img.id} 
            image={img} 
            index={idx} 
            onToggleFavorite={onToggleFavorite} 
            onImageClick={onImageClick} 
            onDelete={onDelete} 
            onCopyTo={onCopyTo} 
            onPublish={onPublish}
            publishDestinations={publishDestinations} 
            bucketId={bucketId}
            sectionVariant={sectionVariant}
          />
        ) : (
          <DraggableImage 
            key={img.id} 
            image={img} 
            index={idx} 
            onToggleFavorite={onToggleFavorite} 
            onImageClick={onImageClick} 
            onDelete={onDelete} 
            onCopyTo={onCopyTo} 
            onPublish={onPublish}
            publishDestinations={publishDestinations} 
            bucketId={bucketId}
            sectionVariant={sectionVariant}
          />
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
  onToggleFavorite?: (img: ImageItem) => void;
  onImageClick?: (img: ImageItem) => void;
  onDelete?: (img: ImageItem) => void;
  onCopyTo?: (img: ImageItem, destId: string) => void;
  onPublish?: (img: ImageItem, destId: string) => void;
  publishDestinations?: Array<{id: string, name: string, headless: boolean}>;
  bucketId?: string;
  sectionVariant?: 'favourites' | 'dated';
}

const SortableImage: React.FC<ImageProps> = ({ 
  image, 
  index, 
  onToggleFavorite, 
  onImageClick, 
  onDelete, 
  onCopyTo, 
  onPublish,
  publishDestinations,
  bucketId,
  sectionVariant
}) => {
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
      <ImageCard 
        image={image} 
        index={index} 
        onToggleFavorite={onToggleFavorite} 
        onClick={onImageClick} 
        onDelete={onDelete}
        onCopyTo={onCopyTo}
        onPublish={onPublish}
        publishDestinations={publishDestinations}
        bucketId={bucketId}
        sectionVariant={sectionVariant}
      />
    </div>
  );
};

// Draggable-only item used when grid is not sortable
const DraggableImage: React.FC<ImageProps> = ({ 
  image, 
  index, 
  onToggleFavorite, 
  onImageClick, 
  onDelete, 
  onCopyTo, 
  onPublish, 
  publishDestinations,
  bucketId,
  sectionVariant
}) => {
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
      <ImageCard 
        image={image} 
        index={index} 
        onToggleFavorite={onToggleFavorite} 
        onClick={onImageClick}
        onDelete={onDelete}
        onCopyTo={onCopyTo}
        onPublish={onPublish}
        publishDestinations={publishDestinations}
        bucketId={bucketId}
        sectionVariant={sectionVariant}
      />
    </div>
  );
};

export default SortableImageGrid; 