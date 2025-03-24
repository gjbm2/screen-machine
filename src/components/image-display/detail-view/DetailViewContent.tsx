
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
  onClose?: () => void; // Added for closing fullscreen view
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
  hidePrompt,
  onClose // New prop for closing
}) => {
  const activeImage = images[activeIndex];
  const [showReferenceImage, setShowReferenceImage] = useState(false);
  const [showImageInfo, setShowImageInfo] = useState(false);
  const referenceImageUrl = activeImage?.referenceImageUrl;
  
  const [imageDimensions, setImageDimensions] = useState({ width: 0, height: 0 });
  
  useEffect(() => {
    if (activeImage) {
      console.log("Active image in DetailViewContent:", activeImage);
      console.log("Active image reference image URL:", activeImage.referenceImageUrl);
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
    if (onClose) {
      onClose();
    }
  };

  const handleInfoClick = () => {
    console.log("Info button clicked for image:", activeImage); 
    
    if (activeImage?.referenceImageUrl) {
      console.log("Opening info dialog with reference images:", activeImage.referenceImageUrl);
    } else {
      console.log("Opening info dialog without reference images");
    }
    
    setShowImageInfo(true);
  };

  // Handle opening reference image dialog directly
  const handleReferenceImageClick = () => {
    console.log("Reference image button clicked in DetailViewContent");
    setShowReferenceImage(true);
  };

  const hasReferenceImages = Boolean(referenceImageUrl);

  // Process reference images array from string or array
  const referenceImages = React.useMemo(() => {
    if (!referenceImageUrl) return [];
    
    if (typeof referenceImageUrl === 'string') {
      return referenceImageUrl.split(',')
        .map(url => url.trim())
        .filter(url => url !== '');
    }
    
    return Array.isArray(referenceImageUrl) ? referenceImageUrl : [];
  }, [referenceImageUrl]);

  return (
    <div className="flex flex-col h-full overflow-hidden min-h-0 min-w-0">
      <ImageKeyboardNavigation 
        activeIndex={activeIndex}
        imagesLength={images.length}
        onNavigatePrev={onNavigatePrev}
        onNavigateNext={onNavigateNext}
        allImages={allImages}
        currentGlobalIndex={currentGlobalIndex}
        onNavigateGlobal={onNavigateGlobal}
      />

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
        onReferenceImageClick={hasReferenceImages ? handleReferenceImageClick : undefined}
        onClose={onClose}
      />

      {/* Reference image dialog for direct reference image button click */}
      {hasReferenceImages && (
        <ReferenceImageDialog
          isOpen={showReferenceImage}
          onOpenChange={setShowReferenceImage}
          imageUrls={referenceImages}
        />
      )}

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
