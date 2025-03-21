
import React from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { Button } from '@/components/ui/button';

interface NavigationControlsProps {
  onPrevious?: (e: React.MouseEvent) => void;
  onNext?: (e: React.MouseEvent) => void;
  size?: 'small' | 'medium' | 'large';
  activeIndex?: number;
  totalImages?: number;
  onNavigatePrev?: (e: React.MouseEvent) => void;
  onNavigateNext?: (e: React.MouseEvent) => void;
  isNavigatingAllImages?: boolean;
  onNavigateGlobal?: (index: number) => void;
  currentGlobalIndex?: number;
  allImages?: any[];
}

const NavigationControls: React.FC<NavigationControlsProps> = ({ 
  onPrevious, 
  onNext,
  size = 'medium',
  activeIndex,
  totalImages,
  onNavigatePrev,
  onNavigateNext,
  isNavigatingAllImages = false,
  onNavigateGlobal,
  currentGlobalIndex,
  allImages
}) => {
  const sizeClasses = {
    small: 'h-2 w-2',
    medium: 'h-3 w-3',
    large: 'h-4 w-4'
  };

  // Use either the older style props or the newer style props
  const handlePrev = onNavigatePrev || onPrevious;
  const handleNext = onNavigateNext || onNext;

  return (
    <div className="absolute inset-0 pointer-events-none">
      {handlePrev && (
        <button 
          className="absolute left-2 top-1/2 -translate-y-1/2 bg-black/20 hover:bg-black/50 rounded-full p-1.5 text-white/70 hover:text-white transition-colors pointer-events-auto z-20"
          onClick={handlePrev}
          aria-label="Previous image"
        >
          <ChevronLeft className={sizeClasses[size]} />
        </button>
      )}
      
      {handleNext && (
        <button 
          className="absolute right-2 top-1/2 -translate-y-1/2 bg-black/20 hover:bg-black/50 rounded-full p-1.5 text-white/70 hover:text-white transition-colors pointer-events-auto z-20"
          onClick={handleNext}
          aria-label="Next image"
        >
          <ChevronRight className={sizeClasses[size]} />
        </button>
      )}
      
      {isNavigatingAllImages && allImages && allImages.length > 0 && currentGlobalIndex !== undefined && (
        <div className="absolute bottom-4 left-1/2 -translate-x-1/2 bg-black/50 px-3 py-1.5 rounded-full text-white text-sm pointer-events-auto">
          {currentGlobalIndex + 1} / {allImages.length}
        </div>
      )}
    </div>
  );
};

export default NavigationControls;
