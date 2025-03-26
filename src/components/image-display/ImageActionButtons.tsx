import React from 'react';
import { Button } from "@/components/ui/button";
import { Trash2, Download, Plus, RefreshCw, Send, Maximize } from 'lucide-react';
import { ViewMode } from './ImageDisplay';
import PublishMenu from './PublishMenu';

interface ImageActionButtonsProps {
  onDeleteImage?: (e: React.MouseEvent) => void;
  onFullScreen?: (e: React.MouseEvent) => void;
  onUseAsInput?: (e: React.MouseEvent) => void;
  onCreateAgain?: (e: React.MouseEvent) => void;
  onDownload?: (e: React.MouseEvent) => void;
  viewMode: ViewMode;
  forceShow?: boolean;
  isRolledUp?: boolean;
  isHovered?: boolean;
  includePublish?: boolean;
  publishInfo?: {
    imageUrl: string;
    generationInfo: {
      prompt?: string;
      workflow?: string;
      params?: Record<string, any>;
    };
  };
  publishButtonColor?: 'green' | 'default';
}

const ImageActionButtons: React.FC<ImageActionButtonsProps> = ({
  onDeleteImage,
  onFullScreen,
  onUseAsInput,
  onCreateAgain,
  onDownload,
  viewMode,
  forceShow = false,
  isRolledUp = false,
  isHovered = false,
  includePublish = false,
  publishInfo,
  publishButtonColor = 'default'
}) => {
  // Only show action buttons on hover in normal mode
  const showActions = forceShow || isHovered || viewMode === 'small';
  
  // Classes for active buttons
  const buttonClass = "rounded-full p-1 transition-colors image-action-button";
  const buttonActiveClass = "bg-black/70 hover:bg-black/90 text-white";
  const buttonDisabledClass = "bg-black/40 text-gray-300 cursor-not-allowed";
  
  // Set green publish button if requested
  const publishBgColor = publishButtonColor === 'green' 
    ? "bg-green-600 hover:bg-green-700" 
    : buttonActiveClass;
  
  return (
    <div 
      className={`absolute bottom-0 left-0 right-0 p-1.5 transition-all flex justify-center gap-1 ${
        showActions ? 'opacity-100' : 'opacity-0'
      } ${viewMode === 'small' ? 'bg-black/40' : ''}`}
    >
      {/* Delete Button */}
      {onDeleteImage && (
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className={`${buttonClass} ${buttonActiveClass}`}
          onClick={onDeleteImage}
          aria-label="Delete image"
        >
          <Trash2 className="h-4 w-4" />
        </Button>
      )}
      
      {/* Download Button */}
      {onDownload && (
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className={`${buttonClass} ${buttonActiveClass}`}
          onClick={onDownload}
          aria-label="Download image"
        >
          <Download className="h-4 w-4" />
        </Button>
      )}
      
      {/* Publish Button - Now uses green color when specified */}
      {includePublish && publishInfo && (
        <PublishMenu 
          imageUrl={publishInfo.imageUrl} 
          generationInfo={publishInfo.generationInfo}
          triggerClassName={`${buttonClass} ${publishBgColor}`}
          triggerIcon={<Send className="h-4 w-4" />}
        />
      )}
      
      {/* Full Screen Button */}
      {onFullScreen && (
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className={`${buttonClass} ${buttonActiveClass}`}
          onClick={onFullScreen}
          aria-label="View fullscreen"
        >
          <Maximize className="h-4 w-4" />
        </Button>
      )}
      
      {/* Use as Input Button */}
      {onUseAsInput && (
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className={`${buttonClass} ${buttonActiveClass}`}
          onClick={onUseAsInput}
          aria-label="Use as input"
        >
          <Plus className="h-4 w-4" />
        </Button>
      )}
      
      {/* Create Again Button */}
      {onCreateAgain && (
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className={`${buttonClass} ${buttonActiveClass}`}
          onClick={onCreateAgain}
          aria-label="Create again"
        >
          <RefreshCw className="h-4 w-4" />
        </Button>
      )}
    </div>
  );
};

export default ImageActionButtons;
