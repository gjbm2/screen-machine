
import React, { useState, useEffect } from 'react';
import { DndContext, closestCenter, KeyboardSensor, PointerSensor, useSensor, useSensors, DragEndEvent } from '@dnd-kit/core';
import { arrayMove, SortableContext, sortableKeyboardCoordinates, verticalListSortingStrategy, horizontalListSortingStrategy } from '@dnd-kit/sortable';
import { Card, CardContent } from '@/components/ui/card';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { LayoutList, GridIcon, Grid2X2, Table2 } from 'lucide-react';
import ImageBatch from './ImageBatch';
import ImageDetailView from './ImageDetailView';
import ReferenceImagesSection from './ReferenceImagesSection';
import { useIsMobile } from '@/hooks/use-mobile'; 
import LoadingPlaceholder from './LoadingPlaceholder';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';

// Export ViewMode type so it can be used by other components
export type ViewMode = 'large' | 'normal' | 'small' | 'table';

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
  const isMobile = useIsMobile();
  const [viewMode, setViewMode] = useState<ViewMode>(isMobile ? 'normal' : 'normal');
  const [expandedContainers, setExpandedContainers] = useState<Record<string, boolean>>({});
  
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
  const hasBatches = Object.keys(batches).length > 0;
  
  return (
    <div className="mt-8">
      {uploadedImages.length > 0 && (
        <ReferenceImagesSection images={uploadedImages} />
      )}
      
      {/* Generated Images Section */}
      {(hasBatches || isLoading) && (
        <div className="mt-8">
          <div className="flex justify-between items-center mb-4">
            <h2 className="text-xl font-bold">Generated Images</h2>
            <Tabs 
              defaultValue="normal" 
              value={viewMode} 
              onValueChange={(value) => setViewMode(value as ViewMode)}
              className="w-auto"
            >
              <TabsList className="grid grid-cols-4 h-8 w-auto">
                <Tooltip>
                  <TooltipTrigger asChild>
                    <TabsTrigger value="large" className="px-1.5 sm:px-2">
                      <LayoutList className="h-4 w-4" />
                    </TabsTrigger>
                  </TooltipTrigger>
                  <TooltipContent>Large View</TooltipContent>
                </Tooltip>
                
                <Tooltip>
                  <TooltipTrigger asChild>
                    <TabsTrigger value="normal" className="px-1.5 sm:px-2">
                      <GridIcon className="h-4 w-4" />
                    </TabsTrigger>
                  </TooltipTrigger>
                  <TooltipContent>Normal View</TooltipContent>
                </Tooltip>
                
                <Tooltip>
                  <TooltipTrigger asChild>
                    <TabsTrigger value="small" className="px-1.5 sm:px-2">
                      <Grid2X2 className="h-4 w-4" />
                    </TabsTrigger>
                  </TooltipTrigger>
                  <TooltipContent>Small View</TooltipContent>
                </Tooltip>
                
                <Tooltip>
                  <TooltipTrigger asChild>
                    <TabsTrigger value="table" className="px-1.5 sm:px-2">
                      <Table2 className="h-4 w-4" />
                    </TabsTrigger>
                  </TooltipTrigger>
                  <TooltipContent>Table View</TooltipContent>
                </Tooltip>
              </TabsList>
            </Tabs>
          </div>
          
          {isLoading && (
            <Card className="mb-4">
              <CardContent className="pt-6 pb-4">
                <LoadingPlaceholder prompt={prompt} />
              </CardContent>
            </Card>
          )}
          
          <ScrollArea className="h-[calc(100vh-24rem)] pr-4">
            <DndContext 
              sensors={sensors}
              collisionDetection={closestCenter}
              onDragEnd={handleDragEnd}
            >
              <SortableContext 
                items={imageContainerOrder}
                strategy={viewMode === 'large' ? verticalListSortingStrategy : horizontalListSortingStrategy}
              >
                <div className={`
                  ${viewMode === 'large' ? 'space-y-4' : ''} 
                  ${viewMode === 'small' ? 'grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-2' : ''}
                  ${viewMode === 'normal' ? 'grid grid-cols-1 sm:grid-cols-2 gap-4' : ''}
                  ${viewMode === 'table' ? '' : ''}
                `}>
                  {imageContainerOrder.map(batchId => {
                    if (!batches[batchId]) return null;
                    
                    return (
                      <ImageBatch
                        key={batchId}
                        batchId={batchId}
                        images={batches[batchId]}
                        isExpanded={!!expandedContainers[batchId] || viewMode === 'large'}
                        toggleExpand={handleToggleExpand}
                        onImageClick={(url, prompt) => {}}
                        onCreateAgain={() => onCreateAgain(batchId)}
                        onDeleteImage={onDeleteImage}
                        onDeleteContainer={() => onDeleteContainer(batchId)}
                        activeImageUrl={imageUrl}
                        viewMode={viewMode}
                      />
                    );
                  })}
                </div>
              </SortableContext>
            </DndContext>
          </ScrollArea>
        </div>
      )}
    </div>
  );
};

export default ImageDisplay;
