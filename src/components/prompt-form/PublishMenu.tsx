import React, { useState } from 'react';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Share2 } from 'lucide-react';
import { usePublishDestinations } from '@/hooks/usePublishDestinations';

interface PublishMenuProps {
  onSelect: (publishId: string) => void;
}

const PublishMenu: React.FC<PublishMenuProps> = ({ onSelect }) => {
  const { destinations } = usePublishDestinations();
  const [selectedDestinations, setSelectedDestinations] = useState<string[]>([]);

  const handleToggleDestination = (destId: string) => {
    setSelectedDestinations(prev =>
      prev.includes(destId)
        ? prev.filter(id => id !== destId)
        : [...prev, destId]
    );
  };

  // Update parent when selections change
  React.useEffect(() => {
    onSelect(selectedDestinations.length === 0 ? 'none' : selectedDestinations.join(','));
  }, [selectedDestinations, onSelect]);

  if (destinations.length === 0) {
    return <div>Loading...</div>;
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" className="flex items-center gap-2">
          <Share2 className="h-5 w-5" />
          <span>Publish</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-56">
        <div className="flex flex-col gap-2 p-2">
          {destinations.map(destination => {
            const isChecked = selectedDestinations.includes(destination.id);
            return (
              <button
                key={destination.id}
                className="flex items-center gap-2 rounded-md p-2 hover:bg-accent"
                onClick={() => handleToggleDestination(destination.id)}
              >
                <Share2 className="h-5 w-5" />
                <span>{destination.name}</span>
                {isChecked && <span className="ml-auto">✓</span>}
              </button>
            );
          })}
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
};

export default PublishMenu; 