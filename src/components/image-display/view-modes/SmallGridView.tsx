
import React from 'react';
import GenerationFailedPlaceholder from '../GenerationFailedPlaceholder';

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
  return (
    <div className="grid grid-cols-4 sm:grid-cols-6 md:grid-cols-8 gap-0.5">
      {images.map((image, idx) => (
        <div 
          key={`${image.batchId}-${image.batchIndex}`} 
          className="aspect-square rounded-md overflow-hidden cursor-pointer"
          onClick={() => onSmallImageClick(image)}
        >
          {image.status === 'completed' ? (
            <img 
              src={image.url}
              alt={image.prompt || `Generated image ${idx + 1}`}
              className="w-full h-full object-cover"
            />
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
