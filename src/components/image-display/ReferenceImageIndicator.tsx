
import React, { useState } from 'react';
import { Image } from 'lucide-react';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog';

interface ReferenceImageIndicatorProps {
  imageUrl: string;
}

const ReferenceImageIndicator: React.FC<ReferenceImageIndicatorProps> = ({ imageUrl }) => {
  const [isOpen, setIsOpen] = useState(false);

  return (
    <Dialog open={isOpen} onOpenChange={setIsOpen}>
      <DialogTrigger asChild>
        <button 
          className="absolute top-2 left-2 bg-black/70 hover:bg-black/90 rounded-full p-1.5 text-white transition-colors group z-10"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="relative w-6 h-6 rounded-full overflow-hidden border border-white/20">
            <img 
              src={imageUrl} 
              alt="Reference" 
              className="w-full h-full object-cover"
            />
            <div className="absolute inset-0 bg-black/40 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity">
              <Image className="h-3 w-3" />
            </div>
          </div>
        </button>
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
