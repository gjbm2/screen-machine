
import React, { useState, useEffect } from 'react';
import { DndContext, closestCenter, KeyboardSensor, PointerSensor, useSensor, useSensors, DragEndEvent } from '@dnd-kit/core';
import { arrayMove, SortableContext, sortableKeyboardCoordinates, verticalListSortingStrategy, horizontalListSortingStrategy } from '@dnd-kit/sortable';
import { Card, CardContent } from '@/components/ui/card';
import { LayoutGrid, Grid, List } from 'lucide-react';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import ImageBatch from './ImageBatch';
import LoadingPlaceholder from './LoadingPlaceholder';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import ReferenceImagesSection from './ReferenceImagesSection';
import { Dialog, DialogContent } from '@/components/ui/dialog';
import ImageDetailView from './ImageDetailView';

// Export ViewMode type but remove 'large' as an option
export type ViewMode = 'normal' | 'small' | 'table';

interface ImageDisplayProps {
  imageUrl: string | null;
  prompt: string | null;
  isLoading: boolean;
  uploadedImages: string[];
  generatedImages: any[];
  imageContainerOrder: string[];
  workflow: string | null;
  generationParams?: Record<string, any>;
  onUseGeneratedAsInput: (url: string) => void;
  onCreateAgain: (batchId?: string) => void;
  onReorderContainers: (sourceIndex: number, destinationIndex: number) => void;
  onDeleteImage: (batchId: string, index: number) => void;
  onDeleteContainer: (batchId: string) => void;
}

