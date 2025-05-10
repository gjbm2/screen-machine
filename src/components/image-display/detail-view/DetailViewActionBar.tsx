import React from 'react';
import { Button } from '@/components/ui/button';
import { CopyPlus, SquareArrowUpRight, Trash2, Download, Share, Loader2 } from 'lucide-react';
import { Separator } from '@/components/ui/separator';
import { useIsMobile } from '@/hooks/use-mobile';
import { toast } from 'sonner';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import * as LucideIcons from 'lucide-react';
import { usePublishDestinations } from '@/hooks/usePublishDestinations';
import apiService from '@/utils/api';

interface DetailViewActionBarProps {
  imageUrl: string;
  sourceType: 'bucket' | 'external';  // Added: Type of source (bucket or external URL)
  sourceBucket?: string;  // Added: Source bucket ID (required when sourceType is 'bucket')
  onCreateAgain: () => void;
  onUseAsInput?: () => void;
  onDeleteImage: () => void;
  onClose?: () => void; // Added for closing fullscreen view
  generationInfo?: {
    prompt?: string;
    workflow?: string;
    params?: Record<string, any>;
  };
}

const DetailViewActionBar: React.FC<DetailViewActionBarProps> = ({
  imageUrl,
  sourceType,
  sourceBucket,
  onCreateAgain,
  onUseAsInput,
  onDeleteImage,
  onClose,
  generationInfo
}) => {
  const isMobile = useIsMobile();
  const { destinations, loading } = usePublishDestinations();
  
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
  
  const getIconComponent = (iconName: string) => {
    const IconComponent = (LucideIcons as any)[iconName];
    return IconComponent ? <IconComponent className="h-4 w-4 mr-2" /> : <Share className="h-4 w-4 mr-2" />;
  };
  
  const handlePublish = async (destinationId: string) => {
    if (!imageUrl) return;
    
    try {
      let success;
      
      if (sourceType === 'bucket' && sourceBucket) {
        // Extract just the filename from the URL path
        const filename = imageUrl.split('/').pop()?.split('?')[0];
        
        if (!filename) {
          console.error('Could not extract filename from URL');
          toast.error('Failed to publish image: Invalid filename');
          return;
        }
        
        // Use the bucket-to-bucket publishing route
        success = await apiService.publishImageUnified({
          dest_bucket_id: destinationId,
          src_bucket_id: sourceBucket,
          filename: filename
        });
      } else {
        // External URL case (generated images)
        success = await apiService.publishImageUnified({
          dest_bucket_id: destinationId,
          source_url: imageUrl,
          metadata: generationInfo,
          skip_bucket: false
        });
      }
      
      if (success) {
        toast.success('Image published successfully');
      } else {
        toast.error('Failed to publish image');
      }
    } catch (error) {
      console.error('Error publishing image:', error);
      toast.error('Failed to publish image');
    }
  };

  const baseButtonClass = "rounded-full font-medium shadow-sm transition-all duration-200";
  const actionButtonClass = `${baseButtonClass} bg-white/90 hover:bg-white text-black`;
  const deleteButtonClass = `${baseButtonClass} bg-destructive/90 hover:bg-destructive text-white`;
  const publishButtonClass = `${baseButtonClass} bg-green-600/90 hover:bg-green-600 text-white`;

  return (
    <div className="flex justify-center py-3 bg-background/80 backdrop-blur-sm">
      <div className="flex flex-wrap gap-2 justify-center">
        {onCreateAgain && (
          <Button 
            type="button" 
            variant="outline" 
            className={`${actionButtonClass} p-2 text-xs flex items-center gap-1.5`}
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
            className={`${actionButtonClass} p-2 text-xs flex items-center gap-1.5`}
            onClick={onUseAsInput}
          >
            <SquareArrowUpRight className="h-3.5 w-3.5" /> 
            {isMobile ? <span>Input</span> : <span>Use as input</span>}
          </Button>
        )}
        
        <Button 
          type="button" 
          variant="outline" 
          className={`${actionButtonClass} p-2 text-xs flex items-center gap-1.5`}
          onClick={handleDownload}
        >
          <Download className="h-3.5 w-3.5" /> 
          {!isMobile && <span>Download</span>}
        </Button>
        
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button 
              type="button" 
              variant="outline" 
              className={`${publishButtonClass} p-2 text-xs flex items-center gap-1.5`}
              disabled={loading}
            >
              {loading ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <>
              <Share className="h-3.5 w-3.5" /> 
              <span>Publish</span>
                </>
              )}
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {destinations.map((destination) => (
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
        
        <div className="h-8 flex items-center mx-1">
          <div className="h-full w-px bg-gray-300"></div>
        </div>
        
        {onDeleteImage && (
          <Button 
            type="button" 
            variant="outline" 
            className={`${deleteButtonClass} p-2 text-xs flex items-center gap-1.5`}
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
