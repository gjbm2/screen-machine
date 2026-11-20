import React from 'react';
import { Maximize2, Star, Film, MoreVertical, Info, Play } from 'lucide-react';
import { ImageMenuItems } from './ImageMenuItems';
import { openNextAtClip, openNextWithInfo } from '@/contexts/LoopeViewContext';
import { ImageItem } from '@/types/image-types';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { 
  DropdownMenu, 
  DropdownMenuContent, 
  DropdownMenuTrigger 
} from '@/components/ui/dropdown-menu';
import { JobPlaceholder, placeholderKeyOf } from '@/components/jobs/JobPlaceholder';

// Temporary stub until HoverActionBar is implemented
// eslint-disable-next-line @typescript-eslint/ban-ts-comment
// @ts-ignore
import HoverActionBar from './HoverActionBar';

/** A thumbnail that tries again when it fails to load (owner, 28 Oct: "a lot of snapshots now aren't loading"):
 *  thumbnails asked for while Flask restarts fail, and since the cards stopped being rebuilt every room frame
 *  nothing reloaded them. Up to 4 more tries, further apart each time; the same URL plus ?retry=n. */
const RETRY_MS = [1500, 4000, 10000, 30000];
function RetryImg({ src, ...rest }: React.ImgHTMLAttributes<HTMLImageElement>) {
  const [attempt, setAttempt] = React.useState(0);
  React.useEffect(() => setAttempt(0), [src]);
  const timer = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  React.useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  const url = !src || attempt === 0 ? src : `${src}${src.includes('?') ? '&' : '?'}retry=${attempt}`;
  return (
    <img
      {...rest}
      src={url}
      onError={(e) => {
        rest.onError?.(e);
        if (attempt < RETRY_MS.length && !timer.current) {
          timer.current = setTimeout(() => { timer.current = null; setAttempt((a) => a + 1); }, RETRY_MS[attempt]);
        }
      }}
    />
  );
}

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
  /** Called to move image to another bucket */
  onMoveTo?: (img: ImageItem, destId: string) => void;
  /** Called to publish image */
  onPublish?: (img: ImageItem, destId: string) => void;
  /** Called when image should be used as a prompt reference */
  onUseAsPrompt?: (img: ImageItem) => void;
  /** Called when image should be used to generate again */
  onGenerateAgain?: (img: ImageItem) => void;
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
  /** Show this picture's Info (the Loope's Info panel); by default where a click opens the Loope */
  onInfo?: (img: ImageItem) => void;
}

/** How far each card behind a still with clips peeks out (px). */
const STACK_STEP = 4;

const ImageCard: React.FC<ImageCardProps> = ({
  image,
  onClick,
  onToggleFavorite,
  onDelete,
  onCopyTo,
  onMoveTo,
  onPublish,
  onUseAsPrompt,
  onGenerateAgain,
  publishDestinations,
  className = '',
  index = 0,
  bucketId = '',
  sectionVariant,
  onFullscreenClick,
  onInfo,
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

  // Get aspect ratio from metadata if available. The thumbnail holds this shape until it loads ('auto' then takes the
  // picture's own), so a bucket's grid doesn't open as collapsed cells with their icons piled up (owner, 19 Nov).
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
  
  // A still with clips is drawn as a stack (the clips "behind" it) with their count on the film badge
  const clipCount = Array.isArray(image.metadata?.clips) ? image.metadata.clips.length : 0;

  // Check if this is a thumbnail in the recent view
  const isRecentThumbnail = className.includes('recent-thumbnail-grid');

  // Info: the caller's, or the Loope opened on this picture's Info where a click opens the Loope
  const info = onInfo ?? (onFullscreenClick && !isRecentThumbnail
    ? (img: ImageItem) => { openNextWithInfo(); onFullscreenClick(img); }
    : undefined);

  return (
    <div
      style={clipCount ? { ...style, paddingTop: STACK_STEP * 2, paddingRight: STACK_STEP * 2, isolation: 'isolate' } : style}
      className={`relative group ${className}`}
    >
      {/* a still with clips: the clips are cards peeking out behind it (inline styles: Generated's strip hides .absolute) */}
      {clipCount > 0 && [2, 1].map((n) => (
        <div
          key={n}
          aria-hidden
          style={{
            position: 'absolute', zIndex: -1, borderRadius: 6, top: (2 - n) * STACK_STEP, right: (2 - n) * STACK_STEP,
            bottom: n * STACK_STEP, left: n * STACK_STEP,
            background: n === 2 ? '#d6d3d1' : '#a8a29e', boxShadow: '0 0 0 1px rgba(0,0,0,0.15)',
          }}
        />
      ))}
      {/* Custom component, placeholder, or image thumbnail */}
      {image.customComponent ? (
        image.customComponent
      ) : isPlaceholder ? (
        // that image's own job progress (docs/JOBS.md), or the plain spinner until the job shows up
        <JobPlaceholder placeholder={placeholderKeyOf(image)} aspectRatio={aspectRatio} />
      ) : isRecentThumbnail ? (
        // Recent view thumbnails: clicking selects, expand icon for Loope
        <div className="relative w-full h-full">
          <RetryImg
            src={image.urlThumb || image.urlFull}
            alt={image.promptKey}
            style={{ aspectRatio: `auto ${aspectRatio}` }}
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
          <RetryImg
            src={image.urlThumb || image.urlFull}
            alt={image.promptKey}
            style={{ aspectRatio: `auto ${aspectRatio}` }}
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
      {isVideo(image) && !clipCount && !isPlaceholder && (
        <Badge className="absolute bottom-2 left-2 z-30 bg-black/60 text-white gap-1" title="A clip">
          <Film className="h-3 w-3" />
        </Badge>
      )}
      {/* a picture with clips: the badge plays them (owner, 20 Nov: "I don't see any way to discover the video") */}
      {clipCount > 0 && !isPlaceholder && onFullscreenClick && (
        <button
          type="button"
          className="absolute bottom-2 left-2 z-30 inline-flex items-center gap-1 rounded-full bg-black/70 px-2 py-1 text-xs font-medium text-white hover:bg-black/85"
          title={clipCount > 1 ? `Play its clips (${clipCount})` : 'Play its clip'}
          onClick={(e) => {
            e.stopPropagation();
            openNextAtClip();
            onFullscreenClick(image);
          }}
          onPointerDown={(e) => e.stopPropagation()}
        >
          <Play className="h-3 w-3 fill-white" />
          {clipCount > 1 ? `${clipCount} clips` : 'Clip'}
        </button>
      )}

      {/* Info button (desktop, on hover): the Loope opened on this picture's Info; on a phone, tap then swipe up.
          Not on Generated's thumbnails, whose expand selects the picture in its batch instead of opening the Loope */}
      {!isPlaceholder && info && (
        <Button
          variant="ghost"
          size="icon"
          title="Info"
          aria-label="Info"
          className={`absolute bottom-2 right-[4.5rem] z-30 h-6 w-6 bg-black/30 hover:bg-black/50 text-white opacity-0 group-hover:opacity-100 transition-opacity ${isMobile ? 'hidden' : ''}`}
          onClick={(e) => {
            e.stopPropagation();
            info(image);
          }}
        >
          <Info className="h-4 w-4" />
        </Button>
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
            <ImageMenuItems
              image={image}
              bucket={bucketId || image.bucketId}
              onInfo={info}
              onUseAsPrompt={onUseAsPrompt}
              onStartAgain={onGenerateAgain}
              onCopyTo={onCopyTo}
              onMoveTo={onMoveTo}
              onPublish={onPublish}
              publishDestinations={publishDestinations}
              onDelete={onDelete}
            />
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