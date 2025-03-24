
import React from 'react';
import { Button } from '@/components/ui/button';
import { Plus, Minus } from 'lucide-react';

interface BatchControlProps {
  batchSize: number;
  incrementBatchSize: () => void;
  decrementBatchSize: () => void;
  isCompact?: boolean;
}

const BatchControl: React.FC<BatchControlProps> = ({
  batchSize,
  incrementBatchSize,
  decrementBatchSize,
  isCompact = false
}) => {
  // Log the current value immediately when rendered
  console.log(`[BatchControl] Current batch size in component: ${batchSize}`);
  
  const handleIncrement = () => {
    incrementBatchSize();
    // We can't log the updated value immediately as state updates are async
    console.log(`[BatchControl] Increment requested (current: ${batchSize})`);
  };
  
  const handleDecrement = () => {
    decrementBatchSize();
    console.log(`[BatchControl] Decrement requested (current: ${batchSize})`);
  };
  
  return (
    <div className="flex items-center space-x-1">
      {!isCompact && (
        <span className="text-xs text-muted-foreground whitespace-nowrap">
          Batch:
        </span>
      )}
      <div className="flex items-center border rounded-md h-7">
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7 rounded-r-none"
          onClick={handleDecrement}
          disabled={batchSize <= 1}
          aria-label="Decrease batch size"
        >
          <Minus className="h-3 w-3" />
        </Button>
        <span className="flex-shrink-0 text-xs font-medium px-1 min-w-[18px] text-center">
          {batchSize}
        </span>
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7 rounded-l-none"
          onClick={handleIncrement}
          disabled={batchSize >= 9}
          aria-label="Increase batch size"
        >
          <Plus className="h-3 w-3" />
        </Button>
      </div>
    </div>
  );
};

export default BatchControl;
