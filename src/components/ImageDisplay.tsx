
import React, { useState } from 'react';
import { Card } from '@/components/ui/card';
import {
  Carousel,
  CarouselContent,
  CarouselItem,
  CarouselNext,
  CarouselPrevious,
} from "@/components/ui/carousel";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Button } from '@/components/ui/button';
import { Info, Download, Share2, Copy, FileInput, ChevronLeft, ChevronRight } from 'lucide-react';
import { toast } from 'sonner';
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
    batchId?: string;
    batchIndex?: number;
  }>;
  workflow?: string | null;
  onUseGeneratedAsInput?: (imageUrl: string) => void;
  onCreateAgain?: (batchId?: string) => void;
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
  // State to track the currently viewed image in each batch
  const [activeImageIndices, setActiveImageIndices] = useState<Record<string, number>>({});
  // State to track which batch is currently being interacted with (for hover persistence)
  const [activeBatchId, setActiveBatchId] = useState<string | null>(null);
  
  // Always render the component when we have uploaded images or when we're loading
  // or when we have generated image results
  const shouldDisplay = isLoading || generatedImages.length > 0 || (uploadedImages && uploadedImages.length > 0);
  
  if (!shouldDisplay) return null;

  // Organize images by batch ID
  const getBatchedImages = () => {
    const batches: Record<string, typeof generatedImages> = {};
    
    generatedImages.forEach(img => {
      const batchId = img.batchId || `single-${img.timestamp}`;
      if (!batches[batchId]) {
        batches[batchId] = [];
      }
      batches[batchId].push(img);
    });
    
    // Sort batches by timestamp (newest first)
    return Object.entries(batches)
      .sort(([, imagesA], [, imagesB]) => {
        const timeA = imagesA[0]?.timestamp || 0;
        const timeB = imagesB[0]?.timestamp || 0;
        return timeB - timeA;
      })
      .map(([batchId, images]) => ({
        batchId,
        images: images.sort((a, b) => (b.batchIndex || 0) - (a.batchIndex || 0))
      }));
  };

  // Get active image for a batch
  const getActiveImageIndex = (batchId: string, imagesCount: number) => {
    if (activeImageIndices[batchId] === undefined) {
      return 0;
    }
    return activeImageIndices[batchId];
  };

  // Navigate to the previous image in a batch
  const navigatePrevImage = (batchId: string, imagesCount: number) => {
    setActiveImageIndices(prev => ({
      ...prev,
      [batchId]: (prev[batchId] || 0) > 0 ? (prev[batchId] || 0) - 1 : imagesCount - 1
    }));
  };

  // Navigate to the next image in a batch
  const navigateNextImage = (batchId: string, imagesCount: number) => {
    setActiveImageIndices(prev => ({
      ...prev,
      [batchId]: (prev[batchId] || 0) < imagesCount - 1 ? (prev[batchId] || 0) + 1 : 0
    }));
  };

  // Format workflow name for display (remove hyphens and capitalize)
  const formatWorkflowName = (name: string) => {
    return name
      .split('-')
      .map(word => word.charAt(0).toUpperCase() + word.slice(1))
      .join(' ');
  };

  const handleCreateVariant = (batchId: string) => {
    if (onCreateAgain) {
      onCreateAgain(batchId);
      toast.info('Creating a new variant...');
    }
  };

  // Get batched images
  const batchedImages = getBatchedImages();

  return (
    <div className="mt-12 animate-fade-in">
      <div className="flex flex-col gap-6">
        {/* Reference images section */}
        {uploadedImages && uploadedImages.length > 0 && (
          <div>
            <h3 className="text-lg font-semibold mb-3">Reference Images</h3>
            <div className="overflow-hidden bg-secondary/20 rounded-lg">
              {uploadedImages.length === 1 ? (
                <div className="aspect-square overflow-hidden">
                  <img
                    src={uploadedImages[0]}
                    alt="Reference image"
                    className="w-full h-full object-contain"
                  />
                </div>
              ) : (
                <Carousel className="w-full">
                  <CarouselContent>
                    {uploadedImages.map((url, index) => (
                      <CarouselItem key={index} className="md:basis-1/2 lg:basis-1/3">
                        <div className="p-1">
                          <Card className="overflow-hidden">
                            <div className="aspect-square relative">
                              <img
                                src={url}
                                alt={`Reference image ${index + 1}`}
                                className="w-full h-full object-cover"
                              />
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
        )}

        {/* Generated images section */}
        <div>
          <h3 className="text-lg font-semibold mb-3">Generated Images</h3>
          
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {/* If loading and no existing images, show single loading placeholder */}
            {isLoading && generatedImages.length === 0 && (
              <Card className="overflow-hidden">
                <div className="aspect-square flex items-center justify-center bg-secondary/20">
                  <div className="h-12 w-12 animate-spin rounded-full border-4 border-primary border-t-transparent"></div>
                  {prompt && (
                    <p className="text-sm text-center text-muted-foreground absolute mt-20">
                      Generating: {prompt}
                    </p>
                  )}
                </div>
              </Card>
            )}
            
            {/* Rendered image batches */}
            {batchedImages.map(({ batchId, images }) => {
              const isGeneratingForThisBatch = isLoading && onCreateAgain && batchId === generatedImages[0]?.batchId;
              const activeIndex = getActiveImageIndex(batchId, images.length);
              const activeImage = images[activeIndex];
              const isActive = activeBatchId === batchId;
              
              return (
                <Card 
                  key={batchId} 
                  className="overflow-hidden relative"
                  onMouseEnter={() => setActiveBatchId(batchId)}
                  onMouseLeave={() => setActiveBatchId(null)}
                >
                  {/* Show loading overlay if generating new image for this batch */}
                  {isGeneratingForThisBatch ? (
                    <div className="aspect-square flex items-center justify-center bg-secondary/20">
                      <div className="h-12 w-12 animate-spin rounded-full border-4 border-primary border-t-transparent"></div>
                      {prompt && (
                        <p className="text-sm text-center text-muted-foreground absolute mt-20">
                          Generating variant: {prompt}
                        </p>
                      )}
                    </div>
                  ) : (
                    <div className="aspect-square relative">
                      <img
                        src={activeImage.url}
                        alt={activeImage.prompt || 'Generated image'}
                        className="w-full h-full object-cover"
                      />
                      
                      {/* Batch counter */}
                      {images.length > 1 && (
                        <div className="absolute top-2 left-2 bg-black/70 text-white px-3 py-1 rounded-full text-xs font-medium z-10">
                          {activeIndex + 1}/{images.length}
                        </div>
                      )}
                      
                      {/* Navigation controls - improved for visibility and reliability */}
                      {images.length > 1 && (
                        <div className="absolute inset-0 pointer-events-none">
                          <button 
                            className="absolute left-2 top-1/2 -translate-y-1/2 bg-black/70 hover:bg-black/90 rounded-full p-2 text-white transition-colors pointer-events-auto z-20"
                            onClick={(e) => {
                              e.stopPropagation();
                              navigatePrevImage(batchId, images.length);
                            }}
                          >
                            <ChevronLeft className="h-5 w-5" />
                          </button>
                          <button 
                            className="absolute right-2 top-1/2 -translate-y-1/2 bg-black/70 hover:bg-black/90 rounded-full p-2 text-white transition-colors pointer-events-auto z-20"
                            onClick={(e) => {
                              e.stopPropagation();
                              navigateNextImage(batchId, images.length);
                            }}
                          >
                            <ChevronRight className="h-5 w-5" />
                          </button>
                        </div>
                      )}
                      
                      {/* Image controls overlay - visible on hover/focus with better labels */}
                      <div 
                        className={`absolute inset-0 bg-black/60 flex flex-col justify-center items-center transition-opacity duration-200 ${isActive ? 'opacity-100' : 'opacity-0'}`}
                      >
                        <div className="flex flex-wrap justify-center gap-3 p-4">
                          {/* Info button */}
                          <Dialog>
                            <DialogTrigger asChild>
                              <Button variant="secondary" size="sm" className="bg-white/20 hover:bg-white/40 transition-colors">
                                <Info className="h-4 w-4 mr-1" />
                                <span className="text-xs">Info</span>
                              </Button>
                            </DialogTrigger>
                            <DialogContent>
                              <DialogHeader>
                                <DialogTitle>Image Generation Details</DialogTitle>
                                <DialogDescription>
                                  Information about this generated image.
                                </DialogDescription>
                              </DialogHeader>
                              <div className="space-y-2 mt-4">
                                <div>
                                  <h4 className="font-semibold">Prompt:</h4>
                                  <p className="text-sm text-muted-foreground">{activeImage.prompt}</p>
                                </div>
                                {activeImage.workflow && (
                                  <div>
                                    <h4 className="font-semibold">Workflow:</h4>
                                    <p className="text-sm text-muted-foreground">{formatWorkflowName(activeImage.workflow)}</p>
                                  </div>
                                )}
                                {activeImage.params && Object.keys(activeImage.params).length > 0 && (
                                  <div>
                                    <h4 className="font-semibold">Parameters:</h4>
                                    <div className="text-sm text-muted-foreground">
                                      {Object.entries(activeImage.params).map(([key, value]) => (
                                        <div key={key} className="flex justify-between">
                                          <span className="capitalize">{key.replace(/([A-Z])/g, ' $1').trim()}:</span>
                                          <span>{value?.toString()}</span>
                                        </div>
                                      ))}
                                    </div>
                                  </div>
                                )}
                                {images.length > 1 && (
                                  <div>
                                    <h4 className="font-semibold">Batch:</h4>
                                    <p className="text-sm text-muted-foreground">Image {activeIndex + 1} of {images.length}</p>
                                  </div>
                                )}
                              </div>
                            </DialogContent>
                          </Dialog>
                          
                          {/* New variant button */}
                          <Button 
                            variant="secondary" 
                            size="sm" 
                            className="bg-white/20 hover:bg-white/40 transition-colors"
                            onClick={() => handleCreateVariant(batchId)}
                          >
                            <Copy className="h-4 w-4 mr-1" />
                            <span className="text-xs">New Variant</span>
                          </Button>
                          
                          {/* Image actions via ImageActions component */}
                          <ImageActions 
                            imageUrl={activeImage.url} 
                            onUseAsInput={() => onUseGeneratedAsInput && onUseGeneratedAsInput(activeImage.url)}
                            generationInfo={{
                              prompt: activeImage.prompt,
                              workflow: activeImage.workflow,
                              params: activeImage.params
                            }}
                          />
                        </div>
                        
                        {/* Prompt preview */}
                        <div className="absolute bottom-0 w-full bg-black/70 p-2">
                          <p className="text-xs text-white truncate">{activeImage.prompt}</p>
                        </div>
                      </div>
                    </div>
                  )}
                </Card>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
};

export default ImageDisplay;
