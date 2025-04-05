
import React, { useState, useEffect } from 'react';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuCheckboxItem,
} from '@/components/ui/dropdown-menu';
import { getPublishDestinations } from '@/services/PublishService';
import { Share2 } from 'lucide-react';
import { CircleSlash } from 'lucide-react';
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
  
  // Track selected publish destinations
  const [selectedDestinations, setSelectedDestinations] = useState<string[]>(
    selectedPublish === 'none' ? [] : selectedPublish.split(',')
  );

  // Update selected destinations when prop changes
  useEffect(() => {
    setSelectedDestinations(selectedPublish === 'none' ? [] : selectedPublish.split(','));
  }, [selectedPublish]);
  
  // Function to get icon component
  const getIconComponent = (iconName: string) => {
    const IconComponent = (LucideIcons as any)[iconName];
    return IconComponent ? <IconComponent className="h-3.5 w-3.5" /> : <Share2 className="h-3.5 w-3.5" />;
  };
  
  // Handle selecting/deselecting a publish destination
  const handleToggleDestination = (destId: string) => {
    setSelectedDestinations(prev => {
      // If it's already selected, remove it
      if (prev.includes(destId)) {
        const newSelections = prev.filter(id => id !== destId);
        // Update the parent component with joined selections or 'none'
        onPublishChange(newSelections.length > 0 ? newSelections.join(',') : 'none');
        return newSelections;
      } 
      // Otherwise add it
      else {
        const newSelections = [...prev, destId];
        // Update the parent component with joined selections
        onPublishChange(newSelections.join(','));
        return newSelections;
      }
    });
  };
  
  // Get display text for the button
  const getDisplayText = () => {
    if (selectedDestinations.length === 0) {
      return "None";
    }
    
    if (selectedDestinations.length === 1) {
      const dest = publishDestinations.find(d => d.id === selectedDestinations[0]);
      return dest ? dest.name : "None";
    }
    
    return `${selectedDestinations.length} destinations`;
  };
  
  // Get the icon to display on the button
	const getDisplayIcon = () => {
	  if (selectedDestinations.length === 0) {
		return <CircleSlash className="h-3.5 w-3.5" />;
	  }

	  if (selectedDestinations.length === 1) {
		const dest = publishDestinations.find(d => d.id === selectedDestinations[0]);
		return dest ? getIconComponent(dest.icon) : <Share2 className="h-3.5 w-3.5" />;
	  }

	  return <Share2 className="h-3.5 w-3.5" />;
	};

  // Get badge for multiple destinations
  const getMultipleDestinationsBadge = () => {
    if (selectedDestinations.length > 1) {
      return (
        <span className="ml-0.5 flex items-center justify-center bg-primary/90 text-primary-foreground text-[10px] font-bold h-4 w-4 rounded-full">
          {selectedDestinations.length}
        </span>
      );
    }
    return null;
  };
  
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
            size="sm"
            className="h-[28px] px-2 text-muted-foreground bg-white flex items-center"
            aria-label="Select publish destination"
          >
            {getDisplayIcon()}
            <span className={`ml-1.5 text-xs ${isMobile ? 'hidden' : ''}`}>
              {getDisplayText()}
            </span>
            {isMobile && getMultipleDestinationsBadge()}
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="bg-white">
          <DropdownMenuCheckboxItem
            className="pl-6 pr-2 py-1 text-xs flex items-center"
            checked={selectedDestinations.length === 0}
            onCheckedChange={() => {
              setSelectedDestinations([]);
              onPublishChange('none');
            }}
          >
            <CircleSlash className="h-3.5 w-3.5 mr-2" />
            <span>None</span>
          </DropdownMenuCheckboxItem>
          
          {publishDestinations.map(destination => (
            <DropdownMenuCheckboxItem
              key={destination.id}
              className="pl-6 pr-2 py-1 text-xs flex items-center"
              checked={selectedDestinations.includes(destination.id)}
              onCheckedChange={() => handleToggleDestination(destination.id)}
            >
              {getIconComponent(destination.icon)}
              <span className="ml-2">{destination.name}</span>
            </DropdownMenuCheckboxItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
};

export default PublishSelector;
