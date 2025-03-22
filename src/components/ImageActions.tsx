
import React from 'react';
import { Button } from '@/components/ui/button';
import { CopyPlus, SquareArrowUpRight, Trash2, Download } from 'lucide-react';
import { toast } from 'sonner';
import { saveAs } from 'file-saver';
import PublishMenu from './image-display/PublishMenu';
import { useIsMobile } from '@/hooks/use-mobile';

interface ImageActionsProps {
  imageUrl: string;
  onCreateAgain?: () => void;
  onUseAsInput?: () => void;
  onDeleteImage?: () => void;
  generationInfo?: {
    prompt?: string;
    workflow?: string;
    params?: Record<string, any>;
    referenceImageUrl?: string;
  };
  alwaysVisible?: boolean;
  isFullScreen?: boolean;
}

const ImageActions: React.FC<ImageActionsProps> = ({ 
  imageUrl, 
  onCreateAgain, 
  onUseAsInput, 
  onDeleteImage,
  generationInfo,
  alwaysVisible = false,
  isFullScreen = false
}) => {
  const isMobile = useIsMobile();
  
  const handleDownload = () => {
    const filename = imageUrl.split('/').pop() || `generated-image-${Date.now()}.png`;
    
    fetch(imageUrl)
      .then(response => response.blob())
      .then(blob => {
        saveAs(blob, filename);
        toast.success('Image downloaded successfully');
      })
      .catch(error => {
        console.error('Error downloading image:', error);
        toast.error('Failed to download image');
        
        window.open(imageUrl, '_blank');
      });
  };

  const baseButtonClass = "p-2 text-xs rounded-full flex items-center gap-1.5";
  const actionButtonClass = `${baseButtonClass} bg-white/90 hover:bg-white text-black shadow-sm`;
  const deleteButtonClass = `${baseButtonClass} bg-destructive/90 hover:bg-destructive text-white shadow-sm`;
  
  // For non-fullscreen view (icon only)
  if (!isFullScreen) {
    return (
      <div className={`flex flex-wrap gap-1 justify-center ${alwaysVisible ? '' : 'opacity-0 group-hover:opacity-100 transition-opacity duration-200'}`}>
        {onCreateAgain && (
          <Button 
            type="button" 
            variant="outline" 
            className={actionButtonClass}
            onClick={onCreateAgain}
            title="Go again"
          >
            <CopyPlus className="h-3.5 w-3.5" />
          </Button>
        )}
        
        {onUseAsInput && (
          <Button 
            type="button" 
            variant="outline" 
            className={actionButtonClass}
            onClick={onUseAsInput}
            title="Use as Input"
          >
            <SquareArrowUpRight className="h-3.5 w-3.5" />
          </Button>
        )}
        
        <Button 
          type="button" 
          variant="outline" 
          className={actionButtonClass}
          onClick={handleDownload}
          title="Download"
        >
          <Download className="h-3.5 w-3.5" />
        </Button>
        
        <PublishMenu 
          imageUrl={imageUrl}
          generationInfo={generationInfo}
        />
        
        <div className="h-6 flex items-center mx-0.5">
          <div className="h-full w-px bg-gray-300"></div>
        </div>
        
        {onDeleteImage && (
          <Button 
            type="button" 
            variant="outline" 
            className={deleteButtonClass}
            onClick={onDeleteImage}
            title="Delete"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        )}
      </div>
    );
  }
  
  // For fullscreen view - responsive buttons
  return (
    <div className={`flex flex-wrap gap-2 justify-center ${alwaysVisible ? '' : 'opacity-0 group-hover:opacity-100 transition-opacity duration-200'}`}>
      {onCreateAgain && (
        <Button 
          type="button" 
          variant="outline" 
          className={actionButtonClass}
          onClick={onCreateAgain}
          title="Go again"
        >
          <CopyPlus className="h-3.5 w-3.5" />
          {!isMobile && <span>Go again</span>}
        </Button>
      )}
      
      {onUseAsInput && (
        <Button 
          type="button" 
          variant="outline" 
          className={actionButtonClass}
          onClick={onUseAsInput}
          title="Use as Input"
        >
          <SquareArrowUpRight className="h-3.5 w-3.5" />
          {isMobile ? <span>Input</span> : <span>Use as Input</span>}
        </Button>
      )}
      
      <Button 
        type="button" 
        variant="outline" 
        className={actionButtonClass}
        onClick={handleDownload}
        title="Download"
      >
        <Download className="h-3.5 w-3.5" />
        {!isMobile && <span>Download</span>}
      </Button>
      
      <PublishMenu 
        imageUrl={imageUrl}
        generationInfo={generationInfo}
      />
      
      <div className="h-8 flex items-center mx-1">
        <div className="h-full w-px bg-gray-300"></div>
      </div>
      
      {onDeleteImage && (
        <Button 
          type="button" 
          variant="outline" 
          className={deleteButtonClass}
          onClick={onDeleteImage}
          title="Delete"
        >
          <Trash2 className="h-3.5 w-3.5" />
          {!isMobile && <span>Delete</span>}
        </Button>
      )}
    </div>
  );
};

export default ImageActions;
