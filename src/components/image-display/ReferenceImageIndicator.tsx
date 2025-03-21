
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
        <div className="relative w-full h-full cursor-pointer">
          <img 
            src={imageUrl} 
            alt="Reference" 
            className="w-full h-full object-cover rounded-md"
            onClick={() => setIsOpen(true)}
          />
          {onRemove && (
            <button 
              className="absolute top-2 right-2 bg-black/70 hover:bg-black/90 rounded-full p-1.5 text-white transition-colors z-10"
              onClick={handleRemove}
            >
              <X className="h-4 w-4" />
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
