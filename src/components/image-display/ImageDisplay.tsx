
import React, { useState, useEffect } from 'react';
import { DndContext, closestCenter, KeyboardSensor, PointerSensor, useSensor, useSensors, DragEndEvent } from '@dnd-kit/core';
import { arrayMove, SortableContext, sortableKeyboardCoordinates, verticalListSortingStrategy, horizontalListSortingStrategy } from '@dnd-kit/sortable';
import { Card, CardContent } from '@/components/ui/card';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { GridIcon, List, LayoutList, Maximize2 } from 'lucide-react';
import ImageBatch from './ImageBatch';
import ImageDetailView from './ImageDetailView';
import ReferenceImagesSection from './ReferenceImagesSection';
import { useIsMobile } from '@/hooks/use-mobile'; // Fixed: useMobile → useIsMobile
import LoadingPlaceholder from './LoadingPlaceholder';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';

// Make sure this type definition is consistent across files
export type ViewMode = 'normal' | 'small' | 'table' | 'fullWidth';

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
  const [activeImage, setActiveImage] = useState<string | null>(null);
  const [activePrompt, setActivePrompt] = useState<string | null>(null);
  const [activeBatchId, setActiveBatchId] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>(isMobile ? 'small' : 'normal');
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
  
  useEffect(() => {
    if (!activeImage && generatedImages.length > 0) {
      const latestImage = generatedImages[0];
      setActiveImage(latestImage.url);
      setActivePrompt(latestImage.prompt);
      setActiveBatchId(latestImage.batchId);
    }
  }, [generatedImages, activeImage]);
  
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
  
  const handleImageClick = (batchId: string, imageUrl: string, prompt: string) => {
    setActiveImage(imageUrl);
    setActivePrompt(prompt);
    setActiveBatchId(batchId);
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
  
  const getViewModeIcon = (mode: ViewMode) => {
    switch (mode) {
      case 'normal':
        return <GridIcon className="h-4 w-4" />;
      case 'small':
        return <Maximize2 className="h-4 w-4" />;
      case 'table':
        return <List className="h-4 w-4" />;
      case 'fullWidth':
        return <LayoutList className="h-4 w-4" />;
      default:
        return <GridIcon className="h-4 w-4" />;
    }
  };

  const batches = getImageBatches();
  const hasBatches = Object.keys(batches).length > 0;
  
  return (
    <div className="mt-8">
      {uploadedImages.length > 0 && (
        <ReferenceImagesSection images={uploadedImages} />
      )}
      
      {activeImage && !isLoading && (
        <div className="mt-4">
          {/* Fixed: passing correct props structure to ImageDetailView */}
          <ImageDetailView 
            batchId={activeBatchId || ""}
            images={[{
              url: activeImage,
              prompt: activePrompt || "",
              workflow: workflow || "text-to-image",
              status: "completed",
              params: generationParams
            }]}
            activeIndex={0}
            onSetActiveIndex={() => {}}
            onNavigatePrev={() => {}}
            onNavigateNext={() => {}}
            onToggleExpand={() => {}}
            onDeleteImage={onDeleteImage}
            onCreateAgain={() => onCreateAgain(activeBatchId || undefined)}
            onUseAsInput={onUseGeneratedAsInput}
          />
        </div>
      )}
      
      {isLoading && (
        <Card className="mt-4">
          <CardContent className="pt-6 pb-4">
            {/* Fixed: changed message to prompt */}
            <LoadingPlaceholder prompt={prompt} />
          </CardContent>
        </Card>
      )}
      
      {hasBatches && (
        <div className="mt-8">
          <div className="flex justify-between items-center mb-4">
            <h2 className="text-xl font-bold">Generated Images</h2>
            <Tabs 
              defaultValue={viewMode} 
              value={viewMode} 
              onValueChange={(value) => setViewMode(value as ViewMode)}
              className="w-auto"
            >
              <TabsList className="grid grid-cols-4 h-8 w-auto">
                <Tooltip>
                  <TooltipTrigger asChild>
                    <TabsTrigger value="normal" className="px-2">
                      <GridIcon className="h-4 w-4" />
                    </TabsTrigger>
                  </TooltipTrigger>
                  <TooltipContent>Grid View</TooltipContent>
                </Tooltip>
                
                <Tooltip>
                  <TooltipTrigger asChild>
                    <TabsTrigger value="small" className="px-2">
                      <Maximize2 className="h-4 w-4" />
                    </TabsTrigger>
                  </TooltipTrigger>
                  <TooltipContent>Compact View</TooltipContent>
                </Tooltip>
                
                <Tooltip>
                  <TooltipTrigger asChild>
                    <TabsTrigger value="table" className="px-2">
                      <List className="h-4 w-4" />
                    </TabsTrigger>
                  </TooltipTrigger>
                  <TooltipContent>Table View</TooltipContent>
                </Tooltip>
                
                <Tooltip>
                  <TooltipTrigger asChild>
                    <TabsTrigger value="fullWidth" className="px-2">
                      <LayoutList className="h-4 w-4" />
                    </TabsTrigger>
                  </TooltipTrigger>
                  <TooltipContent>Full Width View</TooltipContent>
                </Tooltip>
              </TabsList>
            </Tabs>
          </div>
          
          <ScrollArea className="h-[calc(100vh-24rem)] pr-4">
            <DndContext 
              sensors={sensors}
              collisionDetection={closestCenter}
              onDragEnd={handleDragEnd}
            >
              <SortableContext 
                items={imageContainerOrder}
                strategy={viewMode === 'fullWidth' ? verticalListSortingStrategy : horizontalListSortingStrategy}
              >
                <div className={viewMode === 'fullWidth' ? 'space-y-4' : ''}>
                  {imageContainerOrder.map(batchId => {
                    if (!batches[batchId]) return null;
                    
                    return (
                      <ImageBatch
                        key={batchId}
                        batchId={batchId}
                        images={batches[batchId]}
                        isExpanded={!!expandedContainers[batchId]}
                        toggleExpand={handleToggleExpand}
                        onImageClick={(url, prompt) => handleImageClick(batchId, url, prompt)}
                        onCreateAgain={() => onCreateAgain(batchId)}
                        onDeleteImage={(index) => onDeleteImage(batchId, index)}
                        onDeleteContainer={() => onDeleteContainer(batchId)}
                        activeImageUrl={activeImage}
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
