import React, { useState, useEffect } from 'react';
import { DndContext, closestCenter, KeyboardSensor, PointerSensor, useSensor, useSensors, DragEndEvent } from '@dnd-kit/core';
import { SortableContext, verticalListSortingStrategy } from '@dnd-kit/sortable';
import { toast } from 'sonner';
import ImageBatch from './ImageBatch';
import LoadingPlaceholder from './LoadingPlaceholder';
import ReferenceImageIndicator from './ReferenceImageIndicator';
import { DropdownMenu } from '@/components/ui/dropdown-menu';
import { TooltipProvider } from '@/components/ui/tooltip';
import { Table, TableHeader, TableRow, TableHead, TableBody, TableCell } from '@/components/ui/table';
import { Button } from '@/components/ui/button';
import { LayoutGrid, List, Table2, Maximize, ExternalLink } from 'lucide-react';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { formatDistanceToNow } from 'date-fns';
import { Dialog, DialogContent } from '@/components/ui/dialog';

type ViewMode = 'normal' | 'small' | 'table';

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
    refiner?: string;
    referenceImageUrl?: string;
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
  const [activeImageIndices, setActiveImageIndices] = useState<Record<string, number>>({});
  const [activeBatchId, setActiveBatchId] = useState<string | null>(null);
  const [expandedBatches, setExpandedBatches] = useState<Record<string, boolean>>({});
  const [deletedImages, setDeletedImages] = useState<Record<string, Set<number>>>({});
  const [focusBatchId, setFocusBatchId] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>('normal');
  const [openBatchDialog, setOpenBatchDialog] = useState<string | null>(null);
  
  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: {
        distance: 8,
      },
    }),
    useSensor(KeyboardSensor)
  );
  
  useEffect(() => {
    if (focusBatchId) {
      const element = document.getElementById(`batch-${focusBatchId}`);
      if (element) {
        element.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      }
      setFocusBatchId(null);
    }
  }, [focusBatchId, generatedImages]);
  
  const shouldDisplay = isLoading || generatedImages.length > 0;
  
  if (!shouldDisplay) return null;

  const getBatchedImages = () => {
    const batches: Record<string, typeof generatedImages> = {};
    
    generatedImages.forEach(img => {
      const batchId = img.batchId || `single-${img.timestamp}`;
      if (!batches[batchId]) {
        batches[batchId] = [];
      }
      batches[batchId].push(img);
    });
    
    Object.entries(batches).forEach(([batchId, images]) => {
      if (deletedImages[batchId]) {
        batches[batchId] = images.filter((_, index) => !deletedImages[batchId].has(index));
      }
    });
    
    Object.entries(batches).forEach(([batchId, images]) => {
      if (images.length === 0) {
        delete batches[batchId];
      }
    });
    
    const orderedBatches = [];
    
    for (const batchId of imageContainerOrder) {
      if (batches[batchId]) {
        const sortedImages = batches[batchId].sort((a, b) => {
          if (a.referenceImageUrl && !b.referenceImageUrl) return -1;
          if (!a.referenceImageUrl && b.referenceImageUrl) return 1;
          return (b.batchIndex || 0) - (a.batchIndex || 0);
        });
        
        orderedBatches.push({
          batchId,
          images: sortedImages
        });
        delete batches[batchId];
      }
    }
    
    Object.entries(batches)
      .sort(([, imagesA], [, imagesB]) => {
        const timeA = imagesA[0]?.timestamp || 0;
        const timeB = imagesB[0]?.timestamp || 0;
        return timeB - timeA;
      })
      .forEach(([batchId, images]) => {
        const sortedImages = images.sort((a, b) => {
          if (a.referenceImageUrl && !b.referenceImageUrl) return -1;
          if (!a.referenceImageUrl && b.referenceImageUrl) return 1;
          return (b.batchIndex || 0) - (a.batchIndex || 0);
        });
        
        orderedBatches.push({
          batchId,
          images: sortedImages
        });
      });
    
    return orderedBatches;
  };

  const getActiveImageIndex = (batchId: string, imagesCount: number) => {
    if (activeImageIndices[batchId] === undefined) {
      return 0;
    }
    return Math.min(activeImageIndices[batchId], imagesCount - 1);
  };

  const handleDeleteImage = (batchId: string, imageIndex: number) => {
    setDeletedImages(prev => {
      const newDeletedImages = { ...prev };
      if (!newDeletedImages[batchId]) {
        newDeletedImages[batchId] = new Set();
      }
      newDeletedImages[batchId].add(imageIndex);
      
      const batch = getBatchedImages().find(batch => batch.batchId === batchId);
      const remainingImagesCount = batch ? 
        batch.images.length - (newDeletedImages[batchId].size || 0) : 0;
      
      if (remainingImagesCount <= 1) {
        if (onReorderContainers) {
          const index = imageContainerOrder.indexOf(batchId);
          if (index !== -1) {
            onReorderContainers(index, imageContainerOrder.length);
          }
        }
      }
      
      return newDeletedImages;
    });
    
    if (onDeleteImage) {
      onDeleteImage(batchId, imageIndex);
    }
    
    toast.success('Image deleted');
  };

  const navigatePrevImage = (batchId: string, imagesCount: number) => {
    setActiveImageIndices(prev => ({
      ...prev,
      [batchId]: (prev[batchId] || 0) > 0 ? (prev[batchId] || 0) - 1 : imagesCount - 1
    }));
  };

  const navigateNextImage = (batchId: string, imagesCount: number) => {
    setActiveImageIndices(prev => ({
      ...prev,
      [batchId]: (prev[batchId] || 0) < imagesCount - 1 ? (prev[batchId] || 0) + 1 : 0
    }));
  };

  const toggleExpandBatch = (batchId: string) => {
    setExpandedBatches(prev => ({
      ...prev,
      [batchId]: !prev[batchId]
    }));
  };

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

  const setActiveImageIndex = (batchId: string, index: number) => {
    setActiveImageIndices(prev => ({
      ...prev,
      [batchId]: index
    }));
  };

  const handleCreateAnother = (batchId: string) => {
    if (onCreateAgain) {
      setFocusBatchId(batchId);
      onCreateAgain(batchId);
      toast.info('Creating another image...');
    }
  };

  const batchedImages = getBatchedImages();
  const sortableIds = batchedImages.map(batch => batch.batchId);

  const renderReferenceImageIndicator = (image: typeof generatedImages[0]) => {
    if (image.referenceImageUrl) {
      return <ReferenceImageIndicator imageUrl={image.referenceImageUrl} />;
    }
    return null;
  };

  const getGridColsClass = () => {
    if (viewMode === 'small') {
      return "grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 xl:grid-cols-8";
    }
    return "grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5";
  };

  const renderAllImagesInSmallView = () => {
    const allImages: Array<{
      batchId: string;
      image: typeof generatedImages[0];
      index: number;
      total: number;
    }> = [];
    
    batchedImages.forEach(({ batchId, images }) => {
      images.forEach((image, index) => {
        allImages.push({
          batchId,
          image,
          index,
          total: images.length
        });
      });
    });
    
    return (
      <div className={`grid ${getGridColsClass()} gap-4`}>
        {allImages.map(({ batchId, image, index, total }, itemIndex) => (
          <div key={`${batchId}-${index}`} className="relative">
            <ImageBatchItem
              image={image}
              batchId={batchId}
              index={index}
              total={total}
              onCreateAgain={handleCreateAnother}
              onUseAsInput={onUseGeneratedAsInput}
              onDeleteImage={handleDeleteImage}
              viewMode="small"
            />
          </div>
        ))}
      </div>
    );
  };

  const handleOpenBatchDialog = (batchId: string) => {
    setOpenBatchDialog(batchId);
    const batch = batchedImages.find(b => b.batchId === batchId);
    if (batch) {
      setExpandedBatches(prev => ({
        ...prev,
        [batchId]: true
      }));
    }
  };

  const renderTableView = () => {
    return (
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Prompt</TableHead>
            <TableHead className="w-32">When</TableHead>
            <TableHead className="w-20 text-center">Batch</TableHead>
            <TableHead className="w-16 text-center">Open</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {batchedImages.map(({ batchId, images }) => {
            const timestamp = images[0]?.timestamp || Date.now();
            const timeAgo = formatDistanceToNow(new Date(timestamp), { addSuffix: true });
            const promptText = images[0]?.prompt || 'No prompt';
            
            return (
              <TableRow key={batchId}>
                <TableCell className="font-medium">
                  <div className="truncate max-w-64">{promptText}</div>
                </TableCell>
                <TableCell>{timeAgo}</TableCell>
                <TableCell className="text-center">{images.length}</TableCell>
                <TableCell className="text-center">
                  <Button variant="ghost" size="icon" onClick={() => handleOpenBatchDialog(batchId)}>
                    <ExternalLink className="h-4 w-4" />
                  </Button>
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    );
  };

  return (
    <div className="mt-12 animate-fade-in">
      <div className="flex flex-col gap-6">
        <div>
          <div className="flex justify-between items-center mb-3">
            <h3 className="text-lg font-semibold">Generated Images</h3>
            <div className="flex items-center gap-2 bg-card rounded-md p-1">
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant={viewMode === 'normal' ? 'secondary' : 'ghost'}
                    size="icon"
                    className="h-8 w-8"
                    onClick={() => setViewMode('normal')}
                  >
                    <LayoutGrid className="h-4 w-4" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Normal view</TooltipContent>
              </Tooltip>
              
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant={viewMode === 'small' ? 'secondary' : 'ghost'}
                    size="icon"
                    className="h-8 w-8"
                    onClick={() => setViewMode('small')}
                  >
                    <List className="h-4 w-4" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Small view</TooltipContent>
              </Tooltip>
              
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant={viewMode === 'table' ? 'secondary' : 'ghost'}
                    size="icon"
                    className="h-8 w-8"
                    onClick={() => setViewMode('table')}
                  >
                    <Table2 className="h-4 w-4" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Table view</TooltipContent>
              </Tooltip>
            </div>
          </div>
          
          <div className="space-y-4">
            {isLoading && generatedImages.length === 0 && (
              <LoadingPlaceholder prompt={prompt} />
            )}
            
            {viewMode === 'table' ? (
              renderTableView()
            ) : viewMode === 'small' ? (
              renderAllImagesInSmallView()
            ) : (
              <TooltipProvider>
                <DndContext 
                  sensors={sensors}
                  collisionDetection={closestCenter}
                  onDragEnd={handleDragEnd}
                >
                  <SortableContext 
                    items={sortableIds}
                    strategy={verticalListSortingStrategy}
                  >
                    <div className={`grid ${getGridColsClass()} gap-4`}>
                      {batchedImages.map(({ batchId, images }) => {
                        if (images.length === 0) return null;
                        
                        const filteredImages = deletedImages[batchId] 
                          ? images.filter((_, i) => !deletedImages[batchId].has(i))
                          : images;
                        
                        if (filteredImages.length === 0) return null;
                        
                        const activeIndex = getActiveImageIndex(batchId, filteredImages.length);
                        const isExpanded = expandedBatches[batchId];
                        const activeImage = filteredImages[activeIndex];
                        
                        const extraComponents = activeImage?.referenceImageUrl ? 
                          renderReferenceImageIndicator(activeImage) : 
                          null;
                        
                        return (
                          <DropdownMenu key={batchId}>
                            <ImageBatch
                              batchId={batchId}
                              images={filteredImages}
                              isExpanded={isExpanded}
                              activeIndex={activeIndex}
                              activeBatchId={activeBatchId}
                              onSetActiveBatchId={setActiveBatchId}
                              onSetActiveImageIndex={setActiveImageIndex}
                              onToggleExpandBatch={toggleExpandBatch}
                              onNavigatePrevImage={navigatePrevImage}
                              onNavigateNextImage={navigateNextImage}
                              onDeleteImage={handleDeleteImage}
                              onCreateAgain={handleCreateAnother}
                              onUseAsInput={onUseGeneratedAsInput}
                              extraComponents={extraComponents}
                              viewMode={viewMode}
                            />
                          </DropdownMenu>
                        );
                      })}
                    </div>
                  </SortableContext>
                </DndContext>
              </TooltipProvider>
            )}
            
            {/* Batch dialog for table view */}
            {openBatchDialog && (
              <Dialog open={!!openBatchDialog} onOpenChange={() => setOpenBatchDialog(null)}>
                <DialogContent className="max-w-4xl p-0 overflow-hidden">
                  {batchedImages.find(b => b.batchId === openBatchDialog)?.images.length > 0 && (
                    <div className="p-6">
                      <h2 className="text-xl font-semibold mb-4">Image Batch</h2>
                      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-4">
                        {batchedImages.find(b => b.batchId === openBatchDialog)?.images.map((image, index) => (
                          <div key={index} className="relative rounded-md overflow-hidden border">
                            <img 
                              src={image.url} 
                              alt={`Image ${index + 1}`} 
                              className="w-full aspect-square object-cover" 
                            />
                            <div className="absolute bottom-2 right-2 bg-black/90 text-white px-2 py-1 rounded-full text-xs">
                              {index + 1}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </DialogContent>
              </Dialog>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default ImageDisplay;
