
import React from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogClose } from '@/components/ui/dialog';
import { Maximize, Trash2, X } from 'lucide-react';
import { TooltipProvider } from '@/components/ui/tooltip';
import ImageActions from '@/components/ImageActions';

interface ImageBatchItemProps {
  image: {
    url: string;
    prompt?: string;
    workflow: string;
    status?: 'generating' | 'completed' | 'error';
    params?: Record<string, any>;
  };
  batchId: string;
  index: number;
  total: number;
  onCreateAgain: (batchId: string) => void;
  onUseAsInput?: ((imageUrl: string) => void) | null;
  onDeleteImage: (batchId: string, index: number) => void;
}

const ImageBatchItem: React.FC<ImageBatchItemProps> = ({
  image,
  batchId,
  index,
  total,
  onCreateAgain,
  onUseAsInput,
  onDeleteImage,
}) => {
  const isGenerating = image.status === 'generating';
  
  if (isGenerating) {
    return (
      <div className="aspect-square flex items-center justify-center bg-secondary/20">
        <div className="h-12 w-12 animate-spin rounded-full border-4 border-primary border-t-transparent"></div>
        {image.prompt && (
          <p className="text-sm text-center text-muted-foreground absolute mt-20">
            Generating: {image.prompt}
          </p>
        )}
      </div>
    );
  }
  
  return (
    <div className="aspect-square relative group">
      <img
        src={image.url}
        alt={image.prompt || 'Generated image'}
        className="w-full h-full object-cover"
      />
      
      {/* Delete button - always visible */}
      <button 
        className="absolute top-2 left-2 bg-black/90 hover:bg-black text-white rounded-full p-2 transition-colors z-20"
        onClick={(e) => {
          e.stopPropagation();
          onDeleteImage(batchId, index);
        }}
      >
        <Trash2 className="h-4 w-4" />
      </button>
      
      {/* Full screen view button - always visible */}
      <Dialog>
        <DialogContent className="p-0 overflow-hidden" fullscreen>
          <DialogHeader className="absolute top-0 left-0 right-0 bg-black/80 z-10 p-4 flex justify-between items-start">
            <div>
              <DialogTitle className="text-white">Image View</DialogTitle>
              <p className="text-white/70 truncate pr-10">
                {image.prompt}
              </p>
            </div>
            <DialogClose className="rounded-full p-1 hover:bg-black/40 text-white">
              <X className="h-6 w-6" />
            </DialogClose>
          </DialogHeader>
          
          <div className="w-full h-full flex flex-col">
            <div className="flex-1 flex items-center justify-center overflow-auto">
              <div className="w-full h-full flex items-center justify-center overflow-auto">
                <img
                  src={image.url}
                  alt={image.prompt || 'Generated image full view'}
                  className="max-w-full max-h-full object-contain"
                />
              </div>
            </div>
            
            {/* Bottom controls for fullscreen view */}
            <div className="p-4 bg-black/80 flex flex-wrap justify-center gap-2">
              <TooltipProvider>
                <ImageActions 
                  imageUrl={image.url}
                  onCreateAgain={() => onCreateAgain(batchId)}
                  onUseAsInput={() => onUseAsInput && onUseAsInput(image.url)}
                  generationInfo={{
                    prompt: image.prompt || '',
                    workflow: image.workflow,
                    params: image.params
                  }}
                  isFullScreen
                />
              </TooltipProvider>
            </div>
          </div>
        </DialogContent>
      </Dialog>
      
      {/* Batch counter - MOVED to bottom right */}
      {total > 1 && (
        <div className="absolute bottom-2 right-2 bg-black/90 text-white px-3 py-1 rounded-full text-xs font-medium z-10">
          {index + 1}/{total}
        </div>
      )}
      
      {/* Image controls overlay - ALWAYS VISIBLE (reduced opacity when not hovered) */}
      <div className="absolute bottom-0 left-0 right-0 bg-black/80 flex justify-center p-3 opacity-70 group-hover:opacity-100 transition-opacity z-10">
        <div className="flex flex-wrap gap-2 justify-center">
          <TooltipProvider>
            <ImageActions 
              imageUrl={image.url}
              onCreateAgain={() => onCreateAgain(batchId)}
              onUseAsInput={() => onUseAsInput && onUseAsInput(image.url)}
              generationInfo={{
                prompt: image.prompt || '',
                workflow: image.workflow,
                params: image.params
              }}
            />
          </TooltipProvider>
        </div>
      </div>
    </div>
  );
};

export default ImageBatchItem;
