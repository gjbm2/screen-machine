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
  /** Called when image should be used as a prompt reference */
  onUseAsPrompt?: (img: ImageItem) => void;
  /** List of publishable destinations */
  publishDestinations?: Array<{id: string, name: string, headless: boolean}>;
  /** The bucket ID of the current bucket (for raw URL construction) */
  bucketId?: string;
  /** The type of section this grid is in ('favourites' or 'dated') */
  sectionVariant?: 'favourites' | 'dated';
  disableDefaultGridCols?: boolean;
  /** Called when image should be used as a prompt reference */
  onFullscreenClick?: (img: ImageItem) => void;
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
  onUseAsPrompt,
  publishDestinations,
  bucketId = '',
  sectionVariant,
  disableDefaultGridCols,
  onFullscreenClick,
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

  const itemExtraClass = className.includes('recent-thumbnail-grid') ? 'recent-thumbnail-grid' : '';

  const gridContent = (
    <div
      className={`drag-grid${disableDefaultGridCols ? ' recent-tab-grid' : ''} grid${!disableDefaultGridCols ? ' grid-cols-2 sm:grid-cols-3 md:grid-cols-4' : ''} gap-1 overflow-x-hidden ${className} ${itemExtraClass}`}
      style={{ touchAction: active ? 'none' : 'pan-y' }}
    >
      {images.map((img, idx) => {
        // Create a stable, unique key for React
        const itemKey = (img.uniqueKey || img.id) as React.Key;
        
        return img.customComponent ? (
          <div key={itemKey}>
            {img.customComponent}
          </div>
        ) : sortable ? (
          <SortableImage 
            key={itemKey} 
            image={img} 
            index={idx} 
            onToggleFavorite={onToggleFavorite} 
            onImageClick={onImageClick} 
            onDelete={onDelete} 
            onCopyTo={onCopyTo} 
            onPublish={onPublish}
            onUseAsPrompt={onUseAsPrompt}
            publishDestinations={publishDestinations} 
            bucketId={bucketId}
            sectionVariant={sectionVariant}
            className={itemExtraClass}
            onFullscreenClick={onFullscreenClick}
          />
        ) : (
          <DraggableImage 
            key={itemKey} 
            image={img} 
            index={idx} 
            onToggleFavorite={onToggleFavorite} 
            onImageClick={onImageClick} 
            onDelete={onDelete} 
            onCopyTo={onCopyTo} 
            onPublish={onPublish}
            onUseAsPrompt={onUseAsPrompt}
            publishDestinations={publishDestinations} 
            bucketId={bucketId}
            sectionVariant={sectionVariant}
            className={itemExtraClass}
            onFullscreenClick={onFullscreenClick}
          />
        );
      })}
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
  onUseAsPrompt?: (img: ImageItem) => void;
  publishDestinations?: Array<{id: string, name: string, headless: boolean}>;
  bucketId?: string;
  sectionVariant?: 'favourites' | 'dated';
  className?: string;
  onFullscreenClick?: (img: ImageItem) => void;
}

const SortableImage: React.FC<ImageProps> = ({ 
  image, 
  index, 
  onToggleFavorite, 
  onImageClick, 
  onDelete, 
  onCopyTo, 
  onPublish,
  onUseAsPrompt,
  publishDestinations,
  bucketId,
  sectionVariant,
  className = '',
  onFullscreenClick,
}) => {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ 
    id: image.id,
    data: {
      raw_url: image.raw_url || image.urlFull,
      thumbnail_url: image.urlThumb,
      image,
      bucketId
    }
  });

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
      data-selected={image.isSelected}
      className="relative"
      onClick={(e) => {
        if (isDragging) return;
        e.stopPropagation();
        if (onFullscreenClick) {
          onFullscreenClick(image);
        } else if (onImageClick) {
          onImageClick(image);
        }
      }}
      onContextMenu={(e) => {
        if (window.matchMedia('(pointer: coarse)').matches) {
          e.preventDefault();
        }
      }}
    >
      {image.isSelected && (
        <div className="absolute inset-0 border-4 border-sky-500 rounded-md pointer-events-none z-50"></div>
      )}
      <ImageCard 
        image={image} 
        index={index} 
        onToggleFavorite={onToggleFavorite} 
        onClick={onImageClick} 
        onDelete={onDelete}
        onCopyTo={onCopyTo}
        onPublish={onPublish}
        onUseAsPrompt={onUseAsPrompt}
        publishDestinations={publishDestinations}
        bucketId={bucketId}
        sectionVariant={sectionVariant}
        className={className}
        onFullscreenClick={onFullscreenClick}
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
  onUseAsPrompt,
  publishDestinations,
  bucketId,
  sectionVariant,
  className = '',
  onFullscreenClick,
}) => {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    isDragging,
  } = useDraggable({ 
    id: image.id,
    data: {
      raw_url: image.raw_url || image.urlFull,
      thumbnail_url: image.urlThumb,
      image,
      bucketId
    }
  });

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
      data-selected={image.isSelected}
      className="relative"
      onClick={(e) => {
        e.stopPropagation();
        if (onFullscreenClick) {
          onFullscreenClick(image);
        } else if (onImageClick) {
          onImageClick(image);
        }
      }}
      onContextMenu={(e) => {
        if (window.matchMedia('(pointer: coarse)').matches) {
          e.preventDefault();
        }
      }}
    >
      {image.isSelected && (
        <div className="absolute inset-0 border-4 border-sky-500 rounded-md pointer-events-none z-50"></div>
      )}
      <ImageCard 
        image={image} 
        index={index} 
        onToggleFavorite={onToggleFavorite} 
        onClick={onImageClick}
        onDelete={onDelete}
        onCopyTo={onCopyTo}
        onPublish={onPublish}
        onUseAsPrompt={onUseAsPrompt}
        publishDestinations={publishDestinations}
        bucketId={bucketId}
        sectionVariant={sectionVariant}
        className={className}
        onFullscreenClick={onFullscreenClick}
      />
    </div>
  );
};

export default SortableImageGrid; 