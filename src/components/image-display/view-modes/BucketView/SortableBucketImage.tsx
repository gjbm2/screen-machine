import React from 'react';
import { useSortable } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { BucketImage } from './BucketImage';
import { BucketItem } from '@/utils/api';

interface SortableBucketImageProps {
  id: string;
  bucket: string;
  item: BucketItem;
  buckets: string[];
  onToggleFavorite: (bucket: string, filename: string, currentState: boolean) => Promise<void>;
  onDelete: (bucket: string, filename: string) => Promise<void>;
  onCopyTo: (sourceBucket: string, targetBucket: string, filename: string) => Promise<void>;
  onMoveUp: (bucket: string, filename: string) => Promise<void>;
  onMoveDown: (bucket: string, filename: string) => Promise<void>;
  onOpen: (item: BucketItem) => void;
  onPublish: (bucket: string, filename: string) => Promise<void>;
  selectedDestination?: { id: string };
  publishDestinations: { id: string; name: string }[];
}

export function SortableBucketImage({
  id,
  bucket,
  item,
  buckets,
  onToggleFavorite,
  onDelete,
  onCopyTo,
  onMoveUp,
  onMoveDown,
  onOpen,
  onPublish,
  selectedDestination,
  publishDestinations
}: SortableBucketImageProps) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    zIndex: isDragging ? 10 : 1,
    opacity: isDragging ? 0.5 : 1,
    position: 'relative' as const,
  };

  return (
    <div ref={setNodeRef} style={style} {...attributes} {...listeners}>
      <BucketImage
        bucket={bucket}
        item={item}
        buckets={buckets}
        onToggleFavorite={onToggleFavorite}
        onDelete={onDelete}
        onCopyTo={onCopyTo}
        onMoveUp={onMoveUp}
        onMoveDown={onMoveDown}
        onOpen={onOpen}
        onPublish={onPublish}
        selectedDestination={selectedDestination}
        publishDestinations={publishDestinations}
      />
      {/* Drag handle indicator */}
      <div className="absolute top-2 right-2 bg-black/50 text-white text-xs px-1.5 py-0.5 rounded pointer-events-none">
        Drag to reorder
      </div>
    </div>
  );
}
