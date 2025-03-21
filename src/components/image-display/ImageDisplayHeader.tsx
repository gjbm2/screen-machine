
import React from 'react';
import ViewModeSelector from './ViewModeSelector';
import { ViewMode } from './ImageDisplay';

interface ImageDisplayHeaderProps {
  viewMode: ViewMode;
  onViewModeChange: (value: ViewMode) => void;
}

const ImageDisplayHeader: React.FC<ImageDisplayHeaderProps> = ({
  viewMode,
  onViewModeChange
}) => {
  return (
    <div className="flex justify-between items-center mb-1">
      <h2 className="text-xl font-bold">Generated Images</h2>
      <ViewModeSelector 
        viewMode={viewMode} 
        onViewModeChange={(value) => onViewModeChange(value as ViewMode)} 
      />
    </div>
  );
};

export default ImageDisplayHeader;
