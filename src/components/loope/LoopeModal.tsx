import React, { useEffect, useCallback, useState, useRef } from 'react';
import ReactDOM from 'react-dom';
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog';
import { useLoopeView } from '@/contexts/LoopeViewContext';
import useEmblaCarousel from 'embla-carousel-react';
import { ChevronLeft, ChevronRight, X, ChevronUp, Star, Info, Download, Trash2, Share, Clipboard, MoreHorizontal, ExternalLink, Move, Copy, RefreshCcw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { TransformWrapper, TransformComponent } from 'react-zoom-pan-pinch';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  DropdownMenuSub,
  DropdownMenuSubTrigger,
  DropdownMenuSubContent,
  DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu";
import { toast } from 'sonner';
import apiService from '@/utils/api';
import { usePublishDestinations } from '@/hooks/usePublishDestinations';

interface LoopeModalProps {
  title?: string;
}

/**
 * Temporary stub for LoopeModal until full implementation.
 * It simply presents a minimal dialog that can be closed.
 */
const LoopeModal: React.FC<LoopeModalProps> = ({ title }) => {
  const { isOpen, images, currentIndex, goto, close } = useLoopeView();
  const [emblaRef, emblaApi] = useEmblaCarousel({ 
    loop: true
  });
  
  const [isFlipped, setIsFlipped] = useState(false);
  const [zoomSrc, setZoomSrc] = useState<string | null>(null);
  const [zoomPosition, setZoomPosition] = useState({ x: 0, y: 0 });
  const touchStartY = useRef<number | null>(null);
  const [showHint, setShowHint] = useState(true);
  
  // Get publish destinations for actions
  const { destinationsWithBuckets, loading: destinationsLoading } = usePublishDestinations();

  /* ------------------------------------------------------------------ */
  /* Sync embla selection -> context index                               */
  /* ------------------------------------------------------------------ */
  useEffect(() => {
    if (emblaApi && isOpen && !zoomSrc) {
      emblaApi.scrollTo(currentIndex, true);
    }
  }, [currentIndex, emblaApi, isOpen, zoomSrc]);

  useEffect(() => {
    if (!emblaApi) return;
    
    const onSelect = () => {
      const idx = emblaApi.selectedScrollSnap();
      if (idx !== currentIndex) goto(idx);
    };
    
    emblaApi.on('select', onSelect);
    return () => {
      emblaApi.off('select', onSelect);
    };
  }, [emblaApi, currentIndex, goto]);

  useEffect(() => {
    if (!zoomSrc && emblaApi) {
      emblaApi.reInit();
    }
  }, [zoomSrc, emblaApi]);

  /* ------------------------------------------------------------------ */
  /* Keyboard navigation                                                */
  /* ------------------------------------------------------------------ */
  const handleKey = useCallback((e: KeyboardEvent) => {
    if (!isOpen) return;
    
    if (zoomSrc && e.key === 'Escape') {
      e.preventDefault();
      setZoomSrc(null);
      return;
    }
    
    if (!zoomSrc) {
      if (e.key === 'ArrowLeft') {
        e.preventDefault();
        emblaApi?.scrollPrev();
      } else if (e.key === 'ArrowRight') {
        e.preventDefault();
        emblaApi?.scrollNext();
      } else if (e.key === 'ArrowUp' || e.key.toLowerCase() === 'i') {
        e.preventDefault();
        setIsFlipped(true);
        setShowHint(false);
      } else if (e.key === 'ArrowDown') {
        e.preventDefault();
        setIsFlipped(false);
      } else if (e.key === 'Escape') {
        close();
      }
    }
  }, [isOpen, zoomSrc, emblaApi, close]);

  useEffect(() => {
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [handleKey]);

  /* ------------------------------------------------------------------ */
  /* Touch vertical swipe for flip                                      */
  /* ------------------------------------------------------------------ */
  const handleTouchStart: React.TouchEventHandler = (e) => {
    if (zoomSrc) return;
    touchStartY.current = e.touches[0].clientY;
  };

  const handleTouchEnd: React.TouchEventHandler = (e) => {
    if (zoomSrc || touchStartY.current === null) return;
    
    const delta = e.changedTouches[0].clientY - touchStartY.current;
    if (delta < -80) {
      setIsFlipped(true);
      setShowHint(false);
    } else if (delta > 80) {
      setIsFlipped(false);
    }
    
    touchStartY.current = null;
  };

  /* ------------------------------------------------------------------ */
  /* Handle double click to zoom                                        */
  /* ------------------------------------------------------------------ */
  const handleDoubleClick = (e: React.MouseEvent, src: string) => {
    e.preventDefault();
    const rect = e.currentTarget.getBoundingClientRect();
    const x = (e.clientX - rect.left) / rect.width;
    const y = (e.clientY - rect.top) / rect.height;
    
    setZoomPosition({ x, y });
    setZoomSrc(src);
  };

  /* ------------------------------------------------------------------ */
  /* Toggle favorite                                                   */
  /* ------------------------------------------------------------------ */
  const handleToggleFavorite = async (img: any) => {
    if (!img || !img.id) return;
    
    // Get current favorite state
    const currentState = img.isFavourite;
    try {
      // Get bucket ID from img if available, or extract from URL
      let bucketId = img.bucketId;
      
      // If no bucketId directly available, try to parse from URL
      if (!bucketId && img.raw_url) {
        // Parse from URL pattern like /output/north-screen/20250512-220119-fd5cd39f.jpg
        const urlMatch = img.raw_url.match(/\/output\/([^\/]+)\/(.+)/);
        if (urlMatch) {
          bucketId = urlMatch[1];
        }
      }
      
      // If still no bucket ID, try parsing from urlThumb 
      if (!bucketId && img.urlThumb) {
        const thumbMatch = img.urlThumb.match(/\/output\/([^\/]+)\/thumbnails\/(.+)/);
        if (thumbMatch) {
          bucketId = thumbMatch[1];
        }
      }
      
      if (!bucketId) {
        console.error('Could not determine bucket ID for favorite toggle', img);
        toast.error('Could not toggle favorite: missing bucket ID');
        return;
      }
      
      console.log('Toggling favorite state:', { id: img.id, bucketId, currentState, newState: !currentState });
      
      // Call the API to toggle favorite status
      const success = await apiService.toggleFavorite(bucketId, img.id, !currentState);
      
      if (success) {
        // Update the state in context
        img.isFavourite = !currentState;
        toast.success(img.isFavourite ? 'Added to favorites' : 'Removed from favorites');
      } else {
        toast.error('Failed to toggle favorite status');
      }
    } catch (error) {
      console.error('Error toggling favorite:', error);
      toast.error('Failed to toggle favorite');
    }
  };

  /* ------------------------------------------------------------------ */
  /* Use as prompt                                                     */
  /* ------------------------------------------------------------------ */
  const handleUseAsPrompt = (img: any) => {
    if (!img) return;
    
    // Get the URL to use
    const imageUrl = img.raw_url || img.urlFull;
    if (!imageUrl) {
      toast.error('Image URL not found');
      return;
    }
    
    // Dispatch event for prompt form to handle
    const event = new CustomEvent('useImageAsPrompt', { 
      detail: { 
        url: imageUrl,
        preserveFavorites: true,
        useReferenceUrl: true,
        imageId: img.id,
        source: 'loope-view',
        append: false // Replace current reference images
      } 
    });
    
    window.dispatchEvent(event);
    toast.success('Image set as prompt reference');
    
    // Optionally close the modal
    close();
  };
  
  /* ------------------------------------------------------------------ */
  /* Copy to destination                                               */
  /* ------------------------------------------------------------------ */
  const handleCopyTo = async (img: any, destinationId: string) => {
    if (!img || !img.id) return;
    
    try {
      // Get source bucket ID
      let sourceBucketId = img.bucketId;
      
      // If no bucketId directly available, try to parse from URL
      if (!sourceBucketId && img.raw_url) {
        const urlMatch = img.raw_url.match(/\/output\/([^\/]+)\/(.+)/);
        if (urlMatch) {
          sourceBucketId = urlMatch[1];
        }
      }
      
      // If still no bucket ID, try parsing from urlThumb 
      if (!sourceBucketId && img.urlThumb) {
        const thumbMatch = img.urlThumb.match(/\/output\/([^\/]+)\/thumbnails\/(.+)/);
        if (thumbMatch) {
          sourceBucketId = thumbMatch[1];
        }
      }
      
      if (!sourceBucketId) {
        console.error('Could not determine source bucket ID for copy', img);
        toast.error('Could not copy: missing source bucket ID');
        return;
      }
      
      // Call API to copy
      await apiService.copyImageToBucket(sourceBucketId, destinationId, img.id, true);
      toast.success(`Copied to ${destinationId}`);
    } catch (error) {
      console.error('Error copying image:', error);
      toast.error('Failed to copy image');
    }
  };
  
  /* ------------------------------------------------------------------ */
  /* Move to destination                                               */
  /* ------------------------------------------------------------------ */
  const handleMoveTo = async (img: any, destinationId: string) => {
    if (!img || !img.id) return;
    
    try {
      // Get source bucket ID
      let sourceBucketId = img.bucketId;
      
      // If no bucketId directly available, try to parse from URL
      if (!sourceBucketId && img.raw_url) {
        const urlMatch = img.raw_url.match(/\/output\/([^\/]+)\/(.+)/);
        if (urlMatch) {
          sourceBucketId = urlMatch[1];
        }
      }
      
      // If still no bucket ID, try parsing from urlThumb 
      if (!sourceBucketId && img.urlThumb) {
        const thumbMatch = img.urlThumb.match(/\/output\/([^\/]+)\/thumbnails\/(.+)/);
        if (thumbMatch) {
          sourceBucketId = thumbMatch[1];
        }
      }
      
      if (!sourceBucketId) {
        console.error('Could not determine source bucket ID for move', img);
        toast.error('Could not move: missing source bucket ID');
        return;
      }
      
      // Call API to move (copy with delete)
      await apiService.copyImageToBucket(sourceBucketId, destinationId, img.id, false);
      toast.success(`Moved to ${destinationId}`);
      
      // Close modal as the current image is gone
      close();
    } catch (error) {
      console.error('Error moving image:', error);
      toast.error('Failed to move image');
    }
  };
  
  /* ------------------------------------------------------------------ */
  /* Publish to destination                                            */
  /* ------------------------------------------------------------------ */
  const handlePublish = async (img: any, destinationId: string) => {
    if (!img || !img.id) return;
    
    try {
      // Get source bucket ID
      let sourceBucketId = img.bucketId;
      
      // If no bucketId directly available, try to parse from URL
      if (!sourceBucketId && img.raw_url) {
        const urlMatch = img.raw_url.match(/\/output\/([^\/]+)\/(.+)/);
        if (urlMatch) {
          sourceBucketId = urlMatch[1];
        }
      }
      
      // If still no bucket ID, try parsing from urlThumb 
      if (!sourceBucketId && img.urlThumb) {
        const thumbMatch = img.urlThumb.match(/\/output\/([^\/]+)\/thumbnails\/(.+)/);
        if (thumbMatch) {
          sourceBucketId = thumbMatch[1];
        }
      }
      
      if (!sourceBucketId) {
        console.error('Could not determine source bucket ID for publish', img);
        toast.error('Could not publish: missing source bucket ID');
        return;
      }
      
      // Call API to publish - using correct method with proper parameters
      await apiService.publishImageUnified({
        dest_bucket_id: destinationId,
        src_bucket_id: sourceBucketId,
        filename: img.id
      });
      
      toast.success(`Published to ${destinationId}`);
    } catch (error) {
      console.error('Error publishing image:', error);
      toast.error('Failed to publish image');
    }
  };
  
  /* ------------------------------------------------------------------ */
  /* Handle delete                                                     */
  /* ------------------------------------------------------------------ */
  const handleDelete = async (img: any) => {
    if (!img || !img.id) return;
    
    if (!confirm(`Are you sure you want to delete this image${img.isFavourite ? ' (favorite)' : ''}?`)) {
      return;
    }
    
    try {
      // Get bucket ID
      let bucketId = img.bucketId;
      
      // If no bucketId directly available, try to parse from URL
      if (!bucketId && img.raw_url) {
        const urlMatch = img.raw_url.match(/\/output\/([^\/]+)\/(.+)/);
        if (urlMatch) {
          bucketId = urlMatch[1];
        }
      }
      
      // If still no bucket ID, try parsing from urlThumb 
      if (!bucketId && img.urlThumb) {
        const thumbMatch = img.urlThumb.match(/\/output\/([^\/]+)\/thumbnails\/(.+)/);
        if (thumbMatch) {
          bucketId = thumbMatch[1];
        }
      }
      
      if (!bucketId) {
        console.error('Could not determine bucket ID for delete', img);
        toast.error('Could not delete: missing bucket ID');
        return;
      }
      
      // Call API to delete
      await apiService.deleteFromBucket(bucketId, img.id);
      toast.success('Image deleted');
      
      // Close modal as the current image is gone
      close();
    } catch (error) {
      console.error('Error deleting image:', error);
      toast.error('Failed to delete image');
    }
  };

  const closeZoom = () => setZoomSrc(null);

  if (!isOpen) return null;

  const headerText = `${title || ''} ${currentIndex + 1}/${images.length}`;
  const currentImage = images[currentIndex];
  const imageSrc = currentImage?.raw_url || currentImage?.urlFull || '';
  const isVideo = currentImage?.mediaType === 'video' || /\.mp4$|\.webm$/i.test(imageSrc);

  return (
    <Dialog open={isOpen} onOpenChange={close}>
      <DialogContent className="max-w-[95vw] w-[95vw] max-h-[80vh] h-[80vh] p-0 overflow-hidden flex flex-col bg-neutral-900 text-white shadow-[0_0_40px_rgba(0,0,0,.9)] border-0 group">
        <DialogTitle className="sr-only">Image Viewer</DialogTitle>
        
        {/* Close button */}
        <Button
          variant="ghost"
          size="icon"
          className="absolute top-2 right-2 z-50 bg-black/50 hover:bg-black/70 text-white"
          onClick={close}
        >
          <X className="h-5 w-5" />
        </Button>
        
        {/* Header */}
        <div className="absolute top-2 left-4 text-sm opacity-90 pointer-events-none truncate max-w-[70%]">
          {headerText}
        </div>

        {/* Main carousel */}
        <div className="flex-1 w-full h-full overflow-hidden" ref={emblaRef} onTouchStart={handleTouchStart} onTouchEnd={handleTouchEnd}>
          <div className="flex h-full">
            {images.map((img) => {
              const src = img.raw_url || img.urlFull;
              const isImgVideo = img.mediaType === 'video' || /\.mp4$|\.webm$/i.test(src);
              
              return (
                <div 
                  key={img.id} 
                  className="flex-shrink-0 flex items-center justify-center px-4" 
                  style={{ 
                    flex: '0 0 100%'
                  }}
                >
                  {isImgVideo ? (
                    <video
                      src={src}
                      controls
                      className="max-h-full max-w-full object-contain"
                    />
                  ) : (
                    <img
                      src={src}
                      alt={img.promptKey || ''}
                      className="max-h-full max-w-full object-contain select-none"
                      onDoubleClick={(e) => handleDoubleClick(e, src)}
                    />
                  )}
                </div>
              );
            })}
          </div>
        </div>

        {/* Metadata overlay - positioned so it doesn't obscure title */}
        {isFlipped && (
          <div className="absolute top-14 bottom-14 left-4 right-4 z-40 bg-neutral-800/95 rounded overflow-hidden shadow-lg">
            <div className="h-full overflow-y-auto p-4 text-xs">
              <h3 className="font-medium text-center mb-2 pb-2 border-b border-neutral-700">Image Metadata</h3>
              <div className="overflow-y-auto max-h-[calc(100%-3rem)]">
                <table className="w-full border-collapse">
                  <tbody>
                    {currentImage && (
                      <>
                        <tr className="border-b border-neutral-700">
                          <th className="pr-2 text-right align-top py-1 font-medium text-left">ID</th>
                          <td className="pl-2 break-all">{currentImage.id}</td>
                        </tr>
                        {currentImage.promptKey && (
                          <tr className="border-b border-neutral-700">
                            <th className="pr-2 text-right align-top py-1 font-medium text-left">Prompt</th>
                            <td className="pl-2 break-all">{currentImage.promptKey}</td>
                          </tr>
                        )}
                        {/* Show all direct properties of the image */}
                        {Object.entries(currentImage || {}).map(([key, value]) => {
                          // Skip already shown fields or complex objects or functions
                          if (['id', 'promptKey', 'metadata', 'raw_url', 'urlFull', 'urlThumb'].includes(key) || 
                              typeof value === 'object' || 
                              typeof value === 'function') return null;
                          
                          return (
                            <tr key={key} className="border-b border-neutral-700">
                              <th className="pr-2 text-right align-top py-1 font-medium text-left">{key}</th>
                              <td className="pl-2 break-all">{String(value)}</td>
                            </tr>
                          );
                        })}
                        {/* Show nested metadata */}
                        {currentImage.metadata && Object.entries(currentImage.metadata).map(([key, value]) => (
                          <tr key={key} className="border-b border-neutral-700">
                            <th className="pr-2 text-right align-top py-1 font-medium text-left">{key}</th>
                            <td className="pl-2 break-all">{typeof value === 'object' ? JSON.stringify(value) : String(value)}</td>
                          </tr>
                        ))}
                      </>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
            <Button
              variant="ghost"
              size="sm"
              className="absolute top-2 right-2 z-50 bg-black/30 hover:bg-black/50 text-white"
              onClick={() => setIsFlipped(false)}
            >
              <X className="h-4 w-4" />
            </Button>
          </div>
        )}

        {/* Arrow buttons */}
        {images.length > 1 && !zoomSrc && !isFlipped && (
          <>
            <Button
              variant="ghost"
              size="icon"
              className="absolute left-0 top-1/2 -translate-y-1/2 h-20 w-12 rounded-none opacity-0 md:group-hover:opacity-70 hover:opacity-100 z-40 bg-black/30 hover:bg-black/50"
              onClick={() => emblaApi?.scrollPrev()}
            >
              <ChevronLeft className="h-6 w-6" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="absolute right-0 top-1/2 -translate-y-1/2 h-20 w-12 rounded-none opacity-0 md:group-hover:opacity-70 hover:opacity-100 z-40 bg-black/30 hover:bg-black/50"
              onClick={() => emblaApi?.scrollNext()}
            >
              <ChevronRight className="h-6 w-6" />
            </Button>
          </>
        )}

        {/* Flip hint */}
        {!isFlipped && !zoomSrc && showHint && (
          <div className="absolute bottom-16 left-1/2 -translate-x-1/2 text-white/80 flex flex-col items-center text-xs pointer-events-none md:hidden">
            <ChevronUp className="h-4 w-4 animate-bounce mb-1" />
            <span>Info</span>
          </div>
        )}

        {/* Action bar */}
        {!zoomSrc && (
          <div className="border-t border-white/10 bg-gradient-to-t from-black/70 to-transparent w-full px-4 py-2 flex flex-wrap gap-1 justify-center">
            {/* Info button */}
            <Button 
              variant="ghost" 
              size="sm" 
              className={`text-xs flex items-center gap-1 ${isFlipped ? 'bg-white/10 text-white' : 'text-white/90 hover:text-white hover:bg-white/10'}`}
              onClick={() => {
                setIsFlipped(!isFlipped);
                setShowHint(false);
              }}
            >
              <Info className="h-4 w-4" />
              <span className="hidden sm:inline">Info</span>
            </Button>
            
            {/* Favorite */}
            <Button 
              variant="ghost" 
              size="sm" 
              className="text-xs flex items-center gap-1 text-white/90 hover:text-white hover:bg-white/10"
              onClick={() => handleToggleFavorite(currentImage)}
            >
              <Star className={`h-4 w-4 ${currentImage?.isFavourite ? 'fill-yellow-400' : ''}`} />
              <span className="hidden sm:inline">Favorite</span>
            </Button>

            {/* Use as prompt - changed icon to RefreshCcw */}
            <Button 
              variant="ghost" 
              size="sm"
              className="text-xs flex items-center gap-1 text-white/90 hover:text-white hover:bg-white/10"
              onClick={() => handleUseAsPrompt(currentImage)}
            >
              <RefreshCcw className="h-4 w-4" />
              <span className="hidden sm:inline">Use as prompt</span>
            </Button>

            {/* Raw URL */}
            <Button 
              variant="ghost" 
              size="sm"
              className="text-xs flex items-center gap-1 text-white/90 hover:text-white hover:bg-white/10"
              onClick={() => {
                navigator.clipboard.writeText(imageSrc);
                toast.success('URL copied to clipboard');
              }}
            >
              <Clipboard className="h-4 w-4" />
              <span className="hidden sm:inline">Copy URL</span>
            </Button>

            {/* Download */}
            <Button 
              variant="ghost" 
              size="sm"
              className="text-xs flex items-center gap-1 text-white/90 hover:text-white hover:bg-white/10"
              onClick={() => window.open(imageSrc, '_blank')}
            >
              <Download className="h-4 w-4" />
              <span className="hidden sm:inline">Download</span>
            </Button>

            {/* More actions dropdown */}
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="sm" className="text-xs flex items-center gap-1 text-white/90 hover:text-white hover:bg-white/10">
                  <MoreHorizontal className="h-4 w-4" />
                  <span className="hidden sm:inline">More</span>
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent>
                {/* Publish to submenu */}
                {destinationsWithBuckets && destinationsWithBuckets.length > 0 && (
                  <DropdownMenuSub>
                    <DropdownMenuSubTrigger>
                      <Share className="mr-2 h-4 w-4" />
                      <span>Publish to...</span>
                    </DropdownMenuSubTrigger>
                    <DropdownMenuSubContent>
                      {destinationsWithBuckets
                        .filter(dest => !dest.headless)
                        .map(dest => (
                          <DropdownMenuItem 
                            key={`publish-${dest.id}`}
                            onClick={() => handlePublish(currentImage, dest.id)}
                          >
                            {dest.name || dest.id}
                          </DropdownMenuItem>
                        ))}
                    </DropdownMenuSubContent>
                  </DropdownMenuSub>
                )}
                
                {/* Copy to submenu */}
                {destinationsWithBuckets && destinationsWithBuckets.length > 0 && (
                  <DropdownMenuSub>
                    <DropdownMenuSubTrigger>
                      <Copy className="mr-2 h-4 w-4" />
                      <span>Copy to...</span>
                    </DropdownMenuSubTrigger>
                    <DropdownMenuSubContent>
                      {destinationsWithBuckets.map(dest => (
                        <DropdownMenuItem 
                          key={`copy-${dest.id}`}
                          onClick={() => handleCopyTo(currentImage, dest.id)}
                        >
                          {dest.name || dest.id}
                        </DropdownMenuItem>
                      ))}
                    </DropdownMenuSubContent>
                  </DropdownMenuSub>
                )}
                
                {/* Move to submenu */}
                {destinationsWithBuckets && destinationsWithBuckets.length > 0 && (
                  <DropdownMenuSub>
                    <DropdownMenuSubTrigger>
                      <Move className="mr-2 h-4 w-4" />
                      <span>Move to...</span>
                    </DropdownMenuSubTrigger>
                    <DropdownMenuSubContent>
                      {destinationsWithBuckets.map(dest => (
                        <DropdownMenuItem 
                          key={`move-${dest.id}`}
                          onClick={() => handleMoveTo(currentImage, dest.id)}
                        >
                          {dest.name || dest.id}
                        </DropdownMenuItem>
                      ))}
                    </DropdownMenuSubContent>
                  </DropdownMenuSub>
                )}
                
                <DropdownMenuSeparator />
                
                {/* Delete option */}
                <DropdownMenuItem onClick={() => handleDelete(currentImage)}>
                  <Trash2 className="mr-2 h-4 w-4" />
                  <span>Delete</span>
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        )}
      </DialogContent>

      {/* Full viewport zoom overlay */}
      {zoomSrc && ReactDOM.createPortal(
        <div 
          className="fixed inset-0 z-[1000] bg-black/95 flex items-center justify-center" 
          onClick={closeZoom}
          style={{ touchAction: 'none' }}
        >
          <TransformWrapper
            initialScale={1}
            initialPositionX={-window.innerWidth * zoomPosition.x}
            initialPositionY={-window.innerHeight * zoomPosition.y} 
            minScale={0.5}
            maxScale={5}
            doubleClick={{ disabled: true }}
            wheel={{ step: 0.1 }}
            pinch={{ step: 0 }}
          >
            <TransformComponent contentClass="select-none">
              <img src={zoomSrc} className="max-w-none" alt="" />
            </TransformComponent>
          </TransformWrapper>
          <Button
            variant="ghost"
            size="icon"
            className="absolute top-4 right-4 z-50 bg-black/70 text-white"
            onClick={closeZoom}
          >
            <X className="h-6 w-6" />
          </Button>
        </div>, 
        document.body
      )}
    </Dialog>
  );
};

export default LoopeModal; 