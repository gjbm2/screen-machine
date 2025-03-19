
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
}

const ImageActions: React.FC<ImageActionsProps> = ({ 
  imageUrl, 
  onUseAsInput,
  onCreateAgain,
  generationInfo
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

  return (
    <div className="flex justify-center gap-2 mt-4">
      {onCreateAgain && (
        <Button 
          variant="outline" 
          size="sm" 
          onClick={onCreateAgain}
        >
          <RefreshCw className="h-4 w-4 mr-2" />
          Create Again
        </Button>
      )}
      
      <Button 
        variant="outline" 
        size="sm" 
        onClick={handleSaveImage}
        disabled={isSaving}
      >
        <Save className="h-4 w-4 mr-2" />
        {isSaving ? 'Saving...' : 'Save'}
      </Button>
      
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button 
            variant="outline" 
            size="sm"
            disabled={isPublishing}
          >
            <Share2 className="h-4 w-4 mr-2" />
            {isPublishing ? 'Publishing...' : 'Publish'}
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent className="bg-background z-50">
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
      
      {onUseAsInput && (
        <Button 
          variant="outline" 
          size="sm"
          onClick={onUseAsInput}
        >
          <Pencil className="h-4 w-4 mr-2" />
          Use as Input
        </Button>
      )}

      {generationInfo && (
        <Popover>
          <PopoverTrigger asChild>
            <Button variant="outline" size="sm">
              <Info className="h-4 w-4 mr-2" />
              Info
            </Button>
          </PopoverTrigger>
          <PopoverContent className="w-80 p-4">
            <div className="space-y-2">
              <h4 className="font-medium">Generation Details</h4>
              <div className="text-sm">
                <p className="font-semibold">Prompt:</p>
                <p className="text-muted-foreground mb-2">{generationInfo.prompt || "No prompt provided"}</p>
                
                <p className="font-semibold">Workflow:</p>
                <p className="text-muted-foreground mb-2">
                  {generationInfo.workflow ? formatWorkflowName(generationInfo.workflow) : "Standard"}
                </p>
                
                {generationInfo.params && Object.keys(generationInfo.params).length > 0 && (
                  <>
                    <p className="font-semibold">Parameters:</p>
                    <div className="text-muted-foreground">
                      {Object.entries(generationInfo.params).map(([key, value]) => (
                        <div key={key} className="flex justify-between">
                          <span className="capitalize">{key.replace(/([A-Z])/g, ' $1').trim()}:</span>
                          <span>{value?.toString()}</span>
                        </div>
                      ))}
                    </div>
                  </>
                )}
              </div>
            </div>
          </PopoverContent>
        </Popover>
      )}
    </div>
  );
};

export default ImageActions;
