
import React, { useEffect } from 'react';
import { SCREEN_SIZES } from '../ScreenSizeSelector';

interface ScreenContainerProps {
  selectedSize: string;
  contentRef: React.RefObject<HTMLDivElement>;
  containerWidth: number;
  backgroundColor: string;
  children: React.ReactNode;
  onDimensionsChange: (dimensions: { width: number; height: number }) => void;
}

export const ScreenContainer: React.FC<ScreenContainerProps> = ({
  selectedSize,
  contentRef,
  containerWidth,
  backgroundColor,
  children,
  onDimensionsChange
}) => {
  const screenContainerRef = React.useRef<HTMLDivElement>(null);
  
  // Apply selected screen size to container and handle proper scaling
  useEffect(() => {
    if (!screenContainerRef.current || !contentRef.current) return;
    
    const selectedSizeObj = SCREEN_SIZES.find(size => size.name === selectedSize);
    if (!selectedSizeObj) {
      console.error('[ScreenContainer] Size not found:', selectedSize);
      return;
    }
    
    // Get parent dimensions
    const parentWidth = contentRef.current.clientWidth;
    const parentHeight = contentRef.current.clientHeight;
    
    // Calculate aspect ratio of the selected size
    const aspectRatio = selectedSizeObj.width / selectedSizeObj.height;
    
    // Determine max dimensions that fit within the parent
    // Add extra padding to ensure we don't get too close to the edges
    let maxWidth = parentWidth - 40; // Padding
    let maxHeight = parentHeight - 40; // Padding
    
    // Calculate dimensions based on aspect ratio while ensuring they fit in the viewport
    let width, height;
    
    // Calculate which dimension is the limiting factor
    if (maxWidth / aspectRatio <= maxHeight) {
      width = Math.min(maxWidth, window.innerWidth * 0.9);
      height = width / aspectRatio;
    } else {
      height = Math.min(maxHeight, window.innerHeight * 0.9);
      width = height * aspectRatio;
    }
    
    // Apply a maximum size constraint to prevent exceeding viewport
    width = Math.min(width, window.innerWidth * 0.9);
    height = Math.min(height, window.innerHeight * 0.9);
    
    // Set dimensions on the container
    screenContainerRef.current.style.width = `${width}px`;
    screenContainerRef.current.style.height = `${height}px`;
    
    onDimensionsChange({ width, height });
  }, [selectedSize, contentRef, containerWidth, onDimensionsChange]);

  return (
    <div 
      ref={screenContainerRef}
      className="relative border border-gray-300 shadow-md flex items-center justify-center overflow-hidden"
      style={{ 
        backgroundColor: `#${backgroundColor}`, 
        transition: "width 0.3s, height 0.3s"
      }}
    >
      {children}
    </div>
  );
};
