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

export const LoopeViewProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [isOpen, setIsOpen] = useState(false);
  const [images, setImages] = useState<ImageItem[]>([]);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [title, setTitle] = useState<string>('');
  const [carouselOptions, setCarouselOptions] = useState<CarouselOptions>({ loop: true });

  const open = useCallback((imgs: ImageItem[], startIdx = 0, ttl = '', options: CarouselOptions = { loop: true }) => {
    setImages(imgs);
    setCurrentIndex(startIdx);
    setTitle(ttl);
    setCarouselOptions(options);
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