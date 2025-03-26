
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

    // Simply sort all images without attempting to filter by batch
    // This ensures we show one entry for each IMAGE, not each batch
    const sorted = [...images].sort((a, b) => {
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
    <div className="grid grid-cols-4 sm:grid-cols-6 md:grid-cols-8 lg:grid-cols-10 gap-0.5">
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
              prompt={image.prompt || null}
              errorMessage={image.errorMessage || "Generation failed"}
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
