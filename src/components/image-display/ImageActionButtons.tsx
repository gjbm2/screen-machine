
import React from 'react';
import { 
  Trash2,
  Download,
  CopyPlus,
  SquareArrowUpRight,
  Share
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ViewMode } from './ImageDisplay';
import PublishMenu from './PublishMenu';

interface ImageActionButtonsProps {
  onDeleteImage?: (e: React.MouseEvent) => void;
  onFullScreen?: (e: React.MouseEvent) => void;
  onUseAsInput?: (e: React.MouseEvent) => void;
  onCreateAgain?: (e: React.MouseEvent) => void;
  onDownload?: (e: React.MouseEvent) => void;
  viewMode?: ViewMode;
  forceShow?: boolean;
  isRolledUp?: boolean;
  isHovered?: boolean;
  includePublish?: boolean;
  publishInfo?: {
    imageUrl: string;
    generationInfo?: {
      prompt?: string;
      workflow?: string;
      params?: Record<string, any>;
    };
  };
}

const ImageActionButtons: React.FC<ImageActionButtonsProps> = ({
  onDeleteImage,
  onFullScreen,
  onUseAsInput,
  onCreateAgain,
  onDownload,
  viewMode = 'normal',
  forceShow = false,
  isRolledUp = false,
  isHovered = false,
  includePublish = false,
  publishInfo
}) => {
  // Make buttons smaller for rolled-up view
  const buttonSizeClass = isRolledUp
    ? 'h-8 w-auto p-1.5 text-xs' // Smaller buttons for rolled-up mode without labels
    : 'h-9 px-3 py-2 text-xs'; // Regular size with labels for unrolled mode

  // Only show on hover/force for normal view
  const baseVisibilityClass = viewMode === 'small' 
    ? 'opacity-100' 
    : 'opacity-100'; // Changed from 'opacity-0 group-hover:opacity-100 transition-opacity duration-100' to always show buttons
  
  const visibilityClass = forceShow || isHovered ? 'opacity-100' : baseVisibilityClass;

  // For normal view, show labels unless in rolled-up mode
  const showLabels = viewMode === 'normal' && !isRolledUp;

  return (
    <div className={`absolute bottom-0 left-0 right-0 bg-black/80 flex justify-center p-2 z-20 ${visibilityClass}`}>
      <div className="flex gap-2 justify-center items-center">
        {onCreateAgain && (
          <Button 
            type="button" 
            variant="ghost" 
            className={`bg-white/20 hover:bg-white/30 text-white rounded-full ${buttonSizeClass} image-action-button`}
            onClick={onCreateAgain}
            aria-label="Create Again"
          >
            <CopyPlus className={isRolledUp ? "h-4 w-4" : "h-4 w-4 mr-1"} />
            {showLabels && <span>Create Again</span>}
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
            <SquareArrowUpRight className={isRolledUp ? "h-4 w-4" : "h-4 w-4 mr-1"} />
            {showLabels && <span>Use as Input</span>}
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
            <Download className={isRolledUp ? "h-4 w-4" : "h-4 w-4 mr-1"} />
            {showLabels && <span>Download</span>}
          </Button>
        )}
        
        {includePublish && publishInfo && (
          <div className="image-action-button">
            <PublishMenu 
              imageUrl={publishInfo.imageUrl}
              generationInfo={publishInfo.generationInfo}
              isRolledUp={isRolledUp}
              showLabel={showLabels}
            />
          </div>
        )}
        
        {onFullScreen && (
          <Button
            type="button"
            variant="ghost"
            className={`bg-white/20 hover:bg-white/30 text-white rounded-full ${buttonSizeClass} image-action-button`}
            onClick={onFullScreen}
            aria-label="View fullscreen"
          >
            <span className="sr-only">View fullscreen</span>
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
            <Trash2 className={isRolledUp ? "h-4 w-4" : "h-4 w-4 mr-1"} />
            {showLabels && <span>Delete</span>}
          </Button>
        )}
      </div>
    </div>
  );
};

export default ImageActionButtons;
