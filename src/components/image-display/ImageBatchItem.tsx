
import React, { useState } from 'react';
import { ExternalLink, Trash2, Maximize, ChevronLeft, ChevronRight } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import ImageActions from '@/components/ImageActions';
import { ViewMode } from './ImageDisplay';

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
  onImageClick: (url: string) => void;
  onNavigateNext?: () => void;
  onNavigatePrev?: () => void;
  viewMode?: ViewMode;
  showActions?: boolean;
  isRolledUp?: boolean;
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
  onImageClick,
  onNavigateNext,
  onNavigatePrev,
  viewMode = 'normal',
  showActions = true,
  isRolledUp = false
}) => {
  const [isHovered, setIsHovered] = useState(false);
  const [showActionPanel, setShowActionPanel] = useState(false);

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
    // In normal view, clicking the image toggles action panel
    if (viewMode === 'normal') {
      setShowActionPanel(!showActionPanel);
      
      // Only trigger the onImageClick if we're in small view
      if (viewMode === 'small' && image.url) {
        onImageClick(image.url);
      }
    } else if (viewMode === 'small' && image.url) {
      // Small view behavior - open full screen
      onImageClick(image.url);
    }
  };
  
  const handleNavigatePrev = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (onNavigatePrev) {
      onNavigatePrev();
    }
  };
  
  const handleNavigateNext = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (onNavigateNext) {
      onNavigateNext();
    }
  };

  // Small view has smaller images
  const sizeClasses = viewMode === 'small' 
    ? 'aspect-square w-full h-full' 
    : 'aspect-square';

  return (
    <div 
      className={`relative rounded-md overflow-hidden group ${viewMode === 'small' ? 'mb-2' : ''}`}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => {
        setIsHovered(false);
        if (!showActionPanel) {
          setShowActionPanel(false);
        }
      }}
    >
      <div 
        className={`relative ${sizeClasses} cursor-pointer`}
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
        
        {/* Image number indicator */}
        {total > 1 && viewMode !== 'small' && (
          <div className="absolute bottom-2 right-2 bg-black/70 text-white px-2 py-0.5 rounded-full text-xs">
            {index + 1}/{total}
          </div>
        )}
        
        {/* Delete button - show on all views */}
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
        
        {/* Navigation arrows - only show if multiple images */}
        {total > 1 && (
          <>
            {/* Previous arrow */}
            {index > 0 && onNavigatePrev && (
              <button 
                className="absolute left-2 top-1/2 -translate-y-1/2 bg-black/40 hover:bg-black/60 rounded-full p-1.5 text-white transition-colors z-10"
                onClick={handleNavigatePrev}
              >
                <ChevronLeft className="h-3 w-3" />
              </button>
            )}
            
            {/* Next arrow */}
            {index < total - 1 && onNavigateNext && (
              <button 
                className="absolute right-2 top-1/2 -translate-y-1/2 bg-black/40 hover:bg-black/60 rounded-full p-1.5 text-white transition-colors z-10"
                onClick={handleNavigateNext}
              >
                <ChevronRight className="h-3 w-3" />
              </button>
            )}
          </>
        )}
        
        {/* Action panel - show when hovered or clicked in normal view */}
        {image.url && showActions && viewMode === 'normal' && (isHovered || showActionPanel) && (
          <div className="absolute bottom-2 left-2 right-2 flex justify-center space-x-1 transition-opacity bg-black/70 rounded-md p-1">
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
        
        {/* Fullscreen button */}
        {onFullScreen && viewMode !== 'small' && (
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
