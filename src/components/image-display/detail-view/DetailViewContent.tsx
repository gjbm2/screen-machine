
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
    title?: string;
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
    title?: string;
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
  // Log the activeIndex and images length to verify which image we're trying to display
  console.log('DetailViewContent: activeIndex =', activeIndex, 'images.length =', images.length);
  
  // Make sure activeIndex is within bounds
  const validatedActiveIndex = Math.max(0, Math.min(activeIndex, images.length - 1));
  
  // Log if there was a correction
  if (validatedActiveIndex !== activeIndex) {
    console.log('DetailViewContent: activeIndex corrected from', activeIndex, 'to', validatedActiveIndex);
  }
  
  // Check if images array is empty and return early with a message
  if (!images || images.length === 0) {
    console.error('DetailViewContent: No images available to display');
    return (
      <div className="flex flex-col h-full items-center justify-center">
        <p className="text-gray-500">No images available to display</p>
        {onClose && (
          <button 
            className="mt-4 px-4 py-2 bg-gray-200 rounded hover:bg-gray-300"
            onClick={() => onClose()}
          >
            Close
          </button>
        )}
      </div>
    );
  }
  
  const activeImage = images[validatedActiveIndex];
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
    } else {
      console.error("No active image available at index", validatedActiveIndex);
    }
  }, [activeImage, validatedActiveIndex]);
  
  // If activeImage is undefined, show an error and return
  if (!activeImage) {
    console.error(`DetailViewContent: No active image found at index ${validatedActiveIndex}`);
    return (
      <div className="flex flex-col h-full items-center justify-center">
        <p className="text-red-500">Error: Image not found</p>
        {onClose && (
          <button 
            className="mt-4 px-4 py-2 bg-gray-200 rounded hover:bg-gray-300"
            onClick={() => onClose()}
          >
            Close
          </button>
        )}
      </div>
    );
  }
  
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
    if (onUseAsInput && activeImage?.url) {
      onUseAsInput(activeImage.url);
    }
  };
  
  const handleDeleteImage = () => {
    onDeleteImage(batchId, validatedActiveIndex);
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

  // Process reference images array from string or array - USING THE EXACT SAME LOGIC as ImageInfoDialog
  const referenceImages = React.useMemo(() => {
    if (!referenceImageUrl) return [];
    
    if (typeof referenceImageUrl === 'string') {
      return referenceImageUrl
        .split(',')
        .map(url => url.trim())
        .filter(url => url !== '');
    }
    
    return Array.isArray(referenceImageUrl) ? referenceImageUrl : [];
  }, [referenceImageUrl]);

  const hasReferenceImages = referenceImages.length > 0; // Fixed this logic to match ImageInfoDialog

  return (
    <div className="flex flex-col h-full overflow-hidden min-h-0 min-w-0">
      <ImageKeyboardNavigation 
        activeIndex={validatedActiveIndex}
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
        onClose={onClose}
      />

      {/* Reference image dialog - USING EXACTLY THE SAME STRUCTURE as in ImageInfoDialog */}
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
