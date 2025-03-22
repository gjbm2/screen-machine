
import React from 'react';
import ImageMetadata from '../ImageMetadata';
import DetailViewActionBar from './DetailViewActionBar';
import ReferenceImageSection from '../ReferenceImageSection';

interface DetailViewInfoPanelProps {
  activeImage: {
    url: string;
    prompt?: string;
    workflow: string;
    params?: Record<string, any>;
    timestamp?: number;
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
  // Debug log for reference image URL
  if (referenceImageUrl) {
    console.log('DetailViewInfoPanel received referenceImageUrl:', referenceImageUrl);
  }
  
  return (
    <div className="flex-shrink-0 p-2 space-y-1 bg-background select-none border-t min-h-[100px]">
      {/* Image metadata - now includes the "open in new tab" button */}
      <ImageMetadata
        dimensions={dimensions}
        timestamp={activeImage?.timestamp}
        imageUrl={activeImage?.url}
        onOpenInNewTab={onOpenInNewTab}
        onInfoClick={onInfoClick}
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
            params: activeImage.params
          }}
        />
      )}
      
      {/* Reference image at the bottom */}
      {referenceImageUrl && (
        <ReferenceImageSection
          referenceImageUrl={referenceImageUrl}
          onReferenceImageClick={() => setShowReferenceImage(true)}
        />
      )}
    </div>
  );
};

export default DetailViewInfoPanel;
