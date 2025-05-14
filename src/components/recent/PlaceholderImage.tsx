import React from 'react';
import { Loader2 } from 'lucide-react';

interface PlaceholderImageProps {
  aspectRatio?: number;
  className?: string;
}

export const PlaceholderImage: React.FC<PlaceholderImageProps> = ({ 
  aspectRatio = 16/9,
  className = ''
}) => {
  // Calculate padding based on aspect ratio: (height/width) * 100%
  const paddingBottom = `${(1 / aspectRatio) * 100}%`;
  
  return (
    <div 
      className={`relative w-full ${className}`}
      style={{ paddingBottom }}
    >
      <div className="absolute inset-0 flex items-center justify-center bg-gray-100 dark:bg-gray-800 rounded">
        <div className="flex flex-col items-center justify-center gap-2">
          <Loader2 className="h-6 w-6 animate-spin text-gray-400" />
          <span className="text-xs text-gray-500">Generating...</span>
        </div>
      </div>
    </div>
  );
};

export default PlaceholderImage; 