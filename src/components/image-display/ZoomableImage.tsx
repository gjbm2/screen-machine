
import React, { useState, useRef, useEffect } from 'react';

interface ZoomableImageProps {
  src: string;
  alt: string;
  onLoad?: (e: React.SyntheticEvent<HTMLImageElement>) => void;
  onClick?: (e: React.MouseEvent) => void;
}

const ZoomableImage: React.FC<ZoomableImageProps> = ({
  src,
  alt,
  onLoad,
  onClick
}) => {
  const [scale, setScale] = useState(1);
  const [panning, setPanning] = useState(false);
  const [position, setPosition] = useState({ x: 0, y: 0 });
  const [startPosition, setStartPosition] = useState({ x: 0, y: 0 });
  const containerRef = useRef<HTMLDivElement>(null);
  
  // Reset zoom and position when image changes
  useEffect(() => {
    setScale(1);
    setPosition({ x: 0, y: 0 });
  }, [src]);

  // Handle touch events for pinch zoom
  const handleTouchStart = (e: React.TouchEvent) => {
    if (e.touches.length === 2) {
      // Store initial touch positions for pinch-to-zoom
      e.preventDefault();
      const touch1 = e.touches[0];
      const touch2 = e.touches[1];
      const distance = Math.hypot(
        touch2.clientX - touch1.clientX,
        touch2.clientY - touch1.clientY
      );
      
      setStartPosition({ 
        x: distance, 
        y: 0 // y is not used for pinch zoom
      });
    } else if (e.touches.length === 1 && scale > 1) {
      // Start panning with a single touch when zoomed in
      setPanning(true);
      setStartPosition({
        x: e.touches[0].clientX - position.x,
        y: e.touches[0].clientY - position.y
      });
    }
  };

  const handleTouchMove = (e: React.TouchEvent) => {
    if (e.touches.length === 2) {
      // Handle pinch zoom
      e.preventDefault();
      const touch1 = e.touches[0];
      const touch2 = e.touches[1];
      const distance = Math.hypot(
        touch2.clientX - touch1.clientX,
        touch2.clientY - touch1.clientY
      );
      
      // Calculate new scale based on pinch distance
      const initialDistance = startPosition.x;
      if (initialDistance > 0) {
        const newScale = Math.max(1, Math.min(4, scale * (distance / initialDistance)));
        setStartPosition({ x: distance, y: 0 });
        setScale(newScale);
      }
    } else if (e.touches.length === 1 && panning && scale > 1) {
      // Handle panning when zoomed in
      e.preventDefault();
      const newX = e.touches[0].clientX - startPosition.x;
      const newY = e.touches[0].clientY - startPosition.y;
      
      // Limit panning to prevent image from going completely off-screen
      const maxX = (scale - 1) * 150; // Approximate constraints
      const maxY = (scale - 1) * 150;
      
      setPosition({
        x: Math.min(maxX, Math.max(-maxX, newX)),
        y: Math.min(maxY, Math.max(-maxY, newY))
      });
    }
  };

  const handleTouchEnd = (e: React.TouchEvent) => {
    // If we end with a single finger and aren't zoomed, treat it as a tap/click
    if (e.touches.length === 0 && scale === 1 && onClick && !panning) {
      onClick(e as unknown as React.MouseEvent);
    }
    
    setPanning(false);
    
    // If we're zoomed out to near 1, reset completely
    if (scale < 1.1) {
      setScale(1);
      setPosition({ x: 0, y: 0 });
    }
  };

  // Double-tap to reset zoom
  const handleDoubleTap = (e: React.MouseEvent) => {
    if (scale > 1) {
      setScale(1);
      setPosition({ x: 0, y: 0 });
    } else {
      // Zoom in on double tap location
      setScale(2);
      
      if (containerRef.current) {
        const rect = containerRef.current.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const y = e.clientY - rect.top;
        
        setPosition({
          x: (rect.width / 2 - x) * 0.5,
          y: (rect.height / 2 - y) * 0.5
        });
      }
    }
    
    e.stopPropagation();
  };

  return (
    <div
      ref={containerRef}
      className="w-full h-full flex items-center justify-center overflow-hidden"
      onTouchStart={handleTouchStart}
      onTouchMove={handleTouchMove}
      onTouchEnd={handleTouchEnd}
      onDoubleClick={handleDoubleTap}
      style={{ touchAction: scale > 1 ? 'none' : 'pan-y pinch-zoom' }}
    >
      <img
        src={src}
        alt={alt}
        onLoad={onLoad}
        style={{
          transform: `scale(${scale}) translate(${position.x / scale}px, ${position.y / scale}px)`,
          transformOrigin: 'center',
          transition: panning ? 'none' : 'transform 0.05s ease-out',
          maxWidth: '100%',
          maxHeight: '100%',
          objectFit: 'contain',
          userSelect: 'none',
          pointerEvents: 'none'
        }}
        draggable={false}
      />
    </div>
  );
};

export default ZoomableImage;
