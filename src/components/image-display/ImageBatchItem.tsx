
import React, { useState } from 'react';
import { ExternalLink, Trash2, Maximize } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import ImageActions from '@/components/ImageActions';

interface ImageBatchItemProps {
  image: {
    url: string;
    prompt?: string;
    workflow?: string;
    timestamp?: number;
    params?: Record<string, any>;
    batchId?: string;
    batchIndex?: number;
    status?: 'generating' | 'completed' | 'error';
    refiner?: string;
    referenceImageUrl?: string;
  };
  batchId: string;
  index: number;
  total: number;
  onCreateAgain?: (batchId: string) => void;
  onUseAsInput?: ((imageUrl: string) => void) | null;
  onDeleteImage?: (batchId: string, index: number) => void;
  onFullScreen?: (batchId: string, index: number) => void;
  viewMode?: 'normal' | 'small' | 'table';
}

const ImageBatchItem: React.FC<ImageBatchItemProps> = ({
  image,
  batchId,
  index,
  total,
  onCreateAgain,
  onUseAsInput,
  onDeleteImage,
  onFullScreen,
  viewMode = 'normal'
}) => {
  const [isHovered, setIsHovered] = useState(false);

  const handleCreateAgain = () => {
    if (onCreateAgain) {
      onCreateAgain(batchId);
    }
  };

  const handleUseAsInput = () => {
    if (onUseAsInput && image.url) {
      onUseAsInput(image.url);
    }
  };

  const handleFullScreen = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (onFullScreen) {
      onFullScreen(batchId, index);
    }
  };

  return (
    <div 
      className="relative rounded-md overflow-hidden group"
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      <div 
        className="relative aspect-square cursor-pointer"
        onClick={(e) => {
          // Handle click based on view mode
          if (viewMode === 'small' || viewMode === 'table') {
            handleFullScreen(e);
          }
          // In normal view, just show image, don't trigger full screen
        }}
      >
        {image.url ? (
          <img
            src={image.url}
            alt={image.prompt || `Generated image ${index + 1}`}
            className="w-full h-full object-cover"
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center bg-muted">
            <div className="animate-pulse flex flex-col items-center">
              <div className="h-8 w-8 bg-muted-foreground/20 rounded-full mb-2"></div>
              <div className="h-2 w-24 bg-muted-foreground/20 rounded"></div>
            </div>
          </div>
        )}
        
        {viewMode === 'small' && (
          <div className="absolute inset-0 bg-black/0 group-hover:bg-black/40 transition-all flex items-center justify-center opacity-0 group-hover:opacity-100">
            <Button 
              variant="outline" 
              size="sm" 
              className="bg-black/50 border-white/20 text-white"
              onClick={handleFullScreen}
            >
              <Maximize className="h-4 w-4 mr-1" /> View
            </Button>
          </div>
        )}
        
        {/* Show image number indicator only if we have more than 1 image and not in small view mode */}
        {total > 1 && viewMode !== 'small' && (
          <div className="absolute bottom-2 right-2 bg-black/70 text-white px-2 py-0.5 rounded-full text-xs">
            {index + 1}/{total}
          </div>
        )}
        
        {/* Always visible delete button in top left */}
        {onDeleteImage && (
          <Tooltip>
            <TooltipTrigger asChild>
              <button 
                className="absolute top-2 left-2 bg-black/70 hover:bg-black/90 rounded-full p-1.5 text-white transition-colors z-10"
                onClick={(e) => {
                  e.stopPropagation();
                  onDeleteImage(batchId, index);
                }}
              >
                <Trash2 className="h-3 w-3" />
              </button>
            </TooltipTrigger>
            <TooltipContent>Delete image</TooltipContent>
          </Tooltip>
        )}
        
        {/* Image Actions */}
        {image.url && viewMode !== 'small' && (
          <div className="absolute bottom-2 left-2 right-2 flex justify-center space-x-1 opacity-0 group-hover:opacity-100 transition-opacity bg-black/40 rounded-md p-1">
            <ImageActions
              imageUrl={image.url}
              onCreateAgain={onCreateAgain ? handleCreateAgain : undefined}
              onUseAsInput={onUseAsInput ? handleUseAsInput : undefined}
              generationInfo={{
                prompt: image.prompt || '',
                workflow: image.workflow || '',
                params: image.params
              }}
              isMouseOver={true}
            />
          </div>
        )}
        
        {/* Fullscreen button - separate from other actions for better visibility */}
        {onFullScreen && (
          <Tooltip>
            <TooltipTrigger asChild>
              <button 
                className="absolute top-2 right-2 bg-black/70 hover:bg-black/90 rounded-full p-1.5 text-white transition-colors z-10"
                onClick={handleFullScreen}
              >
                <Maximize className="h-3 w-3" />
              </button>
            </TooltipTrigger>
            <TooltipContent>Full screen</TooltipContent>
          </Tooltip>
        )}
      </div>
    </div>
  );
};

export default ImageBatchItem;
