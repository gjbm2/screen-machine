
import React, { useState } from 'react';
import { X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Carousel,
  CarouselContent,
  CarouselItem,
  CarouselNext,
  CarouselPrevious,
} from "@/components/ui/carousel";
import ImageLoadingState from '../image-display/ImageLoadingState';

interface ImagePreviewProps {
  previewUrls: string[];
  handleRemoveImage: (index: number) => void;
  clearAllImages: () => void;
}

const ImagePreview: React.FC<ImagePreviewProps> = ({
  previewUrls,
  handleRemoveImage,
  clearAllImages
}) => {
  const [loadedImages, setLoadedImages] = useState<Record<number, boolean>>({});
  
  if (previewUrls.length === 0) {
    return null;
  }

  const handleImageLoad = (index: number) => {
    setLoadedImages(prev => ({
      ...prev,
      [index]: true
    }));
  };

  return (
    <div className="relative p-4 pb-2">
      <Carousel className="w-full">
        <CarouselContent>
          {previewUrls.map((url, index) => (
            <CarouselItem key={index} className="basis-full md:basis-1/2 lg:basis-1/3">
              <div className="relative rounded-lg overflow-hidden h-48 border border-border/30">
                {!loadedImages[index] && (
                  <div className="absolute inset-0 z-10">
                    <ImageLoadingState />
                  </div>
                )}
                
                <img 
                  src={url} 
                  alt={`Uploaded image ${index + 1}`} 
                  className={`w-full h-full object-contain ${!loadedImages[index] ? 'opacity-0' : 'opacity-100'}`}
                  style={{ transition: 'opacity 0.2s ease-in-out' }}
                  onLoad={() => handleImageLoad(index)}
                />
                <button
                  type="button"
                  onClick={() => handleRemoveImage(index)}
                  className="absolute top-2 right-2 bg-foreground/20 text-background hover:bg-foreground/30 p-1 rounded-full"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
            </CarouselItem>
          ))}
        </CarouselContent>
        {previewUrls.length > 1 && (
          <>
            <CarouselPrevious className="left-1" />
            <CarouselNext className="right-1" />
          </>
        )}
      </Carousel>
      {previewUrls.length > 1 && (
        <div className="flex justify-end mt-2">
          <Button 
            type="button" 
            variant="outline" 
            size="sm" 
            onClick={clearAllImages}
            className="text-xs"
          >
            Clear All Images
          </Button>
        </div>
      )}
    </div>
  );
};

export default ImagePreview;
