import React from 'react';
import { Maximize2, Star, Film, MoreVertical, Copy, Share, Trash, ExternalLink } from 'lucide-react';
import { ImageItem } from '@/types/image-types';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { 
  DropdownMenu, 
  DropdownMenuContent, 
  DropdownMenuItem, 
  DropdownMenuTrigger 
} from '@/components/ui/dropdown-menu';

// Temporary stub until HoverActionBar is implemented
// eslint-disable-next-line @typescript-eslint/ban-ts-comment
// @ts-ignore
import HoverActionBar from './HoverActionBar';

export interface ImageCardProps {
  image: ImageItem;
  // Dragging handled by parent useSortable; no internal sensors.
  /** Called when user clicks the main image (non-drag) */
  onClick?: (img: ImageItem) => void;
  /** Called to toggle favorite status */
  onToggleFavorite?: (img: ImageItem) => void;
  /** Called when image should be deleted */
  onDelete?: (img: ImageItem) => void;
  /** Called to copy image to other bucket */
  onCopyTo?: (img: ImageItem, destId: string) => void;
  /** Called to publish image */
  onPublish?: (img: ImageItem, destId: string) => void;
  /** List of publishable destinations */
  publishDestinations?: Array<{id: string, name: string, headless: boolean}>;
  /** Additional class names */
  className?: string;
  /** Index within its group – needed for fullscreen navigation */
  index?: number;
  /** Current bucket ID (publication destination ID) */
  bucketId?: string;
  /** The type of section this image is in ('favourites' or 'dated') */
  sectionVariant?: 'favourites' | 'dated';
}

