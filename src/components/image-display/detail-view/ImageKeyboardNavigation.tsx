
import React, { useEffect } from 'react';

interface ImageKeyboardNavigationProps {
  activeIndex: number;
  imagesLength: number;
  onNavigatePrev: (e: React.MouseEvent) => void;
  onNavigateNext: (e: React.MouseEvent) => void;
  allImages?: Array<any>;
  currentGlobalIndex?: number;
  onNavigateGlobal?: (imageIndex: number) => void;
}

const ImageKeyboardNavigation: React.FC<ImageKeyboardNavigationProps> = ({
  activeIndex,
  imagesLength,
  onNavigatePrev,
  onNavigateNext,
  allImages,
  currentGlobalIndex,
  onNavigateGlobal
}) => {
  // Handle keyboard navigation
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'ArrowLeft') {
        // Navigate to previous image
        if (onNavigateGlobal && allImages && currentGlobalIndex !== undefined) {
          if (currentGlobalIndex > 0) {
            onNavigateGlobal(currentGlobalIndex - 1);
          }
        } else if (activeIndex > 0) {
          onNavigatePrev(e as unknown as React.MouseEvent);
        }
      } else if (e.key === 'ArrowRight') {
        // Navigate to next image
        if (onNavigateGlobal && allImages && currentGlobalIndex !== undefined) {
          if (currentGlobalIndex < allImages.length - 1) {
            onNavigateGlobal(currentGlobalIndex + 1);
          }
        } else if (activeIndex < imagesLength - 1) {
          onNavigateNext(e as unknown as React.MouseEvent);
        }
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [activeIndex, imagesLength, onNavigateNext, onNavigatePrev, allImages, currentGlobalIndex, onNavigateGlobal]);

  return null; // This is a behavior-only component with no UI
};

export default ImageKeyboardNavigation;
