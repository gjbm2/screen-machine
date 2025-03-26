
import React from 'react';
import { Button } from '@/components/ui/button';
import { Download, CopyPlus, SquareArrowUpRight, Trash2 } from 'lucide-react';
import { ViewMode } from './ImageDisplay';
import PublishMenu from './PublishMenu';

interface ImageActionButtonsProps {
  onDeleteImage?: () => void;
  onFullScreen?: () => void;
  onUseAsInput?: () => void;
  onCreateAgain?: () => void;
  onDownload?: () => void;
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
  const shouldShow = forceShow || isHovered || viewMode === 'small';
  
  // Base styles for all buttons
  const baseButtonClass = "rounded-full shadow-md transition-all duration-200 flex items-center justify-center";
  const actionButtonSizeClass = isRolledUp ? "h-7 w-7 p-0" : "h-8 px-2.5 py-1.5 text-xs";
  const deleteButtonSizeClass = isRolledUp ? "h-7 w-7 p-0" : "h-8 w-8 p-0";
  
  // Specific button styles
  const actionButtonClass = `${baseButtonClass} ${actionButtonSizeClass} bg-white/90 hover:bg-white text-gray-800`;
  const deleteButtonClass = `${baseButtonClass} ${deleteButtonSizeClass} bg-red-500/90 hover:bg-red-600 text-white`;
  
  // Conditionally show buttons based on view mode
  if (viewMode !== 'normal' || !shouldShow) {
    return null;
  }

  return (
    <div className={`absolute bottom-0 left-0 right-0 z-20 p-2 transition-opacity duration-200 ${shouldShow ? 'opacity-100' : 'opacity-0'}`}>
      <div className="flex items-center gap-1.5 justify-center bg-black/40 backdrop-blur-sm rounded-full p-1.5">
        {onCreateAgain && (
          <Button 
            type="button"
            variant="ghost" 
            size="sm"
            onClick={onCreateAgain}
            className={actionButtonClass}
            title="Create again"
          >
            <CopyPlus className="h-3.5 w-3.5" />
            {!isRolledUp && <span className="ml-1">Go again</span>}
          </Button>
        )}
        
        {onUseAsInput && (
          <Button 
            type="button"
            variant="ghost" 
            size="sm"
            onClick={onUseAsInput}
            className={actionButtonClass}
            title="Use as input"
          >
            <SquareArrowUpRight className="h-3.5 w-3.5" />
            {!isRolledUp && <span className="ml-1">Use as input</span>}
          </Button>
        )}
        
        {onDownload && (
          <Button 
            type="button"
            variant="ghost" 
            size="sm"
            onClick={onDownload}
            className={actionButtonClass}
            title="Download"
          >
            <Download className="h-3.5 w-3.5" />
            {!isRolledUp && <span className="ml-1">Download</span>}
          </Button>
        )}
        
        {includePublish && publishInfo && (
          <PublishMenu 
            imageUrl={publishInfo.imageUrl} 
            generationInfo={publishInfo.generationInfo}
            isRolledUp={isRolledUp}
            showLabel={!isRolledUp}
          />
        )}
        
        {onDeleteImage && (
          <Button 
            type="button"
            variant="ghost" 
            size="sm"
            onClick={onDeleteImage}
            className={deleteButtonClass}
            title="Delete"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        )}
      </div>
    </div>
  );
};

export default ImageActionButtons;
