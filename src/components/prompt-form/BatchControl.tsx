
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
  // Log the current batch size when it changes to help debug
  React.useEffect(() => {
    console.log('BatchControl: Current batch size:', batchSize);
  }, [batchSize]);

  return (
    <div className="flex items-center gap-1 shrink-0">
      {!isCompact && <span className="text-xs text-muted-foreground mr-1">Images</span>}
      <div className="flex items-center border rounded-md overflow-hidden">
        <Button
          type="button"
          variant="ghost"
          size="icon"
          onClick={() => {
            console.log('BatchControl: Decrementing batch size');
            decrementBatchSize();
          }}
          disabled={batchSize <= 1}
          className={`${isCompact ? 'h-[28px] w-[22px]' : 'h-[28px] w-[28px]'} rounded-none border-r`}
        >
          <Minus className={`${isCompact ? 'h-2.5 w-2.5' : 'h-3 w-3'}`} />
        </Button>
        <div className={`px-1 flex items-center justify-center ${isCompact ? 'min-w-[14px]' : 'min-w-[20px]'}`}>
          <span className={`${isCompact ? 'text-xs' : 'text-xs'} font-medium`}>{batchSize}</span>
        </div>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          onClick={() => {
            console.log('BatchControl: Incrementing batch size');
            incrementBatchSize();
          }}
          disabled={batchSize >= 9}
          className={`${isCompact ? 'h-[28px] w-[22px]' : 'h-[28px] w-[28px]'} rounded-none border-l`}
        >
          <Plus className={`${isCompact ? 'h-2.5 w-2.5' : 'h-3 w-3'}`} />
        </Button>
      </div>
    </div>
  );
};

export default BatchControl;
