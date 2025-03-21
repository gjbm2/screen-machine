
import React from 'react';
import { 
  Maximize,
  Trash2
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ViewMode } from './ImageDisplay';

interface ImageActionButtonsProps {
  onDeleteImage?: (e: React.MouseEvent) => void;
  onFullScreen?: (e: React.MouseEvent) => void;
  viewMode?: ViewMode;
  forceShow?: boolean;
  isRolledUp?: boolean;
}

const ImageActionButtons: React.FC<ImageActionButtonsProps> = ({
  onDeleteImage,
  onFullScreen,
  viewMode = 'normal',
  forceShow = false,
  isRolledUp = false
}) => {
  // Make buttons smaller for rolled-up view
  const buttonSizeClass = isRolledUp
    ? 'h-6 w-6 p-0.5 text-xs' // Smaller buttons for rolled-up mode
    : 'h-8 w-8 p-1'; // Regular size for unrolled mode

  // Only show in small view and on hover/force for normal view
  const baseVisibilityClass = viewMode === 'small' 
    ? 'opacity-100' 
    : 'opacity-0 group-hover:opacity-100 transition-opacity duration-200';
  
  const visibilityClass = forceShow ? 'opacity-100' : baseVisibilityClass;

  return (
    <>
      {/* Fullscreen button - in the top right */}
      {onFullScreen && (
        <div className={`absolute top-1 right-1 z-10 ${visibilityClass}`}>
          <Button
            type="button"
            size="icon"
            variant="secondary"
            onClick={onFullScreen}
            className={`bg-black/70 hover:bg-black/90 text-white rounded-full border border-white/20 ${buttonSizeClass} image-action-button`}
            aria-label="View fullscreen"
          >
            <Maximize className={isRolledUp ? "h-3 w-3" : "h-4 w-4"} />
          </Button>
        </div>
      )}
      
      {/* Delete button - now in the bottom left, separated from other actions */}
      {onDeleteImage && (
        <div className={`absolute bottom-1 left-1 z-10 ${visibilityClass}`}>
          <Button
            type="button"
            size="icon"
            variant="destructive"
            onClick={onDeleteImage}
            className={`bg-destructive/90 hover:bg-destructive text-white rounded-full border border-white/20 ${buttonSizeClass} image-action-button`}
            aria-label="Delete image"
          >
            <Trash2 className={isRolledUp ? "h-3 w-3" : "h-4 w-4"} />
          </Button>
        </div>
      )}
    </>
  );
};

export default ImageActionButtons;
