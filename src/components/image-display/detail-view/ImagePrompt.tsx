
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
}

const ImagePrompt: React.FC<ImagePromptProps> = ({
  prompt,
  hasReferenceImages = false,
  onReferenceImageClick,
  imageNumber,
  workflowName,
  onInfoClick
}) => {
  // Log whether this component has reference images
  console.log("ImagePrompt has reference images:", hasReferenceImages);
  
  // If no prompt, use a numbered title with the workflow name
  const displayText = prompt 
    ? prompt 
    : (typeof window !== 'undefined' && typeof window.imageCounter !== 'undefined')
      ? `${window.imageCounter}. ${workflowName || 'Generated image'}`
      : workflowName || 'Generated image';
  
  return (
    <div className="flex items-center gap-1 text-gray-700 min-w-0 w-full overflow-hidden">
      <span 
        className="text-sm font-medium truncate flex-1"
        title={displayText}
      >
        {displayText}
      </span>
      
      {hasReferenceImages && onReferenceImageClick && (
        <Tooltip>
          <TooltipTrigger asChild>
            <Button 
              type="button" 
              variant="ghost" 
              size="sm" 
              className="h-6 w-6 p-0 rounded-full" 
              onClick={(e) => {
                e.stopPropagation();
                onReferenceImageClick();
              }}
            >
              <ImageIcon className="h-3.5 w-3.5 text-blue-500" />
              <span className="sr-only">View reference images</span>
            </Button>
          </TooltipTrigger>
          <TooltipContent>
            <p>View reference image</p>
          </TooltipContent>
        </Tooltip>
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
