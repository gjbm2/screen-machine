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
  DialogClose,
} from "@/components/ui/dialog";
import { Button } from '@/components/ui/button';
import { Info, Download, Share2, Plus, ChevronLeft, ChevronRight, Maximize, ChevronDown, ChevronUp, GripVertical, Trash2, X } from 'lucide-react';
import { toast } from 'sonner';
import ImageActions from '@/components/ImageActions';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { useIsMobile } from '@/hooks/use-mobile';
import { DndContext, closestCenter, KeyboardSensor, PointerSensor, useSensor, useSensors, DragEndEvent } from '@dnd-kit/core';
import { SortableContext, verticalListSortingStrategy, useSortable, arrayMove } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card";
import { TooltipProvider } from '@/components/ui/tooltip';

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
  imageContainerOrder?: string[];
  workflow?: string | null;
  onUseGeneratedAsInput?: ((imageUrl: string) => void) | null;
  onCreateAgain?: (batchId?: string) => void;
  onReorderContainers?: (sourceIndex: number, destinationIndex: number) => void;
  onDeleteImage?: (batchId: string, imageIndex: number) => void;
  onDeleteContainer?: (batchId: string) => void;
  generationParams?: Record<string, any>;
}

// Sortable container component
const SortableContainer = ({ 
  batchId, 
  children,
  batch,
  expandedBatches,
  toggleExpandBatch,
}: { 
  batchId: string; 
  children: React.ReactNode;
  batch: any;
  expandedBatches: Record<string, boolean>;
  toggleExpandBatch: (id: string) => void;
}) => {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: batchId });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.6 : 1,
    zIndex: isDragging ? 10 : 1,
    position: 'relative' as 'relative',
    marginBottom: '1rem',
  };

  return (
    <div ref={setNodeRef} style={style}>
      <div className="flex items-center justify-between bg-card rounded-t-lg py-2 px-4 border-b">
        <div 
          {...attributes} 
          {...listeners}
          className="cursor-grab active:cursor-grabbing p-1"
        >
          <GripVertical className="h-5 w-5 opacity-70" />
        </div>
        <div className="flex-1 truncate mx-2 text-sm text-muted-foreground">
          <span className="font-medium">Prompt:</span> {batch.images[0]?.prompt || 'Generated Image'}
        </div>
        <CollapsibleTrigger 
          onClick={() => toggleExpandBatch(batchId)}
          className="p-1 rounded-full hover:bg-accent/50 transition-colors"
        >
          {expandedBatches[batchId] ? (
            <ChevronUp className="h-5 w-5" />
          ) : (
            <ChevronDown className="h-5 w-5" />
          )}
        </CollapsibleTrigger>
      </div>
      {children}
    </div>
  );
};

