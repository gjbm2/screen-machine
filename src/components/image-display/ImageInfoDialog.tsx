
import React from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Image } from 'lucide-react';
import ReferenceImageSection from './ReferenceImageSection';

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
              {image.referenceImageUrl && (
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
          
          {/* Reference Image */}
          {image.referenceImageUrl && (
            <div className="space-y-2">
              <h3 className="text-sm font-bold">Reference Image</h3>
              <div className="border rounded-md overflow-hidden max-w-[200px]">
                <img 
                  src={image.referenceImageUrl} 
                  alt="Reference"
                  className="w-full h-auto object-contain"
                />
              </div>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
};

export default ImageInfoDialog;
