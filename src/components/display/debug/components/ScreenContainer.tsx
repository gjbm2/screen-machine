
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
    
    // Get parent dimensions - this is the available space we have
    const parentEl = contentRef.current;
    const parentRect = parentEl.getBoundingClientRect();
    const parentWidth = parentRect.width;
    const parentHeight = parentRect.height;
    
    console.log('[ScreenContainer] Parent dimensions:', {
      parentWidth, parentHeight, 
      parentOffsetWidth: parentEl.offsetWidth, 
      parentOffsetHeight: parentEl.offsetHeight
    });
    
    // Calculate aspect ratio of the selected size
    const aspectRatio = selectedSizeObj.width / selectedSizeObj.height;
    
    // Always leave some margin around all sides
    const horizontalMargin = 30;
    const verticalMargin = 30;
    
    // Maximum available space
    const maxAvailableWidth = parentWidth - horizontalMargin;
    const maxAvailableHeight = parentHeight - verticalMargin;
    
    // Determine how to scale the content to fit
    let width, height;
    
    // Check if we need to scale by width or height
    if (aspectRatio > maxAvailableWidth / maxAvailableHeight) {
      // Width-constrained
      width = maxAvailableWidth;
      height = width / aspectRatio;
    } else {
      // Height-constrained
      height = maxAvailableHeight;
      width = height * aspectRatio;
    }
    
    // Final safety check to ensure we're always within bounds
    if (width > maxAvailableWidth) {
      width = maxAvailableWidth;
      height = width / aspectRatio;
    }
    
    if (height > maxAvailableHeight) {
      height = maxAvailableHeight;
      width = height * aspectRatio;
    }
    
    console.log('[ScreenContainer] Calculated dimensions:', {
      maxAvailableWidth, maxAvailableHeight,
      calculatedWidth: width, calculatedHeight: height,
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
