
import React from 'react';
import { Image, Info } from 'lucide-react';
import { Button } from '@/components/ui/button';

interface ImagePromptProps {
  prompt: string;
  hasReferenceImages: boolean;
  onReferenceImageClick?: () => void;
  imageNumber?: number;
  workflowName?: string;
  onInfoClick?: () => void;
}

const ImagePrompt: React.FC<ImagePromptProps> = ({
  prompt,
  hasReferenceImages,
  onReferenceImageClick,
  imageNumber,
  workflowName,
  onInfoClick
}) => {
  // Determine what to display based on whether we have a prompt, reference images, or both
  const hasPrompt = prompt && prompt.trim() !== '';
  
  // Get a truncated display of the workflow name if available
  const workflowDisplay = workflowName ? 
    (workflowName.length > 10 ? `${workflowName.substring(0, 10)}...` : workflowName) : 
    null;
  
  // Debug log for reference images
  console.log(`ImagePrompt has reference images: ${hasReferenceImages}`);
  
  return (
    <div className="flex items-center gap-1 overflow-hidden">
      {/* Always show the image icon if we have reference images */}
      {hasReferenceImages && (
        <button 
          onClick={onReferenceImageClick} 
          className="shrink-0 text-blue-500 hover:text-blue-600 transition-colors"
          aria-label="View reference image"
          type="button"
        >
          <Image size={14} />
        </button>
      )}
      
      {/* If we have a prompt, show it */}
      {hasPrompt ? (
        <div className="truncate text-sm">
          {prompt}
        </div>
      ) : (
        /* If we don't have a prompt but have reference images, show a generic message */
        <div className="truncate text-sm flex items-center gap-1">
          {imageNumber && <span>Image {imageNumber}</span>}
          {workflowDisplay && <span className="text-gray-500">({workflowDisplay})</span>}
        </div>
      )}
      
      {/* Info button */}
      {onInfoClick && (
        <button
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            onInfoClick();
          }}
          className="ml-auto shrink-0 text-gray-500 hover:text-gray-700 transition-colors flex-shrink-0"
          aria-label="View image info"
          type="button"
        >
          <Info size={14} />
        </button>
      )}
    </div>
  );
};

export default ImagePrompt;
