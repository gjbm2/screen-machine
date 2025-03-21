
import React, { useState, useEffect } from 'react';
import { DndContext, closestCenter, KeyboardSensor, PointerSensor, useSensor, useSensors, DragEndEvent } from '@dnd-kit/core';
import { SortableContext, sortableKeyboardCoordinates, verticalListSortingStrategy, horizontalListSortingStrategy } from '@dnd-kit/sortable';
import LoadingPlaceholder from './LoadingPlaceholder';

// Import refactored components and hooks
import SmallGridView from './view-modes/SmallGridView';
import TableView from './view-modes/TableView';
import NormalGridView from './view-modes/NormalGridView';
import FullscreenDialog from './FullscreenDialog';
import ViewModeSelector from './ViewModeSelector';
import useFullscreen from './hooks/useFullscreen';
import useImageSort from './hooks/useImageSort';

export type ViewMode = 'normal' | 'small' | 'table';
export type SortField = 'index' | 'prompt' | 'batchSize' | 'timestamp';
export type SortDirection = 'asc' | 'desc';

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
  const [allImagesFlat, setAllImagesFlat] = useState<any[]>([]);

  // Use custom hooks
  const { 
    showFullScreenView, 
    setShowFullScreenView,
    fullScreenBatchId,
    fullScreenImageIndex,
    setFullScreenImageIndex,
    currentGlobalIndex,
    openFullScreenView,
    handleNavigateGlobal
  } = useFullscreen(allImagesFlat);

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
    if (imageContainerOrder.length > 0) {
      if (isLoading) {
        const updatedExpandedState: Record<string, boolean> = {};
        
        imageContainerOrder.forEach(id => {
          updatedExpandedState[id] = false;
        });
        
        updatedExpandedState[imageContainerOrder[0]] = true;
        
        setExpandedContainers(updatedExpandedState);
        
        const container = document.getElementById(imageContainerOrder[0]);
        if (container) {
          container.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }
      }
    }
  }, [imageContainerOrder, isLoading]);
  
  useEffect(() => {
    const allImages = generatedImages
      .filter(img => img.status === 'completed' || img.status === 'failed' || img.status === 'error')
      .map(img => ({
        url: img.url,
        prompt: img.prompt,
        batchId: img.batchId,
        batchIndex: img.batchIndex,
        workflow: img.workflow,
        timestamp: img.timestamp,
        referenceImageUrl: img.referenceImageUrl,
        params: img.params,
        refiner: img.refiner
      }))
      .sort((a, b) => b.timestamp - a.timestamp);
    
    setAllImagesFlat(allImages);
  }, [generatedImages]);

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
  
  const handleSmallImageClick = (image: any) => {
    if (image?.url && image.batchId) {
      openFullScreenView(image.batchId, image.batchIndex || 0);
    }
  };

  const handleTableRowClick = (batchId: string) => {
    const batchImages = batches[batchId]?.filter(img => img.status === 'completed');
    if (batchImages && batchImages.length === 1) {
      openFullScreenView(batchId, 0);
    } else {
      setExpandedContainers(prev => ({
        ...prev,
        [batchId]: true
      }));
    }
  };
  
  const getAllImages = () => {
    return generatedImages
      .filter(img => img.status === 'completed' || img.status === 'failed' || img.status === 'error')
      .sort((a, b) => {
        return b.timestamp - a.timestamp;
      });
  };

  // Use image sorting hook
  const { 
    sortField, 
    sortDirection, 
    handleSortClick, 
    getSortedContainers 
  } = useImageSort(imageContainerOrder, batches);
  
  return (
    <div className="mt-4">
      {hasBatches && (
        <div className="mt-2">
          <div className="flex justify-between items-center mb-1">
            <h2 className="text-xl font-bold">Generated Images</h2>
            <ViewModeSelector 
              viewMode={viewMode} 
              onViewModeChange={(value) => setViewMode(value as ViewMode)} 
            />
          </div>
          
          <div className="pr-2">
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
                  <SmallGridView 
                    images={getAllImages()}
                    isLoading={isLoading}
                    onSmallImageClick={handleSmallImageClick}
                    onCreateAgain={onCreateAgain}
                    onDeleteImage={onDeleteImage}
                  />
                ) : viewMode === 'table' ? (
                  <TableView 
                    sortedContainers={getSortedContainers()}
                    batches={batches}
                    sortField={sortField}
                    sortDirection={sortDirection}
                    onSortClick={handleSortClick}
                    onTableRowClick={handleTableRowClick}
                  />
                ) : (
                  <NormalGridView 
                    imageContainerOrder={imageContainerOrder}
                    batches={batches}
                    expandedContainers={expandedContainers}
                    toggleExpand={handleToggleExpand}
                    onUseGeneratedAsInput={onUseGeneratedAsInput}
                    onCreateAgain={handleCreateAgain}
                    onDeleteImage={onDeleteImage}
                    onDeleteContainer={onDeleteContainer}
                    onFullScreenClick={(image) => {
                      if (image && image.batchId) {
                        openFullScreenView(image.batchId, image.batchIndex || 0);
                      }
                    }}
                    imageUrl={imageUrl}
                  />
                )}
              </SortableContext>
            </DndContext>
          </div>
          
          <FullscreenDialog 
            showFullScreenView={showFullScreenView}
            setShowFullScreenView={setShowFullScreenView}
            fullScreenBatchId={fullScreenBatchId}
            batches={batches}
            fullScreenImageIndex={fullScreenImageIndex}
            setFullScreenImageIndex={setFullScreenImageIndex}
            onDeleteImage={onDeleteImage}
            onCreateAgain={onCreateAgain}
            onUseGeneratedAsInput={onUseGeneratedAsInput}
            allImagesFlat={allImagesFlat}
            currentGlobalIndex={currentGlobalIndex}
            handleNavigateGlobal={handleNavigateGlobal}
          />
        </div>
      )}
    </div>
  );
};

export default ImageDisplay;
