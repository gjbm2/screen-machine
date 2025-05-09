import React from 'react';
// DEPRECATED: This component is no longer in use. Use src/components/common/ImageCard.tsx instead.
// TODO: Remove this file and its dependencies once confirmed unused.
import { Button } from '@/components/ui/button';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { Heart, Trash2, Move, ChevronUp, ChevronDown, ExternalLink } from 'lucide-react';
import { PublishDestination } from '@/utils/api';

interface BucketImageProps {
  bucket: string;
  item: {
    filename: string;
    url: string;
    thumbnail_url: string;
    thumbnail_embedded: string;
    favorite: boolean;
    metadata: any;
  };
  buckets: PublishDestination[];
  onToggleFavorite: (currentState: boolean) => Promise<void>;
  onDelete: () => Promise<void>;
  onCopy: (targetBucketId: string) => Promise<void>;
  onMoveUp: () => Promise<void>;
  onMoveDown: () => Promise<void>;
  onOpen: () => void;
  onPublish: (destinationId: string) => Promise<void>;
}

export function BucketImage({
  bucket,
  item,
  buckets,
  onToggleFavorite,
  onDelete,
  onCopy,
  onMoveUp,
  onMoveDown,
  onOpen,
  onPublish
}: BucketImageProps) {
  const [isHovered, setIsHovered] = React.useState(false);
  const [isTouched, setIsTouched] = React.useState(false);
  
  // For mobile touch handling
  const handleTouch = () => {
    setIsTouched(!isTouched);
  };
  
  // Determine if we should show actions based on device and interaction
  const showActions = isHovered || isTouched;
  
  return (
    <div 
      className="aspect-square relative rounded-md overflow-hidden group bg-gray-100"
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      onTouchStart={handleTouch}
    >
      {/* Image - using only embedded thumbnail from get_complete_bucket */}
      <img 
        src={item.thumbnail_embedded}
        alt={item.filename}
        className="w-full h-full object-cover cursor-pointer"
        onClick={onOpen}
      />
      
      {/* Favorite indicator */}
      {item.favorite && (
        <div className="absolute top-2 left-2 text-yellow-400">
          <Heart className="h-5 w-5 fill-yellow-400" />
        </div>
      )}
      
      {/* Interactive buttons - shown on hover/touch */}
      {showActions && (
        <div className="absolute inset-0 bg-black/60 flex items-center justify-center gap-2 flex-wrap p-2 animate-in fade-in">
          {/* Preview/Open button */}
          <Button 
            size="sm" 
            variant="default"
            className="rounded-full h-8 w-8 p-0"
            onClick={onOpen}
          >
            <ExternalLink className="h-4 w-4" />
            <span className="sr-only">Open</span>
          </Button>
          
          {/* Toggle favorite */}
          <Button 
            size="sm" 
            variant={item.favorite ? "default" : "outline"}
            className={`rounded-full h-8 w-8 p-0 ${item.favorite ? "bg-yellow-500 hover:bg-yellow-600" : "bg-gray-700"}`}
            onClick={() => onToggleFavorite(item.favorite)}
          >
            <Heart className={`h-4 w-4 ${item.favorite ? "fill-white" : ""}`} />
            <span className="sr-only">{item.favorite ? "Unfavorite" : "Favorite"}</span>
          </Button>
          
          {/* Move up */}
          <Button 
            size="sm" 
            variant="outline"
            className="rounded-full h-8 w-8 p-0 bg-gray-700"
            onClick={onMoveUp}
          >
            <ChevronUp className="h-4 w-4" />
            <span className="sr-only">Move up</span>
          </Button>
          
          {/* Move down */}
          <Button 
            size="sm" 
            variant="outline"
            className="rounded-full h-8 w-8 p-0 bg-gray-700"
            onClick={onMoveDown}
          >
            <ChevronDown className="h-4 w-4" />
            <span className="sr-only">Move down</span>
          </Button>
          
          {/* Copy to dropdown */}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button 
                size="sm" 
                variant="outline"
                className="rounded-full h-8 w-8 p-0 bg-gray-700"
              >
                <Move className="h-4 w-4" />
                <span className="sr-only">Copy to</span>
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {buckets.map((dest) => (
                <DropdownMenuItem
                  key={dest.id}
                  onClick={() => onCopy(dest.id)}
                >
                  {dest.name || dest.id}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
          
          {/* Delete button */}
          <Button 
            size="sm" 
            variant="outline"
            className="rounded-full h-8 w-8 p-0 bg-red-500 hover:bg-red-600"
            onClick={onDelete}
          >
            <Trash2 className="h-4 w-4" />
            <span className="sr-only">Delete</span>
          </Button>
          
          {/* TEST: Raw URL button */}
          <Button 
            size="sm" 
            variant="outline"
            className="rounded-full h-8 w-8 p-0 bg-green-500 hover:bg-green-600"
            onClick={() => window.open(`/api/buckets/${bucket}/raw/${item.filename}`, '_blank')}
          >
            <ExternalLink className="h-4 w-4" />
            <span className="sr-only">Raw URL</span>
          </Button>
        </div>
      )}
    </div>
  );
} 