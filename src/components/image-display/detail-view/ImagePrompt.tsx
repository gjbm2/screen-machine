
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
    (workflowName.length > 15 ? `${workflowName.substring(0, 15)}...` : workflowName) : 
    null;
  
  // Get the global image counter if it exists
  const globalCounter = typeof window !== 'undefined' && window.imageCounter !== undefined 
    ? window.imageCounter 
    : null;
  
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
        /* If we don't have a prompt, show a generic title with global counter and workflow */
        <div className="truncate text-sm flex items-center gap-1">
          {globalCounter !== null ? (
            <span>
              {globalCounter}. {workflowDisplay || 'Generated Image'}
            </span>
          ) : (
            /* Fallback if global counter not available */
            <span>
              {imageNumber && `Image ${imageNumber}`}
              {workflowDisplay && ` (${workflowDisplay})`}
            </span>
          )}
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
