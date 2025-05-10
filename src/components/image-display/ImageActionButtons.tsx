import React from 'react';
import { useIsMobile } from '@/hooks/use-mobile';
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
  onUseAsInput?: ((e: React.MouseEvent) => void) | ((url: string) => void);
  onCreateAgain?: (e: React.MouseEvent) => void;
  onDownload?: (e: React.MouseEvent) => void;
  viewMode?: ViewMode;
  forceShow?: boolean;
  isRolledUp?: boolean;
  isHovered?: boolean;
  includePublish?: boolean;
  publishInfo?: {
    imageUrl: string;
    sourceType?: 'bucket' | 'external';
    sourceBucket?: string;
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
  const isMobile = useIsMobile();
  
  // Logic for showing actions on mobile - now we show them for unrolled containers
  if (isMobile && viewMode === 'normal' && isRolledUp) {
    return null;
  }

  // Make buttons smaller for rolled-up view
  const buttonSizeClass = isRolledUp
    ? 'h-8 w-8 p-0' // Smaller, icon-only buttons for rolled-up mode
    : 'h-9 px-2 py-2 text-xs'; // Slightly smaller padding for normal mode to fit more buttons

  // Only show on hover/force for normal view
  // For mobile, we force show them when forceShow is true
  const baseVisibilityClass = (viewMode === 'small' || (isMobile && viewMode === 'normal' && !isRolledUp))
    ? 'opacity-100' 
    : 'opacity-0 group-hover:opacity-100 transition-opacity duration-100';
  
  const visibilityClass = forceShow || isHovered ? 'opacity-100' : baseVisibilityClass;

  // For normal view, show labels unless in rolled-up mode or on smaller screens
  // We use a responsive approach to hide labels on smaller screens
  const showLabels = viewMode === 'normal' && !isRolledUp;

  const handleUseAsInput = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (onUseAsInput && publishInfo?.imageUrl) {
      if (typeof onUseAsInput === 'function') {
        // Check if it accepts a URL parameter
        if (onUseAsInput.length === 1 && 
            // This condition checks if the function is the URL variant
            typeof (onUseAsInput as any).__proto__.apply === 'function') {
          // It's the URL version
          (onUseAsInput as (url: string) => void)(publishInfo.imageUrl);
        } else {
          // It's the event version
          (onUseAsInput as (e: React.MouseEvent) => void)(e);
        }
      }
    }
  };

  // New styled button classes
  const baseButtonClass = "rounded-full backdrop-blur-sm text-white shadow-sm transition-all duration-200 flex items-center justify-center";
  const actionButtonClass = `${baseButtonClass} bg-black/50 hover:bg-black/70`;
  const deleteButtonClass = `${baseButtonClass} bg-destructive/70 hover:bg-destructive`;
  const publishButtonClass = `${baseButtonClass} bg-green-600/90 hover:bg-green-600 font-medium`;

  return (
    <div className={`absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/80 to-transparent flex justify-center p-3 z-20 ${visibilityClass}`}>
      <div className="flex flex-wrap gap-1 justify-center items-center">
        {onCreateAgain && (
          <Button 
            type="button" 
            variant="ghost" 
            className={`${actionButtonClass} ${buttonSizeClass} image-action-button`}
            onClick={onCreateAgain}
            aria-label="Create Again"
          >
            <CopyPlus className="h-4 w-4" />
            {showLabels && <span className="hidden sm:inline ml-1">Create Again</span>}
          </Button>
        )}
        
        {/* Remove "Use as Input" button for normal view */}
        {/* Only show "Use as Input" button if we're NOT in normal view */}
        {onUseAsInput && viewMode !== 'normal' && (
          <Button 
            type="button" 
            variant="ghost" 
            className={`${actionButtonClass} ${buttonSizeClass} image-action-button`}
            onClick={(e) => {
              // For non-normal view, always treat as event handler
              (onUseAsInput as (e: React.MouseEvent) => void)(e);
            }}
            aria-label="Use as Input"
          >
            <SquareArrowUpRight className="h-4 w-4" />
            {showLabels && <span className="hidden sm:inline ml-1">Use as Input</span>}
          </Button>
        )}
        
        {onDownload && (
          <Button 
            type="button" 
            variant="ghost" 
            className={`${actionButtonClass} ${buttonSizeClass} image-action-button`}
            onClick={onDownload}
            aria-label="Download Image"
          >
            <Download className="h-4 w-4" />
            {showLabels && <span className="hidden sm:inline ml-1">Download</span>}
          </Button>
        )}
        
        {includePublish && publishInfo && (
          <PublishMenu 
            imageUrl={publishInfo.imageUrl}
            sourceType={publishInfo.sourceType || 'external'}
            sourceBucket={publishInfo.sourceBucket}
            generationInfo={publishInfo.generationInfo}
            isRolledUp={isRolledUp}
            showLabel={showLabels}
          />
        )}
        
        {onDeleteImage && (
          <Button
            type="button"
            variant="destructive"
            className={`${deleteButtonClass} ${buttonSizeClass} image-action-button`}
            onClick={onDeleteImage}
            aria-label="Delete image"
          >
            <Trash2 className="h-4 w-4" />
            {showLabels && <span className="hidden sm:inline ml-1">Delete</span>}
          </Button>
        )}
      </div>
    </div>
  );
};

export default ImageActionButtons;
