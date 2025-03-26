
import React, { useState } from 'react';
import { X } from 'lucide-react';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog';

interface ReferenceImageIndicatorProps {
  imageUrl: string; // This is now a required prop
  onRemove?: () => void;
  position?: 'top-left' | 'top-right' | 'bottom-left' | 'bottom-right';
}

const ReferenceImageIndicator: React.FC<ReferenceImageIndicatorProps> = ({ 
  imageUrl, 
  onRemove,
  position = 'bottom-left' // Default position
}) => {
  const [isOpen, setIsOpen] = useState(false);

  const handleRemove = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (onRemove) {
      onRemove();
    }
  };

  // Position classes based on the position prop
  const positionClasses = {
    'top-left': 'top-1 left-1',
    'top-right': 'top-1 right-1',
    'bottom-left': 'bottom-1 left-1',
    'bottom-right': 'bottom-1 right-1'
  }[position];

  return (
    <Dialog open={isOpen} onOpenChange={setIsOpen}>
      <DialogTrigger asChild>
        <div 
          className={`absolute ${positionClasses} bg-primary/80 rounded-full h-5 w-5 flex items-center justify-center cursor-pointer z-10`}
          onClick={(e) => {
            e.stopPropagation();
            setIsOpen(true);
          }}
        >
          <span className="text-white text-xs">R</span>
        </div>
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Reference Image</DialogTitle>
        </DialogHeader>
        <div className="flex items-center justify-center">
          <img 
            src={imageUrl} 
            alt="Reference image full view" 
            className="max-w-full max-h-[70vh] object-contain rounded-md"
          />
        </div>
      </DialogContent>
    </Dialog>
  );
};

export default ReferenceImageIndicator;
