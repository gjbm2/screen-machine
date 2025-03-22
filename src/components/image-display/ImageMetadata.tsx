
import React from 'react';
import { Clock, Ruler, ExternalLink } from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';
import { Button } from '@/components/ui/button';

interface ImageMetadataProps {
  dimensions: {
    width: number;
    height: number;
  };
  timestamp?: number;
  imageUrl?: string;
  onOpenInNewTab?: (e: React.MouseEvent) => void;
}

const ImageMetadata: React.FC<ImageMetadataProps> = ({ 
  dimensions, 
  timestamp, 
  imageUrl, 
  onOpenInNewTab 
}) => {
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
      <div className="flex items-center gap-4">
        <div className="flex items-center">
          <Clock className="h-4 w-4 mr-1" />
          <span>{formatTimeAgo(timestamp)}</span>
        </div>
        
        {/* Open in new tab button - now in the metadata row */}
        {imageUrl && onOpenInNewTab && (
          <Button 
            variant="outline" 
            size="sm" 
            className="h-7 px-2 text-xs flex items-center gap-1 text-muted-foreground"
            onClick={onOpenInNewTab}
            aria-label="Open image in new tab"
          >
            <ExternalLink className="h-3.5 w-3.5" />
            <span>Open</span>
          </Button>
        )}
      </div>
    </div>
  );
};

export default ImageMetadata;
