
import React from 'react';
import { SortField, SortDirection } from '../ImageDisplay';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { ArrowUp, ArrowDown } from 'lucide-react';
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
          <TableRow className="text-xs">
            <TableHead 
              className="cursor-pointer max-w-[200px] md:max-w-[300px] py-1 px-2"
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
              className="w-[80px] cursor-pointer py-1 px-2"
            >
              <div className="flex items-center space-x-1">
                <span>Workflow</span>
              </div>
            </TableHead>
            <TableHead 
              className="w-[60px] text-center cursor-pointer py-1 px-2"
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
              className="w-[80px] cursor-pointer py-1 px-2"
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
            
            return (
              <SortableTableRow 
                key={batchId}
                id={batchId}
                onClick={() => onTableRowClick(batchId)}
                prompt={firstImage.prompt}
                workflow={firstImage.workflow || '—'}
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
