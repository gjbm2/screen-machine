import React from 'react';
import { Maximize2, Star, Film, MoreVertical, Copy, Share, Trash, ExternalLink, Sparkles } from 'lucide-react';
import { ImageItem } from '@/types/image-types';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { 
  DropdownMenu, 
  DropdownMenuContent, 
  DropdownMenuItem, 
  DropdownMenuTrigger 
} from '@/components/ui/dropdown-menu';
import { PlaceholderImage } from '@/components/recent/PlaceholderImage';

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
  /** Called when image should be used as a prompt reference */
  onUseAsPrompt?: (img: ImageItem) => void;
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
  /** Called when user clicks the fullscreen/expand icon */
  onFullscreenClick?: (img: ImageItem) => void;
}

const ImageCard: React.FC<ImageCardProps> = ({
  image,
  onClick,
  onToggleFavorite,
  onDelete,
  onCopyTo,
  onPublish,
  onUseAsPrompt,
  publishDestinations,
  className = '',
  index = 0,
  bucketId = '',
  sectionVariant,
  onFullscreenClick,
}) => {
  const style: React.CSSProperties = {
    cursor: 'grab',
  };

  // Add mobile detection
  const [isMobile, setIsMobile] = React.useState(false);
  
  // Detect mobile devices
  React.useEffect(() => {
    const checkMobile = () => {
      setIsMobile(window.innerWidth <= 768);
    };
    
    checkMobile();
    window.addEventListener('resize', checkMobile);
    return () => window.removeEventListener('resize', checkMobile);
  }, []);

  // Check if this is a placeholder image
  const isPlaceholder = React.useMemo(() => {
    return image.metadata && typeof image.metadata === 'object' && 'placeholder' in image.metadata && image.metadata.placeholder === true;
  }, [image.metadata]);

  // Get aspect ratio from metadata if available
  const aspectRatio = React.useMemo(() => {
    if (image.metadata && typeof image.metadata === 'object' && 'aspectRatio' in image.metadata) {
      return typeof image.metadata.aspectRatio === 'number'
        ? image.metadata.aspectRatio
        : 16/9;
    }
    return 16/9;
  }, [image.metadata]);

  // Direct click on mobile opens Loope immediately
  const handleClick = () => {
    if (onClick && !isPlaceholder) {
      // On mobile, always go straight to Loope view
      onClick(image);
    }
  };
  
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
    if (onClick && !isPlaceholder) {
      onClick(image);
    }
  };
  
  // Check if this is a thumbnail in the recent view
  const isRecentThumbnail = className.includes('recent-thumbnail-grid');

  return (
    <div style={style} className={`relative group ${className}`}>
      {/* Custom component, placeholder, or image thumbnail */}
      {image.customComponent ? (
        image.customComponent
      ) : isPlaceholder ? (
        <PlaceholderImage aspectRatio={aspectRatio} />
      ) : isRecentThumbnail ? (
        // Recent view thumbnails: clicking selects, expand icon for Loope
        <div className="relative w-full h-full">
          <img
            src={image.urlThumb || image.urlFull}
            alt={image.promptKey}
            className="w-full h-auto object-cover rounded-md select-none cursor-pointer"
            draggable={false}
            onClick={(e) => {
              e.stopPropagation();
              if (onClick) onClick(image);
            }}
          />
        </div>
      ) : (
        // Other bucket views: clicking opens Loope view directly
        <div 
          className="cursor-pointer" 
          onClick={(e) => {
            e.stopPropagation();
            // For non-recent images, any click opens fullscreen/Loope
            if (onFullscreenClick) {
              onFullscreenClick(image);
            } else if (onClick) {
              onClick(image);
            }
          }}
        >
          <img
            src={image.urlThumb || image.urlFull}
            alt={image.promptKey}
            className="w-full h-auto object-cover rounded-md select-none"
            draggable={false}
          />
        </div>
      )}
      
      {/* Favorite star - top right instead of top left */}
      {onToggleFavorite && sectionVariant !== 'favourites' && !isPlaceholder && (
        <Button
          variant="ghost" 
          size="icon"
          className={`absolute top-2 right-2 z-50 h-6 w-6 bg-black/30 hover:bg-black/50 text-white ${isMobile ? 'hidden' : ''}`}
          onClick={handleFavoriteClick}
        >
          {image.isFavourite ? 
            <Star className="h-4 w-4 fill-yellow-400 text-yellow-400" /> : 
            <Star className="h-4 w-4" />
          }
        </Button>
      )}
      
      {/* Video indicator - bottom left */}
      {isVideo(image) && !isPlaceholder && (
        <Badge className="absolute bottom-2 left-2 z-30 bg-black/60 text-white">
          <Film className="h-3 w-3" />
        </Badge>
      )}

      {/* Fullscreen button - bottom right */}
      {!isPlaceholder && onFullscreenClick && (
        <Button
          variant="ghost" 
          size="icon" 
          className={`absolute bottom-2 right-10 z-30 h-6 w-6 bg-black/30 hover:bg-black/50 text-white opacity-0 group-hover:opacity-100 transition-opacity ${isMobile ? 'hidden' : ''}`}
          onClick={(e) => {
            e.stopPropagation();
            onFullscreenClick(image);
          }}
        >
          <Maximize2 className="h-4 w-4" />
        </Button>
      )}

      {/* Three-dot menu - bottom right */}
      {!isPlaceholder && (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost" 
              size="icon" 
              className={`absolute bottom-2 right-2 z-40 h-6 w-6 bg-black/30 hover:bg-black/50 text-white opacity-0 group-hover:opacity-100 transition-opacity ${isMobile ? 'hidden' : ''}`}
              onClick={(e) => e.stopPropagation()}
            >
              <MoreVertical className="h-4 w-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent side="right">
            {/* Use as prompt */}
            {onUseAsPrompt && (
              <DropdownMenuItem
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  onUseAsPrompt(image);
                }}
                className="flex items-center"
              >
                <Sparkles className="h-4 w-4 mr-2" />
                Use as prompt
              </DropdownMenuItem>
            )}
            
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
                          e.preventDefault();
                          e.stopPropagation();
                          
                          try {
                            onPublish(image, dest.id);
                          } catch (error) {
                            console.error("Error publishing image:", error);
                          }
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
      )}

      {/* Hover overlay - hide in recent thumbnails only */}
      {!isPlaceholder && !isRecentThumbnail && (
        <div className={`absolute inset-0 z-10 bg-black/60 opacity-0 group-hover:opacity-100 transition-opacity flex flex-col justify-between p-3 ${isMobile ? 'hidden' : ''}`}>
          <div className="text-white text-xs break-words whitespace-pre-wrap line-clamp-3 pr-10">
            {image.promptKey || 'No prompt available'}
          </div>
          
          {/* Action bar */}
          {HoverActionBar ? <HoverActionBar image={image} /> : null}
        </div>
      )}
    </div>
  );
};

export default ImageCard; 