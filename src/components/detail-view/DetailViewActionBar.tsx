import React, { useState } from 'react';
import { Button } from '@/components/ui/button';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { Share2, MoreHorizontal } from 'lucide-react';
import { ImageItem } from '@/types/image';
import { usePublishDestinations } from '@/hooks/usePublishDestinations';

interface DetailViewActionBarProps {
  image: ImageItem;
  onClose: () => void;
}

const DetailViewActionBar: React.FC<DetailViewActionBarProps> = ({ image, onClose }) => {
  const { destinations } = usePublishDestinations();
  const [selectedDestinations, setSelectedDestinations] = useState<string[]>([]);

  const handleToggleDestination = (destId: string) => {
    setSelectedDestinations(prev =>
      prev.includes(destId)
        ? prev.filter(id => id !== destId)
        : [...prev, destId]
    );
  };

  if (destinations.length === 0) {
    return <div>Loading...</div>;
  }

  return (
    <div className="flex items-center justify-between p-4 border-t">
      <div className="flex items-center gap-2">
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
      </div>
      <Button variant="ghost" onClick={onClose}>
        Close
      </Button>
    </div>
  );
};

export default DetailViewActionBar; 