
import React, { useState } from 'react';
import { ViewMode } from './ImageDisplay';
import { useIsMobile } from '@/hooks/use-mobile';
import ImageActionButtons from './ImageActionButtons';
import ImageNavigationButtons from './ImageNavigationButtons';
import BatchCountDisplay from './BatchCountDisplay';
import ImageActionsPanel from './ImageActionsPanel';
import ImageLoadingState from './ImageLoadingState';
import { ImageGenerationStatus } from '@/types/workflows';

interface ImageBatchItemProps {
  image: {
    url: string;
    prompt?: string;
    workflow?: string;
    timestamp?: number;
    params?: Record<string, any>;
    batchId?: string;
    batchIndex?: number;
    status?: ImageGenerationStatus;
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
  const isMobile = useIsMobile();

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
    if ((e.target as HTMLElement).closest('.image-action-button') ||
        (e.target as HTMLElement).closest('button')) {
      e.stopPropagation();
      return;
    }
    
    if (viewMode === 'normal' && isRolledUp) {
      if (image.url && onFullScreen) {
        onFullScreen(batchId, index);
      }
    } else if (viewMode === 'normal') {
      setShowActionPanel(!showActionPanel);
    } else if (image.url && onFullScreen) {
      onFullScreen(batchId, index);
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

  const sizeClasses = viewMode === 'small' 
    ? 'aspect-square w-full h-full' 
    : 'aspect-square w-full';

  const shouldShowActionsMenu = (isMobile ? showActionPanel : (isHovered || showActionPanel)) && 
                      image.url && 
                      viewMode === 'normal' &&
                      showActions;

  return (
    <div 
      className={`relative rounded-md overflow-hidden group ${viewMode === 'small' ? 'mb-1' : 'w-full'}`}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
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
          <ImageLoadingState />
        )}
        
        <BatchCountDisplay 
          index={index} 
          total={total} 
          viewMode={viewMode} 
        />
        
        <ImageActionButtons 
          onDeleteImage={onDeleteImage ? handleDeleteImage : undefined}
          onFullScreen={onFullScreen ? handleFullScreen : undefined}
          viewMode={viewMode}
        />
        
        <ImageNavigationButtons 
          index={index}
          total={total}
          onNavigatePrev={onNavigatePrev ? handleNavigatePrev : undefined}
          onNavigateNext={onNavigateNext ? handleNavigateNext : undefined}
        />
        
        {shouldShowActionsMenu && (
          <ImageActionsPanel
            show={shouldShowActionsMenu}
            imageUrl={image.url}
            onCreateAgain={onCreateAgain ? handleCreateAgain : undefined}
            onUseAsInput={onUseAsInput ? handleUseAsInput : undefined}
            generationInfo={{
              prompt: image.prompt || '',
              workflow: image.workflow || '',
              params: image.params
            }}
          />
        )}
      </div>
    </div>
  );
};

export default ImageBatchItem;
