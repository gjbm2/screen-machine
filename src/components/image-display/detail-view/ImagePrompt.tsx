
import React, { useState } from 'react';
import { Maximize2, Image } from 'lucide-react';
import { 
  Dialog, 
  DialogContent, 
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog';

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
  hasReferenceImages, 
  onReferenceImageClick,
  imageNumber,
  workflowName,
  onInfoClick
}) => {
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  
  // Determine what text to display
  let displayText = prompt;
  if (!displayText && (imageNumber !== undefined || workflowName)) {
    displayText = `${imageNumber !== undefined ? `Image ${imageNumber}` : ''} ${workflowName ? `- ${workflowName}` : ''}`.trim();
  }
  
  if (!displayText && !hasReferenceImages) return null;
  
  const isLongPrompt = displayText && displayText.length > 70;
  const truncatedPrompt = isLongPrompt ? `${displayText?.substring(0, 67)}...` : displayText;
  
  const handleExpandClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    // If there's an external info handler, use that instead of our dialog
    if (onInfoClick) {
      onInfoClick();
    } else {
      setIsDialogOpen(true);
    }
  };
  
  const handleReferenceClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (onReferenceImageClick) {
      onReferenceImageClick();
    }
  };
  
  return (
    <>
      <div className="text-sm text-muted-foreground flex items-center justify-between w-full overflow-hidden">
        <div className="flex items-center min-w-0 flex-grow">
          {hasReferenceImages && (
            <button 
              onClick={handleReferenceClick}
              className="flex-shrink-0 mr-2 p-1 rounded-full hover:bg-muted"
              aria-label="View reference images"
            >
              <Image size={14} />
            </button>
          )}
          {truncatedPrompt && <p className="truncate">{truncatedPrompt}</p>}
          {!truncatedPrompt && hasReferenceImages && (
            <p className="truncate">
              {imageNumber !== undefined ? `Image ${imageNumber}` : ''} 
              {workflowName ? ` - ${workflowName}` : ''}
            </p>
          )}
        </div>
        
        {isLongPrompt && (
          <button 
            onClick={handleExpandClick}
            className="flex-shrink-0 ml-2 p-1 rounded-full hover:bg-muted"
            aria-label="Expand prompt"
          >
            <Maximize2 size={14} />
          </button>
        )}
      </div>
      
      {/* Show dialog only if we're not using external info handler */}
      {!onInfoClick && (
        <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
          <DialogContent className="max-w-3xl">
            <DialogHeader>
              <DialogTitle>Prompt</DialogTitle>
            </DialogHeader>
            <div className="mt-4 text-sm whitespace-pre-wrap">
              {displayText}
            </div>
          </DialogContent>
        </Dialog>
      )}
    </>
  );
};

export default ImagePrompt;
