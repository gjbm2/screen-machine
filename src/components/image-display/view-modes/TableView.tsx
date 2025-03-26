
import React from 'react';
import { Button } from '@/components/ui/button';
import { ChevronUp, ChevronDown } from 'lucide-react';
import SortableTableRow from '../SortableTableRow';
import LoadingPlaceholder from '../LoadingPlaceholder';
import { SortField, SortDirection } from '../ImageDisplay';

interface TableViewProps {
  sortedContainerIds: string[]; // Changed from imageContainerOrder to sortedContainerIds
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
  handleTableRowClick: (batchId: string) => void;
  sortField: SortField;
  sortDirection: SortDirection;
  handleSortClick: (field: SortField) => void;
  isLoading: boolean;
  activeGenerations?: string[];
}

const TableView: React.FC<TableViewProps> = ({
  sortedContainerIds, // Using sortedContainerIds
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
  handleTableRowClick,
  sortField,
  sortDirection,
  handleSortClick,
  isLoading,
  activeGenerations = []
}) => {
  // Render loading placeholder when no containers yet and loading
  if (isLoading && sortedContainerIds.length === 0) {
    return <LoadingPlaceholder prompt="Generating your image..." />;
  }

  // Get the sort indicator component (up or down arrow)
  const getSortIndicator = (field: SortField) => {
    if (sortField !== field) return null;
    
    return sortDirection === 'asc' 
      ? <ChevronUp className="h-4 w-4" /> 
      : <ChevronDown className="h-4 w-4" />;
  };

  // Handler for column header clicks
  const handleHeaderClick = (field: SortField) => {
    handleSortClick(field);
  };

  return (
    <div className="overflow-x-auto">
      <table className="w-full">
        <thead>
          <tr className="border-b">
            <th className="text-left py-2">Preview</th>
            <th className="text-left py-2 cursor-pointer" onClick={() => handleHeaderClick('prompt')}>
              <div className="flex items-center">
                <span>Prompt</span>
                {getSortIndicator('prompt')}
              </div>
            </th>
            <th className="text-left py-2 cursor-pointer" onClick={() => handleHeaderClick('batchSize')}>
              <div className="flex items-center">
                <span>Images</span>
                {getSortIndicator('batchSize')}
              </div>
            </th>
            <th className="text-left py-2 cursor-pointer" onClick={() => handleHeaderClick('timestamp')}>
              <div className="flex items-center">
                <span>Date</span>
                {getSortIndicator('timestamp')}
              </div>
            </th>
            <th className="text-left py-2">Actions</th>
          </tr>
        </thead>
        <tbody>
          {sortedContainerIds.map(batchId => {
            const batchImages = batches[batchId] || [];
            const completedImages = batchImages.filter(img => img.status === 'completed');
            const firstImage = completedImages.length > 0 ? completedImages[0] : null;
            
            return (
              <SortableTableRow
                key={batchId}
                id={batchId}
                batch={{
                  id: batchId,
                  firstImage: firstImage,
                  prompt: firstImage?.prompt || 'No prompt',
                  imageCount: completedImages.length,
                  timestamp: firstImage?.timestamp || Date.now(),
                }}
                handleTableRowClick={handleTableRowClick}
                onDeleteContainer={onDeleteContainer}
                onCreateAgain={onCreateAgain}
              />
            );
          })}
        </tbody>
      </table>
    </div>
  );
};

export default TableView;
