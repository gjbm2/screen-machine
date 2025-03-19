
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
  PinIcon
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

interface ImageActionsProps {
  imageUrl: string;
  onUseAsInput?: () => void;
}

const ImageActions: React.FC<ImageActionsProps> = ({ imageUrl, onUseAsInput }) => {
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

  return (
    <div className="flex justify-center gap-2 mt-4">
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
    </div>
  );
};

export default ImageActions;
