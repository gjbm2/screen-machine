
import React from 'react';
import { Card } from '@/components/ui/card';

interface ImageDisplayProps {
  imageUrl: string | null;
  prompt: string | null;
  isLoading: boolean;
  uploadedImage?: string | null;
  workflow?: string | null;
}

const ImageDisplay: React.FC<ImageDisplayProps> = ({ 
  imageUrl, 
  prompt, 
  isLoading,
  uploadedImage,
  workflow
}) => {
  // Always render the component when we have an uploaded image or when we're loading
  // or when we have a generated image result
  const shouldDisplay = isLoading || imageUrl || uploadedImage;
  
  if (!shouldDisplay) return null;

  return (
    <div className="mt-12 animate-fade-in">
      <div className="flex flex-col md:flex-row gap-6">
        {uploadedImage && (
          <Card className="relative w-full md:w-1/2 overflow-hidden border border-border/30 rounded-lg">
            <div className="aspect-square overflow-hidden bg-secondary/20">
              <img
                src={uploadedImage}
                alt="Reference image"
                className="w-full h-full object-contain"
              />
            </div>
            <div className="p-3 text-center">
              <h3 className="text-sm font-medium">Reference Image</h3>
            </div>
          </Card>
        )}

        <Card className="relative w-full md:w-1/2 overflow-hidden border border-border/30 rounded-lg">
          <div className="aspect-square overflow-hidden bg-secondary/20">
            {isLoading ? (
              <div className="flex h-full items-center justify-center">
                <div className="h-12 w-12 animate-spin rounded-full border-4 border-primary border-t-transparent"></div>
              </div>
            ) : imageUrl ? (
              <img
                src={imageUrl}
                alt={prompt || 'Generated image'}
                className="w-full h-full object-contain"
              />
            ) : null}
          </div>
          <div className="p-3">
            {prompt && (
              <p className="text-sm text-center text-muted-foreground truncate">
                {prompt}
              </p>
            )}
            {workflow && (
              <p className="text-xs text-center text-muted-foreground mt-1">
                Workflow: {workflow.replace(/-/g, ' ')}
              </p>
            )}
          </div>
        </Card>
      </div>
    </div>
  );
};

export default ImageDisplay;
