
import React from 'react';
import { ViewMode } from './ImageDisplay';

interface BatchCountDisplayProps {
  batchCount: number;
  totalCount: number;
  prompt?: string;
  workflow?: string | null;
  timestamp?: string | null;
  hasGenerating?: boolean;
  hasFailed?: boolean;
  viewMode?: ViewMode;
}

const BatchCountDisplay: React.FC<BatchCountDisplayProps> = ({ 
  batchCount, 
  totalCount, 
  prompt,
  workflow,
  timestamp,
  hasGenerating = false,
  hasFailed = false,
  viewMode 
}) => {
  if (totalCount <= 1 || viewMode === 'small' || viewMode === 'table') return null;
  
  return (
    <div className="flex items-center">
      <div className="bg-black/70 text-white px-2 py-0.5 rounded-full text-xs">
        {batchCount}/{totalCount}
      </div>
      {prompt && (
        <div className="ml-2 text-sm font-medium truncate max-w-[200px]">
          {prompt}
        </div>
      )}
      {(hasGenerating || hasFailed) && (
        <div className="ml-2 flex items-center">
          {hasGenerating && (
            <span className="text-amber-500 text-xs">Generating</span>
          )}
          {hasFailed && (
            <span className="text-red-500 text-xs ml-1">Failed</span>
          )}
        </div>
      )}
    </div>
  );
};

export default BatchCountDisplay;
