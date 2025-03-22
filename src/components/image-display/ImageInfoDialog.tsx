
import React, { useEffect, useState } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Image } from 'lucide-react';
import ReferenceImagesSection from './ReferenceImagesSection';
import ReferenceImageDialog from './ReferenceImageDialog';

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
  const [referenceDialogOpen, setReferenceDialogOpen] = useState(false);
  const [selectedReferenceImage, setSelectedReferenceImage] = useState<string>('');
  
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

  // Process reference images if they exist
  const referenceImages = image.referenceImageUrl ? 
    image.referenceImageUrl.split(',').map(url => url.trim()).filter(url => url !== '') : 
    [];

  // Handle reference image click
  const handleReferenceImageClick = (imageUrl: string) => {
    setSelectedReferenceImage(imageUrl);
    setReferenceDialogOpen(true);
  };

  // Add useEffect for debugging
  useEffect(() => {
    if (isOpen) {
      console.log("ImageInfoDialog opened with image:", image);
      console.log("Reference image URL from image:", image.referenceImageUrl);
      console.log("Processed reference images:", referenceImages);
    }
  }, [isOpen, image, referenceImages]);

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
          
          {/* Reference Images - Moved above Details */}
          {referenceImages.length > 0 && (
            <div className="space-y-2">
              <h3 className="text-sm font-bold">Reference Images</h3>
              <div className="flex flex-wrap gap-2">
                {referenceImages.map((imgUrl, index) => (
                  <div key={index} className="border rounded-md overflow-hidden w-24 h-24 cursor-pointer" onClick={() => handleReferenceImageClick(imgUrl)}>
                    <img 
                      src={imgUrl} 
                      alt={`Reference ${index + 1}`}
                      className="w-full h-full object-cover"
                      onError={(e) => {
                        console.error("Error loading reference image:", imgUrl);
                        (e.target as HTMLImageElement).src = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='24' height='24' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Crect width='18' height='18' x='3' y='3' rx='2' ry='2'/%3E%3Ccircle cx='9' cy='9' r='2'/%3E%3Cpath d='m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21'/%3E%3C/svg%3E";
                      }}
                    />
                  </div>
                ))}
              </div>
            </div>
          )}
          
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
              
              {referenceImages.length > 0 && (
                <>
                  <div>Reference Images:</div>
                  <div>{referenceImages.length}</div>
                </>
              )}
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
        </div>
        
        {/* Reference image viewer dialog */}
        <ReferenceImageDialog 
          isOpen={referenceDialogOpen}
          onOpenChange={setReferenceDialogOpen}
          imageUrl={selectedReferenceImage}
        />
      </DialogContent>
    </Dialog>
  );
};

export default ImageInfoDialog;
