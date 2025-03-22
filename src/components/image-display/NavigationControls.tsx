import React, { useState } from 'react';
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
  const [hoveredSide, setHoveredSide] = useState<'left' | 'right' | null>(null);

  // Reduced size for the icons but keep button size the same
  const sizeClasses = {
    small: 'h-2 w-2',
    medium: 'h-2.5 w-2.5',
    large: 'h-3 w-3'  // Reduced from h-5 w-5
  };

  const buttonSizeClasses = {
    small: 'p-1.5',
    medium: 'p-1.5', // Reduced padding for medium
    large: 'p-2'     // Reduced from p-4
  };

  // Keep wider clickable areas for fullscreen view
  const widthClasses = {
    small: 'w-1/6',
    medium: 'w-1/5',
    large: 'w-1/4'  // Larger clickable area for large size
  };

  return (
    <div className="absolute inset-0 pointer-events-none">
      <button 
        className={`absolute left-0 top-0 bottom-0 ${widthClasses[size]} flex items-center justify-start pl-4 bg-transparent transition-colors pointer-events-auto z-20`}
        onClick={onPrevious}
        aria-label="Previous image"
        onMouseEnter={() => setHoveredSide('left')}
        onMouseLeave={() => setHoveredSide(null)}
      >
        <div className={`${hoveredSide === 'left' ? 'bg-black/60' : 'bg-black/30'} rounded-full ${buttonSizeClasses[size]} text-white/90 transition-colors`}>
          <ChevronLeft className={sizeClasses[size]} />
        </div>
      </button>
      <button 
        className={`absolute right-0 top-0 bottom-0 ${widthClasses[size]} flex items-center justify-end pr-4 bg-transparent transition-colors pointer-events-auto z-20`}
        onClick={onNext}
        aria-label="Next image"
        onMouseEnter={() => setHoveredSide('right')}
        onMouseLeave={() => setHoveredSide(null)}
      >
        <div className={`${hoveredSide === 'right' ? 'bg-black/60' : 'bg-black/30'} rounded-full ${buttonSizeClasses[size]} text-white/90 transition-colors`}>
          <ChevronRight className={sizeClasses[size]} />
        </div>
      </button>
    </div>
  );
};

export default NavigationControls;
