
import React from 'react';
import ImageBatch from '../ImageBatch';
import LoadingPlaceholder from '../LoadingPlaceholder';

interface SmallGridViewProps {
  sortedContainerIds: string[]; // Changed from imageContainerOrder to sortedContainerIds
  batches: Record<string, any[]>;
  expandedContainers: Record<string, boolean>;
  handleToggleExpand: (batchId: string) => void;
  onUseGeneratedAsInput: (url: string) => void;
  onCreateAgain: (batchId?: string) => void;
  onDeleteImage: (batchId: string, index: number) => void;
  onDeleteContainer: (batchId: string) => void;
  onFullScreenClick: (image: any) => void;
  imageUrl: string | null;
  getAllImages: () => any[];
  handleSmallImageClick: (image: any) => void;
  isLoading: boolean;
  activeGenerations?: string[];
}

const SmallGridView: React.FC<SmallGridViewProps> = ({
  sortedContainerIds, // Using sortedContainerIds
  batches,
  expandedContainers,
  handleToggleExpand,
  onUseGeneratedAsInput,
  onCreateAgain,
  onDeleteImage,
  onDeleteContainer,
  onFullScreenClick,
  imageUrl,
  getAllImages,
  handleSmallImageClick,
  isLoading,
  activeGenerations = []
}) => {
  const allImages = getAllImages();
  
  return (
    <div className="flex flex-col gap-4 mb-4">
      {isLoading && sortedContainerIds.length === 0 && (
        <LoadingPlaceholder prompt="Generating your image..." />
      )}
      
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-4">
        {allImages.map((image, index) => {
          // Handle the case where image is a placeholder
          if (image.status === 'generating') {
            return (
              <div key={`gen-${index}`} className="aspect-square bg-gray-100 animate-pulse rounded"></div>
            );
          }
          
          return (
            <div 
              key={`${image.batchId}-${image.batchIndex || index}`} 
              className="cursor-pointer"
              onClick={() => handleSmallImageClick(image)}
            >
              <img 
                src={image.url} 
                alt={image.prompt || `Image ${index}`}
                className="w-full h-auto rounded"
              />
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default SmallGridView;
