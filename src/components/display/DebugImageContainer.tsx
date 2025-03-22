
import React from 'react';
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { AspectRatio } from "@/components/ui/aspect-ratio";
import { ShowMode, PositionMode, CaptionPosition } from './types';
import { Alert, AlertDescription } from "@/components/ui/alert";
import { RefreshCw } from "lucide-react";

interface DebugImageContainerProps {
  imageUrl: string | null;
  imageKey: number;
  showMode: ShowMode;
  position: PositionMode;
  backgroundColor: string;
  onImageError: () => void;
  imageRef: React.RefObject<HTMLImageElement>;
  imageChanged?: boolean;
  caption?: string | null;
  captionPosition?: CaptionPosition;
  captionSize?: string;
  captionColor?: string;
  captionFont?: string;
  metadata?: Record<string, string>;
}

export const DebugImageContainer: React.FC<DebugImageContainerProps> = ({
  imageUrl,
  imageKey,
  showMode,
  position,
  backgroundColor,
  onImageError,
  imageRef,
  imageChanged,
  caption,
  captionPosition = 'bottom-center',
  captionSize = '16px',
  captionColor = 'ffffff',
  captionFont = 'Arial, sans-serif',
  metadata
}) => {
  // Get the viewport dimensions to simulate the correct aspect ratio
  const viewportRatio = window.innerWidth / window.innerHeight;
  
  return (
    <Card className="w-2/3 max-w-3xl mx-auto">
      <CardHeader className="pb-2 flex flex-row justify-between items-center">
        <CardTitle className="text-lg">Image Preview ({showMode} mode, {position} position)</CardTitle>
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
              <>
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
                    } : showMode === 'fit' ? {
                      maxWidth: '100%',
                      maxHeight: '100%',
                      objectFit: 'contain',
                    } : showMode === 'stretch' ? {
                      width: '100%',
                      height: '100%',
                      objectFit: 'fill',
                    } : {
                      width: 'auto',
                      height: 'auto',
                      objectFit: 'none',
                    }),
                    // Fixed position logic to handle all position types including 'center'
                    ...(position !== 'center' && {
                      position: 'absolute',
                      ...(position.includes('top') ? { top: 0 } : 
                         position.includes('bottom') ? { bottom: 0 } : 
                         { top: '50%', transform: 'translateY(-50%)' }),
                      ...(position.includes('left') ? { left: 0 } : 
                         position.includes('right') ? { right: 0 } : 
                         { left: '50%', transform: position.includes('center-') ? 
                           'translateY(-50%)' : position === 'center' ? 
                           'translate(-50%, -50%)' : 'translateX(-50%)' }),
                    })
                  }}
                  onError={onImageError}
                />
                
                {caption && (
                  <div style={{
                    position: 'absolute',
                    padding: '8px 16px',
                    backgroundColor: 'rgba(0, 0, 0, 0.7)',
                    color: `#${captionColor}`,
                    fontSize: captionSize,
                    fontFamily: captionFont,
                    maxWidth: '80%',
                    textAlign: 'center',
                    borderRadius: '4px',
                    zIndex: 10,
                    ...(captionPosition?.includes('top') ? { top: '10px' } : 
                       captionPosition?.includes('bottom') ? { bottom: '10px' } : 
                       { top: '50%', transform: 'translateY(-50%)' }),
                    ...(captionPosition?.includes('left') ? { left: '10px' } : 
                       captionPosition?.includes('right') ? { right: '10px' } : 
                       { left: '50%', transform: captionPosition === 'bottom-center' || captionPosition === 'top-center' ? 
                         'translateX(-50%)' : 'none' }),
                  }}>
                    {caption}
                  </div>
                )}
                
                {metadata && Object.keys(metadata).length > 0 && (
                  <div style={{
                    position: 'absolute',
                    top: '10px',
                    left: '10px',
                    padding: '10px',
                    backgroundColor: 'rgba(0, 0, 0, 0.7)',
                    color: '#ffffff',
                    borderRadius: '4px',
                    fontSize: '12px',
                    maxWidth: '90%',
                    maxHeight: '70%',
                    overflowY: 'auto',
                    zIndex: 10,
                  }}>
                    {Object.entries(metadata).map(([key, value]) => (
                      <div key={key} className="mb-1">
                        <strong>{key}:</strong> {value}
                      </div>
                    ))}
                  </div>
                )}
              </>
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
