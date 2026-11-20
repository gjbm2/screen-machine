import React, { createContext, useContext, useState, useCallback } from 'react';
import { ImageItem } from '@/types/image-types';
import LoopeModal from '@/components/loope/LoopeModal';

// Add interface for carousel options
interface CarouselOptions {
  loop?: boolean;
  [key: string]: any;
}

interface LoopeViewContextValue {
  isOpen: boolean;
  images: ImageItem[];
  currentIndex: number;
  title: string;
  carouselOptions: CarouselOptions;
  open: (images: ImageItem[], startIndex?: number, title?: string, options?: CarouselOptions) => void;
  close: () => void;
  goto: (index: number) => void;
  next: () => void;
  prev: () => void;
}

const LoopeViewContext = createContext<LoopeViewContextValue | undefined>(undefined);

/** Enlarged, a picture and its clips come apart (owner, 19 Nov): each clip is a slide of its own right after its
 *  picture, so it can be watched, published, moved or deleted by itself. The grids keep them stacked. */
function ungroup(imgs: ImageItem[], startIdx: number): { items: ImageItem[]; start: number } {
  const items: ImageItem[] = [];
  let start = 0;
  imgs.forEach((img, i) => {
    if (i === startIdx) start = items.length;
    items.push(img);
    const clips = Array.isArray(img.metadata?.clips) ? img.metadata!.clips : [];
    clips.forEach((c: { filename: string; url: string; thumbnail_url?: string; metadata?: Record<string, any> }) => {
      items.push({
        id: c.filename,
        uniqueKey: `${img.id}::${c.filename}`,
        urlFull: c.url,
        raw_url: c.url,
        urlThumb: c.thumbnail_url,
        promptKey: (c.metadata?.prompt as string) || '',
        seed: 0,
        createdAt: img.createdAt,
        isFavourite: false,
        disableFavorite: true,
        mediaType: 'video',
        bucketId: img.bucketId,
        metadata: { ...(c.metadata || {}), still: c.metadata?.still || img.id },
      });
    });
  });
  return { items, start };
}

/** A tile's Info button opens the Loope the usual way (its view's own open), straight on the Info panel. Only an
 *  open that follows at once counts: a view whose tile click doesn't open the Loope must not leave it set. */
let infoOnNextOpen = 0;
export function openNextWithInfo(): void {
  infoOnNextOpen = Date.now();
}

/** A tile's clip badge opens the Loope the usual way, on the picture's first clip. */
let clipOnNextOpen = 0;
export function openNextAtClip(): void {
  clipOnNextOpen = Date.now();
}

export const LoopeViewProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [isOpen, setIsOpen] = useState(false);
  const [images, setImages] = useState<ImageItem[]>([]);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [title, setTitle] = useState<string>('');
  const [carouselOptions, setCarouselOptions] = useState<CarouselOptions>({ loop: true });

  const open = useCallback((imgs: ImageItem[], startIdx = 0, ttl = '', options: CarouselOptions = { loop: true }) => {
    const { items, start } = ungroup(imgs, startIdx);
    const atClip = Date.now() - clipOnNextOpen < 1000 && items[start + 1]?.metadata?.still === items[start]?.id;
    clipOnNextOpen = 0;
    setImages(items);
    setCurrentIndex(atClip ? start + 1 : start);
    setTitle(ttl);
    setCarouselOptions({ ...options, showInfo: options.showInfo ?? Date.now() - infoOnNextOpen < 1000,
                         playId: atClip ? items[start + 1].id : ((options.playId as string | undefined) ?? null) });
    infoOnNextOpen = 0;
    setIsOpen(true);
  }, []);

  const close = useCallback(() => setIsOpen(false), []);

  const goto = useCallback((idx: number) => {
    setCurrentIndex((old) => {
      const clamped = Math.max(0, Math.min(idx, images.length - 1));
      return clamped;
    });
  }, [images.length]);

  const next = useCallback(() => {
    setCurrentIndex((idx) => (idx + 1) % images.length);
  }, [images.length]);

  const prev = useCallback(() => {
    setCurrentIndex((idx) => (idx - 1 + images.length) % images.length);
  }, [images.length]);

  const value: LoopeViewContextValue = {
    isOpen,
    images,
    currentIndex,
    title,
    carouselOptions,
    open,
    close,
    goto,
    next,
    prev,
  };

  return (
    <LoopeViewContext.Provider value={value}>
      {children}
      {/* Render the modal at root level */}
      <LoopeModal title={title} />
    </LoopeViewContext.Provider>
  );
};

export const useLoopeView = () => {
  const ctx = useContext(LoopeViewContext);
  if (!ctx) {
    throw new Error('useLoopeView must be used within a LoopeViewProvider');
  }
  return ctx;
}; 