// Image display component
const ImageDisplay: React.FC<ImageDisplayProps> = ({ 
  imageUrl, 
  prompt, 
  isLoading,
  uploadedImages = [],
  generatedImages = [],
  imageContainerOrder = [],
  workflow,
  onUseGeneratedAsInput,
  onCreateAgain,
  onReorderContainers,
  onDeleteImage,
  onDeleteContainer,
  generationParams
}) => {
  // State to track the currently viewed image in each batch
  const [activeImageIndices, setActiveImageIndices] = useState<Record<string, number>>({});
  // State to track which batch is currently being interacted with (for hover persistence)
  const [activeBatchId, setActiveBatchId] = useState<string | null>(null);
  // State to track which batches are expanded (unrolled)
  const [expandedBatches, setExpandedBatches] = useState<Record<string, boolean>>({});
  // Track deleted image indices for each batch
  const [deletedImages, setDeletedImages] = useState<Record<string, Set<number>>>({});
  // Track the focused batch ID for auto-scrolling to new variants
  const [focusBatchId, setFocusBatchId] = useState<string | null>(null);
  
  const isMobile = useIsMobile();
  
  // Configure DnD sensors
  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: {
        distance: 8,
      },
    }),
    useSensor(KeyboardSensor)
  );
  
  // Scroll to newly created variant
  React.useEffect(() => {
    if (focusBatchId) {
      const element = document.getElementById(`batch-${focusBatchId}`);
      if (element) {
        element.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      }
      setFocusBatchId(null);
    }
  }, [focusBatchId, generatedImages]);
  
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
    
    // Filter out deleted images from each batch
    Object.entries(batches).forEach(([batchId, images]) => {
      if (deletedImages[batchId]) {
        batches[batchId] = images.filter((_, index) => !deletedImages[batchId].has(index));
      }
    });
    
    // Remove empty batches
    Object.entries(batches).forEach(([batchId, images]) => {
      if (images.length === 0) {
        delete batches[batchId];
      }
    });
    
    // Order batches according to imageContainerOrder
    const orderedBatches = [];
    
    // First add batches in the order specified by imageContainerOrder
    for (const batchId of imageContainerOrder) {
      if (batches[batchId]) {
        orderedBatches.push({
          batchId,
          images: batches[batchId].sort((a, b) => (b.batchIndex || 0) - (a.batchIndex || 0))
        });
        delete batches[batchId];
      }
    }
    
    // Then add any remaining batches by timestamp (newest first)
    Object.entries(batches)
      .sort(([, imagesA], [, imagesB]) => {
        const timeA = imagesA[0]?.timestamp || 0;
        const timeB = imagesB[0]?.timestamp || 0;
        return timeB - timeA;
      })
      .forEach(([batchId, images]) => {
        orderedBatches.push({
          batchId,
          images: images.sort((a, b) => (b.batchIndex || 0) - (a.batchIndex || 0))
        });
      });
    
    return orderedBatches;
  };

  // Get active image for a batch
  const getActiveImageIndex = (batchId: string, imagesCount: number) => {
    if (activeImageIndices[batchId] === undefined) {
      return 0;
    }
    return Math.min(activeImageIndices[batchId], imagesCount - 1);
  };

  // Delete an image from a batch
  const handleDeleteImage = (batchId: string, imageIndex: number) => {
    setDeletedImages(prev => {
      const newDeletedImages = { ...prev };
      if (!newDeletedImages[batchId]) {
        newDeletedImages[batchId] = new Set();
      }
      newDeletedImages[batchId].add(imageIndex);
      
      // If this was the last image in the batch, update the expanded state
      const batch = getBatchedImages().find(batch => batch.batchId === batchId);
      const remainingImagesCount = batch ? 
        batch.images.length - (newDeletedImages[batchId].size || 0) : 0;
      
      if (remainingImagesCount <= 1) {
        // Remove the batch from imageContainerOrder
        if (onReorderContainers) {
          const index = imageContainerOrder.indexOf(batchId);
          if (index !== -1) {
            // This effectively removes the batch from the order
            onReorderContainers(index, imageContainerOrder.length);
          }
        }
      }
      
      return newDeletedImages;
    });
    
    toast.success('Image deleted');
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

  // Handle drag end for reordering
  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    
    if (over && active.id !== over.id) {
      const oldIndex = imageContainerOrder.indexOf(String(active.id));
      const newIndex = imageContainerOrder.indexOf(String(over.id));
      
      if (onReorderContainers) {
        onReorderContainers(oldIndex, newIndex);
      }
    }
  };

  // Format workflow name for display (remove hyphens and capitalize)
  const formatWorkflowName = (name: string) => {
    return name
      .split('-')
      .map(word => word.charAt(0).toUpperCase() + word.slice(1))
      .join(' ');
  };

  const handleCreateAnother = (batchId: string) => {
    if (onCreateAgain) {
      // Set focus batch ID to auto-scroll to the new variant
      setFocusBatchId(batchId);
      onCreateAgain(batchId);
      toast.info('Creating another image...');
    }
  };

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
      <div className="aspect-square relative group">
        <img
          src={image.url}
          alt={image.prompt || 'Generated image'}
          className="w-full h-full object-cover"
        />
        
        {/* Delete button - always visible */}
        <button 
          className="absolute top-2 left-2 bg-black/70 hover:bg-black/90 rounded-full p-2 text-white transition-colors z-20"
          onClick={(e) => {
            e.stopPropagation();
            handleDeleteImage(batchId, index);
          }}
        >
          <Trash2 className="h-4 w-4" />
        </button>
        
        {/* Full screen view button - always visible */}
        <Dialog>
          <DialogTrigger asChild>
            <button 
              className="absolute top-2 right-2 bg-black/70 hover:bg-black/90 rounded-full p-2 text-white transition-colors z-20"
              onClick={(e) => e.stopPropagation()}
            >
              <Maximize className="h-4 w-4" />
            </button>
          </DialogTrigger>
          <DialogContent className="p-0 overflow-hidden" fullscreen>
            <DialogHeader className="absolute top-0 left-0 right-0 bg-black/80 z-10 p-4 flex justify-between items-start">
              <div>
                <DialogTitle className="text-white">Image View</DialogTitle>
                <DialogDescription className="text-white/70 truncate pr-10">
                  {image.prompt}
                </DialogDescription>
              </div>
              <DialogClose className="rounded-full p-1 hover:bg-black/40 text-white">
                <X className="h-6 w-6" />
              </DialogClose>
            </DialogHeader>
            
            <div className="w-full h-full flex flex-col">
              <div className="flex-1 flex items-center justify-center overflow-auto">
                <div className="w-full h-full flex items-center justify-center overflow-auto">
                  <img
                    src={image.url}
                    alt={image.prompt || 'Generated image full view'}
                    className="max-w-full max-h-full object-contain"
                  />
                </div>
              </div>
              
              {/* Bottom controls for fullscreen view */}
              <div className="p-4 bg-black/80 flex flex-wrap justify-center gap-2">
                <TooltipProvider>
                  <ImageActions 
                    imageUrl={image.url}
                    onCreateAgain={() => handleCreateAnother(batchId)}
                    onUseAsInput={() => onUseGeneratedAsInput && onUseGeneratedAsInput(image.url)}
                    generationInfo={{
                      prompt: image.prompt,
                      workflow: image.workflow,
                      params: image.params
                    }}
                    isFullScreen
                  />
                </TooltipProvider>
              </div>
            </div>
          </DialogContent>
        </Dialog>
        
        {/* Batch counter */}
        {total > 1 && (
          <div className="absolute top-10 left-2 bg-black/70 text-white px-3 py-1 rounded-full text-xs font-medium z-10">
            {index + 1}/{total}
          </div>
        )}
        
        {/* Image controls overlay - REDUCED HEIGHT to 20% of image */}
        <div className="absolute bottom-0 left-0 right-0 bg-black/60 flex justify-center p-3 opacity-0 group-hover:opacity-100 transition-opacity z-10 h-[20%]">
          <div className="flex flex-wrap gap-2 justify-center">
            <TooltipProvider>
              <ImageActions 
                imageUrl={image.url}
                onCreateAgain={() => handleCreateAnother(batchId)}
                onUseAsInput={() => onUseGeneratedAsInput && onUseGeneratedAsInput(image.url)}
                generationInfo={{
                  prompt: image.prompt,
                  workflow: image.workflow,
                  params: image.params
                }}
              />
            </TooltipProvider>
          </div>
        </div>
      </div>
    );
  };

  // Render new variant placeholder
  const renderNewVariantPlaceholder = (batchId: string) => {
    return (
      <Card
        className="overflow-hidden cursor-pointer border-dashed border-2 bg-secondary/10 hover:bg-secondary/20 transition-colors"
        onClick={() => handleCreateAnother(batchId)}
      >
        <div className="aspect-square flex flex-col items-center justify-center p-4 text-muted-foreground">
          <Plus className="h-12 w-12 mb-2 text-primary/60" />
          <p className="text-sm font-medium">Create Another</p>
          <p className="text-xs mt-1 text-center">Click to generate a new image based on this prompt</p>
        </div>
      </Card>
    );
  };

  // Get batched images
  const batchedImages = getBatchedImages();
  const sortableIds = batchedImages.map(batch => batch.batchId);

  return (
    <div className="mt-12 animate-fade-in">
      <div className="flex flex-col gap-6">
        {/* Reference images section */}
        {uploadedImages && uploadedImages.length > 0 && (
          <div>
            <h3 className="text-lg font-semibold mb-3">Reference Images</h3>
            <div className="overflow-hidden bg-secondary/20 rounded-lg">
              {uploadedImages.length === 1 ? (
                <div className="aspect-square w-full max-w-xs mx-auto overflow-hidden relative">
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
                      <DialogHeader>
                        <DialogTitle className="sr-only">Reference Image</DialogTitle>
                      </DialogHeader>
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
        )}

        {/* Generated images section */}
        <div>
          <h3 className="text-lg font-semibold mb-3">Generated Images</h3>
          
          <div className="space-y-4">
            {/* If loading and no existing images, show single loading placeholder */}
            {isLoading && generatedImages.length === 0 && (
              <Card className="overflow-hidden max-w-xs mx-auto">
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
            <DndContext 
              sensors={sensors}
              collisionDetection={closestCenter}
              onDragEnd={handleDragEnd}
            >
              <SortableContext 
                items={sortableIds}
                strategy={verticalListSortingStrategy}
              >
                <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4">
                  {batchedImages.map(({ batchId, images }) => {
                    // Skip empty batches
                    if (images.length === 0) return null;
                    
                    const filteredImages = deletedImages[batchId] 
                      ? images.filter((_, i) => !deletedImages[batchId].has(i))
                      : images;
                    
                    // Skip if all images in batch are deleted
                    if (filteredImages.length === 0) return null;
                    
                    const activeIndex = getActiveImageIndex(batchId, filteredImages.length);
                    const activeImage = filteredImages[activeIndex];
                    const isActive = activeBatchId === batchId;
                    const isExpanded = expandedBatches[batchId];
                    
                    if (isExpanded) {
                      return (
                        <Collapsible 
                          key={batchId} 
                          open={isExpanded}
                          className="overflow-hidden rounded-lg bg-card border shadow-md col-span-1 sm:col-span-2 md:col-span-3 lg:col-span-4 xl:col-span-5"
                          id={`batch-${batchId}`}
                        >
                          <SortableContainer 
                            batchId={batchId} 
                            batch={{ images: filteredImages }}
                            expandedBatches={expandedBatches}
                            toggleExpandBatch={toggleExpandBatch}
                          >
                            <Card 
                              className="overflow-hidden relative border-0 rounded-none"
                              onMouseEnter={() => setActiveBatchId(batchId)}
                              onMouseLeave={() => setActiveBatchId(null)}
                            >
                              {/* Empty content for expanded view */}
                            </Card>
                          </SortableContainer>
                          
                          {/* Expanded content with all images in the batch */}
                          <CollapsibleContent>
                            <div className="p-4 space-y-4">
                              {/* Selected image view */}
                              <div className="aspect-square relative bg-secondary/10 rounded-md overflow-hidden max-w-lg mx-auto group">
                                {renderBatchImage(activeImage, batchId, true, activeIndex, filteredImages.length)}
                                
                                {/* Navigation controls in expanded view */}
                                {filteredImages.length > 1 && (
                                  <div className="absolute inset-0 pointer-events-none">
                                    <button 
                                      className="absolute left-2 top-1/2 -translate-y-1/2 bg-black/70 hover:bg-black/90 rounded-full p-2 text-white transition-colors pointer-events-auto z-20"
                                      onClick={(e) => {
                                        e.stopPropagation();
                                        navigatePrevImage(batchId, filteredImages.length);
                                      }}
                                    >
                                      <ChevronLeft className="h-5 w-5" />
                                    </button>
                                    <button 
                                      className="absolute right-2 top-1/2 -translate-y-1/2 bg-black/70 hover:bg-black/90 rounded-full p-2 text-white transition-colors pointer-events-auto z-20"
                                      onClick={(e) => {
                                        e.stopPropagation();
                                        navigateNextImage(batchId, filteredImages.length);
                                      }}
                                    >
                                      <ChevronRight className="h-5 w-5" />
                                    </button>
                                  </div>
                                )}
                              </div>
                              
                              {/* Action buttons in expanded view */}
                              <div className="flex flex-wrap justify-center gap-2 mt-4">
                                <ImageActions 
                                  imageUrl={activeImage.url} 
                                  onUseAsInput={() => onUseGeneratedAsInput && onUseGeneratedAsInput(activeImage.url)}
                                  onCreateAgain={() => handleCreateAnother(batchId)}
                                  generationInfo={{
                                    prompt: activeImage.prompt,
                                    workflow: activeImage.workflow,
                                    params: activeImage.params
                                  }}
                                  isFullScreen
                                />
                              </div>
                              
                              {/* Thumbnail gallery */}
                              <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-2">
                                {filteredImages.map((image, index) => (
                                  <Card 
                                    key={`${batchId}-${index}`}
                                    className={`overflow-hidden cursor-pointer transition-all ${activeIndex === index ? 'ring-2 ring-primary' : ''}`}
                                    onClick={() => setActiveImageIndices(prev => ({ ...prev, [batchId]: index }))}
                                  >
                                    <div className="aspect-square relative group">
                                      <img
                                        src={image.url}
                                        alt={`Batch image ${index + 1}`}
                                        className="w-full h-full object-cover"
                                      />
                                      
                                      {/* Delete button on thumbnail */}
                                      <button 
                                        className="absolute top-1 left-1 bg-black/70 hover:bg-black/90 rounded-full p-1 text-white transition-colors z-20 opacity-0 group-hover:opacity-100"
                                        onClick={(e) => {
                                          e.stopPropagation();
                                          handleDeleteImage(batchId, index);
                                        }}
                                      >
                                        <Trash2 className="h-3 w-3" />
                                      </button>
                                      
                                      {/* Image number indicator */}
                                      <div className="absolute bottom-1 right-1 bg-black/70 text-white px-2 py-0.5 rounded-full text-xs">
                                        {index + 1}
                                      </div>
                                    </div>
                                  </Card>
                                ))}
                                
                                {/* New variant placeholder in gallery */}
                                {renderNewVariantPlaceholder(batchId)}
                              </div>
                            </div>
                              
                            {/* Compact roll-up button attached to bottom of container */}
                            <div className="flex justify-center">
                              <Button 
                                variant="ghost" 
                                size="sm"
                                className="rounded-t-none rounded-b-lg bg-card hover:bg-accent/20 text-xs h-7 px-3 -mt-1 border-t border-x shadow"
                                onClick={() => toggleExpandBatch(batchId)}
                              >
                                <ChevronUp className="h-4 w-4 mr-1" />
                                Roll Up
                              </Button>
                            </div>
                          </CollapsibleContent>
                        </Collapsible>
                      );
                    } else {
                      return (
                        <Collapsible 
                          key={batchId} 
                          open={isExpanded}
                          className="overflow-hidden rounded-lg bg-card border col-span-1"
                          id={`batch-${batchId}`}
                        >
                          <SortableContainer 
                            batchId={batchId} 
                            batch={{ images: filteredImages }}
                            expandedBatches={expandedBatches}
                            toggleExpandBatch={toggleExpandBatch}
                          >
                            <Card 
                              className="overflow-hidden relative border-0 rounded-none"
                              onMouseEnter={() => setActiveBatchId(batchId)}
                              onMouseLeave={() => setActiveBatchId(null)}
                            >
                              {/* Collapsed view (carousel-like navigation) */}
                              {renderBatchImage(activeImage, batchId, isActive, activeIndex, filteredImages.length)}
                              
                              {/* Navigation controls */}
                              {filteredImages.length > 1 && (
                                <div className="absolute inset-0 pointer-events-none">
                                  <button 
                                    className="absolute left-2 top-1/2 -translate-y-1/2 bg-black/70 hover:bg-black/90 rounded-full p-2 text-white transition-colors pointer-events-auto z-20"
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      navigatePrevImage(batchId, filteredImages.length);
                                    }}
                                  >
                                    <ChevronLeft className="h-5 w-5" />
                                  </button>
                                  <button 
                                    className="absolute right-2 top-1/2 -translate-y-1/2 bg-black/70 hover:bg-black/90 rounded-full p-2 text-white transition-colors pointer-events-auto z-20"
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      navigateNextImage(batchId, filteredImages.length);
                                    }}
                                  >
                                    <ChevronRight className="h-5 w-5" />
                                  </button>
                                </div>
                              )}
                            </Card>
                          </SortableContainer>
                        </Collapsible>
                      );
                    }
                  })}
                </div>
              </SortableContext>
            </DndContext>
          </div>
        </div>
      </div>
    </div>
  );
};

export default ImageDisplay;
