
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
    
    // Reduce threshold even further for more responsive swipes (from 20px to 15px)
    if (Math.abs(diff) > 15) {
      if (diff > 0) {
        // Swipe left, go to next image
        console.log("Swipe detected: LEFT");
        onSwipeLeft();
      } else {
        // Swipe right, go to previous image
        console.log("Swipe detected: RIGHT");
        onSwipeRight();
      }
    }
    
    setStartX(null);
  };

  // Prevent scrolling when in this view on mobile devices
  useEffect(() => {
    if (!isMobile) return;
    
    const preventScroll = (e: TouchEvent) => {
      // Only prevent default if it's a horizontal swipe
      if (startX !== null) {
        const touch = e.touches[0];
        const currentX = touch.clientX;
        const diff = Math.abs(startX - currentX);
        
        // If horizontal movement is significant, prevent the scroll
        if (diff > 10) {
          e.preventDefault();
        }
      }
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
  }, [isMobile, startX]);

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
