
import React from 'react';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
} from '@/components/ui/dropdown-menu';
import { getPublishDestinations } from '@/services/PublishService';
import { Share, Share2 } from 'lucide-react';
import * as LucideIcons from 'lucide-react';
import { useIsMobile } from '@/hooks/use-mobile';

interface PublishSelectorProps {
  selectedPublish: string;
  onPublishChange: (publishId: string) => void;
  isCompact?: boolean;
}

const PublishSelector: React.FC<PublishSelectorProps> = ({
  selectedPublish,
  onPublishChange,
  isCompact = false
}) => {
  const publishDestinations = getPublishDestinations();
  const isMobile = useIsMobile();
  
  // Function to get icon component
  const getIconComponent = (iconName: string) => {
    const IconComponent = (LucideIcons as any)[iconName];
    return IconComponent ? <IconComponent className="h-3.5 w-3.5" /> : <Share className="h-3.5 w-3.5" />;
  };
  
  // Get the selected destination
  const selectedDestination = publishDestinations.find(dest => dest.id === selectedPublish);
  
  // Default to "None" if no publish destination is selected
  const displayIcon = selectedDestination 
    ? getIconComponent(selectedDestination.icon)
    : <Share2 className="h-3.5 w-3.5" />;
  
  const displayName = selectedDestination?.name || "None";
  
  return (
    <div className="flex items-center space-x-1">
      {!isCompact && (
        <span className="text-xs text-muted-foreground whitespace-nowrap">
          Publish:
        </span>
      )}
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="outline"
            size={isMobile ? "icon" : "sm"}
            className={`${isMobile ? "h-[28px] w-[28px]" : "h-[28px] px-2"} text-muted-foreground`}
            aria-label="Select publish destination"
          >
            {displayIcon}
            {!isMobile && <span className="ml-1.5 text-xs">{displayName}</span>}
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="bg-white">
          <DropdownMenuItem
            className="flex items-center px-2 py-1 text-xs"
            onClick={() => onPublishChange('none')}
          >
            <Share2 className="h-3.5 w-3.5 mr-2" />
            <span>None</span>
          </DropdownMenuItem>
          
          {publishDestinations.map(destination => (
            <DropdownMenuItem
              key={destination.id}
              className="flex items-center px-2 py-1 text-xs"
              onClick={() => onPublishChange(destination.id)}
            >
              {getIconComponent(destination.icon)}
              <span className="ml-2">{destination.name}</span>
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
};

export default PublishSelector;
