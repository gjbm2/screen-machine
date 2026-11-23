import React, { useEffect, useCallback, useState, useRef, useReducer } from 'react';
import ReactDOM from 'react-dom';
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog';
import { useLoopeView } from '@/contexts/LoopeViewContext';
import useEmblaCarousel from 'embla-carousel-react';
import { ChevronLeft, ChevronRight, X, ChevronUp, Star, Info, Download, Trash2, Share, Clipboard, MoreHorizontal, ExternalLink, Move, Copy, RefreshCcw, RefreshCw, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import LoopeZoom, { type ZoomOrigin, type ZoomEntry } from './LoopeZoom';
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
import { bucketsApi, publishApi } from '@/shared/api';
import { usePublishDestinations } from '@/hooks/usePublishDestinations';
import { ReferenceImageService } from '@/services/reference-image-service';
import { TagLozenges } from '@/components/tags/TagLozenges';
import { notifyItemUpdated } from '@/components/tags/events';

interface LoopeModalProps {
  title?: string;
}

/** The bucket an image lives in: its bucketId, else parsed from /output/<bucket>/ in its URLs. */
function bucketOf(img: any): string | null {
  if (!img) return null;
  if (typeof img.bucketId === 'string' && img.bucketId) return img.bucketId;
  const m = (typeof img.raw_url === 'string' && img.raw_url.match(/\/output\/([^\/]+)\/(.+)/))
    || (typeof img.urlThumb === 'string' && img.urlThumb.match(/\/output\/([^\/]+)\/thumbnails\/(.+)/));
  return m ? m[1] : null;
}

/** Music groups the tagger scored a good fit (0.7+), best first (docs/ART_TAGS.md). */
function suitedGroups(music: Record<string, number> | undefined): string[] {
  if (!music || typeof music !== 'object') return [];
  return Object.entries(music)
    .filter(([, v]) => typeof v === 'number' && v >= 0.7)
    .sort((a, b) => b[1] - a[1])
    .map(([k]) => k);
}

/**
 * Temporary stub for LoopeModal until full implementation.
 * It simply presents a minimal dialog that can be closed.
 */
const LoopeModal: React.FC<LoopeModalProps> = ({ title }) => {
  // Add a try/catch around context usage to prevent uncaught errors
  try {
    const { isOpen, images, currentIndex, goto, close, carouselOptions } = useLoopeView();
    
    // Use a defensive check - if context isn't fully initialized, return null
    if (images === undefined || goto === undefined || close === undefined) {
      console.log("LoopeView context not fully initialized yet");
      return null;
    }
    
    const [emblaRef, emblaApi] = useEmblaCarousel({ 
      ...carouselOptions,
      loop: carouselOptions?.loop ?? true
    });
    
    const [isFlipped, setIsFlipped] = useState(false);
    const [zoom, setZoom] = useState<ZoomOrigin | null>(null);
    const zoomSrc = zoom?.src ?? null;
    const zoomRef = useRef<ZoomOrigin | null>(null);
    zoomRef.current = zoom;
    const touchStartY = useRef<number | null>(null);
    const [showHint, setShowHint] = useState(true);
    const [dynamicTitle, setDynamicTitle] = useState<string | null>(null);
    
    // Track when we're about to loop around to ensure images are still displayed
    const [hasLooped, setHasLooped] = useState(false);
    
    // Add loading state for images
    const [loadingImages, setLoadingImages] = useState<{[key: string]: boolean}>({});

    // Items are the opener's objects, patched in place (star, tags); this re-renders after a patch.
    const [, bumpItems] = useReducer((n: number) => n + 1, 0);
    
    // Get publish destinations for actions
    const { destinations, nonHeadlessDestinations, loading: destinationsLoading } = usePublishDestinations();

    // Add new state for mobile detection
    const [isMobile, setIsMobile] = useState(false);
    
    // Detect mobile devices
    useEffect(() => {
      const checkMobile = () => {
        setIsMobile(window.innerWidth <= 768);
      };
      
      checkMobile();
      window.addEventListener('resize', checkMobile);
      return () => window.removeEventListener('resize', checkMobile);
    }, []);

    // Log destination information without hardcoding specific IDs
    useEffect(() => {
      // Removed verbose logging to reduce console noise
    }, [destinations, nonHeadlessDestinations]);

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
        
        // Check if we've looped back to the start
        const wasLastSlide = currentIndex === images.length - 1;
        const isFirstSlide = idx === 0;
        
        // If we detect a wrap-around, force a re-init of the carousel
        if (wasLastSlide && isFirstSlide) {
          setHasLooped(true);
          // Using a microtask to ensure state updates first
          setTimeout(() => {
            if (emblaApi) {
              emblaApi.reInit();
            }
          }, 0);
        }
        
        if (idx !== currentIndex) goto(idx);
      };
      
      emblaApi.on('select', onSelect);
      return () => {
        emblaApi.off('select', onSelect);
      };
    }, [emblaApi, currentIndex, goto, images.length]);

    // Ensure carousel is re-initialized when images change or when looping occurs
    useEffect(() => {
      if (!emblaApi) return;
      
      if (hasLooped) {
        emblaApi.reInit();
        setHasLooped(false);
      }
    }, [emblaApi, hasLooped]);

    useEffect(() => {
      if (!zoomSrc && emblaApi) {
        emblaApi.reInit();
      }
    }, [zoomSrc, emblaApi, images]);

    /* ------------------------------------------------------------------ */
    /* Keyboard navigation                                                */
    /* ------------------------------------------------------------------ */
    const handleKey = useCallback((e: KeyboardEvent) => {
      if (!isOpen) return;
      // Typing in a field (the tag search) is not navigation: arrows, "i" and Escape belong to it.
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
      
      if (zoomSrc && e.key === 'Escape') {
        e.preventDefault();
        setZoom(null);
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
      // A second finger makes it a pinch, not a flip.
      if (zoomSrc || e.touches.length > 1) { touchStartY.current = null; return; }
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
        const success = await bucketsApi.toggleFavorite(bucketId, img.id, !currentState);
        
        if (success) {
          // Update the state in context
          img.isFavourite = !currentState;
          bumpItems();
          // The grid holds its own copy: tell it, so it isn't stale until a refresh.
          notifyItemUpdated({ bucket: bucketId, filename: img.id, patch: { favorite: img.isFavourite } });
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
        await bucketsApi.copyImageToBucket(sourceBucketId, destinationId, img.id, true);
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
        await bucketsApi.copyImageToBucket(sourceBucketId, destinationId, img.id, false);
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
        const result = await publishApi.publishImageUnified({
          dest_bucket_id: destinationId,
          src_bucket_id: sourceBucketId,
          filename: img.id
        });
        
        if (result.success) toast.success(`Published to ${destinationId}`);
        else if (!result.cancelled) toast.error(`Failed to publish image: ${result.error ?? ''}`);
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
        await bucketsApi.deleteFromBucket(bucketId, img.id);
        toast.success('Image deleted');
        
        // Close modal as the current image is gone
        close();
      } catch (error) {
        console.error('Error deleting image:', error);
        toast.error('Failed to delete image');
      }
    };

    const handleStartAgain = (img: any) => {
      if (!img) return;
      
      try {
        // Get the prompt text and reference images from the image metadata
        const promptText = img.prompt || img.metadata?.prompt || '';
        const referenceImages = img.reference_images || img.metadata?.reference_images || [];
        
        // Dispatch custom events to set the prompt form
        if (promptText) {
          window.dispatchEvent(new CustomEvent('setPromptText', { 
            detail: { prompt: promptText } 
          }));
        }
        
        if (referenceImages && referenceImages.length > 0) {
          // Get the bucket ID from the image
          const bucketId = img.bucketId || '_recent';
          
          // Convert reference images to accessible URLs
          const referenceUrls = ReferenceImageService.getReferenceImageUrls(bucketId, referenceImages);
          
          if (referenceUrls.length > 0) {
            // Clear existing reference images first
            window.dispatchEvent(new CustomEvent('useImageAsPrompt', { 
              detail: { url: referenceUrls[0], append: false } 
            }));
            
            // Add remaining images
            for (let i = 1; i < referenceUrls.length; i++) {
              window.dispatchEvent(new CustomEvent('useImageAsPrompt', { 
                detail: { url: referenceUrls[i], append: true } 
              }));
            }
          }
        }
        
        // Close the modal
        close();
        
        toast.success('Loaded prompt and reference images for editing');
      } catch (error) {
        console.error('Error starting again:', error);
        toast.error('Failed to load prompt and reference images');
      }
    };

    // Function to copy text to clipboard with fallback
    const copyToClipboard = (text: string) => {
      if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
        navigator.clipboard.writeText(text)
          .then(() => toast.success('Copied to clipboard'))
          .catch(err => {
            console.error('Failed to copy with clipboard API:', err);
            fallbackCopyToClipboard(text);
          });
      } else {
        fallbackCopyToClipboard(text);
      }
    };
    
    // Fallback method for copying to clipboard
    const fallbackCopyToClipboard = (text: string) => {
      try {
        // Create temporary textarea
        const textArea = document.createElement('textarea');
        textArea.value = text;
        textArea.style.position = 'fixed'; // Avoid scrolling to bottom
        textArea.style.top = '0';
        textArea.style.left = '0';
        textArea.style.width = '2em';
        textArea.style.height = '2em';
        textArea.style.padding = '0';
        textArea.style.border = 'none';
        textArea.style.outline = 'none';
        textArea.style.boxShadow = 'none';
        textArea.style.background = 'transparent';
        document.body.appendChild(textArea);
        textArea.focus();
        textArea.select();
        
        // Execute copy command
        const successful = document.execCommand('copy');
        if (successful) {
          toast.success('Copied to clipboard');
        } else {
          toast.error('Failed to copy to clipboard');
          
          // Provide manual copy instructions as last resort
          toast.info('Copy this manually: ' + text.substring(0, 30) + '...');
        }
        
        // Clean up
        document.body.removeChild(textArea);
      } catch (err) {
        console.error('Fallback copy method failed:', err);
        toast.error('Failed to copy to clipboard');
        
        // Show text for user to manually copy
        toast.info('Copy this manually: ' + text.substring(0, 30) + '...');
      }
    };

    /* ------------------------------------------------------------------ */
    /* Zoom: opened from a carousel still or clip (LoopeZoom does the rest) */
    /* ------------------------------------------------------------------ */
    const closeZoom = useCallback(() => {
      setZoom(null);
      // Make sure the carousel is reinitialized properly
      setTimeout(() => emblaApi?.reInit(), 100);
    }, [emblaApi]);

    const openZoom = useCallback((el: HTMLElement, entry: ZoomEntry) => {
      if (zoomRef.current) return;
      const r = el.getBoundingClientRect();
      if (r.width < 2 || r.height < 2) return; // still loading (hidden)
      const isVid = el instanceof HTMLVideoElement;
      const origin: ZoomOrigin = {
        src: el.getAttribute('data-loope-media') || (el as HTMLImageElement).currentSrc || '',
        isVideo: isVid,
        rect: { left: r.left, top: r.top, width: r.width, height: r.height },
        naturalWidth: isVid ? (el as HTMLVideoElement).videoWidth : (el as HTMLImageElement).naturalWidth,
        sourceEl: el,
        entry,
      };
      if (!origin.src) return;
      zoomRef.current = origin;
      setZoom(origin);
    }, []);

    // Double tap / double click on carousel media zooms in at that point (one detector for mouse and touch).
    const carouselTap = useRef({ downAt: 0, x: 0, y: 0, lastAt: 0, lastX: 0, lastY: 0, multi: false });
    const onMediaPointerDown = (e: React.PointerEvent) => {
      const s = carouselTap.current;
      if (!e.isPrimary) { s.multi = true; return; }
      s.multi = false; s.downAt = performance.now(); s.x = e.clientX; s.y = e.clientY;
    };
    const onMediaPointerUp = (e: React.PointerEvent) => {
      const s = carouselTap.current;
      if (!e.isPrimary || s.multi) return;
      const now = performance.now();
      if (now - s.downAt > 300 || Math.hypot(e.clientX - s.x, e.clientY - s.y) > 10) return;
      if (now - s.lastAt < 300 && Math.hypot(e.clientX - s.lastX, e.clientY - s.lastY) < 40) {
        s.lastAt = 0;
        openZoom(e.currentTarget as HTMLElement, { kind: 'point', x: e.clientX, y: e.clientY, scale: 'detail' });
        return;
      }
      s.lastAt = now; s.lastX = e.clientX; s.lastY = e.clientY;
    };

    // Pinch and scroll on carousel media open the zoom; the page itself never zooms under the dialog.
    // Native listeners because React's touch and wheel listeners are passive (preventDefault is ignored).
    const [carouselNode, setCarouselNode] = useState<HTMLDivElement | null>(null);
    const carouselRef = useCallback((el: HTMLDivElement | null) => {
      emblaRef(el);
      setCarouselNode(el);
    }, [emblaRef]);
    useEffect(() => {
      const node = carouselNode;
      if (!node) return;
      const mediaOf = (t: EventTarget | null) =>
        (t as Element | null)?.closest?.('[data-loope-media]') as HTMLElement | null;
      const onTouchStart = (e: TouchEvent) => {
        if (e.touches.length !== 2 || zoomRef.current) return;
        const [a, b] = [e.touches[0], e.touches[1]];
        const media = mediaOf(b.target) || mediaOf(a.target);
        if (!media) return;
        e.preventDefault();
        openZoom(media, {
          kind: 'pinch',
          ids: [a.identifier, b.identifier],
          m0: { x: (a.clientX + b.clientX) / 2, y: (a.clientY + b.clientY) / 2 },
          d0: Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY),
        });
      };
      const onTouchMove = (e: TouchEvent) => {
        if (e.touches.length > 1) e.preventDefault();
      };
      const onWheel = (e: WheelEvent) => {
        if (zoomRef.current) return;
        const media = mediaOf(e.target);
        if (!media) return;
        // Scroll up or a trackpad pinch-out zooms in; a pinch-in is swallowed so the page doesn't shrink.
        if (e.deltaY < 0) {
          e.preventDefault();
          openZoom(media, { kind: 'point', x: e.clientX, y: e.clientY, scale: 1.5 });
        } else if (e.ctrlKey) {
          e.preventDefault();
        }
      };
      const stopGesture = (e: Event) => e.preventDefault();
      const opts = { passive: false } as AddEventListenerOptions;
      node.addEventListener('touchstart', onTouchStart, opts);
      node.addEventListener('touchmove', onTouchMove, opts);
      node.addEventListener('wheel', onWheel, opts);
      node.addEventListener('gesturestart', stopGesture, opts);
      return () => {
        node.removeEventListener('touchstart', onTouchStart, opts);
        node.removeEventListener('touchmove', onTouchMove, opts);
        node.removeEventListener('wheel', onWheel, opts);
        node.removeEventListener('gesturestart', stopGesture, opts);
      };
    }, [carouselNode, openZoom]);

    // Reset zoom when closing the modal
    useEffect(() => {
      if (!isOpen) {
        setZoom(null);
      }
    }, [isOpen]);

    // Update dynamic title when swiping between published images
    useEffect(() => {
      if (isOpen && images.length > 0 && currentIndex >= 0 && currentIndex < images.length) {
        const currentImage = images[currentIndex];
        
        // Check if this is a published image with destination info
        if (currentImage?.isPublished && currentImage.destinationName) {
          // Create dynamic title for published images
          setDynamicTitle(`Currently published - ${currentImage.destinationName} (${currentIndex + 1}/${images.length})`);
        } else {
          // For non-published images, use the provided title
          setDynamicTitle(null);
        }
      }
    }, [isOpen, images, currentIndex]);

    // Reset dynamic title when closing modal
    useEffect(() => {
      if (!isOpen) {
        setDynamicTitle(null);
      }
    }, [isOpen]);

    // Reset states when modal opens or closes
    useEffect(() => {
      if (isOpen) {
        // When modal opens, set all images as loading initially
        const initialLoadingState: {[key: string]: boolean} = {};
        images.forEach(img => {
          const imgSrc = img.raw_url || img.urlFull || '';
          initialLoadingState[imgSrc] = true;
        });
        setLoadingImages(initialLoadingState);
      } else {
        // When modal closes, reset loading states
        setLoadingImages({});
        setZoom(null);
      }
    }, [isOpen, images]);

    // Handle image load completion
    const handleImageLoad = (src: string) => {
      setLoadingImages(prev => ({
        ...prev,
        [src]: false
      }));
    };

    if (!isOpen) return null;

    // Use dynamic title if available, otherwise fall back to the original title
    const displayTitle = dynamicTitle || title || '';
    const headerText = dynamicTitle ? displayTitle : `${displayTitle} ${currentIndex + 1}/${images.length}`;
    const currentImage = images[currentIndex];
    const imageSrc = currentImage?.raw_url || currentImage?.urlFull || '';
    const isVideo = currentImage?.mediaType === 'video' || /\.mp4$|\.webm$/i.test(imageSrc);
    
    // Disable favorite button for published images
    const showFavoriteButton = !(currentImage?.disableFavorite === true);

    // Tag lozenges: saved bucket images only (the _recent scratch area isn't tagged; a published copy's id isn't a file).
    const tagBucket = bucketOf(currentImage);
    const showTags = !!currentImage && !!tagBucket && tagBucket !== '_recent'
      && currentImage.disableFavorite !== true && currentImage.isPublished !== true;
    const tagMeta = (currentImage?.metadata || {}) as Record<string, any>;
    const tagAi = tagMeta.tagging?.ai as Record<string, any> | undefined;

    return (
      <Dialog 
        open={isOpen} 
        onOpenChange={(open) => {
          // Only allow closing via Dialog's built-in mechanisms when NOT in zoom mode
          if (!zoomSrc && !open) {
            close();
          }
        }}
      >
        <DialogContent className={`max-w-[95vw] w-[95vw] p-0 overflow-hidden flex flex-col bg-neutral-900 text-white shadow-[0_0_40px_rgba(0,0,0,.9)] border-0 group ${isMobile ? 'max-h-[65vh] h-[65vh]' : 'max-h-[80vh] h-[80vh]'}`}>
          <DialogTitle className="sr-only">Image Viewer</DialogTitle>
          
          {/* Close button for the main dialog */}
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
          <div className="flex-1 w-full h-full overflow-hidden" ref={carouselRef} onTouchStart={handleTouchStart} onTouchEnd={handleTouchEnd}>
            <div className="flex h-full">
              {images.map((img) => {
                const src = img.raw_url || img.urlFull;
                const isImgVideo = img.mediaType === 'video' || /\.mp4$|\.webm$/i.test(src);
                const isLoading = loadingImages[src] !== false; // true or undefined means loading
                
                return (
                  <div 
                    key={img.id} 
                    className="flex-shrink-0 flex items-center justify-center px-4" 
                    style={{ 
                      flex: '0 0 100%'
                    }}
                  >
                    {isLoading && (
                      <div className="flex flex-col items-center justify-center text-white/70">
                        <Loader2 className="h-8 w-8 animate-spin mb-2" />
                        <span className="text-sm">Loading image...</span>
                      </div>
                    )}
                    
                    {isImgVideo ? (
                      <video
                        src={src}
                        data-loope-media={src}
                        controls
                        playsInline
                        className={`max-h-full max-w-full object-contain ${isLoading ? 'hidden' : ''}`}
                        onLoadedData={() => handleImageLoad(src)}
                        onPointerDown={onMediaPointerDown}
                        onPointerUp={onMediaPointerUp}
                        // Our double-click zooms; stop the browser's own double-click fullscreen.
                        onDoubleClick={(e) => e.preventDefault()}
                        title="Pinch, scroll or double-click to zoom"
                        style={{ touchAction: 'none' }}
                      />
                    ) : (
                      <img
                        src={src}
                        alt={img.promptKey || ''}
                        data-loope-media={src}
                        draggable={false}
                        className={`max-h-full max-w-full object-contain select-none ${isLoading ? 'hidden' : ''}`}
                        onPointerDown={onMediaPointerDown}
                        onPointerUp={onMediaPointerUp}
                        onLoad={() => handleImageLoad(src)}
                        title="Pinch, scroll or double-click to zoom"
                        style={{ touchAction: 'none' }}
                      />
                    )}
                  </div>
                );
              })}
            </div>
          </div>

          {/* Metadata overlay - positioned so it doesn't obscure title */}
          {isFlipped && (
            <div className="absolute inset-10 z-40 bg-neutral-900/95 rounded-md overflow-hidden shadow-xl border border-neutral-700">
              <div className="h-full flex flex-col">
                <div className="p-3 border-b border-neutral-700 flex justify-between items-center">
                  <h3 className="font-medium text-sm">Image Metadata</h3>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 w-7 p-0"
                    onClick={() => setIsFlipped(false)}
                  >
                    <X className="h-4 w-4" />
                  </Button>
                </div>
                
                <div className="flex-1 overflow-y-auto p-3 text-xs">
                  {currentImage && (
                    <div className="space-y-4">
                      {/* Basic info section */}
                      <div>
                        <h4 className="font-medium text-xs text-neutral-400 mb-2">Basic Information</h4>
                        <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-2">
                          <div className="text-neutral-300">ID</div>
                          <div className="break-all text-white">{currentImage.id}</div>
                          
                          {typeof currentImage?.bucketId === 'string' && (
                            <>
                              <div className="text-neutral-300">Bucket</div>
                              <div className="break-all text-white">{currentImage.bucketId}</div>
                            </>
                          )}
                          
                          {currentImage.mediaType && (
                            <>
                              <div className="text-neutral-300">Type</div>
                              <div className="text-white">{currentImage.mediaType}</div>
                            </>
                          )}
                          
                          {currentImage.createdAt && (
                            <>
                              <div className="text-neutral-300">Created</div>
                              <div className="text-white">{new Date(currentImage.createdAt).toLocaleString()}</div>
                            </>
                          )}
                          
                          {currentImage.isFavourite !== undefined && (
                            <>
                              <div className="text-neutral-300">Favorite</div>
                              <div className="text-white">{currentImage.isFavourite ? "Yes" : "No"}</div>
                            </>
                          )}
                        </div>
                      </div>
                      
                      {/* Reference Images section */}
                      {(() => {
                        // Debug the current image data
                        console.log('=== LoopeModal Reference Images Debug ===');
                        console.log('currentImage:', currentImage);
                        console.log('currentImage.reference_images:', currentImage?.reference_images);
                        console.log('currentImage.metadata:', currentImage?.metadata);
                        console.log('currentImage.metadata.reference_images:', (currentImage?.metadata as any)?.reference_images);
                        
                        // Check all possible locations for reference images
                        const directRefImages = currentImage?.reference_images;
                        const metadataRefImages = (currentImage?.metadata as any)?.reference_images;
                        
                        console.log('directRefImages:', directRefImages);
                        console.log('metadataRefImages:', metadataRefImages);
                        
                        // Use the first available reference images array
                        const refImages = directRefImages || metadataRefImages || [];
                        
                        console.log('Final refImages to render:', refImages);
                        console.log('refImages.length:', refImages.length);
                        console.log('refImages is array:', Array.isArray(refImages));
                        
                        // Additional debug info
                        console.log('currentImage keys:', currentImage ? Object.keys(currentImage) : 'none');
                        console.log('currentImage.bucketId:', currentImage?.bucketId);
                        console.log('==========================================');
                        
                        if (!Array.isArray(refImages) || refImages.length === 0) {
                          console.log('No reference images found - not rendering section');
                          return null;
                        }
                        
                        return (
                          <div>
                            <h4 className="font-medium text-xs text-neutral-400 mb-2">Reference Images ({refImages.length})</h4>
                            <div className="flex gap-2 flex-wrap">
                              {refImages.map((ref: any, idx: number) => {
                              console.log(`Processing reference image ${idx}:`, ref);
                              // Get the bucket ID from the image data, fallback to _recent for backward compatibility
                              const bucketId = currentImage.bucketId || '_recent';
                              const refUrl = ReferenceImageService.getReferenceImageUrls(bucketId, [ref])[0];
                              const thumbnailUrl = ReferenceImageService.getReferenceImageThumbnailUrls(bucketId, [ref])[0] || 'data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs=';
                              console.log('Using bucketId:', bucketId);
                              console.log('Generated refUrl:', refUrl);
                              console.log('Generated thumbnailUrl:', thumbnailUrl);

                              
                              return (
                                <button
                                  key={ref.stored_path}
                                  onClick={() => {
                                    console.log('Clicking reference image, opening:', refUrl);
                                    // Open the reference image in a new tab
                                    window.open(refUrl, '_blank', 'noopener,noreferrer');
                                  }}
                                  className="relative group cursor-pointer"
                                  title={`Click to view original: ${ref.original_filename}`}
                                >
                                  <img
                                    src={thumbnailUrl}
                                    alt={`Reference ${idx + 1}`}
                                    className="w-16 h-16 object-cover rounded border border-neutral-600 hover:border-neutral-400 transition-colors"
                                    onError={e => {
                                      console.error('Reference image thumbnail failed to load:', thumbnailUrl);
                                      e.currentTarget.src = 'data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs=';
                                    }}
                                  />
                                  <div className="absolute inset-0 bg-black/0 group-hover:bg-black/20 transition-colors rounded flex items-center justify-center">
                                    <div className="opacity-0 group-hover:opacity-100 transition-opacity text-white text-xs font-medium">
                                      View
                                    </div>
                                  </div>
                                </button>
                              );
                            })}
                          </div>
                        </div>
                        );
                      })()}
                      
                      {/* Prompt section */}
                      {currentImage.promptKey && (
                        <div>
                          <h4 className="font-medium text-xs text-neutral-400 mb-2">Prompt</h4>
                          <div className="bg-neutral-800 p-2 rounded-md text-white break-words whitespace-pre-wrap">{currentImage.promptKey}</div>
                        </div>
                      )}
                      
                      {/* Tags: what the AI tagger saw (the lozenges themselves are under the picture) */}
                      {tagAi && (
                        <div>
                          <h4 className="font-medium text-xs text-neutral-400 mb-2">Tags</h4>
                          <div className="bg-neutral-800 p-2 rounded-md text-white space-y-1">
                            {tagAi.look && <div className="break-words">{String(tagAi.look)}</div>}
                            <div className="flex flex-wrap gap-x-3 gap-y-1 text-neutral-300">
                              {tagAi.palette && <span>Palette: <span className="text-white">{String(tagAi.palette)}</span></span>}
                              {suitedGroups(tagAi.music).length > 0 && (
                                <span>Suits: <span className="text-white">{suitedGroups(tagAi.music).join(', ')}</span></span>
                              )}
                              {typeof tagAi.quality === 'number' && (
                                <span>Quality <span className="text-white tabular-nums">{tagAi.quality.toFixed(2)}</span></span>
                              )}
                            </div>
                          </div>
                        </div>
                      )}
                      {tagMeta.tagging?.status === 'failed' && (
                        <div className="text-neutral-400">
                          Not tagged{tagMeta.tagging.error ? `: ${String(tagMeta.tagging.error)}` : ''}
                        </div>
                      )}

                      {/* Other properties section (excluding already shown and metadata) */}
                      {Object.entries(currentImage).some(([key, value]) => 
                        !['id', 'promptKey', 'metadata', 'raw_url', 'urlFull', 'urlThumb', 'bucketId', 'mediaType', 'createdAt', 'isFavourite'].includes(key) &&
                        typeof value !== 'object' && 
                        typeof value !== 'function' &&
                        value !== null &&
                        value !== undefined
                      ) && (
                        <div>
                          <h4 className="font-medium text-xs text-neutral-400 mb-2">Properties</h4>
                          <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-2">
                            {Object.entries(currentImage).map(([key, value]) => {
                              if (['id', 'promptKey', 'metadata', 'raw_url', 'urlFull', 'urlThumb', 'bucketId', 'mediaType', 'createdAt', 'isFavourite'].includes(key) || 
                                  typeof value === 'object' || 
                                  typeof value === 'function' ||
                                  value === null ||
                                  value === undefined) return null;
                              
                              return (
                                <React.Fragment key={key}>
                                  <div className="text-neutral-300">{key}</div>
                                  <div className="break-all text-white">{String(value)}</div>
                                </React.Fragment>
                              );
                            })}
                          </div>
                        </div>
                      )}
                      
                      {/* Additional metadata section */}
                      {currentImage.metadata && Object.keys(currentImage.metadata).length > 0 && (
                        <div>
                          <h4 className="font-medium text-xs text-neutral-400 mb-2">Extended Metadata</h4>
                          <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-2">
                            {Object.entries(currentImage.metadata)
                              .filter(([key, value]) => 
                                // Don't show prompt again if already shown in prompt section
                                !(key === 'prompt' && currentImage.promptKey) &&
                                // Skip favorite since it's shown in basic info
                                key !== 'favorite' &&
                                // Tags have their own block and lozenges
                                key !== 'tags' && key !== 'tagging' &&
                                value !== null &&
                                value !== undefined
                              )
                              .sort(([a], [b]) => a.localeCompare(b))
                              .map(([key, value]) => {
                                // Format the value based on its type
                                let displayValue;
                                if (value === null || value === undefined) {
                                  return null; // Skip null/undefined values
                                } else if (Array.isArray(value)) {
                                  if (value.length === 0) {
                                    displayValue = "[]"; // Empty array
                                  } else {
                                    displayValue = (
                                      <span className="text-xs bg-neutral-800 p-1 rounded">
                                        {JSON.stringify(value, null, 2)}
                                      </span>
                                    );
                                  }
                                } else if (typeof value === 'object') {
                                  try {
                                    const jsonString = JSON.stringify(value, null, 2) || "";
                                    displayValue = (
                                      <span className="text-xs bg-neutral-800 p-1 rounded">
                                        <pre className="whitespace-pre-wrap">{jsonString}</pre>
                                      </span>
                                    );
                                  } catch (err) {
                                    displayValue = "[Object]";
                                  }
                                } else if (typeof value === 'number' && key.includes('time')) {
                                  // Format timestamps more nicely
                                  try {
                                    const date = new Date(value * 1000);
                                    if (!isNaN(date.getTime())) {
                                      displayValue = date.toLocaleString();
                                    } else {
                                      displayValue = String(value);
                                    }
                                  } catch (e) {
                                    displayValue = String(value);
                                  }
                                } else {
                                  displayValue = String(value);
                                }
                                
                                return (
                                  <React.Fragment key={key}>
                                    <div className="text-neutral-300">{key}</div>
                                    <div className="break-all text-white">
                                      {displayValue}
                                    </div>
                                  </React.Fragment>
                                );
                              })}
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>
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
          {/* not with the tag row: its height varies and the hint landed on the lozenges; the Info button stays */}
          {!isFlipped && !zoomSrc && showHint && !showTags && (
            <div className={`absolute bottom-16 left-1/2 -translate-x-1/2 text-white/80 flex flex-col items-center text-xs pointer-events-none md:hidden`}>
              <ChevronUp className="h-4 w-4 animate-bounce mb-1" />
              <span>Info</span>
            </div>
          )}

          {/* Tag lozenges, just above the action bar: visible without flipping, phone and desktop (owner, 27 Oct) */}
          {!zoomSrc && showTags && (
            <div className="w-full px-4 pt-2 flex justify-center">
              <TagLozenges
                key={`${tagBucket}/${currentImage.id}`}
                variant="dark"
                compact
                className="justify-center"
                bucket={tagBucket as string}
                filename={currentImage.id}
                tags={tagMeta.tags}
                tagging={tagMeta.tagging}
                favorite={!!currentImage.isFavourite}
                isVideo={isVideo}
                onTagsChange={(patch) => {
                  currentImage.metadata = { ...(currentImage.metadata || {}), ...patch };
                  bumpItems();
                }}
                onFavoriteChange={(fav) => {
                  currentImage.isFavourite = fav;
                  bumpItems();
                }}
              />
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
              {showFavoriteButton && (
                <Button 
                  variant="ghost" 
                  size="sm" 
                  className="text-xs flex items-center gap-1 text-white/90 hover:text-white hover:bg-white/10"
                  onClick={() => handleToggleFavorite(currentImage)}
                >
                  <Star className={`h-4 w-4 ${currentImage?.isFavourite ? 'fill-yellow-400' : ''}`} />
                  <span className="hidden sm:inline">Favorite</span>
                </Button>
              )}

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
                  copyToClipboard(imageSrc);
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
                  {nonHeadlessDestinations && nonHeadlessDestinations.length > 0 && (
                    <DropdownMenuSub>
                      <DropdownMenuSubTrigger>
                        <Share className="mr-2 h-4 w-4" />
                        <span>Publish to...</span>
                      </DropdownMenuSubTrigger>
                      <DropdownMenuSubContent>
                        {nonHeadlessDestinations.map(dest => (
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
                  {destinations && destinations.length > 0 && (
                    <DropdownMenuSub>
                      <DropdownMenuSubTrigger>
                        <Copy className="mr-2 h-4 w-4" />
                        <span>Copy to...</span>
                      </DropdownMenuSubTrigger>
                      <DropdownMenuSubContent>
                        {destinations.map(dest => (
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
                  {destinations && destinations.length > 0 && (
                    <DropdownMenuSub>
                      <DropdownMenuSubTrigger>
                        <Move className="mr-2 h-4 w-4" />
                        <span>Move to...</span>
                      </DropdownMenuSubTrigger>
                      <DropdownMenuSubContent>
                        {destinations.map(dest => (
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
                  
                  {/* Start Again option */}
                  <DropdownMenuItem onClick={() => handleStartAgain(currentImage)}>
                    <RefreshCw className="mr-2 h-4 w-4" />
                    <span>Start Again</span>
                  </DropdownMenuItem>
                  
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

        {zoom && <LoopeZoom origin={zoom} onClose={closeZoom} />}
      </Dialog>
    );
  } catch (error) {
    // Log the error but prevent it from crashing the app
    console.warn("Error in LoopeModal:", error);
    return null;
  }
};

export default LoopeModal; 