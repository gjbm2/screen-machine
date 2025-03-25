
import React from 'react';
import { ViewMode } from './ImageDisplay';
import ImageActionButtons from './ImageActionButtons';
import ImageNavigationButtons from './ImageNavigationButtons';
import BatchCountDisplay from './BatchCountDisplay';
import ImageBatchItemContent from './ImageBatchItemContent';
import { useImageBatchItem } from './hooks/useImageBatchItem';
import { Button } from '@/components/ui/button';
import { Maximize } from 'lucide-react';

interface ImageBatchItemProps {
  image: {
    url: string;
    prompt?: string;
    workflow?: string;
    timestamp?: number;
    params?: Record<string, any>;
    batchId?: string;
    batchIndex?: number;
    status?: string;
    refiner?: string;
    referenceImageUrl?: string;
    title?: string;
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
  const {
    isHovered,
    setIsHovered,
    showActionButtons,
    handleCreateAgain,
    handleUseAsInput,
    handleFullScreen,
    handleDeleteImage,
    handleDownload,
    handleImageClick,
    shouldShowActionButtons,
    isMobile
  } = useImageBatchItem({
    image,
    batchId,
    index,
    onCreateAgain,
    onUseAsInput,
    onDeleteImage,
    onFullScreen,
    onImageClick,
    viewMode,
    isRolledUp
  });

  // Parse reference images if they exist
  const hasReferenceImages = Boolean(image.referenceImageUrl && image.referenceImageUrl.trim() !== '');

  return (
    <div 
      className={`relative rounded-md overflow-hidden group ${viewMode === 'small' ? 'mb-1' : 'w-full'}`}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      <ImageBatchItemContent 
        imageUrl={image.url}
        prompt={image.prompt}
        index={index}
        onClick={handleImageClick}
        viewMode={viewMode}
        hasReferenceImages={hasReferenceImages}
        title={image.title}
      />
      
      <BatchCountDisplay 
        index={index} 
        total={total} 
        viewMode={viewMode} 
      />
      
      <ImageNavigationButtons 
        index={index}
        total={total}
        onNavigatePrev={onNavigatePrev}
        onNavigateNext={onNavigateNext}
      />
      
      {/* Fullscreen button in top right of the image on hover */}
      {showActions && viewMode === 'normal' && onFullScreen && isHovered && !isMobile && (
        <Button
          type="button"
          variant="ghost"
          className="absolute top-2 right-2 bg-black/70 hover:bg-black/90 text-white h-8 w-8 p-1 rounded-full z-30"
          onClick={handleFullScreen}
          aria-label="View fullscreen"
        >
          <Maximize className="h-4 w-4" />
        </Button>
      )}
      
      {/* Display image actions on hover in normal mode for desktop */}
      {showActions && viewMode === 'normal' && (
        <ImageActionButtons 
          onDeleteImage={onDeleteImage ? handleDeleteImage : undefined}
          onFullScreen={null} // Removed from bottom bar since we have it at the top now
          onUseAsInput={onUseAsInput ? handleUseAsInput : undefined}
          onCreateAgain={onCreateAgain ? handleCreateAgain : undefined}
          onDownload={handleDownload}
          viewMode={viewMode}
          forceShow={isMobile && showActionButtons}
          isRolledUp={isRolledUp}
          isHovered={isHovered && !isMobile}
          includePublish={true}
          publishInfo={{
            imageUrl: image.url,
            generationInfo: {
              prompt: image.prompt,
              workflow: image.workflow,
              params: image.params
            }
          }}
        />
      )}
    </div>
  );
};

export default ImageBatchItem;
