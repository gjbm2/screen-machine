
import React from 'react';
import { Button } from '@/components/ui/button';
import { ChevronLeft, ChevronRight } from 'lucide-react';

interface ImageNavigationButtonsProps {
  index: number;
  total: number;
  onNavigatePrev?: () => void;
  onNavigateNext?: () => void;
  alwaysVisible?: boolean;
}

const ImageNavigationButtons: React.FC<ImageNavigationButtonsProps> = ({
  index,
  total,
  onNavigatePrev,
  onNavigateNext,
  alwaysVisible = false
}) => {
  // Only show navigation if we have more than one image
  if (total <= 1 || (!onNavigatePrev && !onNavigateNext)) {
    return null;
  }
  
  const baseButtonClass = "absolute top-1/2 -translate-y-1/2 bg-black/50 hover:bg-black/70 text-white rounded-full w-8 h-8 flex items-center justify-center z-50";
  
  // Add visibility classes based on whether buttons should always be visible
  const visibilityClass = alwaysVisible 
    ? "opacity-70 hover:opacity-100" 
    : "opacity-0 group-hover:opacity-70 group-hover:hover:opacity-100 transition-opacity duration-200";
  
  return (
    <div className="w-full h-full relative pointer-events-none navigation-button-container">
      {onNavigatePrev && (
        <Button
          type="button" 
          variant="ghost"
          onClick={(e) => {
            e.stopPropagation();
            e.preventDefault();
            console.log("Navigation: Previous button clicked");
            onNavigatePrev();
          }}
          className={`${baseButtonClass} ${visibilityClass} left-2 pointer-events-auto`}
          aria-label="Previous image"
        >
          <ChevronLeft className="h-5 w-5" />
        </Button>
      )}
      
      {onNavigateNext && (
        <Button 
          type="button"
          variant="ghost"
          onClick={(e) => {
            e.stopPropagation();
            e.preventDefault();
            console.log("Navigation: Next button clicked");
            onNavigateNext();
          }}
          className={`${baseButtonClass} ${visibilityClass} right-2 pointer-events-auto`}
          aria-label="Next image"
        >
          <ChevronRight className="h-5 w-5" />
        </Button>
      )}
    </div>
  );
};

export default ImageNavigationButtons;
