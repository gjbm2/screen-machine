
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
    
    // Find the selected size from the predefined sizes
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
    
    // Enforce a more aggressive margin to ensure container fits properly
    const horizontalMargin = Math.max(parentWidth * 0.05, 20); // At least 5% of width or 20px
    const verticalMargin = Math.max(parentHeight * 0.05, 20);  // At least 5% of height or 20px
    
    // Maximum available space is now strictly enforced
    const maxAvailableWidth = parentWidth - horizontalMargin * 2;
    const maxAvailableHeight = parentHeight - verticalMargin * 2;
    
    // For very large screen sizes like 4K, we need to be much more aggressive with scaling
    let width, height;
    
    // Calculate scaling ratio based on the selected size and available space
    const widthRatio = maxAvailableWidth / selectedSizeObj.width;
    const heightRatio = maxAvailableHeight / selectedSizeObj.height;
    
    // Use the minimum ratio to ensure we stay within the parent container
    const ratio = Math.min(widthRatio, heightRatio, 1); // Cap at 1.0 to avoid overflow for any reason
    
    // Apply the scaling
    width = Math.min(selectedSizeObj.width * ratio, maxAvailableWidth);
    height = Math.min(selectedSizeObj.height * ratio, maxAvailableHeight);
    
    console.log('[ScreenContainer] Calculated dimensions:', {
      maxAvailableWidth, maxAvailableHeight,
      calculatedWidth: width, calculatedHeight: height,
      selectedSize, aspectRatio, ratio
    });
    
    // Double-check that the calculated dimensions fit within the available space
    // This is a safety measure to ensure we never exceed the parent container
    if (width > maxAvailableWidth) {
      width = maxAvailableWidth;
      height = width / aspectRatio;
    }
    
    if (height > maxAvailableHeight) {
      height = maxAvailableHeight;
      width = height * aspectRatio;
    }
    
    // Final safety check - ensure the container is never larger than the parent
    width = Math.min(width, parentWidth - 10);
    height = Math.min(height, parentHeight - 10);
    
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