const ImageCard: React.FC<ImageCardProps> = ({
  image,
  onClick,
  onToggleFavorite,
  onDelete,
  onCopyTo,
  onPublish,
  publishDestinations,
  className = '',
  index = 0,
  bucketId = '',
  sectionVariant,
}) => {
  const style: React.CSSProperties = {
    cursor: 'grab',
  };

  const handleClick = () => onClick?.(image);
  
  // Check if image is a video by file extension or mediaType
  const isVideo = (image: ImageItem) => {
    // First check if mediaType is explicitly set
    if (image.mediaType === 'video') {
      return true;
    }
    
    // Fallback to URL check for older data
    const videoExtensions = ['.mp4', '.avi', '.mov', '.webm', '.mkv'];
    return videoExtensions.some(ext => 
      image.urlFull.toLowerCase().endsWith(ext)
    );
  };
  
  const handleFavoriteClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (onToggleFavorite) {
      onToggleFavorite(image);
    }
  };

  const handleFullscreenClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (onClick) {
      onClick(image);
    }
  };

  return (
    <div style={style} className={`relative group ${className}`}>
      {/* Image thumbnail */}
      <img
        src={image.urlThumb || image.urlFull}
        alt={image.promptKey}
        className="w-full h-auto object-cover rounded-md select-none"
        draggable={false}
        onClick={handleClick}
      />
      
      {/* Favorite star - top left - hidden in favourites container */}
      {onToggleFavorite && sectionVariant !== 'favourites' && (
        <Button
          variant="ghost" 
          size="icon"
          className="absolute top-2 left-2 z-40 h-6 w-6 bg-black/30 hover:bg-black/50 text-white"
          onClick={handleFavoriteClick}
        >
          {image.isFavourite ? 
            <Star className="h-4 w-4 fill-yellow-400 text-yellow-400" /> : 
            <Star className="h-4 w-4" />
          }
        </Button>
      )}
      
      {/* Fullscreen button - top right */}
      {onClick && (
        <Button
          variant="ghost"
          size="icon"
          className="absolute top-2 right-2 z-40 h-6 w-6 bg-black/30 hover:bg-black/50 text-white opacity-0 group-hover:opacity-100 transition-opacity"
          onClick={handleFullscreenClick}
        >
          <Maximize2 className="h-4 w-4" />
        </Button>
      )}
      
      {/* Video indicator - bottom left */}
      {isVideo(image) && (
        <Badge className="absolute bottom-2 left-2 z-30 bg-black/60 text-white">
          <Film className="h-3 w-3" />
        </Badge>
      )}

      {/* Three-dot menu - bottom right */}
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="ghost" 
            size="icon" 
            className="absolute bottom-2 right-2 z-40 h-6 w-6 bg-black/30 hover:bg-black/50 text-white opacity-0 group-hover:opacity-100 transition-opacity"
            onClick={(e) => e.stopPropagation()}
          >
            <MoreVertical className="h-4 w-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent side="right">
          {/* Copy to submenu */}
          {onCopyTo && publishDestinations && publishDestinations.length > 0 && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <DropdownMenuItem
                  onClick={(e) => e.stopPropagation()}
                  className="flex items-center"
                >
                  <Copy className="h-4 w-4 mr-2" />
                  Copy to...
                </DropdownMenuItem>
              </DropdownMenuTrigger>
              <DropdownMenuContent side="right">
                {publishDestinations.map(dest => (
                  <DropdownMenuItem
                    key={dest.id}
                    onClick={(e) => {
                      e.stopPropagation();
                      if (onCopyTo) onCopyTo(image, dest.id);
                    }}
                  >
                    {dest.name}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          )}
          
          {/* Publish to submenu */}
          {onPublish && publishDestinations && publishDestinations.length > 0 && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <DropdownMenuItem
                  onClick={(e) => e.stopPropagation()}
                  className="flex items-center"
                >
                  <Share className="h-4 w-4 mr-2" />
                  Publish to...
                </DropdownMenuItem>
              </DropdownMenuTrigger>
              <DropdownMenuContent side="right">
                {publishDestinations
                  .filter(dest => !dest.headless)
                  .map(dest => (
                    <DropdownMenuItem
                      key={dest.id}
                      onClick={(e) => {
                        e.stopPropagation();
                        onPublish(image, dest.id);
                      }}
                    >
                      {dest.name}
                    </DropdownMenuItem>
                  ))}
              </DropdownMenuContent>
            </DropdownMenu>
          )}
          
          {/* Full Screen option */}
          {image.urlFull && (
            <DropdownMenuItem
              onClick={(e) => {
                e.stopPropagation();
                // Open the original URL in a new tab
                window.open(image.urlFull, '_blank');
              }}
            >
              <Maximize2 className="h-4 w-4 mr-2" />
              Full Screen
            </DropdownMenuItem>
          )}
          
          {/* Raw URL */}
          <DropdownMenuItem
            onClick={(e) => {
              e.stopPropagation();
              
              // Use the provided bucket ID from props
              const url = `/output/${bucketId}/${image.id}`;
              
              window.open(url, '_blank');
            }}
          >
            <ExternalLink className="h-4 w-4 mr-2" />
            Raw URL
          </DropdownMenuItem>
          
          {/* Delete option */}
          {onDelete && (
            <DropdownMenuItem
              onClick={(e) => {
                e.stopPropagation();
                if (image.isFavourite) {
                  if (!confirm('Are you sure you want to delete this favorite image?')) {
                    return;
                  }
                }
                onDelete(image);
              }}
            >
              <Trash className="h-4 w-4 mr-2" />
              Delete
            </DropdownMenuItem>
          )}
        </DropdownMenuContent>
      </DropdownMenu>

      {/* Hover overlay */}
      <div className="absolute inset-0 z-10 bg-black/60 opacity-0 group-hover:opacity-100 transition-opacity flex flex-col justify-between p-3">
        <div className="text-white text-xs break-words whitespace-pre-wrap line-clamp-3">
          {image.promptKey || 'No prompt available'}
        </div>
        
        {/* Action bar */}
        {HoverActionBar ? <HoverActionBar image={image} /> : null}
      </div>
    </div>
  );
};

export default ImageCard; 