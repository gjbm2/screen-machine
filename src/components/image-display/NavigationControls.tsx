
import React from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';

interface NavigationControlsProps {
  onPrevious: (e: React.MouseEvent) => void;
  onNext: (e: React.MouseEvent) => void;
}

const NavigationControls: React.FC<NavigationControlsProps> = ({ 
  onPrevious, 
  onNext 
}) => {
  return (
    <div className="absolute inset-0 pointer-events-none">
      <button 
        className="absolute left-2 top-1/2 -translate-y-1/2 bg-black/30 hover:bg-black/50 rounded-full p-1 text-white/80 transition-colors pointer-events-auto z-20"
        onClick={onPrevious}
        aria-label="Previous image"
      >
        <ChevronLeft className="h-3 w-3" />
      </button>
      <button 
        className="absolute right-2 top-1/2 -translate-y-1/2 bg-black/30 hover:bg-black/50 rounded-full p-1 text-white/80 transition-colors pointer-events-auto z-20"
        onClick={onNext}
        aria-label="Next image"
      >
        <ChevronRight className="h-3 w-3" />
      </button>
    </div>
  );
};

export default NavigationControls;
