
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
        className="absolute left-2 top-1/2 -translate-y-1/2 bg-black/50 hover:bg-black/70 rounded-full p-1.5 text-white transition-colors pointer-events-auto z-20"
        onClick={onPrevious}
      >
        <ChevronLeft className="h-4 w-4" />
      </button>
      <button 
        className="absolute right-2 top-1/2 -translate-y-1/2 bg-black/50 hover:bg-black/70 rounded-full p-1.5 text-white transition-colors pointer-events-auto z-20"
        onClick={onNext}
      >
        <ChevronRight className="h-4 w-4" />
      </button>
    </div>
  );
};

export default NavigationControls;
