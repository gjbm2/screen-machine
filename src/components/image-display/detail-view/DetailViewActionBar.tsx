
import React from 'react';
import { Button } from '@/components/ui/button';
import { CopyPlus, SquareArrowUpRight, Trash2, Download, Share } from 'lucide-react';
import { Separator } from '@/components/ui/separator';
import { useIsMobile } from '@/hooks/use-mobile';
import { publishImage, getPublishDestinations } from '@/services/PublishService';
import { toast } from 'sonner';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import * as LucideIcons from 'lucide-react';

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
  const publishDestinations = getPublishDestinations();
  
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
  
  // Get icon component from Lucide
  const getIconComponent = (iconName: string) => {
    const IconComponent = (LucideIcons as any)[iconName];
    return IconComponent ? <IconComponent className="h-4 w-4 mr-2" /> : <Share className="h-4 w-4 mr-2" />;
  };
  
  // Handle publish
  const handlePublish = async (destinationId: string) => {
    try {
      await publishImage(imageUrl, destinationId, generationInfo);
    } catch (error) {
      console.error('Error in publish handler:', error);
      toast.error('Failed to publish image');
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
        
        {/* Publish Button */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button 
              type="button" 
              variant="outline" 
              className="bg-white/90 hover:bg-white text-black shadow-sm p-2 text-xs rounded-full flex items-center gap-1.5"
            >
              <Share className="h-3.5 w-3.5" /> 
              {!isMobile && <span>Publish</span>}
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {publishDestinations.map((destination) => (
              <DropdownMenuItem 
                key={destination.id}
                onClick={() => handlePublish(destination.id)}
                className="flex items-center"
              >
                {getIconComponent(destination.icon)}
                <span>{destination.name}</span>
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
        
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
