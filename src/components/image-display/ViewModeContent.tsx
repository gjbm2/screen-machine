
import React from 'react';
import NormalGridView from './view-modes/NormalGridView';
import SmallGridView from './view-modes/SmallGridView';
import TableView from './view-modes/TableView';
import { DndContext, closestCenter, KeyboardSensor, PointerSensor, useSensor, useSensors, DragEndEvent } from '@dnd-kit/core';
import { restrictToVerticalAxis } from '@dnd-kit/modifiers';
import { SortableContext, verticalListSortingStrategy, arrayMove } from '@dnd-kit/sortable';
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
  getSortedContainers: (containers: string[]) => string[];
  handleTableRowClick: (batchId: string) => void;
  isLoading: boolean;
  onReorderContainers: (sourceIndex: number, destinationIndex: number) => void;
  activeGenerations?: string[]; // Add activeGenerations prop
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
  onReorderContainers,
  activeGenerations = [] // Default to empty array
}) => {
  const sensors = useSensors(
    useSensor(PointerSensor),
    useSensor(KeyboardSensor)
  );
  
  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    
    if (active.id !== over?.id) {
      const oldIndex = imageContainerOrder.indexOf(active.id as string);
      const newIndex = imageContainerOrder.indexOf(over?.id as string);
      
      if (oldIndex !== -1 && newIndex !== -1) {
        onReorderContainers(oldIndex, newIndex);
      }
    }
  };
  
  // Get sorted container IDs based on current sort settings
  const sortedContainerIds = getSortedContainers(imageContainerOrder);
  
  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCenter}
      onDragEnd={handleDragEnd}
      modifiers={[restrictToVerticalAxis]}
    >
      <SortableContext items={sortedContainerIds} strategy={verticalListSortingStrategy}>
        {viewMode === 'normal' && (
          <NormalGridView
            sortedContainerIds={sortedContainerIds}
            batches={batches}
            expandedContainers={expandedContainers}
            handleToggleExpand={handleToggleExpand}
            onUseGeneratedAsInput={onUseGeneratedAsInput}
            onCreateAgain={onCreateAgain}
            onDeleteImage={onDeleteImage}
            onDeleteContainer={onDeleteContainer}
            onFullScreenClick={onFullScreenClick}
            imageUrl={imageUrl}
            isLoading={isLoading}
            activeGenerations={activeGenerations} // Pass activeGenerations prop
          />
        )}
        
        {viewMode === 'small' && (
          <SmallGridView 
            sortedContainerIds={sortedContainerIds}
            batches={batches}
            expandedContainers={expandedContainers}
            handleToggleExpand={handleToggleExpand}
            onUseGeneratedAsInput={onUseGeneratedAsInput}
            onCreateAgain={onCreateAgain}
            onDeleteImage={onDeleteImage}
            onDeleteContainer={onDeleteContainer}
            onFullScreenClick={onFullScreenClick}
            imageUrl={imageUrl}
            getAllImages={getAllImages}
            handleSmallImageClick={handleSmallImageClick}
            isLoading={isLoading}
            activeGenerations={activeGenerations} // Pass activeGenerations prop
          />
        )}
        
        {viewMode === 'table' && (
          <TableView 
            sortedContainers={sortedContainerIds}
            batches={batches}
            sortField={sortField}
            sortDirection={sortDirection}
            onSortClick={handleSortClick}
            onTableRowClick={handleTableRowClick}
          />
        )}
      </SortableContext>
    </DndContext>
  );
};

export default ViewModeContent;
