
import React from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { Button } from '@/components/ui/button';

interface ImageNavigationButtonsProps {
  index: number;
  total: number;
  onNavigatePrev?: (e: React.MouseEvent) => void;
  onNavigateNext?: (e: React.MouseEvent) => void;
  alwaysVisible?: boolean;
}

const ImageNavigationButtons: React.FC<ImageNavigationButtonsProps> = ({
  index,
  total,
  onNavigatePrev,
  onNavigateNext,
  alwaysVisible = false
}) => {
  if (total <= 1) {
    return null;
  }

  // Ensure we stop propagation on button clicks
  const handlePrevClick = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (onNavigatePrev) {
      onNavigatePrev(e);
    }
  };

  const handleNextClick = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (onNavigateNext) {
      onNavigateNext(e);
    }
  };

  return (
    <div className={`flex justify-between w-full h-full items-center pointer-events-none ${!alwaysVisible ? 'group-hover:opacity-100 opacity-0' : 'opacity-100'} transition-opacity`}>
      <Button
        type="button"
        onClick={handlePrevClick}
        className="image-action-button h-8 w-8 rounded-full p-0 pointer-events-auto"
        variant="ghost"
        size="icon"
        aria-label="Previous image"
      >
        <ChevronLeft className="h-5 w-5 text-white drop-shadow-md" />
      </Button>
      
      <Button
        type="button"
        onClick={handleNextClick}
        className="image-action-button h-8 w-8 rounded-full p-0 pointer-events-auto"
        variant="ghost"
        size="icon"
        aria-label="Next image"
      >
        <ChevronRight className="h-5 w-5 text-white drop-shadow-md" />
      </Button>
    </div>
  );
};

export default ImageNavigationButtons;
