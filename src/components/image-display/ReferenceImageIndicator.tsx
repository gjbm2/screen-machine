
import React, { useState } from 'react';
import { X } from 'lucide-react';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog';

interface ReferenceImageIndicatorProps {
  imageUrl: string;
  onRemove?: () => void;
}

const ReferenceImageIndicator: React.FC<ReferenceImageIndicatorProps> = ({ 
  imageUrl, 
  onRemove 
}) => {
  const [isOpen, setIsOpen] = useState(false);

  const handleRemove = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (onRemove) {
      onRemove();
    }
  };

  return (
    <Dialog open={isOpen} onOpenChange={setIsOpen}>
      <DialogTrigger asChild>
        <div className="relative w-full h-full cursor-pointer flex items-center justify-center">
          <img 
            src={imageUrl} 
            alt="Reference" 
            className="max-w-full max-h-full object-contain"
            onClick={() => setIsOpen(true)}
          />
          {onRemove && (
            <button 
              className="absolute top-0.5 right-0.5 bg-black/70 hover:bg-black/90 rounded-full p-1 text-white transition-colors z-10"
              onClick={handleRemove}
            >
              <X className="h-3 w-3" />
            </button>
          )}
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
