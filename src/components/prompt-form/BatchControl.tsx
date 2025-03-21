
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
  const height = isCompact ? "h-[32px]" : "h-[36px]";
  const iconSize = isCompact ? "h-2.5 w-2.5" : "h-3 w-3";
  const buttonWidth = isCompact ? "w-5" : "w-6 sm:w-7";
  
  return (
    <div className={`flex items-center ${isCompact ? "h-[32px]" : "h-[48px]"}`}>
      <Button 
        type="button"
        className={`${height} rounded-l-md px-0.5 hover:bg-purple-500/10 text-purple-700 border border-r-0 border-input`}
        onClick={decrementBatchSize}
        disabled={batchSize <= 1}
        variant="outline"
      >
        <Minus className={iconSize} />
      </Button>
      
      <div className={`flex justify-center items-center ${height} bg-background border-y border-input text-foreground ${buttonWidth}`}>
        <span className={`${isCompact ? 'text-xs' : 'text-sm'} font-medium`}>{batchSize}</span>
      </div>
      
      <Button 
        type="button"
        className={`${height} rounded-r-md px-0.5 hover:bg-purple-500/10 text-purple-700 border border-l-0 border-input`}
        onClick={incrementBatchSize}
        disabled={batchSize >= 9}
        variant="outline"
      >
        <Plus className={iconSize} />
      </Button>
    </div>
  );
};

export default BatchControl;
