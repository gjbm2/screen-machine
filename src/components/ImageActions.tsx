
import React from 'react';
import { Button } from '@/components/ui/button';
import { CopyPlus, SquareArrowInUpRight, Trash2, Download } from 'lucide-react';
import { toast } from 'sonner';
import { saveAs } from 'file-saver';

interface ImageActionsProps {
  imageUrl: string;
  onCreateAgain?: () => void;
  onUseAsInput?: () => void;
  onDeleteImage?: () => void;
  generationInfo?: {
    prompt?: string;
    workflow?: string;
    params?: Record<string, any>;
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
  const handleDownload = () => {
    // Extract filename from URL or generate a timestamped name
    const filename = imageUrl.split('/').pop() || `generated-image-${Date.now()}.png`;
    
    // Try to download using fetch API
    fetch(imageUrl)
      .then(response => response.blob())
      .then(blob => {
        saveAs(blob, filename);
        toast.success('Image downloaded successfully');
      })
      .catch(error => {
        console.error('Error downloading image:', error);
        toast.error('Failed to download image');
        
        // Fallback: Open image in new tab
        window.open(imageUrl, '_blank');
      });
  };

  // Define button classes
  const baseButtonClass = "px-3 py-1.5 text-xs rounded-full flex items-center gap-1.5";
  const actionButtonClass = `${baseButtonClass} bg-white/90 hover:bg-white text-black shadow-sm`;
  const deleteButtonClass = `${baseButtonClass} bg-destructive/90 hover:bg-destructive text-white shadow-sm`;
  
  // For non-fullscreen view
  if (!isFullScreen) {
    return (
      <div className={`flex flex-wrap gap-2 justify-center ${alwaysVisible ? '' : 'opacity-0 group-hover:opacity-100 transition-opacity duration-200'}`}>
        {onCreateAgain && (
          <Button 
            type="button" 
            variant="outline" 
            className={actionButtonClass}
            onClick={onCreateAgain}
          >
            <CopyPlus className="h-3.5 w-3.5" /> Create Again
          </Button>
        )}
        
        {onUseAsInput && (
          <Button 
            type="button" 
            variant="outline" 
            className={actionButtonClass}
            onClick={onUseAsInput}
          >
            <SquareArrowInUpRight className="h-3.5 w-3.5" /> Use as Input
          </Button>
        )}
        
        <Button 
          type="button" 
          variant="outline" 
          className={actionButtonClass}
          onClick={handleDownload}
        >
          <Download className="h-3.5 w-3.5" /> Download
        </Button>
        
        {onDeleteImage && (
          <Button 
            type="button" 
            variant="outline" 
            className={deleteButtonClass}
            onClick={onDeleteImage}
          >
            <Trash2 className="h-3.5 w-3.5" /> Delete
          </Button>
        )}
      </div>
    );
  }
  
  // For fullscreen view, separate the delete button
  return (
    <>
      <div className={`flex flex-wrap gap-2 justify-center ${alwaysVisible ? '' : 'opacity-0 group-hover:opacity-100 transition-opacity duration-200'}`}>
        {onCreateAgain && (
          <Button 
            type="button" 
            variant="outline" 
            className={actionButtonClass}
            onClick={onCreateAgain}
          >
            <CopyPlus className="h-3.5 w-3.5" /> Create Again
          </Button>
        )}
        
        {onUseAsInput && (
          <Button 
            type="button" 
            variant="outline" 
            className={actionButtonClass}
            onClick={onUseAsInput}
          >
            <SquareArrowInUpRight className="h-3.5 w-3.5" /> Use as Input
          </Button>
        )}
        
        <Button 
          type="button" 
          variant="outline" 
          className={actionButtonClass}
          onClick={handleDownload}
        >
          <Download className="h-3.5 w-3.5" /> Download
        </Button>
      </div>
      
      {/* Separate delete button for fullscreen view */}
      {isFullScreen && onDeleteImage && (
        <div className="mt-4 flex justify-center">
          <Button 
            type="button" 
            variant="outline" 
            className={deleteButtonClass}
            onClick={onDeleteImage}
          >
            <Trash2 className="h-3.5 w-3.5" /> Delete
          </Button>
        </div>
      )}
    </>
  );
};

export default ImageActions;
