
import React from 'react';
import { X, Info, Image } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';

interface FullscreenHeaderProps {
  prompt: string;
  hasReferenceImages: boolean;
  onReferenceImageClick: () => void;
  workflowName?: string;
  onInfoClick: () => void;
  onClose: (e: React.MouseEvent) => void;
  imageNumber: number;
  totalImages?: number;
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
  totalImages,
  title
}) => {
  return (
    <div className="flex items-center justify-between p-3 text-sm border-b border-border bg-background/80 backdrop-blur-sm sticky top-0 z-10">
      <div className="flex flex-col max-w-[80%] min-w-0">
        {title && (
          <div className="font-semibold truncate mb-0.5">
            {title}
          </div>
        )}
        <div className="text-muted-foreground text-xs truncate">
          {prompt}
        </div>
        {workflowName && (
          <div className="text-xs text-muted-foreground mt-0.5">
            <span className="text-foreground font-medium">Model:</span> {workflowName}
            {totalImages && (
              <span className="ml-2">
                <span className="text-foreground font-medium">Image:</span> {imageNumber} of {totalImages}
              </span>
            )}
          </div>
        )}
      </div>
      
      <div className="flex items-center gap-1.5">
        {hasReferenceImages && (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button 
                variant="ghost" 
                size="icon"
                className="h-8 w-8"
                onClick={onReferenceImageClick}
              >
                <Image className="h-4 w-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>
              View reference images
            </TooltipContent>
          </Tooltip>
        )}
        
        <Tooltip>
          <TooltipTrigger asChild>
            <Button 
              variant="ghost" 
              size="icon"
              className="h-8 w-8"
              onClick={onInfoClick}
            >
              <Info className="h-4 w-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>
            Image information
          </TooltipContent>
        </Tooltip>
        
        <Button 
          variant="ghost" 
          size="icon"
          className="h-8 w-8"
          onClick={onClose}
        >
          <X className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
};

export default FullscreenHeader;
