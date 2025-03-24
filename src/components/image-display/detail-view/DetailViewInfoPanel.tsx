
import React, { useEffect } from 'react';
import ImageMetadata from '../ImageMetadata';
import DetailViewActionBar from './DetailViewActionBar';
import ImagePrompt from './ImagePrompt';

interface DetailViewInfoPanelProps {
  activeImage: {
    url: string;
    prompt?: string;
    workflow: string;
    params?: Record<string, any>;
    timestamp?: number;
    referenceImageUrl?: string;
    title?: string; // Added title property to the activeImage type
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
  onReferenceImageClick?: () => void;
  onClose?: () => void; // Added for closing fullscreen view
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
  onInfoClick,
  onReferenceImageClick,
  onClose // New prop for closing
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
  
  // Process reference images array from string to determine if we have any
  const hasReferenceImages = React.useMemo(() => {
    if (!effectiveReferenceImageUrl) return false;
    
    if (typeof effectiveReferenceImageUrl === 'string') {
      const urls = effectiveReferenceImageUrl
        .split(',')
        .map(url => url.trim())
        .filter(url => url !== '');
      return urls.length > 0;
    }
    
    return Array.isArray(effectiveReferenceImageUrl) ? effectiveReferenceImageUrl.length > 0 : false;
  }, [effectiveReferenceImageUrl]);
  
  return (
    <div className="flex-shrink-0 p-2 space-y-1 bg-background select-none border-t min-h-[100px]">
      {/* Prompt display with reference image button */}
      {!hidePrompt && activeImage?.prompt && (
        <ImagePrompt
          prompt={activeImage.prompt}
          hasReferenceImages={hasReferenceImages}
          onReferenceImageClick={onReferenceImageClick}
          workflowName={activeImage.workflow}
          onInfoClick={onInfoClick}
          title={activeImage.title}
        />
      )}
      
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
          onClose={onClose} // Pass the onClose handler
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
