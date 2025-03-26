
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
    console.log('Touch start detected at X:', e.touches[0].clientX);
  };

  const handleTouchMove = (e: React.TouchEvent) => {
    // Used to capture the touchmove event, but we don't need to prevent default
    // This helps ensure the component captures all touch events
    if (startX !== null) {
      console.log('Touch move detected, delta:', e.touches[0].clientX - startX);
    }
  };

  const handleTouchEnd = (e: React.TouchEvent) => {
    if (startX === null) return;
    
    const endX = e.changedTouches[0].clientX;
    const diff = startX - endX;
    
    console.log('Touch end detected, total swipe distance:', Math.abs(diff));
    
    // Reduced threshold (20px) for more responsive swiping
    if (Math.abs(diff) > 20) {
      if (diff > 0) {
        // Swipe left, go to next image
        console.log('Detected left swipe, navigating next');
        onSwipeLeft();
      } else {
        // Swipe right, go to previous image
        console.log('Detected right swipe, navigating previous');
        onSwipeRight();
      }
    }
    
    setStartX(null);
  };

  return (
    <div 
      ref={touchRef}
      className="w-full h-full touch-pan-y"
      onTouchStart={handleTouchStart}
      onTouchMove={handleTouchMove}
      onTouchEnd={handleTouchEnd}
    >
      {children}
    </div>
  );
};

export default DetailViewTouchHandler;
