
import React, { useState } from 'react';
import { Card } from '@/components/ui/card';
import {
  Carousel,
  CarouselContent,
  CarouselItem,
  CarouselNext,
  CarouselPrevious,
} from "@/components/ui/carousel";
import ImageActions from '@/components/ImageActions';

interface ImageDisplayProps {
  imageUrl: string | null;
  prompt: string | null;
  isLoading: boolean;
  uploadedImages?: string[];
  generatedImages?: Array<{
    url: string;
    prompt: string;
    workflow: string;
    timestamp: number;
    params?: Record<string, any>;
  }>;
  workflow?: string | null;
  onUseGeneratedAsInput?: (imageUrl: string) => void;
  onCreateAgain?: () => void;
  generationParams?: Record<string, any>;
}

const ImageDisplay: React.FC<ImageDisplayProps> = ({ 
  imageUrl, 
  prompt, 
  isLoading,
  uploadedImages = [],
  generatedImages = [],
  workflow,
  onUseGeneratedAsInput,
  onCreateAgain,
  generationParams
}) => {
  const [selectedImageIndex, setSelectedImageIndex] = useState<number | null>(
    generatedImages.length > 0 ? 0 : null
  );
  
  // Always render the component when we have uploaded images or when we're loading
  // or when we have generated image results
  const shouldDisplay = isLoading || generatedImages.length > 0 || (uploadedImages && uploadedImages.length > 0);
  
  if (!shouldDisplay) return null;

  // Format workflow name for display (remove hyphens and capitalize)
  const formatWorkflowName = (name: string) => {
    return name
      .split('-')
      .map(word => word.charAt(0).toUpperCase() + word.slice(1))
      .join(' ');
  };

  const handleImageSelect = (index: number) => {
    setSelectedImageIndex(index);
  };

  const selectedImage = selectedImageIndex !== null ? generatedImages[selectedImageIndex] : null;

  return (
    <div className="mt-12 animate-fade-in">
      <div className="flex flex-col md:flex-row gap-6">
        {/* Only show the reference images section if we have uploaded images */}
        {uploadedImages && uploadedImages.length > 0 && (
          <Card className="relative w-full md:w-1/2 overflow-hidden border border-border/30 rounded-lg">
            {uploadedImages.length === 1 ? (
              <div className="aspect-square overflow-hidden bg-secondary/20">
                <img
                  src={uploadedImages[0]}
                  alt="Reference image"
                  className="w-full h-full object-contain"
                />
              </div>
            ) : (
              <div className="aspect-square overflow-hidden bg-secondary/20">
                <Carousel className="w-full h-full">
                  <CarouselContent className="h-full">
                    {uploadedImages.map((url, index) => (
                      <CarouselItem key={index} className="h-full">
                        <div className="h-full flex items-center justify-center">
                          <img
                            src={url}
                            alt={`Reference image ${index + 1}`}
                            className="max-w-full max-h-full object-contain"
                          />
                        </div>
                      </CarouselItem>
                    ))}
                  </CarouselContent>
                  <CarouselPrevious className="left-2" />
                  <CarouselNext className="right-2" />
                </Carousel>
              </div>
            )}
            <div className="p-3 text-center">
              <h3 className="text-sm font-medium">
                {uploadedImages.length > 1 
                  ? `Reference Images (${uploadedImages.length})` 
                  : "Reference Image"}
              </h3>
            </div>
          </Card>
        )}

        {/* Generated images section */}
        {(isLoading || generatedImages.length > 0) && (
          <Card className="relative w-full md:w-1/2 overflow-hidden border border-border/30 rounded-lg">
            <div className="aspect-square overflow-hidden bg-secondary/20">
              {isLoading ? (
                <div className="flex h-full items-center justify-center">
                  <div className="h-12 w-12 animate-spin rounded-full border-4 border-primary border-t-transparent"></div>
                </div>
              ) : generatedImages.length > 0 ? (
                <Carousel className="w-full h-full">
                  <CarouselContent className="h-full">
                    {generatedImages.map((img, index) => (
                      <CarouselItem key={index} className="h-full relative">
                        <div 
                          className={`h-full flex items-center justify-center cursor-pointer ${selectedImageIndex === index ? 'ring-2 ring-primary' : ''}`}
                          onClick={() => handleImageSelect(index)}
                        >
                          <img
                            src={img.url}
                            alt={img.prompt || 'Generated image'}
                            className="max-w-full max-h-full object-contain"
                          />
                        </div>
                      </CarouselItem>
                    ))}
                  </CarouselContent>
                  <CarouselPrevious className="left-2" />
                  <CarouselNext className="right-2" />
                </Carousel>
              ) : null}
            </div>
            <div className="p-3">
              {selectedImage && (
                <>
                  <p className="text-sm text-center text-muted-foreground truncate">
                    {selectedImage.prompt}
                  </p>
                  {selectedImage.workflow && (
                    <p className="text-xs text-center text-muted-foreground mt-1">
                      Workflow: {formatWorkflowName(selectedImage.workflow)}
                    </p>
                  )}
                  
                  {/* Add image actions only for the selected image */}
                  <ImageActions 
                    imageUrl={selectedImage.url}
                    onUseAsInput={() => onUseGeneratedAsInput && onUseGeneratedAsInput(selectedImage.url)}
                    onCreateAgain={onCreateAgain}
                    generationInfo={{
                      prompt: selectedImage.prompt || '',
                      workflow: selectedImage.workflow || 'text-to-image',
                      params: selectedImage.params || {}
                    }}
                  />
                </>
              )}
              
              {isLoading && prompt && (
                <p className="text-sm text-center text-muted-foreground truncate">
                  Generating: {prompt}
                </p>
              )}
            </div>
          </Card>
        )}
      </div>
    </div>
  );
};

export default ImageDisplay;
