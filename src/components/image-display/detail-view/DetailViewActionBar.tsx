
import React from 'react';
import { Button } from '@/components/ui/button';
import { CopyPlus, SquareArrowUpRight, Trash2, Download } from 'lucide-react';
import { Separator } from '@/components/ui/separator';
import PublishMenu from '../PublishMenu';
import { useIsMobile } from '@/hooks/use-mobile';

interface DetailViewActionBarProps {
  imageUrl: string;
  onCreateAgain: () => void;
  onUseAsInput?: () => void;
  onDeleteImage: () => void;
  onClose?: () => void; // Added for closing fullscreen view
  generationInfo: {
    prompt: string;
    workflow: string;
    params?: Record<string, any>;
    referenceImageUrl?: string;
  };
}

const DetailViewActionBar: React.FC<DetailViewActionBarProps> = ({
  imageUrl,
  onCreateAgain,
  onUseAsInput,
  onDeleteImage,
  onClose,
  generationInfo
}) => {
  const isMobile = useIsMobile();
  
  const handleDownload = () => {
    const filename = imageUrl.split('/').pop() || `generated-image-${Date.now()}.png`;
    
    fetch(imageUrl)
      .then(response => response.blob())
      .then(blob => {
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.style.display = 'none';
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);
      })
      .catch(error => {
        console.error('Error downloading image:', error);
        // Fallback to opening in new tab
        window.open(imageUrl, '_blank');
      });
  };

  const handleDelete = () => {
    onDeleteImage();
    // Also close the fullscreen view if provided
    if (onClose) {
      onClose();
    }
  };

  return (
    <div className="flex justify-center py-3 bg-background/80 backdrop-blur-sm">
      <div className="flex flex-wrap gap-2 justify-center">
        {onCreateAgain && (
          <Button 
            type="button" 
            variant="outline" 
            className="bg-white/90 hover:bg-white text-black shadow-sm p-2 text-xs rounded-full flex items-center gap-1.5"
            onClick={onCreateAgain}
          >
            <CopyPlus className="h-3.5 w-3.5" /> 
            {!isMobile && <span>Go again</span>}
          </Button>
        )}
        
        {onUseAsInput && (
          <Button 
            type="button" 
            variant="outline" 
            className="bg-white/90 hover:bg-white text-black shadow-sm p-2 text-xs rounded-full flex items-center gap-1.5"
            onClick={onUseAsInput}
          >
            <SquareArrowUpRight className="h-3.5 w-3.5" /> 
            {isMobile ? <span>Input</span> : <span>Use as input</span>}
          </Button>
        )}
        
        <Button 
          type="button" 
          variant="outline" 
          className="bg-white/90 hover:bg-white text-black shadow-sm p-2 text-xs rounded-full flex items-center gap-1.5"
          onClick={handleDownload}
        >
          <Download className="h-3.5 w-3.5" /> 
          {!isMobile && <span>Download</span>}
        </Button>
        
        <PublishMenu 
          imageUrl={imageUrl}
          generationInfo={generationInfo}
          inFullscreenView={true}
        />
        
        {/* Separator */}
        <div className="h-8 flex items-center mx-1">
          <div className="h-full w-px bg-gray-300"></div>
        </div>
        
        {onDeleteImage && (
          <Button 
            type="button" 
            variant="outline" 
            className="bg-destructive/90 hover:bg-destructive text-white shadow-sm p-2 text-xs rounded-full flex items-center gap-1.5"
            onClick={handleDelete}
          >
            <Trash2 className="h-3.5 w-3.5" /> 
            {!isMobile && <span>Delete</span>}
          </Button>
        )}
      </div>
    </div>
  );
};

export default DetailViewActionBar;
