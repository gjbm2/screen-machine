
import React, { useState } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogClose } from '@/components/ui/dialog';
import { Maximize, Trash2, X } from 'lucide-react';
import { TooltipProvider } from '@/components/ui/tooltip';
import ImageActions from '@/components/ImageActions';

type ViewMode = 'normal' | 'small' | 'table';

interface ImageBatchItemProps {
  image: {
    url: string;
    prompt?: string;
    workflow: string;
    status?: 'generating' | 'completed' | 'error';
    params?: Record<string, any>;
    referenceImageUrl?: string;
  };
  batchId: string;
  index: number;
  total: number;
  onCreateAgain: (batchId: string) => void;
  onUseAsInput?: ((imageUrl: string) => void) | null;
  onDeleteImage: (batchId: string, index: number) => void;
  viewMode?: ViewMode;
}

const ImageBatchItem: React.FC<ImageBatchItemProps> = ({
  image,
  batchId,
  index,
  total,
  onCreateAgain,
  onUseAsInput,
  onDeleteImage,
  viewMode = 'normal'
}) => {
  const isGenerating = image.status === 'generating';
  const [actionsVisible, setActionsVisible] = useState(false);
  const [isClicked, setIsClicked] = useState(false);
  const [fullScreenOpen, setFullScreenOpen] = useState(false);
  const isSmallView = viewMode === 'small';
  
  if (isGenerating) {
    return (
      <div className="aspect-square flex items-center justify-center bg-secondary/20">
        <div className="h-12 w-12 animate-spin rounded-full border-4 border-primary border-t-transparent"></div>
        {image.prompt && !isSmallView && (
          <p className="text-sm text-center text-muted-foreground absolute mt-20">
            Generating: {image.prompt}
          </p>
        )}
      </div>
    );
  }
  
  const handleClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    setIsClicked(!isClicked);
    setActionsVisible(!actionsVisible);
  };

  const handleDeleteImage = (e: React.MouseEvent) => {
    e.stopPropagation();
    onDeleteImage(batchId, index);
  };

  const handleCreateAgain = () => {
    onCreateAgain(batchId);
  };

  const handleOpenFullScreen = (e: React.MouseEvent) => {
    e.stopPropagation();
    setFullScreenOpen(true);
  };
  
  return (
    <div 
      className="aspect-square relative group cursor-pointer" 
      onClick={handleClick}
      onMouseEnter={() => !isClicked && setActionsVisible(true)}
      onMouseLeave={() => !isClicked && setActionsVisible(false)}
    >
      <img
        src={image.url}
        alt={image.prompt || 'Generated image'}
        className="w-full h-full object-cover"
      />
      
      {/* Delete button - always visible in normal mode, hidden in small mode */}
      {!isSmallView && (
        <button 
          className="absolute top-2 left-2 bg-black/90 hover:bg-black text-white rounded-full p-2 transition-colors z-20"
          onClick={handleDeleteImage}
        >
          <Trash2 className="h-4 w-4" />
        </button>
      )}
      
      {/* Full screen view button - always available */}
      <button 
        className="absolute top-2 right-2 bg-black/70 hover:bg-black/90 text-white rounded-full p-1.5 transition-colors z-20"
        onClick={handleOpenFullScreen}
      >
        <Maximize className="h-4 w-4" />
      </button>
      
      {/* Full-screen dialog */}
      <Dialog open={fullScreenOpen} onOpenChange={setFullScreenOpen}>
        <DialogContent className="p-0 overflow-hidden" fullscreen>
          <DialogHeader className="absolute top-0 left-0 right-0 bg-black/80 z-10 p-4 flex justify-between items-center">
            <DialogTitle className="text-white">Image View</DialogTitle>
            <DialogClose className="rounded-full p-1 hover:bg-black/40 text-white">
              <X className="h-6 w-6" />
            </DialogClose>
          </DialogHeader>
          
          <div className="w-full h-full flex flex-col">
            <div className="flex-1 flex items-center justify-center overflow-auto">
              <img
                src={image.url}
                alt={image.prompt || 'Generated image full view'}
                className="max-w-full max-h-full object-contain"
              />
            </div>
            
            {/* Bottom controls for fullscreen view */}
            <div className="p-4 bg-black/80 flex flex-wrap justify-center gap-2">
              <TooltipProvider>
                <ImageActions 
                  imageUrl={image.url}
                  onCreateAgain={handleCreateAgain}
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
      
      {/* Batch counter - Bottom right position */}
      {total > 1 && (
        <div className="absolute bottom-2 right-2 bg-black/90 text-white px-3 py-1 rounded-full text-xs font-medium z-10">
          {index + 1}/{total}
        </div>
      )}
      
      {/* Image controls overlay - ANIMATED slide in/out */}
      <div 
        className={`absolute bottom-0 left-0 right-0 bg-black/90 flex justify-center p-3 z-10 transition-transform duration-300 ${
          actionsVisible ? 'translate-y-0' : 'translate-y-full'
        }`}
        style={{ 
          backgroundColor: isClicked ? 'rgba(0, 0, 0, 0.90)' : 'rgba(0, 0, 0, 0.75)'
        }}
      >
        <div className="flex gap-2 justify-center">
          <TooltipProvider>
            <ImageActions 
              imageUrl={image.url}
              onCreateAgain={handleCreateAgain}
              onUseAsInput={() => onUseAsInput && onUseAsInput(image.url)}
              generationInfo={{
                prompt: image.prompt || '',
                workflow: image.workflow,
                params: image.params
              }}
              isMouseOver={!isClicked}
            />
          </TooltipProvider>
        </div>
      </div>
    </div>
  );
};

export default ImageBatchItem;
