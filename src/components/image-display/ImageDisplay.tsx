import React from 'react';
import ImageBatch from './ImageBatch';
import FullscreenDialog from './FullscreenDialog';
import { useFullscreen } from './hooks/useFullscreen';
import { ImageDisplayHeader } from './ImageDisplayHeader';

interface ImageDisplayProps {
  imageUrl: string | null;
  prompt: string;
  isLoading: boolean;
  uploadedImages: string[];
  generatedImages: any[];
  imageContainerOrder: string[];
  workflow: string;
  generationParams: Record<string, any>;
  onUseGeneratedAsInput: (url: string) => void;
  onCreateAgain: (batchId: string) => void;
  onReorderContainers: (newOrder: string[]) => void;
  onDeleteImage: (batchId: string, index: number) => void;
  onDeleteContainer: (batchId: string) => void;
  fullscreenRefreshTrigger?: number;
  activeGenerations?: string[]; // Add this with a default
}

const ImageDisplay: React.FC<ImageDisplayProps> = ({
  imageUrl,
  prompt,
  isLoading,
  uploadedImages,
  generatedImages,
  imageContainerOrder,
  workflow,
  generationParams,
  onUseGeneratedAsInput,
  onCreateAgain,
  onReorderContainers,
  onDeleteImage,
  onDeleteContainer,
  fullscreenRefreshTrigger,
  activeGenerations = [] // Add this with a default
}) => {
  const allImagesFlat = generatedImages.reduce((acc, img) => {
    if (Array.isArray(img)) {
      return acc.concat(img);
    } else {
      acc.push(img);
      return acc;
    }
  }, []);

  const batches = generatedImages.reduce((acc, img) => {
    if (Array.isArray(img)) {
      img.forEach(i => {
        if (!acc[i.batchId]) {
          acc[i.batchId] = [];
        }
        acc[i.batchId].push(i);
      });
    }
    return acc;
  }, {});

  const {
    showFullScreenView,
    setShowFullScreenView,
    fullScreenBatchId,
    fullScreenImageIndex,
    setFullScreenImageIndex,
    currentGlobalIndex,
    openFullScreenView,
    handleNavigateGlobal,
    handleNavigateWithBatchAwareness
  } = useFullscreen(allImagesFlat);

  const handleReorderContainers = (newOrder: string[]) => {
    onReorderContainers(newOrder);
  };

  // Render the component based on the state
  return (
    <div className="w-full flex-grow max-w-full relative overflow-hidden flex flex-col min-h-0">
      <ImageDisplayHeader
        prompt={prompt}
        isLoading={isLoading}
        uploadedImages={uploadedImages}
      />
      
      <div className="flex-grow overflow-auto px-3 md:px-5 py-3 min-h-0">
        {generatedImages.length === 0 && !isLoading && (
          <div className="flex items-center justify-center h-full text-muted-foreground">
            No images generated yet.
          </div>
        )}
        
        {imageContainerOrder.length > 0 && (
          <div className="space-y-8">
            {imageContainerOrder.map((batchId) => {
              const batchImages = generatedImages.filter(img => img.batchId === batchId);
              return (
                <ImageBatch
                  key={batchId}
                  batchId={batchId}
                  images={batchImages}
                  onDelete={onDeleteImage}
                  onCreateAgain={onCreateAgain}
                  onFullscreenView={openFullScreenView}
                  onUseAsInput={onUseGeneratedAsInput}
                  onDeleteBatch={onDeleteContainer}
                  activeGenerations={activeGenerations} // Pass the activeGenerations prop
                />
              );
            })}
          </div>
        )}
      </div>
      
      <FullscreenDialog
        showFullScreenView={showFullScreenView}
        setShowFullScreenView={setShowFullScreenView}
        fullScreenBatchId={fullScreenBatchId}
        batches={batches}
        fullScreenImageIndex={fullScreenImageIndex}
        setFullScreenImageIndex={setFullScreenImageIndex}
        onDeleteImage={onDeleteImage}
        onCreateAgain={onCreateAgain}
        onUseGeneratedAsInput={onUseGeneratedAsInput}
        allImagesFlat={allImagesFlat}
        currentGlobalIndex={currentGlobalIndex}
        handleNavigateGlobal={handleNavigateGlobal}
        handleNavigateWithBatchAwareness={handleNavigateWithBatchAwareness}
        fullscreenRefreshTrigger={fullscreenRefreshTrigger}
      />
    </div>
  );
};

export default ImageDisplay;
