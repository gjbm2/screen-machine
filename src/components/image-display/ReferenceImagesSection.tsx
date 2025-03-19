
import React from 'react';
import { Carousel, CarouselContent, CarouselItem, CarouselNext, CarouselPrevious } from '@/components/ui/carousel';
import { Card } from '@/components/ui/card';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog';
import { Maximize } from 'lucide-react';

interface ReferenceImagesSectionProps {
  images: string[];
}

const ReferenceImagesSection: React.FC<ReferenceImagesSectionProps> = ({ images }) => {
  if (!images || images.length === 0) return null;
  
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
            <Dialog>
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
          <Carousel className="w-full">
            <CarouselContent>
              {images.map((url, index) => (
                <CarouselItem key={index} className="md:basis-1/2 lg:basis-1/3">
                  <div className="p-1">
                    <Card className="overflow-hidden">
                      <div className="aspect-square relative">
                        <img
                          src={url}
                          alt={`Reference image ${index + 1}`}
                          className="w-full h-full object-cover"
                        />
                        <Dialog>
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
                                src={url}
                                alt={`Reference image ${index + 1} full view`}
                                className="max-w-full max-h-full object-contain"
                              />
                            </div>
                          </DialogContent>
                        </Dialog>
                      </div>
                    </Card>
                  </div>
                </CarouselItem>
              ))}
            </CarouselContent>
            <CarouselPrevious />
            <CarouselNext />
          </Carousel>
        )}
      </div>
    </div>
  );
};

export default ReferenceImagesSection;
