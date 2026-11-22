import React, { useEffect, useCallback, useMemo, useState, useRef, useReducer } from 'react';
import * as DialogPrimitive from '@radix-ui/react-dialog';
import { useLoopeView } from '@/contexts/LoopeViewContext';
import useEmblaCarousel from 'embla-carousel-react';
import type { ReactZoomPanPinchRef } from 'react-zoom-pan-pinch';
import { ChevronLeft, ChevronRight, X, ChevronUp, Star, Info, Download, Clipboard, MoreHorizontal, RefreshCcw, Loader2, Scissors, Play } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { toast } from 'sonner';
import { bucketsApi, publishApi } from '@/shared/api';
import { usePublishDestinations } from '@/hooks/usePublishDestinations';
import { ReferenceImageService } from '@/services/reference-image-service';
import { TagLozenges } from '@/components/tags/TagLozenges';
import type { ImageItem } from '@/types/image-types';
import { ImageInfo } from './ImageInfo';
import { ZoomSlide } from './ZoomSlide';
import type { Size } from './zoomMath';
import { ImageMenuItems } from '@/components/common/ImageMenuItems';
import { notifyItemRemoved, notifyItemUpdated } from '@/components/tags/events';

interface LoopeModalProps {
  title?: string;
}

/** The bucket an image lives in: its bucketId, else parsed from /output/<bucket>/ in its URLs. */
function bucketOf(img: ImageItem | undefined): string | null {
  if (!img) return null;
  if (typeof img.bucketId === 'string' && img.bucketId) return img.bucketId;
  const m = (typeof img.raw_url === 'string' && img.raw_url.match(/\/output\/([^/]+)\/(.+)/))
    || (typeof img.urlThumb === 'string' && img.urlThumb.match(/\/output\/([^/]+)\/thumbnails\/(.+)/));
  return m ? m[1] : null;
}

const isVideoItem = (img: ImageItem, src: string) => img.mediaType === 'video' || /\.mp4$|\.webm$/i.test(src);

/** A picture and its clips: a small strip of their tiles; each clip's tile plays that clip (owner, 20 Nov: one big
 *  "Play its clip" button took room and didn't say which clip). */
