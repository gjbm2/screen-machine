
import React from 'react';
import { useSortable } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { GripVertical, ChevronUp, ChevronDown, Maximize } from 'lucide-react';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';

type ViewMode = 'normal' | 'small' | 'table';

interface SortableContainerProps { 
  batchId: string; 
  children: React.ReactNode;
  batch: {
    images: any[];
  };
  isExpanded: boolean;
  toggleExpand: (id: string) => void;
  viewMode?: ViewMode;
}

const SortableImageContainer: React.FC<SortableContainerProps> = ({ 
  batchId, 
  children,
  batch,
  isExpanded,
  toggleExpand,
  viewMode = 'normal'
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

  // In small view, we hide the header or simplify it
  if (viewMode === 'small' && !isExpanded) {
    return (
      <div ref={setNodeRef} style={style}>
        {children}
        <div className="absolute top-2 right-2 z-10">
          <Tooltip>
            <TooltipTrigger asChild>
              <button 
                className="bg-black/70 hover:bg-black/90 text-white rounded-full p-1.5 transition-colors"
                onClick={(e) => {
                  e.stopPropagation();
                  toggleExpand(batchId);
                }}
              >
                <Maximize className="h-4 w-4" />
              </button>
            </TooltipTrigger>
            <TooltipContent>Expand</TooltipContent>
          </Tooltip>
        </div>
      </div>
    );
  }

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
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="font-medium cursor-help">
                Prompt: <span className="font-normal">{batch.images[0]?.prompt || 'Generated Image'}</span>
              </span>
            </TooltipTrigger>
            <TooltipContent className="max-w-md whitespace-normal">
              {batch.images[0]?.prompt || 'No prompt provided'}
            </TooltipContent>
          </Tooltip>
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
