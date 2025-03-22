
import React from 'react';
import { Clock, Ruler, ExternalLink, Info } from 'lucide-react';
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
  onInfoClick?: () => void;
  hasReferenceImages?: boolean;
}

const ImageMetadata: React.FC<ImageMetadataProps> = ({ 
  dimensions, 
  timestamp, 
  imageUrl, 
  onOpenInNewTab,
  onInfoClick,
  hasReferenceImages
}) => {
  const formatTimeAgo = (timestamp?: number) => {
    if (!timestamp) return "Unknown time";
    return formatDistanceToNow(new Date(timestamp), { addSuffix: true });
  };

  return (
    <div className="flex justify-between items-center text-xs md:text-sm text-muted-foreground">
      <div className="flex items-center gap-3">
        <div className="flex items-center">
          <Ruler className="h-3 w-3 md:h-4 md:w-4 mr-1" />
          <span className="whitespace-nowrap">{dimensions.width} × {dimensions.height} px</span>
        </div>
        <div className="flex items-center">
          <Clock className="h-3 w-3 md:h-4 md:w-4 mr-1" />
          <span className="whitespace-nowrap">{formatTimeAgo(timestamp)}</span>
        </div>
      </div>
      
      <div className="flex items-center gap-1">
        {/* Info button - only shown if onInfoClick is provided */}
        {onInfoClick && (
          <Button 
            variant="outline" 
            size="sm" 
            className="h-6 md:h-7 px-1.5 md:px-2 text-xs md:text-sm flex items-center gap-1 text-muted-foreground whitespace-nowrap"
            onClick={() => onInfoClick()} // Ensure click event calls the function properly
            aria-label="View image information"
            onMouseDown={(e) => e.preventDefault()} // Prevent text selection
          >
            <Info className="h-3 w-3 md:h-3.5 md:w-3.5" />
            <span className="hidden xs:inline">Info</span>
          </Button>
        )}
        
        {/* Open in new tab button */}
        {imageUrl && onOpenInNewTab && (
          <Button 
            variant="outline" 
            size="sm" 
            className="h-6 md:h-7 px-1.5 md:px-2 text-xs md:text-sm flex items-center gap-1 text-muted-foreground whitespace-nowrap"
            onClick={onOpenInNewTab}
            aria-label="Open image in new tab"
            onMouseDown={(e) => e.preventDefault()} // Prevent text selection
          >
            <ExternalLink className="h-3 w-3 md:h-3.5 md:w-3.5" />
            <span className="hidden xs:inline">Open</span>
          </Button>
        )}
      </div>
    </div>
  );
};

export default ImageMetadata;
