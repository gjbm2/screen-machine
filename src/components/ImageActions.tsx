
import React, { useState } from 'react';
import { toast } from 'sonner';
import { 
  Download, 
  Share2, 
  Plus,
  Instagram,
  Twitter,
  Facebook,
  MessageCircle,
  PinIcon,
  Info
} from 'lucide-react';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Button } from '@/components/ui/button';
import publishDestinations from '@/data/publish-destinations.json';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

interface ImageActionsProps {
  imageUrl: string;
  onCreateAgain?: () => void;
  onUseAsInput?: () => void;
  generationInfo?: {
    prompt: string;
    workflow: string;
    params?: Record<string, any>;
  };
  isFullScreen?: boolean;
}

const ImageActions: React.FC<ImageActionsProps> = ({ 
  imageUrl, 
  onCreateAgain,
  onUseAsInput,
  generationInfo,
  isFullScreen = false
}) => {
  const [isSaving, setSaving] = useState(false);
  const [isPublishing, setPublishing] = useState(false);
  
  const handleSaveImage = async () => {
    try {
      setSaving(true);
      
      // Fetch the image data
      const response = await fetch(imageUrl);
      const blob = await response.blob();
      
      // Create a temporary anchor element
      const link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      
      // Set filename with date
      const date = new Date().toISOString().split('T')[0];
      link.download = `generated-image-${date}.png`;
      
      // Trigger download and clean up
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(link.href);
      
      toast.success('Image saved successfully!');
    } catch (error) {
      console.error('Error saving image:', error);
      toast.error('Failed to save image. Please try again.');
    } finally {
      setSaving(false);
    }
  };
  
  const getIconComponent = (iconName: string) => {
    switch (iconName) {
      case 'instagram':
        return <Instagram className="h-4 w-4" />;
      case 'twitter':
        return <Twitter className="h-4 w-4" />;
      case 'facebook':
        return <Facebook className="h-4 w-4" />;
      case 'pinterest':
        return <PinIcon className="h-4 w-4" />;
      case 'message-circle':
        return <MessageCircle className="h-4 w-4" />;
      default:
        return <Share2 className="h-4 w-4" />;
    }
  };

  const handlePublish = (destinationId: string) => {
    setPublishing(true);
    
    // In a real implementation, this would handle the publishing logic
    // for now, we'll just show a toast
    const destination = publishDestinations.find(d => d.id === destinationId);
    
    setTimeout(() => {
      toast.success(`Image shared to ${destination?.name || destinationId}!`);
      setPublishing(false);
    }, 1000);
  };
  
  const buttonSizeClass = isFullScreen 
    ? "px-2 py-2 text-sm" 
    : "bg-white/20 hover:bg-white/40";

  return (
    <>
      {/* Save button */}
      <TooltipProvider>
        <Tooltip delayDuration={300}>
          <TooltipTrigger asChild>
            <Button 
              variant="secondary" 
              size={isFullScreen ? "default" : "sm"}
              className={buttonSizeClass}
              onClick={handleSaveImage}
              disabled={isSaving}
            >
              <Download className="h-4 w-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="bottom">
            <p>Save image</p>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
      
      {/* Publish dropdown button */}
      <TooltipProvider>
        <Tooltip delayDuration={300}>
          <TooltipTrigger asChild>
            <DropdownMenuTrigger asChild>
              <Button 
                variant="secondary" 
                size={isFullScreen ? "default" : "sm"}
                className={buttonSizeClass}
                disabled={isPublishing}
              >
                <Share2 className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
          </TooltipTrigger>
          <TooltipContent side="bottom">
            <p>Publish/Share</p>
          </TooltipContent>
          <DropdownMenuContent align="end" className="bg-background/90 backdrop-blur-sm z-50 border">
            <DropdownMenuLabel>Share to</DropdownMenuLabel>
            <DropdownMenuSeparator />
            {publishDestinations.map((destination) => (
              <DropdownMenuItem 
                key={destination.id}
                onClick={() => handlePublish(destination.id)}
                className="cursor-pointer"
              >
                {getIconComponent(destination.icon)}
                {destination.name}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </Tooltip>
      </TooltipProvider>
      
      {/* Info button */}
      <TooltipProvider>
        <Tooltip delayDuration={300}>
          <TooltipTrigger asChild>
            <Button 
              variant="secondary" 
              size={isFullScreen ? "default" : "sm"}
              className={buttonSizeClass}
            >
              <Info className="h-4 w-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="bottom">
            <p>Image info</p>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>

      {/* Use as input button */}
      {onUseAsInput && (
        <TooltipProvider>
          <Tooltip delayDuration={300}>
            <TooltipTrigger asChild>
              <Button 
                variant="secondary" 
                size={isFullScreen ? "default" : "sm"}
                className={buttonSizeClass}
                onClick={onUseAsInput}
              >
                <Plus className="h-4 w-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="bottom">
              <p>Use as input</p>
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      )}

      {/* Create Again button */}
      {onCreateAgain && (
        <TooltipProvider>
          <Tooltip delayDuration={300}>
            <TooltipTrigger asChild>
              <Button 
                variant="secondary" 
                size={isFullScreen ? "default" : "sm"}
                className={buttonSizeClass}
                onClick={onCreateAgain}
              >
                <Plus className="h-4 w-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="bottom">
              <p>Create another</p>
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      )}
    </>
  );
};

export default ImageActions;
