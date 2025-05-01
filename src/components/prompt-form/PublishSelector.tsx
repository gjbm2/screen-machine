import React, { useState, useEffect } from 'react';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuPortal,
} from '@/components/ui/dropdown-menu';
import {
  HoverCard,
  HoverCardTrigger,
  HoverCardContent,
} from '@/components/ui/hover-card';
import apiService from '@/utils/api';
import { PublishDestination } from '@/utils/api';
import { Share2, ScreenShareOff } from 'lucide-react';
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
  isCompact = false,
}) => {
  const isMobile = useIsMobile();
  const [publishDestinations, setPublishDestinations] = useState<PublishDestination[]>([]);
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    const fetchDestinations = async () => {
      try {
        const destinations = await apiService.getPublishDestinations();
        setPublishDestinations(destinations);
      } catch (error) {
        console.error('Error fetching publish destinations:', error);
      }
    };

    fetchDestinations();
  }, []);

  const [selectedDestinations, setSelectedDestinations] = useState<string[]>(
    selectedPublish === 'none' ? [] : selectedPublish.split(',')
  );

  useEffect(() => {
    setSelectedDestinations(
      selectedPublish === 'none' ? [] : selectedPublish.split(',')
    );
  }, [selectedPublish]);

  const getIconComponent = (iconName: string) => {
    const IconComponent = (LucideIcons as any)[iconName];
    return IconComponent ? (
      <IconComponent className="h-5 w-5" />
    ) : (
      <Share2 className="h-5 w-5" />
    );
  };

  const handleNone = () => {
    const updated: string[] = [];
    setSelectedDestinations(updated);
    onPublishChange('none');
  };

  const handleToggleDestination = (destId: string) => {
    setSelectedDestinations(prev => {
      const isSelected = prev.includes(destId);
      const newSelections = isSelected
        ? prev.filter(id => id !== destId)
        : [...prev, destId];

      const updated = newSelections.length > 0 ? newSelections : [];
      onPublishChange(updated.length > 0 ? updated.join(',') : 'none');
      return updated;
    });
  };

  const getDisplayText = () => {
    if (selectedDestinations.length === 0) return 'Not published';
    if (selectedDestinations.length === 1) {
      const dest = publishDestinations.find(d => d.id === selectedDestinations[0]);
      return dest?.name || 'Not published';
    }
    return `${selectedDestinations.length} destinations`;
  };

  const getDisplayIcons = () => {
    if (selectedDestinations.length === 0) return <ScreenShareOff className="h-5 w-5" />;
    const icons = selectedDestinations.slice(0, 2).map(id => {
      const dest = publishDestinations.find(d => d.id === id);
      return dest ? getIconComponent(dest.icon) : null;
    }).filter(Boolean);

    return (
      <div className="flex items-center">
        {icons.map((icon, idx) => (
          <span key={idx} className="flex items-center">
            {icon}
            {idx < icons.length - 1 && (
              <span className="text-xs px-[2px]">+</span>
            )}
          </span>
        ))}
        {selectedDestinations.length > 2 && (
          <span className="text-xs px-1">...</span>
        )}
      </div>
    );
  };

  const renderDestinationList = () => (
    <div className="space-y-1">
      <div className="text-sm font-semibold mb-1 px-2">Publish to</div>

      <button
        onClick={handleNone}
        className={`w-full text-left px-4 py-1.5 text-sm flex items-center hover:bg-muted rounded-md ${
          selectedDestinations.length === 0 ? 'bg-muted font-medium' : ''
        }`}
      >
        <ScreenShareOff className="h-5 w-5 mr-2" />
        <span>Nowhere</span>
      </button>

      {publishDestinations.map(destination => {
        const isChecked = selectedDestinations.includes(destination.id);
        return (
          <button
            key={destination.id}
            onClick={() => handleToggleDestination(destination.id)}
            className={`w-full text-left px-4 py-1.5 text-sm flex items-center rounded-md transition-colors ${
              isChecked ? 'bg-muted font-medium' : 'hover:bg-muted'
            }`}
          >
            {getIconComponent(destination.icon)}
            <span className="ml-2">{destination.name}</span>
          </button>
        );
      })}
    </div>
  );

  return (
    <div className="flex items-center h-[48px]">
      {isMobile ? (
        <DropdownMenu open={menuOpen} onOpenChange={setMenuOpen} modal={false}>
          <DropdownMenuTrigger asChild>
            <Button
              variant="outline"
              className={`h-[36px] border border-input flex items-center px-3 text-sm ${
                menuOpen ? 'bg-purple-500/10 text-purple-700' : 'hover:bg-purple-500/10 text-purple-700'
              }`}
              aria-label="Select publish destination"
            >
              {getDisplayIcons()}
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuPortal>
            <DropdownMenuContent
              key={selectedDestinations.join(',')}
              align="start"
              className="bg-white w-64 p-2"
              onCloseAutoFocus={e => e.preventDefault()} // 👈 prevent auto-close on item click
            >
              {renderDestinationList()}
            </DropdownMenuContent>
          </DropdownMenuPortal>
        </DropdownMenu>
      ) : (
        <HoverCard openDelay={100} closeDelay={100}>
          <HoverCardTrigger asChild>
            <Button
              variant="outline"
              className="h-[36px] border border-input hover:bg-purple-500/10 text-purple-700 flex items-center px-3 text-sm"
              aria-label="Select publish destination"
            >
              {getDisplayIcons()}
              <span className="ml-2 text-sm truncate max-w-[150px]">
                {getDisplayText()}
              </span>
            </Button>
          </HoverCardTrigger>
          <HoverCardContent
            align="start"
            side="bottom"
            sideOffset={4}
            className="w-64 p-2 max-h-[60vh] overflow-y-auto"
          >
            {renderDestinationList()}
          </HoverCardContent>
        </HoverCard>
      )}
    </div>
  );
};

export default PublishSelector;
