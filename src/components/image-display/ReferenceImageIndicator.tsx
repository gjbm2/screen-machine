
import React, { useState } from 'react';
import { X, Loader2 } from 'lucide-react';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog';

interface ReferenceImageIndicatorProps {
  imageUrl: string;
  onRemove?: () => void;
  position?: 'top-left' | 'top-right' | 'bottom-left' | 'bottom-right';
}

const ReferenceImageIndicator: React.FC<ReferenceImageIndicatorProps> = ({ 
  imageUrl, 
  onRemove,
  position = 'bottom-left' // Default position
}) => {
  const [isOpen, setIsOpen] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [hasError, setHasError] = useState(false);

  const handleRemove = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (onRemove) {
      onRemove();
    }
  };

  const handleImageLoad = () => {
    setIsLoading(false);
    setHasError(false);
  };

  const handleImageError = () => {
    setIsLoading(false);
    setHasError(true);
  };

  return (
    <Dialog open={isOpen} onOpenChange={setIsOpen}>
      <DialogTrigger asChild>
        <div className="relative w-full h-full cursor-pointer flex items-center justify-center bg-muted/20">
          {/* Loading state */}
          {isLoading && (
            <div className="absolute inset-0 flex items-center justify-center bg-muted/30">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          )}
          
          {/* Error state */}
          {hasError && (
            <div className="absolute inset-0 flex items-center justify-center bg-muted/30">
              <div className="text-center text-muted-foreground">
                <div className="text-xs">Failed to load</div>
              </div>
            </div>
          )}
          
          {/* Image */}
          <img 
            src={imageUrl} 
            alt="Reference" 
            className={`max-w-full max-h-full object-contain transition-opacity duration-200 ${
              isLoading ? 'opacity-0' : 'opacity-100'
            }`}
            onClick={() => setIsOpen(true)}
            onLoad={handleImageLoad}
            onError={handleImageError}
          />
          
          {/* Remove button */}
          {onRemove && !isLoading && (
            <button 
              className="absolute top-0.5 right-0.5 bg-black/70 hover:bg-black/90 rounded-full p-1 text-white transition-colors z-10"
              onClick={handleRemove}
            >
              <X className="h-3 w-3" />
            </button>
          )}
        </div>
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Reference Image</DialogTitle>
        </DialogHeader>
        <div className="flex items-center justify-center">
          <img 
            src={imageUrl} 
            alt="Reference image full view" 
            className="max-w-full max-h-[70vh] object-contain rounded-md"
          />
        </div>
      </DialogContent>
    </Dialog>
  );
};

export default ReferenceImageIndicator;
