
import React from 'react';

const ImageLoadingState: React.FC = () => {
  return (
    <div className="w-full h-full flex items-center justify-center bg-muted/30">
      <div className="animate-pulse flex flex-col items-center">
        <div className="h-12 w-12 animate-spin rounded-full border-4 border-primary border-t-transparent"></div>
        <div className="h-2 w-24 bg-muted-foreground/20 rounded mt-2"></div>
      </div>
    </div>
  );
};

export default ImageLoadingState;
