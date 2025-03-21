
import React from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';

interface ImageNavigationButtonsProps {
  index: number;
  total: number;
  onNavigatePrev?: (e: React.MouseEvent) => void;
  onNavigateNext?: (e: React.MouseEvent) => void;
}

const ImageNavigationButtons: React.FC<ImageNavigationButtonsProps> = ({
  index,
  total,
  onNavigatePrev,
  onNavigateNext
}) => {
  if (total <= 1) return null;
  
  return (
    <>
      {index > 0 && onNavigatePrev && (
        <button 
          className="absolute left-2 top-1/2 -translate-y-1/2 bg-black/40 hover:bg-black/60 rounded-full p-1.5 text-white transition-colors z-10"
          onClick={onNavigatePrev}
        >
          <ChevronLeft className="h-3 w-3" />
        </button>
      )}
      
      {index < total - 1 && onNavigateNext && (
        <button 
          className="absolute right-2 top-1/2 -translate-y-1/2 bg-black/40 hover:bg-black/60 rounded-full p-1.5 text-white transition-colors z-10"
          onClick={onNavigateNext}
        >
          <ChevronRight className="h-3 w-3" />
        </button>
      )}
    </>
  );
};

export default ImageNavigationButtons;
