
import React, { useRef, useState, useEffect } from 'react';
import { useIsMobile } from '@/hooks/use-mobile';

interface DetailViewTouchHandlerProps {
  children: React.ReactNode;
  onSwipeLeft: () => void;
  onSwipeRight: () => void;
}

const DetailViewTouchHandler: React.FC<DetailViewTouchHandlerProps> = ({
  children,
  onSwipeLeft,
  onSwipeRight
}) => {
  const touchRef = useRef<HTMLDivElement>(null);
  const [startX, setStartX] = useState<number | null>(null);
  const isMobile = useIsMobile();
  
  const handleTouchStart = (e: React.TouchEvent) => {
    setStartX(e.touches[0].clientX);
  };

  const handleTouchEnd = (e: React.TouchEvent) => {
    if (startX === null) return;
    
    const endX = e.changedTouches[0].clientX;
    const diff = startX - endX;
    
    // Reduce threshold to make swiping more responsive (from 50px to 20px)
    if (Math.abs(diff) > 20) {
      if (diff > 0) {
        // Swipe left, go to next image
        onSwipeLeft();
      } else {
        // Swipe right, go to previous image
        onSwipeRight();
      }
    }
    
    setStartX(null);
  };

  // Prevent scrolling when in this view on mobile devices
  useEffect(() => {
    if (!isMobile) return;
    
    const preventScroll = (e: Event) => {
      e.preventDefault();
    };
    
    const element = touchRef.current;
    if (element) {
      element.addEventListener('touchmove', preventScroll, { passive: false });
    }
    
    return () => {
      if (element) {
        element.removeEventListener('touchmove', preventScroll);
      }
    };
  }, [isMobile]);

  return (
    <div 
      ref={touchRef}
      className="w-full h-full"
      onTouchStart={handleTouchStart}
      onTouchEnd={handleTouchEnd}
    >
      {children}
    </div>
  );
};

export default DetailViewTouchHandler;
