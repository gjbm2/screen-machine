
import React, { useState } from 'react';
import { toast } from 'sonner';
import { 
  Save, 
  Share2, 
  Pencil,
  Instagram,
  Twitter,
  Facebook,
  MessageCircle,
  PinIcon,
  RefreshCw,
  Info,
  Download,
  Plus
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
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";

interface ImageActionsProps {
  imageUrl: string;
  onUseAsInput?: () => void;
  onCreateAgain?: () => void;
  generationInfo?: {
    prompt: string;
    workflow: string;
    params?: Record<string, any>;
  };
  isFullScreen?: boolean;
}

const ImageActions: React.FC<ImageActionsProps> = ({ 
  imageUrl, 
  onUseAsInput,
  onCreateAgain,
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
        return <Instagram className="h-4 w-4 mr-2" />;
      case 'twitter':
        return <Twitter className="h-4 w-4 mr-2" />;
      case 'facebook':
        return <Facebook className="h-4 w-4 mr-2" />;
      case 'pinterest':
        return <PinIcon className="h-4 w-4 mr-2" />;
      case 'message-circle':
        return <MessageCircle className="h-4 w-4 mr-2" />;
      default:
        return <Share2 className="h-4 w-4 mr-2" />;
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

  // Format workflow name for display
  const formatWorkflowName = (name: string) => {
    return name
      .split('-')
      .map(word => word.charAt(0).toUpperCase() + word.slice(1))
      .join(' ');
  };
  
  const buttonSizeClass = isFullScreen 
    ? "px-4 py-2 text-sm" 
    : "bg-white/20 hover:bg-white/40";
  
  return (
    <>
      {/* Save button */}
      <Button 
        variant="secondary" 
        size={isFullScreen ? "default" : "sm"}
        className={buttonSizeClass}
        onClick={handleSaveImage}
        disabled={isSaving}
      >
        <Download className="h-4 w-4 mr-1" />
        <span className={isFullScreen ? "" : "text-xs"}>{isSaving ? 'Saving...' : 'Save'}</span>
      </Button>
      
      {/* Publish dropdown button */}
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button 
            variant="secondary" 
            size={isFullScreen ? "default" : "sm"}
            className={buttonSizeClass}
            disabled={isPublishing}
          >
            <Share2 className="h-4 w-4 mr-1" />
            <span className={isFullScreen ? "" : "text-xs"}>{isPublishing ? 'Publishing...' : 'Publish'}</span>
          </Button>
        </DropdownMenuTrigger>
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
      </DropdownMenu>
      
      {/* Use as input button */}
      {onUseAsInput && (
        <Button 
          variant="secondary" 
          size={isFullScreen ? "default" : "sm"}
          className={buttonSizeClass}
          onClick={onUseAsInput}
        >
          <Pencil className="h-4 w-4 mr-1" />
          <span className={isFullScreen ? "" : "text-xs"}>Use as Input</span>
        </Button>
      )}

      {/* Create Again button */}
      {onCreateAgain && (
        <Button 
          variant="secondary" 
          size={isFullScreen ? "default" : "sm"}
          className={buttonSizeClass}
          onClick={onCreateAgain}
        >
          <Plus className="h-4 w-4 mr-1" />
          <span className={isFullScreen ? "" : "text-xs"}>Create Another</span>
        </Button>
      )}
    </>
  );
};

export default ImageActions;
