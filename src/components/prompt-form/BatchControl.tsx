
import React from 'react';
import { Button } from '@/components/ui/button';
import { Plus, Minus } from 'lucide-react';
import { BatchControlProps } from './types';

const BatchControl: React.FC<BatchControlProps> = ({
  batchSize,
  incrementBatchSize,
  decrementBatchSize
}) => {
  return (
    <div className="flex items-center h-[48px]">
      <Button 
        type="button"
        className="h-[36px] rounded-l-md px-1 sm:px-1.5 hover:bg-purple-500/10 text-purple-700 border border-r-0 border-input"
        onClick={decrementBatchSize}
        disabled={batchSize <= 1}
        variant="outline"
      >
        <Minus className="h-3 w-3" />
      </Button>
      
      <div className="flex justify-center items-center h-[36px] bg-background border-y border-input text-foreground w-6 sm:w-7">
        <span className="text-sm font-medium">{batchSize}</span>
      </div>
      
      <Button 
        type="button"
        className="h-[36px] rounded-r-md px-1 sm:px-1.5 hover:bg-purple-500/10 text-purple-700 border border-l-0 border-input"
        onClick={incrementBatchSize}
        disabled={batchSize >= 9}
        variant="outline"
      >
        <Plus className="h-3 w-3" />
      </Button>
    </div>
  );
};

export default BatchControl;
