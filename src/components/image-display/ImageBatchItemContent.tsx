
import React from 'react';
import { ViewMode } from './ImageDisplay';
import ReferenceImageIndicator from './ReferenceImageIndicator';

interface ImageBatchItemContentProps {
  imageUrl: string;
  prompt?: string;
  index: number;
  onClick: (e: React.MouseEvent) => void;
  viewMode: ViewMode;
  hasReferenceImages?: boolean;
  title?: string;
  isExpandedMain?: boolean;
}

const ImageBatchItemContent: React.FC<ImageBatchItemContentProps> = ({
  imageUrl,
  prompt,
  index,
  onClick,
  viewMode,
  hasReferenceImages = false,
  title,
  isExpandedMain = false
}) => {
  // For expanded main view, we use object-contain to preserve aspect ratio
  // within the 4:3 container defined in ExpandedBatchView
  const imageObjectFit = isExpandedMain ? 'object-contain' : 'object-cover';
  
  // Add background color to the image container
  const containerBgColor = 'bg-[#f3f3f3]';
  
  return (
    <div 
      className={`w-full h-full ${containerBgColor} ${viewMode === 'normal' ? 'aspect-square' : ''}`}
      onClick={onClick}
    >
      <img 
        src={imageUrl} 
        alt={prompt || `Generated image ${index + 1}`} 
        title={title || prompt || `Generated image ${index + 1}`}
        className={`w-full h-full ${imageObjectFit}`}
      />
      
      {/* Show reference image indicator if image has reference images */}
      {hasReferenceImages && (
        <ReferenceImageIndicator imageUrl={imageUrl} />
      )}
    </div>
  );
};

export default ImageBatchItemContent;
