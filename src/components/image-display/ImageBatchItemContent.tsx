
import React from 'react';
import { Image, Info } from 'lucide-react';
import { ViewMode } from './ImageDisplay';

interface ImageBatchItemContentProps {
  imageUrl: string;
  prompt?: string;
  index: number;
  onClick: (e: React.MouseEvent) => void; // Updated to accept a MouseEvent parameter
  viewMode?: ViewMode;
  hasReferenceImages?: boolean;
  title?: string; // Add title field
}

const ImageBatchItemContent: React.FC<ImageBatchItemContentProps> = ({
  imageUrl,
  prompt,
  index,
  onClick,
  viewMode = 'normal',
  hasReferenceImages = false,
  title // Add to component props
}) => {
  // Simple truncate function
  const truncateText = (text: string, maxLength: number) => {
    if (!text) return '';
    return text.length > maxLength ? text.substring(0, maxLength) + '...' : text;
  };

  // Log for debugging
  if (hasReferenceImages) {
    console.log(`Image ${index} has reference images: ${hasReferenceImages}`);
  }

  // Use title if available, otherwise use prompt or default
  const displayText = title || prompt || `Generated image ${index + 1}`;

  return (
    <div 
      className={`w-full relative cursor-pointer ${viewMode === 'normal' ? 'aspect-square' : 'h-20'}`}
      onClick={onClick}
    >
      <img
        src={imageUrl}
        alt={displayText}
        className="w-full h-full object-cover"
      />
      
      {hasReferenceImages && (
        <div className="absolute top-1 left-1 bg-black/60 rounded-md p-0.5 text-white text-xs">
          <Image size={14} />
        </div>
      )}
      
      {viewMode === 'small' && (
        <div className="absolute bottom-0 left-0 right-0 p-1 text-[10px] leading-tight bg-black/60 text-white truncate">
          {truncateText(displayText, 30)}
        </div>
      )}
    </div>
  );
};

export default ImageBatchItemContent;
