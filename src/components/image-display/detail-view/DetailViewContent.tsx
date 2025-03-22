
import React, { useState, useEffect } from 'react';
import ImageKeyboardNavigation from './ImageKeyboardNavigation';
import DetailViewImageSection from './DetailViewImageSection';
import DetailViewInfoPanel from './DetailViewInfoPanel';
import ReferenceImageDialog from '../ReferenceImageDialog';
import ImageInfoDialog from '../ImageInfoDialog';

interface DetailViewContentProps {
  batchId: string;
  images: Array<{
    url: string;
    prompt?: string;
    workflow: string;
    status?: 'generating' | 'completed' | 'error';
    params?: Record<string, any>;
    referenceImageUrl?: string;
    timestamp?: number;
  }>;
  activeIndex: number;
  onSetActiveIndex: (index: number) => void;
  onNavigatePrev: (e: React.MouseEvent) => void;
  onNavigateNext: (e: React.MouseEvent) => void;
  onToggleExpand: (batchId: string) => void;
  onDeleteImage: (batchId: string, index: number) => void;
  onCreateAgain: (batchId: string) => void;
  onUseAsInput?: ((imageUrl: string) => void) | null;
  onImageClick?: (e: React.MouseEvent) => void;
  allImages?: Array<{
    url: string;
    batchId: string;
    batchIndex: number;
    prompt?: string;
  }>;
  isNavigatingAllImages?: boolean;
  onNavigateGlobal?: (imageIndex: number) => void;
  currentGlobalIndex?: number;
  hidePrompt?: boolean;
}

const DetailViewContent: React.FC<DetailViewContentProps> = ({
  batchId,
  images,
  activeIndex,
  onSetActiveIndex,
  onNavigatePrev,
  onNavigateNext,
  onToggleExpand,
  onDeleteImage,
  onCreateAgain,
  onUseAsInput,
  onImageClick,
  allImages,
  isNavigatingAllImages,
  onNavigateGlobal,
  currentGlobalIndex,
  hidePrompt
}) => {
  const activeImage = images[activeIndex];
  const [showReferenceImage, setShowReferenceImage] = useState(false);
  const [showImageInfo, setShowImageInfo] = useState(false);
  const referenceImageUrl = activeImage?.referenceImageUrl;
  
  const [imageDimensions, setImageDimensions] = useState({ width: 0, height: 0 });
  
  // Debug log when activeImage changes
  useEffect(() => {
    if (activeImage) {
      console.log("Active image in DetailViewContent:", activeImage);
      if (activeImage.referenceImageUrl) {
        console.log("Active image has reference image URL:", activeImage.referenceImageUrl);
      } else {
        console.log("Active image has NO reference image URL");
      }
    }
  }, [activeImage]);
  
  const handleImageLoad = (e: React.SyntheticEvent<HTMLImageElement>) => {
    const img = e.currentTarget;
    setImageDimensions({
      width: img.naturalWidth,
      height: img.naturalHeight
    });
  };
  
  const handleOpenInNewTab = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (activeImage?.url) {
      window.open(activeImage.url, '_blank', 'noopener,noreferrer');
    }
  };

  const handleCreateAgain = () => {
    onCreateAgain(batchId);
  };
  
  const handleUseAsInput = () => {
    if (onUseAsInput && activeImage.url) {
      onUseAsInput(activeImage.url);
    }
  };
  
  const handleDeleteImage = () => {
    onDeleteImage(batchId, activeIndex);
  };

  const handleInfoClick = () => {
    console.log("Info button clicked for image:", activeImage); 
    
    // Log reference image status
    if (activeImage?.referenceImageUrl) {
      console.log("Opening info dialog with reference images:", activeImage.referenceImageUrl);
    } else {
      console.log("Opening info dialog without reference images");
    }
    
    setShowImageInfo(true);
  };

  // Determine if there are reference images
  const hasReferenceImages = Boolean(referenceImageUrl);

  return (
    <div className="flex flex-col h-full overflow-hidden min-h-0 min-w-0">
      {/* Keyboard navigation */}
      <ImageKeyboardNavigation 
        activeIndex={activeIndex}
        imagesLength={images.length}
        onNavigatePrev={onNavigatePrev}
        onNavigateNext={onNavigateNext}
        allImages={allImages}
        currentGlobalIndex={currentGlobalIndex}
        onNavigateGlobal={onNavigateGlobal}
      />

      {/* Selected image view - adaptive sizing to ensure control visibility */}
      {activeImage && (
        <div className="flex-grow overflow-hidden flex flex-col min-h-0 min-w-0 w-auto">
          <DetailViewImageSection
            activeImage={activeImage}
            onImageLoad={handleImageLoad}
            onOpenInNewTab={handleOpenInNewTab}
            allImages={allImages}
            isNavigatingAllImages={isNavigatingAllImages}
            onNavigateGlobal={onNavigateGlobal}
            currentGlobalIndex={currentGlobalIndex}
            onImageClick={onImageClick}
          />
        </div>
      )}
      
      {/* Bottom panel with metadata and controls */}
      <DetailViewInfoPanel
        activeImage={activeImage}
        dimensions={imageDimensions}
        referenceImageUrl={referenceImageUrl}
        showReferenceImage={showReferenceImage}
        setShowReferenceImage={setShowReferenceImage}
        handleCreateAgain={handleCreateAgain}
        handleUseAsInput={onUseAsInput ? handleUseAsInput : undefined}
        handleDeleteImage={handleDeleteImage}
        onOpenInNewTab={handleOpenInNewTab}
        hidePrompt={hidePrompt}
        onInfoClick={handleInfoClick}
      />

      {/* Reference image dialog */}
      {referenceImageUrl && (
        <ReferenceImageDialog
          isOpen={showReferenceImage}
          onOpenChange={setShowReferenceImage}
          imageUrl={referenceImageUrl.split(',')[0]} // Use the first URL if multiple
        />
      )}

      {/* Image info dialog */}
      {activeImage && (
        <ImageInfoDialog
          isOpen={showImageInfo}
          onOpenChange={setShowImageInfo}
          image={activeImage}
          dimensions={imageDimensions}
        />
      )}
    </div>
  );
};

export default DetailViewContent;
