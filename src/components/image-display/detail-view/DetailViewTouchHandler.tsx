
import React, { useRef, useState } from 'react';

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
  
  const handleTouchStart = (e: React.TouchEvent) => {
    setStartX(e.touches[0].clientX);
  };

  const handleTouchEnd = (e: React.TouchEvent) => {
    if (startX === null) return;
    
    const endX = e.changedTouches[0].clientX;
    const diff = startX - endX;
    
    // If swipe distance is sufficient (30px)
    if (Math.abs(diff) > 30) {
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

  return (
    <div 
      ref={touchRef}
      className="w-full"
      onTouchStart={handleTouchStart}
      onTouchEnd={handleTouchEnd}
    >
      {children}
    </div>
  );
};

export default DetailViewTouchHandler;
