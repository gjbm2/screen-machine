
import React, { useState, useRef, TouchEvent } from 'react';
import { Carousel, CarouselContent, CarouselItem, CarouselNext, CarouselPrevious } from '@/components/ui/carousel';
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
      <div className="overflow-hidden bg-secondary/20 rounded-lg">
        {images.length === 1 ? (
          <div className="aspect-square w-full max-w-xs mx-auto overflow-hidden relative">
            <img
              src={images[0]}
              alt="Reference image"
              className="w-full h-full object-contain"
            />
            <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
              <DialogTrigger asChild>
                <button 
                  className="absolute top-2 right-2 bg-black/70 hover:bg-black/90 rounded-full p-2 text-white transition-colors"
                  onClick={(e) => e.stopPropagation()}
                >
                  <Maximize className="h-4 w-4" />
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
        ) : (
          <div 
            ref={touchRef}
            className="relative"
            onTouchStart={handleTouchStart}
            onTouchEnd={handleTouchEnd}
          >
            <div className="aspect-square w-full max-w-xs mx-auto overflow-hidden relative">
              <img
                src={images[currentIndex]}
                alt={`Reference image ${currentIndex + 1}`}
                className="w-full h-full object-contain"
              />
              
              <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
                <DialogTrigger asChild>
                  <button 
                    className="absolute top-2 right-2 bg-black/70 hover:bg-black/90 rounded-full p-2 text-white transition-colors"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <Maximize className="h-4 w-4" />
                  </button>
                </DialogTrigger>
                <DialogContent fullscreen>
                  <DialogHeader>
                    <DialogTitle className="sr-only">Reference Image</DialogTitle>
                  </DialogHeader>
                  <div className="w-full h-full flex items-center justify-center">
                    <img
                      src={images[currentIndex]}
                      alt={`Reference image ${currentIndex + 1} full view`}
                      className="max-w-full max-h-full object-contain"
                    />
                  </div>
                </DialogContent>
              </Dialog>
              
              <NavigationControls
                onPrevious={(e) => {
                  e.stopPropagation();
                  handlePrevious();
                }}
                onNext={(e) => {
                  e.stopPropagation();
                  handleNext();
                }}
                size="small"
              />
              
              <div className="absolute bottom-2 right-2 bg-black/70 text-white px-2 py-1 rounded-full text-xs">
                {currentIndex + 1}/{images.length}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default ReferenceImagesSection;
