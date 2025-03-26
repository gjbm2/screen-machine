
import React from 'react';
import { DndContext, closestCenter, KeyboardSensor, PointerSensor, useSensor, useSensors, DragEndEvent } from '@dnd-kit/core';
import { SortableContext, sortableKeyboardCoordinates, verticalListSortingStrategy, horizontalListSortingStrategy } from '@dnd-kit/sortable';
import SmallGridView from './view-modes/SmallGridView';
import TableView from './view-modes/TableView';
import NormalGridView from './view-modes/NormalGridView';
import { ViewMode, SortField, SortDirection } from './ImageDisplay';

interface ViewModeContentProps {
  viewMode: ViewMode;
  imageContainerOrder: string[];
  batches: Record<string, any[]>;
  expandedContainers: Record<string, boolean>;
  handleToggleExpand: (batchId: string) => void;
  onUseGeneratedAsInput: (url: string) => void;
  onCreateAgain: (batchId?: string) => void;
  onDeleteImage: (batchId: string, index: number) => void;
  onDeleteContainer: (batchId: string) => void;
  onFullScreenClick: (image: any) => void;
  imageUrl: string | null;
  getAllImages: () => any[];
  handleSmallImageClick: (image: any) => void;
  sortField: SortField;
  sortDirection: SortDirection;
  handleSortClick: (field: SortField) => void;
  getSortedContainers: () => string[];
  handleTableRowClick: (batchId: string) => void;
  isLoading: boolean;
  onReorderContainers: (sourceIndex: number, destinationIndex: number) => void;
}

const ViewModeContent: React.FC<ViewModeContentProps> = ({
  viewMode,
  imageContainerOrder,
  batches,
  expandedContainers,
  handleToggleExpand,
  onUseGeneratedAsInput,
  onCreateAgain,
  onDeleteImage,
  onDeleteContainer,
  onFullScreenClick,
  imageUrl,
  getAllImages,
  handleSmallImageClick,
  sortField,
  sortDirection,
  handleSortClick,
  getSortedContainers,
  handleTableRowClick,
  isLoading,
  onReorderContainers
}) => {
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

  return (
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
            onCreateAgain={onCreateAgain}
            onDeleteImage={onDeleteImage}
            onDeleteContainer={onDeleteContainer}
            onFullScreenClick={onFullScreenClick}
            imageUrl={imageUrl}
          />
        )}
      </SortableContext>
    </DndContext>
  );
};

export default ViewModeContent;
