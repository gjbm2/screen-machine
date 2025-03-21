
import React from 'react';
import { Button } from '@/components/ui/button';
import { Trash2, Maximize2 } from 'lucide-react';
import { ViewMode } from './ImageDisplay';

interface ImageActionButtonsProps {
  onDeleteImage?: (e: React.MouseEvent) => void;
  onFullScreen?: (e: React.MouseEvent) => void;
  viewMode: ViewMode;
}

const ImageActionButtons: React.FC<ImageActionButtonsProps> = ({
  onDeleteImage,
  onFullScreen,
  viewMode
}) => {
  if (viewMode !== 'normal') return null;
  
  return (
    <div className="absolute top-2 right-2 flex space-x-1 opacity-0 group-hover:opacity-100 transition-opacity image-action-button">
      {onFullScreen && (
        <Button 
          size="icon" 
          variant="secondary" 
          className="h-7 w-7 bg-black/70 border-none shadow-lg hover:bg-black/90"
          onClick={onFullScreen}
        >
          <Maximize2 className="h-4 w-4 text-white" />
        </Button>
      )}
      
      {onDeleteImage && (
        <Button 
          size="icon" 
          variant="secondary" 
          className="h-7 w-7 bg-black/70 border-none shadow-lg hover:bg-black/90"
          onClick={onDeleteImage}
        >
          <Trash2 className="h-4 w-4 text-white" />
        </Button>
      )}
    </div>
  );
};

export default ImageActionButtons;
