
import React, { useState } from 'react';
import ImageActions from '../ImageActions';
import ReferenceImageIndicator from './ReferenceImageIndicator';

interface ImageBatchItemContentProps {
  url: string;
  prompt: string;
  onClick: () => void;
  onUseAsInput: () => void;
  onCreateAgain: () => void;
  onFullScreenClick: () => void;
  onDeleteImage: () => void;
  referenceImageUrl?: string | null;
  isRolledUp?: boolean;
  batchId?: string;
  activeGenerations?: string[]; // Add activeGenerations prop
}

const ImageBatchItemContent: React.FC<ImageBatchItemContentProps> = ({
  url,
  prompt,
  onClick,
  onUseAsInput,
  onCreateAgain,
  onFullScreenClick,
  onDeleteImage,
  referenceImageUrl = null,
  isRolledUp = false,
  batchId,
  activeGenerations = [] // Default to empty array
}) => {
  const [showActions, setShowActions] = useState(false);

  return (
    <div 
      className="group relative aspect-square overflow-hidden"
      onMouseEnter={() => setShowActions(true)}
      onMouseLeave={() => setShowActions(false)}
    >
      <img 
        src={url} 
        alt={prompt || 'Generated image'} 
        className="w-full h-full object-cover"
        onClick={onClick}
      />
      
      {/* Image actions overlay */}
      <div 
        className={`absolute inset-0 bg-black/60 flex items-center justify-center transition-opacity duration-200 ${
          showActions || isRolledUp ? 'opacity-100' : 'opacity-0'
        }`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex flex-col gap-2">
          <div className="text-center text-white text-xs line-clamp-3 w-full max-w-[200px] px-1">
            {prompt}
          </div>
          
          <div className="flex flex-wrap justify-center">
            <ImageActions
              imageUrl={url}
              onCreateAgain={onCreateAgain}
              onUseAsInput={onUseAsInput}
              onDeleteImage={onDeleteImage}
              small={true}
              generationInfo={{
                prompt: prompt,
                workflow: '',
                title: prompt
              }}
            />
            
            {/* Fullscreen button if not in a rolled-up view */}
            {!isRolledUp && (
              <button 
                className="text-white hover:text-primary text-sm p-1"
                onClick={onFullScreenClick}
              >
                View
              </button>
            )}
          </div>
        </div>
      </div>
      
      {/* Reference image indicator - only if there's a reference image */}
      {referenceImageUrl && (
        <ReferenceImageIndicator imageUrl={referenceImageUrl} />
      )}
    </div>
  );
};

export default ImageBatchItemContent;
