
import React from 'react';
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { AspectRatio } from "@/components/ui/aspect-ratio";
import { ShowMode } from './types';

interface DebugImageContainerProps {
  imageUrl: string | null;
  imageKey: number;
  showMode: ShowMode;
  backgroundColor: string;
  onImageError: () => void;
  imageRef: React.RefObject<HTMLImageElement>;
}

export const DebugImageContainer: React.FC<DebugImageContainerProps> = ({
  imageUrl,
  imageKey,
  showMode,
  backgroundColor,
  onImageError,
  imageRef
}) => {
  // Get the viewport dimensions to simulate the correct aspect ratio
  const viewportRatio = window.innerWidth / window.innerHeight;
  
  return (
    <Card className="w-2/3 max-w-3xl mx-auto">
      <CardHeader className="pb-2">
        <CardTitle className="text-lg">Image Preview ({showMode} mode)</CardTitle>
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
          </div>
        </AspectRatio>
      </CardContent>
    </Card>
  );
};
