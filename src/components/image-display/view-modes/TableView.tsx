
import React from 'react';
import { Table, TableHeader, TableRow, TableHead, TableBody } from '@/components/ui/table';
import { ChevronDown, ChevronUp } from 'lucide-react';
import { SortField, SortDirection } from '../ImageDisplay';
import SortableTableRow from '../SortableTableRow';

interface TableViewProps {
  sortedContainerIds: string[];
  batches: Record<string, any[]>;
  sortField: SortField;
  sortDirection: SortDirection;
  onSortClick: (field: SortField) => void;
  onTableRowClick: (batchId: string) => void;
  onDeleteContainer: (batchId: string) => void;
  onCreateAgain: (batchId?: string) => void;
}

const TableView: React.FC<TableViewProps> = ({
  sortedContainerIds,
  batches,
  sortField,
  sortDirection,
  onSortClick,
  onTableRowClick,
  onDeleteContainer,
  onCreateAgain
}) => {
  // This function renders the sort icon based on the current sort state
  const renderSortIndicator = (field: SortField) => {
    if (sortField !== field) return null;
    
    return sortDirection === 'asc' ? (
      <ChevronUp className="ml-1 h-4 w-4" />
    ) : (
      <ChevronDown className="ml-1 h-4 w-4" />
    );
  };
  
  // Helper function to generate batch data for each row
  const getBatchData = (batchId: string) => {
    const images = batches[batchId] || [];
    const firstImage = images.find(img => img.status === 'completed') || images[0] || {};
    const timestamp = firstImage.timestamp || Date.now();
    
    return {
      id: batchId,
      firstImage,
      prompt: firstImage.prompt || 'No prompt',
      imageCount: images.filter(img => img.status === 'completed').length,
      timestamp
    };
  };
  
  // Generate table rows from sorted container IDs
  const tableRows = sortedContainerIds.map(id => getBatchData(id));
  
  return (
    <div className="w-full">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead 
              className="w-[60%] cursor-pointer hover:bg-secondary/50"
              onClick={() => onSortClick('prompt')}
            >
              <div className="flex items-center">
                Prompt
                {renderSortIndicator('prompt')}
              </div>
            </TableHead>
            <TableHead 
              className="w-[15%] cursor-pointer hover:bg-secondary/50"
              onClick={() => onSortClick('count')}
            >
              <div className="flex items-center">
                Images
                {renderSortIndicator('count')}
              </div>
            </TableHead>
            <TableHead 
              className="w-[15%] cursor-pointer hover:bg-secondary/50"
              onClick={() => onSortClick('date')}
            >
              <div className="flex items-center">
                Date
                {renderSortIndicator('date')}
              </div>
            </TableHead>
            <TableHead className="w-[10%] text-right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {tableRows.map(batch => (
            <SortableTableRow
              key={batch.id}
              id={batch.id}
              prompt={batch.prompt}
              imageCount={batch.imageCount}
              timestamp={batch.timestamp}
              firstImage={batch.firstImage}
              handleTableRowClick={() => onTableRowClick(batch.id)}
              onDeleteContainer={() => onDeleteContainer(batch.id)}
              onCreateAgain={() => onCreateAgain(batch.id)}
            />
          ))}
        </TableBody>
      </Table>
    </div>
  );
};

export default TableView;
