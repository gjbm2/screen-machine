
import React from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Image } from 'lucide-react';

interface ImageInfoDialogProps {
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
  image: {
    url: string;
    prompt?: string;
    workflow: string;
    params?: Record<string, any>;
    referenceImageUrl?: string;
    timestamp?: number;
  };
  dimensions?: { width: number; height: number };
}

const ImageInfoDialog: React.FC<ImageInfoDialogProps> = ({
  isOpen,
  onOpenChange,
  image,
  dimensions
}) => {
  const formatDate = (timestamp?: number) => {
    if (!timestamp) return 'Unknown';
    
    return new Date(timestamp).toLocaleString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  // Handle multiple reference images if they exist (they could be in a comma-separated string)
  const referenceImages = image.referenceImageUrl ? 
    image.referenceImageUrl.split(',').map(url => url.trim()).filter(url => url !== '') : 
    [];

  console.log("Reference images in dialog:", referenceImages);

  return (
    <Dialog open={isOpen} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>Image Information</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          {/* Prompt Section */}
          <div className="space-y-2">
            <h3 className="text-sm font-bold">Prompt</h3>
            <div className="text-sm whitespace-pre-wrap flex">
              {referenceImages.length > 0 && (
                <span className="mr-2 flex-shrink-0">
                  <Image size={16} />
                </span>
              )}
              <span>{image.prompt || 'No prompt provided'}</span>
            </div>
          </div>
          
          {/* Image Details */}
          <div className="space-y-2">
            <h3 className="text-sm font-bold">Details</h3>
            <div className="grid grid-cols-2 gap-2 text-sm">
              <div>Workflow:</div>
              <div>{image.workflow || 'Unknown'}</div>
              
              {dimensions && (
                <>
                  <div>Dimensions:</div>
                  <div>{dimensions.width} x {dimensions.height}px</div>
                </>
              )}
              
              <div>Generated:</div>
              <div>{formatDate(image.timestamp)}</div>
            </div>
          </div>
          
          {/* Parameters */}
          {image.params && Object.keys(image.params).length > 0 && (
            <div className="space-y-2">
              <h3 className="text-sm font-bold">Parameters</h3>
              <div className="grid grid-cols-2 gap-2 text-sm">
                {Object.entries(image.params).map(([key, value]) => (
                  <React.Fragment key={key}>
                    <div className="font-medium">{key}:</div>
                    <div>{String(value)}</div>
                  </React.Fragment>
                ))}
              </div>
            </div>
          )}
          
          {/* Reference Images */}
          {referenceImages.length > 0 && (
            <div className="space-y-2">
              <h3 className="text-sm font-bold">Reference Images</h3>
              <div className="flex flex-wrap gap-2">
                {referenceImages.map((imageUrl, index) => (
                  <div key={index} className="border rounded-md overflow-hidden w-24 h-24">
                    <img 
                      src={imageUrl} 
                      alt={`Reference ${index + 1}`}
                      className="w-full h-full object-cover"
                    />
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
};

export default ImageInfoDialog;
