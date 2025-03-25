
import React from 'react';
import { 
  Maximize,
  Trash2,
  Download,
  CopyPlus,
  SquareArrowUpRight
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ViewMode } from './ImageDisplay';

interface ImageActionButtonsProps {
  onDeleteImage?: (e: React.MouseEvent) => void;
  onFullScreen?: (e: React.MouseEvent) => void;
  onUseAsInput?: (e: React.MouseEvent) => void;
  onCreateAgain?: (e: React.MouseEvent) => void;
  onDownload?: (e: React.MouseEvent) => void;
  viewMode?: ViewMode;
  forceShow?: boolean;
  isRolledUp?: boolean;
}

const ImageActionButtons: React.FC<ImageActionButtonsProps> = ({
  onDeleteImage,
  onFullScreen,
  onUseAsInput,
  onCreateAgain,
  onDownload,
  viewMode = 'normal',
  forceShow = false,
  isRolledUp = false
}) => {
  // Make buttons smaller for rolled-up view
  const buttonSizeClass = isRolledUp
    ? 'h-6 w-6 p-0.5 text-xs' // Smaller buttons for rolled-up mode
    : 'h-8 w-8 p-1'; // Regular size for unrolled mode

  // Only show on hover/force for normal view
  const baseVisibilityClass = viewMode === 'small' 
    ? 'opacity-100' 
    : 'opacity-0 group-hover:opacity-100 transition-opacity duration-100';
  
  const visibilityClass = forceShow ? 'opacity-100' : baseVisibilityClass;

  return (
    <div className={`absolute bottom-0 left-0 right-0 bg-black/80 flex justify-center p-2 z-20 ${visibilityClass}`}>
      <div className="flex gap-2 justify-center">
        {onCreateAgain && (
          <Button 
            type="button" 
            variant="ghost" 
            className={`bg-white/20 hover:bg-white/30 text-white rounded-full ${buttonSizeClass} image-action-button`}
            onClick={onCreateAgain}
            aria-label="Create Again"
          >
            <CopyPlus className={isRolledUp ? "h-3 w-3" : "h-4 w-4"} />
          </Button>
        )}
        
        {onUseAsInput && (
          <Button 
            type="button" 
            variant="ghost" 
            className={`bg-white/20 hover:bg-white/30 text-white rounded-full ${buttonSizeClass} image-action-button`}
            onClick={onUseAsInput}
            aria-label="Use as Input"
          >
            <SquareArrowUpRight className={isRolledUp ? "h-3 w-3" : "h-4 w-4"} />
          </Button>
        )}
        
        {onDownload && (
          <Button 
            type="button" 
            variant="ghost" 
            className={`bg-white/20 hover:bg-white/30 text-white rounded-full ${buttonSizeClass} image-action-button`}
            onClick={onDownload}
            aria-label="Download Image"
          >
            <Download className={isRolledUp ? "h-3 w-3" : "h-4 w-4"} />
          </Button>
        )}
        
        {onFullScreen && (
          <Button
            type="button"
            variant="ghost"
            className={`bg-white/20 hover:bg-white/30 text-white rounded-full ${buttonSizeClass} image-action-button`}
            onClick={onFullScreen}
            aria-label="View fullscreen"
          >
            <Maximize className={isRolledUp ? "h-3 w-3" : "h-4 w-4"} />
          </Button>
        )}
        
        {onDeleteImage && (
          <Button
            type="button"
            variant="destructive"
            className={`bg-destructive/90 hover:bg-destructive text-white rounded-full ${buttonSizeClass} image-action-button`}
            onClick={onDeleteImage}
            aria-label="Delete image"
          >
            <Trash2 className={isRolledUp ? "h-3 w-3" : "h-4 w-4"} />
          </Button>
        )}
      </div>
    </div>
  );
};

export default ImageActionButtons;
