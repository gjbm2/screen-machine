
import React, { useRef, useState, useEffect } from 'react';

interface DetailViewTouchHandlerProps {
  children: React.ReactNode;
  onSwipeLeft: () => void;
  onSwipeRight: () => void;
  allowZoom?: boolean;
}

const DetailViewTouchHandler: React.FC<DetailViewTouchHandlerProps> = ({
  children,
  onSwipeLeft,
  onSwipeRight,
  allowZoom = true
}) => {
  const touchRef = useRef<HTMLDivElement>(null);
  const [startX, setStartX] = useState<number | null>(null);
  const [startY, setStartY] = useState<number | null>(null);
  const [isSwiping, setIsSwiping] = useState(false);
  
  const handleTouchStart = (e: React.TouchEvent) => {
    // Only track single-touch events for swiping
    if (e.touches.length === 1) {
      setStartX(e.touches[0].clientX);
      setStartY(e.touches[0].clientY);
      setIsSwiping(false);
    }
  };

  const handleTouchMove = (e: React.TouchEvent) => {
    // Skip handling multi-touch events (let them be used for zoom)
    if (e.touches.length !== 1 || startX === null || startY === null) return;
    
    const currentX = e.touches[0].clientX;
    const currentY = e.touches[0].clientY;
    const diffX = startX - currentX;
    const diffY = startY - currentY;
    
    // If horizontal movement is greater than vertical and exceeds threshold,
    // consider this a swipe and prevent default to avoid page scrolling
    if (Math.abs(diffX) > Math.abs(diffY) && Math.abs(diffX) > 10) {
      e.preventDefault();
      setIsSwiping(true);
    }
  };

  const handleTouchEnd = (e: React.TouchEvent) => {
    if (startX === null || !isSwiping) return;
    
    const endX = e.changedTouches[0].clientX;
    const diff = startX - endX;
    
    // Reduced threshold to make swiping more responsive
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
    setStartY(null);
    setIsSwiping(false);
  };

  // Only apply touch-action: none if we're allowing zoom
  // This allows pinch-zoom to work in the child components
  return (
    <div 
      ref={touchRef}
      className="w-full h-full"
      onTouchStart={handleTouchStart}
      onTouchMove={handleTouchMove}
      onTouchEnd={handleTouchEnd}
      style={{ touchAction: allowZoom ? 'none' : 'pan-y' }}
    >
      {children}
    </div>
  );
};

export default DetailViewTouchHandler;
