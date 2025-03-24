
import React, { useState, useEffect } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { Button } from '@/components/ui/button';

interface ReferenceImageDialogProps {
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
  imageUrls: string[];
}

const ReferenceImageDialog: React.FC<ReferenceImageDialogProps> = ({
  isOpen,
  onOpenChange,
  imageUrls
}) => {
  const [currentIndex, setCurrentIndex] = useState(0);
  
  const hasMultipleImages = imageUrls.length > 1;
  
  const navigatePrev = () => {
    setCurrentIndex((prev) => (prev === 0 ? imageUrls.length - 1 : prev - 1));
  };
  
  const navigateNext = () => {
    setCurrentIndex((prev) => (prev === imageUrls.length - 1 ? 0 : prev + 1));
  };
  
  // Reset index when dialog opens
  useEffect(() => {
    if (isOpen) {
      setCurrentIndex(0);
    }
  }, [isOpen]);
  
  return (
    <Dialog open={isOpen} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Reference Image {hasMultipleImages ? `(${currentIndex + 1}/${imageUrls.length})` : ''}</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col items-center">
          <p className="text-sm mb-2 text-muted-foreground">Reference image used for generation</p>
          <div className="relative border rounded-md overflow-hidden">
            {imageUrls.length > 0 ? (
              <img 
                src={imageUrls[currentIndex]} 
                alt={`Reference image ${currentIndex + 1}`}
                className="w-full h-auto object-contain max-h-[70vh]"
              />
            ) : (
              <div className="h-48 w-full flex items-center justify-center text-muted-foreground">
                No reference image available
              </div>
            )}
            
            {hasMultipleImages && (
              <>
                <Button 
                  variant="ghost" 
                  size="icon" 
                  className="absolute left-1 top-1/2 -translate-y-1/2 bg-black/30 hover:bg-black/50 text-white rounded-full"
                  onClick={navigatePrev}
                >
                  <ChevronLeft className="h-4 w-4" />
                </Button>
                <Button 
                  variant="ghost" 
                  size="icon" 
                  className="absolute right-1 top-1/2 -translate-y-1/2 bg-black/30 hover:bg-black/50 text-white rounded-full"
                  onClick={navigateNext}
                >
                  <ChevronRight className="h-4 w-4" />
                </Button>
              </>
            )}
          </div>
          
          {hasMultipleImages && (
            <div className="flex justify-center mt-3 gap-1">
              {imageUrls.map((_, idx) => (
                <button 
                  key={idx} 
                  onClick={() => setCurrentIndex(idx)}
                  className={`w-2 h-2 rounded-full ${idx === currentIndex ? 'bg-primary' : 'bg-gray-300'}`}
                  aria-label={`View reference image ${idx + 1}`}
                />
              ))}
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
};

export default ReferenceImageDialog;
