
import React from 'react';
import { Share } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
} from '@/components/ui/dropdown-menu';
import { getPublishDestinations } from '@/services/PublishService';

interface PublishMenuProps {
  imageUrl: string;
  generationInfo?: {
    prompt?: string;
    workflow?: string;
    params?: Record<string, any>;
  };
  isRolledUp?: boolean;
  showLabel?: boolean;
}

const PublishMenu: React.FC<PublishMenuProps> = ({
  imageUrl,
  generationInfo,
  isRolledUp = false,
  showLabel = true
}) => {
  const publishDestinations = getPublishDestinations();
  
  // Determine button styling based on current state
  const buttonSizeClass = isRolledUp
    ? 'h-8 w-auto p-1.5 text-xs' // Smaller buttons for rolled-up mode
    : 'h-9 px-3 py-2 text-xs'; // Regular size with labels for unrolled mode
  
  const handlePublish = (destination: string) => {
    console.log(`Publishing to ${destination}`, { imageUrl, generationInfo });
    // Actual publish logic would be implemented here
  };
  
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button 
          type="button" 
          variant="ghost" 
          className={`bg-white/20 hover:bg-white/30 text-white rounded-full ${buttonSizeClass}`}
          aria-label="Publish image"
        >
          <Share className={isRolledUp ? "h-4 w-4" : "h-4 w-4 mr-1"} />
          {showLabel && !isRolledUp && <span>Publish</span>}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-48">
        {publishDestinations.map(destination => (
          <DropdownMenuItem
            key={destination.id}
            onClick={() => handlePublish(destination.id)}
            className="flex items-center gap-2 text-xs"
          >
            <span>{destination.name}</span>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
};

export default PublishMenu;
