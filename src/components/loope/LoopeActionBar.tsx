import React from 'react';
import { Button } from '@/components/ui/button';
import { Star, StarOff, Trash2, Clipboard } from 'lucide-react';
import { ImageItem } from '@/types/image-types';

interface ActionBarProps {
  image: ImageItem;
  onToggleFavourite?: (img: ImageItem) => void;
  onDelete?: (img: ImageItem) => void;
  className?: string;
}

const LoopeActionBar: React.FC<ActionBarProps> = ({ image, onToggleFavourite, onDelete, className='' }) => {
  return (
    <div className={`flex gap-2 justify-center items-center w-full ${className}`}>
      <Button
        size="sm"
        variant="ghost"
        onClick={() => onToggleFavourite?.(image)}
        className="flex items-center gap-1 hover:bg-white/10"
      >
        {image.isFavourite ? <Star className="h-4 w-4 fill-yellow-400"/> : <StarOff className="h-4 w-4"/>}
        <span className="hidden md:inline-flex text-xs">Favourite</span>
      </Button>

      <Button
        size="sm"
        variant="ghost"
        onClick={() => navigator.clipboard.writeText(image.raw_url || image.urlFull)}
        className="flex items-center gap-1 hover:bg-white/10"
      >
        <Clipboard className="h-4 w-4"/>
        <span className="hidden md:inline-flex text-xs">Copy URL</span>
      </Button>

      <Button
        size="sm"
        variant="ghost"
        onClick={() => onDelete?.(image)}
        className="flex items-center gap-1 hover:bg-white/10"
      >
        <Trash2 className="h-4 w-4"/>
        <span className="hidden md:inline-flex text-xs">Delete</span>
      </Button>
    </div>
  );
};

export default LoopeActionBar; 