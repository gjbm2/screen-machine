
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
  
  const handleDeleteImage = (e: React.MouseEvent) => {
    e.stopPropagation();
    e.preventDefault();
    if (onDeleteImage) {
      onDeleteImage(batchId, index);
    }
  };

  const handleImageClick = (e: React.MouseEvent) => {
    // In small view or table view, clicking the image should open full screen
    // In normal view, don't do anything on image click (other interactions handle this)
    if ((viewMode === 'small' || viewMode === 'table') && onFullScreen) {
      handleFullScreen(e);
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
        onClick={handleImageClick}
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
              <div className="h-12 w-12 animate-spin rounded-full border-4 border-primary border-t-transparent"></div>
              <div className="h-2 w-24 bg-muted-foreground/20 rounded mt-2"></div>
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
        
        {/* Delete button - improved positioning and visibility */}
        {onDeleteImage && (
          <Tooltip>
            <TooltipTrigger asChild>
              <button 
                className="absolute top-2 left-2 bg-black/70 hover:bg-black/90 rounded-full p-1.5 text-white transition-colors z-10"
                onClick={handleDeleteImage}
              >
                <Trash2 className="h-3 w-3" />
              </button>
            </TooltipTrigger>
            <TooltipContent>Delete this image</TooltipContent>
          </Tooltip>
        )}
        
        {/* Image Actions - improved visibility */}
        {image.url && viewMode !== 'small' && (
          <div className="absolute bottom-2 left-2 right-2 flex justify-center space-x-1 opacity-70 group-hover:opacity-100 transition-opacity bg-black/70 rounded-md p-1">
            <ImageActions
              imageUrl={image.url}
              onCreateAgain={onCreateAgain ? handleCreateAgain : undefined}
              onUseAsInput={onUseAsInput ? handleUseAsInput : undefined}
              generationInfo={{
                prompt: image.prompt || '',
                workflow: image.workflow || '',
                params: image.params
              }}
              isMouseOver={isHovered}
            />
          </div>
        )}
        
        {/* Fullscreen button - separate from other actions for better visibility */}
        {onFullScreen && viewMode === 'normal' && (
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
