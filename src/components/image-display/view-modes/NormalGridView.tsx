
import React from 'react';
import ImageBatch from '../ImageBatch';

interface NormalGridViewProps {
  imageContainerOrder: string[];
  batches: Record<string, any[]>;
  expandedContainers: Record<string, boolean>;
  toggleExpand: (batchId: string) => void;
  onUseGeneratedAsInput: (url: string) => void;
  onCreateAgain: (batchId?: string) => void;
  onDeleteImage: (batchId: string, index: number) => void;
  onDeleteContainer: (batchId: string) => void;
  onFullScreenClick: (image: any) => void;
  imageUrl: string | null;
  mostRecentBatchId?: string;
}

const NormalGridView: React.FC<NormalGridViewProps> = ({
  imageContainerOrder,
  batches,
  expandedContainers,
  toggleExpand,
  onUseGeneratedAsInput,
  onCreateAgain,
  onDeleteImage,
  onDeleteContainer,
  onFullScreenClick,
  imageUrl,
  mostRecentBatchId
}) => {
  return (
    <div className="grid grid-cols-2 xs:grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-1">
      {imageContainerOrder.map((batchId) => {
        if (!batches[batchId]) return null;
        
        // If this is the most recent batch, ensure it's expanded, otherwise collapsed
        const isMostRecent = batchId === mostRecentBatchId;
        
        // If the expandedContainers state doesn't match our desired state, update it
        if (isMostRecent && !expandedContainers[batchId]) {
          setTimeout(() => toggleExpand(batchId), 0);
        }
        
        return (
          <div key={batchId} id={batchId} className={expandedContainers[batchId] ? "col-span-full" : ""}>
            <ImageBatch
              batchId={batchId}
              images={batches[batchId]}
              isExpanded={!!expandedContainers[batchId]}
              toggleExpand={toggleExpand}
              onImageClick={(url, prompt) => {
                // Always go to fullscreen when clicking image
                const image = batches[batchId].find(img => img.status === 'completed');
                if (image) {
                  onFullScreenClick(image);
                }
              }}
              onCreateAgain={() => onCreateAgain(batchId)}
              onDeleteImage={onDeleteImage}
              onDeleteContainer={() => onDeleteContainer(batchId)}
              activeImageUrl={imageUrl}
              viewMode="normal"
              onFullScreenClick={(image) => {
                if (image && image.batchId) {
                  onFullScreenClick(image);
                }
              }}
              thumbnailsAlignment="left" // Set thumbnails to be left-aligned
            />
          </div>
        );
      })}
    </div>
  );
};

export default NormalGridView;
