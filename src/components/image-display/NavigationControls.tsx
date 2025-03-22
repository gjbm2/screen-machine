
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
      hotspot: 'w-16 h-[100px]', // Wider hotspot (3x button width)
      button: 'h-full w-5',
      icon: 'h-3 w-3'
    },
    medium: {
      hotspot: 'w-16 h-[100px]', // Wider hotspot (3x button width)
      button: 'h-full w-5',
      icon: 'h-5 w-5' // Larger icon for better visibility
    },
    large: {
      hotspot: 'w-16 h-[100px]', // Wider hotspot (3x button width)
      button: 'h-full w-5',
      icon: 'h-5 w-5' // Larger icon for better visibility
    }
  };

  // Get the size classes based on the size prop
  const { hotspot, button, icon } = sizeClasses[size];

  return (
    <>
      {/* Previous button - conditionally rendered */}
      {showPrevButton && (
        <div 
          className={`absolute left-0 top-1/2 -translate-y-1/2 ${hotspot} flex items-center justify-start cursor-pointer z-10`}
          onClick={onPrevious} // Apply the click handler to the entire hotspot div
          onMouseDown={(e) => e.preventDefault()} // Prevent text selection
        >
          <Button
            size="icon"
            variant="ghost"
            className={`${button} rounded-r-md rounded-l-none bg-black/50 hover:bg-black/70 transition-opacity opacity-80 group-hover:opacity-100 pointer-events-none text-white`}
            tabIndex={-1}
          >
            <ChevronLeft className={icon} />
          </Button>
        </div>
      )}
      
      {/* Next button - conditionally rendered */}
      {showNextButton && (
        <div 
          className={`absolute right-0 top-1/2 -translate-y-1/2 ${hotspot} flex items-center justify-end cursor-pointer z-10`} 
          onClick={onNext} // Apply the click handler to the entire hotspot div
          onMouseDown={(e) => e.preventDefault()} // Prevent text selection
        >
          <Button
            size="icon"
            variant="ghost"
            className={`${button} rounded-l-md rounded-r-none bg-black/50 hover:bg-black/70 transition-opacity opacity-80 group-hover:opacity-100 pointer-events-none text-white`}
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
