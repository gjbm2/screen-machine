
import React, { useEffect } from 'react';
import ImageMetadata from '../ImageMetadata';
import DetailViewActionBar from './DetailViewActionBar';

interface DetailViewInfoPanelProps {
  activeImage: {
    url: string;
    prompt?: string;
    workflow: string;
    params?: Record<string, any>;
    timestamp?: number;
    referenceImageUrl?: string;
  };
  dimensions: { width: number; height: number };
  referenceImageUrl?: string;
  showReferenceImage: boolean;
  setShowReferenceImage: (show: boolean) => void;
  handleCreateAgain: () => void;
  handleUseAsInput?: () => void;
  handleDeleteImage: () => void;
  onOpenInNewTab: (e: React.MouseEvent) => void;
  hidePrompt?: boolean;
  onInfoClick?: () => void;
}

const DetailViewInfoPanel: React.FC<DetailViewInfoPanelProps> = ({
  activeImage,
  dimensions,
  referenceImageUrl,
  showReferenceImage,
  setShowReferenceImage,
  handleCreateAgain,
  handleUseAsInput,
  handleDeleteImage,
  onOpenInNewTab,
  hidePrompt = false,
  onInfoClick
}) => {
  // Use the referenceImageUrl from props first, then from activeImage as fallback
  const effectiveReferenceImageUrl = referenceImageUrl || activeImage?.referenceImageUrl;
  
  useEffect(() => {
    // Log reference image information for debugging
    console.log('DetailViewInfoPanel - activeImage:', activeImage);
    if (effectiveReferenceImageUrl) {
      console.log('DetailViewInfoPanel: Reference image URL:', effectiveReferenceImageUrl);
    } else {
      console.log('DetailViewInfoPanel has no referenceImageUrl');
    }
  }, [effectiveReferenceImageUrl, activeImage]);
  
  // Determine if this image has reference images
  const hasReferenceImages = Boolean(effectiveReferenceImageUrl);
  
  return (
    <div className="flex-shrink-0 p-2 space-y-1 bg-background select-none border-t min-h-[100px]">
      {/* Image metadata - now includes the "open in new tab" button */}
      <ImageMetadata
        dimensions={dimensions}
        timestamp={activeImage?.timestamp}
        imageUrl={activeImage?.url}
        onOpenInNewTab={onOpenInNewTab}
        onInfoClick={onInfoClick}
        hasReferenceImages={hasReferenceImages}
      />
      
      {/* Image Actions Bar - all buttons in a single row */}
      {activeImage?.url && (
        <DetailViewActionBar 
          imageUrl={activeImage.url}
          onCreateAgain={handleCreateAgain}
          onUseAsInput={handleUseAsInput}
          onDeleteImage={handleDeleteImage}
          generationInfo={{
            prompt: activeImage.prompt || '',
            workflow: activeImage.workflow || '',
            params: activeImage.params,
            referenceImageUrl: effectiveReferenceImageUrl
          }}
        />
      )}
    </div>
  );
};

export default DetailViewInfoPanel;
