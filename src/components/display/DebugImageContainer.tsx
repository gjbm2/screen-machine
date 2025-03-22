
import React from 'react';
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { AspectRatio } from "@/components/ui/aspect-ratio";
import { ShowMode } from './types';
import { Alert, AlertDescription } from "@/components/ui/alert";
import { RefreshCw } from "lucide-react";

interface DebugImageContainerProps {
  imageUrl: string | null;
  imageKey: number;
  showMode: ShowMode;
  backgroundColor: string;
  onImageError: () => void;
  imageRef: React.RefObject<HTMLImageElement>;
  imageChanged?: boolean;
}

export const DebugImageContainer: React.FC<DebugImageContainerProps> = ({
  imageUrl,
  imageKey,
  showMode,
  backgroundColor,
  onImageError,
  imageRef,
  imageChanged
}) => {
  // Get the viewport dimensions to simulate the correct aspect ratio
  const viewportRatio = window.innerWidth / window.innerHeight;
  
  return (
    <Card className="w-2/3 max-w-3xl mx-auto">
      <CardHeader className="pb-2 flex flex-row justify-between items-center">
        <CardTitle className="text-lg">Image Preview ({showMode} mode)</CardTitle>
        {imageChanged && (
          <Alert variant="default" className="py-2 border-amber-500 bg-amber-50">
            <RefreshCw className="h-4 w-4 text-amber-500 mr-2 animate-spin" />
            <AlertDescription className="text-amber-600">
              Image has been updated on the server
            </AlertDescription>
          </Alert>
        )}
      </CardHeader>
      <CardContent>
        {/* Maintain aspect ratio of viewport */}
        <AspectRatio ratio={viewportRatio} className="overflow-hidden">
          <div 
            className="w-full h-full relative flex items-center justify-center"
            style={{ backgroundColor: `#${backgroundColor}` }}
          >
            {imageUrl && (
              <img
                key={imageKey}
                ref={imageRef}
                src={imageUrl}
                alt=""
                style={{
                  ...(showMode === 'fill' ? {
                    width: '100%',
                    height: '100%',
                    objectFit: 'cover',
                    position: 'absolute',
                    top: 0,
                    left: 0,
                  } : showMode === 'fit' ? {
                    maxWidth: '100%',
                    maxHeight: '100%',
                    objectFit: 'contain',
                  } : {
                    width: 'auto',
                    height: 'auto',
                    objectFit: 'none',
                  })
                }}
                onError={onImageError}
              />
            )}
            {!imageUrl && (
              <div className="text-center p-4 text-gray-500">
                <p>No image selected</p>
                <p className="text-sm mt-2">Select an image from the available files list or enter a custom URL</p>
              </div>
            )}
          </div>
        </AspectRatio>
      </CardContent>
    </Card>
  );
};
