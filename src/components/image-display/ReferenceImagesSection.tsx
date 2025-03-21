
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
    <div className="flex flex-wrap gap-2">
      {images.map((image, index) => (
        <div key={index} className="relative w-16 h-16 rounded-md overflow-hidden border border-border/50">
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
