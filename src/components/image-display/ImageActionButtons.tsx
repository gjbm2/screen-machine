
import React from 'react';
import { ExternalLink, Trash2, Maximize } from 'lucide-react';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';

interface ImageActionButtonsProps {
  onDeleteImage?: (e: React.MouseEvent) => void;
  onFullScreen?: (e: React.MouseEvent) => void;
  showFullScreenButton?: boolean;
  viewMode: 'normal' | 'small';
}

const ImageActionButtons: React.FC<ImageActionButtonsProps> = ({
  onDeleteImage,
  onFullScreen,
  showFullScreenButton = true,
  viewMode
}) => {
  return (
    <>
      {onDeleteImage && (
        <Tooltip>
          <TooltipTrigger asChild>
            <button 
              className="absolute top-2 left-2 bg-black/70 hover:bg-black/90 rounded-full p-1 text-white transition-colors z-10"
              onClick={onDeleteImage}
            >
              <Trash2 className="h-3 w-3" />
            </button>
          </TooltipTrigger>
          <TooltipContent>Delete this image</TooltipContent>
        </Tooltip>
      )}
      
      {onFullScreen && viewMode !== 'small' && showFullScreenButton && (
        <Tooltip>
          <TooltipTrigger asChild>
            <button 
              className="absolute top-2 right-2 bg-black/70 hover:bg-black/90 rounded-full p-1.5 text-white transition-colors z-10"
              onClick={onFullScreen}
            >
              <Maximize className="h-3 w-3" />
            </button>
          </TooltipTrigger>
          <TooltipContent>Full screen</TooltipContent>
        </Tooltip>
      )}
    </>
  );
};

export default ImageActionButtons;
