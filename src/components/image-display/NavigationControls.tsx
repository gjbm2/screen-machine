
import React from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';

interface NavigationControlsProps {
  onPrevious: (e: React.MouseEvent) => void;
  onNext: (e: React.MouseEvent) => void;
  size?: 'small' | 'medium' | 'large';
  activeIndex?: number;
  totalImages?: number;
  isNavigatingAllImages?: boolean;
  onNavigateGlobal?: (imageIndex: number) => void;
  currentGlobalIndex?: number;
  allImages?: Array<any>;
}

const NavigationControls: React.FC<NavigationControlsProps> = ({ 
  onPrevious, 
  onNext,
  size = 'medium',
  // The following props are not used directly in this component,
  // but are included in the type definition for compatibility
  activeIndex,
  totalImages,
  isNavigatingAllImages,
  onNavigateGlobal,
  currentGlobalIndex,
  allImages
}) => {
  const sizeClasses = {
    small: 'h-2 w-2',
    medium: 'h-3 w-3',
    large: 'h-4 w-4'
  };

  return (
    <div className="absolute inset-0 pointer-events-none">
      <button 
        className="absolute left-2 top-1/2 -translate-y-1/2 bg-black/20 hover:bg-black/50 rounded-full p-1.5 text-white/70 hover:text-white transition-colors pointer-events-auto z-20"
        onClick={onPrevious}
        aria-label="Previous image"
      >
        <ChevronLeft className={sizeClasses[size]} />
      </button>
      <button 
        className="absolute right-2 top-1/2 -translate-y-1/2 bg-black/20 hover:bg-black/50 rounded-full p-1.5 text-white/70 hover:text-white transition-colors pointer-events-auto z-20"
        onClick={onNext}
        aria-label="Next image"
      >
        <ChevronRight className={sizeClasses[size]} />
      </button>
    </div>
  );
};

export default NavigationControls;