const ImageDisplay: React.FC<ImageDisplayProps> = ({
  imageUrl,
  prompt,
  isLoading,
  uploadedImages,
  generatedImages,
  imageContainerOrder,
  workflow,
  generationParams,
  onUseGeneratedAsInput,
  onCreateAgain,
  onReorderContainers,
  onDeleteImage,
  onDeleteContainer
}) => {
  const [viewMode, setViewMode] = useState<ViewMode>('normal');
  const [expandedContainers, setExpandedContainers] = useState<Record<string, boolean>>({});
  const [selectedImage, setSelectedImage] = useState<{ url: string; prompt: string; batchId: string; index: number } | null>(null);
  
  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: {
        distance: 8,
      },
    }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    })
  );

  useEffect(() => {
    if (imageContainerOrder.length > 0 && isLoading) {
      const container = document.getElementById(imageContainerOrder[0]);
      if (container) {
        container.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      }
    }
  }, [imageContainerOrder, isLoading]);

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    
    if (over && active.id !== over.id) {
      const oldIndex = imageContainerOrder.findIndex(id => id === active.id);
      const newIndex = imageContainerOrder.findIndex(id => id === over.id);
      
      if (oldIndex !== -1 && newIndex !== -1) {
        onReorderContainers(oldIndex, newIndex);
      }
    }
  };
  
  const handleToggleExpand = (batchId: string) => {
    setExpandedContainers(prev => ({
      ...prev,
      [batchId]: !prev[batchId]
    }));
  };
  
  const getImageBatches = () => {
    const batches: Record<string, any[]> = {};
    
    generatedImages.forEach(image => {
      if (!batches[image.batchId]) {
        batches[image.batchId] = [];
      }
      batches[image.batchId].push(image);
    });
    
    return batches;
  };

  const batches = getImageBatches();
  const hasBatches = Object.keys(batches).length > 0 || isLoading;
  
  const handleCreateAgain = (batchId?: string) => {
    onCreateAgain(batchId);
    
    if (imageContainerOrder.length > 0) {
      setTimeout(() => {
        setExpandedContainers(prev => ({
          ...prev,
          [imageContainerOrder[0]]: true
        }));
      }, 100);
    }
  };
  
  // Function to handle small view image click to open full screen detail view
  const handleSmallImageClick = (image: any) => {
    if (viewMode === 'small' && image.url) {
      setSelectedImage({
        url: image.url,
        prompt: image.prompt || '',
        batchId: image.batchId,
        index: image.batchIndex
      });
    }
  };
  
  // Flatten all images for small view
  const getAllImages = () => {
    return generatedImages
      .filter(img => img.status === 'completed')
      .sort((a, b) => {
        // Sort by container order first, then by batch index
        const aContainerIndex = imageContainerOrder.indexOf(a.batchId);
        const bContainerIndex = imageContainerOrder.indexOf(b.batchId);
        
        if (aContainerIndex !== bContainerIndex) {
          return aContainerIndex - bContainerIndex;
        }
        
        return a.batchIndex - b.batchIndex;
      });
  };
  
  return (
    <div className="mt-8">
      {uploadedImages.length > 0 && (
        <ReferenceImagesSection images={uploadedImages} />
      )}
      
      {hasBatches && (
        <div className="mt-8">
          <div className="flex justify-between items-center mb-4">
            <h2 className="text-xl font-bold">Generated Images</h2>
            <Tabs 
              defaultValue="normal" 
              value={viewMode} 
              onValueChange={(value) => setViewMode(value as ViewMode)}
              className="w-auto"
            >
              <TabsList className="grid grid-cols-3 h-8 w-auto">
                <Tooltip>
                  <TooltipTrigger asChild>
                    <TabsTrigger value="normal" className="px-1.5 sm:px-2">
                      <LayoutGrid className="h-4 w-4" />
                    </TabsTrigger>
                  </TooltipTrigger>
                  <TooltipContent>Normal View</TooltipContent>
                </Tooltip>
                
                <Tooltip>
                  <TooltipTrigger asChild>
                    <TabsTrigger value="small" className="px-1.5 sm:px-2">
                      <Grid className="h-4 w-4" />
                    </TabsTrigger>
                  </TooltipTrigger>
                  <TooltipContent>Small View</TooltipContent>
                </Tooltip>
                
                <Tooltip>
                  <TooltipTrigger asChild>
                    <TabsTrigger value="table" className="px-1.5 sm:px-2">
                      <List className="h-4 w-4" />
                    </TabsTrigger>
                  </TooltipTrigger>
                  <TooltipContent>Table View</TooltipContent>
                </Tooltip>
              </TabsList>
            </Tabs>
          </div>
          
          <div className="pr-4">
            <DndContext 
              sensors={sensors}
              collisionDetection={closestCenter}
              onDragEnd={handleDragEnd}
            >
              <SortableContext 
                items={imageContainerOrder}
                strategy={viewMode === 'small' ? horizontalListSortingStrategy : verticalListSortingStrategy}
              >
                {viewMode === 'small' ? (
                  <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-2">
                    {getAllImages().map((image, idx) => (
                      <div 
                        key={`${image.batchId}-${image.batchIndex}`} 
                        className="aspect-square rounded-md overflow-hidden cursor-pointer"
                        onClick={() => handleSmallImageClick(image)}
                      >
                        <img 
                          src={image.url}
                          alt={image.prompt || `Generated image ${idx + 1}`}
                          className="w-full h-full object-cover"
                        />
                      </div>
                    ))}
                    {isLoading && (
                      <div className="aspect-square rounded-md overflow-hidden bg-muted flex items-center justify-center">
                        <div className="animate-pulse flex flex-col items-center">
                          <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent"></div>
                        </div>
                      </div>
                    )}
                  </div>
                ) : viewMode === 'table' ? (
                  <div className="space-y-4">
                    {imageContainerOrder.map(batchId => {
                      if (!batches[batchId]) return null;
                      
                      return (
                        <ImageBatch
                          key={batchId}
                          batchId={batchId}
                          images={batches[batchId]}
                          isExpanded={true}
                          toggleExpand={handleToggleExpand}
                          onImageClick={(url, prompt) => {}}
                          onCreateAgain={() => handleCreateAgain(batchId)}
                          onDeleteImage={onDeleteImage}
                          onDeleteContainer={() => onDeleteContainer(batchId)}
                          activeImageUrl={imageUrl}
                          viewMode="table"
                        />
                      );
                    })}
                  </div>
                ) : (
                  <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                    {imageContainerOrder.map(batchId => {
                      if (!batches[batchId]) return null;
                      
                      return (
                        <div key={batchId} id={batchId} className={expandedContainers[batchId] ? "col-span-full" : ""}>
                          <ImageBatch
                            batchId={batchId}
                            images={batches[batchId]}
                            isExpanded={!!expandedContainers[batchId]}
                            toggleExpand={handleToggleExpand}
                            onImageClick={(url, prompt) => {
                              if (url) {
                                onUseGeneratedAsInput(url);
                              }
                            }}
                            onCreateAgain={() => handleCreateAgain(batchId)}
                            onDeleteImage={onDeleteImage}
                            onDeleteContainer={() => onDeleteContainer(batchId)}
                            activeImageUrl={imageUrl}
                            viewMode="normal"
                          />
                        </div>
                      );
                    })}
                  </div>
                )}
              </SortableContext>
            </DndContext>
          </div>
          
          {/* Fullscreen detail view for small mode */}
          <Dialog open={!!selectedImage} onOpenChange={(open) => !open && setSelectedImage(null)}>
            <DialogContent className="max-w-4xl">
              {selectedImage && (
                <div className="flex flex-col">
                  <div className="relative rounded-md overflow-hidden mb-4">
                    <img 
                      src={selectedImage.url} 
                      alt={selectedImage.prompt}
                      className="w-full h-auto object-contain max-h-[70vh]" 
                    />
                  </div>
                  {selectedImage.prompt && (
                    <div className="text-sm text-muted-foreground mt-2">
                      {selectedImage.prompt}
                    </div>
                  )}
                </div>
              )}
            </DialogContent>
          </Dialog>
        </div>
      )}
    </div>
  );
};

export default ImageDisplay;
