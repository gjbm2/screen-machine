
import React from 'react';
import { ViewMode } from './ImageDisplay';
import ImagePrompt from './detail-view/ImagePrompt';

interface ImageBatchItemContentProps {
  imageUrl: string;
  prompt?: string;
  index: number;
  onClick?: () => void;
  viewMode?: ViewMode;
  className?: string;
  hasReferenceImages?: boolean;
  title?: string;
  batchIndex?: number;
  batchSize?: number;
}

const ImageBatchItemContent: React.FC<ImageBatchItemContentProps> = ({
  imageUrl,
  prompt,
  index,
  onClick,
  viewMode = 'normal',
  className = '',
  hasReferenceImages = false,
  title,
  batchIndex,
  batchSize = 0
}) => {
  // For small view, we don't include the prompt (to save space)
  const showPrompt = viewMode !== 'small';
  
  return (
    <div 
      className={`flex flex-col gap-1 relative w-full h-full ${className}`}
      onClick={onClick}
    >
      {/* Image container */}
      <div className="aspect-square overflow-hidden rounded-md relative">
        <img
          src={imageUrl}
          alt={prompt || `Generated image ${index + 1}`}
          className="w-full h-full object-cover hover:scale-105 transition-transform cursor-pointer"
        />
      </div>
      
      {/* Prompt below image (only in normal and large views) */}
      {showPrompt && (
        <div className="mt-1 px-0.5">
          <ImagePrompt 
            prompt={prompt || ''} 
            hasReferenceImages={hasReferenceImages} 
            imageNumber={index + 1}
            title={title}
            batchIndex={batchIndex}
            batchSize={batchSize}
          />
        </div>
      )}
    </div>
  );
};

export default ImageBatchItemContent;
