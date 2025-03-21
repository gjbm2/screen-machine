
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
    <div className="flex items-center gap-1 shrink-0">
      {!isCompact && <span className="text-xs text-muted-foreground mr-1">Batch</span>}
      <div className="flex items-center border rounded-md overflow-hidden">
        <Button
          type="button"
          variant="ghost"
          size="icon"
          onClick={decrementBatchSize}
          disabled={batchSize <= 1}
          className={`${isCompact ? 'h-[28px] w-[28px]' : 'h-[36px] w-[36px]'} rounded-none border-r`}
        >
          <Minus className={`${isCompact ? 'h-2.5 w-2.5' : 'h-3 w-3'}`} />
        </Button>
        <div className={`px-1 flex items-center justify-center ${isCompact ? 'min-w-[18px]' : 'min-w-[25px]'}`}>
          <span className={`${isCompact ? 'text-xs' : 'text-sm'} font-medium`}>{batchSize}</span>
        </div>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          onClick={incrementBatchSize}
          disabled={batchSize >= 9}
          className={`${isCompact ? 'h-[28px] w-[28px]' : 'h-[36px] w-[36px]'} rounded-none border-l`}
        >
          <Plus className={`${isCompact ? 'h-2.5 w-2.5' : 'h-3 w-3'}`} />
        </Button>
      </div>
    </div>
  );
};

export default BatchControl;
