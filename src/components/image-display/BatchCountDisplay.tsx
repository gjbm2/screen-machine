
import React from 'react';
import { ViewMode } from './ImageDisplay';

interface BatchCountDisplayProps {
  index: number;
  total: number;
  viewMode: ViewMode;
}

const BatchCountDisplay: React.FC<BatchCountDisplayProps> = ({ index, total, viewMode }) => {
  if (total <= 1 || viewMode === 'small' || viewMode === 'table') return null;
  
  return (
    <div className="absolute top-2 left-2 bg-black/70 text-white px-2 py-0.5 rounded-full text-xs">
      {index + 1}/{total}
    </div>
  );
};

export default BatchCountDisplay;
