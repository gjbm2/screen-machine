
import React from 'react';
import ImageMetadata from '../ImageMetadata';
import DetailViewActionBar from './DetailViewActionBar';
import ImagePrompt from './ImagePrompt';
import ReferenceImageSection from '../ReferenceImageSection';
import ReferenceImageDialog from '../ReferenceImageDialog';

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
  hidePrompt?: boolean;
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
  hidePrompt = false
}) => {
  return (
    <div className="flex-shrink-0 p-2 space-y-2 bg-background">
      {/* Image metadata */}
      <ImageMetadata
        dimensions={dimensions}
        timestamp={activeImage?.timestamp}
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
      
      {/* Prompt info - conditionally rendered based on hidePrompt */}
      {!hidePrompt && <ImagePrompt prompt={activeImage?.prompt} />}

      {/* Reference image at the bottom */}
      {referenceImageUrl && (
        <ReferenceImageSection
          referenceImageUrl={referenceImageUrl}
          onReferenceImageClick={() => setShowReferenceImage(true)}
        />
      )}

      {/* Reference image popup (full size view) */}
      {referenceImageUrl && (
        <ReferenceImageDialog
          isOpen={showReferenceImage}
          onOpenChange={setShowReferenceImage}
          imageUrl={referenceImageUrl}
        />
      )}
    </div>
  );
};

export default DetailViewInfoPanel;
