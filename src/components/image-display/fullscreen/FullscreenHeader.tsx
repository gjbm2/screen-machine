
import React from 'react';
import { X } from 'lucide-react';
import ImagePrompt from '../detail-view/ImagePrompt';

interface FullscreenHeaderProps {
  prompt: string;
  hasReferenceImages: boolean;
  onReferenceImageClick: () => void;
  workflowName?: string;
  onInfoClick: () => void;
  onClose: (e: React.MouseEvent) => void;
  imageNumber: number;
  title?: string; 
}

const FullscreenHeader: React.FC<FullscreenHeaderProps> = ({
  prompt,
  hasReferenceImages,
  onReferenceImageClick,
  workflowName,
  onInfoClick,
  onClose,
  imageNumber,
  title
}) => {
  // Always use the title for display
  const displayText = title || prompt;
  
  // Add debug logging
  console.log(`FullscreenHeader rendering with prompt: "${prompt}", workflowName: ${workflowName}, and title: ${title}`);
  
  return (
    <div className="px-4 py-2 border-b h-10 flex-shrink-0 flex items-center">
      <div className="flex items-center justify-between w-full min-w-0 overflow-hidden">
        <div className="flex-grow min-w-0 overflow-hidden">
          <ImagePrompt 
            prompt={prompt}
            hasReferenceImages={hasReferenceImages}
            onReferenceImageClick={onReferenceImageClick}
            imageNumber={imageNumber}
            workflowName={workflowName}
            onInfoClick={onInfoClick}
            title={title}
            useTitle={true} // Always use the title
          />
        </div>
        
        {/* Close button */}
        <button 
          onClick={onClose}
          className="inline-flex items-center justify-center p-1 hover:bg-gray-100 rounded-md flex-shrink-0 ml-2"
          aria-label="Close dialog"
        >
          <X size={16} />
        </button>
      </div>
    </div>
  );
};

export default FullscreenHeader;
