
import React from 'react';
import { Clock, Ruler } from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';

interface ImageMetadataProps {
  dimensions: {
    width: number;
    height: number;
  };
  timestamp?: number;
}

const ImageMetadata: React.FC<ImageMetadataProps> = ({ dimensions, timestamp }) => {
  const formatTimeAgo = (timestamp?: number) => {
    if (!timestamp) return "Unknown time";
    return formatDistanceToNow(new Date(timestamp), { addSuffix: true });
  };

  return (
    <div className="flex justify-between items-center text-sm text-muted-foreground">
      <div className="flex items-center">
        <Ruler className="h-4 w-4 mr-1" />
        <span>{dimensions.width} × {dimensions.height} px</span>
      </div>
      <div className="flex items-center">
        <Clock className="h-4 w-4 mr-1" />
        <span>{formatTimeAgo(timestamp)}</span>
      </div>
    </div>
  );
};

export default ImageMetadata;
