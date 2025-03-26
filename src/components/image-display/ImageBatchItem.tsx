
import React from 'react';
import { ViewMode } from './ImageDisplay';
import ImageActionButtons from './ImageActionButtons';
import ImageNavigationButtons from './ImageNavigationButtons';
import BatchCountDisplay from './BatchCountDisplay';
import ImageBatchItemContent from './ImageBatchItemContent';
import { useImageBatchItem } from './hooks/useImageBatchItem';
import { Button } from '@/components/ui/button';
import { Maximize } from 'lucide-react';
import { useIsMobile } from '@/hooks/use-mobile';

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
  isExpandedMain?: boolean;
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
  isRolledUp = false,
  isExpandedMain = false
}) => {
  const isMobile = useIsMobile();
  
  const {
    isHovered,
    setIsHovered,
    showActionButtons,
    setShowActionButtons,
    handleCreateAgain,
    handleUseAsInput,
    handleFullScreen,
    handleDeleteImage,
    handleDownload,
    handleImageClick,
    shouldShowActionButtons,
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
      className={`relative rounded-md overflow-hidden group ${viewMode === 'small' ? 'mb-1' : 'w-full h-full'}`}
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
        isExpandedMain={isExpandedMain}
      />
      
      <BatchCountDisplay 
        index={index} 
        total={total} 
        viewMode={viewMode} 
      />
      
      {/* Navigation buttons should always be visible when available, not hidden by hover overlay */}
      <div className="absolute top-0 bottom-0 left-0 right-0 pointer-events-none z-30">
        <ImageNavigationButtons 
          index={index}
          total={total}
          onNavigatePrev={onNavigatePrev}
          onNavigateNext={onNavigateNext}
          alwaysVisible={!isRolledUp}
        />
      </div>
      
      {/* Fullscreen button in top right of the image on hover */}
      {showActions && viewMode === 'normal' && onFullScreen && isHovered && !isMobile && (
        <Button
          type="button"
          variant="ghost"
          className="absolute top-2 right-2 bg-black/70 hover:bg-black/90 text-white h-8 w-8 p-1 rounded-full z-30"
          onClick={(e) => handleFullScreen(e)}
          aria-label="View fullscreen"
        >
          <Maximize className="h-4 w-4" />
        </Button>
      )}
      
      {/* Display image actions on hover in normal mode for desktop only */}
      {showActions && viewMode === 'normal' && !isMobile && (
        <ImageActionButtons 
          onDeleteImage={onDeleteImage ? handleDeleteImage : undefined}
          onFullScreen={null} // Removed from bottom bar since we have it at the top now
          onUseAsInput={onUseAsInput ? handleUseAsInput : undefined}
          onCreateAgain={onCreateAgain ? handleCreateAgain : undefined}
          onDownload={handleDownload}
          viewMode={viewMode}
          forceShow={showActionButtons}
          isRolledUp={isRolledUp}
          isHovered={isHovered}
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
