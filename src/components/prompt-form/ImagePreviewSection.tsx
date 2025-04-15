
import React from 'react';
import { X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Carousel,
  CarouselContent,
  CarouselItem,
  CarouselNext,
  CarouselPrevious,
} from "@/components/ui/carousel";
import { ImagePreviewSectionProps } from './types';

const ImagePreviewSection: React.FC<ImagePreviewSectionProps> = ({
  previewUrls,
  handleRemoveImage,
  clearAllImages
}) => {
  // If there are no preview URLs, don't render anything
  if (!previewUrls || previewUrls.length === 0) {
    return null;
  }

  // Remove duplicate URLs if any exist
  const uniqueUrls = [...new Set(previewUrls)];
  
  // If after removing duplicates we have no images, don't render anything
  if (uniqueUrls.length === 0) {
    return null;
  }
  
  return (
    <div className="relative p-4 pb-2">
      <Carousel className="w-full">
        <CarouselContent>
          {uniqueUrls.map((url, index) => (
            <CarouselItem key={`preview-${index}-${url.substring(url.length - 8)}`} className="basis-full md:basis-1/2 lg:basis-1/3">
              <div className="relative rounded-lg overflow-hidden h-48 border border-border/30">
                <img 
                  src={url} 
                  alt={`Uploaded image ${index + 1}`} 
                  className="w-full h-full object-contain"
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
        {uniqueUrls.length > 1 && (
          <>
            <CarouselPrevious className="left-1" />
            <CarouselNext className="right-1" />
          </>
        )}
      </Carousel>
      {uniqueUrls.length > 1 && (
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

export default ImagePreviewSection;
