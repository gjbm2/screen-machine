
import React from 'react';
import { Button } from '@/components/ui/button';
import { CopyPlus, SquareArrowUpRight, Trash2, Download, Info, Share } from 'lucide-react';
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

  const handleShare = () => {
    if (navigator.share) {
      navigator.share({
        title: 'Share generated image',
        text: generationInfo?.prompt || 'Check out this generated image!',
        url: imageUrl,
      })
      .then(() => toast.success('Shared successfully'))
      .catch((error) => {
        console.error('Error sharing:', error);
        // Fallback to copying URL
        handleCopyImageUrl();
      });
    } else {
      // If Web Share API is not available, copy URL to clipboard
      handleCopyImageUrl();
    }
  };

  const handleCopyImageUrl = () => {
    navigator.clipboard.writeText(imageUrl)
      .then(() => toast.success('Image URL copied to clipboard'))
      .catch(() => toast.error('Failed to copy URL'));
  };

  const handleShowInfo = () => {
    const details = [
      `Prompt: ${generationInfo?.prompt || 'Not available'}`,
      `Workflow: ${generationInfo?.workflow || 'Not available'}`
    ].join('\n');
    
    toast.info(details, {
      duration: 5000,
    });
  };

  const baseButtonClass = "p-2 text-xs rounded-full flex items-center gap-1.5";
  const actionButtonClass = `${baseButtonClass} bg-white/90 hover:bg-white text-black shadow-sm`;
  const deleteButtonClass = `${baseButtonClass} bg-destructive/90 hover:bg-destructive text-white shadow-sm`;
  
  // For non-fullscreen view (icon only)
  if (!isFullScreen) {
    return (
      <div className={`flex flex-wrap gap-1 justify-center ${alwaysVisible ? '' : 'opacity-0 group-hover:opacity-100 transition-opacity duration-200'}`}>
        <Button 
          type="button" 
          variant="outline" 
          className={actionButtonClass}
          onClick={handleShowInfo}
          title="Image Info"
        >
          <Info className="h-3.5 w-3.5" />
        </Button>
        
        {onCreateAgain && (
          <Button 
            type="button" 
            variant="outline" 
            className={actionButtonClass}
            onClick={onCreateAgain}
            title="Create Again"
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
        
        <Button 
          type="button" 
          variant="outline" 
          className={actionButtonClass}
          onClick={handleShare}
          title="Share"
        >
          <Share className="h-3.5 w-3.5" />
        </Button>
        
        {onDeleteImage && (
          <Button 
            type="button" 
            variant="outline" 
            className={`${deleteButtonClass} ml-1`}
            onClick={onDeleteImage}
            title="Delete"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        )}
      </div>
    );
  }
  
  // For fullscreen view - all buttons (including delete) in a single row
  return (
    <div className={`flex flex-wrap gap-2 justify-center ${alwaysVisible ? '' : 'opacity-0 group-hover:opacity-100 transition-opacity duration-200'}`}>
      <Button 
        type="button" 
        variant="outline" 
        className={actionButtonClass}
        onClick={handleShowInfo}
      >
        <Info className="h-3.5 w-3.5" /> Info
      </Button>
      
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
          <SquareArrowUpRight className="h-3.5 w-3.5" /> Use as Input
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
      
      <Button 
        type="button" 
        variant="outline" 
        className={actionButtonClass}
        onClick={handleShare}
      >
        <Share className="h-3.5 w-3.5" /> Share
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
};

export default ImageActions;
