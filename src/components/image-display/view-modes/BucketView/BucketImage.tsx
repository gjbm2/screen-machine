import React, { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { Star, Trash2, Plus, ChevronDown, ChevronUp, ArrowUpDown, Move, ExternalLink } from 'lucide-react';
import { BucketItem } from '@/utils/api';

interface BucketImageProps {
  bucket: string;
  item: BucketItem;
  buckets: string[];
  onToggleFavorite: (bucket: string, filename: string, currentState: boolean) => Promise<void>;
  onDelete: (bucket: string, filename: string) => Promise<void>;
  onCopyTo: (sourceBucket: string, targetBucket: string, filename: string) => Promise<void>;
  onMoveUp: (bucket: string, filename: string) => Promise<void>;
  onMoveDown: (bucket: string, filename: string) => Promise<void>;
  onOpen: (item: BucketItem) => void;
  onPublish: (bucket: string, filename: string) => Promise<void>;
}

export function BucketImage({
  bucket,
  item,
  buckets,
  onToggleFavorite,
  onDelete,
  onCopyTo,
  onMoveUp,
  onMoveDown,
  onOpen,
  onPublish
}: BucketImageProps) {
  const [isHovered, setIsHovered] = useState(false);
  const [isTouched, setIsTouched] = useState(false);
  
  // For mobile touch handling
  const handleTouch = () => {
    setIsTouched(!isTouched);
  };
  
  // Determine if we should show actions based on device and interaction
  const showActions = isHovered || isTouched;
  
  // Filter out the current bucket from the copy-to list
  const availableBuckets = buckets.filter(b => b !== bucket);
  
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
        onClick={() => onOpen(item)}
      />
      
      {/* Favorite indicator */}
      {item.favorite && (
        <div className="absolute top-2 left-2 text-yellow-400">
          <Star className="h-5 w-5 fill-yellow-400" />
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
            onClick={() => onOpen(item)}
          >
            <ExternalLink className="h-4 w-4" />
            <span className="sr-only">Open</span>
          </Button>
          
          {/* Toggle favorite */}
          <Button 
            size="sm" 
            variant={item.favorite ? "default" : "outline"}
            className={`rounded-full h-8 w-8 p-0 ${item.favorite ? "bg-yellow-500 hover:bg-yellow-600" : "bg-gray-700"}`}
            onClick={() => onToggleFavorite(bucket, item.filename, item.favorite)}
          >
            <Star className={`h-4 w-4 ${item.favorite ? "fill-white" : ""}`} />
            <span className="sr-only">{item.favorite ? "Unfavorite" : "Favorite"}</span>
          </Button>
          
          {/* Move up */}
          <Button 
            size="sm" 
            variant="outline"
            className="rounded-full h-8 w-8 p-0 bg-gray-700"
            onClick={() => onMoveUp(bucket, item.filename)}
          >
            <ChevronUp className="h-4 w-4" />
            <span className="sr-only">Move up</span>
          </Button>
          
          {/* Move down */}
          <Button 
            size="sm" 
            variant="outline"
            className="rounded-full h-8 w-8 p-0 bg-gray-700"
            onClick={() => onMoveDown(bucket, item.filename)}
          >
            <ChevronDown className="h-4 w-4" />
            <span className="sr-only">Move down</span>
          </Button>
          
          {/* Add to bucket */}
          {availableBuckets.length > 0 && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button 
                  size="sm" 
                  variant="outline"
                  className="rounded-full h-8 w-8 p-0 bg-gray-700"
                >
                  <Plus className="h-4 w-4" />
                  <span className="sr-only">Add to destination</span>
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent>
                {availableBuckets.map(targetBucket => (
                  <DropdownMenuItem 
                    key={targetBucket}
                    onClick={() => onCopyTo(bucket, targetBucket, item.filename)}
                  >
                    {targetBucket}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          )}
          
          {/* Publish */}
          <Button 
            size="sm" 
            variant="default"
            className="rounded-full h-8 w-8 p-0 bg-green-600 hover:bg-green-700"
            onClick={() => onPublish(bucket, item.filename)}
          >
            <ArrowUpDown className="h-4 w-4" />
            <span className="sr-only">Publish</span>
          </Button>
          
          {/* Delete */}
          <Button 
            size="sm" 
            variant="destructive"
            className="rounded-full h-8 w-8 p-0"
            onClick={() => onDelete(bucket, item.filename)}
          >
            <Trash2 className="h-4 w-4" />
            <span className="sr-only">Delete</span>
          </Button>
        </div>
      )}
      
      {/* Mobile tap indicator */}
      <div className="absolute bottom-2 right-2 sm:hidden">
        {!isTouched && <div className="bg-black/50 text-white text-xs px-1.5 py-0.5 rounded">Tap</div>}
      </div>
    </div>
  );
}
