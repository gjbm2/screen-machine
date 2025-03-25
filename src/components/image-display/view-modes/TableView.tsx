
import React from 'react';
import { SortField, SortDirection } from '../ImageDisplay';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { ArrowUp, ArrowDown, Image } from 'lucide-react';
import SortableTableRow from '../SortableTableRow';

interface TableViewProps {
  sortedContainers: string[];
  batches: Record<string, any[]>;
  sortField: SortField;
  sortDirection: SortDirection;
  onSortClick: (field: SortField) => void;
  onTableRowClick: (batchId: string) => void;
}

const TableView: React.FC<TableViewProps> = ({
  sortedContainers,
  batches,
  sortField,
  sortDirection,
  onSortClick,
  onTableRowClick
}) => {
  return (
    <div className="w-full overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead 
              className="w-[80px] cursor-pointer" 
              onClick={() => onSortClick('index')}
            >
              <div className="flex items-center space-x-1">
                <span>No.</span>
                {sortField === 'index' && (
                  sortDirection === 'asc' ? 
                  <ArrowUp className="h-3 w-3" /> : 
                  <ArrowDown className="h-3 w-3" />
                )}
              </div>
            </TableHead>
            <TableHead 
              className="cursor-pointer max-w-[200px] md:max-w-[300px]"
              onClick={() => onSortClick('prompt')}
            >
              <div className="flex items-center space-x-1">
                <span>Prompt</span>
                {sortField === 'prompt' && (
                  sortDirection === 'asc' ? 
                  <ArrowUp className="h-3 w-3" /> : 
                  <ArrowDown className="h-3 w-3" />
                )}
              </div>
            </TableHead>
            <TableHead 
              className="w-[80px] text-center cursor-pointer"
              onClick={() => onSortClick('batchSize')}
            >
              <div className="flex items-center justify-center space-x-1">
                <span>Batch</span>
                {sortField === 'batchSize' && (
                  sortDirection === 'asc' ? 
                  <ArrowUp className="h-3 w-3" /> : 
                  <ArrowDown className="h-3 w-3" />
                )}
              </div>
            </TableHead>
            <TableHead 
              className="w-[120px] cursor-pointer"
              onClick={() => onSortClick('timestamp')}
            >
              <div className="flex items-center space-x-1">
                <span>When</span>
                {sortField === 'timestamp' && (
                  sortDirection === 'asc' ? 
                  <ArrowUp className="h-3 w-3" /> : 
                  <ArrowDown className="h-3 w-3" />
                )}
              </div>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {sortedContainers.map((batchId) => {
            if (!batches[batchId]) return null;
            
            const batchImages = batches[batchId];
            const firstImage = batchImages[0];
            const completedImages = batchImages.filter(img => img.status === 'completed');
            const hasReferenceImage = !!firstImage.referenceImageUrl;
            
            // Extract numeric image ID from containerId or fallback to 0
            const imageNumber = firstImage.containerId || 0;
            
            return (
              <SortableTableRow 
                key={batchId}
                id={batchId}
                onClick={() => onTableRowClick(batchId)}
                index={imageNumber}
                prompt={firstImage.prompt}
                hasReferenceImage={hasReferenceImage}
                completedImages={completedImages.length}
                timestamp={firstImage.timestamp}
                title={firstImage.title}
              />
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
};

export default TableView;
