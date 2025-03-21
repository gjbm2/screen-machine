
import React from 'react';
import { Button } from '@/components/ui/button';
import { Trash2, Maximize2 } from 'lucide-react';
import { ViewMode } from './ImageDisplay';

interface ImageActionButtonsProps {
  onDeleteImage?: (e: React.MouseEvent) => void;
  onFullScreen?: (e: React.MouseEvent) => void;
  viewMode: ViewMode;
  isVisible?: boolean;
}

const ImageActionButtons: React.FC<ImageActionButtonsProps> = ({
  onDeleteImage,
  onFullScreen,
  viewMode,
  isVisible = false
}) => {
  if (viewMode !== 'normal') return null;
  
  // Add opacity classes based on visibility
  const visibilityClass = isVisible ? 'opacity-100' : 'opacity-0 group-hover:opacity-100';
  
  return (
    <>
      {/* Delete button moved to top left */}
      {onDeleteImage && (
        <div className={`absolute top-2 left-2 ${visibilityClass} transition-opacity image-action-button`}>
          <Button 
            size="icon" 
            variant="destructive" 
            className="h-8 w-8 bg-destructive/90 border-none shadow-lg hover:bg-destructive"
            onClick={onDeleteImage}
          >
            <Trash2 className="h-5 w-5 text-white" />
          </Button>
        </div>
      )}
      
      {/* Fullscreen button remains top right */}
      {onFullScreen && (
        <div className={`absolute top-2 right-2 ${visibilityClass} transition-opacity image-action-button`}>
          <Button 
            size="icon" 
            variant="secondary" 
            className="h-8 w-8 bg-black/70 border-none shadow-lg hover:bg-black/90"
            onClick={onFullScreen}
          >
            <Maximize2 className="h-5 w-5 text-white" />
          </Button>
        </div>
      )}
    </>
  );
};

export default ImageActionButtons;
