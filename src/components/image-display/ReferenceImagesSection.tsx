
import React from 'react';
import ReferenceImageIndicator from './ReferenceImageIndicator';

interface ReferenceImagesSectionProps {
  images: string[];
  onRemoveImage?: (index: number) => void;
}

const ReferenceImagesSection: React.FC<ReferenceImagesSectionProps> = ({ 
  images,
  onRemoveImage
}) => {
  if (!images || images.length === 0) {
    return null;
  }

  return (
    <div className="flex flex-wrap gap-3">
      {images.map((image, index) => (
        <div key={index} className="relative w-1/2 sm:w-32 h-32 rounded-md overflow-hidden border border-border/50 bg-muted/20">
          <ReferenceImageIndicator 
            imageUrl={image} 
            onRemove={onRemoveImage ? () => onRemoveImage(index) : undefined}
          />
        </div>
      ))}
    </div>
  );
};

export default ReferenceImagesSection;
