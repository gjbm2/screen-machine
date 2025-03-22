
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
  const [leftHovered, setLeftHovered] = useState(false);
  const [rightHovered, setRightHovered] = useState(false);

  const sizeClasses = {
    small: 'h-2 w-2',
    medium: 'h-3 w-3',
    large: 'h-4 w-4'  // Reduced from h-5 w-5
  };

  const buttonSizeClasses = {
    small: 'p-1.5',
    medium: 'p-2',
    large: 'p-3'  // Reduced from p-4
  };

  // Wider clickable areas for fullscreen view
  const widthClasses = {
    small: 'w-1/6',
    medium: 'w-1/5',
    large: 'w-1/4'  // Keep large clickable area
  };

  return (
    <div className="absolute inset-0 pointer-events-none">
      <button 
        className={`absolute left-0 top-0 bottom-0 ${widthClasses[size]} flex items-center justify-start pl-4 bg-transparent transition-colors pointer-events-auto z-20`}
        onClick={onPrevious}
        aria-label="Previous image"
        onMouseEnter={() => setLeftHovered(true)}
        onMouseLeave={() => setLeftHovered(false)}
      >
        <div className={`${leftHovered ? 'bg-black/60' : 'bg-black/30'} rounded-full ${buttonSizeClasses[size]} text-white/90 hover:text-white transition-colors`}>
          <ChevronLeft className={sizeClasses[size]} />
        </div>
      </button>
      <button 
        className={`absolute right-0 top-0 bottom-0 ${widthClasses[size]} flex items-center justify-end pr-4 bg-transparent transition-colors pointer-events-auto z-20`}
        onClick={onNext}
        aria-label="Next image"
        onMouseEnter={() => setRightHovered(true)}
        onMouseLeave={() => setRightHovered(false)}
      >
        <div className={`${rightHovered ? 'bg-black/60' : 'bg-black/30'} rounded-full ${buttonSizeClasses[size]} text-white/90 hover:text-white transition-colors`}>
          <ChevronRight className={sizeClasses[size]} />
        </div>
      </button>
    </div>
  );
};

export default NavigationControls;
