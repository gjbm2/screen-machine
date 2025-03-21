
import React from 'react';

interface BatchCountDisplayProps {
  index: number;
  total: number;
  viewMode: 'normal' | 'small' | 'table';
}

const BatchCountDisplay: React.FC<BatchCountDisplayProps> = ({ index, total, viewMode }) => {
  if (total <= 1 || viewMode === 'small') return null;
  
  return (
    <div className="absolute bottom-2 right-2 bg-black/70 text-white px-2 py-0.5 rounded-full text-xs">
      {index + 1}/{total}
    </div>
  );
};

export default BatchCountDisplay;
