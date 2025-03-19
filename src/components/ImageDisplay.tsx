
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
import { Info, Download, Share2, Copy, FileInput, ChevronLeft, ChevronRight, Maximize, ChevronDown, ChevronUp } from 'lucide-react';
import { toast } from 'sonner';
import ImageActions from '@/components/ImageActions';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";

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
    status?: 'generating' | 'completed' | 'error';
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
  // State to track the currently full-screen image
  const [fullScreenImage, setFullScreenImage] = useState<string | null>(null);
  // State to track which batches are expanded (unrolled)
  const [expandedBatches, setExpandedBatches] = useState<Record<string, boolean>>({});
  
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

  // Toggle expanded state for a batch
  const toggleExpandBatch = (batchId: string) => {
    setExpandedBatches(prev => ({
      ...prev,
      [batchId]: !prev[batchId]
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

  // View image in full screen
  const openFullScreen = (url: string, event: React.MouseEvent) => {
    event.stopPropagation();
    setFullScreenImage(url);
  };

  // Get batched images
  const batchedImages = getBatchedImages();

  // Render an individual image within a batch
  const renderBatchImage = (image: typeof generatedImages[0], batchId: string, isActive: boolean, index: number, total: number) => {
    const isGenerating = image.status === 'generating';
    
    if (isGenerating) {
      return (
        <div className="aspect-square flex items-center justify-center bg-secondary/20">
          <div className="h-12 w-12 animate-spin rounded-full border-4 border-primary border-t-transparent"></div>
          {image.prompt && (
            <p className="text-sm text-center text-muted-foreground absolute mt-20">
              Generating: {image.prompt}
            </p>
          )}
        </div>
      );
    }
    
    return (
      <div className="aspect-square relative">
        <img
          src={image.url}
          alt={image.prompt || 'Generated image'}
          className="w-full h-full object-cover"
        />
        
        {/* Full screen view button */}
        <Dialog>
          <DialogTrigger asChild>
            <button 
              className="absolute top-2 right-2 bg-black/70 hover:bg-black/90 rounded-full p-2 text-white transition-colors z-20"
              onClick={(e) => e.stopPropagation()}
            >
              <Maximize className="h-4 w-4" />
            </button>
          </DialogTrigger>
          <DialogContent fullscreen>
            <div className="w-full h-full flex items-center justify-center">
              <img
                src={image.url}
                alt={image.prompt || 'Generated image full view'}
                className="max-w-full max-h-full object-contain"
              />
            </div>
          </DialogContent>
        </Dialog>
        
        {/* Batch counter */}
        {total > 1 && (
          <div className="absolute top-2 left-2 bg-black/70 text-white px-3 py-1 rounded-full text-xs font-medium z-10">
            {index + 1}/{total}
          </div>
        )}
        
        {/* Image controls overlay - visible on hover/focus with better labels */}
        {isActive && (
          <div className="absolute inset-0 bg-black/60 flex flex-col justify-center items-center transition-opacity duration-200 opacity-100">
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
                      <p className="text-sm text-muted-foreground">{image.prompt}</p>
                    </div>
                    {image.workflow && (
                      <div>
                        <h4 className="font-semibold">Workflow:</h4>
                        <p className="text-sm text-muted-foreground">{formatWorkflowName(image.workflow)}</p>
                      </div>
                    )}
                    {image.params && Object.keys(image.params).length > 0 && (
                      <div>
                        <h4 className="font-semibold">Parameters:</h4>
                        <div className="text-sm text-muted-foreground">
                          {Object.entries(image.params).map(([key, value]) => (
                            <div key={key} className="flex justify-between">
                              <span className="capitalize">{key.replace(/([A-Z])/g, ' $1').trim()}:</span>
                              <span>{value?.toString()}</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                    {total > 1 && (
                      <div>
                        <h4 className="font-semibold">Batch:</h4>
                        <p className="text-sm text-muted-foreground">Image {index + 1} of {total}</p>
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
                imageUrl={image.url} 
                onUseAsInput={() => onUseGeneratedAsInput && onUseGeneratedAsInput(image.url)}
                generationInfo={{
                  prompt: image.prompt,
                  workflow: image.workflow,
                  params: image.params
                }}
              />
            </div>
            
            {/* Prompt preview */}
            <div className="absolute bottom-0 w-full bg-black/70 p-2">
              <p className="text-xs text-white truncate">{image.prompt}</p>
            </div>
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="mt-12 animate-fade-in">
      <div className="flex flex-col gap-6">
        {/* Reference images section */}
        {uploadedImages && uploadedImages.length > 0 && (
          <div>
            <h3 className="text-lg font-semibold mb-3">Reference Images</h3>
            <div className="overflow-hidden bg-secondary/20 rounded-lg">
              {uploadedImages.length === 1 ? (
                <div className="aspect-square overflow-hidden relative">
                  <img
                    src={uploadedImages[0]}
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
                      <div className="w-full h-full flex items-center justify-center">
                        <img
                          src={uploadedImages[0]}
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
              const activeIndex = getActiveImageIndex(batchId, images.length);
              const activeImage = images[activeIndex];
              const isActive = activeBatchId === batchId;
              const isExpanded = expandedBatches[batchId];
              
              return (
                <Collapsible 
                  key={batchId} 
                  open={isExpanded}
                  onOpenChange={() => toggleExpandBatch(batchId)}
                  className={isExpanded ? "col-span-full" : ""}
                >
                  <Card 
                    className={`overflow-hidden relative ${isExpanded ? "mb-2" : ""}`}
                    onMouseEnter={() => setActiveBatchId(batchId)}
                    onMouseLeave={() => setActiveBatchId(null)}
                  >
                    {/* Expand/Collapse button */}
                    <CollapsibleTrigger asChild>
                      <button 
                        className="absolute top-2 left-10 bg-black/70 hover:bg-black/90 rounded-full p-2 text-white transition-colors z-20"
                        onClick={(e) => e.stopPropagation()}
                      >
                        {isExpanded ? 
                          <ChevronUp className="h-4 w-4" /> : 
                          <ChevronDown className="h-4 w-4" />
                        }
                      </button>
                    </CollapsibleTrigger>
                    
                    {/* Collapsed view (carousel-like navigation) */}
                    {!isExpanded && (
                      <>
                        {renderBatchImage(activeImage, batchId, isActive, activeIndex, images.length)}
                        
                        {/* Navigation controls */}
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
                      </>
                    )}
                    
                    {/* Expanded view (single selected image) */}
                    {isExpanded && (
                      <div className="aspect-square">
                        {renderBatchImage(activeImage, batchId, isActive, activeIndex, images.length)}
                      </div>
                    )}
                  </Card>
                  
                  {/* Expanded content with all images in the batch */}
                  <CollapsibleContent>
                    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2">
                      {images.map((image, index) => (
                        <Card 
                          key={`${batchId}-${index}`}
                          className={`overflow-hidden cursor-pointer transition-all ${activeIndex === index ? 'ring-2 ring-primary' : ''}`}
                          onClick={() => setActiveImageIndices(prev => ({ ...prev, [batchId]: index }))}
                        >
                          <div className="aspect-square relative">
                            <img
                              src={image.url}
                              alt={`Batch image ${index + 1}`}
                              className="w-full h-full object-cover"
                            />
                            
                            {/* Full screen button */}
                            <Dialog>
                              <DialogTrigger asChild>
                                <button 
                                  className="absolute top-2 right-2 bg-black/70 hover:bg-black/90 rounded-full p-2 text-white transition-colors z-10"
                                  onClick={(e) => e.stopPropagation()}
                                >
                                  <Maximize className="h-4 w-4" />
                                </button>
                              </DialogTrigger>
                              <DialogContent fullscreen>
                                <div className="w-full h-full flex items-center justify-center">
                                  <img
                                    src={image.url}
                                    alt={`Batch image ${index + 1} full view`}
                                    className="max-w-full max-h-full object-contain"
                                  />
                                </div>
                              </DialogContent>
                            </Dialog>
                            
                            {/* Image number indicator */}
                            <div className="absolute bottom-2 right-2 bg-black/70 text-white px-2 py-1 rounded-full text-xs">
                              {index + 1}
                            </div>
                          </div>
                        </Card>
                      ))}
                    </div>
                  </CollapsibleContent>
                </Collapsible>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
};

export default ImageDisplay;
