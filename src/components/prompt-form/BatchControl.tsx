
import React from 'react';
import { Button } from '@/components/ui/button';
import { Plus, Minus } from 'lucide-react';
import { BatchControlProps } from './types';

const BatchControl: React.FC<BatchControlProps> = ({
  batchSize,
  incrementBatchSize,
  decrementBatchSize,
  isCompact = false
}) => {
  return (
    <div className="flex items-center gap-1">
      {!isCompact && <span className="text-xs text-muted-foreground mr-1">Batch Size</span>}
      <div className="flex items-center border rounded-md overflow-hidden">
        <Button
          type="button"
          variant="ghost"
          size="icon"
          onClick={decrementBatchSize}
          disabled={batchSize <= 1}
          className="h-[36px] w-[36px] rounded-none border-r"
        >
          <Minus className="h-3 w-3" />
        </Button>
        <div className="px-2 flex items-center justify-center min-w-[25px]">
          <span className="text-sm font-medium">{batchSize}</span>
        </div>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          onClick={incrementBatchSize}
          disabled={batchSize >= 9}
          className="h-[36px] w-[36px] rounded-none border-l"
        >
          <Plus className="h-3 w-3" />
        </Button>
      </div>
    </div>
  );
};

export default BatchControl;
