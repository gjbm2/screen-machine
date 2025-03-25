
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
    
    // Apply maximum bounds to ensure the container fits within the viewport
    // with extra padding to prevent touching the edges
    const maxWidth = Math.min(parentWidth - 40, window.innerWidth * 0.8);
    const maxHeight = Math.min(parentHeight - 40, window.innerHeight * 0.8);
    
    // Calculate dimensions that preserve aspect ratio and fit within bounds
    let width, height;
    
    if (maxWidth / aspectRatio <= maxHeight) {
      // Width is the limiting factor
      width = maxWidth;
      height = width / aspectRatio;
    } else {
      // Height is the limiting factor
      height = maxHeight;
      width = height * aspectRatio;
    }
    
    console.log('[ScreenContainer] Calculated dimensions:', {
      parentWidth, parentHeight, maxWidth, maxHeight,
      finalWidth: width, finalHeight: height,
      selectedSize, aspectRatio
    });
    
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
