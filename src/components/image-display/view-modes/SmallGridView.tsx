import React, { useEffect, useState } from 'react';
import GenerationFailedPlaceholder from '../GenerationFailedPlaceholder';
import LoadingPlaceholder from '../LoadingPlaceholder';

interface SmallGridViewProps {
  images: any[];
  isLoading: boolean;
  onSmallImageClick: (image: any) => void;
  onCreateAgain: (batchId?: string) => void;
  onDeleteImage: (batchId: string, index: number) => void;
}

const SmallGridView: React.FC<SmallGridViewProps> = ({
  images,
  isLoading,
  onSmallImageClick,
  onCreateAgain,
  onDeleteImage
}) => {
  const [sortedImages, setSortedImages] = useState<any[]>([]);

  useEffect(() => {
    if (!images || images.length === 0) {
      setSortedImages([]);
      return;
    }

    // Create a map to collect all images by batchId and batchIndex for proper filtering
    const imagesByBatchAndIndex = new Map<string, any>();
    
    // First pass: add all images to the map with a composite key of batchId-batchIndex
    // This ensures we only keep one image per unique batchId-batchIndex combination
    images.forEach(img => {
      const key = `${img.batchId}-${img.batchIndex || 0}`;
      // For generating images, always keep them
      // For other images, only keep if not already in the map or replace if newer
      if (img.status === 'generating' || !imagesByBatchAndIndex.has(key)) {
        imagesByBatchAndIndex.set(key, img);
      } else {
        // If we already have this image, keep the one with the newest timestamp
        const existingImg = imagesByBatchAndIndex.get(key);
        if ((img.timestamp || 0) > (existingImg.timestamp || 0)) {
          imagesByBatchAndIndex.set(key, img);
        }
      }
    });
    
    // Convert map values back to array and sort
    const uniqueImages = Array.from(imagesByBatchAndIndex.values());
    
    // Sort the unique images
    const sorted = uniqueImages.sort((a, b) => {
      // First prioritize by status - generating images always first
      if (a.status === 'generating' && b.status !== 'generating') return -1;
      if (a.status !== 'generating' && b.status === 'generating') return 1;
      
      // If both are generating, sort by timestamp (newest first)
      if (a.status === 'generating' && b.status === 'generating') {
        return (b.timestamp || 0) - (a.timestamp || 0);
      }
      
      // For all other images, sort by timestamp (newest first)
      return (b.timestamp || 0) - (a.timestamp || 0);
    });
    
    console.log('[SmallGridView] Total sorted images:', sorted.length);
    console.log('[SmallGridView] Generating images count:', sorted.filter(img => img.status === 'generating').length);
    
    setSortedImages(sorted);
  }, [images]);

  return (
    <div className="grid grid-cols-4 sm:grid-cols-6 md:grid-cols-8 gap-0.5">
      {sortedImages.map((image, idx) => (
        <div 
          key={`${image.batchId}-${image.batchIndex || idx}`} 
          className="aspect-square rounded-md overflow-hidden cursor-pointer"
          onClick={() => onSmallImageClick(image)}
        >
          {image.status === 'completed' ? (
            <img 
              src={image.url}
              alt={image.prompt || `Generated image ${idx + 1}`}
              className="w-full h-full object-cover"
            />
          ) : image.status === 'generating' ? (
            <LoadingPlaceholder prompt={image.prompt} isCompact={true} />
          ) : (
            <GenerationFailedPlaceholder 
              prompt={null} 
              onRetry={() => onCreateAgain(image.batchId)}
              onRemove={() => onDeleteImage(image.batchId || '', image.batchIndex || 0)}
              isCompact={true}
            />
          )}
        </div>
      ))}
      {isLoading && (
        <div className="aspect-square rounded-md overflow-hidden bg-muted flex items-center justify-center">
          <div className="animate-pulse flex flex-col items-center">
            <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent"></div>
          </div>
        </div>
      )}
    </div>
  );
};

export default SmallGridView;