function ClipStrip({ group, currentIndex, onPick, className = '' }: {
  group: { item: ImageItem; index: number }[];
  currentIndex: number;
  onPick: (index: number, play: boolean) => void;
  className?: string;
}) {
  return (
    <div className={`max-w-[92%] gap-1.5 overflow-x-auto rounded-lg bg-black/55 p-1.5 ${className}`}>
      {group.map(({ item, index }, n) => {
        const secs = Number(item.metadata?.clip?.play_s) || 0;
        return (
          <button
            key={item.uniqueKey || item.id}
            type="button"
            title={n === 0 ? 'The picture' : `Clip ${n}${item.metadata?.clip?.pace ? ` (${item.metadata.clip.pace})` : ''}: play it`}
            className={`relative h-10 w-10 shrink-0 overflow-hidden rounded md:h-12 md:w-12 ${index === currentIndex ? 'ring-2 ring-white' : 'opacity-75 hover:opacity-100'}`}
            onClick={() => onPick(index, n > 0)}
          >
            {item.urlThumb ? (
              <img src={item.urlThumb} alt="" className="h-full w-full object-cover" />
            ) : (
              <video src={`${item.urlFull}#t=0.1`} preload="metadata" muted className="h-full w-full object-cover" />
            )}
            {n > 0 && (
              <span className="absolute inset-x-0 bottom-0 flex items-center justify-center gap-0.5 bg-black/60 text-[10px] leading-4 text-white">
                <Play className="h-2.5 w-2.5 fill-white" />
                {secs ? `${Math.round(secs)}s` : ''}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

/**
 * The Loope: the full-screen viewer for a set of pictures and clips (owner, 22 Nov: "quite easy to get 'stuck' after
 * pinch zooming & panning"; "doesn't cope with landscape mode on a phone").
 * - It fills the screen (dynamic viewport height, safe areas), any device, either way up. On a phone on its side the
 *   tags and buttons lie over the picture, and a tap on the picture hides or shows them.
 * - Zoom is in place, on the slide (ZoomSlide): pinch, wheel or double tap; never smaller than fit; zoomed, a finger
 *   pans; at fit it swipes to the next. The close button is always there; Escape and the phone's Back step out
 *   (zoom, then Info, then the viewer).
 */
const LoopeModal: React.FC<LoopeModalProps> = ({ title }) => {
  const { isOpen, images, currentIndex, goto, close, carouselOptions, open } = useLoopeView();

  // zoomed, the carousel must not take the finger: read when a drag starts
  const zoomedRef = useRef(false);
  const [zoomed, setZoomedState] = useState(false);
  const setZoomed = useCallback((z: boolean) => {
    zoomedRef.current = z;
    setZoomedState(z);
  }, []);
  const zoomApis = useRef(new Map<string, ReactZoomPanPinchRef>());

  const emblaOptions = useMemo(() => ({
    ...carouselOptions,
    loop: carouselOptions?.loop ?? true,
    watchDrag: () => !zoomedRef.current,
  }), [carouselOptions]);
  const [emblaRef, emblaApi] = useEmblaCarousel(emblaOptions);

  const [isFlipped, setIsFlipped] = useState(false);
  // the controls laid over the picture (a phone on its side) and the title: a tap on the picture hides or shows them
  const [chrome, setChrome] = useState(true);
  // a clip asked for (its tile in the strip below the picture, or a tile's clip badge) plays on arrival; others wait
  const [playId, setPlayId] = useState<string | null>(null);
  const videoEls = useRef(new Map<string, HTMLVideoElement>());
  const [loadedVideos, setLoadedVideos] = useState<Record<string, boolean>>({});
  const [showHint, setShowHint] = useState(true);
  const touchStartY = useRef<number | null>(null);

  // Items are the opener's objects, patched in place (star, tags); this re-renders after a patch.
  const [, bumpItems] = useReducer((n: number) => n + 1, 0);
  const { destinations, nonHeadlessDestinations } = usePublishDestinations();

  // The stage's size: each picture is fitted to it, and a change (a phone turned) starts its zoom afresh.
  const [stage, setStage] = useState<Size>({ w: 0, h: 0 });
  const stageObserver = useRef<ResizeObserver | null>(null);
  const stageRef = useCallback((el: HTMLDivElement | null) => {
    stageObserver.current?.disconnect();
    stageObserver.current = null;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      setStage((s) => (Math.round(width) === s.w && Math.round(height) === s.h ? s : { w: Math.round(width), h: Math.round(height) }));
    });
    ro.observe(el);
    stageObserver.current = ro;
  }, []);

  /* ------------------------------------------------------------------ */
  /* Opening, closing, the phone's Back                                  */
  /* ------------------------------------------------------------------ */

  // Each opening starts on the picture, or on its Info when asked (a tile's Info button)
  useEffect(() => {
    if (!isOpen) return;
    setIsFlipped(!!carouselOptions?.showInfo);
    setPlayId((carouselOptions?.playId as string | null) ?? null);
    setChrome(true);
    setZoomed(false);
  }, [isOpen, carouselOptions, setZoomed]);

  // Back closes the viewer: it puts an entry on top while open (the same pattern as the score's stand).
  // The entry is put on and taken off by the open state itself, not an effect's cleanup (a re-run must not pop it).
  const backEntry = useRef(false);
  useEffect(() => {
    if (isOpen && !backEntry.current) {
      try {
        window.history.pushState(window.history.state, '');
        backEntry.current = true;
      } catch {
        /* no entry: Back leaves the page as before */
      }
    } else if (!isOpen && backEntry.current) {
      backEntry.current = false;
      window.history.back();
    }
  }, [isOpen]);
  useEffect(() => {
    if (!isOpen) return;
    const onPop = () => {
      if (!backEntry.current) return;
      backEntry.current = false;
      close();
    };
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, [isOpen, close]);

  // a new slide starts at fit
  useEffect(() => {
    setZoomed(false);
  }, [currentIndex, setZoomed]);

  const unzoom = useCallback(() => {
    const id = images[currentIndex]?.id;
    const api = id ? zoomApis.current.get(id) : undefined;
    if (api) api.centerView(1, 200);
    else setZoomed(false);
  }, [images, currentIndex, setZoomed]);

  /** Escape steps out: the zoom, then Info, then the viewer. */
  const stepOut = useCallback((): boolean => {
    if (zoomedRef.current) {
      unzoom();
      return true;
    }
    if (isFlipped) {
      setIsFlipped(false);
      return true;
    }
    return false;
  }, [unzoom, isFlipped]);

  /* ------------------------------------------------------------------ */
  /* Carousel <-> context index                                          */
  /* ------------------------------------------------------------------ */
  useEffect(() => {
    if (emblaApi && isOpen && emblaApi.selectedScrollSnap() !== currentIndex) emblaApi.scrollTo(currentIndex, true);
  }, [currentIndex, emblaApi, isOpen]);

  useEffect(() => {
    if (!emblaApi) return;
    const onSelect = () => goto(emblaApi.selectedScrollSnap());
    emblaApi.on('select', onSelect);
    return () => {
      emblaApi.off('select', onSelect);
    };
  }, [emblaApi, goto]);

  // Only the slide in view plays: leaving a clip pauses it, and the clip asked for is started here (an autoPlay
  // attribute does nothing to a slide's <video> that has already loaded, which is why Play did nothing).
  useEffect(() => {
    if (!isOpen) return;
    const id = images[currentIndex]?.id;
    videoEls.current.forEach((el, key) => {
      if (key !== id && !el.paused) el.pause();
    });
    if (id && id === playId) {
      const el = videoEls.current.get(id);
      if (el) {
        el.currentTime = 0;
        void el.play().catch(() => {});
      }
      setPlayId(null);
    }
  }, [currentIndex, playId, isOpen, images]);

  /* ------------------------------------------------------------------ */
  /* Keys (Escape is the dialog's: stepOut first)                        */
  /* ------------------------------------------------------------------ */
  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent) => {
      // Typing in a field (the tag search) is not navigation: arrows and "i" belong to it.
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
      if (zoomedRef.current) return;
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
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [isOpen, emblaApi]);

  // iOS Safari zooms the whole page on a pinch unless its gesture events are stopped; the picture zooms instead.
  useEffect(() => {
    if (!isOpen) return;
    const stop = (e: Event) => e.preventDefault();
    document.addEventListener('gesturestart', stop);
    return () => document.removeEventListener('gesturestart', stop);
  }, [isOpen]);

  /* ------------------------------------------------------------------ */
  /* Vertical swipe at fit: up opens Info, down closes it                */
  /* ------------------------------------------------------------------ */
  const handleTouchStart: React.TouchEventHandler = (e) => {
    touchStartY.current = !zoomedRef.current && e.touches.length === 1 ? e.touches[0].clientY : null;
  };
  const handleTouchEnd: React.TouchEventHandler = (e) => {
    if (zoomedRef.current || touchStartY.current === null || e.touches.length > 0) return;
    const delta = e.changedTouches[0].clientY - touchStartY.current;
    touchStartY.current = null;
    if (delta < -80) {
      setIsFlipped(true);
      setShowHint(false);
    } else if (delta > 80) {
      setIsFlipped(false);
    }
  };

  /* ------------------------------------------------------------------ */
  /* Actions                                                             */
  /* ------------------------------------------------------------------ */
  const needBucket = (img: ImageItem, what: string): string | null => {
    const b = bucketOf(img);
    if (!b) toast.error(`Could not ${what}: missing bucket ID`);
    return b;
  };

  const handleToggleFavorite = async (img: ImageItem) => {
    if (!img?.id) return;
    const bucketId = needBucket(img, 'toggle favorite');
    if (!bucketId) return;
    try {
      const want = !img.isFavourite;
      if (!(await bucketsApi.toggleFavorite(bucketId, img.id, want))) throw new Error('not toggled');
      img.isFavourite = want;
      bumpItems();
      // The grid holds its own copy: tell it, so it isn't stale until a refresh.
      notifyItemUpdated({ bucket: bucketId, filename: img.id, patch: { favorite: want } });
      toast.success(want ? 'Added to favorites' : 'Removed from favorites');
    } catch (error) {
      console.error('Error toggling favorite:', error);
      toast.error('Failed to toggle favorite');
    }
  };

  const handleUseAsPrompt = (img: ImageItem) => {
    const imageUrl = img?.raw_url || img?.urlFull;
    if (!imageUrl) {
      toast.error('Image URL not found');
      return;
    }
    window.dispatchEvent(new CustomEvent('useImageAsPrompt', {
      detail: { url: imageUrl, preserveFavorites: true, useReferenceUrl: true, imageId: img.id, source: 'loope-view', append: false },
    }));
    toast.success('Image set as prompt reference');
    close();
  };

  const handleCopyTo = async (img: ImageItem, destinationId: string) => {
    const source = img?.id && needBucket(img, 'copy');
    if (!source) return;
    try {
      await bucketsApi.copyImageToBucket(source, destinationId, img.id, true);
      toast.success(`Copied to ${destinationId}`);
    } catch (error) {
      console.error('Error copying image:', error);
      toast.error('Failed to copy image');
    }
  };

  const handleMoveTo = async (img: ImageItem, destinationId: string) => {
    const source = img?.id && needBucket(img, 'move');
    if (!source) return;
    try {
      await bucketsApi.copyImageToBucket(source, destinationId, img.id, false);
      toast.success(`Moved to ${destinationId}`);
      notifyItemRemoved({ bucket: source, filename: img.id });
      close();
    } catch (error) {
      console.error('Error moving image:', error);
      toast.error('Failed to move image');
    }
  };

  const handlePublish = async (img: ImageItem, destinationId: string) => {
    const source = img?.id && needBucket(img, 'publish');
    if (!source) return;
    try {
      const result = await publishApi.publishImageUnified({ dest_bucket_id: destinationId, src_bucket_id: source, filename: img.id });
      if (result.success) toast.success(publishApi.publishedWords(result, `Published to ${destinationId}`));
      else if (!result.cancelled) toast.error(`Failed to publish image: ${result.error ?? ''}`);
    } catch (error) {
      console.error('Error publishing image:', error);
      toast.error('Failed to publish image');
    }
  };

  const handleDelete = async (img: ImageItem) => {
    if (!img?.id) return;
    if (!confirm(`Are you sure you want to delete this image${img.isFavourite ? ' (favorite)' : ''}?`)) return;
    const bucketId = needBucket(img, 'delete');
    if (!bucketId) return;
    try {
      await bucketsApi.deleteFromBucket(bucketId, img.id);
      toast.success('Image deleted');
      notifyItemRemoved({ bucket: bucketId, filename: img.id });
      close();
    } catch (error) {
      console.error('Error deleting image:', error);
      toast.error('Failed to delete image');
    }
  };

  const handleStartAgain = (img: ImageItem) => {
    if (!img) return;
    const promptText = img.prompt || img.metadata?.prompt || '';
    const referenceImages = img.reference_images || img.metadata?.reference_images || [];
    if (promptText) window.dispatchEvent(new CustomEvent('setPromptText', { detail: { prompt: promptText } }));
    const urls = referenceImages.length ? ReferenceImageService.getReferenceImageUrls(img.bucketId || '_recent', referenceImages) : [];
    urls.forEach((url, i) => window.dispatchEvent(new CustomEvent('useImageAsPrompt', { detail: { url, append: i > 0 } })));
    close();
    toast.success('Loaded prompt and reference images for editing');
  };

  const copyToClipboard = (text: string) => {
    const fallback = () => {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand('copy');
      document.body.removeChild(ta);
      if (ok) toast.success('Copied to clipboard');
      else toast.info('Copy this manually: ' + text.substring(0, 30) + '...');
    };
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(text).then(() => toast.success('Copied to clipboard'), fallback);
    } else {
      fallback();
    }
  };

  if (!isOpen) return null;

  const currentImage = images[currentIndex];
  const imageSrc = currentImage?.raw_url || currentImage?.urlFull || '';
  const headerText = currentImage?.isPublished && currentImage.destinationName
    ? `Currently published - ${currentImage.destinationName} (${currentIndex + 1}/${images.length})`
    : `${title || ''} ${currentIndex + 1}/${images.length}`;
  // a picture's clip is a slide of its own (LoopeViewContext ungroup): this is it, and the picture it belongs to
  const clipOf = currentImage?.mediaType === 'video' ? (currentImage?.metadata?.still as string | undefined) : undefined;
  // the picture and its clips around the current slide (ungroup puts a picture's clips right after it)
  const groupId = clipOf ?? (currentImage?.metadata?.clips?.length ? currentImage.id : undefined);
  const group: { item: ImageItem; index: number }[] = [];
  if (groupId) {
    const at = images.findIndex((x) => x.id === groupId);
    if (at >= 0) {
      group.push({ item: images[at], index: at });
      for (let i = at + 1; i < images.length && images[i].metadata?.still === groupId; i++) group.push({ item: images[i], index: i });
    }
  }
  const pickInGroup = (index: number, play: boolean) => {
    if (play) setPlayId(images[index].id);
    goto(index);
  };

  // A picture's clips share its star and its tag row: moving between them leaves the rows below as they are, so the
  // picture stays where it is (owner, 20 Nov: "don't let the image move up and down")
  const tagItem = group.length > 1 ? group[0].item : currentImage;
  const showFavoriteButton = !(tagItem?.disableFavorite === true);
  // Tag lozenges: saved bucket images only (the _recent scratch area isn't tagged; a published copy's id isn't a file).
  const tagBucket = bucketOf(tagItem);
  const showTags = !!tagItem && !!tagBucket && tagBucket !== '_recent'
    && tagItem.disableFavorite !== true && tagItem.isPublished !== true;
  const tagMeta = tagItem?.metadata || {};

  // the controls laid over the picture on a phone on its side, hidden by a tap (or while zoomed in)
  const overlayHidden = !chrome || zoomed;
  const barButton = 'text-xs flex items-center gap-1 text-white/90 hover:text-white hover:bg-white/10';

  return (
    <DialogPrimitive.Root open={isOpen} onOpenChange={(o) => { if (!o) close(); }}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Content
          aria-describedby={undefined}
          onOpenAutoFocus={(e) => e.preventDefault()}
          onEscapeKeyDown={(e) => {
            if (stepOut()) e.preventDefault();
          }}
          className="fixed inset-0 z-50 flex h-[100dvh] w-screen flex-col overflow-hidden bg-neutral-950 text-white outline-none group"
        >
          <DialogPrimitive.Title className="sr-only">Image Viewer</DialogPrimitive.Title>

          {/* Top: what this is, and the way out (always there, zoomed or not) */}
          <div className="pointer-events-none absolute inset-x-0 top-0 z-50 flex items-start justify-between gap-2 bg-gradient-to-b from-black/60 to-transparent px-3 pb-6 pt-[max(0.5rem,env(safe-area-inset-top))]">
            <div className={`mt-1.5 truncate text-sm transition-opacity ${overlayHidden ? 'opacity-0' : 'opacity-90'}`}>
              {headerText}
            </div>
            <Button
              variant="ghost"
              size="icon"
              aria-label="Close"
              className="pointer-events-auto shrink-0 rounded-full bg-black/50 text-white hover:bg-black/70"
              onClick={close}
            >
              <X className="h-5 w-5" />
            </Button>
          </div>

          {/* The pictures */}
          <div ref={stageRef} className="relative min-h-0 w-full flex-1">
            <div
              className="h-full w-full overflow-hidden"
              ref={emblaRef}
              style={{ touchAction: 'none' }}
              onTouchStart={handleTouchStart}
              onTouchEnd={handleTouchEnd}
            >
              <div className="flex h-full">
                {images.map((img, slide) => {
                  const src = img.raw_url || img.urlFull || '';
                  // only the slides in view and beside it load now: a day of pictures, or a picture's 4K clips (each
                  // ~100 MB), all loading at once made the viewer slow to open
                  const near = Math.abs(slide - currentIndex) <= 1
                    || (carouselOptions?.loop !== false && Math.abs(slide - currentIndex) === images.length - 1);
                  return (
                    <div key={img.uniqueKey || img.id} className="relative h-full min-w-0 flex-[0_0_100%]">
                      {isVideoItem(img, src) ? (
                        <div className="flex h-full w-full items-center justify-center px-2">
                          {!loadedVideos[src] && (
                            <div className="absolute inset-0 flex flex-col items-center justify-center text-white/70 pointer-events-none">
                              <Loader2 className="h-8 w-8 animate-spin mb-2" />
                              <span className="text-sm">Loading clip...</span>
                            </div>
                          )}
                          <video
                            src={`${src}#t=0.001`}
                            controls
                            preload={near ? 'auto' : 'none'}
                            playsInline
                            ref={(el) => {
                              if (el) videoEls.current.set(img.id, el);
                              else videoEls.current.delete(img.id);
                            }}
                            className="h-full w-full object-contain"
                            onLoadedData={() => setLoadedVideos((v) => ({ ...v, [src]: true }))}
                          />
                        </div>
                      ) : (
                        <ZoomSlide
                          src={src}
                          alt={img.promptKey || ''}
                          active={slide === currentIndex && !isFlipped}
                          near={near}
                          stage={stage}
                          onZoomed={slide === currentIndex ? setZoomed : noop}
                          onApi={(api) => {
                            if (api) zoomApis.current.set(img.id, api);
                            else zoomApis.current.delete(img.id);
                          }}
                          onTap={() => setChrome((c) => !c)}
                        />
                      )}
                    </div>
                  );
                })}
              </div>
            </div>

            {!isFlipped && !zoomed && group.length > 1 && (
              <ClipStrip
                group={group}
                currentIndex={currentIndex}
                onPick={pickInGroup}
                className="absolute bottom-2 left-1/2 z-30 flex -translate-x-1/2 short:hidden"
              />
            )}

            {/* Arrows (a mouse; fingers swipe) */}
            {images.length > 1 && !zoomed && !isFlipped && (
              <>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label="Previous"
                  className="absolute left-0 top-1/2 z-40 h-20 w-12 -translate-y-1/2 rounded-none bg-black/30 opacity-0 hover:bg-black/50 [@media(hover:none)]:hidden hover:opacity-100 md:group-hover:opacity-70"
                  onClick={() => emblaApi?.scrollPrev()}
                >
                  <ChevronLeft className="h-6 w-6" />
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label="Next"
                  className="absolute right-0 top-1/2 z-40 h-20 w-12 -translate-y-1/2 rounded-none bg-black/30 opacity-0 hover:bg-black/50 [@media(hover:none)]:hidden hover:opacity-100 md:group-hover:opacity-70"
                  onClick={() => emblaApi?.scrollNext()}
                >
                  <ChevronRight className="h-6 w-6" />
                </Button>
              </>
            )}

            {/* Flip hint: not with the tag row (its height varies and the hint landed on the lozenges) */}
            {!isFlipped && !zoomed && showHint && !showTags && group.length < 2 && (
              <div className="pointer-events-none absolute bottom-4 left-1/2 flex -translate-x-1/2 flex-col items-center text-xs text-white/80 md:hidden short:hidden">
                <ChevronUp className="mb-1 h-4 w-4 animate-bounce" />
                <span>Info</span>
              </div>
            )}
          </div>

          {/* Info: what the picture (or clip) is, how it was made, its movement (ImageInfo.tsx) */}
          {isFlipped && currentImage && (
            <ImageInfo
              key={currentImage.id}
              image={currentImage}
              bucket={bucketOf(currentImage)}
              placeName={destinations.find((d) => d.id === bucketOf(currentImage))?.name ?? null}
              onClose={() => setIsFlipped(false)}
              onWatch={(i = 0) => {
                const at = images.findIndex((x) => x.id === currentImage.metadata?.clips?.[i]?.filename);
                setIsFlipped(false);
                if (at >= 0) {
                  setPlayId(images[at].id);
                  goto(at);
                }
              }}
              publishTargets={(nonHeadlessDestinations || []).map((d) => ({ id: d.id, name: d.name || d.id }))}
              onOpenReferences={(items, i) => {
                setIsFlipped(false);
                open(items, i, 'Reference pictures');
              }}
            />
          )}

          {/* Below the picture: tags and buttons. On a phone on its side they lie over the picture instead, and a tap
              on the picture (or zooming in) hides them. Elsewhere they stay put: the picture's area never changes size
              under a zoom. */}
          <div
            className={`z-40 w-full shrink-0 pb-[env(safe-area-inset-bottom)] transition-opacity short:absolute short:inset-x-0 short:bottom-0 short:bg-gradient-to-t short:from-black/80 short:via-black/50 short:to-transparent short:pt-6 ${
              overlayHidden ? 'short:pointer-events-none short:opacity-0' : ''
            }`}
          >
            {!isFlipped && group.length > 1 && (
              <div className="hidden justify-center px-2 short:flex">
                <ClipStrip group={group} currentIndex={currentIndex} onPick={pickInGroup} className="flex" />
              </div>
            )}

            {/* Tag lozenges, just above the action bar: visible without flipping, phone and desktop (owner, 27 Oct) */}
            {showTags && (
              <div className="flex w-full justify-center px-4 pt-2">
                <TagLozenges
                  key={`${tagBucket}/${tagItem.id}`}
                  variant="dark"
                  compact
                  className="justify-center"
                  bucket={tagBucket as string}
                  filename={tagItem.id}
                  tags={tagMeta.tags}
                  tagging={tagMeta.tagging}
                  favorite={!!tagItem.isFavourite}
                  isVideo={tagItem.mediaType === 'video'}
                  onTagsChange={(patch) => {
                    tagItem.metadata = { ...(tagItem.metadata || {}), ...patch };
                    bumpItems();
                  }}
                  onFavoriteChange={(fav) => {
                    tagItem.isFavourite = fav;
                    bumpItems();
                  }}
                />
              </div>
            )}

            <div className="flex w-full flex-wrap justify-center gap-1 border-t border-white/10 px-4 py-2 short:border-0 short:py-1">
              <Button
                variant="ghost"
                size="sm"
                className={`${barButton} ${isFlipped ? 'bg-white/10 text-white' : ''}`}
                onClick={() => {
                  setIsFlipped(!isFlipped);
                  setShowHint(false);
                }}
              >
                <Info className="h-4 w-4" />
                <span className="hidden sm:inline">Info</span>
              </Button>

              {showFavoriteButton && (
                <Button variant="ghost" size="sm" className={barButton} onClick={() => handleToggleFavorite(tagItem)}>
                  <Star className={`h-4 w-4 ${tagItem?.isFavourite ? 'fill-yellow-400' : ''}`} />
                  <span className="hidden sm:inline">Favorite</span>
                </Button>
              )}

              <Button variant="ghost" size="sm" className={barButton} onClick={() => handleUseAsPrompt(currentImage)}>
                <RefreshCcw className="h-4 w-4" />
                <span className="hidden sm:inline">Use as prompt</span>
              </Button>

              <Button variant="ghost" size="sm" className={barButton} onClick={() => copyToClipboard(imageSrc)}>
                <Clipboard className="h-4 w-4" />
                <span className="hidden sm:inline">Copy URL</span>
              </Button>

              <Button variant="ghost" size="sm" className={barButton} onClick={() => window.open(imageSrc, '_blank')}>
                <Download className="h-4 w-4" />
                <span className="hidden sm:inline">Download</span>
              </Button>

              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" size="sm" className={barButton}>
                    <MoreHorizontal className="h-4 w-4" />
                    <span className="hidden sm:inline">More</span>
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent>
                  {/* the same items as the picture's menu everywhere else (ImageMenuItems), less the bar's own buttons */}
                  <ImageMenuItems
                    image={currentImage}
                    bucket={bucketOf(currentImage)}
                    except={['info', 'useAsPrompt', 'fullScreen', 'rawUrl']}
                    onStartAgain={handleStartAgain}
                    onPublish={handlePublish}
                    onCopyTo={handleCopyTo}
                    onMoveTo={handleMoveTo}
                    publishDestinations={(destinations || []).map((d) => ({ id: d.id, name: d.name || d.id, headless: !(nonHeadlessDestinations || []).some((n) => n.id === d.id) }))}
                    onDelete={handleDelete}
                  />
                  {clipOf && bucketOf(currentImage) && (
                    <DropdownMenuItem
                      onClick={() =>
                        bucketsApi.detachClip(bucketOf(currentImage) as string, currentImage.id).then(
                          () => toast.success('The clip is now an item of its own'),
                          (err) => toast.error(String(err?.message || err)),
                        )
                      }
                    >
                      <Scissors className="mr-2 h-4 w-4" />
                      <span>Separate from the picture</span>
                    </DropdownMenuItem>
                  )}
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          </div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
};

const noop = () => {};

export default LoopeModal;
