import React from 'react';
import { Info, ImageIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';

interface ImagePromptProps {
  prompt?: string;
  hasReferenceImages?: boolean;
  onReferenceImageClick?: () => void;
  imageNumber?: number;
  workflowName?: string;
  onInfoClick?: () => void;
  title?: string;
  useTitle?: boolean; // Flag to prioritize title over prompt
  batchIndex?: number; // Added for showing image index within batch
  batchSize?: number; // Added to determine if we should show the batch index
}

const ImagePrompt: React.FC<ImagePromptProps> = ({
  prompt,
  hasReferenceImages = false,
  onReferenceImageClick,
  imageNumber,
  workflowName,
  onInfoClick,
  title,
  useTitle = false,
  batchIndex,
  batchSize = 0
}) => {
  // Use title with high priority if useTitle flag is set
  let displayText = useTitle && title 
    ? title 
    : (title || prompt || (typeof window.imageCounter !== 'undefined' 
        ? `${window.imageCounter}. ${workflowName || 'Generated image'}`
        : workflowName || 'Generated image'));
  
  // If we have an image number and optional batch index, format the prefix
  if (imageNumber) {
    // Format like "2.1. " if batch size > 1 and we have a batch index
    // Otherwise just use "2. " format
    const prefix = batchSize > 1 && batchIndex !== undefined
      ? `${imageNumber}.${batchIndex + 1}. `  // +1 to show batch index starting from 1, not 0
      : `${imageNumber}. `;
      
    // Prepend the prefix to the display text
    displayText = prefix + displayText;
  }
  
  return (
    <div className="flex items-center gap-1 text-gray-700 min-w-0 w-full overflow-hidden">
      <span 
        className="text-sm font-medium truncate flex-1"
        title={displayText}
      >
        {displayText}
      </span>
      
      {hasReferenceImages && (
        <div className="flex items-center justify-center h-6 w-6">
          <ImageIcon className="h-3.5 w-3.5 text-blue-500" />
        </div>
      )}
      
      {onInfoClick && (
        <Tooltip>
          <TooltipTrigger asChild>
            <Button 
              type="button" 
              variant="ghost" 
              size="sm" 
              className="h-6 w-6 p-0 rounded-full" 
              onClick={(e) => {
                e.stopPropagation();
                onInfoClick();
              }}
            >
              <Info className="h-3.5 w-3.5 text-gray-500" />
              <span className="sr-only">Image info</span>
            </Button>
          </TooltipTrigger>
          <TooltipContent>
            <p>Show image info</p>
          </TooltipContent>
        </Tooltip>
      )}
    </div>
  );
};

export default ImagePrompt;
