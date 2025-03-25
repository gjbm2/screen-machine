
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
      selectedSize, 
      selectedSizeWidth: selectedSizeObj.width,
      selectedSizeHeight: selectedSizeObj.height
    });
    
    // Calculate aspect ratio of the selected size
    const aspectRatio = selectedSizeObj.width / selectedSizeObj.height;
    
    // Always leave some margin around all sides - more for larger screens
    const horizontalMargin = Math.min(parentWidth * 0.1, 40); // 10% of width up to 40px
    const verticalMargin = Math.min(parentHeight * 0.1, 40);  // 10% of height up to 40px
    
    // Maximum available space
    const maxAvailableWidth = parentWidth - horizontalMargin * 2;
    const maxAvailableHeight = parentHeight - verticalMargin * 2;
    
    // Determine how to scale the content to fit
    let width, height;
    
    // We need to use the minimum ratio to ensure it fits entirely in both dimensions
    const widthRatio = maxAvailableWidth / selectedSizeObj.width;
    const heightRatio = maxAvailableHeight / selectedSizeObj.height;
    
    // Use the minimum ratio to ensure we're constrained by whichever dimension is more limiting
    const ratio = Math.min(widthRatio, heightRatio);
    
    width = selectedSizeObj.width * ratio;
    height = selectedSizeObj.height * ratio;
    
    console.log('[ScreenContainer] Calculated dimensions:', {
      maxAvailableWidth, maxAvailableHeight,
      calculatedWidth: width, calculatedHeight: height,
      selectedSize, aspectRatio, ratio
    });
    
    // Set dimensions on the container with a safety check
    if (width > maxAvailableWidth || height > maxAvailableHeight) {
      console.warn('[ScreenContainer] Calculated dimensions exceed available space, applying additional constraints');
      if (width > maxAvailableWidth) {
        width = maxAvailableWidth;
        height = width / aspectRatio;
      }
      if (height > maxAvailableHeight) {
        height = maxAvailableHeight;
        width = height * aspectRatio;
      }
    }
    
    // Apply the dimensions
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
