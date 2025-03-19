
import React, { useState, useRef, TouchEvent } from 'react';
import { Card } from '@/components/ui/card';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog';
import { Maximize } from 'lucide-react';
import NavigationControls from './NavigationControls';

interface ReferenceImagesSectionProps {
  images: string[];
}

const ReferenceImagesSection: React.FC<ReferenceImagesSectionProps> = ({ images }) => {
  if (!images || images.length === 0) return null;
  
  const [currentIndex, setCurrentIndex] = useState(0);
  const [startX, setStartX] = useState<number | null>(null);
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const touchRef = useRef<HTMLDivElement>(null);

  const handlePrevious = () => {
    setCurrentIndex(prev => (prev > 0 ? prev - 1 : images.length - 1));
  };

  const handleNext = () => {
    setCurrentIndex(prev => (prev < images.length - 1 ? prev + 1 : 0));
  };

  const handleTouchStart = (e: TouchEvent<HTMLDivElement>) => {
    setStartX(e.touches[0].clientX);
  };

  const handleTouchEnd = (e: TouchEvent<HTMLDivElement>) => {
    if (startX === null) return;
    
    const endX = e.changedTouches[0].clientX;
    const diff = startX - endX;
    
    if (Math.abs(diff) > 50) {
      if (diff > 0) {
        handleNext();
      } else {
        handlePrevious();
      }
    }
    
    setStartX(null);
  };
  
  return (
    <div>
      <h3 className="text-lg font-semibold mb-3">Reference Images</h3>
      {images.length === 1 ? (
        <div className="flex items-center justify-start overflow-x-auto pb-2">
          <Card className="flex-shrink-0 overflow-hidden">
            <div className="w-32 h-32 relative">
              <img
                src={images[0]}
                alt="Reference image"
                className="w-full h-full object-cover"
              />
              <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
                <DialogTrigger asChild>
                  <button 
                    className="absolute top-2 right-2 bg-black/70 hover:bg-black/90 rounded-full p-1 text-white transition-colors"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <Maximize className="h-3 w-3" />
                  </button>
                </DialogTrigger>
                <DialogContent fullscreen>
                  <DialogHeader>
                    <DialogTitle className="sr-only">Reference Image</DialogTitle>
                  </DialogHeader>
                  <div className="w-full h-full flex items-center justify-center">
                    <img
                      src={images[0]}
                      alt="Reference image full view"
                      className="max-w-full max-h-full object-contain"
                    />
                  </div>
                </DialogContent>
              </Dialog>
            </div>
          </Card>
        </div>
      ) : (
        <div className="flex items-start space-x-2 overflow-x-auto pb-2">
          {images.map((image, idx) => (
            <Card key={idx} className="flex-shrink-0 overflow-hidden">
              <div 
                className="w-32 h-32 relative"
                onClick={() => setCurrentIndex(idx)}
              >
                <img
                  src={image}
                  alt={`Reference image ${idx + 1}`}
                  className={`w-full h-full object-cover ${currentIndex === idx ? 'ring-2 ring-primary' : ''}`}
                />
                {currentIndex === idx && (
                  <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
                    <DialogTrigger asChild>
                      <button 
                        className="absolute top-2 right-2 bg-black/70 hover:bg-black/90 rounded-full p-1 text-white transition-colors"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <Maximize className="h-3 w-3" />
                      </button>
                    </DialogTrigger>
                    <DialogContent fullscreen>
                      <DialogHeader>
                        <DialogTitle className="sr-only">Reference Image</DialogTitle>
                      </DialogHeader>
                      <div className="w-full h-full flex items-center justify-center">
                        <img
                          src={image}
                          alt={`Reference image ${idx + 1} full view`}
                          className="max-w-full max-h-full object-contain"
                        />
                      </div>
                    </DialogContent>
                  </Dialog>
                )}
              </div>
              <div className="absolute bottom-1 right-1 bg-black/70 text-white px-1.5 py-0.5 rounded-full text-xs">
                {idx + 1}
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
};

export default ReferenceImagesSection;
