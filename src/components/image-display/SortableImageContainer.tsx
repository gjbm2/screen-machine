
import React from 'react';
import { useSortable } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { GripVertical, ChevronUp, ChevronDown } from 'lucide-react';

interface SortableContainerProps { 
  batchId: string; 
  children: React.ReactNode;
  batch: {
    images: any[];
  };
  isExpanded: boolean;
  toggleExpand: (id: string) => void;
}

const SortableImageContainer: React.FC<SortableContainerProps> = ({ 
  batchId, 
  children,
  batch,
  isExpanded,
  toggleExpand,
}) => {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: batchId });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.6 : 1,
    zIndex: isDragging ? 10 : 1,
    position: 'relative' as 'relative',
    marginBottom: '1rem',
  };

  return (
    <div ref={setNodeRef} style={style}>
      <div className="flex items-center justify-between bg-card rounded-t-lg py-2 px-4 border-b">
        <div 
          {...attributes} 
          {...listeners}
          className="cursor-grab active:cursor-grabbing p-1"
        >
          <GripVertical className="h-5 w-5 opacity-70" />
        </div>
        <div className="flex-1 truncate mx-2 text-sm text-muted-foreground">
          <span className="font-medium">Prompt:</span> {batch.images[0]?.prompt || 'Generated Image'}
        </div>
        <button 
          onClick={() => toggleExpand(batchId)}
          className="p-1 rounded-full hover:bg-accent/50 transition-colors"
        >
          {isExpanded ? (
            <ChevronUp className="h-5 w-5" />
          ) : (
            <ChevronDown className="h-5 w-5" />
          )}
        </button>
      </div>
      {children}
    </div>
  );
};

export default SortableImageContainer;
