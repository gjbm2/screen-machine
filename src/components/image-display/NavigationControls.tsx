
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
    large: 'h-5 w-5'
  };

  const buttonSizeClasses = {
    small: 'p-1.5',
    medium: 'p-2',
    large: 'p-4'
  };

  return (
    <div className="absolute inset-0 pointer-events-none">
      <button 
        className={`absolute left-0 top-0 bottom-0 w-1/5 flex items-center justify-start pl-4 bg-transparent hover:bg-black/10 transition-colors pointer-events-auto z-20`}
        onClick={onPrevious}
        aria-label="Previous image"
      >
        <div className={`bg-black/40 hover:bg-black/60 rounded-full ${buttonSizeClasses[size]} text-white/90 hover:text-white transition-colors`}>
          <ChevronLeft className={sizeClasses[size]} />
        </div>
      </button>
      <button 
        className={`absolute right-0 top-0 bottom-0 w-1/5 flex items-center justify-end pr-4 bg-transparent hover:bg-black/10 transition-colors pointer-events-auto z-20`}
        onClick={onNext}
        aria-label="Next image"
      >
        <div className={`bg-black/40 hover:bg-black/60 rounded-full ${buttonSizeClasses[size]} text-white/90 hover:text-white transition-colors`}>
          <ChevronRight className={sizeClasses[size]} />
        </div>
      </button>
    </div>
  );
};

export default NavigationControls;
