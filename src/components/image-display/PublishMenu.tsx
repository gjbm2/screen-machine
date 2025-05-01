import React, { useState } from 'react';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Share, Share2, Loader2 } from 'lucide-react';
import * as LucideIcons from 'lucide-react';
import { toast } from 'sonner';
import apiService from '@/utils/api';
import { usePublishDestinations } from '@/hooks/usePublishDestinations';

interface PublishMenuProps {
  imageUrl: string;
  generationInfo?: {
    prompt?: string;
    workflow?: string;
    params?: Record<string, any>;
  };
  isRolledUp?: boolean;
  showLabel?: boolean;
  includePublish?: boolean;
}

const PublishMenu: React.FC<PublishMenuProps> = ({ 
  imageUrl, 
  generationInfo,
  isRolledUp = false,
  showLabel = true,
  includePublish = true
}) => {
  const { destinations, isLoading } = usePublishDestinations();
  
  // If includePublish is false, return null
  if (!includePublish) {
    return null;
  }
  
  const handlePublish = async (destinationId: string) => {
    if (!imageUrl) return;
    
    // Extract bucket and filename from the image URL
    const urlParts = imageUrl.split('/');
    const bucket = urlParts[urlParts.length - 2];
    const filename = urlParts[urlParts.length - 1];
    
    const success = await apiService.publishImage(bucket, filename, destinationId, generationInfo);
    if (success) {
      toast.success('Image published successfully');
    }
  };

  // Dynamically get icon component from Lucide
  const getIconComponent = (iconName: string) => {
    const IconComponent = (LucideIcons as any)[iconName];
    return IconComponent ? <IconComponent className="h-4 w-4 mr-2" /> : <Share className="h-4 w-4 mr-2" />;
  };

  // New styled publish button
  const buttonSizeClass = isRolledUp ? 'h-8 w-8 p-0' : 'h-9 px-2 py-2 text-xs';
  const buttonClass = `rounded-full backdrop-blur-sm text-white font-medium shadow-sm transition-all duration-200 flex items-center justify-center bg-green-600/90 hover:bg-green-600 ${buttonSizeClass} image-action-button`;

  if (isLoading) {
    return (
      <Button variant="ghost" className={buttonClass} disabled>
        <Loader2 className="h-4 w-4 animate-spin" />
      </Button>
    );
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" className={buttonClass}>
          <Share className="h-4 w-4" />
          {showLabel && !isRolledUp && <span className="ml-1">Publish</span>}
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
  );
};

export default PublishMenu;
