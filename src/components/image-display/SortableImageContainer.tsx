
import React from 'react';
import { useSortable } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { GripVertical, ChevronUp, ChevronDown, Maximize, Image } from 'lucide-react';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { ViewMode } from './ImageDisplay';

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

  const [showReferenceImage, setShowReferenceImage] = React.useState(false);
  const referenceImageUrl = batch.images[0]?.referenceImageUrl;
  const containerId = batch.images[0]?.containerId || '';
  const promptText = batch.images[0]?.prompt || '';
  const workflowName = batch.images[0]?.workflow || 'Generated Image';
  
  // Use the title if available, otherwise fall back to the previous format
  const titleText = batch.images[0]?.title || 
    (promptText ? 
      `${containerId}. ${promptText} (${workflowName})` : 
      `${containerId}. ${workflowName}`);

  // Create width class based on viewMode and isExpanded state
  const widthClass = viewMode === 'normal' 
    ? isExpanded 
      ? 'col-span-full w-full' // Full width when expanded in normal mode
      : 'w-full' // Full width for rolled up view to fill the grid cell
    : 'w-full'; // Default width for other view modes

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.6 : 1,
    zIndex: isDragging ? 10 : 1,
    position: 'relative' as 'relative',
    marginBottom: '0.5rem', // Reduced margin from 1rem to 0.5rem
    width: '100%',
  };

  const handleReferenceImageClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (referenceImageUrl) {
      setShowReferenceImage(true);
    }
  };

  return (
    <div ref={setNodeRef} style={style} className={`block ${widthClass}`}>
      <div className="flex items-center justify-between bg-card rounded-t-lg py-1 px-2 border-b">
        <div 
          {...attributes} 
          {...listeners}
          className="cursor-grab active:cursor-grabbing p-1"
        >
          <GripVertical className="h-4 w-4 opacity-70" />
        </div>
        <div className="flex-1 truncate mx-1 text-xs text-muted-foreground">
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="font-medium cursor-help flex items-center">
                {referenceImageUrl && (
                  <button onClick={handleReferenceImageClick} className="mr-1">
                    <Image className="h-3 w-3 text-primary" />
                  </button>
                )}
                {titleText}
              </span>
            </TooltipTrigger>
            <TooltipContent className="max-w-md whitespace-normal">
              {titleText}
            </TooltipContent>
          </Tooltip>
        </div>
        <button 
          onClick={() => toggleExpand(batchId)}
          className="p-1 rounded-full hover:bg-accent/50 transition-colors"
        >
          {isExpanded ? (
            <ChevronUp className="h-4 w-4" />
          ) : (
            <ChevronDown className="h-4 w-4" />
          )}
        </button>
      </div>
      
      {/* Always show content */}
      <div className="block">
        {children}
      </div>

      {referenceImageUrl && (
        <Dialog open={showReferenceImage} onOpenChange={setShowReferenceImage}>
          <DialogContent 
            className="max-w-lg"
            description="Reference image used for generation"
          >
            <DialogHeader>
              <DialogTitle>Reference Image</DialogTitle>
            </DialogHeader>
            <div className="flex flex-col items-center">
              <p className="text-sm mb-2 text-muted-foreground">Reference image used for generation</p>
              <div className="border rounded-md overflow-hidden">
                <img 
                  src={referenceImageUrl} 
                  alt="Reference image"
                  className="w-full h-auto object-contain"
                />
              </div>
            </div>
          </DialogContent>
        </Dialog>
      )}
    </div>
  );
};

export default SortableImageContainer;
