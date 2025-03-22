
import React from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { Button } from '@/components/ui/button';

interface NavigationControlsProps {
  onPrevious: (e: React.MouseEvent) => void;
  onNext: (e: React.MouseEvent) => void;
  size?: 'small' | 'medium' | 'large';
  currentGlobalIndex?: number;
  allImages?: any[];
  showPrevButton?: boolean;
  showNextButton?: boolean;
}

const NavigationControls: React.FC<NavigationControlsProps> = ({
  onPrevious,
  onNext,
  size = 'medium',
  currentGlobalIndex,
  allImages,
  showPrevButton = true,
  showNextButton = true
}) => {
  // Size mappings for hotspot areas and navigation buttons
  const sizeClasses = {
    small: {
      hotspot: 'w-12 h-full', // Full height of container
      button: 'h-12 w-6',
      icon: 'h-3 w-3'
    },
    medium: {
      hotspot: 'w-14 h-full', // Full height of container
      button: 'h-12 w-8',
      icon: 'h-5 w-5'
    },
    large: {
      hotspot: 'w-16 h-full', // Full height of container
      button: 'h-16 w-10',
      icon: 'h-6 w-6'
    }
  };

  // Get the size classes based on the size prop
  const { hotspot, button, icon } = sizeClasses[size];

  return (
    <>
      {/* Previous button - attached to left edge */}
      {showPrevButton && (
        <div 
          className={`absolute left-0 top-0 ${hotspot} flex items-center justify-start cursor-pointer z-20`}
          onClick={onPrevious}
          onMouseDown={(e) => e.preventDefault()}
        >
          <Button
            size="icon"
            variant="ghost"
            className={`${button} rounded-r-md rounded-l-none bg-black/50 hover:bg-black/70 transition-opacity opacity-80 hover:opacity-100 pointer-events-none text-white`}
            tabIndex={-1}
          >
            <ChevronLeft className={icon} />
          </Button>
        </div>
      )}
      
      {/* Next button - attached to right edge */}
      {showNextButton && (
        <div 
          className={`absolute right-0 top-0 ${hotspot} flex items-center justify-end cursor-pointer z-20`}
          onClick={onNext}
          onMouseDown={(e) => e.preventDefault()}
        >
          <Button
            size="icon"
            variant="ghost"
            className={`${button} rounded-l-md rounded-r-none bg-black/50 hover:bg-black/70 transition-opacity opacity-80 hover:opacity-100 pointer-events-none text-white`}
            tabIndex={-1}
          >
            <ChevronRight className={icon} />
          </Button>
        </div>
      )}
    </>
  );
};

export default NavigationControls;
